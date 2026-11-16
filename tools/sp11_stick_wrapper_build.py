#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Builds the two payloads of the SP11 bundle dry run (docs/SP11-STICK-WRAPPER.md): SNAPMON.BIN, the wrapper and the loader with NO
monitor in it, and SNAPREP.BIN, the read-only reporter, with the recipe of q1n1's stick payloads.

    export Q1N1_SP11_TREE=<q1n1 checkout with platform/uefi/Makefile and payloads/>     (absent or wrong: REFUSED=Q1N1_TREE_ABSENT)
    python3 tools/sp11_stick_wrapper_build.py --out build/sp11-wrapper [--profile config/monitor-profile-sp11-m1.json] [--gic]

With --gic the tool builds the opt-in GIC and stop variant instead, SNAPMON-GIC.BIN and SNAPREP-GIC.BIN (-DSNAP_SP11_STICK_GIC=1 on top of the
same recipe; docs/SP11-STICK-GIC.md). Without it the output is, byte for byte, what ran on the unit.

With --monitor and --monitor-build the tool builds the monitor entry variant instead, SNAPMON-M1.BIN and SNAPREP-M1.BIN
(docs/SP11-STICK-MONITOR.md): the wrapper image padded to a fixed offset, then the profile build's xnuboot.bin byte for byte, called by a
direct `bl` to the symbol sp11_monitor that --defsym places at that offset. The monitor must be the output of the profile build that
--monitor-build names (python3 tools/sp11_m1_profile.py build ... writes profile-build.json), must start at 0 with its vectors at 0x800 and
must be position independent: its objects are relinked with --emit-relocs, the flat bytes must equal the monitor's, q1n1's ELF check must
pass on the result and the entry and vectors are checked in that relinked ELF. Build order: monitor, its digest, wrapper, concatenation.
What that proof cannot show: an address dependence that is not a relocation, an address built from integers, .init_array and TLS are not
examined, and profile-build.json is self-attesting: the tool holds its flags to the profile's rules and its digests to the files beside it,
and nothing proves that the objects were compiled with those flags.

The recipe is q1n1's and is read from its Makefile, not copied: PAYLOAD_CFLAGS and PAYLOAD_LDFLAGS (the flags, --emit-relocs among
them), payloads/start.S first, payloads/payload.ld, then llvm-objcopy -O binary, then q1n1's tools/sp11_payload_elfcheck.py on each ELF.
A change of that recipe changes the bytes, and therefore the digest an authorisation names; the tool prints the digest of every q1n1 file
it used so that a record can say which. Added to the recipe: -DSNAP_PLATFORM_SP11=1 -DSNAP_SP11_STICK=1 (the gate, default off, probes/
sp11-stick-hw.h), the include directories of probes/ and of q1n1's payloads/ and platform/uefi/, and for SNAPMON the five GIC macros that
PIN.DAT pins: the build has no monitor, so it takes them from the profile of the monitor build it stands for (the default is
config/monitor-profile-sp11-m1.json; a value is a number, nothing else).

