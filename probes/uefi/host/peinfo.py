# SPDX-License-Identifier: MIT
"""Mechanical audits of a linked probe image and of its object files.

The probe runs at EL2 with whatever CPTR_EL2, MDCR_EL2 and vector table the
firmware left behind, so "it cannot trap by itself" and "it only reads" are
properties worth checking on the artifact rather than on the source:

  * the PE headers describe a relocatable AArch64 EFI application without
    imports, without a writable+executable section and without an exception
    or TLS directory;
  * the code contains no FP/SIMD/SVE/SME instruction (an FP trap would hide
    the report), no `msr`, no `svc`, no `eret`, no TLB or address-translation
    maintenance, and reads only system registers from an independent table;
  * `smc` exists only in the opt-in PSCI image, with immediate 0, and no image
    contains an `hvc`: an HVC taken at EL2 goes to this exception level's own
    vector, which no image installs;
  * `brk` appears only as the unreachable guard after a call that never
    returns;
  * the memcpy family cannot call itself (the compiler may turn a byte loop
    into a call to the very function it is compiling).

Evidence level: these are checks of the emitted code, OBSERVED on the build
that was audited. They say nothing about how the firmware or the hardware
reacts to that code.

llvm-objdump is only needed by the disassembly helpers; the PE parser is plain
Python.
"""
from __future__ import annotations

import re
import struct
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, Optional

IMAGE_FILE_MACHINE_ARM64 = 0xAA64
PE32_PLUS_MAGIC = 0x20B
SUBSYSTEM_EFI_APPLICATION = 10

SCN_CNT_CODE = 0x00000020
SCN_MEM_DISCARDABLE = 0x02000000
SCN_MEM_EXECUTE = 0x20000000
SCN_MEM_READ = 0x40000000
SCN_MEM_WRITE = 0x80000000

DIR_IMPORT = 1
DIR_EXCEPTION = 3
DIR_CERTIFICATE = 4
DIR_BASERELOC = 5
DIR_TLS = 9
DIR_DELAY_IMPORT = 13


class PeError(Exception):
    pass


@dataclass
class Section:
    name: str
    virtual_size: int
    virtual_address: int
    raw_size: int
    raw_offset: int
    characteristics: int

    @property
    def executable(self) -> bool:
        return bool(self.characteristics & SCN_MEM_EXECUTE)

    @property
    def writable(self) -> bool:
        return bool(self.characteristics & SCN_MEM_WRITE)

    @property
    def extent(self) -> int:
        return max(self.virtual_size, self.raw_size)

    def contains_rva(self, rva: int) -> bool:
        return self.virtual_address <= rva < self.virtual_address + self.extent


@dataclass
class Pe:
    data: bytes
    machine: int
    magic: int
    subsystem: int
    image_base: int
    section_alignment: int
    file_alignment: int
    entry_rva: int
    size_of_image: int
    size_of_headers: int
    dll_characteristics: int
    directories: list = field(default_factory=list)  # [(rva, size)] x number_of_rva_and_sizes
    sections: list = field(default_factory=list)

    def directory(self, index: int):
        return self.directories[index] if index < len(self.directories) else (0, 0)

    def section_of(self, rva: int) -> Optional[Section]:
        return next((s for s in self.sections if s.contains_rva(rva)), None)

    def section(self, name: str) -> Optional[Section]:
        return next((s for s in self.sections if s.name == name), None)

    def section_bytes(self, name: str) -> bytes:
        s = self.section(name)
        if s is None:
            raise PeError(f"no section {name}")
        return self.data[s.raw_offset:s.raw_offset + min(s.raw_size, s.virtual_size or s.raw_size)]


