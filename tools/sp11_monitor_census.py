#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The census of an EL2 binary's system-level instructions, and the check of an authorisation record against it (monitor port work item 4b).

    sp11_monitor_census.py census ELF
    sp11_monitor_census.py check --monitor-elf ELF [--wrapper-elf ELF] RECORD

`census` prints what llvm-objdump finds in ELF: every system register an MSR writes, every SYS-class operation (TLBI, DC, IC, AT), SMC, ERET and
WFE, each with the functions that hold it, the system registers read, and the number of indirect branches. `check` reads the section 3
`el2_sysreg` class of an M1 authorisation record (tools/sp11_stick_bundle_record.py), whose groups `wrapper` and `monitor A` .. `monitor G` name what
each binary can write, and compares it with the binaries in both directions: a register or operation that a binary has and no group names, and a name
in a group that the binary neither writes nor reads, are both problems. With --monitor-elf the SHA-256 of the file must be the `elf sha256` that
section 1b of the record names, so the binary checked is the binary recorded.

What this can show: which instructions exist in the binary, by their encodings as llvm-objdump decodes them. What it cannot: which of them run. The
monitor has indirect calls (BLR) and indirect branches (BR) that no static pass follows, so the groups' conditions (which group is reached when)
are INFERRED from the sources, never shown by this tool; a register written through an instruction llvm-objdump does not decode (an `.inst` word)
is counted and refused. Exit status: 0 the record names what the binaries hold, 1 it does not, 2 the tool cannot run (no llvm-objdump, a bad file).

Evidence: the census of a binary is VERIFIED for that binary on the host; hardware NOT RUN."""
import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

LLVM_DIRS = ('/opt/homebrew/opt/llvm/bin', '/opt/homebrew/opt/lld/bin')
SYS_OPS = ('tlbi', 'dc', 'ic', 'at', 'sys', 'sysl')
PLAIN = ('smc', 'hvc', 'svc', 'eret', 'wfe', 'wfi', 'brk', 'hlt')       # instructions that are not a system register or a SYS operation but change what runs
INDIRECT = ('blr', 'br', 'blraa', 'blrab', 'braa', 'brab', 'blraaz', 'blrabz', 'braaz', 'brabz')
SIDE_EFFECT_READS = ('ICC_IAR1_EL1', 'ICC_IAR0_EL1')       # an MRS that acknowledges an interrupt changes the GIC's state
REGISTER = re.compile(r'\b(?:[A-Za-z][A-Za-z0-9_]*_EL[0-3]|DAIFSet|DAIF|SPSel|CurrentEL|RNDR|S\d_\d_C\d+_C\d+_\d)\b')
SYSTEM_OP = re.compile(r'\b(TLBI|DC|IC|AT) ([A-Z0-9]+)\b')
PLAIN_NAME = re.compile(r'\b(SMC|HVC|SVC|ERET|WFE|WFI|BRK|HLT)\b')
GROUP_HEAD = re.compile(r'^ {6}(wrapper|monitor [A-G])[:,](.*)$')
CLASS_HEAD = re.compile(r'^ {3}(ram|el2_sysreg|gic): ')
DATA = re.compile(r'\.(word|byte|short|quad|long)\b')
UNDECODED = re.compile(r'\.inst\b|<unknown>')
GENERIC = re.compile(r'[sS]\d_\d_[cC]\d+_[cC]\d+_\d')       # a register llvm-objdump has no name for


class CensusError(Exception):
    """The tool cannot run: exit status 2."""


def find_tool(name):
    found = shutil.which(name) or next((str(Path(d) / name) for d in LLVM_DIRS if (Path(d) / name).is_file()), None)
    if not found:
        raise CensusError(f'{name} is not on PATH or in {LLVM_DIRS}')
    return found


def disassemble(elf):
    """llvm-objdump's listing of the executable sections of `elf`."""
    if not Path(elf).is_file():
        raise CensusError(f'{elf} is not a file')
    done = subprocess.run([find_tool('llvm-objdump'), '-d', '--no-show-raw-insn', str(elf)], capture_output=True, text=True)
    if done.returncode:
        raise CensusError(f'llvm-objdump refused {Path(elf).name}: {done.stderr.strip()[:200]}')
    return done.stdout


