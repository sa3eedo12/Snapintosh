#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The census of an EL2 binary's system-level instructions, and the check of an authorisation record against it (monitor port work item 4b).

    sp11_monitor_census.py census ELF
    sp11_monitor_census.py check --monitor-elf ELF --wrapper-elf ELF RECORD
    sp11_monitor_census.py placed --binary wrapper|monitor ELF

`census` prints what llvm-objdump finds in ELF: every system register an MSR writes, every SYS-class operation (TLBI, DC, IC, AT), SMC, HVC, ERET and
WFE and the like, each with the functions that hold it and how many instructions each holds, the system registers read, the number of indirect
branches, and every data word that decodes as a system instruction. `check` reads the section 3 `el2_sysreg` class of an M1 authorisation record
(tools/sp11_stick_bundle_record.py) and compares it with the binaries in three ways.
 1. Names, in both directions: the groups `wrapper` and `monitor A` .. `monitor G` name what each binary can write; a register or operation that a
    binary has and no group names, and a name in a group that the binary neither writes nor reads, are both problems.
 2. Places: the `placed` entries of the class name, for the wrapper and for the monitor, every register written, every SYS operation, every SMC, HVC,
    SVC, ERET, WFE, WFI, BRK or HLT and every data word that decodes as a system instruction, with the functions that hold it and the number of
    instructions each holds (`name*N`; no count means one). The comparison is complete in both directions, so a write that moves to another function,
    a second one in a function that has one, and a new register, operation or word anywhere, are problems (a data word is placed by its value: the
    monitor's table of guest instruction words, compat/native-writer.h, is six words that encode MSR and are never executed, so they are named and
    any other fails). `placed` prints the entries for one binary, in the form the record holds them.
 3. Bytes: the flat bytes of each ELF (llvm-objcopy -O binary, the recipe of both builds) must be the wrapper and the monitor that section 1b names,
    by size and SHA-256, and the SHA-256 of the monitor ELF file the `elf sha256` line: the binaries checked are the binaries staged.

What this can show: which instructions exist in the binary, by their encodings as llvm-objdump decodes them, and where. What it cannot: which of
them run. The monitor has indirect calls (BLR) and indirect branches (BR) that no static pass follows, so the groups' conditions (which group is
reached when) are INFERRED from the sources, never shown by this tool; a register written through an instruction llvm-objdump does not decode (an
`.inst` word) is counted and refused; data words are decoded only in the executable sections that llvm-objdump disassembles, and an instruction
hidden in data of a section it does not list, or built at run time, is not seen. Function names are those of the symbol table with an LTO clone
suffix (`.NNN`) taken off: a change of inlining moves an instruction to another function and the check says so. Exit status: 0 the record names and
places what the binaries hold, 1 it does not, 2 the tool cannot run.

