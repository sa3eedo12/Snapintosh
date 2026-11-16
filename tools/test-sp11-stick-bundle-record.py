#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the authorisation record of tools/sp11_stick_bundle_build.py (docs/SP11-STICK-BUILD.md section 6): the optional note that is
appended to it, so that the operator's approval covers it, and what its store-class lines say about the dry-run wrapper
(docs/SP11-STICK-WRAPPER.md section 4).

    python3 tools/test-sp11-stick-bundle-record.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-bundle-record.py      (no test here needs q1n1's manifest tool)

The fixtures are those of tools/test-sp11-stick-bundle-build.py, which is at its size limit, loaded here with a scratch directory of its own
(build/sp11-bundle-record-scratch/, deleted at the end) so that both files can run at the same time. No hardware: NOT RUN."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS, THIS = ROOT / 'tools', Path(__file__).resolve()
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('sp11_stick_bundle_build_tests', TOOLS / 'test-sp11-stick-bundle-build.py')
BT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BT)
SCRATCH = ROOT / 'build' / 'sp11-bundle-record-scratch'
NOTE = ['DRY RUN NOTE: part of this record\'s digest.', '', '   an indented line and a trailing space ', 'SNAPMON.BIN is the wrapper of docs/SP11-STICK-WRAPPER.md.']
SECTION = '\n6. Notes of this build (part of the digest)\n'


def setUpModule():
    BT.SCRATCH = SCRATCH
    BT.setUpModule()


def tearDownModule():
    BT.tearDownModule()


def note_file(name, text):
    return BT.write(SCRATCH / 'notes' / name, text if isinstance(text, bytes) else text.encode())


class Record(BT.Case):
    def noted(self, text='\n'.join(NOTE) + '\n', **over):
        return BT.template(note=note_file('note.txt', text), **over)

    def test_the_stale_phrase_is_gone_and_the_record_points_at_the_wrapper_note(self):
        text = BT.template()[1]
        flat = ' '.join(text.split())
        self.assertNotIn('no wrapper exists yet', flat)
        for needle in ('docs/SP11-STICK-WRAPPER.md section 4', 'RAM stores only to page 0 of REPORT.DAT and, after the stage COPYING, to the chunk destinations',
                       'cache maintenance by VA; AT S1E2W and AT S1E2R', 'MRS reads; nothing else', 'The dry-run wrapper stores no EL2 system register',
                       'required in every PIN.DAT by the interface (STORE_REQUIRED)'):
            self.assertIn(needle, flat)
        with_gic = BT.template(stores='ram,el2_sysreg,gic')[1]
        self.assertIn('authorised_stores = 7', with_gic)
        self.assertIn('GIC registers for the timer stop (PPI 26)', ' '.join(with_gic.split()))
        block = with_gic.split('3. Store classes')[1].split('   Not authorised')[0]
        self.assertLessEqual(max(map(len, block.splitlines())), 120)        # wrapped by hand, so that no library version changes the digest

    def test_a_note_is_appended_verbatim_as_the_final_section_and_nothing_else_changes(self):
        plain, noted = BT.template()[1], self.noted()[1]
        self.assertEqual(noted, plain + SECTION + '\n'.join(NOTE) + '\n')
        self.assertEqual(len(noted.splitlines()), len(plain.splitlines()) + 2 + len(NOTE))
        self.assertEqual(self.noted('\n'.join(NOTE))[1], noted)            # the last line feed ends the last line, it is not text
        self.assertEqual(self.noted('\n'.join(NOTE) + '\n\n')[1], noted + '\n')
        self.assertEqual(self.noted(stores='ram,el2_sysreg,gic')[1], BT.template(stores='ram,el2_sysreg,gic')[1] + SECTION + '\n'.join(NOTE) + '\n')
        self.refused('STORE_NOT_AUTH', self.noted(stores='ram'))         # the interface still forces RAM and EL2_SYSREG, note or not

    def test_the_note_is_part_of_the_digest_so_another_note_is_another_record(self):
        plain, noted = BT.template()[1], self.noted()[1]
        approved, original = BT.write(SCRATCH / 'records/noted.txt', noted.encode()), note_file('note.txt', '\n'.join(NOTE) + '\n')
        code, text, err, out = BT.build_cli('noted', extra=['--no-manifest'], note=original)
        self.assertEqual(code, 0, (text, err))
        self.assertEqual((out / 'bundle/SP11/PIN.DAT').read_bytes()[0x60:0x80].hex(), BT.sha(noted.encode()))
        self.assertNotEqual(BT.sha(plain.encode()), BT.sha(noted.encode()))
        plain_record = BT.write(SCRATCH / 'records/plain.txt', plain.encode())
        changed = note_file('changed.txt', '\n'.join(NOTE).replace('DRY', 'dry') + '\n')
        for name, record, over in (('plain-record', plain_record, dict(note=original)), ('no-note', approved, {}), ('changed-note', approved, dict(note=changed))):
            self.refused('AUTH_RECORD_MISMATCH', BT.build_cli(name, approve=False, extra=['--no-manifest', '--authorisation', record], **over))
            self.assertFalse((SCRATCH / 'out' / name).exists())

    def test_a_note_that_is_not_plain_text_of_the_stated_size_is_refused(self):
        cases = {'not ASCII': 'caf\u00e9\n'.encode(), 'a carriage return': b'a\r\nb\r\n', 'a tab': b'a\tb\n', 'an escape sequence': b'\x1b[2J\n', 'a NUL': b'a\0b\n',
                 '41 lines': b'x\n' * 41, 'a line of 121 characters': b'x' * 121 + b'\n', 'over the size cap': b'x\n' * 2500, 'empty': b'', 'blank': b'\n \n'}
        for name, data in cases.items():
            with self.subTest(name):
                code, text, err = BT.template(note=note_file('bad.txt', data))
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=NOTE_BAD'), err)
                self.assertTrue(all(c == '\n' or ' ' <= c <= '~' for c in err), 'the detail quotes no byte of the file')
        os.symlink(note_file('target.txt', 'x\n'), SCRATCH / 'notes/link.txt')
        for name in ('missing.txt', 'link.txt', ''):                        # absent, a symbolic link, a directory
            self.refused('NOTE_BAD', BT.template(note=SCRATCH / 'notes' / name))
        self.assertEqual(self.noted(('x' * 120 + '\n') * 40)[0], 0)         # 40 lines of 120 characters is the most
        bad, good = note_file('bad.txt', b'caf\xc3\xa9\n'), note_file('good.txt', 'x\n')
        self.refused('NOTE_BAD', BT.build_cli('bad-note', approve=False, extra=['--no-manifest', '--authorisation-sha256', '1' * 64], note=bad))
        self.assertFalse((SCRATCH / 'out/bad-note').exists())
        real = dict(kernelcache=SCRATCH / 'in/none.kc', panel='withheld', sptm_hvc_calls=0)
        self.refused('NOTE_BAD', BT.template('real', note=bad, **real))
        self.refused('DESCRIPTOR_NOT_READY', BT.template('real', note=good, **real))      # the note passed: the next refusal is the descriptor's

    def test_this_file_is_licensed_small_and_free_of_machine_text(self):
        text = THIS.read_text()
        self.assertTrue(text.isascii() and 'SPDX-License-Identifier: MIT' in text[:100])
        self.assertLess(len(text.encode()), 50 * 1024)
        self.assertEqual([p for p in BT.MACHINE_PATHS if p in text], [])


if __name__ == '__main__':
    unittest.main()
