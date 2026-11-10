#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""EDID extension decoding for the SP11 display tools: the timings a panel can advertise beyond the base block.

Why it exists. A base-block detailed timing descriptor holds a 16-bit pixel clock in 10 kHz units, so it cannot name a
timing above 655.35 MHz; a high-refresh mode of a large panel has to be advertised somewhere else (docs/SP11-120HZ-STUDY.md
section 3). This module decodes the places that can carry one, from the public layouts of the VESA E-EDID 1.4 range
limits descriptor, the CTA-861 extension (video data blocks, the DisplayID Type VII video timing data block) and
DisplayID 1.3 Type I and 2.x Type VII timings, and says which advertised modes exceed the base-block limit. The field
positions are facts of those standards, cross-read in Linux `drm_displayid_internal.h` and `drm_edid.c`
(`drm_mode_displayid_detailed`) at commit 7475ea8b58616b33c04afb976cf0fbaad9dd63fb and in edid-decode
`parse-displayid-block.cpp` and `parse-cta-block.cpp` at v4l-utils commit 95ad25f6a77a0a6650f5f657ac2c5046efcd04a0
(MIT); no text or code is copied.

Strictness. Anything truncated, overrunning, mis-summed or self-contradictory raises ExtError with a stable `edid-*`
code. What is merely not understood (a DisplayID version this module does not read, a data block type it does not
decode) is reported in `partial`, never guessed: a mode list with a non-empty `partial` may be missing modes and must
not be used to claim that a mode is absent.

Evidence. VERIFIED by tests built from independently written encoders (tools/test-sp11-display-120hz.py). INFERRED:
that the Surface Pro 11 panel uses any of this. UNKNOWN until its EDID is dumped: which block, if any, carries 120 Hz.
"""
from dataclasses import dataclass
from fractions import Fraction

from sp11_cta_vic import vic_timing

BASE_BLOCK_PIXEL_CLOCK_LIMIT_KHZ = 0xFFFF * 10      # the widest pixel clock an 18-byte descriptor can hold
CTA_EXT_TAG = 0x02
DISPLAYID_EXT_TAG = 0x70
DISPLAYID_V1 = (0x10, 0x11, 0x12, 0x13)             # DisplayID 1.0 to 1.3
DISPLAYID_V2 = (0x20, 0x21)                         # DisplayID 2.0 and 2.1
REFRESH_RANGE_HZ = (20, 1000)                       # outside this a timing is a corrupt descriptor, not a mode
CTA_EXT_VTDB_TYPE7, CTA_EXT_VTDB_TYPE8, CTA_EXT_VTDB_TYPE10 = 0x22, 0x23, 0x2a


class ExtError(ValueError):
    """A refusal with a stable machine-readable code (the part before the colon)."""

    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code, self.message = code, message


@dataclass(frozen=True)
class Mode:
    """One advertised timing. `refresh_*` is the frame rate, or the field rate for an interlaced mode."""
    source: str                 # base-dtd, cta-dtd, cta-vic, cta-vtdb, displayid1-type1 or displayid2-type7
    hactive: int
    vactive: int
    pixel_clock_khz: int
    htotal: int
    vtotal: int
    interlaced: bool
    refresh_num: int
    refresh_den: int
    preferred: bool = False
    vic: int = 0                # non-zero for a mode that came from a video identification code
    native: bool = False

    @property
    def refresh_hz(self):
        return Fraction(self.refresh_num, self.refresh_den)

    @property
    def exceeds_base_block_limit(self):
        return self.pixel_clock_khz > BASE_BLOCK_PIXEL_CLOCK_LIMIT_KHZ

    def as_dict(self):
        return dict(source=self.source, width=self.hactive, height=self.vactive, pixel_clock_khz=self.pixel_clock_khz,
                    htotal=self.htotal, vtotal=self.vtotal, refresh_hz=round(float(self.refresh_hz), 3),
                    interlaced=self.interlaced, preferred=self.preferred, vic=self.vic or None, native=self.native,
                    exceeds_base_block_limit=self.exceeds_base_block_limit)


@dataclass(frozen=True)
class RangeLimits:
    """A refresh-rate range an EDID or DisplayID block advertises. `seamless` is None when the source has no such bit."""
    source: str                 # base-0xfd, displayid1-range or displayid2-dynamic
    min_refresh_hz: int
    max_refresh_hz: int
    max_pixel_clock_khz: int    # 0 when the source does not say
    seamless: object = None

    def as_dict(self):
        return dict(source=self.source, min_refresh_hz=self.min_refresh_hz, max_refresh_hz=self.max_refresh_hz,
                    max_pixel_clock_khz=self.max_pixel_clock_khz or None, seamless=self.seamless,
                    variable=self.min_refresh_hz != self.max_refresh_hz)


@dataclass(frozen=True)
class ExtResult:
    modes: tuple = ()
    ranges: tuple = ()
    unknown_vics: tuple = ()
    partial: tuple = ()
    decoded: bool = True


def mode_from_dtd(source, preferred, hactive, vactive, htotal, vtotal, pixel_clock_khz, interlaced):
    """A mode from an 18-byte descriptor's numbers; its vertical values are per field when interlaced."""
    return Mode(source, hactive, vactive, pixel_clock_khz, htotal, vtotal, interlaced, pixel_clock_khz * 1000,
                htotal * vtotal, preferred)