Same inputs, same bytes: nothing of the build host is in a flat binary (no debug information, no __FILE__). Output only below build/.
A refusal prints REFUSED=<NAME> as the last line of standard output and the reason on standard error, and exits 1; a build ends with
HARDWARE=NOT RUN and exits 0. EXPERIMENTAL."""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('SNAPMON.BIN', 'SNAPREP.BIN')
GIC_NAMES = ('SNAPMON-GIC.BIN', 'SNAPREP-GIC.BIN')
SOURCES = {'SNAPMON.BIN': ('probes/sp11-stick-wrapper.c', 'probes/sp11-stick-load.c', 'probes/sp11-stick-report.c', 'platform/uefi/sha256.c'),
           'SNAPREP.BIN': ('probes/sp11-stick-reporter.c',),
           # The opt-in GIC and stop variant (docs/SP11-STICK-GIC.md): the same sources and the stage in two halves (the arm and put-back, then the
           # dry run's own); the vector tables are assembly in a C file.
           'SNAPMON-GIC.BIN': ('probes/sp11-stick-wrapper.c', 'probes/sp11-stick-load.c', 'probes/sp11-stick-report.c', 'probes/sp11-stick-gic.c',
                               'probes/sp11-stick-gic-dry.c', 'probes/sp11-stick-gic-vec.c', 'platform/uefi/sha256.c'),
           'SNAPREP-GIC.BIN': ('probes/sp11-stick-reporter.c',),
           # The monitor entry variant (docs/SP11-STICK-MONITOR.md): the arm and put-back half of the GIC stage only (built with SW_GIC_DRY_REGS=0, so
           # no CNTHP, VBAR_EL2 or HCR_EL2 write is in it), and the monitor stage in place of the dry run's.
           'SNAPMON-M1.BIN': ('probes/sp11-stick-wrapper.c', 'probes/sp11-stick-load.c', 'probes/sp11-stick-report.c', 'probes/sp11-stick-gic.c',
                              'probes/sp11-stick-monitor.c', 'platform/uefi/sha256.c'),
           'SNAPREP-M1.BIN': ('probes/sp11-stick-reporter.c',)}
MONITOR_NAMES = ('SNAPMON-M1.BIN', 'SNAPREP-M1.BIN')
MONITOR_OFFSET = 0x20000                # where the monitor starts in SNAPMON-M1.BIN: a multiple of 4 KiB, so its vectors (at +0x800) are 2 KiB aligned
MONITOR_MAX = 8 * 1024 * 1024           # a sanity bound on the appended image; the runner's own cap is far higher
MONITOR_CALL = 'sp11_monitor'           # the symbol the wrapper calls, defined by --defsym
BUILD_RECORD_FORMAT = 1                 # tools/sp11_m1_profile.py: profile-build.json "format"
GIC_MACROS = ('SNAP_GICD_BASE', 'SNAP_GICR_FIRST', 'SNAP_GICR_LIMIT', 'SNAP_GICR_STRIDE', 'SNAP_TIMER_INTID')
Q1N1_INPUTS = ('platform/uefi/Makefile', 'payloads/start.S', 'payloads/payload.ld', 'payloads/payload-util.h', 'platform/uefi/sp11-payload-abi.h',
               'platform/uefi/preload.h', 'platform/uefi/efi.h', 'platform/uefi/sha256.c', 'platform/uefi/sha256.h', 'tools/sp11_payload_elfcheck.py')
REPORT_NAME = 'build-report.json'
REPORTER_MAX = 64 * 1024
REFUSALS = ('Q1N1_TREE_ABSENT', 'PROFILE_BAD', 'RECIPE_CHANGED', 'TOOL_MISSING', 'OUT_DIR_REFUSED', 'OUT_NOT_EMPTY', 'BUILD_FAILED',
            'ELF_REFUSED', 'UNDEFINED_SYMBOL', 'REPORTER_TOO_BIG')
# The refusals of the monitor entry variant (docs/SP11-STICK-MONITOR.md section 7), apart from the older ones that docs/SP11-STICK-WRAPPER.md lists.
MONITOR_REFUSALS = ('MONITOR_BUILD_BAD', 'MONITOR_MISMATCH', 'MONITOR_LAYOUT', 'MONITOR_NOT_PIC', 'GIC_MACROS_DIFFER', 'WRAPPER_TOO_BIG', 'IMAGE_TOO_BIG')
LLVM_DIRS = ('/opt/homebrew/opt/llvm/bin', '/opt/homebrew/opt/lld/bin')


class Refused(Exception):
    def __init__(self, name, detail=''):
        assert name in REFUSALS + MONITOR_REFUSALS, name          # a typo must not become a new refusal
        super().__init__(f'{name}: {detail}')
        self.name, self.detail = name, detail


def sha(data):
    return hashlib.sha256(data).hexdigest()


def q1n1_tree():
    """$Q1N1_SP11_TREE when set, even if empty or wrong (no other tree is substituted); else the checkouts beside this one."""
    named = os.environ.get('Q1N1_SP11_TREE')
    candidates = [Path(named)] if named and named.strip() else []
    if named is None:
        for base in (ROOT.parent, ROOT.parent.parent, ROOT.parent.parent.parent):
            candidates += [base / 'q1n1', *sorted(base.glob('q1n1-worktrees/*sp11*'))]
    tree = next((t for t in candidates if all((t / rel).is_file() for rel in Q1N1_INPUTS)), None)
    if tree is None:
        raise Refused('Q1N1_TREE_ABSENT', 'set Q1N1_SP11_TREE to a q1n1 checkout that holds ' + ', '.join(Q1N1_INPUTS))
    return tree


def tool(name):
    found = shutil.which(name) or next((str(Path(d) / name) for d in LLVM_DIRS if (Path(d) / name).is_file()), None)
    if not found:
        raise Refused('TOOL_MISSING', f'{name} is not on PATH or in {LLVM_DIRS}')
    return found


def make_variable(text, name):
    """The value of `NAME := ...` in a Makefile, backslash continuations joined; nothing is expanded, so a $(...) is refused."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        found = re.match(rf'^{name}\s*:?=\s*(.*)$', line)
        if not found:
            continue
        value = found.group(1)
        while value.rstrip().endswith('\\') and i + 1 < len(lines):
            i += 1
            value = value.rstrip()[:-1] + ' ' + lines[i].strip()
        if '$(' in value or '${' in value:
            raise Refused('RECIPE_CHANGED', f'{name} uses a make variable; this tool does not expand them')
        return shlex.split(value)
    raise Refused('RECIPE_CHANGED', f'platform/uefi/Makefile has no {name}')


