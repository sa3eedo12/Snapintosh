# SPDX-License-Identifier: MIT
"""ImageTests: structural and instruction-level audits of the built PE images.

probes/uefi/host/peinfo.py parses the PE headers and the llvm-objdump
disassembly independently of the build script. The audits are themselves
tested: each is fed hand-made defective input and compiled snippets that really
contain the forbidden instructions, so a checker that went blind would fail.
VERIFIED for the five variants built from this tree; nothing is run.
"""
from __future__ import annotations

import dataclasses
import json
import re
import struct

from uefi_testkit import (Base, ROOT, WORK, bup, disassembly, images, object_path, objdump_path, run_cmd,
                          text_of, tools, work_dir)
import peinfo  # the testkit import above puts probes/uefi/host on sys.path

BUILD_TAG_PREFIX = b"SP11PROBE-BUILD-ID="  # g_build_tag in probes/uefi/probe_main.c

SNIPPET_FLAGS = ["--target=" + bup.TARGET, "-std=c11", "-O2", "-ffreestanding", "-fno-builtin",
                 "-fno-stack-protector", "-fshort-wchar", "-mno-outline-atomics",
                 "-mno-incremental-linker-compatible"]

PRIVILEGED_SNIPPET = r"""
typedef unsigned long long u64;
void use_msr(u64 v) { __asm__ volatile("msr hcr_el2, %0" :: "r"(v)); }
void use_svc(void) { __asm__ volatile("svc #0"); }
void use_hvc(void) { __asm__ volatile("hvc #0"); }
void use_smc(void) { __asm__ volatile("smc #0"); }
void use_eret(void) { __asm__ volatile("eret"); }
void use_tlbi(void) { __asm__ volatile("tlbi alle2"); }
void use_at(u64 v) { __asm__ volatile("at s1e2r, %0" :: "r"(v)); }
void use_ic(void) { __asm__ volatile("ic iallu"); }
void use_dc_zva(u64 v) { __asm__ volatile("dc zva, %0" :: "r"(v)); }
void use_dc_civac(u64 v) { __asm__ volatile("dc civac, %0" :: "r"(v)); }
void use_bare_brk(void) { __asm__ volatile("brk #0"); }
void use_call_then_trap(void (*f)(void)) { f(); __builtin_trap(); }
u64 read_allowed(void) { u64 v; __asm__ volatile("mrs %0, hcr_el2" : "=r"(v)); return v; }
u64 read_other(void) { u64 v; __asm__ volatile("mrs %0, S3_5_C15_C2_0" : "=r"(v)); return v; }
"""

FLOAT_SNIPPET = r"""
double scale(double a) { return a * a; }
void clear(void *p) { __builtin_memset(p, 0, 64); }
"""

# 15 words, then an object whose .text needs 64-byte alignment: lld-link leaves
# a one-word gap between them, the shape psci.c's aligned(64) stub produces.
FILL_HEAD_SNIPPET = r"""
__attribute__((naked)) void head(void) { __asm__ volatile(".rept 14\n nop\n .endr\n ret\n"); }
"""
FILL_TAIL_SNIPPET = r"""
__attribute__((naked, aligned(64))) void tail(void) { __asm__ volatile("ret\n"); }
"""


def compile_object(tag, source, *extra):
    """Compiles C text from stdin to an object file and returns its path."""
    WORK.mkdir(parents=True, exist_ok=True)
    obj = WORK / f"snippet-{tag}.obj"
    rc, out, err = run_cmd([tools().clang, *SNIPPET_FLAGS, *extra, "-c", "-x", "c", "-", "-o", obj],
                           input=source.encode())
    if rc != 0:
        raise AssertionError(f"the {tag} snippet does not compile:\n" + text_of(out + err))
    return obj


def compile_snippet(tag, source, *extra):
    """Compiles C text from stdin and returns its disassembly (no file with the source exists)."""
    return disassembly(compile_object(tag, source, *extra))


def flagged(problems, kind, mnemonic):
    return any(kind in p and re.search(rf"0x[0-9a-f]+ {mnemonic}\b", p) for p in problems)


