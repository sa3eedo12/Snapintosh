# SPDX-License-Identifier: MIT
"""Host model of the SP11 NVMe takeover contract, and the written-out verdict tables the tests hold it to.

Support module of tools/test-nvme-window-hamoa.py; it lives with the layout fixtures it works on. Everything here is
EXPERIMENTAL (host-tested only) and synthetic: no address, GUID or identity below is an SP11 measurement.

check_model, prove_model, admin_model, io_model and target_from_board_model are the Python statement of what
compat/nvme-host.c does under -DSNAP_PLATFORM_SP11=1 (nvmeh_sp11_check, nvmeh_sp11_prove_endpoint,
nvmeh_sp11_admin_verdict, nvmeh_sp11_io_verdict, nvmeh_sp11_target_from_board), and `native` runs the C code itself
(tools/test-nvme-window-hamoa.c, built with ASan and UBSan). The tables hold the expected verdicts as literals, so the two
implementations cannot agree on a wrong answer unnoticed.
"""
import copy
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'tools' / 'fixtures' / 'sp11'
for path in (FIXTURES, ROOT / 'tools', ROOT / 'probes'):
    sys.path.insert(0, str(path))
import gptlayout as gl                      # noqa: E402
import board_descriptor as bd               # noqa: E402,F401
import boardinfo as bi                      # noqa: E402,F401

BUILD = ROOT / 'build' / 'test-nvme-window-hamoa'
TOOLS = ('/opt/homebrew/opt/llvm/bin',)
U64 = gl.U64
IPA = 1 << 36
MIB_LBAS = 2048
MAX_PROT = 32
TARGET_FIELDS = ('abi', 'known', 'segment', 'ecam_base', 'start_bus', 'end_bus', 'bdf', 'smmu_base', 'stream_base',
                 'rid_base', 'rid_count', 'eui64', 'generation', 'reserved0', 'reserved1', 'reserved2')
RECORD_FIELDS = ('abi', 'flags', 'generation', 'segment', 'bdf', 'ecam_base', 'cfg_pa', 'class_code', 'smmu_base',
                 'smmu_mode', 'stream_id', 'eui64', 'lba_bytes', 'metadata_bytes', 'nsze', 'first_usable',
                 'last_usable', 'protected_count')
PARAM_FIELDS = ('flags', 'bar', 'stride', 'dma_base', 'io_tail', 'io_head', 'io_phase', 'io_next_cid', 'lba_bytes',
                'first_lba', 'last_lba', 'max_transfer', 'timeout_ticks', 'write_first_lba', 'write_last_lba',
                'reserved')
K_ECAM, K_BDF, K_SMMU, K_STREAM, K_EUI64, K_GEN, K_ALL = 1, 2, 4, 8, 16, 32, 63
R_CLEAN, R_DMA, R_WRITE, R_ALL = 1, 2, 4, 7
F_ENABLE, F_WRITE = 1, 2
UNKNOWN_ORDER = ((K_ECAM, 'UNKNOWN_ECAM'), (K_BDF, 'UNKNOWN_BDF'), (K_SMMU, 'UNKNOWN_SMMU'),
                 (K_STREAM, 'UNKNOWN_STREAM'), (K_EUI64, 'UNKNOWN_EUI64'), (K_GEN, 'UNKNOWN_GENERATION'))
# The C code's names in enum order; the test holds this to the header and to the driver's own table.
CODE_NAMES = ('ABI RESERVED UNKNOWN_ECAM UNKNOWN_BDF UNKNOWN_SMMU UNKNOWN_STREAM UNKNOWN_EUI64 UNKNOWN_GENERATION '
              'UNKNOWN_DEVICE TARGET_INVALID RECORD_UNCLEAN STALE SEGMENT ECAM BUS_RANGE BDF CFG_ADDRESS CLASS EUI64 '
              'SMMU_BASE SMMU_MODE DMA_UNPROVEN STREAM LBA_FORMAT METADATA PARAMS GEOMETRY PROTECTED WINDOW_ORDER '
              'WINDOW_SIZE WINDOW_RANGE WINDOW_GPT WINDOW_ALIGN WINDOW_GUARD OVERLAP WRITE_WINDOW WRITE_UNAUTHORISED '
              'NO_DEVICE DEVICE_DISABLED HEADER_TYPE BAR ADMIN_PHASE ADMIN_OPCODE ADMIN_CNS ADMIN_LOG ADMIN_FEATURE '
              'ADMIN_SAVE IO_OPCODE IO_READONLY').split()
CODE = {name: -96 - i for i, name in enumerate(CODE_NAMES)}
NAME = {v: k for k, v in CODE.items()}
NAME[0] = 'OK'


def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / name) for d in TOOLS if (Path(d) / name).exists()), None)


# ------------------------------------------------------------------------------------- the native driver --------
_NATIVE = {}


def driver():
    """Build tools/test-nvme-window-hamoa.c once per run; SkipTest when there is no compiler."""
    if 'exe' not in _NATIVE:
        clang = find_tool('clang')
        if not clang:
            raise unittest.SkipTest('clang not found')
        BUILD.mkdir(parents=True, exist_ok=True)
        exe = BUILD / 'driver'
        cmd = [clang, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
               '-fno-sanitize-recover=undefined', '-DSNAP_PLATFORM_SP11=1', f'-I{ROOT / "compat"}',
               str(ROOT / 'tools' / 'test-nvme-window-hamoa.c'), str(ROOT / 'compat' / 'nvme-host.c'),
               str(ROOT / 'compat' / 'board-info.c'), '-o', str(exe)]
        built = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if built.returncode:
            raise AssertionError(f'host build of the SP11 driver failed:\n{built.stderr[-3000:]}')
        _NATIVE['exe'] = exe
    return _NATIVE['exe']