def parse_pe(data: bytes) -> Pe:
    """Parses and bounds-checks the headers of a PE32+ image."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise PeError("missing MZ header")
    (pe_off,) = struct.unpack_from("<I", data, 0x3C)
    if pe_off < 0x40 or pe_off + 24 > len(data) or data[pe_off:pe_off + 4] != b"PE\0\0":
        raise PeError("missing PE signature")
    machine, nsec, _ts, _symptr, _nsym, opt_size, _chars = struct.unpack_from(
        "<HHIIIHH", data, pe_off + 4)
    opt = pe_off + 24
    if opt_size < 112 or opt + opt_size > len(data):
        raise PeError("optional header truncated")
    (magic,) = struct.unpack_from("<H", data, opt)
    if magic != PE32_PLUS_MAGIC:
        raise PeError(f"not PE32+ (magic {magic:#x})")
    (entry,) = struct.unpack_from("<I", data, opt + 16)
    (image_base,) = struct.unpack_from("<Q", data, opt + 24)
    sec_align, file_align = struct.unpack_from("<II", data, opt + 32)
    size_of_image, size_of_headers = struct.unpack_from("<II", data, opt + 56)
    subsystem, dll_chars = struct.unpack_from("<HH", data, opt + 68)
    (ndirs,) = struct.unpack_from("<I", data, opt + 108)
    if ndirs > 16 or 112 + ndirs * 8 > opt_size:
        raise PeError("data directory table does not fit the optional header")
    directories = [struct.unpack_from("<II", data, opt + 112 + 8 * i) for i in range(ndirs)]

    table = opt + opt_size
    if nsec > 96 or table + 40 * nsec > len(data):
        raise PeError("section table truncated")
    sections = []
    for i in range(nsec):
        name, vsize, vaddr, rsize, roff, _r, _l, _nr, _nl, chars = struct.unpack_from(
            "<8sIIIIIIHHI", data, table + 40 * i)
        sec = Section(name.rstrip(b"\0").decode("ascii", "replace"), vsize, vaddr, rsize, roff, chars)
        if rsize and roff + rsize > len(data):
            raise PeError(f"section {sec.name} extends past the end of the file")
        sections.append(sec)
    return Pe(data, machine, magic, subsystem, image_base, sec_align, file_align, entry,
              size_of_image, size_of_headers, dll_chars, directories, sections)


def audit_pe(pe: Pe, max_image_bytes: int = 4 << 20) -> list:
    """Returns a list of problems; an empty list means the image is acceptable."""
    bad = []
    if pe.machine != IMAGE_FILE_MACHINE_ARM64:
        bad.append(f"machine is {pe.machine:#06x}, not AArch64")
    if pe.subsystem != SUBSYSTEM_EFI_APPLICATION:
        bad.append(f"subsystem is {pe.subsystem}, not 10 (EFI application)")
    if pe.size_of_image > max_image_bytes:
        bad.append(f"image is {pe.size_of_image} bytes in memory (limit {max_image_bytes})")
    if pe.section_alignment < 0x1000 or pe.section_alignment & (pe.section_alignment - 1):
        bad.append(f"section alignment {pe.section_alignment:#x} is not a power of two >= 4 KiB")
    entry = pe.section_of(pe.entry_rva)
    if entry is None:
        bad.append("entry point lies in no section")
    elif not entry.executable or entry.writable:
        bad.append(f"entry point is in section {entry.name} which is not R+X")
    for sec in pe.sections:
        if sec.executable and sec.writable:
            bad.append(f"section {sec.name} is writable and executable")
        if sec.virtual_address % pe.section_alignment:
            bad.append(f"section {sec.name} is not aligned to the section alignment")
    for name, idx in (("import", DIR_IMPORT), ("delay-import", DIR_DELAY_IMPORT),
                      ("exception", DIR_EXCEPTION), ("certificate", DIR_CERTIFICATE),
                      ("TLS", DIR_TLS)):
        if pe.directory(idx)[1]:
            bad.append(f"image has a {name} directory")
    reloc_rva, reloc_size = pe.directory(DIR_BASERELOC)
    if not reloc_size:
        bad.append("no base relocations: a loader whose preferred range is busy cannot place it")
    elif (sec := pe.section_of(reloc_rva)) is None or sec.writable or sec.executable:
        bad.append("relocation directory is outside a read-only section")
    spans = sorted((s.virtual_address, s.virtual_address + s.extent, s.name) for s in pe.sections)
    for (a0, a1, an), (b0, _b1, bn) in zip(spans, spans[1:]):
        if b0 < a1:
            bad.append(f"sections {an} and {bn} overlap in memory")
    return bad


# Independent of probes/uefi/sysreg.h: (op0, op1, CRn, CRm, op2) from the Arm
# architecture reference for A-profile, so a typo in either table is a failure.
# ID_AA64MMFR3_EL1, ID_AA64MMFR4_EL1, ID_AA64ISAR2_EL1 and ID_AA64DFR1_EL1 are
# absent on purpose: the passive probe must not read them (no recovery path for
# a fault), so an `mrs` of one is "a register outside the probe's table".
SYSREGS = {
    "CurrentEL": (3, 0, 4, 2, 2),
    "HCR_EL2": (3, 4, 1, 1, 0),
    "SCTLR_EL2": (3, 4, 1, 0, 0),
    "TCR_EL2": (3, 4, 2, 0, 2),
    "TTBR0_EL2": (3, 4, 2, 0, 0),
    "TTBR1_EL2": (3, 4, 2, 0, 1),
    "MAIR_EL2": (3, 4, 10, 2, 0),
    "VBAR_EL2": (3, 4, 12, 0, 0),
    "CPTR_EL2": (3, 4, 1, 1, 2),
    "MDCR_EL2": (3, 4, 1, 1, 1),
    "CNTHCTL_EL2": (3, 4, 14, 1, 0),
    "CNTVOFF_EL2": (3, 4, 14, 0, 3),
    "ICC_SRE_EL2": (3, 4, 12, 9, 5),
    "SPSR_EL2": (3, 4, 4, 0, 0),
    "ELR_EL2": (3, 4, 4, 0, 1),
    "MPIDR_EL1": (3, 0, 0, 0, 5),
    "MIDR_EL1": (3, 0, 0, 0, 0),
    "REVIDR_EL1": (3, 0, 0, 0, 6),
    "ID_AA64PFR0_EL1": (3, 0, 0, 4, 0),
    "ID_AA64PFR1_EL1": (3, 0, 0, 4, 1),
    "ID_AA64ZFR0_EL1": (3, 0, 0, 4, 4),
    "ID_AA64SMFR0_EL1": (3, 0, 0, 4, 5),
    "ID_AA64DFR0_EL1": (3, 0, 0, 5, 0),
    "ID_AA64ISAR0_EL1": (3, 0, 0, 6, 0),
    "ID_AA64ISAR1_EL1": (3, 0, 0, 6, 1),
    "ID_AA64MMFR0_EL1": (3, 0, 0, 7, 0),
    "ID_AA64MMFR1_EL1": (3, 0, 0, 7, 1),
    "ID_AA64MMFR2_EL1": (3, 0, 0, 7, 2),
    "DCZID_EL0": (3, 3, 0, 0, 7),
    "CTR_EL0": (3, 3, 0, 0, 1),
    "CNTFRQ_EL0": (3, 3, 14, 0, 0),
    "CNTPCT_EL0": (3, 3, 14, 0, 1),
}
_GENERIC = re.compile(r"S(\d)_(\d)_C(\d+)_C(\d+)_(\d)", re.I)
_BY_ENCODING = {v: k for k, v in SYSREGS.items()}


def canonical_sysreg(operand: str) -> Optional[str]:
    """Maps an objdump system-register operand (name or S<op0>_<op1>_C<n>_C<m>_<op2>)
    to the table name, or None if it is not a register this probe may read."""
    operand = operand.strip()
    if operand in SYSREGS:
        return operand
    m = _GENERIC.fullmatch(operand)
    if m:
        return _BY_ENCODING.get(tuple(int(g) for g in m.groups()))
    return None


@dataclass
class Insn:
    addr: int
    mnemonic: str
    operands: str
    func: str = ""
    reloc: str = ""     # relocation target, when the disassembly shows one


_INSN = re.compile(r"^\s*([0-9a-f]+):\s*\t(\S+)(?:\t+(.*?))?\s*$")
_LABEL = re.compile(r"^([0-9a-f]+) <([^>]+)>:\s*$")
_RELOC = re.compile(r"^\s+[0-9a-f]+:\s+(IMAGE_REL_\w+)\s+(\S+)\s*$")


def parse_disassembly(text: str) -> list:
    """Parses llvm-objdump -d [-r] --no-show-raw-insn output into Insn records."""
    out, func = [], ""
    for line in text.splitlines():
        m = _LABEL.match(line)
        if m:
            func = m.group(2)
            continue
        m = _RELOC.match(line)
        if m and out:
            out[-1].reloc = m.group(2)
            continue
        m = _INSN.match(line)
        if m:
            operands = re.sub(r"\s*//.*$", "", m.group(3) or "")
            out.append(Insn(int(m.group(1), 16), m.group(2), operands, func))
    return out


def disassemble(objdump: str, path, relocations: bool = False) -> list:
    cmd = [objdump, "-d", "--no-show-raw-insn"] + (["-r"] if relocations else []) + [str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise PeError(f"{objdump} failed: {proc.stderr.strip()}")
    return parse_disassembly(proc.stdout)


# Any use of a vector, scalar-FP, SVE or predicate register name. Every
# FP/SIMD/SVE data instruction names one; FPCR/FPSR accesses are mrs/msr and
# are caught by the register table and the msr ban.
_VECTOR_REG = re.compile(r"(?<![\w.#])[bhsdqvzp](?:[0-9]|[12][0-9]|3[01])(?![\w])")
# No base A64 integer instruction starts with 'f'.
_FP_MNEMONIC = re.compile(r"^(f[a-z0-9]+|movi|mvni|dup|ins|umov|smov|addv|tbl|tbx|"
                          r"zip[12]|uzp[12]|trn[12]|ext|bsl|bif|bit|ld[1-4]r?|st[1-4])$")
_ALWAYS_FORBIDDEN = {"msr", "svc", "eret", "eretaa", "eretab", "tlbi", "at", "sysl", "hlt", "udf",
                     "ic", "smstart", "smstop", "rdvl", "addvl", "addpl"}
_BRANCH_CALLS = {"bl", "blr", "blraa", "blrab"}
_FUNCTION_END = {"ret", "retaa", "retab", "b", "br", "braa", "brab", "brk"}
# psci.c's stub is aligned(64), so lld-link starts that object's .text on a
# 64-byte boundary and fills the gap with zero bytes: 0 to 60 of them,
# depending on how much code precedes it. llvm-objdump elides a zero run of two
# or more words but prints a single word as `udf #0x0` (OBSERVED with the
# pinned toolchain), which would trip the udf ban on about one code change in
# sixteen. Only fill of that exact shape is excused.
_MAX_FILL_WORDS = 15


def _is_zero_word(ins: Insn) -> bool:
    return ins.mnemonic == "udf" and ins.operands.strip() == "#0x0"


def alignment_fill(insns: list) -> set:
    """Indices of the zero words that are linker fill between two functions.

    A run of zero words qualifies if it directly follows an instruction that
    ends a function, has at most 15 words, and ends on an address aligned to
    the smallest power of two above the run's length: a fill is shorter than
    the alignment it pads up to. Any other zero word, and every udf with a
    non-zero immediate, is left for the audit to reject.
    """
    fill, i = set(), 0
    while i < len(insns):
        if not _is_zero_word(insns[i]):
            i += 1
            continue
        j = i
        while j + 1 < len(insns) and _is_zero_word(insns[j + 1]) and insns[j + 1].addr == insns[j].addr + 4:
            j += 1
        words = j - i + 1
        alignment = 1 << (4 * words).bit_length()
        if (i > 0 and insns[i - 1].mnemonic in _FUNCTION_END and insns[i - 1].addr + 4 == insns[i].addr
                and words <= _MAX_FILL_WORDS and (insns[j].addr + 4) % alignment == 0):
            fill.update(range(i, j + 1))
        i = j + 1
    return fill


def registers_read(insns: Iterable[Insn]) -> set:
    """Names of the system registers the image reads with mrs (table names)."""
    out = set()
    for ins in insns:
        if ins.mnemonic == "mrs":
            name = canonical_sysreg(ins.operands.split(",")[-1])
            if name:
                out.add(name)
    return out


def audit_instructions(insns: Iterable[Insn], psci: bool, allow_dc=("civac",)) -> list:
    """Checks the instruction stream of a linked image; returns a list of problems."""
    bad = []
    insns = list(insns)
    fill = alignment_fill(insns)
    for i, ins in enumerate(insns):
        if i in fill:
            continue
        where = f"{ins.addr:#x} {ins.mnemonic} {ins.operands}".rstrip()
        mnem = ins.mnemonic
        if mnem == "mrs":
            if canonical_sysreg(ins.operands.split(",")[-1]) is None:
                bad.append(f"mrs of a register outside the probe's table: {where}")
            continue
        if mnem in _ALWAYS_FORBIDDEN:
            hint = " (a zero word that is not alignment fill)" if _is_zero_word(ins) else ""
            bad.append(f"forbidden instruction: {where}{hint}")
        elif mnem == "hvc":
            bad.append(f"hvc in a probe image (an HVC at EL2 is taken by a vector this image "
                       f"does not install): {where}")
        elif mnem == "smc":
            if not psci:
                bad.append(f"{mnem} in an image that must not issue PSCI calls: {where}")
            elif ins.operands.strip() != "#0":
                bad.append(f"{mnem} with a non-zero immediate: {where}")
        elif mnem == "dc":
            op = ins.operands.split(",")[0].strip()
            if op not in allow_dc:
                bad.append(f"cache maintenance other than {allow_dc}: {where}")
        elif mnem == "brk":
            prev = insns[i - 1].mnemonic if i else ""
            if prev not in _BRANCH_CALLS:
                bad.append(f"brk that does not follow a call (reachable trap?): {where}")
        elif mnem == "sys":
            bad.append(f"raw sys instruction: {where}")
        elif _FP_MNEMONIC.match(mnem):
            bad.append(f"FP/SIMD-class mnemonic: {where}")
        if _VECTOR_REG.search(re.sub(r"<[^>]*>", "", ins.operands)):
            bad.append(f"FP/SIMD/SVE register operand: {where}")
    return bad


def call_targets(insns: Iterable[Insn]) -> dict:
    """Maps function label -> set of call targets (relocation names or labels)."""
    out = {}
    for ins in insns:
        out.setdefault(ins.func, set())
        if ins.mnemonic in _BRANCH_CALLS or ins.mnemonic == "b":
            target = ins.reloc
            if not target:
                m = re.search(r"<([^>+]+)(?:\+0x[0-9a-f]+)?>", ins.operands)
                target = m.group(1) if m else ""
            if target and (ins.mnemonic != "b" or target != ins.func or ins.reloc):
                out[ins.func].add(target)
    return out


MEM_FORWARDERS = {"memcpy": "probe_memcpy", "memmove": "probe_memmove", "memset": "probe_memset",
                  "memcmp": "probe_memcmp", "bcmp": "probe_memcmp"}
MEM_LEAVES = ("probe_memcpy", "probe_memmove", "probe_memset", "probe_memcmp", "probe_strlen")


def audit_mem_objects(logic_insns: Iterable[Insn], mem_insns: Iterable[Insn]) -> list:
    """The compiler-visible memcpy family must not be able to recurse.

    mem.obj holds one-instruction forwarders to the probe_* implementations in
    logic.obj, and those implementations must be leaves: any call from them,
    to anything, would be a way back to memcpy.
    """
    bad = []
    forwarders = call_targets(mem_insns)
    for name, target in MEM_FORWARDERS.items():
        got = forwarders.get(name)
        if got != {target}:
            bad.append(f"{name} should only branch to {target}, found {sorted(got or [])}")
    leaves = call_targets(logic_insns)
    for name in MEM_LEAVES:
        if name not in leaves:
            bad.append(f"{name} not found in the logic object")
        elif leaves[name]:
            bad.append(f"{name} calls {sorted(leaves[name])} and is no longer a leaf")
    return bad
