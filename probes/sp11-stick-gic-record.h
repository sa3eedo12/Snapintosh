/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_GIC_RECORD_H
#define SNAP_PROBES_SP11_STICK_GIC_RECORD_H
/* The record of the GIC and stop dry run (docs/SP11-STICK-GIC.md, sections 5 and 12): what the stage of SNAPMON-GIC.BIN read, stored,
 * measured and put back, written by the wrapper and read by the reporter (SNAPREP-GIC.BIN).
 *
 * Where it lives: an extension record. REPORT.DAT is the frozen head page (docs/SP11-STICK-BUNDLE.md section 6), then the window the
 * monitor writes through x0 (REPORT.DAT + SP11SB_REPORT_OUT; every store of the monitor lies below out + 0x10000: the static asserts of
 * probes/xnuboot/report_layout.c, lines 524 to 586, and REPORT_RESERVE in probes/run.py), then the extension records, which only the
 * wrapper writes and only the reporter reads. This record is the first, at REPORT.DAT + SG_REC_AT = 0x11000, in a slot of SG_REC_SLOT
 * bytes. Every extension record begins with an 8-byte ASCII tag, a u32 version and a u32 size, so that a reader skips what it does not
 * know and a zero tag means the stage did not run. The head page keeps its zero tail and the head's monitor_* fields stay zero when no
 * monitor is called, so sp11sb_report_check passes (a host test proves it).
 *
 * Shape. 182 little-endian 64-bit words, no padding, reserved words zero, an unknown value in a field a refusal (the reporter says the
 * record is damaged and prints nothing of it). Every register the stage touches has one row (old value read before any change, the value
 * written, the read-back after the write, the final value after the put-back), so "old and new values" and "restored" are read from one
 * table; after the table, the survey of version 3 (what the reads saw beyond the stage's own PPI: the EL1 timers, the SPIs, the other PPIs'
 * priorities) and the quarantine's own words. The stage keeps the record in its stack, stores it whole after every step (so a hang is named
 * in RAM by the last sub-stage and by the classes begun) and prints its page lines from the same bytes the reporter reads (sg_line below):
 * the two pages cannot disagree about a number.
 *
 * Evidence: EXPERIMENTAL; hardware NOT RUN. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK_GIC
#include "sp11-stick-common.h"

#define SG_REC_VERSION 3u       /* 2 was 1376 bytes, the table alone; 3 adds struct sg_survey after it */
#define SG_REC_MAGIC "SNAPGIC1"
#define SG_REC_BYTES 1456u
#define SG_MONITOR_WINDOW 0x10000u          /* what the monitor's report may occupy from its x0 (VERIFIED: the static asserts of report_layout.c) */
#define SG_REC_AT (SP11SB_REPORT_OUT + SG_MONITOR_WINDOW)       /* the offset in REPORT.DAT: 0x11000, the first extension slot */
#define SG_REC_SLOT 0x1000u                 /* the slot of this record: the next extension record starts at the next page */
#define SG_NAME_MAX 24u
_Static_assert(SG_REC_AT == 0x11000u && SG_REC_BYTES <= SG_REC_SLOT && SG_REC_AT + SG_REC_SLOT <= SP11SB_REPORT_BYTES,
               "the record is inside REPORT.DAT, in its own page, after the monitor's window");

/* Store classes of the stage, one bit each. The authorisation record (docs/SP11-STICK-GIC.md section 7) groups them: G and R are the
 * frozen format's GIC class, C, T, V and E its EL2 system register class. */
#define SG_CLASS_G 1u       /* the distributor's non-secure group 1 enable: GICD_CTLR */
#define SG_CLASS_R 2u       /* this CPU's redistributor: the quarantine of the other enabled interrupts first, then PPI 26 only: IGROUPR0, IPRIORITYR byte, ICFGR1, ISENABLER0 (only when found disabled), and their put-back, the quarantine's last */
#define SG_CLASS_C 4u       /* the CPU interface: ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1 */
#define SG_CLASS_T 8u       /* the EL2 physical timer: CNTHP_CVAL_EL2, CNTHP_CTL_EL2 */
#define SG_CLASS_V 16u      /* VBAR_EL2 and PSTATE.I, inside the window routines only */
#define SG_CLASS_E 32u      /* the EL1 excursion: HCR_EL2, VBAR_EL1, SCTLR_EL1 (and SPSR_EL2/ELR_EL2, which only the ERET uses) */
#define SG_CLASS_ALL (SG_CLASS_G | SG_CLASS_R | SG_CLASS_C | SG_CLASS_T | SG_CLASS_V | SG_CLASS_E)

/* The registers of the table, in the order of the rows. */
#define SG_REGS(X) \
    X(GICD_CTLR, 0) X(IGROUPR0, 1) X(ISENABLER0, 2) X(ISPENDR0, 3) X(ISACTIVER0, 4) X(IPRIORITYR, 5) X(ICFGR1, 6) X(ICC_PMR, 7) \
    X(ICC_BPR1, 8) X(ICC_IGRPEN1, 9) X(CNTHP_CTL, 10) X(CNTHP_CVAL, 11) X(VBAR_EL2, 12) X(DAIF, 13) X(ICC_RPR, 14) X(HCR_EL2, 15) \
    X(VBAR_EL1, 16) X(SCTLR_EL1, 17)
enum sg_reg_id {
#define X(name, value) SG_R_##name = value,
    SG_REGS(X)
#undef X
    SG_R_COUNT = 18
};
#define SG_R_MASK ((1u << SG_R_COUNT) - 1)

/* What the stage decided, in one number: a refusal before any store (1 to 15), a store that did not hold or an arm that left a foreign
 * interrupt pending (16 to 31, always after a store and so always with a put-back), what the windows showed (32 to 47) or a put-back that
 * failed (48). The number is the head's refusal_detail when the stage returns a refusal. The verdicts of the EL2 window come first in the
 * order the checks run; the last three are the EL1 window's. */
#define SG_OUTCOMES(X) \
    X(NONE, 0) X(NOT_EL2, 1) X(SPSEL_NOT_1, 2) X(DAIF_NOT_MASKED, 3) X(IMO_CLEAR, 4) X(PIN_GIC, 5) X(COUNTER, 6) X(GICD_VIEW, 7) \
    X(GICR_NOT_FOUND, 8) X(GICR_ASLEEP, 9) X(PPI_OWNED, 10) X(SRE_CLEAR, 11) X(TIMER_OWNED, 12) X(WINDOW_UNWRITABLE, 13) \
    X(EL1_PCT_TRAPPED, 14) X(EL1_IMAGE, 15) \
    X(G_NOT_HELD, 16) X(R_NOT_HELD, 17) X(C_NOT_HELD, 18) X(T_NOT_HELD, 19) X(PRIO_NOT_BELOW_PMR, 20) X(EL1_NOT_HELD, 21) \
    X(Q_NOT_HELD, 22) X(FOREIGN_PENDING, 23) \
    X(IRQ_OK, 32) X(NO_IRQ, 33) X(WRONG_INTID, 34) X(EXCEPTION, 35) X(STORM, 36) X(CYCLE_LOST, 37) X(HPPIR_NOT_26, 38) X(NO_ISTATUS, 39) \
    X(NOT_CLEAN, 40) X(FINAL_PENDING, 41) X(NO_IRQ_FROM_EL1, 42) X(EL1_WRONG_INTID, 43) X(EL1_EXCEPTION, 44) X(NOT_RESTORED, 48)
