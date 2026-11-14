# SPDX-License-Identifier: MIT
"""Instruction and source audit of the SP11 X2 tests image.

The X2 image is read-only by construction except for a short list of EL2
register writes, the EL1 stub and one DC ZVA. This audit is the independent
check of that claim, made from the built image's disassembly and from the
source text, so a later change that adds a write (an `msr` to a register not on
the list, a store through a device pointer, an `smc`, a TLB or cache operation
other than the two allowed) fails a test instead of reaching a laptop.

It shares no code with the image. The allow-lists below are the specification:
probes/uefi/tests/regs.h names the registers the C code may write (every one
saved before and restored after), and vectors_aarch64.c explains the rest.
"""
from __future__ import annotations

import re
from pathlib import Path

# The registers the C state model saves before a test and restores afterwards
# (enum tests_wreg in regs.h), each with the `msr` spellings through which the
# image writes it (llvm prints the architectural name when the target knows it
# and the generic S<op0>_<op1>_C<n>_C<m>_<op2> form otherwise, so both spellings
# are listed). ELR_EL2 and SPSR_EL2 are in the model because every exception the
# guarded reads take and every ERET into the EL1 stub overwrites them: a write
# the audit allows but the model does not restore would leave EL2 changed.
WREG_MSR = {
    "VBAR_EL2": frozenset({"VBAR_EL2"}),
    "HCR_EL2": frozenset({"HCR_EL2"}),
    "VNCR_EL2": frozenset({"VNCR_EL2", "S3_4_C2_C2_0"}),
    "SCTLR_EL1X": frozenset({"SCTLR_EL1", "SCTLR_EL12", "S3_5_C1_C0_0"}),
    "VBAR_EL1X": frozenset({"VBAR_EL1", "VBAR_EL12", "S3_5_C12_C0_0"}),
    "SP_EL1": frozenset({"SP_EL1"}),
    "DAIF": frozenset({"DAIF"}),
    "ELR_EL2": frozenset({"ELR_EL2"}),
    "SPSR_EL2": frozenset({"SPSR_EL2"}),
}
MSR_WRITE_LIST = frozenset().union(*WREG_MSR.values())
MSR_EL1_STUB = frozenset({"TPIDR_EL2"})  # the EL1 stub's own access, the one the test is about
MSR_ALLOWED = MSR_WRITE_LIST | MSR_EL1_STUB
# `msr DAIFSet, #4` / `msr DAIFClr, #4`: unmask and mask SError around one MMIO read.
PSTATE_ALLOWED = {"DAIFSET": {"#0x4"}, "DAIFCLR": {"#0x4"}}
DC_ALLOWED = frozenset({"zva", "civac"})
HVC_STUB = frozenset({0x10, 0x11})
HVC_VECTORS = tuple(range(0x100, 0x110))
FORBIDDEN_ALWAYS = frozenset({"smc", "svc", "ic", "tlbi", "at", "sys", "sysl", "ldxr", "ldaxr",
                              "stxr", "stlxr", "swp", "cas", "ldadd", "ldset", "ldclr", "ldeor"})
ERET_COUNT = 2  # into the EL1 stub, and the exception handler's own return

# Instructions are probes/uefi/host/peinfo.Insn records (addr, mnemonic,
# operands) from the probe's own disassembly parser, so this module has no
# second copy of that parser.


def audit_instructions(insns) -> list:
    """Problems with the privileged instructions of an image; empty when clean."""
    problems = []
    zva = eret = 0
    hvc_seen = {}
    for i in insns:
        mn, ops = i.mnemonic, i.operands
        if mn in FORBIDDEN_ALWAYS:
            problems.append(f"{i.addr:#x}: forbidden instruction {mn} {ops}")
        elif mn == "msr":
            target, _, rest = ops.partition(",")
            target = target.strip().upper()
            if target in PSTATE_ALLOWED:
                if rest.strip() not in PSTATE_ALLOWED[target]:
                    problems.append(f"{i.addr:#x}: msr {ops}: pstate field with an unlisted immediate")
            elif target not in MSR_ALLOWED:
                problems.append(f"{i.addr:#x}: msr to {target}, which is not on the write list")
        elif mn == "dc":
            op = ops.split(",")[0].strip()
            if op not in DC_ALLOWED:
                problems.append(f"{i.addr:#x}: dc {op} is not an allowed cache operation")
            zva += op == "zva"
        elif mn == "hvc":
            imm = int(ops.strip().lstrip("#"), 0)
            if imm not in HVC_STUB and imm not in HVC_VECTORS:
                problems.append(f"{i.addr:#x}: hvc #{imm:#x} is not one of the stub's immediates")
            hvc_seen[imm] = hvc_seen.get(imm, 0) + 1
        elif mn == "eret":
            eret += 1
    if zva != 1:
        problems.append(f"{zva} DC ZVA instructions, expected exactly 1")
    if eret != ERET_COUNT:
        problems.append(f"{eret} ERET instructions, expected {ERET_COUNT}")
    for imm in HVC_VECTORS:
        if hvc_seen.get(imm, 0) != 1:
            problems.append(f"hvc #{imm:#x} appears {hvc_seen.get(imm, 0)} times, expected once")
    for imm in HVC_STUB:
        if hvc_seen.get(imm, 0) != 1:
            problems.append(f"hvc #{imm:#x} appears {hvc_seen.get(imm, 0)} times, expected once")
    if sum(1 for i in insns if i.mnemonic == "msr" and i.operands.upper().startswith("TPIDR_EL2")) != 1:
        problems.append("expected exactly one msr TPIDR_EL2 (the EL1 redirect stub)")
    return problems


