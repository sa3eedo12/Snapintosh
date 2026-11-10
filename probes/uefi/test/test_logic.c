/* SPDX-License-Identifier: MIT */
/* Tests for the decision logic that keeps the SP11 EL2 probe passive: which
 * device path is a USB stick, whether a memory map and the ranges inside it
 * can be trusted, when ExitBootServices may be retried, and that every system
 * register is read with the instruction word it claims.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "efi_types.h"
#include "harness.h"
#include "logic.h"
#include "sysreg.h"

/* ---- Device path classification ---- */

struct dp_node {
    uint8_t type;
    uint8_t sub;
    uint16_t len;
};

/* The header carries the declared length. The bytes emitted are that many only
 * when it is a plausible node (4..64); a hostile declared length is written as
 * a bare header so the buffer really is shorter than the node claims. */
static size_t dp_build(uint8_t *out, const struct dp_node *n, size_t count)
{
    size_t off = 0;

    for (size_t i = 0; i < count; i++) {
        size_t emitted = (n[i].len >= 4 && n[i].len <= 64) ? n[i].len : 4;

        out[off] = n[i].type;
        out[off + 1] = n[i].sub;
        st16(out + off + 2, n[i].len);
        if (emitted > 4)
            memset(out + off + 4, 0x5A, emitted - 4);
        off += emitted;
    }
    return off;
}

static int dp_class(const struct dp_node *n, size_t count)
{
    uint8_t path[2048];
    size_t len = dp_build(path, n, count);
    uint8_t *exact = exact_copy(path, len);
    int cls = (int)probe_dp_classify(exact, len);

    free(exact);
    return cls;
}

#define DP_ACPI {2, 1, 12}
#define DP_PCI {1, 1, 6}
#define DP_USB {3, 5, 6}
#define DP_USBCLASS {3, 0x0F, 11}
#define DP_NVME {3, 0x17, 16}
#define DP_SATA {3, 0x12, 10}
#define DP_SD {3, 0x1A, 5}
#define DP_EMMC {3, 0x1D, 5}
#define DP_UFS {3, 0x19, 6}
#define DP_SCSI {3, 0x02, 8}
#define DP_ATAPI {3, 0x01, 8}
#define DP_UART {3, 0x0E, 19}
#define DP_HD {4, 1, 42}
#define DP_END {0x7F, 0xFF, 4}
#define DP_ARRAY(...) ((const struct dp_node[]){__VA_ARGS__})
#define DP_COUNT(...) (sizeof((const struct dp_node[]){__VA_ARGS__}) / sizeof(struct dp_node))
#define DP_CLASS(...) dp_class(DP_ARRAY(__VA_ARGS__), DP_COUNT(__VA_ARGS__))

