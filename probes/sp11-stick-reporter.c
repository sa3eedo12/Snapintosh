/* SPDX-License-Identifier: MIT */
/* SNAPREP.BIN of the bundle dry run (docs/SP11-STICK-WRAPPER.md; docs/SP11-MONITOR-PORT.md, work item 4a): the minimal reporter that
 * q1n1's stick runner calls second. It reads REPORT.DAT, which the wrapper wrote, and says on the panel what happened. READ ONLY:
 * it executes no instruction that writes memory but a store into its own stack, no system register access, no cache or translation
 * instruction and no barrier (tools/test-sp11-stick-wrapper-build.py disassembles the binary and proves each).
 *
 * It needs nothing from PIN.DAT, so a damaged PIN.DAT cannot hide the report: it finds REPORT.DAT through its own arguments' preload
 * table, like the wrapper, and checks the five slots first. What it prints, per docs/SP11-STICK-BUNDLE.md section 6 (at most 40 lines;
 * this build prints at most 9):
 *   arguments or slots refused      one line naming the refusal, nothing else
 *   the first word is zero          "report empty": the wrapper wrote nothing
 *   the head fails its check        the refusal (REPORT_*) and the first eight words in hex
 *   a refusal below COPYING         the refusal, stage, detail, raw reads, "RAM untouched beyond REPORT.DAT"
 *   a refusal at COPYING or after   the same, the copy counters and the placement, "placement written"
 *   the dry run completed           the head's summary: stage, policy, counters, ticks, placement, descriptor, GUEST.DAT's base
 * UNIMPLEMENTED: the decode of the monitor's window and the comparison of the raw reads with PIN.DAT's pinned values (work item 4b); the
 * wrapper of this build never enters a monitor, so the window is all zero and nothing is said about it. (Under the opt-in
 * SNAP_SP11_STICK_GIC=1, SNAPREP-GIC.BIN, the first bytes of that window are the GIC and stop stage's record, which this prints after
 * the head's summary: at most 23 more lines, from the same bytes and the same function as the wrapper's page. Read only, as before. Under the
 * third opt-in, SNAP_SP11_STICK_MONITOR=1, SNAPREP-M1.BIN, the monitor's record and, when the call returned zero, its window are decoded as well and
 * the GIC lines are the arm half's five: docs/SP11-STICK-MONITOR.md section 4. Still read only.)
 *
 * Returns 0 after a decoded report or an empty one, 0x1000 + code when it refused its own arguments, a slot or the head, and 2 when
 * the guarded read of REPORT.DAT failed. Evidence: EXPERIMENTAL; hardware NOT RUN. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK
#include "sp11-stick-common.h"
#if SNAP_SP11_STICK_GIC
#include "sp11-stick-gic-record.h"
#endif
#if SNAP_SP11_STICK_MONITOR
#include "sp11-stick-monitor-record.h"
#endif

#define RP_RETURN_UNREADABLE UINT64_C(2)

uint64_t payload_entry(const struct q1n1_payload_args *a);

#define R32(f) sp11sb_rd32(report + offsetof(struct sp11sb_report, f))
#define R64(f) sp11sb_rd64(report + offsetof(struct sp11sb_report, f))

/* A line out through the runner, counted against the 40 it keeps for this payload. */
SC_INLINE void rp_emit(const struct q1n1_payload_args *a, const struct pl_line *l, uint32_t *lines)
{
    if (*lines >= SP11SB_REPORTER_LINES) return;
    pl_emit(a, l);
    (*lines)++;
}

SC_INLINE void rp_begin(struct pl_line *l)
{
    pl_begin(l);
    pl_text(l, "SNAPREP ");
}

/* "SNAPREP REFUSED=NAME DETAIL=0X..": the one line of an argument or slot refusal. */
SC_INLINE uint64_t rp_refuse(const struct q1n1_payload_args *a, uint32_t code, uint64_t detail, uint32_t *lines)
{
    struct pl_line l;
    rp_begin(&l);
    pl_text(&l, "REFUSED=");
    pl_text(&l, sp11sb_error_name(code));
    sc_key_hex(a, &l, " DETAIL=", detail);
    rp_emit(a, &l, lines);
    return SP11SB_RET_REFUSED + code;
}