def _vic_mode(vic, native):
    row = vic_timing(vic)
    if row is None:
        return None
    hactive, vactive, interlaced, clock, htotal, vtotal = row
    return Mode('cta-vic', hactive, vactive, clock, htotal, vtotal, bool(interlaced),
                clock * 1000 * (2 if interlaced else 1), htotal * vtotal, False, vic, native)


def decode_svd(byte):
    """A short video descriptor byte -> (vic, native). Reserved bytes raise ExtError('edid-cta')."""
    if byte in (0, 128) or byte >= 254:
        raise ExtError('edid-cta', f'the video data block holds the reserved code byte {byte}')
    if 129 <= byte <= 192:
        return byte & 0x7f, True
    return byte, False


def _timing_20(b, clock_unit_khz, source, block_rev):
    """A 20-byte DisplayID timing (Type I or VII) -> Mode. Raises ExtError('edid-displayid')."""
    clock = ((b[0] | b[1] << 8 | b[2] << 16) + 1) * clock_unit_khz
    aspect, interlaced, stereo = b[3] & 0x0f, bool(b[3] & 0x10), (b[3] >> 5) & 3
    if aspect > 8:
        raise ExtError('edid-displayid', f'the timing has the reserved aspect ratio code {aspect}')
    if stereo == 3:
        raise ExtError('edid-displayid', 'the timing has the reserved stereo code 3')
    hactive, hblank = (b[4] | b[5] << 8) + 1, (b[6] | b[7] << 8) + 1
    hfront, hsync = (b[8] | (b[9] & 0x7f) << 8) + 1, (b[10] | b[11] << 8) + 1
    vactive, vblank = (b[12] | b[13] << 8) + 1, (b[14] | b[15] << 8) + 1
    vfront, vsync = (b[16] | (b[17] & 0x7f) << 8) + 1, (b[18] | b[19] << 8) + 1
    if hfront + hsync > hblank or vfront + vsync > vblank:
        raise ExtError('edid-displayid', 'the sync pulse and front porch do not fit in the blanking interval')
    htotal, vtotal = hactive + hblank, vactive + vblank
    rate = Fraction(clock * 1000 * (2 if interlaced else 1), htotal * vtotal)
    if not REFRESH_RANGE_HZ[0] <= rate <= REFRESH_RANGE_HZ[1]:
        raise ExtError('edid-displayid', f'the timing implies a refresh rate of {float(rate):.2f} Hz')
    preferred = block_rev < 2 and bool(b[3] & 0x80)
    return Mode(source, hactive, vactive, clock, htotal, vtotal, interlaced, clock * 1000 * (2 if interlaced else 1),
                htotal * vtotal, preferred)


