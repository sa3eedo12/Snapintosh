/* SPDX-License-Identifier: MIT */
/* Tests for the firmware-table parsers of the SP11 EL2 probe, run over
 * synthetic physical memory. Every table is hand-built in this file; no
 * vendor firmware or captured table is used. Each segment is an exact-size
 * heap block, so a parser that reads past a table it was told is shorter is
 * reported by AddressSanitizer, and the fuzz loop mutates headers, lengths,
 * checksums and root-table pointers to check the scan invariants hold.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "acpi.h"
#include "efi_types.h"
#include "harness.h"
#include "logic.h"

/* ---- Synthetic physical memory for the firmware table parsers ---- */

typedef struct {
    uint64_t base;
    uint8_t *data;
    size_t len;
} seg_t;

typedef struct {
    seg_t seg[40];
    unsigned n;
} world_t;

static seg_t *w_add(world_t *w, uint64_t base, size_t len)
{
    if (w->n >= sizeof w->seg / sizeof w->seg[0]) {
        fputs("test world full\n", stderr);
        exit(2);
    }
    seg_t *s = &w->seg[w->n++];
    s->base = base;
    s->data = xalloc(len);
    s->len = len;
    return s;
}

static void w_free(world_t *w)
{
    for (unsigned i = 0; i < w->n; i++)
        free(w->seg[i].data);
    w->n = 0;
}

static int w_lookup(const world_t *w, uint64_t addr, uint64_t len)
{
    for (unsigned i = 0; i < w->n; i++) {
        const seg_t *s = &w->seg[i];
        if (addr >= s->base && addr - s->base <= s->len && len <= s->len - (addr - s->base))
            return (int)i;
    }
    return -1;
}

static int w_read(void *ctx, uint64_t addr, void *dst, size_t len)
{
    const world_t *w = ctx;
    int i = w_lookup(w, addr, len);

    if (i < 0)
        return -1;
    memcpy(dst, w->seg[i].data + (addr - w->seg[i].base), len);
    return 0;
}

static int w_covers(void *ctx, uint64_t addr, uint64_t len)
{
    return w_lookup(ctx, addr, len) >= 0;
}

static const probe_phys_ops world_ops = {w_read, w_covers};

static seg_t *w_find(world_t *w, uint64_t base)
{
    for (unsigned i = 0; i < w->n; i++)
        if (w->seg[i].base == base)
            return &w->seg[i];
    return NULL;
}

static void make_sum_zero(uint8_t *t, size_t len, size_t at)
{
    uint8_t s = 0;

    t[at] = 0;
    for (size_t i = 0; i < len; i++)
        s = (uint8_t)(s + t[i]);
    t[at] = (uint8_t)(0 - s);
}

static void set_header(uint8_t *t, const char *sig, uint32_t len, uint8_t rev)
{
    memcpy(t, sig, 4);
    st32(t + 4, len);
    t[8] = rev;
    memcpy(t + 10, "TESTOE", 6);
}

static seg_t *w_table(world_t *w, uint64_t base, const char *sig, size_t len, uint8_t rev)
{
    seg_t *s = w_add(w, base, len);

    set_header(s->data, sig, (uint32_t)len, rev);
    make_sum_zero(s->data, len, 9);
    return s;
}

#define A_RSDP 0x80001000ull
#define A_XSDT 0x80002000ull
#define A_RSDT 0x80003000ull
#define A_FADT 0x80004000ull
#define A_DSDT 0x80005000ull
#define A_FACS 0x80006000ull
#define A_MADT 0x80007000ull
#define A_SPCR 0x80008000ull
#define A_SSDT1 0x80009000ull
#define A_SSDT2 0x120000000ull
#define A_UNMAPPED 0x70000000ull

#define FADT_LEN 276u
#define MADT_GICC_LEN 80u

static void build_root_table(world_t *w, uint64_t base, const char *sig, int wide,
                             const uint64_t *addrs, unsigned n)
{
    size_t len = 36 + (size_t)n * (wide ? 8 : 4);
    seg_t *s = w_table(w, base, sig, len, 1);

    for (unsigned i = 0; i < n; i++) {
        if (wide)
            st64(s->data + 36 + i * 8, addrs[i]);
        else
            st32(s->data + 36 + i * 4, (uint32_t)addrs[i]);
    }
    make_sum_zero(s->data, len, 9);
}

static void build_rsdp(world_t *w, uint8_t rev, uint64_t rsdt, uint64_t xsdt, uint32_t claimed_len,
                       size_t alloc)
{
    seg_t *s = w_add(w, A_RSDP, alloc);

    memcpy(s->data, "RSD PTR ", 8);
    memcpy(s->data + 9, "TESTOE", 6);
    s->data[15] = rev;
    st32(s->data + 16, (uint32_t)rsdt);
    if (rev >= 2 && alloc >= 36) {
        st32(s->data + 20, claimed_len);
        st64(s->data + 24, xsdt);
    }
    make_sum_zero(s->data, 20, 8);
    if (rev >= 2 && alloc >= claimed_len && claimed_len >= 36)
        make_sum_zero(s->data, claimed_len, 32);
}