/* A head that failed its check: the refusal and the first eight words, four to a line, so that what is there can be read. */
SC_INLINE void rp_words(const struct q1n1_payload_args *a, const uint8_t *report, uint32_t *lines)
{
    struct pl_line l;
    uint32_t i, j;
    for (i = 0; i < 2; i++) {
        rp_begin(&l);
        pl_text(&l, "WORDS");
        for (j = 0; j < 4; j++) {
            pl_text(&l, " ");
            pl_hex(a, &l, sp11sb_rd64(report + 8 * (4 * i + j)));
        }
        rp_emit(a, &l, lines);
    }
}

/* Ticks from the entry of the wrapper, or 0 for a point it did not reach. */
SC_INLINE uint64_t rp_since_entry(uint64_t enter, uint64_t at)
{
    return at ? at - enter : 0;
}

SC_INLINE void rp_decode(const struct q1n1_payload_args *a, const uint8_t *report, uint32_t *lines)
{
    struct pl_line l;
    uint32_t stage = R32(stage), refusal = R32(refusal);
    uint64_t enter = R64(ticks_enter);
    rp_begin(&l);
    if (refusal) {
        pl_text(&l, "REFUSED=");
        pl_text(&l, sp11sb_error_name(refusal));
        pl_text(&l, " STAGE=");
        pl_text(&l, sp11sb_stage_name(stage));
        sc_key_hex(a, &l, " DETAIL=", R64(refusal_detail));
    } else if (stage == SP11SB_STAGE_DONE) {
#if SNAP_SP11_STICK_MONITOR
        pl_text(&l, "MONITOR RETURNED STAGE=DONE");
#else
        pl_text(&l, "DRY RUN COMPLETE STAGE=DONE: THE MONITOR WAS NOT CALLED");
#endif
    } else {
        pl_text(&l, "WRAPPER DID NOT FINISH STAGE=");
        pl_text(&l, sp11sb_stage_name(stage));
    }
    rp_emit(a, &l, lines);
    rp_begin(&l);
    pl_text(&l, "VERDICTS PIN=");
    pl_text(&l, sp11sb_error_name(R32(pin_code)));
    pl_text(&l, " GUEST=");
    pl_text(&l, sp11sb_error_name(R32(guest_code)));
    sc_key_hex(a, &l, " POLICY=", R32(hash_policy));
    sc_key_dec(&l, " LINES=", R32(lines_printed));
    rp_emit(a, &l, lines);
    if (R64(midr_el1)) {
        rp_begin(&l);
        pl_text(&l, "REGS");
        sc_key_hex(a, &l, " EL=", R64(current_el));
        sc_key_hex(a, &l, " MIDR=", R64(midr_el1));
        sc_key_hex(a, &l, " MPIDR=", R64(mpidr_el1));
        sc_key_dec(&l, " CNTFRQ=", R64(cntfrq_el0));
        rp_emit(a, &l, lines);
        rp_begin(&l);
        pl_text(&l, "REGS");
        sc_key_hex(a, &l, " HCR=", R64(hcr_el2));
        sc_key_hex(a, &l, " SCTLR=", R64(sctlr_el2));
        sc_key_hex(a, &l, " TCR=", R64(tcr_el2));
        rp_emit(a, &l, lines);
    }
    if (refusal && stage < SP11SB_STAGE_COPYING) {
        rp_begin(&l);
        pl_text(&l, "RAM UNTOUCHED BEYOND REPORT.DAT");
        rp_emit(a, &l, lines);
        return;
    }
    if (refusal) {
        rp_begin(&l);
        pl_text(&l, "PLACEMENT WRITTEN");
        rp_emit(a, &l, lines);
    }
    rp_begin(&l);
    pl_text(&l, "CHUNKS");
    sc_key_dec(&l, " TOTAL=", R32(chunks_total));
    sc_key_dec(&l, " COPIED=", R32(chunks_copied));
    sc_key_dec(&l, " HASHED=", R32(chunks_hashed));
    sc_key_dec(&l, " BYTES_COPIED=", R64(bytes_copied));
    sc_key_dec(&l, " BYTES_HASHED=", R64(bytes_hashed));
    rp_emit(a, &l, lines);
    rp_begin(&l);
    pl_text(&l, "TICKS");
    sc_key_dec(&l, " HZ=", R64(counter_hz));
    sc_key_dec(&l, " PLACED=", rp_since_entry(enter, R64(ticks_placed)));
    sc_key_dec(&l, " COPIED=", rp_since_entry(enter, R64(ticks_copied)));
    sc_key_dec(&l, " HASHED=", rp_since_entry(enter, R64(ticks_hashed)));
    rp_emit(a, &l, lines);
    rp_begin(&l);
    pl_text(&l, "PLACE");
    sc_key_hex(a, &l, " BASE=", R64(place_base));
    sc_key_hex(a, &l, " SIZE=", R64(place_size));
    sc_key_dec(&l, " DESC=", R32(desc_index));
    sc_key_hex(a, &l, " DBASE=", R64(desc_base));
    sc_key_hex(a, &l, " DPAGES=", R64(desc_pages));
    rp_emit(a, &l, lines);
    rp_begin(&l);
    pl_text(&l, "GUEST.DAT");
    sc_key_hex(a, &l, " BASE=", R64(guest_file_base));
    sc_key_dec(&l, " BYTES=", R64(guest_file_size));
    rp_emit(a, &l, lines);
}