Evidence: the census of a binary is VERIFIED for that binary on the host; hardware NOT RUN."""
import argparse
import collections
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
PLACED_HEAD = re.compile(r'^ {6}placed ')
PLACED_ITEM = re.compile(r'^ {9}(wrapper|monitor) (\S.*)$')
PLACED_MORE = re.compile(r'^ {12}(\S.*)$')
DATA = re.compile(r'\.(word|byte|short|quad|long)\b')
DATA_CELL = re.compile(r'\.(word|byte|short|quad|long)\s+(-?0x[0-9a-fA-F]+|-?\d+)')
DATA_SIZE = {'byte': 1, 'short': 2, 'word': 4, 'long': 4, 'quad': 8}
UNDECODED = re.compile(r'\.inst\b|<unknown>')
GENERIC = re.compile(r'[sS]\d_\d_[cC]\d+_[cC]\d+_\d')       # a register llvm-objdump has no name for
CLONE = re.compile(r'\.\d+$')
# The encodings a data word must not have (mask, value, name). HINT classes listed: the four that stop or wake a CPU; the others do nothing the record has to say.
SYSTEM_WORDS = ((0xFFF00000, 0xD5100000, 'MSR (register)'), (0xFFF8F01F, 0xD500401F, 'MSR (immediate)'), (0xFFF80000, 0xD5080000, 'SYS'),
                (0xFFF80000, 0xD5280000, 'SYSL'), (0xFFFFFFFF, 0xD503205F, 'WFE'), (0xFFFFFFFF, 0xD503207F, 'WFI'), (0xFFFFFFFF, 0xD503209F, 'SEV'),
                (0xFFFFFFFF, 0xD50320BF, 'SEVL'), (0xFFE0001F, 0xD4000001, 'SVC'), (0xFFE0001F, 0xD4000002, 'HVC'), (0xFFE0001F, 0xD4000003, 'SMC'),
                (0xFFE0001F, 0xD4200000, 'BRK'), (0xFFE0001F, 0xD4400000, 'HLT'), (0xFFE0001F, 0xD4A00001, 'DCPS1'), (0xFFE0001F, 0xD4A00002, 'DCPS2'),
                (0xFFE0001F, 0xD4A00003, 'DCPS3'), (0xFFFFFFFF, 0xD69F03E0, 'ERET'), (0xFFFFFFFF, 0xD69F0BFF, 'ERETAA'), (0xFFFFFFFF, 0xD69F0FFF, 'ERETAB'),
                (0xFFFFFFFF, 0xD6BF03E0, 'DRPS'))


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


def function_name(name):
    """The symbol's name with a duplicate marker and an LTO clone suffix taken off."""
    return CLONE.sub('', name.split('#')[0])


def data_cells(listing):
    """[(function, address, size, value)] of every .byte, .short, .word, .long and .quad that llvm-objdump shows in the executable sections."""
    cells, current = [], None
    for line in listing.splitlines():
        label = re.match(r'^[0-9a-f]+ <(.+)>:$', line)
        if label:
            current = function_name(label.group(1))
            continue
        ins = re.match(r'^\s+([0-9a-f]+):\s', line)
        cell = DATA_CELL.search(line) if ins and current else None
        if cell:
            size = DATA_SIZE[cell.group(1)]
            cells.append((current, int(ins.group(1), 16), size, int(cell.group(2), 0) & ((1 << 8 * size) - 1)))
    return cells


def system_class(word):
    """The name of the system instruction that a 32-bit word encodes, or None."""
    return next((name for mask, value, name in SYSTEM_WORDS if word & mask == value), None)


def data_system_words(listing):
    """[(function, address, word, class)] of the 4-byte aligned words, made of the data cells, that decode as a system instruction."""
    byte, owner = {}, {}
    for function, address, size, value in data_cells(listing):
        for i in range(size):
            byte[address + i] = (value >> 8 * i) & 0xFF
            owner[address + i] = function
    found = []
    for address in sorted(a for a in byte if a % 4 == 0):
        if all(address + i in byte for i in range(4)):
            word = sum(byte[address + i] << 8 * i for i in range(4))
            if name := system_class(word):
                found.append((owner[address], address, word, name))
    return found


def census(elf):
    """{'msr': {register: {function: instructions}}, 'mrs': {...}, 'sys': {'TLBI ALLE1': {function: instructions}, ...}, 'plain': {'SMC': {...}, ...},
    'indirect': count, 'data': words shown as data, 'undecoded': words llvm-objdump could not decode, 'data_system': data words that decode as a system
    instruction}. The inner mappings are Counters: their keys are the functions that hold the instruction (a set, as far as a caller that only
    tests membership or iterates is concerned) and their values how many instructions each holds."""
    listing = disassemble(elf)
    rows, data, undecoded = parse(listing)
    out = dict(msr={}, mrs={}, sys={}, plain={}, indirect=0, data=data, undecoded=undecoded, data_system=data_system_words(listing))
    for function, _, mnemonic, operands in rows:
        function = function_name(function)
        if mnemonic == 'msr':
            key, table = register_name(operands.split(',')[0]), out['msr']
        elif mnemonic == 'mrs':
            key, table = register_name(operands.split(',')[-1]), out['mrs']
        elif mnemonic in SYS_OPS:
            key, table = f'{mnemonic.upper()} {operands.split(",")[0].strip().upper()}', out['sys']
        elif mnemonic in PLAIN:
            key, table = mnemonic.upper(), out['plain']
        else:
            out['indirect'] += mnemonic in INDIRECT
            continue
        table.setdefault(key, collections.Counter())[function] += 1
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