def native(mode, lines):
    """One answer line per input line from the sanitizer-built driver; any failure or sanitizer report raises."""
    proc = subprocess.run([str(driver()), mode], input='\n'.join(lines) + '\n', capture_output=True, text=True,
                          timeout=600)
    if proc.returncode:
        raise AssertionError(f'driver {mode} exited {proc.returncode}: {proc.stdout[-500:]} {proc.stderr[-2500:]}')
    out = proc.stdout.splitlines()
    if len(out) != len(lines):
        raise AssertionError(f'driver {mode} answered {len(out)} of {len(lines)} lines')
    return out


def hexw(words):
    return ' '.join(f'{w:x}' for w in words)


# ------------------------------------------------------------------------------------- the host model ------------
def words_of(case):
    """The (target, record, params) word line the driver reads; every word must be an unsigned 64-bit integer."""
    t, r, p = case['t'], case['r'], case['p']
    for part in (t, r, p):
        for k, v in part.items():
            if k != 'ext' and not 0 <= v <= U64:
                raise ValueError(k)
    ext = list(r['ext'])[:MAX_PROT] + [(0, 0)] * (MAX_PROT - len(r['ext']))
    return [t[k] for k in TARGET_FIELDS] + [r[k] for k in RECORD_FIELDS] + [x for e in ext for x in e] + \
           [p[k] for k in PARAM_FIELDS]


def cfg_offset(rid):
    """PCIe ECAM: bus 27:20, device 19:15, function 14:12 of the offset from the allocation base."""
    return (rid >> 8) << 20 | ((rid >> 3) & 0x1f) << 15 | (rid & 7) << 12


def check_model(case):
    """nvmeh_sp11_check, statement for statement (compat/nvme-host.c). Returns 'OK' or the reason name."""
    t, r, p = case['t'], case['r'], case['p']
    if t['abi'] != 1 or r['abi'] != 1:
        return 'ABI'
    if t['known'] & ~K_ALL or r['flags'] & ~R_ALL or t['reserved0'] | t['reserved1'] | t['reserved2']:
        return 'RESERVED'
    for bit, name in UNKNOWN_ORDER:
        if not t['known'] & bit:
            return name
    if (t['segment'] > 0xffff or t['start_bus'] > t['end_bus'] or t['end_bus'] > 255 or not t['ecam_base'] or
            t['ecam_base'] & 0xfffff or t['ecam_base'] >= IPA or ((t['end_bus'] + 1) << 20) > IPA - t['ecam_base'] or
            t['bdf'] > 0xffff or t['bdf'] >> 8 < t['start_bus'] or t['bdf'] >> 8 > t['end_bus'] or
            not t['smmu_base'] or t['smmu_base'] & 0xffff or t['smmu_base'] >= IPA - 0x20000 or
            t['rid_base'] > 0xffff or not t['rid_count'] or t['rid_count'] > 0x10000 - t['rid_base'] or
            t['stream_base'] > 0xffffffff - (t['rid_count'] - 1) or not t['eui64'] or not t['generation']):
        return 'TARGET_INVALID'
    if not r['flags'] & R_CLEAN:
        return 'RECORD_UNCLEAN'
    if r['generation'] != t['generation']:
        return 'STALE'
    if r['segment'] != t['segment']:
        return 'SEGMENT'
    if r['ecam_base'] != t['ecam_base']:
        return 'ECAM'
    if r['bdf'] > 0xffff:
        return 'BDF'
    if r['bdf'] >> 8 < t['start_bus'] or r['bdf'] >> 8 > t['end_bus']:
        return 'BUS_RANGE'
    if r['bdf'] != t['bdf']:
        return 'BDF'
    if r['cfg_pa'] != t['ecam_base'] + cfg_offset(t['bdf']):
        return 'CFG_ADDRESS'
    if r['class_code'] != 0x010802:
        return 'CLASS'
    if r['eui64'] != t['eui64']:
        return 'EUI64'
    if r['smmu_base'] != t['smmu_base']:
        return 'SMMU_BASE'
    if r['smmu_mode'] not in (1, 2):
        return 'SMMU_MODE'
    if not r['flags'] & R_DMA:
        return 'DMA_UNPROVEN'
    if (r['bdf'] < t['rid_base'] or r['bdf'] - t['rid_base'] >= t['rid_count'] or
            r['stream_id'] != t['stream_base'] + (r['bdf'] - t['rid_base'])):
        return 'STREAM'
    if r['lba_bytes'] != 512:
        return 'LBA_FORMAT'
    if r['metadata_bytes']:
        return 'METADATA'
    if p['lba_bytes'] != r['lba_bytes']:
        return 'PARAMS'
    if (r['nsze'] < 69 or r['nsze'] > U64 // 512 or r['first_usable'] < 34 or r['first_usable'] > r['last_usable'] or
            r['last_usable'] > r['nsze'] - 34):
        return 'GEOMETRY'
    if r['protected_count'] > MAX_PROT or not r['protected_count']:
        return 'PROTECTED'
    ext = r['ext'][:r['protected_count']]
    for first, last in ext:
        if first > last or first < r['first_usable'] or last > r['last_usable']:
            return 'PROTECTED'
    first, last = p['first_lba'], p['last_lba']
    if first > last or last == U64:
        return 'WINDOW_ORDER'
    size = last - first + 1
    if size < 4096:
        return 'WINDOW_SIZE'
    if last >= r['nsze']:
        return 'WINDOW_RANGE'
    if first < r['first_usable'] or last > r['last_usable']:
        return 'WINDOW_GPT'
    if any(first <= e_last and e_first <= last for e_first, e_last in ext):
        return 'OVERLAP'
    if (first | size) & (MIB_LBAS - 1):
        return 'WINDOW_ALIGN'
    if first - r['first_usable'] < MIB_LBAS or r['last_usable'] - last < MIB_LBAS:
        return 'WINDOW_GUARD'
    for e_first, e_last in ext:
        if (first - e_last - 1 if e_last < first else e_first - last - 1) < MIB_LBAS:
            return 'WINDOW_GUARD'
    if not p['flags'] & F_WRITE:
        return 'WRITE_WINDOW' if p['write_first_lba'] | p['write_last_lba'] else 'OK'
    if not r['flags'] & R_WRITE:
        return 'WRITE_UNAUTHORISED'
    if (p['write_first_lba'] > p['write_last_lba'] or p['write_first_lba'] < first + MIB_LBAS or
            p['write_last_lba'] > last - MIB_LBAS):
        return 'WRITE_WINDOW'
    return 'OK'