static void w_standard(world_t *w)
{
    uint64_t xsdt_list[] = {A_FADT, A_MADT, A_SPCR, A_SSDT1, A_SSDT2};
    uint64_t rsdt_list[] = {A_FADT, A_MADT, A_SPCR, A_SSDT1};

    memset(w, 0, sizeof *w);
    w_table(w, A_DSDT, "DSDT", 64, 2);

    seg_t *facs = w_add(w, A_FACS, 64);
    memcpy(facs->data, "FACS", 4);
    st32(facs->data + 4, 64);

    seg_t *fadt = w_table(w, A_FADT, "FACP", FADT_LEN, 6);
    st32(fadt->data + 36, (uint32_t)A_FACS);
    st32(fadt->data + 40, (uint32_t)A_DSDT);
    st32(fadt->data + 112, 0x00000110u);
    st16(fadt->data + 129, 0x0001);
    fadt->data[131] = 1;
    st64(fadt->data + 132, A_FACS);
    st64(fadt->data + 140, A_DSDT);
    make_sum_zero(fadt->data, FADT_LEN, 9);

    size_t madt_len = 44 + 24 + 3 * MADT_GICC_LEN;
    seg_t *madt = w_table(w, A_MADT, "APIC", madt_len, 6);
    size_t off = 44;
    madt->data[off] = 0x0C;
    madt->data[off + 1] = 24;
    off += 24;
    for (unsigned i = 0; i < 3; i++) {
        madt->data[off] = 0x0B;
        madt->data[off + 1] = MADT_GICC_LEN;
        st32(madt->data + off + 12, i < 2 ? 1u : 0u);
        st64(madt->data + off + 68, (uint64_t)i << 8);
        off += MADT_GICC_LEN;
    }
    make_sum_zero(madt->data, madt_len, 9);

    w_table(w, A_SPCR, "SPCR", 80, 2);
    w_table(w, A_SSDT1, "SSDT", 60, 2);
    w_table(w, A_SSDT2, "SSDT", 70, 2);
    build_root_table(w, A_XSDT, "XSDT", 1, xsdt_list, 5);
    build_root_table(w, A_RSDT, "RSDT", 0, rsdt_list, 4);
    build_rsdp(w, 2, A_RSDT, A_XSDT, 36, 36);
}

static void w_resum(world_t *w, uint64_t base)
{
    seg_t *s = w_find(w, base);

    make_sum_zero(s->data, s->len, 9);
}

static const probe_acpi_entry *entry_named(const probe_acpi_scan *s, const char *name)
{
    for (unsigned i = 0; i < s->count; i++)
        if (s->entries[i].status == PROBE_ACPI_OK && strcmp(s->entries[i].name, name) == 0)
            return &s->entries[i];
    return NULL;
}

static const probe_acpi_entry *entry_at(const probe_acpi_scan *s, uint64_t addr)
{
    for (unsigned i = 0; i < s->count; i++)
        if (s->entries[i].addr == addr)
            return &s->entries[i];
    return NULL;
}

static int scan(world_t *w, probe_acpi_scan *s, uint64_t rsdp)
{
    return probe_acpi_scan_run(s, &world_ops, w, rsdp);
}

static void test_acpi_good(void)
{
    world_t w;
    probe_acpi_scan *s = xalloc(sizeof *s);

    w_standard(&w);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(s->rsdp_status, PROBE_ACPI_OK);
    CHECK_EQ(s->rsdp_revision, 2);
    CHECK_EQ(s->rsdp_length, 36);
    CHECK_EQ(s->root_is_xsdt, 1);
    CHECK_EQ(s->root_status, PROBE_ACPI_OK);
    CHECK_EQ(s->count, 8);
    CHECK_EQ(s->n_ok, 8);
    CHECK_EQ(s->n_rejected, 0);
    CHECK_EQ(s->n_dup, 0);
    static const struct {
        const char *name;
        uint64_t addr;
        int origin;
    } want[] = {
        {"XSDT.bin", A_XSDT, PROBE_ORIGIN_ROOT},         {"FACP.bin", A_FADT, PROBE_ORIGIN_ROOT_ENTRY},
        {"APIC.bin", A_MADT, PROBE_ORIGIN_ROOT_ENTRY},   {"SPCR.bin", A_SPCR, PROBE_ORIGIN_ROOT_ENTRY},
        {"SSDT.bin", A_SSDT1, PROBE_ORIGIN_ROOT_ENTRY},  {"SSDT_1.bin", A_SSDT2, PROBE_ORIGIN_ROOT_ENTRY},
        {"DSDT.bin", A_DSDT, PROBE_ORIGIN_FADT_DSDT},    {"FACS.bin", A_FACS, PROBE_ORIGIN_FADT_FACS},
    };
    for (size_t i = 0; i < sizeof want / sizeof want[0]; i++) {
        const probe_acpi_entry *e = entry_named(s, want[i].name);
        CHECK(e != NULL);
        if (e == NULL)
            continue;
        CHECK_EQU(e->addr, want[i].addr);
        CHECK_EQ(e->origin, want[i].origin);
    }
    CHECK_EQ(entry_named(s, "XSDT.bin")->length, 76);
    CHECK_EQ(entry_named(s, "FACP.bin")->length, FADT_LEN);
    CHECK_EQ(entry_named(s, "FACP.bin")->revision, 6);
    CHECK_EQ(entry_named(s, "FACS.bin")->length, 64);
    CHECK(s->fadt.valid);
    CHECK_EQ(s->fadt.revision, 6);
    CHECK_EQ(s->fadt.minor, 1);
    CHECK_EQ(s->fadt.arm_boot_arch, 1);
    CHECK(s->fadt.has_arm_boot_arch);
    CHECK_EQU(s->fadt.dsdt, A_DSDT);
    CHECK_EQU(s->fadt.facs, A_FACS);
    CHECK_EQ(probe_psci_conduit_from_fadt(s->fadt.revision, s->fadt.length, s->fadt.arm_boot_arch),
             PROBE_PSCI_SMC);
    w_free(&w);
    free(s);
}