#if SNAP_SP11_STICK_GIC
/* The GIC and stop record, the first extension record after the monitor's window (sp11-stick-gic-record.h), in the order a reader wants it:
 * the verdict, then what was read, old and new, the timer, what the windows showed (each interrupt on a line of its own), what EL1 was
 * given, and the final values. A slot that is still zero says the stage did not run; a record that fails its check is named and not
 * printed. At most 23 lines. */
#if SNAP_SP11_STICK_MONITOR
SC_INLINE void rp_gic_line(const struct q1n1_payload_args *a, const uint8_t *rec, unsigned id, uint32_t *lines)
{
    struct pl_line l;
    rp_begin(&l);
    if (sg_line(a, &l, id, rec)) rp_emit(a, &l, lines);
}
#endif

SC_INLINE void rp_gic(const struct q1n1_payload_args *a, const uint8_t *report, uint32_t *lines)
{
#if !SNAP_SP11_STICK_MONITOR
    static const uint8_t order[] = {SG_L_VERDICT, SG_L_FRAMES, SG_L_GICD, SG_L_GICR, SG_L_CPU, SG_L_OLD, SG_L_NEW, SG_L_TIMER, SG_L_IRQ,
                                    SG_L_CYC0, SG_L_CYC1, SG_L_CYC2, SG_L_CYC3, SG_L_DIAG, SG_L_EXC, SG_L_EL1, SG_L_CYC4, SG_L_DIAG1,
                                    SG_L_HVC, SG_L_EXC1, SG_L_HCR, SG_L_VBAR1, SG_L_SCTLR1, SG_L_FINAL, SG_L_RESTORE};
    uint32_t i;
#endif
    const uint8_t *rec = report + SG_REC_AT;
    struct pl_line l;
    int code;
    rp_begin(&l);
    if (sp11sb_zero(rec, 8)) {
        pl_text(&l, "GIC NO RECORD: THE STAGE DID NOT RUN");
        rp_emit(a, &l, lines);
        return;
    }
    if ((code = sg_record_check(rec, SG_REC_BYTES)) != 0) {
        pl_text(&l, "GIC RECORD DAMAGED");
        sc_key_dec(&l, " CODE=", (uint64_t)code);
        rp_emit(a, &l, lines);
        return;
    }
#if SNAP_SP11_STICK_MONITOR
    /* The arm half and the put-back only: what was read, what was stored and read back, and whether it went back (the windows and the
     * EL1 excursion belong to the dry run, and a line of theirs would only say that they did not run). */
    rp_gic_line(a, rec, SG_L_GICD, lines);
    rp_gic_line(a, rec, SG_L_GICR, lines);
    rp_gic_line(a, rec, SG_L_CPU, lines);
    rp_gic_line(a, rec, SG_L_NEW, lines);
    rp_gic_line(a, rec, SG_L_RESTORE, lines);
#else
    for (i = 0; i < sizeof order; i++) {
        rp_begin(&l);
        if (sg_line(a, &l, order[i], rec)) rp_emit(a, &l, lines);
    }
#endif
}
#endif

