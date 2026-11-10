#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the 120 Hz additions to tools/sp11_display_mode.py: EDID extension decoding, the link-budget model and
the `modes` and `link-budget` subcommands (tools/sp11_edid_ext.py, tools/sp11_cta_vic.py, tools/sp11_link_budget.py).

No hardware and no private input. Every EDID block is built here from the public layouts by an encoder written
separately from the decoders under test. The panel-like timings are SYNTHETIC: their numbers say nothing about the
Surface Pro 11's real EDID. The reduced-blanking vectors are the VESA DMT entries listed in Linux `drm_edid.c` at the
commit named in docs/SP11-120HZ-STUDY.md. tools/test-sp11-display-120hz-mutants.py seeds bugs into copies of the modules
(under build/) and requires this suite to fail on each one.

    python3 tools/test-sp11-display-120hz.py
    SP11_LINUX_TREE=<checkout> python3 tools/test-sp11-display-120hz.py     # also compares the whole VIC table
"""
import contextlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get('SP11_TEST_BUILD_DIR') or ROOT / 'build' / 'test-sp11-display-120hz')   # unique per mutant run
MODULE_DIR = Path(os.environ.get('SP11_DISPLAY_MODE_DIR') or ROOT / 'tools')
sys.path.insert(0, str(MODULE_DIR))
import sp11_cta_vic as vics                 # noqa: E402
import sp11_display_mode as dm              # noqa: E402
import sp11_edid_ext as ee                  # noqa: E402
import sp11_link_budget as lb               # noqa: E402

HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')
PANEL = (2880, 1920)                        # a SYNTHETIC panel size for fixtures only: no module may know it
SYN60 = dict(clock10k=34632, hactive=2880, hblank=80, vactive=1920, vblank=30, hfront=24, hsync=16, vfront=8, vsync=4)


def encode_dtd(clock10k, hactive, hblank, vactive, vblank, hfront, hsync, vfront, vsync, flags=0x1e):
    b = bytearray(18)
    b[0], b[1] = clock10k & 0xff, clock10k >> 8
    b[2], b[3], b[4] = hactive & 0xff, hblank & 0xff, (hactive >> 8) << 4 | hblank >> 8
    b[5], b[6], b[7] = vactive & 0xff, vblank & 0xff, (vactive >> 8) << 4 | vblank >> 8
    b[8], b[9], b[10] = hfront & 0xff, hsync & 0xff, (vfront & 15) << 4 | (vsync & 15)
    b[11] = (hfront >> 8) << 6 | (hsync >> 8) << 4 | (vfront >> 4) << 2 | (vsync >> 4)
    b[17] = flags
    return bytes(b)


def seal(block):
    block = bytearray(block)
    block[127] = (-sum(block[:127])) & 0xff
    return bytes(block)


def base_block(dtds=(), declared=True, revision=4, extensions=0, mutate=None):
    b = bytearray(128)
    b[:8] = bytes([0, 255, 255, 255, 255, 255, 255, 0])
    b[8], b[9], b[10], b[11] = 0x4f, 0x2e, 0x34, 0x12                   # manufacturer 'SYN', product 0x1234
    b[0x12], b[0x13], b[0x14], b[0x18], b[0x7e] = 1, revision, 0xa5, 0x02 if declared else 0, extensions
    b[0x26:0x36] = b'\x01\x01' * 8                                       # standard timings: all unused
    for i, off in enumerate((0x36, 0x48, 0x5a, 0x6c)):
        if i < len(dtds):
            b[off:off + 18] = dtds[i]
        else:
            b[off + 3] = 0xfc if i == len(dtds) else 0x10
            b[off + 5:off + 11] = b'SYNTH\n' if i == len(dtds) else bytes(6)
    if mutate:
        mutate(b)
    return seal(b)


def cta_block(dtds=(), blocks=b'', revision=3, native=None, mutate=None):
    b = bytearray(128)
    b[0], b[1] = 2, revision
    offset = 4 + len(blocks)
    b[2], b[3] = offset if (dtds or blocks) else 0, len(dtds) if native is None else native
    b[4:4 + len(blocks)] = blocks
    for i, dtd in enumerate(dtds):
        b[offset + 18 * i:offset + 18 * (i + 1)] = dtd
    if mutate:
        mutate(b)
    return seal(b)


def did_timing(unit_khz, clock_khz, ha, hb, hf, hs, va, vb, vf, vs, flags=0x84, hpol=0, vpol=0):
    """A 20-byte DisplayID Type I (unit 10) or Type VII (unit 1) timing: every count is stored minus one."""
    clock = clock_khz // unit_khz - 1
    return bytes([clock & 255, clock >> 8 & 255, clock >> 16 & 255, flags]) + struct.pack(
        '<8H', ha - 1, hb - 1, hf - 1 | hpol << 15, hs - 1, va - 1, vb - 1, vf - 1 | vpol << 15, vs - 1)


# 2880x1920 at 120 Hz with modest blanking: 120 x 2960 x 1950 = 692.64 MHz, beyond an 18-byte descriptor.
T120 = dict(ha=2880, hb=80, hf=24, hs=16, va=1920, vb=30, vf=8, vs=4)


def did_block(tag, rev, payload):
    return bytes([tag, rev, len(payload)]) + payload


def did_ext(blocks, version=0x13, primary=2, size=None, checksum=True, tail=b'', mutate=None):
    body = b''.join(blocks)
    section = bytes([version, len(body) if size is None else size, primary, 0]) + body
    section += bytes([(-sum(section)) & 0xff if checksum else 0x55])
    block = bytearray(128)
    block[0] = 0x70
    block[1:1 + len(section)] = section
    block[1 + len(section):1 + len(section) + len(tail)] = tail
    if mutate:
        mutate(block)
    return seal(block)


def edid(*extensions, dtds=None, declared=True, **kw):
    return base_block([encode_dtd(**SYN60)] if dtds is None else dtds, declared, extensions=len(extensions), **kw) \
        + b''.join(extensions)


def type7_vtdb(timing, rev=2):
    return bytes([7 << 5 | 1 + 1 + len(timing), 0x22, rev]) + timing


def gop(width=PANEL[0], height=PANEL[1]):
    return json.dumps(dict(width=width, height=height, stride_pixels=width, format='BGRX'))


# ---- The link budget ----------------------------------------------------------------------------------------------
# VESA DMT reduced-blanking entries from Linux drm_edid.c (60 and 120 Hz): (h, v, hz, pixel clock kHz, htotal, vtotal).
DMT_RB = ((1440, 900, 60, 88750, 1600, 926), (1440, 900, 120, 182750, 1600, 953),
          (1680, 1050, 60, 119000, 1840, 1080), (1680, 1050, 120, 245500, 1840, 1112),
          (1920, 1200, 60, 154000, 2080, 1235), (1920, 1200, 120, 317000, 2080, 1271),
          (2560, 1600, 60, 268500, 2720, 1646), (2560, 1600, 120, 552750, 2720, 1694))
RATE_KBPS = dict(RBR=1_620_000, HBR=2_700_000, HBR2=5_400_000, HBR3=8_100_000)


def refused(case, code, fn, *args, **kw):
    with case.assertRaises((lb.BudgetError, ee.ExtError, dm.ModeError)) as raised:
        fn(*args, **kw)
    case.assertEqual(raised.exception.code, code, raised.exception)
    return raised.exception


class LinkBudget(unittest.TestCase):
    def test_reduced_blanking_matches_the_dmt_entries_at_60_and_120_hz(self):
        for h, v, hz, clock, htotal, vtotal in DMT_RB:
            t = lb.cvt_rb_timing(h, v, hz)
            self.assertEqual((t.pixel_clock_khz, t.htotal, t.vtotal), (clock, htotal, vtotal), (h, v, hz))
            self.assertEqual(t.assumption, 'cvt-rb-v1')

    def test_the_panel_sized_timings_under_the_reduced_blanking_assumption(self):
        t60, t120 = lb.cvt_rb_timing(*PANEL, 60), lb.cvt_rb_timing(*PANEL, 120)
        self.assertEqual((t60.htotal, t60.vtotal, t60.pixel_clock_khz), (3040, 1975, 360000))
        self.assertEqual((t120.htotal, t120.vtotal, t120.pixel_clock_khz), (3040, 2033, 741500))

    def test_payload_capacity_is_eight_tenths_of_the_line_rate(self):
        t = lb.explicit_timing(*PANEL, 80, 30, refresh_hz=60)
        for name, kbps in RATE_KBPS.items():
            for lanes in (1, 2, 4):
                r = lb.link_budget(t, 8, lanes, name)
                self.assertEqual(r['capacity_kbps'], kbps * lanes * 8 // 10, (name, lanes))

    def test_required_rate_is_pixel_clock_times_bits_per_pixel(self):
        t = lb.explicit_timing(*PANEL, 80, 30, refresh_hz=120)
        self.assertEqual(t.pixel_clock_khz, 692640)
        for bpc in (6, 8, 10):
            self.assertEqual(lb.link_budget(t, bpc, 4, 'HBR3')['required_kbps'], 692640 * 3 * bpc)

    def test_what_fits_at_60_and_120_hz(self):
        """Expected outcomes derived here with exact integers, not read back from the module."""
        cases = {}
        for hz, clock in ((60, 360000), (120, 741500)):
            for bpc in (8, 10):
                for rate, lanes in (('HBR3', 4), ('HBR2', 4), ('HBR3', 2), ('HBR', 4), ('HBR2', 2), ('HBR3', 1)):
                    cases[(hz, bpc, rate, lanes)] = clock * 3 * bpc <= RATE_KBPS[rate] * lanes * 8 // 10
        for (hz, bpc, rate, lanes), expect in cases.items():
            t = lb.cvt_rb_timing(*PANEL, hz)
            self.assertEqual(lb.link_budget(t, bpc, lanes, rate)['fits'], expect, (hz, bpc, rate, lanes))
        self.assertTrue(cases[(120, 8, 'HBR3', 4)] and cases[(120, 10, 'HBR3', 4)])
        self.assertFalse(cases[(120, 8, 'HBR2', 4)] or cases[(120, 8, 'HBR3', 2)])         # these need DSC or less blanking
        self.assertTrue(cases[(60, 8, 'HBR2', 2)] and cases[(60, 8, 'HBR', 4)])

    def test_a_stream_that_exactly_fills_the_link_fits_with_no_margin(self):
        r = lb.link_budget(lb.cvt_rb_timing(*PANEL, 60), 8, 4, 'HBR')
        self.assertEqual((r['fits'], r['margin_kbps'], r['margin_percent']), (True, 0, 0.0))

    def test_a_stream_one_kbps_over_does_not_fit(self):
        t = lb.timing_from_totals(*PANEL, 3040, 1975, 360001)
        r = lb.link_budget(t, 8, 4, 'HBR')
        self.assertEqual((r['fits'], r['margin_kbps'] < 0, r['margin_percent'] <= 0), (False, True, True))

    def test_dsc_makes_a_case_fit_that_does_not_fit_without_it(self):
        t = lb.cvt_rb_timing(*PANEL, 120)
        self.assertFalse(lb.link_budget(t, 8, 2, 'HBR3')['fits'])
        r = lb.link_budget(t, 8, 2, 'HBR3', dsc_target_bpp=8)
        self.assertEqual((r['fits'], r['dsc'], r['bits_per_pixel'], r['required_kbps']), (True, True, 8.0, 741500 * 8))
        self.assertFalse(lb.link_budget(t, 8, 2, 'HBR3', dsc_target_bpp='17.5')['fits'])

    def test_the_required_rate_is_rounded_up_and_fractional_targets_are_exact(self):
        t = lb.cvt_rb_timing(*PANEL, 120)
        r = lb.link_budget(t, 8, 4, 'HBR3', dsc_target_bpp='8.0625')
        self.assertEqual(r['required_kbps'], -(-741500 * 129 // 16))                       # ceil, never under-stated
        self.assertEqual(lb.explicit_timing(*PANEL, 80, 30, refresh_hz='119.88').pixel_clock_khz,
                         -(-Fraction('119.88') * 2960 * 1950 // 1000))

    def test_the_highest_target_that_fits_is_reported(self):
        r = lb.link_budget(lb.cvt_rb_timing(*PANEL, 120), 8, 2, 'HBR3')
        self.assertEqual(r['max_bits_per_pixel_that_fits'], (12_960_000 * 16 // 741500) / 16)

    def test_matrix_rows_agree_with_the_single_answers(self):
        t = lb.cvt_rb_timing(*PANEL, 120)
        rows = lb.feasibility_matrix(t)
        self.assertEqual(len(rows), 4 * 3 * 3)
        for row in rows:
            single = lb.link_budget(t, row['bits_per_component'], row['lanes'], row['rate'])
            self.assertEqual((row['fits'], row['capacity_kbps']), (single['fits'], single['capacity_kbps']))
            self.assertEqual(row['needs_dsc'], not row['fits'] and not row['impossible'])
        slowest = next(r for r in rows if (r['rate'], r['lanes'], r['bits_per_component']) == ('RBR', 1, 10))
        self.assertEqual((slowest['fits'], slowest['impossible']), (False, True))

    def test_a_timing_is_checked_even_when_built_by_hand(self):
        refused(self, 'budget-range', lb.link_budget, lb.StreamTiming(0, 1, 1, 1, 1), 8, 4, 'HBR3')
        refused(self, 'budget-range', lb.link_budget, lb.StreamTiming(10, 10, 12, 12, 0), 8, 4, 'HBR3')
        refused(self, 'budget-blanking', lb.link_budget, lb.StreamTiming(10, 10, 5, 12, 1000), 8, 4, 'HBR3')
        refused(self, 'budget-input', lb.link_budget, 'not a timing', 8, 4, 'HBR3')

    def test_faulty_link_parameters_are_refused(self):
        t = lb.cvt_rb_timing(*PANEL, 60)
        for lanes in (0, 3, 8, -1, 4.0, True, None, '0', 'four'):
            with self.subTest(lanes=lanes):
                with self.assertRaises(lb.BudgetError):
                    lb.link_budget(t, 8, lanes, 'HBR3')
        for bpc in (0, 7, 12, 16, -8, 8.0, True, None):
            with self.subTest(bpc=bpc):
                with self.assertRaises(lb.BudgetError):
                    lb.link_budget(t, bpc, 4, 'HBR3')
        for rate in ('HBR4', '', 'UHBR10', 'uhbr20', 'EDP-9.99', 8.1, None, 'HBR3X'):
            with self.subTest(rate=rate):
                with self.assertRaises(lb.BudgetError):
                    lb.link_budget(t, 8, 4, rate)
        self.assertEqual(refused(self, 'budget-rate', lb.line_rate_kbps, 'UHBR10').code, 'budget-rate')

    def test_faulty_timings_are_refused(self):
        refused(self, 'budget-blanking', lb.explicit_timing, *PANEL, -1, 30, refresh_hz=60)
        refused(self, 'budget-blanking', lb.explicit_timing, *PANEL, 80, -30, refresh_hz=60)
        refused(self, 'budget-blanking', lb.timing_from_totals, *PANEL, 2000, 1975, 360000)
        refused(self, 'budget-blanking', lb.timing_from_totals, *PANEL, 3040, 1000, 360000)
        for args in ((0, 1920, 80, 30), (2880, 0, 80, 30)):
            with self.assertRaises(lb.BudgetError):
                lb.explicit_timing(*args, refresh_hz=60)
        refused(self, 'budget-range', lb.explicit_timing, *PANEL, 80, 30, refresh_hz=0)
        refused(self, 'budget-range', lb.explicit_timing, *PANEL, 80, 30, refresh_hz=1001)
        refused(self, 'budget-range', lb.explicit_timing, *PANEL, 80, 30, pixel_clock_khz=0)
        refused(self, 'budget-input', lb.explicit_timing, *PANEL, 80, 30)
        refused(self, 'budget-input', lb.explicit_timing, *PANEL, 80, 30, refresh_hz=60, pixel_clock_khz=360000)
        for hz in ('fast', float('nan'), float('inf'), True, None, '-1', '1e3'):
            with self.assertRaises(lb.BudgetError):
                lb.cvt_rb_timing(*PANEL, hz)

    def test_arithmetic_that_could_overflow_is_refused(self):
        refused(self, 'budget-range', lb.timing_from_totals, *PANEL, 3040, 1975, (1 << 24) + 1)
        refused(self, 'budget-range', lb.timing_from_totals, 70000, 1920, 70001, 1975, 360000)
        refused(self, 'budget-range', lb.timing_from_totals, *PANEL, 1 << 17 | 1, 1975, 360000)
        refused(self, 'budget-range', lb.explicit_timing, 65536, 65536, 65536, 65536, refresh_hz=1000)
        refused(self, 'budget-range', lb.cvt_rb_timing, 100000, 1920, 60)
        self.assertEqual(lb.timing_from_totals(*PANEL, 3040, 1975, 1 << 24).pixel_clock_khz, 1 << 24)

    def test_dsc_targets_are_validated(self):
        t = lb.cvt_rb_timing(*PANEL, 120)
        for bpp in (0, 5, '5.9375', 24, 25, '8.03', '7.99', 'x', True, -8):
            with self.subTest(bpp=bpp):
                with self.assertRaises(lb.BudgetError):
                    lb.link_budget(t, 8, 4, 'HBR3', dsc_target_bpp=bpp)
        lb.link_budget(t, 8, 4, 'HBR3', dsc_target_bpp='23.9375')
        lb.link_budget(t, 6, 4, 'HBR3', dsc_target_bpp=6)
        with self.assertRaises(lb.BudgetError):
            lb.link_budget(t, 6, 4, 'HBR3', dsc_target_bpp=18)

    def test_results_are_labelled_deterministic_and_serialisable(self):
        t = lb.cvt_rb_timing(*PANEL, 120)
        a = lb.link_budget(t, 8, 4, 'HBR3')
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(lb.link_budget(t, 8, 4, 'HBR3'), sort_keys=True))
        self.assertTrue(a['label'].startswith('MODEL'))
        self.assertEqual(a['timing']['blanking'], 'cvt-rb-v1')
        self.assertEqual(a['timing']['refresh_hz'], round(741_500_000 / (3040 * 2033), 4))     # just under 120: the 250 kHz step
        self.assertEqual(lb.explicit_timing(*PANEL, 80, 30, refresh_hz=120).refresh_hz, 120)
        self.assertEqual(lb.explicit_timing(*PANEL, 80, 30, refresh_hz=60).assumption, 'explicit')

    def test_the_edp_intermediate_rates_are_selected_by_name_only(self):
        self.assertEqual(lb.line_rate_kbps('edp-2.43'), 2_430_000)
        t = lb.explicit_timing(*PANEL, 80, 30, refresh_hz=60)
        self.assertEqual(lb.link_budget(t, 8, 4, 'EDP-2.43')['capacity_kbps'], 2_430_000 * 4 * 8 // 10)

    def test_no_native_120_hz_timing_fits_an_18_byte_descriptor(self):
        """The study's claim: even with no blanking the active pixels alone exceed what a base-block descriptor holds."""
        active_only_khz = PANEL[0] * PANEL[1] * 120 // 1000
        self.assertEqual((active_only_khz, ee.BASE_BLOCK_PIXEL_CLOCK_LIMIT_KHZ), (663552, 655350))
        self.assertGreater(active_only_khz, ee.BASE_BLOCK_PIXEL_CLOCK_LIMIT_KHZ)
        self.assertLessEqual(PANEL[0] * PANEL[1] * 60 // 1000, ee.BASE_BLOCK_PIXEL_CLOCK_LIMIT_KHZ)


# ---- CTA video identification codes -------------------------------------------------------------------------------
class VicTable(unittest.TestCase):
    def rate(self, vic):
        hactive, vactive, il, clock, htotal, vtotal = vics.vic_timing(vic)
        return Fraction(clock * 1000 * (2 if il else 1), htotal * vtotal)

    def test_known_public_timings(self):
        self.assertEqual(vics.vic_timing(16), (1920, 1080, 0, 148500, 2200, 1125))
        self.assertEqual(vics.vic_timing(4), (1280, 720, 0, 74250, 1650, 750))
        self.assertEqual(vics.vic_timing(63), (1920, 1080, 0, 297000, 2200, 1125))
        self.assertEqual(vics.vic_timing(97), (3840, 2160, 0, 594000, 4400, 2250))
        self.assertEqual(vics.vic_timing(118), (3840, 2160, 0, 1188000, 4400, 2250))
        self.assertEqual((self.rate(16), self.rate(63), self.rate(118)), (60, 120, 120))
        self.assertAlmostEqual(float(self.rate(1)), 59.94, places=2)
        self.assertEqual(self.rate(5), 60)                          # 1080i: the field rate, twice the frame rate

    def test_the_table_is_complete_and_every_rate_is_a_standard_one(self):
        self.assertEqual([r[0] for r in vics.VIC_TABLE], list(range(1, 128)) + list(range(193, 220)))
        standard = [24, 25, 30, 48, 50, 60, 100, 120, 200, 240]
        for row in vics.VIC_TABLE:
            rate = self.rate(row[0])
            near = min(abs(rate - s) / s for s in standard + [Fraction(s * 1000, 1001) for s in (24, 30, 48, 60, 120, 240)])
            self.assertLess(near, Fraction(1, 200), row)
        for reserved in (0, 128, 220, 254, 255, 1000):
            self.assertIsNone(vics.vic_timing(reserved))

    @unittest.skipUnless(os.environ.get('SP11_LINUX_TREE'), 'SP11_LINUX_TREE is not set: the table is not compared with Linux')
    def test_every_row_equals_the_pinned_linux_table(self):
        text = (Path(os.environ['SP11_LINUX_TREE']) / 'drivers/gpu/drm/drm_edid.c').read_text()
        rows = {}
        for name in ('edid_cea_modes_1[]', 'edid_cea_modes_193[]'):
            start = text.index('static const struct drm_display_mode ' + name)
            for m in re.finditer(r'/\*\s*(\d+)\s*-[^*]*\*/\s*\{\s*DRM_MODE\("[^"]*",\s*DRM_MODE_TYPE_DRIVER,\s*(\d+),\s*(\d+),'
                                 r'\s*\d+,\s*\d+,\s*(\d+),\s*\d+,\s*(\d+),\s*\d+,\s*\d+,\s*(\d+),\s*\d+,\s*([^)]*)\)',
                                 text[start:text.index('\n};', start)]):
                vic, clock, hd, ht, vd, vt, flags = m.groups()
                rows[int(vic)] = (int(vic), int(hd), int(vd), 1 if 'INTERLACE' in flags else 0, int(clock), int(ht), int(vt))
        self.assertEqual(rows, {r[0]: r for r in vics.VIC_TABLE})


# ---- CTA-861 extension --------------------------------------------------------------------------------------------
class Cta(unittest.TestCase):
    def modes(self, block):
        return dm.parse_edid(edid(block)).modes

    def test_video_data_block_modes_carry_the_vic_and_the_native_flag(self):
        e = dm.parse_edid(edid(cta_block(blocks=bytes([0x40 | 3, 0x90, 63, 118]))))
        vic_modes = [m for m in e.modes if m.source == 'cta-vic']
        self.assertEqual([(m.vic, m.native, m.hactive, m.vactive) for m in vic_modes],
                         [(16, True, 1920, 1080), (63, False, 1920, 1080), (118, False, 3840, 2160)])
        self.assertEqual([m.refresh_hz for m in vic_modes], [60, 120, 120])
        self.assertEqual([m.exceeds_base_block_limit for m in vic_modes], [False, False, True])
        self.assertEqual(e.partial, ())

    def test_an_unknown_vic_is_reported_and_makes_the_list_partial(self):
        e = dm.parse_edid(edid(cta_block(blocks=bytes([0x40 | 2, 16, 230]))))
        self.assertEqual((e.unknown_vics, [m.vic for m in e.modes if m.vic]), ((230,), [16]))
        self.assertTrue(any('230' in p for p in e.partial))

    def test_reserved_and_empty_video_data_blocks_are_refused(self):
        for byte in (0, 128, 254, 255):
            refused(self, 'edid-cta', dm.parse_edid, edid(cta_block(blocks=bytes([0x40 | 1, byte]))))
        refused(self, 'edid-cta', dm.parse_edid, edid(cta_block(blocks=bytes([0x40]))))

    def test_extra_structure_checks(self):
        junk = lambda b: b.__setitem__(120, 0x55)
        refused(self, 'edid-cta', dm.parse_edid, edid(cta_block([encode_dtd(**SYN60)], mutate=junk)))
        refused(self, 'edid-cta', dm.parse_edid, edid(cta_block([encode_dtd(**SYN60)], native=2)))
        refused(self, 'edid-cta', dm.parse_edid, edid(cta_block(blocks=bytes([0xe0]))))             # extended block, no tag
        dm.parse_edid(edid(cta_block([encode_dtd(**SYN60)], native=1)))
        dm.parse_edid(edid(cta_block([encode_dtd(**SYN60)], native=0)))

    def test_data_blocks_are_not_decoded_before_revision_three(self):
        e = dm.parse_edid(edid(cta_block([encode_dtd(**SYN60)], revision=2)))
        self.assertEqual([m.source for m in e.modes], ['base-dtd', 'cta-dtd'])

    def test_a_type_vii_video_timing_data_block_carries_a_mode_past_the_base_block_limit(self):
        timing = did_timing(1, 692640, **T120)
        e = dm.parse_edid(edid(cta_block(blocks=type7_vtdb(timing))))
        (mode,) = [m for m in e.modes if m.source == 'cta-vtdb']
        self.assertEqual((mode.hactive, mode.vactive, mode.pixel_clock_khz, mode.refresh_hz), (2880, 1920, 692640, 120))
        self.assertTrue(mode.exceeds_base_block_limit)
        refused(self, 'edid-cta', dm.parse_edid, edid(cta_block(blocks=type7_vtdb(timing[:19]))))
        bad = bytearray(timing)
        bad[3] |= 0x0f                                                                       # a reserved aspect ratio
        refused(self, 'edid-cta', dm.parse_edid, edid(cta_block(blocks=type7_vtdb(bytes(bad)))))

    def test_layouts_that_are_not_decoded_are_reported_not_guessed(self):
        timing = did_timing(1, 692640, **T120)
        for block in (type7_vtdb(timing, rev=3), type7_vtdb(timing + b'\x00', rev=2),
                      bytes([7 << 5 | 4, 0x23, 0, 0, 0]), bytes([7 << 5 | 4, 0x2a, 0, 0, 0])):
            e = dm.parse_edid(edid(cta_block(blocks=block)))
            self.assertEqual([m.source for m in e.modes if m.source == 'cta-vtdb'], [], block)
            self.assertTrue(e.partial, block)


# ---- DisplayID ----------------------------------------------------------------------------------------------------
class DisplayId(unittest.TestCase):
    def decode(self, block):
        return dm.parse_edid(edid(block))

    def test_a_type_i_block_carries_the_120_hz_mode(self):
        e = self.decode(did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120))]))
        (mode,) = [m for m in e.modes if m.source == 'displayid1-type1']
        self.assertEqual((mode.hactive, mode.vactive, mode.pixel_clock_khz, mode.htotal, mode.vtotal),
                         (2880, 1920, 692640, 2960, 1950))
        self.assertEqual((mode.refresh_hz, mode.preferred, mode.interlaced), (120, True, False))
        self.assertTrue(mode.exceeds_base_block_limit)
        self.assertEqual(e.extensions, ((0x70, True),))
        self.assertEqual(e.partial, ())

    def test_a_type_vii_block_uses_kilohertz_units(self):
        e = self.decode(did_ext([did_block(0x22, 1, did_timing(1, 692641, flags=0x04, **T120))], version=0x20))
        (mode,) = [m for m in e.modes if m.source == 'displayid2-type7']
        self.assertEqual((mode.pixel_clock_khz, mode.preferred), (692641, False))
        self.assertAlmostEqual(float(mode.refresh_hz), 120.0, places=2)

    def test_several_timings_and_the_60_hz_one_are_all_listed(self):
        t60 = did_timing(10, 346320, **T120, flags=0x04)
        e = self.decode(did_ext([did_block(0x03, 1, t60 + did_timing(10, 692640, **T120))]))
        self.assertEqual([(m.source, m.refresh_hz) for m in e.modes[1:]], [('displayid1-type1', 60), ('displayid1-type1', 120)])
        self.assertEqual([m.exceeds_base_block_limit for m in e.modes[1:]], [False, True])

    def test_polarity_interlace_and_preferred_flags(self):
        flags = 0x04 | 0x10 | 0x80
        e = self.decode(did_ext([did_block(0x03, 0, did_timing(10, 74250, 1920, 280, 88, 44, 1080, 45, 4, 5, flags=flags,
                                                               hpol=1, vpol=1))]))
        mode = e.modes[-1]
        self.assertEqual((mode.interlaced, mode.preferred), (True, True))
        self.assertEqual(mode.refresh_hz, 60)                                               # the field rate

    def test_range_limits_say_whether_the_refresh_rate_is_variable(self):
        v1 = (bytes([0xe7, 0x03, 0x00]) + bytes([0x7f, 0x38, 0x01]) + bytes([30, 200]) + bytes([80, 0]) + bytes([48, 120])
              + bytes([30, 0]) + bytes([0x40]))                       # 10..800 MHz, 30..200 kHz, 48..120 Hz
        (r,) = self.decode(did_ext([did_block(0x09, 0, v1)])).ranges
        self.assertEqual((r.source, r.min_refresh_hz, r.max_refresh_hz, r.max_pixel_clock_khz), ('displayid1-range', 48, 120, 800000))
        self.assertTrue(r.as_dict()['variable'])
        v2 = bytes([0x00, 0x01, 0x00]) + bytes([0x40, 0x9a, 0x0a]) + bytes([48, 120]) + bytes([0x80])
        (r,) = self.decode(did_ext([did_block(0x25, 0, v2)], version=0x20)).ranges
        self.assertEqual((r.source, r.min_refresh_hz, r.max_refresh_hz, r.seamless), ('displayid2-dynamic', 48, 120, True))
        self.assertEqual(r.max_pixel_clock_khz, 0x0a9a40 + 1)

    def test_a_revision_one_dynamic_range_holds_rates_above_255_hz(self):
        v2 = bytes([0, 0, 0]) + bytes([0x40, 0x9a, 0x0a]) + bytes([48]) + bytes([0x68, 0x01])         # 0x168 = 360 Hz
        (r,) = self.decode(did_ext([did_block(0x25, 1, v2)], version=0x21)).ranges
        self.assertEqual((r.max_refresh_hz, r.seamless), (360, False))
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x25, 0, v2)], version=0x21)))

    def test_a_fixed_pair_of_refresh_rates_is_not_a_range(self):
        e = self.decode(did_ext([did_block(0x03, 0, did_timing(10, 346320, **T120, flags=0x04)
                                           + did_timing(10, 692640, **T120))]))
        self.assertEqual(e.ranges, ())

    def test_an_unread_displayid_version_is_reported_not_decoded(self):
        block = seal(bytes([0x70, 1, 0x4d] + [0] * 125))
        e = dm.parse_edid(edid(block))
        self.assertEqual(e.extensions, ((0x70, False),))
        self.assertTrue(any('0x01' in p for p in e.partial))

    def test_structural_faults_are_refused(self):
        good = did_block(0x03, 0, did_timing(10, 692640, **T120))
        faults = {
            'checksum': dict(blocks=[good], checksum=False),
            'section past the block': dict(blocks=[good], size=122),
            'bytes after the section': dict(blocks=[good], tail=b'\x01'),
            'block overruns the section': dict(blocks=[bytes([0x03, 0, 40]) + good[3:]], ),
            'cut-off block header': dict(blocks=[good, b'\x03\x00']),
            'empty block': dict(blocks=[good, bytes([0, 0, 0])]),
            'not a whole number of timings': dict(blocks=[did_block(0x03, 0, did_timing(10, 692640, **T120) + b'\x00')]),
            'no timings': dict(blocks=[did_block(0x03, 0, b'')]),
            'reserved revision bits': dict(blocks=[did_block(0x03, 0x40, did_timing(10, 692640, **T120))]),
            'type i revision 2': dict(blocks=[did_block(0x03, 2, did_timing(10, 692640, **T120))]),
        }
        for name, kw in faults.items():
            with self.subTest(name):
                blocks = kw.pop('blocks')
                refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext(blocks, **kw)))

    def test_the_truncation_is_what_is_refused(self):
        error = refused(self, 'edid-displayid', dm.parse_edid,
                        edid(did_ext([bytes([0x03, 0, 40]) + did_timing(10, 692640, **T120)])))
        self.assertIn('runs past the section', error.message)
        error = refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120)),
                                                                              b'\x03\x00'])))
        self.assertIn('cut off', error.message)

    def test_timing_faults_are_refused(self):
        def one(**kw):
            return edid(did_ext([did_block(0x03, 0, did_timing(10, **{**dict(clock_khz=692640, **T120), **kw}))]))
        refused(self, 'edid-displayid', dm.parse_edid, one(hf=40, hs=60))                       # sync past the blanking
        refused(self, 'edid-displayid', dm.parse_edid, one(vf=20, vs=20))
        refused(self, 'edid-displayid', dm.parse_edid, one(clock_khz=1000))                     # a refresh far below 20 Hz
        refused(self, 'edid-displayid', dm.parse_edid, one(flags=0x8f))                         # a reserved aspect code
        refused(self, 'edid-displayid', dm.parse_edid, one(flags=0xe4))                         # the reserved stereo code
        dm.parse_edid(one())

    def test_generations_are_not_mixed(self):
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x22, 0, did_timing(1, 692641, **T120))])))
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120))],
                                                                    version=0x20)))
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x09, 0, bytes(15))], version=0x21)))

    def test_type_vii_revisions_and_extended_descriptors(self):
        refused(self, 'edid-displayid', dm.parse_edid,
                edid(did_ext([did_block(0x22, 3, did_timing(1, 692641, **T120))], version=0x20)))
        refused(self, 'edid-displayid', dm.parse_edid,
                edid(did_ext([did_block(0x22, 0x80, did_timing(1, 692641, **T120))], version=0x20)))
        e = dm.parse_edid(edid(did_ext([did_block(0x22, 0x10, did_timing(1, 692641, **T120) + b'\x00')], version=0x20)))
        self.assertEqual([m.source for m in e.modes], ['base-dtd'])
        self.assertTrue(e.partial)

    def test_range_limit_faults_are_refused(self):
        v1 = lambda lo, hi: bytes([0, 1, 0, 0x40, 0x0d, 0x03, 30, 200, 80, 0, lo, hi, 30, 0, 0])
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x09, 0, v1(120, 48))])))
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x09, 0, v1(0, 120))])))
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x09, 0, v1(48, 120)[:14])])))
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x09, 0, bytes([0, 0, 1, 0, 0, 0]) + v1(48, 120)[6:])])))
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x25, 0, bytes([0, 0, 0, 0, 0, 0, 120, 48, 0]))],
                                                                    version=0x20)))
        refused(self, 'edid-displayid', dm.parse_edid, edid(did_ext([did_block(0x25, 0, bytes(8))], version=0x20)))


