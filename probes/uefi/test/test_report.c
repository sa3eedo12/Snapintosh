/* SPDX-License-Identifier: MIT */
/* Tests for the register collectors and the on-screen report of the SP11 EL2
 * probe. The registers come from a mock reader, so the order in which the
 * collectors touch them, and the registers they refuse to touch, are checked
 * without executing a single system-register instruction. The report is built
 * from random facts and random register values: its line count must not depend
 * on either, because the screen layout is fixed before ExitBootServices while
 * the registers are only read afterwards.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dump.h"
#include "efi_types.h"
#include "fbtext.h"
#include "harness.h"
#include "logic.h"
#include "report.h"
#include "sysreg.h"

/* ---- Register collection and the report ---- */

typedef struct {
    uint64_t value[PSR_COUNT];
    int log[256];
    unsigned n;
} mock_t;

static uint64_t mock_read(void *ctx, int reg)
{
    mock_t *m = ctx;

    if (m->n < sizeof m->log / sizeof m->log[0])
        m->log[m->n] = reg;
    m->n++;
    return m->value[reg];
}

static int mock_index(const mock_t *m, int reg)
{
    for (unsigned i = 0; i < m->n && i < sizeof m->log / sizeof m->log[0]; i++)
        if (m->log[i] == reg)
            return (int)i;
    return -1;
}

static void mock_fill(mock_t *m, uint64_t currentel)
{
    memset(m, 0, sizeof *m);
    for (int i = 0; i < PSR_COUNT; i++)
        m->value[i] = 0x0123456789ABCDEFull ^ ((uint64_t)i * 0x1111111111111111ull);
    m->value[PSR_CURRENTEL] = currentel;
    /* Features present: VH, GIC, SVE, SME all non-zero. */
    m->value[PSR_ID_AA64MMFR1_EL1] = 0x0000000000000100ull;
    m->value[PSR_ID_AA64PFR0_EL1] = 0x0000000101000000ull | (1ull << 32);
    m->value[PSR_ID_AA64PFR1_EL1] = 0x0000000001000000ull;
}

static void collect_all(probe_regs *r, mock_t *m)
{
    probe_regs_init(r);
    probe_collect_el(r, mock_read, m);
    probe_collect_stage1(r, mock_read, m);
    probe_collect_stage2a(r, mock_read, m);
}