def placed_entries(text):
    """[[binary, joined text]] of the entries of the `placed` sub-block of the el2_sysreg class of the record. The sub-block starts at a line of six
    spaces and `placed`; an entry starts at a line of nine spaces and `wrapper` or `monitor` and goes on over the lines of twelve spaces; the block
    ends at the next group, at the next class or at any other line."""
    entries, block, mode = [], False, False
    for line in text.splitlines():
        cls = CLASS_HEAD.match(line)
        if cls:
            block, mode = cls.group(1) == 'el2_sysreg', False
            continue
        if not block:
            continue
        if line and not line.startswith(' '):
            block = False
        elif PLACED_HEAD.match(line):
            mode = True
        elif GROUP_HEAD.match(line):
            mode = False
        elif mode and (item := PLACED_ITEM.match(line)):
            entries.append([item.group(1), item.group(2)])
        elif mode and entries and (more := PLACED_MORE.match(line)):
            entries[-1][1] += ' ' + more.group(1)
        elif mode and line.startswith(' ' * 9):
            continue            # prose of the sub-block: it must not start with `wrapper ` or `monitor `
        elif line.strip():
            mode = False
    return entries


def parse_placed(text):
    """({'wrapper': {item: Counter}, 'monitor': {item: Counter}}, [problems]) from the placed entries of the record. An entry reads
    `monitor TCR_EL2, SCTLR_EL2: fn, fn*2` and places every item before the colon in the functions after it with the instruction count after the `*` (one
    without), which is how placed_lines prints a binary. The item `data words` is placed by the words it holds in place of functions."""
    found, problems = {'wrapper': {}, 'monitor': {}}, []
    for binary, entry in placed_entries(text):
        items, colon, rest = entry.partition(': ')
        if not colon or not items.strip() or not rest.strip():
            problems.append(f'the record has a placed entry for the {binary} that does not read `item, item: function*count, ...`')
            continue
        spec = collections.Counter()
        for part in (p.strip() for p in rest.split(',')):
            name, star, count = part.partition('*')
            if not name.strip() or (star and not count.isdigit()):
                problems.append(f'the record places {items} in a way that does not parse: {part!r}')
                break
            spec[name.strip()] += int(count) if star else 1
        else:
            for item in (i.strip() for i in items.split(',')):
                if item in found[binary]:
                    problems.append(f'the record places {item} twice under the {binary}')
                found[binary][item] = spec
    return found, problems


def place_problems(cen, placed, what):
    """The problems between where one binary holds each register, operation and data word that decodes as a system instruction, and where the record
    places it. Complete in both directions: an item the binary holds and the record does not place, an item the record places and the binary lacks,
    and a function or a count that differs, are each a problem."""
    problems = []
    table = {**cen['msr'], **cen['sys'], **cen['plain']}
    words = collections.Counter(f'{word:#010x}' for _, _, word, _ in cen['data_system'])
    if words:
        table['data words'] = words
    for item, actual in sorted(table.items()):
        spec = placed.get(item)
        if spec is None:
            problems.append(f'{what}: holds {item} (in {", ".join(sorted(actual)[:3])}) and the record does not place it')
        elif item == 'data words':
            if spec != actual:
                problems.append(f'{what}: holds the data words {dict(sorted(actual.items()))} that decode as system instructions and the record places {dict(sorted(spec.items()))}')
        else:
            for function in sorted(set(actual) - set(spec)):
                problems.append(f'{what}: {item} is held in {function} ({actual[function]}) and the record does not place it there')
            for function in sorted(set(spec) - set(actual)):
                problems.append(f'the record places {item} in {function} under {what} and the binary does not hold it there')
            for function in sorted(set(actual) & set(spec)):
                if actual[function] != spec[function]:
                    problems.append(f'{what}: {item} is held {actual[function]} times in {function} and the record places it {spec[function]}')
    for item in sorted(set(placed) - set(table)):
        problems.append(f'the record places {item} under {what} and the binary does not write or execute it')
    return problems