# ---- Base block ---------------------------------------------------------------------------------------------------
def parse_base_range_limits(base, minor):
    """The Display Range Limits descriptor (tag 0xfd) of a base block, as a tuple of RangeLimits (empty if absent)."""
    out = []
    for off in (0x36, 0x48, 0x5a, 0x6c):
        d = base[off:off + 18]
        if d[0] or d[1] or d[2] or d[3] != 0xfd:
            continue
        flags = d[4]
        if (minor < 4 and flags) or flags & 0xf0 or (flags & 1 and not flags & 2) or (flags & 4 and not flags & 8):
            raise ExtError('edid-range-limits', f'the range-limits offset flags {flags:#04x} are not valid for EDID 1.{minor}')
        vmin = d[5] + (255 if flags & 1 else 0)
        vmax = d[6] + (255 if flags & 2 else 0)
        hmin = d[7] + (255 if flags & 4 else 0)
        hmax = d[8] + (255 if flags & 8 else 0)
        if not 0 < vmin <= vmax:
            raise ExtError('edid-range-limits', f'the vertical rate range {vmin}..{vmax} Hz is empty or starts at zero')
        if hmin > hmax:
            raise ExtError('edid-range-limits', f'the horizontal rate range {hmin}..{hmax} kHz is empty')
        out.append(RangeLimits('base-0xfd', vmin, vmax, d[9] * 10000))
    return tuple(out)


def base_partial_reasons(base):
    """Why the base block's modes may be incomplete: timings it carries that this module does not decode."""
    reasons = []
    if any(base[0x23:0x26]):
        reasons.append('the base block has established timings, which are not decoded')
    if any(base[0x26 + 2 * i:0x28 + 2 * i] != b'\x01\x01' for i in range(8)):
        reasons.append('the base block has standard timings, which are not decoded')
    return tuple(reasons)


# ---- CTA-861 extension --------------------------------------------------------------------------------------------
def parse_cta_extension(block, index):
    """The video-data-block and timing-data-block content of one CTA-861 extension. Raises ExtError('edid-cta').

    The caller has already verified the checksum and decoded the 18-byte descriptors (tools/sp11_display_mode.py);
    this adds the structure the descriptor pass does not check: the padding after the descriptors, the native
    descriptor count, and the data blocks' contents."""
    revision, offset = block[1], block[2]
    if not offset:
        return ExtResult()
    if offset in (1, 2, 3) or offset > 127:
        raise ExtError('edid-cta', f'block {index}: detailed timings start at the invalid offset {offset}')
    count, pos = 0, offset
    while pos + 18 <= 127 and (block[pos] or block[pos + 1]):
        count, pos = count + 1, pos + 18
    if any(block[pos:127]):
        raise ExtError('edid-cta', f'block {index}: non-zero bytes follow the last detailed timing descriptor')
    if revision >= 2 and (block[3] & 0x0f) > count:
        raise ExtError('edid-cta', f'block {index}: {block[3] & 0x0f} native timings are announced but only {count} exist')
    modes, unknown, partial = [], [], []
    pos = 4
    while revision >= 3 and pos < offset:
        tag, length = block[pos] >> 5, block[pos] & 0x1f
        if pos + 1 + length > offset:
            raise ExtError('edid-cta', f'block {index}: a data block runs past the detailed timings')
        payload = block[pos + 1:pos + 1 + length]
        pos += 1 + length
        if tag == 2:
            if not length:
                raise ExtError('edid-cta', f'block {index}: an empty video data block')
            for byte in payload:
                vic, native = decode_svd(byte)
                mode = _vic_mode(vic, native)
                if mode is None:
                    unknown.append(vic)
                else:
                    modes.append(mode)
        elif tag == 7:
            if not length:
                raise ExtError('edid-cta', f'block {index}: an extended data block without its extended tag')
            _extended_block(payload, index, modes, partial)
    if unknown:
        partial.append(f'video identification codes {sorted(set(unknown))} are not in this tool\'s table')
    return ExtResult(tuple(modes), (), tuple(sorted(set(unknown))), tuple(partial))