static void replace_rsdp(world_t *w, uint8_t rev, uint64_t rsdt, uint64_t xsdt, uint32_t claimed,
                         size_t alloc)
{
    seg_t *s = w_find(w, A_RSDP);

    free(s->data);
    w->n--;
    if (s != &w->seg[w->n])
        *s = w->seg[w->n];
    build_rsdp(w, rev, rsdt, xsdt, claimed, alloc);
}

static void replace_root(world_t *w, uint64_t base, const char *sig, int wide,
                         const uint64_t *addrs, unsigned n)
{
    seg_t *s = w_find(w, base);

    free(s->data);
    w->n--;
    if (s != &w->seg[w->n])
        *s = w->seg[w->n];
    build_root_table(w, base, sig, wide, addrs, n);
}

static void test_acpi_root_and_rsdp(void)
{
    world_t w;
    probe_acpi_scan *s = xalloc(sizeof *s);

    /* RSDP revision 0 has no XSDT: the RSDT is the root and its 32-bit list
     * cannot reach the table above 4 GiB. */
    w_standard(&w);
    replace_rsdp(&w, 0, A_RSDT, A_XSDT, 36, 20);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(s->root_is_xsdt, 0);
    CHECK_EQ(s->rsdp_length, 20);
    CHECK(entry_named(s, "RSDT.bin") != NULL);
    CHECK(entry_named(s, "SSDT_1.bin") == NULL);
    CHECK_EQ(s->n_ok, 7);
    w_free(&w);

    /* Revision 2 with an empty XSDT pointer falls back to the RSDT. */
    w_standard(&w);
    replace_rsdp(&w, 2, A_RSDT, 0, 36, 36);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(s->root_is_xsdt, 0);
    CHECK_EQ(s->n_ok, 7);
    w_free(&w);

    /* A broken XSDT is not swapped for the RSDT: disagreement is a finding. */
    w_standard(&w);
    w_find(&w, A_XSDT)->data[40] ^= 1;
    CHECK(scan(&w, s, A_RSDP) != 0);
    CHECK_EQ(s->root_status, PROBE_ACPI_BAD_CHECKSUM);
    CHECK_EQ(s->count, 1);
    CHECK_EQ(s->n_ok, 0);
    CHECK_EQ(s->n_rejected, 1);
    w_free(&w);

    w_standard(&w);
    memcpy(w_find(&w, A_XSDT)->data, "RSDT", 4);
    w_resum(&w, A_XSDT);
    CHECK(scan(&w, s, A_RSDP) != 0);
    CHECK_EQ(s->root_status, PROBE_ACPI_BAD_SIGNATURE);
    w_free(&w);

    w_standard(&w);
    replace_rsdp(&w, 2, A_RSDT, A_UNMAPPED, 36, 36);
    CHECK(scan(&w, s, A_RSDP) != 0);
    CHECK_EQ(s->root_status, PROBE_ACPI_UNREADABLE);
    w_free(&w);

    /* 128 entries is the limit, 129 is refused, and a length that is not a
     * whole number of entries is refused. */
    {
        uint64_t list[129];
        for (unsigned i = 0; i < 129; i++)
            list[i] = A_FADT;
        w_standard(&w);
        replace_root(&w, A_XSDT, "XSDT", 1, list, 128);
        CHECK_EQ(scan(&w, s, A_RSDP), 0);
        CHECK_EQ(s->n_dup, 127 + 0);
        CHECK(s->count <= PROBE_ACPI_MAX_ENTRIES);
        w_free(&w);

        w_standard(&w);
        replace_root(&w, A_XSDT, "XSDT", 1, list, 129);
        CHECK(scan(&w, s, A_RSDP) != 0);
        CHECK_EQ(s->root_status, PROBE_ACPI_BAD_COUNT);
        CHECK_EQ(s->count, 1);
        w_free(&w);

        w_standard(&w);
        replace_root(&w, A_XSDT, "XSDT", 0, list, 3);
        CHECK(scan(&w, s, A_RSDP) != 0);
        CHECK_EQ(s->root_status, PROBE_ACPI_BAD_COUNT);
        w_free(&w);
    }

    /* RSDP field checks. */
    w_standard(&w);
    CHECK(scan(&w, s, 0) != 0);
    CHECK_EQ(s->rsdp_status, PROBE_ACPI_NULL_PTR);
    CHECK(scan(&w, s, A_UNMAPPED) != 0);
    CHECK_EQ(s->rsdp_status, PROBE_ACPI_UNREADABLE);
    CHECK(scan(&w, s, A_FADT) != 0);
    CHECK_EQ(s->rsdp_status, PROBE_ACPI_BAD_SIGNATURE);
    w_find(&w, A_RSDP)->data[8] ^= 0x40;
    CHECK(scan(&w, s, A_RSDP) != 0);
    CHECK_EQ(s->rsdp_status, PROBE_ACPI_BAD_CHECKSUM);
    w_free(&w);

    w_standard(&w);
    w_find(&w, A_RSDP)->data[32] ^= 0x01;
    CHECK(scan(&w, s, A_RSDP) != 0);
    CHECK_EQ(s->rsdp_status, PROBE_ACPI_BAD_CHECKSUM);
    w_free(&w);

    for (unsigned claimed = 0; claimed < 300; claimed += (claimed == 35 ? 1 : 11)) {
        if (claimed == 36 || claimed == 256 || claimed < 30 || claimed > 270)
            continue;
        w_standard(&w);
        replace_rsdp(&w, 2, A_RSDT, A_XSDT, claimed, claimed > 36 ? claimed : 36);
        int rc = scan(&w, s, A_RSDP);
        if (claimed < 36 || claimed > PROBE_RSDP_RAW_MAX) {
            CHECK(rc != 0);
            CHECK_EQ(s->rsdp_status, PROBE_ACPI_BAD_LENGTH);
        }
        w_free(&w);
    }
    w_standard(&w);
    replace_rsdp(&w, 2, A_RSDT, A_XSDT, 256, 256);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(s->rsdp_length, 256);
    w_free(&w);
    w_standard(&w);
    replace_rsdp(&w, 2, A_RSDT, A_XSDT, 257, 257);
    CHECK(scan(&w, s, A_RSDP) != 0);
    CHECK_EQ(s->rsdp_status, PROBE_ACPI_BAD_LENGTH);
    w_free(&w);
    w_standard(&w);
    replace_rsdp(&w, 2, A_RSDT, A_XSDT, 36, 20);
    CHECK(scan(&w, s, A_RSDP) != 0);
    CHECK_EQ(s->rsdp_status, PROBE_ACPI_UNREADABLE);
    w_free(&w);
    free(s);
}

