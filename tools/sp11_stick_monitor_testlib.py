# SPDX-License-Identifier: MIT
"""Shared test support for the monitor entry of SNAPMON-M1.BIN (docs/SP11-STICK-MONITOR.md, section 9): an independent decoder of its record and an
independent renderer of its page lines (written from the document, not from the C), the host harness of tools/test-sp11-stick-monitor.c built with
AddressSanitizer and UndefinedBehaviorSanitizer around the REAL wrapper, GIC arm and put-back, monitor stage and reporter sources, a scenario writer
(the fake monitor's behaviour goes in a configuration file) and a parser of what the harness prints, and the properties a run is held to. Each
property returns its violations, so that a seeded-bad stage can be shown to break the check that matches its fault while the real one passes it.
Used by tools/test-sp11-stick-monitor.py, tools/test-sp11-stick-monitor-record.py and tools/test-sp11-stick-monitor-build.py. Nothing here touches
hardware; every file is written below build/. EXPERIMENTAL."""
import concurrent.futures
import dataclasses
import hashlib
import os
import struct
import subprocess
import threading
from pathlib import Path

import sp11_stick_gic_testlib as G
import sp11_stick_wrapper_testlib as T
from sp11_stick_wrapper_testlib import ROOT, TOOLS, sb

HARNESS_C = TOOLS / 'test-sp11-stick-monitor.c'
RECORD_H = ROOT / 'probes/sp11-stick-monitor-record.h'
MON_SOURCES = (*T.WRAPPER_SOURCES, 'probes/sp11-stick-gic.c', 'probes/sp11-stick-monitor.c')
FONT = G.FONT
OFFSET, IMAGE_BYTES = 0x4000, 0x8000                      # the fake monitor image in the harness's file: small, so that the file fits the model's slot
FAKE_IMAGE = bytes((i * 7 + (i >> 8) * 13 + 3) & 0xFF for i in range(IMAGE_BYTES))
FAKE_FILE = bytes(OFFSET) + FAKE_IMAGE
REC_AT, REC_BYTES, WINDOW = 0x12000, 512, 0x10000          # the record's offset in REPORT.DAT; the monitor's window
STACK_AT, STACK_BYTES, PATTERN = 0x41000, 0x40000, 0xC3A5C3A5C3A5C3A5
FILLER_AT, FILLER_BYTES = 0x13000, 0x2E000                 # REPORT.DAT between the monitor's record slot and the stack's area
QUARANTINE = (0x51554E01, 0x51554E02)
BOX_MAX = 5
PARAMS_BYTES, PARAMS_WATCHDOG, PARAMS_GPU, PARAMS_EARLY = 0x2410, 192, 0x2340, 0x23E0
ROWS = ('DAIF', 'SPSEL', 'VBAR_EL2', 'HCR_EL2', 'TPIDR_EL2', 'SCTLR_EL2', 'TCR_EL2', 'CNTHCTL_EL2', 'CNTHP_CTL_EL2', 'MDCR_EL2', 'VTTBR_EL2', 'VTCR_EL2',
        'VMPIDR_EL2')
ROW_MASKS = {'DAIF': 0x3C0, 'SPSEL': 1, 'CNTHP_CTL_EL2': 3}
OUTCOMES = {0: 'NONE', 64: 'PARAMS_PA', 65: 'PARAMS_SIZE', 66: 'PARAMS_EARLY_STOP', 67: 'PARAMS_GPU', 68: 'PARAMS_BOX', 69: 'IMAGE_SIZE',
            70: 'IMAGE_UNREADABLE', 71: 'IMAGE_DIGEST', 72: 'REPORT_UNWRITABLE', 73: 'WINDOW_DIRTY', 74: 'GIC_NOT_READY', 75: 'PIN_LABEL', 96: 'RETURNED',
            97: 'QUARANTINE', 98: 'RET_OTHER'}