def recipe_flags(tree):
    """(compile flags, link flags) of q1n1's payload recipe: its own variables, without the dependency-file flags, with its relative -I
    directories made absolute."""
    text = (tree / 'platform/uefi/Makefile').read_text()
    cflags = [f'-I{tree / f[2:]}' if f.startswith('-I') and not Path(f[2:]).is_absolute() else f
              for f in make_variable(text, 'PAYLOAD_CFLAGS') if f not in ('-MMD', '-MP')]
    ldflags = make_variable(text, 'PAYLOAD_LDFLAGS')
    if not {'--target=aarch64-unknown-none-elf', '-ffreestanding', '-fno-pic', '-mgeneral-regs-only'} <= set(cflags) or '--emit-relocs' not in ldflags:
        raise Refused('RECIPE_CHANGED', 'the payload recipe no longer has the flags this tool relies on')
    return cflags, ldflags


def gic_defines(profile):
    """The five GIC macros of the monitor build, from the `defines` of a monitor profile: each exactly once and a plain number."""
    try:
        defines = json.loads(Path(profile).read_text())['defines']
        assert isinstance(defines, list)
    except (OSError, ValueError, KeyError, AssertionError) as error:
        raise Refused('PROFILE_BAD', f'{profile}: no usable "defines" list ({error})') from None
    found = {}
    for item in defines:
        match = re.fullmatch(r'-D(\w+)=(.*)', item) if isinstance(item, str) else None
        if match and match.group(1) in GIC_MACROS:
            if match.group(1) in found:
                raise Refused('PROFILE_BAD', f'{match.group(1)} is defined twice')
            if not re.fullmatch(r'0[xX][0-9A-Fa-f]+|[0-9]+', match.group(2)):
                raise Refused('PROFILE_BAD', f'{match.group(1)}={match.group(2)!r} is not a number')
            found[match.group(1)] = match.group(2)
    if sorted(found) != sorted(GIC_MACROS):
        raise Refused('PROFILE_BAD', f'the profile lacks {sorted(set(GIC_MACROS) - set(found))}')
    return [f'-D{name}={found[name]}' for name in GIC_MACROS]


