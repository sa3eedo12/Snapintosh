/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_MONITOR_RECORD_H
#define SNAP_PROBES_SP11_STICK_MONITOR_RECORD_H
/* The monitor entry's record (docs/SP11-STICK-MONITOR.md, section 5): what the wrapper kept around the monitor call, in REPORT.DAT's second
 * extension slot, and the page lines made from it. The wrapper stores it before the call (so that a hang leaves its sub-stage) and again after,
 * and prints its lines; SNAPREP-M1.BIN prints the same lines from the same bytes with the same function. Compiled only with
 * -DSNAP_SP11_STICK_MONITOR=1. Evidence: EXPERIMENTAL; hardware NOT RUN.
 *
 * Where it is. REPORT.DAT is the head page (frozen), the monitor's window of 64 KiB (the monitor stores nothing at or above out + 0x10000:
 * probes/xnuboot/report_layout.c static assertion), then the wrapper's extension slots of one page each: the GIC record at +0x11000
 * (sp11-stick-gic-record.h) and this one at +0x12000. The frozen head's own tail stays zero; the bytes after this record, up to the stack
 * area at +0x41000 (sp11-stick-monitor.h), stay zero.
 *
 * Evidence classes of the fields. Counters, ticks, the call arguments and the register rows are the machine's own words (OBSERVED when read
 * on the unit, nothing until then). The decisions in `outcome` and `flags` are the wrapper's. A register is `changed` when its masked value
 * after the call is not the one before it: the monitor puts these registers back at its exit (probes/xnuboot/report.c), so a difference is a
 * finding about the monitor, never a refusal of the wrapper (a refusal after MONITOR_ENTERED is not allowed: sp11sb_report_check). */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK_MONITOR
#include "sp11-stick-gic-record.h"

#define SM_REC_AT (SG_REC_AT + SG_REC_SLOT)     /* REPORT.DAT + 0x12000 */
#define SM_REC_BYTES 512u
#define SM_REC_VERSION 1u
#define SM_REC_MAGIC "SNAPMNT1"
#define SM_ROWS 13u
#define SM_IDS 5u
#define SM_QUARANTINE_OWNERSHIP UINT64_C(0x51554E01)    /* probes/xnuboot-terminal.h: the monitor returns these when it could not leave RAM safe to reuse */
#define SM_QUARANTINE_GPU UINT64_C(0x51554E02)

/* What the stage decided, in two ranges: a refusal (raised before MONITOR_ENTERED as GIC_NOT_READY with this value as the detail; the GIC arm's
 * own refusals keep their sg_outcome values, 0 to 48) and what the call came to (never a refusal). */
#define SM_OUTCOMES(X) \
    X(NONE, 0) X(PARAMS_PA, 64) X(PARAMS_SIZE, 65) X(PARAMS_EARLY_STOP, 66) X(PARAMS_GPU, 67) X(PARAMS_BOX, 68) X(IMAGE_SIZE, 69) \
    X(IMAGE_UNREADABLE, 70) X(IMAGE_DIGEST, 71) X(REPORT_UNWRITABLE, 72) X(WINDOW_DIRTY, 73) X(GIC_NOT_READY, 74) \
    X(RETURNED, 96) X(QUARANTINE, 97) X(RET_OTHER, 98)
enum sm_outcome {
#define X(name, value) SM_OUT_##name = value,
    SM_OUTCOMES(X)
#undef X
};
#define SM_OUT_IS_REFUSAL(o) ((o) >= 64u && (o) < 96u)
#define SM_SUBSTAGES(X) X(NONE, 0) X(CHECKS, 1) X(SYNCED, 2) X(ARMING, 3) X(ARMED, 4) X(CALLING, 5) X(RETURNED, 6) X(DONE, 7)
enum sm_substage {
#define X(name, value) SM_SUB_##name = value,
    SM_SUBSTAGES(X)
#undef X
    SM_SUB_COUNT
};
enum sm_flag {
    SM_F_IC_SYNC = 1u << 0,             /* IC IALLUIS, DSB ISH and ISB ran after the copy */
    SM_F_CALLED = 1u << 1,              /* the stage MONITOR_ENTERED was stored and the call made */
    SM_F_RETURNED = 1u << 2,            /* the call came back */
    SM_F_STACK_S2 = 1u << 3,            /* the call ran on the stack in REPORT.DAT's tail (option S2), not on the runner's */
    SM_F_WINDOW_OVERRUN = 1u << 4,      /* a byte of the extension slots changed during the call: the monitor stored beyond its window */
    SM_F_REGS_CHANGED = 1u << 5,        /* `changed` is not zero */
    SM_F_STACK_FULL = 1u << 6,          /* the monitor wrote the lowest word of its stack: the depth is a lower bound and the stack may have overrun */
    SM_F_GIC_NOT_RESTORED = 1u << 7,    /* the GIC put-back reported a register that did not go back */
    SM_F_ALL = (1u << 8) - 1
};
enum sm_rec_error { SM_E_MAGIC = 1, SM_E_VERSION, SM_E_SIZE, SM_E_RESERVED, SM_E_VALUE };