OUT = type('OUT', (), {name: code for code, name in OUTCOMES.items()})
SUBSTAGES = ('NONE', 'CHECKS', 'SYNCED', 'ARMING', 'ARMED', 'CALLING', 'RETURNED', 'DONE')
FLAGS = dict(IC_SYNC=1, CALLED=2, RETURNED=4, STACK_S2=8, WINDOW_OVERRUN=16, REGS_CHANGED=32, STACK_FULL=64, GIC_NOT_RESTORED=128)
SEEDS = {'no-params-check': 'NO_PARAMS_CHECK', 'no-digest': 'NO_DIGEST', 'no-reg-check': 'NO_REG_CHECK', 'no-ic-sync': 'NO_IC_SYNC', 'stray-gic': 'STRAY_GIC',
         'wrong-args': 'WRONG_ARGS', 'no-s2': 'NO_S2', 'no-overrun-check': 'NO_OVERRUN_CHECK', 'store-after': 'STORE_AFTER', 'refuse-after': 'REFUSE_AFTER',
         'no-gic-finish': 'NO_GIC_FINISH', 'no-filler-scan': 'NO_FILLER_SCAN', 'no-filler-precheck': 'NO_FILLER_PRECHECK', 'flush-unwritable': 'FLUSH_UNWRITABLE',
         'ticks-early': 'TICKS_EARLY', 'no-window-clean': 'NO_WINDOW_CLEAN', 'no-record-clean': 'NO_RECORD_CLEAN', 'no-filler-clean': 'NO_FILLER_CLEAN',
         'no-label-check': 'NO_LABEL_CHECK'}
LABEL_PREFIX = 'M1RUN-'         # tools/sp11_stick_bundle_m1.py LABEL_PREFIX, spelled again here so that the harness does not take it from the code it tests
GIC_REFUSAL = 'GIC_NOT_READY'

# ---- The record, from the document's table (section 5), independent of the C header ------------------------------------------
HEADER = (('version', 8, 4), ('size', 12, 4), ('outcome', 0x10, 4), ('substage', 0x14, 4), ('flags', 0x18, 4), ('changed', 0x1C, 4), ('counter_hz', 0x20, 8),
          ('ticks_call', 0x28, 8), ('ticks_return', 0x30, 8), ('monitor_pa', 0x38, 8), ('monitor_offset', 0x40, 8), ('monitor_bytes', 0x48, 8),
          ('stack_top', 0x70, 8), ('stack_bytes', 0x78, 8), ('stack_used', 0x80, 8), ('ret', 0xB0, 8), ('status', 0xB8, 8), ('entered', 0xC0, 8),
          ('box_s', 0xC8, 8), ('early_stop_flags', 0xD0, 8), ('gpu_flags', 0xD8, 8))


@dataclasses.dataclass
class Rec:
    raw: bytes
    f: dict
    sha: bytes
    call_x: tuple
    ids: tuple
    rows: list                  # [(pre, post)] in ROWS order

    def __getattr__(self, name):
        try:
            return self.f[name]
        except KeyError:
            raise AttributeError(name) from None


def decode(raw):
    f = {name: int.from_bytes(raw[off:off + width], 'little') for name, off, width in HEADER}
    return Rec(raw, f, raw[0x50:0x70], struct.unpack_from('<5Q', raw, 0x88), struct.unpack_from('<5Q', raw, 0xE0),
               [struct.unpack_from('<2Q', raw, 0x108 + 16 * i) for i in range(len(ROWS))])


def check(raw):
    """What the reporter makes of a record: 0, or the first failing class (1 magic, 2 version, 3 size, 4 reserved, 5 values), in that order."""
    r = decode(raw)
    if raw[:8] != b'SNAPMNT1':
        return 1
    if r.version != 1:
        return 2
    if r.size != REC_BYTES:
        return 3
    if any(raw[0x1D8:0x200]):
        return 4
    bad = (r.outcome not in OUTCOMES or r.substage >= len(SUBSTAGES) or r.flags & ~0xFF or r.changed >> len(ROWS) or r.stack_used > r.stack_bytes
           or r.stack_bytes > 1 << 24)
    return 5 if bad else 0


def h(v):
    return '0X%X' % v


def changed_rows(r):
    return [name for i, name in enumerate(ROWS) if (r.rows[i][0] ^ r.rows[i][1]) & ROW_MASKS.get(name, (1 << 64) - 1)]


