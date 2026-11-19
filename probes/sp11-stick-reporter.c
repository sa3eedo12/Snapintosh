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
 * the head's summary: at most 28 more lines, from the same bytes and the same function as the wrapper's page. Read only, as before. Under the
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
 * printed. At most 28 lines. */
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
    static const uint8_t order[] = SG_REPORTER_ORDER;
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
/* Under this gate the window also prints, after MON EXIT and for a guest that ran, the decoded syndrome of a stage-2 fault and the value it wrote, the guest's registers, the
 * clock block, the guest timer, the monitor's counters, the CPU-start acknowledge of an ack monitor and its call histogram, and shares what is left of the 40 lines between the
 * events and the console: docs/SP11-STICK-REPORT-LINES.md. The first comment of this file counts the dry run's lines ("at most 9"): this build prints up to 40. */
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
#define RP_W_ENDPOINTS 0x6870u          /* calls by SPTM endpoint number: 64 buckets, then one for every call that is not an endpoint call */
#define RP_W_ASYNC 0x7AC8u              /* a capture word, then x0 to x30, sp, spsr, pc, esr, far of the guest at the stop */
#define RP_W_CLOCK_READS 0x7C40u        /* the guest clock's trapped counter reads (OUT_CLOCK_FLOW word 0) */
#define RP_W_TIMER 0x7C80u              /* the guest timer's words: reads, writes, irqs, asserts, clears, vectors, ctl, cval, irq_pc (OUT_TIMER_FLOW words 0 to 8) */
#define RP_W_CLOCK_RAW 0x8520u          /* the host-rate counter at the clock backend's first and last sample: words 8 and 9 of OUT_CLOCK_ADAPTER */
#define RP_W_ACK 0xFA10u                /* an ack monitor's CPU-start block (OUT_CPU_START_ACK, 24 words; compat/cpu-start-ack.h): "CPUACK01", struct size << 32 | version, the total, the count at +4, +8 and +12, the last PC and what, then the first 8 stores as PC and what (offset << 32 | value) */
#define RP_ACK_MAGIC UINT64_C(0x31304B4341555043)       /* "CPUACK01": CPU_START_ACK_MAGIC */
#define RP_ACK_LAYOUT ((UINT64_C(176) << 32) | 1u)      /* sizeof(struct cpu_start_ack) << 32 | CPU_START_ACK_VERSION */
#define RP_ACK_FIRST 8u                 /* CPU_START_ACK_FIRST: the stores the block keeps whole */
#define RP_ACK_WORDS 24u                /* 2 + sizeof(struct cpu_start_ack) / 8: CPU_START_ACK_REPORT_WORDS */
#define RP_EVENTS 5u                    /* events printed, at most */
#define RP_CONSOLE_LINES 6u             /* console lines printed, at most */
#define RP_CONSOLE_WIDTH 64u
#define RP_COUNTERS 13u
#define RP_ENDPOINTS 64u                /* ENDPOINT_BUCKETS of probes/xnuboot/report_layout.c: one bucket per endpoint number, the highest of SPTM's being 0x32 */
#define RP_REG_LINES 8u                 /* x0 to x30 and the SP, four to a line */
#define RP_CNT_LINES 2u                 /* the counters' lines: always two, and a third only when a line is left for the histogram after it */
#define RP_CNT_LINES_MAX 3u
#define RP_CNT_SPLIT 7u                 /* the first line holds counters 0 to 6, the second 7 to 12, while the values leave them room */
#define RP_CLK_FIELDS 4u                /* READS, AFTER, FIRST, LAST */
#define RP_TIM_FIELDS 9u                /* the guest timer's nine words */
#define RP_TIM_LINES_MAX 2u             /* the timer's lines: one, and a second when the counters and the acknowledge line still have theirs after it (the histogram does not keep any: it is the first to give way) */
#define RP_EP_LINES 3u                  /* the histogram's lines, at most */
#define RP_GIC_LINES 5u                 /* the arm half's lines, which follow the window's: never given up for the window's own */
#define RP_WIDE 95u                     /* a line the page builds from values is never wider than this: the runner cuts a line at 100, and a cut value is a wrong one */
#define RP_MORE_WIDTH 9u                /* " MORE=63": kept free at the end of the last line a group may use */
#define RP_WINDOW_BYTES 0x10000u        /* the monitor stores nothing at or above out + 0x10000: probes/xnuboot/report_layout.c */
#define RP_CAPTURE_MAX 3u               /* the capture word is 0 or the class 1, 2 or 3 that the monitor stores (every `async_captured =` of probes/xnuboot): nothing else is a capture */
#define RP_CAPTURE_S2 3u                /* the class of a stage-2 fault and of a device-window failure: the registers are those of the faulting instruction, the ESR word its syndrome */
#define RP_EV_S2_FAULT 2u               /* EV_S2_FAULT of probes/xnuboot/report_layout.c: a = IPA, b = ESR_EL2, c = FAR_EL2, pc = the guest's */
#define RP_EC_ABORT_INSN 0x20u          /* ESR_EL2.EC of an instruction abort and of a data abort from the guest's EL1 (probes/run.py s2_fault_words, shadow/dockchannel.c dc_decode) */
#define RP_EC_ABORT_DATA 0x24u
_Static_assert(RP_W_ENDPOINTS + 8 * (RP_ENDPOINTS + 1) <= RP_W_ASYNC && RP_W_ASYNC + 8 * 37 <= RP_W_CLOCK_READS && RP_W_CLOCK_READS + 8 <= RP_W_TIMER &&
               RP_W_TIMER + 8 * RP_TIM_FIELDS <= RP_W_CLOCK_RAW && RP_W_CLOCK_RAW + 16 <= RP_W_ACK && RP_W_ACK + 8 * RP_ACK_WORDS <= RP_WINDOW_BYTES &&
               RP_W_COUNTERS + 8 * RP_COUNTERS <= RP_W_BAD, "every word the page reads is in the window, and the blocks do not overlap");

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