static void set_xsdt_entry(world_t *w, unsigned index, uint64_t addr)
{
    seg_t *x = w_find(w, A_XSDT);

    st64(x->data + 36 + index * 8, addr);
    w_resum(w, A_XSDT);
}

static void test_acpi_tables(void)
{
    world_t w;
    probe_acpi_scan *s = xalloc(sizeof *s);

    /* Bad checksum: listed with its status, never given a file name. */
    w_standard(&w);
    w_find(&w, A_SPCR)->data[40] ^= 1;
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    {
        const probe_acpi_entry *e = entry_at(s, A_SPCR);
        CHECK(e != NULL);
        CHECK_EQ(e->status, PROBE_ACPI_BAD_CHECKSUM);
        CHECK_STR(e->sig, "SPCR");
        CHECK_EQ(e->name[0], 0);
    }
    CHECK_EQ(s->n_ok, 7);
    CHECK_EQ(s->n_rejected, 1);
    CHECK(entry_named(s, "SPCR.bin") == NULL);
    w_free(&w);

    /* Hostile lengths in the header. */
    static const uint32_t hostile[] = {0, 1, 35, 0xFFFFFFFFu, 0x80000000u, 16u * 1024 * 1024 + 1};
    for (size_t i = 0; i < sizeof hostile / sizeof hostile[0]; i++) {
        w_standard(&w);
        st32(w_find(&w, A_SPCR)->data + 4, hostile[i]);
        CHECK_EQ(scan(&w, s, A_RSDP), 0);
        CHECK_EQ(entry_at(s, A_SPCR)->status, PROBE_ACPI_BAD_LENGTH);
        CHECK_EQ(s->n_ok, 7);
        w_free(&w);
    }

    /* A length that fits the limits but runs off the readable memory. */
    w_standard(&w);
    st32(w_find(&w, A_SPCR)->data + 4, 0x1000);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, A_SPCR)->status, PROBE_ACPI_UNREADABLE);
    w_free(&w);

    static const char *const bad_sigs[] = {"sPCR", "SP\x01R", "SPC ", "S\xC3PC"};
    for (size_t i = 0; i < sizeof bad_sigs / sizeof bad_sigs[0]; i++) {
        w_standard(&w);
        memcpy(w_find(&w, A_SPCR)->data, bad_sigs[i], 4);
        CHECK_EQ(scan(&w, s, A_RSDP), 0);
        CHECK_EQ(entry_at(s, A_SPCR)->status, PROBE_ACPI_BAD_SIGNATURE);
        CHECK_STR(entry_at(s, A_SPCR)->sig, "????");
        w_free(&w);
    }

    w_standard(&w);
    set_xsdt_entry(&w, 4, 0);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, 0)->status, PROBE_ACPI_NULL_PTR);
    w_free(&w);

    w_standard(&w);
    set_xsdt_entry(&w, 4, A_UNMAPPED);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, A_UNMAPPED)->status, PROBE_ACPI_UNREADABLE);
    w_free(&w);

    /* Listing a table twice is reported as a duplicate, not dumped twice. */
    w_standard(&w);
    {
        uint64_t list[] = {A_FADT, A_MADT, A_SPCR, A_SSDT1, A_SSDT2, A_FADT, A_MADT, A_DSDT};
        replace_root(&w, A_XSDT, "XSDT", 1, list, 8);
    }
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(s->n_dup, 3);
    CHECK_EQ(s->n_ok, 8);
    CHECK_EQ(s->count, 8);
    CHECK_EQ(entry_named(s, "DSDT.bin")->origin, PROBE_ORIGIN_ROOT_ENTRY);
    w_free(&w);

    /* Two valid tables that overlap without being identical are refused. */
    w_standard(&w);
    {
        seg_t *a = w_add(&w, 0x80010000ull, 100);
        set_header(a->data, "OVLA", 100, 1);
        set_header(a->data + 40, "OVLB", 36, 1);
        make_sum_zero(a->data + 40, 36, 9);
        make_sum_zero(a->data, 100, 9);
        uint64_t list[] = {A_FADT, 0x80010000ull, 0x80010028ull};
        replace_root(&w, A_XSDT, "XSDT", 1, list, 3);
    }
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, 0x80010000ull)->status, PROBE_ACPI_OK);
    CHECK_EQ(entry_at(s, 0x80010028ull)->status, PROBE_ACPI_OVERLAP);
    CHECK(entry_named(s, "OVLB.bin") == NULL);
    w_free(&w);

    /* Pointers taken from the FADT. */
    w_standard(&w);
    st32(w_find(&w, A_FADT)->data + 40, 0);
    st64(w_find(&w, A_FADT)->data + 140, 0);
    w_resum(&w, A_FADT);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, 0)->status, PROBE_ACPI_NULL_PTR);
    CHECK_EQ(entry_at(s, 0)->origin, PROBE_ORIGIN_FADT_DSDT);
    CHECK_EQ(s->n_rejected, 1);
    CHECK(entry_named(s, "DSDT.bin") == NULL);
    w_free(&w);

    w_standard(&w);
    st64(w_find(&w, A_FADT)->data + 140, A_UNMAPPED);
    w_resum(&w, A_FADT);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, A_UNMAPPED)->status, PROBE_ACPI_UNREADABLE);
    w_free(&w);

    w_standard(&w);
    memcpy(w_find(&w, A_DSDT)->data, "SSDT", 4);
    w_resum(&w, A_DSDT);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, A_DSDT)->status, PROBE_ACPI_BAD_SIGNATURE);
    w_free(&w);

    w_standard(&w);
    st32(w_find(&w, A_FADT)->data + 36, 0);
    st64(w_find(&w, A_FADT)->data + 132, 0);
    w_resum(&w, A_FADT);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, 0)->status, PROBE_ACPI_ABSENT);
    CHECK_EQ(s->n_rejected, 0);
    CHECK_EQ(s->n_ok, 7);
    w_free(&w);

    /* FACS has no checksum, so its length is the only guard. */
    static const struct {
        uint32_t len;
        size_t alloc;
        int status;
    } facs_cases[] = {
        {64, 64, PROBE_ACPI_OK},        {24, 24, PROBE_ACPI_OK},
        {23, 64, PROBE_ACPI_BAD_LENGTH}, {4096, 4096, PROBE_ACPI_OK},
        {4097, 4097, PROBE_ACPI_BAD_LENGTH}, {0xFFFFFFFFu, 64, PROBE_ACPI_BAD_LENGTH},
        {128, 64, PROBE_ACPI_UNREADABLE},
    };
    for (size_t i = 0; i < sizeof facs_cases / sizeof facs_cases[0]; i++) {
        w_standard(&w);
        seg_t *old = w_find(&w, A_FACS);
        free(old->data);
        old->data = xalloc(facs_cases[i].alloc);
        old->len = facs_cases[i].alloc;
        memcpy(old->data, "FACS", 4);
        st32(old->data + 4, facs_cases[i].len);
        CHECK_EQ(scan(&w, s, A_RSDP), 0);
        CHECK_EQ(entry_at(s, A_FACS)->status, facs_cases[i].status);
        w_free(&w);
    }
    w_standard(&w);
    memcpy(w_find(&w, A_FACS)->data, "FACT", 4);
    CHECK_EQ(scan(&w, s, A_RSDP), 0);
    CHECK_EQ(entry_at(s, A_FACS)->status, PROBE_ACPI_BAD_SIGNATURE);
    w_free(&w);
    free(s);
}