def check_out(out_arg, force):
    out = Path(os.path.abspath(out_arg))
    below = ROOT / 'build'
    if '..' in Path(out_arg).parts or below not in out.parents:
        raise Refused('OUT_DIR_REFUSED', f'--out must be below {below}, with no ".."')
    probe = out
    while probe != probe.parent:
        if probe.is_symlink():
            raise Refused('OUT_DIR_REFUSED', f'{probe} is a symbolic link')
        probe = probe.parent
    if str(out).startswith(('/Volumes/', '/dev/')):
        raise Refused('OUT_DIR_REFUSED', 'a mounted volume or a device is never written')
    if out.exists() and (not out.is_dir() or any(out.iterdir())) and not (out.is_dir() and force and (out / REPORT_NAME).is_file()):
        raise Refused('OUT_NOT_EMPTY', f'{out_arg} is not an empty directory; --force replaces only a directory with a {REPORT_NAME} of this tool')
    return out


def run(cmd, what, cwd=None):
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if done.returncode:
        raise Refused('BUILD_FAILED', f'{what} failed ({done.returncode}):\n{(done.stderr or done.stdout)[-3000:]}')
    return done


def build_one(name, tree, cflags, ldflags, defines, out, extra=(), extra_ld=()):
    """Compile start.S and the sources of `name`, link, check the ELF, flatten. Returns the flat bytes. `extra` and `extra_ld` are the monitor
    variant's: its defines, and --defsym for the symbol the wrapper calls."""
    clang, lld, objcopy, readelf, nm = (tool(t) for t in ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-readelf', 'llvm-nm'))
    stem = name.split('.')[0].lower()
    obj = out / 'obj' / stem
    obj.mkdir(parents=True)
    flags = [*cflags, f'-I{tree / "payloads"}', '-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1',
             *(['-DSNAP_SP11_STICK_GIC=1'] if name in GIC_NAMES + MONITOR_NAMES else []),
             *(['-DSNAP_SP11_STICK_MONITOR=1'] if name in MONITOR_NAMES else []),
             *(defines if name in ('SNAPMON.BIN', 'SNAPMON-GIC.BIN', 'SNAPMON-M1.BIN') else []), *extra]
    objects = []
    for source in ('payloads/start.S', *SOURCES[name]):
        base = tree if source.startswith(('payloads/', 'platform/')) else ROOT
        target = obj / (Path(source).stem + '.o')
        run([clang, *flags, '-c', str(base / source), '-o', str(target)], f'compiling {source}')
        objects.append(str(target))
    elf = out / f'{stem}.elf'
    run([lld, *ldflags, *extra_ld, '-T', str(tree / 'payloads/payload.ld'), '-o', str(elf), *objects], f'linking {name}')
    checked = subprocess.run([sys.executable, '-B', str(tree / 'tools/sp11_payload_elfcheck.py'), '--readelf', readelf, str(elf)], capture_output=True, text=True)
    if checked.returncode:
        raise Refused('ELF_REFUSED', f'{name}: {checked.stderr.strip() or checked.stdout.strip()}')
    undefined = run([nm, '-u', str(elf)], f'listing the undefined symbols of {name}').stdout.strip()
    if undefined:
        raise Refused('UNDEFINED_SYMBOL', f'{name} needs {undefined.split()[-1]}: a payload has no libc')
    binary = out / name
    run([objcopy, '-O', 'binary', str(elf), str(binary)], f'flattening {name}')
    data = binary.read_bytes()
    if name.startswith('SNAPREP') and len(data) >= REPORTER_MAX:
        raise Refused('REPORTER_TOO_BIG', f'{len(data)} bytes; the reporter is under {REPORTER_MAX}')
    return data


def build(out_arg, profile, force=False, gic=False):
    """Build both payloads below `out_arg` (with `gic`, the opt-in GIC and stop variant, SNAPMON-GIC.BIN and SNAPREP-GIC.BIN); returns the
    report dict, which is also written as build-report.json. The default build and its report are what they were before the variant existed."""
    tree = q1n1_tree()
    out = check_out(out_arg, force)
    cflags, ldflags = recipe_flags(tree)
    defines = gic_defines(profile)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    files = {name: build_one(name, tree, cflags, ldflags, defines, out) for name in (GIC_NAMES if gic else NAMES)}
    shown = [f.replace(str(tree), '<q1n1>') for f in cflags]
    report = {'files': {n: {'bytes': len(d), 'sha256': sha(d)} for n, d in files.items()},
              'q1n1_inputs': {rel: sha((tree / rel).read_bytes()) for rel in Q1N1_INPUTS}, 'cflags': shown, 'ldflags': ldflags,
              'added': ['-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1'], 'gic_macros': defines, 'monitor': 'none: the wrapper never enters one',
              'hardware': 'NOT RUN'}
    if gic:
        report['added'].append('-DSNAP_SP11_STICK_GIC=1')
        report['variant'] = 'gic: the stage after COPIED arms the EL2 timer and writes GIC and EL2 registers (docs/SP11-STICK-GIC.md)'
    (out / REPORT_NAME).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')
    return report


def macros_of(flags):
    """{NAME: number} of the five GIC macros among -DNAME=value flags, each exactly once and a plain number, or None when that does not hold."""
    found = {}
    for item in flags:
        match = re.fullmatch(r'-D(\w+)=(.*)', item) if isinstance(item, str) else None
        if match and match.group(1) in GIC_MACROS:
            if match.group(1) in found or not re.fullmatch(r'0[xX][0-9A-Fa-f]+|[0-9]+', match.group(2)):
                return None
            found[match.group(1)] = int(match.group(2), 0)
    return found if sorted(found) == sorted(GIC_MACROS) else None


def flag_problems(flags):
    """[what] in the `flags` of a profile-build.json that the rules of tools/sp11_m1_profile.py load_profile (read from this tree's profile) refuse:
    a required define that is absent or has another value, a define whose name is forbidden or holds a forbidden part (THERMAL), one that occurs twice,
    a base define the profile requires that is missing, and a record that does not carry the NV2 record's header define exactly once. The profile tool
    appends that define itself, so it is the one name the forbidden list may not apply to."""
    try:
        import sp11_m1_profile as profile_tool
    except ImportError as error:
        raise Refused('BUILD_FAILED', f'tools/sp11_m1_profile.py cannot be imported: {error}') from None
    try:
        profile, _ = profile_tool.load_profile(ROOT)
    except profile_tool.Refused as error:
        raise Refused('PROFILE_BAD', str(error)) from None
    header, defines, problems = profile['required_input']['define'], {}, []
    for flag in flags:
        match = re.fullmatch(r'-D([A-Z][A-Z0-9_]*)(?:=(.*))?', flag)
        if not match:
            continue
        name = match.group(1)
        if name in defines:
            problems.append(f'-D{name} occurs twice')
        defines[name] = match.group(2)
        if name != header and (name in profile['forbidden_define_names'] or any(part in name for part in profile['forbidden_define_substrings'])):
            problems.append(f'-D{name} is forbidden in the sp11-m1 profile')
    problems += [f'-D{name}={value} is required' for name, value in profile_tool.REQUIRED_DEFINES.items() if defines.get(name) != value]
    problems += [f'{flag} is required' for flag in profile['required_base_defines'] if flag not in flags]
    problems += [] if header in defines else [f'-D{header} is missing']
    return problems


def read_monitor(monitor_arg, record_arg):
    """(the monitor's bytes, the build record, its directory): the monitor must be the output of the profile build that `record_arg` (its
    profile-build.json) describes: the record's own digests, and the ELF and objects beside it, say so. The record is a file the profile tool wrote
    and nothing authenticates it (self-attesting): its digests tie the files together and its flags are held to the profile's rules, but nothing
    proves that the objects were compiled with those flags."""
    try:
        record = json.loads(Path(record_arg).read_text())
        built = record['monitor']
        assert record['format'] == BUILD_RECORD_FORMAT and record['profile'] == 'sp11-m1' and record['hardware'] == 'NOT RUN'
        assert isinstance(record['flags'], list) and isinstance(built['bytes'], int) and isinstance(record['nv2_record'], dict)
        assert all(re.fullmatch(r'[0-9a-f]{64}', built[k]) for k in ('sha256', 'elf_sha256')) and macros_of(record['flags']) is not None
        assert all(isinstance(flag, str) for flag in record['flags'])
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as error:
        raise Refused('MONITOR_BUILD_BAD', f'{record_arg} is not the record of an sp11-m1 profile build ({error!r})') from None
    if problems := flag_problems(record['flags']):
        raise Refused('MONITOR_BUILD_BAD', f'{record_arg}: the flags of the build break the rules of the sp11-m1 profile: {"; ".join(problems[:3])}')
    directory = Path(record_arg).resolve().parent
    try:
        monitor = Path(monitor_arg).read_bytes()
        elf = (directory / 'xnuboot.elf').read_bytes()
    except OSError as error:
        raise Refused('MONITOR_BUILD_BAD', f'cannot read the monitor or the ELF beside the record: {error}') from None
    if len(monitor) != built['bytes'] or sha(monitor) != built['sha256'] or sha(elf) != built['elf_sha256']:
        raise Refused('MONITOR_MISMATCH', f'{monitor_arg} is not the xnuboot.bin that {record_arg} recorded (bytes, SHA-256 or the ELF beside it differ)')
    if not 0 < len(monitor) <= MONITOR_MAX:
        raise Refused('IMAGE_TOO_BIG', f'the monitor is {len(monitor)} bytes; the bound is {MONITOR_MAX}')
    return monitor, record, directory


def check_layout(elf):
    """The entry is the image's first byte and the vector table 2 KiB in: what the wrapper's `bl` and the monitor's `msr vbar_el2` rely on."""
    symbols = {}
    for line in run([tool('llvm-nm'), str(elf)], 'listing the symbols of the monitor').stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] in ('_start', 'xnuboot_vectors'):
            symbols[parts[2]] = int(parts[0], 16)
    if symbols.get('_start') != 0 or symbols.get('xnuboot_vectors') != 0x800:
        raise Refused('MONITOR_LAYOUT', f'_start must be at 0 and xnuboot_vectors at 0x800; the ELF has {symbols}')