def audit_vectors(insns) -> list:
    """The two 2 KiB vector tables: alignment, 16 entries at 0x80 spacing, entry contents."""
    problems = []
    by_addr = {i.addr: i for i in insns}
    el1 = sorted(i.addr for i in insns if i.mnemonic == "hvc" and i.operands.strip() == "#0x100")
    if len(el1) != 1:
        return [f"{len(el1)} EL1 vector tables found, expected 1"]
    base = el1[0]
    if base % 0x800:
        problems.append(f"EL1 vector table at {base:#x} is not 2 KiB aligned")
    for k in range(16):
        ins = by_addr.get(base + 0x80 * k)
        if ins is None or ins.mnemonic != "hvc" or int(ins.operands.strip().lstrip("#"), 0) != 0x100 + k:
            problems.append(f"EL1 vector entry {k} is not `hvc #{0x100 + k:#x}` at +{0x80 * k:#x}")
    heads = sorted(i.addr for i in insns if i.mnemonic == "sub" and re.fullmatch(r"sp, sp, #0x110", i.operands)
                   and i.addr % 0x800 == 0)
    if len(heads) != 1:
        return problems + [f"{len(heads)} EL2 vector tables found, expected 1"]
    base2 = heads[0]
    targets = set()
    for k in range(16):
        a = base2 + 0x80 * k
        parts = [by_addr.get(a + 4 * n) for n in range(4)]
        if any(p is None for p in parts):
            problems.append(f"EL2 vector entry {k} is incomplete")
            continue
        if not (parts[0].mnemonic == "sub" and parts[0].operands == "sp, sp, #0x110"):
            problems.append(f"EL2 vector entry {k} does not open a 272-byte frame")
        if not (parts[1].mnemonic == "stp" and parts[1].operands.startswith("x0, x1, [sp")):
            problems.append(f"EL2 vector entry {k} does not save x0/x1")
        if not (parts[2].mnemonic == "mov" and parts[2].operands in (f"x0, #{k}", f"x0, #{k:#x}")):
            problems.append(f"EL2 vector entry {k} does not load its index {k}: {parts[2].operands!r}")
        if parts[3].mnemonic != "b":
            problems.append(f"EL2 vector entry {k} does not branch to the common code")
        else:
            targets.add(parts[3].operands.split()[0])
    if len(targets) != 1:
        problems.append(f"EL2 vector entries branch to {len(targets)} different targets, expected 1")
    return problems


_ASM_STORE = re.compile(r"\b(st[a-z0-9]*)\s+[^\\\"\n]*")
_VOLATILE_STORE = re.compile(r"\*\s*\(\s*volatile\b[^)]*\)\s*\([^;]*\)\s*=[^=]|\*\s*\(\s*volatile\b[^)]*\)[^;=]*=[^=]")


def audit_asm_text(text: str, name: str, stores_to_stack_only: bool) -> list:
    """Stores inside inline-assembly strings: none, or only to the stack in the vector code."""
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith('"') and "__asm__" not in stripped and "asm volatile" not in stripped:
            continue
        for m in _ASM_STORE.finditer(stripped):
            mnemonic = m.group(1)
            if mnemonic not in {"str", "stp", "stur", "strb", "strh", "stlr", "st1", "stxr"}:
                continue
            if stores_to_stack_only and "[sp" in m.group(0):
                continue
            problems.append(f"{name}:{n}: store `{m.group(0).strip()}` in inline assembly")
    return problems


def _block(text: str, start: str, end: str) -> str:
    """The text between two markers, excluding both; empty if either is missing."""
    a = text.find(start)
    b = text.find(end, a + len(start)) if a >= 0 else -1
    return text[a + len(start):b] if a >= 0 and b >= 0 else ""


