# SPDX-License-Identifier: MIT
"""Shared test support for the SP11 GIC and stop stage (docs/SP11-STICK-GIC.md, section 9): an independent decoder of its record and an
independent renderer of its page lines (written from the document, not from the C), the host harness of tools/test-sp11-stick-gic.c built
with AddressSanitizer and UndefinedBehaviorSanitizer around the REAL wrapper, stage and reporter sources, a scenario writer and a parser
of what the model prints, and the properties a run is held to. Each property returns its violations, so that a seeded-bad stage can be
shown to break the check that matches its fault while the real one passes it. Used by tools/test-sp11-stick-gic.py,
tools/test-sp11-stick-gic-build.py and tools/test-sp11-stick-gic-qemu.py. Nothing here touches hardware; every file is written below
build/. EXPERIMENTAL."""
import concurrent.futures
import dataclasses
import os
import re
import struct
import subprocess
import threading
from pathlib import Path

import sp11_stick_wrapper_testlib as T
from sp11_stick_wrapper_testlib import ROOT, TOOLS, sb

HARNESS_C = TOOLS / 'test-sp11-stick-gic.c'
RECORD_H = ROOT / 'probes/sp11-stick-gic-record.h'
GIC_SOURCES = (*T.WRAPPER_SOURCES, 'probes/sp11-stick-gic.c', 'probes/sp11-stick-gic-dry.c')
FONT = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 :-./=?()_')
BOX_S, SLACK_US, CYCLE_US, WAIT_S = 2, 1000, 10000, 8      # the stage's defaults (probes/sp11-stick-gic.h)
CYCLES, MONITOR_WINDOW, REC_SLOT = 3, 0x10000, 0x1000     # acknowledged interrupts of window 1; what the monitor may write; the record's slot
REC_AT, REC_BYTES = sb.REPORT_OUT + MONITOR_WINDOW, 1456
GIC_REFUSAL = 'GIC_NOT_READY'
EL1_HCR, EL1_SCTLR = 0x80000010, 0x30500800               # q1n1's el1trap values, HCR_EL2 with IMO
FAKE_EL1_VECTORS = 0xCE0E0800                              # the model's EL1 table: inside the model's stack region, mapped and identity-mapped

# ---- The record, from the document's table (section 5), independent of the C header ------------------------------------------
HEADER = (('version', 8, 4), ('size', 12, 4), ('outcome', 0x10, 4), ('substage', 0x14, 4), ('planned', 0x18, 4), ('begun', 0x1C, 4),
          ('wrote', 0x20, 4), ('restore_failed', 0x24, 4), ('restore_retried', 0x28, 4), ('first_outcome', 0x2C, 4), ('done', 0x30, 4),
          ('cycle', 0x34, 4), ('counter_hz', 0x38, 8), ('timebox_ticks', 0x40, 8), ('slack_ticks', 0x48, 8), ('cycle_ticks', 0x50, 8),
          ('gicd_base', 0x58, 8), ('gicr_base', 0x60, 8), ('gicr_index', 0x68, 4), ('intid', 0x6C, 4), ('gicd_typer', 0x70, 4),
          ('gicd_iidr', 0x74, 4), ('gicr_waker', 0x78, 4), ('reserved0', 0x7C, 4), ('gicr_typer', 0x80, 8), ('icc_sre_el2', 0x88, 8),
          ('icc_ctlr_el1', 0x90, 8), ('cnthctl_el2', 0x98, 8), ('resume_spsr', 0xA0, 8))
IRQ_AT, IRQ_SIZE, IRQ_N = 0xA8, 0x48, 5
IRQ_FIELDS = (('cval', 0, 8), ('t_irq', 8, 8), ('elr', 0x10, 8), ('spsr', 0x18, 8), ('iar', 0x20, 8), ('hppir_vec', 0x28, 4), ('hppir_post', 0x2C, 4),
              ('ctl', 0x30, 4), ('flags', 0x34, 4), ('lat_ticks', 0x38, -8), ('lat_us', 0x40, -8))
WIN_AT, WIN_SIZE, WIN_N = 0x210, 0x88, 2
WIN_FIELDS = (('t_arm', 0, 8), ('deadline', 8, 8), ('irq_entries', 0x10, 4), ('other_entries', 0x14, 4), ('hvc_end', 0x18, 4), ('reserved', 0x1C, 4),
              ('exc_vector', 0x20, 8), ('exc_esr', 0x28, 8), ('exc_elr', 0x30, 8), ('exc_far', 0x38, 8), ('exc_spsr', 0x40, 8), ('dg_ctl', 0x48, 8),
              ('dg_hppir1', 0x50, 8), ('dg_ispendr0', 0x58, 4), ('dg_isenabler0', 0x5C, 4), ('dg_gicd_ctlr', 0x60, 4), ('dg_daif', 0x64, 4),
              ('dg_rpr', 0x68, 8), ('dg_pmr', 0x70, 8), ('dg_hcr', 0x78, 8), ('dg_spsr', 0x80, 8))
FIELDS = HEADER
SV_AT = 0x560                                              # version 3: the survey and the quarantine, after the register table (section 5)
SV_FIELDS = (('cntv_ctl', 0, 8), ('cntv_cval', 8, 8), ('cntp_ctl', 0x10, 8), ('cntp_cval', 0x18, 8), ('spi_lines', 0x20, 4), ('spi_enabled', 0x24, 4),
             ('spi_pending', 0x28, 4), ('spi_both', 0x2C, 4), ('spi_first', 0x30, 4), ('spi_second', 0x34, 4), ('ppi_prio', 0x38, 4),
             ('quarantined', 0x3C, 4), ('quar_rb', 0x40, 4), ('hppir_pre', 0x44, 4), ('flags', 0x48, 4), ('reserved', 0x4C, 4))