enum sg_outcome {
#define X(name, value) SG_OUT_##name = value,
    SG_OUTCOMES(X)
#undef X
    SG_OUT_LAST = 48
};
#define SG_OUT_IS_REFUSAL(o) ((o) >= 1 && (o) <= 15)
#define SG_OUT_IS_VERDICT(o) ((o) >= 32 && (o) <= 47)

/* The last point the stage reached: a hang is named by it. */
#define SG_SUBSTAGES(X) \
    X(NONE, 0) X(READS, 1) X(PRE_OK, 2) X(STORING, 3) X(ARMED, 4) X(WINDOW, 5) X(WINDOW_DONE, 6) X(EL1_PREP, 7) X(EL1_WINDOW, 8) \
    X(EL1_DONE, 9) X(RESTORING, 10) X(DONE, 11)
enum sg_substage {
#define X(name, value) SG_SUB_##name = value,
    SG_SUBSTAGES(X)
#undef X
    SG_SUB_LAST = 11
};

/* The delivery measured against the programmed deadline (CNTHP_CVAL_EL2): before it, within the slack after it, later. Not stored: it is
 * computed from the latency and the slack wherever it is shown, so that the two pages cannot disagree about it. */
#define SG_TIMING_EARLY 1u
#define SG_TIMING_ON_TIME 2u
#define SG_TIMING_LATE 3u

/* The interrupts the windows take, one row each: 0 to 2 the acknowledged cycles of the EL2 window (SG_CYCLES), 3 its final deadline taken
 * without an acknowledge, 4 the interrupt of the EL1 window. */
#define SG_CYCLES 3u
#define SG_IRQ_FINAL 3u
#define SG_IRQ_EL1 4u
#define SG_IRQ_N 5u
#define SG_WIN_EL2 0u
#define SG_WIN_EL1 1u
#define SG_WIN_N 2u
#define SG_IRQF_ENTERED 1u  /* the vector was entered */
#define SG_IRQF_ACKED 2u    /* ICC_IAR1_EL1 was read */
#define SG_IRQF_PEND 4u     /* the PPI is pending in the redistributor after the end of interrupt (after CNTHP_CTL = 0 for the final one) */
#define SG_IRQF_ACT 8u      /* and active */
#define SG_IRQF_FINAL 16u   /* taken without an acknowledge, as the monitor's stop path does (probes/xnuboot/async.c) */
#define SG_IRQF_OK 32u      /* every check of this interrupt passed (set by the stage's classifier, not by the vector) */
#define SG_IRQF_ALL 63u
#define SG_CNTHP_ISTATUS 4u /* CNTHP_CTL_EL2.ISTATUS */
#define SG_INTID_NONE 1023u /* ICC_HPPIR1_EL1 with nothing pending */

/* What struct sg_survey holds, one bit each: set when the stage read (or wrote) it, so that a zero is never taken for an observation. */
#define SG_SV_TIMERS 1u     /* the EL1 timers, read at EL2 (the dry run's build only: the monitor entry's arm half does not read them) */
#define SG_SV_SPI 2u        /* the SPIs' enable and pending registers of the distributor */
#define SG_SV_PPI 4u        /* the priorities of the other enabled PPIs */
#define SG_SV_QUAR 8u       /* the quarantine's write was made: its mask, and the enable register read back after it */
#define SG_SV_HPPIR 16u     /* ICC_HPPIR1_EL1 was read after every store and before any interrupt was unmasked */
#define SG_SV_ALL 31u
#define SG_SPI_LINES_MAX 31u    /* GICD_TYPER.ITLinesNumber is five bits: GICD_ISENABLER[1..31] and GICD_ISPENDR[1..31], INTIDs 32 to 1023 */
#define SG_SURVEY_PPI0 23u      /* INTIDs 23, 25, 27 and 30: the PPIs the firmware left enabled besides PPI 26 (boot 12, OBSERVED); SGI 0 is the fifth enabled bit */
#define SG_SURVEY_PPI1 25u
#define SG_SURVEY_PPI2 27u      /* the EL1 virtual timer's: the unit's GTDT gives Virtual EL1 GSIV 27 (OBSERVED) */
#define SG_SURVEY_PPI3 30u

struct sg_reg {
    uint64_t old;       /* read before any change */
    uint64_t new;       /* what the stage wrote, 0 when it wrote nothing */
    uint64_t rb;        /* read right after the write */
    uint64_t fin;       /* read after the put-back */
};

struct sg_irq {
    uint64_t cval;      /* +0x00 the deadline programmed for this interrupt (CNTHP_CVAL_EL2, absolute CNTPCT) */
    uint64_t t_irq;     /* +0x08 CNTPCT_EL0 at the vector entry; 0 until it came */
    uint64_t elr;       /* +0x10 ELR_EL2: the interrupted instruction */
    uint64_t spsr;      /* +0x18 SPSR_EL2: the interrupted state (bit 7 is I, bits 3:0 the exception level and stack) */
    uint64_t iar;       /* +0x20 what ICC_IAR1_EL1 returned; 0 when there was no acknowledge */
    uint32_t hppir_vec, hppir_post;     /* +0x28 ICC_HPPIR1_EL1 at the entry, before any acknowledge; and after the end of interrupt */
    uint32_t ctl, flags;                /* +0x30 CNTHP_CTL_EL2 at the entry (ISTATUS); SG_IRQF_* */
    int64_t lat_ticks;  /* +0x38 t_irq - cval */
    int64_t lat_us;     /* +0x40 in microseconds */
};

struct sg_win {
    uint64_t t_arm, deadline;           /* +0x00 CNTPCT_EL0 when the window's timer was armed; the window's last tick */
    uint32_t irq_entries, other_entries;        /* +0x10 vector entries: the interrupt's, and every other */
    uint32_t hvc_end, reserved;         /* +0x18 the EL1 stub's HVC #0 was taken (its counter bound ended with no interrupt); zero */
    uint64_t exc_vector, exc_esr, exc_elr, exc_far, exc_spsr;   /* +0x20 the first entry of any other vector */
    uint64_t dg_ctl, dg_hppir1;         /* +0x48 CNTHP_CTL_EL2 (ISTATUS) and ICC_HPPIR1_EL1 when the window ended with no interrupt */
    uint32_t dg_ispendr0, dg_isenabler0;        /* +0x58 */
    uint32_t dg_gicd_ctlr, dg_daif;     /* +0x60 */
    uint64_t dg_rpr, dg_pmr, dg_hcr, dg_spsr;   /* +0x68 */
};