def _extended_block(payload, index, modes, partial):
    ext, body = payload[0], payload[1:]
    if ext == CTA_EXT_VTDB_TYPE7:
        if len(body) < 21:
            raise ExtError('edid-cta', f'block {index}: a Type VII video timing data block of {len(body)} bytes')
        if body[0] & 0x70 or body[0] & 7 != 2 or len(body) != 21:
            partial.append(f'a CTA Type VII video timing data block in block {index} uses a layout that is not decoded')
            return
        try:
            mode = _timing_20(body[1:21], 1, 'cta-vtdb', 2)
        except ExtError as error:
            raise ExtError('edid-cta', f'block {index}: {error.message}')
        modes.append(mode)
    elif ext in (CTA_EXT_VTDB_TYPE8, CTA_EXT_VTDB_TYPE10):
        partial.append(f'a CTA extended video timing data block (tag {ext:#04x}) in block {index} is not decoded')


# ---- DisplayID ----------------------------------------------------------------------------------------------------
def parse_displayid_extension(block, index):
    """One EDID extension block that carries a DisplayID section -> ExtResult.

    `decoded` is False, with no error, for a DisplayID version this module does not read. Everything else that is
    inconsistent raises ExtError('edid-displayid')."""
    version = block[1]
    if version not in DISPLAYID_V1 + DISPLAYID_V2:
        return ExtResult(decoded=False, partial=(f'extension block {index} carries DisplayID version byte '
                                                 f'{version:#04x}, which is not decoded',))
    size = block[2]
    end = 5 + size                                      # index of the section checksum
    if end + 1 > 127:
        raise ExtError('edid-displayid', f'block {index}: a section of {size} bytes runs past the extension block')
    if sum(block[1:end + 1]) & 0xff:
        raise ExtError('edid-displayid', f'block {index}: the section checksum is wrong')
    if any(block[end + 1:127]):
        raise ExtError('edid-displayid', f'block {index}: non-zero bytes follow the DisplayID section')
    v2 = version in DISPLAYID_V2
    modes, ranges, partial = [], [], []
    pos = 5
    while pos < end:
        if end - pos < 3:
            raise ExtError('edid-displayid', f'block {index}: a data block header is cut off by the end of the section')
        tag, rev, length = block[pos], block[pos + 1], block[pos + 2]
        if pos + 3 + length > end:
            raise ExtError('edid-displayid', f'block {index}: data block {tag:#04x} runs past the section')
        if not tag and not length:
            raise ExtError('edid-displayid', f'block {index}: an empty data block inside the section')
        payload = block[pos + 3:pos + 3 + length]
        pos += 3 + length
        if v2:
            _displayid2_block(tag, rev, payload, index, modes, ranges, partial)
        else:
            _displayid1_block(tag, rev, payload, index, modes, ranges, partial)
    return ExtResult(tuple(modes), tuple(ranges), (), tuple(partial))


def _timings(payload, size, unit_khz, source, block_rev, index, tag):
    if not payload or len(payload) % size:
        raise ExtError('edid-displayid', f'block {index}: data block {tag:#04x} of {len(payload)} bytes is not a '
                                         f'whole number of {size}-byte timings')
    return [_timing_20(payload[i:i + 20], unit_khz, source, block_rev) for i in range(0, len(payload), size)]