def flat_bytes(elf):
    """The bytes of `elf` flattened as both builds flatten it (llvm-objcopy -O binary), made in a directory of build/ that is removed at once."""
    scratch = ROOT / 'build'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, prefix='census-') as directory:
        out = Path(directory) / 'flat.bin'
        done = subprocess.run([find_tool('llvm-objcopy'), '-O', 'binary', str(elf), str(out)], capture_output=True, text=True)
        if done.returncode:
            raise CensusError(f'llvm-objcopy refused {Path(elf).name}: {done.stderr.strip()[:200]}')
        return out.read_bytes()


def tie_problems(text, monitor_flat, wrapper_flat):
    """The problems between the flat bytes of the two ELFs and the wrapper and the monitor that section 1b of the record names (size and SHA-256)."""
    problems = []
    if monitor_flat is not None:
        named_monitor = re.search(r'^ {3}monitor\s+xnuboot\.bin at offset 0x[0-9a-f]+, (\d+) bytes, profile \S+\n {16}sha256 ([0-9a-f]{64})$', text, re.M)
        if not named_monitor or (int(named_monitor.group(1)), named_monitor.group(2)) != (len(monitor_flat), hashlib.sha256(monitor_flat).hexdigest()):
            problems.append('the flat bytes of the monitor ELF are not the monitor that section 1b of the record names (its size and sha256 lines)')
    if wrapper_flat is not None:
        named_wrapper = re.search(r'^ {3}wrapper\s+(\d+) bytes\s+sha256 ([0-9a-f]{64})$', text, re.M)
        if not named_wrapper or (int(named_wrapper.group(1)), named_wrapper.group(2)) != (len(wrapper_flat), hashlib.sha256(wrapper_flat).hexdigest()):
            problems.append('the flat bytes of the wrapper ELF are not the wrapper that section 1b of the record names (its bytes and sha256 line)')
    return problems


def check(text, monitor_census, wrapper_census=None, monitor_elf_sha256=None, monitor_flat=None, wrapper_flat=None):
    """[problems] of the record text against the census of the monitor (groups `monitor *`) and, when given, of the wrapper (group `wrapper`), by
    names, by places (every register, SYS operation and plain instruction of each binary, with the functions and the counts, and every data word that
    decodes as a system instruction), and by the flat bytes of the two ELFs against section 1b."""
    groups = groups_of(text)
    placed, problems = parse_placed(text)
    monitor = [t for g, t in groups.items() if g.startswith('monitor ')]
    if not monitor:
        problems.append('the record has no `monitor X:` group in its el2_sysreg class: it is not the record of a monitor entry run')
    else:
        problems += compare(monitor_census, monitor, 'the monitor')
        problems += place_problems(monitor_census, placed['monitor'], 'the monitor')
    if wrapper_census is not None:
        if 'wrapper' not in groups:
            problems.append('the record has no `wrapper:` group in its el2_sysreg class')
        else:
            problems += compare(wrapper_census, [groups['wrapper']], 'the wrapper')
            problems += place_problems(wrapper_census, placed['wrapper'], 'the wrapper')
    if monitor_elf_sha256 is not None:
        named_digest = re.search(r'^ {3}elf sha256\s+([0-9a-f]{64})', text, re.M)
        if not named_digest or named_digest.group(1) != monitor_elf_sha256:
            problems.append('the monitor ELF checked is not the one section 1b of the record names (its elf sha256 line)')
    return problems + tie_problems(text, monitor_flat, wrapper_flat)