static void check_scan_invariants(world_t *w, const probe_acpi_scan *s)
{
    unsigned ok = 0;
    unsigned rejected = 0;

    CHECK(s->count <= PROBE_ACPI_MAX_ENTRIES);
    for (unsigned i = 0; i < s->count; i++) {
        const probe_acpi_entry *e = &s->entries[i];
        if (e->status == PROBE_ACPI_OK) {
            ok++;
            int seg = w_lookup(w, e->addr, e->length);
            CHECK(seg >= 0);
            CHECK(e->length >= 24 && e->length <= PROBE_ACPI_MAX_TABLE_LEN);
            CHECK(strlen(e->name) >= 8 && strlen(e->name) <= 12);
            CHECK(memcmp(e->name, e->sig, 4) == 0);
            CHECK(strcmp(e->name + strlen(e->name) - 4, ".bin") == 0);
            if (seg >= 0 && memcmp(e->sig, "FACS", 4) != 0) {
                const uint8_t *t = w->seg[seg].data + (e->addr - w->seg[seg].base);
                CHECK_EQ(probe_sum8(t, e->length), 0);
                CHECK(memcmp(t, e->sig, 4) == 0);
            }
            for (unsigned j = 0; j < i; j++) {
                const probe_acpi_entry *o = &s->entries[j];
                CHECK(strcmp(o->name, e->name) != 0 || o->status != PROBE_ACPI_OK);
                if (o->status == PROBE_ACPI_OK)
                    CHECK(e->addr >= o->addr + o->length || o->addr >= e->addr + e->length);
            }
        } else {
            CHECK_EQ(e->name[0], 0);
            if (e->status != PROBE_ACPI_ABSENT)
                rejected++;
        }
    }
    CHECK_EQ(s->n_ok, ok);
    CHECK_EQ(s->n_rejected, rejected);
}