/* Text, a decimal and a hexadecimal number onto a line, out of line: the compiler unrolls a copy of a literal at every site it is inlined and expands the digit loop of a
 * number at every one, and the 64 KiB of this binary are nearly used by the lines. They store only through the line they are given, which is the caller's stack. Key "" is
 * the hexadecimal number alone. */
static __attribute__((noinline)) void rp_put(struct pl_line *l, const char *text)
{
    pl_text(l, text);
}

static __attribute__((noinline)) void rp_put_dec(struct pl_line *l, const char *key, uint64_t value)
{
    sc_key_dec(l, key, value);
}

static __attribute__((noinline)) void rp_put_hex(const struct q1n1_payload_args *a, struct pl_line *l, const char *key, uint64_t value)
{
    sc_key_hex(a, l, key, value);
}

SC_INLINE void rp_event(const struct q1n1_payload_args *a, const uint8_t *e, uint64_t n, uint32_t *lines)
{
    struct pl_line l;
    rp_begin(&l);
    rp_put_dec(&l, "MON EV ", n);
    rp_put(&l, " ");
    rp_put(&l, rp_event_name(sp11sb_rd64(e)));
    rp_put_hex(a, &l, " PC=", sp11sb_rd64(e + 8));
    rp_put_hex(a, &l, " A=", sp11sb_rd64(e + 16));
    rp_put_hex(a, &l, " B=", sp11sb_rd64(e + 24));
    rp_put_hex(a, &l, " C=", sp11sb_rd64(e + 32));
    rp_emit(a, &l, lines);
}

/* The next non-empty console line: its width (at most RP_CONSOLE_WIDTH) from *start, with *i after it and after the run of breaks that follows. A line
 * of more than the width goes on in the next one. 0 when the text has no more. Out of line: it is called to count and again to print. */
static __attribute__((noinline)) uint32_t rp_console_segment(const uint8_t *text, uint32_t bytes, uint32_t *i, uint32_t *start)
{
    uint32_t width;
    do {
        width = 0;
        *start = *i;
        while (*i < bytes && text[*i] != '\n' && text[*i] != 0 && width < RP_CONSOLE_WIDTH) {
            (*i)++;
            width++;
        }
        while (*i < bytes && (text[*i] == '\n' || text[*i] == 0)) (*i)++;     /* the break, and a run of them */
    } while (!width && *i < bytes);
    return width;
}

/* How many console lines there are to print, at most RP_CONSOLE_LINES: the page gives them what the window's other lines leave. */
SC_INLINE uint32_t rp_console_count(const uint8_t *text, uint32_t bytes)
{
    uint32_t i = 0, start, n = 0;
    while (i < bytes && n < RP_CONSOLE_LINES) {
        if (rp_console_segment(text, bytes, &i, &start)) n++;
    }
    return n;
}

SC_INLINE void rp_console(const struct q1n1_payload_args *a, const uint8_t *text, uint32_t bytes, uint32_t *lines, uint32_t limit)
{
    struct pl_line l;
    uint32_t i = 0, start, width, shown = 0, n;
    while (i < bytes && shown < limit) {
        width = rp_console_segment(text, bytes, &i, &start);
        if (!width) break;
        rp_begin(&l);
        pl_text(&l, "MON CON ");
        for (n = 0; n < width && l.len < Q1N1_PAYLOAD_LINE_MAX; n++) {
            l.buf[l.len++] = rp_font(text[start + n]);
            l.buf[l.len] = 0;
        }
        rp_emit(a, &l, lines);
        shown++;
    }
}

