#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate the SP11 panel mode from a GOP report and an optional EDID. The tool assumes no panel size.

Why it exists. The plan takes the panel mode "from GOP/EDID, not assumed" (docs/SURFACE-PRO-11.md, WS-E P1, gate X5)
and the repository's documents disagree about the size, so nothing here knows a resolution: every number comes from
the two inputs, and an operator who does have an expectation passes it explicitly with --expect-size.

Inputs.
  --gop FILE        a UEFI GOP mode report, either a JSON object {"width", "height", "stride_pixels", "format" and
                    optionally "base", "size"} or the `key=value` summary.txt of the EFI state probe (probes/uefi,
                    keys gop_width, gop_height, gop_stride_pixels, gop_format, gop_base, gop_size). `format` is the
                    UEFI EFI_GRAPHICS_PIXEL_FORMAT number (0 RGBX, 1 BGRX, 2 bit mask, 3 blt only) or its name.
                    Only the two 32-bit linear formats are usable by a plain framebuffer console.
  --edid FILE       a binary EDID: a 128-byte base block plus the extension blocks its byte 126 announces. Each
                    block's checksum is verified. The base block's detailed timing descriptors, a CTA-861 extension's
                    (descriptors, video data blocks) and a DisplayID 1.3 or 2.x extension's timings are decoded
                    (tools/sp11_edid_ext.py); other extension types are checksummed and reported, not decoded.
                    In this flow the decoded extension blocks only add information: a malformed one is listed under
                    `edid.partial` in the output and never changes the verdict, which rests on the base block's
                    preferred timing and the GOP size. The `modes` subcommand, by contrast, refuses a malformed block.
  --edid-hex HEX    the same bytes as hexadecimal text.
  --expect-size WxH an operator-supplied expectation, refused if it differs from the GOP mode.

Verdict. ACCEPT only when the GOP report is well formed and, if an EDID was given, the EDID is well formed, declares
a preferred timing, and that timing's active size equals the GOP size. Anything malformed, any mismatch and an EDID
that cannot confirm the GOP all give REJECT with the reasons. Exit status: 0 ACCEPT, 1 REJECT, 2 unusable
invocation or unreadable file. Nothing is ever written.

Evidence. VERIFIED by tests: the decoding rules below, against known public timings (CTA-861 VIC 4 and VIC 16).
Layouts follow the public VESA E-EDID 1.4 base block and the CTA-861 extension structure (field positions are facts;
no text is copied) and the UEFI Specification 2.10 GOP mode information. INFERRED: that the SP11 firmware reports a
GOP mode and an EDID of this shape. UNKNOWN until the unit is read: the actual mode.

Two more subcommands serve the 120 Hz stretch goal (docs/SP11-120HZ-STUDY.md); neither reads a GOP report:
  modes        list every timing an EDID advertises, from the base block, CTA-861 and DisplayID extensions, with its
               refresh rate and whether it exceeds the 655.35 MHz an 18-byte descriptor can hold. --require WxH@HZ
               exits 1 unless that progressive timing is advertised (and says UNKNOWN, not "absent", when a block
               could not be decoded). Only an advertised timing may ever be programmed.
  link-budget  does a timing fit a DisplayPort or eDP link at a given lane count, rate and depth, with or without DSC?
               A labelled model (tools/sp11_link_budget.py), never a measurement.

    python3 tools/sp11_display_mode.py --gop summary.txt --edid panel.edid
    python3 tools/sp11_display_mode.py modes --edid panel.edid --require WxH@HZ
    python3 tools/sp11_display_mode.py link-budget --h-active W --v-active H --refresh HZ --bpc 8 --lanes 4 --rate HBR3