struct sg_survey {      /* version 3: what the reads saw beyond the stage's own PPI, and the quarantine; SG_SV_* says which words are observations */
    uint64_t cntv_ctl, cntv_cval, cntp_ctl, cntp_cval;     /* +0x00 CNTV_CTL_EL0, CNTV_CVAL_EL0, CNTP_CTL_EL0, CNTP_CVAL_EL0 as read at EL2 */
    uint32_t spi_lines, spi_enabled;    /* +0x20 ITLinesNumber as used; INTIDs enabled in GICD_ISENABLER[1..lines] */
    uint32_t spi_pending, spi_both;     /* +0x28 INTIDs pending in GICD_ISPENDR[1..lines]; INTIDs that are both */
    uint32_t spi_first, spi_second;     /* +0x30 the first two INTIDs that are enabled and pending, 0 when there are fewer */
    uint32_t ppi_prio, quarantined;     /* +0x38 IPRIORITYR bytes of INTIDs 23, 25, 27 and 30, the first in the low byte; the ISENABLER0 bits written to ICENABLER0 (never PPI 26's) */
    uint32_t quar_rb, hppir_pre;        /* +0x40 ISENABLER0 read right after that write; ICC_HPPIR1_EL1 after every store, with IRQ still masked */
    uint32_t flags, reserved;           /* +0x48 SG_SV_*; zero */
};

struct sw_gic_rec {
    uint8_t magic[8];                           /* 0x000 SG_REC_MAGIC */
    uint32_t version, size;                     /* 0x008 */
    uint32_t outcome, substage;                 /* 0x010 */
    uint32_t planned, begun;                    /* 0x018 SG_CLASS_*: decided from the reads, and entered (set before the first store of the class) */
    uint32_t wrote, restore_failed;             /* 0x020 SG_R_* bits: written, and still not as found after the put-back and its one retry */
    uint32_t restore_retried, first_outcome;    /* 0x028 SG_R_* bits that needed the one more write; the outcome before the put-back */
    uint32_t done, cycle;                       /* 0x030 the window's exit flag, set by a handler; the EL2 window's next cycle (SG_CYCLES is the final one) */
    uint64_t counter_hz, timebox_ticks, slack_ticks, cycle_ticks;       /* 0x038 */
    uint64_t gicd_base, gicr_base;              /* 0x058 the frames the stage used: the distributor, this CPU's redistributor */
    uint32_t gicr_index, intid;                 /* 0x068 which of the pinned frames matched; the PPI */
    uint32_t gicd_typer, gicd_iidr;             /* 0x070 */
    uint32_t gicr_waker, reserved0;             /* 0x078 */
    uint64_t gicr_typer;                        /* 0x080 */
    uint64_t icc_sre_el2, icc_ctlr_el1;         /* 0x088 */
    uint64_t cnthctl_el2, resume_spsr;          /* 0x098 CNTHCTL_EL2 as read (EL1PCTEN decides the EL1 window); the SPSR_EL2 the excursion returns with */
    struct sg_irq irq[SG_IRQ_N];                /* 0x0A8 */
    struct sg_win win[SG_WIN_N];                /* 0x210 */
    struct sg_reg reg[SG_R_COUNT];              /* 0x320 */
    struct sg_survey sv;                        /* 0x560 */
};
_Static_assert(sizeof(struct sg_reg) == 32 && sizeof(struct sg_irq) == 0x48 && sizeof(struct sg_win) == 0x88 && sizeof(struct sg_survey) == 0x50,
               "a row is four words, an interrupt nine, a window seventeen, the survey ten");
_Static_assert(sizeof(struct sw_gic_rec) == SG_REC_BYTES && SG_REC_BYTES % 8 == 0, "the record is 182 words with no padding");
_Static_assert(offsetof(struct sw_gic_rec, outcome) == 0x10 && offsetof(struct sw_gic_rec, done) == 0x30 &&
               offsetof(struct sw_gic_rec, counter_hz) == 0x38 && offsetof(struct sw_gic_rec, gicd_base) == 0x58 &&
               offsetof(struct sw_gic_rec, gicr_typer) == 0x80 && offsetof(struct sw_gic_rec, cnthctl_el2) == 0x98 &&
               offsetof(struct sw_gic_rec, irq) == 0xA8 && offsetof(struct sw_gic_rec, win) == 0x210 &&
               offsetof(struct sw_gic_rec, reg) == 0x320 && offsetof(struct sw_gic_rec, sv) == 0x560,
               "record group starts: a padding byte before one would shift every later field");
#define SW_GIC_REC_DONE 0x30        /* offsetof(struct sw_gic_rec, done): the window routines poll it, the handlers set it */

#define SG_U32(b, f) sp11sb_rd32((b) + offsetof(struct sw_gic_rec, f))
#define SG_U64(b, f) sp11sb_rd64((b) + offsetof(struct sw_gic_rec, f))
#define SG_REG_AT(b, id) ((b) + offsetof(struct sw_gic_rec, reg) + sizeof(struct sg_reg) * (id))
#define SG_REG_OLD(b, id) sp11sb_rd64(SG_REG_AT(b, id) + offsetof(struct sg_reg, old))
#define SG_REG_NEW(b, id) sp11sb_rd64(SG_REG_AT(b, id) + offsetof(struct sg_reg, new))
#define SG_REG_RB(b, id) sp11sb_rd64(SG_REG_AT(b, id) + offsetof(struct sg_reg, rb))
#define SG_REG_FIN(b, id) sp11sb_rd64(SG_REG_AT(b, id) + offsetof(struct sg_reg, fin))
#define SG_IRQ_AT(b, i) ((b) + offsetof(struct sw_gic_rec, irq) + sizeof(struct sg_irq) * (i))
#define SG_IRQ_U64(b, i, f) sp11sb_rd64(SG_IRQ_AT(b, i) + offsetof(struct sg_irq, f))
#define SG_IRQ_U32(b, i, f) sp11sb_rd32(SG_IRQ_AT(b, i) + offsetof(struct sg_irq, f))
#define SG_WIN_AT(b, w) ((b) + offsetof(struct sw_gic_rec, win) + sizeof(struct sg_win) * (w))
#define SG_WIN_U64(b, w, f) sp11sb_rd64(SG_WIN_AT(b, w) + offsetof(struct sg_win, f))
#define SG_WIN_U32(b, w, f) sp11sb_rd32(SG_WIN_AT(b, w) + offsetof(struct sg_win, f))
#define SG_SV_AT(b) ((b) + offsetof(struct sw_gic_rec, sv))
#define SG_SV_U64(b, f) sp11sb_rd64(SG_SV_AT(b) + offsetof(struct sg_survey, f))
#define SG_SV_U32(b, f) sp11sb_rd32(SG_SV_AT(b) + offsetof(struct sg_survey, f))