def audit_state_model(root: Path) -> list:
    """The saved/restored register model, the code that reads and writes the registers, and the
    audit's own write list name the same registers.

    Adding a register to one and not the others is the defect: an `msr` target the audit allows
    without a saved copy leaves EL2 altered, and an enumerator nobody restores is saved for nothing.
    """
    problems = []
    tests = root / "probes" / "uefi" / "tests"
    regs = (tests / "regs.h").read_text(encoding="utf-8")
    m = re.search(r"enum tests_wreg \{(.*?)\};", regs, re.S)
    if not m:
        return ["regs.h: enum tests_wreg is not defined as expected"]
    model = {n for n in re.findall(r"\bW_(\w+)", m.group(1)) if n != "COUNT"}
    if model != set(WREG_MSR):
        problems.append(f"state model: regs.h has {sorted(model)} but the audit's table has "
                        f"{sorted(WREG_MSR)}; only in regs.h {sorted(model - set(WREG_MSR))}, "
                        f"only in the audit {sorted(set(WREG_MSR) - model)}")
    sys_text = (tests / "sys_aarch64.c").read_text(encoding="utf-8")
    for label, body in (("wop_rd", _block(sys_text, "static int wop_rd(", "static int wop_wr(")),
                        ("wop_wr", _block(sys_text, "static int wop_wr(", "static const tests_wops"))):
        cases = set(re.findall(r"case W_(\w+):", body))
        if not body or cases != model:
            problems.append(f"state model: {label} in sys_aarch64.c handles {sorted(cases)}, "
                            f"the model has {sorted(model)}")
    state = (tests / "sysstate.c").read_text(encoding="utf-8")
    order = set(re.findall(r"\bW_(\w+)", _block(state, "restore_order[W_COUNT] = {", "};")))
    if order != model:
        problems.append(f"state model: restore_order in sysstate.c lists {sorted(order)}, "
                        f"the model has {sorted(model)}")
    return problems


def audit_verified_writes(root: Path) -> list:
    """Every register write of the privileged file is read back before the image relies on it.

    HCR_EL2 and VNCR_EL2 in particular are compared in full before the ERET to EL1: a bit the
    hardware ignored, or a firmware that changed the register, must stop the run rather than
    send the stub into EL1 under a configuration nobody chose.
    """
    problems = []
    text = (root / "probes" / "uefi" / "tests" / "sys_aarch64.c").read_text(encoding="utf-8")
    if re.search(r"\btests_sysstate_write\s*\(", text):
        problems.append("sys_aarch64.c: a register write that is not read back (tests_sysstate_write)")
    body = _block(text, "static int nv2_set_hcr(", "static int nv2_run(")
    for reg in ("W_VNCR_EL2", "W_HCR_EL2"):
        if not re.search(rf"tests_sysstate_write_verified\([^;]*\b{reg}\b[^;]*~0ull\)", body):
            problems.append(f"sys_aarch64.c: nv2_set_hcr does not compare every bit of {reg} "
                            f"after writing it")
    return problems


def _code_only(text: str) -> str:
    """The text with comments and string literals blanked and every newline kept."""
    out, i, n = [], 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(re.sub(r"[^\n]", " ", text[i:j + 1]))
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# The one place a UEFI variable is written: the declared-ranges step of the `declared` variant
# (probe_main.c, tests/ebs_ranges.h). Any other mention of SetVariable is a new write path that
# must be reviewed, so the audit refuses it.
VARIABLE_WRITE_FILE = "probe_main.c"
VARIABLE_WRITE_GATE = "PROBE_TESTS_IMAGE && TESTS_DECLARE_EBS_RANGES"
_SET_VARIABLE = re.compile(r"\bset_variable\b")


def audit_variable_writes(root: Path) -> list:
    """`set_variable` appears only in probe_main.c, only inside the declared-ranges gate."""
    problems = []
    base = root / "probes" / "uefi"
    for path in sorted([*base.glob("*.c"), *(base / "tests").glob("*.c")]):
        gates = []  # one entry per open conditional: its #if condition, or None (#ifdef, #ifndef, #else)
        for n, line in enumerate(_code_only(path.read_text(encoding="utf-8")).splitlines(), 1):
            m = re.match(r"\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)", line)
            if m:
                kind, rest = m.group(1), " ".join(m.group(2).split())
                if kind in ("if", "ifdef", "ifndef"):
                    gates.append(rest if kind == "if" else None)
                elif kind in ("elif", "else") and gates:
                    gates[-1] = None
                elif kind == "endif" and gates:
                    gates.pop()
                continue
            if _SET_VARIABLE.search(line):
                if path.name != VARIABLE_WRITE_FILE or path.parent != base:
                    problems.append(f"{path.name}:{n}: set_variable outside probe_main.c")
                elif VARIABLE_WRITE_GATE not in gates:
                    problems.append(f"{path.name}:{n}: set_variable outside `#if {VARIABLE_WRITE_GATE}`")
    return problems


