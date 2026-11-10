#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Record and compare the identity of the A16 monitor build, so a change to shared code can show that the
default path is unchanged (AGENTS.md rule 3: compare monitor ELF bytes when the build is deterministic).

    python3 tools/monitor_baseline.py capture LABEL [--tree TREE] [--results DIR]
    python3 tools/monitor_baseline.py compare FIRST SECOND [--results DIR]

capture builds four flag sets with tools/monitor_build_offline.py, each twice into fresh directories, and writes
DIR/LABEL.json (default build/monitor-baseline). The sets come from the recipes that define them: bare is
probes/run.py FLAGS (no SNAP_* define), base is config/monitor-flags.json, control and fast are the speed-test
variants built on base. A set is reproducible only when both builds agree on the binary, the linked ELF and every
object; capture exits 1 when one is not, and an existing label is never overwritten.

compare exits 0 when every set is identical, 1 when a set differs (the differing objects are named) and 2 when
the records cannot be compared: another toolchain, a set missing from one record, a set that was not
reproducible, or an unknown or damaged record. Identity is decided by the size, the binary and the linked ELF;
the LTO objects carry the source path, so the objects it names are meaningful only when both records were
captured from the same checkout path. Hashes depend on the exact compiler and linker, so a record from
another toolchain proves nothing and is refused rather than waived. Every refusal (a bad or existing label, a
missing prerequisite, a failed build, a damaged record) exits 2 with the reason on stderr.

TREE is a Snapintosh checkout whose ../q1n1 exists (default: this one). Capture "before" ahead of an edit to
shared code and "after" once it is done. Nothing here touches hardware."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import monitor_build_offline

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 1
SETS = ('bare', 'base', 'control', 'fast')
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
DIGEST = re.compile(r'[0-9a-f]{64}')