void test_device_path(void)
{
    section("device path");
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_PCI, DP_PCI, DP_USB, DP_HD, DP_END), PROBE_DP_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_PCI, DP_USBCLASS, DP_END), PROBE_DP_USB);
    CHECK_EQ(DP_CLASS(DP_USB, DP_END), PROBE_DP_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_PCI, DP_NVME, DP_HD, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_PCI, DP_SATA, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_SD, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_EMMC, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_UFS, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_SCSI, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_ATAPI, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_PCI, DP_UART, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_END), PROBE_DP_NO_USB);
    CHECK_EQ(DP_CLASS(DP_END), PROBE_DP_NO_USB);

    CHECK_EQ(DP_CLASS(DP_ACPI, DP_USB, DP_SATA, DP_END), PROBE_DP_AMBIGUOUS);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_NVME, DP_USB, DP_END), PROBE_DP_AMBIGUOUS);
    CHECK_EQ(DP_CLASS(DP_USBCLASS, DP_SD, DP_END), PROBE_DP_AMBIGUOUS);
    CHECK_EQ(DP_CLASS(DP_USB, DP_EMMC, DP_END), PROBE_DP_AMBIGUOUS);

    CHECK_EQ(DP_CLASS(DP_ACPI, DP_USB, DP_END, DP_HD), PROBE_DP_USB);
    CHECK_EQ(DP_CLASS(DP_ACPI, {3, 5, 8}, DP_END), PROBE_DP_MALFORMED);
    CHECK_EQ(DP_CLASS(DP_ACPI, {3, 0x0F, 6}, DP_END), PROBE_DP_MALFORMED);
    CHECK_EQ(DP_CLASS(DP_ACPI, {3, 5, 3}, DP_END), PROBE_DP_MALFORMED);
    CHECK_EQ(DP_CLASS(DP_ACPI, {1, 1, 0}, DP_END), PROBE_DP_MALFORMED);
    CHECK_EQ(DP_CLASS(DP_ACPI, {0x83, 5, 6}, DP_END), PROBE_DP_MALFORMED);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_USB, {0x7F, 0x01, 4}, DP_USB, DP_END), PROBE_DP_MALFORMED);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_USB, {0x7F, 0xFF, 5}), PROBE_DP_MALFORMED);
    CHECK_EQ(DP_CLASS(DP_ACPI, DP_USB), PROBE_DP_MALFORMED);
    CHECK_EQ(DP_CLASS(DP_ACPI, {3, 5, 6}, {1, 1, 0xFFFF}, DP_END), PROBE_DP_MALFORMED);

    CHECK_EQ(probe_dp_classify(NULL, 4), PROBE_DP_MALFORMED);
    {
        uint8_t end_only[4] = {0x7F, 0xFF, 4, 0};
        uint8_t *exact = exact_copy(end_only, 4);
        CHECK_EQ(probe_dp_classify(exact, 4), PROBE_DP_NO_USB);
        CHECK_EQ(probe_dp_classify(exact, 3), PROBE_DP_MALFORMED);
        CHECK_EQ(probe_dp_classify(exact, 0), PROBE_DP_MALFORMED);
        free(exact);
    }

    /* A path cut short anywhere is never read past its buffer and never
     * accepted, even when what precedes the cut is a USB node. */
    {
        struct dp_node nodes[] = {DP_ACPI, DP_PCI, DP_USB, DP_HD, DP_END};
        uint8_t path[256];
        size_t len = dp_build(path, nodes, 5);
        for (size_t cut = 0; cut < len; cut++) {
            uint8_t *exact = exact_copy(path, cut);
            CHECK_EQ(probe_dp_classify(exact, cut), PROBE_DP_MALFORMED);
            free(exact);
        }
    }

    /* At most 64 nodes including the end node. */
    {
        struct dp_node nodes[70];
        for (int i = 0; i < 70; i++)
            nodes[i] = (struct dp_node)DP_PCI;
        nodes[62] = (struct dp_node)DP_USB;
        nodes[63] = (struct dp_node)DP_END;
        CHECK_EQ(dp_class(nodes, 64), PROBE_DP_USB);
        nodes[63] = (struct dp_node)DP_PCI;
        nodes[64] = (struct dp_node)DP_END;
        CHECK_EQ(dp_class(nodes, 65), PROBE_DP_MALFORMED);
        nodes[62] = (struct dp_node)DP_PCI;
        nodes[63] = (struct dp_node)DP_END;
        CHECK_EQ(dp_class(nodes, 64), PROBE_DP_NO_USB);
    }
    CHECK_STR(probe_dp_class_name(PROBE_DP_USB), "USB");
    CHECK_STR(probe_dp_class_name(PROBE_DP_NO_USB), "NOT USB");
    CHECK_STR(probe_dp_class_name(PROBE_DP_AMBIGUOUS), "AMBIGUOUS");
    CHECK_STR(probe_dp_class_name(PROBE_DP_MALFORMED), "MALFORMED");
}

/* ---- Memory map ---- */

