# SPDX-License-Identifier: MIT
"""The chunks of GUEST.DAT (docs/SP11-STICK-BUILD.md): what probes/xnuboot.py build() returns, classified into the interface's kinds,
flattened to the memory the A16 loader's upload leaves, padded, split into ZERO chunks and packed with tools/sp11_stick_bundle.py; a
deterministic synthetic `built` of the same shape, so that a bundle can be rehearsed without Apple material; and real_built(), which calls
build() on a kernelcache with probes/run.py's sequence.

Evidence: EXPERIMENTAL, hardware NOT RUN. build() ran on the J700 26A428 kernelcache, host only (docs/SP11-STICK-REAL.md): the shape of its
return value, read from its code (probes/xnuboot.py, `built = {...}` and the chunks it fills), held. Anything unexpected is refused with
BUILT_SHAPE rather than guessed at."""
import bisect
import contextlib
import dataclasses
import hashlib
import io
import os
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle as sb  # noqa: E402

PARAMS_WORDS, PARAMS_WATCHDOG, PARAMS_MEM_LOW, PARAMS_MEM_HIGH = 1154, 24, 30, 31    # word indexes in struct params (probes/xnuboot/report_layout.c)
AFFINITY = 0xFF00FFFFFF     # Aff3:Aff2:Aff1:Aff0 of MPIDR_EL1, the part ACPI states
ZERO_MIN = 0x10000      # a shorter zero stretch stays data: every ZERO chunk is a table entry, and a table holds 1024
KNOWN_REFUSALS = {n for _, n, _ in sb.ERRORS} | {'BUILT_SHAPE', 'PARAMS_MISMATCH', 'TIME_BOX_MISMATCH', 'ENV_SNAP_SET', 'KERNELCACHE_MISSING'}   # the builder adds its own


class Refused(Exception):
    def __init__(self, name, detail=''):
        assert name in KNOWN_REFUSALS, name          # a typo must not become a new refusal
        super().__init__(f'{name}: {detail}')
        self.name, self.detail = name, detail


def from_checker(error):
    """The interface's refusal named inside a packer's ValueError ('... refused by its own checker: NAME'), else BUILT_SHAPE."""
    found = re.search(r'checker: ([A-Z0-9_]+)', str(error))
    return Refused(found.group(1) if found else 'BUILT_SHAPE', str(error))


@dataclasses.dataclass(frozen=True)
class Layout:
    phys_base: int
    region_end: int
    bootstrap_pa: int
    bootstrap_span: int
    params_pa: int
    devicetree_pa: int
    handoff_pa: int
    classes: tuple          # (lo, hi, reservation name), ascending


def layout_of(xb):
    """The layout a probes/xnuboot.py module holds after place(): where build() puts what."""
    return Layout(xb.PHYS_BASE, xb.REGION_END, xb.BOOTSTRAP_PA, xb.BOOTSTRAP_SPAN, xb.PARAMS_PA, xb.DEVICETREE_PA, xb.HANDOFF_PA,
                  tuple((xb.PHYS_BASE + off, xb.PHYS_BASE + off + span, name) for name, off, span in xb.reservations()))


KIND_OF = {'handoff': sb.KIND_HANDOFF, 'image': sb.KIND_KERNEL, 'monitors': sb.KIND_MONITOR_IMAGE, 'frame table': sb.KIND_MONITOR_DATA,
           'frame map': sb.KIND_MONITOR_DATA, 'page tables': sb.KIND_TABLES, 'sptm data': sb.KIND_MONITOR_DATA, 'aux collection': sb.KIND_OTHER}