def placed_lines(cen, binary, width=112):
    """The lines of the record's `placed` entries for `cen`: every register written, SYS operation, plain instruction and data word that decodes as a
    system instruction, with the functions and counts, in the form parse_placed reads. Items with the same placement share an entry, entries are
    ordered by their first item (the data words last), and an entry wraps between its comma-separated parts on lines of twelve spaces, none
    longer than `width`."""
    table = {**cen['msr'], **cen['sys'], **cen['plain']}
    words = collections.Counter(f'{word:#010x}' for _, _, word, _ in cen['data_system'])
    if words:
        table['data words'] = words
    by_spec = collections.defaultdict(list)
    for item, spec in sorted(table.items()):
        by_spec[tuple(sorted(spec.items()))].append(item)
    lines = []
    for spec, items in sorted(by_spec.items(), key=lambda kv: ('data words' in kv[1], kv[1][0])):
        parts = [f'{item},' for item in items[:-1]] + [f'{items[-1]}:'] + [f'{name}*{n},' if n > 1 else f'{name},' for name, n in spec]
        parts[-1] = parts[-1].rstrip(',')
        line = f'         {binary}'
        for part in parts:
            if len(line) + 1 + len(part) > width and line.strip() != binary:
                lines.append(line)
                line = '           '
            line += ' ' + part
        lines.append(line)
    return lines


def summary(cen, label):
    lines = [f'{label}: {len(cen["msr"])} registers written, {len(cen["mrs"])} read, {len(cen["sys"])} SYS operations, {sum(len(v) for v in cen["plain"].values())} '
             f'plain instructions, {cen["indirect"]} indirect branches (not followed), {cen["data"]} data words skipped, '
             f'{len(cen["data_system"])} of them decode as system instructions']
    lines += [f'  msr {name}: {", ".join(f"{f}*{n}" if n > 1 else f for f, n in sorted(fns.items())[:4])}{" ..." if len(fns) > 4 else ""}'
              for name, fns in sorted(cen['msr'].items())]
    lines += [f'  {name}: {", ".join(f"{f}*{n}" if n > 1 else f for f, n in sorted(fns.items())[:4])}{" ..." if len(fns) > 4 else ""}'
              for name, fns in sorted({**cen['sys'], **cen['plain']}.items())]
    lines += [f'  data word {word:#010x} ({name}) at {address:#x} in {function}' for function, address, word, name in cen['data_system']]
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('census').add_argument('elf')
    chk = sub.add_parser('check')
    chk.add_argument('--monitor-elf', required=True)
    chk.add_argument('--wrapper-elf')
    chk.add_argument('record')
    pl = sub.add_parser('placed')
    pl.add_argument('--binary', choices=('wrapper', 'monitor'), required=True)
    pl.add_argument('elf')
    a = ap.parse_args(argv)
    try:
        if a.command == 'census':
            print('\n'.join(summary(census(a.elf), Path(a.elf).name)))
            return 0
        if a.command == 'placed':
            print('\n'.join(placed_lines(census(a.elf), a.binary)))
            return 0
        text = Path(a.record).read_text(encoding='ascii')
        monitor = census(a.monitor_elf)
        wrapper = census(a.wrapper_elf) if a.wrapper_elf else None
        problems = check(text, monitor, wrapper, hashlib.sha256(Path(a.monitor_elf).read_bytes()).hexdigest(), flat_bytes(a.monitor_elf),
                         flat_bytes(a.wrapper_elf) if a.wrapper_elf else None)
        if not a.wrapper_elf:
            problems.append('the wrapper was not checked: give --wrapper-elf (the wrapper is tied to section 1b and placed only with it)')
    except (CensusError, OSError, UnicodeDecodeError) as error:
        print(f'CANNOT RUN: {error}', file=sys.stderr)
        return 2
    print('\n'.join(summary(monitor, 'the monitor') + summary(wrapper, 'the wrapper') if wrapper else summary(monitor, 'the monitor')))
    print('\n'.join(f'PROBLEM {p}' for p in problems) if problems else 'CHECK PASS: the record names and places what the binaries write and execute, and nothing they lack')
    print('INFERRED, not shown: which group is reached when; the indirect branches are not followed. Hardware NOT RUN.')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