struct sw_mon_row {
    uint64_t pre, post;                 /* the register as read before the call and after it (raw, not masked) */
};
struct sw_mon_rec {
    uint8_t magic[8];                   /* 0x000 SNAPMNT1 */
    uint32_t version, size;             /* 0x008 */
    uint32_t outcome, substage;         /* 0x010 SM_OUT_*, SM_SUB_*: the last sub-stage reached names where a hang happened */
    uint32_t flags, changed;            /* 0x018 SM_F_*; bit i set when row i differs after the call */
    uint64_t counter_hz;                /* 0x020 */
    uint64_t ticks_call, ticks_return;  /* 0x028 CNTPCT_EL0 just before the call and just after it */
    uint64_t monitor_pa;                /* 0x038 where the wrapper found the monitor image: the file's base plus the build's offset */
    uint64_t monitor_offset, monitor_bytes;     /* 0x040 as built */
    uint64_t monitor_sha[4];            /* 0x050 SHA-256 of the image as built: four little-endian words of the digest */
    uint64_t stack_top, stack_bytes;    /* 0x070 the call's stack (0, 0 when it stayed on the runner's) */
    uint64_t stack_used;                /* 0x080 bytes below the top that the monitor changed: a lower bound, exact to one word */
    uint64_t call_x[5];                 /* 0x088 x0 to x4 as passed */
    uint64_t ret, status, entered;      /* 0x0B0 x0 on return; words 0 and 1 of the monitor's window */
    uint64_t box_s, early_stop_flags, gpu_flags;        /* 0x0C8 PARAMS words the stage read: the time box, early_stop.flags, gpu.flags */
    uint64_t id[SM_IDS];                /* 0x0E0 ID_AA64MMFR2_EL1, ID_AA64MMFR4_EL1, REVIDR_EL1, ID_AA64ISAR1_EL1, ID_AA64ISAR2_EL1 */
    struct sw_mon_row row[SM_ROWS];     /* 0x108 */
    uint8_t reserved[40];               /* 0x1D8 zero */
};
_Static_assert(sizeof(struct sw_mon_rec) == SM_REC_BYTES && offsetof(struct sw_mon_rec, counter_hz) == 0x20 &&
               offsetof(struct sw_mon_rec, monitor_sha) == 0x50 && offsetof(struct sw_mon_rec, stack_used) == 0x80 &&
               offsetof(struct sw_mon_rec, ret) == 0xB0 && offsetof(struct sw_mon_rec, box_s) == 0xC8 &&
               offsetof(struct sw_mon_rec, id) == 0xE0 && offsetof(struct sw_mon_rec, row) == 0x108 &&
               offsetof(struct sw_mon_rec, reserved) == 0x1D8, "the record's layout is the document's table: group starts, no padding");
_Static_assert(SM_REC_BYTES <= SG_REC_SLOT && SM_REC_AT == 0x12000u, "the record is one slot after the GIC record's");

/* The rows, in the order of `row` and of the bits of `changed`, and which bits of each are compared. The masks take out what a read can show
 * and a put-back cannot restore: CNTHP_CTL_EL2.ISTATUS is read only. */
