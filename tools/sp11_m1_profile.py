#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The SP11 milestone M1 monitor profile: its flag set, the NV2 record it requires, and the offline build.

    python3 tools/sp11_m1_profile.py show  --nv2-record-header FILE [--tree TREE] [--cpu-start stop|ack]
    python3 tools/sp11_m1_profile.py flags --nv2-record-header FILE [--tree TREE] [--cpu-start stop|ack]
    python3 tools/sp11_m1_profile.py build --nv2-record-header FILE OUT [--tree TREE] [--cpu-start stop|ack]

The profile is config/monitor-profile-sp11-m1.json laid over config/monitor-flags.json, which stays the A16 recipe and
is never edited for the SP11. It builds the monitor milestone M1 needs (docs/SP11-MONITOR-PORT.md sections 2 and 13,
WI 5; docs/SP11-MONITOR-PROFILE.md is the interface note): SNAP_PLATFORM_SP11, the queued service lock without which six
vCPUs refuse at -146, the five GIC macros, no relocation and no thermal define. Nothing in it is an A16 default.

--cpu-start chooses what the monitor does with the guest's first CPU-start write. `stop` is the default and the profile as it
always was: the write is a stage-2 fault and stops the run. `ack` adds -DSNAP_SP11_CPU_START_ACK=1 after the profile's defines (the
NV2 header define stays last) and records "cpu_start": "ack" in OUT/profile-build.json: an EXPERIMENTAL diagnostic that takes the
write and starts no CPU (docs/SP11-MONITOR-PROFILE.md section 10). A `stop` build's flags, record and monitor are byte for byte
what they were before the option existed.

The NV2 record is a required input and has no default. `show`, `flags` and `build` all refuse, with a nonzero exit, when
none is named, when the file is missing, a link, not a regular file, too large, not exactly the header
tools/nv2_record.py writes, damaged, for another SoC than the build's, or a record the launch gate would deny whatever
the live registers read. Which measurement to embed (the local one came from the first X2 image, HCR_EL2.E2H = 1, and the
monitor runs with E2H = 0) is the operator's decision D6; this tool prints what it embeds and never chooses. The
record does not say which regime it was measured in, so nothing here can check that.

`build` compiles with tools/monitor_build_offline.py (q1n1's sha256 sources come from the sibling q1n1 or from
$Q1N1_SP11_TREE; nothing is created outside TREE and OUT), requires the record's 96 bytes to occur exactly once in the
monitor it produced and the monitor to fit the code window probes/run.py reserves (OUT_AT - CODE_AT), and writes
OUT/profile-build.json, whose "flags" key rebuilds the same monitor with tools/monitor_build_offline.py. A monitor that
fails either check has its binary and ELF removed. Exit status: 0 success, 1 a build that failed or whose result was
removed, 2 a refusal. Nothing here touches hardware: the monitor is compile-checked, never run on a Surface Pro 11."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import monitor_build_offline  # noqa: E402
import nv2_record  # noqa: E402

PROFILE_PATH = 'config/monitor-profile-sp11-m1.json'
BASE_PATH = 'config/monitor-flags.json'
PROFILE_KEYS = ('profile', 'target', 'qualification', 'base_flags', 'defines', 'required_base_defines', 'required_input',
                'forbidden_define_names', 'forbidden_define_substrings', 'runtime_contract')
REQUIRED_DEFINES = {'SNAP_PLATFORM_SP11': '1', 'SNAP_FAIR_TOKEN': '1'}
GIC_MACROS = ('SNAP_GICD_BASE', 'SNAP_GICR_FIRST', 'SNAP_GICR_LIMIT', 'SNAP_GICR_STRIDE', 'SNAP_TIMER_INTID')
DEFINE = re.compile(r'-D([A-Z][A-Z0-9_]*)=([A-Za-z0-9_.+-]+)')
# The header is a few hundred bytes. The path ends up inside a C string literal in a -D, so it is a plain path.
MAX_HEADER_BYTES = 8192
SAFE_PATH = re.compile(r'/[A-Za-z0-9._/+@,:=-]+')
NO_RECORD = ('the sp11-m1 profile needs an NV2 record and has none by default: name a header written by '
             'tools/nv2_record.py with --nv2-record-header FILE. Which record to embed is operator decision D6 '
             '(docs/SP11-MONITOR-PORT.md section 9); this tool does not choose one')
# What the monitor does with the guest's first CPU-start write (docs/SP11-MONITOR-PROFILE.md section 10): the default stops the run on it; `ack` is the opt-in
# diagnostic that takes the write and starts no CPU. The define is code here, not a key of the profile file, so that the profile's keys and every digest that
# names it stay what they were.
CPU_STARTS = ('stop', 'ack')
CPU_START_DEFINES = {'stop': [], 'ack': ['-DSNAP_SP11_CPU_START_ACK=1']}