class ImageTests(Base):
    def all_images(self):
        return [(name, bup.VARIANTS[name], built.image.read_bytes()) for name, built in images().items()]

    def test_build_id_is_embedded_as_text_in_read_only_data(self):
        """The optimiser folds the id into immediates, so only the tag lets `strings` or a stick audit see it."""
        problems = []
        for name, built in images().items():
            data = built.image.read_bytes()
            tag = BUILD_TAG_PREFIX + built.build_id.encode("ascii") + b"\0"
            if data.count(tag) != 1:
                problems.append(f"{name}: the tag for build id {built.build_id} occurs {data.count(tag)} times")
                continue
            ids = set(re.findall(re.escape(BUILD_TAG_PREFIX) + rb"([0-9A-Fa-f]{12})", data))
            if ids != {built.build_id.encode("ascii")}:
                problems.append(f"{name}: tags in the image carry {sorted(ids)}, expected only {built.build_id}")
            offset = data.index(tag)
            holders = [s for s in peinfo.parse_pe(data).sections if s.raw_offset <= offset < s.raw_offset + s.raw_size]
            if [s.name for s in holders] != [".rdata"] or holders[0].writable or holders[0].executable:
                problems.append(f"{name}: the tag lies in {[s.name for s in holders]}, expected read-only .rdata")
            if json.loads(built.manifest.read_text())["build_id"] != built.build_id:
                problems.append(f"{name}: the manifest names another build id")
        if len({b.build_id for b in images().values()}) != len(images()):
            problems.append("two variants share a build id")
        self.check(problems)

    def test_pe_structure(self):
        problems = []
        for name, variant, data in self.all_images():
            pe = peinfo.parse_pe(data)
            problems += [f"{name}: {p}" for p in peinfo.audit_pe(pe)]
            extra = {s.name for s in pe.sections} - {".text", ".rdata", ".data", ".reloc"}
            if extra:
                problems.append(f"{name}: unexpected sections {sorted(extra)}")
            if (pe.machine, pe.subsystem) != (peinfo.IMAGE_FILE_MACHINE_ARM64, 10):
                problems.append(f"{name}: machine/subsystem {pe.machine:#x}/{pe.subsystem}")
            entry = pe.section_of(pe.entry_rva)
            if entry is None or entry.name != ".text":
                problems.append(f"{name}: the entry point is not in .text")
        self.check(problems)

    def test_pe_audit_rejects_defective_images(self):
        pe = peinfo.parse_pe(images()["default"].image.read_bytes())
        text = pe.section(".text")
        rdata = pe.section(".rdata")
        rwx = [dataclasses.replace(s, characteristics=s.characteristics | peinfo.SCN_MEM_WRITE)
               if s.name == ".text" else s for s in pe.sections]
        overlapped = [dataclasses.replace(s, virtual_address=text.virtual_address)
                      if s.name == ".rdata" else s for s in pe.sections]
        with_dir = lambda i, entry: [entry if n == i else d for n, d in enumerate(pe.directories)]  # noqa: E731
        cases = (
            ("wrong machine", dataclasses.replace(pe, machine=0x8664), "not AArch64"),
            ("wrong subsystem", dataclasses.replace(pe, subsystem=3), "subsystem is 3"),
            ("writable code", dataclasses.replace(pe, sections=rwx), "writable and executable"),
            ("imports", dataclasses.replace(pe, directories=with_dir(peinfo.DIR_IMPORT, (0x1000, 20))),
             "import directory"),
            ("exceptions", dataclasses.replace(pe, directories=with_dir(peinfo.DIR_EXCEPTION, (0x1000, 8))),
             "exception directory"),
            ("no relocations", dataclasses.replace(pe, directories=with_dir(peinfo.DIR_BASERELOC, (0, 0))),
             "no base relocations"),
            ("relocations in code", dataclasses.replace(
                pe, directories=with_dir(peinfo.DIR_BASERELOC, (text.virtual_address, 8))),
             "outside a read-only section"),
            ("entry in no section", dataclasses.replace(pe, entry_rva=0), "entry point lies in no section"),
            ("entry in data", dataclasses.replace(pe, entry_rva=rdata.virtual_address), "not R+X"),
            ("odd alignment", dataclasses.replace(pe, section_alignment=0x1800), "power of two"),
            ("overlapping sections", dataclasses.replace(pe, sections=overlapped), "overlap"),
        )
        problems = []
        for label, defective, fragment in cases:
            found = peinfo.audit_pe(defective)
            if not any(fragment in p for p in found):
                problems.append(f"{label}: expected {fragment!r}, got {found}")
        if not any("bytes in memory" in p for p in peinfo.audit_pe(pe, max_image_bytes=1024)):
            problems.append("an image above the size limit was accepted")
        data = images()["default"].image.read_bytes()
        pe_off = struct.unpack_from("<I", data, 0x3C)[0]
        flipped = bytearray(data)
        flipped[pe_off + 4:pe_off + 6] = struct.pack("<H", 0x8664)
        if not peinfo.audit_pe(peinfo.parse_pe(bytes(flipped))):
            problems.append("a machine type patched into the raw bytes was accepted")
        for label, broken in (("a truncated file", data[:0x100]), ("no MZ", b"XX" + data[2:]),
                              ("no PE signature", data[:pe_off] + b"XX" + data[pe_off + 2:])):
            try:
                peinfo.parse_pe(broken)
                problems.append(f"{label} was parsed")
            except peinfo.PeError:
                pass
        self.check(problems)

    def test_instruction_audit(self):
        problems = []
        for name, variant, _ in self.all_images():
            insns = disassembly(images()[name].image)
            calls = [i for i in insns if i.mnemonic in ("hvc", "smc")]
            problems += [f"{name}: {p}" for p in peinfo.audit_instructions(insns, psci=variant.psci)]
            if bool(calls) != variant.psci:
                problems.append(f"{name}: {len(calls)} hvc/smc instructions in a "
                                f"{'psci' if variant.psci else 'non-psci'} image")
            hvcs = [i for i in insns if i.mnemonic == "hvc"]
            if hvcs:
                problems.append(f"{name}: {len(hvcs)} hvc instruction(s); an HVC at EL2 is taken by "
                                f"a vector no probe image installs")
            if variant.psci:
                strict = peinfo.audit_instructions(insns, psci=False)
                if len(strict) != len(calls):
                    problems.append(f"{name}: {len(strict)} problems without the PSCI allowance, "
                                    f"expected one per hvc/smc ({len(calls)})")
            missing = set(peinfo.SYSREGS) - peinfo.registers_read(insns)
            if missing:
                problems.append(f"{name}: never reads {sorted(missing)}")
        self.check(problems)

    def test_instruction_audit_classifies_synthetic_instructions(self):
        ins = peinfo.Insn
        clean = [ins(0, "mrs", "x0, HCR_EL2"), ins(4, "mrs", "x1, S3_4_C1_C1_0"),
                 ins(8, "dc", "civac, x0"), ins(12, "bl", "0x40 <f>"), ins(16, "brk", "#0xf003"),
                 ins(24, "smc", "#0")]
        problems = []
        if peinfo.audit_instructions(clean, psci=True):
            problems.append(f"false alarm: {peinfo.audit_instructions(clean, psci=True)}")
        for label, insn, fragment in (
                ("unlisted register", ins(0, "mrs", "x0, S3_5_C15_C2_0"), "outside the probe's table"),
                ("a write", ins(0, "msr", "HCR_EL2, x0"), "forbidden instruction"),
                ("tlbi", ins(0, "tlbi", "alle2"), "forbidden instruction"),
                ("hvc, even with PSCI allowed", ins(0, "hvc", "#0"), "hvc in a probe image"),
                ("hvc with an immediate", ins(0, "hvc", "#1"), "hvc in a probe image"),
                ("smc with an immediate", ins(0, "smc", "#7"), "non-zero immediate"),
                ("dc zva", ins(0, "dc", "zva, x0"), "cache maintenance"),
                ("a lone brk", ins(0, "brk", "#0"), "brk that does not follow a call"),
                ("raw sys", ins(0, "sys", "#0, c7, c5, #0"), "raw sys"),
                ("scalar FP", ins(0, "fadd", "d0, d1, d2"), "FP/SIMD"),
                ("vector move", ins(0, "movi", "v0.2d, #0"), "FP/SIMD"),
                ("a q register", ins(0, "stp", "q0, q1, [x0]"), "register operand")):
            found = peinfo.audit_instructions([insn], psci=True)
            if not any(fragment in p for p in found):
                problems.append(f"{label}: expected {fragment!r}, got {found}")
        if not any("hvc in a probe image" in p
                   for p in peinfo.audit_instructions([ins(0, "hvc", "#0")], psci=False)):
            problems.append("hvc accepted in an image without PSCI")
        if not any("must not issue PSCI" in p
                   for p in peinfo.audit_instructions([ins(0, "smc", "#0")], psci=False)):
            problems.append("smc accepted in an image without PSCI")
        self.assertEqual(peinfo.registers_read([ins(0, "mrs", "x1, S3_4_C1_C1_0"),
                                                ins(4, "mrs", "x0, S3_5_C15_C2_0")]), {"HCR_EL2"})
        self.check(problems)

    def test_instruction_audit_excuses_only_alignment_fill(self):
        ins = peinfo.Insn
        zero = lambda addr: ins(addr, "udf", "#0x0")  # noqa: E731
        ret = lambda addr: ins(addr, "ret", "")  # noqa: E731
        body = lambda addr: ins(addr, "add", "x0, x0, #1")  # noqa: E731
        longest = [ret(0x1000), *[zero(0x1004 + 4 * k) for k in range(15)], body(0x1040)]
        too_long = [ret(0x103c), *[zero(0x1040 + 4 * k) for k in range(16)], body(0x1080)]
        accepted = (
            ("one word before an 8-byte boundary", [ret(0x1000), zero(0x1004), body(0x1008)], {1}),
            ("two words before a 16-byte boundary", [ret(0x1004), zero(0x1008), zero(0x100c), body(0x1010)], {1, 2}),
            ("a fill after a tail call", [ins(0x1000, "b", "0x2000 <f>"), zero(0x1004), body(0x1008)], {1}),
            ("the longest fill, 60 bytes up to 64", longest, set(range(1, 16))),
        )
        problems = []
        for label, insns, expected in accepted:
            if peinfo.alignment_fill(insns) != expected or peinfo.audit_instructions(insns, psci=False):
                problems.append(f"{label}: fill {sorted(peinfo.alignment_fill(insns))}, "
                                f"audit {peinfo.audit_instructions(insns, psci=False)}")
        hint = "a zero word that is not alignment fill"
        rejected = (
            ("a zero word inside a function", [body(0x1000), zero(0x1004), body(0x1008)], 1, True),
            ("a fill that ends off the boundary", [ret(0x1004), zero(0x1008), body(0x100c)], 1, True),
            ("a zero word with no predecessor", [zero(0x1004), body(0x1008)], 1, True),
            ("a predecessor that is not adjacent", [ret(0x0ff8), zero(0x1004), body(0x1008)], 1, True),
            ("a fill of 16 words, 64 bytes", too_long, 16, True),
            ("udf with an immediate", [ret(0x1000), ins(0x1004, "udf", "#0x1"), body(0x1008)], 1, False),
        )
        for label, insns, count, hinted in rejected:
            found = peinfo.audit_instructions(insns, psci=False)
            if len(found) != count or not all("forbidden instruction" in p for p in found):
                problems.append(f"{label}: expected {count} forbidden-instruction problems, got {found}")
            elif any(hint in p for p in found) != hinted:
                problems.append(f"{label}: the 'not alignment fill' hint is {'missing' if hinted else 'misplaced'}")
        hidden = peinfo.audit_instructions([ret(0x1000), zero(0x1004), ins(0x1008, "msr", "HCR_EL2, x0")], psci=False)
        if len(hidden) != 1 or "msr" not in hidden[0]:
            problems.append(f"a fill hid or replaced the msr that follows it: {hidden}")
        self.check(problems)

    def test_audit_accepts_the_linker_fill_in_front_of_an_aligned_object(self):
        head = compile_object("fill-head", FILL_HEAD_SNIPPET)
        tail = compile_object("fill-tail", FILL_TAIL_SNIPPET)
        image = work_dir("fill-link") / "fill.efi"
        rc, out, err = run_cmd([tools().lld_link, "/nologo", "/subsystem:efi_application", "/entry:head",
                                "/nodefaultlib", "/machine:arm64", "/Brepro", f"/out:{image}", head, tail])
        self.assertEqual(rc, 0, text_of(out + err))
        insns = peinfo.disassemble(objdump_path(), image)
        words = [i for i in insns if i.mnemonic == "udf"]
        self.assertEqual([i.operands for i in words], ["#0x0"],
                         "the fixture no longer leaves a one-word gap, so this test would prove nothing")
        self.assertEqual(peinfo.alignment_fill(insns), {insns.index(words[0])})
        self.check(peinfo.audit_instructions(insns, psci=False))

    def test_audit_sees_privileged_instructions_in_real_disassembly(self):
        insns = compile_snippet("privileged", PRIVILEGED_SNIPPET, "-mgeneral-regs-only")
        strict = peinfo.audit_instructions(insns, psci=False)
        loose = peinfo.audit_instructions(insns, psci=True)
        problems = [f"{m} was not flagged as forbidden" for m in ("msr", "svc", "eret", "tlbi", "at", "ic")
                    if not flagged(strict, "forbidden instruction", m)]
        problems += [f"{m} was not flagged without the PSCI allowance" for m in ("smc",)
                     if not flagged(strict, "must not issue PSCI", m)]
        problems += [f"{m} was flagged although PSCI is allowed" for m in ("smc",)
                     if any(re.search(rf"0x[0-9a-f]+ {m}\b", p) for p in loose)]
        # hvc is refused in every image, PSCI or not.
        problems += [f"hvc was not flagged {label}" for label, found in (("with PSCI allowed", loose),
                                                                         ("without PSCI", strict))
                     if not flagged(found, "hvc in a probe image", "hvc")]
        if not flagged(strict, "cache maintenance", "dc"):
            problems.append("dc zva was not flagged")
        if sum("cache maintenance" in p for p in strict) != 1:
            problems.append("dc civac must be the one allowed cache operation")
        if sum("brk that does not follow a call" in p for p in strict) != 1:
            problems.append("exactly the bare brk should be flagged, not the one after a call")
        if sum("outside the probe's table" in p for p in strict) != 1:
            problems.append("exactly the unlisted register should be flagged; HCR_EL2 is allowed")
        self.check(problems, "\n".join(strict))

    def test_audit_sees_floating_point_in_real_disassembly(self):
        found = peinfo.audit_instructions(compile_snippet("float", FLOAT_SNIPPET), psci=False)
        problems = [f"no {kind} problem reported" for kind in ("FP/SIMD-class mnemonic", "register operand")
                    if not any(kind in p for p in found)]
        self.check(problems, "\n".join(found))

    def test_memory_forwarders_are_leaves(self):
        logic = disassembly(object_path("default", "logic"), relocations=True)
        mem = disassembly(object_path("default", "mem"), relocations=True)
        self.check(peinfo.audit_mem_objects(logic, mem))

    def test_memory_audit_rejects_recursion_and_missing_functions(self):
        ins = peinfo.Insn
        forwarders = [ins(i, "b", "", func=name, reloc=target)
                      for i, (name, target) in enumerate(peinfo.MEM_FORWARDERS.items())]
        leaves = [ins(100 + i, "ret", "", func=name) for i, name in enumerate(peinfo.MEM_LEAVES)]
        self.assertEqual(peinfo.audit_mem_objects(leaves, forwarders), [])
        cases = (
            ("a forwarder that calls itself", leaves, [*forwarders, ins(50, "bl", "", func="memcpy", reloc="memcpy")],
             "memcpy should only branch to probe_memcpy"),
            ("a forwarder to the wrong target", leaves,
             [i for i in forwarders if i.func != "memset"] + [ins(60, "b", "", func="memset", reloc="probe_memcpy")],
             "memset should only branch"),
            ("a missing forwarder", leaves, [i for i in forwarders if i.func != "memcmp"],
             "memcmp should only branch"),
            ("a leaf that calls out", [*leaves, ins(200, "bl", "", func="probe_memcpy", reloc="memcpy")],
             forwarders, "probe_memcpy calls"),
            ("a missing leaf", [i for i in leaves if i.func != "probe_strlen"], forwarders,
             "probe_strlen not found"),
        )
        problems = []
        for label, logic_insns, mem_insns, fragment in cases:
            found = peinfo.audit_mem_objects(logic_insns, mem_insns)
            if not any(fragment in p for p in found):
                problems.append(f"{label}: expected {fragment!r}, got {found}")
        self.check(problems)

    def test_inline_assembly_stays_in_two_files(self):
        pattern = re.compile(r"\b(__asm__|__asm|asm)\b")
        users = sorted(p.name for p in (ROOT / "probes" / "uefi").iterdir()
                       if p.suffix in (".c", ".h") and pattern.search(p.read_text(encoding="utf-8")))
        self.assertEqual(users, ["cpu_aarch64.c", "psci.c"])