/* Names as a table of characters, as the interface's: nothing here needs a relocation to be printed. */
struct sg_name_row { uint8_t value; char name[SG_NAME_MAX + 1]; };
static const struct sg_name_row sg_outcomes[] __attribute__((unused)) = {
#define X(name, value) {value, #name},
    SG_OUTCOMES(X)
#undef X
};
static const struct sg_name_row sg_substages[] __attribute__((unused)) = {
#define X(name, value) {value, #name},
    SG_SUBSTAGES(X)
#undef X
};
#define SG_OUTCOME_ROWS (sizeof sg_outcomes / sizeof sg_outcomes[0])
static inline const char *sg_outcome_name(uint32_t value)
{
    uint32_t i;
    for (i = 0; i < SG_OUTCOME_ROWS; i++)
        if (sg_outcomes[i].value == value) return sg_outcomes[i].name;
    return "UNKNOWN";
}
static inline int sg_outcome_known(uint32_t value) { return value <= SG_OUT_LAST && sg_outcome_name(value)[0] != 'U'; }
static inline const char *sg_substage_name(uint32_t value) { return value <= SG_SUB_LAST ? sg_substages[value].name : "UNKNOWN"; }
static inline unsigned sg_timing(int64_t lat_ticks, uint64_t slack_ticks)
{
    return lat_ticks < 0 ? SG_TIMING_EARLY : (uint64_t)lat_ticks <= slack_ticks ? SG_TIMING_ON_TIME : SG_TIMING_LATE;
}
static inline const char *sg_timing_name(unsigned timing)
{
    return timing == SG_TIMING_EARLY ? "EARLY" : timing == SG_TIMING_ON_TIME ? "ON_TIME" : "LATE";
}

/* The record as the reporter reads it: 0, or one SG_E_* code. Numbers that a line prints in decimal are bounded here, so that a damaged
 * record cannot make a line longer than the panel holds: the latency in ticks by the clamp the stage itself applies (2^40 ticks is 16
 * hours at the unit's 19.2 MHz), in microseconds by the stage's own display clamp (100 s), the time box, the slack and the cycle by what
 * the build allows (5 s at 1 GHz is 5e9). The counter frequency is not bounded: it is the longest field of its line and the line fits.
 * tools/test-sp11-stick-gic-record.py proves that no line is longer than 100 characters for any record that passes this check. */
#define SG_E_MAGIC 1
#define SG_E_VERSION 2
#define SG_E_SIZE 3
#define SG_E_RESERVED 4
#define SG_E_VALUE 5
#define SG_LATENCY_CLAMP (INT64_C(1) << 40)
#define SG_LAT_US_CLAMP INT64_C(99999999)
#define SG_TICKS_LIMIT UINT64_C(1000000000000)
#define SG_INTID_LIMIT 0xFFFFFFu
static inline int sg_irq_check(const uint8_t *b, unsigned i)
{
    int64_t lat = (int64_t)SG_IRQ_U64(b, i, lat_ticks), us = (int64_t)SG_IRQ_U64(b, i, lat_us);
    return (SG_IRQ_U32(b, i, flags) & ~SG_IRQF_ALL) || SG_IRQ_U32(b, i, hppir_vec) > SG_INTID_LIMIT || SG_IRQ_U32(b, i, hppir_post) > SG_INTID_LIMIT ||
           lat < -SG_LATENCY_CLAMP || lat > SG_LATENCY_CLAMP || us < -SG_LAT_US_CLAMP || us > SG_LAT_US_CLAMP;
}
/* The survey of version 3: a word whose flag is clear is zero (so that an unfilled word is never printed as an observation), the counts are what
 * the registers can hold, and the two INTIDs agree with the count of INTIDs that are enabled and pending. The PPI's own bit is never in the
 * quarantine. */
static inline int sg_survey_check(const uint8_t *b)
{
    uint32_t flags = SG_SV_U32(b, flags), lines = SG_SV_U32(b, spi_lines), en = SG_SV_U32(b, spi_enabled), pend = SG_SV_U32(b, spi_pending);
    uint32_t both = SG_SV_U32(b, spi_both), first = SG_SV_U32(b, spi_first), second = SG_SV_U32(b, spi_second);
    uint32_t most = 32u * SG_SPI_LINES_MAX;
    if (flags & ~SG_SV_ALL) return 1;
    if (!(flags & SG_SV_TIMERS) && (SG_SV_U64(b, cntv_ctl) | SG_SV_U64(b, cntv_cval) | SG_SV_U64(b, cntp_ctl) | SG_SV_U64(b, cntp_cval))) return 1;
    if (!(flags & SG_SV_SPI) && (lines | en | pend | both | first | second)) return 1;
    if (!(flags & SG_SV_PPI) && SG_SV_U32(b, ppi_prio)) return 1;
    if (!(flags & SG_SV_QUAR) && (SG_SV_U32(b, quarantined) | SG_SV_U32(b, quar_rb))) return 1;
    if (!(flags & SG_SV_HPPIR) && SG_SV_U32(b, hppir_pre)) return 1;
    if (lines > SG_SPI_LINES_MAX || en > most || pend > most || both > en || both > pend) return 1;
    if ((both == 0) != (first == 0) || (both < 2) != (second == 0) || (second && first >= second)) return 1;
    if ((first && (first < 32 || first > 1023)) || (second && second > 1023)) return 1;
    if (SG_SV_U32(b, hppir_pre) > SG_INTID_LIMIT || (SG_U32(b, intid) < 32 && (SG_SV_U32(b, quarantined) >> SG_U32(b, intid) & 1))) return 1;
    return 0;
}
static inline int sg_record_check(const uint8_t *b, uint64_t len)
{
    unsigned i;
    if (!b || len < SG_REC_BYTES || !sp11sb_magic(b, SG_REC_MAGIC)) return SG_E_MAGIC;
    if (SG_U32(b, version) != SG_REC_VERSION) return SG_E_VERSION;
    if (SG_U32(b, size) != SG_REC_BYTES) return SG_E_SIZE;
    if (SG_U32(b, reserved0) || SG_U32(b, gicr_index) > SP11SB_GICR_MAX || SG_WIN_U32(b, SG_WIN_EL2, reserved) || SG_WIN_U32(b, SG_WIN_EL1, reserved) ||
        SG_SV_U32(b, reserved))
        return SG_E_RESERVED;
    if (!sg_outcome_known(SG_U32(b, outcome)) || !sg_outcome_known(SG_U32(b, first_outcome)) || SG_U32(b, substage) > SG_SUB_LAST ||
        (SG_U32(b, planned) & ~SG_CLASS_ALL) || (SG_U32(b, begun) & ~SG_CLASS_ALL) || (SG_U32(b, wrote) & ~SG_R_MASK) ||
        (SG_U32(b, restore_failed) & ~SG_R_MASK) || (SG_U32(b, restore_retried) & ~SG_R_MASK) || SG_U32(b, done) > 1 || SG_U32(b, cycle) > SG_CYCLES + 1 ||
        SG_U32(b, intid) < 16 || SG_U32(b, intid) > 31 || SG_U64(b, timebox_ticks) > SG_TICKS_LIMIT || SG_U64(b, slack_ticks) > SG_TICKS_LIMIT ||
        SG_U64(b, cycle_ticks) > SG_TICKS_LIMIT || sg_survey_check(b))
        return SG_E_VALUE;
    for (i = 0; i < SG_IRQ_N; i++)
        if (sg_irq_check(b, i)) return SG_E_VALUE;
    for (i = 0; i < SG_WIN_N; i++)
        if (SG_WIN_U32(b, i, irq_entries) > 0xFFFF || SG_WIN_U32(b, i, other_entries) > 0xFFFF || SG_WIN_U32(b, i, hvc_end) > 1) return SG_E_VALUE;
    return 0;
}

