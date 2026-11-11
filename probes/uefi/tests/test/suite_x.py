# SPDX-License-Identifier: MIT
"""Suites of the SP11 X2 tests image (probes/uefi/tests, tools/build-uefi-tests.py).

    XNativeTests   the C logic tests (tools/test-uefi-tests-logic.c), plain and under
                   ASan+UBSan, and the NV2 record round trip through tools/nv2_record.py
    XBuildTests    the build tool: --soc refusal, variants and their flags, reproducibility,
                   the default probe build staying free of the tests
    XAuditTests    the instruction audit of the built images, the vector table layout and
                   the source audit, each against deliberately defective input
    XQemuTests     the model image under QEMU + EDK2 (GICv3, SMMUv3, NVMe behind a root
                   port): the whole run, the same run started from the pinned UEFI Shell
                   (skipped without SP11_UEFI_SHELL), the files on the stick, the record
                   through the monitor's own gate logic, the retyped screen, a guest at
                   EL1, the fatal page

QEMU is a model. A PASS there says the image's control flow, record and screen
work against a standard firmware and a standard virtual machine; it says nothing
about the Surface Pro 11. The record such a run writes is EXPERIMENTAL and is
refused by the gate (tested below), which is the point.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from dataclasses import replace

import audit
import nv2_record
import nv2screen
import peinfo
import qemu_probe
import uefi_testkit as kit
from uefi_testkit import Base, ROOT, run_cmd, tail, text_of

NATIVE_SOURCE = ROOT / "tools" / "test-uefi-tests-logic.c"
TESTS_DIR = ROOT / "probes" / "uefi" / "tests"
X_SECTIONS = ("ids and nv classes", "faulty id images", "syndrome and hcr", "guard policy",
              "sysstate and scope", "read windows", "nv2 classifiers", "nv2 decision", "nv2 run",
              "record evidence", "record format", "acpi tables", "gic against madt",
              "pcie and nvme", "smmu timer zva misc", "device windows", "report and layout",
              "stick writer",
              "hook: clean run", "hook: cleanup failures", "hook: console deferral",
              "hook: fatal page", "hook: phase b fallback")
# Sources of the image that are not part of the host build: the entry hook and
# the two files made of privileged instructions (audited as built, not run).
NOT_NATIVE = {"hook.c", "sys_aarch64.c", "vectors_aarch64.c"}
_OK_SECTION = re.compile(r"^ok\s+(.+?)\s+(\d+) checks$")


def native_command():
    rows = [re.sub(r"^\s*(/\*+|\*+/?|//+)\s?", "", raw).replace("*/", "").strip()
            for raw in NATIVE_SOURCE.read_text(encoding="utf-8").splitlines()[:80]]
    for i, row in enumerate(rows):
        if re.search(r"(?:cc|clang)\s+-", row):
            command = row
            while command.endswith("\\") and i + 1 < len(rows):
                i += 1
                command = command[:-1].rstrip() + " " + rows[i]
            argv = shlex.split(command)
            return argv[:argv.index("&&")] if "&&" in argv else argv
    raise AssertionError("tools/test-uefi-tests-logic.c has no compile command in its header")


def native_exe(tag):
    def build():
        argv = native_command()
        argv[0] = shutil.which("clang") or kit.tools().clang
        exe = kit.WORK / f"xnative-{tag}"
        exe.parent.mkdir(parents=True, exist_ok=True)
        argv[argv.index("-o") + 1] = str(exe)
        if tag == "asan":
            argv[1:1] = kit.SANITIZE_FLAGS
        rc, out, err = run_cmd(argv)
        if rc != 0:
            raise AssertionError(f"building the {tag} native X2 test failed:\n"
                                 f"{tail(text_of(err), 30)}")
        return exe
    return kit.once(("xnative", tag), build)


def native_output_problems(rc, text, sections=X_SECTIONS):
    bad = []
    if rc != 0:
        bad.append(f"exit status {rc}")
    seen = {}
    for row in text.splitlines():
        if row.startswith("FAIL"):
            bad.append(f"failure line: {row.strip()}")
        m = _OK_SECTION.match(row)
        if m:
            seen[m.group(1)] = int(m.group(2))
    for name in sections:
        if name not in seen:
            bad.append(f"section {name!r} did not report success")
        elif seen[name] < 10:
            bad.append(f"section {name!r} ran only {seen[name]} checks")
    if not re.search(r"^PASS: \d+ checks, 0 failures$", text, re.M):
        bad.append("no 'PASS: N checks, 0 failures' summary line")
    return bad


def emitted_records():
    rc, out, err = run_cmd([native_exe("plain"), "--emit-records"])
    if rc != 0:
        raise AssertionError(f"--emit-records failed: {tail(text_of(err))}")
    records, current = {}, None
    for line in text_of(out).splitlines():
        if line.startswith("RECORD "):
            current = records.setdefault(line.split()[1], {"rows": []})
        elif current is not None and line.startswith(("TEXT ", "JSON ", "BIN ")):
            kind, _, hexdata = line.partition(" ")
            current[kind] = bytes.fromhex(hexdata)
        elif current is not None and line.startswith("ROW "):
            current["rows"].append(line[4:])
    return records


EXPECTED_FIXTURES = {
    "go-hw": dict(soc=1, vncr_redirect=1, negative_control_trapped=1, evidence=2,
                  tool="SP11-X2-TESTS", build_id=0xC0FFEE01),
    "go-model": dict(soc=1, vncr_redirect=1, negative_control_trapped=1, evidence=4,
                     tool="SP11-X2-MODEL", build_id=0xC0FFEE01),
    "redirect-fail": dict(soc=1, vncr_redirect=2, negative_control_trapped=1, evidence=2,
                          tool="SP11-X2-TESTS", build_id=0xC0FFEE01),
    "not-run": dict(soc=1, vncr_redirect=0, negative_control_trapped=0, evidence=0,
                    tool="SP11-X2-TESTS", build_id=0xC0FFEE01),
    "extremes": dict(soc=2, midr_el1=2**64 - 1, id_aa64mmfr2_el1=2**64 - 1, vncr_redirect=1,
                     negative_control_trapped=2, evidence=2, tool="ABCDEFGHIJKLMNO",
                     build_id=0xFFFFFFFF),
    "tool-chars": dict(soc=0, tool="a.b_c-Z9", evidence=5),
}


class XNativeTests(Base):
    def test_build_line_lists_every_test_unit(self):
        argv = native_command()
        listed = {a for a in argv if a.endswith(".c")}
        on_disk = {str(p.relative_to(ROOT)) for p in (TESTS_DIR / "test").glob("test_*.c")}
        on_disk |= {str(p.relative_to(ROOT)) for p in TESTS_DIR.glob("*.c")
                    if p.name not in NOT_NATIVE and p.name != "desc_data.c"}
        problems = [f"{n} is not in the build line of the C test's header" for n in sorted(on_disk - listed)]
        problems += [f"{n} is on the build line but does not exist"
                     for n in sorted(listed) if not (ROOT / n).is_file()]
        problems += [f"{flag} is missing from the build line" for flag in ("-Wall", "-Wextra", "-Werror")
                     if flag not in argv]
        for name in NOT_NATIVE:
            if f"probes/uefi/tests/{name}" in listed:
                problems.append(f"{name} cannot be built for the host but is on the build line")
        self.check(problems)

    def test_logic_suite_passes(self):
        rc, out, err = run_cmd([native_exe("plain")], env=kit.asan_env())
        output = text_of(out)
        problems = native_output_problems(rc, output)
        if "sanitizers: none (plain build)" not in output:
            problems.append("the plain build did not say it has no sanitizers")
        self.check(problems, "--- stdout tail ---\n" + tail(output) + "\n--- stderr ---\n"
                   + tail(text_of(err), 5))

    def test_logic_suite_passes_under_asan_and_ubsan(self):
        rc, out, err = run_cmd([native_exe("asan")], env=kit.asan_env())
        output = text_of(out)
        problems = native_output_problems(rc, output)
        if "sanitizers: AddressSanitizer + UndefinedBehaviorSanitizer" not in output:
            problems.append("the sanitized build did not report that it is sanitized")
        self.check(problems, "--- stdout tail ---\n" + tail(output) + "\n--- stderr ---\n"
                   + tail(text_of(err), 30))

    def test_output_checker_rejects_bad_output(self):
        def synthetic(names=X_SECTIONS, each=50):
            rows = ["sanitizers: none (plain build)"]
            rows += [f"ok   {n:<26} {each} checks" for n in names]
            rows.append(f"PASS: {each * len(names)} checks, 0 failures")
            return "\n".join(rows) + "\n"

        problems = []
        if native_output_problems(0, synthetic()):
            problems.append("a clean synthetic run was rejected")
        cases = (
            ("a non-zero status", native_output_problems(1, synthetic())),
            ("a missing section", native_output_problems(0, synthetic(X_SECTIONS[1:]))),
            ("a section with too few checks", native_output_problems(0, synthetic(each=3))),
            ("a FAIL row", native_output_problems(0, synthetic() + "FAIL line 3 [x]: y\n")),
            ("no summary", native_output_problems(0, synthetic().rsplit("PASS:", 1)[0])),
        )
        problems += [f"{label} was accepted" for label, found in cases if not found]
        self.check(problems)

    def test_record_forms_agree_with_the_operator_tool(self):
        """C text, JSON and binary are byte-identical to the tool's own canonical forms."""
        records, problems = emitted_records(), []
        if set(records) != set(EXPECTED_FIXTURES):
            problems.append(f"fixtures {sorted(records)} differ from {sorted(EXPECTED_FIXTURES)}")
        for name, want in EXPECTED_FIXTURES.items():
            got = records.get(name)
            if got is None:
                continue
            rec_text = nv2_record.parse_text(text_of(got["TEXT"]))
            rec_json = nv2_record.parse_json(text_of(got["JSON"]))
            rec_bin = nv2_record.unpack(got["BIN"])
            if not (rec_text == rec_json == rec_bin):
                problems.append(f"{name}: the three forms decode to different records")
                continue
            for key, value in want.items():
                if getattr(rec_bin, key) != value:
                    problems.append(f"{name}: {key} is {getattr(rec_bin, key)!r}, expected {value!r}")
            if text_of(got["TEXT"]) != nv2_record.format_text(rec_bin):
                problems.append(f"{name}: text differs from the tool's canonical text")
            if text_of(got["JSON"]) != nv2_record.format_json(rec_bin):
                problems.append(f"{name}: JSON differs from the tool's canonical JSON")
            if got["BIN"] != nv2_record.pack(rec_bin):
                problems.append(f"{name}: binary differs from the tool's canonical binary")
        self.check(problems)

    def test_screen_rows_retype_to_the_same_record(self):
        records, problems = emitted_records(), []
        for name, got in records.items():
            try:
                text = nv2screen.rows_to_text(got["rows"])
            except nv2screen.ScreenError as exc:
                problems.append(f"{name}: {exc}")
                continue
            if text != text_of(got["TEXT"]):
                problems.append(f"{name}: the retyped rows do not give back the record text")
        self.check(problems)

    def test_retype_tool_rejects_a_misread_row(self):
        rows = emitted_records()["go-hw"]["rows"]
        problems = []
        for i, row in enumerate(rows):
            if row.startswith("+"):
                continue
            broken = list(rows)
            # One character misread (0 for O, B for 8 and so on) must be refused, never repaired.
            idx = next((k for k, c in enumerate(row) if c.isalnum() and k > 5), None)
            if idx is None:
                continue
            swapped = "1" if row[idx] != "1" else "2"
            broken[i] = row[:idx] + swapped + row[idx + 1:]
            try:
                nv2screen.rows_to_text(broken)
                problems.append(f"row {i} {row!r} with one wrong character was accepted")
            except nv2screen.ScreenError:
                pass
        for label, mangled in (("a continuation with nothing before it", ["+12345678*00"]),
                               ("no record rows at all", ["HELLO"])):
            try:
                nv2screen.rows_to_text(mangled)
                problems.append(f"{label} was accepted")
            except nv2screen.ScreenError:
                pass
        self.check(problems)

    def test_gate_decision_on_the_records(self):
        """The monitor's gate logic (tools/nv2_record.decide) allows only the hardware GO."""
        records = emitted_records()
        mmfr2, mmfr4 = 0x02001234, 0
        midr, revidr = 0x00000000510F0010, 3
        soc = nv2_record.SOC["x1e80100"]
        problems = []
        verdicts = {name: nv2_record.decide(rec["BIN"], soc, midr, revidr, mmfr2, mmfr4)
                    for name, rec in records.items() if name in
                    ("go-hw", "go-model", "redirect-fail", "not-run")}
        if verdicts["go-hw"] != nv2_record.ALLOW:
            problems.append(f"the hardware GO record is not allowed: {verdicts['go-hw']}")
        for name in ("go-model", "redirect-fail", "not-run"):
            if verdicts[name] == nv2_record.ALLOW:
                problems.append(f"{name}: the gate allowed a record that must not unlock NV2")
        self.check(problems)