def parse(listing):
    """([(function, address, mnemonic, operands)], words shown as data, words llvm-objdump does not decode). A duplicated local name keeps both copies."""
    rows, data, undecoded, current, serial = [], 0, 0, None, {}
    for line in listing.splitlines():
        label = re.match(r'^[0-9a-f]+ <(.+)>:$', line)
        if label:
            name = label.group(1)
            serial[name] = serial.get(name, 0) + 1
            current = name if serial[name] == 1 else f'{name}#{serial[name]}'
            continue
        ins = re.match(r'^\s+([0-9a-f]+):\s+(\S+)\s*(.*)$', line)
        if ins and current:
            if UNDECODED.search(line):
                undecoded += 1
            elif DATA.search(line):
                data += 1
            else:
                rows.append((current, int(ins.group(1), 16), ins.group(2), ins.group(3).split('//')[0].strip()))
    return rows, data, undecoded


def register_name(text):
    name = text.strip()
    return name.upper() if GENERIC.fullmatch(name) else name


def census(elf):
    """{'msr': {register: {functions}}, 'mrs': {...}, 'sys': {'TLBI ALLE1': {functions}, ...}, 'plain': {'SMC': {functions}, ...}, 'indirect': count,
    'data': words shown as data, 'undecoded': words llvm-objdump could not decode}."""
    rows, data, undecoded = parse(disassemble(elf))
    out = dict(msr={}, mrs={}, sys={}, plain={}, indirect=0, data=data, undecoded=undecoded)
    for function, _, mnemonic, operands in rows:
        function = function.split('#')[0]
        if mnemonic == 'msr':
            out['msr'].setdefault(register_name(operands.split(',')[0]), set()).add(function)
        elif mnemonic == 'mrs':
            out['mrs'].setdefault(register_name(operands.split(',')[-1]), set()).add(function)
        elif mnemonic in SYS_OPS:
            out['sys'].setdefault(f'{mnemonic.upper()} {operands.split(",")[0].strip().upper()}', set()).add(function)
        elif mnemonic in PLAIN:
            out['plain'].setdefault(mnemonic.upper(), set()).add(function)
        elif mnemonic in INDIRECT:
            out['indirect'] += 1
    return out


def groups_of(text):
    """{'wrapper': text, 'monitor A': text, ...} of the el2_sysreg class of the record: a group starts at a line `      wrapper:` or `      monitor X,`
    (six spaces; a colon or a comma ends the label) and holds the lines after it that are indented further; the class ends at the next class or
    at the first line with no indent."""
    found, block, group = {}, False, None
    for line in text.splitlines():
        cls = CLASS_HEAD.match(line)
        if cls:
            block, group = cls.group(1) == 'el2_sysreg', None
            continue
        if not block:
            continue
        if line and not line.startswith(' '):
            block = False
            continue
        head = GROUP_HEAD.match(line)
        if head:
            group = head.group(1)
            found[group] = found.get(group, '') + head.group(2)
        elif group and line.startswith(' ' * 9):
            found[group] += ' ' + line.strip()
        elif line.strip():
            group = None
    return found


def named(group_texts):
    """(registers, SYS operations, plain instructions) named by the given group texts."""
    joined = '\n'.join(group_texts)
    return (set(REGISTER.findall(joined)), {f'{a} {b}' for a, b in SYSTEM_OP.findall(joined)}, set(PLAIN_NAME.findall(joined)))


