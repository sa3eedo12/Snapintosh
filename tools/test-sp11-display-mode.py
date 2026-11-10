#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for tools/sp11_display_mode.py: GOP and EDID mode validation that assumes no panel size.

No hardware and no private input. Every EDID is built here from public layout rules (VESA E-EDID 1.4 base block,
CTA-861 extension) by an encoder written separately from the decoder under test, and the decoder is also checked
against two literal descriptors of public CEA/CTA-861 timings (VIC 16, 1080p60, and VIC 4, 720p60). The 3:2 panel
EDIDs are SYNTHETIC: their numbers say nothing about the Surface Pro 11's real EDID. The mutation test seeds bugs into
copies of the tool (under build/) and requires this suite to fail on each one.

    python3 tools/test-sp11-display-mode.py
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'test-sp11-display-mode'
TOOL = ROOT / 'tools' / 'sp11_display_mode.py'
MODULE_DIR = Path(os.environ.get('SP11_DISPLAY_MODE_DIR') or TOOL.parent)
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')

spec = importlib.util.spec_from_file_location('sp11_display_mode', MODULE_DIR / 'sp11_display_mode.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# Public CTA-861 timings (VIC 16 and VIC 4) as detailed timing descriptors, written out by hand from the encoding.
DTD_1080P60 = bytes.fromhex('023a801871382d40582c4500c48e2100001e')
DTD_720P60 = bytes.fromhex('011d007251d01e206e285500c48e2100001e')
# A SYNTHETIC 3:2 panel timing: 346.32 MHz over 2960 x 1950 is exactly 60 Hz.
SYN = dict(clock10k=34632, hactive=2880, hblank=80, vactive=1920, vblank=30, hfront=24, hsync=16, vfront=8, vsync=4,
           hsize=274, vsize=183)


def encode_dtd(clock10k, hactive, hblank, vactive, vblank, hfront, hsync, vfront, vsync, hsize=0, vsize=0, flags=0x1e):
    b = bytearray(18)
    b[0], b[1] = clock10k & 0xff, clock10k >> 8
    b[2], b[3], b[4] = hactive & 0xff, hblank & 0xff, (hactive >> 8) << 4 | hblank >> 8
    b[5], b[6], b[7] = vactive & 0xff, vblank & 0xff, (vactive >> 8) << 4 | vblank >> 8
    b[8], b[9], b[10] = hfront & 0xff, hsync & 0xff, (vfront & 15) << 4 | (vsync & 15)
    b[11] = (hfront >> 8) << 6 | (hsync >> 8) << 4 | (vfront >> 4) << 2 | (vsync >> 4)
    b[12], b[13], b[14] = hsize & 0xff, vsize & 0xff, (hsize >> 8) << 4 | vsize >> 8
    b[17] = flags
    return bytes(b)


def seal(block):
    block = bytearray(block)
    block[127] = (-sum(block[:127])) & 0xff
    return bytes(block)


def base_block(dtds=(), declared=True, version=1, revision=4, extensions=0, mfg='SYN', fix=True):
    b = bytearray(128)
    b[:8] = bytes([0, 255, 255, 255, 255, 255, 255, 0])
    word = sum((ord(c) - 64) << s for c, s in zip(mfg, (10, 5, 0)))
    b[8], b[9], b[10], b[11] = word >> 8, word & 0xff, 0x34, 0x12
    b[0x12], b[0x13], b[0x14], b[0x18], b[0x7e] = version, revision, 0xa5, 0x02 if declared else 0, extensions
    for i, off in enumerate(m.DTD_OFFSETS):
        if i < len(dtds):
            b[off:off + 18] = dtds[i]
        else:
            b[off + 3] = 0xfc if i == len(dtds) else 0x10
            b[off + 5:off + 11] = b'SYNTH\n' if i == len(dtds) else bytes(6)
    return seal(b) if fix else bytes(b)


def cta_block(dtds=(), blocks=b'', revision=3, offset=None, fix=True):
    b = bytearray(128)
    b[0], b[1] = 2, revision
    d = 4 + len(blocks) if offset is None else offset
    b[2], b[3] = d if (dtds or blocks or offset is not None) else 0, len(dtds)
    b[4:4 + len(blocks)] = blocks
    for i, dtd in enumerate(dtds):
        b[d + 18 * i:d + 18 * (i + 1)] = dtd
    return seal(b) if fix else bytes(b)


def panel_edid(**over):
    return base_block([encode_dtd(**{**SYN, **over})])


def gop(width=2880, height=1920, stride=None, fmt='BGRX', **extra):
    return json.dumps(dict(width=width, height=height, stride_pixels=width if stride is None else stride, format=fmt,
                           **extra))


class Decoder(unittest.TestCase):
    def test_known_public_timings_decode_to_their_published_parameters(self):
        d = m.parse_dtd(DTD_1080P60)
        self.assertEqual((d.hactive, d.hblank, d.hfront, d.hsync), (1920, 280, 88, 44))
        self.assertEqual((d.vactive, d.vblank, d.vfront, d.vsync), (1080, 45, 4, 5))
        self.assertEqual((d.pixel_clock_khz, d.htotal, d.vtotal, d.interlaced), (148500, 2200, 1125, False))
        self.assertAlmostEqual(d.refresh_hz, 60.0, places=6)
        d = m.parse_dtd(DTD_720P60)
        self.assertEqual((d.hactive, d.hblank, d.hfront, d.hsync, d.vactive, d.vblank, d.vfront, d.vsync),
                         (1280, 370, 110, 40, 720, 30, 5, 5))
        self.assertEqual((d.pixel_clock_khz, d.htotal, d.vtotal), (74250, 1650, 750))
        self.assertAlmostEqual(d.refresh_hz, 60.0, places=6)

    def test_the_test_encoder_agrees_with_the_literals(self):
        self.assertEqual(encode_dtd(14850, 1920, 280, 1080, 45, 88, 44, 4, 5, 0x1c4, 0x18e, 0x1e)[:12], DTD_1080P60[:12])
        self.assertEqual(encode_dtd(7425, 1280, 370, 720, 30, 110, 40, 5, 5)[:12], DTD_720P60[:12])

    def test_every_field_survives_a_round_trip_including_the_high_bits(self):
        spec_ = dict(clock10k=30000, hactive=3840, hblank=1100, vactive=2160, vblank=300, hfront=500, hsync=500,
                     vfront=62, vsync=61, hsize=1500, vsize=1900)
        d = m.parse_dtd(encode_dtd(**spec_, flags=0x80))
        self.assertEqual((d.hactive, d.hblank, d.vactive, d.vblank), (3840, 1100, 2160, 300))
        self.assertEqual((d.hfront, d.hsync, d.vfront, d.vsync), (500, 500, 62, 61))
        self.assertEqual((d.hsize_mm, d.vsize_mm, d.interlaced), (1500, 1900, True))

    def test_synthetic_panel(self):
        e = m.parse_edid(panel_edid())
        self.assertEqual((e.version, e.revision, e.manufacturer, e.product_code), (1, 4, 'SYN', 0x1234))
        self.assertEqual((e.preferred.hactive, e.preferred.vactive), (2880, 1920))
        self.assertAlmostEqual(e.preferred.refresh_hz, 60.0, places=6)
        self.assertEqual((e.preferred.hsize_mm, e.preferred.vsize_mm), (274, 183))

    def test_cta_extension_timings_are_decoded_and_data_blocks_tiled(self):
        video = bytes([0x40 | 1, 0x90])           # a video data block of one VIC
        edid = base_block([encode_dtd(**SYN)], extensions=1) + cta_block([DTD_1080P60, DTD_720P60], video)
        e = m.parse_edid(edid)
        self.assertEqual([(t.hactive, t.vactive) for t in e.timings], [(2880, 1920), (1920, 1080), (1280, 720)])
        self.assertEqual(e.extensions, ((2, True),))

    def test_other_extension_types_are_checksummed_and_reported_not_decoded(self):
        other = seal(bytes([0x70, 1, 0x4d] + [0] * 125))
        e = m.parse_edid(base_block([encode_dtd(**SYN)], extensions=1) + other)
        self.assertEqual(e.extensions, ((0x70, False),))
        self.assertRaises(m.ModeError, m.parse_edid, base_block([encode_dtd(**SYN)], extensions=1) + other[:127] + b'\x01')

    def test_edid_13_is_accepted(self):
        self.assertEqual(m.parse_edid(base_block([encode_dtd(**SYN)], revision=3)).revision, 3)

    def test_no_preferred_timing_declared(self):
        e = m.parse_edid(base_block([encode_dtd(**SYN)], declared=False))
        self.assertIsNone(e.preferred)
        self.assertEqual(len(e.timings), 1)


class MalformedEdid(unittest.TestCase):
    def refused(self, data, code):
        with self.assertRaises(m.ModeError) as raised:
            m.parse_edid(data)
        self.assertEqual(raised.exception.code, code, raised.exception)

    def test_length(self):
        for data in (b'', bytes(127), panel_edid()[:127], panel_edid() + b'\x00', panel_edid()[:100] + panel_edid()):
            self.refused(data, 'edid-length')

    def test_extension_count_must_match_the_length(self):
        self.refused(panel_edid() + cta_block(), 'edid-length')
        self.refused(base_block([encode_dtd(**SYN)], extensions=1), 'edid-length')
        self.refused(base_block([encode_dtd(**SYN)], extensions=2) + cta_block(), 'edid-length')

    def test_header(self):
        good = bytearray(panel_edid())
        good[3] = 0x00
        self.refused(seal(good), 'edid-header')

    def test_base_checksum(self):
        bad = bytearray(panel_edid())
        bad[0x40] ^= 1
        self.refused(bytes(bad), 'edid-checksum')
        self.refused(base_block([encode_dtd(**SYN)], fix=False), 'edid-checksum')

    def test_extension_checksum(self):
        bad = bytearray(cta_block([DTD_1080P60]))
        bad[10] ^= 1
        self.refused(base_block([encode_dtd(**SYN)], extensions=1) + bytes(bad), 'edid-checksum')

    def test_version(self):
        for version, revision in ((2, 0), (1, 2), (1, 5), (0, 4)):
            self.refused(base_block([encode_dtd(**SYN)], version=version, revision=revision), 'edid-version')

    def test_manufacturer_id(self):
        self.refused(base_block([encode_dtd(**SYN)], mfg='@@@'), 'edid-base')
        self.refused(base_block([encode_dtd(**SYN)], mfg='[AB'), 'edid-base')

    def test_preferred_declared_but_first_descriptor_is_not_a_timing(self):
        self.refused(base_block([], declared=True), 'edid-base')

    def test_display_descriptor_with_a_reserved_byte_set(self):
        b = bytearray(base_block([encode_dtd(**SYN)]))
        b[0x48 + 2] = 1
        self.refused(seal(b), 'edid-base')

    def test_timing_descriptor_faults(self):
        bad = {'empty active width': dict(hactive=0), 'empty active height': dict(vactive=0),
               'sync does not fit horizontally': dict(hfront=60, hsync=40), 'sync does not fit vertically': dict(vfront=20, vsync=20),
               'refresh too low': dict(clock10k=3463),
               'refresh too high': dict(clock10k=65000, hactive=640, hblank=16, vactive=480, vblank=4, hfront=4, hsync=4,
                                        vfront=1, vsync=1)}
        for name, over in bad.items():
            with self.subTest(name):
                self.refused(base_block([encode_dtd(**{**SYN, **over})]), 'edid-dtd')

    def test_a_corrupt_secondary_descriptor_refuses_the_whole_edid(self):
        self.refused(base_block([encode_dtd(**SYN), encode_dtd(**{**SYN, 'vactive': 0})]), 'edid-dtd')

    def test_cta_faults(self):
        wrap = lambda block: base_block([encode_dtd(**SYN)], extensions=1) + block
        for offset in (1, 2, 3, 128, 200):
            self.refused(wrap(cta_block(offset=offset)), 'edid-cta')
        self.refused(wrap(cta_block(blocks=bytes([0x40 | 20, 0x90]), offset=6)), 'edid-cta')      # block overruns offset
        self.refused(wrap(cta_block(revision=0, dtds=[DTD_1080P60])), 'edid-cta')
        broken = bytearray(DTD_1080P60)
        broken[2] = 0
        broken[4] &= 0x0f
        self.refused(wrap(cta_block([bytes(broken)])), 'edid-dtd')


class GopReport(unittest.TestCase):
    def refused(self, text, code):
        with self.assertRaises(m.ModeError) as raised:
            m.parse_gop_text(text)
        self.assertEqual(raised.exception.code, code, raised.exception)

    def test_json_and_summary_agree(self):
        a = m.parse_gop_text(gop(base='0x80000000', size=2880 * 1920 * 4))
        b = m.parse_gop_text('# SP11PROBE summary v1\nbuild=X\ngop_width=2880\ngop_height=1920\ngop_stride_pixels=2880\n'
                             'gop_format=0x1\ngop_base=0x0000000080000000\ngop_size=%#018x\nlayout_scale=5\n' % (2880 * 1920 * 4))
        self.assertEqual(a, b)
        self.assertEqual((a.format, a.stride_bytes), ('BGRX', 11520))

    def test_formats(self):
        self.assertEqual(m.parse_gop_text(gop(fmt=0)).format, 'RGBX')
        self.assertEqual(m.parse_gop_text(gop(fmt='rgbx')).format, 'RGBX')
        self.assertEqual(m.parse_gop_text(gop(fmt='0x1')).format, 'BGRX')
        for fmt in (2, 3, 'BITMASK', 'BLT_ONLY'):
            self.refused(gop(fmt=fmt), 'gop-format')
        for fmt in (4, 'ARGB', -1, 1.0, None, True):
            self.refused(gop(fmt=fmt), 'gop-format' if fmt in (4, 'ARGB') else 'gop-field')

    def test_dimensions(self):
        for kwargs in (dict(width=0), dict(height=0), dict(width=-1), dict(height=16385), dict(width=True),
                       dict(width=1.5), dict(width='abc'), dict(width=None)):
            self.refused(gop(**kwargs), 'gop-field')

    def test_stride(self):
        self.refused(gop(stride=2879), 'gop-stride')
        self.assertEqual(m.parse_gop_text(gop(stride=3072)).stride_bytes, 12288)
        self.refused(gop(stride=0), 'gop-field')

    def test_framebuffer_size(self):
        self.assertEqual(m.parse_gop_text(gop(size=2880 * 1920 * 4)).size, 2880 * 1920 * 4)
        self.refused(gop(size=2880 * 1920 * 4 - 1), 'gop-size')
        self.refused(gop(stride=3072, size=2880 * 1920 * 4), 'gop-size')
        self.refused(gop(base=(1 << 64) - 4096, size=2880 * 1920 * 4), 'gop-size')
        self.refused(gop(base=0), 'gop-field')

    def test_malformed_reports(self):
        self.refused('', 'gop-absent')
        self.refused('# header only\nbuild=X\n', 'gop-absent')
        self.refused('gop=none\n', 'gop-absent')
        self.refused('{"width": 1', 'gop-syntax')
        self.refused('[1, 2]', 'gop-syntax')
        self.refused('{"width": 1, "width": 2}', 'gop-syntax')
        self.refused('gop_width=2880\ngop_width=2880\n', 'gop-syntax')
        self.refused('not a pair\n', 'gop-syntax')
        self.refused('{"width": 100, "height": 100}', 'gop-field')
        self.refused('{"width": 100, "height": 100, "stride_pixels": 100, "format": 1, "colour": 1}', 'gop-field')
        self.refused('gop_width=2880\ngop_height=1920\n', 'gop-field')


class Decision(unittest.TestCase):
    def verdict(self, gop_text, edid=None, expect=None):
        return m.evaluate(gop_text, edid, expect)

    def test_gop_only_is_accepted_with_a_warning(self):
        r = self.verdict(gop())
        self.assertEqual((r['verdict'], r['cross_check']), ('ACCEPT', 'not-performed'))
        self.assertEqual(r['mode'], dict(width=2880, height=1920, stride_pixels=2880, stride_bytes=11520, format='BGRX',
                                         bytes_per_pixel=4))
        self.assertTrue(r['warnings'])

    def test_matching_edid_is_accepted(self):
        r = self.verdict(gop(), panel_edid())
        self.assertEqual((r['verdict'], r['cross_check'], r['reasons']), ('ACCEPT', 'match', []))
        self.assertEqual(r['edid']['preferred']['width'], 2880)

    def test_mismatching_edid_is_refused(self):
        for kwargs in (dict(height=1800), dict(width=2560), dict(width=1920, height=1280), dict(width=1920, height=2880)):
            r = self.verdict(gop(**kwargs), panel_edid())
            self.assertEqual((r['verdict'], r['cross_check']), ('REJECT', 'mismatch'), kwargs)
            self.assertIn('mode-mismatch', r['reasons'])

    def test_width_and_height_are_not_interchangeable(self):
        r = self.verdict(gop(width=1920, height=2880), panel_edid())
        self.assertEqual(r['verdict'], 'REJECT')

    def test_the_mismatch_names_the_edid_timing_the_gop_does_match(self):
        edid = base_block([encode_dtd(**SYN)], extensions=1) + cta_block([DTD_1080P60])
        r = self.verdict(gop(width=1920, height=1080), edid)
        self.assertEqual(r['verdict'], 'REJECT')
        self.assertIn('timing #1', ' '.join(r['messages']))

    def test_an_edid_that_cannot_confirm_the_gop_is_refused(self):
        r = self.verdict(gop(), base_block([encode_dtd(**SYN)], declared=False))
        self.assertEqual((r['verdict'], r['reasons'], r['cross_check']), ('REJECT', ['edid-no-preferred'], 'not-performed'))

    def test_interlaced_preferred_timing_is_refused(self):
        r = self.verdict(gop(), base_block([encode_dtd(**SYN, flags=0x9e)]))
        self.assertEqual((r['verdict'], r['reasons']), ('REJECT', ['edid-interlaced']))

    def test_both_inputs_are_reported_when_both_are_bad(self):
        r = self.verdict(gop(stride=10), b'\x00' * 128)
        self.assertEqual((r['verdict'], r['reasons']), ('REJECT', ['gop-stride', 'edid-header']))

    def test_a_bad_edid_never_yields_a_mode(self):
        bad = bytearray(panel_edid())
        bad[0x40] ^= 1
        r = self.verdict(gop(), bytes(bad))
        self.assertEqual((r['verdict'], r['mode']), ('REJECT', None))

    def test_operator_expectation(self):
        self.assertEqual(self.verdict(gop(), expect=(2880, 1920))['verdict'], 'ACCEPT')
        r = self.verdict(gop(), expect=(2880, 1800))
        self.assertEqual((r['verdict'], r['reasons']), ('REJECT', ['expect-mismatch']))

    def test_the_tool_assumes_no_panel_size(self):
        """The two sizes the repository's documents disagree about must not occur in the tool: it only reports what
        its inputs say."""
        text = (MODULE_DIR / 'sp11_display_mode.py').read_text()
        for literal in ('2880', '1920', '1800', '1280', '2560', '3840'):
            self.assertNotRegex(text, r'\b%s\b' % literal)

    def test_result_is_json_serialisable_and_deterministic(self):
        a = json.dumps(self.verdict(gop(), panel_edid()), sort_keys=True)
        self.assertEqual(a, json.dumps(self.verdict(gop(), panel_edid()), sort_keys=True))


class CommandLine(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([sys.executable, '-B', str(MODULE_DIR / 'sp11_display_mode.py'), *args], capture_output=True,
                              text=True, timeout=60)

    def setUp(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        self.gop = BUILD / 'gop.json'
        self.gop.write_text(gop())
        self.edid = BUILD / 'panel.edid'
        self.edid.write_bytes(panel_edid())

    def test_accept(self):
        run = self.run_tool('--gop', str(self.gop), '--edid', str(self.edid))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['verdict'], 'ACCEPT')

    def test_edid_as_hex_text(self):
        run = self.run_tool('--gop', str(self.gop), '--edid-hex', panel_edid().hex(' ', 16))
        self.assertEqual((run.returncode, json.loads(run.stdout)['cross_check']), (0, 'match'))

    def test_reject(self):
        self.gop.write_text(gop(height=1800))
        run = self.run_tool('--gop', str(self.gop), '--edid', str(self.edid))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)['reasons'], ['mode-mismatch'])

    def test_expect_size(self):
        self.assertEqual(self.run_tool('--gop', str(self.gop), '--expect-size', '2880x1920').returncode, 0)
        self.assertEqual(self.run_tool('--gop', str(self.gop), '--expect-size', '1x1').returncode, 1)
        self.assertEqual(self.run_tool('--gop', str(self.gop), '--expect-size', 'wide').returncode, 2)

    def test_unusable_invocations_exit_2(self):
        self.assertEqual(self.run_tool('--gop', str(BUILD / 'missing.json')).returncode, 2)
        self.assertEqual(self.run_tool('--gop', str(BUILD)).returncode, 2)                      # a directory
        self.assertEqual(self.run_tool('--gop', str(self.gop), '--edid-hex', 'zz').returncode, 2)
        self.assertEqual(self.run_tool().returncode, 2)
        big = BUILD / 'big.edid'
        big.write_bytes(bytes(128 * 256 + 1))
        self.assertEqual(self.run_tool('--gop', str(self.gop), '--edid', str(big)).returncode, 2)
        binary = BUILD / 'binary.json'
        binary.write_bytes(b'\xff\xfe\x00')
        self.assertEqual(self.run_tool('--gop', str(binary)).returncode, 2)

    def test_the_tool_does_not_write(self):
        before = sorted(p.name for p in BUILD.iterdir())
        self.run_tool('--gop', str(self.gop), '--edid', str(self.edid))
        self.assertEqual(sorted(p.name for p in BUILD.iterdir()), before)

    def test_an_empty_edid_path_is_unusable_not_absent(self):
        run = self.run_tool('--gop', str(self.gop), '--edid', '')
        self.assertEqual((run.returncode, run.stdout), (2, ''), run.stderr)

    def test_an_empty_edid_text_is_an_edid_that_is_refused(self):
        run = self.run_tool('--gop', str(self.gop), '--edid-hex', '')
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)['reasons'], ['edid-length'])

    def test_links_and_special_files_are_unusable_and_never_block(self):
        link, fifo = BUILD / 'gop-link.json', BUILD / 'gop.fifo'
        for path in (link, fifo):
            path.unlink(missing_ok=True)
        link.symlink_to(self.gop)
        os.mkfifo(fifo)
        for path in (link, fifo):
            for argv in (('--gop', str(path)), ('--gop', str(self.gop), '--edid', str(path))):
                with self.subTest(argv=argv):
                    self.assertEqual(self.run_tool(*argv).returncode, 2)


