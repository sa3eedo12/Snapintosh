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
 * the head's summary: at most 23 more lines, from the same bytes and the same function as the wrapper's page. Read only, as before.)
 *
 * Returns 0 after a decoded report or an empty one, 0x1000 + code when it refused its own arguments, a slot or the head, and 2 when
 * the guarded read of REPORT.DAT failed. Evidence: EXPERIMENTAL; hardware NOT RUN. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK
#include "sp11-stick-common.h"
#if SNAP_SP11_STICK_GIC
#include "sp11-stick-gic-record.h"
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
        pl_text(&l, "DRY RUN COMPLETE STAGE=DONE: THE MONITOR WAS NOT CALLED");
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
SC_INLINE void rp_gic(const struct q1n1_payload_args *a, const uint8_t *report, uint32_t *lines)
{
    static const uint8_t order[] = {SG_L_VERDICT, SG_L_FRAMES, SG_L_GICD, SG_L_GICR, SG_L_CPU, SG_L_OLD, SG_L_NEW, SG_L_TIMER, SG_L_IRQ,
                                    SG_L_CYC0, SG_L_CYC1, SG_L_CYC2, SG_L_CYC3, SG_L_DIAG, SG_L_EXC, SG_L_EL1, SG_L_CYC4, SG_L_DIAG1,
                                    SG_L_HVC, SG_L_EXC1, SG_L_HCR, SG_L_VBAR1, SG_L_SCTLR1, SG_L_FINAL, SG_L_RESTORE};
    const uint8_t *rec = report + SG_REC_AT;
    struct pl_line l;
    uint32_t i;
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
    for (i = 0; i < sizeof order; i++) {
        rp_begin(&l);
        if (sg_line(a, &l, order[i], rec)) rp_emit(a, &l, lines);
    }
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
#if SNAP_SP11_STICK_GIC
    rp_gic(a, report, &lines);
#endif
    return 0;
}
#endif /* SNAP_SP11_STICK */