def kind_of(lay, pa, size, from_tables):
    """The kind of one chunk, from the reservation that holds all of it. The table pages come from built['tables'], and only from there."""
    end, home = pa + size, None
    for lo, hi, name in lay.classes:
        if lo <= pa and end <= hi:
            home = name
    if home is not None:
        kind = KIND_OF.get(home)
        if home == 'handoff':
            kind = {lay.params_pa: sb.KIND_PARAMS, lay.devicetree_pa: sb.KIND_DEVICE_TREE}.get(pa, kind)
    elif max(hi for _, hi, _ in lay.classes) <= pa and end <= lay.region_end:
        kind = sb.KIND_OTHER                # the payload area: a RAM disk, SEP preload or trust-cache fallback of some profiles
    else:
        kind = None
    if kind is None or (kind == sb.KIND_TABLES) != from_tables:
        raise Refused('BUILT_SHAPE', f'the chunk at {pa:#x} (+{size:#x}) is in no reservation this builder knows, or comes from the wrong source')
    return kind


def gather(built, lay):
    """{pa: (kind, bytes-like)} of everything the A16 loader uploads: built['chunks'] and the pages of built['tables'].chunks()."""
    if not isinstance(built, dict) or not isinstance(built.get('chunks'), dict) or not hasattr(built.get('tables'), 'chunks'):
        raise Refused('BUILT_SHAPE', 'built needs a dict "chunks" and a "tables" object with chunks()')
    if built.get('params_pa') != lay.params_pa:
        raise Refused('BUILT_SHAPE', 'built["params_pa"] is not the layout\'s PARAMS_PA')
    writes = {}
    for from_tables, items in ((False, built['chunks'].items()), (True, built['tables'].chunks())):
        for pa, blob in items:
            if not isinstance(pa, int) or isinstance(pa, bool) or not isinstance(blob, (bytes, bytearray, memoryview)) or not len(blob):
                raise Refused('BUILT_SHAPE', f'the chunk at {pa!r} is not a non-empty bytes-like value')
            if pa in writes or (from_tables and (pa % 0x4000 or len(blob) != 0x4000)):
                raise Refused('BUILT_SHAPE', f'two chunks at {pa:#x}, or a table page that is not one aligned 16 KiB page')
            writes[pa] = (kind_of(lay, pa, len(blob), from_tables), memoryview(blob).cast('B'))
    if not writes:
        raise Refused('BUILT_SHAPE', 'build() returned no chunks')
    return writes


def flatten(writes):
    """Non-overlapping runs [(start, kind, bytes)]: the memory the A16 loader's upload leaves. It writes in ascending address order, so where
    chunks overlap (build() puts the debug table and the TXM stack array inside a zero page) the one that starts later wins."""
    order, segs = sorted(writes), []        # segs: disjoint and ascending, (start, end, index into order, offset into that chunk)
    for i, pa in enumerate(order):
        kind, blob = writes[pa]
        end = pa + len(blob)
        lo, hi = bisect.bisect_right(segs, pa, key=lambda s: s[1]), bisect.bisect_left(segs, end, key=lambda s: s[0])
        if any(writes[order[s[2]]][0] != kind for s in segs[lo:hi]):
            raise Refused('BUILT_SHAPE', f'the chunk at {pa:#x} overlaps a chunk of another kind')
        new = [(pa, end, i, 0)]
        if lo < hi and segs[lo][0] < pa:
            new.insert(0, (segs[lo][0], pa, segs[lo][2], segs[lo][3]))
        if lo < hi and end < segs[hi - 1][1]:
            s, e, j, o = segs[hi - 1]
            new.append((end, e, j, o + end - s))
        segs[lo:hi] = new
    runs = []                               # [start, end, kind, pieces]
    for s, e, j, o in segs:
        kind, blob = writes[order[j]]
        if runs and runs[-1][1] == s and runs[-1][2] == kind:
            runs[-1][1] = e
            runs[-1][3].append(blob[o:o + e - s])
        else:
            runs.append([s, e, kind, [blob[o:o + e - s]]])
    return [(s, k, b''.join(parts)) for s, _, k, parts in runs]