class OpenedOnlyWhenRegular(unittest.TestCase):
    """read_regular_file looks at a path before it opens it: opening a device node or a terminal can act on it."""

    def setUp(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        self.plain, self.other = BUILD / 'plain.bin', BUILD / 'other.bin'
        self.link, self.link_to_fifo, self.fifo = BUILD / 'plain.link', BUILD / 'fifo.link', BUILD / 'plain.fifo'
        for path in (self.link, self.link_to_fifo, self.fifo):
            path.unlink(missing_ok=True)
        self.plain.write_bytes(b'0123456789')
        self.other.write_bytes(b'abcdefghij')
        self.link.symlink_to(self.plain)
        os.mkfifo(self.fifo)
        self.link_to_fifo.symlink_to(self.fifo)

    def never_opened(self, path, cap=1024):
        with mock.patch.object(m.os, 'open', side_effect=AssertionError('opened before its type was known')):
            with self.assertRaises(OSError):
                m.read_regular_file(path, cap)

    def test_nothing_that_is_not_a_regular_file_is_opened(self):
        for path in (self.fifo, BUILD, self.link, self.link_to_fifo):
            with self.subTest(path=path.name):
                self.never_opened(path)

    def test_an_oversize_file_is_refused_before_it_is_opened(self):
        self.never_opened(self.plain, cap=9)

    def test_a_regular_file_is_read_whole_up_to_the_cap(self):
        self.assertEqual(m.read_regular_file(self.plain, 10), b'0123456789')
        with self.assertRaises(OSError):
            m.read_regular_file(self.plain, 9)

    def test_the_descriptor_must_be_the_file_that_was_inspected(self):
        with mock.patch.object(m.os, 'lstat', return_value=os.lstat(self.other)):
            with self.assertRaises(OSError):
                m.read_regular_file(self.plain, 1024)

    def test_a_link_swapped_in_after_the_inspection_is_not_followed(self):
        with mock.patch.object(m.os, 'lstat', return_value=os.lstat(self.plain)):
            with self.assertRaises(OSError):
                m.read_regular_file(self.link, 1024)


class Hygiene(unittest.TestCase):
    FILES = ('tools/sp11_display_mode.py', 'tools/test-sp11-display-mode.py')

    def test_spdx_no_machine_strings_and_sizes(self):
        for rel in self.FILES:
            text = (ROOT / rel).read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]), rel)
            for prefix in HOME_PREFIXES:
                self.assertNotIn(prefix, text, rel)
            self.assertLess(len(text.splitlines()), 1500, rel)
            self.assertLess((ROOT / rel).stat().st_size, 50 * 1024, rel)