# ---- Base-block descriptors ---------------------------------------------------------------------------------------
def range_descriptor(vmin, vmax, hmin=30, hmax=200, clock10mhz=75, flags=0, kind=0x01):
    return bytes([0, 0, 0, 0xfd, flags, vmin, vmax, hmin, hmax, clock10mhz, kind, 0x0a, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20])


def with_range(descriptor, revision=4):
    def put(b):
        b[0x6c:0x7e] = descriptor
    return base_block([encode_dtd(**SYN60)], revision=revision, mutate=put)


class BaseBlock(unittest.TestCase):
    def test_the_display_range_limits_descriptor_is_decoded(self):
        e = dm.parse_edid(with_range(range_descriptor(48, 120)))
        (r,) = e.ranges
        self.assertEqual((r.source, r.min_refresh_hz, r.max_refresh_hz, r.max_pixel_clock_khz), ('base-0xfd', 48, 120, 750000))
        self.assertTrue(r.as_dict()['variable'])

    def test_offset_flags_extend_the_rates_in_edid_14(self):
        e = dm.parse_edid(with_range(range_descriptor(10, 20, flags=0x03)))
        self.assertEqual((e.ranges[0].min_refresh_hz, e.ranges[0].max_refresh_hz), (265, 275))
        e = dm.parse_edid(with_range(range_descriptor(10, 10, flags=0x02)))
        self.assertEqual((e.ranges[0].min_refresh_hz, e.ranges[0].max_refresh_hz), (10, 265))

    def test_range_limit_faults_are_refused(self):
        refused(self, 'edid-range-limits', dm.parse_edid, with_range(range_descriptor(120, 48)))
        refused(self, 'edid-range-limits', dm.parse_edid, with_range(range_descriptor(0, 120)))
        refused(self, 'edid-range-limits', dm.parse_edid, with_range(range_descriptor(48, 120, flags=0x01)))
        refused(self, 'edid-range-limits', dm.parse_edid, with_range(range_descriptor(48, 120, flags=0x40)))
        refused(self, 'edid-range-limits', dm.parse_edid, with_range(range_descriptor(48, 120, flags=0x02), revision=3))
        refused(self, 'edid-range-limits', dm.parse_edid, with_range(range_descriptor(48, 120, hmin=200, hmax=30)))

    def test_established_and_standard_timings_make_the_list_partial(self):
        def put_established(b):
            b[0x23] = 0x20
        def put_standard(b):
            b[0x26:0x28] = bytes([0xd1, 0xc0])
        for put in (put_established, put_standard):
            e = dm.parse_edid(base_block([encode_dtd(**SYN60)], mutate=put))
            self.assertEqual(len(e.partial), 1)
        self.assertEqual(dm.parse_edid(base_block([encode_dtd(**SYN60)])).partial, ())

    def test_an_unknown_extension_type_is_partial_not_an_error(self):
        e = dm.parse_edid(edid(seal(bytes([0x10] + [0] * 127))))
        self.assertEqual((e.extensions, len(e.partial)), (((0x10, False),), 1))

    def test_base_dtd_preferred_flag_follows_the_declaration(self):
        self.assertEqual([m.preferred for m in dm.parse_edid(edid()).modes], [True])
        self.assertEqual([m.preferred for m in dm.parse_edid(edid(declared=False)).modes], [False])


