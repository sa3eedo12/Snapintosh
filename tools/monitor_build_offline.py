#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build probes/xnuboot exactly as probes/run.py build('xnuboot') does, offline.

    python3 tools/monitor_build_offline.py TREE OUT FLAGS_JSON [EXTRA_DEFINE ...]

TREE is a Snapintosh checkout, OUT a fresh directory, FLAGS_JSON a file holding
the ordered compile flag list (for example a run's launch.json, whose "flags"
key is used). Extra defines are appended after the run's flags, the way
cycle.py appends opt-in defines.

The monitor links q1n1's platform/uefi/sha256.c and includes its sha256.h. They
come from the q1n1 checkout beside TREE (../q1n1), or, for a layout without
that sibling, from the checkout named by $Q1N1_SP11_TREE. The variable is
explicit: when it is set and does not name a q1n1 with both files, the build
refuses; it never falls back to the sibling. Nothing is created outside TREE
and OUT.

The source list, include directories, compiler, linker and objcopy steps are
read from TREE/probes/run.py (MULTI['xnuboot'], FLAGS order irrelevant: the
given flags replace FLAGS exactly as installed_fresh.prepare does). run.py is
parsed, not imported, so no q1n1 proxy module is loaded. Prints one JSON line.
"""
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

LLVM = Path('/opt/homebrew/opt/llvm/bin')
LLD = '/opt/homebrew/opt/lld/bin/ld.lld' if Path('/opt/homebrew/opt/lld/bin/ld.lld').exists() else 'ld.lld'
Q1N1_ENV = 'Q1N1_SP11_TREE'
Q1N1_SOURCE_PREFIX = '../q1n1/'     # how probes/run.py MULTI names q1n1's sha256.c, relative to the tree
UEFI_FILES = ('sha256.c', 'sha256.h')


def locate_q1n1(tree):
    """(platform/uefi directory, None) of the q1n1 this build uses, or (None, why it cannot be used).

    $Q1N1_SP11_TREE wins when it is set, even when it is empty or wrong: a build that silently took another q1n1 than
    the one the operator named would prove nothing about the one they meant."""
    if Q1N1_ENV in os.environ:
        named = os.environ[Q1N1_ENV]
        where = f'${Q1N1_ENV}={named!r}'
        q1n1 = Path(named).resolve() if named.strip() else None
    else:
        q1n1 = Path(tree).resolve().parent / 'q1n1'
        where = f'{q1n1} (beside the tree)'
    if q1n1 is None:
        return None, f'{where} names no directory'
    uefi = q1n1 / 'platform/uefi'
    missing = [name for name in UEFI_FILES if not (uefi / name).is_file()]
    if missing:
        return None, (f'{where}: {uefi} lacks {" and ".join(missing)}; the monitor build links the q1n1 checkout '
                      f'beside the tree, or the one named by ${Q1N1_ENV}')
    return uefi, None


def run_py_literal(tree, name):
    mod = ast.parse((tree / 'probes/run.py').read_text())
    for node in mod.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def multi_sources(tree, flags=()):
    multi = run_py_literal(tree, 'MULTI')
    if multi is None:
        raise SystemExit('no MULTI in run.py')
    # run.py's OPTIONAL units: linked only when their define is in the flags (run.py optional_sources).
    optional = run_py_literal(tree, 'OPTIONAL') or {}
    return multi['xnuboot'] + [rel for define, rel in optional.get('xnuboot', ()) if define in flags]


def build(tree, out, flags):
    tree = Path(tree).resolve()
    uefi, why = locate_q1n1(tree)
    if uefi is None:
        raise SystemExit(why)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    objs = []
    for rel in multi_sources(tree, flags):
        src = tree / rel
        # Only an explicit $Q1N1_SP11_TREE changes where q1n1's sha256.c is read from: the sibling layout keeps the
        # spelling it always had, so the objects of an unchanged setup do not change.
        if Q1N1_ENV in os.environ and rel.startswith(Q1N1_SOURCE_PREFIX):
            src = uefi.parent.parent / rel[len(Q1N1_SOURCE_PREFIX):]
        obj = out / (src.stem + '-' + src.suffix.lstrip('.') + '.o')
        subprocess.run([str(LLVM / 'clang'), *flags, f'-I{tree / "shadow"}',
                        f'-I{uefi}', '-c', str(src), '-o', str(obj)],
                       check=True)
        objs.append(str(obj))
    elf = out / 'xnuboot.elf'
    subprocess.run([LLD, '-T', str(tree / 'probes' / 'probe.ld'), '-o', str(elf), *objs], check=True)
    binary = out / 'xnuboot.bin'
    subprocess.run([str(LLVM / 'llvm-objcopy'), '-O', 'binary', str(elf), str(binary)], check=True)
    blob = binary.read_bytes()
    return dict(bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest(),
                elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest())


def main():
    tree, out, flags_json, *extra = sys.argv[1:]
    data = json.loads(Path(flags_json).read_text())
    flags = data['flags'] if isinstance(data, dict) else data
    result = build(tree, out, list(flags) + extra)
    result.update(tree=str(tree), out=str(out), extra=extra)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