"""
from dataclasses import dataclass
import argparse
import json
import os
import re
import stat
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:               # the sibling modules; this file is also loaded by path (tests, other tools)
    sys.path.insert(0, _HERE)
import sp11_edid_ext as edid_ext        # noqa: E402
import sp11_link_budget as budget       # noqa: E402

MAX_DIMENSION = 16384               # a larger width or height is a corrupt report, not a panel
MAX_TEXT_BYTES = 1 << 16
MAX_EDID_BLOCKS = 256               # byte 126 is 8 bits wide: at most 255 extensions after the base block
BYTES_PER_PIXEL = 4
GOP_FORMATS = {0: 'RGBX', 1: 'BGRX', 2: 'BITMASK', 3: 'BLT_ONLY'}
LINEAR_FORMATS = ('RGBX', 'BGRX')
REFRESH_HZ_RANGE = (20.0, 500.0)    # a preferred timing outside this is a corrupt descriptor
EDID_HEADER = bytes([0, 255, 255, 255, 255, 255, 255, 0])
DTD_OFFSETS = (0x36, 0x48, 0x5a, 0x6c)
GOP_KEYS = ('width', 'height', 'stride_pixels', 'format', 'base', 'size')
SUMMARY_KEYS = {'gop_width': 'width', 'gop_height': 'height', 'gop_stride_pixels': 'stride_pixels',
                'gop_format': 'format', 'gop_base': 'base', 'gop_size': 'size'}


class ModeError(ValueError):
    """A refusal with a stable machine-readable code (the part before the colon)."""

    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code, self.message = code, message


# ---- GOP ----------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Gop:
    width: int
    height: int
    stride_pixels: int
    format: str
    base: int = None
    size: int = None

    @property
    def stride_bytes(self):
        return self.stride_pixels * BYTES_PER_PIXEL


def _integer(value, name, minimum, maximum):
    """A non-negative integer given as an int or a decimal or 0x string; booleans, floats and None are refused."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ModeError('gop-field', f'{name} must be an integer, not {type(value).__name__}')
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r'0[xX][0-9a-fA-F]+|[0-9]+', text):
            raise ModeError('gop-field', f'{name} is not a number: {value!r}')
        value = int(text, 0) if text[:2].lower() == '0x' else int(text, 10)
    if not minimum <= value <= maximum:
        raise ModeError('gop-field', f'{name} {value} is outside {minimum}..{maximum}')
    return value


def _format(value):
    if isinstance(value, str) and not re.fullmatch(r'0[xX][0-9a-fA-F]+|[0-9]+', value.strip()):
        name = value.strip().upper()
        if name not in GOP_FORMATS.values():
            raise ModeError('gop-format', f'unknown pixel format {value!r}')
        return name
    number = _integer(value, 'format', 0, 0xffffffff)
    if number not in GOP_FORMATS:
        raise ModeError('gop-format', f'unknown pixel format number {number}')
    return GOP_FORMATS[number]


def parse_gop(fields):
    """A dict of GOP fields (see the module docstring) -> Gop, validated. Raises ModeError."""
    if not isinstance(fields, dict):
        raise ModeError('gop-field', 'a GOP report must be an object')
    unknown = sorted(set(fields) - set(GOP_KEYS))
    if unknown:
        raise ModeError('gop-field', f'unknown GOP field(s): {", ".join(map(str, unknown))}')
    missing = [k for k in ('width', 'height', 'stride_pixels', 'format') if k not in fields]
    if missing:
        raise ModeError('gop-field', f'missing GOP field(s): {", ".join(missing)}')
    width = _integer(fields['width'], 'width', 1, MAX_DIMENSION)
    height = _integer(fields['height'], 'height', 1, MAX_DIMENSION)
    stride = _integer(fields['stride_pixels'], 'stride_pixels', 1, 4 * MAX_DIMENSION)
    fmt = _format(fields['format'])
    if stride < width:
        raise ModeError('gop-stride', f'a scan line of {stride} pixels cannot hold a width of {width}')
    if fmt not in LINEAR_FORMATS:
        raise ModeError('gop-format', f'{fmt} has no 32-bit linear framebuffer a console can draw into')
    base = _integer(fields['base'], 'base', 1, (1 << 64) - 1) if 'base' in fields else None
    size = _integer(fields['size'], 'size', 1, (1 << 64) - 1) if 'size' in fields else None
    if size is not None and size < stride * BYTES_PER_PIXEL * height:
        raise ModeError('gop-size', f'the framebuffer is {size} bytes but {stride}x{height} pixels need '
                                    f'{stride * BYTES_PER_PIXEL * height}')
    if base is not None and size is not None and base + size > 1 << 64:
        raise ModeError('gop-size', 'the framebuffer wraps the address space')
    return Gop(width, height, stride, fmt, base, size)