/* The lines the window may still print: the budget of the payload less the lines printed so far and the GIC lines that follow it. A page that is
 * full drops the lines that come last, which would be the GIC lines, so the window's own lines are held to this. */
SC_INLINE uint32_t rp_room(uint32_t lines)
{
    return lines + RP_GIC_LINES < SP11SB_REPORTER_LINES ? SP11SB_REPORTER_LINES - RP_GIC_LINES - lines : 0;
}

/* The characters a decimal number takes, so that a line is cut between two values and never inside one. */
SC_INLINE uint32_t rp_dec_len(uint64_t value)
{
    uint32_t n = 1;
    while (value >= 10) {
        value /= 10;
        n++;
    }
    return n;
}

/* The same for a hexadecimal one (the digits after 0X, at least one). */
SC_INLINE uint32_t rp_hex_len(uint64_t value)
{
    uint32_t n = 1;
    while (value >= 16) {
        value >>= 4;
        n++;
    }
    return n;
}

/* The smaller of two counts, out of line: written inline the compiler keeps one comparison's flags alive across the next one, through an MRS and an
 * MSR of NZCV, and this binary has no instruction on a system register. */
static __attribute__((noinline)) uint32_t rp_min(uint32_t x, uint32_t y)
{
    return x < y ? x : y;
}

/* A 64-bit word of the report at a byte offset: the window's offsets above are all this way. */
SC_INLINE uint64_t rp_word(const uint8_t *base, uint32_t offset)
{
    return sp11sb_rd64(base + offset);
}

/* The thirteen counters in the order the monitor writes them at RP_W_COUNTERS (probes/xnuboot/report.c, `counters`), named for the fields of
 * struct context. A switch, as the other names: a payload has no relocation of an absolute address. */
static const char *rp_counter_name(uint32_t k)
{
    switch (k) {
    case 0: return "MAPS";          /* map_pages */
    case 1: return "MAPFAIL";       /* map_failures */
    case 2: return "DISP";          /* dispatch_cmds */
    case 3: return "PANIC";         /* panic_calls */
    case 4: return "UNDEF";         /* undef_skipped */
    case 5: return "MAPOUT";        /* map_outside */
    case 6: return "LOGDROP";       /* log_dropped */
    case 7: return "CONDROP";       /* console_dropped */
    case 8: return "RNGDROP";       /* ranges_dropped */
    case 9: return "MAPDEV";        /* map_device */
    case 10: return "MMIORD";       /* mmio_reads */
    case 11: return "MMIOWR";       /* mmio_writes */
    default: return "UARTTX";       /* uart_tx */
    }
}

/* The guest's x0 to x30 and the SP the monitor copied at the stop (fill_regs, probes/xnuboot/mmu_access.c): four values to a line, the line named
 * by the registers it holds, the last line x28, x29, x30 and the SP. The words at RP_W_ASYNC are a capture word, x0 to x30, the SP (SP_EL1 when
 * SPSR_EL2 bit 0 is set, else SP_EL0), SPSR, PC, ESR and FAR: MON EXIT prints the last four. */
SC_INLINE void rp_registers(const struct q1n1_payload_args *a, const uint8_t *w, uint32_t *lines)
{
    struct pl_line l;
    uint32_t i, j;
    for (i = 0; i < RP_REG_LINES; i++) {
        rp_begin(&l);
        rp_put_dec(&l, "MON GX", 4 * i);
        if (i == RP_REG_LINES - 1) rp_put(&l, "-SP");
        else rp_put_dec(&l, "-", 4 * i + 3);
        for (j = 0; j < 4; j++) {
            rp_put(&l, " ");
            rp_put_hex(a, &l, "", rp_word(w, RP_W_ASYNC + 8 * (1 + 4 * i + j)));
        }
        rp_emit(a, &l, lines);
    }
}

/* A value added to a line and then found too wide is taken off again, so that no line ends in a cut value. */
SC_INLINE void rp_unput(struct pl_line *l, uint32_t mark)
{
    l->len = mark;
    l->buf[mark] = 0;
}

