/* SPDX-License-Identifier: MIT */
/* T05: the GIC against the MADT.
 *
 * Addresses: the distributor, the redistributor discovery ranges and the ITS
 * frames come from the MADT, nothing else; the descriptor is only compared
 * afterwards. Register offsets are the architectural GICv3 ones (Arm GIC
 * Architecture Specification; Linux v6.12 include/linux/irqchip/arm-gic-v3.h:
 * GICD_CTLR 13, GICD_TYPER 14, GICD_IIDR 15, GICD_PIDR2 45, GICR_IIDR 115,
 * GICR_TYPER 116, GICR_WAKER 118, GICR_TYPER_LAST 246, GITS_CTLR 362,
 * GITS_IIDR 363, GITS_TYPER 364, GIC_PIDR2_ARCH_MASK 100). Every access is a
 * guarded 32-bit read; no register is written.
 */
#include "suite.h"

#define GICD_CTLR 0x0000u
#define GICD_TYPER 0x0004u
#define GICD_IIDR 0x0008u
#define GIC_PIDR2 0xFFE8u
#define GICR_TYPER 0x0008u
#define GICR_WAKER 0x0014u
#define GITS_CTLR 0x0000u
#define GITS_TYPER 0x0008u
#define GICR_RD_SGI_BYTES 0x20000u
#define MAX_FRAMES 64u

typedef struct {
    unsigned frames;
    unsigned ranges_walked;
    unsigned ranges_with_last; /* ranges whose walk reached a frame marked Last */
    int stopped;               /* a walk ended early: fault or a bad PIDR2 */
    uint32_t aff[MAX_FRAMES];
    uint64_t addr[MAX_FRAMES];
    unsigned awake;
    unsigned asleep;
} gicr_walk;

static uint32_t mpidr_to_aff(uint64_t mpidr)
{
    return (uint32_t)((((mpidr >> 32) & 0xFFu) << 24) | (mpidr & 0xFFFFFFu));
}

/* Walks one discovery range frame by frame. A frame is RD_base plus SGI_base
 * (two 64 KiB pages), plus two more pages when GICR_TYPER.VLPIS is set; the
 * last frame is marked by GICR_TYPER.Last. */
static void walk_range(const tests_bus *bus, const tests_allow *allow, tests_range range,
                       gicr_walk *w)
{
    uint64_t frame = range.base;
    uint64_t end = range.base + range.len;

    w->ranges_walked++;
    while (w->frames < MAX_FRAMES && frame + GICR_RD_SGI_BYTES <= end) {
        uint32_t pidr2, typer_lo, typer_hi, waker;
        unsigned stride = GICR_RD_SGI_BYTES;

        if (tests_rd32(bus, allow, frame + GIC_PIDR2, &pidr2) != RD_OK ||
            tests_rd32(bus, allow, frame + GICR_TYPER, &typer_lo) != RD_OK ||
            tests_rd32(bus, allow, frame + GICR_TYPER + 4u, &typer_hi) != RD_OK ||
            tests_rd32(bus, allow, frame + GICR_WAKER, &waker) != RD_OK) {
            w->stopped = 1;
            return;
        }
        if (((pidr2 >> 4) & 0xFu) != 3u && ((pidr2 >> 4) & 0xFu) != 4u) {
            w->stopped = 1;
            return;
        }
        w->aff[w->frames] = typer_hi;
        w->addr[w->frames] = frame;
        w->frames++;
        if (waker & 2u)
            w->asleep++;
        else
            w->awake++;
        if (typer_lo & 2u) /* VLPIS: two more pages */
            stride += GICR_RD_SGI_BYTES;
        if (typer_lo & 0x10u) {
            w->ranges_with_last++;
            return;
        }
        frame += stride;
    }
}

static const char *cmp(uint64_t known, uint64_t seen)
{
    if (known == 0)
        return "UNK";
    return known == seen ? "OK" : "DIFF";
}