SVF = dict(TIMERS=1, SPI=2, PPI=4, QUAR=8, HPPIR=16)       # the survey flags: set when the word was read or written
SPI_LINES_MAX = 31
PPI_SURVEY = (23, 25, 27, 30)                              # the PPIs whose priorities the survey reads, in the order of the record's bytes
REGS = ('GICD_CTLR', 'IGROUPR0', 'ISENABLER0', 'ISPENDR0', 'ISACTIVER0', 'IPRIORITYR', 'ICFGR1', 'ICC_PMR', 'ICC_BPR1', 'ICC_IGRPEN1',
        'CNTHP_CTL', 'CNTHP_CVAL', 'VBAR_EL2', 'DAIF', 'ICC_RPR', 'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1')
REG_TABLE = 0x320
OUTCOMES = {0: 'NONE', 1: 'NOT_EL2', 2: 'SPSEL_NOT_1', 3: 'DAIF_NOT_MASKED', 4: 'IMO_CLEAR', 5: 'PIN_GIC', 6: 'COUNTER', 7: 'GICD_VIEW',
            8: 'GICR_NOT_FOUND', 9: 'GICR_ASLEEP', 10: 'PPI_OWNED', 11: 'SRE_CLEAR', 12: 'TIMER_OWNED', 13: 'WINDOW_UNWRITABLE',
            14: 'EL1_PCT_TRAPPED', 15: 'EL1_IMAGE', 16: 'G_NOT_HELD', 17: 'R_NOT_HELD', 18: 'C_NOT_HELD', 19: 'T_NOT_HELD',
            20: 'PRIO_NOT_BELOW_PMR', 21: 'EL1_NOT_HELD', 22: 'Q_NOT_HELD', 23: 'FOREIGN_PENDING', 32: 'IRQ_OK', 33: 'NO_IRQ', 34: 'WRONG_INTID',
            35: 'EXCEPTION', 36: 'STORM',
            37: 'CYCLE_LOST', 38: 'HPPIR_NOT_26', 39: 'NO_ISTATUS', 40: 'NOT_CLEAN', 41: 'FINAL_PENDING', 42: 'NO_IRQ_FROM_EL1',
            43: 'EL1_WRONG_INTID', 44: 'EL1_EXCEPTION', 48: 'NOT_RESTORED'}
OUT = type('OUT', (), {name: code for code, name in OUTCOMES.items()})
SUBSTAGES = ('NONE', 'READS', 'PRE_OK', 'STORING', 'ARMED', 'WINDOW', 'WINDOW_DONE', 'EL1_PREP', 'EL1_WINDOW', 'EL1_DONE', 'RESTORING', 'DONE')
CLASS_BITS = (('G', 1), ('R', 2), ('C', 4), ('T', 8), ('V', 16), ('E', 32))
REG_BIT = {name: 1 << i for i, name in enumerate(REGS)}
IRQF = dict(ENTERED=1, ACKED=2, PEND=4, ACT=8, FINAL=16, OK=32)
LAT_US_CLAMP = 99999999


class Row:
    """A named group of fields decoded from a slice of the record."""

    def __init__(self, raw, base, fields):
        self.f = {name: int.from_bytes(raw[base + off:base + off + abs(width)], 'little', signed=width < 0) for name, off, width in fields}

    def __getattr__(self, name):
        try:
            return self.f[name]
        except KeyError:
            raise AttributeError(name) from None


@dataclasses.dataclass
class Rec:
    """The record's fields, interrupts, windows, register rows and survey, decoded from its bytes."""
    raw: bytes
    f: dict
    regs: dict
    irq: list
    win: list
    sv: object

    def __getattr__(self, name):
        try:
            return self.f[name]
        except KeyError:
            raise AttributeError(name) from None

    def reg(self, name, which):
        return self.regs[name][which]


def decode(raw):
    f = {}
    for name, off, width in HEADER:
        f[name] = int.from_bytes(raw[off:off + abs(width)], 'little', signed=width < 0)
    f['magic'] = raw[:8]
    regs = {name: dict(zip(('old', 'new', 'rb', 'fin'), struct.unpack_from('<4Q', raw, REG_TABLE + 32 * i))) for i, name in enumerate(REGS)}
    irq = [Row(raw, IRQ_AT + IRQ_SIZE * i, IRQ_FIELDS) for i in range(IRQ_N)]
    win = [Row(raw, WIN_AT + WIN_SIZE * i, WIN_FIELDS) for i in range(WIN_N)]
    return Rec(raw, f, regs, irq, win, Row(raw, SV_AT, SV_FIELDS))


def classes(mask):
    return ''.join(letter for letter, bit in CLASS_BITS if mask & bit) or 'NONE'


E_MAGIC, E_VERSION, E_SIZE, E_RESERVED, E_VALUE = 1, 2, 3, 4, 5
LATENCY_CLAMP, TICKS_LIMIT, INTID_LIMIT = 1 << 40, 10 ** 12, 0xFFFFFF