def prove_model(bar, cfg):
    """nvmeh_sp11_prove_endpoint over the endpoint's first 16 configuration dwords: 'OK' or the reason."""
    if cfg[0] == 0xffffffff or cfg[0] & 0xffff == 0xffff:
        return 'NO_DEVICE'
    if cfg[2] >> 8 != 0x010802:
        return 'CLASS'
    if cfg[1] & 6 != 6:
        return 'DEVICE_DISABLED'
    if (cfg[3] >> 16) & 0x7f:
        return 'HEADER_TYPE'
    if cfg[4] & 1:
        return 'BAR'
    kind = (cfg[4] >> 1) & 3
    if kind == 0:
        base = cfg[4] & ~0xf & 0xffffffff
    elif kind == 2:
        base = (cfg[4] & ~0xf & 0xffffffff) | cfg[5] << 32
    else:
        return 'BAR'
    return 'BAR' if not base or base != bar else 'OK'


def admin_model(phase, opcode, cdw10):
    """nvmeh_sp11_admin_verdict. Phase 0 is the host takeover, phase 1 the adopted monitor."""
    ident = cdw10 & 0xff
    if phase not in (0, 1):
        return 'ADMIN_PHASE'
    if opcode > 0xff:
        return 'ADMIN_OPCODE'
    if opcode == 0x06:
        return 'OK' if ident <= 1 else 'ADMIN_CNS'
    if opcode == 0x02:
        return 'OK' if 1 <= ident <= 3 else 'ADMIN_LOG'
    if opcode == 0x0a:
        return 'OK' if 1 <= ident <= 0x14 or ident in (0x16, 0x17) else 'ADMIN_FEATURE'
    if phase == 1:
        return 'ADMIN_OPCODE'
    if opcode in (0x00, 0x01, 0x04, 0x05):
        return 'OK'
    if opcode == 0x09:
        if ident != 0x07:
            return 'ADMIN_FEATURE'
        return 'ADMIN_SAVE' if cdw10 >> 31 else 'OK'
    return 'ADMIN_OPCODE'


def io_model(opcode, writable):
    if opcode in (0x00, 0x02):
        return 'OK'
    if opcode == 0x01:
        return 'OK' if writable else 'IO_READONLY'
    return 'IO_OPCODE'


def target_from_board_model(board, segment):
    """nvmeh_sp11_target_from_board over a validated descriptor Board: (reason, target fields)."""
    zero = dict.fromkeys(TARGET_FIELDS, 0) | {'abi': 1}
    if board.devices['nvme'][0] != 'present':
        return 'UNKNOWN_DEVICE', zero
    hit = [e for e in board.ecam or () if e[0] == segment]
    if not hit:
        return 'UNKNOWN_ECAM', zero
    v3 = [s for s in board.smmus or () if s[1] == 'smmu-v3']
    if len(v3) != 1:
        return 'UNKNOWN_SMMU', zero
    return 'OK', zero | dict(known=K_ECAM | K_SMMU, segment=segment, ecam_base=hit[0][1], start_bus=hit[0][2],
                             end_bus=hit[0][3], smmu_base=v3[0][2])


def identify_ns_page(nsze, formats, flbas, eui64=bytes.fromhex('1122334455667788')):
    """A synthetic Identify Namespace page. Offsets as in Linux include/linux/nvme.h struct nvme_id_ns (lines 434-471):
    NSZE 0, NLBAF 25, FLBAS 26, EUI64 120, LBAF array 128 (ms u16, ds u8, rp u8)."""
    page = bytearray(4096)
    struct.pack_into('<QQQ', page, 0, nsze, nsze, nsze)
    page[25], page[26] = len(formats) - 1, flbas
    page[120:128] = eui64
    for i, (ms, ds) in enumerate(formats):
        struct.pack_into('<HBB', page, 128 + 4 * i, ms, ds, 0)
    return bytes(page)