void test_collect(void)
{
    mock_t m;
    probe_regs r;

    section("register collection");

    /* Not EL2: CurrentEL is the only register ever read. */
    static const uint64_t not_el2[] = {0x0, 0x4, 0xC};
    for (size_t i = 0; i < sizeof not_el2 / sizeof not_el2[0]; i++) {
        mock_fill(&m, not_el2[i]);
        probe_regs_init(&r);
        CHECK_EQ(r.el, PROBE_EL_NOT_READ);
        CHECK_EQ(probe_collect_el(&r, mock_read, &m), (int)(not_el2[i] >> 2));
        CHECK_EQ(probe_collect_stage1(&r, mock_read, &m), -1);
        CHECK_EQ(probe_collect_stage2a(&r, mock_read, &m), -1);
        CHECK_EQ(m.n, 1);
        CHECK_EQ(m.log[0], PSR_CURRENTEL);
    }
    mock_fill(&m, 0x1);
    probe_regs_init(&r);
    CHECK_EQ(probe_collect_el(&r, mock_read, &m), -1);
    CHECK_EQ(probe_collect_stage1(&r, mock_read, &m), -1);
    CHECK_EQ(m.n, 1);

    /* Stages cannot be skipped or run before stage 0. */
    mock_fill(&m, 0x8);
    probe_regs_init(&r);
    CHECK_EQ(probe_collect_stage1(&r, mock_read, &m), -1);
    CHECK_EQ(m.n, 0);
    probe_collect_el(&r, mock_read, &m);
    CHECK_EQ(probe_collect_stage2a(&r, mock_read, &m), -1);
    CHECK_EQ(m.n, 1);
    CHECK_EQ(probe_collect_stage1(&r, mock_read, &m), 0);
    CHECK_EQ(probe_collect_stage1(&r, mock_read, &m), -1);
    CHECK_EQ(probe_collect_stage2a(&r, mock_read, &m), 0);
    CHECK_EQ(r.stage, PROBE_STAGE_2A);
    CHECK_EQ(probe_collect_stage2a(&r, mock_read, &m), -1);

    /* Everything in the report inventory is read exactly once and the registers
     * that gate other reads come first. CNTPCT_EL0 is in the list only so that
     * the assembly switch and the PE test cover the countdown timebase; the
     * collectors must never read it. */
    mock_fill(&m, 0x8);
    collect_all(&r, &m);
    CHECK_EQ(m.n, PSR_COUNT - 1);
    for (int id = 0; id < PSR_COUNT; id++) {
        int count = 0;
        for (unsigned i = 0; i < m.n; i++)
            count += m.log[i] == id;
        CHECK_EQ(count, id == PSR_CNTPCT_EL0 ? 0 : 1);
        CHECK_EQ(r.state[id], id == PSR_CNTPCT_EL0 ? PROBE_REG_NOT_READ : PROBE_REG_READ);
    }
    CHECK_EQ(m.log[0], PSR_CURRENTEL);
    CHECK(mock_index(&m, PSR_ID_AA64MMFR1_EL1) < mock_index(&m, PSR_TTBR1_EL2));
    CHECK(mock_index(&m, PSR_ID_AA64PFR0_EL1) < mock_index(&m, PSR_ICC_SRE_EL2));
    CHECK(mock_index(&m, PSR_ID_AA64PFR0_EL1) < mock_index(&m, PSR_ID_AA64ZFR0_EL1));
    CHECK(mock_index(&m, PSR_ID_AA64PFR1_EL1) < mock_index(&m, PSR_ID_AA64SMFR0_EL1));
    /* The ID registers of newer architecture revisions are not in the passive
     * image's inventory at all: a read of one has no recovery path here. */
    for (int id = 0; id < PSR_COUNT; id++) {
        const char *name = probe_sysreg_name(id);

        CHECK(strcmp(name, "ID_AA64MMFR3_EL1") != 0 && strcmp(name, "ID_AA64MMFR4_EL1") != 0 &&
              strcmp(name, "ID_AA64ISAR2_EL1") != 0 && strcmp(name, "ID_AA64DFR1_EL1") != 0);
    }
    static const int tier1[] = {PSR_HCR_EL2,    PSR_SCTLR_EL2,   PSR_TCR_EL2,    PSR_TTBR0_EL2,
                                PSR_TTBR1_EL2,  PSR_MAIR_EL2,    PSR_VBAR_EL2,   PSR_CPTR_EL2,
                                PSR_MDCR_EL2,   PSR_CNTHCTL_EL2, PSR_CNTVOFF_EL2, PSR_ICC_SRE_EL2,
                                PSR_SPSR_EL2,   PSR_ELR_EL2,     PSR_MPIDR_EL1,  PSR_CTR_EL0,
                                PSR_CNTFRQ_EL0};
    for (size_t i = 0; i < sizeof tier1 / sizeof tier1[0]; i++)
        CHECK(mock_index(&m, tier1[i]) >= 1 && mock_index(&m, tier1[i]) <= 19);

    /* Optional registers are skipped, with a stated reason, when the feature
     * that allocates them is absent. */
    mock_fill(&m, 0x8);
    m.value[PSR_ID_AA64MMFR1_EL1] = 0;
    m.value[PSR_ID_AA64PFR0_EL1] = 0;
    m.value[PSR_ID_AA64PFR1_EL1] = 0;
    collect_all(&r, &m);
    CHECK_EQ(m.n, PSR_COUNT - 5);
    CHECK_EQ(mock_index(&m, PSR_TTBR1_EL2), -1);
    CHECK_EQ(mock_index(&m, PSR_ICC_SRE_EL2), -1);
    CHECK_EQ(mock_index(&m, PSR_ID_AA64ZFR0_EL1), -1);
    CHECK_EQ(mock_index(&m, PSR_ID_AA64SMFR0_EL1), -1);
    CHECK_EQ(r.state[PSR_TTBR1_EL2], PROBE_REG_SKIPPED);
    CHECK_STR(r.skip_reason[PSR_TTBR1_EL2], "VH=0");
    CHECK_STR(r.skip_reason[PSR_ICC_SRE_EL2], "GIC=0");
    CHECK_STR(r.skip_reason[PSR_ID_AA64ZFR0_EL1], "SVE=0");
    CHECK_STR(r.skip_reason[PSR_ID_AA64SMFR0_EL1], "SME=0");
    mock_fill(&m, 0x8);
    m.value[PSR_ID_AA64MMFR1_EL1] = 0x10;
    m.value[PSR_ID_AA64PFR0_EL1] = 0xF0FFFFFFull;
    m.value[PSR_ID_AA64PFR1_EL1] = 0xF0FFFFFFull;
    collect_all(&r, &m);
    CHECK_EQ(m.n, PSR_COUNT - 5);
    CHECK_EQ(probe_bits(0xF0FFFFFFull, 27, 24), 0);
}