def parse_gop_text(text):
    """A GOP report as JSON, or the probe's `key=value` summary -> Gop. Raises ModeError."""
    stripped = text.lstrip()
    if stripped.startswith('{'):
        try:
            fields = json.loads(text, object_pairs_hook=_no_duplicate_keys)
        except (ValueError, RecursionError) as error:
            raise ModeError('gop-syntax', f'the GOP report is not valid JSON: {error}')
        return parse_gop(fields)
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep:
            raise ModeError('gop-syntax', f'not a key=value line: {line[:40]!r}')
        key = key.strip()
        if key == 'gop' and value.strip() == 'none':
            raise ModeError('gop-absent', 'the firmware reported no usable GOP')
        if key in SUMMARY_KEYS:
            if SUMMARY_KEYS[key] in fields:
                raise ModeError('gop-syntax', f'{key} appears twice')
            fields[SUMMARY_KEYS[key]] = value.strip()
    if not fields:
        raise ModeError('gop-absent', 'no gop_* keys and no JSON object in the report')
    return parse_gop(fields)


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f'duplicate key {key!r}')
        out[key] = value
    return out


# ---- EDID ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Dtd:
    pixel_clock_khz: int
    hactive: int
    hblank: int
    hfront: int
    hsync: int
    vactive: int
    vblank: int
    vfront: int
    vsync: int
    hsize_mm: int
    vsize_mm: int
    interlaced: bool

    @property
    def htotal(self):
        return self.hactive + self.hblank

    @property
    def vtotal(self):
        return self.vactive + self.vblank

    @property
    def refresh_hz(self):
        return self.pixel_clock_khz * 1000.0 / (self.htotal * self.vtotal)


@dataclass(frozen=True)
class Edid:
    version: int
    revision: int
    manufacturer: str
    product_code: int
    preferred: Dtd
    timings: tuple
    extensions: tuple               # ((tag, decoded?), ...), one per extension block
    preferred_declared: bool
    modes: tuple = ()               # every advertised timing, with its source (tools/sp11_edid_ext.py)
    ranges: tuple = ()              # refresh-rate ranges the EDID advertises
    partial: tuple = ()             # why `modes` may be incomplete; empty means every block was decoded
    unknown_vics: tuple = ()


def parse_dtd(b):
    """An 18-byte detailed timing descriptor whose pixel clock is non-zero -> Dtd. Raises ModeError('edid-dtd')."""
    if len(b) != 18:
        raise ModeError('edid-dtd', 'a detailed timing descriptor is 18 bytes')
    clock = b[0] | b[1] << 8
    d = Dtd(pixel_clock_khz=clock * 10,
            hactive=b[2] | (b[4] & 0xf0) << 4, hblank=b[3] | (b[4] & 0x0f) << 8,
            vactive=b[5] | (b[7] & 0xf0) << 4, vblank=b[6] | (b[7] & 0x0f) << 8,
            hfront=b[8] | (b[11] & 0xc0) << 2, hsync=b[9] | (b[11] & 0x30) << 4,
            vfront=(b[10] >> 4) | (b[11] & 0x0c) << 2, vsync=(b[10] & 0x0f) | (b[11] & 0x03) << 4,
            hsize_mm=b[12] | (b[14] & 0xf0) << 4, vsize_mm=b[13] | (b[14] & 0x0f) << 8,
            interlaced=bool(b[17] & 0x80))
    if not clock:
        raise ModeError('edid-dtd', 'a timing descriptor with a zero pixel clock')
    if not d.hactive or not d.vactive:
        raise ModeError('edid-dtd', 'a timing descriptor with an empty active area')
    if d.hfront + d.hsync > d.hblank or d.vfront + d.vsync > d.vblank:
        raise ModeError('edid-dtd', 'the sync pulse and front porch do not fit in the blanking interval')
    if not REFRESH_HZ_RANGE[0] <= d.refresh_hz <= REFRESH_HZ_RANGE[1]:
        raise ModeError('edid-dtd', f'the timing implies a refresh rate of {d.refresh_hz:.2f} Hz')
    return d


def _check_block(block, index):
    if sum(block) & 0xff:
        raise ModeError('edid-checksum', f'block {index} does not sum to zero modulo 256')


def _manufacturer(word):
    letters = ((word >> 10) & 31, (word >> 5) & 31, word & 31)
    if word & 0x8000 or not all(1 <= x <= 26 for x in letters):
        raise ModeError('edid-base', f'the manufacturer id {word:#06x} is not three letters')
    return ''.join(chr(64 + x) for x in letters)


