#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""HID report descriptors for the Type Cover's keyboard and touchpad: a bounded parser, a builder, and the descriptor-driven
keyboard model that a kext would have to implement.

EXPERIMENTAL and host-only. The Type Cover's own descriptors are UNKNOWN until the descriptor read of the bring-up
(tools/sam_session.py, steps hidN-report-descriptor) has run on hardware. Until then this module provides: the parser that
turns any descriptor into report layouts (so a descriptor read from the cover can be validated and summarised on the host),
a builder for synthetic descriptors built from the public item encoding (USB HID 1.11, section 6.2.2; the keyboard, bitmap
keyboard and Precision-Touchpad-shaped fixtures here are SYNTHETIC: they are not the cover's), and `KeyboardModel`, which maps
reports to key transitions using only what the descriptor says. The A16 kext's keyboard translation is fixed to one report
layout (drivers/snap-i2c-hid, src/hid-keyboard.h: input report 54, nine bytes), so a cover whose layout differs needs a
descriptor-driven replacement; this is its host oracle. The touchpad side only extracts the Precision Touchpad finger layout
that the kext's existing hid-ptp layer reads from the descriptor.

    python3 tools/sam_hid_desc.py describe <hex or file>
"""
import dataclasses
import sys
from pathlib import Path

sys.dont_write_bytecode = True

MAX_BYTES, MAX_ITEMS, MAX_STACK, MAX_DEPTH, MAX_REPORTS, MAX_FIELDS = 4096, 2048, 16, 32, 64, 512
# Declared sizes are bounded before anything is built from them: a 32-byte descriptor can declare a count or a usage range of
# 2**32, and the expansion below is what would hold memory. The limits sit well above any real HID report (a report is carried
# in one SAM message of at most 4086 bytes).
MAX_COUNT, MAX_FIELD_BITS, MAX_REPORT_BITS, MAX_USAGE_RANGE, MAX_TOTAL_USAGES = 4096, 32768, 32768, 4096, 8192

# Main item tags, global tags and local tags (HID 1.11, 6.2.2.4 to 6.2.2.8).
T_INPUT, T_OUTPUT, T_FEATURE, T_COLLECTION, T_END = 0x8, 0x9, 0xB, 0xA, 0xC
G_USAGE_PAGE, G_LOG_MIN, G_LOG_MAX, G_PHYS_MIN, G_PHYS_MAX, G_EXP, G_UNIT, G_SIZE, G_ID, G_COUNT, G_PUSH, G_POP = range(12)
L_USAGE, L_USAGE_MIN, L_USAGE_MAX = 0, 1, 2
COLL_PHYSICAL, COLL_APPLICATION, COLL_LOGICAL = 0, 1, 2
F_CONST, F_VAR, F_REL = 1, 2, 4


class DescError(Exception):
    """A malformed descriptor. The message names the first failure, as the kext's parser would (hid-rdesc.h, hid_rdesc_result)."""


def item(kind, tag, value=0, signed=False):
    """One short item with the smallest payload that holds value (HID 1.11, 6.2.2.2)."""
    for size, code in ((0, 0), (1, 1), (2, 2), (4, 3)):
        if size == 0:
            if value == 0:
                break
            continue
        lo, hi = (-(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1) if signed else (0, (1 << (8 * size)) - 1)
        if lo <= value <= hi:
            break
    else:
        raise DescError('value does not fit an item')
    return bytes(((tag << 4) | (kind << 2) | code,)) + (value & ((1 << (8 * size)) - 1)).to_bytes(size, 'little')


class Builder:
    def __init__(self):
        self.out = bytearray()

    def _add(self, kind, tag, value=0, signed=False):
        self.out += item(kind, tag, value, signed)
        return self

    def page(self, n): return self._add(1, G_USAGE_PAGE, n)
    def lmin(self, n): return self._add(1, G_LOG_MIN, n, n < 0)
    def lmax(self, n): return self._add(1, G_LOG_MAX, n, n < 0)
    def pmin(self, n): return self._add(1, G_PHYS_MIN, n, n < 0)
    def pmax(self, n): return self._add(1, G_PHYS_MAX, n, n < 0)
    def size(self, n): return self._add(1, G_SIZE, n)
    def count(self, n): return self._add(1, G_COUNT, n)
    def report_id(self, n): return self._add(1, G_ID, n)
    def usage(self, n): return self._add(2, L_USAGE, n)
    def umin(self, n): return self._add(2, L_USAGE_MIN, n)
    def umax(self, n): return self._add(2, L_USAGE_MAX, n)
    def input(self, flags): return self._add(0, T_INPUT, flags)
    def output(self, flags): return self._add(0, T_OUTPUT, flags)
    def feature(self, flags): return self._add(0, T_FEATURE, flags)
    def collection(self, kind): return self._add(0, T_COLLECTION, kind)
    def end(self): return self._add(0, T_END)

    def bytes(self):
        return bytes(self.out)


DATA_VAR_ABS, CONST_ABS, DATA_ARRAY = 0x02, 0x01, 0x00


def boot_keyboard(report_id=0):
    """Eight modifier bits, a reserved byte, a five-LED output report and a six-key array (HID 1.11, appendix B.1 shape). SYNTHETIC."""
    b = Builder().page(1).usage(6).collection(COLL_APPLICATION)
    if report_id:
        b.report_id(report_id)
    b.page(7).umin(0xE0).umax(0xE7).lmin(0).lmax(1).size(1).count(8).input(DATA_VAR_ABS)
    b.count(1).size(8).input(CONST_ABS)
    b.count(5).size(1).page(8).umin(1).umax(5).output(DATA_VAR_ABS)
    b.count(1).size(3).output(CONST_ABS)
    b.count(6).size(8).lmin(0).lmax(101).page(7).umin(0).umax(101).input(DATA_ARRAY)
    return b.end().bytes()


def nkro_keyboard(report_id=1):
    """Modifiers plus a bitmap of usages 4..0xA4 (one bit each): no array, no rollover error. SYNTHETIC."""
    b = Builder().page(1).usage(6).collection(COLL_APPLICATION).report_id(report_id)
    b.page(7).umin(0xE0).umax(0xE7).lmin(0).lmax(1).size(1).count(8).input(DATA_VAR_ABS)
    b.umin(4).umax(0xA4).count(0xA4 - 4 + 1).input(DATA_VAR_ABS)
    b.count(7).input(CONST_ABS)         # pad the 169 bits to a byte boundary
    return b.end().bytes()


def consumer_keys(report_id=2):
    """One 16-bit Consumer usage as an array (0 = none), as in the A16 keyboard's second report. SYNTHETIC."""
    b = Builder().page(0x0C).usage(1).collection(COLL_APPLICATION).report_id(report_id)
    b.lmin(0).lmax(0x3FF).umin(0).umax(0x3FF).size(16).count(1).input(DATA_ARRAY)
    return b.end().bytes()


def ptp_touchpad(fingers=5, x_max=3000, y_max=2000, report_id=1):
    """A Precision-Touchpad-shaped descriptor: per-finger tip, confidence, contact id, X, Y; scan time; contact count; one
    button; and the Device Configuration feature. The collection order follows Microsoft's published requirements; the
    numbers are made up. SYNTHETIC."""
    b = Builder().page(0x0D).usage(0x05).collection(COLL_APPLICATION).report_id(report_id)
    for _ in range(fingers):
        b.usage(0x22).collection(COLL_LOGICAL)
        b.lmin(0).lmax(1).size(1).count(1).usage(0x42).input(DATA_VAR_ABS)          # tip switch
        b.usage(0x47).input(DATA_VAR_ABS)                                              # confidence
        b.size(4).lmax(15).usage(0x51).input(DATA_VAR_ABS)                             # contact id
        b.size(2).count(1).input(CONST_ABS)
        b.page(1).size(16).lmax(x_max).usage(0x30).input(DATA_VAR_ABS)                 # X
        b.lmax(y_max).usage(0x31).input(DATA_VAR_ABS).end()                            # Y
        b.page(0x0D)
    b.size(16).lmax(0xFFFF).usage(0x56).input(DATA_VAR_ABS)                            # scan time
    b.size(8).lmax(fingers).usage(0x54).input(DATA_VAR_ABS)                            # contact count
    b.page(9).umin(1).umax(1).size(1).lmax(1).input(DATA_VAR_ABS)                      # button 1
    b.size(7).count(1).input(CONST_ABS)
    b.page(0x0D).usage(0x0E).collection(COLL_LOGICAL).report_id(report_id + 1)
    b.usage(0x52).lmin(0).lmax(10).size(8).count(1).feature(DATA_VAR_ABS)             # input mode
    b.end().end()
    return b.bytes()


@dataclasses.dataclass
class Field:
    kind: str                 # 'input' | 'output' | 'feature'
    report_id: int
    bit_offset: int           # after the report-id byte
    bit_size: int
    count: int
    flags: int
    page: int
    usages: tuple             # one per element (variable) or the usage range's first and last (array)
    log_min: int
    log_max: int
    path: tuple               # ((collection kind, page, usage), ...) from the outermost collection

    @property
    def is_var(self):
        return bool(self.flags & F_VAR)

    @property
    def is_const(self):
        return bool(self.flags & F_CONST)


@dataclasses.dataclass
class Layout:
    fields: list
    apps: list                # (page, usage) of the top-level application collections
    reports: dict             # (kind, report_id) -> total bits; report_id 0 = unnumbered

    def report_bytes(self, kind, report_id):
        bits = self.reports.get((kind, report_id))
        return None if bits is None else (bits + 7) // 8 + (1 if report_id else 0)


def _signed(value, size):
    return value - (1 << (8 * size)) if size and value >> (8 * size - 1) else value


def parse(desc):
    desc = bytes(desc)
    if not desc:
        raise DescError('empty')
    if len(desc) > MAX_BYTES:
        raise DescError('too long')
    g = {'page': 0, 'lmin': 0, 'lmax': 0, 'size': 0, 'count': 0, 'id': 0}
    stack, coll, apps, fields, reports = [], [], [], [], {}
    local = {'usages': [], 'umin': None, 'umax': None}
    pos, items, total_usages = 0, 0, 0
    numbered = unnumbered = False
    while pos < len(desc):
        items += 1
        if items > MAX_ITEMS:
            raise DescError('too many items')
        prefix = desc[pos]
        pos += 1
        if prefix == 0xFE:
            if pos + 2 > len(desc):
                raise DescError('truncated long item')
            pos += 2 + desc[pos]
            if pos > len(desc):
                raise DescError('truncated long item')
            continue
        size = (0, 1, 2, 4)[prefix & 3]
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if pos + size > len(desc):
            raise DescError('truncated item')
        raw = int.from_bytes(desc[pos:pos + size], 'little')
        pos += size
        if kind == 3:
            raise DescError('reserved item type')
        if kind == 1:
            if tag == G_USAGE_PAGE: g['page'] = raw
            elif tag == G_LOG_MIN: g['lmin'] = _signed(raw, size)
            elif tag == G_LOG_MAX: g['lmax'] = _signed(raw, size) if g['lmin'] < 0 else raw
            elif tag == G_SIZE: g['size'] = raw
            elif tag == G_COUNT: g['count'] = raw
            elif tag == G_ID:
                if not 1 <= raw <= 255:
                    raise DescError('report id out of range')
                g['id'] = raw
            elif tag == G_PUSH:
                if len(stack) >= MAX_STACK:
                    raise DescError('push overflow')
                stack.append(dict(g))
            elif tag == G_POP:
                if not stack:
                    raise DescError('pop without push')
                g = stack.pop()
            elif tag in (G_PHYS_MIN, G_PHYS_MAX, G_EXP, G_UNIT):
                pass
            else:
                raise DescError('unknown global item')
        elif kind == 2:
            if tag == L_USAGE:
                local['usages'].append((g['page'] if size < 4 else raw >> 16, raw & 0xFFFF if size == 4 else raw))
            elif tag == L_USAGE_MIN: local['umin'] = raw
            elif tag == L_USAGE_MAX: local['umax'] = raw
        else:
            if tag == T_COLLECTION:
                if len(coll) >= MAX_DEPTH:
                    raise DescError('collection nesting')
                usage = local['usages'][0] if local['usages'] else (g['page'], 0)
                if not coll:
                    apps.append(usage)
                coll.append((raw, usage[0], usage[1]))
            elif tag == T_END:
                if not coll:
                    raise DescError('unbalanced end collection')
                coll.pop()
            elif tag in (T_INPUT, T_OUTPUT, T_FEATURE):
                if not coll:
                    raise DescError('field outside a collection')
                if not g['size'] or not g['count'] or g['size'] > 32:
                    raise DescError('field without size or count')
                if g['count'] > MAX_COUNT or g['size'] * g['count'] > MAX_FIELD_BITS:
                    raise DescError('field larger than any report')
                if g['id']:
                    numbered = True
                else:
                    unnumbered = True
                if numbered and unnumbered:
                    raise DescError('numbered and unnumbered reports together')
                name = {T_INPUT: 'input', T_OUTPUT: 'output', T_FEATURE: 'feature'}[tag]
                key = (name, g['id'])
                if key not in reports and len(reports) >= MAX_REPORTS:
                    raise DescError('too many reports')
                if len(fields) >= MAX_FIELDS:
                    raise DescError('too many fields')
                offset = reports.get(key, 0)
                if offset + g['size'] * g['count'] > MAX_REPORT_BITS:
                    raise DescError('report larger than any message')
                reports[key] = offset + g['size'] * g['count']
                if local['umin'] is not None and local['umax'] is not None and local['umax'] >= local['umin']:
                    span = local['umax'] - local['umin'] + 1
                    if raw & F_VAR:
                        if span > MAX_USAGE_RANGE:
                            raise DescError('usage range too large')
                        usages = tuple(local['umin'] + i for i in range(min(g['count'], span)))
                    else:
                        usages = (local['umin'], local['umax'])        # an array keeps two usages however wide its range
                else:
                    usages = tuple(u for _, u in local['usages'])
                total_usages += len(usages)
                if total_usages > MAX_TOTAL_USAGES:
                    raise DescError('too many usages')
                fields.append(Field(name, g['id'], offset, g['size'], g['count'], raw, g['page'], usages,
                                    g['lmin'], g['lmax'], tuple(coll)))
            else:
                raise DescError('unknown main item')
            local = {'usages': [], 'umin': None, 'umax': None}
    if coll:
        raise DescError('unclosed collection')
    if not fields:
        raise DescError('no report field')
    return Layout(fields, apps, reports)


def _bits(report, offset_bits, size):
    value = 0
    for i in range(size):
        bit = offset_bits + i
        value |= ((report[bit // 8] >> (bit % 8)) & 1) << i
    return value


class KeyboardModel:
    """Key transitions from reports, using only the descriptor. Output order matches the A16 translation (modifier releases,
    key releases, modifier presses, key presses; Consumer release then press), the order the event system saw work on the A16."""

    def __init__(self, layout):
        self.layout = layout
        self.mods, self.array, self.bitmap, self.consumer, self.ids = {}, {}, {}, {}, set()
        for f in layout.fields:
            if f.kind != 'input' or f.is_const:
                continue
            self.ids.add(f.report_id)
            if f.page == 7 and f.is_var and f.bit_size == 1:
                for i, u in enumerate(f.usages):
                    (self.mods if 0xE0 <= u <= 0xE7 else self.bitmap).setdefault(f.report_id, []).append((f.bit_offset + i, u))
            elif f.page == 7 and not f.is_var:
                self.array.setdefault(f.report_id, []).append(f)
            elif f.page == 0x0C and not f.is_var:
                self.consumer.setdefault(f.report_id, []).append(f)
        if not (self.mods or self.array or self.bitmap):
            raise DescError('no keyboard fields')
        self.held = {}
        self.consumer_held = {}

    def translate(self, report):
        """Returns the transitions, [] when nothing changed, or None for a report this model refuses (wrong length or id,
        or an ErrorRollOver array: usage 1 in any slot means the whole report is unusable)."""
        report = bytes(report)
        rid = report[0] if report and any(k[1] for k in self.layout.reports) else 0
        if rid not in self.ids:
            return None
        want = self.layout.report_bytes('input', rid)
        if len(report) != want:
            return None
        body = report[1:] if rid else report
        now_mods, now_keys, now_consumer = set(), set(), set()
        for off, u in self.mods.get(rid, []):
            if _bits(body, off, 1):
                now_mods.add(u)
        for off, u in self.bitmap.get(rid, []):
            if _bits(body, off, 1):
                now_keys.add(u)
        for f in self.array.get(rid, []):
            first = f.usages[0] if f.usages else 0
            for i in range(f.count):
                value = _bits(body, f.bit_offset + i * f.bit_size, f.bit_size)
                if not f.log_min <= value <= f.log_max:
                    continue
                u = first + value - f.log_min
                if u == 1:
                    return None
                if u > 3:
                    now_keys.add(u)
        for f in self.consumer.get(rid, []):
            first = f.usages[0] if f.usages else 0
            for i in range(f.count):
                value = _bits(body, f.bit_offset + i * f.bit_size, f.bit_size)
                if f.log_min <= value <= f.log_max and first + value - f.log_min:
                    now_consumer.add(first + value - f.log_min)
        out = []
        out += [(7, u, 0) for u in sorted(self.held.get('mods', set()) - now_mods)]
        out += [(7, u, 0) for u in sorted(self.held.get('keys', set()) - now_keys)]
        out += [(7, u, 1) for u in sorted(now_mods - self.held.get('mods', set()))]
        out += [(7, u, 1) for u in sorted(now_keys - self.held.get('keys', set()))]
        out += [(0x0C, u, 0) for u in sorted(self.consumer_held.get(rid, set()) - now_consumer)]
        out += [(0x0C, u, 1) for u in sorted(now_consumer - self.consumer_held.get(rid, set()))]
        if self.mods.get(rid) or self.array.get(rid) or self.bitmap.get(rid):
            self.held['mods'], self.held['keys'] = now_mods, now_keys
        if self.consumer.get(rid):
            self.consumer_held[rid] = now_consumer
        return out


def pack(layout, kind, report_id, values):
    """Builds a report: values is {(field index within the report's fields of this kind, element): integer}. A report
    generator for tests; the layout decides every offset."""
    bits = layout.reports[(kind, report_id)]
    body = bytearray((bits + 7) // 8)
    fields = [f for f in layout.fields if f.kind == kind and f.report_id == report_id]
    for (fi, el), value in values.items():
        f = fields[fi]
        if not 0 <= el < f.count or not 0 <= value < (1 << f.bit_size):
            raise DescError('value does not fit the field')
        for i in range(f.bit_size):
            bit = f.bit_offset + el * f.bit_size + i
            body[bit // 8] |= ((value >> i) & 1) << (bit % 8)
    return (bytes((report_id,)) if report_id else b'') + bytes(body)


@dataclasses.dataclass
class PtpLayout:
    report_id: int
    fingers: list             # dicts of Field per finger: tip, confidence, id, x, y
    contact_count: Field
    scan_time: Field
    button: Field
    input_mode: Field


def ptp_layout(layout):
    """The Precision Touchpad fields the kext's hid-ptp layer reads from the descriptor; raises when one is missing."""
    want = {0x42: 'tip', 0x47: 'confidence', 0x51: 'id', 0x30: 'x', 0x31: 'y'}
    fingers, current, tail = [], {}, {}
    for f in layout.fields:
        usage = f.usages[0] if f.usages else None
        path = [p for p in f.path if p[0] == COLL_LOGICAL]
        if f.kind == 'input' and path and path[0][2] == 0x22:
            name = want.get(usage)
            if name is None:
                continue
            if name in current:                     # the same usage again: the next finger collection began
                fingers.append(current)
                current = {}
            current[name] = f
        elif f.kind == 'input' and f.page == 0x0D and usage in (0x54, 0x56):
            tail['contact_count' if usage == 0x54 else 'scan_time'] = f
        elif f.kind == 'input' and f.page == 9:
            tail['button'] = f
        elif f.kind == 'feature' and usage == 0x52:
            tail['input_mode'] = f
    if current:
        fingers.append(current)
    rows = [r for r in fingers if {'tip', 'id', 'x', 'y'} <= set(r)]
    if not rows or {'contact_count', 'scan_time', 'button', 'input_mode'} - set(tail):
        raise DescError('not a Precision Touchpad layout')
    return PtpLayout(rows[0]['tip'].report_id, rows, tail['contact_count'], tail['scan_time'], tail['button'], tail['input_mode'])


def describe(desc):
    lay = parse(desc)
    lines = [f'applications: {[(hex(p), hex(u)) for p, u in lay.apps]}']
    for (kind, rid), bits in sorted(lay.reports.items()):
        lines.append(f'{kind} report id {rid}: {lay.report_bytes(kind, rid)} bytes ({bits} bits)')
    return '\n'.join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != 'describe':
        print(__doc__)
        return 2
    arg = argv[1]
    text = Path(arg).read_text() if Path(arg).exists() else arg
    print(describe(bytes.fromhex(''.join(text.split()))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
