#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Damaged bundles for the SP11 stick wrapper (SNAPMON.BIN) and reporter (SNAPREP.BIN) of the bundle dry run, monitor port work item 4a
(docs/SP11-STICK-WRAPPER.md section 9): every check of the interface, refused with its code, at its stage and with its detail word, on the
host harness (tools/test-sp11-stick-wrapper.c under AddressSanitizer and UndefinedBehaviorSanitizer, around the REAL sources of
probes/sp11-stick-*.c).

    python3 tools/test-sp11-stick-wrapper-refusals.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-wrapper-refusals.py   (SKIP: q1n1's headers and sha256.c build the wrapper)

The bundles are made by the real builder (tools/sp11_stick_bundle_build.py synthetic) and DAMAGED one way per case: PIN.DAT, GUEST.DAT, the
registers, the memory map, the framebuffer, the translation results or the runner's arguments. After each the wrapper must stop with the
named refusal at the named stage, say so on the panel, store nothing but REPORT.DAT before COPYING, and the reporter must name what the
wrapper recorded. The other host-harness tests (the dry run, the copy, the reporter, the seeded-bad wrappers) are
tools/test-sp11-stick-wrapper.py; the fixtures and the checks they share are tools/sp11_stick_wrapper_testlib.py and
tools/sp11_stick_wrapper_checks.py.

No hardware, nothing under /Volumes, no network. Everything is written below build/sp11-wrapper-test-scratch-refusals/, deleted at the
end. A SKIP is never a PASS. EXPERIMENTAL; hardware NOT RUN."""
import hashlib
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_wrapper_checks import Checks, expected_reporter_lines, fresh, one, reporter_checks, run, runnable, set_up, tear_down  # noqa: E402
from sp11_stick_wrapper_testlib import E, MIB, sb  # noqa: E402


def setUpModule():
    set_up('refusals', variants=('gic',), bundles=('low',))


def tearDownModule():
    tear_down()


# ---- Refusals: every check of the interface, at the stage that raises it ---------------------------------------------------
def pin_fields(**fields):
    return lambda w: setattr(w, 'pin', w.bundle.repacked_pin(**fields))


def raw(attribute, edit):
    def mutate(w):
        data = bytearray(getattr(w, attribute))
        edit(data)
        setattr(w, attribute, bytes(data))
    return mutate


def regs(**values):
    return lambda w: w.regs.update(values)


def put(attribute, offset, fmt, value):
    return raw(attribute, lambda b: struct.pack_into(fmt, b, offset, value))


def rule(address, length, kind):
    return lambda w: w.extra.append(f'AT {address(w):#x} {length:#x} {kind}')


def slot(**changes):
    def mutate(w):
        for key, values in changes.items():
            getattr(w, key).update(values)
    return mutate


def descriptor_edit(fn):
    """Replace the conventional descriptor that holds the placement by fn(descriptor) (a list of descriptors) in the map the wrapper reads."""
    def mutate(w):
        out = []
        for d in w.descriptors():
            out += fn(w, d) if d[0] == 7 and d[1] <= w.place[0] < d[1] + d[2] * 4096 else [d]
        w.map_descs = out
    return mutate


def inside_placement(w):
    """A world whose stack, or framebuffer, lies inside the placement and whose map does not say so; the placement has no region of its
    own in the harness, because the refusal comes before anything is read or written there."""
    w.with_placement = False


def declared_in_placement(w):
    inside_placement(w)
    w.map_descs = [d for d in w.descriptors() if d[1] != w.stack[0]]
    w.stack = (w.place[0] + 0x1000000, 0x10000)


def framebuffer_in_placement(w):
    inside_placement(w)
    w.fb = (w.place[0] + 0x100000, *w.fb[1:])
    w.map_descs = [d for d in w.descriptors() if not (d[0] == 0 and d[1] == w.fb[0])]
    w.pin = w.bundle.repacked_pin(fb_base=w.fb[0])


def avoid_in_placement(w):
    w.pin = w.bundle.repacked_pin(avoid=struct.pack('<QQII', w.place[0] + 0x2000000, 0x100000, 3, 0).ljust(96, b'\0'))


def pad_target(bundle):
    """A data chunk that has pad bytes after it and a data chunk behind it."""
    data = [i for i, c in enumerate(bundle.chunks) if not c['flags'] & sb.CHUNK_ZERO]
    return next(i for i in data[:-1] if bundle.chunks[i]['size'] % 64)


def retag(which, kind):
    """The chunk table with the kind of the chunks `which(bundle, i, chunk)` picks changed: the container rules on kinds are checked after the
    per-chunk rules, and the table digest is not recomputed because the digests come later still."""
    def mutate(w):
        data = bytearray(w.bundle.guest)
        for i, c in enumerate(w.bundle.chunks):
            if which(w.bundle, i, c):
                struct.pack_into('<H', data, 128 + 64 * i + 0x18, kind)
        w.guest = bytes(data)
    return mutate


PIN_STAGE, OBS_STAGE, PLACE_STAGE, GUEST_STAGE = 'FILES_OK', 'PIN_OK', 'OBSERVED_OK', 'PLACED_OK'
PB, PE = (lambda w: w.place[0]), (lambda w: w.place[0] + w.place[1])
SILENT = [('args_magic', dict(args=(sb.RUNNER_MAGIC ^ 1, 1, 192))), ('args_version', dict(args=(sb.RUNNER_MAGIC, 2, 192))),
          ('args_size', dict(args=(sb.RUNNER_MAGIC, 1, 191))), ('no_puts', dict(ops=(0, 1, 1)))]
# name, refusal, stage the head names ('NONE': nothing could be recorded), the detail word (a number, a function of the world, or None for
# "not checked"), the change to the world, and the harness variant.
CASES = [
    ('not_el2', 'NOT_EL2', 'NONE', 4, regs(currentel=4), 'base'),
    ('table_magic', 'ARGS_BAD', 'NONE', 0, lambda w: setattr(w, 'table_magic', 0x1234), 'base'),
    ('table_count_6', 'FILE_COUNT', 'NONE', 6, lambda w: setattr(w, 'table_count', 6), 'base'),
    ('table_count_4', 'FILE_COUNT', 'NONE', 4, lambda w: setattr(w, 'table_count', 4), 'base'),
    ('this_payload_is_slot_1', 'ARGS_BAD', 'NONE', 1, lambda w: setattr(w, 'self_slot', 1), 'base'),
    ('report_path', 'FILE_ROLE', 'NONE', 4, lambda w: w.paths.update({4: b'\\SP11\\REPORT.TXT'}), 'base'),
    ('report_size', 'FILE_SIZE', 'NONE', 4, slot(size_over={4: 0x80000}), 'base'),
    ('report_base_zero', 'FILE_BASE', 'NONE', 4, slot(bases={4: 0}), 'base'),
    ('report_not_mapped', 'PLACE_UNREADABLE', 'NONE', lambda w: w.REPORT, rule(lambda w: w.REPORT, 0x1000, 'unmapped'), 'base'),
    ('report_last_page_read_only', 'PLACE_UNREADABLE', 'NONE', lambda w: w.REPORT + 0x80000, rule(lambda w: w.REPORT + 0x80000, 0x1000, 'ro'), 'base'),
    ('report_device_memory', 'PLACE_UNREADABLE', 'NONE', lambda w: w.REPORT, rule(lambda w: w.REPORT, 0x1000, 'device'), 'base'),
    ('guest_path', 'FILE_ROLE', 'ARGS_OK', 2, lambda w: w.paths.update({2: b'\\SP11\\GUEST.TXT'}), 'base'),
    ('paths_swapped', 'FILE_ROLE', 'ARGS_OK', 2, lambda w: w.paths.update({2: b'\\SP11\\PIN.DAT', 3: b'\\SP11\\GUEST.DAT'}), 'base'),
    ('payload_base_unaligned', 'FILE_BASE', 'ARGS_OK', 0, slot(bases={0: T.World.MON + 0x10}), 'base'),
    ('pin_base_zero', 'FILE_BASE', 'ARGS_OK', 3, slot(bases={3: 0}), 'base'),
    ('reporter_size_zero', 'FILE_SIZE', 'ARGS_OK', 1, slot(size_over={1: 0}), 'base'),
    ('guest_over_the_cap', 'FILE_SIZE', 'ARGS_OK', 2, slot(size_over={2: (256 << 20) + 1}), 'base'),
    ('pin_truncated', 'PIN_TRUNCATED', PIN_STAGE, 0, lambda w: setattr(w, 'pin', w.bundle.pin[:1023]), 'base'),
    ('pin_longer', 'PIN_SIZE', PIN_STAGE, 0, lambda w: setattr(w, 'pin', w.bundle.pin + b'\0'), 'base'),
    ('pin_magic', 'PIN_MAGIC', PIN_STAGE, 0, raw('pin', lambda b: b.__setitem__(0, b[0] ^ 1)), 'base'),
    ('pin_version', 'PIN_VERSION', PIN_STAGE, 0, pin_fields(version=2), 'base'),
    ('pin_size_field', 'PIN_SIZE', PIN_STAGE, 0, pin_fields(size=1023), 'base'),
    ('pin_digest', 'PIN_DIGEST', PIN_STAGE, 0, raw('pin', lambda b: b.__setitem__(0xD8, b[0xD8] ^ 1)), 'base'),
    ('pin_flags', 'PIN_FLAGS', PIN_STAGE, 0, pin_fields(flags=1), 'base'),
    ('pin_string', 'PIN_STRING', PIN_STAGE, 0, pin_fields(build_label=b''), 'base'),
    ('pin_count', 'PIN_COUNT', PIN_STAGE, 0, pin_fields(midr_count=0), 'base'),
    ('pin_reserved', 'PIN_RESERVED', PIN_STAGE, 0, pin_fields(reserved0=1), 'base'),
    ('pin_order', 'PIN_ORDER', PIN_STAGE, 0, pin_fields(midr=struct.pack('<4I', 0x512F0011, 0x511F0011, 0, 0)), 'base'),
    ('pin_value', 'PIN_VALUE', PIN_STAGE, 0, pin_fields(cntfrq_hz=0), 'base'),
    ('no_authorisation', 'NO_AUTHORISATION', PIN_STAGE, 0, pin_fields(authorisation_sha256=bytes(32)), 'base'),
    ('store_class_not_authorised', 'STORE_NOT_AUTH', PIN_STAGE, 0, pin_fields(authorised_stores=1), 'base'),
    ('placement_unaligned', 'PLACE_ALIGN', PIN_STAGE, 0, pin_fields(reserve_size=48 * MIB + 4096), 'base'),
    ('placement_shape', 'PLACE_LAYOUT', PIN_STAGE, 0, pin_fields(work_size=239 * MIB), 'base'),
    ('placement_beyond_2_36', 'PLACE_LIMIT', PIN_STAGE, 0, pin_fields(**sb.placement((1 << 36) - 96 * MIB, 256 * MIB)), 'base'),
    ('pin_not_mapped', 'PLACE_UNREADABLE', PIN_STAGE, lambda w: w.PIN, rule(lambda w: w.PIN, 0x1000, 'unmapped'), 'base'),
    ('midr', 'MIDR_MISMATCH', OBS_STAGE, 0x410FD0C0, regs(midr=0x410FD0C0), 'base'),
    ('mpidr', 'MPIDR_MISMATCH', OBS_STAGE, 0x80000100, regs(mpidr=0x80000100), 'base'),
    ('cntfrq', 'CNTFRQ_MISMATCH', OBS_STAGE, 24000000, regs(cntfrq=24000000), 'base'),
    ('e2h_set', 'E2H_SET', OBS_STAGE, 0x88000010 | 1 << 34, regs(hcr=0x88000010 | 1 << 34), 'base'),
    ('mmu_off', 'EL2_REGIME', OBS_STAGE, 0x100030C5183C, regs(sctlr=0x100030C5183C), 'base'),
    ('granule_16k', 'EL2_REGIME', OBS_STAGE, 0x80843514 | 2 << 14, regs(tcr=0x80843514 | 2 << 14), 'base'),
    ('t0sz_30', 'EL2_REGIME', OBS_STAGE, (0x80843514 & ~63) | 30, regs(tcr=(0x80843514 & ~63) | 30), 'base'),
    ('framebuffer_width', 'GOP_MISMATCH', OBS_STAGE, lambda w: w.fb[0], lambda w: setattr(w, 'fb', (*w.fb[:2], w.fb[2] + 1, *w.fb[3:])), 'base'),
    ('framebuffer_base', 'GOP_MISMATCH', OBS_STAGE, lambda w: w.fb[0], lambda w: setattr(w, 'fb', (w.fb[0] + 0x1000, *w.fb[1:])), 'base'),
    ('gic_macros_of_the_build', 'GIC_MISMATCH', OBS_STAGE, 0, lambda w: None, 'gic'),
    ('map_stride_below_40', 'MAP_BAD', PLACE_STAGE, PB, lambda w: setattr(w, 'map_stride', 24), 'base'),
    ('map_size_not_whole', 'MAP_BAD', PLACE_STAGE, PB, lambda w: setattr(w, 'map_size', len(T.efi_map(w.descriptors())) - 1), 'base'),
    ('placement_not_conventional', 'PLACE_NOT_CONV', PLACE_STAGE, PB, descriptor_edit(lambda w, d: [(2, d[1], d[2], d[3])]), 'base'),
    ('placement_across_two_descriptors', 'PLACE_NOT_CONV', PLACE_STAGE, PB,
     descriptor_edit(lambda w, d: [(7, d[1], d[2] // 2, d[3]), (7, d[1] + d[2] // 2 * 4096, d[2] - d[2] // 2, d[3])]), 'base'),
    ('another_descriptor_touches_it', 'PLACE_NOT_CONV', PLACE_STAGE, PB, descriptor_edit(lambda w, d: [d, (2, w.place[0] + 0x1000000, 1, d[3])]), 'base'),
    ('descriptor_without_write_back', 'PLACE_ATTR', PLACE_STAGE, PB, descriptor_edit(lambda w, d: [(d[0], d[1], d[2], d[3] & ~8)]), 'base'),
    ('declared_range_inside_it', 'PLACE_DECLARED', PLACE_STAGE, PB, declared_in_placement, 'base'),
    ('framebuffer_inside_it', 'PLACE_FB', PLACE_STAGE, PB, framebuffer_in_placement, 'base'),
    ('avoid_range_inside_it', 'PLACE_AVOID', PLACE_STAGE, PB, avoid_in_placement, 'base'),
    ('first_word_aborts', 'PLACE_UNREADABLE', PLACE_STAGE, PB, lambda w: w.extra.append(f'LOADFAIL {w.place[0]:#x} 6'), 'base'),
    ('last_word_aborts', 'PLACE_UNREADABLE', PLACE_STAGE, lambda w: w.place[0] + w.place[1] - 4,
     lambda w: w.extra.append(f'LOADFAIL {w.place[0] + w.place[1] - 4:#x} 6'), 'base'),
    ('guarded_read_refused', 'PLACE_UNREADABLE', PLACE_STAGE, PB, lambda w: w.extra.append(f'LOADFAIL {w.place[0]:#x} 5'), 'base'),
    ('first_page_not_mapped', 'PLACE_UNREADABLE', PLACE_STAGE, PB, rule(PB, 0x1000, 'unmapped'), 'base'),
    ('last_page_read_only', 'PLACE_UNREADABLE', PLACE_STAGE, lambda w: w.place[0] + w.place[1] - 0x1000, rule(lambda w: PE(w) - 0x1000, 0x1000, 'ro'), 'base'),
    ('a_middle_boundary_is_device', 'PLACE_UNREADABLE', PLACE_STAGE, lambda w: w.place[0] + 0x400000, rule(lambda w: PB(w) + 0x400000, 0x1000, 'device'), 'base'),
    ('first_page_non_cacheable', 'PLACE_UNREADABLE', PLACE_STAGE, PB, rule(PB, 0x1000, 'nc'), 'base'),
    ('first_page_write_through', 'PLACE_UNREADABLE', PLACE_STAGE, PB, rule(PB, 0x1000, 'wt'), 'base'),
    ('first_page_not_an_identity_map', 'PLACE_UNREADABLE', PLACE_STAGE, PB, rule(PB, 0x1000, 'nonident'), 'base'),
    ('first_page_external_abort', 'PLACE_UNREADABLE', PLACE_STAGE, PB, rule(PB, 0x1000, 'abort'), 'base'),
    ('map_not_mapped', 'PLACE_UNREADABLE', PLACE_STAGE, lambda w: w.MAP, rule(lambda w: w.MAP, 0x1000, 'unmapped'), 'base'),
    ('guest_not_mapped', 'PLACE_UNREADABLE', GUEST_STAGE, lambda w: w.GUEST, rule(lambda w: w.GUEST, 0x1000, 'unmapped'), 'base'),
    ('guest_last_page_not_mapped', 'PLACE_UNREADABLE', GUEST_STAGE, lambda w: w.GUEST + (len(w.guest) - 1) // 4096 * 4096,
     rule(lambda w: w.GUEST + (len(w.guest) - 1) // 4096 * 4096, 0x1000, 'unmapped'), 'base'),
    ('guest_truncated', 'GUEST_TRUNCATED', GUEST_STAGE, 0, lambda w: setattr(w, 'guest', w.bundle.guest[:-1]), 'base'),
    ('guest_longer_than_its_head_says', 'GUEST_SIZE', GUEST_STAGE, 0, lambda w: setattr(w, 'guest', w.bundle.guest + bytes(64)), 'base'),
    ('guest_magic', 'GUEST_MAGIC', GUEST_STAGE, 0, raw('guest', lambda b: b.__setitem__(0, b[0] ^ 1)), 'base'),
    ('guest_version', 'GUEST_VERSION', GUEST_STAGE, 0, raw('guest', lambda b: b.__setitem__(8, 2)), 'base'),
    ('guest_flags', 'GUEST_FLAGS', GUEST_STAGE, 0, raw('guest', lambda b: b.__setitem__(0x10, 1)), 'base'),
    ('guest_does_not_match_the_pin', 'GUEST_PIN', GUEST_STAGE, 0, lambda w: setattr(w, 'pin', w.bundle.repacked_pin(guest_chunk_count=99)), 'base'),
    ('chunk_destination_outside_the_window', 'CHUNK_RANGE', GUEST_STAGE, 0,
     lambda w: raw('guest', lambda b: struct.pack_into('<Q', b, 128, w.bundle.head['load_base'] - 0x10000))(w), 'base'),
    ('table_digest', 'INDEX_DIGEST', GUEST_STAGE, 0, raw('guest', lambda b: b.__setitem__(128 + 0x20, b[128 + 0x20] ^ 1)), 'base'),
    ('chunk_digest_names_the_chunk', 'CHUNK_DIGEST', GUEST_STAGE, lambda w: w.bundle.data_index(),
     lambda w: raw('guest', lambda b: b.__setitem__(w.bundle.chunks[w.bundle.data_index()]['file_offset'] + 3,
                                                   b[w.bundle.chunks[w.bundle.data_index()]['file_offset'] + 3] ^ 0x10))(w), 'base'),
    ('file_digest', 'FILE_DIGEST', GUEST_STAGE, 0, pin_fields(guest_file_sha256=hashlib.sha256(b'another file').digest()), 'base'),
    ('two_params_chunks', 'CHUNK_DUP', GUEST_STAGE, 0, retag(lambda b, i, c: i == b.data_index(), sb.KIND_PARAMS), 'base'),
    ('no_handoff_chunk', 'CHUNK_MISSING', GUEST_STAGE, 0, retag(lambda b, i, c: c['kind'] == sb.KIND_HANDOFF, sb.KIND_OTHER), 'base'),
    ('pad_byte_set', 'PAD_NONZERO', GUEST_STAGE, 0,
     lambda w: raw('guest', lambda b: b.__setitem__(w.bundle.chunks[pad_target(w.bundle)]['file_offset'] + w.bundle.chunks[pad_target(w.bundle)]['size'], 1))(w),
     'base'),
    ('chunk_kind_unknown', 'CHUNK_KIND', GUEST_STAGE, 0, put('guest', 128 + 0x18, '<H', 9), 'base'),
    ('chunk_flag_unknown', 'CHUNK_FLAGS', GUEST_STAGE, 0, put('guest', 128 + 0x1A, '<H', 2), 'base'),
    ('chunk_reserved_word', 'CHUNK_RESERVED', GUEST_STAGE, 0, put('guest', 128 + 0x1C, '<I', 1), 'base'),
    ('data_chunk_without_a_digest', 'CHUNK_ZERO', GUEST_STAGE, 0, raw('guest', lambda b: b.__setitem__(slice(128 + 0x20, 128 + 0x40), bytes(32))), 'base'),
    ('destination_not_sixteen_aligned', 'CHUNK_GEOMETRY', GUEST_STAGE, 0, lambda w: put('guest', 128, '<Q', w.bundle.head['load_base'] + 8)(w), 'base'),
    ('chunks_out_of_order', 'CHUNK_ORDER', GUEST_STAGE, 0, lambda w: put('guest', 128 + 64 * 2, '<Q', w.bundle.chunks[1]['dest'] - 0x10)(w), 'base'),
    ('chunks_overlap', 'CHUNK_OVERLAP', GUEST_STAGE, 0, lambda w: put('guest', 128 + 64, '<Q', w.bundle.chunks[0]['dest'] + 0x10)(w), 'base'),
    ('data_not_where_the_layout_puts_it', 'CHUNK_OFFSET', GUEST_STAGE, 0,
     lambda w: put('guest', 128 + 0x10, '<Q', w.bundle.chunks[0]['file_offset'] + 64)(w), 'base'),
    ('data_past_the_end_of_the_file', 'CHUNK_EOF', GUEST_STAGE, 0, lambda w: put('guest', 128 + 0x10, '<Q', len(w.guest) - 16)(w), 'base'),
    ('entry_count_over_1024', 'GUEST_COUNT', GUEST_STAGE, 0, put('guest', 0x18, '<I', 2000), 'base'),
    ('head_reserved_word', 'GUEST_RESERVED', GUEST_STAGE, 0, put('guest', 0x1C, '<I', 1), 'base'),
    ('load_window_is_not_the_pinned_guest', 'GUEST_LOAD', GUEST_STAGE, 0, lambda w: put('guest', 0x38, '<Q', w.bundle.head['load_base'] + 32 * MIB)(w), 'base'),
    ('head_size_field', 'GUEST_SIZE', GUEST_STAGE, 0, put('guest', 0x0C, '<I', 64), 'base'),
]


class Refusals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('clang or the q1n1 checkout is missing')
        cls.cases, cls.worlds, cls.results = {c[0]: c for c in CASES}, {}, {}
        assert len(cls.cases) == len(CASES), 'case names are unique'
        for variant in ('base', 'gic'):
            batch = []
            for name, code, stage, detail, mutate, v in CASES:
                if v != variant:
                    continue
                w = fresh('low')
                mutate(w)
                cls.worlds[name] = w
                batch.append((name, w, ('snapmon', 'snaprep')))
            cls.results.update(run(batch, variant))

    def test_every_damaged_bundle_is_refused_with_its_code_at_its_stage(self):
        for name, code, stage, detail, mutate, variant in CASES:
            with self.subTest(case=name):
                w, (mon, rep) = self.worlds[name], self.results[name]
                want = detail(w) if callable(detail) else detail
                self.assertEqual(Checks.refusal(mon, w, code, stage, want), [], mon.texts())

    def test_a_refusal_before_copying_leaves_the_placement_and_everything_else_as_it_was(self):
        for name, code, stage, detail, mutate, variant in CASES:
            with self.subTest(case=name):
                mon = self.results[name][0]
                self.assertEqual([e for e in mon.of('ST') if not Checks.in_report(self.worlds[name], e.a, e.b)], [])
                self.assertEqual(mon.dirty.get('placement', 0), 0)
                self.assertNotIn('DONE', ' '.join(mon.texts()))

    def test_the_printed_second_line_says_whether_ram_was_touched(self):
        for name, code, stage, detail, mutate, variant in CASES:
            with self.subTest(case=name):
                second = [t for t in self.results[name][0].texts() if t.startswith('SNAPMON REFUSED=')]
                texts = self.results[name][0].texts()
                after = texts[texts.index(second[0]) + 1]
                self.assertEqual(after, 'SNAPMON NOTHING STORED: REPORT.DAT NOT USED YET' if stage == 'NONE' else 'SNAPMON RAM UNTOUCHED BEYOND REPORT.DAT')

    def test_the_head_records_the_raw_registers_even_when_they_are_refused(self):
        for name, code, stage, detail, mutate, variant in CASES:
            if stage != OBS_STAGE or code == 'GIC_MISMATCH':
                continue
            with self.subTest(case=name):
                w, h = self.worlds[name], self.results[name][0].head()
                self.assertEqual((h['midr_el1'], h['mpidr_el1'], h['cntfrq_el0'], h['hcr_el2'], h['sctlr_el2'], h['tcr_el2']),
                                 tuple(w.regs[k] for k in ('midr', 'mpidr', 'cntfrq', 'hcr', 'sctlr', 'tcr')))

    def test_the_wrapper_records_which_verdict_it_gave_on_which_record(self):
        for name, code, stage, detail, mutate, variant in CASES:
            if stage == 'NONE':
                continue
            with self.subTest(case=name):
                h = self.results[name][0].head()
                pin = getattr(E, code) if stage == PIN_STAGE and code != 'PLACE_UNREADABLE' else 0
                guest = getattr(E, code) if stage == GUEST_STAGE and code != 'PLACE_UNREADABLE' else 0
                self.assertEqual((h['pin_code'], h['guest_code']), (pin, guest))

    def test_the_reporter_names_what_the_wrapper_recorded_and_stores_nothing(self):
        for name, code, stage, detail, mutate, variant in CASES:
            with self.subTest(case=name):
                w, (mon, rep) = self.worlds[name], self.results[name]
                self.assertEqual(reporter_checks(rep, w), [], rep.texts())
                if stage == 'NONE' or code.startswith('FILE_'):
                    continue
                self.assertEqual(rep.texts(), expected_reporter_lines(mon.head()))
                self.assertTrue(rep.texts()[0].startswith(f'SNAPREP REFUSED={code} STAGE={stage} '), rep.texts())

    def test_the_silent_refusals_print_nothing_and_touch_nothing(self):
        batch = [(name, fresh('low', **over), ('snapmon',)) for name, over in SILENT]
        for (name, w, _), result in zip(batch, run(batch).values()):
            with self.subTest(case=name):
                r = result[0]
                self.assertEqual((r.ret, r.texts(), [(e.kind, e.a) for e in r.events], r.faulted), (sb.RET_REFUSED + E.ARGS_BAD, [], [('MRS', 0)], False))

    def test_missing_ops_are_named_when_there_is_a_puts_and_the_run_ends(self):
        for name, ops in (('no_hex', (1, 0, 1)), ('no_load32', (1, 1, 0))):
            with self.subTest(case=name):
                r = one(name, fresh('low', ops=ops))[0]
                self.assertEqual((r.ret, r.texts(), [(e.kind, e.a) for e in r.events]),
                                 (sb.RET_REFUSED + E.ARGS_BAD, ['SNAPMON REFUSED=ARGS_BAD STAGE=NONE DETAIL=OPS'], [('MRS', 0)]))

    def test_not_el2_reads_current_el_and_nothing_else(self):
        r = self.results['not_el2'][0]
        self.assertEqual([e.a for e in r.of('MRS')], [0], 'CurrentEL only')
        self.assertEqual((r.counts['store'], r.counts['at_w'], r.counts['at_r'], r.counts['cvac'], r.counts['load32']), (0, 0, 0, 0, 0))
        self.assertEqual(r.texts()[-2:], ['SNAPMON REFUSED=NOT_EL2 STAGE=NONE DETAIL=0X4', 'SNAPMON NOTHING STORED: REPORT.DAT NOT USED YET'])

    def test_every_refusal_the_wrapper_can_raise_has_a_case(self):
        covered = {code for _, code, *_ in CASES}
        wrapper_side = {name for _, name, sides in sb.ERRORS if sides & 2}
        excused = {'HASH_POLICY': 'a compile-time constant that a static assertion refuses (the Gate tests)',
                   'GIC_NOT_READY': 'UNIMPLEMENTED: this build has no GIC code', 'COPY_VERIFY': 'the Copy tests'}
        self.assertEqual(sorted(wrapper_side - covered - set(excused)), [])
        self.assertTrue(set(excused) <= wrapper_side)


if __name__ == '__main__':
    unittest.main()