def refuse(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def need(condition, message):
    if not condition:
        refuse(message)


def check_label(label):
    need(LABEL.fullmatch(label), f'bad label {label!r}: use letters, digits, ".", "_" and "-", starting with a letter or digit')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def flag_recipes(tree):
    from cycle_lib import speed_test
    sets, _ = speed_test.flag_sets(tree)
    base = json.loads((tree / 'config/monitor-flags.json').read_text())['flags']
    bare = monitor_build_offline.run_py_literal(tree, 'FLAGS')
    need(base and bare, 'flag recipes not found (config/monitor-flags.json, probes/run.py FLAGS)')
    return dict(bare=list(bare), base=list(base), control=list(sets['control']), fast=list(sets['fast']))


def toolchain_versions():
    def first_line(argv):
        try:
            out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()
        except FileNotFoundError:
            refuse(f'{argv[0]} not found: the monitor build needs Homebrew llvm and lld (docs/BUILDING.md)')
        return out.splitlines()[0] if out else ''
    return dict(clang=first_line([str(monitor_build_offline.LLVM / 'clang'), '--version']),
                lld=first_line([monitor_build_offline.LLD, '--version']))


def git_revision(tree):
    """The commit the tree is a checkout of and whether it carries uncommitted, non-ignored changes (an
    untracked source can feed the build too); None for an unpacked archive, including one inside another
    repository."""
    def git(*args):
        return subprocess.run(['git', '-C', str(tree), *args], capture_output=True, text=True)
    top = git('rev-parse', '--show-toplevel')
    if top.returncode or Path(top.stdout.strip()).resolve() != Path(tree).resolve():
        return None
    head = git('rev-parse', 'HEAD')
    if head.returncode:
        return None
    return dict(commit=head.stdout.strip(), dirty=bool(git('status', '--porcelain').stdout.strip()))


def describe_revision(revision):
    if revision is None:
        return 'not a git checkout'
    return revision['commit'][:12] + (' with uncommitted changes' if revision['dirty'] else '')


def summarize(flag_count, first, second):
    """One set's row from its two builds; `unstable` names every artifact on which the builds disagree."""
    unstable = sorted(k for k in {*first['objects'], *second['objects']}
                      if first['objects'].get(k) != second['objects'].get(k))
    unstable += [artifact for key, artifact in (('sha256', 'xnuboot.bin'), ('elf_sha256', 'xnuboot.elf'))
                 if first[key] != second[key]]
    return dict(flag_count=flag_count, bytes=first['bytes'], bin_sha256=first['sha256'],
                elf_sha256=first['elf_sha256'], objects=first['objects'], unstable=unstable)


def capture(tree, label, results):
    """Build every set twice and write results/label.json; returns the record."""
    tree, results = Path(tree).resolve(), Path(results)
    check_label(label)
    need((tree.parent / 'q1n1/platform/uefi').is_dir(),
         f'{tree.parent}/q1n1/platform/uefi is missing: the monitor build links the q1n1 checkout beside the tree')
    record_path, work = results / f'{label}.json', results / label
    partial = record_path.with_suffix('.json.partial')
    for leftover in (record_path, work, partial):
        need(not leftover.exists(), f'{leftover} exists: a baseline is never overwritten, so pick another label or delete it')
    flags = flag_recipes(tree)
    need(set(flags) == set(SETS) and all(flags.values()), f'the flag recipes must give exactly {SETS}, none empty')
    toolchain = toolchain_versions()

    def build_once(job):
        name, attempt = job
        out = work / f'{name}-{attempt}'
        built = monitor_build_offline.build(tree, out, flags[name])
        return dict(built, objects={p.name: sha256(p) for p in sorted(out.glob('*.o'))})

    work.mkdir(parents=True)
    jobs = [(name, attempt) for name in SETS for attempt in (1, 2)]
    try:
        with ThreadPoolExecutor(min(len(jobs), os.cpu_count() or 4)) as pool:
            built = dict(zip(jobs, pool.map(build_once, jobs)))
        record = dict(format=FORMAT, revision=git_revision(tree), toolchain=toolchain,
                      sets={name: summarize(len(flags[name]), built[(name, 1)], built[(name, 2)]) for name in SETS})
        # Written aside and renamed, so a partial record is never mistaken for a baseline.
        partial.write_text(json.dumps(record, indent=1) + '\n')
        os.replace(partial, record_path)
    except BaseException:
        # A leftover directory or partial file would make the label unusable for the retry.
        shutil.rmtree(work, ignore_errors=True)
        partial.unlink(missing_ok=True)
        raise
    return record


def load_record(results, label):
    check_label(label)
    path = Path(results) / f'{label}.json'
    need(path.is_file(), f'no record {path}')
    try:
        record = json.loads(path.read_text())
    except ValueError as error:
        refuse(f'{path} is not valid JSON: {error}')
    need(isinstance(record, dict) and record.get('format') == FORMAT, f'{path}: unknown record format')
    revision = record.get('revision')
    need(revision is None or (isinstance(revision, dict) and isinstance(revision.get('commit'), str)
                              and type(revision.get('dirty')) is bool), f'{path}: damaged revision')
    toolchain, sets = record.get('toolchain'), record.get('sets')
    need(isinstance(toolchain, dict) and all(isinstance(toolchain.get(k), str) and toolchain[k] for k in ('clang', 'lld')),
         f'{path}: no toolchain recorded')
    need(isinstance(sets, dict) and sets, f'{path}: no flag sets recorded')
    for name, row in sets.items():
        # A missing hash would compare equal to another missing hash, so every field is required.
        need(isinstance(row, dict) and type(row.get('bytes')) is int and isinstance(row.get('unstable'), list)
             and isinstance(row.get('objects'), dict)
             and all(isinstance(row.get(k), str) and DIGEST.fullmatch(row[k]) for k in ('bin_sha256', 'elf_sha256')),
             f'{path}: damaged row for set {name!r}')
    return dict(record, label=label)


def compare(one, two):
    """(exit code, report lines) for two loaded records."""
    report = [f'{rec["label"]:<10} {describe_revision(rec["revision"])}; {rec["toolchain"]["clang"]}; {rec["toolchain"]["lld"]}'
              for rec in (one, two)]
    incomparable = different = False
    for key in ('clang', 'lld'):
        if one['toolchain'][key] != two['toolchain'][key]:
            report.append(f'INCOMPARABLE toolchain differs ({key}): {one["toolchain"][key]!r} vs {two["toolchain"][key]!r}')
            incomparable = True
    if incomparable:
        return 2, report
    for name in sorted(set(one['sets']) | set(two['sets'])):
        a, b = one['sets'].get(name), two['sets'].get(name)
        if a is None or b is None:
            report.append(f'INCOMPARABLE {name}: present in only one record')
            incomparable = True
            continue
        unstable = [(rec['label'], row['unstable']) for rec, row in ((one, a), (two, b)) if row['unstable']]
        for label, names in unstable:
            report.append(f'INCOMPARABLE {name}: not reproducible in {label} ({", ".join(names)})')
            incomparable = True
        if unstable:
            continue
        key = ('bytes', 'bin_sha256', 'elf_sha256')
        if all(a[k] == b[k] for k in key):
            report.append(f'IDENTICAL    {name:<8} {a["bytes"]} bytes, elf {a["elf_sha256"][:16]}')
            continue
        different = True
        report.append(f'DIFFERENT    {name:<8} {a["bytes"]} -> {b["bytes"]} bytes, elf {a["elf_sha256"][:16]} -> {b["elf_sha256"][:16]}')
        changed = sorted(k for k in {*a['objects'], *b['objects']} if a['objects'].get(k) != b['objects'].get(k))
        report.append('             objects that differ (object hashes also depend on the checkout path): '
                      + (', '.join(changed) or 'none (the difference is in the link)'))
    return (2 if incomparable else 1 if different else 0), report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = ap.add_subparsers(dest='command', required=True)
    cap = commands.add_parser('capture')
    cap.add_argument('label')
    cap.add_argument('--tree', default=str(ROOT))
    cmp = commands.add_parser('compare')
    cmp.add_argument('first')
    cmp.add_argument('second')
    for sub in (cap, cmp):
        sub.add_argument('--results', default=str(ROOT / 'build/monitor-baseline'))
    args = ap.parse_args(argv)
    if args.command == 'capture':
        try:
            record = capture(args.tree, args.label, args.results)
        except subprocess.CalledProcessError as error:
            refuse(f'build failed: {error.cmd[0]} exited {error.returncode}')
        print(f'toolchain  {record["toolchain"]["clang"]}; {record["toolchain"]["lld"]}')
        print(f'revision   {describe_revision(record["revision"])}')
        for name, row in record['sets'].items():
            state = 'reproducible' if not row['unstable'] else 'NOT REPRODUCIBLE: ' + ', '.join(row['unstable'])
            print(f'{name:<8} {row["bytes"]:>8} bytes  bin {row["bin_sha256"][:16]}  elf {row["elf_sha256"][:16]}  {state}')
        return 0 if all(not row['unstable'] for row in record['sets'].values()) else 1
    code, report = compare(load_record(args.results, args.first), load_record(args.results, args.second))
    print('\n'.join(report))
    return code


if __name__ == '__main__':
    sys.exit(main())