def survey_bad(r):
    """The survey's rules from the document (section 5): a word whose flag is clear is zero, the counts are what the registers can hold, the two INTIDs
    agree with the count of INTIDs that are enabled and pending, and the PPI's own bit is never in the quarantine."""
    s, flags = r.sv, r.sv.flags
    most = 32 * SPI_LINES_MAX
    if flags & ~31:
        return True
    unflagged = ((SVF['TIMERS'], ('cntv_ctl', 'cntv_cval', 'cntp_ctl', 'cntp_cval')),
                 (SVF['SPI'], ('spi_lines', 'spi_enabled', 'spi_pending', 'spi_both', 'spi_first', 'spi_second')), (SVF['PPI'], ('ppi_prio',)),
                 (SVF['QUAR'], ('quarantined', 'quar_rb')), (SVF['HPPIR'], ('hppir_pre',)))
    if any(not flags & bit and any(getattr(s, name) for name in names) for bit, names in unflagged):
        return True
    if s.spi_lines > SPI_LINES_MAX or s.spi_enabled > most or s.spi_pending > most or s.spi_both > min(s.spi_enabled, s.spi_pending):
        return True
    if (s.spi_both == 0) != (s.spi_first == 0) or (s.spi_both < 2) != (s.spi_second == 0) or (s.spi_second and s.spi_first >= s.spi_second):
        return True
    if (s.spi_first and not 32 <= s.spi_first <= 1023) or (s.spi_second and s.spi_second > 1023):
        return True
    return s.hppir_pre > INTID_LIMIT or bool(r.intid < 32 and (s.quarantined >> r.intid) & 1)


def check(raw):
    """What the reporter makes of a record, from the document's section 5: 0, or the first failing class (magic, version, size, reserved,
    values) in that order."""
    r = decode(raw)
    if raw[:8] != b'SNAPGIC1':
        return E_MAGIC
    if r.version != 3:
        return E_VERSION
    if r.size != REC_BYTES:
        return E_SIZE
    if r.reserved0 or r.gicr_index > sb.GICR_MAX or any(w.reserved for w in r.win) or r.sv.reserved:
        return E_RESERVED
    bad = (r.outcome not in OUTCOMES or r.first_outcome not in OUTCOMES or r.substage >= len(SUBSTAGES) or r.planned & ~0x3F or r.begun & ~0x3F
           or any(v & ~0x3FFFF for v in (r.wrote, r.restore_failed, r.restore_retried)) or r.done > 1 or r.cycle > CYCLES + 1
           or not 16 <= r.intid <= 31 or r.timebox_ticks > TICKS_LIMIT or r.slack_ticks > TICKS_LIMIT or r.cycle_ticks > TICKS_LIMIT
           or any(q.flags & ~0x3F or q.hppir_vec > INTID_LIMIT or q.hppir_post > INTID_LIMIT or abs(q.lat_ticks) > LATENCY_CLAMP
                  or abs(q.lat_us) > LAT_US_CLAMP for q in r.irq)
           or any(w.irq_entries > 0xFFFF or w.other_entries > 0xFFFF or w.hvc_end > 1 for w in r.win) or survey_bad(r))
    return E_VALUE if bad else 0


def h(v):
    return '0X%X' % v


def timing(lat_ticks, slack):
    return 'EARLY' if lat_ticks < 0 else 'ON_TIME' if lat_ticks <= slack else 'LATE'


def entered(q):
    return bool(q.flags & IRQF['ENTERED'])


def clean(q):
    return q.hppir_post == 1023 and not q.flags & (IRQF['PEND'] | IRQF['ACT'])