#if SNAP_SP11_STICK_MONITOR
/* ---- The monitor entry's part (sp11-stick-monitor-record.h; docs/SP11-STICK-MONITOR.md section 6) ---- */
/* Where the monitor wrote what, in bytes from the window's start (the `out` it was given: REPORT.DAT + 0x1000). From probes/xnuboot.S and
 * probes/xnuboot/report_layout.c: tools/test-sp11-stick-monitor-record.py compiles the monitor's own translation unit against these. */
#define RP_W_STATUS 0x0u                /* 0, a negative prepare status, or ESR_EL2 of an EL2 fault */
#define RP_W_ENTERED 0x8u               /* 1 once the guest was entered */
#define RP_W_WORDS 0x10u                /* eight report words */
#define RP_W_LOG 0x50u                  /* 512 events of six words: kind, pc, a, b, c, repeats */
#define RP_W_CONSOLE 0x6050u            /* the first 1,024 bytes of the guest's console output */
#define RP_W_COUNTERS 0x67C0u           /* thirteen counters */
#define RP_W_BAD 0x6830u                /* ELR_EL2 and FAR_EL2 at an EL2 fault */
#define RP_W_TERMINAL 0x6840u           /* the last event: kind, pc, a, b, c, 0 */
#define RP_W_ASYNC 0x7AC8u              /* a capture word, then x0 to x30, sp, spsr, pc, esr, far of the guest at an interrupt stop */
#define RP_EVENTS 5u                    /* events printed */
#define RP_CONSOLE_LINES 6u
#define RP_CONSOLE_WIDTH 64u

/* Names are switches, not tables of pointers: a payload has no relocation of an absolute address. */
static const char *rp_event_name(uint64_t kind)
{
    switch (kind) {
    case 1: return "MONITOR";
    case 2: return "S2_FAULT";
    case 3: return "GUEST_EXC";
    case 4: return "SYSREG";
    case 5: return "OTHER";
    case 6: return "WATCHDOG";
    case 7: return "HANDOFF";
    case 8: return "DISPATCH_CMD";
    case 9: return "PANIC_ENTRY";
    case 10: return "MAP_FAILED";
    case 11: return "MAP_OUTSIDE";
    case 12: return "SERROR";
    case 13: return "ASYNC";
    case 15: return "MAP_DEVICE";
    case 16: return "MMIO_STOP";
    case 24: return "EXCEPTION_RETURN";
    case 33: return "TIMER_FIQ";
    case 36: return "DEVICE_RESET";
    case 52: return "EARLY_STOP";
    default: return "UNNAMED";
    }
}

/* The prepare failures an SP11 run can end in (probes/run.py PREPARE_FAILURES holds the full text; tools/test-sp11-stick-monitor-record.py ties
 * each name here to that table). The number is always printed: a status not named is still a status. */