def _descriptors(block):
    """The four base-block descriptors: Dtd for a timing, None for a display descriptor."""
    out = []
    for off in DTD_OFFSETS:
        raw = block[off:off + 18]
        if raw[0] or raw[1]:
            out.append(parse_dtd(raw))
        elif raw[2]:
            raise ModeError('edid-base', f'the display descriptor at {off:#x} has a non-zero reserved byte')
        else:
            out.append(None)
    return out


def _cta_timings(block, index):
    """Decode a CTA-861 extension's detailed timings; the data blocks are only checked for tiling."""
    offset = block[2]
    if offset in (1, 2, 3) or offset > 127:
        raise ModeError('edid-cta', f'block {index}: detailed timings start at the invalid offset {offset}')
    if block[1] < 1:
        raise ModeError('edid-cta', f'block {index}: CTA revision 0')
    if not offset:
        return []
    pos = 4
    while pos < offset:                         # data blocks: tag 7:5, length 4:0, then the payload
        end = pos + 1 + (block[pos] & 0x1f)
        if end > offset:
            raise ModeError('edid-cta', f'block {index}: a data block runs past the detailed timings')
        pos = end
    out = []
    while offset + 18 <= 127 and (block[offset] or block[offset + 1]):
        out.append(parse_dtd(block[offset:offset + 18]))
        offset += 18
    return out


def _extension_decode(function, *args, note=None):
    """Run a tools/sp11_edid_ext.py decoder and re-raise its refusals as this module's ModeError.

    With `note` (a list, passed only by the GOP confirmation) a refusal is recorded there as `code: message` and None is
    returned instead: that flow needs only the base-block timings, so a malformed extension block must not become a new
    reason to refuse a mode the same EDID confirmed before the extension decoders existed."""
    try:
        return function(*args)
    except edid_ext.ExtError as error:
        if note is None:
            raise ModeError(error.code, error.message)
        note.append(f'{error.code}: {error.message}')
        return None