def render(r, which):
    """The page line `which` of the document's section 6, from the record alone, without its prefix; None when it does not apply."""
    intid, o, regs = r.intid, r.outcome, r.regs
    ctlr = regs['GICD_CTLR']['old']
    w0, w1 = r.win
    bit = lambda value, n: (value >> n) & 1                                      # noqa: E731
    h8, h32 = (lambda v: h(v & 0xFF)), (lambda v: h(v & 0xFFFFFFFF))             # noqa: E731
    if which == 'GICD':
        return (f'GIC GICD CTLR={h32(ctlr)} ARE={bit(ctlr, 4)} DS={bit(ctlr, 6)} G1A={bit(ctlr, 1)} NEED_G={r.planned & 1} '
                f'TYPER={h32(r.gicd_typer)} IIDR={h32(r.gicd_iidr)}')
    if which == 'GICR':
        if o == OUT.GICR_NOT_FOUND:
            return f'GIC GICR NOT_FOUND FRAMES={r.gicr_index} FIRST={h(r.gicr_base)} TYPER0={h(r.gicr_typer)}'
        return (f'GIC GICR IDX={r.gicr_index} TYPER={h(r.gicr_typer)} WAKER={h32(r.gicr_waker)} ISEN={h32(regs["ISENABLER0"]["old"])} '
                f'PEND={bit(regs["ISPENDR0"]["old"], intid)} ACT={bit(regs["ISACTIVER0"]["old"], intid)}')
    if which == 'CPU':
        cfg = (regs['ICFGR1']['old'] >> (2 * ((intid - 16) & 15))) & 3
        return (f'GIC CPU SRE={h8(r.icc_sre_el2)} EOIMODE={bit(r.icc_ctlr_el1, 1)} PMR={h8(regs["ICC_PMR"]["old"])} '
                f'BPR1={h8(regs["ICC_BPR1"]["old"])} IGRPEN1={bit(regs["ICC_IGRPEN1"]["old"], 0)} PRIO={h8(regs["IPRIORITYR"]["old"])} '
                f'CFG={h8(cfg)} GRP={bit(regs["IGROUPR0"]["old"], intid)} CTL={h8(regs["CNTHP_CTL"]["old"])}')
    if which == 'IRQ':
        if not w0.irq_entries:
            return None
        lat = '/'.join(str(r.irq[k].lat_us) if entered(r.irq[k]) else '-' for k in range(4))
        ok = ''.join('-' if not entered(r.irq[k]) else '1' if r.irq[k].flags & IRQF['OK'] else '0' for k in range(4))
        final = 'NONE' if not entered(r.irq[3]) else 'CLEAR' if clean(r.irq[3]) else 'PEND'
        return f'GIC IRQ INTID={r.irq[0].iar & 0xFFFFFF} LAT_US={lat} OK={ok} FINAL={final}'
    if which == 'DIAG':
        if w0.irq_entries or not r.begun & 16:
            return None
        return (f'GIC DIAG CTL={h8(w0.dg_ctl)} HPPIR={h(w0.dg_hppir1 & 0xFFFFFF)} PEND={bit(w0.dg_ispendr0, intid)} ISEN={h32(w0.dg_isenabler0)} '
                f'GICD={h32(w0.dg_gicd_ctlr)} RPR={h8(w0.dg_rpr)} DAIF={h32(w0.dg_daif & 0x3C0)}')
    if which == 'EL1':
        if not w1.irq_entries:
            return None
        q = r.irq[4]
        return (f'GIC EL1 INTID={q.iar & 0xFFFFFF} LAT_US={q.lat_us} {timing(q.lat_ticks, r.slack_ticks)} ELR={h(q.elr)} SPSR={h32(q.spsr)} '
                f'OK={1 if q.flags & IRQF["OK"] else 0}')
    if which == 'DIAG1':
        if not w1.hvc_end:
            return None
        return (f'GIC DIAG1 CTL={h8(w1.dg_ctl)} HPPIR={h(w1.dg_hppir1 & 0xFFFFFF)} PEND={bit(w1.dg_ispendr0, intid)} ISEN={h32(w1.dg_isenabler0)} '
                f'GICD={h32(w1.dg_gicd_ctlr)} RPR={h8(w1.dg_rpr)} PMR={h8(w1.dg_pmr)}')
    if which == 'RESTORE':
        vbar = 'NONE' if not r.begun & 16 else 'OK' if regs['VBAR_EL2']['old'] == regs['VBAR_EL2']['fin'] else 'BAD'
        el1 = ('NONE' if not r.begun & 32 else 'OK' if all(regs[n]['old'] == regs[n]['fin'] for n in ('HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1')) else 'BAD')
        return (f'GIC RESTORE CLEAN={"NO" if r.restore_failed else "YES"} FAILED={h32(r.restore_failed)} RETRIED={h32(r.restore_retried)} '
                f'BEGUN={classes(r.begun)} VBAR={vbar} EL1={el1}')
    if which == 'VERDICT':
        first = f' FIRST={OUTCOMES[r.first_outcome]}' if r.first_outcome != o else ''
        return f'GIC VERDICT={OUTCOMES[o]}{first} OUTCOME={h(o)} SUB={SUBSTAGES[r.substage]}'
    if which == 'FRAMES':
        return f'GIC FRAMES GICD={h(r.gicd_base)} GICR={h(r.gicr_base)} INTID={intid} HZ={r.counter_hz}'
    if which == 'OLD':
        g = lambda n, f: f(regs[n]['old'])                                        # noqa: E731
        return (f'GIC OLD PRIO={g("IPRIORITYR", h8)} ICFGR1={g("ICFGR1", h32)} IGROUPR0={g("IGROUPR0", h32)} ISPEND={g("ISPENDR0", h32)} '
                f'ISACT={g("ISACTIVER0", h32)}')
    if which == 'NEW':
        return (f'GIC NEW GICD={h32(regs["GICD_CTLR"]["new"])} RB={h32(regs["GICD_CTLR"]["rb"])} ISEN={h32(regs["ISENABLER0"]["rb"])} '
                f'PRIO={h8(regs["IPRIORITYR"]["rb"])} PMR={h8(regs["ICC_PMR"]["rb"])} IGRPEN1={bit(regs["ICC_IGRPEN1"]["rb"], 0)}')
    if which == 'TIMER':
        return (f'GIC TIMER BOX_TICKS={r.timebox_ticks} CYCLE_TICKS={r.cycle_ticks} SLACK={r.slack_ticks}')
    if which.startswith('CYC'):
        k = int(which[3:])
        q = r.irq[k]
        if not entered(q):
            return None
        flags = f'{bit(q.ctl, 2)}{1 if q.flags & IRQF["PEND"] else 0}{1 if q.flags & IRQF["ACT"] else 0}'
        return f'GIC CYC{k} IAR={q.iar & 0xFFFFFF} HPPIR={q.hppir_vec} POST={q.hppir_post} F={flags} TICKS={q.lat_ticks} US={q.lat_us}'
    if which == 'EXC':
        if not w0.other_entries:
            return None
        return f'GIC EXC ENTRIES={w0.other_entries} VECTOR={w0.exc_vector & 0xFFFF} ESR={h32(w0.exc_esr)} ELR={h(w0.exc_elr)}'
    if which == 'EXC1':
        if not w1.other_entries:
            return None
        return (f'GIC EXC1 ENTRIES={w1.other_entries} VECTOR={h(w1.exc_vector & 0xFFFF)} ESR={h32(w1.exc_esr)} ELR={h(w1.exc_elr)} '
                f'SPSR={h32(w1.exc_spsr)}')
    if which == 'HVC':
        if not w1.hvc_end:
            return None
        return f'GIC HVC HCR={h32(w1.dg_hcr)} SPSR={h32(w1.dg_spsr)}'
    if which in ('HCR', 'VBAR1', 'SCTLR1'):
        if not r.begun & 32:
            return None
        row = regs[{'HCR': 'HCR_EL2', 'VBAR1': 'VBAR_EL1', 'SCTLR1': 'SCTLR_EL1'}[which]]
        return f'GIC {which} OLD={h(row["old"])} RB={h(row["rb"])} FIN={h(row["fin"])}'
    if which == 'FINAL':
        f = lambda n: regs[n]['fin']                                              # noqa: E731
        return (f'GIC FINAL GICD={h32(f("GICD_CTLR"))} ISEN={h32(f("ISENABLER0"))} PRIO={h8(f("IPRIORITYR"))} PMR={h8(f("ICC_PMR"))} '
                f'IGRPEN1={bit(f("ICC_IGRPEN1"), 0)} RPR={h8(f("ICC_RPR"))} DAIF={h32(f("DAIF") & 0x3C0)}')
    s, sf = r.sv, r.sv.flags
    if which == 'TIMERS':
        if not sf & SVF['TIMERS']:
            return None
        return f'GIC TIMERS VCTL={h8(s.cntv_ctl & 7)} VCVAL={h(s.cntv_cval)} PCTL={h8(s.cntp_ctl & 7)} PCVAL={h(s.cntp_cval)}'
    if which == 'SPI':
        if not sf & SVF['SPI']:
            return None
        return (f'GIC SPI LINES={s.spi_lines} EN={s.spi_enabled} PEND={s.spi_pending} BOTH={s.spi_both} FIRST={s.spi_first} SECOND={s.spi_second}')
    if which == 'PPIP':
        if not sf & SVF['PPI']:
            return None
        return 'GIC PPIP ' + ' '.join(f'P{intid}={h8(s.ppi_prio >> (8 * k))}' for k, intid in enumerate(PPI_SURVEY))
    if which == 'QUAR':
        if not sf & (SVF['QUAR'] | SVF['HPPIR']):
            return None
        mask = f'MASK={h32(s.quarantined)} DONE={h32(s.quar_rb)}' if sf & SVF['QUAR'] else 'MASK=NONE'
        return f'GIC QUAR {mask} HPPIR={s.hppir_pre if sf & SVF["HPPIR"] else "-"}'
    raise AssertionError(which)


