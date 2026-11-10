/* SPDX-License-Identifier: MIT */
/* T01 (ID dump), T02 (NV2 wrapper), T03 (timer), T04 (DC ZVA), T08 (ADSP and
 * PMIC-GLINK) and T09 (DRAM map). */
#include "suite.h"

#include "nv2.h"

/* ---- Static statements ---- */

static const struct {
    int id;
    const char *name;
    const char *proves;
    const char *not_proves;
} statements[TESTS_COUNT] = {
    {T_ID, "ID DUMP", "P: ID REGS AS THIS CORE READS THEM", "N: NOT THAT FEATURES WORK (SEE T02)"},
    {T_NV2, "NV2 FUNC", "P: EL1 ACCESS LANDED IN VNCR PAGE", "N: STABLE OVER TIME OR ON OTHER CORES"},
    {T_TIMER, "TIMER", "P: CNTFRQ, COUNTER, ECV BITS AS READ", "N: OTHER CORES OR CNTPOFF WRITES"},
    {T_ZVA, "DC ZVA", "P: DC ZVA ZEROES ONE BLOCK AT EL2", "N: BEHAVIOUR UNDER EL1 OR STAGE 2"},
    {T_GIC, "GIC MADT", "P: GIC REGS AGREE WITH THE MADT", "N: THAT INTERRUPTS ARE DELIVERED"},
    {T_PCIE, "PCIE NVME", "P: LINK AND NVME BAR STATE AS READ", "N: STATE AFTER ANY LATER FW ACTION"},
    {T_SMMU, "PCIE SMMU", "P: SMMUV3 ENABLE AND BYPASS STATE", "N: PER-STREAM TABLE CONTENTS"},
    {T_ADSP, "ADSP GLINK", "P: NOTHING (NO JUSTIFIED ADDRESS)", "N: ADSP OR PMIC-GLINK ARE ALIVE"},
    {T_DRAM, "DRAM MAP", "P: RAM EXTENTS THE FIRMWARE REPORTS", "N: CARVE-OUTS HIDDEN FROM THE MAP"},
};

void tests_init_result(tests_result *r, int id)
{
    for (unsigned i = 0; i < TESTS_COUNT; i++) {
        if (statements[i].id == id) {
            tests_result_init(r, id, statements[i].name, statements[i].proves,
                              statements[i].not_proves);
            return;
        }
    }
    tests_result_init(r, id, "?", "P: ?", "N: ?");
}

void tests_row_refused(tests_result *r, const char *what, int index, int status)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, what);
    if (index >= 0)
        probe_sb_dec(sb, (uint64_t)index);
    probe_sb_puts(sb, " REFUSED: ");
    probe_sb_puts(sb, tests_window_name(status));
}

void tests_row_windows(tests_result *r, const tests_allow *a, unsigned parse_refused, int strict)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, strict ? "MAP STRICT MMIO=" : "MAP MMIO=");
    probe_sb_dec(sb, a->n - a->unlisted);
    if (!strict) {
        probe_sb_puts(sb, " UNLISTED=");
        probe_sb_dec(sb, a->unlisted);
    }
    probe_sb_puts(sb, " REFUSED=");
    probe_sb_dec(sb, (uint64_t)a->refused + parse_refused);
}

static int rd(tests_ctx *c, int reg, uint64_t *v)
{
    return c->bus->rd_sys(c->bus->ctx, reg, v);
}

/* ---- T01 ---- */

void tests_run_id(tests_ctx *c, tests_result *r)
{
    const char *why = NULL;

    tests_init_result(r, T_ID);
    probe_memset(&c->ids, 0, sizeof c->ids);
    for (int ti = 0; ti < TI_COUNT; ti++) {
        uint64_t v;

        if (rd(c, tests_ti_sysreg(ti), &v) == 0) {
            c->ids.have |= 1u << ti;
            c->ids.v[ti] = v;
        }
    }
    tests_ids_fill_rows(&c->ids, r);
    r->label = TL_OBSERVED;
    r->verdict = TV_INFO;
    if (!tests_ids_plausible(&c->ids, &why)) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, why);
    }
    if (c->ids.have != (1u << TI_COUNT) - 1u)
        r->verdict = TV_UNKNOWN;
    tests_result_seal(r);
}

/* ---- T02 ---- */

