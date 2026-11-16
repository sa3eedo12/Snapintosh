#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Builds the two payloads of the SP11 bundle dry run (docs/SP11-STICK-WRAPPER.md): SNAPMON.BIN, the wrapper and the loader with NO
monitor in it, and SNAPREP.BIN, the read-only reporter, with the recipe of q1n1's stick payloads.

    export Q1N1_SP11_TREE=<q1n1 checkout with platform/uefi/Makefile and payloads/>     (absent or wrong: REFUSED=Q1N1_TREE_ABSENT)
    python3 tools/sp11_stick_wrapper_build.py --out build/sp11-wrapper [--profile config/monitor-profile-sp11-m1.json]

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
SOURCES = {'SNAPMON.BIN': ('probes/sp11-stick-wrapper.c', 'probes/sp11-stick-load.c', 'probes/sp11-stick-report.c', 'platform/uefi/sha256.c'),
           'SNAPREP.BIN': ('probes/sp11-stick-reporter.c',)}
GIC_MACROS = ('SNAP_GICD_BASE', 'SNAP_GICR_FIRST', 'SNAP_GICR_LIMIT', 'SNAP_GICR_STRIDE', 'SNAP_TIMER_INTID')
Q1N1_INPUTS = ('platform/uefi/Makefile', 'payloads/start.S', 'payloads/payload.ld', 'payloads/payload-util.h', 'platform/uefi/sp11-payload-abi.h',
               'platform/uefi/preload.h', 'platform/uefi/efi.h', 'platform/uefi/sha256.c', 'platform/uefi/sha256.h', 'tools/sp11_payload_elfcheck.py')
REPORT_NAME = 'build-report.json'
REPORTER_MAX = 64 * 1024
REFUSALS = ('Q1N1_TREE_ABSENT', 'PROFILE_BAD', 'RECIPE_CHANGED', 'TOOL_MISSING', 'OUT_DIR_REFUSED', 'OUT_NOT_EMPTY', 'BUILD_FAILED',
            'ELF_REFUSED', 'UNDEFINED_SYMBOL', 'REPORTER_TOO_BIG')
LLVM_DIRS = ('/opt/homebrew/opt/llvm/bin', '/opt/homebrew/opt/lld/bin')


class Refused(Exception):
    def __init__(self, name, detail=''):
        assert name in REFUSALS, name          # a typo must not become a new refusal
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


def build_one(name, tree, cflags, ldflags, defines, out):
    """Compile start.S and the sources of `name`, link, check the ELF, flatten. Returns the flat bytes."""
    clang, lld, objcopy, readelf, nm = (tool(t) for t in ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-readelf', 'llvm-nm'))
    stem = name.split('.')[0].lower()
    obj = out / 'obj' / stem
    obj.mkdir(parents=True)
    flags = [*cflags, f'-I{tree / "payloads"}', '-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1', *(defines if name == 'SNAPMON.BIN' else [])]
    objects = []
    for source in ('payloads/start.S', *SOURCES[name]):
        base = tree if source.startswith(('payloads/', 'platform/')) else ROOT
        target = obj / (Path(source).stem + '.o')
        run([clang, *flags, '-c', str(base / source), '-o', str(target)], f'compiling {source}')
        objects.append(str(target))
    elf = out / f'{stem}.elf'
    run([lld, *ldflags, '-T', str(tree / 'payloads/payload.ld'), '-o', str(elf), *objects], f'linking {name}')
    checked = subprocess.run([sys.executable, '-B', str(tree / 'tools/sp11_payload_elfcheck.py'), '--readelf', readelf, str(elf)], capture_output=True, text=True)
    if checked.returncode:
        raise Refused('ELF_REFUSED', f'{name}: {checked.stderr.strip() or checked.stdout.strip()}')
    undefined = run([nm, '-u', str(elf)], f'listing the undefined symbols of {name}').stdout.strip()
    if undefined:
        raise Refused('UNDEFINED_SYMBOL', f'{name} needs {undefined.split()[-1]}: a payload has no libc')
    binary = out / name
    run([objcopy, '-O', 'binary', str(elf), str(binary)], f'flattening {name}')
    data = binary.read_bytes()
    if name == 'SNAPREP.BIN' and len(data) >= REPORTER_MAX:
        raise Refused('REPORTER_TOO_BIG', f'{len(data)} bytes; the reporter is under {REPORTER_MAX}')
    return data


def build(out_arg, profile, force=False):
    """Build both payloads below `out_arg`; returns the report dict, which is also written as build-report.json."""
    tree = q1n1_tree()
    out = check_out(out_arg, force)
    cflags, ldflags = recipe_flags(tree)
    defines = gic_defines(profile)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    files = {name: build_one(name, tree, cflags, ldflags, defines, out) for name in NAMES}
    shown = [f.replace(str(tree), '<q1n1>') for f in cflags]
    report = {'files': {n: {'bytes': len(d), 'sha256': sha(d)} for n, d in files.items()},
              'q1n1_inputs': {rel: sha((tree / rel).read_bytes()) for rel in Q1N1_INPUTS}, 'cflags': shown, 'ldflags': ldflags,
              'added': ['-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1'], 'gic_macros': defines, 'monitor': 'none: the wrapper never enters one',
              'hardware': 'NOT RUN'}
    (out / REPORT_NAME).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', required=True, help='a directory below build/ (empty or absent)')
    parser.add_argument('--profile', default=str(ROOT / 'config/monitor-profile-sp11-m1.json'), help='the monitor profile whose GIC macros the wrapper compares with PIN.DAT')
    parser.add_argument('--force', action='store_true', help='replace an earlier build of this tool')
    args = parser.parse_args(argv)
    try:
        report = build(args.out, args.profile, args.force)
    except Refused as error:
        print(error.detail, file=sys.stderr)
        print(f'REFUSED={error.name}')
        return 1
    for name in NAMES:
        print(f'{name} {report["files"][name]["bytes"]} bytes sha256 {report["files"][name]["sha256"]}')
    for rel, digest in report['q1n1_inputs'].items():
        print(f'INPUT q1n1:{rel} sha256 {digest}')
    print('MONITOR=NONE (the wrapper never enters one)')
    print('HARDWARE=NOT RUN')
    return 0


if __name__ == '__main__':
    sys.exit(main())