void test_memmap(void)
{
    static const size_t strides[] = {40, 48, 56, 256};
    probe_memmap mm;

    section("memory map");
    for (size_t si = 0; si < sizeof strides / sizeof strides[0]; si++) {
        size_t stride = strides[si];
        size_t count = 6;
        uint8_t *map = xalloc(stride * count);
        put_desc(map, stride, 0, 7, 0x80000000ull, 0x100);
        put_desc(map, stride, 1, 4, 0x80100000ull, 4);
        put_desc(map, stride, 2, 9, 0x80104000ull, 2);
        put_desc(map, stride, 3, 11, 0xA0000000ull, 16);
        put_desc(map, stride, 4, 0, 0x90000000ull, 1);
        put_desc(map, stride, 5, 10, 0x80106000ull, 1);

        CHECK_EQ(probe_memmap_init(&mm, map, stride * count, stride * count, stride), PROBE_MM_OK);
        CHECK_EQ(mm.count, count);
        CHECK_EQ(mm.desc_size, stride);

        uint32_t type;
        uint64_t phys, virt, pages, attr;
        CHECK_EQ(probe_memmap_get(&mm, 5, &type, &phys, &virt, &pages, &attr), 0);
        CHECK_EQ(type, 10);
        CHECK_EQU(phys, 0x80106000ull);
        CHECK_EQU(pages, 1);
        CHECK_EQU(attr, 0xF);
        CHECK_EQ(probe_memmap_get(&mm, 6, &type, &phys, &virt, &pages, &attr), -1);

        const uint32_t rd = PROBE_READABLE_TYPES_MASK;
        CHECK(probe_memmap_covers(&mm, 0x80104000ull, 0x2000, rd));
        CHECK(probe_memmap_covers(&mm, 0x80104000ull, 0x3000, rd));
        CHECK(!probe_memmap_covers(&mm, 0x80104000ull, 0x3001, rd));
        CHECK(!probe_memmap_covers(&mm, 0x80104000ull, 0x4000, rd));
        CHECK(probe_memmap_covers(&mm, 0x80100000ull, 0x4000, rd));
        CHECK(probe_memmap_covers(&mm, 0x80100FFFull, 2, rd));
        CHECK(!probe_memmap_covers(&mm, 0x80000000ull, 0x1000, rd));
        CHECK(probe_memmap_covers(&mm, 0x80000000ull, 0x1000, 1u << 7));
        CHECK(!probe_memmap_covers(&mm, 0x90000000ull, 1, rd));
        CHECK(!probe_memmap_covers(&mm, 0xA0000000ull, 1, rd));
        CHECK(!probe_memmap_covers(&mm, 0x80100000ull, 0, rd));
        CHECK(!probe_memmap_covers(&mm, 0xFFFFFFFFFFFFF000ull, 0x2000, rd));
        CHECK(!probe_memmap_covers(&mm, 0x80100000ull, UINT64_MAX, rd));
        CHECK(!probe_memmap_covers(NULL, 0x80100000ull, 8, rd));

        CHECK_EQ(probe_memmap_type_at(&mm, 0x80100000ull), 4);
        CHECK_EQ(probe_memmap_type_at(&mm, 0x80103FFFull), 4);
        CHECK_EQ(probe_memmap_type_at(&mm, 0x80104000ull), 9);
        CHECK_EQ(probe_memmap_type_at(&mm, 0x7FFFFFFFull), -1);
        CHECK_EQ(probe_memmap_type_at(&mm, 0x80107000ull), -1);

        uint64_t lo, hi;
        CHECK_EQ(probe_memmap_ram_bounds(&mm, &lo, &hi), 1);
        CHECK_EQU(lo, 0x80000000ull);
        CHECK_EQU(hi, 0x80107000ull);
        free(map);
    }

    {
        uint8_t *map = xalloc(48 * 2);
        put_desc(map, 48, 0, 11, 0x1000, 1);
        put_desc(map, 48, 1, 0, 0x2000, 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 48), PROBE_MM_OK);
        uint64_t lo = 1, hi = 1;
        CHECK_EQ(probe_memmap_ram_bounds(&mm, &lo, &hi), 0);
        free(map);
    }

    {
        uint8_t *map = xalloc(48 * 2);
        put_desc(map, 48, 0, 7, 0x1000, 1);
        put_desc(map, 48, 1, 7, 0x2000, 1);
        CHECK_EQ(probe_memmap_init(&mm, NULL, 96, 96, 48), PROBE_MM_BAD_ARGS);
        CHECK_EQ(probe_memmap_init(&mm, map, 0, 96, 48), PROBE_MM_BAD_ARGS);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 39), PROBE_MM_BAD_DESC_SIZE);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 44), PROBE_MM_BAD_DESC_SIZE);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 41), PROBE_MM_BAD_DESC_SIZE);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 264), PROBE_MM_BAD_DESC_SIZE);
        CHECK_EQ(probe_memmap_init(&mm, map, 97, 97, 48), PROBE_MM_BAD_MAP_SIZE);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 95, 48), PROBE_MM_BAD_MAP_SIZE);
        CHECK(mm.base == NULL && mm.count == 0);

        put_desc(map, 48, 1, 7, 0x2000, 0);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 48), PROBE_MM_BAD_DESC);
        put_desc(map, 48, 1, 7, 0x2001, 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 48), PROBE_MM_BAD_DESC);
        put_desc(map, 48, 1, 7, 0xFFFFFFFFFFFFF000ull, 2);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 48), PROBE_MM_BAD_DESC);
        put_desc(map, 48, 1, 7, 0x2000, (UINT64_MAX >> 12) + 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 48), PROBE_MM_BAD_DESC);
        /* A region ending exactly at 2^64 is refused as well: its exclusive
         * end is not representable, and no platform maps the last page. */
        put_desc(map, 48, 1, 7, 0xFFFFFFFFFFFFF000ull, 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 48), PROBE_MM_BAD_DESC);
        put_desc(map, 48, 1, 7, 0xFFFFFFFFFFFFE000ull, 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 48), PROBE_MM_OK);
        free(map);
    }

    {
        size_t n = PROBE_MEMMAP_MAX_DESC + 1;
        uint8_t *map = xalloc(48 * n);
        for (size_t i = 0; i < n; i++)
            put_desc(map, 48, i, 7, 0x1000 * (i + 1), 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 48 * n, 48 * n, 48), PROBE_MM_TOO_MANY);
        CHECK_EQ(probe_memmap_init(&mm, map, 48 * (n - 1), 48 * n, 48), PROBE_MM_OK);
        free(map);
    }

    /* A range may span at most 64 descriptors; longer spans are refused
     * rather than walked, which is the fail-closed direction. */
    {
        size_t n = 70;
        uint8_t *map = xalloc(48 * n);
        for (size_t i = 0; i < n; i++)
            put_desc(map, 48, n - 1 - i, 4, 0x10000000ull + 0x1000 * i, 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 48 * n, 48 * n, 48), PROBE_MM_OK);
        CHECK(probe_memmap_covers(&mm, 0x10000000ull, 64 * 0x1000ull, PROBE_READABLE_TYPES_MASK));
        CHECK(!probe_memmap_covers(&mm, 0x10000000ull, 65 * 0x1000ull, PROBE_READABLE_TYPES_MASK));
        free(map);
    }

    /* Overlapping descriptors (the map the firmware promised not to give): a
     * range the readable type claims is refused as soon as any descriptor of
     * another type touches it, in either descriptor order, so an allowed
     * descriptor cannot launder an MMIO or reserved range into "readable". */
    for (int order = 0; order < 2; order++) {
        uint8_t *map = xalloc(48 * 3);
        const uint32_t rd = PROBE_READABLE_TYPES_MASK;

        put_desc(map, 48, order ? 1 : 0, 4, 0x80100000ull, 4);   /* BS data, readable */
        put_desc(map, 48, order ? 0 : 1, 11, 0x80101000ull, 1);  /* MMIO inside it */
        put_desc(map, 48, 2, 9, 0x80104000ull, 2);                /* ACPI, adjacent and clean */
        CHECK_EQ(probe_memmap_init(&mm, map, 48 * 3, 48 * 3, 48), PROBE_MM_OK);
        CHECK(!probe_memmap_covers(&mm, 0x80100000ull, 0x4000, rd));
        CHECK(!probe_memmap_covers(&mm, 0x80101000ull, 8, rd));
        CHECK(probe_memmap_covers(&mm, 0x80100000ull, 8, rd)); /* the clean first page is fine */
        CHECK(probe_memmap_covers(&mm, 0x80100000ull, 0x1000, rd));
        CHECK(!probe_memmap_covers(&mm, 0x80100FF8ull, 0x10, rd)); /* straddles into the MMIO page */
        CHECK(probe_memmap_covers(&mm, 0x80102000ull, 0x4000, rd));
        CHECK(probe_memmap_overlaps_other(&mm, 0x80100000ull, 0x4000, rd));
        CHECK(!probe_memmap_overlaps_other(&mm, 0x80100000ull, 0x1000, rd));
        CHECK(!probe_memmap_overlaps_other(&mm, 0x80102000ull, 0x2000, rd));
        CHECK_EQ(probe_memmap_type_at(&mm, 0x80101800ull), -1); /* two types claim it: no answer */
        CHECK_EQ(probe_memmap_type_at(&mm, 0x80100000ull), 4);
        free(map);
    }
    {
        /* The same bytes described twice with allowed types is not a conflict. */
        uint8_t *map = xalloc(48 * 2);

        put_desc(map, 48, 0, 4, 0x80100000ull, 4);
        put_desc(map, 48, 1, 9, 0x80101000ull, 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 96, 48), PROBE_MM_OK);
        CHECK(probe_memmap_covers(&mm, 0x80100000ull, 0x4000, PROBE_READABLE_TYPES_MASK));
        CHECK_EQ(probe_memmap_type_at(&mm, 0x80101000ull), -1);
        free(map);
    }
    {
        /* overlaps_other on empty, absent and wrapping input. */
        uint8_t *map = xalloc(48);

        put_desc(map, 48, 0, 7, 0x1000, 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 48, 48, 48), PROBE_MM_OK);
        CHECK(!probe_memmap_overlaps_other(NULL, 0x1000, 8, 0));
        CHECK(!probe_memmap_overlaps_other(&mm, 0x1000, 0, 0));
        CHECK(probe_memmap_overlaps_other(&mm, 0x1000, 8, 0));
        CHECK(!probe_memmap_overlaps_other(&mm, 0x1000, 8, 1u << 7));
        CHECK(probe_memmap_overlaps_other(&mm, 0xFFFFFFFFFFFFF000ull, 0x2000, 0) == 0);
        CHECK(probe_memmap_overlaps_other(&mm, 0, UINT64_MAX, 0));
        free(map);
    }

    CHECK_STR(probe_memtype_name(7), "CONV");
    CHECK_STR(probe_memtype_name(9), "ACPI RECLAIM");
    CHECK_STR(probe_memtype_name(0x7FFFFFFF), "UNKNOWN");
}