static void test_acpi_fuzz(void)
{
    probe_acpi_scan *s = xalloc(sizeof *s);
    world_t w;

    for (int iter = 0; iter < 20000; iter++) {
        w_standard(&w);
        unsigned edits = 1 + rnd_below(4);
        for (unsigned e = 0; e < edits; e++) {
            seg_t *seg = &w.seg[rnd_below(w.n)];
            switch (rnd_below(5)) {
            case 0:
                seg->data[rnd_below((uint32_t)seg->len)] ^= (uint8_t)(1u << rnd_below(8));
                break;
            case 1:
                seg->data[rnd_below((uint32_t)seg->len)] = (uint8_t)rnd();
                break;
            case 2:
                if (seg->len >= 8)
                    st32(seg->data + 4, (uint32_t)rnd());
                break;
            case 3:
                if (seg->len >= 8)
                    st32(seg->data + 4, rnd_below(300));
                break;
            default: {
                seg_t *x = w_find(&w, A_XSDT);
                static const uint64_t pool[] = {0, A_FADT, A_FADT + 4, A_FADT - 4, A_RSDP, A_DSDT,
                                                A_MADT + 8, 0xFFFFFFFFFFFFFFFFull, A_UNMAPPED};
                st64(x->data + 36 + 8 * rnd_below(5), pool[rnd_below(9)]);
                break;
            }
            }
            if (rnd_below(3) == 0 && seg->len > 9 && seg->base != A_RSDP && seg->base != A_FACS)
                make_sum_zero(seg->data, seg->len, 9);
        }
        scan(&w, s, A_RSDP);
        check_scan_invariants(&w, s);
        w_free(&w);
    }
    free(s);
}

void test_acpi(void)
{
    section("acpi scan");
    test_acpi_good();
    test_acpi_root_and_rsdp();
    test_acpi_tables();
    section("acpi scan (fuzz)");
    test_acpi_fuzz();
}

/* ---- SMBIOS entry points ---- */

#define A_SMEP 0x80020000ull
#define A_SMTAB 0x80021000ull

static void smbios_world(world_t *w, int is64, uint32_t table_len, size_t table_alloc)
{
    memset(w, 0, sizeof *w);
    if (table_alloc)
        w_add(w, A_SMTAB, table_alloc);
    if (is64) {
        seg_t *e = w_add(w, A_SMEP, 24);
        memcpy(e->data, "_SM3_", 5);
        e->data[6] = 24;
        e->data[7] = 3;
        e->data[8] = 6;
        st32(e->data + 12, table_len);
        st64(e->data + 16, A_SMTAB);
        make_sum_zero(e->data, 24, 5);
    } else {
        seg_t *e = w_add(w, A_SMEP, 31);
        memcpy(e->data, "_SM_", 4);
        e->data[5] = 31;
        e->data[6] = 2;
        e->data[7] = 8;
        memcpy(e->data + 16, "_DMI_", 5);
        st16(e->data + 22, (uint16_t)table_len);
        st32(e->data + 24, (uint32_t)A_SMTAB);
        make_sum_zero(e->data + 16, 15, 5);
        make_sum_zero(e->data, 31, 4);
    }
}