def _displayid1_block(tag, rev, payload, index, modes, ranges, partial):
    if tag == 0x03:
        if rev & 0xf8 or rev & 7 > 1:
            raise ExtError('edid-displayid', f'block {index}: Type I timing block with the revision byte {rev:#04x}')
        modes.extend(_timings(payload, 20, 10, 'displayid1-type1', rev & 7, index, tag))
    elif tag == 0x09:
        if rev or len(payload) != 15:
            raise ExtError('edid-displayid', f'block {index}: a video timing range block of {len(payload)} bytes')
        vmin, vmax = payload[10], payload[11]
        if not 0 < vmin <= vmax:
            raise ExtError('edid-displayid', f'block {index}: the vertical refresh range {vmin}..{vmax} Hz is empty')
        clock_max = ((payload[3] | payload[4] << 8 | payload[5] << 16) + 1) * 10
        clock_min = ((payload[0] | payload[1] << 8 | payload[2] << 16) + 1) * 10
        if clock_min > clock_max:
            raise ExtError('edid-displayid', f'block {index}: the minimum pixel clock is above the maximum')
        ranges.append(RangeLimits('displayid1-range', vmin, vmax, clock_max))
    elif tag in (0x22, 0x24, 0x2a):
        raise ExtError('edid-displayid', f'block {index}: a DisplayID 2.x data block tag {tag:#04x} in a 1.x section')
    elif tag in (0x04, 0x05, 0x06, 0x07, 0x08, 0x11, 0x13):
        partial.append(f'DisplayID 1.x timing data block {tag:#04x} in block {index} is not decoded')


def _displayid2_block(tag, rev, payload, index, modes, ranges, partial):
    if tag == 0x22:
        block_rev, extra = rev & 7, (rev >> 4) & 7
        if rev & 0x80 or block_rev > 2 or (block_rev == 0 and rev & 0x08):
            raise ExtError('edid-displayid', f'block {index}: Type VII timing block with the revision byte {rev:#04x}')
        if extra:
            partial.append(f'a Type VII block in block {index} uses {20 + extra}-byte timings, which are not decoded')
            return
        modes.extend(_timings(payload, 20, 1, 'displayid2-type7', block_rev, index, tag))
    elif tag == 0x25:
        if len(payload) != 9 or rev & 0xf8 or rev & 7 > 1:
            raise ExtError('edid-displayid', f'block {index}: a dynamic video timing range block of {len(payload)} bytes '
                                             f'with the revision byte {rev:#04x}')
        clock_min = (payload[0] | payload[1] << 8 | payload[2] << 16) + 1
        clock_max = (payload[3] | payload[4] << 8 | payload[5] << 16) + 1
        vmin = payload[6]
        vmax = payload[7] + ((payload[8] & 3) << 8 if rev & 7 else 0)
        if (rev & 7 == 0 and payload[8] & 3) or clock_min > clock_max or not 0 < vmin <= vmax:
            raise ExtError('edid-displayid', f'block {index}: an inconsistent dynamic video timing range')
        ranges.append(RangeLimits('displayid2-dynamic', vmin, vmax, clock_max, bool(payload[8] & 0x80)))
    elif tag in (0x03, 0x09):
        raise ExtError('edid-displayid', f'block {index}: a DisplayID 1.x data block tag {tag:#04x} in a 2.x section')
    elif tag in (0x23, 0x24, 0x2a):
        partial.append(f'DisplayID 2.x timing data block {tag:#04x} in block {index} is not decoded')


# ---- Queries ------------------------------------------------------------------------------------------------------
def find_advertised(modes, width, height, refresh_hz, tolerance_hz=Fraction(1, 2)):
    """The progressive modes of exactly width x height whose refresh rate is within tolerance of refresh_hz.

    Only an advertised timing may ever be programmed (docs/SP11-120HZ-STUDY.md section 7), so a caller asks here
    before it uses a mode; an empty answer means "not advertised" only when the mode list had no `partial` reasons."""
    target = Fraction(refresh_hz)
    return [m for m in modes if (m.hactive, m.vactive) == (width, height) and not m.interlaced
            and abs(m.refresh_hz - target) <= tolerance_hz]