static const char *rp_status_name(int64_t s)
{
    if (s >= -144 && s <= -96) return "NVME_WINDOW";
    if (s >= -272 && s <= -258) return "S2_GEOMETRY";
    switch (s) {
    case -1: return "SHADOW_ROOT_ROOM";
    case -2: return "WALK_16K";
    case -3: return "S2_ROOT_ROOM";
    case -4: return "PROTECT_PAGE";
    case -5: return "PROTECT_VECTORS";
    case -6: return "ENTRY_SHADOW";
    case -7: return "WATCH_PAGE";
    case -8: return "TTBR0_ROOT_ROOM";
    case -9: return "TTBR0_WALK";
    case -10: return "S2_RANGES";
    case -11: return "S2_MAP";
    case -12: return "WRITE_WATCH";
    case -13: return "SLIDE_REGION";
    case -14: return "DEVICE_REGISTRY";
    case -15: return "CLOCK_ADAPTER";
    case -16: return "CPU_CONTRACT";
    case -19: return "SECONDARY_CONFIG";
    case -21: return "TRUST_CACHE";
    case -27: return "S2_PRESPLIT";
    case -28: return "THERMAL";
    case -90: return "NV2_NO_RECORD";
    case -91: return "NV2_OTHER_CORE";
    case -92: return "NV2_FEAT";
    case -93: return "NV2_NOT_PASSING";
    case -94: return "PAUTH_MIDR";
    case -95: return "PAUTH_IDS";
    case -145: return "NVME_WINDOW";
    case -146: return "VCPU_CLUSTERS";
    case -147: return "TIMER_GUARD_CNTHCTL";
    case -148: return "TIMER_GUARD_CNTVOFF";
    default: return "UNNAMED";
    }
}

/* A console byte as the panel's font has it: capitals, digits and the few signs it draws; anything else is a question mark. */
static char rp_font(uint8_t c)
{
    if (c >= 'a' && c <= 'z') return (char)(c - 32);
    if ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == ' ' || c == ':' || c == '-' || c == '.' || c == '/' || c == '=' || c == '(' || c == ')' ||
        c == '_') return (char)c;
    return '?';
}

SC_INLINE void rp_event(const struct q1n1_payload_args *a, const uint8_t *e, uint64_t n, uint32_t *lines)
{
    struct pl_line l;
    rp_begin(&l);
    sc_key_dec(&l, "MON EV ", n);
    pl_text(&l, " ");
    pl_text(&l, rp_event_name(sp11sb_rd64(e)));
    sc_key_hex(a, &l, " PC=", sp11sb_rd64(e + 8));
    sc_key_hex(a, &l, " A=", sp11sb_rd64(e + 16));
    sc_key_hex(a, &l, " B=", sp11sb_rd64(e + 24));
    sc_key_hex(a, &l, " C=", sp11sb_rd64(e + 32));
    rp_emit(a, &l, lines);
}

SC_INLINE void rp_console(const struct q1n1_payload_args *a, const uint8_t *text, uint32_t bytes, uint32_t *lines)
{
    struct pl_line l;
    uint32_t i = 0, shown = 0;
    while (i < bytes && shown < RP_CONSOLE_LINES) {
        uint32_t width = 0;
        rp_begin(&l);
        pl_text(&l, "MON CON ");
        while (i < bytes && text[i] != '\n' && text[i] != 0 && width < RP_CONSOLE_WIDTH) {
            if (l.len < Q1N1_PAYLOAD_LINE_MAX) {
                l.buf[l.len++] = rp_font(text[i]);
                l.buf[l.len] = 0;
            }
            i++;
            width++;
        }
        while (i < bytes && (text[i] == '\n' || text[i] == 0)) i++;     /* the break, and a run of them */
        if (width) {
            rp_emit(a, &l, lines);
            shown++;
        }
    }
}

/* What the monitor wrote into its window, when it returned zero (any other return leaves the window partial and nothing here is trusted: the
 * quarantine line has said so). */