void tests_run_gic(tests_ctx *c, tests_result *r)
{
    tests_madt madt;
    size_t len;
    tests_allow allow;
    uint32_t ctlr = 0, typer = 0, iidr = 0, pidr2 = 0;
    unsigned arch = 0, unreadable = 0, matched = 0, mismatched = 0, addr_mismatch = 0;
    gicr_walk w;
    probe_sb *sb;
    const tests_desc *d = c->desc;
    unsigned faults_before = c->bus->faults(c->bus->ctx);
    int gicd_st, complete;
    int gicr_ok[TESTS_MAX_GICR] = {0}, its_ok[TESTS_MAX_ITS] = {0};

    tests_init_result(r, T_GIC);
    r->label = TL_OBSERVED;
    probe_memset(&w, 0, sizeof w);
    len = tests_acpi_load(c->scan, c->phys, c->phys_ctx, "APIC", c->acpi_buf, TESTS_ACPI_BUF_BYTES);
    if (len == 0 || tests_madt_parse(c->acpi_buf, len, &madt) != 0) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, "NO USABLE MADT: NOTHING TOUCHED");
        tests_result_seal(r);
        return;
    }
    if (!madt.have_gicd || madt.duplicate_gicd != 0) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, madt.have_gicd ? "MADT HAS TWO GICD ENTRIES" : "MADT HAS NO GICD ENTRY");
        tests_result_seal(r);
        return;
    }

    tests_allow_init(&allow);
    gicd_st = madt.gicd_status;
    if (gicd_st == TESTS_WIN_OK)
        gicd_st = tests_allow_add_device(&allow, c->memmap, c->require_mmio_map, madt.gicd_base,
                                         TESTS_GIC_FRAME_BYTES, TESTS_ALIGN_GICD, "MADT GICD");
    if (gicd_st != TESTS_WIN_OK) {
        r->verdict = TV_UNKNOWN;
        tests_row_hex(r, "GICD BASE", madt.gicd_base, 10);
        tests_row_refused(r, "GICD", -1, gicd_st);
        tests_row_text(r, "NOTHING TOUCHED");
        tests_result_seal(r);
        return;
    }
    for (unsigned i = 0; i < madt.n_gicr; i++) {
        int st = tests_allow_add_device(&allow, c->memmap, c->require_mmio_map, madt.gicr[i].base,
                                        madt.gicr[i].len, TESTS_ALIGN_GICR, "MADT GICR");

        gicr_ok[i] = st == TESTS_WIN_OK;
        if (st != TESTS_WIN_OK)
            tests_row_refused(r, "GICR", (int)i, st);
    }
    for (unsigned i = 0; i < madt.n_its; i++) {
        int st = tests_allow_add_device(&allow, c->memmap, c->require_mmio_map, madt.its[i],
                                        TESTS_GIC_FRAME_BYTES, TESTS_ALIGN_ITS, "MADT ITS");

        its_ok[i] = st == TESTS_WIN_OK;
        if (st != TESTS_WIN_OK)
            tests_row_refused(r, "ITS", (int)i, st);
    }
    /* A redistributor range that was not read cannot be told apart from a CPU
     * without one, so the per-CPU match below is only made when every range the
     * MADT listed was admitted. */
    complete = madt.rejected == 0 && allow.refused == 0;
    tests_row_windows(r, &allow, madt.rejected, c->require_mmio_map);

    if (tests_rd32(c->bus, &allow, madt.gicd_base + GICD_CTLR, &ctlr) != RD_OK ||
        tests_rd32(c->bus, &allow, madt.gicd_base + GICD_TYPER, &typer) != RD_OK ||
        tests_rd32(c->bus, &allow, madt.gicd_base + GICD_IIDR, &iidr) != RD_OK ||
        tests_rd32(c->bus, &allow, madt.gicd_base + GIC_PIDR2, &pidr2) != RD_OK)
        unreadable = 1;
    arch = (pidr2 >> 4) & 0xFu;

    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "GICD @");
        probe_sb_hex(sb, madt.gicd_base, 10);
        probe_sb_puts(sb, " ARCH=");
        if (unreadable)
            probe_sb_putc(sb, '?');
        else
            probe_sb_dec(sb, arch);
        probe_sb_puts(sb, " MADT=");
        probe_sb_dec(sb, madt.gicd_version);
    }
    if (unreadable) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, "GICD NOT READABLE (FAULT)");
        tests_result_seal(r);
        return;
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "GICD CTLR=");
        probe_sb_hex(sb, ctlr, 8);
        probe_sb_puts(sb, " TYPER=");
        probe_sb_hex(sb, typer, 8);
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "GICD IIDR=");
        probe_sb_hex(sb, iidr, 8);
        probe_sb_puts(sb, " ARE_NS=");
        probe_sb_dec(sb, (ctlr >> 4) & 1u);
    }

    if (arch >= 3u) {
        for (unsigned i = 0; i < madt.n_gicr; i++)
            if (gicr_ok[i])
                walk_range(c->bus, &allow, madt.gicr[i], &w);
        for (unsigned i = 0; complete && i < madt.n_cpus; i++) {
            uint32_t want = mpidr_to_aff(madt.mpidr[i]);
            int found = 0;

            for (unsigned f = 0; f < w.frames; f++) {
                if (w.aff[f] != want)
                    continue;
                found++;
                if (madt.gicc_gicr[i] != 0 && madt.gicc_gicr[i] != w.addr[f])
                    addr_mismatch++;
            }
            if (found == 1)
                matched++;
            else
                mismatched++;
        }
        if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "GICR RANGES=");
            probe_sb_dec(sb, madt.n_gicr);
            probe_sb_puts(sb, " FRAMES=");
            probe_sb_dec(sb, w.frames);
            probe_sb_puts(sb, " LAST=");
            probe_sb_puts(sb, w.ranges_walked != 0 && w.ranges_with_last == w.ranges_walked ? "Y" : "N");
        }
        if (!complete) {
            tests_row_text(r, "CPU MATCH SKIPPED: RANGE NOT READ");
        } else if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "MADT CPUS=");
            probe_sb_dec(sb, madt.n_cpus);
            probe_sb_puts(sb, " MATCHED=");
            probe_sb_dec(sb, matched);
            probe_sb_puts(sb, " NO FRAME=");
            probe_sb_dec(sb, mismatched);
        }
        if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "GICR AWAKE=");
            probe_sb_dec(sb, w.awake);
            probe_sb_puts(sb, " ASLEEP=");
            probe_sb_dec(sb, w.asleep);
            if (complete) {
                probe_sb_puts(sb, " GICC BASE DIFF=");
                probe_sb_dec(sb, addr_mismatch);
            }
        }
    } else {
        tests_row_text(r, "GICV2 OR OLDER: NO REDISTRIBUTORS");
    }

    for (unsigned i = 0; i < madt.n_its && i < 2u; i++) {
        uint32_t its_ctlr = 0, its_typer = 0, its_pidr2 = 0;
        int ok;

        if (!its_ok[i])
            continue; /* already reported as refused */
        ok = tests_rd32(c->bus, &allow, madt.its[i] + GITS_CTLR, &its_ctlr) == RD_OK &&
             tests_rd32(c->bus, &allow, madt.its[i] + GITS_TYPER, &its_typer) == RD_OK &&
             tests_rd32(c->bus, &allow, madt.its[i] + GIC_PIDR2, &its_pidr2) == RD_OK;

        if ((sb = tests_row(r)) != NULL) {
            probe_sb_puts(sb, "ITS");
            probe_sb_dec(sb, i);
            if (ok) {
                probe_sb_puts(sb, " CTLR=");
                probe_sb_hex(sb, its_ctlr, 8);
                probe_sb_puts(sb, " ARCH=");
                probe_sb_dec(sb, (its_pidr2 >> 4) & 0xFu);
            } else {
                probe_sb_puts(sb, " NOT READABLE");
            }
        }
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "DESC D=");
        probe_sb_puts(sb, cmp(d->gicd_base, madt.gicd_base));
        probe_sb_puts(sb, " R=");
        probe_sb_puts(sb, cmp(d->gicr_base, madt.n_gicr != 0 ? madt.gicr[0].base : 0));
        probe_sb_puts(sb, " I=");
        probe_sb_puts(sb, cmp(d->its_base, madt.n_its != 0 ? madt.its[0] : 0));
        probe_sb_puts(sb, " C=");
        probe_sb_puts(sb, cmp(d->n_cpus, madt.n_cpus));
    }
    tests_row_dec(r, "FAULTS", c->bus->faults(c->bus->ctx) - faults_before);
    tests_result_seal(r);

    if (madt.gicd_version != 0 && madt.gicd_version != arch)
        r->verdict = TV_FAIL;
    else if (!complete)
        r->verdict = TV_UNKNOWN; /* a window the MADT listed was not read: no agreement can be claimed */
    else if (arch >= 3u && (mismatched != 0 || addr_mismatch != 0))
        r->verdict = TV_FAIL;
    else if (arch >= 3u && (w.stopped || w.frames == 0 || w.ranges_with_last != w.ranges_walked))
        r->verdict = TV_UNKNOWN;
    else
        r->verdict = TV_PASS;
}