WRAPPER_ORDER = ('GICD', 'GICR', 'CPU', 'IRQ', 'DIAG', 'EL1', 'DIAG1', 'RESTORE', 'VERDICT')
REPORTER_ORDER = ('VERDICT', 'FRAMES', 'GICD', 'GICR', 'CPU', 'OLD', 'SPI', 'PPIP', 'TIMERS', 'QUAR', 'NEW', 'TIMER', 'IRQ', 'CYC0', 'CYC1', 'CYC2', 'CYC3',
                  'DIAG', 'EXC', 'EL1', 'CYC4', 'DIAG1', 'HVC', 'EXC1', 'HCR', 'VBAR1', 'SCTLR1', 'FINAL', 'RESTORE')


def expected(r, order):
    return [line for line in (render(r, which) for which in order) if line]


def next_stores_line(r):
    return ('NEXT GIC STORES AFTER 8 S' + ''.join(f' {c}={int(bool(r.planned & b))}' for c, b in CLASS_BITS))


def next_window_line(r):
    g = r.regs
    return (f'NEXT GIC WINDOW {BOX_S} S GICD={h(g["GICD_CTLR"]["rb"] & 0xFFFFFFFF)} ISEN={h(g["ISENABLER0"]["rb"] & 0xFFFFFFFF)} '
            f'PRIO={h(g["IPRIORITYR"]["rb"] & 0xFF)} PMR={h(g["ICC_PMR"]["rb"] & 0xFF)} IGRPEN1={(g["ICC_IGRPEN1"]["rb"] >> 0) & 1} '
            f'HPPIR={r.sv.hppir_pre & 0xFFFFFF}')


def next_el1_line(r):
    return (f'NEXT GIC EL1 EXCURSION AFTER 8 S HCR={h(EL1_HCR)} SCTLR1={h(EL1_SCTLR)} VBAR1={h(FAKE_EL1_VECTORS)} PCT={r.cnthctl_el2 & 1}')


def header_enums():
    """(outcomes, substages) as the C header's X macros give them, to compare with the tables above."""
    text = RECORD_H.read_text()
    block = lambda name: re.search(rf'#define {name}\(X\)((?:[^\n]*\\\n)*[^\n]*)\nenum', text).group(1)  # noqa: E731
    pairs = lambda s: re.findall(r'X\((\w+), (\d+)\)', s)                                                  # noqa: E731
    return ({int(v): n for n, v in pairs(block('SG_OUTCOMES'))}, tuple(n for n, _ in pairs(block('SG_SUBSTAGES'))))


# ---- The harness -------------------------------------------------------------------------------------------------------------
_objects, _lock = {}, threading.Lock()


def use_scratch(name):
    """The wrapper test library's scratch directory for this test process, with the executables built into an earlier one forgotten."""
    _objects.clear()
    return T.use_scratch(name)


