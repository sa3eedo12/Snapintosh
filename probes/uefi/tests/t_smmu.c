/* SPDX-License-Identifier: MIT */
/* T07: the PCIe SMMUv3 state, read-only.
 *
 * Address source: the IORT, which names each SMMUv3 node's base address. The
 * descriptor's value for the PCIe SMMU is only compared with what the IORT
 * says. Registers read (Linux v6.12 drivers/iommu/arm/arm-smmu-v3/
 * arm-smmu-v3.h): IDR0 0x0, IDR1 0x4, IDR5 0x14, IIDR 0x18, CR0 0x20 (SMMUEN
 * is bit 0), CR0ACK 0x24, GBPA 0x44 (ABORT is bit 20), GERROR 0x60,
 * STRTAB_BASE_CFG 0x88. All are 32-bit reads inside the first 0x100 bytes of the
 * node's register page. Nothing is written.
 *
 * The state names follow the SMMUv3 rule that with SMMUEN clear the global
 * bypass attribute register decides (ABORT set: every transaction is aborted;
 * clear: bypass), and with SMMUEN set the per-stream table entries decide,
 * which this test does not read.
 */
#include "suite.h"

enum {
    SMMU_STATE_UNKNOWN = 0,
    SMMU_STATE_TRANSLATING, /* SMMUEN set and acknowledged: per-stream entries decide */
    SMMU_STATE_BYPASS,
    SMMU_STATE_ABORT,
    SMMU_STATE_TRANSITION   /* CR0 and CR0ACK disagree */
};

static unsigned classify(uint32_t cr0, uint32_t cr0ack, uint32_t gbpa)
{
    int en = (int)(cr0 & 1u), ack = (int)(cr0ack & 1u);

    if (en != ack)
        return SMMU_STATE_TRANSITION;
    if (en)
        return SMMU_STATE_TRANSLATING;
    return (gbpa & (1u << 20)) ? SMMU_STATE_ABORT : SMMU_STATE_BYPASS;
}

static const char *state_text(unsigned s)
{
    switch (s) {
    case SMMU_STATE_TRANSLATING:
        return "SMMUEN SET (PER-STREAM)";
    case SMMU_STATE_BYPASS:
        return "DISABLED, GBPA BYPASS";
    case SMMU_STATE_ABORT:
        return "DISABLED, GBPA ABORT";
    case SMMU_STATE_TRANSITION:
        return "CR0 AND CR0ACK DISAGREE";
    default:
        return "?";
    }
}

void tests_run_smmu(tests_ctx *c, tests_result *r)
{
    tests_iort iort;
    tests_allow allow;
    size_t len;
    unsigned read_ok = 0, failed = 0, nread;
    unsigned faults_before = c->bus->faults(c->bus->ctx);
    probe_sb *sb;
    int desc_match = 0;
    int win_st[2] = {0, 0};

    tests_init_result(r, T_SMMU);
    r->label = TL_OBSERVED;
    probe_memset(&iort, 0, sizeof iort);
    len = tests_acpi_load(c->scan, c->phys, c->phys_ctx, "IORT", c->acpi_buf, TESTS_ACPI_BUF_BYTES);
    if (len == 0 || tests_iort_parse(c->acpi_buf, len, &iort) != 0 || iort.n == 0) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, iort.valid && iort.rejected != 0 ? "SMMUV3 BASE BAD: NOT TOUCHED"
                                                            : "NO SMMUV3 NODE IN IORT: NOT TOUCHED");
        if (c->desc->pcie_smmu_base != 0)
            tests_row_text(r, "DESC HAS ONE: INFERRED, NOT USED");
        tests_result_seal(r);
        return;
    }
    /* Only the nodes that are read are admitted: a window on the list is a
     * promise that the test may touch it. */
    nread = iort.n < 2u ? iort.n : 2u;
    tests_allow_init(&allow);
    for (unsigned i = 0; i < nread; i++)
        win_st[i] = tests_allow_add_device(&allow, c->memmap, c->require_mmio_map, iort.base[i],
                                           TESTS_SMMU_WINDOW_BYTES, TESTS_ALIGN_SMMU, "IORT SMMUV3");
    tests_row_windows(r, &allow, iort.rejected, c->require_mmio_map);

    for (unsigned i = 0; i < nread; i++) {
        uint64_t base = iort.base[i];
        uint32_t idr0, idr1, idr5, cr0, cr0ack, gbpa, gerror, cfg;
        char note[40];
        probe_sb nsb;
        int ok;
        unsigned st;

        if (win_st[i] != TESTS_WIN_OK) {
            failed++;
            tests_row_refused(r, "SMMU", (int)i, win_st[i]);
            continue;
        }
        probe_sb_init(&nsb, note, sizeof note);
        probe_sb_puts(&nsb, "T07 SMMU ");
        probe_sb_hex(&nsb, base, 10);
        if (c->progress != NULL)
            c->progress(c->progress_ctx, note);

        ok = tests_rd32(c->bus, &allow, base + 0x00u, &idr0) == RD_OK &&
             tests_rd32(c->bus, &allow, base + 0x04u, &idr1) == RD_OK &&
             tests_rd32(c->bus, &allow, base + 0x14u, &idr5) == RD_OK &&
             tests_rd32(c->bus, &allow, base + 0x20u, &cr0) == RD_OK &&
             tests_rd32(c->bus, &allow, base + 0x24u, &cr0ack) == RD_OK &&
             tests_rd32(c->bus, &allow, base + 0x44u, &gbpa) == RD_OK &&
             tests_rd32(c->bus, &allow, base + 0x60u, &gerror) == RD_OK &&
             tests_rd32(c->bus, &allow, base + 0x88u, &cfg) == RD_OK;
        if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "SMMU");
            probe_sb_dec(sb, i);
            probe_sb_puts(sb, " @");
            probe_sb_hex(sb, base, 10);
        }
        if (!ok) {
            failed++;
            tests_row_text(r, "SMMU READ FAULTED: NO STATE");
            continue;
        }
        read_ok++;
        st = classify(cr0, cr0ack, gbpa);
        if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "IDR0=");
            probe_sb_hex(sb, idr0, 8);
            probe_sb_puts(sb, " S1=");
            probe_sb_dec(sb, (idr0 >> 1) & 1u);
            probe_sb_puts(sb, " S2=");
            probe_sb_dec(sb, idr0 & 1u);
        }
        if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "CR0=");
            probe_sb_hex(sb, cr0, 8);
            probe_sb_puts(sb, " ACK=");
            probe_sb_hex(sb, cr0ack, 8);
        }
        if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "GBPA=");
            probe_sb_hex(sb, gbpa, 8);
            probe_sb_puts(sb, " GERROR=");
            probe_sb_hex(sb, gerror, 8);
        }
        if ((sb = tests_row(r)) != NULL)
            probe_sb_puts(sb, state_text(st));
        (void)idr1;
        (void)idr5;
        (void)cfg;
        if (c->desc->pcie_smmu_base != 0 && c->desc->pcie_smmu_base == base)
            desc_match = 1;
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "DESC PCIE SMMU: ");
        probe_sb_puts(sb, c->desc->pcie_smmu_base == 0 ? "UNK" : desc_match ? "OK" : "DIFF");
    }
    tests_row_dec(r, "FAULTS", c->bus->faults(c->bus->ctx) - faults_before);
    tests_result_seal(r);
    r->verdict = (read_ok != 0 && failed == 0 && iort.rejected == 0) ? TV_INFO : TV_UNKNOWN;
}
