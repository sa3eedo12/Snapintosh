#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the SP11 stick wrapper (SNAPMON.BIN) and reporter (SNAPREP.BIN) of the bundle dry run, monitor port work item 4a
(docs/SP11-STICK-WRAPPER.md section 9): the dry run, the copy and its faults, the reporter, the seeded-bad wrappers and the default-size bundle.

    python3 tools/test-sp11-stick-wrapper.py
    SP11_WRAPPER_SKIP_SLOW=1 python3 tools/test-sp11-stick-wrapper.py      (without the default-size bundle: 144 MB under build/, deleted after)
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-wrapper.py   (the tests that need q1n1's headers and sha256.c SKIP)

The wrapper and the reporter under test are the REAL sources (probes/sp11-stick-*.c). The host harness (tools/test-sp11-stick-wrapper.c,
built with AddressSanitizer and UndefinedBehaviorSanitizer) replaces the machine and the runner: fake runner arguments and ops, a fake memory
map, fake regions of physical memory with a CPU view, a DRAM view and a dirty bit per cache line, and a log of every store, cache operation,
translation probe and guarded read. The bundles are made by the real builder (tools/sp11_stick_bundle_build.py synthetic). A seeded-bad
wrapper (a compile-time test define) must FAIL the check that matches its fault: that proves the checks can fail. The damaged-bundle
matrix, every refusal at its stage, is tools/test-sp11-stick-wrapper-refusals.py; the fixtures and the checks the two share are
tools/sp11_stick_wrapper_testlib.py and tools/sp11_stick_wrapper_checks.py.

No hardware, nothing under /Volumes, no network. Everything is written below build/sp11-wrapper-test-scratch-host/, deleted at the end. A SKIP
is never a PASS: without clang or a q1n1 checkout the harness tests report SKIP. EXPERIMENTAL; hardware NOT RUN."""
import os
import re
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_wrapper_checks import (FIX, STAGE, Checks, expected_reporter_lines, fresh, full_report, hexs, one, reporter_checks,  # noqa: E402
                                       run, runnable, set_up, tear_down)
from sp11_stick_wrapper_testlib import E, MIB, sb  # noqa: E402

VARIANTS = ('policy0', 'policy1', 'policy2', 'seed-early-store', 'seed-no-chunk-check', 'seed-no-prepass', 'seed-no-clean', 'seed-verify-source',
            'seed-no-at')


def setUpModule():
    set_up('host', variants=VARIANTS)


def tearDownModule():
    tear_down()


# ---- The happy path ------------------------------------------------------------------------------------------------------
class Happy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('clang or the q1n1 checkout is missing')
        cls.runs = {}
        for bundle in ('low', 'bank'):
            w = fresh(bundle, prefill=True, digest_chunks=True)
            cls.runs[bundle] = (w, one(f'happy-{bundle}', w, ('snapmon', 'snaprep')))

    def each(self, check):
        for bundle, (w, (mon, rep)) in self.runs.items():
            with self.subTest(bundle=bundle):
                check(w, mon, rep)

    def test_the_dry_run_reaches_done_and_returns_zero(self):
        def check(w, mon, rep):
            self.assertEqual((mon.ret, mon.faulted, mon.fault), (0, False, ''), mon.texts())
            head = mon.head()
            self.assertEqual((head['stage'], head['refusal'], head['pin_code'], head['guest_code']), (STAGE.DONE, 0, 0, 0))
            self.assertEqual(sb.check_report(full_report(mon.report)), 0)
            self.assertEqual(mon.report_tail, 0, 'the monitor window and the rest of page 0 stay zero')
            names = [re.match(r'SNAPMON STAGE=(\w+)', t).group(1) for t in mon.texts() if t.startswith('SNAPMON STAGE=')]
            self.assertEqual(names, ['ENTERED', 'ARGS_OK', 'FILES_OK', 'PIN_OK', 'OBSERVED_OK', 'PLACED_OK', 'GUEST_OK', 'COPYING', 'COPIED', 'DONE'])
            self.assertEqual(mon.texts()[0], 'SNAPMON START VERSION=1 EL=0X8 POLICY=0X3')
            self.assertEqual(mon.texts()[-1], 'SNAPMON MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD')
            self.assertEqual(Checks.lines(mon, sb.WRAPPER_LINES), [])
            self.assertEqual(head['lines_printed'], len(mon.lines))
        self.each(check)

    def test_the_head_says_what_was_read_pinned_and_copied(self):
        def check(w, mon, rep):
            h, f, b = mon.head(), w.bundle.fields, w.bundle
            data = [c for c in b.chunks if not c['flags'] & sb.CHUNK_ZERO]
            descs = w.descriptors()
            held = next(i for i, d in enumerate(descs) if d[0] == 7 and d[1] <= w.place[0] < d[1] + d[2] * 4096)
            self.assertEqual((h['current_el'], h['midr_el1'], h['mpidr_el1'], h['cntfrq_el0'], h['hcr_el2'], h['sctlr_el2'], h['tcr_el2']),
                             tuple(w.regs[k] for k in ('currentel', 'midr', 'mpidr', 'cntfrq', 'hcr', 'sctlr', 'tcr')))
            self.assertEqual((h['counter_hz'], h['hash_policy']), (f['cntfrq_hz'], 3))
            self.assertEqual((h['guest_file_base'], h['guest_file_size']), (w.GUEST, len(w.guest)))
            self.assertEqual((h['place_base'], h['place_size']), (f['place_base'], f['place_size']))
            self.assertEqual((h['desc_index'], h['desc_base'], h['desc_pages']), (held, descs[held][1], descs[held][2]))
            self.assertEqual((h['chunks_total'], h['chunks_copied'], h['chunks_hashed']), (len(b.chunks), len(b.chunks), len(data)))
            self.assertEqual((h['bytes_copied'], h['bytes_hashed']), (sum(c['size'] for c in b.chunks), sum(c['size'] for c in data)))
            self.assertTrue(0 < h['ticks_enter'] < h['ticks_placed'] < h['ticks_copied'] < h['ticks_hashed'])
            for field in ('ticks_launch', 'ticks_return', 'monitor_out', 'work_base', 'work_size', 'params_pa', 'monitor_ret', 'monitor_status',
                          'monitor_entered'):
                self.assertEqual(h[field], 0, f'{field} belongs to the monitor entry, which this build does not have')
        self.each(check)

    def test_nothing_but_the_report_is_stored_before_copying_and_every_store_is_a_chunk(self):
        def check(w, mon, rep):
            self.assertEqual(Checks.early_stores(mon, w), [])
            self.assertEqual(Checks.confined(mon, w), [])
            ranges = {c['dest']: c['size'] for c in w.bundle.chunks}
            placement_stores = [(e.a, e.b) for e in mon.of('ST') if not Checks.in_report(w, e.a, e.b)]
            covered = sorted(placement_stores)
            merged = []
            for a, n in covered:
                if merged and merged[-1][0] + merged[-1][1] == a:
                    merged[-1] = (merged[-1][0], merged[-1][1] + n)
                else:
                    merged.append((a, n))
            expect = []
            for dest in sorted(ranges):
                if expect and expect[-1][0] + expect[-1][1] == dest:
                    expect[-1] = (expect[-1][0], expect[-1][1] + ranges[dest])
                else:
                    expect.append((dest, ranges[dest]))
            self.assertEqual(merged, expect, 'the bytes written are exactly the bytes of the chunks, no more and no less')
            first = next(e for e in mon.of('ST') if not Checks.in_report(w, e.a, e.b))
            self.assertEqual(first.stage, STAGE.COPYING, 'the first store to the placement comes after COPYING is in REPORT.DAT')
        self.each(check)

    def test_every_chunk_is_in_the_placement_byte_identical_in_cpu_view_and_dram(self):
        def check(w, mon, rep):
            self.assertEqual(len(mon.digests), len(w.bundle.chunks))
            for i, (addr, size, cpu, dram) in enumerate(mon.digests):
                want = T.sha(T.expected_chunk_image(w.bundle, i))
                self.assertEqual((addr, size, cpu, dram), (w.bundle.chunks[i]['dest'], w.bundle.chunks[i]['size'], want, want), f'chunk {i}')
        self.each(check)

    def test_cache_operations_cover_exactly_what_was_written_and_the_caches_end_clean(self):
        self.each(lambda w, mon, rep: self.assertEqual(Checks.caches(mon, w), []))

    def test_barriers_separate_the_copy_the_invalidate_and_the_reread(self):
        self.each(lambda w, mon, rep: self.assertEqual(Checks.barriers(mon, w), []))

    def test_the_probes_cover_the_first_the_last_and_every_two_mib_boundary(self):
        def check(w, mon, rep):
            pb, ps = w.place
            expected = [(a, 1) for a in T.probe_addresses(w.REPORT, len(w.report))] + [(a, 0) for a in T.probe_addresses(w.PIN, 1024)]
            expected += [(a, 0) for a in T.probe_addresses(w.MAP, len(T.efi_map(w.descriptors())))] + [(a, 1) for a in T.probe_addresses(pb, ps)]
            expected += [(a, 0) for a in T.probe_addresses(w.GUEST, len(w.guest))]
            self.assertEqual([(e.a, e.extra) for e in mon.of('AT')], expected)
            self.assertEqual(mon.counts['at_w'], len(T.probe_addresses(w.REPORT, len(w.report))) + len(T.probe_addresses(pb, ps)))
            loads = [(e.a, e.b) for e in mon.of('LOAD32')]
            self.assertEqual(loads, [(pb, 0), (pb + ps - 4, 0)], 'the guarded read of the first and last word of the placement')
        self.each(check)

    def test_only_the_named_registers_are_read_and_no_register_is_written(self):
        def check(w, mon, rep):
            self.assertEqual({e.a for e in mon.of('MRS')}, set(range(8)), 'CurrentEL, MIDR, MPIDR, CNTFRQ, HCR_EL2, SCTLR_EL2, TCR_EL2 and CTR_EL0')
            self.assertEqual(mon.of('MRS')[0].a, 0, 'CurrentEL is read first')
        self.each(check)

    def test_the_reporter_prints_the_expected_summary_and_stores_nothing(self):
        def check(w, mon, rep):
            self.assertEqual((rep.ret, reporter_checks(rep, w)), (0, []), rep.texts())
            self.assertEqual(rep.texts(), expected_reporter_lines(mon.head()))
            self.assertNotIn('MONITOR STATUS', ' '.join(rep.texts()), 'the monitor window is not decoded in this build')
        self.each(check)

    def test_a_synthetic_bundle_for_each_placement_passed_the_interface_checks_first(self):
        for bundle in ('low', 'bank'):
            b = FIX[bundle]
            self.assertEqual((sb.check_pin(b.pin), sb.check_guest(b.guest, b.pin)), (0, 0))
            self.assertEqual(sb.check_digests(b.guest, b.pin, 3), (0, None))


# ---- The copy: tables that change under the wrapper, memory that changes after it, policies, cache geometry --------------------
def tamper(w, needle, offset, value, k, width=8):
    w.extra.append(f'TAMPER {needle} {w.GUEST + 128 + 64 * k + offset:#x} {int(value).to_bytes(width, "little").hex()}')


class Copy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('clang or the q1n1 checkout is missing')
        b = FIX['low']
        cls.b, cls.k = b, b.data_index()
        cls.z = b.zero_index()
        cls.work = b.fields['work_base']

    def world(self, **over):
        return fresh('low', prefill=True, digest_chunks=True, **over)

    def test_a_table_changed_after_the_digests_is_refused_before_the_first_store(self):
        w = self.world()
        tamper(w, 'STAGE=GUEST_OK', 0, 0x1000, self.k)
        r = one('tamper-prepass', w)[0]
        self.assertEqual(Checks.refusal(r, w, 'CHUNK_RANGE', 'GUEST_OK', self.k), [], r.texts())
        self.assertEqual([e for e in r.of('ST') if not Checks.in_report(w, e.a, e.b)], [])

    def test_a_table_changed_after_that_check_is_refused_at_the_chunk_before_its_store(self):
        for name, offset, value, code in (('outside the placement', 0, 0x1000, 'CHUNK_RANGE'), ('in the work area, outside the guest window', 0, None, 'CHUNK_RANGE'),
                                          ('size not a multiple of 16', 8, None, 'CHUNK_GEOMETRY'), ('source past the end of the file', 0x10, None, 'CHUNK_EOF')):
            with self.subTest(change=name):
                w = self.world()
                c = self.b.chunks[self.k]
                value = {'in the work area, outside the guest window': self.work, 'size not a multiple of 16': c['size'] + 8,
                         'source past the end of the file': len(w.guest) - 16}.get(name, value)
                tamper(w, 'STAGE=COPYING', offset, value, self.k)
                r = one('tamper-copy', w)[0]
                self.assertEqual(Checks.refusal(r, w, code, 'COPYING', self.k), [], r.texts())
                head = r.head()
                self.assertEqual((head['chunks_copied'], head['bytes_copied']), (self.k, sum(c['size'] for c in self.b.chunks[:self.k])))
                self.assertEqual(Checks.confined(r, w), [])
                done = [(c['dest'], c['dest'] + c['size']) for c in self.b.chunks[:self.k]]
                stray = [e for e in r.of('ST') if not Checks.in_report(w, e.a, e.b) and not any(s <= e.a < t for s, t in done)]
                self.assertEqual(stray, [], 'chunk k and everything after it was not written')
                self.assertEqual(r.texts()[-1], f'SNAPMON PLACEMENT WRITTEN CHUNKS_COPIED={self.k} OF={len(self.b.chunks)}')

    def test_a_flipped_bit_in_dram_or_a_lost_clean_is_found_by_the_reread_and_names_the_chunk(self):
        data, zero = self.b.chunks[self.k], self.b.chunks[self.z]
        cases = (('bit flipped in a data chunk', self.k, f'FLIP {data["dest"] + 5:#x} 0x10'), ('bit flipped in a zero chunk', self.z, f'FLIP {zero["dest"] + 70:#x} 0x01'),
                 ('lost clean in a data chunk', self.k, f'LOSE {data["dest"] + 64:#x}'), ('lost clean in a zero chunk', self.z, f'LOSE {zero["dest"] + 64:#x}'))
        for name, index, line in cases:
            with self.subTest(fault=name):
                w = self.world()
                w.extra.append(line)
                r = one('post-copy', w)[0]
                self.assertEqual(Checks.refusal(r, w, 'COPY_VERIFY', 'COPYING', index), [], r.texts())
                head = r.head()
                self.assertEqual((head['chunks_copied'], head['chunks_hashed']),
                                 (len(self.b.chunks), sum(1 for c in self.b.chunks[:index] if not c['flags'] & sb.CHUNK_ZERO)))
                self.assertEqual(Checks.confined(r, w), [])

    def test_damage_the_policy_does_not_see_is_found_by_the_copy_check(self):
        c = self.b.chunks[self.k]
        flipped = bytearray(self.b.guest)
        flipped[c['file_offset'] + 3] ^= 0x10
        for variant, code, stage in (('base', 'CHUNK_DIGEST', 'PLACED_OK'), ('policy1', 'CHUNK_DIGEST', 'PLACED_OK'), ('policy2', 'FILE_DIGEST', 'PLACED_OK'),
                                     ('policy0', 'COPY_VERIFY', 'COPYING')):
            with self.subTest(policy=variant):
                w = self.world(guest=bytes(flipped))
                r = one(f'policy-{variant}', w, variant=variant)[0]
                self.assertEqual(Checks.refusal(r, w, code, stage, self.k if code != 'FILE_DIGEST' else 0), [], r.texts())

    def test_each_policy_runs_to_done_and_the_report_says_which_was_used(self):
        for variant, policy in (('base', 3), ('policy0', 0), ('policy1', 1), ('policy2', 2)):
            with self.subTest(policy=variant):
                r = one(f'policy-ok-{variant}', fresh('low'), variant=variant)[0]
                self.assertEqual((r.ret, r.head()['stage'], r.head()['hash_policy']), (0, STAGE.DONE, policy))
                self.assertEqual(r.texts()[0], f'SNAPMON START VERSION=1 EL=0X8 POLICY={hexs(policy)}')

    def test_cache_maintenance_covers_every_line_whatever_the_line_size_register_says(self):
        for dmin in (0, 2, 4, 5, 15):
            with self.subTest(dmin_line=dmin):
                w = self.world(regs={**fresh('low').regs, 'ctr': 0x8444C004 & ~(15 << 16) | dmin << 16})
                r = one('lines', w)[0]
                self.assertEqual((r.ret, Checks.caches(r, w), Checks.barriers(r, w)), (0, [], []))
                stride = min(max(4 << dmin, 16), 64)
                cvac = r.counts['cvac']
                self.assertGreaterEqual(cvac, sum((c['size'] + stride - 1) // stride for c in self.b.chunks), 'at least one operation per stride')

    def test_junk_in_page_zero_of_report_dat_is_overwritten_and_the_head_is_valid(self):
        junk = bytes(range(1, 256)) * 16
        w = fresh('low', images={4: junk[:4096] + bytes(len(FIX['low'].report) - 4096)})
        r = one('junk', w)[0]
        self.assertEqual((r.ret, sb.check_report(full_report(r.report)), r.report_tail), (0, 0, 0))

    def test_a_counter_that_stands_still_gives_equal_ticks_and_a_valid_report(self):
        w = fresh('low', tick=(5, 0))
        r = one('still', w)[0]
        h = r.head()
        self.assertEqual((r.ret, h['ticks_enter'], h['ticks_placed'], h['ticks_copied'], h['ticks_hashed']), (0, 5, 5, 5, 5))


# ---- The reporter, on every shape of REPORT.DAT ---------------------------------------------------------------------------
class Reporter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('clang or the q1n1 checkout is missing')
        cls.good = full_report(one('rep-source', fresh('low'))[0].report)

    def world(self, report, **over):
        return fresh('low', report=report, images={2: None, 3: None}, with_placement=False, **over)

    def damaged(self, edit):
        data = bytearray(self.good)
        edit(data)
        return bytes(data)

    def test_the_head_that_fails_its_check_is_named_with_its_first_eight_words(self):
        u32, u64 = (lambda off, v: lambda b: struct.pack_into('<I', b, off, v)), (lambda off, v: lambda b: struct.pack_into('<Q', b, off, v))
        variants = (('magic', lambda b: b.__setitem__(5, b[5] ^ 1), 'REPORT_MAGIC'), ('version', u32(8, 2), 'REPORT_VERSION'), ('head_size', u32(0xC, 100), 'REPORT_SIZE'),
                    ('tail_byte', lambda b: b.__setitem__(0x200, 1), 'REPORT_RESERVED'), ('stage_unknown', u32(0x10, 14), 'REPORT_VALUE'),
                    ('refusal_unknown', u32(0x14, 7), 'REPORT_VALUE'), ('copied_over_total', u32(0xA0, 1000), 'REPORT_VALUE'),
                    ('lines_over_30', u32(0x24, 31), 'REPORT_VALUE'), ('policy_bit', u32(0x20, 4), 'REPORT_VALUE'), ('pin_code_unknown', u32(0x60, 7), 'REPORT_VALUE'),
                    ('refusal_after_the_monitor_ran', lambda b: (struct.pack_into('<I', b, 0x10, STAGE.MONITOR_RETURNED), struct.pack_into('<I', b, 0x14, E.PIN_DIGEST)),
                     'REPORT_VALUE'))
        batch = [(name, self.world(self.damaged(edit)), ('snaprep',)) for name, edit, _ in variants]
        results = run(batch)
        for (name, w, _), (_, _, code) in zip(batch, variants):
            with self.subTest(damage=name):
                r = results[name][0]
                self.assertEqual((r.ret, reporter_checks(r, w)), (sb.RET_REFUSED + getattr(E, code), []), r.texts())
                words = [struct.unpack_from('<Q', w.report, 8 * i)[0] for i in range(8)]
                self.assertEqual(r.texts(), [f'SNAPREP REFUSED={code} DETAIL=0X0', 'SNAPREP WORDS ' + ' '.join(hexs(x) for x in words[:4]),
                                             'SNAPREP WORDS ' + ' '.join(hexs(x) for x in words[4:])])
                self.assertEqual(sb.error_name(sb.check_report(w.report)), code, 'the interface\'s own check names the same damage')

    def test_an_empty_report_is_said_to_be_empty_and_an_unreadable_one_is_named(self):
        for name, report in (('all_zero', bytes(sb.REPORT_BYTES)), ('first_word_zero', bytes(4) + self.good[4:])):
            with self.subTest(report=name):
                w = self.world(report)
                r = one(f'empty-{name}', w, ('snaprep',))[0]
                self.assertEqual((r.ret, r.texts(), reporter_checks(r, w)), (0, ['SNAPREP REPORT EMPTY: THE WRAPPER WROTE NOTHING'], []))
        w = self.world(self.good)
        w.extra.append(f'LOADFAIL {w.REPORT:#x} 6')
        r = one('unreadable', w, ('snaprep',))[0]
        self.assertEqual((r.ret, r.texts(), reporter_checks(r, w)), (2, [f'SNAPREP REPORT UNREADABLE ADDR={hexs(w.REPORT)} RC=6'], []))

    def test_a_head_that_passes_is_decoded_whatever_stage_it_names_and_the_monitor_window_is_never_decoded(self):
        window = self.damaged(lambda b: b.__setitem__(slice(0x1000, 0x1010), b'\x55' * 16))
        ran = self.damaged(lambda b: struct.pack_into('<I', b, 0x10, STAGE.MONITOR_RETURNED))
        stopped = self.damaged(lambda b: struct.pack_into('<I', b, 0x10, STAGE.PLACED_OK))
        for name, report in (('window_not_zero', window), ('monitor_stage', ran), ('stopped_without_a_refusal', stopped), ('the_dry_run', self.good)):
            with self.subTest(report=name):
                w = self.world(report)
                r = one(f'decode-{name}', w, ('snaprep',))[0]
                self.assertEqual((r.ret, reporter_checks(r, w)), (0, []), r.texts())
                self.assertEqual(r.texts(), expected_reporter_lines(sb.unpack(report, sb.REPORT_LAYOUT)))
                self.assertNotIn('WINDOW', ' '.join(r.texts()))
                if name == 'the_dry_run':
                    self.assertTrue(r.texts()[0].startswith('SNAPREP DRY RUN COMPLETE'))

    def test_the_arguments_and_the_slots_are_refused_with_one_line(self):
        cases = (('table_magic', lambda w: setattr(w, 'table_magic', 0x1234), 'ARGS_BAD', 0), ('four_files', lambda w: setattr(w, 'table_count', 4), 'FILE_COUNT', 4),
                 ('six_files', lambda w: setattr(w, 'table_count', 6), 'FILE_COUNT', 6), ('report_size', lambda w: w.size_over.update({4: 0x80000}), 'FILE_SIZE', 4),
                 ('report_role', lambda w: w.paths.update({4: b'\\SP11\\REPORT.TXT'}), 'FILE_ROLE', 4),
                 ('guest_role', lambda w: w.paths.update({2: b'\\SP11\\GUEST.TXT'}), 'FILE_ROLE', 2), ('pin_base', lambda w: w.bases.update({3: 0}), 'FILE_BASE', 3))
        for name, mutate, code, detail in cases:
            with self.subTest(case=name):
                w = self.world(self.good)
                mutate(w)
                r = one(f'slots-{name}', w, ('snaprep',))[0]
                self.assertEqual((r.ret, r.texts(), reporter_checks(r, w)), (sb.RET_REFUSED + getattr(E, code), [f'SNAPREP REFUSED={code} DETAIL={hexs(detail)}'], []))
        for name, over in (('bad_magic', dict(args=(sb.RUNNER_MAGIC ^ 1, 1, 192))), ('no_puts', dict(ops=(0, 1, 1)))):
            with self.subTest(case=name):
                r = one(f'args-{name}', self.world(self.good, **over), ('snaprep',))[0]
                self.assertEqual((r.ret, r.texts()), (sb.RET_REFUSED + E.ARGS_BAD, []))
        for name, ops in (('no_hex', (1, 0, 1)), ('no_load32', (1, 1, 0))):
            with self.subTest(case=name):
                r = one(f'args-{name}', self.world(self.good, ops=ops), ('snaprep',))[0]
                self.assertEqual((r.ret, r.texts()), (sb.RET_REFUSED + E.ARGS_BAD, ['SNAPREP REFUSED=ARGS_BAD DETAIL=OPS']))

    def test_the_reporter_reads_only_the_table_and_report_dat(self):
        w = self.world(self.good)
        r = one('reads', w, ('snaprep',))[0]
        self.assertEqual(reporter_checks(r, w), [])
        self.assertTrue(r.of('VIEW') and all(w.table <= e.a < w.table + 0x2000 or w.REPORT <= e.a < w.REPORT + len(w.report) for e in r.of('VIEW')))
        self.assertEqual({e.a for e in r.of('LOAD32')}, {w.REPORT}, 'one guarded read, of the first word of REPORT.DAT')


# ---- The tests can fail: each seeded-bad wrapper breaks the check that matches its fault ------------------------------------
class SeededBad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('clang or the q1n1 checkout is missing')
        cls.b = FIX['low']
        cls.k = cls.b.data_index()

    def pair(self, name, w, variant):
        return one(f'{name}-good', w)[0], one(f'{name}-bad', w, variant=variant)[0]

    def test_a_store_outside_report_dat_before_copying_fails_the_write_log_check(self):
        w = fresh('low')
        good, bad = self.pair('early', w, 'seed-early-store')
        self.assertEqual(Checks.early_stores(good, w), [])
        self.assertTrue(Checks.early_stores(bad, w), 'the seeded wrapper stores one byte at the placement before COPYING and the check must say so')

    def test_a_skipped_range_check_fails_the_confinement_check(self):
        w = fresh('low', prefill=True)
        tamper(w, 'STAGE=COPYING', 0, FIX['low'].fields['work_base'], self.k)
        good, bad = self.pair('range', w, 'seed-no-chunk-check')
        self.assertEqual((Checks.fault(good), Checks.confined(good, w)), ([], []))
        self.assertEqual(Checks.refusal(good, w, 'CHUNK_RANGE', 'COPYING', self.k), [])
        self.assertTrue(Checks.confined(bad, w), 'the chunk landed in the work area: outside every chunk range')

    def test_a_skipped_table_prepass_fails_the_stage_the_refusal_is_raised_at(self):
        w = fresh('low')
        tamper(w, 'STAGE=GUEST_OK', 0, 0x1000, self.k)
        good, bad = self.pair('prepass', w, 'seed-no-prepass')
        self.assertEqual(Checks.refusal(good, w, 'CHUNK_RANGE', 'GUEST_OK', self.k), [])
        self.assertTrue(Checks.refusal(bad, w, 'CHUNK_RANGE', 'GUEST_OK', self.k), 'refused later, at COPYING, with the placement already written')

    def test_a_skipped_clean_fails_the_cache_coverage_check(self):
        w = fresh('low')
        good, bad = self.pair('clean', w, 'seed-no-clean')
        self.assertEqual(Checks.caches(good, w), [])
        self.assertTrue(Checks.caches(bad, w))

    def test_a_reread_of_the_source_fails_the_post_copy_fault_check(self):
        w = fresh('low', prefill=True)
        w.extra.append(f'FLIP {self.b.chunks[self.k]["dest"] + 5:#x} 0x10')
        good, bad = self.pair('verify', w, 'seed-verify-source')
        self.assertEqual(Checks.refusal(good, w, 'COPY_VERIFY', 'COPYING', self.k), [])
        self.assertTrue(Checks.refusal(bad, w, 'COPY_VERIFY', 'COPYING', self.k), 'a corrupted copy went unseen')

    def test_a_skipped_translation_probe_fails_the_unmapped_placement_check(self):
        w = fresh('low')
        w.extra.append(f'AT {w.place[0]:#x} 0x1000 unmapped')
        good, bad = self.pair('probe', w, 'seed-no-at')
        self.assertEqual(Checks.refusal(good, w, 'PLACE_UNREADABLE', 'OBSERVED_OK', w.place[0]), [])
        self.assertTrue(Checks.refusal(bad, w, 'PLACE_UNREADABLE', 'OBSERVED_OK', w.place[0]))


class Full(unittest.TestCase):
    """The default-size bundle, the size the unit will carry (a GUEST.DAT of about 144 MB), through the harness: the happy path again at
    full size. Slow, so SP11_WRAPPER_SKIP_SLOW=1 skips it (reported SKIP, not PASS)."""

    @classmethod
    def setUpClass(cls):
        if os.environ.get('SP11_WRAPPER_SKIP_SLOW'):
            raise unittest.SkipTest('SP11_WRAPPER_SKIP_SLOW is set')
        if not runnable():
            raise unittest.SkipTest('clang or the q1n1 checkout is missing')
        cls.bundle = FIX['fixtures'].bundle('full', T.LOW, sizes=None)
        cls.world = T.World(cls.bundle, FIX['scratch'], prefill=True, digest_chunks=True)
        cls.mon, cls.rep = one('full', cls.world, ('snapmon', 'snaprep'))

    def test_the_dry_run_reaches_done_and_every_chunk_is_in_the_placement_byte_identical(self):
        mon, w = self.mon, self.world
        self.assertGreater(len(w.guest), 100 * MIB)
        self.assertEqual((mon.ret, mon.faulted, mon.head()['stage'], mon.head()['refusal']), (0, False, STAGE.DONE, 0), mon.texts())
        self.assertEqual(sb.check_report(full_report(mon.report)), 0)
        self.assertEqual(len(mon.digests), len(w.bundle.chunks))
        for i, (addr, size, cpu, dram) in enumerate(mon.digests):
            want = T.sha(T.expected_chunk_image(w.bundle, i))
            self.assertEqual((addr, size, cpu, dram), (w.bundle.chunks[i]['dest'], w.bundle.chunks[i]['size'], want, want), f'chunk {i}')

    def test_the_stores_the_caches_and_the_barriers_hold_at_full_size_and_the_reporter_agrees(self):
        mon, rep, w = self.mon, self.rep, self.world
        for check in (Checks.early_stores, Checks.confined, Checks.caches, Checks.barriers):
            self.assertEqual(check(mon, w), [], check.__name__)
        self.assertEqual((rep.ret, reporter_checks(rep, w)), (0, []))
        self.assertEqual(rep.texts(), expected_reporter_lines(mon.head()))


if __name__ == '__main__':
    unittest.main()