/* ---- The page lines. One function renders every line of both pages from the record's bytes: the caller has begun the line with its own
 * prefix (SNAPMON or SNAPREP) and emits it if the function says it applies. Values narrower than 64 bits are masked to their width, so
 * that no line depends on a damaged upper half; the widths are also chosen so that no line of a record that passes sg_record_check is longer
 * than 100 characters with its prefix (byte registers print as bytes, IGRPEN1 as its one bit, DAIF as its four bits). A key's text carries its
 * own leading space and equals sign, as sc_key_hex does. */
#define SG_DAIF_SHOWN 0x3C0u            /* D, A, I and F: the only bits of DAIF a line shows */
enum sg_line_id {
    SG_L_GICD, SG_L_GICR, SG_L_CPU,                                 /* the reads: the wrapper's three lines before the first store, the reporter repeats them */
    SG_L_IRQ, SG_L_DIAG, SG_L_EL1, SG_L_DIAG1, SG_L_RESTORE, SG_L_VERDICT,  /* what the windows showed and what was put back: the wrapper's last lines, repeated */
    SG_L_FRAMES, SG_L_OLD, SG_L_NEW, SG_L_TIMER,                    /* the reporter's own */
    SG_L_CYC0, SG_L_CYC1, SG_L_CYC2, SG_L_CYC3, SG_L_CYC4,          /* one per interrupt of the windows: the row of SG_IRQ_N */
    SG_L_EXC, SG_L_EXC1, SG_L_HVC, SG_L_HCR, SG_L_VBAR1, SG_L_SCTLR1, SG_L_FINAL,
    SG_L_TIMERS, SG_L_SPI, SG_L_PPIP, SG_L_QUAR,                    /* version 3: the survey of the reads (the reporter's), and the quarantine and the check before the window (also the wrapper's on a refusal) */
    SG_L_COUNT
};
/* The reporter's order for the dry run's page: the head of the stage first, then what was read, what was surveyed, what was stored, and what the windows
 * showed. The monitor entry's page prints its own shorter list. At most 27 lines when every one applies, of the reporter's 40. */
#define SG_REPORTER_ORDER {SG_L_VERDICT, SG_L_FRAMES, SG_L_GICD, SG_L_GICR, SG_L_CPU, SG_L_OLD, SG_L_SPI, SG_L_PPIP, SG_L_TIMERS, SG_L_QUAR, SG_L_NEW, \
                           SG_L_TIMER, SG_L_IRQ, SG_L_CYC0, SG_L_CYC1, SG_L_CYC2, SG_L_CYC3, SG_L_DIAG, SG_L_EXC, SG_L_EL1, SG_L_CYC4, SG_L_DIAG1, \
                           SG_L_HVC, SG_L_EXC1, SG_L_HCR, SG_L_VBAR1, SG_L_SCTLR1, SG_L_FINAL, SG_L_RESTORE}