/* ---- Decision tables ---- */

void test_decisions(void)
{
    section("decision tables");
    CHECK_EQ(probe_currentel_decode(0x0), 0);
    CHECK_EQ(probe_currentel_decode(0x4), 1);
    CHECK_EQ(probe_currentel_decode(0x8), 2);
    CHECK_EQ(probe_currentel_decode(0xC), 3);
    CHECK_EQ(probe_currentel_decode(0x1), -1);
    CHECK_EQ(probe_currentel_decode(0x2), -1);
    CHECK_EQ(probe_currentel_decode(0x10), -1);
    CHECK_EQ(probe_currentel_decode(0x100), -1);
    CHECK_EQ(probe_currentel_decode(0x8ull | (1ull << 63)), -1);
    CHECK_EQ(probe_currentel_decode(~0ull), -1);

    CHECK_EQ(probe_psci_conduit_from_fadt(6, 276, 0), PROBE_PSCI_NONE);
    CHECK_EQ(probe_psci_conduit_from_fadt(6, 276, 1), PROBE_PSCI_SMC);
    CHECK_EQ(probe_psci_conduit_from_fadt(6, 276, 3), PROBE_PSCI_HVC);
    CHECK_EQ(probe_psci_conduit_from_fadt(6, 276, 2), PROBE_PSCI_NONE);
    CHECK_EQ(probe_psci_conduit_from_fadt(6, 276, 5), PROBE_PSCI_NONE);
    CHECK_EQ(probe_psci_conduit_from_fadt(6, 276, 0x8001), PROBE_PSCI_NONE);
    CHECK_EQ(probe_psci_conduit_from_fadt(4, 276, 1), PROBE_PSCI_NONE);
    CHECK_EQ(probe_psci_conduit_from_fadt(5, 276, 1), PROBE_PSCI_SMC);
    CHECK_EQ(probe_psci_conduit_from_fadt(6, 130, 1), PROBE_PSCI_NONE);
    CHECK_EQ(probe_psci_conduit_from_fadt(6, 131, 1), PROBE_PSCI_SMC);
    CHECK_STR(probe_psci_conduit_name(PROBE_PSCI_SMC), "SMC");
    CHECK_STR(probe_psci_conduit_name(PROBE_PSCI_HVC), "HVC");
    CHECK_STR(probe_psci_conduit_name(PROBE_PSCI_NONE), "NONE");

    for (int mask = 0; mask < 8; mask++)
        CHECK_EQ(probe_ebs_allowed(mask & 1, (mask >> 1) & 1, (mask >> 2) & 1), mask == 7);

    for (unsigned a = 0; a < 8; a++)
        CHECK_EQ(probe_ebs_retry(EFI_INVALID_PARAMETER, a, 8), a < 7);
    CHECK_EQ(probe_ebs_retry(EFI_INVALID_PARAMETER, 0, 0), 0);
    CHECK_EQ(probe_ebs_retry(EFI_INVALID_PARAMETER, 0, 1), 0);
    CHECK_EQ(probe_ebs_retry(EFI_INVALID_PARAMETER, 0, 2), 1);
    CHECK_EQ(probe_ebs_retry(EFI_INVALID_PARAMETER, 0xFFFFFFFFu, 8), 0);
    static const uint64_t others[] = {EFI_SUCCESS,       EFI_LOAD_ERROR,  EFI_UNSUPPORTED,
                                      EFI_BUFFER_TOO_SMALL, EFI_DEVICE_ERROR, EFI_OUT_OF_RESOURCES,
                                      EFI_NOT_FOUND,     EFI_ABORTED,     2ull};
    for (size_t i = 0; i < sizeof others / sizeof others[0]; i++)
        CHECK_EQ(probe_ebs_retry(others[i], 0, 8), 0);
}