# ---- Through the decision ------------------------------------------------------------------------------------------
class Decision(unittest.TestCase):
    def two_rates(self):
        return edid(did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120))]))

    def test_the_decision_lists_the_modes_and_marks_the_ones_past_the_base_block_limit(self):
        r = dm.evaluate(gop(), self.two_rates())
        self.assertEqual((r['verdict'], r['reasons']), ('ACCEPT', []))
        modes = r['edid']['modes']
        self.assertEqual([(m['source'], m['refresh_hz'], m['exceeds_base_block_limit']) for m in modes],
                         [('base-dtd', 60.0, False), ('displayid1-type1', 120.0, True)])
        self.assertEqual((r['edid']['modes_complete'], r['edid']['partial'], r['edid']['extension_tags']), (True, [], [0x70]))
        self.assertEqual(r['edid']['timings'], [[2880, 1920, 60.0]])                         # the old key is unchanged

    def test_a_partial_decode_is_visible_in_the_decision(self):
        r = dm.evaluate(gop(), edid(seal(bytes([0x10] + [0] * 127))))
        self.assertEqual((r['verdict'], r['edid']['modes_complete']), ('ACCEPT', False))
        self.assertTrue(r['edid']['partial'])

    def lenient_cases(self):
        """One extension fault per reason code. Each is refused by `modes` and only reported by the GOP confirmation."""
        bad_displayid = did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120))], checksum=False)
        return (('edid-displayid', edid(bad_displayid)),
                ('edid-range-limits', with_range(range_descriptor(120, 48))),
                ('edid-cta', edid(cta_block(blocks=bytes([0x40 | 1, 0])))))        # a reserved video code

    def test_the_gop_flow_reports_a_malformed_extension_and_still_accepts(self):
        for code, data in self.lenient_cases():
            with self.subTest(code):
                refused(self, code, dm.parse_edid, data)                              # `modes` and direct callers: strict
                r = dm.evaluate(gop(), data)
                self.assertEqual((r['verdict'], r['reasons'], r['messages']), ('ACCEPT', [], []))
                self.assertEqual((r['mode']['width'], r['mode']['height']), PANEL)
                self.assertEqual([m['source'] for m in r['edid']['modes']], ['base-dtd'])
                self.assertFalse(r['edid']['modes_complete'])
                self.assertEqual([p.split(':')[0] for p in r['edid']['partial']], [code])      # visible, with its reason code

    def test_a_refused_extension_is_never_reported_as_decoded(self):
        data = edid(did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120))], checksum=False))
        self.assertEqual(dm.parse_edid(data, lenient_extensions=True).extensions, ((0x70, False),))

    def test_a_base_block_fault_or_a_cta_structure_fault_still_rejects_the_gop_flow(self):
        bad = bytearray(edid(did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120))])))
        bad[0x40] ^= 1                                                                # the base block checksum
        self.assertEqual(dm.evaluate(gop(), bytes(bad))['reasons'], ['edid-checksum'])
        offset = edid(cta_block(blocks=bytes([0x40 | 1, 16]), mutate=lambda b: b.__setitem__(2, 3)))   # an invalid DTD offset
        r = dm.evaluate(gop(), offset)
        self.assertEqual((r['verdict'], r['reasons']), ('REJECT', ['edid-cta']))
        self.assertEqual(dm.evaluate(gop(width=PANEL[0] + 8), self.two_rates())['reasons'], ['mode-mismatch'])

    def test_only_an_advertised_timing_is_found(self):
        modes = dm.parse_edid(self.two_rates()).modes
        found = ee.find_advertised(modes, 2880, 1920, 120)
        self.assertEqual([m.source for m in found], ['displayid1-type1'])
        self.assertEqual(len(ee.find_advertised(modes, 2880, 1920, '60.4')), 1)
        self.assertEqual(ee.find_advertised(modes, 2880, 1920, 90), [])
        self.assertEqual(ee.find_advertised(modes, 1920, 2880, 120), [])                    # not interchangeable
        self.assertEqual(ee.find_advertised(modes, 2880, 1920, 119, tolerance_hz=Fraction(1, 2)), [])

    def test_an_interlaced_timing_is_never_returned_as_a_progressive_one(self):
        timing = did_timing(10, 692640, **T120, flags=0x14)
        modes = dm.parse_edid(edid(did_ext([did_block(0x03, 0, timing)]))).modes
        self.assertEqual([m.interlaced for m in modes], [False, True])
        self.assertEqual(ee.find_advertised(modes, 2880, 1920, 240), [])                    # its field rate is 240 Hz
        self.assertEqual(ee.find_advertised(modes, 2880, 1920, 120), [])

    def test_the_base_block_limit_is_exactly_655_35_mhz(self):
        edge = lambda khz: dm.parse_edid(edid(did_ext([did_block(0x03, 0, did_timing(10, khz, **T120))]))).modes[-1]
        self.assertEqual((edge(655350).exceeds_base_block_limit, edge(655360).exceeds_base_block_limit), (False, True))