class Refused(Exception):
    """An input the profile will not build from; reported on stderr with exit status 2."""


def need(condition, message):
    if not condition:
        raise Refused(message)


# ---- the profile -------------------------------------------------------------------------------------------------

def read_json(path):
    def reject_duplicates(pairs):
        keys = [key for key, _ in pairs]
        need(len(keys) == len(set(keys)), f'{path}: a key occurs twice')
        return dict(pairs)
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=reject_duplicates)
    except (OSError, ValueError) as error:
        raise Refused(f'{path}: {error}') from error


def strings(value, what):
    need(isinstance(value, list) and all(isinstance(item, str) and item for item in value), f'{what} must be a list of strings')
    return value


def load_profile(tree):
    """(profile, base flags) from TREE, validated; the profile is data, but a damaged one must not build."""
    profile = read_json(Path(tree) / PROFILE_PATH)
    need(isinstance(profile, dict) and tuple(sorted(profile)) == tuple(sorted(PROFILE_KEYS)),
         f'{PROFILE_PATH}: the keys must be exactly {", ".join(PROFILE_KEYS)}')
    need(profile['profile'] == 'sp11-m1', f'{PROFILE_PATH}: not the sp11-m1 profile')
    for key in ('target', 'qualification'):
        need(isinstance(profile[key], str) and profile[key], f'{PROFILE_PATH}: {key} must be a non-empty string')
    need(profile['base_flags'] == BASE_PATH, f'{PROFILE_PATH}: the base is {BASE_PATH}, the A16 recipe, and nothing else')
    base = strings(read_json(Path(tree) / BASE_PATH).get('flags'), f'{BASE_PATH} flags')
    defines = {}
    for flag in strings(profile['defines'], 'defines'):
        match = DEFINE.fullmatch(flag)
        need(match, f'{PROFILE_PATH}: {flag!r} is not -DNAME=VALUE')
        need(match.group(1) not in defines, f'{PROFILE_PATH}: {match.group(1)} is defined twice')
        defines[match.group(1)] = match.group(2)
    for name, value in REQUIRED_DEFINES.items():
        need(defines.get(name) == value, f'{PROFILE_PATH}: -D{name}={value} is required')
    for name in GIC_MACROS:
        need(name in defines, f'{PROFILE_PATH}: -D{name} is required: an SP11 build has no GIC defaults')
    need(all(flag in base for flag in strings(profile['required_base_defines'], 'required_base_defines')),
         f'{BASE_PATH} lacks a define the profile requires: {profile["required_base_defines"]}')
    required = profile['required_input']
    need(isinstance(required, dict) and sorted(required) == ['define', 'meaning', 'soc'], f'{PROFILE_PATH}: required_input')
    need(required['define'] == 'SNAP_NV2_RECORD_HEADER' and required['soc'] in nv2_record.SOC and required['soc'] != 'unknown',
         f'{PROFILE_PATH}: required_input names another define or an unknown SoC')
    contract = profile['runtime_contract']
    need(isinstance(contract, dict) and contract.get('x4') == 0 and contract.get('early_stop_thermal_bit') == 'clear',
         f'{PROFILE_PATH}: the launch contract is x4 = 0 and the A16 thermal bit clear')
    names, substrings = strings(profile['forbidden_define_names'], 'names'), strings(profile['forbidden_define_substrings'], 'substrings')
    flags = [*base, *profile['defines']]
    need(len(flags) == len(set(flags)), 'the base and the profile define the same flag twice')
    for flag in flags:
        match = DEFINE.fullmatch(flag) or re.fullmatch(r'-D([A-Z][A-Z0-9_]*)', flag)
        if match:
            need(match.group(1) not in names, f'{flag} is forbidden in the sp11-m1 profile')
            need(not any(part in match.group(1) for part in substrings), f'{flag} is forbidden in the sp11-m1 profile')
    return profile, base


def platform_soc(tree):
    """The SoC the monitor's NV2 gate is built for (compat/nv2-gate.h SNAP_NV2_PLATFORM_SOC), as nv2_record names it."""
    found = re.search(r'#define\s+SNAP_NV2_PLATFORM_SOC\s+SNAP_NV2_SOC_([A-Z0-9_]+)\b',
                      (Path(tree) / 'compat/nv2-gate.h').read_text(encoding='utf-8'))
    need(found, 'compat/nv2-gate.h names no SNAP_NV2_PLATFORM_SOC')
    name = found.group(1).lower().replace('_', '-')
    need(name in nv2_record.SOC and name != 'unknown', f'compat/nv2-gate.h: SNAP_NV2_SOC_{found.group(1)} is not a SoC of tools/nv2_record.py')
    return name


