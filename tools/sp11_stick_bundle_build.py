#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP11 stick bundle builder, host side (monitor port work item 3; docs/SP11-STICK-BUILD.md). Evidence: EXPERIMENTAL, hardware NOT RUN.

    sp11_stick_bundle_build.py facts-check  --facts F [--dump DIR ...]
    sp11_stick_bundle_build.py place-check  --base A --guest-mib N [--reserve-mib N] [--facts F] [--dump DIR ...] [--declared BASE:SIZE ...]
    sp11_stick_bundle_build.py synthetic    --out DIR ...     a complete bundle from deterministic filler: no Apple material
    sp11_stick_bundle_build.py authorisation-template synthetic|real ...      the record the operator approves, on stdout
    sp11_stick_bundle_build.py real         --kernelcache K ...   NOT RUN: no kernelcache exists on the machine this was written on

The bundle is the five files of docs/SP11-STICK-BUNDLE.md, q1n1's PAYLOAD.MAN and, with --base-tree, a stage tree that the stager verifies.
A refusal prints `REFUSED=<NAME>` and exits 1: the interface's name (tools/sp11_stick_bundle.py) where it has one, else BUILDER_REFUSALS.
Output is deterministic and is written only below --out, which must be under build/ (or --allow-under). The placement (D1), the stores (D4),
the time box (D5), the panel and the call patch (D9) are arguments without defaults: nothing is decided here. The chunk plumbing is in
tools/sp11_stick_bundle_chunks.py."""
import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import board_descriptor as bd  # noqa: E402
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402

Refused = ch.Refused
MIB = sb.MIB
NAMES = ('SNAPMON.BIN', 'SNAPREP.BIN', 'GUEST.DAT', 'PIN.DAT', 'REPORT.DAT')
SLBOUNCE, RUNNER = 'SLBOUNCE-DEVHE-MARK.EFI', 'Q1N1-PAYLOAD.EFI'
STEP = f'{SLBOUNCE} {RUNNER} --el2'
FACTS_FORMAT, REPORT_NAME = 'sp11-bundle-facts-1', 'build-report.json'
AFFINITY = 0xFF00FFFFFF     # Aff3:Aff2:Aff1:Aff0 of MPIDR_EL1, the part ACPI states
GICR_STRIDE = {3: 0x20000, 4: 0x40000}
STORES = {'ram': sb.STORE_RAM, 'el2_sysreg': sb.STORE_EL2_SYSREG, 'gic': sb.STORE_GIC}
TAGS = {'EL2_UNREACHABLE': 1, 'DEVICE_OWNED': 2, 'OPERATOR': 3}
KIND_NAME = {v: k[12:] for k, v in sb.CONSTANTS.items() if k.startswith('SP11SB_KIND_') and k != 'SP11SB_KIND_MAX'}
DUMMY = b'\x01' * 32        # a nonzero stand-in digest for the early checks, before GUEST.DAT exists
BUILDER_REFUSALS = (
    'FACTS_BAD', 'FACTS_DISAGREE', 'NO_DUMPS', 'DUMP_INCOMPLETE', 'FB_UNKNOWN', 'DESCRIPTOR_NOT_READY', 'AUTH_RECORD_MISMATCH', 'Q1N1_TREE_ABSENT',
    'STAGE_REFUSED', 'STAGE_VERIFY_FAILED', 'OUT_DIR_REFUSED', 'OUT_NOT_EMPTY', 'PAYLOAD_BAD', 'REPORT_NOT_ZERO', 'ENV_SNAP_SET', 'KERNELCACHE_MISSING',
    'OPTIONS_BAD')
ch.KNOWN_REFUSALS.update(BUILDER_REFUSALS)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fmt(value):
    return f'{value:#x}' if isinstance(value, int) and value > 255 else str(value)


def number(value, what, bad='OPTIONS_BAD'):
    try:
        if isinstance(value, bool):
            raise TypeError
        return value if isinstance(value, int) else int(str(value), 0)
    except (TypeError, ValueError):
        raise Refused(bad, f'{what} is not a number: {value!r}') from None


def read_regular(path, what, cap=sb.FILE_MAX, minimum=1):
    try:
        info = Path(path).lstat()
    except OSError as error:
        raise Refused('PAYLOAD_BAD', f'{what}: {error.strerror}') from None
    if not stat.S_ISREG(info.st_mode):
        raise Refused('PAYLOAD_BAD', f'{what} is not a regular file')
    if info.st_size > cap:
        raise Refused('FILE_SIZE', f'{what} is {info.st_size} bytes; the runner takes at most {sb.FILE_MAX}')
    if info.st_size < minimum:
        raise Refused('PAYLOAD_BAD', f'{what} is shorter than {minimum} bytes')
    data = Path(path).read_bytes()
    if len(data) != info.st_size:
        raise Refused('PAYLOAD_BAD', f'{what} changed while it was read')
    return data


# ---- The tools this one reuses -------------------------------------------------------------------------------------------
def load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stager():
    return load_path('stage_sp11_boot_media', ROOT / 'tools/stage-sp11-boot-media.py')


def xnuboot():
    """A private copy per call, as probes/run.py makes one: place() rewrites the module's globals, which must not leak between builds."""
    return load_path('xnuboot_private', ROOT / 'probes/xnuboot.py')


def q1n1_tree():
    """$Q1N1_SP11_TREE when set, even if empty or wrong (no other tree is substituted); else the checkouts beside this one."""
    named = os.environ.get('Q1N1_SP11_TREE')
    candidates = [Path(named)] if named and named.strip() else []
    if named is None:
        for base in (ROOT.parent, ROOT.parent.parent, ROOT.parent.parent.parent):
            candidates += [base / 'q1n1', *sorted(base.glob('q1n1-worktrees/*sp11*'))]
    return next((t for t in candidates if (t / 'tools/sp11_payload_manifest.py').is_file() and (t / 'tools/preload.py').is_file()), None)