SC_INLINE void rp_window(const struct q1n1_payload_args *a, const uint8_t *report, const uint8_t *rec, uint32_t *lines)
{
    const uint8_t *w = report + SP11SB_REPORT_OUT;
    uint64_t status = sp11sb_rd64(w + RP_W_STATUS), kind, n, count;
    struct pl_line l;
    uint32_t bytes;
    rp_begin(&l);
    if (status == 0) {
        sc_key_hex(a, &l, "MON STATUS=OK ENTERED=", sp11sb_rd64(w + RP_W_ENTERED));
    } else if ((int64_t)status < 0) {
        pl_text(&l, "MON PREPARE FAILED CODE=-");
        sc_dec(&l, (uint64_t)0 - status);
        pl_text(&l, " ");
        pl_text(&l, rp_status_name((int64_t)status));
    } else {
        sc_key_hex(a, &l, "MON EL2 FAULT ESR=", status);
        sc_key_hex(a, &l, " EC=", (status >> 26) & 0x3F);
        rp_emit(a, &l, lines);
        rp_begin(&l);
        sc_key_hex(a, &l, "MON FAULT ELR=", sp11sb_rd64(w + RP_W_BAD));
        sc_key_hex(a, &l, " OFF=", sp11sb_rd64(w + RP_W_BAD) - SM_U64(rec, monitor_pa));      /* in the monitor image: llvm-nm -n on its ELF */
        sc_key_hex(a, &l, " FAR=", sp11sb_rd64(w + RP_W_BAD + 8));
    }
    rp_emit(a, &l, lines);
    count = sp11sb_rd64(w + RP_W_WORDS + 8 * 3);
    rp_begin(&l);
    sc_key_dec(&l, "MON COUNTS EVENTS=", count);
    sc_key_dec(&l, " TRAPS=", sp11sb_rd64(w + RP_W_WORDS + 8 * 2));
    sc_key_dec(&l, " ABORTS=", sp11sb_rd64(w + RP_W_WORDS + 8 * 4) & 0xFFFFFFFFu);
    sc_key_dec(&l, " STEPS=", sp11sb_rd64(w + RP_W_WORDS + 8 * 4) >> 32);
    sc_key_dec(&l, " CALLS=", sp11sb_rd64(w + RP_W_WORDS + 8 * 7) & 0xFFFFFFFFu);
    sc_key_dec(&l, " DROPPED=", sp11sb_rd64(w + RP_W_COUNTERS + 8 * 6));
    rp_emit(a, &l, lines);
    kind = sp11sb_rd64(w + RP_W_TERMINAL);
    if (kind) {
        rp_begin(&l);
        pl_text(&l, "MON STOP ");
        pl_text(&l, rp_event_name(kind));
        sc_key_hex(a, &l, " PC=", sp11sb_rd64(w + RP_W_TERMINAL + 8));
        sc_key_hex(a, &l, " A=", sp11sb_rd64(w + RP_W_TERMINAL + 16));
        sc_key_hex(a, &l, " B=", sp11sb_rd64(w + RP_W_TERMINAL + 24));
        sc_key_hex(a, &l, " C=", sp11sb_rd64(w + RP_W_TERMINAL + 32));
        rp_emit(a, &l, lines);
    }
    if (sp11sb_rd64(w + RP_W_ASYNC)) {          /* the guest's state at an interrupt stop: where it was, in its own terms */
        rp_begin(&l);
        sc_key_hex(a, &l, "MON EXIT PC=", sp11sb_rd64(w + RP_W_ASYNC + 8 * 34));
        sc_key_hex(a, &l, " SPSR=", sp11sb_rd64(w + RP_W_ASYNC + 8 * 33));
        sc_key_hex(a, &l, " ESR=", sp11sb_rd64(w + RP_W_ASYNC + 8 * 35));
        sc_key_hex(a, &l, " FAR=", sp11sb_rd64(w + RP_W_ASYNC + 8 * 36));
        rp_emit(a, &l, lines);
    }
    for (n = 0; n < count && n < RP_EVENTS; n++) rp_event(a, w + RP_W_LOG + 48 * n, n, lines);
    bytes = (uint32_t)(sp11sb_rd64(w + RP_W_WORDS + 8 * 7) >> 32);
    if (bytes > 1024u) bytes = 1024u;
    rp_console(a, w + RP_W_CONSOLE, bytes, lines);
}