def parse_edid(data, lenient_extensions=False):
    """A complete binary EDID -> Edid. Raises ModeError with an `edid-*` code for anything malformed.

    The base block and the CTA-861 timing structure are always strict. `lenient_extensions` (the GOP confirmation)
    only changes what happens when the 120 Hz decoders of tools/sp11_edid_ext.py refuse a block, a CTA data block, a
    DisplayID block or the 0xFD descriptor: the reason goes into `Edid.partial` and the block contributes no modes."""
    data = bytes(data)
    if len(data) < 128 or len(data) % 128:
        raise ModeError('edid-length', f'{len(data)} bytes is not a whole number of 128-byte blocks')
    if len(data) // 128 > MAX_EDID_BLOCKS:
        raise ModeError('edid-length', 'more blocks than an EDID can announce')
    if data[:8] != EDID_HEADER:
        raise ModeError('edid-header', 'the base block does not start with the EDID header pattern')
    for i in range(len(data) // 128):
        _check_block(data[128 * i:128 * i + 128], i)
    base = data[:128]
    version, revision = base[0x12], base[0x13]
    if version != 1 or revision not in (3, 4):
        raise ModeError('edid-version', f'EDID {version}.{revision} is not 1.3 or 1.4')
    if len(data) != 128 * (1 + base[0x7e]):
        raise ModeError('edid-length', f'byte 126 announces {base[0x7e]} extension(s) but {len(data)} bytes were given')
    manufacturer = _manufacturer(base[8] << 8 | base[9])
    declared = bool(base[0x18] & 0x02)
    descriptors = _descriptors(base)
    timings = [d for d in descriptors if d]
    modes = [edid_ext.mode_from_dtd('base-dtd', declared and k == 0, d.hactive, d.vactive, d.htotal, d.vtotal,
                                    d.pixel_clock_khz, d.interlaced) for k, d in enumerate(descriptors) if d]
    partial, unknown_vics, extensions = list(edid_ext.base_partial_reasons(base)), [], []
    note = partial if lenient_extensions else None
    ranges = list(_extension_decode(edid_ext.parse_base_range_limits, base, base[0x13], note=note) or ())
    for i in range(1, len(data) // 128):
        block = data[128 * i:128 * i + 128]
        result, decoded = edid_ext.ExtResult(), block[0] == 0x02
        if block[0] == 0x02:
            cta = _cta_timings(block, i)
            timings += cta
            modes += [edid_ext.mode_from_dtd('cta-dtd', False, d.hactive, d.vactive, d.htotal, d.vtotal,
                                             d.pixel_clock_khz, d.interlaced) for d in cta]
            result = _extension_decode(edid_ext.parse_cta_extension, block, i, note=note)
        elif block[0] == edid_ext.DISPLAYID_EXT_TAG:
            result = _extension_decode(edid_ext.parse_displayid_extension, block, i, note=note)
            decoded = result is not None and result.decoded
        else:
            partial.append(f'extension block {i} has the tag {block[0]:#04x}, which is not decoded')
        if result is None:
            result, decoded = edid_ext.ExtResult(), False
        modes += result.modes
        ranges += result.ranges
        partial += result.partial
        unknown_vics += result.unknown_vics
        extensions.append((block[0], decoded))
    preferred = None
    if declared:
        if not descriptors[0]:
            raise ModeError('edid-base', 'a preferred timing is declared but the first descriptor is not a timing')
        preferred = descriptors[0]
    return Edid(version, revision, manufacturer, base[10] | base[11] << 8, preferred, tuple(timings),
                tuple(extensions), declared, tuple(modes), tuple(ranges), tuple(partial), tuple(sorted(set(unknown_vics))))


# ---- Decision -----------------------------------------------------------------------------------------------------
def evaluate(gop_text, edid_bytes=None, expect_size=None):
    """Validate the inputs and cross-check them. Returns a JSON-serialisable dict; never raises ModeError."""
    result = dict(verdict='ACCEPT', reasons=[], messages=[], gop=None, edid=None, cross_check='not-performed',
                  expect_size=None, mode=None, warnings=[])
    gop = edid = None

    def refuse(error):
        result['reasons'].append(error.code)
        result['messages'].append(error.message)

    try:
        gop = parse_gop_text(gop_text)
        result['gop'] = dict(width=gop.width, height=gop.height, stride_pixels=gop.stride_pixels, format=gop.format,
                             base=gop.base, size=gop.size)
    except ModeError as error:
        refuse(error)
    if edid_bytes is not None:
        try:
            edid = parse_edid(edid_bytes, lenient_extensions=True)
            p = edid.preferred
            result['edid'] = dict(version=f'{edid.version}.{edid.revision}', manufacturer=edid.manufacturer,
                                  product_code=edid.product_code, extension_tags=[t for t, _ in edid.extensions],
                                  preferred=None if p is None else dict(
                                      width=p.hactive, height=p.vactive, refresh_hz=round(p.refresh_hz, 3),
                                      pixel_clock_khz=p.pixel_clock_khz, interlaced=p.interlaced,
                                      size_mm=[p.hsize_mm, p.vsize_mm]),
                                  timings=[[t.hactive, t.vactive, round(t.refresh_hz, 3)] for t in edid.timings])
            result['edid'].update(modes=[m.as_dict() for m in edid.modes],
                                  range_limits=[r.as_dict() for r in edid.ranges], modes_complete=not edid.partial,
                                  partial=list(edid.partial))
            if p is None:
                refuse(ModeError('edid-no-preferred', 'the EDID declares no preferred timing, so it cannot confirm the GOP mode'))
            elif p.interlaced:
                refuse(ModeError('edid-interlaced', 'the preferred timing is interlaced'))
        except ModeError as error:
            refuse(error)
    if gop and edid and edid.preferred and not edid.preferred.interlaced:
        p = edid.preferred
        if (gop.width, gop.height) == (p.hactive, p.vactive):
            result['cross_check'] = 'match'
        else:
            result['cross_check'] = 'mismatch'
            others = [i for i, t in enumerate(edid.timings) if (t.hactive, t.vactive) == (gop.width, gop.height)]
            note = f' (it equals timing #{others[0]} of the EDID)' if others else ''
            refuse(ModeError('mode-mismatch', f'the GOP mode is {gop.width}x{gop.height} but the EDID preferred timing is '
                                              f'{p.hactive}x{p.vactive}{note}'))
    if gop and edid_bytes is None:
        result['warnings'].append('no EDID was supplied: the GOP mode is unconfirmed by the panel')
    if gop and expect_size is not None:
        result['expect_size'] = list(expect_size)
        if tuple(expect_size) != (gop.width, gop.height):
            refuse(ModeError('expect-mismatch', f'the operator expected {expect_size[0]}x{expect_size[1]} but the GOP mode '
                                                f'is {gop.width}x{gop.height}'))
    if result['reasons']:
        result['verdict'] = 'REJECT'
    elif gop:
        result['mode'] = dict(width=gop.width, height=gop.height, stride_pixels=gop.stride_pixels,
                              stride_bytes=gop.stride_bytes, format=gop.format, bytes_per_pixel=BYTES_PER_PIXEL)
    return result


# ---- Command line -------------------------------------------------------------------------------------------------
def read_regular_file(path, cap):
    """The bytes of a regular file of at most `cap` bytes. Never writes; refuses devices, directories and links.

    The path is inspected before it is opened: opening a device node or a terminal can act on the device, so only a
    regular file that is not itself a link is opened, and the descriptor must still be the file that was inspected.
    """
    listed = os.lstat(path)
    if not stat.S_ISREG(listed.st_mode):
        raise OSError('not a regular file')
    if listed.st_size > cap:
        raise OSError(f'larger than {cap} bytes')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):
            raise OSError('not a regular file')
        data = os.read(fd, cap + 1)
        while len(data) <= cap:
            more = os.read(fd, cap + 1 - len(data))
            if not more:
                break
            data += more
    finally:
        os.close(fd)
    if len(data) > cap:
        raise OSError(f'larger than {cap} bytes')
    return data


def _size(text):
    match = re.fullmatch(r'([0-9]{1,5})x([0-9]{1,5})', text)
    if not match:
        raise argparse.ArgumentTypeError('expected WIDTHxHEIGHT')
    return int(match.group(1)), int(match.group(2))


def _edid_bytes(args):
    """The EDID bytes named by --edid or --edid-hex, or None if neither was given. Raises OSError or ValueError.

    Every path an operator names is read through read_regular_file, never opened directly."""
    # `is not None`, not truthiness: --edid '' names a file that cannot be opened, which must stop the run
    # instead of reading as "no EDID" and accepting the GOP mode unconfirmed.
    if args.edid is not None:
        return read_regular_file(args.edid, 128 * MAX_EDID_BLOCKS)
    if args.edid_hex is not None:
        return bytes.fromhex(re.sub(r'\s+', '', args.edid_hex))
    return None


def _requirement(text):
    match = re.fullmatch(r'([0-9]{1,5})x([0-9]{1,5})@([0-9]{1,4}(?:\.[0-9]{1,3})?)', text)
    if not match:
        raise argparse.ArgumentTypeError('expected WIDTHxHEIGHT@HZ')
    return int(match.group(1)), int(match.group(2)), match.group(3)


def main_modes(argv):
    """`modes`: list every timing an EDID advertises, with its refresh rate and whether it exceeds the base-block limit.

    Exit 0: the EDID is well formed (and, with --require, advertises the timing); 1: it is malformed, or the timing is
    not advertised, or the decode was partial so absence cannot be concluded; 2: an unusable invocation."""
    ap = argparse.ArgumentParser(prog='sp11_display_mode.py modes', description=main_modes.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--edid', metavar='FILE')
    source.add_argument('--edid-hex', metavar='HEX')
    ap.add_argument('--require', type=_requirement, metavar='WxH@HZ',
                    help='the progressive timing that must be advertised (size exactly, refresh within --tolerance)')
    ap.add_argument('--tolerance', default='0.5', metavar='HZ')
    args = ap.parse_args(argv)
    try:
        data = _edid_bytes(args)
        tolerance = budget.rational(args.tolerance, 'tolerance', 0, 100)
    except (OSError, ValueError, budget.BudgetError) as error:
        print(f'sp11_display_mode: {error}', file=sys.stderr)
        return 2
    try:
        edid = parse_edid(data)
    except ModeError as error:
        print(json.dumps(dict(verdict='REJECT', reasons=[error.code], messages=[error.message]), indent=2, sort_keys=True))
        return 1
    result = dict(verdict='OK', modes=[m.as_dict() for m in edid.modes], range_limits=[r.as_dict() for r in edid.ranges],
                  modes_complete=not edid.partial, partial=list(edid.partial), unknown_vics=list(edid.unknown_vics))
    if args.require:
        width, height, hz = args.require
        found = edid_ext.find_advertised(edid.modes, width, height, hz, tolerance)
        result['require'] = dict(width=width, height=height, refresh_hz=hz, tolerance_hz=args.tolerance,
                                 found=[m.as_dict() for m in found])
        if not found:
            result['verdict'] = 'UNKNOWN' if edid.partial else 'NOT-ADVERTISED'
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['verdict'] == 'OK' else 1


def main_budget(argv):
    """`link-budget`: does a video timing fit a DisplayPort or eDP main link? A model; see tools/sp11_link_budget.py.

    Give the active size, a refresh rate and either the CVT reduced-blanking assumption (the default, labelled in the
    output) or explicit blanking, then the bits per component, lane count, link rate and optionally a DSC target
    bits-per-pixel. --matrix prints every rate, lane count and depth for the timing instead.
    Exit 0: it fits; 1: it does not; 2: an unusable input (zero lanes, an unknown rate, negative blanking ...)."""
    ap = argparse.ArgumentParser(prog='sp11_display_mode.py link-budget', description=main_budget.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--h-active', required=True, metavar='PIXELS')
    ap.add_argument('--v-active', required=True, metavar='LINES')
    ap.add_argument('--refresh', metavar='HZ')
    ap.add_argument('--pixel-clock-khz', metavar='KHZ', help='instead of --refresh, with explicit blanking')
    ap.add_argument('--blanking', choices=('cvt-rb', 'explicit'), default='cvt-rb')
    ap.add_argument('--h-blank', metavar='PIXELS')
    ap.add_argument('--v-blank', metavar='LINES')
    ap.add_argument('--bpc', metavar='BITS')
    ap.add_argument('--lanes', metavar='N')
    ap.add_argument('--rate', metavar='NAME', help='RBR, HBR, HBR2, HBR3 or an eDP 1.4 rate such as EDP-2.43')
    ap.add_argument('--dsc-bpp', metavar='BPP')
    ap.add_argument('--matrix', action='store_true')
    args = ap.parse_args(argv)
    try:
        if args.blanking == 'cvt-rb':
            if args.refresh is None or args.pixel_clock_khz is not None or args.h_blank is not None \
                    or args.v_blank is not None:
                raise budget.BudgetError('budget-input', 'the cvt-rb assumption takes --refresh and no explicit blanking '
                                                         'or pixel clock')
            timing = budget.cvt_rb_timing(args.h_active, args.v_active, args.refresh)
        else:
            timing = budget.explicit_timing(args.h_active, args.v_active, args.h_blank, args.v_blank, args.refresh,
                                            args.pixel_clock_khz)
        if args.matrix:
            print(json.dumps(dict(timing=dict(hactive=timing.hactive, vactive=timing.vactive, htotal=timing.htotal,
                                              vtotal=timing.vtotal, pixel_clock_khz=timing.pixel_clock_khz,
                                              blanking=timing.assumption),
                                  rows=budget.feasibility_matrix(timing)), indent=2, sort_keys=True))
            return 0
        if args.bpc is None or args.lanes is None or args.rate is None:
            raise budget.BudgetError('budget-input', '--bpc, --lanes and --rate are required (no defaults are assumed)')
        result = budget.link_budget(timing, args.bpc, args.lanes, args.rate, args.dsc_bpp)
    except budget.BudgetError as error:
        print(f'sp11_display_mode: {error}', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['fits'] else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == 'modes':
        return main_modes(argv[1:])
    if argv and argv[0] == 'link-budget':
        return main_budget(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--gop', required=True, metavar='FILE')
    edid = ap.add_mutually_exclusive_group()
    edid.add_argument('--edid', metavar='FILE')
    edid.add_argument('--edid-hex', metavar='HEX')
    ap.add_argument('--expect-size', type=_size, metavar='WxH')
    args = ap.parse_args(argv)
    try:
        gop_text = read_regular_file(args.gop, MAX_TEXT_BYTES).decode('utf-8')
        edid_bytes = _edid_bytes(args)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print(f'sp11_display_mode: {error}', file=sys.stderr)
        return 2
    result = evaluate(gop_text, edid_bytes, args.expect_size)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['verdict'] == 'ACCEPT' else 1


if __name__ == '__main__':
    sys.exit(main())