/* The guest clock's counters: the trapped reads (`clock.reads`: the reads the monitor answered for the guest) and the host counter's first and last raw samples. LAST is the
 * monitor's OWN sample, taken by the report writer itself (probes/xnuboot/report.c reads the counter for the guest timer's control word, and the clock backend stores every sample it
 * takes as clock_raw_last): so AFTER, `LAST - ticks_call` (the wrapper's own counter read before the call), is the time from the call's start to the report, NOT the guest's last use
 * of the clock, and a small AFTER does not say that the backend was idle. FIRST is the first sample the clock backend took for any caller (the prepare, a timer, a wait or a guest
 * read). The raw words are CNTVCT_EL0 read at EL2, the physical count only while CNTVOFF_EL2 is zero (the SP11 guard). AFTER is NA when there was no sample, when the last one is
 * below the call's start or when it is above the call's end (`ticks_return`): a sample is taken inside the call, so the counters disagree. A field that would take the line past
 * RP_WIDE, or past what ` MORE=n` needs when fields follow it, is left out and the line ends with MORE=n, the number of fields left out: never with a cut value. */
SC_INLINE void rp_clock(const struct q1n1_payload_args *a, const uint8_t *w, const uint8_t *rec, uint32_t *lines)
{
    struct pl_line l;
    uint64_t first = rp_word(w, RP_W_CLOCK_RAW), last = rp_word(w, RP_W_CLOCK_RAW + 8);
    uint64_t call = rp_word(rec, offsetof(struct sw_mon_rec, ticks_call)), back = rp_word(rec, offsetof(struct sw_mon_rec, ticks_return));
    uint32_t field, mark;
    rp_begin(&l);
    rp_put_dec(&l, "MON CLK READS=", rp_word(w, RP_W_CLOCK_READS));
    for (field = 1; field < RP_CLK_FIELDS; field++) {
        mark = l.len;
        if (field == 1) {
            if (last && last >= call && last <= back) rp_put_dec(&l, " AFTER=", last - call);
            else rp_put(&l, " AFTER=NA");
        } else {
            rp_put(&l, field == 2 ? " FIRST=" : " LAST=");
            rp_put_hex(a, &l, "", field == 2 ? first : last);
        }
        if (l.len + (field + 1 < RP_CLK_FIELDS ? RP_MORE_WIDTH : 0) > RP_WIDE) {
            rp_unput(&l, mark);
            rp_put_dec(&l, " MORE=", RP_CLK_FIELDS - field);
            break;
        }
    }
    rp_emit(a, &l, lines);
}

/* The guest timer's nine words (OUT_TIMER_FLOW of probes/xnuboot/report.c, the "deadline timer" of probes/run.py), by the position they are printed at: the chain that
 * delivers a guest deadline first (the host's timer interrupts, the virtual FIQ set and cleared, the guest's entries to its FIQ vector), the guest timer's control and
 * compare value, then its writes, the PC of the last host interrupt and its reads. A switch, as the other names; `word` is the word's place at RP_W_TIMER. */
static const char *rp_timer_field(uint32_t k, uint32_t *word, uint32_t *hex)
{
    *hex = k == 4 || k == 5 || k == 7;
    switch (k) {
    case 0: *word = 2; return "IRQS";       /* timer_irqs: the monitor's timer interrupts taken (INTID 26), whatever deadline they served */
    case 1: *word = 3; return "ASSERTS";    /* timer_asserts: HCR_EL2.VF set for the guest */
    case 2: *word = 4; return "CLEARS";     /* timer_clears: and cleared */
    case 3: *word = 5; return "VECTORS";    /* timer_vectors: entries into the guest's FIQ vector with VF set */
    case 4: *word = 6; return "CTL";        /* guest_timer_control at the report: ENABLE 1, IMASK 2, ISTATUS 4 (5 is a deadline that has passed, unmasked) */
    case 5: *word = 7; return "CVAL";       /* the guest's compare value */
    case 6: *word = 1; return "WRITES";     /* guest writes to the timer's registers */
    case 7: *word = 8; return "IRQPC";      /* ELR_EL2 at the last of those interrupts */
    default: *word = 0; return "READS";     /* guest reads of the timer's registers */
    }
}

/* MON TIM: the guest timer's words as NAME=value, the counters in decimal and CTL, CVAL and IRQPC in hex, in the order of rp_timer_field. `budget` lines at most (one
 * or two): a pair that does not fit the line goes on the next, and those that do not fit the last are counted in MORE, never cut and never dropped unsaid. */