# ---- Mutation sanity check --------------------------------------------------------------------------------------------
# (name, old text, new text), all in tools/sp11_display_mode.py. Each is a plausible bug; this suite must fail on it.
MUTANTS = (
    ('bad EDID checksum accepted', 'if sum(block) & 0xff:', 'if False:'),
    ('GOP/EDID mismatch accepted', 'if (gop.width, gop.height) == (p.hactive, p.vactive):', 'if True:'),
    ('width and height swapped in the comparison', 'if (gop.width, gop.height) == (p.hactive, p.vactive):',
     'if (gop.width, gop.height) == (p.vactive, p.hactive):'),
    ('EDID header not checked', 'if data[:8] != EDID_HEADER:', 'if False:'),
    ('hactive high nibble mispacked', 'hactive=b[2] | (b[4] & 0xf0) << 4', 'hactive=b[2] | (b[4] & 0xf0) << 8'),
    ('vfront high bits dropped', '(b[10] >> 4) | (b[11] & 0x0c) << 2', '(b[10] >> 4)'),
    ('stride below width accepted', 'if stride < width:', 'if False:'),
    ('interlaced preferred accepted', 'elif p.interlaced:', 'elif False:'),
    ('extension count not checked', 'if len(data) != 128 * (1 + base[0x7e]):', 'if False:'),
    ('sync outside blanking accepted', 'if d.hfront + d.hsync > d.hblank or d.vfront + d.vsync > d.vblank:', 'if False:'),
    ('blt-only format accepted', 'if fmt not in LINEAR_FORMATS:', 'if False:'),
    ('framebuffer smaller than its geometry accepted', 'if size is not None and size < stride * BYTES_PER_PIXEL * height:',
     'if False:'),
    ('preferred timing assumed', 'declared = bool(base[0x18] & 0x02)', 'declared = True'),
    ('operator expectation ignored', 'if tuple(expect_size) != (gop.width, gop.height):', 'if False:'),
    ('unknown GOP field accepted', 'if unknown:', 'if False:'),
    ('bad CTA timing offset accepted', 'if offset in (1, 2, 3) or offset > 127:', 'if False:'),
    ('boolean accepted as a number', 'if isinstance(value, bool) or not isinstance(value, (int, str)):',
     'if not isinstance(value, (int, str)):'),
    ('manufacturer id unchecked', 'if word & 0x8000 or not all(1 <= x <= 26 for x in letters):', 'if False:'),
    ('refresh range unchecked', 'if not REFRESH_HZ_RANGE[0] <= d.refresh_hz <= REFRESH_HZ_RANGE[1]:', 'if False:'),
    ('three bytes per pixel', 'BYTES_PER_PIXEL = 4', 'BYTES_PER_PIXEL = 3'),
    ('duplicate JSON key accepted', 'if key in out:', 'if False:'),
    ('framebuffer wrap not checked', 'if base is not None and size is not None and base + size > 1 << 64:', 'if False:'),
    ('EDID version unchecked', 'if version != 1 or revision not in (3, 4):', 'if False:'),
    ('CTA data blocks not tiled', 'if end > offset:', 'if False:'),
    ('empty active area accepted', 'if not d.hactive or not d.vactive:', 'if False:'),
    ('other bad-EDID reasons dropped from the verdict', "result['verdict'] = 'REJECT'", "result['verdict'] = 'REJECT' if gop is None else 'ACCEPT'"),
    ('empty --edid path treated as no EDID', 'if args.edid is not None:', 'if args.edid:'),
    ('input type checked only after the open', 'if not stat.S_ISREG(listed.st_mode):', 'if False:'),
    ('input size checked only after the open', 'if listed.st_size > cap:', 'if False:'),
    ('opened descriptor not compared with the inspected file',
     'if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):',
     'if not stat.S_ISREG(opened.st_mode):'),
    ('links followed on open', "| getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))",
     "| getattr(os, 'O_NONBLOCK', 0))"),
)