void tests_run_nv2(tests_ctx *c, const tests_nv2_ops *ops, uint32_t soc, uint32_t build_id,
                   tests_result *r, tests_nv2_record *rec, int *have_record)
{
    tests_nv2_obs obs;
    tests_nv2_verdict v;
    char lines[TESTS_NV2_LINES][TESTS_NV2_LINE_MAX];
    probe_payload screen[TESTS_NV2_LINES + 2];
    unsigned n;

    tests_init_result(r, T_NV2);
    tests_nv2_run(ops, &c->ids, &obs);
    v = tests_nv2_decide(&c->ids, &obs);
    r->verdict = (uint8_t)v.verdict;
    r->label = (uint8_t)v.label;
    r->fatal = obs.cleanup_failed ? 1 : 0;
    tests_nv2_fill_rows(&c->ids, &obs, &v, r);

    *have_record = tests_nv2_make_record(&c->ids, &obs, &v, c->model, soc, build_id, rec) == 0;
    if (r->fatal)
        *have_record = 0; /* a run that did not put everything back leaves no record */
    if (!*have_record) {
        tests_row_text(r, r->fatal ? "NV2 RECORD: NONE (NOT RESTORED)"
                                   : "NV2 RECORD: NOT WRITTEN (IDS UNREAD)");
        tests_result_seal(r);
        return;
    }
    if (tests_nv2rec_lines(rec, lines) != TESTS_NV2_LINES)
        *have_record = 0;
    n = *have_record ? tests_nv2rec_screen_rows((const char(*)[TESTS_NV2_LINE_MAX])lines, screen,
                                                TESTS_NV2_LINES + 2)
                     : 0;
    if (n == 0) {
        *have_record = 0;
        tests_row_text(r, "NV2 RECORD: NOT WRITTEN (BAD RECORD)");
        tests_result_seal(r);
        return;
    }
    tests_row_text(r, "NV2 RECORD (RETYPE: SEE GUIDE):");
    for (unsigned i = 0; i < n; i++)
        tests_row_text(r, screen[i]);
    tests_result_seal(r);
}

/* ---- T03 ---- */

static void put_dec_or_q(probe_sb *sb, int v)
{
    if (v < 0)
        probe_sb_putc(sb, '?');
    else
        probe_sb_dec(sb, (uint64_t)v);
}

void tests_run_timer(tests_ctx *c, tests_result *r)
{
    uint64_t frq = 0, hctl = 0, voff = 0, poff = 0, p0 = 0, vct = 0, p1 = 0;
    int ecv = tests_id_ecv(&c->ids);
    int ok_frq, ok_hctl, ok_voff, ok_cnt, have_poff = 0, poff_fault = 0, advancing = 0, window;
    probe_sb *sb;

    tests_init_result(r, T_TIMER);
    r->label = TL_OBSERVED;
    ok_frq = rd(c, TR_CNTFRQ_EL0, &frq) == 0;
    ok_hctl = rd(c, TR_CNTHCTL_EL2, &hctl) == 0;
    ok_voff = rd(c, TR_CNTVOFF_EL2, &voff) == 0;
    ok_cnt = rd(c, TR_CNTPCT_EL0, &p0) == 0 && rd(c, TR_CNTVCT_EL0, &vct) == 0;
    if (ok_cnt) {
        /* The counter is read until it changes: its frequency is what is being
         * measured, so no delay may be derived from it. */
        p1 = p0;
        for (unsigned i = 0; i < 200000u && p1 == p0; i++)
            if (rd(c, TR_CNTPCT_EL0, &p1) != 0) {
                ok_cnt = 0;
                break;
            }
        advancing = p1 != p0;
    }
    if (ecv >= 2) {
        have_poff = 1;
        poff_fault = rd(c, TR_CNTPOFF_EL2, &poff) != 0;
    }
    /* CNTVCT = physical count - CNTVOFF_EL2, so vct + voff must lie between the two
     * physical reads that bracket it (modular arithmetic: the offset may be larger
     * than the count). */
    window = ok_cnt && ok_voff && (vct + voff - p0) <= (p1 - p0);

    if (ok_frq)
        tests_row_hex(r, "CNTFRQ_EL0", frq, 16);
    else
        tests_row_str(r, "CNTFRQ_EL0", "FAULT");
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "FREQ=");
        probe_sb_dec(sb, frq);
        probe_sb_puts(sb, " HZ");
    }
    if (ok_hctl)
        tests_row_hex(r, "CNTHCTL_EL2", hctl, 16);
    else
        tests_row_str(r, "CNTHCTL_EL2", "FAULT");
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "ID ECV=");
        put_dec_or_q(sb, ecv);
        probe_sb_puts(sb, " CNTHCTL.ECV=");
        probe_sb_putc(sb, ok_hctl ? (char)('0' + ((hctl >> 12) & 1u)) : '?');
    }
    if (ok_voff)
        tests_row_hex(r, "CNTVOFF_EL2", voff, 16);
    else
        tests_row_str(r, "CNTVOFF_EL2", "FAULT");
    if (!have_poff)
        tests_row_str(r, "CNTPOFF_EL2", "N/A (ID ECV<2)");
    else if (poff_fault)
        tests_row_str(r, "CNTPOFF_EL2", "FAULT");
    else
        tests_row_hex(r, "CNTPOFF_EL2", poff, 16);
    tests_row_str(r, "COUNTER ADVANCES", advancing ? "YES" : "NO");
    tests_row_str(r, "VCT+VOFF WITHIN PCT", window ? "YES" : "NO");
    tests_row_text(r, "CORES: BOOT CPU ONLY");
    tests_result_seal(r);

    if (!ok_frq || !ok_hctl || !ok_voff || !ok_cnt)
        r->verdict = TV_UNKNOWN;
    else if (frq == 0 || !advancing || !window || poff_fault)
        r->verdict = TV_FAIL;
    else
        r->verdict = TV_PASS;
}