SC_INLINE void rp_timer(const struct q1n1_payload_args *a, const uint8_t *w, uint32_t *lines, uint32_t budget)
{
    struct pl_line l;
    uint32_t k = 0, used = 1, mark, word, hex;
    rp_begin(&l);
    rp_put(&l, "MON TIM");
    while (k < RP_TIM_FIELDS) {
        mark = l.len;
        rp_put(&l, " ");
        rp_put(&l, rp_timer_field(k, &word, &hex));
        rp_put(&l, "=");
        if (hex) rp_put_hex(a, &l, "", rp_word(w, RP_W_TIMER + 8 * word));
        else rp_put_dec(&l, "", rp_word(w, RP_W_TIMER + 8 * word));
        if (l.len + (used == budget && k + 1 < RP_TIM_FIELDS ? RP_MORE_WIDTH : 0) <= RP_WIDE) {
            k++;
            continue;
        }
        rp_unput(&l, mark);
        if (used == budget) break;
        rp_emit(a, &l, lines);
        used++;
        rp_begin(&l);
        rp_put(&l, "MON TIM");
    }
    if (k < RP_TIM_FIELDS) rp_put_dec(&l, " MORE=", RP_TIM_FIELDS - k);
    rp_emit(a, &l, lines);
}

/* The thirteen counters in the order of rp_counter_name, seven and six to a line while the values leave them room, each by its name. `budget` lines at most:
 * a counter that does not fit the line goes on the next one, and those that do not fit the last are counted in MORE, never cut and never dropped unsaid. */
SC_INLINE void rp_counters(const struct q1n1_payload_args *a, const uint8_t *w, uint32_t *lines, uint32_t budget)
{
    struct pl_line l;
    uint32_t k = 0, used = 1, mark;
    rp_begin(&l);
    rp_put(&l, "MON CNT");
    while (k < RP_COUNTERS) {
        if (k == RP_CNT_SPLIT && used == 1) {
            rp_emit(a, &l, lines);
            used++;
            rp_begin(&l);
            rp_put(&l, "MON CNT");
        }
        mark = l.len;
        rp_put(&l, " ");
        rp_put(&l, rp_counter_name(k));
        rp_put_dec(&l, "=", rp_word(w, RP_W_COUNTERS + 8 * k));
        if (l.len + (used == budget && k + 1 < RP_COUNTERS ? RP_MORE_WIDTH : 0) <= RP_WIDE) {
            k++;
            continue;
        }
        rp_unput(&l, mark);
        if (used == budget) break;
        rp_emit(a, &l, lines);
        used++;
        rp_begin(&l);
        rp_put(&l, "MON CNT");
    }
    if (k < RP_COUNTERS) rp_put_dec(&l, " MORE=", RP_COUNTERS - k);
    rp_emit(a, &l, lines);
}

/* One " KEY=n" of a syndrome, or " KEY=NA" when the bits that name it mean something else for this class of exception. */
static __attribute__((noinline)) void rp_esr_field(struct pl_line *l, const char *key, uint32_t valid, uint64_t value)
{
    if (valid) {
        rp_put_dec(l, key, value);
    } else {
        rp_put(l, key);
        rp_put(l, "NA");
    }
}

/* The syndrome of a stage-2 fault, from the stop's ESR word (`b` of EV_S2_FAULT), with the fields where probes/run.py s2_fault_words and the monitor's dc_decode
 * (shadow/dockchannel.c) take them. Only an abort from the guest's EL1 (EC 0x20 or 0x24) has a fault status and S1PTW; ISV, the size, SRT and WnR are a data abort's, and the
 * size (SAS, ESR[23:22], printed as the bytes it means: 1, 2, 4 or 8, so that a W-register store reads SIZE=4 and not the code 2) and SRT mean something only with ISV set: ISV
 * clear (a pair or a writeback form) leaves the instruction at the PC as the only word on what it did. */
SC_INLINE void rp_esr(const struct q1n1_payload_args *a, uint64_t esr, uint32_t *lines)
{
    struct pl_line l;
    uint32_t ec = (uint32_t)(esr >> 26) & 0x3F, data = ec == RP_EC_ABORT_DATA, abort = data || ec == RP_EC_ABORT_INSN, isv = data && ((esr >> 24) & 1);
    rp_begin(&l);
    rp_put(&l, "MON ESR");
    rp_put_hex(a, &l, " EC=", ec);
    rp_esr_field(&l, " ISV=", data, (esr >> 24) & 1);
    rp_esr_field(&l, " SIZE=", isv, 1u << ((esr >> 22) & 3));
    rp_esr_field(&l, " SRT=", isv, (esr >> 16) & 0x1F);
    rp_esr_field(&l, " WNR=", data, (esr >> 6) & 1);
    rp_esr_field(&l, " S1PTW=", abort, (esr >> 7) & 1);
    rp_put(&l, " DFSC=");
    if (abort) rp_put_hex(a, &l, "", esr & 0x3F);
    else rp_put(&l, "NA");
    rp_emit(a, &l, lines);
}

