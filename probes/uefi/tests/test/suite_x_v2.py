# SPDX-License-Identifier: MIT
"""Suites of the second version of the SP11 X2 tests image: the order of the tests after the
bounce, T03 without its CNTPOFF_EL2 read, T05 with the redistributor frames taken from the GICC
structures, and T02's REGIME row (probes/uefi/tests/hook.c, t_misc.c, t_gic.c, acpix.c, nv2.c).

    XV2SourceTests   the order list as the source states it, and the source audit's pin on the one
                     place CNTPOFF_EL2 may be read, each against deliberately defective sources
    XV2BuildTests    --cntpoff: refused for every image for the machine, and what the emulator
                     image that has it says about itself
    XV2MutantTests   seeded bugs in each of the four changes, which the native C tests must reject
                     (and an unmodified copy, which they must not, so that a mutant cannot fail for
                     the way it was built)
    XV2QemuTests     the model image under QEMU + EDK2: the console order of phase A, T03's row, the
                     REGIME row at the end of T02, and the redistributor layout the QEMU machine has

After ExitBootServices the image has no console (con_puts does nothing once g_post is set), so under
QEMU only phase A's tests print RUN markers, and the order of the tests after the bounce has no
channel the harness can read: the footer that names the running test changes within milliseconds,
the screen is sampled twice a second, and the samples of a model run held only the final "RESET
DISABLED - HOLDING" footer (OBSERVED while writing these tests). That order is pinned natively
(test_x_hook_post_order runs the real hook.c over models and checks the exact sequence of scope
entries, and a freeze at T03) and in the source (XV2SourceTests).

None of this has run on the Surface Pro 11. A PASS here says the code behaves as specified against
host models and a standard firmware, nothing about the unit.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import shutil
import subprocess
import sys
from dataclasses import replace

import audit
import peinfo
import suite_x as sx
import suite_x_ebs as sxe
import uefi_testkit as kit
from uefi_testkit import Base, ROOT, run_cmd, tail, text_of

HOOK_C = "probes/uefi/tests/hook.c"
T_MISC = "probes/uefi/tests/t_misc.c"
T_GIC = "probes/uefi/tests/t_gic.c"
ACPIX = "probes/uefi/tests/acpix.c"
NV2_C = "probes/uefi/tests/nv2.c"
SYSSTATE = "probes/uefi/tests/sysstate.c"
POST_ORDER = ["T_ID", "T_NV2", "T_ZVA", "T_DRAM", "T_GIC", "T_SMMU", "T_ADSP", "T_TIMER", "T_PCIE"]
PHASE_A_ORDER = ["T_ID", "T_TIMER", "T_ZVA", "T_NV2"]
TEST_IDS = {"T_ID": 1, "T_NV2": 2, "T_TIMER": 3, "T_ZVA": 4, "T_GIC": 5, "T_PCIE": 6, "T_SMMU": 7,
            "T_ADSP": 8, "T_DRAM": 9}
REGIME_ROW = re.compile(r"^REGIME ([0-9A-F]{16}) E2H=([01]) TGE=([01])$")
ORDER_LIST = re.compile(r"static const int order_post\[\] = \{([^}]*)\};")
PHASE_A_LIST = re.compile(r"static const int order_a\[\] = \{([^}]*)\};")


def names_in(text):
    return [n.strip() for n in text.split(",") if n.strip()]


class XV2SourceTests(Base):
    def test_the_post_bounce_order_is_the_documented_one(self):
        hook = (ROOT / HOOK_C).read_text(encoding="utf-8")
        problems = []
        found = ORDER_LIST.findall(hook)
        if len(found) != 1 or names_in(found[0]) != POST_ORDER:
            problems.append(f"order_post is {[names_in(f) for f in found]}, expected {POST_ORDER}")
        phase_a = PHASE_A_LIST.findall(hook)
        if len(phase_a) != 1 or names_in(phase_a[0]) != PHASE_A_ORDER:
            problems.append(f"phase A's order_a is {[names_in(f) for f in phase_a]}, expected {PHASE_A_ORDER}: "
                            f"phase A is untouched")
        post = hook[hook.index("void tests_hook_post_ebs"):]
        if "order_a" in post or "order_b" in post:
            problems.append("tests_hook_post_ebs still names an order_a or order_b: there is one list")
        before = hook[:hook.index("static const int order_post")]
        comment = before[before.rindex("/*"):]
        for needle in ("OBSERVED", "INFERRED", "T03", "T06", "T02", "must not wait behind"):
            if needle not in comment:
                problems.append(f"the comment above order_post does not mention {needle!r}")
        self.check(problems)

    def test_the_post_bounce_order_follows_the_rules_it_was_made_for(self):
        ids = [TEST_IDS[n] for n in POST_ORDER]
        pos = {n: POST_ORDER.index(n) for n in POST_ORDER}
        problems = []
        if sorted(ids) != list(range(1, 10)):
            problems.append("the list is not a permutation of the nine tests")
        if POST_ORDER[:2] != ["T_ID", "T_NV2"]:
            problems.append("T01 and T02 do not come first")
        if POST_ORDER[-1] != "T_PCIE" or POST_ORDER[-2] != "T_TIMER":
            problems.append("T06 is not last, or T03 is not second to last")
        for waiting in ("T_ZVA", "T_DRAM", "T_GIC", "T_SMMU", "T_ADSP"):
            if pos[waiting] > pos["T_TIMER"]:
                problems.append(f"{waiting} has no current result on the unit and waits behind T03")
        self.check(problems)

    def test_the_cntpoff_pin_passes_on_the_real_tree(self):
        self.check(audit.audit_cntpoff_read(ROOT) + audit.audit_sources(ROOT))

    def test_the_cntpoff_pin_rejects_defective_sources(self):
        read_here = "        if (c->read_cntpoff) {\n"
        cases = (
            ("a second read, not behind the flag", T_MISC,
             'tests_row_text(r, "CORES: BOOT CPU ONLY");',
             'tests_row_text(r, "CORES: BOOT CPU ONLY");\n    (void)rd(c, TR_CNTPOFF_EL2, &poff);',
             ("2 times", "not directly behind")),
            ("the read no longer behind the flag", T_MISC, read_here, "        if (1) {\n",
             ("not directly behind",)),
            ("a read in another file", T_GIC, "tests_init_result(r, T_GIC);",
             "tests_init_result(r, T_GIC);\n    (void)c->bus->rd_sys(c->bus->ctx, TR_CNTPOFF_EL2, NULL);",
             ("outside tests/t_misc.c",)),
            ("the flag set in another file", SYSSTATE, "int tests_sysstate_cleanup_failed(void)\n{",
             "void tests_bad(tests_ctx *c)\n{\n    c->read_cntpoff = 1;\n}\n\nint tests_sysstate_cleanup_failed(void)\n{",
             ("set outside tests/hook.c",)),
            ("the flag set to something else", HOOK_C, "g_tc.read_cntpoff = TESTS_READ_CNTPOFF;",
             "g_tc.read_cntpoff = 1;", ("other than TESTS_READ_CNTPOFF",)),
            ("the option on by default", HOOK_C, "#define TESTS_READ_CNTPOFF 0", "#define TESTS_READ_CNTPOFF 1",
             ("not defined to 0 by default",)),
        )
        problems = []
        for name, relative, old, new, wanted in cases:
            tree = sxe.copy_tree("cntpoff-" + re.sub(r"\W+", "-", name), [(relative, old, new)])
            found = audit.audit_cntpoff_read(tree)
            if not found or any(not any(w in p for p in found) for w in wanted):
                problems.append(f"{name}: the audit said {found or 'nothing'}, expected {wanted}")
        # A mention in a comment or a string is not a read.
        quiet = sxe.copy_tree("cntpoff-quiet", [(SYSSTATE, "int tests_sysstate_cleanup_failed(void)\n{",
                                                  '/* TR_CNTPOFF_EL2 and read_cntpoff = 1 */\nint tests_sysstate_cleanup_failed(void)\n{')])
        if audit.audit_cntpoff_read(quiet):
            problems.append(f"a comment that names the register was reported: {audit.audit_cntpoff_read(quiet)}")
        self.check(problems)


def build_cmd(*args, out):
    return [sys.executable, "-B", "tools/build-uefi-tests.py", "--out", out, *args]


class XV2BuildTests(Base):
    def test_the_option_is_refused_for_every_image_for_the_machine(self):
        problems = []
        out = kit.WORK / "xv2-refused"
        for extra in ([], ["--variant", "hardware"], ["--variant", "declared"], ["--variant", "all"],
                      ["--variant", "model", "--variant", "declared"]):
            rc, _, err = run_cmd(build_cmd("--soc", "x1e80100", "--cntpoff", *extra, out=out))
            text = text_of(err)
            if rc != 2 or "REFUSED" not in text or "--cntpoff" not in text or "q1n1" not in text:
                problems.append(f"{extra}: exit {rc}, stderr {text.strip()[:160]!r}")
        rc, _, err = run_cmd(build_cmd("--soc", "x1e80100", "--cntpoff", "--print-build-id", out=out))
        if rc != 2:
            problems.append(f"--print-build-id with --cntpoff and the hardware image exited {rc}, expected 2")
        if out.exists() and any(out.iterdir()):
            problems.append("a refused build still wrote output")
        self.check(problems)

    def test_the_option_changes_the_build_id_of_an_emulator_image(self):
        problems, ids = [], {}
        for flag in ([], ["--cntpoff"]):
            rc, out, err = run_cmd(build_cmd("--print-build-id", "--variant", "model", *flag,
                                             out=kit.WORK / "xv2-ids"))
            ids[bool(flag)] = text_of(out).strip()
            if rc != 0:
                problems.append(f"--print-build-id {flag}: exit {rc}, {text_of(err)!r}")
        if ids[False] == ids[True] or not ids[False].startswith("model: "):
            problems.append(f"the build ids are {ids}: the option does not change the id")
        self.check(problems)

    def test_machine_images_never_carry_the_option_and_say_so(self):
        problems = []
        images = sx.x_images()
        for name in ("hardware", "declared"):
            manifest = json.loads(images[name].manifest.read_text())
            if any(d.startswith("TESTS_READ_CNTPOFF") for d in manifest["defines"]):
                problems.append(f"{name} defines TESTS_READ_CNTPOFF: {manifest['defines']}")
            if any(d.startswith("TESTS_READ_CNTPOFF") for d in sx.build_tool().VARIANTS[name].defines):
                problems.append(f"the variant {name} carries the option in the build tool itself")
        for name, built in images.items():
            data = built.image.read_bytes()
            if b"SP11X2-CNTPOFF=NOT-READ" not in data or b"SP11X2-CNTPOFF=READ" in data:
                problems.append(f"{name} does not say, in bytes `strings` shows, that T03 does not read CNTPOFF_EL2")
        self.check(problems)

    def test_the_emulator_image_with_the_option_reads_and_says_so(self):
        bt = sx.build_tool()
        model = bt.VARIANTS["model"]
        built = bt.build_variant(replace(model, defines=model.defines + ("TESTS_READ_CNTPOFF=1",)), "x1e80100",
                                 bt.DEFAULT_DESCRIPTOR, kit.work_dir("xv2-cntpoff"), kit.tools())
        data = built.image.read_bytes()
        problems = []
        if b"SP11X2-CNTPOFF=READ" not in data or b"SP11X2-CNTPOFF=NOT-READ" in data:
            problems.append("the image does not say that T03 reads CNTPOFF_EL2")
        if "TESTS_READ_CNTPOFF=1" not in json.loads(built.manifest.read_text())["defines"]:
            problems.append("the manifest does not record the option")
        if built.build_id == sx.x_images()["model"].build_id:
            problems.append("the option does not change the build id")
        try:
            pe = peinfo.parse_pe(data)
            problems += [f"PE: {p}" for p in peinfo.audit_pe(pe)]
        except peinfo.PeError as exc:
            problems.append(f"PE: {exc}")
        problems += [f"instructions: {p}" for p in audit.audit_instructions(kit.disassembly(built.image))]
        self.check(problems)


# Each entry seeds one defect into one of the four changes; the native C tests must then fail. A
# test that cannot fail proves nothing (project convention, as in tools/test-sp11-ebs-sweep-model.py).
MUTANTS = (
    # T03 without its CNTPOFF_EL2 read
    ("T03 reads CNTPOFF_EL2 by default", T_MISC, "        if (c->read_cntpoff) {\n", "        if (1) {\n"),
    ("the build defaults to reading CNTPOFF_EL2", HOOK_C, "#define TESTS_READ_CNTPOFF 0",
     "#define TESTS_READ_CNTPOFF 1"),
    ("the hook hands T03 the read whatever the build says", HOOK_C, "g_tc.read_cntpoff = TESTS_READ_CNTPOFF;",
     "g_tc.read_cntpoff = 1;"),
    ("the NOT READ row says something else", T_MISC, '"CNTPOFF_EL2 NOT READ (ECV="', '"CNTPOFF_EL2 N/A (ECV="'),
    ("a skipped read fails the verdict", T_MISC, "!window || poff_fault)", "!window || poff_fault || poff_skipped)"),
    ("the statement still says only writes", T_MISC, '#define TIMER_NOT_PROVES "N: OTHER CORES OR ANY CNTPOFF ACCESS"',
     '#define TIMER_NOT_PROVES "N: OTHER CORES OR CNTPOFF WRITES"'),
    ("a build that reads keeps the not-read statement", T_MISC,
     "    if (c->read_cntpoff)\n        r->not_proves = TIMER_NOT_PROVES_READ;\n", ""),
    # The order after the bounce
    ("T06 is not last", HOOK_C, "T_ADSP, T_TIMER, T_PCIE};", "T_ADSP, T_PCIE, T_TIMER};"),
    ("T03 runs before T04", HOOK_C,
     "{T_ID, T_NV2, T_ZVA, T_DRAM, T_GIC, T_SMMU, T_ADSP, T_TIMER, T_PCIE}",
     "{T_ID, T_NV2, T_TIMER, T_ZVA, T_DRAM, T_GIC, T_SMMU, T_ADSP, T_PCIE}"),
    ("T02 is not second", HOOK_C, "{T_ID, T_NV2, T_ZVA,", "{T_ID, T_ZVA, T_NV2,"),
    ("the earlier order is back", HOOK_C,
     "{T_ID, T_NV2, T_ZVA, T_DRAM, T_GIC, T_SMMU, T_ADSP, T_TIMER, T_PCIE}",
     "{T_ID, T_NV2, T_TIMER, T_ZVA, T_DRAM, T_GIC, T_SMMU, T_PCIE, T_ADSP}"),
    ("a test phase A ran is run again", HOOK_C,
     "        if (g_res[order_post[i]].phase != 0)\n            continue;\n", ""),
    ("a test is missing from the list", HOOK_C, "T_DRAM, T_GIC, T_SMMU,", "T_GIC, T_SMMU,"),
    # T05 with the redistributor frames of the GICC structures
    ("a GICC base is off by one frame", ACPIX, "out[at].base = m->gicc_gicr[i];",
     "out[at].base = m->gicc_gicr[i] + TESTS_GICC_FRAME_BYTES;"),
    ("the frames are not put in UID order", ACPIX, "while (at > 0 && m->uid[out[at - 1].cpu] > m->uid[i]) {",
     "while (0) {"),
    ("a dropped discovery range lets frames be derived", ACPIX, "m->gicd_version < 3u || m->n_gicr_seen != 0",
     "m->gicd_version < 3u || m->n_gicr != 0"),
    ("a discovery range that was seen is not counted", ACPIX, "            out->n_gicr_seen++;\n", ""),
    ("frames are derived from a GICv2 MADT", ACPIX, "m->gicd_version < 3u", "m->gicd_version < 2u"),
    ("the match is made after a frame stopped the read", T_GIC, "match_ok = complete && !(per_cpu && w.stopped);",
     "match_ok = complete;"),
    ("a stopped read can end in PASS", T_GIC, "    else if (arch >= 3u && per_cpu && (w.stopped || w.frames != n_frames))",
     "    else if (0)"),
    ("a dropped GICC structure is not a missing frame", T_GIC, "(!per_cpu || madt.truncated == 0)", "1"),
    ("the count of refusals not listed is wrong", T_GIC, "probe_sb_dec(sb, frames_refused - MAX_FRAME_REFUSAL_ROWS);",
     "probe_sb_dec(sb, frames_refused);"),
    ("a base that differs from its frame is not counted", T_GIC,
     "if (madt.gicc_gicr[i] != 0 && madt.gicc_gicr[i] != w.addr[f])", "if (0)"),
    ("the wake row can overflow", T_GIC, "    if (sb.len <= PROBE_LINE_PAYLOAD_MAX) {", "    if (1) {"),
    # T02's REGIME row
    ("the REGIME row prints the stub's HCR_EL2", NV2_C, "probe_sb_hex(sb, o->entry_hcr, 16);",
     "probe_sb_hex(sb, tests_hcr_for(o->entry_hcr, 1, 1), 16);"),
    ("HCR_EL2 is read after the control run", NV2_C,
     "    run_control(ops, o);\n    if (!o->mech_fault && cls == NVC_NV_NV2)",
     "    run_control(ops, o);\n    if (ops->real_hcr != NULL)\n        o->have_entry_hcr = ops->real_hcr(ops->ctx, &o->entry_hcr) == 0;\n"
     "    if (!o->mech_fault && cls == NVC_NV_NV2)"),
    ("the REGIME row shows TGE as E2H", NV2_C, "probe_sb_dec(sb, (o->entry_hcr & TESTS_HCR_E2H) != 0);",
     "probe_sb_dec(sb, (o->entry_hcr & TESTS_HCR_TGE) != 0);"),
    ("the REGIME row is not added", T_MISC, "    tests_nv2_fill_regime(obs, r);\n", "    (void)obs;\n"),
    ("the regime reaches the NV2 record", NV2_C, "    rec->build_id = build_id;",
     "    rec->build_id = build_id ^ (uint32_t)o->entry_hcr;"),
)


def mutated_exe(tag, relative, old, new):
    """The X2 native test built from a copy of probes/uefi with one text replaced; (exe, why not)."""
    tree = kit.work_dir(f"xv2-{tag}")
    shutil.copytree(ROOT / "probes" / "uefi", tree / "probes" / "uefi",
                    ignore=shutil.ignore_patterns("__pycache__", "host"))
    (tree / "tools").mkdir()
    shutil.copy2(ROOT / "tools" / "test-uefi-tests-logic.c", tree / "tools")
    if relative:
        path = tree / relative
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return None, f"the text to change occurs {text.count(old)} times in {relative}, not once"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    argv = sx.native_command()
    argv[0] = shutil.which("clang") or kit.tools().clang
    exe = tree / "native-under-test"
    argv[argv.index("-o") + 1] = str(exe)
    rc, out, err = run_cmd(argv, cwd=tree)
    if rc != 0:
        return None, f"the mutant does not compile:\n{tail(text_of(out + err), 12)}"
    return exe, ""


def native_problems(exe):
    try:
        rc, out, err = run_cmd([exe], env=kit.asan_env(), timeout=120)
    except subprocess.TimeoutExpired:
        return ["the tests do not finish: they hang"]
    return sx.native_output_problems(rc, text_of(out))


class XV2MutantTests(Base):
    def test_an_unmodified_copy_passes(self):
        """The way the mutants are built does not itself break the tests."""
        exe, why = mutated_exe("control", None, "", "")
        self.assertIsNotNone(exe, why)
        self.check(native_problems(exe))

    def test_seeded_bugs_in_the_four_changes_are_rejected(self):
        def run(indexed):
            index, (name, relative, old, new) = indexed
            exe, why = mutated_exe(f"m{index:02d}", relative, old, new)
            if exe is None:
                return name, why
            return name, None if native_problems(exe) else "the C tests still pass"

        problems = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for name, problem in pool.map(run, enumerate(MUTANTS)):
                if problem:
                    problems.append(f"{name}: {problem}")
        self.check(problems)


# ---- QEMU --------------------------------------------------------------------------------------

def report_rows(out):
    return [p for _, body in sx.page_rows(out) for p in body]


def reported_ecv(rows):
    for row in rows:
        m = re.match(r"^ECV=(\d+) FGT=", row)
        if m:
            return int(m.group(1))
    return None


class XV2QemuTests(Base):
    def setUp(self):
        if kit.SKIP_QEMU:
            self.skipTest("UEFI_PROBE_SKIP_QEMU=1")

    def test_phase_a_keeps_its_order_on_the_console_and_nothing_follows_exitbootservices(self):
        out = sx.default_run()
        ids = [m[1].get("ID") for m in sx.x_markers(out) if m[0] == "RUN"]
        problems = []
        if ids != ["1", "3", "4", "2"]:
            problems.append(f"the RUN markers on the console are tests {ids}, phase A's order is 1, 3, 4, 2")
        calling = re.search(r"\bEBS\b[^\n]*STATE=CALLING", out.serial)
        if calling is None:
            problems.append("no ExitBootServices call was logged")
        elif "SP11X2 RUN" in out.serial[calling.end():]:
            problems.append("a RUN marker follows the ExitBootServices call: the console is gone by then")
        self.check(problems, tail(out.serial, 8))

    def test_t03_says_it_did_not_read_cntpoff_and_still_decides(self):
        rows = report_rows(sx.default_run())
        ecv = reported_ecv(rows)
        problems = []
        if ecv is None:
            problems.append("T01's ECV row is not on any page")
        want = f"CNTPOFF_EL2 NOT READ (ECV={ecv})" if ecv is not None and ecv >= 2 else "CNTPOFF_EL2=N/A (ID ECV<2)"
        if want not in rows:
            problems.append(f"the row {want!r} is not on any page")
        if any(re.match(r"^CNTPOFF_EL2=[0-9A-F]{16}$", r) for r in rows):
            problems.append("T03 shows a CNTPOFF_EL2 value: the default model image read it")
        if "N: OTHER CORES OR ANY CNTPOFF ACCESS" not in rows:
            problems.append("T03's does-not-prove statement is not the one for an image that does not read it")
        summary = [r for r in rows if r.startswith("T03 ")]
        if not summary or " PASS " not in summary[0]:
            problems.append(f"T03's summary row is {summary}, expected PASS")
        self.check(problems, f"ECV={ecv}")

    def test_the_regime_row_closes_t02(self):
        rows = report_rows(sx.default_run())
        problems = []
        found = [i for i, r in enumerate(rows) if r.startswith("REGIME ")]
        if len(found) != 1:
            self.check([f"{len(found)} REGIME rows on the pages, expected one"], "\n".join(rows[-10:]))
        i = found[0]
        m = REGIME_ROW.match(rows[i])
        if m is None:
            problems.append(f"the REGIME row is {rows[i]!r}")
        else:
            hcr = int(m.group(1), 16)
            if (int(m.group(2)), int(m.group(3))) != ((hcr >> 34) & 1, (hcr >> 27) & 1):
                problems.append(f"E2H and TGE do not agree with the value in {rows[i]!r}")
        if not rows[i - 1].startswith("NV2R END"):
            problems.append(f"the row before it is {rows[i - 1]!r}, not the last line of the record")
        if i + 1 >= len(rows) or rows[i + 1] != "P: EL1 ACCESS LANDED IN VNCR PAGE":
            problems.append(f"the row after it is {rows[i + 1:i + 2]!r}, not T02's does-prove statement")
        self.check(problems)

    def test_the_qemu_machine_keeps_the_discovery_layout_for_the_redistributors(self):
        """Its MADT has a GICR discovery range: T05 walks it, as it did before, and does not take
        the per-CPU layout (which only a MADT without a range gets)."""
        rows = report_rows(sx.default_run())
        problems = []
        if not any(re.match(r"^GICR RANGES=\d+ FRAMES=\d+ LAST=Y", r) for r in rows):
            problems.append("T05 did not walk the discovery range of the QEMU machine")
        if any("GICR GICC FRAMES" in r or "GICC GICR" in r for r in rows):
            problems.append("T05 used the per-CPU layout on a MADT that has a discovery range")
        if not any(re.match(r"^T05 .* PASS ", r) for r in rows):
            problems.append("T05 does not pass on the QEMU machine")
        self.check(problems)

    def test_the_emulator_image_with_the_option_reads_cntpoff_under_qemu(self):
        bt = sx.build_tool()
        model = bt.VARIANTS["model"]
        built = bt.build_variant(replace(model, defines=model.defines + ("TESTS_READ_CNTPOFF=1",)), "x1e80100",
                                 bt.DEFAULT_DESCRIPTOR, kit.work_dir("xv2-qemu-cntpoff"), kit.tools())
        out = sx.scenario("v2-cntpoff", built.image, sx.FULL, 150, until=sx.whole_report_seen)
        rows = report_rows(out)
        ecv = reported_ecv(rows)
        if ecv is None or ecv < 2:
            self.skipTest(f"QEMU's CPU reports ECV={ecv}: the CNTPOFF_EL2 read is not reached here, "
                          f"only by the host tests")
        problems = []
        if not any(re.match(r"^CNTPOFF_EL2=[0-9A-F]{16}$", r) for r in rows):
            problems.append("T03 shows no CNTPOFF_EL2 value")
        if any("NOT READ" in r for r in rows):
            problems.append("T03 says it did not read the register")
        if "N: OTHER CORES OR CNTPOFF WRITES" not in rows:
            problems.append("T03's does-not-prove statement is not the one for an image that reads it")
        summary = [r for r in rows if r.startswith("T03 ")]
        if not summary or " PASS " not in summary[0]:
            problems.append(f"T03's summary row is {summary}, expected PASS")
        self.check(problems, f"ECV={ecv}")