def pad_runs(runs):
    """Starts must be multiples of 16 and sizes are padded to 16 with zeros. A pad never reaches the next run: that starts on a multiple of 16 too."""
    if any(start % sb.DEST_ALIGN for start, _, _ in runs):
        raise Refused('BUILT_SHAPE', f'a chunk starts at an address that is not a multiple of {sb.DEST_ALIGN}')
    return [(start, kind, data + bytes(-len(data) % sb.DEST_ALIGN)) for start, kind, data in runs]


def split_zero(kind, start, data):
    """A run that is all zero, or holds a zero stretch of ZERO_MIN or more, becomes ZERO chunks (no file bytes). PARAMS and DEVICE_TREE stay whole."""
    if kind in (sb.KIND_PARAMS, sb.KIND_DEVICE_TREE):
        return [dict(kind=kind, dest=start, data=data)]
    if data.count(0) == len(data):
        return [dict(kind=kind, dest=start, size=len(data))]
    out, at = [], 0
    for m in re.finditer(rb'\x00{%d,}' % ZERO_MIN, data):
        a, b = -(-m.start() // sb.DEST_ALIGN) * sb.DEST_ALIGN, m.end() // sb.DEST_ALIGN * sb.DEST_ALIGN
        if b - a >= ZERO_MIN:
            out += [dict(kind=kind, dest=start + at, data=data[at:a])] if a > at else []
            out.append(dict(kind=kind, dest=start + a, size=b - a))
            at = b
    return out + ([dict(kind=kind, dest=start + at, data=data[at:])] if at < len(data) else [])


def chunks_from_built(built, lay):
    return [c for start, kind, data in pad_runs(flatten(gather(built, lay))) for c in split_zero(kind, start, data)]


def guest_from_chunks(place, chunks):
    try:
        return sb.pack_guest(place['guest_base'], place['guest_size'], chunks)
    except (ValueError, struct.error, OverflowError, KeyError, TypeError) as error:      # the packer's own refusal, or a chunk it cannot encode
        raise from_checker(error) from None


def check_params(chunks, place, time_box):
    """PARAMS mem_low and mem_high are the guest DRAM and the reserve above it: guest_base and work_base. Its watchdog is the time box."""
    params = [c for c in chunks if c['kind'] == sb.KIND_PARAMS]
    data = params[0].get('data') if len(params) == 1 else None
    if data is None or len(data) < 8 * (PARAMS_MEM_HIGH + 1):
        raise Refused('BUILT_SHAPE', 'the PARAMS chunk is absent, zero-filled or shorter than its memory range')
    word = lambda i: int.from_bytes(data[8 * i:8 * i + 8], 'little')   # noqa: E731
    if (word(PARAMS_MEM_LOW), word(PARAMS_MEM_HIGH)) != (place['guest_base'], place['work_base']):
        raise Refused('PARAMS_MISMATCH', f'PARAMS memory {word(PARAMS_MEM_LOW):#x}..{word(PARAMS_MEM_HIGH):#x}, '
                      f'the placement {place["guest_base"]:#x}..{place["work_base"]:#x}')
    if word(PARAMS_WATCHDOG) != time_box:
        raise Refused('TIME_BOX_MISMATCH', f'PARAMS carries a watchdog of {word(PARAMS_WATCHDOG)} s, the record says {time_box} s')


# ---- Synthetic filler ----------------------------------------------------------------------------------------------------------
def stream(label, n):
    """n bytes of a SHA-256 counter stream, block i = SHA-256(label ':' i): reproducible and opaque."""
    prefix, blocks, parts = label.encode() + b':', -(-n // 32), []
    for lo in range(0, blocks, 1 << 16):
        parts.append(b''.join([hashlib.sha256(prefix + i.to_bytes(8, 'little')).digest() for i in range(lo, min(lo + (1 << 16), blocks))]))
    return b''.join(parts)[:n]


class SyntheticTables:
    def __init__(self, pages):
        self.pages = pages

    def chunks(self):
        return sorted(self.pages.items())


def synthetic_built(xb, image_bytes, tables_bytes, sptm_bytes, seed, time_box):
    """A dict of the shape xnuboot.build() returns ('chunks', 'tables', 'params_pa'), filled from the counter stream and laid out by `xb` after
    its place(), so that the pipeline treats it as it treats a real build: overlapping handoff chunks, zero pages, an image whose last body
    is not a multiple of 16, and a PARAMS record whose memory range is what params_blob() would write."""
    lay = layout_of(xb)
    res = {name: (lo, hi) for lo, hi, name in lay.classes}
    for what, size, (lo, hi) in (('image', image_bytes, res['image']), ('tables', tables_bytes, res['page tables']), ('sptm data', sptm_bytes, res['sptm data'])):
        if not 0 < size <= hi - lo:
            raise Refused('CHUNK_RANGE', f'the synthetic {what} ({size} bytes) does not fit its reservation of {hi - lo} bytes')
    g = lambda tag, n: stream(f'{seed}/{tag}', n)   # noqa: E731
    h = lay.handoff_pa
    chunks = {h: g('sptm', 0x1000), h + 0x1000: g('bootargs', 0x1000), h + xb.REGIONS_OFFSET: g('regions', 0x1000), h + 0x4000: bytes(0x4000),
              h + xb.DEBUG_TABLE_OFFSET: g('debug', 0x28), h + xb.TXM_STACK_ARRAY_OFFSET: g('txm', 0x30),
              h + 0x8000: bytes(0x78) + g('vncr', 8) + bytes(0x4000 - 0x80), lay.devicetree_pa: g('devicetree', 0x1D420)}
    words = [0] * PARAMS_WORDS
    words[0], words[3], words[PARAMS_WATCHDOG] = res['page tables'][0], res['image'][0] + 0x10000, time_box
    words[PARAMS_MEM_LOW], words[PARAMS_MEM_HIGH] = lay.phys_base, lay.bootstrap_pa + lay.bootstrap_span
    chunks[lay.params_pa] = struct.pack(f'<{PARAMS_WORDS}Q', *words)
    off, sizes = 0, [image_bytes * f // 20 // 0x4000 * 0x4000 for f in (11, 1, 2)]
    for tag, size in zip(('text', 'const', 'data', 'linkedit'), sizes + [image_bytes - sum(sizes)]):
        body = g(tag, size - 0x234 if tag == 'linkedit' and size > 0x1000 else size)
        if tag == 'data' and size >= 0x80000:
            body = body[:0x10000] + bytes(0x40000) + body[0x50000:]
        chunks[res['image'][0] + off] = body
        off += -(-size // 0x4000) * 0x4000
    for n, size in enumerate((0x180400, 0x13FC10)):
        chunks[res['monitors'][0] + n * xb.MONITOR_STRIDE] = g(f'monitor{n}', size)
    for name, tag in (('frame table', 'frametable'), ('frame map', 'framemap')):
        chunks[res[name][0]] = g(tag, min(0x100000, res[name][1] - res[name][0]))
    data_lo, zero_span = res['sptm data'][0], xb.SPTM_REGION_END - xb.SPTM_REGION_VA
    for step in range(0, zero_span, 0x4000):
        chunks[data_lo + step] = bytes(0x4000)               # build() backs the monitor's fixed region with 16 KiB zero pages
    if sptm_bytes > zero_span:
        chunks[data_lo + zero_span] = g('sptmdata', sptm_bytes - zero_span)
    chunks[data_lo + xb.SCRATCH_OFFSET] = bytes(xb.SCRATCH_SPAN)
    blob = g('tables', tables_bytes // 0x4000 * 0x4000)
    pages = {res['page tables'][0] + i * 0x4000: bytearray(blob[i * 0x4000:(i + 1) * 0x4000]) for i in range(len(blob) // 0x4000)}
    return dict(chunks=chunks, tables=SyntheticTables(pages), params_pa=lay.params_pa)


# ---- The real build() ----------------------------------------------------------------------------------------------------------
def place_layout(xb, place):
    try:
        xb.place(place['guest_base'], place['guest_size'] + place['reserve_size'], guest_span=place['guest_size'])
    except (ValueError, SystemExit) as error:       # the loader's own rule: the large layout needs 232 MiB for the image and its tables
        raise Refused('PLACE_LAYOUT', str(error)) from None


def real_built(a, xb, place, facts):
    """probes/run.py's sequence with the choices as arguments. EXPERIMENTAL, host only: it ran on the J700 26A428 kernelcache (docs/SP11-STICK-REAL.md)."""
    stale = sorted(k for k in os.environ if k.startswith('SNAP_'))
    if stale:
        raise Refused('ENV_SNAP_SET', f'{stale}: the build reads SNAP_* variables, so an inherited one would change the guest: unset them')
    if not Path(a.kernelcache).is_file():
        raise Refused('KERNELCACHE_MISSING', 'the kernelcache is Apple material: it is read from a local path and never committed')
    xb.configure_guest()
    xb.configure_txm_arena()
    xb.configure_adreno_va_wide()
    xb.HOST_TIMEBASE_HZ = facts['cntfrq_hz']
    place_layout(xb, place)
    xb.FRAMEBUFFER = None if a.panel == 'withheld' else (facts['fb_base'], facts['fb_width'], facts['fb_height'], facts['fb_stride'] * 4, 32)
    env = {'SNAP_NATIVE_CALLS': '1', 'SNAP_SPTM_HVC_CALLS': str(a.sptm_hvc_calls), 'SNAP_SEED': a.seed}   # else the loader draws a fresh boot seed per build
    os.environ.update(env)
    try:
        with contextlib.redirect_stdout(io.StringIO()):     # the loader narrates its layout on stdout: not for the record the operator approves
            built = xb.build(str(a.kernelcache))
    except (OSError, ValueError, SystemExit) as error:
        raise Refused('BUILT_SHAPE', f'probes/xnuboot.py build() refused: {error}') from None
    finally:
        for key in env:
            os.environ.pop(key, None)
    try:        # run.py's check before an upload: no APF slice of the guest's tree may alias owned memory (the loader's message names a device node: not quoted)
        xb.validate_apf_memory(built['dart_apf_slices'], [(lo, hi - lo) for lo, hi, _, _ in xb.stage2_ranges()] + [(place['work_base'], place['work_size'])])
    except (ValueError, SystemExit):
        raise Refused('BUILT_SHAPE', 'an APF device slice of the guest\'s device tree overlaps the guest, the panel or the work area') from None
    built['watchdog'] = a.time_box_s
    built['chunks'][xb.PARAMS_PA] = xb.params_blob(built)       # run.py serialises the parameters once more, after its run-time overrides
    return built


def descriptor_mismatches(board, facts):
    """The pinned facts a boot-ready descriptor (a tools/board_descriptor.py Board) does not state: the GIC and timer pins, a MIDR value that no
    pair matches under its mask, and a boot MPIDR whose affinity the descriptor does not list."""
    bad = [n for n, want in (('gicd_base', board.gicd[0]), ('gicr_first', board.gicr[0]), ('gicr_stride', board.gicr[1]), ('gicr_limit', board.gicr[0] + board.gicr[2]),
                             ('its_base', board.its[1] if board.its[0] else 0), ('cntfrq_hz', board.timer_hz), ('timer_intid', board.timer_ppi[3])) if facts[n] != want]
    if any(not any(v & mask == mid & mask for mid, mask in board.midr) for v in facts['midr']):
        bad.append('midr')
    if facts['boot_mpidr'] & AFFINITY not in board.mpidrs:
        bad.append('boot_mpidr')
    return bad