def monitor_objects(directory, flags):
    """The objects of the profile build, in link order, from the directory it wrote them to."""
    try:
        import monitor_build_offline as offline
    except ImportError as error:
        raise Refused('BUILD_FAILED', f'tools/monitor_build_offline.py cannot be imported: {error}') from None
    objects = []
    for rel in offline.multi_sources(ROOT, flags):
        path = directory / (Path(rel).stem + '-' + Path(rel).suffix.lstrip('.') + '.o')
        if not path.is_file():
            raise Refused('MONITOR_BUILD_BAD', f'{path} is missing: the directory is not the whole output of the profile build')
        objects.append(path)
    return objects


def relink_check(objects, tree, out, monitor):
    """The proof that the monitor does not depend on the address it is loaded at: `objects` relinked with --emit-relocs under the monitor's own
    linker script must pass q1n1's ELF check (no absolute relocation) and flatten to exactly `monitor`. Returns the relocation counts."""
    lld, objcopy, readelf = tool('ld.lld'), tool('llvm-objcopy'), tool('llvm-readelf')
    out.mkdir(parents=True, exist_ok=True)
    elf, flat = out / 'xnuboot-relocs.elf', out / 'xnuboot-relocs.bin'
    run([lld, '--emit-relocs', '-T', str(ROOT / 'probes/probe.ld'), '-o', str(elf), *map(str, objects)], 'relinking the monitor with --emit-relocs')
    checked = subprocess.run([sys.executable, '-B', str(tree / 'tools/sp11_payload_elfcheck.py'), '--readelf', readelf, str(elf)], capture_output=True, text=True)
    if checked.returncode:
        raise Refused('MONITOR_NOT_PIC', checked.stderr.strip() or checked.stdout.strip())
    run([objcopy, '-O', 'binary', str(elf), str(flat)], 'flattening the relinked monitor')
    if flat.read_bytes() != monitor:
        raise Refused('MONITOR_MISMATCH', 'the objects of the profile build do not link to the monitor given (an edited object or another link)')
    counts = {}
    for kind in re.findall(r'R_AARCH64_\w+', run([readelf, '-r', str(elf)], 'listing the relocations').stdout):
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def monitor_defines(monitor, offset):
    """What the wrapper is built with to know the image it calls: where it is, how long, and its SHA-256 as four little-endian words."""
    digest = hashlib.sha256(monitor).digest()
    return [f'-DSP11_MONITOR_OFFSET={offset:#x}', f'-DSP11_MONITOR_BYTES={len(monitor)}',
            *[f'-DSP11_MONITOR_SHA{i}={int.from_bytes(digest[8 * i:8 * i + 8], "little"):#x}ULL' for i in range(4)]]