#define SM_ROW_NAMES(X) \
    X(DAIF, 0x3C0) X(SPSEL, 1) X(VBAR_EL2, ~UINT64_C(0)) X(HCR_EL2, ~UINT64_C(0)) X(TPIDR_EL2, ~UINT64_C(0)) X(SCTLR_EL2, ~UINT64_C(0)) \
    X(TCR_EL2, ~UINT64_C(0)) X(CNTHCTL_EL2, ~UINT64_C(0)) X(CNTHP_CTL_EL2, 3) X(MDCR_EL2, ~UINT64_C(0)) X(VTTBR_EL2, ~UINT64_C(0)) \
    X(VTCR_EL2, ~UINT64_C(0)) X(VMPIDR_EL2, ~UINT64_C(0))
enum sm_row {
#define X(name, mask) SM_ROW_##name,
    SM_ROW_NAMES(X)
#undef X
    SM_ROW_COUNT
};
_Static_assert(SM_ROW_COUNT == SM_ROWS, "one row per register");
static inline uint64_t sm_row_mask(unsigned row)
{
    static const uint64_t masks[SM_ROWS] = {
#define X(name, mask) (uint64_t)(mask),
        SM_ROW_NAMES(X)
#undef X
    };
    return row < SM_ROWS ? masks[row] : 0;
}
static inline const char *sm_row_name(unsigned row)
{
    switch (row) {         /* a switch, not a table of pointers: a payload has no relocation of an absolute address */
#define X(name, mask) case SM_ROW_##name: return #name;
    SM_ROW_NAMES(X)
#undef X
    default: return "?";
    }
}
static inline const char *sm_outcome_name(unsigned value)
{
    switch (value) {
#define X(name, v) case v: return #name;
    SM_OUTCOMES(X)
#undef X
    default: return "UNKNOWN";
    }
}
static inline int sm_outcome_known(unsigned value)
{
    switch (value) {
#define X(name, v) case v: return 1;
    SM_OUTCOMES(X)
#undef X
    default: return 0;
    }
}

#define SM_U32(b, f) sp11sb_rd32((b) + offsetof(struct sw_mon_rec, f))
#define SM_U64(b, f) sp11sb_rd64((b) + offsetof(struct sw_mon_rec, f))
#define SM_ID(b, i) sp11sb_rd64((b) + offsetof(struct sw_mon_rec, id) + 8 * (i))
#define SM_ROWV(b, i, which) sp11sb_rd64((b) + offsetof(struct sw_mon_rec, row) + 16 * (i) + 8 * (which))

/* What a reader makes of a slot: 0, or the first failing class (magic, version, size, reserved, values) in that order. The reporter prints a
 * record that passes and names one that does not. */
static inline int sm_record_check(const uint8_t *b, uint32_t size)
{
    uint32_t i, outcome;
    for (i = 0; i < 8; i++)
        if (b[i] != (uint8_t)SM_REC_MAGIC[i]) return SM_E_MAGIC;
    if (SM_U32(b, version) != SM_REC_VERSION) return SM_E_VERSION;
    if (size < SM_REC_BYTES || SM_U32(b, size) != SM_REC_BYTES) return SM_E_SIZE;
    if (!sp11sb_zero(b + offsetof(struct sw_mon_rec, reserved), sizeof(((struct sw_mon_rec *)0)->reserved))) return SM_E_RESERVED;
    outcome = SM_U32(b, outcome);
    if (!sm_outcome_known(outcome) || SM_U32(b, substage) >= SM_SUB_COUNT || (SM_U32(b, flags) & ~(uint32_t)SM_F_ALL) || SM_U32(b, changed) >> SM_ROWS ||
        SM_U64(b, stack_used) > SM_U64(b, stack_bytes) || SM_U64(b, stack_bytes) > UINT64_C(1) << 24)
        return SM_E_VALUE;
    return 0;
}

/* The page lines, by number. The wrapper puts its prefix in front and the reporter its own; a line that does not apply returns 0 and is not
 * printed. Called with a constant `id`, so that each call keeps only its own case (the reporter's budget is 64 KiB). The font has no comma,
 * semicolon, angle bracket or plus: nothing below uses one. */
