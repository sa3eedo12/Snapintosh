/* SPDX-License-Identifier: MIT */
#include "acpix.h"

#define HDR 36u

static int header_ok(const uint8_t *t, size_t len, const char sig[4], size_t min_len)
{
    return len >= min_len && len >= HDR && probe_memcmp(t, sig, 4) == 0 &&
           probe_le32(t + 4) == (uint32_t)len;
}

/* The range is a run of pages: a length that is not a whole number of them is as
 * wrong as a base that is not page aligned. */
static void keep_gicr(tests_madt *out, uint64_t base, uint64_t len)
{
    if (tests_window_check(base, len, TESTS_ALIGN_GICR) != TESTS_WIN_OK ||
        (len & (TESTS_ALIGN_GICR - 1u)) != 0)
        out->rejected++;
    else if (out->n_gicr < TESTS_MAX_GICR)
        out->gicr[out->n_gicr++] = (tests_range){base, len};
    else
        out->truncated++;
}

static void keep_its(tests_madt *out, uint64_t base)
{
    if (tests_window_check(base, TESTS_GIC_FRAME_BYTES, TESTS_ALIGN_ITS) != TESTS_WIN_OK)
        out->rejected++;
    else if (out->n_its < TESTS_MAX_ITS)
        out->its[out->n_its++] = base;
    else
        out->truncated++;
}

int tests_madt_parse(const uint8_t *t, size_t len, tests_madt *out)
{
    size_t off = HDR + 8u; /* the local interrupt controller address and the flags */
    unsigned steps = 0;

    probe_memset(out, 0, sizeof *out);
    if (!header_ok(t, len, "APIC", HDR + 8u))
        return -1;
    while (off + 2u <= len) {
        uint8_t type = t[off];
        size_t elen = t[off + 1];

        if (++steps > 4096u || elen < 2u || off + elen > len)
            return -1;
        switch (type) {
        case 0x0B: /* GICC */
            if (elen < 80u)
                return -1;
            out->n_gicc_seen++;
            if ((probe_le32(t + off + 12) & 1u) != 0) { /* enabled */
                if (out->n_cpus < TESTS_MAX_CPUS) {
                    out->mpidr[out->n_cpus] = probe_le64(t + off + 68);
                    out->gicc_gicr[out->n_cpus] = probe_le64(t + off + 60);
                    out->n_cpus++;
                } else {
                    out->truncated++;
                }
            }
            break;
        case 0x0C: /* GICD */
            if (elen < 24u)
                return -1;
            if (out->have_gicd)
                out->duplicate_gicd++;
            out->have_gicd = 1;
            out->gicd_base = probe_le64(t + off + 8);
            out->gicd_version = t[off + 20];
            out->gicd_status =
                tests_window_check(out->gicd_base, TESTS_GIC_FRAME_BYTES, TESTS_ALIGN_GICD);
            break;
        case 0x0E: /* GICR discovery range */
            if (elen < 16u)
                return -1;
            keep_gicr(out, probe_le64(t + off + 4), probe_le32(t + off + 12));
            break;
        case 0x0F: /* GIC ITS */
            if (elen < 20u)
                return -1;
            keep_its(out, probe_le64(t + off + 8));
            break;
        default:
            break;
        }
        off += elen;
    }
    if (off != len)
        return -1;
    out->valid = 1;
    return 0;
}

int tests_mcfg_parse(const uint8_t *t, size_t len, tests_mcfg *out)
{
    size_t off = HDR + 8u;

    probe_memset(out, 0, sizeof *out);
    if (!header_ok(t, len, "MCFG", HDR + 8u) || (len - off) % 16u != 0)
        return -1;
    for (; off + 16u <= len; off += 16u) {
        tests_ecam *e;

        if (out->n >= TESTS_MAX_ECAM) {
            out->truncated++;
            continue;
        }
        e = &out->e[out->n];
        e->base = probe_le64(t + off);
        e->segment = probe_le16(t + off + 8);
        e->start_bus = t[off + 10];
        e->end_bus = t[off + 11];
        if (e->end_bus < e->start_bus)
            return -1;
        /* An entry keeps its place so that the report can say which window was
         * refused, but a bad one is never readable: the window is whole buses of
         * 1 MiB, so a base that is not 1 MiB aligned cannot be an ECAM base. */
        e->status = (uint8_t)tests_window_check(
            e->base, ((uint64_t)e->end_bus - e->start_bus + 1u) << 20, TESTS_ALIGN_ECAM);
        out->n++;
    }
    out->valid = 1;
    return 0;
}

int tests_iort_parse(const uint8_t *t, size_t len, tests_iort *out)
{
    uint32_t count, first;
    size_t off;

    probe_memset(out, 0, sizeof *out);
    if (!header_ok(t, len, "IORT", HDR + 12u))
        return -1;
    count = probe_le32(t + HDR);
    first = probe_le32(t + HDR + 4);
    if (first < HDR + 12u || first > len || count > 4096u)
        return -1;
    off = first;
    for (uint32_t i = 0; i < count; i++) {
        size_t nlen;

        if (off + 16u > len)
            return -1;
        nlen = probe_le16(t + off + 1);
        if (nlen < 16u || off + nlen > len)
            return -1;
        if (t[off] == 0x04u) { /* SMMUv3: the base address follows the 16-byte node header */
            uint64_t base;

            if (nlen < 16u + 8u)
                return -1;
            base = probe_le64(t + off + 16);
            if (tests_window_check(base, TESTS_SMMU_WINDOW_BYTES, TESTS_ALIGN_SMMU) != TESTS_WIN_OK)
                out->rejected++;
            else if (out->n < TESTS_MAX_SMMU)
                out->base[out->n++] = base;
            else
                out->truncated++;
        }
        off += nlen;
    }
    out->valid = 1;
    return 0;
}

size_t tests_acpi_load(const probe_acpi_scan *scan, const probe_phys_ops *ops, void *ops_ctx,
                       const char sig[4], uint8_t *buf, size_t cap)
{
    for (unsigned i = 0; i < scan->count; i++) {
        const probe_acpi_entry *e = &scan->entries[i];

        if (e->status != PROBE_ACPI_OK || probe_memcmp(e->sig, sig, 4) != 0)
            continue;
        if (e->length > cap || ops->read(ops_ctx, e->addr, buf, e->length) != 0)
            return 0;
        return e->length;
    }
    return 0;
}