def render(r, which):
    """The page line `which` of the document's section 6, from the record alone, without its prefix; None when it does not apply."""
    ret = bool(r.flags & FLAGS['RETURNED'])
    if which == 'IMAGE':
        return (f'MON IMAGE OFF={h(r.monitor_offset)} BYTES={r.monitor_bytes} SHA={h(int.from_bytes(r.sha[:8], "big"))} BOX_S={r.box_s} '
                f'ESF={h(r.early_stop_flags)} GPUF={h(r.gpu_flags)}')
    if which == 'IDS0':
        return f'MON IDS MMFR2={h(r.ids[0])} MMFR4={h(r.ids[1])} REVIDR={h(r.ids[2])}'
    if which == 'IDS1':
        return f'MON IDS ISAR1={h(r.ids[3])} ISAR2={h(r.ids[4])}'
    if which == 'CALL':
        return 'MON CALL ' + ' '.join(f'X{i}={h(v)}' for i, v in enumerate(r.call_x)) + f' SP={h(r.stack_top)}'
    if which == 'RET' and ret:
        return (f'MON RET={h(r.ret)} STATUS={h(r.status)} ENTERED={r.entered} TICKS={r.ticks_return - r.ticks_call} '
                f'STACK={r.stack_used}/{r.stack_bytes}')
    if which == 'REGS' and ret:
        names = changed_rows(r)
        text = 'MON REGS RESTORED=NO' + f' CHANGED={h(r.changed)}' + ''.join(' ' + n for n in [name for name in ROWS if name in names][:3]) \
            if r.changed else 'MON REGS RESTORED=YES'
        return text + (' WINDOW OVERRUN' if r.flags & FLAGS['WINDOW_OVERRUN'] else '') + (' STACK FULL' if r.flags & FLAGS['STACK_FULL'] else '')
    if which == 'VERDICT':
        return f'MON VERDICT={OUTCOMES[r.outcome]} FLAGS={h(r.flags)} SUB={r.substage}'
    if which == 'QUARANTINE' and r.outcome == OUT.QUARANTINE:
        return 'MON QUARANTINE: THE REPORT IS PARTIAL AND RAM MUST NOT BE REUSED. POWER CYCLE'
    return None


PRE_CALL = ('IDS0', 'IDS1', 'IMAGE')
POST_CALL = ('RET', 'REGS', 'QUARANTINE')


# ---- The harness -------------------------------------------------------------------------------------------------------------
_objects, _lock = {}, threading.Lock()


def use_scratch(name):
    _objects.clear()
    return G.use_scratch(name)


def defines():
    """The macros of the monitor build, as tools/sp11_stick_wrapper_build.py passes them, for the harness's fake image."""
    digest = hashlib.sha256(FAKE_IMAGE).digest()
    return ['-DSNAP_SP11_STICK_GIC=1', '-DSNAP_SP11_STICK_MONITOR=1', '-DSW_GIC_DRY_REGS=0', f'-DSP11_MONITOR_OFFSET={OFFSET:#x}',
            f'-DSP11_MONITOR_BYTES={IMAGE_BYTES:#x}', *[f'-DSP11_MONITOR_SHA{i}={int.from_bytes(digest[8 * i:8 * i + 8], "little"):#x}ULL' for i in range(4)]]


def flags(tree, gic=None):
    return [*T.common_flags(tree, gic), *defines()]


def shared(tree, gic=None):
    key = ('shared', tuple(sorted((gic or {}).items())))
    with _lock:
        if key not in _objects:
            out = T.BUILD / 'monitor-harness' / ('shared-' + T.sha(repr(key).encode())[:8])
            out.mkdir(parents=True, exist_ok=True)
            fl = flags(tree, gic)
            jobs = {'harness': (['-DQ1N1_SP11_PAYLOAD=1'], HARNESS_C), 'text': (['-DQ1N1_SP11_PAYLOAD=1'], tree / 'platform/uefi/sp11-payload-text.c'),
                    'sha256': ([], tree / 'platform/uefi/sha256.c'), 'reporter': (['-Dpayload_entry=snaprep_entry'], ROOT / 'probes/sp11-stick-reporter.c')}
            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                for future in [pool.submit(T.clang, [*fl, *extra, '-c', str(src), '-o', str(out / f'{n}.o')], f'compiling {src.name}')
                               for n, (extra, src) in jobs.items()]:
                    future.result()
            _objects[key] = [str(out / f'{n}.o') for n in jobs]
        return _objects[key]


def harness(tree, name='base', extra=(), gic=None):
    """The executable of one variant: `extra` are -D flags (a seeded fault). Only the wrapper's sources are compiled again for a seed; the harness,
    the text layer, sha256 and the reporter are the base variant's objects."""
    key = (name, tuple(extra), tuple(sorted((gic or {}).items())))
    objs = shared(tree, gic)
    with _lock:
        if key in _objects:
            return _objects[key]
    out = T.BUILD / 'monitor-harness' / ('v-' + T.sha(repr(key).encode())[:10])
    out.mkdir(parents=True, exist_ok=True)
    fl = [*flags(tree, gic), *extra, '-Dpayload_entry=snapmon_entry']
    built = []
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        futures = []
        for src in MON_SOURCES:
            obj = out / (Path(src).stem + '.o')
            built.append(str(obj))
            futures.append(pool.submit(T.clang, [*fl, '-c', str(ROOT / src), '-o', str(obj)], f'compiling {src} ({name})'))
        for future in futures:
            future.result()
    exe = out / 'harness'
    T.clang([*T.SANITIZE, *built, *objs, '-o', str(exe)], f'linking the monitor harness ({name})')
    with _lock:
        _objects[key] = exe
    return exe