def record_from_identify(page):
    """The record fields Identify supplies. The CURRENT format only: the model has no way to pick another."""
    if len(page) != 4096:
        raise ValueError('an Identify page is 4096 bytes')
    nsze = struct.unpack_from('<Q', page, 0)[0]
    nlbaf, flbas = page[25], page[26]
    index = (flbas & 0xf) | ((flbas & 0x60) >> 1)       # Linux nvme_lbaf_index, nvme.h:642-643
    if index > nlbaf or nlbaf > 63:
        raise ValueError('the current format index is beyond NLBAF')
    ms, ds = struct.unpack_from('<HB', page, 128 + 4 * index)
    if not 9 <= ds <= 16:
        raise ValueError(f'LBA data size 2**{ds} is not a supported format')
    return dict(nsze=nsze, lba_bytes=1 << ds, metadata_bytes=ms, eui64=int.from_bytes(page[120:128], 'big'))


# ---------------------------------------------------------------------------------- the synthetic base case ------
LAYOUT_A = gl.load(FIXTURES / 'gpt-512-shrunk.json')
LAYOUT_B = gl.load(FIXTURES / 'gpt-512-free-at-end.json')
LAYOUT_4K = gl.load(FIXTURES / 'gpt-4096-shrunk.json')
WIN_A = (1342746624, 1476964351)        # 64 GiB, one guard (1 MiB) after the Windows volume
# Synthetic values. The ECAM window and the BAR are made up (the BAR sits inside the pcie6a memory window of the public
# device tree only to look plausible); the SMMU base and the stream window are the device tree's and the EL2 overlay's
# (docs/SP11-NVME-WINDOW.md), INFERRED to hold on a unit.
ECAM, BDF, BAR = 0x40000000, 0x0100, 0x70300000
GOOD_CFG = [0x0011face, 0x00100006, 0x01080201, 0, 0x70300004, 0, 0, 0] + [0] * 8
ESP, MSR, WINDOWS, RECOVERY = LAYOUT_A.partitions
GUARD_FIRST = WINDOWS.last + 1 + MIB_LBAS                  # first LBA that keeps a guard after the Windows volume
GUARD_LAST = RECOVERY.first - MIB_LBAS - 1                 # last LBA that keeps a guard before the recovery partition
WRAP_FIRST = U64 + 1 - 4096
END_B = (LAYOUT_B.last_usable + 1) // MIB_LBAS * MIB_LBAS       # the last aligned end inside the usable range
FREE_B = LAYOUT_B.partitions[-1].last + 1 + MIB_LBAS             # first LBA after the guard that follows recovery


def base_case(layout=LAYOUT_A, window=WIN_A, writable=False, others=None):
    first, last = window
    ext = list(others if others is not None else [(p.first, p.last) for p in layout.partitions])
    t = dict(abi=1, known=K_ALL, segment=0, ecam_base=ECAM, start_bus=0, end_bus=255, bdf=BDF,
             smmu_base=0x15400000, stream_base=0x60000, rid_base=0, rid_count=0x10000, eui64=0x1122334455667788,
             generation=7, reserved0=0, reserved1=0, reserved2=0)
    r = dict(abi=1, flags=R_CLEAN | R_DMA | (R_WRITE if writable else 0), generation=7, segment=0, bdf=BDF,
             ecam_base=ECAM, cfg_pa=ECAM + cfg_offset(BDF), class_code=0x010802, smmu_base=0x15400000, smmu_mode=2,
             stream_id=0x60000 + BDF, eui64=0x1122334455667788, lba_bytes=layout.block_size, metadata_bytes=0,
             nsze=layout.nsze, first_usable=layout.first_usable, last_usable=layout.last_usable,
             protected_count=len(ext), ext=ext + [(0, 0)] * (MAX_PROT - len(ext)))
    p = dict(flags=F_ENABLE | (F_WRITE if writable else 0), bar=BAR, stride=0, dma_base=0xA00000000, io_tail=3,
             io_head=3, io_phase=1, io_next_cid=9, lba_bytes=layout.block_size, first_lba=first, last_lba=last,
             max_transfer=131072, timeout_ticks=100000, write_first_lba=first + MIB_LBAS if writable else 0,
             write_last_lba=last - MIB_LBAS if writable else 0, reserved=0)
    return dict(t=t, r=r, p=p)


def edited(*edits, base=None):
    case = copy.deepcopy(base or base_case())
    for edit in edits:
        edit(case)
    return case


def setr(**kw):
    return lambda c: c['r'].update(kw)


def sett(**kw):
    return lambda c: c['t'].update(kw)


def setp(**kw):
    return lambda c: c['p'].update(kw)


def window(first, last):
    return setp(first_lba=first, last_lba=last)


def extent(i, first, last):
    return lambda c: c['r']['ext'].__setitem__(i, (first, last))


def writable(c):
    base = base_case(writable=True)
    c['p'], c['r']['flags'] = base['p'], base['r']['flags']


def verdicts(cases):
    """(model reasons, C reasons) for a list of cases, in one driver run."""
    return [check_model(c) for c in cases], [NAME[int(x)] for x in native('check', [hexw(words_of(c)) for c in cases])]