static void random_facts(probe_facts *f)
{
    uint16_t utf16[40];
    static const uint64_t els[] = {0, 4, 8, 12};

    memset(f, 0, sizeof *f);
    for (unsigned i = 0; i < PROBE_BUILD_ID_LEN; i++)
        f->build_id[i] = "0123456789ABCDEF"[rnd_below(16)];
    f->variant_psci = (int)rnd_below(2);
    f->reset_enabled = (int)rnd_below(2);
    f->have_preebs_el = (int)rnd_below(2);
    f->preebs_el_raw = rnd_below(2) ? els[rnd_below(4)] : rnd();
    f->fw_revision = (uint32_t)rnd();
    for (unsigned i = 0; i < 40; i++)
        utf16[i] = (uint16_t)(i < 39 ? rnd() : 0);
    probe_sanitize_utf16(f->fw_vendor, sizeof f->fw_vendor, utf16, 40);
    f->have_gop = (int)rnd_below(2);
    f->gop_width = 1 + rnd_below(16384);
    f->gop_height = 1 + rnd_below(16384);
    f->gop_stride_pixels = f->gop_width + rnd_below(65536 - f->gop_width);
    f->gop_format = rnd_below(5);
    f->gop_base = rnd();
    f->gop_size = rnd();
    f->have_layout = (int)rnd_below(2);
    f->layout_scale = 3 + rnd_below(10);
    f->layout_cols = 1 + rnd_below(3);
    f->layout_rows = 1 + rnd_below(999);
    f->layout_pages = 1 + rnd_below(8);
    f->cfg_count = rnd_below(4097);
    f->cfg_acpi20 = (int)rnd_below(2);
    f->cfg_acpi10 = (int)rnd_below(2);
    f->cfg_smbios = (int)rnd_below(2);
    f->cfg_smbios3 = (int)rnd_below(2);
    f->rsdp_status = (int)rnd_below(PROBE_ACPI_STATUS_COUNT);
    f->rsdp_revision = (uint8_t)rnd();
    f->root_is_xsdt = (int)rnd_below(2);
    f->root_status = (int)rnd_below(PROBE_ACPI_STATUS_COUNT);
    f->acpi_ok = rnd_below(PROBE_ACPI_MAX_ENTRIES + 1);
    f->acpi_rejected = rnd_below(PROBE_ACPI_MAX_ENTRIES + 1);
    f->acpi_dup = rnd_below(PROBE_ACPI_MAX_ENTRIES * 2);
    f->fadt_valid = (int)rnd_below(2);
    f->fadt_revision = (uint8_t)rnd();
    f->fadt_minor = (uint8_t)rnd();
    f->has_arm_boot_arch = (int)rnd_below(2);
    f->arm_boot_arch = (uint16_t)rnd();
    f->psci_conduit = (int)rnd_below(3);
    f->smbios_status = (int)rnd_below(7);
    f->smbios_is64 = (int)rnd_below(2);
    f->smbios_major = (uint8_t)rnd();
    f->smbios_minor = (uint8_t)rnd();
    f->smbios_table_len = rnd_below(PROBE_SMBIOS_MAX_TABLE + 1);
    f->memmap_ok = (int)rnd_below(2);
    f->memmap_count = rnd_below(PROBE_MEMMAP_MAX_DESC + 1);
    f->memmap_desc_size = 40 + 8 * rnd_below(28);
    f->have_ram = (int)rnd_below(2);
    f->ram_lo = rnd();
    f->ram_hi = rnd();
    f->dump_state = (int)rnd_below(PROBE_DUMP_STATE_COUNT);
    f->dump_run = rnd_below(PROBE_DUMP_MAX_RUN + 1);
    f->dump_files = rnd_below(PROBE_DUMP_MAX_FILES + 1);
    f->dump_bytes = rnd() % (PROBE_DUMP_MAX_TOTAL_BYTES + 1);
    f->ebs_attempts = rnd_below(9);
    f->psci.enabled = f->variant_psci;
    f->psci.state = (int)rnd_below(PROBE_PSCI_RUN_STATE_COUNT);
    f->psci.version_called = (int)rnd_below(2);
    f->psci.version = (uint32_t)rnd();
    f->psci.have_target = (int)rnd_below(2);
    f->psci.target_mpidr = rnd();
    f->psci.cpu_on_called = (int)rnd_below(2);
    f->psci.cpu_on_ret = rnd_below(2) ? (uint32_t)(-(int)rnd_below(12)) : (uint32_t)rnd();
    f->psci.arrived = (int)rnd_below(2);
    f->psci.arrived_el_raw = rnd_below(2) ? els[rnd_below(4)] : rnd();
    f->psci.arrived_mpidr = rnd();
}