@dataclasses.dataclass
class Out:
    results: dict
    gic: dict
    marks: dict         # scenario name -> [(kind, gic_nev, base_nev, tick, a, b, c, d, e, f)] in the order printed
    replay: dict        # scenario name -> [dict(kind=..., k=..., field=int...)]
    state: dict
    recs: dict          # scenario name -> {point: the monitor record as REPORT.DAT held it at that point}
    dirty: dict         # scenario name -> {point: {part: dirty 64-byte lines of REPORT.DAT in that part, in the model's cache}}


def parse_extra(text):
    marks, replay, state, recs, dirty, cur_m, cur_r, cur_s, cur_c, cur_d = {}, {}, {}, {}, {}, [], [], {}, {}, {}
    for raw in text.splitlines():
        word, _, rest = raw.partition(' ')
        if word == 'MONMARK':
            f = rest.split()
            cur_m.append((f[0], int(f[1]), int(f[2]), int(f[3]), *[int(x, 16) for x in f[4:]]))
        elif word == 'MONREPLAY':
            f = rest.split()
            cur_r.append({'kind': f[0], **{k: int(v, 16) if k != 'k' and k not in ('pend', 'act') else int(v) for k, v in (x.split('=') for x in f[1:])}})
        elif word == 'MONSTATE':
            cur_s = {k: int(v, 16) for k, v in (x.split('=') for x in rest.split())}
        elif word == 'MONREC':
            point, _, hexed = rest.partition(' ')
            cur_c[point] = bytes.fromhex(hexed.strip())
        elif word == 'MONDIRTY':
            point, _, parts = rest.partition(' ')
            cur_d[point] = {k: int(v) for k, v in (x.split('=') for x in parts.split())}
        elif word == 'END':
            marks[rest], replay[rest], state[rest], recs[rest], dirty[rest] = cur_m, cur_r, cur_s, cur_c, cur_d
            cur_m, cur_r, cur_s, cur_c, cur_d = [], [], {}, {}, {}
    return marks, replay, state, recs, dirty


def run(exe, scenarios, cfg=None, timeout=1800):
    """Run [(name, scenario text)] in one harness process; `cfg` {name: 'key=value ...'} is the fake monitor's behaviour per scenario."""
    env = {**os.environ, 'ASAN_OPTIONS': 'detect_stack_use_after_return=0:detect_leaks=0', 'UBSAN_OPTIONS': 'print_stacktrace=1'}
    path = T.BUILD / f'monitor-cfg-{os.getpid()}-{threading.get_ident()}.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    T.write(path, ''.join(f'{name} {text}\n' for name, text in (cfg or {}).items()).encode())
    env['SP11_MON_CFG'] = str(path)
    done = subprocess.run([str(exe)], input=''.join(text for _, text in scenarios), capture_output=True, text=True, env=env, timeout=timeout)
    if done.returncode:
        raise AssertionError(f'the harness exited {done.returncode}:\n{done.stderr[-4000:]}\n{done.stdout[-1000:]}')
    marks, replay, state, recs, dirty = parse_extra(done.stdout)
    return Out(T.parse_output(done.stdout), G.parse_gic(done.stdout), marks, replay, state, recs, dirty)


def marked_pin(bundle, label=None):
    """PIN.DAT as the builder writes it for --m1-run: the marker in front of the facts' label (or `label` as given), the record digest recomputed."""
    return bundle.repacked_pin(build_label=(LABEL_PREFIX.encode() + bundle.fields['build_label'].rstrip(b'\0')) if label is None else label)


def world(bundle, directory, **over):
    """The base fake machine around `bundle` with the monitor's file in slot 0: the wrapper's part is zeros here (the wrapper is the host code),
    then the fake image, and the sizes that follow. The PIN.DAT is the bundle's with the label of an --m1-run build (the bundle is a --gic-stop one,
    so its own label lacks the marker: a case that wants that passes `pin=bundle.pin`). `over` replaces any of it."""
    return T.World(bundle, directory, **{'pin': marked_pin(bundle), 'images': {0: FAKE_FILE}, 'sizes': {0: len(FAKE_FILE), 1: 0x6000}, **over})
