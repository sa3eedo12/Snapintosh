#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""monitor_baseline must call two monitor builds identical only when its evidence says so.

The build is replaced by a function of the flags, so no LLVM, q1n1 checkout or hardware is involved. The faults
are the ones the tool exists to catch: a changed object, a changed link, a build that does not reproduce, another
toolchain, a missing set, and records that are damaged, partial or about to be overwritten."""
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import monitor_baseline as mb

RECIPES = {
    'bare': ['-O1'],
    'base': ['-O1', '-DSNAP_BASE'],
    'control': ['-O1', '-DSNAP_BASE', '-DSNAP_CONTROL'],
    'fast': ['-O1', '-DSNAP_BASE', '-DSNAP_CONTROL', '-DSNAP_FAST'],
}
NAMES = {tuple(flags): name for name, flags in RECIPES.items()}
OBJECTS = ['a-c.o', 'b-c.o']
TOOLCHAIN = dict(clang='fake clang 1', lld='fake lld 1')
REVISION = dict(commit='a' * 40, dirty=False)


class FakeBuild:
    """Stands in for monitor_build_offline.build: the output is a function of the flags, so two runs agree,
    plus whichever faults a test asks for. The second attempt of a set is the one whose directory ends in -2."""

    def __init__(self, changed=(), relinked=(), flaky=(), flaky_link=(), failing=()):
        self.changed, self.relinked, self.flaky = set(changed), set(relinked), set(flaky)
        self.flaky_link, self.failing = set(flaky_link), set(failing)
        self.calls = []

    def __call__(self, tree, out, flags):
        name, out = NAMES[tuple(flags)], Path(out)
        second = out.name.endswith('-2')
        self.calls.append((out.name, tuple(flags)))
        if name in self.failing:
            raise subprocess.CalledProcessError(1, ['clang'])
        out.mkdir(parents=True, exist_ok=False)
        texts = {}
        for obj in OBJECTS:
            texts[obj] = (f'{obj} {" ".join(flags)}' + (' changed' if (name, obj) in self.changed else '')
                          + (' flaky' if second and (name, obj) in self.flaky else ''))
            (out / obj).write_text(texts[obj])
        elf = ('\n'.join(texts.values()) + (' relinked' if name in self.relinked else '')
               + (' flaky-link' if second and name in self.flaky_link else ''))
        blob = hashlib.sha256(elf.encode()).digest() * 3
        (out / 'xnuboot.elf').write_text(elf)
        (out / 'xnuboot.bin').write_bytes(blob)
        return dict(bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest(),
                    elf_sha256=hashlib.sha256(elf.encode()).hexdigest())


class Workspace(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.tree, self.results = self.base / 'Snapintosh', self.base / 'results'
        self.tree.mkdir()
        self.results.mkdir()
        (self.base / 'q1n1/platform/uefi').mkdir(parents=True)

    @contextmanager
    def fakes(self, toolchain=TOOLCHAIN, revision=REVISION, **faults):
        builder = FakeBuild(**faults)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(mb.monitor_build_offline, 'build', builder))
            stack.enter_context(mock.patch.object(mb, 'flag_recipes', lambda tree: {k: list(v) for k, v in RECIPES.items()}))
            stack.enter_context(mock.patch.object(mb, 'toolchain_versions', lambda: dict(toolchain)))
            stack.enter_context(mock.patch.object(mb, 'git_revision', lambda tree: revision and dict(revision)))
            yield builder

    def capture(self, label, **options):
        with self.fakes(**options) as builder:
            return mb.capture(self.tree, label, self.results), builder

    def cli(self, command, *labels):
        argv = [command, *labels, '--results', str(self.results)] + (['--tree', str(self.tree)] if command == 'capture' else [])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = mb.main(argv)
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    def load(self, label):
        return mb.load_record(self.results, label)

    def compare(self, first, second):
        return mb.compare(self.load(first), self.load(second))

    def rewrite(self, label, edit):
        path = self.results / f'{label}.json'
        record = json.loads(path.read_text())
        edit(record)
        path.write_text(json.dumps(record))

    def refused(self, call, *args):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            call(*args)
        self.assertEqual(stop.exception.code, 2)
        return err.getvalue()


class CaptureTests(Workspace):
    def test_every_set_is_built_twice_and_recorded(self):
        record, builder = self.capture('before')
        self.assertEqual(tuple(record['sets']), mb.SETS)
        for name, row in record['sets'].items():
            self.assertEqual(row['unstable'], [], name)
            self.assertEqual(row['flag_count'], len(RECIPES[name]), name)
            self.assertEqual(sorted(row['objects']), OBJECTS, name)
            self.assertTrue(mb.DIGEST.fullmatch(row['bin_sha256']) and mb.DIGEST.fullmatch(row['elf_sha256']), name)
        self.assertEqual(sorted(call for call, _ in builder.calls), sorted(f'{s}-{n}' for s in mb.SETS for n in (1, 2)))
        self.assertEqual({(call.rsplit('-', 1)[0], flags) for call, flags in builder.calls},
                         {(name, tuple(flags)) for name, flags in RECIPES.items()})
        self.assertEqual(sorted(p.name for p in self.results.iterdir()), ['before', 'before.json'])
        self.assertEqual(self.load('before'), dict(record, label='before'))

    def test_an_interrupted_write_leaves_no_record_and_frees_the_label(self):
        with mock.patch.object(mb.os, 'replace', side_effect=OSError('disk full')), self.fakes():
            with self.assertRaises(OSError):
                mb.capture(self.tree, 'before', self.results)
        self.assertEqual(list(self.results.iterdir()), [])
        self.assertIn('no record', self.refused(self.load, 'before'))
        self.assertEqual(self.capture('before')[0]['sets']['base']['unstable'], [])

    def test_leftovers_of_a_killed_capture_block_the_label_and_are_named(self):
        (self.results / 'killed.json.partial').write_text('{')
        (self.results / 'other').mkdir()
        self.assertIn('killed.json.partial exists', self.refused(self.capture, 'killed'))
        self.assertIn('other exists', self.refused(self.capture, 'other'))
        self.assertEqual(sorted(p.name for p in self.results.iterdir()), ['killed.json.partial', 'other'])

    def test_a_baseline_is_never_overwritten(self):
        self.capture('before')
        kept = (self.results / 'before.json').read_bytes()
        message = self.refused(self.capture, 'before')
        self.assertIn('never overwritten', message)
        self.assertEqual((self.results / 'before.json').read_bytes(), kept)
        (self.results / 'orphan').mkdir()
        self.assertIn('never overwritten', self.refused(self.capture, 'orphan'))

    def test_labels_are_validated_before_anything_is_written(self):
        for bad in ('', '../escape', 'a/b', ' x', 'x y', '.hidden', 'x' * 65, 'before\n', '\u00e9'):
            with self.subTest(label=bad):
                self.refused(mb.check_label, bad)
                self.refused(self.capture, bad)
        for good in ('before', 'after-2', 'v1.2_x', 'x' * 64):
            mb.check_label(good)
        self.assertEqual(list(self.results.iterdir()), [])
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ['Snapintosh', 'q1n1', 'results'])

    def test_the_q1n1_checkout_beside_the_tree_is_required(self):
        shutil.rmtree(self.base / 'q1n1')
        self.assertIn('q1n1', self.refused(self.capture, 'before'))
        self.assertEqual(list(self.results.iterdir()), [])

    def test_a_failed_build_leaves_the_label_free_for_a_retry(self):
        with self.fakes(failing={'control'}):
            code, _, err = self.cli('capture', 'broken')
        self.assertEqual(code, 2)
        self.assertIn('build failed: clang exited 1', err)
        self.assertEqual(list(self.results.iterdir()), [])
        with self.fakes():
            self.assertEqual(self.cli('capture', 'broken')[0], 0)

    def test_exit_status_of_capture_follows_reproducibility(self):
        with self.fakes():
            code, out, _ = self.cli('capture', 'good')
        self.assertEqual(code, 0)
        self.assertNotIn('NOT REPRODUCIBLE', out)
        self.assertIn('fake clang 1; fake lld 1', out)
        with self.fakes(flaky={('fast', 'a-c.o')}):
            code, out, _ = self.cli('capture', 'flaky')
        self.assertEqual(code, 1)
        self.assertIn('NOT REPRODUCIBLE: a-c.o, xnuboot.bin, xnuboot.elf', out)
        self.assertEqual(self.load('flaky')['sets']['fast']['unstable'], ['a-c.o', 'xnuboot.bin', 'xnuboot.elf'])
        self.assertEqual(self.load('flaky')['sets']['base']['unstable'], [])
        with self.fakes(flaky_link={'bare'}):
            self.cli('capture', 'flaky-link')
        self.assertEqual(self.load('flaky-link')['sets']['bare']['unstable'], ['xnuboot.bin', 'xnuboot.elf'])


class CompareTests(Workspace):
    def lines(self, report, prefix):
        return [line for line in report if line.startswith(prefix)]

    def test_identical_builds_compare_clean(self):
        self.capture('before')
        self.capture('after')
        code, report = self.compare('before', 'after')
        self.assertEqual(code, 0)
        self.assertEqual(len(self.lines(report, 'IDENTICAL')), 4)
        self.assertEqual(len(report), 6)
        self.assertTrue(all('aaaaaaaaaaaa' in line and 'fake clang 1' in line for line in report[:2]))
        code, out, _ = self.cli('compare', 'before', 'after')
        self.assertEqual(code, 0)
        self.assertEqual(out.count('IDENTICAL'), 4)

    def test_a_changed_object_is_named(self):
        self.capture('before')
        self.capture('after', changed={('control', 'b-c.o')})
        code, report = self.compare('before', 'after')
        self.assertEqual(code, 1)
        self.assertEqual(len(self.lines(report, 'IDENTICAL')), 3)
        self.assertEqual(len(self.lines(report, 'DIFFERENT')), 1)
        self.assertIn('control', self.lines(report, 'DIFFERENT')[0])
        detail = report[report.index(self.lines(report, 'DIFFERENT')[0]) + 1]
        self.assertTrue(detail.endswith('b-c.o'), detail)
        self.assertIn('checkout path', detail)
        self.assertEqual(self.cli('compare', 'before', 'after')[0], 1)

    def test_a_change_confined_to_the_link_is_reported_as_such(self):
        self.capture('before')
        self.capture('after', relinked={'fast'})
        code, report = self.compare('before', 'after')
        self.assertEqual(code, 1)
        self.assertIn('fast', self.lines(report, 'DIFFERENT')[0])
        self.assertIn('none (the difference is in the link)', '\n'.join(report))

    def test_a_set_that_does_not_reproduce_is_incomparable_not_identical(self):
        self.capture('before')
        self.capture('after', flaky={('fast', 'a-c.o')})
        code, report = self.compare('before', 'after')
        self.assertEqual(code, 2)
        self.assertIn('INCOMPARABLE fast: not reproducible in after (a-c.o, xnuboot.bin, xnuboot.elf)', report)
        self.assertEqual(len(self.lines(report, 'IDENTICAL')), 3)

    def test_two_records_that_agree_are_still_refused_if_neither_reproduced(self):
        # Both first attempts are the same build, so the hashes match: only the unstable list refuses it.
        self.capture('flaky-one', flaky={('fast', 'a-c.o')})
        self.capture('flaky-two', flaky={('fast', 'a-c.o')})
        self.assertEqual(self.load('flaky-one')['sets']['fast']['elf_sha256'], self.load('flaky-two')['sets']['fast']['elf_sha256'])
        code, report = self.compare('flaky-one', 'flaky-two')
        self.assertEqual(code, 2)
        self.assertEqual(len(self.lines(report, 'INCOMPARABLE fast: not reproducible')), 2)

    def test_another_toolchain_is_incomparable(self):
        self.capture('before')
        for key in ('clang', 'lld'):
            with self.subTest(changed=key):
                self.capture(f'after-{key}', toolchain=dict(TOOLCHAIN, **{key: 'fake 2'}))
                code, report = self.compare('before', f'after-{key}')
                self.assertEqual(code, 2)
                self.assertTrue(self.lines(report, f'INCOMPARABLE toolchain differs ({key})'))
                self.assertEqual(self.lines(report, 'IDENTICAL'), [])

    def test_a_set_missing_from_one_record_is_incomparable(self):
        self.capture('before')
        self.capture('after')
        self.rewrite('after', lambda record: record['sets'].pop('fast'))
        code, report = self.compare('before', 'after')
        self.assertEqual(code, 2)
        self.assertIn('INCOMPARABLE fast: present in only one record', report)
        self.assertEqual(len(self.lines(report, 'IDENTICAL')), 3)

    def test_records_without_source_checkout_compare(self):
        self.capture('before', revision=None)
        self.capture('after', revision=dict(commit='b' * 40, dirty=True))
        code, report = self.compare('before', 'after')
        self.assertEqual(code, 0)
        self.assertIn('not a git checkout', report[0])
        self.assertIn('bbbbbbbbbbbb with uncommitted changes', report[1])


class DamagedRecordTests(Workspace):
    EDITS = [
        ('format', lambda r: r.update(format=2), 'unknown record format'),
        ('no toolchain', lambda r: r.pop('toolchain'), 'no toolchain'),
        ('empty compiler', lambda r: r['toolchain'].update(clang=''), 'no toolchain'),
        ('no sets', lambda r: r.update(sets={}), 'no flag sets'),
        ('missing elf hash', lambda r: r['sets']['base'].pop('elf_sha256'), "damaged row for set 'base'"),
        ('short hash', lambda r: r['sets']['base'].update(bin_sha256='ab' * 31), 'damaged row'),
        ('upper-case hash', lambda r: r['sets']['base'].update(elf_sha256='AB' * 32), 'damaged row'),
        ('boolean size', lambda r: r['sets']['base'].update(bytes=True), 'damaged row'),
        ('no unstable list', lambda r: r['sets']['base'].pop('unstable'), 'damaged row'),
        ('objects not a mapping', lambda r: r['sets']['base'].update(objects=[]), 'damaged row'),
        ('row not an object', lambda r: r['sets'].update(base=[]), 'damaged row'),
        ('revision commit', lambda r: r.update(revision=dict(commit=1, dirty=False)), 'damaged revision'),
        ('revision dirty', lambda r: r.update(revision=dict(commit='a', dirty='no')), 'damaged revision'),
    ]

    def test_each_damage_is_refused_and_never_compares_equal(self):
        self.capture('before')
        pristine = (self.results / 'before.json').read_text()
        for name, edit, needle in self.EDITS:
            with self.subTest(damage=name):
                (self.results / 'before.json').write_text(pristine)
                self.rewrite('before', edit)
                self.assertIn(needle, self.refused(self.load, 'before'))
                code, out, err = self.cli('compare', 'before', 'before')
                self.assertEqual((code, out), (2, ''), name)
                self.assertIn(needle, err)

    def test_unreadable_records_are_refused(self):
        (self.results / 'garbled.json').write_text('{not json')
        self.assertIn('not valid JSON', self.refused(self.load, 'garbled'))
        (self.results / 'list.json').write_text('[]')
        self.assertIn('unknown record format', self.refused(self.load, 'list'))
        self.assertIn('no record', self.refused(self.load, 'absent'))
        self.assertIn('bad label', self.refused(self.load, '../before'))

    def test_a_missing_hash_in_both_records_does_not_make_them_equal(self):
        for label in ('before', 'after'):
            self.capture(label)
            self.rewrite(label, lambda record: record['sets']['base'].pop('elf_sha256'))
        code, out, _ = self.cli('compare', 'before', 'after')
        self.assertEqual((code, out), (2, ''))


class RecipeTests(unittest.TestCase):
    def test_recipes_come_from_the_files_that_define_them(self):
        from cycle_lib import speed_test
        recipes = mb.flag_recipes(ROOT)
        self.assertEqual(tuple(sorted(recipes)), tuple(sorted(mb.SETS)))
        self.assertEqual(len({tuple(flags) for flags in recipes.values()}), len(mb.SETS))
        self.assertEqual(recipes['base'], json.loads((ROOT / 'config/monitor-flags.json').read_text())['flags'])
        self.assertEqual(recipes['bare'], list(mb.monitor_build_offline.run_py_literal(ROOT, 'FLAGS')))
        sets, _ = speed_test.flag_sets(ROOT)
        self.assertEqual((recipes['control'], recipes['fast']), (list(sets['control']), list(sets['fast'])))
        self.assertFalse([flag for flag in recipes['bare'] if flag.startswith('-DSNAP_')])
        self.assertTrue(all(any(flag.startswith('-DSNAP_') for flag in recipes[name]) for name in mb.SETS if name != 'bare'))


class RevisionTests(unittest.TestCase):
    def git(self, cwd, *args):
        subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                        *args], cwd=cwd, check=True, capture_output=True)

    def test_dirty_means_uncommitted_sources_not_ignored_output(self):
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch).resolve() / 'repo'
            (repo / 'probes').mkdir(parents=True)
            (repo / '.gitignore').write_text('build/\n')
            self.git(repo, 'init', '-q')
            self.git(repo, 'add', '-A')
            self.git(repo, 'commit', '-q', '-m', 'seed')
            revision = mb.git_revision(repo)
            self.assertEqual((len(revision['commit']), revision['dirty']), (40, False))
            (repo / 'build').mkdir()
            (repo / 'build/out.bin').write_bytes(b'x')
            self.assertFalse(mb.git_revision(repo)['dirty'])
            (repo / 'probes/new.c').write_text('int x;\n')
            self.assertTrue(mb.git_revision(repo)['dirty'])

    def test_an_archive_is_not_the_checkout_of_the_repository_around_it(self):
        with tempfile.TemporaryDirectory() as scratch:
            outer = Path(scratch).resolve()
            self.assertIsNone(mb.git_revision(outer))
            self.git(outer, 'init', '-q')
            self.git(outer, 'commit', '-q', '--allow-empty', '-m', 'seed')
            (outer / 'archive').mkdir()
            self.assertIsNotNone(mb.git_revision(outer))
            self.assertIsNone(mb.git_revision(outer / 'archive'))


if __name__ == '__main__':
    unittest.main()