/* ---- T04 ---- */

void tests_run_zva(tests_ctx *c, tests_result *r)
{
    int block = tests_dczid_block_bytes(&c->ids);
    uint8_t *b = c->zva != NULL ? c->zva->buffer : NULL;
    unsigned bad_in = 0, bad_out = 0;
    int zfault = 0;
    probe_sb *sb;

    tests_init_result(r, T_ZVA);
    r->label = TL_OBSERVED;
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "DCZID BLOCK=");
        if (block < 0)
            probe_sb_putc(sb, '?');
        else if (block == 0)
            probe_sb_puts(sb, "PROHIBITED");
        else {
            probe_sb_dec(sb, (uint64_t)block);
            probe_sb_puts(sb, " BYTES");
        }
    }
    if (block > 0 && tests_ids_have(&c->ids, TI_DCZID))
        tests_row_str(r, "MONITOR NEEDS DCZID 4",
                      (c->ids.v[TI_DCZID] & 0x1Fu) == 4u ? "MATCH" : "DIFFERS");
    if (block < 0 || b == NULL) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, block < 0 ? "DCZID NOT READ" : "NO TEST BUFFER");
        tests_result_seal(r);
        return;
    }
    if (block == 0) {
        r->verdict = TV_INFO;
        tests_row_text(r, "DC ZVA NOT EXECUTED (DZP SET)");
        tests_result_seal(r);
        return;
    }
    if ((unsigned)block > TESTS_ZVA_MAX_BLOCK || (block & (block - 1)) != 0) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, "IMPLAUSIBLE BLOCK SIZE");
        tests_result_seal(r);
        return;
    }

    for (unsigned i = 0; i < 3u * (unsigned)block; i++)
        b[i] = 0xA5;
    c->zva->sync(c->zva->ctx);
    zfault = c->zva->zva(c->zva->ctx, b + block) != 0;
    c->zva->sync(c->zva->ctx);
    for (unsigned i = 0; i < (unsigned)block; i++) {
        bad_out += b[i] != 0xA5;
        bad_out += b[2u * (unsigned)block + i] != 0xA5;
        bad_in += b[(unsigned)block + i] != 0;
    }
    tests_row_str(r, "DC ZVA FAULTED", zfault ? "YES" : "NO");
    tests_row_dec(r, "BYTES NOT ZEROED IN BLOCK", bad_in);
    tests_row_dec(r, "BYTES CHANGED OUTSIDE", bad_out);
    tests_result_seal(r);
    r->verdict = (!zfault && bad_in == 0 && bad_out == 0) ? TV_PASS : TV_FAIL;
}

/* ---- T08 ---- */

/* The verdict logic is written and host-tested for the day an address source
 * exists; today nothing can justify an address, so tests_run_adsp never
 * produces an observation. */
int tests_adsp_decide(const tests_adsp_obs *o, int *label)
{
    int seen = (o->smem >= 0) + (o->glink >= 0) + (o->ipcc >= 0);
    int bad = o->smem == 0 || o->glink == 0 || o->ipcc == 0;

    *label = TL_EXPERIMENTAL;
    if (bad)
        return TV_FAIL;
    if (seen == 3)
        return TV_PASS;
    return TV_UNKNOWN;
}

void tests_run_adsp(tests_ctx *c, tests_result *r)
{
    tests_adsp_obs obs = {-1, -1, -1};
    int label;

    (void)c;
    tests_init_result(r, T_ADSP);
    r->verdict = (uint8_t)tests_adsp_decide(&obs, &label);
    r->label = TL_UNIMPLEMENTED;
    tests_row_text(r, "NOT TOUCHED: NO ADDRESS SOURCE");
    tests_row_text(r, "ACPI TABLES DO NOT NAME THE ADSP");
    tests_row_text(r, "SEE GUIDE: DSDT FROM THE STICK DUMP");
    tests_result_seal(r);
}

/* ---- T09 ---- */

#define RAM_TYPES PROBE_RAM_TYPES_MASK