# ---- The subcommands ----------------------------------------------------------------------------------------------
class CommandLine(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([sys.executable, '-B', str(MODULE_DIR / 'sp11_display_mode.py'), *args], capture_output=True,
                              text=True, timeout=60)

    def setUp(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        self.path = BUILD / 'two-rates.edid'
        self.path.write_bytes(edid(did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120))])))

    def test_modes_lists_what_the_edid_advertises(self):
        run = self.run_tool('modes', '--edid', str(self.path))
        out = json.loads(run.stdout)
        self.assertEqual((run.returncode, out['verdict'], out['modes_complete']), (0, 'OK', True))
        self.assertEqual([m['refresh_hz'] for m in out['modes']], [60.0, 120.0])

    def test_require_succeeds_only_for_an_advertised_timing(self):
        ok = self.run_tool('modes', '--edid', str(self.path), '--require', '2880x1920@120')
        self.assertEqual((ok.returncode, json.loads(ok.stdout)['require']['found'][0]['source']), (0, 'displayid1-type1'))
        no = self.run_tool('modes', '--edid', str(self.path), '--require', '2880x1920@144')
        self.assertEqual((no.returncode, json.loads(no.stdout)['verdict']), (1, 'NOT-ADVERTISED'))
        near = self.run_tool('modes', '--edid', str(self.path), '--require', '2880x1920@120.4', '--tolerance', '0.2')
        self.assertEqual(near.returncode, 1)

    def test_a_partial_decode_never_concludes_that_a_timing_is_absent(self):
        run = self.run_tool('modes', '--edid-hex', edid(seal(bytes([0x10] + [0] * 127))).hex(' ', 16),
                            '--require', '2880x1920@120')
        out = json.loads(run.stdout)
        self.assertEqual((run.returncode, out['verdict'], out['modes_complete']), (1, 'UNKNOWN', False))

    def test_an_empty_path_a_link_a_directory_and_a_missing_file_are_unusable(self):
        link = BUILD / 'two-rates.link'
        link.unlink(missing_ok=True)
        link.symlink_to(self.path)
        for target in ('', str(link), str(BUILD), str(BUILD / 'missing')):
            with self.subTest(path=target):
                run = self.run_tool('modes', '--edid', target)
                self.assertEqual((run.returncode, run.stdout), (2, ''), run.stderr)

    def test_a_pipe_is_refused_and_never_blocks(self):
        fifo = BUILD / 'two-rates.fifo'
        fifo.unlink(missing_ok=True)
        os.mkfifo(fifo)
        self.assertEqual(subprocess.run([sys.executable, '-B', str(MODULE_DIR / 'sp11_display_mode.py'), 'modes', '--edid',
                                         str(fifo)], capture_output=True, text=True, timeout=10).returncode, 2)

    def test_an_oversize_edid_file_is_unusable(self):
        big = BUILD / 'oversize.edid'
        big.write_bytes(bytes(128 * dm.MAX_EDID_BLOCKS + 1))
        self.assertEqual(self.run_tool('modes', '--edid', str(big)).returncode, 2)

    def test_the_modes_subcommand_reads_its_file_only_through_read_regular_file(self):
        seen, real = [], dm.read_regular_file

        def spy(path, cap):
            seen.append((path, cap))
            return real(path, cap)
        with mock.patch.object(dm, 'read_regular_file', spy), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dm.main_modes(['--edid', str(self.path)]), 0)
        self.assertEqual(seen, [(str(self.path), 128 * dm.MAX_EDID_BLOCKS)])

    def test_a_malformed_edid_is_a_rejection_and_an_unusable_call_is_exit_2(self):
        bad = bytearray(self.path.read_bytes())
        bad[0x40] ^= 1
        run = self.run_tool('modes', '--edid-hex', bytes(bad).hex())
        self.assertEqual((run.returncode, json.loads(run.stdout)['reasons']), (1, ['edid-checksum']))
        for argv in (('modes',), ('modes', '--edid', str(BUILD / 'missing')), ('modes', '--edid-hex', 'zz'),
                     ('modes', '--edid', str(self.path), '--require', 'wide'), ('modes', '--edid', str(self.path),
                                                                              '--tolerance', 'x'),
                     ('modes', '--edid', str(self.path), '--edid-hex', '00')):
            self.assertEqual(self.run_tool(*argv).returncode, 2, argv)

    def test_link_budget_exit_codes_follow_the_answer(self):
        base = ('link-budget', '--h-active', str(PANEL[0]), '--v-active', str(PANEL[1]), '--refresh', '120')
        fits = self.run_tool(*base, '--bpc', '8', '--lanes', '4', '--rate', 'HBR3')
        self.assertEqual((fits.returncode, json.loads(fits.stdout)['fits']), (0, True))
        no = self.run_tool(*base, '--bpc', '8', '--lanes', '2', '--rate', 'HBR3')
        self.assertEqual((no.returncode, json.loads(no.stdout)['fits']), (1, False))
        dsc = self.run_tool(*base, '--bpc', '8', '--lanes', '2', '--rate', 'HBR3', '--dsc-bpp', '8')
        self.assertEqual((dsc.returncode, json.loads(dsc.stdout)['dsc']), (0, True))
        explicit = self.run_tool('link-budget', '--h-active', str(PANEL[0]), '--v-active', str(PANEL[1]), '--refresh', '120',
                                 '--blanking', 'explicit', '--h-blank', '80', '--v-blank', '30', '--bpc', '8', '--lanes', '4',
                                 '--rate', 'HBR2')
        self.assertEqual((explicit.returncode, json.loads(explicit.stdout)['timing']['pixel_clock_khz']), (0, 692640))

    def test_link_budget_refuses_unusable_inputs(self):
        base = ('link-budget', '--h-active', str(PANEL[0]), '--v-active', str(PANEL[1]), '--refresh', '120')
        for extra in (('--bpc', '8', '--lanes', '0', '--rate', 'HBR3'), ('--bpc', '8', '--lanes', '4', '--rate', 'HBR9'),
                      ('--bpc', '8', '--lanes', '4'), ('--bpc', '9', '--lanes', '4', '--rate', 'HBR3'),
                      ('--blanking', 'explicit', '--h-blank', '-1', '--v-blank', '30', '--bpc', '8', '--lanes', '4',
                       '--rate', 'HBR3'),
                      ('--h-blank', '80', '--bpc', '8', '--lanes', '4', '--rate', 'HBR3')):
            self.assertEqual(self.run_tool(*base, *extra).returncode, 2, extra)
        self.assertEqual(self.run_tool('link-budget', '--h-active', '0', '--v-active', '10', '--refresh', '60',
                                       '--bpc', '8', '--lanes', '4', '--rate', 'HBR3').returncode, 2)

    def test_the_matrix_prints_every_combination(self):
        run = self.run_tool('link-budget', '--h-active', str(PANEL[0]), '--v-active', str(PANEL[1]), '--refresh', '60',
                            '--matrix')
        out = json.loads(run.stdout)
        self.assertEqual((run.returncode, len(out['rows']), out['timing']['blanking']), (0, 36, 'cvt-rb-v1'))

    def test_the_subcommands_write_nothing(self):
        before = sorted(p.name for p in BUILD.iterdir())
        self.run_tool('modes', '--edid', str(self.path))
        self.run_tool('link-budget', '--h-active', '64', '--v-active', '48', '--refresh', '60', '--matrix')
        self.assertEqual(sorted(p.name for p in BUILD.iterdir()), before)

    def test_the_original_invocation_is_unchanged(self):
        gop_file = BUILD / 'gop.json'
        gop_file.write_text(gop())
        run = self.run_tool('--gop', str(gop_file), '--edid', str(self.path))
        self.assertEqual((run.returncode, json.loads(run.stdout)['verdict']), (0, 'ACCEPT'))

    def test_one_malformed_extension_is_accepted_with_a_reason_by_gop_and_refused_by_modes(self):
        gop_file, bad = BUILD / 'gop.json', BUILD / 'bad-displayid.edid'
        gop_file.write_text(gop())
        bad.write_bytes(edid(did_ext([did_block(0x03, 0, did_timing(10, 692640, **T120))], checksum=False)))
        lenient = self.run_tool('--gop', str(gop_file), '--edid', str(bad))
        out = json.loads(lenient.stdout)
        self.assertEqual((lenient.returncode, out['verdict'], out['reasons']), (0, 'ACCEPT', []))
        self.assertTrue(out['edid']['partial'][0].startswith('edid-displayid: '))
        strict = self.run_tool('modes', '--edid', str(bad))
        self.assertEqual((strict.returncode, json.loads(strict.stdout)['reasons']), (1, ['edid-displayid']))