# ---- the record --------------------------------------------------------------------------------------------------

class RecordInfo:
    def __init__(self, path, header, data, record):
        self.path, self.data, self.record = path, data, record
        self.header_sha256 = hashlib.sha256(header).hexdigest()
        self.record_sha256 = hashlib.sha256(data).hexdigest()
        self.crc32 = struct.unpack('<I', data[nv2_record.CRC_OFFSET:])[0]


def read_header_bytes(path):
    """The bytes of a regular file that is not a link, at most MAX_HEADER_BYTES; the descriptor must be the file inspected."""
    try:
        listed = os.lstat(path)
    except OSError as error:
        raise Refused(f'{path}: {error.strerror}') from error
    need(not stat.S_ISLNK(listed.st_mode), f'{path}: a link is refused; name the file itself')
    need(stat.S_ISREG(listed.st_mode), f'{path}: not a regular file')
    need(listed.st_size <= MAX_HEADER_BYTES, f'{path}: larger than {MAX_HEADER_BYTES} bytes, so not an NV2 record header')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except OSError as error:
        raise Refused(f'{path}: {error.strerror}') from error
    try:
        opened = os.fstat(fd)
        need(stat.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino) == (listed.st_dev, listed.st_ino),
             f'{path}: not the file that was inspected')
        chunks, total = [], 0
        while True:
            block = os.read(fd, 4096)
            if not block:
                return b''.join(chunks)
            total += len(block)
            need(total <= MAX_HEADER_BYTES, f'{path}: larger than {MAX_HEADER_BYTES} bytes, so not an NV2 record header')
            chunks.append(block)
    finally:
        os.close(fd)


def read_record(path, expected_soc):
    """RecordInfo for the header at `path`, or Refused. The header is #included into the monitor, so it must be exactly
    the text tools/nv2_record.py writes for the bytes it carries: any other content would be code in the monitor."""
    need(path, NO_RECORD)
    header = read_header_bytes(path)
    need(header, f'{path}: empty')
    try:
        text = header.decode('ascii')
    except UnicodeDecodeError as error:
        raise Refused(f'{path}: not ASCII text ({error})') from error
    data = bytes(int(token, 16) for token in re.findall(r'\b0x([0-9a-f]{2})\b', text))
    need(len(data) == nv2_record.SIZE, f'{path}: carries {len(data)} record bytes, a record is {nv2_record.SIZE}')
    verdict, record = nv2_record.parse_binary(data)
    need(verdict == nv2_record.PARSED, f'{path}: the record is damaged ({nv2_record.verdict_name(verdict)})')
    need(nv2_record.emit_header(data) == text,
         f'{path}: not the header tools/nv2_record.py writes for this record (edited, another version, or other content '
         'after the bytes); regenerate it with `tools/nv2_record.py ingest`')
    info = RecordInfo(Path(path).resolve(), header, data, record)
    need(SAFE_PATH.fullmatch(str(info.path)),
         f'{info.path}: the path goes into a C string literal; keep the header under a plain path (letters, digits and ._/+@,:=-)')
    soc = nv2_record.fields_of(record)['soc']
    need(soc == expected_soc, f'{path}: the record is for SoC {soc}, this build is for {expected_soc}: its launch would be '
                              f'refused with prepare status {nv2_record.STATUS_MISMATCH}')
    blocker = nv2_record.record_only_denial(record)
    if blocker is not None:
        raise Refused(f'{path}: the launch gate would deny this record whatever the live registers read: '
                      f'{nv2_record.verdict_name(blocker)} (prepare status {nv2_record.verdict_status(blocker)})')
    return info


def identity_lines(info):
    fields = nv2_record.fields_of(info.record)
    lines = [f'NV2 record   {info.path}',
             f'  {"header sha256":<24} {info.header_sha256}',
             f'  {"record sha256":<24} {info.record_sha256}   crc32 {info.crc32:#010x}']
    lines += [f'  {key:<24} {fields[key]}' for key in ('soc', 'build_id', 'tool', 'midr_el1', 'revidr_el1',
                                                       'id_aa64mmfr2_el1', 'id_aa64mmfr4_el1', 'vncr_redirect',
                                                       'negative_control_trapped', 'evidence')]
    lines += [f'  {"regime":<24} NOT RECORDED: the record does not say whether HCR_EL2.E2H was 0 or 1 when it was measured,',
              f'  {"":<24} and the monitor runs with E2H = 0. Which record to embed is operator decision D6;',
              f'  {"":<24} nothing here chooses, and the launch gate also compares MIDR, REVIDR and both ID registers',
              f'  {"":<24} with the CPU it runs on']
    return lines