/* The record and, when the call returned zero, the window. */
SC_INLINE void rp_monitor(const struct q1n1_payload_args *a, const uint8_t *report, uint32_t *lines)
{
    const uint8_t *rec = report + SM_REC_AT;
    struct pl_line l;
    int code;
    rp_begin(&l);
    if (sp11sb_zero(rec, 8)) {
        pl_text(&l, "MON NO RECORD: THE STAGE DID NOT RUN");
        rp_emit(a, &l, lines);
        return;
    }
    if ((code = sm_record_check(rec, SM_REC_BYTES)) != 0) {
        pl_text(&l, "MON RECORD DAMAGED");
        sc_key_dec(&l, " CODE=", (uint64_t)code);
        rp_emit(a, &l, lines);
        return;
    }
    if (sm_line(a, &l, SM_L_VERDICT, rec)) rp_emit(a, &l, lines);
    rp_begin(&l);
    if (sm_line(a, &l, SM_L_IMAGE, rec)) rp_emit(a, &l, lines);
    rp_begin(&l);
    if (sm_line(a, &l, SM_L_IDS0, rec)) rp_emit(a, &l, lines);
    rp_begin(&l);
    if (sm_line(a, &l, SM_L_IDS1, rec)) rp_emit(a, &l, lines);
    rp_begin(&l);
    if (sm_line(a, &l, SM_L_CALL, rec)) rp_emit(a, &l, lines);
    rp_begin(&l);
    if (sm_line(a, &l, SM_L_RET, rec)) rp_emit(a, &l, lines);
    rp_begin(&l);
    if (sm_line(a, &l, SM_L_REGS, rec)) rp_emit(a, &l, lines);
    rp_begin(&l);
    if (sm_line(a, &l, SM_L_QUARANTINE, rec)) rp_emit(a, &l, lines);
    if ((SM_U32(rec, flags) & SM_F_RETURNED) && !SM_U64(rec, ret)) rp_window(a, report, rec, lines);
}
#endif

uint64_t payload_entry(const struct q1n1_payload_args *a)
{
    struct sp11sb_file files[SP11SB_FILES];
    struct pl_line l;
    const uint8_t *report;
    uint64_t base;
    uint32_t count, self, word = 0, lines = 0;
    int code;
    if (!a) return Q1N1_PAYLOAD_RET_BAD_ARGS;
    if (sp11sb_args_check(a->magic, a->version, a->size) || !a->ops.puts) return SP11SB_RET_REFUSED + SP11SB_E_ARGS_BAD;
    if (!a->ops.hex || !a->ops.load32) {
        a->ops.puts("SNAPREP REFUSED=ARGS_BAD DETAIL=OPS");
        return SP11SB_RET_REFUSED + SP11SB_E_ARGS_BAD;
    }
    if ((code = sc_files(a, files, &count, &self)) != 0) return rp_refuse(a, (uint32_t)code, 0, &lines);
    if (self != SP11SB_ROLE_REPORTER) return rp_refuse(a, SP11SB_E_ARGS_BAD, self, &lines);
    if ((code = sp11sb_files_check(files, count)) != 0) return rp_refuse(a, (uint32_t)code, sc_bad_slot(files, count), &lines);
    base = files[SP11SB_ROLE_REPORT].base;
    code = a->ops.load32(base, &word);
    if (code != Q1N1_PAYLOAD_LOAD_OK) {
        rp_begin(&l);
        pl_text(&l, "REPORT UNREADABLE");
        sc_key_hex(a, &l, " ADDR=", base);
        sc_key_dec(&l, " RC=", (uint64_t)(int64_t)code);
        rp_emit(a, &l, &lines);
        return RP_RETURN_UNREADABLE;
    }
    if (!word) {
        rp_begin(&l);
        pl_text(&l, "REPORT EMPTY: THE WRAPPER WROTE NOTHING");
        rp_emit(a, &l, &lines);
        return 0;
    }
    report = (const uint8_t *)sp11hw_view(base, files[SP11SB_ROLE_REPORT].size);
    if ((code = sp11sb_report_check(report, files[SP11SB_ROLE_REPORT].size)) != 0) {
        uint64_t ret = rp_refuse(a, (uint32_t)code, 0, &lines);
        rp_words(a, report, &lines);
        return ret;
    }
    rp_decode(a, report, &lines);
#if SNAP_SP11_STICK_MONITOR
    rp_monitor(a, report, &lines);
#endif
#if SNAP_SP11_STICK_GIC
    rp_gic(a, report, &lines);
#endif
    return 0;
}
#endif /* SNAP_SP11_STICK */