# ------------------------------------------------------------------------- expected-verdict tables --------------
TARGET_CASES = (                     # (name, expected verdict, edits to the valid base case)
    ('base is accepted', 'OK', ()),
    ('target ABI', 'ABI', (sett(abi=2),)), ('record ABI', 'ABI', (setr(abi=0),)),
    ('target reserved word', 'RESERVED', (sett(reserved1=1),)),
    ('unknown known bit', 'RESERVED', (sett(known=K_ALL | 64),)),
    ('unknown record flag', 'RESERVED', (setr(flags=R_CLEAN | R_DMA | 8),)),
    ('ECAM unknown', 'UNKNOWN_ECAM', (sett(known=K_ALL & ~K_ECAM),)),
    ('BDF unknown', 'UNKNOWN_BDF', (sett(known=K_ALL & ~K_BDF),)),
    ('SMMU unknown', 'UNKNOWN_SMMU', (sett(known=K_ALL & ~K_SMMU),)),
    ('stream mapping unknown', 'UNKNOWN_STREAM', (sett(known=K_ALL & ~K_STREAM),)),
    ('namespace identity unknown', 'UNKNOWN_EUI64', (sett(known=K_ALL & ~K_EUI64),)),
    ('run identity unknown', 'UNKNOWN_GENERATION', (sett(known=K_ALL & ~K_GEN),)),
    ('nothing known', 'UNKNOWN_ECAM', (sett(known=0),)),
    ('segment beyond 16 bits', 'TARGET_INVALID', (sett(segment=0x10000),)),
    ('bus range reversed', 'TARGET_INVALID', (sett(start_bus=5, end_bus=4),)),
    ('end bus 256', 'TARGET_INVALID', (sett(end_bus=256),)),
    ('ECAM base zero', 'TARGET_INVALID', (sett(ecam_base=0),)),
    ('ECAM base not 1 MiB aligned', 'TARGET_INVALID', (sett(ecam_base=ECAM + 0x80000),)),
    ('ECAM base at the 36-bit limit', 'TARGET_INVALID', (sett(ecam_base=IPA),)),
    ('ECAM window crosses the 36-bit limit', 'TARGET_INVALID', (sett(ecam_base=IPA - (1 << 20)),)),
    ('BDF beyond 16 bits', 'TARGET_INVALID', (sett(bdf=0x10000),)),
    ('pinned BDF outside the pinned bus range', 'TARGET_INVALID', (sett(start_bus=2),)),
    ('SMMU base zero', 'TARGET_INVALID', (sett(smmu_base=0),)),
    ('SMMU base not 64 KiB aligned', 'TARGET_INVALID', (sett(smmu_base=0x15400010),)),
    ('SMMU pages cross the 36-bit limit', 'TARGET_INVALID', (sett(smmu_base=IPA - 0x10000),)),
    ('RID base beyond 16 bits', 'TARGET_INVALID', (sett(rid_base=0x10000),)),
    ('RID count zero', 'TARGET_INVALID', (sett(rid_count=0),)),
    ('RID window runs past 16 bits', 'TARGET_INVALID', (sett(rid_base=1),)),
    ('StreamID beyond 32 bits', 'TARGET_INVALID', (sett(stream_base=1 << 32),)),
    ('StreamID window runs past 32 bits', 'TARGET_INVALID', (sett(stream_base=0xffffffff, rid_count=2),)),
    ('EUI64 pinned as zero', 'TARGET_INVALID', (sett(eui64=0),)),
    ('generation pinned as zero', 'TARGET_INVALID', (sett(generation=0),)),
    ('hand-off not clean', 'RECORD_UNCLEAN', (setr(flags=R_DMA),)),
    ('stale record: another run', 'STALE', (setr(generation=6),)),
    ('stale record: generation wrapped', 'STALE', (setr(generation=U64),)),
    ('another PCI segment', 'SEGMENT', (setr(segment=5),)),
    ('another ECAM base (the A16 one)', 'ECAM', (setr(ecam_base=0x7A0000000),)),
    ('record BDF beyond 16 bits', 'BDF', (setr(bdf=0x10000),)),
    ('record bus outside the MCFG range', 'BUS_RANGE', (sett(start_bus=1, end_bus=1), setr(bdf=0x0200))),
    ('another device on the right bus', 'BDF', (setr(bdf=0x0108),)),
    ('another bus inside the range', 'BDF', (setr(bdf=0x0200),)),
    ('config address of another BDF', 'CFG_ADDRESS', (setr(cfg_pa=ECAM + cfg_offset(0x0200)),)),
    ('config address off by one page', 'CFG_ADDRESS', (setr(cfg_pa=ECAM + (1 << 20) + 0x1000),)),
    ('not an NVMe class code', 'CLASS', (setr(class_code=0x010601),)),
    ('another namespace (disk swapped)', 'EUI64', (setr(eui64=0x1122334455667789),)),
    ('the A16 SMMU base', 'SMMU_BASE', (setr(smmu_base=0x15480000),)),
    ('SMMU mode unknown', 'SMMU_MODE', (setr(smmu_mode=0),)),
    ('SMMU aborting', 'SMMU_MODE', (setr(smmu_mode=3),)),
    ('SMMU stage 1', 'SMMU_MODE', (setr(smmu_mode=4),)),
    ('SMMU stream unconfigured', 'SMMU_MODE', (setr(smmu_mode=6),)),
    ('SMMU bypass is accepted', 'OK', (setr(smmu_mode=1),)),
    ('DMA window not proven', 'DMA_UNPROVEN', (setr(flags=R_CLEAN),)),
    ('StreamID off by one', 'STREAM', (setr(stream_id=0x60000 + BDF + 1),)),
    ('StreamID of the A16 formula', 'STREAM', (setr(stream_id=(5 << 16) | BDF),)),
    ('RID outside the map entry', 'STREAM', (sett(rid_count=0x100),)),
    ('RID below the map entry', 'STREAM', (sett(rid_base=0x200, rid_count=0x1000, stream_base=0x70000),)),
    ('4096-byte LBA format', 'LBA_FORMAT', (setr(lba_bytes=4096), setp(lba_bytes=4096))),
    ('LBA size zero', 'LBA_FORMAT', (setr(lba_bytes=0),)),
    ('LBA format with metadata', 'METADATA', (setr(metadata_bytes=8),)),
    ('params disagree about the LBA size', 'PARAMS', (setp(lba_bytes=4096),)),
    ('namespace smaller than the GPT', 'GEOMETRY', (setr(nsze=68),)),
    ('namespace too large for byte offsets', 'GEOMETRY', (setr(nsze=1 << 55),)),
    ('usable range starts inside the GPT', 'GEOMETRY', (setr(first_usable=33),)),
    ('usable range reversed', 'GEOMETRY', (setr(first_usable=LAYOUT_A.last_usable + 1),)),
    ('usable range overlaps the backup GPT', 'GEOMETRY', (setr(last_usable=LAYOUT_A.nsze - 33),)),
    ('too many protected extents', 'PROTECTED', (setr(protected_count=33),)),
    ('no protected extent at all', 'PROTECTED', (setr(protected_count=0),)),
    ('protected extent reversed', 'PROTECTED', (extent(0, 20000, 10000),)),
    ('protected extent beyond the usable range', 'PROTECTED', (extent(3, 1951066112, LAYOUT_A.last_usable + 1),)),
    ('protected extent inside the primary GPT', 'PROTECTED', (extent(0, 33, 4000),)),
    ('padding counted as a partition', 'PROTECTED', (setr(protected_count=5),)),
    ('zero size', 'WINDOW_ORDER', (window(WIN_A[0], WIN_A[0] - 1),)),
    ('zero size at LBA 0', 'WINDOW_ORDER', (window(0, U64),)),
    ('size wraparound', 'WINDOW_ORDER', (window(WRAP_FIRST, 4095),)),
    ('last LBA of the address space', 'WINDOW_ORDER', (window(WRAP_FIRST, U64),)),
    ('first after last', 'WINDOW_ORDER', (window(WIN_A[1], WIN_A[0]),)),
    ('one LBA', 'WINDOW_SIZE', (window(GUARD_FIRST, GUARD_FIRST),)),
    ('one MiB', 'WINDOW_SIZE', (window(GUARD_FIRST, GUARD_FIRST + 2047),)),
    ('two MiB is the smallest window', 'OK', (window(GUARD_FIRST, GUARD_FIRST + 4095),)),
    ('start beyond the disk end', 'WINDOW_RANGE', (window(LAYOUT_A.nsze + 4096, LAYOUT_A.nsze + 8191),)),
    ('ends one block past the disk', 'WINDOW_RANGE', (window(GUARD_FIRST, LAYOUT_A.nsze),)),
    ('starts at LBA 0, inside the primary GPT', 'WINDOW_GPT', (window(0, 134217727),)),
    ('contains the primary GPT header', 'WINDOW_GPT', (window(1, 8192),)),
    ('contains the backup entry array', 'WINDOW_GPT', (window(GUARD_FIRST, LAYOUT_A.last_usable + 1),)),
    ('contains the backup header', 'WINDOW_GPT', (window(GUARD_FIRST, LAYOUT_A.last_block),)),
    ('the whole disk', 'WINDOW_GPT', (window(0, LAYOUT_A.last_block),)),
    ('inside the EFI system partition', 'OVERLAP', (window(ESP.first + 4096, ESP.first + 8191),)),
    ('inside the reserved partition', 'OVERLAP', (window(MSR.first + 2048, MSR.first + 6143),)),
    ('inside the Windows volume', 'OVERLAP', (window(WINDOWS.first + 4096, WINDOWS.first + 134217727 + 4096),)),
    ('inside the recovery partition', 'OVERLAP', (window(RECOVERY.first + 4096, RECOVERY.first + 8191),)),
    ('containing the reserved partition', 'OVERLAP', (window(ESP.last + 1 - 2048, MSR.last + 2048),)),
    ('straddling the Windows volume by one block', 'OVERLAP', (window(WINDOWS.last, WINDOWS.last + 134217728),)),
    ('straddling the recovery partition by one block', 'OVERLAP', (window(GUARD_FIRST, RECOVERY.first),)),
    ('straddling the first block of the Windows volume', 'OVERLAP', (window(WINDOWS.first - 4095, WINDOWS.first),)),
    ('overlap beats alignment', 'OVERLAP', (window(WINDOWS.last - 1, WINDOWS.last + 8190),)),
    ('start one LBA past the guard', 'WINDOW_ALIGN', (window(GUARD_FIRST + 1, GUARD_FIRST + 134217728),)),
    ('size not a whole number of MiB', 'WINDOW_ALIGN', (window(GUARD_FIRST, GUARD_FIRST + 8191 + 1),)),
    ('unaligned in free space', 'WINDOW_ALIGN', (window(WIN_A[0] + 1, WIN_A[1] + 1),)),
    ('touching the Windows volume', 'WINDOW_GUARD', (window(WINDOWS.last + 1, WINDOWS.last + 134217728),)),
    ('touching the recovery partition', 'WINDOW_GUARD', (window(GUARD_FIRST, RECOVERY.first - 1),)),
    ('one LBA short of the guard needs an unaligned start', 'WINDOW_ALIGN', (window(GUARD_FIRST - 1, WIN_A[1]),)),
    ('exactly one guard from each neighbour', 'OK', (window(GUARD_FIRST, GUARD_LAST),)),
    ('one MiB further into the guard', 'WINDOW_GUARD', (window(GUARD_FIRST - 2048, GUARD_LAST),)),
    ('one MiB past the recovery guard', 'WINDOW_GUARD', (window(GUARD_FIRST, GUARD_LAST + 2048),)),
    ('flag clear, write window set', 'WRITE_WINDOW', (setp(write_first_lba=WIN_A[0] + 2048),)),
    ('flag clear, write end set', 'WRITE_WINDOW', (setp(write_last_lba=1),)),
    ('write flag without the per-run authorisation', 'WRITE_UNAUTHORISED',
     (writable, lambda c: c['r'].update(flags=R_CLEAN | R_DMA))),
    ('writable window is accepted', 'OK', (writable,)),
    ('write window one block into the first edge MiB', 'WRITE_WINDOW', (writable, setp(write_first_lba=WIN_A[0] + 2047))),
    ('write window one block into the last edge MiB', 'WRITE_WINDOW', (writable, setp(write_last_lba=WIN_A[1] - 2047))),
    ('write window reversed', 'WRITE_WINDOW',
     (writable, setp(write_first_lba=WIN_A[0] + 4096, write_last_lba=WIN_A[0] + 4095))),
    ('write flag with an empty write window', 'WRITE_WINDOW', (writable, setp(write_first_lba=0, write_last_lba=0))),
    ('write window the whole window', 'WRITE_WINDOW', (writable, setp(write_first_lba=WIN_A[0], write_last_lba=WIN_A[1]))),
    ('one block of write window', 'OK',
     (writable, setp(write_first_lba=WIN_A[0] + 2048, write_last_lba=WIN_A[0] + 2048))),
)
LAYOUT_B_CASES = (                   # (name, expected, (first, last)) over the free-at-end layout
    ('free space after recovery', 'OK', (FREE_B, FREE_B + 134217727)),
    ('largest window ends one guard before the backup GPT', 'OK', (FREE_B, END_B - MIB_LBAS - 1)),
    ('ends against the backup GPT guard', 'WINDOW_GUARD', (FREE_B, END_B - 1)),
    ('touching the recovery partition', 'WINDOW_GUARD', (LAYOUT_B.partitions[-1].last + 1, FREE_B + 134217727)),
    ('inside the recovery partition', 'OVERLAP', (LAYOUT_B.partitions[-1].first + 4096, FREE_B + 134217727)),
    ('ends in the last usable block', 'WINDOW_ALIGN', (FREE_B, LAYOUT_B.last_usable)),
    ('one block into the backup entry array', 'WINDOW_GPT', (FREE_B, LAYOUT_B.last_usable + 1)),
)