def q1n1_manifest_tool():
    tree = q1n1_tree()
    if tree is None:
        raise Refused('Q1N1_TREE_ABSENT', 'set Q1N1_SP11_TREE to a q1n1 checkout that holds tools/sp11_payload_manifest.py')
    held = sys.modules.pop('preload', None)         # the tool imports `preload` from its own directory
    sys.path.insert(0, str(tree / 'tools'))
    try:
        return load_path('q1n1_sp11_payload_manifest', tree / 'tools/sp11_payload_manifest.py')
    finally:
        sys.path.remove(str(tree / 'tools'))
        sys.modules.pop('preload', None)
        if held is not None:
            sys.modules['preload'] = held


# ---- Firmware dumps --------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Dump:
    name: str
    path: Path
    memmap: bytes
    stride: int
    summary: dict


def find_dumps(explicit):
    dirs = [Path(d) for d in explicit] or sorted(p for p in (ROOT / 'downloads/sp11').glob('x*-run*') if (p / 'summary.txt').is_file())
    if not dirs:
        raise Refused('NO_DUMPS', 'pass --dump DIR, or put the probe dumps under downloads/sp11/x*-run*')
    dumps = []
    for d in dirs:
        try:
            memmap = (d / 'memmap.raw').read_bytes()
            text = (d / 'summary.txt').read_text() if (d / 'summary.txt').is_file() else ''
        except OSError:
            raise Refused('DUMP_INCOMPLETE', f'{d.name}: no memmap.raw') from None
        summary = dict(line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
        dumps.append(Dump(d.name, d, memmap, number(summary.get('memmap_descriptor_size', 48), 'memmap_descriptor_size'), summary))
    return dumps


def descriptors(memmap, stride):
    """(type, start, end, attribute) of each EFI descriptor: type u32 at 0, start u64 at 8, pages u64 at 24, attribute u64 at 32."""
    for off in range(0, len(memmap) - stride + 1, stride):
        kind, _, start, _, pages, attr = struct.unpack_from('<IIQQQQ', memmap, off)
        yield kind, start, start + (pages << 12), attr


def sdt(dump, signature):
    try:
        data = (dump.path / f'{signature}.bin').read_bytes()
    except OSError:
        raise Refused('DUMP_INCOMPLETE', f'{dump.name}: {signature}.bin is missing') from None
    if len(data) < 36 or data[:4] != signature.encode() or struct.unpack_from('<I', data, 4)[0] != len(data) or sum(data) & 255:
        raise Refused('DUMP_INCOMPLETE', f'{dump.name}: {signature}.bin is not a well-formed table')
    return data


def madt(data):
    """gicd (base, version), gicr [(base, length)], its [base], gicc [(enabled, mpidr, gicr_base)] of a MADT. A structure may be longer than
    ACPI's size for its type and the extra bytes are ignored, as Linux does. The unit's firmware writes its GICD structure as 25 bytes and
    states no redistributor range at all, only each CPU's own redistributor base in its GICC: probes/bootcontract.py accepts exactly that
    GICD form (zero final byte) and takes the redistributors from the GICC bases, but this reader is more lenient than it on purpose."""
    least, out, off = {0xB: 76, 0xC: 24, 0xE: 16, 0xF: 20}, dict(gicd=None, gicr=[], its=[], gicc=[]), 44
    while off < len(data):
        kind, size = data[off], data[off + 1] if off + 1 < len(data) else 0
        if size < max(2, least.get(kind, 2)) or off + size > len(data):
            raise Refused('DUMP_INCOMPLETE', f'MADT structure type {kind:#x} at {off} has length {size}')
        e = data[off:off + size]
        if kind == 0xB:
            out['gicc'].append((bool(struct.unpack_from('<I', e, 12)[0] & 1), struct.unpack_from('<Q', e, 68)[0], struct.unpack_from('<Q', e, 60)[0]))
        elif kind == 0xC:
            out['gicd'] = (struct.unpack_from('<Q', e, 8)[0], e[20])
        elif kind == 0xE:
            out['gicr'].append(struct.unpack_from('<QI', e, 4))
        elif kind == 0xF:
            out['its'].append(struct.unpack_from('<Q', e, 8)[0])
        off += size
    if out['gicd'] is None:
        raise Refused('DUMP_INCOMPLETE', 'the MADT has no GIC distributor')
    return out


def gtdt_el2(data):
    """The non-secure EL2 timer GSIV: the u32 at offset 72 of a GTDT of revision 2 or later."""
    if len(data) < 76:
        raise Refused('DUMP_INCOMPLETE', 'the GTDT is too short for its EL2 timer')
    return struct.unpack_from('<I', data, 72)[0]


def mcfg(data):
    return sorted((struct.unpack_from('<H', data, o + 8)[0], struct.unpack_from('<Q', data, o)[0], data[o + 10], data[o + 11])
                  for o in range(44, len(data) - 15, 16))


# ---- Facts -------------------------------------------------------------------------------------------------------------------
INT_KEYS = ('boot_mpidr', 'cntfrq_hz', 'gicd_base', 'gicr_first', 'gicr_limit', 'gicr_stride', 'gicr_count', 'its_base', 'timer_intid')
GOP_KEYS = ('base', 'bytes', 'width', 'height', 'stride', 'format')
PIN_FACTS = INT_KEYS + tuple(f'fb_{k}' for k in GOP_KEYS)
FACT_KEYS = {'format', 'description', 'board_id', 'build_label', 'midr', 'gop', 'avoid', 'acpi', 'ecam', *INT_KEYS}


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f'duplicate key {key!r}')
        out[key] = value
    return out