/* ---- System registers ---- */

static const struct {
    const char *name;
    unsigned enc[PROBE_SYSREG_ENC_FIELDS];
} ref_sysregs[] = {
    {"CURRENTEL", {3, 0, 4, 2, 2}},        {"HCR_EL2", {3, 4, 1, 1, 0}},
    {"SCTLR_EL2", {3, 4, 1, 0, 0}},        {"TCR_EL2", {3, 4, 2, 0, 2}},
    {"TTBR0_EL2", {3, 4, 2, 0, 0}},        {"TTBR1_EL2", {3, 4, 2, 0, 1}},
    {"MAIR_EL2", {3, 4, 10, 2, 0}},        {"VBAR_EL2", {3, 4, 12, 0, 0}},
    {"CPTR_EL2", {3, 4, 1, 1, 2}},         {"MDCR_EL2", {3, 4, 1, 1, 1}},
    {"CNTHCTL_EL2", {3, 4, 14, 1, 0}},     {"CNTVOFF_EL2", {3, 4, 14, 0, 3}},
    {"ICC_SRE_EL2", {3, 4, 12, 9, 5}},     {"SPSR_EL2", {3, 4, 4, 0, 0}},
    {"ELR_EL2", {3, 4, 4, 0, 1}},          {"MPIDR_EL1", {3, 0, 0, 0, 5}},
    {"MIDR_EL1", {3, 0, 0, 0, 0}},         {"REVIDR_EL1", {3, 0, 0, 0, 6}},
    {"ID_AA64PFR0_EL1", {3, 0, 0, 4, 0}},  {"ID_AA64PFR1_EL1", {3, 0, 0, 4, 1}},
    {"ID_AA64ZFR0_EL1", {3, 0, 0, 4, 4}},  {"ID_AA64SMFR0_EL1", {3, 0, 0, 4, 5}},
    {"ID_AA64DFR0_EL1", {3, 0, 0, 5, 0}},
    {"ID_AA64ISAR0_EL1", {3, 0, 0, 6, 0}}, {"ID_AA64ISAR1_EL1", {3, 0, 0, 6, 1}},
    {"ID_AA64MMFR0_EL1", {3, 0, 0, 7, 0}},
    {"ID_AA64MMFR1_EL1", {3, 0, 0, 7, 1}}, {"ID_AA64MMFR2_EL1", {3, 0, 0, 7, 2}},
    {"DCZID_EL0", {3, 3, 0, 0, 7}},        {"CTR_EL0", {3, 3, 0, 0, 1}},
    {"CNTFRQ_EL0", {3, 3, 14, 0, 0}},      {"CNTPCT_EL0", {3, 3, 14, 0, 1}},
};