def flags(tree, gic=None):
    return [*T.common_flags(tree, gic), '-DSNAP_SP11_STICK_GIC=1']


def shared(tree, gic=None):
    """The harness (which includes the base harness), the runner's text layer, q1n1's sha256 and the GIC reporter: once per macro set."""
    key = ('shared', tuple(sorted((gic or {}).items())))
    with _lock:
        if key not in _objects:
            out = T.BUILD / 'gic-harness' / ('shared-' + T.sha(repr(key).encode())[:8])
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


def harness(tree, name='base', defines=(), gic=None):
    """The executable of one variant: `defines` are -D flags (a seeded fault, a time box). Only the stage is compiled again for a seed;
    the rest of the wrapper is the base variant's objects."""
    key = (name, tuple(defines), tuple(sorted((gic or {}).items())))
    objs = shared(tree, gic)
    with _lock:
        if key in _objects:
            return _objects[key]
    out = T.BUILD / 'gic-harness' / ('v-' + T.sha(repr(key).encode())[:10])
    out.mkdir(parents=True, exist_ok=True)
    fl = [*flags(tree, gic), *defines, '-Dpayload_entry=snapmon_entry']
    built = []
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        futures = []
        for src in GIC_SOURCES:
            obj = out / (Path(src).stem + '.o')
            built.append(str(obj))
            futures.append(pool.submit(T.clang, [*fl, '-c', str(ROOT / src), '-o', str(obj)], f'compiling {src} ({name})'))
        for future in futures:
            future.result()
    exe = out / 'harness'
    T.clang([*T.SANITIZE, *built, *objs, '-o', str(exe)], f'linking the GIC harness ({name})')
    with _lock:
        _objects[key] = exe
    return exe


@dataclasses.dataclass
class Gic:
    rec: bytes = None
    log: list = dataclasses.field(default_factory=list)       # (kind, name, value, lines, tick)
    line_ticks: dict = dataclasses.field(default_factory=dict)
    state: dict = dataclasses.field(default_factory=dict)     # name -> (initial, final) or value
    viol: list = dataclasses.field(default_factory=list)
    nviol: int = 0

    def r(self):
        return decode(self.rec)

    def writes(self):
        return [(name, value) for kind, name, value, _, _ in self.log if kind == 'W']

    def events(self, name):
        return [e for e in self.log if e[1] == name]

    def first_write(self):
        return next((e for e in self.log if e[0] == 'W'), None)


def parse_gic(text):
    found, cur = {}, Gic()
    for raw in text.splitlines():
        word, _, rest = raw.partition(' ')
        if word == 'GICREC':
            cur.rec = bytes.fromhex(rest)
        elif word == 'GICLOG':
            _, kind, name, value, lines, tick = rest.split()
            cur.log.append((kind, name, int(value, 16), int(lines), int(tick)))
        elif word == 'GICLINE':
            index, tick = rest.split()
            cur.line_ticks[int(index)] = int(tick)
        elif word == 'GICSTATE':
            for item in rest.split():
                key, _, value = item.partition('=')
                cur.state[key] = tuple(int(v, 16) for v in value.split('/')) if '/' in value else int(value, 16) if key == 'resume' else int(value)
        elif word == 'GICVIOL':
            cur.viol.append(rest)
        elif word == 'GICVIOLS':
            cur.nviol = int(rest)
        elif word == 'END':
            found[rest] = cur
            cur = Gic()
    return found


@dataclasses.dataclass
class Out:
    results: dict
    gic: dict


def run(exe, scenarios, timeout=1800):
    """Run [(name, scenario text)] in one harness process; returns Out(results by name, Gic by name)."""
    env = {**os.environ, 'ASAN_OPTIONS': 'detect_stack_use_after_return=0:detect_leaks=0', 'UBSAN_OPTIONS': 'print_stacktrace=1'}
    done = subprocess.run([str(exe)], input=''.join(text for _, text in scenarios), capture_output=True, text=True, env=env, timeout=timeout)
    if done.returncode:
        raise AssertionError(f'the harness exited {done.returncode}:\n{done.stderr[-4000:]}\n{done.stdout[-1000:]}')
    return Out(T.parse_output(done.stdout), parse_gic(done.stdout))


def scenario(name, w, gic=(), runs=('snapmon',), extra=(), between=()):
    """The scenario text of World `w` with GIC model lines and more base-harness lines (TAMPER, AT, ...) before the first RUN, and the GIC
    lines `between` after it (a mutation of the record between the monitor's run and the reporter's)."""
    lines = w.text(name, runs).split('\n')
    at = next(i for i, line in enumerate(lines) if line.startswith('RUN '))
    return '\n'.join([*lines[:at], *(f'GIC {g}' for g in gic), *extra, lines[at], *(f'GIC {g}' for g in between), *lines[at + 1:]])


# ---- What a run is held to ---------------------------------------------------------------------------------------------------
ALLOWED_GIC = {'GICD_CTLR', 'GICR_IGROUPR0', 'GICR_IPRIORITYR', 'GICR_ICFGR1', 'GICR_ISENABLER0', 'GICR_ICENABLER0', 'GICR_ICPENDR0'}
ALLOWED_EL2 = {'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1', 'ICC_EOIR1_EL1', 'ICC_DIR_EL1', 'CNTHP_CVAL_EL2', 'CNTHP_CTL_EL2', 'VBAR_EL2', 'DAIF',
               'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1'}
EL1_WRITES = {'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1'}


