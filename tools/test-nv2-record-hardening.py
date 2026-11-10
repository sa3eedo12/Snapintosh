#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for `nv2_record.py show --check` without live registers and for how it reads its input.

* `show --check` is documented to exit 3 unless the gate would allow. Without the four --live-* values it used to
  return 0 before looking at --check, so a record the gate can never allow (a failed VNCR redirect, an evidence label
  that is not VERIFIED or OBSERVED, ID registers that do not advertise NV2) read as a pass. It now exits 3 for such
  a record and 2 for every other record: there is no decision to check, so it never answers with 0.
* The input path used to be opened before its type was known and read without a bound. A FIFO blocked the open, a
  device or terminal was opened, and an oversize file was read whole. It is now inspected first and must be a
  regular file, not a link, of at most MAX_INPUT_BYTES.

Every register value is made up (see tools/test-nv2-gate.py, whose fixtures are loaded here); nothing touches hardware.

    python3 tools/test-nv2-record-hardening.py
"""
from pathlib import Path
from unittest import mock
import importlib.util
import io
import os
import subprocess
import sys
import types
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('nv2_gate_fixtures', TOOLS / 'test-nv2-gate.py')
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
nv2, make_rec, LIVE, ROOT = G.nv2, G.make_rec, G.LIVE, G.ROOT

LIVE_ARGS = ['--live-midr', hex(LIVE['midr']), '--live-revidr', hex(LIVE['revidr']),
             '--live-mmfr2', hex(LIVE['mmfr2']), '--live-mmfr4', hex(LIVE['mmfr4'])]
# Records whose denial no live register value can change (compat/nv2-gate.c record_only_denial's conditions).
NEVER_ALLOWED = {
    'VNCR redirect failed': (dict(vncr_redirect=2), 'DENY_VNCR_FAIL'),
    'VNCR redirect not run': (dict(vncr_redirect=0), 'DENY_VNCR_NOT_RUN'),
    'negative control failed': (dict(negative_control_trapped=2), 'DENY_NEGCTL_FAIL'),
    'negative control not run': (dict(negative_control_trapped=0), 'DENY_NEGCTL_NOT_RUN'),
    'evidence INFERRED': (dict(evidence=3), 'DENY_EVIDENCE'),
    'evidence UNIMPLEMENTED': (dict(evidence=5), 'DENY_EVIDENCE'),
    'NV2 not advertised': (dict(id_aa64mmfr2_el1=0, id_aa64mmfr4_el1=0), 'DENY_NOT_ADVERTISED'),
}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = G.scratch('hardening-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def cli(self, *args, timeout=60):
        return subprocess.run([sys.executable, '-B', 'tools/nv2_record.py', *map(str, args)], cwd=ROOT,
                              capture_output=True, text=True, timeout=timeout)

    def record(self, name='run.log', **changes):
        path = self.dir / name
        path.write_text(nv2.format_text(make_rec(**changes)))
        return path


class ShowCheckWithoutLiveValues(Case):
    def test_a_record_the_gate_can_never_allow_exits_3(self):
        for name, (changes, deny) in NEVER_ALLOWED.items():
            with self.subTest(name):
                shown = self.cli('show', self.record(**changes), '--check')
                self.assertEqual(shown.returncode, 3, shown.stdout + shown.stderr)
                self.assertIn(f'gate: {deny}', shown.stdout)
                self.assertIn('whatever the live registers read', shown.stdout)

    def test_any_other_record_is_a_usage_error_never_a_pass(self):
        shown = self.cli('show', self.record(), '--check')
        self.assertEqual(shown.returncode, 2, shown.stdout)
        self.assertIn('--check needs --live-midr', shown.stderr)
        self.assertNotIn('gate:', shown.stdout)

    def test_check_never_exits_0_without_a_gate_decision(self):
        for changes in [{}] + [c for c, _ in NEVER_ALLOWED.values()]:
            with self.subTest(changes=changes):
                self.assertNotEqual(self.cli('show', self.record(**changes), '--check').returncode, 0)

    def test_without_check_the_exit_status_and_output_are_unchanged(self):
        shown = self.cli('show', self.record(vncr_redirect=2))
        self.assertEqual(shown.returncode, 0)
        self.assertNotIn('gate:', shown.stdout)
        self.assertEqual(self.cli('show', self.record()).returncode, 0)

    def test_with_all_live_values_the_decision_is_unchanged(self):
        good = self.cli('show', self.record(), *LIVE_ARGS, '--check')
        self.assertEqual((good.returncode, 'gate: ALLOW' in good.stdout), (0, True))
        denied = self.cli('show', self.record(vncr_redirect=2), *LIVE_ARGS, '--check')
        self.assertEqual((denied.returncode, 'gate: DENY_VNCR_FAIL' in denied.stdout), (3, True))

    def test_partial_live_values_stay_an_input_error(self):
        self.assertEqual(self.cli('show', self.record(), '--live-midr', '0x1', '--check').returncode, 1)

    def test_the_documented_exit_statuses_name_the_new_cases(self):
        text = (TOOLS / 'nv2_record.py').read_text()
        self.assertIn('2 bad usage (including `show --check` without', text)
        self.assertIn('3 `show --check` when the gate would not allow, or, without --live-*,', text)


class ReadingTheInput(Case):
    def test_a_path_that_is_not_a_regular_file_is_refused_without_being_opened(self):
        fifo, link_to_file, link_to_fifo = self.dir / 'r.fifo', self.dir / 'r.link', self.dir / 'f.link'
        os.mkfifo(fifo)
        good = self.record()
        link_to_file.symlink_to(good)
        link_to_fifo.symlink_to(fifo)
        for path in (fifo, self.dir, link_to_file, link_to_fifo):
            with self.subTest(path=path.name):
                with mock.patch('builtins.open', side_effect=AssertionError('opened')), \
                        mock.patch.object(nv2.os, 'open', side_effect=AssertionError('opened')):
                    with self.assertRaisesRegex(nv2.RecordError, 'not a regular file'):
                        nv2.load(str(path))

    def test_the_command_line_refuses_them_and_never_blocks(self):
        fifo, link = self.dir / 'r.fifo', self.dir / 'r.link'
        os.mkfifo(fifo)
        link.symlink_to(self.record())
        for path in (fifo, link):
            for command in ('show', 'ingest'):
                with self.subTest(path=path.name, command=command):
                    extra = ['--out', self.dir / 'o.bin', '--header', self.dir / 'o.h'] if command == 'ingest' else []
                    run = self.cli(command, path, *extra, timeout=30)
                    self.assertEqual(run.returncode, 1, run.stdout)
                    self.assertIn('not a regular file', run.stderr)
        self.assertFalse((self.dir / 'o.bin').exists())

    def test_an_oversize_file_is_refused_before_it_is_opened(self):
        path = self.record()
        with mock.patch.object(nv2, 'MAX_INPUT_BYTES', path.stat().st_size - 1), \
                mock.patch.object(nv2.os, 'open', side_effect=AssertionError('opened')):
            with self.assertRaisesRegex(nv2.RecordError, 'larger than'):
                nv2.load(str(path))

    def test_a_file_that_grows_after_it_was_inspected_is_still_bounded(self):
        path = self.record()
        size = path.stat().st_size
        real_lstat, real_fstat = os.lstat, os.fstat
        with mock.patch.object(nv2, 'MAX_INPUT_BYTES', size - 1), \
                mock.patch.object(nv2.os, 'lstat', side_effect=lambda p: claim_size(real_lstat(p), 1)), \
                mock.patch.object(nv2.os, 'fstat', side_effect=lambda fd: claim_size(real_fstat(fd), 1)):
            with self.assertRaisesRegex(nv2.RecordError, 'larger than'):
                nv2.load(str(path))

    def test_standard_input_is_bounded_too(self):
        data = nv2.format_text(make_rec()).encode()
        with mock.patch.object(nv2, 'MAX_INPUT_BYTES', len(data) - 1), \
                mock.patch.object(sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(data))):
            with self.assertRaisesRegex(nv2.RecordError, 'standard input is larger than'):
                nv2.load('-')

    def test_a_regular_file_and_standard_input_still_load(self):
        rec = make_rec()
        path = self.record()
        self.assertEqual(nv2.load(str(path))[0], rec)
        self.assertEqual(nv2.load(str(path), 'text')[1], nv2.pack(rec))
        with mock.patch.object(sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(path.read_bytes()))):
            self.assertEqual(nv2.load('-')[0], rec)

    def test_the_bound_is_32_mib(self):
        self.assertEqual(nv2.MAX_INPUT_BYTES, 32 << 20)


def claim_size(result, size):
    """A stat result that claims `size` bytes, so a size check passes and only the read bound can stop the file."""
    return os.stat_result((result.st_mode, result.st_ino, result.st_dev, result.st_nlink, result.st_uid,
                           result.st_gid, size, 0, 0, 0))


class Hygiene(unittest.TestCase):
    def test_spdx_and_sizes(self):
        for name in ('nv2_record.py', 'test-nv2-record-hardening.py'):
            path = TOOLS / name
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), name)
            self.assertLess(len(text.splitlines()), 1500, name)
            self.assertLess(path.stat().st_size, 50 * 1024, name)


if __name__ == '__main__':
    unittest.main()