void test_smbios(void)
{
    world_t w;
    probe_smbios sm;

    section("smbios");
    smbios_world(&w, 1, 600, 600);
    CHECK_EQ(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 1), 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_OK);
    CHECK_EQ(sm.is64, 1);
    CHECK_EQ(sm.ep_len, 24);
    CHECK_EQ(sm.major, 3);
    CHECK_EQ(sm.minor, 6);
    CHECK_EQ(sm.table_len, 600);
    CHECK_EQU(sm.table_addr, A_SMTAB);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 0) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_ANCHOR);
    w.seg[1].data[8] ^= 1;
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 1) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_CHECKSUM);
    w.seg[1].data[8] ^= 1;
    w.seg[1].data[6] = 23;
    make_sum_zero(w.seg[1].data, 24, 5);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 1) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_LENGTH);
    w_free(&w);

    smbios_world(&w, 0, 500, 500);
    CHECK_EQ(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 0), 0);
    CHECK_EQ(sm.is64, 0);
    CHECK_EQ(sm.ep_len, 31);
    CHECK_EQ(sm.major, 2);
    CHECK_EQ(sm.minor, 8);
    CHECK_EQ(sm.table_len, 500);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 1) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_ANCHOR);
    w.seg[1].data[22] ^= 1;
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 0) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_CHECKSUM);
    w.seg[1].data[22] ^= 1;
    w.seg[1].data[21] ^= 1;
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 0) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_CHECKSUM);
    w.seg[1].data[21] ^= 1;
    w.seg[1].data[5] = 30;
    make_sum_zero(w.seg[1].data, 31, 4);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 0) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_LENGTH);
    w.seg[1].data[5] = 31;
    memcpy(w.seg[1].data + 16, "_DMX_", 5);
    make_sum_zero(w.seg[1].data + 16, 15, 5);
    make_sum_zero(w.seg[1].data, 31, 4);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 0) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_CHECKSUM);
    w_free(&w);

    static const struct {
        int is64;
        uint32_t len;
        size_t alloc;
        int status;
    } cases[] = {
        {1, 3, 8, PROBE_SMBIOS_BAD_TABLE},
        {1, 4, 4, PROBE_SMBIOS_OK},
        {1, 0, 8, PROBE_SMBIOS_BAD_TABLE},
        {1, PROBE_SMBIOS_MAX_TABLE, PROBE_SMBIOS_MAX_TABLE, PROBE_SMBIOS_OK},
        {1, PROBE_SMBIOS_MAX_TABLE + 1, 16, PROBE_SMBIOS_BAD_TABLE},
        {1, 0xFFFFFFFFu, 16, PROBE_SMBIOS_BAD_TABLE},
        {1, 600, 599, PROBE_SMBIOS_UNREADABLE},
        {0, 3, 8, PROBE_SMBIOS_BAD_TABLE},
        {0, 600, 599, PROBE_SMBIOS_UNREADABLE},
        {0, 600, 600, PROBE_SMBIOS_OK},
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        smbios_world(&w, cases[i].is64, cases[i].len, cases[i].alloc);
        probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, cases[i].is64);
        CHECK_EQ(sm.status, cases[i].status);
        w_free(&w);
    }

    smbios_world(&w, 1, 600, 0);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 1) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_UNREADABLE);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, 0, 1) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_NULL_PTR);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_UNMAPPED, 1) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_UNREADABLE);
    memcpy(w.seg[0].data, "JUNKJUNK", 8);
    CHECK(probe_smbios_locate(&sm, &world_ops, &w, A_SMEP, 1) != 0);
    CHECK_EQ(sm.status, PROBE_SMBIOS_BAD_ANCHOR);
    w_free(&w);
    CHECK_STR(probe_smbios_status_name(PROBE_SMBIOS_BAD_ANCHOR), "BAD ANCHOR");
    CHECK_STR(probe_smbios_status_name(99), "UNKNOWN");
    CHECK_STR(probe_acpi_status_name(PROBE_ACPI_OVERLAP), "OVERLAP");
    CHECK_STR(probe_acpi_status_name(-1), "UNKNOWN");
}

/* ---- FADT and MADT parsers ---- */