def offgrid_layout(windows_end_shift, recovery_start_shift):
    """LAYOUT_A with the Windows volume's end and the recovery partition's start moved off the MiB grid. Older
    partitioning tools leave such neighbours; the window is always on the grid, so the gap to one is 2047 or 2049
    blocks, not a multiple of the guard, and only these layouts tell a guard of exactly one MiB from one block less."""
    doc = json.loads((FIXTURES / 'gpt-512-shrunk.json').read_text(encoding='utf-8'))
    doc['partitions'][2]['last_lba'] += windows_end_shift
    doc['partitions'][3]['first_lba'] += recovery_start_shift
    return gl.validate(doc)


OFFGRID_CASES = (                    # (name, expected, (Windows end shift, recovery start shift), (first, last))
    ('one block short of the guard before the window', 'WINDOW_GUARD', (1, 0), (GUARD_FIRST, GUARD_LAST)),
    ('one block short of the guard after the window', 'WINDOW_GUARD', (0, -1), (GUARD_FIRST, GUARD_LAST)),
    ('one block of slack before the window', 'OK', (-1, 0), (GUARD_FIRST, GUARD_LAST)),
    ('one block of slack after the window', 'OK', (0, 1), (GUARD_FIRST, GUARD_LAST)),
    ('short on both sides', 'WINDOW_GUARD', (1, -1), (GUARD_FIRST, GUARD_LAST)),
    ('short only after the window', 'WINDOW_GUARD', (1, -1), (GUARD_FIRST + 2048, GUARD_LAST)),
    ('short only before the window', 'WINDOW_GUARD', (1, -1), (GUARD_FIRST, GUARD_LAST - 2048)),
    ('one MiB further from each side is enough', 'OK', (1, -1), (GUARD_FIRST + 2048, GUARD_LAST - 2048)),
)
FOURK_CASES = (                      # the generic model over the 4096-byte layout; alignment and guard are in bytes
    ('free space, one MiB (256 LBAs) after the Windows volume', 'OK', (167843328, 167843328 + 16777216 - 1)),
    ('touching the Windows volume', 'WINDOW_GUARD', (167843072, 167843072 + 16777216 - 1)),
    ('4 KiB past the guard is not 1 MiB aligned', 'WINDOW_ALIGN', (167843329, 167843329 + 16777216 - 1)),
    ('the 512-byte window numbers on a 4096-byte disk', 'WINDOW_RANGE', WIN_A),
    ('one MiB is below the minimum', 'WINDOW_SIZE', (167843328, 167843328 + 255)),
    ('two MiB is the smallest window', 'OK', (167843328, 167843328 + 511)),
    ('contains the header at LBA 1', 'WINDOW_GPT', (1, 1024)),
    ('contains the 4-block backup array', 'WINDOW_GPT', (167843328, 244190641)),
    ('overlaps the recovery partition by one block', 'OVERLAP', (167843328, 243883264)),
)
PROBE_CDW10 = (0, 1, 2, 3, 4, 7, 0xc, 0x14, 0x15, 0x16, 0x17, 0x18, 0x80, 0xff, 1 << 31 | 7, 0x7f << 16 | 2, 5 << 16 | 1)
ADMIN_CASES = (                      # (phase, opcode, cdw10, expected)
    (0, 0x06, 0, 'OK'), (0, 0x06, 1, 'OK'), (1, 0x06, 1, 'OK'), (0, 0x06, 5 << 16 | 1, 'OK'),
    (0, 0x06, 2, 'ADMIN_CNS'), (1, 0x06, 0x10, 'ADMIN_CNS'), (0, 0x06, 0xff, 'ADMIN_CNS'),
    (0, 0x02, 2, 'OK'), (1, 0x02, 1, 'OK'), (1, 0x02, 3, 'OK'), (0, 0x02, 0x7f << 16 | 2, 'OK'),
    (0, 0x02, 0, 'ADMIN_LOG'), (1, 0x02, 4, 'ADMIN_LOG'), (1, 0x02, 7, 'ADMIN_LOG'), (1, 0x02, 0x80, 'ADMIN_LOG'),
    (0, 0x0a, 0x0c, 'OK'), (1, 0x0a, 1, 'OK'), (1, 0x0a, 0x14, 'OK'), (1, 0x0a, 0x16, 'OK'), (1, 0x0a, 0x17, 'OK'),
    (1, 0x0a, 0, 'ADMIN_FEATURE'), (1, 0x0a, 0x15, 'ADMIN_FEATURE'), (1, 0x0a, 0x18, 'ADMIN_FEATURE'),
    (1, 0x0a, 0x80, 'ADMIN_FEATURE'), (1, 0x0a, 0xc0, 'ADMIN_FEATURE'), (1, 0x0a, 0xff, 'ADMIN_FEATURE'),
    (0, 0x09, 0x07, 'OK'), (0, 0x09, 1 << 31 | 7, 'ADMIN_SAVE'),
    (0, 0x09, 0x03, 'ADMIN_FEATURE'),            # LBA Range Type: describes geometry, never needed
    (0, 0x09, 0x06, 'ADMIN_FEATURE'), (0, 0x09, 0x0c, 'ADMIN_FEATURE'), (0, 0x09, 0x80, 'ADMIN_FEATURE'),
    (0, 0x09, 1 << 31 | 3, 'ADMIN_FEATURE'), (1, 0x09, 0x07, 'ADMIN_OPCODE'), (1, 0x09, 0x03, 'ADMIN_OPCODE'),
    (0, 0x00, 1, 'OK'), (0, 0x01, 1, 'OK'), (0, 0x04, 1, 'OK'), (0, 0x05, 1, 'OK'),
    (1, 0x00, 1, 'ADMIN_OPCODE'), (1, 0x01, 1, 'ADMIN_OPCODE'), (1, 0x04, 1, 'ADMIN_OPCODE'),
    (1, 0x05, 1, 'ADMIN_OPCODE'),
    *[(phase, opcode, 0, 'ADMIN_OPCODE') for phase in (0, 1) for opcode in (
        0x80,           # Format NVM: the only way to switch the LBA format
        0x84, 0x10, 0x11, 0x0d, 0x15, 0x81, 0x82, 0x14, 0x19, 0x1a, 0x1c, 0x1d, 0x1e, 0x7c, 0x08, 0x0c, 0x18, 0x86,
        0x03, 0x07, 0x0b, 0x0f, 0x12, 0x13, 0x1b, 0xc0, 0xd8, 0xff)],
    (2, 0x06, 0, 'ADMIN_PHASE'), (0xffffffff, 0x06, 0, 'ADMIN_PHASE'), (0, 0x106, 0, 'ADMIN_OPCODE'),
)
ENDPOINT_CASES = (                   # (name, expected, {config dword index: value}, BAR the params name)
    ('64-bit memory BAR', 'OK', {}, BAR), ('32-bit memory BAR', 'OK', {4: 0x70300000, 5: 0}, BAR),
    ('prefetchable bit is ignored', 'OK', {4: 0x7030000c}, BAR),
    ('multi-function endpoint is still type 0', 'OK', {3: 0x00800000}, BAR),
    ('no device: all ones', 'NO_DEVICE', {0: 0xffffffff}, BAR), ('vendor ffff', 'NO_DEVICE', {0: 0x0011ffff}, BAR),
    ('SATA class', 'CLASS', {2: 0x01060101}, BAR), ('NVM subclass but not the NVMe interface', 'CLASS', {2: 0x01080101}, BAR),
    ('memory space off', 'DEVICE_DISABLED', {1: 0x00100004}, BAR), ('bus master off', 'DEVICE_DISABLED', {1: 0x00100002}, BAR),
    ('both off', 'DEVICE_DISABLED', {1: 0x00100000}, BAR), ('a bridge header', 'HEADER_TYPE', {3: 0x00010000}, BAR),
    ('I/O-space BAR', 'BAR', {4: 0x70300001}, BAR), ('reserved BAR type 1', 'BAR', {4: 0x70300002}, BAR),
    ('reserved BAR type 3', 'BAR', {4: 0x70300006}, BAR), ('BAR zero', 'BAR', {4: 0x4, 5: 0}, BAR),
    ('BAR differs in the low dword', 'BAR', {4: 0x70304004}, BAR), ('BAR high dword set', 'BAR', {5: 1}, BAR),
    ('the params name another BAR', 'BAR', {}, BAR + 0x4000), ('the params name BAR zero', 'BAR', {}, 0),
)