class Checks:
    """Each property returns its violations; a seeded fault breaks the matching one."""

    @staticmethod
    def stray_writes(g):
        bad = [f'a write to {name} is in no authorised class' for kind, name, *_ in g.log if kind == 'W' and name not in ALLOWED_GIC | ALLOWED_EL2]
        return bad + [f'the model refused an access: {v}' for v in g.viol]

    @staticmethod
    def _after_next(res, g, prefix, writes):
        lines = res.texts()
        index = next((i for i, t in enumerate(lines) if t.startswith(prefix)), None)
        first = next((e for e in g.log if e[0] == 'W' and e[1] in writes), None)
        if first is None:
            return []
        if index is None:
            return [f'no {prefix.removeprefix("SNAPMON ")} line was printed']
        found = []
        if first[3] <= index:
            found.append(f'the first store of {sorted(writes)[:2]} came with {first[3]} lines on the page, before the NEXT line (index {index})')
        elapsed = first[4] - g.line_ticks[index]
        if elapsed < WAIT_S * 19200000:
            found.append(f'only {elapsed} ticks between the NEXT line and that store')
        return found

    @staticmethod
    def next_before_store(res, g):
        """The NEXT line is on the page, and eight seconds of counter pass, before the first store of the stage."""
        first = g.first_write()
        if first is None:
            return []
        return Checks._after_next(res, g, 'SNAPMON NEXT GIC STORES AFTER 8 S', {first[1]})

    @staticmethod
    def next_before_el1(res, g):
        """The second NEXT line, and eight seconds, before the first write of the EL1 excursion (HCR_EL2, VBAR_EL1, SCTLR_EL1)."""
        return Checks._after_next(res, g, 'SNAPMON NEXT GIC EL1 EXCURSION AFTER 8 S', EL1_WRITES)

    @staticmethod
    def restored(g):
        """Every register of the model is as it was found, the PPI is idle and the timer is off."""
        found = [f'{k} is {v[1]:#x}, found {v[0]:#x}' for k, v in g.state.items() if isinstance(v, tuple) and v[0] != v[1]]
        found += ['the interrupt was left active'] if g.state.get('active') or g.state.get('fact') else []
        return found

    @staticmethod
    def timer_off(g):
        """The stage disables the timer when it is done with it, and a timer found enabled is put back after that: the last write is the disable, or, when
        the timer was found with enable bits, the disable and then those bits; and the timer ends as it was found."""
        found = g.state['ctl'][0] & 3
        ctl = [v & 3 for k, n, v, *_ in g.log if k == 'W' and n == 'CNTHP_CTL_EL2']
        return [] if ctl[-2 if found else -1:] == ([0, found] if found else [0]) and g.state['ctl'][1] == found else ['the timer was left enabled']

    @staticmethod
    def timer_quiet(g):
        """The EL2 timer is never enabled and unmasked with its deadline already in the past, which would assert the line if only until the next
        write: replays the writes of CNTHP_CVAL_EL2 and CNTHP_CTL_EL2 from the deadline the machine was found with."""
        cval, found = g.state['cval'][0], []
        for kind, name, value, _, tick in g.log:
            if kind == 'W' and name == 'CNTHP_CVAL_EL2':
                cval = value
            elif kind == 'W' and name == 'CNTHP_CTL_EL2' and value & 3 == 1 and cval <= tick:
                found.append(f'the timer was enabled and unmasked at tick {tick} with its deadline {cval} already past')
        return found

    @staticmethod
    def arm_overwrites_deadline(g):
        """The stage's arming overwrites the deadline it found: its first enable of the timer comes after a write of CNTHP_CVAL_EL2."""
        for kind, name, value, *_ in g.log:
            if kind == 'W' and name == 'CNTHP_CVAL_EL2':
                return []
            if kind == 'W' and name == 'CNTHP_CTL_EL2' and value & 1:
                return ['the timer was enabled with the deadline it was found with']
        return ['the timer was never armed']

    @staticmethod
    def quarantine(g, found, bit=1 << 26):
        """The document's rule for the redistributor's enable register (section 4): the bits found enabled but PPI 26's (`q`) are disabled by ONE write of
        ICENABLER0 with exactly them and enabled again by ONE write of ISENABLER0 with exactly them; PPI 26's own bit is written only when it was found
        disabled (a set, and a clear after the windows) and is never cleared when it was found enabled; no other bit is ever written."""
        q = found & ~bit
        sets = [v for k, n, v, *_ in g.log if k == 'W' and n == 'GICR_ISENABLER0']
        clears = [v for k, n, v, *_ in g.log if k == 'W' and n == 'GICR_ICENABLER0']
        own = [] if found & bit else [bit]
        bad = []
        if clears != ([q] if q else []) + own:
            bad.append(f'ICENABLER0 was written with {[hex(v) for v in clears]}, expected {[hex(v) for v in ([q] if q else []) + own]}')
        if sets != own + ([q] if q else []):
            bad.append(f'ISENABLER0 was written with {[hex(v) for v in sets]}, expected {[hex(v) for v in own + ([q] if q else [])]}')
        return bad + [f'ISENABLER0 ends {v[1]:#x}, found {v[0]:#x}' for k, v in g.state.items() if k == 'isenabler0' and v[0] != v[1]]

    @staticmethod
    def bit26_kept(g, found, bit=1 << 26):
        """PPI 26's own enable is never cleared while the stage uses it: no write of ICENABLER0 holds its bit unless the stage set it (found disabled)."""
        return [f'ICENABLER0 was written with {v:#x}, which holds PPI 26\'s bit, although it was found enabled' for k, n, v, *_ in g.log
                if k == 'W' and n == 'GICR_ICENABLER0' and v & bit and found & bit]

    @staticmethod
    def quarantine_first(g, found, bit=1 << 26):
        """The quarantine is the first store of the stage: nothing foreign may be signalled once group 1 is enabled."""
        first = g.first_write()
        q = found & ~bit
        if not q:
            return []
        return [] if first and first[1] == 'GICR_ICENABLER0' and first[2] == q else [f'the first store was {first[1:3] if first else None}, not ICENABLER0 {q:#x}']

    @staticmethod
    def quarantine_last(g, found, bit=1 << 26):
        """The enables the quarantine cleared go back last, after the CPU interface and the distributor are: the last write of the whole run is ISENABLER0 with exactly
        those bits."""
        q = found & ~bit
        writes = g.writes()
        if not q:
            return []
        return [] if writes and writes[-1] == ('GICR_ISENABLER0', q) else [f'the last write was {writes[-1] if writes else None}, not ISENABLER0 {q:#x}']

    @staticmethod
    def foreign_quiet_in_windows(g, found, bit=1 << 26):
        """While a window is open (IRQ unmasked at EL2 or at EL1) no enable bit but PPI 26's is set: replays the writes of both enable registers from what was
        found."""
        state, found_bad = found, []
        for kind, name, value, *_ in g.log:
            if kind == 'W' and name == 'GICR_ISENABLER0':
                state |= value
            elif kind == 'W' and name == 'GICR_ICENABLER0':
                state &= ~value
            elif kind == 'E' and name in ('WINDOW_OPEN', 'EXC_OPEN') and state & ~bit:
                found_bad.append(f'{name}: ISENABLER0 is {state:#x}, a bit but PPI 26\'s is enabled')
        return found_bad

    @staticmethod
    def pending_untouched(g, bit=1 << 26):
        """The pending state of another interrupt is never written: ICPENDR0 is written, if at all, with PPI 26's bit only."""
        return [f'ICPENDR0 was written with {v:#x}' for k, n, v, *_ in g.log if k == 'W' and n == 'GICR_ICPENDR0' and v & ~bit]

    @staticmethod
    def no_window_with_foreign_pending(g):
        """No window is opened, and nothing unmasks IRQ, after the arm left a foreign interrupt as the next one: a run whose arm was refused has no window."""
        return [f'{name} after the arm left INTID {g.r().sv.hppir_pre} next' for kind, name, *_ in g.log if kind == 'E' and name in ('WINDOW_OPEN', 'EXC_OPEN')]

    @staticmethod
    def eoi(g):
        """Every acknowledge is ended with the INTID that was acknowledged, after the level was dropped: a re-arm of the deadline (an acknowledged
        cycle) or CNTHP_CTL = 0 (the end of a window), the order of the monitor's tick path."""
        found = []
        for i, e in enumerate(g.log):
            if e[1] != 'ACK' or e[2] >= 1020:
                continue
            tail = [(x[1], x[2]) for x in g.log[i + 1:] if x[0] == 'W']
            names = [n for n, _ in tail]
            if 'ICC_EOIR1_EL1' not in names:
                found.append('an interrupt was acknowledged and never ended')
                continue
            end = names.index('ICC_EOIR1_EL1')
            if not {'CNTHP_CTL_EL2', 'CNTHP_CVAL_EL2'} & set(names[:end]):
                found.append('the level was not dropped (the deadline re-armed or the timer disabled) before the end of interrupt')
            if tail[end][1] != e[2]:
                found.append('the EOI is not of the acknowledged INTID')
        return found

    @staticmethod
    def settled(g, ticks=19200000 // 100000):
        """The PPI's state after an end of interrupt, a deactivation or CNTHP_CTL = 0 is read at least 10 us later: no read of HPPIR1 comes
        right after such a write within `ticks` of it (a level change may reach the GIC after the instruction that made it)."""
        found = []
        for before, e in zip(g.log, g.log[1:]):
            ended = before[0] == 'W' and (before[1] in ('ICC_EOIR1_EL1', 'ICC_DIR_EL1') or (before[1] == 'CNTHP_CTL_EL2' and before[2] == 0))
            if ended and e[0] == 'R' and e[1] == 'ICC_HPPIR1_EL1' and e[4] - before[4] < ticks:
                found.append(f'HPPIR1 was read {e[4] - before[4]} ticks after {before[1]}')
        return found

    @staticmethod
    def acks(g, expect):
        """The number of acknowledges that returned an interrupt: three for window 1, whose fourth deadline is taken without one, and one for
        window 2."""
        n = sum(1 for e in g.log if e[1] == 'ACK' and e[2] < 1020)
        return [] if n == expect else [f'{n} acknowledges, expected {expect}']

    @staticmethod
    def saved(g, expect):
        """The record's old values are what the model held when the stage read them (`expect`: {register: value})."""
        r = g.r()
        return [f'{n}.old is {r.reg(n, "old"):#x}, the machine held {v:#x}' for n, v in expect.items() if r.reg(n, 'old') != v]

    @staticmethod
    def one_interrupt(g):
        """No more entries than window 1's three cycles and final deadline plus window 2's one, and no hang."""
        return [] if g.state['deliveries'] <= CYCLES + 1 and not g.state['hang'] else [f'{g.state["deliveries"]} interrupts were taken, hang={g.state["hang"]}']