/* Whether the syndrome describes a store the guest's own instruction made: a data abort with ISV set and WnR set, and not on the stage-1 table walk (S1PTW), whose
 * access is the hardware's and not the instruction's. Only then is the register that SRT names the data that was written. */
SC_INLINE uint32_t rp_store_described(uint64_t esr)
{
    return ((esr >> 26) & 0x3F) == RP_EC_ABORT_DATA && ((esr >> 24) & 1) && ((esr >> 6) & 1) && !((esr >> 7) & 1);
}

/* What the store that faulted wrote: the guest register that SRT names, whole (SRT 31 is the zero register, as the monitor's own handlers read it: shadow/dockchannel.c
 * and probes/xnuboot/mmio_dispatch.c), and the part of it the access carried, its low SAS bytes, as dc_narrow takes it. The registers are the capture's. */
SC_INLINE void rp_wval(const struct q1n1_payload_args *a, const uint8_t *w, uint64_t esr, uint32_t *lines)
{
    struct pl_line l;
    uint32_t srt = (uint32_t)(esr >> 16) & 0x1F, bits = 8u << ((esr >> 22) & 3);
    uint64_t value = srt == 31 ? 0 : rp_word(w, RP_W_ASYNC + 8 * (1 + srt));
    rp_begin(&l);
    rp_put(&l, "MON WVAL ");
    if (srt == 31) rp_put(&l, "XZR");
    else rp_put_dec(&l, "X", srt);
    rp_put(&l, "=");
    rp_put_hex(a, &l, "", value);
    rp_put(&l, " DATA=");
    rp_put_hex(a, &l, "", bits < 64 ? value & ((UINT64_C(1) << bits) - 1) : value);
    rp_emit(a, &l, lines);
}

/* The calls by endpoint: the total over the 64 buckets and the other bucket, then the buckets that are not zero as ENDPOINT:CALLS in ascending
 * endpoint number (decimal; docs/SP11-STICK-REPORT-LINES.md names them). `budget` lines at most; what does not fit is counted in MORE, not dropped
 * unsaid. */
SC_INLINE void rp_endpoints(const struct q1n1_payload_args *a, const uint8_t *w, uint32_t *lines, uint32_t budget)
{
    struct pl_line l;
    uint64_t other = rp_word(w, RP_W_ENDPOINTS + 8 * RP_ENDPOINTS), total = other, calls;
    uint32_t n, used = 1, hidden = 0;
    for (n = 0; n < RP_ENDPOINTS; n++) total += rp_word(w, RP_W_ENDPOINTS + 8 * n);
    rp_begin(&l);
    rp_put_dec(&l, "MON EP TOTAL=", total);
    rp_put_dec(&l, " OTHER=", other);
    for (n = 0; n < RP_ENDPOINTS; n++) {
        calls = rp_word(w, RP_W_ENDPOINTS + 8 * n);
        if (!calls) continue;
        if (hidden || l.len + 2 + rp_dec_len(n) + rp_dec_len(calls) + (used == budget ? RP_MORE_WIDTH : 0) > RP_WIDE) {
            if (hidden || used == budget) {
                hidden++;
                continue;
            }
            rp_emit(a, &l, lines);
            used++;
            rp_begin(&l);
            rp_put(&l, "MON EP");
        }
        rp_put_dec(&l, " ", n);
        rp_put_dec(&l, ":", calls);
    }
    if (hidden) rp_put_dec(&l, " MORE=", hidden);
    rp_emit(a, &l, lines);
}

/* An ack monitor's CPU-start acknowledge (compat/cpu-start-ack.h; docs/SP11-MONITOR-PROFILE.md section 10): the guest's 32-bit stores to PMGR's CPU-start block that the monitor
 * took and ignored, no CPU started. COUNT is all of them; then the first eight, each as `offset=value` (offset 4, 8 or 12: the global, E-cluster and P-cluster masks; the value is
 * the 32 bits written) and MORE=n for the stores that are not listed, the ones past the eight the block keeps and the ones that do not fit the line, the number counted exactly
 * so that the room for it is too. Every store is a 32-bit one (cpu_start_ack_match accepts no other width), so the width is not in the block and is not printed. The block is
 * there when its first word is the magic; one whose layout word is not the one this reads is named and not read. */