static void add_extent(tests_dram *d, uint64_t s, uint64_t e)
{
    unsigned i;

    for (i = 0; i < d->extents; i++) {
        if (s <= d->ext_end[i] && e >= d->ext_base[i]) {
            if (s < d->ext_base[i])
                d->ext_base[i] = s;
            if (e > d->ext_end[i])
                d->ext_end[i] = e;
            return;
        }
    }
    if (d->extents < 8u) {
        d->ext_base[d->extents] = s;
        d->ext_end[d->extents] = e;
        d->extents++;
    } else {
        d->truncated++;
    }
}

void tests_dram_summarize(const probe_memmap *m, tests_dram *d)
{
    probe_memset(d, 0, sizeof *d);
    if (m == NULL || m->count == 0)
        return;
    for (size_t i = 0; i < m->count; i++) {
        uint32_t type;
        uint64_t phys, virt, pages, attr, end;

        if (probe_memmap_get(m, i, &type, &phys, &virt, &pages, &attr) != 0)
            return;
        if (type >= 32u || !((RAM_TYPES >> type) & 1u))
            continue;
        end = phys + pages * 4096ull;
        d->total_bytes += pages * 4096ull;
        if (!d->ok || phys < d->low)
            d->low = phys;
        if (!d->ok || end > d->high)
            d->high = end;
        d->ok = 1;
        add_extent(d, phys, end);
    }
    /* A descriptor can bridge two extents that were separate when they were added. */
    for (unsigned pass = 0; pass < 8u; pass++) {
        int merged = 0;

        for (unsigned i = 0; i < d->extents && !merged; i++)
            for (unsigned j = i + 1; j < d->extents && !merged; j++)
                if (d->ext_base[j] <= d->ext_end[i] && d->ext_end[j] >= d->ext_base[i]) {
                    if (d->ext_base[j] < d->ext_base[i])
                        d->ext_base[i] = d->ext_base[j];
                    if (d->ext_end[j] > d->ext_end[i])
                        d->ext_end[i] = d->ext_end[j];
                    d->ext_base[j] = d->ext_base[d->extents - 1];
                    d->ext_end[j] = d->ext_end[d->extents - 1];
                    d->extents--;
                    merged = 1;
                }
        if (!merged)
            break;
    }
    /* Sort the few extents by base so that the report reads low to high. */
    for (unsigned i = 1; i < d->extents; i++)
        for (unsigned j = i; j > 0 && d->ext_base[j] < d->ext_base[j - 1]; j--) {
            uint64_t tb = d->ext_base[j], te = d->ext_end[j];

            d->ext_base[j] = d->ext_base[j - 1];
            d->ext_end[j] = d->ext_end[j - 1];
            d->ext_base[j - 1] = tb;
            d->ext_end[j - 1] = te;
        }
}

int tests_dram_verdict(const tests_dram *d, unsigned ipa_bits)
{
    if (!d->ok || ipa_bits == 0 || ipa_bits >= 64u)
        return TV_UNKNOWN;
    return d->high <= (1ull << ipa_bits) ? TV_PASS : TV_FAIL;
}

void tests_run_dram(tests_ctx *c, tests_result *r)
{
    tests_dram d;
    unsigned ipa = c->desc->ipa_bits != 0 ? c->desc->ipa_bits : 36u;
    probe_sb *sb;

    tests_init_result(r, T_DRAM);
    tests_dram_summarize(c->memmap, &d);
    r->label = TL_OBSERVED;
    r->verdict = (uint8_t)tests_dram_verdict(&d, ipa);
    if (!d.ok) {
        tests_row_text(r, "NO USABLE UEFI MEMORY MAP");
        tests_result_seal(r);
        return;
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "RAM TOTAL=");
        probe_sb_dec(sb, d.total_bytes >> 20);
        probe_sb_puts(sb, " MIB IN ");
        probe_sb_dec(sb, d.extents);
        probe_sb_puts(sb, " EXTENTS");
    }
    for (unsigned i = 0; i < d.extents && i < 4u; i++) {
        if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "EXT");
            probe_sb_dec(sb, i);
            probe_sb_putc(sb, ' ');
            probe_sb_hex(sb, d.ext_base[i], 10);
            probe_sb_putc(sb, '-');
            probe_sb_hex(sb, d.ext_end[i], 10);
        }
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "HIGH END=");
        probe_sb_hex(sb, d.high, 10);
        probe_sb_puts(sb, d.high <= (1ull << ipa) ? " <= " : " > ");
        probe_sb_puts(sb, "2^");
        probe_sb_dec(sb, ipa);
    }
    if (d.truncated != 0)
        tests_row_text(r, "MORE THAN 8 EXTENTS: LIST CUT");
    tests_row_text(r, "DESC DRAM: UNKNOWN (NOT COMPARED)");
    tests_result_seal(r);
}