# ---- Hygiene -------------------------------------------------------------------------------------------------------
class Hygiene(unittest.TestCase):
    FILES = ('tools/sp11_edid_ext.py', 'tools/sp11_cta_vic.py', 'tools/sp11_link_budget.py', 'tools/sp11_display_mode.py',
             'tools/test-sp11-display-120hz.py', 'tools/test-sp11-display-120hz-mutants.py')

    def test_spdx_no_machine_strings_and_sizes(self):
        for rel in self.FILES:
            text = (ROOT / rel).read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]), rel)
            for prefix in HOME_PREFIXES:
                self.assertNotIn(prefix, text, rel)
            self.assertLess(len(text.splitlines()), 1500, rel)
            self.assertLess((ROOT / rel).stat().st_size, 50 * 1024, rel)
            self.assertTrue(text.isascii(), rel)

    def test_the_new_modules_assume_no_panel_size(self):
        for rel in ('tools/sp11_edid_ext.py', 'tools/sp11_link_budget.py'):
            text = (ROOT / rel).read_text()
            for literal in ('2880', '1920', '1800'):
                self.assertNotRegex(text, r'\b%s\b' % literal, rel)

    def test_operator_paths_are_opened_only_by_read_regular_file(self):
        for rel in ('tools/sp11_edid_ext.py', 'tools/sp11_cta_vic.py', 'tools/sp11_link_budget.py'):
            self.assertIsNone(re.search(r'(?<![\w.])open\(|os\.open|read_bytes|read_text|\bPath\b', (ROOT / rel).read_text()), rel)
        text = (ROOT / 'tools/sp11_display_mode.py').read_text()
        self.assertIsNone(re.search(r'(?<![\w.])open\(|read_bytes|read_text', text))
        self.assertEqual(text.count('os.open('), 1)

    def test_the_study_table_is_what_the_model_computes(self):
        study = ROOT / 'docs' / 'SP11-120HZ-STUDY.md'
        if not study.is_file():
            self.skipTest('docs/SP11-120HZ-STUDY.md is not present')
        text = study.read_text()
        match = re.search(r'<!-- BEGIN LINK-BUDGET-TABLE.*?-->(.*?)<!-- END LINK-BUDGET-TABLE -->', text, re.S)
        self.assertTrue(match, 'the link-budget table markers are missing')
        rows = re.findall(r'^\| (\d+) \| (cvt-rb-v1|explicit-80x30) \| (\d+) \| (\d+) \| (\d+) \| ([A-Z0-9]+) \| (\d) \| ([^|]+) \| '
                          r'([-0-9.]+) \| (yes|no) \|$', match.group(1), re.M)
        self.assertGreaterEqual(len(rows), 12)
        for hz, blanking, clock, htotal, vtotal, rate, lanes, need, margin, fits in rows:
            t = (lb.cvt_rb_timing(*PANEL, int(hz)) if blanking == 'cvt-rb-v1'
                 else lb.explicit_timing(*PANEL, 80, 30, refresh_hz=int(hz)))
            self.assertEqual((t.pixel_clock_khz, t.htotal, t.vtotal), (int(clock), int(htotal), int(vtotal)), (hz, blanking))
            r = lb.link_budget(t, 8 if '8 bpc' in need else 10, int(lanes), rate)
            self.assertEqual(('yes' if r['fits'] else 'no', float(margin)), (fits, round(r['margin_percent'], 1)), (hz, rate, lanes))


if __name__ == '__main__':
    unittest.main(verbosity=1)