def load_facts(path):
    """The local facts JSON (tools/testdata/sp11-facts.example.json), read strictly: no unknown or duplicate key, every number checked."""
    data = read_regular(path, 'facts', cap=1 << 20)
    n = lambda v: number(v, 'a fact', 'FACTS_BAD')   # noqa: E731
    try:
        raw = json.loads(data.decode('utf-8'), object_pairs_hook=_no_duplicates)
        if raw.get('format') != FACTS_FORMAT or set(raw) - FACT_KEYS or set(raw['gop']) != set(GOP_KEYS):
            raise KeyError('format, keys or gop')
        f = dict(board_id=raw['board_id'], build_label=raw['build_label'], midr=sorted(map(n, raw['midr'])), **{k: n(raw[k]) for k in INT_KEYS},
                 **{f'fb_{k}': n(raw['gop'][k]) for k in GOP_KEYS},
                 avoid=sorted((n(a['base']), n(a['size']), TAGS.get(a['tag']) or n(a['tag'])) for a in raw.get('avoid', [])),
                 acpi=sorted((a['sig'], n(a['length']), bytes.fromhex(a['sha256'])) for a in raw.get('acpi', [])),
                 ecam=sorted(tuple(map(n, (e['segment'], e['base'], e['start_bus'], e['end_bus']))) for e in raw['ecam']) if 'ecam' in raw else None)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise Refused('FACTS_BAD', f'{type(error).__name__}: {error}') from None
    if not (isinstance(f['board_id'], str) and re.fullmatch(r'[A-Za-z0-9._-]{1,15}', f['board_id'])
            and isinstance(f['build_label'], str) and re.fullmatch(r'[A-Za-z0-9._-]{1,23}', f['build_label'])):
        raise Refused('PIN_STRING', 'board_id (at most 15 characters) and build_label (at most 23) are letters, digits, ".", "_" and "-"')
    if len(f['midr']) > sb.MIDR_MAX or len(f['avoid']) > sb.AVOID_MAX or len(f['acpi']) > sb.ACPI_MAX:
        raise Refused('PIN_COUNT', 'at most 4 midr values, 4 avoid ranges and 8 acpi tables')
    if any(not re.fullmatch(r'[A-Z0-9]{4}', s) or len(d) != 32 for s, _, d in f['acpi']):
        raise Refused('FACTS_BAD', 'an acpi entry needs a 4-character upper-case signature and a SHA-256')
    f['sha256'] = sha(data)
    return f