/* MRS x0 words as printed by an assembler listing, kept as literals so that
 * the encoder is checked against something it did not compute. */
static const struct {
    const char *name;
    uint32_t word;
} known_words[] = {
    {"MIDR_EL1", 0xD5380000u},        {"MPIDR_EL1", 0xD53800A0u},   {"CURRENTEL", 0xD5384240u},
    {"HCR_EL2", 0xD53C1100u},         {"SCTLR_EL2", 0xD53C1000u},   {"CNTFRQ_EL0", 0xD53BE000u},
    {"CNTPCT_EL0", 0xD53BE020u},      {"CTR_EL0", 0xD53B0020u},     {"DCZID_EL0", 0xD53B00E0u},
    {"ID_AA64MMFR0_EL1", 0xD5380700u}, {"ID_AA64PFR0_EL1", 0xD5380400u}, {"VBAR_EL2", 0xD53CC000u},
    {"TTBR0_EL2", 0xD53C2000u},       {"TCR_EL2", 0xD53C2040u},     {"MAIR_EL2", 0xD53CA200u},
    {"SPSR_EL2", 0xD53C4000u},        {"ELR_EL2", 0xD53C4020u},
};

static int sysreg_by_name(const char *name)
{
    for (int i = 0; i < PSR_COUNT; i++)
        if (strcmp(probe_sysreg_name(i), name) == 0)
            return i;
    return -1;
}

