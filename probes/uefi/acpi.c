/* SPDX-License-Identifier: MIT */
#include "acpi.h"

static const char *const acpi_status_names[PROBE_ACPI_STATUS_COUNT] = {
    "OK",         "ABSENT",      "NULL POINTER", "UNREADABLE", "BAD SIGNATURE",
    "BAD LENGTH", "BAD CHECKSUM", "OVERLAP",      "NO ROOT",    "BAD COUNT",
};

const char *probe_acpi_status_name(int status)
{
    if (status < 0 || status >= PROBE_ACPI_STATUS_COUNT)
        return "UNKNOWN";
    return acpi_status_names[status];
}

/* ACPI table signatures are four characters from [A-Z0-9_]. */
static int sig_is_valid(const uint8_t *s)
{
    for (int i = 0; i < 4; i++) {
        uint8_t c = s[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_'))
            return 0;
    }
    return 1;
}

/* Sums len bytes at addr in small chunks so a hostile length can only waste
 * time up to PROBE_ACPI_MAX_TABLE_LEN, never overrun a stack buffer. */
static int phys_sum(const probe_phys_ops *ops, void *ctx, uint64_t addr, uint32_t len,
                    uint8_t *sum)
{
    uint8_t buf[512];
    uint8_t acc = 0;
    uint32_t off = 0;
    while (off < len) {
        uint32_t n = len - off < sizeof buf ? len - off : (uint32_t)sizeof buf;
        if (ops->read(ctx, addr + off, buf, n))
            return -1;
        acc = (uint8_t)(acc + probe_sum8(buf, n));
        off += n;
    }
    *sum = acc;
    return 0;
}

static int read_checked_table(const probe_phys_ops *ops, void *ctx, uint64_t addr,
                              uint8_t hdr[PROBE_ACPI_HDR_LEN], uint32_t *len)
{
    if (addr == 0)
        return PROBE_ACPI_NULL_PTR;
    if (ops->read(ctx, addr, hdr, PROBE_ACPI_HDR_LEN))
        return PROBE_ACPI_UNREADABLE;
    if (!sig_is_valid(hdr))
        return PROBE_ACPI_BAD_SIGNATURE;
    uint32_t l = probe_le32(hdr + 4);
    if (l < PROBE_ACPI_HDR_LEN || l > PROBE_ACPI_MAX_TABLE_LEN || addr > UINT64_MAX - l)
        return PROBE_ACPI_BAD_LENGTH;
    uint8_t sum;
    if (phys_sum(ops, ctx, addr, l, &sum))
        return PROBE_ACPI_UNREADABLE;
    if (sum)
        return PROBE_ACPI_BAD_CHECKSUM;
    *len = l;
    return PROBE_ACPI_OK;
}

/* Returns 1 if [addr, addr+len) collides with an accepted table. An exact
 * repeat of an accepted table is reported through *same instead, because
 * firmware legitimately lists one table twice. */
static int collides(const probe_acpi_scan *s, uint64_t addr, uint32_t len, int *same)
{
    *same = 0;
    for (unsigned i = 0; i < s->count; i++) {
        const probe_acpi_entry *e = &s->entries[i];
        if (e->status != PROBE_ACPI_OK)
            continue;
        if (e->addr == addr && e->length == len) {
            *same = 1;
            return 0;
        }
        if (addr < e->addr + e->length && e->addr < addr + len)
            return 1;
    }
    return 0;
}

static probe_acpi_entry *new_entry(probe_acpi_scan *s, uint64_t addr, int origin)
{
    if (s->count >= PROBE_ACPI_MAX_ENTRIES)
        return NULL;
    probe_acpi_entry *e = &s->entries[s->count++];
    probe_memset(e, 0, sizeof *e);
    e->addr = addr;
    e->origin = (uint8_t)origin;
    probe_memcpy(e->sig, "????", 4);
    return e;
}

static void finish_entry(probe_acpi_scan *s, probe_acpi_entry *e, int status)
{
    e->status = (uint8_t)status;
    if (status == PROBE_ACPI_OK)
        s->n_ok++;
    else if (status != PROBE_ACPI_ABSENT)
        s->n_rejected++;
}

/* Validates one regular (checksummed) table and records it. */
static void add_table(probe_acpi_scan *s, const probe_phys_ops *ops, void *ctx, uint64_t addr,
                      int origin, const char *expect_sig)
{
    uint8_t hdr[PROBE_ACPI_HDR_LEN];
    uint32_t len = 0;
    int same = 0;

    probe_memset(hdr, 0, sizeof hdr);
    int status = read_checked_table(ops, ctx, addr, hdr, &len);

    if (status == PROBE_ACPI_OK && expect_sig != NULL && probe_memcmp(hdr, expect_sig, 4) != 0)
        status = PROBE_ACPI_BAD_SIGNATURE;
    if (status == PROBE_ACPI_OK && collides(s, addr, len, &same))
        status = PROBE_ACPI_OVERLAP;
    if (status == PROBE_ACPI_OK && same) {
        s->n_dup++;
        return;
    }

    probe_acpi_entry *e = new_entry(s, addr, origin);
    if (e == NULL)
        return;
    /* A rejected table still reports what its header claimed, but only when
     * the signature is printable; an all-zero or garbage header stays "????". */
    if (sig_is_valid(hdr)) {
        probe_memcpy(e->sig, hdr, 4);
        e->length = probe_le32(hdr + 4);
        e->revision = hdr[8];
    }
    finish_entry(s, e, status);
}

/* FACS has no checksum; only its signature and length are meaningful. */
static void add_facs(probe_acpi_scan *s, const probe_phys_ops *ops, void *ctx, uint64_t addr)
{
    uint8_t hdr[8];
    uint32_t len = 0;
    int same = 0;
    int status = PROBE_ACPI_OK;

    /* The classification must finish before the entry exists, otherwise the
     * overlap test would find the new entry and report a collision with itself. */
    if (addr == 0) {
        status = PROBE_ACPI_ABSENT;
    } else if (ops->read(ctx, addr, hdr, sizeof hdr)) {
        status = PROBE_ACPI_UNREADABLE;
    } else if (probe_memcmp(hdr, "FACS", 4) != 0) {
        status = PROBE_ACPI_BAD_SIGNATURE;
    } else {
        len = probe_le32(hdr + 4);
        if (len < 24 || len > PROBE_FACS_MAX_LEN || addr > UINT64_MAX - len)
            status = PROBE_ACPI_BAD_LENGTH;
        else if (!ops->covers(ctx, addr, len))
            status = PROBE_ACPI_UNREADABLE;
        else if (collides(s, addr, len, &same))
            status = PROBE_ACPI_OVERLAP;
    }
    if (status == PROBE_ACPI_OK && same) {
        s->n_dup++;
        return;
    }

    probe_acpi_entry *e = new_entry(s, addr, PROBE_ORIGIN_FADT_FACS);
    if (e == NULL)
        return;
    probe_memcpy(e->sig, "FACS", 4);
    e->length = len;
    finish_entry(s, e, status);
}

int probe_fadt_parse(const uint8_t *buf, size_t len, probe_fadt_info *out)
{
    probe_memset(out, 0, sizeof *out);
    if (len < 44 || probe_memcmp(buf, "FACP", 4) != 0)
        return -1;

    out->revision = buf[8];
    out->length = probe_le32(buf + 4);
    if (len >= 116) {
        out->flags = probe_le32(buf + 112);
        out->has_flags = 1;
    }
    if (len >= 132 && out->revision >= 5) {
        out->arm_boot_arch = probe_le16(buf + 129);
        out->minor = buf[131];
        out->has_arm_boot_arch = 1;
    }

    uint64_t facs = probe_le32(buf + 36);
    uint64_t dsdt = probe_le32(buf + 40);
    uint64_t x_facs = len >= 140 ? probe_le64(buf + 132) : 0;
    uint64_t x_dsdt = len >= 148 ? probe_le64(buf + 140) : 0;
    out->facs = x_facs ? x_facs : facs;
    out->dsdt = x_dsdt ? x_dsdt : dsdt;
    out->valid = 1;
    return 0;
}

static void assign_names(probe_acpi_scan *s)
{
    for (unsigned i = 0; i < s->count; i++) {
        probe_acpi_entry *e = &s->entries[i];
        if (e->status != PROBE_ACPI_OK)
            continue;
        unsigned seen = 0;
        for (unsigned j = 0; j < i; j++)
            if (s->entries[j].status == PROBE_ACPI_OK &&
                probe_memcmp(s->entries[j].sig, e->sig, 4) == 0)
                seen++;
        probe_sb sb;
        probe_sb_init(&sb, e->name, sizeof e->name);
        for (int k = 0; k < 4; k++)
            probe_sb_putc(&sb, e->sig[k]);
        if (seen) {
            probe_sb_putc(&sb, '_');
            probe_sb_dec(&sb, seen);
        }
        probe_sb_puts(&sb, ".bin");
    }
}

int probe_acpi_scan_run(probe_acpi_scan *s, const probe_phys_ops *ops, void *ctx,
                        uint64_t rsdp_addr)
{
    uint8_t *r = s->rsdp_raw;
    uint8_t hdr[PROBE_ACPI_HDR_LEN];
    uint32_t root_len = 0;

    probe_memset(s, 0, sizeof *s);
    s->rsdp_addr = rsdp_addr;
    s->root_status = PROBE_ACPI_NO_ROOT;

    if (rsdp_addr == 0) {
        s->rsdp_status = PROBE_ACPI_NULL_PTR;
        return -1;
    }
    if (ops->read(ctx, rsdp_addr, r, 20)) {
        s->rsdp_status = PROBE_ACPI_UNREADABLE;
        return -1;
    }
    if (probe_memcmp(r, "RSD PTR ", 8) != 0) {
        s->rsdp_status = PROBE_ACPI_BAD_SIGNATURE;
        return -1;
    }
    if (probe_sum8(r, 20) != 0) {
        s->rsdp_status = PROBE_ACPI_BAD_CHECKSUM;
        return -1;
    }
    s->rsdp_revision = r[15];
    s->rsdp_length = 20;
    uint64_t rsdt = probe_le32(r + 16);
    uint64_t xsdt = 0;
    if (s->rsdp_revision >= 2) {
        uint32_t l;
        if (ops->read(ctx, rsdp_addr, r, 36)) {
            s->rsdp_status = PROBE_ACPI_UNREADABLE;
            return -1;
        }
        l = probe_le32(r + 20);
        if (l < 36 || l > PROBE_RSDP_RAW_MAX) {
            s->rsdp_status = PROBE_ACPI_BAD_LENGTH;
            return -1;
        }
        if (ops->read(ctx, rsdp_addr, r, l)) {
            s->rsdp_status = PROBE_ACPI_UNREADABLE;
            return -1;
        }
        if (probe_sum8(r, l) != 0) {
            s->rsdp_status = PROBE_ACPI_BAD_CHECKSUM;
            return -1;
        }
        s->rsdp_length = l;
        xsdt = probe_le64(r + 24);
    }
    s->rsdp_status = PROBE_ACPI_OK;

    /* Prefer the XSDT; once a root is chosen it is not swapped for the other
     * one on failure, because disagreement between them is itself a finding. */
    s->root_is_xsdt = (s->rsdp_revision >= 2 && xsdt != 0);
    uint64_t root_addr = s->root_is_xsdt ? xsdt : rsdt;
    size_t esz = s->root_is_xsdt ? 8 : 4;
    if (root_addr == 0) {
        s->root_status = PROBE_ACPI_NO_ROOT;
        return -1;
    }

    int st = read_checked_table(ops, ctx, root_addr, hdr, &root_len);
    if (st == PROBE_ACPI_OK && probe_memcmp(hdr, s->root_is_xsdt ? "XSDT" : "RSDT", 4) != 0)
        st = PROBE_ACPI_BAD_SIGNATURE;
    if (st == PROBE_ACPI_OK) {
        uint32_t body = root_len - PROBE_ACPI_HDR_LEN;
        if (body % esz || body / esz > PROBE_ACPI_MAX_ROOT_ENTRIES)
            st = PROBE_ACPI_BAD_COUNT;
    }
    s->root_status = st;

    probe_acpi_entry *root = new_entry(s, root_addr, PROBE_ORIGIN_ROOT);
    if (root != NULL) {
        if (st == PROBE_ACPI_OK || st == PROBE_ACPI_BAD_COUNT) {
            probe_memcpy(root->sig, hdr, 4);
            root->length = root_len;
            root->revision = hdr[8];
        }
        finish_entry(s, root, st);
    }
    if (st != PROBE_ACPI_OK)
        return -1;

    uint8_t ents[PROBE_ACPI_MAX_ROOT_ENTRIES * 8];
    uint32_t body = root_len - PROBE_ACPI_HDR_LEN;
    if (body && ops->read(ctx, root_addr + PROBE_ACPI_HDR_LEN, ents, body)) {
        /* The checksum pass just read this range, so a failure here means the
         * physical-memory backend is unstable; downgrade the root and stop. */
        s->root_status = PROBE_ACPI_UNREADABLE;
        s->entries[0].status = PROBE_ACPI_UNREADABLE;
        s->n_ok--;
        s->n_rejected++;
        return -1;
    }
    for (uint32_t i = 0; i < body / esz; i++) {
        uint64_t addr = s->root_is_xsdt ? probe_le64(ents + i * 8) : probe_le32(ents + i * 4);
        add_table(s, ops, ctx, addr, PROBE_ORIGIN_ROOT_ENTRY, NULL);
    }

    for (unsigned i = 0; i < s->count; i++) {
        probe_acpi_entry *e = &s->entries[i];
        if (e->status == PROBE_ACPI_OK && probe_memcmp(e->sig, "FACP", 4) == 0) {
            uint8_t fbuf[512];
            uint32_t n = e->length < sizeof fbuf ? e->length : (uint32_t)sizeof fbuf;
            if (ops->read(ctx, e->addr, fbuf, n) == 0 && probe_fadt_parse(fbuf, n, &s->fadt) == 0)
                break;
        }
    }
    if (s->fadt.valid) {
        add_table(s, ops, ctx, s->fadt.dsdt, PROBE_ORIGIN_FADT_DSDT, "DSDT");
        add_facs(s, ops, ctx, s->fadt.facs);
    }

    assign_names(s);
    return 0;
}

int probe_madt_parse(const uint8_t *madt, size_t len, probe_cpu_list *out)
{
    probe_memset(out, 0, sizeof *out);
    if (len < 44 || probe_memcmp(madt, "APIC", 4) != 0)
        return -1;

    size_t off = 44;
    while (off < len) {
        if (len - off < 2)
            return -1;
        uint8_t type = madt[off];
        size_t elen = madt[off + 1];
        if (elen < 2 || elen > len - off)
            return -1;
        if (type == 0x0B) { /* GICC */
            if (elen < 76)
                return -1;
            out->total_seen++;
            if (out->count < PROBE_MAX_GICC) {
                out->cpu[out->count].flags = probe_le32(madt + off + 12);
                out->cpu[out->count].mpidr = probe_le64(madt + off + 68);
                out->count++;
            }
        }
        off += elen;
    }
    return 0;
}

int probe_pick_psci_target(const probe_cpu_list *cpus, uint64_t own_mpidr, uint64_t *target)
{
    for (unsigned i = 0; i < cpus->count; i++) {
        if (!(cpus->cpu[i].flags & 1u))
            continue;
        if ((cpus->cpu[i].mpidr & PROBE_MPIDR_AFF_MASK) == (own_mpidr & PROBE_MPIDR_AFF_MASK))
            continue;
        *target = cpus->cpu[i].mpidr & PROBE_MPIDR_AFF_MASK;
        return 1;
    }
    return 0;
}

static const char *const smbios_status_names[] = {
    "OK", "NULL POINTER", "UNREADABLE", "BAD ANCHOR", "BAD LENGTH", "BAD CHECKSUM", "BAD TABLE",
};

const char *probe_smbios_status_name(int status)
{
    if (status < 0 || (size_t)status >= sizeof smbios_status_names / sizeof smbios_status_names[0])
        return "UNKNOWN";
    return smbios_status_names[status];
}

int probe_smbios_locate(probe_smbios *o, const probe_phys_ops *ops, void *ctx, uint64_t ep_addr,
                        int expect64)
{
    uint8_t *b = o->ep;

    probe_memset(o, 0, sizeof *o);
    if (ep_addr == 0) {
        o->status = PROBE_SMBIOS_NULL_PTR;
        return -1;
    }
    if (ops->read(ctx, ep_addr, b, 5)) {
        o->status = PROBE_SMBIOS_UNREADABLE;
        return -1;
    }
    if (probe_memcmp(b, "_SM3_", 5) == 0) {
        o->is64 = 1;
        o->ep_len = 24;
    } else if (probe_memcmp(b, "_SM_", 4) == 0) {
        o->is64 = 0;
        o->ep_len = 31;
    } else {
        o->status = PROBE_SMBIOS_BAD_ANCHOR;
        return -1;
    }
    if (o->is64 != (expect64 != 0)) {
        o->status = PROBE_SMBIOS_BAD_ANCHOR;
        return -1;
    }
    if (ops->read(ctx, ep_addr, b, o->ep_len)) {
        o->status = PROBE_SMBIOS_UNREADABLE;
        return -1;
    }

    if (o->is64) {
        if (b[6] != 24) {
            o->status = PROBE_SMBIOS_BAD_LENGTH;
            return -1;
        }
        if (probe_sum8(b, 24) != 0) {
            o->status = PROBE_SMBIOS_BAD_CHECKSUM;
            return -1;
        }
        o->major = b[7];
        o->minor = b[8];
        o->table_len = probe_le32(b + 12);
        o->table_addr = probe_le64(b + 16);
    } else {
        if (b[5] != 31) {
            o->status = PROBE_SMBIOS_BAD_LENGTH;
            return -1;
        }
        if (probe_sum8(b, 31) != 0 || probe_memcmp(b + 16, "_DMI_", 5) != 0 ||
            probe_sum8(b + 16, 15) != 0) {
            o->status = PROBE_SMBIOS_BAD_CHECKSUM;
            return -1;
        }
        o->major = b[6];
        o->minor = b[7];
        o->table_len = probe_le16(b + 22);
        o->table_addr = probe_le32(b + 24);
    }

    if (o->table_addr == 0 || o->table_len < 4 || o->table_len > PROBE_SMBIOS_MAX_TABLE ||
        o->table_addr > UINT64_MAX - o->table_len) {
        o->status = PROBE_SMBIOS_BAD_TABLE;
        return -1;
    }
    if (!ops->covers(ctx, o->table_addr, o->table_len)) {
        o->status = PROBE_SMBIOS_UNREADABLE;
        return -1;
    }
    o->status = PROBE_SMBIOS_OK;
    return 0;
}