@unittest.skipIf(os.environ.get('SP11_SKIP_MUTANTS'), 'running inside a mutant')
class Mutants(unittest.TestCase):
    def test_every_seeded_bug_is_caught(self):
        source = (TOOL).read_text()
        rows = []
        for i, (name, old, new) in enumerate(MUTANTS):
            self.assertEqual(source.count(old), 1, f'mutant {name!r}: the text to mutate must occur exactly once')
            work = BUILD / f'mutant-{i:02d}'
            work.mkdir(parents=True, exist_ok=True)
            (work / 'sp11_display_mode.py').write_text(source.replace(old, new))
            run = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve())], capture_output=True, text=True,
                                 timeout=300, env={**os.environ, 'SP11_DISPLAY_MODE_DIR': str(work), 'SP11_SKIP_MUTANTS': '1',
                                                   'PYTHONDONTWRITEBYTECODE': '1'})
            failing = re.findall(r'^(?:FAIL|ERROR): (\S+)', run.stderr, re.M)
            rows.append((i, name, failing[0] if run.returncode and failing else 'SURVIVED'))
        BUILD.mkdir(parents=True, exist_ok=True)
        (BUILD / 'mutants.tsv').write_text('id\tmutant\tfirst failing test\n' + ''.join('%d\t%s\t%s\n' % r for r in rows))
        self.assertEqual([r for r in rows if r[2] == 'SURVIVED'], [])
        self.assertGreaterEqual(len(rows), 20)


if __name__ == '__main__':
    unittest.main(verbosity=1)