def compare(cen, group_texts, what):
    """The problems between one binary's census and the groups that claim to describe it: [str]."""
    regs, ops, plain = named(group_texts)
    problems = []
    if cen['undecoded']:
        problems.append(f'{what}: {cen["undecoded"]} words that llvm-objdump does not decode: the census is not complete')
    for name in sorted(set(cen['msr']) - regs):
        problems.append(f'{what}: writes {name} (in {", ".join(sorted(cen["msr"][name])[:3])}) and the record does not name it')
    for name in sorted(set(cen['sys']) - ops):
        problems.append(f'{what}: executes {name} and the record does not name it')
    for name in sorted(set(cen['plain']) - plain):
        problems.append(f'{what}: executes {name} and the record does not name it')
    for name in sorted(set(SIDE_EFFECT_READS) & set(cen['mrs']) - regs):
        problems.append(f'{what}: reads {name}, which acknowledges an interrupt, and the record does not name it')
    for name in sorted(regs - set(cen['msr']) - set(cen['mrs'])):
        problems.append(f'the record names {name} under {what} and the binary neither writes nor reads it')
    for name in sorted(ops - set(cen['sys'])):
        problems.append(f'the record names {name} under {what} and the binary does not execute it')
    for name in sorted(plain - set(cen['plain'])):
        problems.append(f'the record names {name} under {what} and the binary does not execute it')
    return problems


def check(text, monitor_census, wrapper_census=None, monitor_elf_sha256=None):
    """[problems] of the record text against the census of the monitor (groups `monitor *`) and, when given, of the wrapper (group `wrapper`)."""
    groups = groups_of(text)
    problems = []
    monitor = [t for g, t in groups.items() if g.startswith('monitor ')]
    if not monitor:
        problems.append('the record has no `monitor X:` group in its el2_sysreg class: it is not the record of a monitor entry run')
    else:
        problems += compare(monitor_census, monitor, 'the monitor')
    if wrapper_census is not None:
        if 'wrapper' not in groups:
            problems.append('the record has no `wrapper:` group in its el2_sysreg class')
        else:
            problems += compare(wrapper_census, [groups['wrapper']], 'the wrapper')
    if monitor_elf_sha256 is not None:
        named_digest = re.search(r'^ {3}elf sha256\s+([0-9a-f]{64})', text, re.M)
        if not named_digest or named_digest.group(1) != monitor_elf_sha256:
            problems.append('the monitor ELF checked is not the one section 1b of the record names (its elf sha256 line)')
    return problems


def summary(cen, label):
    lines = [f'{label}: {len(cen["msr"])} registers written, {len(cen["mrs"])} read, {len(cen["sys"])} SYS operations, {sum(len(v) for v in cen["plain"].values())} '
             f'functions with SMC, HVC, SVC, ERET, WFE, WFI, BRK or HLT, {cen["indirect"]} indirect branches (not followed), {cen["data"]} data words skipped']
    lines += [f'  msr {name}: {", ".join(sorted(fns)[:4])}{" ..." if len(fns) > 4 else ""}' for name, fns in sorted(cen['msr'].items())]
    lines += [f'  {name}: {", ".join(sorted(fns)[:4])}{" ..." if len(fns) > 4 else ""}' for name, fns in sorted({**cen['sys'], **cen['plain']}.items())]
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('census').add_argument('elf')
    chk = sub.add_parser('check')
    chk.add_argument('--monitor-elf', required=True)
    chk.add_argument('--wrapper-elf')
    chk.add_argument('record')
    a = ap.parse_args(argv)
    try:
        if a.command == 'census':
            print('\n'.join(summary(census(a.elf), Path(a.elf).name)))
            return 0
        text = Path(a.record).read_text(encoding='ascii')
        monitor = census(a.monitor_elf)
        wrapper = census(a.wrapper_elf) if a.wrapper_elf else None
        problems = check(text, monitor, wrapper, hashlib.sha256(Path(a.monitor_elf).read_bytes()).hexdigest())
    except (CensusError, OSError, UnicodeDecodeError) as error:
        print(f'CANNOT RUN: {error}', file=sys.stderr)
        return 2
    print('\n'.join(summary(monitor, 'the monitor') + (summary(wrapper, 'the wrapper') if wrapper else [])))
    print('\n'.join(f'PROBLEM {p}' for p in problems) if problems else 'CHECK PASS: the record names what the binaries write and execute, and nothing they lack')
    print('INFERRED, not shown: which group is reached when; the indirect branches are not followed. Hardware NOT RUN.')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