# ---- flags and build ---------------------------------------------------------------------------------------------

def profile_flags(profile, base, info, cpu_start='stop'):
    need(cpu_start in CPU_STARTS, f'--cpu-start is one of {", ".join(CPU_STARTS)}, not {cpu_start!r}')
    return [*base, *profile['defines'], *CPU_START_DEFINES[cpu_start], f'-D{profile["required_input"]["define"]}="{info.path}"']


def prepare(tree, header, cpu_start='stop'):
    tree = Path(tree).resolve()
    profile, base = load_profile(tree)
    need(profile['required_input']['soc'] == platform_soc(tree),
         f'{PROFILE_PATH} names SoC {profile["required_input"]["soc"]} but compat/nv2-gate.h builds for {platform_soc(tree)}')
    info = read_record(header, profile['required_input']['soc'])
    return tree, profile, info, profile_flags(profile, base, info, cpu_start)


def code_window(tree):
    """OUT_AT - CODE_AT of probes/run.py, the bytes a monitor may take there (build() refuses a larger blob)."""
    at = [monitor_build_offline.run_py_literal(Path(tree), name) for name in ('CODE_AT', 'OUT_AT')]
    need(all(isinstance(value, int) for value in at) and at[1] > at[0], 'probes/run.py has no CODE_AT and OUT_AT literals')
    return at[1] - at[0]


def build(tree, out, profile, info, flags, cpu_start='stop'):
    out = Path(out)
    need(not out.exists(), f'{out} exists: the build goes into a fresh directory')
    uefi, why = monitor_build_offline.locate_q1n1(tree)
    need(uefi is not None, why)
    window = code_window(tree)
    try:
        built = monitor_build_offline.build(tree, out, flags)
    except subprocess.CalledProcessError as error:
        print(f'build failed: {Path(str(error.cmd[0])).name} exited {error.returncode}', file=sys.stderr)
        return 1
    blob = (out / 'xnuboot.bin').read_bytes()
    carried = blob.count(info.data)
    problem = (f'the monitor carries the NV2 record {carried} times, not once' if carried != 1 else
               f'the monitor is {len(blob)} bytes, more than the {window} that probes/run.py reserves for it'
               if len(blob) > window else None)
    if problem:
        for name in ('xnuboot.bin', 'xnuboot.elf'):
            (out / name).unlink(missing_ok=True)
        print(f'{problem}; its binary and ELF were removed', file=sys.stderr)
        return 1
    record = dict(format=1, profile=profile['profile'], hardware='NOT RUN', flags=flags,
                  nv2_record=dict(header_sha256=info.header_sha256, record_sha256=info.record_sha256,
                                  **nv2_record.fields_of(info.record)), monitor=built)
    if cpu_start != 'stop':
        record['cpu_start'] = cpu_start      # only a variant says so: the record of a `stop` build is what it was before the option
    (out / 'profile-build.json').write_text(json.dumps(record, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(dict(profile=profile['profile'], out=str(out), record_sha256=info.record_sha256, **built)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = ap.add_subparsers(dest='command', required=True)
    for name in ('show', 'flags', 'build'):
        sub = commands.add_parser(name)
        sub.add_argument('--nv2-record-header', metavar='FILE', default=None)
        sub.add_argument('--tree', default=str(ROOT))
        sub.add_argument('--cpu-start', choices=CPU_STARTS, default='stop',
                         help='what the monitor does with the guest\'s first CPU-start write: stop (the default, a stage-2 fault ends the run) or ack (EXPERIMENTAL: '
                              'take the write, start no CPU)')
        if name == 'build':
            sub.add_argument('out')
    args = ap.parse_args(argv)
    try:
        tree, profile, info, flags = prepare(args.tree, args.nv2_record_header, args.cpu_start)
        if args.command == 'flags':
            print(json.dumps({'flags': flags}, indent=1))
            return 0
        print(f'profile      {profile["profile"]}: {profile["target"]}')
        print(f'qualification {profile["qualification"]}')
        print('\n'.join(identity_lines(info)))
        print('defines      ' + ' '.join([*profile['defines'], *CPU_START_DEFINES[args.cpu_start], flags[-1]]))
        if args.cpu_start == 'ack':
            print('cpu start    ACK: EXPERIMENTAL diagnostic, not a model of the hardware: a 32-bit store to the PMGR CPU-start block is taken and ignored, no CPU is '
                  'started (docs/SP11-MONITOR-PROFILE.md section 10)')
        if args.command == 'show':
            return 0
        return build(tree, args.out, profile, info, flags, args.cpu_start)
    except Refused as refusal:
        print(f'REFUSED: {refusal}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