void test_fadt_madt(void)
{
    probe_fadt_info fi;
    probe_cpu_list cpus;
    uint64_t target = 0;

    section("fadt and madt");
    {
        uint8_t fadt[276];
        memset(fadt, 0, sizeof fadt);
        set_header(fadt, "FACP", sizeof fadt, 6);
        st32(fadt + 36, 0x1000);
        st32(fadt + 40, 0x2000);
        st32(fadt + 112, 0xABCD);
        st16(fadt + 129, 0x0003);
        fadt[131] = 2;
        st64(fadt + 132, 0x111100000ull);
        st64(fadt + 140, 0x222200000ull);

        static const size_t lens[] = {43, 44, 115, 116, 131, 132, 139, 140, 147, 148, 276};
        for (size_t i = 0; i < sizeof lens / sizeof lens[0]; i++) {
            uint8_t *exact = exact_copy(fadt, lens[i]);
            int rc = probe_fadt_parse(exact, lens[i], &fi);
            size_t n = lens[i];
            CHECK_EQ(rc, n < 44 ? -1 : 0);
            if (n >= 44) {
                CHECK_EQ(fi.valid, 1);
                CHECK_EQ(fi.has_flags, n >= 116);
                CHECK_EQ(fi.has_arm_boot_arch, n >= 132);
                CHECK_EQ(fi.arm_boot_arch, n >= 132 ? 3 : 0);
                CHECK_EQ(fi.minor, n >= 132 ? 2 : 0);
                CHECK_EQU(fi.dsdt, n >= 148 ? 0x222200000ull : 0x2000);
                CHECK_EQU(fi.facs, n >= 140 ? 0x111100000ull : 0x1000);
            } else {
                CHECK_EQ(fi.valid, 0);
            }
            free(exact);
        }
        fadt[131] = 0;
        memcpy(fadt, "FACX", 4);
        CHECK_EQ(probe_fadt_parse(fadt, sizeof fadt, &fi), -1);
        memcpy(fadt, "FACP", 4);
        fadt[8] = 4;
        CHECK_EQ(probe_fadt_parse(fadt, sizeof fadt, &fi), 0);
        CHECK_EQ(fi.has_arm_boot_arch, 0);
    }

    {
        size_t len = 44 + 24 + 4 * MADT_GICC_LEN;
        uint8_t madt[44 + 24 + 4 * MADT_GICC_LEN];
        memset(madt, 0, sizeof madt);
        set_header(madt, "APIC", (uint32_t)len, 6);
        size_t off = 44;
        madt[off] = 0x0C;
        madt[off + 1] = 24;
        off += 24;
        static const uint32_t flags[] = {1, 0, 1, 1};
        static const uint64_t mpidr[] = {0x81000000ull, 0x81000100ull, 0x81000200ull, 0x81000300ull};
        for (unsigned i = 0; i < 4; i++) {
            madt[off] = 0x0B;
            madt[off + 1] = MADT_GICC_LEN;
            st32(madt + off + 12, flags[i]);
            st64(madt + off + 68, mpidr[i]);
            off += MADT_GICC_LEN;
        }
        uint8_t *exact = exact_copy(madt, len);
        CHECK_EQ(probe_madt_parse(exact, len, &cpus), 0);
        CHECK_EQ(cpus.count, 4);
        CHECK_EQ(cpus.total_seen, 4);
        CHECK_EQU(cpus.cpu[1].mpidr, 0x81000100ull);
        CHECK_EQ(cpus.cpu[1].flags, 0);

        /* The first enabled core other than the caller; disabled cores and
         * the caller's own affinity (ignoring the MT and RES1 bits) are out. */
        CHECK_EQ(probe_pick_psci_target(&cpus, 0x81000000ull, &target), 1);
        CHECK_EQU(target, 0x81000200ull & PROBE_MPIDR_AFF_MASK);
        CHECK_EQ(probe_pick_psci_target(&cpus, 0x81000200ull, &target), 1);
        CHECK_EQU(target, 0x81000000ull & PROBE_MPIDR_AFF_MASK);
        CHECK_EQ(probe_pick_psci_target(&cpus, 0x81000000ull | (1ull << 24), &target), 1);
        CHECK_EQU(target, 0x81000200ull & PROBE_MPIDR_AFF_MASK);
        probe_cpu_list none;
        memset(&none, 0, sizeof none);
        CHECK_EQ(probe_pick_psci_target(&none, 0, &target), 0);
        none.count = 1;
        none.cpu[0].flags = 1;
        none.cpu[0].mpidr = 0x80000000ull;
        CHECK_EQ(probe_pick_psci_target(&none, 0x80000000ull, &target), 0);
        free(exact);

        for (size_t cut = 0; cut < len; cut++) {
            uint8_t *part = exact_copy(madt, cut);
            int rc = probe_madt_parse(part, cut, &cpus);
            if (cut < 44)
                CHECK_EQ(rc, -1);
            free(part);
        }

        uint8_t *bad = exact_copy(madt, len);
        bad[44 + 24 + 1] = 75;
        CHECK_EQ(probe_madt_parse(bad, len, &cpus), -1);
        bad[44 + 24 + 1] = 0;
        CHECK_EQ(probe_madt_parse(bad, len, &cpus), -1);
        bad[44 + 24 + 1] = 1;
        CHECK_EQ(probe_madt_parse(bad, len, &cpus), -1);
        bad[44 + 24 + 1] = 0xFF;
        CHECK_EQ(probe_madt_parse(bad, len, &cpus), -1);
        free(bad);
    }

    {
        size_t n = 40;
        size_t len = 44 + n * MADT_GICC_LEN;
        uint8_t *madt = xalloc(len);
        set_header(madt, "APIC", (uint32_t)len, 6);
        for (size_t i = 0; i < n; i++) {
            madt[44 + i * MADT_GICC_LEN] = 0x0B;
            madt[44 + i * MADT_GICC_LEN + 1] = MADT_GICC_LEN;
            st32(madt + 44 + i * MADT_GICC_LEN + 12, 1);
            st64(madt + 44 + i * MADT_GICC_LEN + 68, i);
        }
        CHECK_EQ(probe_madt_parse(madt, len, &cpus), 0);
        CHECK_EQ(cpus.count, PROBE_MAX_GICC);
        CHECK_EQ(cpus.total_seen, n);
        free(madt);
    }
}