SC_INLINE void rp_ack(const struct q1n1_payload_args *a, const uint8_t *w, uint32_t *lines)
{
    struct pl_line l;
    uint64_t total = rp_word(w, RP_W_ACK + 16), what;
    uint32_t k, n, mark;
    rp_begin(&l);
    if (rp_word(w, RP_W_ACK + 8) != RP_ACK_LAYOUT) {
        rp_put_hex(a, &l, "MON ACK LAYOUT=", rp_word(w, RP_W_ACK + 8));
        rp_put(&l, " NOT READ");
        rp_emit(a, &l, lines);
        return;
    }
    rp_put_dec(&l, "MON ACK COUNT=", total);
    n = total < RP_ACK_FIRST ? (uint32_t)total : RP_ACK_FIRST;
    for (k = 0; k < n; k++) {
        mark = l.len;
        what = rp_word(w, RP_W_ACK + 72 + 16 * k);
        rp_put_dec(&l, " ", what >> 32);
        rp_put(&l, "=");
        rp_put_hex(a, &l, "", what & 0xFFFFFFFFu);
        if (l.len + (total - (k + 1) ? 6 + rp_dec_len(total - (k + 1)) : 0) > RP_WIDE) {
            rp_unput(&l, mark);
            break;
        }
    }
    if (total - k) rp_put_dec(&l, " MORE=", total - k);
    rp_emit(a, &l, lines);
}

/* What the monitor wrote into its window, when it returned zero (any other return leaves the window partial and nothing here is trusted: the
 * quarantine line has said so). After MON EXIT, and only for a guest that ran (status zero): for a stage-2 fault the syndrome and the value the store wrote, then the
 * registers, the clock, the guest timer, the counters, an ack monitor's CPU-start acknowledge and the calls by endpoint, in that order of worth (the histogram is the first to give
 * way); the events and the console follow and have the lines that are left, at least one each. The guest timer is printed, like the registers, only with a capture: a window
 * without one keeps the page it had without that line; a capture word that is no class is named (MON CAPTURE WORD=), not read as a capture. */