void test_sysreg(void)
{
    section("system registers");
    CHECK_EQ((size_t)PSR_COUNT, sizeof ref_sysregs / sizeof ref_sysregs[0]);
    for (size_t i = 0; i < sizeof ref_sysregs / sizeof ref_sysregs[0]; i++) {
        int id = sysreg_by_name(ref_sysregs[i].name);
        unsigned enc[PROBE_SYSREG_ENC_FIELDS];
        CHECK(id >= 0);
        if (id < 0)
            continue;
        CHECK_EQ(probe_sysreg_encoding(id, enc), 0);
        for (int f = 0; f < PROBE_SYSREG_ENC_FIELDS; f++)
            CHECK_EQ(enc[f], ref_sysregs[i].enc[f]);
        const unsigned *e = ref_sysregs[i].enc;
        uint32_t word = 0xD5300000u | ((e[0] & 1u) << 19) | (e[1] << 16) | (e[2] << 12) |
                        (e[3] << 8) | (e[4] << 5);
        CHECK_EQU(probe_sysreg_mrs_word(id), word);
    }
    for (size_t i = 0; i < sizeof known_words / sizeof known_words[0]; i++) {
        int id = sysreg_by_name(known_words[i].name);
        CHECK(id >= 0);
        if (id >= 0)
            CHECK_EQU(probe_sysreg_mrs_word(id), known_words[i].word);
    }
    for (int a = 0; a < PSR_COUNT; a++) {
        for (int b = a + 1; b < PSR_COUNT; b++) {
            CHECK(strcmp(probe_sysreg_name(a), probe_sysreg_name(b)) != 0);
            CHECK(probe_sysreg_mrs_word(a) != probe_sysreg_mrs_word(b));
        }
    }
    unsigned enc[PROBE_SYSREG_ENC_FIELDS];
    CHECK_EQ(probe_sysreg_encoding(-1, enc), -1);
    CHECK_EQ(probe_sysreg_encoding(PSR_COUNT, enc), -1);
    CHECK_EQU(probe_sysreg_mrs_word(PSR_COUNT), 0);
    CHECK_STR(probe_sysreg_name(PSR_COUNT), "?");
}