def build_monitor(out_arg, profile, monitor_arg, record_arg, force=False, offset=MONITOR_OFFSET):
    """Build SNAPMON-M1.BIN (the wrapper padded to `offset`, then the monitor) and SNAPREP-M1.BIN below `out_arg`; returns the report dict,
    which is also written as build-report.json."""
    tree = q1n1_tree()
    out = check_out(out_arg, force)
    cflags, ldflags = recipe_flags(tree)
    defines = gic_defines(profile)
    monitor, record, directory = read_monitor(monitor_arg, record_arg)
    if macros_of(defines) != macros_of(record['flags']):
        raise Refused('GIC_MACROS_DIFFER', f'the profile {profile} and the monitor build disagree about {sorted(GIC_MACROS)}')
    if offset <= 0 or offset % 4096:
        raise Refused('MONITOR_LAYOUT', f'the monitor offset {offset:#x} must be a positive multiple of 4 KiB')
    check_layout(directory / 'xnuboot.elf')
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    pic = relink_check(monitor_objects(directory, record['flags']), tree, out / 'monitor-pic', monitor)
    check_layout(out / 'monitor-pic' / 'xnuboot-relocs.elf')       # the ELF that was proved to flatten to these bytes, not only the record's
    pinned = monitor_defines(monitor, offset)
    extra = [*pinned, '-DSW_GIC_DRY_REGS=0']
    wrapper = build_one('SNAPMON-M1.BIN', tree, cflags, ldflags, defines, out, extra, [f'--defsym={MONITOR_CALL}={offset:#x}'])
    if len(wrapper) > offset:
        raise Refused('WRAPPER_TOO_BIG', f'the wrapper is {len(wrapper)} bytes; the monitor starts at {offset:#x}')
    (out / 'wrapper-m1.bin').write_bytes(wrapper)
    image = wrapper + bytes(offset - len(wrapper)) + monitor
    (out / 'SNAPMON-M1.BIN').write_bytes(image)
    files = {'SNAPMON-M1.BIN': image, 'SNAPREP-M1.BIN': build_one('SNAPREP-M1.BIN', tree, cflags, ldflags, defines, out)}
    report = {'files': {n: {'bytes': len(d), 'sha256': sha(d)} for n, d in files.items()},
              'wrapper': {'bytes': len(wrapper), 'sha256': sha(wrapper), 'padding': offset - len(wrapper)},
              'monitor': {'offset': offset, 'bytes': len(monitor), 'sha256': sha(monitor), 'elf_sha256': record['monitor']['elf_sha256'],
                          'build_record_sha256': sha(Path(record_arg).read_bytes()), 'nv2_record': record['nv2_record'].get('record_sha256'),
                          'relocations': pic, 'call': f'bl {MONITOR_CALL} (R_AARCH64_CALL26, --defsym)'},
              'q1n1_inputs': {rel: sha((tree / rel).read_bytes()) for rel in Q1N1_INPUTS}, 'cflags': [f.replace(str(tree), '<q1n1>') for f in cflags],
              'ldflags': ldflags, 'gic_macros': defines, 'hardware': 'NOT RUN', 'profile': record['profile'],
              'added': ['-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1', '-DSNAP_SP11_STICK_GIC=1', '-DSNAP_SP11_STICK_MONITOR=1', '-DSW_GIC_DRY_REGS=0', *pinned],
              'variant': 'm1: the wrapper arms the GIC (classes G, R, C), calls the appended monitor and puts the GIC back (docs/SP11-STICK-MONITOR.md)'}
    (out / REPORT_NAME).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', required=True, help='a directory below build/ (empty or absent)')
    parser.add_argument('--profile', default=str(ROOT / 'config/monitor-profile-sp11-m1.json'), help='the monitor profile whose GIC macros the wrapper compares with PIN.DAT')
    parser.add_argument('--force', action='store_true', help='replace an earlier build of this tool')
    parser.add_argument('--gic', action='store_true', help='build the opt-in GIC and stop variant (SNAPMON-GIC.BIN, SNAPREP-GIC.BIN) instead of the dry run')
    parser.add_argument('--monitor', help='build the monitor entry variant (SNAPMON-M1.BIN, SNAPREP-M1.BIN): the profile build\'s xnuboot.bin to append')
    parser.add_argument('--monitor-build', help='the profile-build.json that the monitor given with --monitor was built by')
    parser.add_argument('--monitor-offset', type=lambda text: int(text, 0), default=MONITOR_OFFSET, help='where the monitor starts in SNAPMON-M1.BIN (default %(default)#x)')
    args = parser.parse_args(argv)
    if bool(args.monitor) != bool(args.monitor_build) or (args.monitor and args.gic):
        parser.error('--monitor and --monitor-build go together, and replace --gic')
    try:
        report = build_monitor(args.out, args.profile, args.monitor, args.monitor_build, args.force, args.monitor_offset) if args.monitor \
            else build(args.out, args.profile, args.force, args.gic)
    except Refused as error:
        print(error.detail, file=sys.stderr)
        print(f'REFUSED={error.name}')
        return 1
    for name in MONITOR_NAMES if args.monitor else GIC_NAMES if args.gic else NAMES:
        print(f'{name} {report["files"][name]["bytes"]} bytes sha256 {report["files"][name]["sha256"]}')
    if args.monitor:
        print(f'WRAPPER {report["wrapper"]["bytes"]} bytes sha256 {report["wrapper"]["sha256"]} (then {report["wrapper"]["padding"]} zero bytes)')
        print(f'MONITOR {report["monitor"]["bytes"]} bytes sha256 {report["monitor"]["sha256"]} at {report["monitor"]["offset"]:#x}')
    for rel, digest in report['q1n1_inputs'].items():
        print(f'INPUT q1n1:{rel} sha256 {digest}')
    print('MONITOR=APPENDED (called by the wrapper)' if args.monitor else 'MONITOR=NONE (the wrapper never enters one)')
    print('HARDWARE=NOT RUN')
    return 0


if __name__ == '__main__':
    sys.exit(main())