SC_INLINE void rp_window(const struct q1n1_payload_args *a, const uint8_t *report, const uint8_t *rec, uint32_t *lines)
{
    const uint8_t *w = report + SP11SB_REPORT_OUT;
    uint64_t status = sp11sb_rd64(w + RP_W_STATUS), kind, esr, n, count, captured;
    struct pl_line l;
    uint32_t bytes, events, console, keep, room, shown_events, shown_console, ack = 0;
    rp_begin(&l);
    if (status == 0) {
        rp_put_hex(a, &l, "MON STATUS=OK ENTERED=", sp11sb_rd64(w + RP_W_ENTERED));
    } else if ((int64_t)status < 0) {
        rp_put(&l, "MON PREPARE FAILED CODE=-");
        rp_put_dec(&l, "", (uint64_t)0 - status);
        rp_put(&l, " ");
        rp_put(&l, rp_status_name((int64_t)status));
    } else {
        rp_put_hex(a, &l, "MON EL2 FAULT ESR=", status);
        rp_put_hex(a, &l, " EC=", (status >> 26) & 0x3F);
        rp_emit(a, &l, lines);
        rp_begin(&l);
        rp_put_hex(a, &l, "MON FAULT ELR=", sp11sb_rd64(w + RP_W_BAD));
        rp_put_hex(a, &l, " OFF=", sp11sb_rd64(w + RP_W_BAD) - SM_U64(rec, monitor_pa));      /* in the monitor image: llvm-nm -n on its ELF */
        rp_put_hex(a, &l, " FAR=", sp11sb_rd64(w + RP_W_BAD + 8));
    }
    rp_emit(a, &l, lines);
    count = sp11sb_rd64(w + RP_W_WORDS + 8 * 3);
    rp_begin(&l);
    rp_put_dec(&l, "MON COUNTS EVENTS=", count);
    rp_put_dec(&l, " TRAPS=", sp11sb_rd64(w + RP_W_WORDS + 8 * 2));
    rp_put_dec(&l, " ABORTS=", sp11sb_rd64(w + RP_W_WORDS + 8 * 4) & 0xFFFFFFFFu);
    rp_put_dec(&l, " STEPS=", sp11sb_rd64(w + RP_W_WORDS + 8 * 4) >> 32);
    rp_put_dec(&l, " CALLS=", sp11sb_rd64(w + RP_W_WORDS + 8 * 7) & 0xFFFFFFFFu);
    rp_put_dec(&l, " DROPPED=", sp11sb_rd64(w + RP_W_COUNTERS + 8 * 6));
    rp_emit(a, &l, lines);
    kind = sp11sb_rd64(w + RP_W_TERMINAL);
    if (kind) {
        rp_begin(&l);
        rp_put(&l, "MON STOP ");
        rp_put(&l, rp_event_name(kind));
        rp_put_hex(a, &l, " PC=", sp11sb_rd64(w + RP_W_TERMINAL + 8));
        rp_put_hex(a, &l, " A=", sp11sb_rd64(w + RP_W_TERMINAL + 16));
        rp_put_hex(a, &l, " B=", sp11sb_rd64(w + RP_W_TERMINAL + 24));
        if (l.len + 5 + rp_hex_len(sp11sb_rd64(w + RP_W_TERMINAL + 32)) > Q1N1_PAYLOAD_LINE_MAX) {      /* C= would be cut by the runner (boot 25 lost the last two digits of a kernel address): it has a line of its own */
            rp_emit(a, &l, lines);
            rp_begin(&l);
            rp_put(&l, "MON STOP");
        }
        rp_put_hex(a, &l, " C=", sp11sb_rd64(w + RP_W_TERMINAL + 32));
        rp_emit(a, &l, lines);
    }
    captured = sp11sb_rd64(w + RP_W_ASYNC);
    if (captured - 1 >= RP_CAPTURE_MAX) {       /* a word that is not a class the monitor stores, zero included, is no capture: the page has none of its lines */
        if (captured && status == 0) {          /* but a word that is not zero says so, for a guest that ran: a window of another build or a damaged one, not a window that took none */
            rp_begin(&l);
            rp_put_hex(a, &l, "MON CAPTURE WORD=", captured);
            rp_put(&l, " IS NO CLASS");
            rp_emit(a, &l, lines);
        }
        captured = 0;
    }
    if (captured) {                             /* the guest's state at the stop: where it was, in its own terms */
        rp_begin(&l);
        rp_put_hex(a, &l, "MON EXIT PC=", sp11sb_rd64(w + RP_W_ASYNC + 8 * 34));
        rp_put_hex(a, &l, " SPSR=", sp11sb_rd64(w + RP_W_ASYNC + 8 * 33));
        rp_put_hex(a, &l, " ESR=", sp11sb_rd64(w + RP_W_ASYNC + 8 * 35));
        rp_put_hex(a, &l, " FAR=", sp11sb_rd64(w + RP_W_ASYNC + 8 * 36));
        rp_emit(a, &l, lines);
    }
    bytes = (uint32_t)(sp11sb_rd64(w + RP_W_WORDS + 8 * 7) >> 32);
    if (bytes > 1024u) bytes = 1024u;
    events = count < RP_EVENTS ? (uint32_t)count : RP_EVENTS;
    console = rp_console_count(w + RP_W_CONSOLE, bytes);
    keep = (events ? 1u : 0u) + (console ? 1u : 0u);
    if (status == 0) {
        esr = sp11sb_rd64(w + RP_W_TERMINAL + 24);
        ack = rp_word(w, RP_W_ACK) == RP_ACK_MAGIC;
        if (kind == RP_EV_S2_FAULT && rp_room(*lines) >= 1 + keep) rp_esr(a, esr, lines);
        if (kind == RP_EV_S2_FAULT && captured == RP_CAPTURE_S2 && rp_store_described(esr) && rp_room(*lines) >= 1 + keep) rp_wval(a, w, esr, lines);
        if (captured && rp_room(*lines) >= RP_REG_LINES + keep) rp_registers(a, w, lines);
        if (rp_room(*lines) >= 1 + keep) rp_clock(a, w, rec, lines);
        if (captured && rp_room(*lines) >= 1 + keep) rp_timer(a, w, lines, rp_room(*lines) - keep >= RP_TIM_LINES_MAX + RP_CNT_LINES + ack ? RP_TIM_LINES_MAX : 1);
        if (rp_room(*lines) >= RP_CNT_LINES + keep) rp_counters(a, w, lines, rp_room(*lines) - keep > RP_CNT_LINES + 1 ? RP_CNT_LINES_MAX : RP_CNT_LINES);
        if (ack && rp_room(*lines) >= 1 + keep) rp_ack(a, w, lines);
        if (rp_room(*lines) > keep) rp_endpoints(a, w, lines, rp_min(rp_room(*lines) - keep, RP_EP_LINES));
    }
    /* The first event and the first console line are never given up; the console has the rest of the room before the later events, which are the
     * same early calls on every run of one kernel. */
    room = rp_room(*lines);
    shown_events = rp_min(events, rp_min(1, room));
    shown_console = rp_min(console, room - shown_events);
    shown_events += rp_min(events - shown_events, room - shown_events - shown_console);
    for (n = 0; n < shown_events; n++) rp_event(a, w + RP_W_LOG + 48 * n, n, lines);
    rp_console(a, w + RP_W_CONSOLE, bytes, lines, shown_console);
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