static void check_report_canonical(const probe_report *r)
{
    char out[PROBE_LINE_COLS + 1];

    CHECK_EQ(r->overflowed, 0);
    CHECK(r->count > 0 && r->count <= PROBE_REPORT_MAX_LINES);
    for (unsigned i = 0; i < r->count; i++) {
        CHECK(strlen(r->line[i]) <= PROBE_LINE_PAYLOAD_MAX);
        CHECK_EQ(probe_line_format(out, r->line[i]), 0);
    }
}

static int report_has(const probe_report *r, const char *prefix)
{
    for (unsigned i = 0; i < r->count; i++)
        if (strncmp(r->line[i], prefix, strlen(prefix)) == 0)
            return 1;
    return 0;
}

static const char *report_line(const probe_report *r, const char *prefix)
{
    for (unsigned i = 0; i < r->count; i++)
        if (strncmp(r->line[i], prefix, strlen(prefix)) == 0)
            return r->line[i];
    return "";
}

void test_report(void)
{
    static const char *const el2_labels[] = {
        "HCR_EL2=",   "SCTLR_EL2=",  "TCR_EL2=",    "TTBR0_EL2=", "TTBR1_EL2=",
        "MAIR_EL2=",  "VBAR_EL2=",   "CPTR_EL2=",   "MDCR_EL2=",  "CNTHCTL_EL2=",
        "CNTVOFF_EL2=", "ICC_SRE_EL2=", "SPSR_EL2 RAW=", "ELR_EL2 RAW=", "MPIDR_EL1=",
    };
    probe_report *r = xalloc(sizeof *r);
    probe_report *baseline = xalloc(sizeof *baseline);
    probe_facts *f = xalloc(sizeof *f);
    probe_regs regs;
    mock_t m;

    section("report");
    mock_fill(&m, 0x8);
    collect_all(&regs, &m);
    random_facts(f);
    probe_report_build(baseline, f, &regs);
    check_report_canonical(baseline);

    for (size_t i = 0; i < sizeof el2_labels / sizeof el2_labels[0]; i++) {
        const char *line = report_line(baseline, el2_labels[i]);
        size_t n = strlen(el2_labels[i]);
        CHECK(strlen(line) == n + 16);
        for (size_t k = n; line[k]; k++)
            CHECK(strchr("0123456789ABCDEF", line[k]) != NULL);
    }
    CHECK(report_has(baseline, "== EL2 STATE =="));
    CHECK(report_has(baseline, "== DECODED (OBSERVED) =="));
    CHECK(report_has(baseline, "== RAW ID REGISTERS =="));
    CHECK(report_has(baseline, "== PRE-EBS FACTS =="));
    CHECK(report_has(baseline, "NOTE SPSR/ELR RAW MAY BE STALE"));
    CHECK(!report_has(baseline, "NOT AT EL2"));

    /* The line count may depend on the facts and on the mode but never on what
     * the registers contain, or the layout chosen before ExitBootServices
     * would not fit afterwards. Each iteration builds the same facts twice
     * over different register values. */
    probe_report *other = xalloc(sizeof *other);
    for (int iter = 0; iter < 3000; iter++) {
        random_facts(f);
        mock_fill(&m, 0x8);
        for (int i = 0; i < PSR_COUNT; i++)
            if (i != PSR_CURRENTEL && rnd_below(8) != 0)
                m.value[i] = rnd();
        collect_all(&regs, &m);
        probe_report_build(r, f, &regs);
        check_report_canonical(r);
        for (int i = 0; i < PSR_COUNT; i++)
            if (i != PSR_CURRENTEL)
                m.value[i] = rnd();
        collect_all(&regs, &m);
        probe_report_build(other, f, &regs);
        CHECK_EQ(other->count, r->count);
    }
    free(other);

    /* Count stability across register values and across stages. */
    {
        probe_facts facts;
        memset(&facts, 0, sizeof facts);
        memcpy(facts.build_id, "0123456789AB", 12);
        unsigned counts[6];
        int n = 0;
        mock_fill(&m, 0x8);
        probe_regs_init(&regs);
        probe_report_build(r, &facts, &regs);
        counts[n++] = r->count;
        probe_collect_el(&regs, mock_read, &m);
        probe_report_build(r, &facts, &regs);
        counts[n++] = r->count;
        probe_collect_stage1(&regs, mock_read, &m);
        probe_report_build(r, &facts, &regs);
        counts[n++] = r->count;
        probe_collect_stage2a(&regs, mock_read, &m);
        probe_report_build(r, &facts, &regs);
        counts[n++] = r->count;
        for (int i = 1; i < n; i++)
            CHECK_EQ(counts[i], counts[0]);
        char want[48];
        snprintf(want, sizeof want, "HCR_EL2=%016llX", (unsigned long long)m.value[PSR_HCR_EL2]);
        CHECK_STR(report_line(r, "HCR_EL2="), want);
        for (int iter = 0; iter < 200; iter++) {
            for (int i = 0; i < PSR_COUNT; i++)
                if (i != PSR_CURRENTEL)
                    m.value[i] = rnd();
            collect_all(&regs, &m);
            probe_report_build(r, &facts, &regs);
            CHECK_EQ(r->count, counts[0]);
            check_report_canonical(r);
        }
        facts.psci.enabled = 1;
        probe_report_build(r, &facts, &regs);
        CHECK_EQ(r->count, counts[0] + 8);
        CHECK(report_has(r, "== PSCI (OPT-IN VARIANT) =="));
        static const char *const psci_lines[] = {
            "PSCI RUN=",         "PSCI_VERSION=",       "CPU_ON TARGET=", "CPU_ON RET=",
            "SECONDARY ARRIVED=", "SECONDARY EL=",      "SECONDARY MPIDR=",
        };
        for (size_t i = 0; i < sizeof psci_lines / sizeof psci_lines[0]; i++)
            CHECK(report_has(r, psci_lines[i]));
        check_report_canonical(r);
    }

    /* Not EL2: no register line, one explanatory block, then the facts. */
    {
        static const uint64_t raws[] = {0x0, 0x4, 0xC};
        for (size_t k = 0; k < sizeof raws / sizeof raws[0]; k++) {
            mock_fill(&m, raws[k]);
            probe_regs_init(&regs);
            probe_collect_el(&regs, mock_read, &m);
            random_facts(f);
            probe_report_build(r, f, &regs);
            check_report_canonical(r);
            CHECK(report_has(r, "NOT AT EL2"));
            CHECK(report_has(r, "NO OTHER REGISTERS READ"));
            CHECK(report_has(r, "CURRENTEL RAW="));
            CHECK(report_has(r, "== PRE-EBS FACTS =="));
            for (size_t i = 0; i < sizeof el2_labels / sizeof el2_labels[0]; i++)
                CHECK(!report_has(r, el2_labels[i]));
            CHECK(!report_has(r, "== DECODED"));
            CHECK(!report_has(r, "== RAW ID"));
            CHECK(r->count < baseline->count);
        }
        mock_fill(&m, 0x1);
        probe_regs_init(&regs);
        probe_collect_el(&regs, mock_read, &m);
        probe_report_build(r, f, &regs);
        CHECK(report_has(r, "NOT AT EL2"));
        CHECK_STR(report_line(r, "CURRENTEL RAW="), "CURRENTEL RAW=0000000000000001 EL=?");
    }

    /* Decoded fields are looked up from the raw values shown beside them. */
    {
        probe_facts facts;
        memset(&facts, 0, sizeof facts);
        memcpy(facts.build_id, "0123456789AB", 12);
        mock_fill(&m, 0x8);
        m.value[PSR_ID_AA64MMFR0_EL1] = 0xF0000000ull | (0xFull << 24) | (0x1ull << 20) | 0x5ull;
        m.value[PSR_ID_AA64MMFR1_EL1] = 0x00000120ull;
        m.value[PSR_ID_AA64PFR0_EL1] = 0x1000000ull | (1ull << 32);
        m.value[PSR_ID_AA64PFR1_EL1] = 0x2000000ull;
        m.value[PSR_ID_AA64ISAR1_EL1] = 0x0ull | (5ull << 8) | (1ull << 28);
        collect_all(&regs, &m);
        probe_report_build(r, &facts, &regs);
        CHECK_STR(report_line(r, "PARANGE="), "PARANGE=5 48 BIT");
        CHECK_STR(report_line(r, "TGRAN4="), "TGRAN4=F NO");
        CHECK_STR(report_line(r, "TGRAN16="), "TGRAN16=1 YES");
        CHECK_STR(report_line(r, "TGRAN64="), "TGRAN64=F NO");
        CHECK_STR(report_line(r, "VMIDBITS="), "VMIDBITS=2 16 BIT");
        CHECK_STR(report_line(r, "VH="), "VH=1 YES");
        CHECK_STR(report_line(r, "GIC="), "GIC=1 SYSREG V3/V4");
        CHECK_STR(report_line(r, "SVE="), "SVE=1 YES");
        CHECK_STR(report_line(r, "SME="), "SME=2 SME2");
        CHECK_STR(report_line(r, "PAUTH APA="), "PAUTH APA=0 API=5");
        CHECK_STR(report_line(r, "PAUTH GPA="), "PAUTH GPA=0 GPI=1");
        /* The fields that live in the registers this image does not read are not
         * shown as if they were facts. */
        CHECK(!report_has(r, "NV_FRAC"));
        CHECK(!report_has(r, "E2H0"));
        CHECK(!report_has(r, "PAUTH ALGO"));
        CHECK(!report_has(r, "APA3"));
        CHECK_STR(report_line(r, "NOT READ HERE"), "NOT READ HERE: MMFR3 MMFR4 ISAR2 DFR1");
        /* A value the table does not list stays raw. */
        m.value[PSR_ID_AA64MMFR0_EL1] = 0xAull;
        collect_all(&regs, &m);
        probe_report_build(r, &facts, &regs);
        CHECK_STR(report_line(r, "PARANGE="), "PARANGE=A");
    }

    /* Out-of-range facts are flagged, never silently truncated. */
    {
        probe_facts facts;
        memset(&facts, 0, sizeof facts);
        facts.dump_run = 99;
        facts.dump_files = 0xFFFFFFFFu;
        facts.dump_bytes = UINT64_MAX;
        probe_regs_init(&regs);
        probe_report_build(r, &facts, &regs);
        CHECK_EQ(r->overflowed, 1);
        for (unsigned i = 0; i < r->count; i++)
            CHECK(memchr(r->line[i], 0, sizeof r->line[i]) != NULL);
    }

    /* Headers and footers. */
    {
        probe_payload p;
        probe_facts facts;
        char out[PROBE_LINE_COLS + 1];
        memset(&facts, 0, sizeof facts);
        memcpy(facts.build_id, "ABCDEF012345", 12);
        probe_report_header0(p, &facts);
        CHECK_STR(p, "SP11 EL2 PROBE B=ABCDEF012345");
        facts.variant_psci = 1;
        probe_report_header0(p, &facts);
        CHECK_STR(p, "SP11 EL2 PROBE+PSCI B=ABCDEF012345");
        CHECK_EQ(probe_line_format(out, p), 0);
        probe_regs_init(&regs);
        probe_report_header1(p, &regs, 1, 1);
        CHECK_STR(p, "CURRENTEL=? PAGE 1/1");
        regs.el = 2;
        probe_report_header1(p, &regs, 3, 5);
        CHECK_STR(p, "CURRENTEL=2 PAGE 3/5");
        probe_report_header1(p, &regs, 4294967295u, 4294967295u);
        CHECK_EQ(probe_line_format(out, p), 0);

        static const unsigned secs[] = {0, 1, 9, 10, 59, 60, 99, 3600, 0xFFFFFFFFu};
        for (int kind = 0; kind <= PROBE_FOOTER_KIND_COUNT; kind++) {
            for (size_t i = 0; i < sizeof secs / sizeof secs[0]; i++) {
                probe_report_footer(p, kind, secs[i]);
                CHECK_EQ(probe_line_format(out, p), 0);
                CHECK(strlen(p) > 0);
            }
        }
        probe_report_footer(p, PROBE_FOOTER_COUNTDOWN, 42);
        CHECK_STR(p, "RESET IN 42 S");
        probe_report_footer(p, PROBE_FOOTER_HALTED, 0);
        CHECK_STR(p, "HALTED - NO RESET");
        probe_report_footer(p, PROBE_FOOTER_HOLD, 0);
        CHECK_STR(p, "RESET DISABLED - HOLDING");
    }

    /* Firmware strings are untrusted text. */
    {
        char out[PROBE_VENDOR_MAX + 1];
        static const uint16_t vendor[] = {'e', 'D', 'k', ' ', 'I', 'I', 0x00E9, 0x4E2D, '|', ' ', ' ',
                                          'x', 0};
        probe_sanitize_utf16(out, sizeof out, vendor, 13);
        /* U+00E9 and U+4E2D become '?', the bar is outside the font so it
         * becomes '?' too, and the two spaces after it collapse into one. */
        CHECK_STR(out, "EDK II??? X");
        probe_sanitize_utf16(out, sizeof out, vendor, 3);
        CHECK_STR(out, "EDK");
        probe_sanitize_utf16(out, 4, vendor, 13);
        CHECK_STR(out, "EDK");
        probe_sanitize_utf16(out, 1, vendor, 13);
        CHECK_EQ(out[0], 0);
        probe_sanitize_utf16(out, sizeof out, NULL, 5);
        CHECK_EQ(out[0], 0);
        uint16_t longer[64];
        for (int i = 0; i < 63; i++)
            longer[i] = 'A';
        longer[63] = 0;
        char *exact = xalloc(PROBE_VENDOR_MAX + 1);
        probe_sanitize_utf16(exact, PROBE_VENDOR_MAX + 1, longer, 64);
        CHECK_EQ(strlen(exact), PROBE_VENDOR_MAX);
        free(exact);
    }

    free(f);
    free(baseline);
    free(r);
}