SC_INLINE void sg_dec_signed(struct pl_line *l, int64_t value)
{
    if (value < 0) {
        pl_text(l, "-");
        sc_dec(l, (uint64_t)0 - (uint64_t)value);
    } else {
        sc_dec(l, (uint64_t)value);
    }
}
SC_INLINE void sg_key_bit(struct pl_line *l, const char *key, uint64_t value, unsigned bit)
{
    pl_text(l, key);
    pl_text(l, (value >> bit & 1) ? "1" : "0");
}
SC_INLINE void sg_key_h8(const struct q1n1_payload_args *a, struct pl_line *l, const char *key, uint64_t value)
{
    sc_key_hex(a, l, key, value & 0xFF);
}
SC_INLINE void sg_key_h32(const struct q1n1_payload_args *a, struct pl_line *l, const char *key, uint64_t value)
{
    sc_key_hex(a, l, key, value & 0xFFFFFFFFu);
}
SC_INLINE void sg_classes(struct pl_line *l, const char *key, uint32_t mask)
{
    pl_text(l, key);
    pl_text(l, (mask & SG_CLASS_G) ? "G" : "");
    pl_text(l, (mask & SG_CLASS_R) ? "R" : "");
    pl_text(l, (mask & SG_CLASS_C) ? "C" : "");
    pl_text(l, (mask & SG_CLASS_T) ? "T" : "");
    pl_text(l, (mask & SG_CLASS_V) ? "V" : "");
    pl_text(l, (mask & SG_CLASS_E) ? "E" : "");
    if (!mask) pl_text(l, "NONE");
}
SC_INLINE int sg_irq_entered(const uint8_t *b, unsigned i) { return (SG_IRQ_U32(b, i, flags) & SG_IRQF_ENTERED) != 0; }
SC_INLINE int sg_irq_clean(const uint8_t *b, unsigned i)
{
    return SG_IRQ_U32(b, i, hppir_post) == SG_INTID_NONE && !(SG_IRQ_U32(b, i, flags) & (SG_IRQF_PEND | SG_IRQF_ACT));
}
/* "a/b/c/d" for the four interrupts of the EL2 window: the latency in microseconds, or a dash for one that did not come. */
SC_INLINE void sg_lat_list(struct pl_line *l, const uint8_t *b)
{
    unsigned i;
    for (i = 0; i <= SG_IRQ_FINAL; i++) {
        if (i) pl_text(l, "/");
        if (sg_irq_entered(b, i)) sg_dec_signed(l, (int64_t)SG_IRQ_U64(b, i, lat_us));
        else pl_text(l, "-");
    }
}
SC_INLINE void sg_ok_list(struct pl_line *l, const uint8_t *b)
{
    unsigned i;
    for (i = 0; i <= SG_IRQ_FINAL; i++) pl_text(l, !sg_irq_entered(b, i) ? "-" : (SG_IRQ_U32(b, i, flags) & SG_IRQF_OK) ? "1" : "0");
}
SC_INLINE void sg_diag(const struct q1n1_payload_args *a, struct pl_line *l, const uint8_t *b, unsigned w, unsigned intid)
{
    sg_key_h8(a, l, " CTL=", SG_WIN_U64(b, w, dg_ctl));
    sc_key_hex(a, l, " HPPIR=", SG_WIN_U64(b, w, dg_hppir1) & 0xFFFFFF);
    sg_key_bit(l, " PEND=", SG_WIN_U32(b, w, dg_ispendr0), intid);
    sg_key_h32(a, l, " ISEN=", SG_WIN_U32(b, w, dg_isenabler0));
    sg_key_h32(a, l, " GICD=", SG_WIN_U32(b, w, dg_gicd_ctlr));
    sg_key_h8(a, l, " RPR=", SG_WIN_U64(b, w, dg_rpr));
}
SC_INLINE int sg_regs_restored(const uint8_t *b, unsigned first, unsigned last)
{
    unsigned id;
    for (id = first; id <= last; id++)
        if (SG_REG_OLD(b, id) != SG_REG_FIN(b, id)) return 0;
    return 1;
}
SC_INLINE int sg_line(const struct q1n1_payload_args *a, struct pl_line *l, unsigned id, const uint8_t *b)
{
    uint32_t intid = SG_U32(b, intid), outcome = SG_U32(b, outcome), begun = SG_U32(b, begun);
    uint64_t ctlr = SG_REG_OLD(b, SG_R_GICD_CTLR), cfg = SG_REG_OLD(b, SG_R_ICFGR1) >> (2 * (intid - 16 & 15)) & 3;
    unsigned k;
    switch (id) {
    case SG_L_GICD:
        pl_text(l, "GIC GICD");
        sg_key_h32(a, l, " CTLR=", ctlr);
        sg_key_bit(l, " ARE=", ctlr, 4);
        sg_key_bit(l, " DS=", ctlr, 6);
        sg_key_bit(l, " G1A=", ctlr, 1);
        pl_text(l, (SG_U32(b, planned) & SG_CLASS_G) ? " NEED_G=1" : " NEED_G=0");
        sg_key_h32(a, l, " TYPER=", SG_U32(b, gicd_typer));
        sg_key_h32(a, l, " IIDR=", SG_U32(b, gicd_iidr));
        return 1;
    case SG_L_GICR:
        if (outcome == SG_OUT_GICR_NOT_FOUND) {         /* nothing matched: only the first frame's GICR_TYPER was kept */
            pl_text(l, "GIC GICR NOT_FOUND");
            sc_key_dec(l, " FRAMES=", SG_U32(b, gicr_index));
            sc_key_hex(a, l, " FIRST=", SG_U64(b, gicr_base));
            sc_key_hex(a, l, " TYPER0=", SG_U64(b, gicr_typer));
            return 1;
        }
        pl_text(l, "GIC GICR");
        sc_key_dec(l, " IDX=", SG_U32(b, gicr_index));
        sc_key_hex(a, l, " TYPER=", SG_U64(b, gicr_typer));
        sg_key_h32(a, l, " WAKER=", SG_U32(b, gicr_waker));
        sg_key_h32(a, l, " ISEN=", SG_REG_OLD(b, SG_R_ISENABLER0));
        sg_key_bit(l, " PEND=", SG_REG_OLD(b, SG_R_ISPENDR0), intid);
        sg_key_bit(l, " ACT=", SG_REG_OLD(b, SG_R_ISACTIVER0), intid);
        return 1;
    case SG_L_CPU:
        pl_text(l, "GIC CPU");
        sg_key_h8(a, l, " SRE=", SG_U64(b, icc_sre_el2));
        sg_key_bit(l, " EOIMODE=", SG_U64(b, icc_ctlr_el1), 1);
        sg_key_h8(a, l, " PMR=", SG_REG_OLD(b, SG_R_ICC_PMR));
        sg_key_h8(a, l, " BPR1=", SG_REG_OLD(b, SG_R_ICC_BPR1));
        sg_key_bit(l, " IGRPEN1=", SG_REG_OLD(b, SG_R_ICC_IGRPEN1), 0);
        sg_key_h8(a, l, " PRIO=", SG_REG_OLD(b, SG_R_IPRIORITYR));
        sg_key_h8(a, l, " CFG=", cfg);
        sg_key_bit(l, " GRP=", SG_REG_OLD(b, SG_R_IGROUPR0), intid);
        sg_key_h8(a, l, " CTL=", SG_REG_OLD(b, SG_R_CNTHP_CTL));
        return 1;
    case SG_L_IRQ:
        if (!SG_WIN_U32(b, SG_WIN_EL2, irq_entries)) return 0;
        pl_text(l, "GIC IRQ");
        sc_key_dec(l, " INTID=", SG_IRQ_U64(b, 0, iar) & 0xFFFFFF);
        pl_text(l, " LAT_US=");
        sg_lat_list(l, b);
        pl_text(l, " OK=");
        sg_ok_list(l, b);
        pl_text(l, !sg_irq_entered(b, SG_IRQ_FINAL) ? " FINAL=NONE" : sg_irq_clean(b, SG_IRQ_FINAL) ? " FINAL=CLEAR" : " FINAL=PEND");
        return 1;
    case SG_L_DIAG:
        if (SG_WIN_U32(b, SG_WIN_EL2, irq_entries) || !(begun & SG_CLASS_V)) return 0;      /* only a window that ran and saw nothing has a diagnosis */
        pl_text(l, "GIC DIAG");
        sg_diag(a, l, b, SG_WIN_EL2, intid);
        sg_key_h32(a, l, " DAIF=", SG_WIN_U32(b, SG_WIN_EL2, dg_daif) & SG_DAIF_SHOWN);
        return 1;
    case SG_L_EL1:
        if (!SG_WIN_U32(b, SG_WIN_EL1, irq_entries)) return 0;
        pl_text(l, "GIC EL1");
        sc_key_dec(l, " INTID=", SG_IRQ_U64(b, SG_IRQ_EL1, iar) & 0xFFFFFF);
        pl_text(l, " LAT_US=");
        sg_dec_signed(l, (int64_t)SG_IRQ_U64(b, SG_IRQ_EL1, lat_us));
        pl_text(l, " ");
        pl_text(l, sg_timing_name(sg_timing((int64_t)SG_IRQ_U64(b, SG_IRQ_EL1, lat_ticks), SG_U64(b, slack_ticks))));
        sc_key_hex(a, l, " ELR=", SG_IRQ_U64(b, SG_IRQ_EL1, elr));
        sg_key_h32(a, l, " SPSR=", SG_IRQ_U64(b, SG_IRQ_EL1, spsr));
        pl_text(l, (SG_IRQ_U32(b, SG_IRQ_EL1, flags) & SG_IRQF_OK) ? " OK=1" : " OK=0");
        return 1;
    case SG_L_DIAG1:
        if (!SG_WIN_U32(b, SG_WIN_EL1, hvc_end)) return 0;      /* the stub's counter bound ended with no interrupt: the registers that say why */
        pl_text(l, "GIC DIAG1");
        sg_diag(a, l, b, SG_WIN_EL1, intid);
        sg_key_h8(a, l, " PMR=", SG_WIN_U64(b, SG_WIN_EL1, dg_pmr));
        return 1;
    case SG_L_RESTORE:
        pl_text(l, "GIC RESTORE");
        pl_text(l, SG_U32(b, restore_failed) ? " CLEAN=NO" : " CLEAN=YES");
        sg_key_h32(a, l, " FAILED=", SG_U32(b, restore_failed));
        sg_key_h32(a, l, " RETRIED=", SG_U32(b, restore_retried));
        sg_classes(l, " BEGUN=", begun);
        pl_text(l, !(begun & SG_CLASS_V) ? " VBAR=NONE" :      /* nothing to put back before the window's class began */
                   SG_REG_OLD(b, SG_R_VBAR_EL2) == SG_REG_FIN(b, SG_R_VBAR_EL2) ? " VBAR=OK" : " VBAR=BAD");
        pl_text(l, !(begun & SG_CLASS_E) ? " EL1=NONE" : sg_regs_restored(b, SG_R_HCR_EL2, SG_R_SCTLR_EL1) ? " EL1=OK" : " EL1=BAD");
        return 1;
    case SG_L_VERDICT:
        pl_text(l, "GIC VERDICT=");
        pl_text(l, sg_outcome_name(outcome));
        if (SG_U32(b, first_outcome) != outcome) {      /* a put-back that failed hides what the window had shown: both are named */
            pl_text(l, " FIRST=");
            pl_text(l, sg_outcome_name(SG_U32(b, first_outcome)));
        }
        sc_key_hex(a, l, " OUTCOME=", outcome);
        pl_text(l, " SUB=");
        pl_text(l, sg_substage_name(SG_U32(b, substage)));
        return 1;
    case SG_L_FRAMES:
        pl_text(l, "GIC FRAMES");
        sc_key_hex(a, l, " GICD=", SG_U64(b, gicd_base));
        sc_key_hex(a, l, " GICR=", SG_U64(b, gicr_base));
        sc_key_dec(l, " INTID=", intid);
        sc_key_dec(l, " HZ=", SG_U64(b, counter_hz));
        return 1;
    case SG_L_OLD:      /* the whole of ISPENDR0 and ISACTIVER0, so that another interrupt in flight is visible before the first store */
        pl_text(l, "GIC OLD");
        sg_key_h8(a, l, " PRIO=", SG_REG_OLD(b, SG_R_IPRIORITYR));
        sg_key_h32(a, l, " ICFGR1=", SG_REG_OLD(b, SG_R_ICFGR1));
        sg_key_h32(a, l, " IGROUPR0=", SG_REG_OLD(b, SG_R_IGROUPR0));
        sg_key_h32(a, l, " ISPEND=", SG_REG_OLD(b, SG_R_ISPENDR0));
        sg_key_h32(a, l, " ISACT=", SG_REG_OLD(b, SG_R_ISACTIVER0));
        return 1;
    case SG_L_NEW:
        pl_text(l, "GIC NEW");
        sg_key_h32(a, l, " GICD=", SG_REG_NEW(b, SG_R_GICD_CTLR));
        sg_key_h32(a, l, " RB=", SG_REG_RB(b, SG_R_GICD_CTLR));
        sg_key_h32(a, l, " ISEN=", SG_REG_RB(b, SG_R_ISENABLER0));
        sg_key_h8(a, l, " PRIO=", SG_REG_RB(b, SG_R_IPRIORITYR));
        sg_key_h8(a, l, " PMR=", SG_REG_RB(b, SG_R_ICC_PMR));
        sg_key_bit(l, " IGRPEN1=", SG_REG_RB(b, SG_R_ICC_IGRPEN1), 0);
        return 1;
    case SG_L_TIMER:
        pl_text(l, "GIC TIMER");
        sc_key_dec(l, " BOX_TICKS=", SG_U64(b, timebox_ticks));
        sc_key_dec(l, " CYCLE_TICKS=", SG_U64(b, cycle_ticks));
        sc_key_dec(l, " SLACK=", SG_U64(b, slack_ticks));
        return 1;
    case SG_L_CYC0: case SG_L_CYC1: case SG_L_CYC2: case SG_L_CYC3: case SG_L_CYC4:
        k = id - SG_L_CYC0;
        if (!sg_irq_entered(b, k)) return 0;
        pl_text(l, "GIC CYC");
        sc_dec(l, k);
        sc_key_dec(l, " IAR=", SG_IRQ_U64(b, k, iar) & 0xFFFFFF);
        sc_key_dec(l, " HPPIR=", SG_IRQ_U32(b, k, hppir_vec));
        sc_key_dec(l, " POST=", SG_IRQ_U32(b, k, hppir_post));
        pl_text(l, " F=");
        pl_text(l, (SG_IRQ_U32(b, k, ctl) & SG_CNTHP_ISTATUS) ? "1" : "0");
        pl_text(l, (SG_IRQ_U32(b, k, flags) & SG_IRQF_PEND) ? "1" : "0");
        pl_text(l, (SG_IRQ_U32(b, k, flags) & SG_IRQF_ACT) ? "1" : "0");
        pl_text(l, " TICKS=");
        sg_dec_signed(l, (int64_t)SG_IRQ_U64(b, k, lat_ticks));
        pl_text(l, " US=");
        sg_dec_signed(l, (int64_t)SG_IRQ_U64(b, k, lat_us));
        return 1;
    case SG_L_EXC:
        if (!SG_WIN_U32(b, SG_WIN_EL2, other_entries)) return 0;
        pl_text(l, "GIC EXC");
        sc_key_dec(l, " ENTRIES=", SG_WIN_U32(b, SG_WIN_EL2, other_entries));
        sc_key_dec(l, " VECTOR=", SG_WIN_U64(b, SG_WIN_EL2, exc_vector) & 0xFFFF);
        sg_key_h32(a, l, " ESR=", SG_WIN_U64(b, SG_WIN_EL2, exc_esr));
        sc_key_hex(a, l, " ELR=", SG_WIN_U64(b, SG_WIN_EL2, exc_elr));
        return 1;
    case SG_L_EXC1:
        if (!SG_WIN_U32(b, SG_WIN_EL1, other_entries)) return 0;
        pl_text(l, "GIC EXC1");
        sc_key_dec(l, " ENTRIES=", SG_WIN_U32(b, SG_WIN_EL1, other_entries));
        sc_key_hex(a, l, " VECTOR=", SG_WIN_U64(b, SG_WIN_EL1, exc_vector) & 0xFFFF);
        sg_key_h32(a, l, " ESR=", SG_WIN_U64(b, SG_WIN_EL1, exc_esr));
        sc_key_hex(a, l, " ELR=", SG_WIN_U64(b, SG_WIN_EL1, exc_elr));
        sg_key_h32(a, l, " SPSR=", SG_WIN_U64(b, SG_WIN_EL1, exc_spsr));
        return 1;
    case SG_L_HVC:
        if (!SG_WIN_U32(b, SG_WIN_EL1, hvc_end)) return 0;      /* the stub's HVC #0: the HCR_EL2 and the SPSR_EL2 it was taken with */
        pl_text(l, "GIC HVC");
        sg_key_h32(a, l, " HCR=", SG_WIN_U64(b, SG_WIN_EL1, dg_hcr));
        sg_key_h32(a, l, " SPSR=", SG_WIN_U64(b, SG_WIN_EL1, dg_spsr));
        return 1;
    case SG_L_HCR: case SG_L_VBAR1: case SG_L_SCTLR1:
        if (!(begun & SG_CLASS_E)) return 0;
        k = id == SG_L_HCR ? SG_R_HCR_EL2 : id == SG_L_VBAR1 ? SG_R_VBAR_EL1 : SG_R_SCTLR_EL1;
        pl_text(l, id == SG_L_HCR ? "GIC HCR" : id == SG_L_VBAR1 ? "GIC VBAR1" : "GIC SCTLR1");
        sc_key_hex(a, l, " OLD=", SG_REG_OLD(b, k));
        sc_key_hex(a, l, " RB=", SG_REG_RB(b, k));
        sc_key_hex(a, l, " FIN=", SG_REG_FIN(b, k));
        return 1;
    case SG_L_FINAL:
        pl_text(l, "GIC FINAL");
        sg_key_h32(a, l, " GICD=", SG_REG_FIN(b, SG_R_GICD_CTLR));
        sg_key_h32(a, l, " ISEN=", SG_REG_FIN(b, SG_R_ISENABLER0));
        sg_key_h8(a, l, " PRIO=", SG_REG_FIN(b, SG_R_IPRIORITYR));
        sg_key_h8(a, l, " PMR=", SG_REG_FIN(b, SG_R_ICC_PMR));
        sg_key_bit(l, " IGRPEN1=", SG_REG_FIN(b, SG_R_ICC_IGRPEN1), 0);
        sg_key_h8(a, l, " RPR=", SG_REG_FIN(b, SG_R_ICC_RPR));
        sg_key_h32(a, l, " DAIF=", SG_REG_FIN(b, SG_R_DAIF) & SG_DAIF_SHOWN);
        return 1;
    case SG_L_TIMERS:       /* the EL1 timers as read at EL2: CTL bits 2:0 are ENABLE, IMASK and ISTATUS; V is virtual, P physical */
        if (!(SG_SV_U32(b, flags) & SG_SV_TIMERS)) return 0;
        pl_text(l, "GIC TIMERS");
        sg_key_h8(a, l, " VCTL=", SG_SV_U64(b, cntv_ctl) & 7);
        sc_key_hex(a, l, " VCVAL=", SG_SV_U64(b, cntv_cval));
        sg_key_h8(a, l, " PCTL=", SG_SV_U64(b, cntp_ctl) & 7);
        sc_key_hex(a, l, " PCVAL=", SG_SV_U64(b, cntp_cval));
        return 1;
    case SG_L_SPI:          /* counts of INTIDs; FIRST and SECOND are the first two that are enabled and pending, 0 for none */
        if (!(SG_SV_U32(b, flags) & SG_SV_SPI)) return 0;
        pl_text(l, "GIC SPI");
        sc_key_dec(l, " LINES=", SG_SV_U32(b, spi_lines));
        sc_key_dec(l, " EN=", SG_SV_U32(b, spi_enabled));
        sc_key_dec(l, " PEND=", SG_SV_U32(b, spi_pending));
        sc_key_dec(l, " BOTH=", SG_SV_U32(b, spi_both));
        sc_key_dec(l, " FIRST=", SG_SV_U32(b, spi_first));
        sc_key_dec(l, " SECOND=", SG_SV_U32(b, spi_second));
        return 1;
    case SG_L_PPIP:         /* the priorities of SG_SURVEY_PPI0 to SG_SURVEY_PPI3, in that order */
        if (!(SG_SV_U32(b, flags) & SG_SV_PPI)) return 0;
        pl_text(l, "GIC PPIP");
        sg_key_h8(a, l, " P23=", SG_SV_U32(b, ppi_prio));
        sg_key_h8(a, l, " P25=", SG_SV_U32(b, ppi_prio) >> 8);
        sg_key_h8(a, l, " P27=", SG_SV_U32(b, ppi_prio) >> 16);
        sg_key_h8(a, l, " P30=", SG_SV_U32(b, ppi_prio) >> 24);
        return 1;
    case SG_L_QUAR:         /* the first store and the last check of the arm: what was disabled and read back, and what the CPU interface would hand out next */
        if (!(SG_SV_U32(b, flags) & (SG_SV_QUAR | SG_SV_HPPIR))) return 0;
        pl_text(l, "GIC QUAR");
        if (SG_SV_U32(b, flags) & SG_SV_QUAR) {
            sg_key_h32(a, l, " MASK=", SG_SV_U32(b, quarantined));
            sg_key_h32(a, l, " DONE=", SG_SV_U32(b, quar_rb));
        } else {
            pl_text(l, " MASK=NONE");
        }
        if (SG_SV_U32(b, flags) & SG_SV_HPPIR) sc_key_dec(l, " HPPIR=", SG_SV_U32(b, hppir_pre));
        else pl_text(l, " HPPIR=-");
        return 1;
    default:
        return 0;
    }
}
#endif /* SNAP_SP11_STICK_GIC */
#endif /* SNAP_PROBES_SP11_STICK_GIC_RECORD_H */