def check_facts(f, d):
    """How `f` disagrees with one dump: [(fact, text)]. What no dump states (midr, cntfrq_hz, avoid) is not looked at."""
    bad = []

    def eq(name, want, got):
        if want != got:
            bad.append((name, f'facts {fmt(want)}, {d.name} {fmt(got)}'))
    m = madt(sdt(d, 'APIC'))
    eq('gicd_base', f['gicd_base'], m['gicd'][0])
    if m['gicd'][1] in GICR_STRIDE:
        eq('gicr_stride', f['gicr_stride'], GICR_STRIDE[m['gicd'][1]])
    stride, ranges = f['gicr_stride'] or 1, sorted(m['gicr'])
    if not ranges:      # the unit's form: no range, only each enabled CPU's own redistributor base, one frame apart
        bases = sorted(b for on, _, b in m['gicc'] if on and b)
        ranges = [(bases[0], len(bases) * stride)] if bases and bases == [bases[0] + i * stride for i in range(len(bases))] else []
    if not ranges or any(a[0] + a[1] != b[0] for a, b in zip(ranges, ranges[1:])):
        bad.append(('gicr', f'{d.name}: the redistributor bases are absent, or not contiguous at the stride of the facts'))
    else:
        first, limit = ranges[0][0], ranges[-1][0] + ranges[-1][1]
        eq('gicr_first', f['gicr_first'], first)
        eq('gicr_limit', f['gicr_limit'], limit)
        eq('gicr_count', f['gicr_count'], (limit - first) // stride)
    eq('its_base', f['its_base'], m['its'][0] if m['its'] else 0)
    eq('timer_intid', f['timer_intid'], gtdt_el2(sdt(d, 'GTDT')))
    if f['boot_mpidr'] & AFFINITY not in {mpidr & AFFINITY for on, mpidr, _ in m['gicc'] if on}:
        bad.append(('boot_mpidr', f'{fmt(f["boot_mpidr"])} is no enabled CPU of the {d.name} MADT'))
    for fact, key in (('fb_base', 'gop_base'), ('fb_bytes', 'gop_size'), ('fb_width', 'gop_width'), ('fb_height', 'gop_height'),
                      ('fb_stride', 'gop_stride_pixels'), ('fb_format', 'gop_format')):
        if key in d.summary:
            eq(fact, f[fact], number(d.summary[key], key))
    if any(k == sb.EFI_CONVENTIONAL and s < f['fb_base'] + f['fb_bytes'] and f['fb_base'] < e for k, s, e, _ in descriptors(d.memmap, d.stride)):
        bad.append(('fb_base', f'the framebuffer is in conventional memory of the {d.name} map'))
    for sig, length, digest in f['acpi']:
        eq(f'acpi {sig} length', length, len(sdt(d, sig)))
        eq(f'acpi {sig} sha256', digest.hex(), sha(sdt(d, sig)))
    if f['ecam'] is not None:
        eq('ecam', [tuple(e) for e in f['ecam']], mcfg(sdt(d, 'MCFG')))
    return bad


def facts_check(facts, dumps):
    lines, bad = [], []
    for d in dumps:
        found = check_facts(facts, d)
        bad += [f'{name}: {text}' for name, text in found]
        lines.append(f'DUMP {d.name} ' + ('OK' if not found else 'DISAGREES ' + ', '.join(name for name, _ in found)))
    if bad:
        raise Refused('FACTS_DISAGREE', '; '.join(bad))
    return lines + ['NOT CHECKED FROM A DUMP: midr, cntfrq_hz, avoid, board_id, build_label']


# ---- Placement ---------------------------------------------------------------------------------------------------------------
def declared_ranges(items):
    return [tuple(number(v, '--declared') for v in item.partition(':')[::2]) for item in items]


def bare_pin(place, avoid=()):
    """The PIN.DAT fields sb.check_place and the interface's placement arithmetic read, so that a placement can be checked before a PIN.DAT exists."""
    pin = bytearray(sb.PIN_BYTES)
    for key, value in place.items():
        sb.put(pin, sb.PIN_LAYOUT, key, value)
    sb.put(pin, sb.PIN_LAYOUT, 'avoid_count', len(avoid))
    for i, entry in enumerate(avoid):
        for key, value in zip(('base', 'size', 'tag'), entry):
            sb.put(pin, sb.AVOID_LAYOUT, key, value, 0x180 + 24 * i)
    return bytes(pin)


def check_maps(pin, dumps, fb, declared):
    """[(dump, descriptors, code)]: the interface's placement rule against each recorded map. fb is (base, bytes), or None to use each
    dump's own GOP lines."""
    out = []
    for d in dumps:
        frame = fb or (('gop_base' in d.summary and 'gop_size' in d.summary) and (number(d.summary['gop_base'], 'gop_base'),
                                                                                  number(d.summary['gop_size'], 'gop_size')))
        if not frame:
            raise Refused('FB_UNKNOWN', f'{d.name} states no framebuffer and no --facts were given: the placement cannot be kept off it')
        out.append((d.name, len(d.memmap) // d.stride, sb.check_place(pin, d.memmap, d.stride, *frame, declared)))
    return out


def common_extents(dumps):
    """Conventional write-back extents on the 32 MiB grid that lie inside ONE conventional descriptor of every dump."""
    grid = sb.GUEST_ALIGN

    def extents(d):
        spans = [(-(-s // grid) * grid, e // grid * grid) for k, s, e, a in descriptors(d.memmap, d.stride) if k == sb.EFI_CONVENTIONAL and a & sb.EFI_WB]
        return [x for x in spans if x[0] < x[1]]
    cur = extents(dumps[0])
    for d in dumps[1:]:
        cur = [(max(a, c), min(b, e)) for a, b in cur for c, e in extents(d) if max(a, c) < min(b, e)]
    return sorted(cur)


def proposals(dumps, guest, reserve, fb, avoid, declared):
    """What could be placed, listed and never chosen (D1): the largest extent below 4 GiB and the largest above it, each with the biggest
    guest that leaves room for the reserve and the work area, verified against every dump."""
    lines, ext = [], common_extents(dumps)
    for name, pick in (('low', [x for x in ext if x[1] <= 1 << 32]), ('bank', [x for x in ext if x[0] >= 1 << 32])):
        if not pick:
            lines.append(f'PROPOSAL {name} none')
            continue
        start, end = max(pick, key=lambda x: (x[1] - x[0], -x[0]))
        most = (min(end, sb.IPA_LIMIT) - start - reserve - sb.WORK_BYTES) // sb.GUEST_ALIGN * sb.GUEST_ALIGN
        use = min(most, guest) if name == 'bank' else most
        if use < sb.GUEST_MIN:
            lines.append(f'PROPOSAL {name} none: the extent {start:#x}..{end:#x} is too small')
            continue
        ok = sum(not c for _, _, c in check_maps(bare_pin(sb.placement(start, use, reserve), avoid), dumps, fb, declared))
        lines.append(f'PROPOSAL {name} base={start:#x} extent_mib={(end - start) // MIB} guest_mib_max={most // MIB} '
                     f'verified_guest_mib={use // MIB} maps={ok}/{len(dumps)}')
    return lines


def placement_of(a):
    base, guest = number(a.base, '--base'), a.guest_mib * MIB
    reserve = a.reserve_mib * MIB if a.reserve_mib is not None else xnuboot().bootstrap_reserve(max(guest, 1))   # the loader's rule, not a choice
    if min(base, guest, reserve) <= 0:
        raise Refused('PLACE_LAYOUT', 'the base, the guest and the reserve are positive')
    if base + guest + reserve + sb.WORK_BYTES >= 1 << 64:
        raise Refused('PLACE_LIMIT', 'the placement wraps the 64-bit address space')
    return sb.placement(base, guest, reserve)


# ---- The authorisation record ------------------------------------------------------------------------------------------------
TOUCHES = {
    'ram': 'RAM inside the placement only: the copy of the chunks, zero fills, the monitor work area and out buffer, the report written into '
           'REPORT.DAT. No disk, no device, no RAM outside the placement (INFERRED: no wrapper exists yet).',
    'el2_sysreg': 'the EL2 system registers the monitor entry writes and restores: HCR_EL2, VBAR_EL2, TPIDR_EL2, VTCR_EL2, VTTBR_EL2, CNTHCTL_EL2, '
                  'MDCR_EL2 and the OS lock (docs/SP11-MONITOR-PORT.md section 12; read from the sources, not run).',
    'gic': 'GIC registers for the timer stop (PPI 26). Which registers is UNKNOWN until the wrapper (work item 4) and its register list (work item 6) '
           'exist: do not approve this class before they do.'}


def record_text(r):
    """The text the operator approves (its SHA-256 is PIN.DAT's authorisation_sha256): deterministic, ASCII, no personal name."""
    p, g = r['place'], r['guest']
    lines = [
        f'SNAPINTOSH SP11 STICK BUNDLE - AUTHORISATION RECORD (format 1, label {r["label"]})', '',
        'Status: operator decision NOT RECORDED. Hardware: NOT RUN. This text is a template until the operator approves it.',
        'Approval is the operator\'s alone: the SHA-256 of exactly this text, recorded as authorisation_sha256 of PIN.DAT; a changed byte is another',
        'record. It names no person and stays local: it is never committed or published.', '',
        '1. What may run (named by SHA-256; the runner hashes the payloads before each call)',
        f'   payload 0  SNAPMON.BIN  {r["monitor"][0]} bytes  sha256 {r["monitor"][1]}',
        f'   payload 1  SNAPREP.BIN  {r["reporter"][0]} bytes  sha256 {r["reporter"][1]}',
        f'   data       GUEST.DAT    {g["size"]} bytes  sha256 {g["sha256"]}  chunks {g["chunks"]}  table sha256 {g["index"]}',
        f'   data       REPORT.DAT   {sb.REPORT_BYTES} bytes, all zero, sha256 {sha(bytes(sb.REPORT_BYTES))}',
        '   data       PIN.DAT      1024 bytes; it carries this record\'s digest, so its own digest cannot be named here', '',
        '2. Placement (the one range the run may store to)',
        f'   guest_base {p["guest_base"]:#x}  guest_size {p["guest_size"] // MIB} MiB  reserve {p["reserve_size"] // MIB} MiB  '
        f'work_base {p["work_base"]:#x}  work_size {p["work_size"] // MIB} MiB',
        f'   place_base {p["place_base"]:#x}  place_end {p["place_base"] + p["place_size"]:#x}  (limit 2^36)', '',
        f'3. Store classes authorised (PIN.DAT authorised_stores = {r["stores"]})']
    lines += [f'   {name}: {TOUCHES[name]}' for name in STORES if r['stores'] & STORES[name]]
    lines += ['   Not authorised: any other store class, a disk or NVMe write, a firmware write, a clock, voltage or DVFS change, a secondary CPU.', '',
              f'4. Time box: {r["time_box_s"]} s of wall time on CNTPCT_EL0, carried in the PARAMS chunk (watchdog_seconds); the stop is the monitor\'s own.', '',
              '5. Refusal and stop rules (docs/SP11-MONITOR-PORT.md section 14)',
              '   - A wrapper REFUSED line ends the boot: fix the cause, never retry the same bundle.',
              '   - No page change for twice the time box after ENTER: note the last line and power cycle.',
              '   - Two resets at the same last line stop the series; a touch outside RAM is a finding, not a retry.',
              '   - One hardware client; AC power or at least 50% battery; the operator present and filming. A spinning guest is ended by the',
              '     operator\'s power cycle, which writes nothing.', '']
    text = '\n'.join(lines)
    text.encode('ascii')
    return text


def need_authorisation(a):
    if not (a.authorisation or a.authorisation_sha256):
        raise Refused('NO_AUTHORISATION', 'give --authorisation (the approved record) or --authorisation-sha256; authorisation-template prints the record')


def approve(a, text):
    """The authorisation digest: that of the approved record, which must be the record this build regenerates."""
    need_authorisation(a)
    if a.authorisation:
        digest = hashlib.sha256(read_regular(a.authorisation, '--authorisation', cap=1 << 16)).digest()
    else:
        if not re.fullmatch(r'[0-9a-fA-F]{64}', a.authorisation_sha256):
            raise Refused('OPTIONS_BAD', '--authorisation-sha256 is 64 hexadecimal digits')
        digest = bytes.fromhex(a.authorisation_sha256)
    if digest == bytes(32):
        raise Refused('NO_AUTHORISATION', 'the authorisation digest is all zero')
    if digest != hashlib.sha256(text.encode('ascii')).digest():
        raise Refused('AUTH_RECORD_MISMATCH', 'the approved record is not the record this build regenerates (another build, or an edited text)')
    return digest


# ---- Descriptor, the real build() call, files, PIN.DAT -----------------------------------------------------------------------
def descriptor_digest(a, facts, kind):
    if not a.descriptor:
        if kind == 'real':
            raise Refused('DESCRIPTOR_NOT_READY', 'a real bundle needs --descriptor: a boot-ready board descriptor')
        return hashlib.sha256(b'synthetic bundle: no board descriptor given').digest(), 'NOT GIVEN (a synthetic digest is pinned)'
    data = read_regular(a.descriptor, '--descriptor', cap=1 << 20)
    try:
        board = bd.loads(data.decode('utf-8'))
        bd.require_boot_ready(board)
    except (bd.BoardDescriptorError, UnicodeDecodeError) as error:
        raise Refused('DESCRIPTOR_NOT_READY', str(error)) from None
    bad = [n for n, want in (('gicd_base', board.gicd[0]), ('gicr_first', board.gicr[0]), ('gicr_stride', board.gicr[1]), ('gicr_limit', board.gicr[0] + board.gicr[2]),
                             ('its_base', board.its[1] if board.its[0] else 0), ('cntfrq_hz', board.timer_hz), ('timer_intid', board.timer_ppi[3])) if facts[n] != want]
    if any(not any(v & mask == mid & mask for mid, mask in board.midr) for v in facts['midr']):
        bad.append('midr')
    if facts['boot_mpidr'] & AFFINITY not in board.mpidrs:
        bad.append('boot_mpidr')
    if bad:
        raise Refused('FACTS_DISAGREE', f'the facts disagree with the descriptor on {bad}')
    return hashlib.sha256(data).digest(), 'boot-ready'


def place_layout(xb, place):
    try:
        xb.place(place['guest_base'], place['guest_size'] + place['reserve_size'], guest_span=place['guest_size'])
    except (ValueError, SystemExit) as error:       # the loader's own rule: the large layout needs 232 MiB for the image and its tables
        raise Refused('PLACE_LAYOUT', str(error)) from None


def real_built(a, xb, place, facts):
    """NOT RUN: no kernelcache exists on the machine this was written on. probes/run.py's sequence, with the choices as arguments."""
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
    env = {'SNAP_NATIVE_CALLS': '1', 'SNAP_SPTM_HVC_CALLS': str(a.sptm_hvc_calls)}
    os.environ.update(env)
    try:
        built = xb.build(str(a.kernelcache))
    except (OSError, ValueError, SystemExit) as error:
        raise Refused('BUILT_SHAPE', f'probes/xnuboot.py build() refused: {error}') from None
    finally:
        for key in env:
            os.environ.pop(key, None)
    built['watchdog'] = a.time_box_s
    built['chunks'][xb.PARAMS_PA] = xb.params_blob(built)       # run.py serialises the parameters once more, after its run-time overrides
    return built


def stores_of(text):
    try:
        return sum(STORES[w] for w in text.split(',') if w)
    except KeyError as error:
        raise Refused('OPTIONS_BAD', f'--stores names ram, el2_sysreg and gic, not {error}') from None


def pin_fields(f, place, stores, descriptor, auth, guest):
    return dict(authorised_stores=stores, board_id=f['board_id'], build_label=f['build_label'], descriptor_sha256=descriptor, authorisation_sha256=auth,
                midr=f['midr'], avoid=[dict(base=b, size=s, tag=t) for b, s, t in f['avoid']], acpi=[dict(sig=s, length=n, sha256=d) for s, n, d in f['acpi']],
                **place, **guest, **{k: f[k] for k in PIN_FACTS})


def pack_pin(fields, check=True):
    try:
        return sb.pack_pin(fields, check)
    except (ValueError, OverflowError, KeyError, TypeError) as error:      # the interface's refusal, or a fact too large for its field
        raise ch.from_checker(error) from None


def check_bundle_files(named):
    """[(name, size)] in manifest order through the interface's own file rule: the count, the roles and the sizes."""
    code = sb.check_files([(b'\\SP11\\' + n.encode() + bytes(sb.PATH_MAX), 0x1000 * (i + 1), size) for i, (n, size) in enumerate(named)])
    if code:
        raise Refused(sb.error_name(code), f'files {[n for n, _ in named]}, sizes {[s for _, s in named]}')


def verify_files(files, p):
    """The interface's own Python checks on the files as they will be written (digests included), then the final PIN.DAT against every map."""
    check_bundle_files([(n, len(b)) for n, b in files.items()])
    if any(files['REPORT.DAT']):
        raise Refused('REPORT_NOT_ZERO', 'REPORT.DAT must be all zero: the wrapper writes the report into its own memory')
    pin, guest = files['PIN.DAT'], files['GUEST.DAT']
    if code := sb.check_pin(pin) or sb.check_guest(guest, pin) or sb.check_digests(guest, pin, sb.HASH_CHUNKS | sb.HASH_FILE)[0]:
        raise Refused(sb.error_name(code), 'PIN.DAT or GUEST.DAT')
    for _, _, code in check_maps(pin, p['dumps'], (p['facts']['fb_base'], p['facts']['fb_bytes']), p['declared']):
        if code:
            raise Refused(sb.error_name(code), 'the final PIN.DAT against a recorded map')


# ---- The pipeline ------------------------------------------------------------------------------------------------------------
def prepare(a, kind, before_filler=None):
    """Everything up to the authorisation record: the first disagreement refuses. `before_filler` runs after the cheap checks and before the
    expensive generation. Returns what finish() and the writers need."""
    if not 0 < a.time_box_s < 1 << 32:
        raise Refused('OPTIONS_BAD', '--time-box-s is a positive number of seconds')
    facts, dumps = load_facts(a.facts), find_dumps(a.dump)
    lines, stores, place = facts_check(facts, dumps), stores_of(a.stores), placement_of(a)
    descriptor, state = descriptor_digest(a, facts, kind)
    monitor, reporter = read_regular(a.monitor, '--monitor', minimum=4), read_regular(a.reporter, '--reporter', minimum=4)
    declared = declared_ranges(a.declared)
    early = pack_pin(pin_fields(facts, place, stores, DUMMY, DUMMY, dict(guest_file_size=1024, guest_chunk_count=1, guest_file_sha256=DUMMY,
                                                                       guest_index_sha256=DUMMY)), check=False)
    if code := sb.check_pin(early, digest=False):       # alignment, shape, 2^36, the pinned facts: refused before any filler exists
        raise Refused(sb.error_name(code), 'the facts and the placement, with stand-in digests')
    maps = check_maps(bare_pin(place, facts['avoid']), dumps, (facts['fb_base'], facts['fb_bytes']), declared)
    if bad := [(n, sb.error_name(c)) for n, _, c in maps if c]:
        raise Refused(bad[0][1], 'maps: ' + ', '.join(f'{n} {c}' for n, c in bad))
    if before_filler:
        before_filler()
    xb = xnuboot()
    if kind == 'synthetic':
        place_layout(xb, place)
        built = ch.synthetic_built(xb, int(a.image_mib * MIB), int(a.tables_mib * MIB), int(a.sptm_data_mib * MIB), a.seed, a.time_box_s)
    else:
        built = real_built(a, xb, place, facts)
    lay = ch.layout_of(xb)
    chunks = ch.chunks_from_built(built, lay)
    guest = ch.guest_from_chunks(place, chunks)
    if sb.params_dest(guest) != lay.params_pa:
        raise Refused('BUILT_SHAPE', 'x3 of the monitor call, the PARAMS chunk destination, is not PARAMS_PA')
    ch.check_params(chunks, place, a.time_box_s)
    head, table = sb.unpack_guest(guest)
    record = record_text(dict(place=place, stores=stores, time_box_s=a.time_box_s, label=facts['build_label'], monitor=(len(monitor), sha(monitor)),
                              reporter=(len(reporter), sha(reporter)),
                              guest=dict(size=len(guest), sha256=sha(guest), chunks=len(table), index=head['index_sha256'].hex())))
    return dict(facts=facts, dumps=dumps, place=place, stores=stores, descriptor=descriptor, state=state, monitor=monitor, reporter=reporter,
                guest=guest, maps=maps, lines=lines, record=record, declared=declared)


def finish(p, digest):
    """PIN.DAT and the all-zero REPORT.DAT; every file passes the interface's checks before this returns them in manifest order."""
    pin = pack_pin(pin_fields(p['facts'], p['place'], p['stores'], p['descriptor'], digest, sb.guest_pin_fields(p['guest'])))
    files = dict(zip(NAMES, (p['monitor'], p['reporter'], p['guest'], pin, bytes(sb.REPORT_BYTES))))
    verify_files(files, p)
    return files


def check_out(a):
    """--out is confined: under build/ or an --allow-under directory, no ".." and no symbolic link, never /Volumes or /dev; and not a directory
    with something in it unless --force names an earlier build of this tool. Nothing is touched here: a refused build must not cost a good one."""
    out, st = Path(os.path.abspath(a.out)), stager()
    roots = [ROOT / 'build', *(Path(os.path.abspath(r)) for r in a.allow_under)]
    if '..' in Path(a.out).parts or not any(r in (out, *out.parents) for r in roots) or st.find_symlink_component(out) is not None:
        raise Refused('OUT_DIR_REFUSED', f'--out must be below {ROOT / "build"} or an --allow-under directory, with no ".." and no symbolic link')
    try:
        st.refuse_protected_location(out)
    except st.Refusal as error:
        raise Refused('OUT_DIR_REFUSED', error.reason) from None
    if out.exists() and (not out.is_dir() or any(out.iterdir())) and not (out.is_dir() and a.force and (out / REPORT_NAME).is_file()):
        raise Refused('OUT_NOT_EMPTY', f'{a.out} is not an empty directory; --force replaces only a directory with a {REPORT_NAME} from an earlier build')
    return out


def make_out(out):
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)


def put(out, rel, data):
    path = out / rel
    if out.resolve() not in path.resolve().parents:
        raise Refused('OUT_DIR_REFUSED', f'{rel} would be written outside the output directory')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o644)
    os.utime(path, (0, 0))


def stage_tree(a, out, p, files, manifest):
    """The stage tree: --base-tree copied, then the stager's autorun of one step with the six data files; verified before and after."""
    st = stager()
    if not a.base_tree or sorted(n for n, _, _ in a.file) != sorted((RUNNER, SLBOUNCE)):
        raise Refused('OPTIONS_BAD', f'--base-tree needs exactly --file {SLBOUNCE} PATH SHA256 and --file {RUNNER} PATH SHA256')
    base, dest, pins = Path(a.base_tree), out / 'stage', dict(pins=Path(a.pins) if a.pins else st.DEFAULT_PINS, no_pins=a.no_pins)
    if not base.is_dir() or st.find_symlink_component(base) is not None:
        raise Refused('STAGE_VERIFY_FAILED', '--base-tree is not a plain directory')
    try:
        for src in sorted(base.rglob('*')):
            rel = src.relative_to(base)
            if src.is_symlink():
                raise Refused('STAGE_VERIFY_FAILED', f'{rel} in the base tree is a symbolic link')
            if src.is_file() and (rel.parts[0] in ('EFI', 'SP11') or rel.as_posix() == st.TCBLAUNCH_ROOT_REL):
                st.write_file_deterministic(dest / rel, src.read_bytes(), st.FILE_MODE)
        st.finalize_permissions(dest)
        if (found := st.cmd_verify(argparse.Namespace(dest=dest, **pins)))['status'] != 'COMPLETE':
            raise Refused('STAGE_VERIFY_FAILED', f'the base tree is {found["status"]}: {found["manifest_mismatches"]}')
        data = [('PAYLOAD.MAN', manifest)] + [(n, files[n]) for n in NAMES]
        for name, blob in data:
            put(out, f'bundle/SP11/{name}', blob)
        done = st.cmd_autorun(argparse.Namespace(dest=dest, step=[STEP], name=a.plan_name or p['facts']['build_label'], clear=False, remove=False, **pins,
                                                 file=[list(f) for f in a.file], data=[[n, str(out / 'bundle/SP11' / n), sha(b)] for n, b in data]))
        found = st.cmd_verify(argparse.Namespace(dest=dest, **pins))
    except st.Refusal as error:
        raise Refused('STAGE_REFUSED', f'{error.reason}: {error.detail}') from None
    if found['status'] != 'COMPLETE':
        raise Refused('STAGE_VERIFY_FAILED', f'{found["status"]}: {found["manifest_mismatches"]} {found.get("autorun")}')
    tree = {q.relative_to(dest).as_posix(): sha(q.read_bytes()) for q in sorted(dest.rglob('*')) if q.is_file()}
    put(out, 'STICK-FILES.TXT', ''.join(f'{digest}  {rel}\n' for rel, digest in sorted(tree.items())).encode('ascii'))
    return dict(status=found['status'], shell_pin=found['shell_pin'], files_written=done['files_written'], tree=tree)


def report_of(a, kind, p, files, digest, manifest, staged):
    head, table = sb.unpack_guest(files['GUEST.DAT'])
    kinds = {}
    for c in table:
        key = f'{KIND_NAME[c["kind"]]}.{"ZERO" if c["flags"] & sb.CHUNK_ZERO else "DATA"}'
        kinds[key] = kinds.get(key, 0) + c['size']
    return dict(
        format='sp11-bundle-build-report-1', kind=kind, hardware='NOT RUN', evidence='EXPERIMENTAL', authorisation_sha256=digest.hex(), stage=staged,
        files={n: dict(size=len(files[n]), sha256=sha(files[n])) for n in NAMES}, manifest=manifest and dict(size=len(manifest), sha256=sha(manifest)),
        placement={k: f'{v:#x}' for k, v in p['place'].items()}, stores=p['stores'], time_box_s=a.time_box_s, descriptor=p['state'],
        guest=dict(chunks=len(table), bytes_by_kind=kinds, index_sha256=head['index_sha256'].hex(), params_dest=f'{sb.params_dest(files["GUEST.DAT"]):#x}'),
        facts_sha256=p['facts']['sha256'], dumps=[d.name for d in p['dumps']], maps=[dict(dump=n, descriptors=c, result=sb.error_name(code)) for n, c, code in p['maps']],
        checked=['facts against every dump', 'placement against every map', 'PIN.DAT and GUEST.DAT (structure, table, chunk and file digests) by the interface checks',
                 'PARAMS memory range and watchdog', 'file count, roles and sizes'],
        not_run=['the wrapper and the reporter (work item 4)', 'any hardware'] + (['the kernelcache path'] if kind == 'synthetic' else []))


def build(a, kind):
    """Cheap refusals first (the q1n1 tree, the options, --out, a missing authorisation), then the pipeline, and only then the files."""
    tool = None if a.no_manifest else q1n1_manifest_tool()
    if a.no_manifest and (a.base_tree or a.file):
        raise Refused('OPTIONS_BAD', '--base-tree and --file need the manifest: drop --no-manifest')
    out = check_out(a)
    p = prepare(a, kind, lambda: need_authorisation(a))
    digest = approve(a, p['record'])
    files = finish(p, digest)
    manifest = staged = None
    make_out(out)
    try:
        for n in NAMES:
            put(out, f'bundle/SP11/{n}', files[n])
        if tool:
            try:
                manifest, listed = tool.build([out / 'bundle/SP11' / n for n in NAMES])
            except ValueError as error:
                raise Refused('STAGE_REFUSED', f'q1n1\'s manifest tool: {error}') from None
            if [e[:3] for e in listed] != [(f'\\SP11\\{n}', len(files[n]), sha(files[n])) for n in NAMES]:
                raise Refused('STAGE_REFUSED', 'q1n1\'s manifest tool lists other files than the ones written')
            put(out, 'bundle/SP11/PAYLOAD.MAN', manifest)
            staged = stage_tree(a, out, p, files, manifest) if a.base_tree else None
        put(out, REPORT_NAME, (json.dumps(report_of(a, kind, p, files, digest, manifest, staged), indent=1, sort_keys=True) + '\n').encode())
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return out, files, manifest, staged


# ---- Command line ------------------------------------------------------------------------------------------------------------
def build_opts(p, kind, template=False):
    for flag, kw in (('--facts', dict(required=True, help='the local facts JSON, see tools/testdata/sp11-facts.example.json')),
                     ('--dump', dict(action='append', default=[], help='a firmware dump directory (default downloads/sp11/x*-run*)')),
                     ('--base', dict(required=True, help='guest_base: a multiple of 32 MiB (D1)')), ('--guest-mib', dict(type=int, required=True)),
                     ('--reserve-mib', dict(type=int, help='default: the loader\'s bootstrap_reserve() for this guest')),
                     ('--declared', dict(action='append', default=[], help='BASE:SIZE the runner declared: kept clear')),
                     ('--monitor', dict(required=True, help='SNAPMON.BIN (a placeholder until work item 4)')), ('--reporter', dict(required=True)),
                     ('--stores', dict(required=True, help='ram,el2_sysreg[,gic]: the store classes authorised (D4)')),
                     ('--time-box-s', dict(type=int, required=True, help='D5')), ('--descriptor', dict(help='a boot-ready board descriptor (real needs one)'))):
        p.add_argument(flag, **kw)
    if kind == 'synthetic':
        for flag, default in (('--image-mib', 115.5), ('--tables-mib', 16.0), ('--sptm-data-mib', 5.0)):
            p.add_argument(flag, type=float, default=default)
        p.add_argument('--seed', default='synthetic')
    else:
        p.add_argument('--kernelcache', required=True)
        p.add_argument('--panel', choices=('handed', 'withheld'), required=True, help='D9: SNAP_NO_FB')
        p.add_argument('--sptm-hvc-calls', choices=(0, 1), type=int, required=True, help='D9: the SPTM HVC kernel patch')
    if not template:
        for flag, kw in (('--out', dict(required=True)), ('--force', dict(action='store_true')), ('--allow-under', dict(action='append', default=[])),
                         ('--authorisation', dict(help='the approved record')), ('--authorisation-sha256', dict()),
                         ('--no-manifest', dict(action='store_true', help='the bundle files only: no PAYLOAD.MAN, no stage tree')),
                         ('--base-tree', dict(help='a staged tree that stage-sp11-boot-media.py verify accepts')),
                         ('--file', dict(nargs=3, action='append', default=[], metavar=('NAME', 'PATH', 'SHA256'))), ('--pins', dict()),
                         ('--no-pins', dict(action='store_true')), ('--plan-name', dict())):
            p.add_argument(flag, **kw)


def build_parser():
    ap = argparse.ArgumentParser(prog='sp11_stick_bundle_build.py', description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('facts-check')
    f.add_argument('--facts', required=True)
    f.add_argument('--dump', action='append', default=[])
    c = sub.add_parser('place-check')
    c.add_argument('--base', required=True)
    c.add_argument('--guest-mib', type=int, required=True)
    c.add_argument('--reserve-mib', type=int)
    c.add_argument('--facts')
    c.add_argument('--dump', action='append', default=[])
    c.add_argument('--declared', action='append', default=[])
    build_opts(sub.add_parser('synthetic'), 'synthetic')
    build_opts(sub.add_parser('real'), 'real')
    t = sub.add_parser('authorisation-template').add_subparsers(dest='kind', required=True)
    for kind in ('synthetic', 'real'):
        build_opts(t.add_parser(kind), kind, template=True)
    return ap


def run(a):
    if a.cmd == 'facts-check':
        print('\n'.join(facts_check(load_facts(a.facts), find_dumps(a.dump))))
    elif a.cmd == 'place-check':
        facts, dumps = (load_facts(a.facts) if a.facts else None), find_dumps(a.dump)
        place, declared = placement_of(a), declared_ranges(a.declared)
        avoid, fb = (facts['avoid'], (facts['fb_base'], facts['fb_bytes'])) if facts else ((), None)
        pin = bare_pin(place, avoid)
        if code := sb._pin_geometry(pin):      # PLACE_ALIGN, PLACE_LAYOUT, PLACE_LIMIT: the interface's placement arithmetic, before any map
            raise Refused(sb.error_name(code), 'the placement arithmetic of docs/SP11-STICK-BUNDLE.md section 5')
        results = check_maps(pin, dumps, fb, declared)
        print(f'PLACE base={place["guest_base"]:#x} guest_mib={place["guest_size"] // MIB} reserve_mib={place["reserve_size"] // MIB} '
              f'work_mib={place["work_size"] // MIB} end={place["place_base"] + place["place_size"]:#x}')
        print('\n'.join(f'MAP {n} descriptors={c} ' + ('PASS' if not code else f'REFUSED={sb.error_name(code)}') for n, c, code in results))
        print('\n'.join(proposals(dumps, place['guest_size'], place['reserve_size'], fb, avoid, declared)))
        failed = [sb.error_name(code) for _, _, code in results if code]
        print(f'RESULT maps={len(results) - len(failed)}/{len(results)}')
        if failed:
            raise Refused(failed[0], f'{len(failed)} of {len(results)} maps')
    elif a.cmd == 'authorisation-template':
        print(prepare(a, a.kind)['record'], end='')
    else:
        out, files, manifest, staged = build(a, a.cmd)
        print(f'BUILT kind={a.cmd} out={os.path.relpath(out, ROOT) if ROOT in out.parents else out}')
        print('\n'.join(f'FILE {n} {len(files[n])} sha256 {sha(files[n])}' for n in NAMES))
        print(f'FILE PAYLOAD.MAN {len(manifest)} sha256 {sha(manifest)}' if manifest else 'MANIFEST none (--no-manifest)')
        print('STAGE ' + (f'{len(staged["tree"])} files, {staged["status"]}, shell_pin {staged["shell_pin"]}' if staged else 'none'))
        print('HARDWARE=NOT RUN')
    return 0


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return run(a)
    except Refused as refusal:
        print(f'REFUSED={refusal.name}')
        print(f'detail: {refusal.detail}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