# CNTPOFF_EL2 is read in one place, behind a flag of the test context that only the build option
# sets. EL2 access to the register is also gated at EL3 (SCR_EL3.ECVEn); firmware that leaves the
# gate closed takes the access to EL3, where a monitor that does not handle it can hang the machine
# (INFERRED from one frozen page on the Surface Pro 11, not tested), so no image for the machine
# may reach the read: tools/build-uefi-tests.py refuses --cntpoff for them, and this audit refuses a
# second read, a read that is not behind the flag and a second place that sets the flag.
CNTPOFF_FILE = "t_misc.c"
CNTPOFF_FLAG_FILE = "hook.c"
_CNTPOFF_REG = re.compile(r"\bTR_CNTPOFF_EL2\b")
_CNTPOFF_FLAG = re.compile(r"\bread_cntpoff\b")
_CNTPOFF_SET = re.compile(r"\bread_cntpoff\s*=(?!=)")
_CNTPOFF_DEFAULT = re.compile(r"#\s*ifndef\s+TESTS_READ_CNTPOFF\s*\n\s*#\s*define\s+TESTS_READ_CNTPOFF\s+0\s*\n"
                              r"\s*#\s*endif")


def audit_cntpoff_read(root: Path) -> list:
    """TR_CNTPOFF_EL2 appears once, in t_misc.c, within three lines after `read_cntpoff`; only
    hook.c assigns that flag, from TESTS_READ_CNTPOFF, which it defaults to 0."""
    problems = []
    base = root / "probes" / "uefi"
    uses = 0
    for path in sorted([*base.glob("*.[ch]"), *(base / "tests").glob("*.[ch]")]):
        raw = path.read_text(encoding="utf-8")
        code = _code_only(raw).splitlines()
        for n, line in enumerate(code):
            if _CNTPOFF_REG.search(line):
                uses += 1
                if path.name != CNTPOFF_FILE or path.parent != base / "tests":
                    problems.append(f"{path.name}:{n + 1}: TR_CNTPOFF_EL2 outside tests/{CNTPOFF_FILE}")
                elif not any(_CNTPOFF_FLAG.search(prev) for prev in code[max(0, n - 3):n]):
                    problems.append(f"{path.name}:{n + 1}: TR_CNTPOFF_EL2 is not directly behind `read_cntpoff`")
            if _CNTPOFF_SET.search(line):
                if path.name != CNTPOFF_FLAG_FILE or path.parent != base / "tests":
                    problems.append(f"{path.name}:{n + 1}: read_cntpoff is set outside tests/{CNTPOFF_FLAG_FILE}")
                elif not re.search(r"\bg_tc\.read_cntpoff\s*=\s*TESTS_READ_CNTPOFF\s*;", line):
                    problems.append(f"{path.name}:{n + 1}: read_cntpoff is set to something other than "
                                    f"TESTS_READ_CNTPOFF")
        if path.name == CNTPOFF_FLAG_FILE and path.parent == base / "tests" and not _CNTPOFF_DEFAULT.search(
                "\n".join(code)):
            problems.append(f"{path.name}: TESTS_READ_CNTPOFF is not defined to 0 by default")
    if uses != 1:
        problems.append(f"TR_CNTPOFF_EL2 appears {uses} times in the image's sources, expected once")
    return problems


def audit_sources(root: Path) -> list:
    """No store through a volatile device pointer, no write member on the bus interface."""
    problems = (audit_state_model(root) + audit_verified_writes(root) + audit_variable_writes(root)
                + audit_cntpoff_read(root))
    tests = root / "probes" / "uefi" / "tests"
    for path in sorted(tests.glob("*.[ch]")):
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            code = line.split("//")[0]
            if _VOLATILE_STORE.search(code):
                problems.append(f"{path.name}:{n}: assignment through a volatile pointer: {line.strip()}")
    bus = (tests / "env.h").read_text(encoding="utf-8")
    m = re.search(r"typedef struct \{(.*?)\} tests_bus;", bus, re.S)
    if not m:
        problems.append("env.h: tests_bus is not defined as expected")
    else:
        members = re.findall(r"\(\*(\w+)\)", m.group(1))
        if sorted(members) != ["faults", "rd32", "rd_sys"]:
            problems.append(f"env.h: tests_bus members are {members}; only rd32, rd_sys and faults may exist")
    for name, stack_only in (("sys_aarch64.c", False), ("vectors_aarch64.c", True)):
        problems += audit_asm_text((tests / name).read_text(encoding="utf-8"), name, stack_only)
    return problems