enum sm_line_id { SM_L_IMAGE, SM_L_IDS0, SM_L_IDS1, SM_L_CALL, SM_L_RET, SM_L_REGS, SM_L_VERDICT, SM_L_QUARANTINE };
SC_INLINE int sm_line(const struct q1n1_payload_args *a, struct pl_line *l, unsigned id, const uint8_t *b)
{
    uint32_t flags = SM_U32(b, flags), outcome = SM_U32(b, outcome), changed = SM_U32(b, changed), i, shown = 0;
    switch (id) {
    case SM_L_IMAGE:        /* the image the call will run and the PARAMS words the stage read, on one line: the page has thirty */
        pl_text(l, "MON IMAGE");
        sc_key_hex(a, l, " OFF=", SM_U64(b, monitor_offset));
        sc_key_dec(l, " BYTES=", SM_U64(b, monitor_bytes));
        sc_key_hex(a, l, " SHA=", __builtin_bswap64(SM_U64(b, monitor_sha)));      /* the first eight bytes of the digest, in digest order */
        sc_key_dec(l, " BOX_S=", SM_U64(b, box_s));
        sc_key_hex(a, l, " ESF=", SM_U64(b, early_stop_flags));
        sc_key_hex(a, l, " GPUF=", SM_U64(b, gpu_flags));
        return 1;
    case SM_L_IDS0:
        pl_text(l, "MON IDS");
        sc_key_hex(a, l, " MMFR2=", SM_ID(b, 0));
        sc_key_hex(a, l, " MMFR4=", SM_ID(b, 1));
        sc_key_hex(a, l, " REVIDR=", SM_ID(b, 2));
        return 1;
    case SM_L_IDS1:
        pl_text(l, "MON IDS");
        sc_key_hex(a, l, " ISAR1=", SM_ID(b, 3));
        sc_key_hex(a, l, " ISAR2=", SM_ID(b, 4));
        return 1;
    case SM_L_CALL:
        pl_text(l, "MON CALL");
        sc_key_hex(a, l, " X0=", SM_U64(b, call_x));
        sc_key_hex(a, l, " X1=", sp11sb_rd64(b + offsetof(struct sw_mon_rec, call_x) + 8));
        sc_key_hex(a, l, " X2=", sp11sb_rd64(b + offsetof(struct sw_mon_rec, call_x) + 16));
        sc_key_hex(a, l, " X3=", sp11sb_rd64(b + offsetof(struct sw_mon_rec, call_x) + 24));
        sc_key_hex(a, l, " X4=", sp11sb_rd64(b + offsetof(struct sw_mon_rec, call_x) + 32));
        sc_key_hex(a, l, " SP=", SM_U64(b, stack_top));
        return 1;
    case SM_L_RET:
        if (!(flags & SM_F_RETURNED)) return 0;
        sc_key_hex(a, l, "MON RET=", SM_U64(b, ret));
        sc_key_hex(a, l, " STATUS=", SM_U64(b, status));
        sc_key_dec(l, " ENTERED=", SM_U64(b, entered));
        sc_key_dec(l, " TICKS=", SM_U64(b, ticks_return) - SM_U64(b, ticks_call));
        sc_key_dec(l, " STACK=", SM_U64(b, stack_used));
        sc_key_dec(l, "/", SM_U64(b, stack_bytes));
        return 1;
    case SM_L_REGS:
        if (!(flags & SM_F_RETURNED)) return 0;
        pl_text(l, changed ? "MON REGS RESTORED=NO" : "MON REGS RESTORED=YES");
        if (changed) {
            sc_key_hex(a, l, " CHANGED=", changed);
            for (i = 0; i < SM_ROWS && shown < 3; i++)
                if (changed & (1u << i)) {
                    pl_text(l, " ");
                    pl_text(l, sm_row_name(i));
                    shown++;
                }
        }
        if (flags & SM_F_WINDOW_OVERRUN) pl_text(l, " WINDOW OVERRUN");
        if (flags & SM_F_STACK_FULL) pl_text(l, " STACK FULL");
        return 1;
    case SM_L_VERDICT:
        pl_text(l, "MON VERDICT=");
        pl_text(l, sm_outcome_name(outcome));
        sc_key_hex(a, l, " FLAGS=", flags);
        pl_text(l, " SUB=");
        sc_dec(l, SM_U32(b, substage));
        return 1;
    case SM_L_QUARANTINE:
        if (outcome != SM_OUT_QUARANTINE) return 0;
        pl_text(l, "MON QUARANTINE: THE REPORT IS PARTIAL AND RAM MUST NOT BE REUSED. POWER CYCLE");
        return 1;
    default:
        return 0;
    }
}
#endif /* SNAP_SP11_STICK_MONITOR */
#endif /* SNAP_PROBES_SP11_STICK_MONITOR_RECORD_H */