# ---- Build ------------------------------------------------------------------

_BT = None


def build_tool():
    global _BT
    if _BT is None:
        spec = importlib.util.spec_from_file_location("build_uefi_tests", ROOT / "tools" / "build-uefi-tests.py")
        _BT = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _BT
        spec.loader.exec_module(_BT)
    return _BT


def x_images():
    """{variant: BuildResult} for the three variants, built once."""
    def build():
        bt, tools = build_tool(), kit.tools()
        out = kit.WORK / "ximages"
        out.mkdir(parents=True, exist_ok=True)
        return {name: bt.build_variant(v, "x1e80100", bt.DEFAULT_DESCRIPTOR,
                                       bt.variant_out_dir(v, out), tools)
                for name, v in bt.VARIANTS.items()}
    return kit.once("ximages", build)


class XBuildTests(Base):
    def test_hardware_image_requires_an_explicit_soc(self):
        rc, out, err = run_cmd([sys.executable, "-B", "tools/build-uefi-tests.py", "--out",
                                kit.WORK / "xrefuse"])
        text = text_of(err)
        problems = []
        if rc != 2:
            problems.append(f"exit status {rc}, expected 2")
        if "REFUSED" not in text or "--soc" not in text:
            problems.append(f"the refusal does not name --soc: {text!r}")
        if (kit.WORK / "xrefuse").exists() and any((kit.WORK / "xrefuse").iterdir()):
            problems.append("a refused build still wrote output")
        self.check(problems)

    def test_variants_carry_exactly_their_flags(self):
        images, problems = x_images(), []
        manifests = {n: json.loads(r.manifest.read_text()) for n, r in images.items()}
        hw = set(manifests["hardware"]["defines"])
        if hw != {"PROBE_TESTS_IMAGE=1", "TESTS_SOC=1"}:
            problems.append(f"the hardware image's defines are {sorted(hw)}")
        for name in ("model", "model-fatal", "model-fatal-pre"):
            d = set(manifests[name]["defines"])
            for need in ("TESTS_MODEL_BUILD=1", "PROBE_FAST_TEST=1", "TESTS_NO_RESET=1", "TESTS_SELFTEST=1"):
                if need not in d:
                    problems.append(f"{name} lacks {need}")
        for name, flag in (("model-fatal", "TESTS_SELFTEST_FATAL=1"),
                           ("model-fatal-pre", "TESTS_SELFTEST_FATAL_PRE=1")):
            if flag not in manifests[name]["defines"]:
                problems.append(f"{name} lacks {flag}")
            for other in manifests:
                if other != name and flag in manifests[other]["defines"]:
                    problems.append(f"{other} has {flag}, which only {name} may carry")
        for name, m in manifests.items():
            if any(d.startswith("TESTS_REQUIRE_MMIO_IN_MAP") for d in m["defines"]):
                problems.append(f"{name} turns the strict window policy on by default")
        if images["hardware"].image.parent.name != "ximages" or \
                images["model"].image.parent.name != "test":
            problems.append("only the hardware image belongs in the shipped output directory")
        ids = {r.build_id for r in images.values()}
        if len(ids) != len(images):
            problems.append("two variants share a build id")
        self.check(problems)

    def test_the_strict_window_policy_is_an_explicit_build_option(self):
        bt = build_tool()
        hw = bt.VARIANTS["hardware"]
        strict = replace(hw, defines=hw.defines + ("TESTS_REQUIRE_MMIO_IN_MAP=1",))
        problems = []
        digest = bt.descriptor_c_source(bt.DEFAULT_DESCRIPTOR)[1]
        a = bt.compute_build_id(hw, "x1e80100", digest)
        b = bt.compute_build_id(strict, "x1e80100", digest)
        if a == b:
            problems.append("the build id ignores the window policy")
        ids = {}
        for flag in ([], ["--require-mmio-map"]):
            rc, out, err = run_cmd([sys.executable, "-B", "tools/build-uefi-tests.py", "--soc", "x1e80100",
                                    "--print-build-id", *flag])
            ids[bool(flag)] = text_of(out).strip()
            if rc != 0:
                problems.append(f"--print-build-id {' '.join(flag)} exited {rc}: {text_of(err)!r}")
        if ids[False] == ids[True]:
            problems.append("--require-mmio-map does not change the build id")
        if ids[False] != f"hardware: {a}":
            problems.append(f"the default build id {ids[False]!r} is not the plain hardware one")
        if ids[True] != f"hardware: {b}":
            problems.append(f"--require-mmio-map builds {ids[True]!r}, not the strict hardware variant")
        # The image says which policy it applies, in bytes `strings` shows.
        hook = (TESTS_DIR / "hook.c").read_text(encoding="utf-8")
        if "g_tc.require_mmio_map = TESTS_REQUIRE_MMIO_IN_MAP;" not in hook:
            problems.append("hook.c does not hand the build option to the device tests")
        default = x_images()["hardware"].image.read_bytes()
        if b"SP11X2-WINDOWS=NO-CONFLICT" not in default or b"SP11X2-WINDOWS=STRICT" in default:
            problems.append("the default hardware image does not say NO-CONFLICT")
        built = bt.build_variant(strict, "x1e80100", bt.DEFAULT_DESCRIPTOR, kit.work_dir("xstrict"),
                                 kit.tools())
        image = built.image.read_bytes()
        if b"SP11X2-WINDOWS=STRICT" not in image or b"SP11X2-WINDOWS=NO-CONFLICT" in image:
            problems.append("the strict hardware image does not say STRICT")
        insns = kit.disassembly(built.image)
        problems += [f"strict image: {p}" for p in audit.audit_instructions(insns)]
        self.check(problems)

    def test_images_are_valid_efi_applications(self):
        problems = []
        for name, res in x_images().items():
            try:
                pe = peinfo.parse_pe(res.image.read_bytes())
                problems += [f"{name}: {p}" for p in peinfo.audit_pe(pe)]
            except peinfo.PeError as exc:
                problems.append(f"{name}: {exc}")
        self.check(problems)

    def test_hardware_build_is_reproducible(self):
        bt, first = build_tool(), x_images()["hardware"]
        out = kit.work_dir("xrepro")
        again = bt.build_variant(bt.VARIANTS["hardware"], "x1e80100", bt.DEFAULT_DESCRIPTOR, out, kit.tools())
        self.check([] if again.sha256 == first.sha256 and again.build_id == first.build_id else
                   [f"two builds differ: {first.sha256} / {again.sha256}"])

    def test_build_id_follows_the_soc_and_the_sources(self):
        bt = build_tool()
        a = bt.compute_build_id(bt.VARIANTS["hardware"], "x1e80100", "d")
        b = bt.compute_build_id(bt.VARIANTS["hardware"], "x2e-96-100", "d")
        c = bt.compute_build_id(bt.VARIANTS["hardware"], "x1e80100", "e")
        self.check([] if len({a, b, c}) == 3 else ["the build id ignores the SoC or the descriptor"])

    def test_the_default_probe_build_does_not_contain_the_tests(self):
        bup = build_tool().bup
        problems = []
        if any("tests/" in s for s in bup.BASE_SOURCES):
            problems.append("a tests/ source is in the probe's default source list")
        proc = subprocess.run([kit.tools().clang, *[f for f in bup.CFLAGS if not f.startswith("-D")],
                               "-E", "-Iprobes/uefi", "probes/uefi/probe_main.c"], cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            problems.append(f"preprocessing the probe failed: {tail(proc.stderr)}")
        elif "tests_hook" in proc.stdout or "PROBE_TESTS_IMAGE" in proc.stdout:
            problems.append("the default probe_main.c preprocesses to something that mentions the tests")
        self.check(problems)

    def test_descriptor_is_a_cross_check_only(self):
        hw = x_images()["hardware"].image.read_bytes()
        model = x_images()["model"].image.read_bytes()
        self.check([] if b"DESC D=" in hw and b"DESC D=" in model else
                   ["the descriptor comparison rows are missing from an image"])


# ---- Audit ------------------------------------------------------------------

def ins(addr, mnemonic, operands=""):
    return peinfo.Insn(addr, mnemonic, operands)


class XAuditTests(Base):
    def test_every_image_passes_the_instruction_audit(self):
        problems = []
        for name, res in x_images().items():
            insns = kit.disassembly(res.image)
            problems += [f"{name}: {p}" for p in audit.audit_instructions(insns)]
            problems += [f"{name}: {p}" for p in audit.audit_vectors(insns)]
        self.check(problems)

    def test_instruction_audit_rejects_defective_images(self):
        insns = kit.disassembly(x_images()["hardware"].image)
        cases = (
            ("msr to SCTLR_EL2", [ins(0x1, "msr", "SCTLR_EL2, x0")], "not on the write list"),
            ("msr to MAIR_EL2", [ins(0x1, "msr", "MAIR_EL2, x0")], "not on the write list"),
            ("an unlisted pstate field", [ins(0x1, "msr", "DAIFSet, #0xf")], "unlisted immediate"),
            ("smc", [ins(0x1, "smc", "#0")], "forbidden instruction"),
            ("svc", [ins(0x1, "svc", "#0")], "forbidden instruction"),
            ("tlbi", [ins(0x1, "tlbi", "vmalle1is")], "forbidden instruction"),
            ("at", [ins(0x1, "at", "s1e1r, x0")], "forbidden instruction"),
            ("ic", [ins(0x1, "ic", "iallu")], "forbidden instruction"),
            ("an atomic swap", [ins(0x1, "swp", "x1, x2, [x3]")], "forbidden instruction"),
            ("another DC ZVA", [ins(0x1, "dc", "zva, x1")], "DC ZVA instructions"),
            ("dc cvac", [ins(0x1, "dc", "cvac, x1")], "not an allowed cache operation"),
            ("an extra eret", [ins(0x1, "eret")], "ERET instructions"),
            ("an unlisted hvc", [ins(0x1, "hvc", "#0x12")], "not one of the stub's immediates"),
            ("a second TPIDR_EL2 write", [ins(0x1, "msr", "TPIDR_EL2, x0")], "exactly one msr TPIDR_EL2"),
        )
        problems = []
        for label, extra, fragment in cases:
            found = audit.audit_instructions(insns + extra)
            if not any(fragment in p for p in found):
                problems.append(f"{label}: expected {fragment!r}, got {found}")
        # Removing what must be there is caught too.
        for label, keep, fragment in (
                ("no DC ZVA", lambda i: not (i.mnemonic == "dc" and i.operands.startswith("zva")), "DC ZVA"),
                ("no eret", lambda i: i.mnemonic != "eret", "ERET"),
                ("a missing vector hvc", lambda i: not (i.mnemonic == "hvc" and i.operands == "#0x105"), "0x105")):
            found = audit.audit_instructions([i for i in insns if keep(i)])
            if not any(fragment in p for p in found):
                problems.append(f"{label}: expected {fragment!r}, got {found}")
        self.check(problems)

    def test_vector_audit_rejects_defective_tables(self):
        insns = kit.disassembly(x_images()["hardware"].image)
        base = next(i.addr for i in insns if i.mnemonic == "hvc" and i.operands == "#0x100")
        entry = next(i.addr for i in insns if i.mnemonic == "sub" and i.operands == "sp, sp, #0x110"
                     and i.addr % 0x800 == 0)
        problems = []

        def mutated(change):
            return [change(i) for i in insns]

        cases = (
            ("an EL1 table moved by 4", lambda i: peinfo.Insn(i.addr + 4, i.mnemonic, i.operands)
             if i.mnemonic == "hvc" and i.operands.startswith("#0x1") and len(i.operands) == 6 else i,
             "EL1 vector"),
            ("an EL1 entry with the wrong index", lambda i: peinfo.Insn(i.addr, i.mnemonic, "#0x1ff")
             if i.addr == base + 0x80 * 3 else i, "EL1 vector entry 3"),
            ("an EL2 entry without its frame", lambda i: peinfo.Insn(i.addr, "nop", "")
             if i.addr == entry + 0x80 * 5 else i, "entry 5"),
            ("an EL2 entry with the wrong index", lambda i: peinfo.Insn(i.addr, i.mnemonic, "x0, #0x7")
             if i.addr == entry + 0x80 * 9 + 8 else i, "entry 9"),
            ("an EL2 entry branching elsewhere", lambda i: peinfo.Insn(i.addr, i.mnemonic, "0x1234 <x>")
             if i.addr == entry + 0x80 * 2 + 12 else i, "different targets"),
        )
        for label, change, fragment in cases:
            found = audit.audit_vectors(mutated(change))
            if not any(fragment in p for p in found):
                problems.append(f"{label}: expected {fragment!r}, got {found}")
        self.check(problems)

    def test_source_audit(self):
        problems = audit.audit_sources(ROOT)
        self.check(problems)

    def test_source_audit_rejects_defective_sources(self):
        work = kit.work_dir("xsrcaudit")
        tests = work / "probes" / "uefi" / "tests"
        tests.mkdir(parents=True)
        for p in TESTS_DIR.glob("*.[ch]"):
            (tests / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        problems = []

        def with_change(name, old, new):
            text = (tests / name).read_text(encoding="utf-8")
            (tests / name).write_text(text.replace(old, new, 1) if old else text + new, encoding="utf-8")
            found = audit.audit_sources(work)
            (tests / name).write_text(text, encoding="utf-8")
            return found

        found = with_change("t_misc.c", "", "\nstatic void bad(uint64_t a) { *(volatile uint32_t *)(uintptr_t)a = 1; }\n")
        if not any("volatile" in p for p in found):
            problems.append(f"a store through a volatile pointer was not flagged: {found}")
        found = with_change("env.h", "unsigned (*faults)(void *ctx);", "unsigned (*faults)(void *ctx);\n    int (*wr32)(void *ctx, uint64_t a, uint32_t v);")
        if not any("tests_bus members" in p for p in found):
            problems.append(f"a write member of tests_bus was not flagged: {found}")
        found = with_change("sys_aarch64.c", "", '\n__asm__ volatile("str x0, [x1]");\n')
        if not any("store" in p for p in found):
            problems.append(f"a store in the guarded-read assembly was not flagged: {found}")
        # The saved/restored register model, the code that reads and writes the registers and
        # the audit's write list must name the same registers.
        found = with_change("regs.h", "    W_SPSR_EL2,\n", "    W_SPSR_EL2,\n    W_TTBR0_EL2,\n")
        if not any("state model: regs.h has" in p for p in found):
            problems.append(f"a register added to the model alone was not flagged: {found}")
        found = with_change("regs.h", "    W_SPSR_EL2,\n", "")
        if not any("state model" in p for p in found):
            problems.append(f"a register missing from the model was not flagged: {found}")
        found = with_change("sys_aarch64.c", "    case W_SPSR_EL2:\n        return", "    case W_ELR_EL2:\n        return")
        if not any("wop_" in p for p in found):
            problems.append(f"a register the code cannot read or write was not flagged: {found}")
        found = with_change("sysstate.c", "W_ELR_EL2, W_SPSR_EL2, W_VBAR_EL2,", "W_ELR_EL2, W_VBAR_EL2,")
        if not any("restore_order" in p for p in found):
            problems.append(f"a register nobody restores was not flagged: {found}")
        # Register writes of the privileged file are all read back.
        found = with_change("sys_aarch64.c", "",
                            "\nstatic int bad(void) { return tests_sysstate_write(&g_nv2_state, &g_wops, W_HCR_EL2, 0); }\n")
        if not any("not read back" in p for p in found):
            problems.append(f"a register write without read-back was not flagged: {found}")
        found = with_change("sys_aarch64.c",
                            "nv2),\n                                         ~0ull);\n}",
                            "nv2),\n                                         0xFFull);\n}")
        if not any("W_HCR_EL2" in p and "every bit" in p for p in found):
            problems.append(f"an HCR_EL2 read-back that compares only some bits was not flagged: {found}")
        self.check(problems)


# ---- QEMU -------------------------------------------------------------------

FULL = "virt,virtualization=on,gic-version=3,iommu=smmuv3"


@contextlib.contextmanager
def machine(spec):
    """QEMU with a GICv3, an SMMUv3 and an NVMe controller behind a PCIe root port."""
    original = qemu_probe.qemu_command

    def patched(cfg, assets, vars_copy, stick, serial):
        cmd = original(cfg, assets, vars_copy, stick, serial)
        cmd[cmd.index("-machine") + 1] = spec
        disk = serial.parent / "nvme.img"
        disk.write_bytes(bytes(1 << 20))
        return cmd + ["-drive", f"if=none,id=nv0,format=raw,file={disk}",
                      "-device", "pcie-root-port,id=rp1,bus=pcie.0,chassis=1",
                      "-device", "nvme,bus=rp1,serial=sp11x2,drive=nv0"]

    qemu_probe.qemu_command = patched
    try:
        yield
    finally:
        qemu_probe.qemu_command = original


def scenario(name, variant, spec, capture, el2=True, until=None, capture_when=None, shell=False):
    """`shell`: boot the pinned UEFI Shell; a test-only STARTUP.NSH types the operator's command."""
    def build():
        launcher = kit.pinned_shell() if shell else None  # skips before anything is booted
        try:
            assets = qemu_probe.find_assets()
        except qemu_probe.Unavailable as exc:
            raise unittest.SkipTest(str(exc))
        image = x_images()[variant].image
        cfg = qemu_probe.RunConfig(name=f"x-{name}", efi=launcher or image,
                                   extra_files=kit.shell_launch_files(image) if launcher else {},
                                   workdir=kit.work_dir(f"xq-{name}") / "run", capture_seconds=capture,
                                   el2=el2, smp=2, boot_timeout=300.0, pre_ebs_timeout=240.0,
                                   stop_when=until, capture_when=capture_when)
        with machine(spec):
            return qemu_probe.run(cfg, assets, kit.font_table())
    return kit.once(("xqemu", name), build)


def x_markers(out):
    rows = []
    for line in out.serial.splitlines():
        m = re.search(r"SP11X2 (\w+)\s*(.*)$", line)
        if m:
            rows.append((m.group(1), dict(re.findall(r"(\w+)=(\S+)", m.group(2))), line.strip()))
    return rows


def stable_screens(out):
    """Decoded screens that stayed on the display for at least a second before the next
    change. The runner records a sample only when the frame changes, so a frame captured
    while the image was still drawing it is replaced within a fraction of a second, and
    a page of the report (2 s dwell in the model build) outlasts the threshold."""
    samples = out.samples
    keep = []
    for i, smp in enumerate(samples):
        lasted = samples[i + 1].t - smp.t if i + 1 < len(samples) else 1.0
        if smp.screen.ok and lasted >= 1.0:
            keep.append(smp.screen)
    return keep


def report_pages(out):
    """(page count, {page: {(col, row): payload}}) of the final report seen on stable frames.

    The progress pages drawn while phase B runs say "PAGE 1/1"; only the pages of the
    largest page count are the report. Rows of one page seen in several frames are merged,
    so a page that was captured twice is complete even if neither frame alone was."""
    caps = []
    for s in stable_screens(out):
        match = re.search(r"PAGE (\d+)/(\d+)", s.headers[1].check.payload if len(s.headers) > 1 else "")
        if match:
            caps.append((int(match.group(2)), int(match.group(1)),
                         {(r.col, r.row): r.check.payload for r in s.body}))
    if not caps:
        return 0, {}
    final = max(total for total, _, _ in caps)
    pages = {}
    for total, n, rows in caps:
        if total == final:
            pages.setdefault(n, {}).update(rows)
    return final, pages


def page_rows(out):
    """Body payloads of every page of the final report, in reading order: (page, [payload...])."""
    _, pages = report_pages(out)
    return [(n, [pages[n][k] for k in sorted(pages[n])]) for n in sorted(pages)]


def whole_report_seen(out):
    """Every page of the final report was captured on stable frames, and the summary lists
    all nine tests with a verdict (so the page count cannot still be growing)."""
    total, pages = report_pages(out)
    if total < 2 or len(pages) != total:
        return False
    rows = [p for _, body in page_rows(out) for p in body]
    return all(any(re.match(rf"^T0{i} .+ (INFO|PASS|FAIL|UNKNOWN|GO|NO-GO|INCONCL) ", r) for r in rows)
               for i in range(1, 10))


def default_run():
    return scenario("default", "model", FULL, 150, until=whole_report_seen)


class XQemuTests(Base):
    def setUp(self):
        if kit.SKIP_QEMU:
            self.skipTest("UEFI_PROBE_SKIP_QEMU=1")

    def model_run_problems(self, out):
        problems = [f"harness: {out.failure}"] if out.failure else []
        marks = x_markers(out)
        results = {m[1].get("ID"): m[1] for m in marks if m[0] == "RESULT"}
        for tid, verdict in (("1", "INFO"), ("2", "GO"), ("3", "PASS"), ("4", "PASS")):
            if results.get(tid, {}).get("VERDICT") != verdict:
                problems.append(f"T0{tid}: {results.get(tid)} (expected {verdict})")
            if results.get(tid, {}).get("LABEL") != "OBS-MODEL":
                problems.append(f"T0{tid} is not labelled OBS-MODEL: {results.get(tid)}")
        if not any(m[0] == "STICK" and m[1].get("STATE") == "OK" for m in marks):
            problems.append("the stick state marker does not say OK")
        pages = page_rows(out)
        if not pages:
            problems.append("no page of the report was decoded")
        rows = [p for _, body in pages for p in body]
        summary = {m.group(1): (m.group(2), m.group(3)) for m in
                   (re.match(r"^T0(\d) (.+) (INFO|PASS|FAIL|UNKNOWN|GO|NO-GO|INCONCL|NOT-RUN) "
                             r"(OBS-MODEL|OBSERVED|UNIMPL|INFERRED|EXPERIMENTAL|UNKNOWN)$", r) for r in rows)
                   if m}
        want = {"1": "INFO", "2": "GO", "3": "PASS", "4": "PASS", "5": "PASS", "6": "INFO", "7": "INFO",
                "8": "UNKNOWN", "9": "PASS"}
        for tid, verdict in want.items():
            if summary.get(tid, (None, None))[1] != verdict:
                problems.append(f"summary row of T0{tid}: {summary.get(tid)} (expected {verdict})")
        for needle in ("K1 NV2: GO", "COUNTER ADVANCES=YES", "DC ZVA FAULTED=NO", "FAULTS=0"):
            if needle not in rows:
                problems.append(f"row {needle!r} is not on any page")
        for needle in ("NVME CAP=", "GICD @", "SMMU0 @", "RAM TOTAL="):
            if not any(r.startswith(needle) for r in rows):
                problems.append(f"no row starts with {needle!r}")
        # The window policy against the real UEFI memory map of the machine: T05 to T07 each
        # admit every window they were given (nothing refused), although the map lists none
        # of them as MMIO (OBSERVED here, which is why the strict policy is an option).
        maps = [r for r in rows if r.startswith("MAP ")]
        if len(maps) != 3 or any(not r.endswith("REFUSED=0") or "UNLISTED=0" in r for r in maps):
            problems.append(f"the window policy did not admit every window the QEMU map leaves unlisted: {maps}")
        bad = [r.check.payload for s in stable_screens(out) for r in s.bad_rows()]
        if bad:
            problems.append(f"{len(bad)} rows failed their per-row check: {bad[:3]}")
        return problems

    def test_default_run_model_image(self):
        self.check(self.model_run_problems(default_run()))

    def test_default_run_started_from_the_pinned_shell(self):
        """The Shell gives the image a console handle of its own, so the probe's GOP rule must
        find the screen by the framebuffer (SEL=SAME) for the same run to happen as above."""
        out = scenario("shell", "model", FULL, 150, until=whole_report_seen, shell=True)
        problems = kit.shell_launch_problems(out.serial)
        gop = out.marker("GOP")
        if gop is None or gop.fields.get("SEL") != "SAME":
            problems.append(f"the GOP marker does not say SEL=SAME: {gop and gop.fields}")
        problems += self.model_run_problems(out)
        files = {} if out.stick is None else out.stick.files
        names = ("results.txt", "nv2.txt", "nv2.json", "nv2.bin")
        problems += [f"SP11DUMP/RUN01/x2-{n} was not written" for n in names
                     if f"SP11DUMP/RUN01/x2-{n}" not in files]
        pinned = kit.shell_pin_problem(files.get("EFI/BOOT/BOOTAA64.EFI", b""))
        if pinned:
            problems.append(f"EFI/BOOT/BOOTAA64.EFI is {pinned}")
        self.check(problems)

    def test_the_record_files_on_the_stick(self):
        out = default_run()
        if out.stick is None:
            self.fail(f"no stick was recovered: {out.stick_error}")
        files = {p: out.stick.files[p] for p in out.stick.files if "/x2-" in p}
        base = "SP11DUMP/RUN01/"
        problems = []
        for name in ("x2-results.txt", "x2-nv2.txt", "x2-nv2.json", "x2-nv2.bin"):
            if base + name not in files:
                problems.append(f"{base + name} was not written")
        if problems:
            self.check(problems)
        rec_text = nv2_record.parse_text(text_of(files[base + "x2-nv2.txt"]))
        rec_json = nv2_record.parse_json(text_of(files[base + "x2-nv2.json"]))
        rec_bin = nv2_record.unpack(files[base + "x2-nv2.bin"])
        if not rec_text == rec_json == rec_bin:
            problems.append("the three record files disagree")
        if rec_bin.tool != "SP11-X2-MODEL" or rec_bin.evidence != 4 or rec_bin.soc != 1:
            problems.append(f"a model record must be EXPERIMENTAL and say so: {rec_bin}")
        if (rec_bin.vncr_redirect, rec_bin.negative_control_trapped) != (1, 1):
            problems.append("the model core did not produce the pass pair this scenario expects")
        verdict = nv2_record.decide(files[base + "x2-nv2.bin"], rec_bin.soc, rec_bin.midr_el1,
                                    rec_bin.revidr_el1, rec_bin.id_aa64mmfr2_el1, rec_bin.id_aa64mmfr4_el1)
        if verdict != nv2_record.DENY["DENY_EVIDENCE"]:
            problems.append(f"the gate must refuse a model record on its evidence, got {verdict}")
        if "SP11-X2-MODEL" not in text_of(files[base + "x2-results.txt"]) and \
                "OBS-MODEL" not in text_of(files[base + "x2-results.txt"]):
            problems.append("x2-results.txt does not mark the run as a model run")
        self.check(problems)

    def test_the_photographed_record_rows_retype_to_the_stick_record(self):
        out = default_run()
        rows = [p for _, body in page_rows(out) for p in body]
        nv2_rows = []
        for i, p in enumerate(rows):
            if p.startswith("NV2R") or (p.startswith("+") and nv2_rows and rows[i - 1] in nv2_rows):
                nv2_rows.append(p)
        problems = []
        try:
            text = nv2screen.rows_to_text(nv2_rows)
        except nv2screen.ScreenError as exc:
            self.fail(f"the on-screen record could not be retyped: {exc}")
        on_stick = text_of(out.stick.files["SP11DUMP/RUN01/x2-nv2.txt"]) if out.stick is not None else ""
        if text != on_stick:
            problems.append("the record read off the screen differs from the record on the stick")
        self.check(problems)

    def test_el1_guest_runs_nothing(self):
        out = scenario("el1", "model", "virt,gic-version=3", 15, el2=False)
        marks = x_markers(out)
        problems = []
        if not any(m[0] == "SKIP" and "NOT_EL2" in m[2] for m in marks):
            problems.append("no 'SKIP ... NOT_EL2' marker from a guest at EL1")
        if any(m[0] in ("RESULT", "PROGRESS") for m in marks):
            problems.append("tests ran although the image was not at EL2")
        if out.stick is not None and any("/x2-" in p for p in out.stick.files):
            problems.append("result files were written by a run that was not at EL2")
        self.check(problems)

    def test_exception_with_no_guard_ends_in_the_fatal_page(self):
        out = scenario("fatal", "model-fatal", FULL, 40)
        rows = [p for s in stable_screens(out) for p in s.payloads()]
        problems = []
        if not any("FATAL" in r for r in rows):
            problems.append("no page says FATAL")
        if not any(m[0] == "FATAL" for m in x_markers(out)) and not any("FATAL" in r for r in rows):
            problems.append("neither the serial log nor the screen reports the fatal exception")
        self.check(problems)

    def test_exception_before_exitbootservices_ends_in_the_fatal_page(self):
        """Phase A runs with the firmware console possibly invisible: a failed HCR, VNCR or ERET
        step there must still end on a framebuffer page, and must not go on to ExitBootServices
        or to the result files."""
        out = scenario("fatal-pre", "model-fatal-pre", FULL, 15,
                       capture_when=lambda r: "SP11X2 RESULT ID=2" in r.serial)
        rows = [p for s in stable_screens(out) for p in s.payloads()]
        problems = [f"harness: {out.failure}"] if out.failure else []
        if not any("FATAL" in r for r in rows):
            problems.append(f"no page says FATAL; the serial log ends: {tail(out.serial)!r}")
        if re.search(r"\bEBS\b.*STATE=CALLING", out.serial):
            problems.append("the image called ExitBootServices after the fatal exception")
        if out.stick is not None and any("/x2-" in p for p in out.stick.files):
            problems.append("a fatal run wrote result files")
        self.check(problems)
