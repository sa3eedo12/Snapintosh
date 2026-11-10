/* SPDX-License-Identifier: MIT */
#include "dump.h"

typedef struct {
    const probe_dump_ctx *c;
    probe_dump_result *res;
    efi_file *root;
    efi_file *base;
    efi_file *run;
    int write_failed;  /* a UEFI write error: no further files are attempted */
    int incomplete;    /* a file was skipped or a text file was truncated */
} dump_state;

static void to_utf16(efi_char16 *dst, size_t cap, const char *src)
{
    size_t i = 0;

    for (; src[i] != 0 && i + 1 < cap; i++)
        dst[i] = (efi_char16)(uint8_t)src[i];
    dst[i] = 0;
}

enum { OPEN_NEW_OK = 0, OPEN_NEW_EXISTS = 1, OPEN_NEW_ERROR = -1 };

/* Creates `name` in dir only if it does not already exist, so that no run can
 * overwrite earlier evidence. */
static int open_new(efi_file *dir, const char *name, efi_file **out)
{
    efi_char16 n16[48];
    efi_file *h = NULL;
    efi_status st;

    to_utf16(n16, sizeof n16 / sizeof n16[0], name);
    st = dir->open(dir, &h, n16, EFI_FILE_MODE_READ, 0);
    if (!EFI_IS_ERROR(st)) {
        if (h != NULL)
            h->close(h);
        return OPEN_NEW_EXISTS;
    }
    if (st != EFI_NOT_FOUND)
        return OPEN_NEW_ERROR;
    st = dir->open(dir, out, n16, EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE | EFI_FILE_MODE_CREATE, 0);
    return EFI_IS_ERROR(st) || *out == NULL ? OPEN_NEW_ERROR : OPEN_NEW_OK;
}

static int write_all(efi_file *f, const void *buf, size_t len)
{
    const uint8_t *p = buf;

    while (len != 0) {
        size_t chunk = len < (1u << 20) ? len : (1u << 20);
        efi_uintn n = chunk;

        if (EFI_IS_ERROR(f->write(f, &n, p)) || n != chunk)
            return -1;
        p += chunk;
        len -= chunk;
    }
    return 0;
}

/* Reserves room for one file in the caps; 0 if it fits. */
static int check_caps(dump_state *d, uint64_t len)
{
    if (d->res->files >= PROBE_DUMP_MAX_FILES || len > PROBE_DUMP_MAX_FILE_BYTES ||
        d->res->bytes + len > PROBE_DUMP_MAX_TOTAL_BYTES) {
        d->incomplete = 1;
        return -1;
    }
    return 0;
}

static void finish_file(dump_state *d, efi_file *f, int ok, uint64_t len)
{
    if (ok && EFI_IS_ERROR(f->flush(f)))
        ok = 0;
    /* Close writes out whatever the flush left cached. */
    if (EFI_IS_ERROR(f->close(f)))
        ok = 0;
    if (ok) {
        d->res->files++;
        d->res->bytes += len;
    } else {
        d->write_failed = 1;
    }
}

static void put_buffer(dump_state *d, const char *name, const void *buf, size_t len)
{
    efi_file *f = NULL;
    int rc;

    if (d->write_failed || check_caps(d, len))
        return;
    rc = open_new(d->run, name, &f);
    if (rc == OPEN_NEW_EXISTS) {
        d->incomplete = 1;
        return;
    }
    if (rc != OPEN_NEW_OK) {
        d->write_failed = 1;
        return;
    }
    finish_file(d, f, write_all(f, buf, len) == 0, len);
}

/* Copies a firmware table to a file through the caller's bounce buffer; the
 * source range is re-validated by ops->read on every chunk. */
static void put_phys(dump_state *d, const char *name, uint64_t addr, uint32_t len)
{
    const probe_dump_ctx *c = d->c;
    efi_file *f = NULL;
    uint32_t off = 0;
    int ok = 1;
    int rc;

    if (d->write_failed || check_caps(d, len))
        return;
    rc = open_new(d->run, name, &f);
    if (rc == OPEN_NEW_EXISTS) {
        d->incomplete = 1;
        return;
    }
    if (rc != OPEN_NEW_OK) {
        d->write_failed = 1;
        return;
    }
    while (off < len) {
        uint32_t n = len - off < PROBE_DUMP_BOUNCE_BYTES ? len - off : PROBE_DUMP_BOUNCE_BYTES;

        if (c->ops->read(c->ops_ctx, addr + off, c->bounce, n) != 0 ||
            write_all(f, c->bounce, n) != 0) {
            ok = 0;
            break;
        }
        off += n;
    }
    finish_file(d, f, ok, len);
}

/* ---- Text files ---- */

static void kv_str(probe_sb *sb, const char *k, const char *v)
{
    probe_sb_puts(sb, k);
    probe_sb_putc(sb, '=');
    probe_sb_puts(sb, v);
    probe_sb_putc(sb, '\n');
}

static void kv_hex(probe_sb *sb, const char *k, uint64_t v, unsigned digits)
{
    probe_sb_puts(sb, k);
    probe_sb_puts(sb, "=0x");
    probe_sb_hex(sb, v, digits);
    probe_sb_putc(sb, '\n');
}

static void kv_dec(probe_sb *sb, const char *k, uint64_t v)
{
    probe_sb_puts(sb, k);
    probe_sb_putc(sb, '=');
    probe_sb_dec(sb, v);
    probe_sb_putc(sb, '\n');
}

static void put_guid(probe_sb *sb, const efi_guid *g)
{
    probe_sb_hex(sb, g->data1, 8);
    probe_sb_putc(sb, '-');
    probe_sb_hex(sb, g->data2, 4);
    probe_sb_putc(sb, '-');
    probe_sb_hex(sb, g->data3, 4);
    probe_sb_putc(sb, '-');
    probe_sb_hex(sb, ((unsigned)g->data4[0] << 8) | g->data4[1], 4);
    probe_sb_putc(sb, '-');
    for (int i = 2; i < 8; i++)
        probe_sb_hex(sb, g->data4[i], 2);
}

static const char *origin_name(uint8_t origin)
{
    switch (origin) {
    case PROBE_ORIGIN_ROOT:
        return "ROOT";
    case PROBE_ORIGIN_ROOT_ENTRY:
        return "ENTRY";
    case PROBE_ORIGIN_FADT_DSDT:
        return "DSDT";
    case PROBE_ORIGIN_FADT_FACS:
        return "FACS";
    default:
        return "?";
    }
}

static void put_text(dump_state *d, const char *name, const probe_sb *sb)
{
    if (sb->overflow)
        d->incomplete = 1;
    put_buffer(d, name, d->c->text, sb->len);
}

/* One row per descriptor with fixed-width hex fields, so that a consumer can
 * parse it strictly. The raw bytes are in memmap.raw. */
static void write_memmap_txt(dump_state *d)
{
    const probe_dump_ctx *c = d->c;
    probe_sb sb;

    probe_sb_init(&sb, c->text, PROBE_DUMP_TEXT_BYTES);
    probe_sb_puts(&sb, "# SP11PROBE memmap v1\n");
    probe_sb_puts(&sb, "# descriptor_size=");
    probe_sb_dec(&sb, c->memmap->desc_size);
    probe_sb_puts(&sb, " descriptor_version=");
    probe_sb_dec(&sb, c->memmap_desc_version);
    probe_sb_puts(&sb, " count=");
    probe_sb_dec(&sb, c->memmap->count);
    probe_sb_puts(&sb, " map_size=");
    probe_sb_dec(&sb, c->memmap_size);
    probe_sb_puts(&sb, "\n# idx type phys virt pages attr name\n");
    for (size_t i = 0; i < c->memmap->count; i++) {
        uint32_t type;
        uint64_t phys, virt, pages, attr;

        if (probe_memmap_get(c->memmap, i, &type, &phys, &virt, &pages, &attr) != 0)
            break;
        probe_sb_hex(&sb, i, 4);
        probe_sb_putc(&sb, ' ');
        probe_sb_hex(&sb, type, 2);
        probe_sb_putc(&sb, ' ');
        probe_sb_hex(&sb, phys, 16);
        probe_sb_putc(&sb, ' ');
        probe_sb_hex(&sb, virt, 16);
        probe_sb_putc(&sb, ' ');
        probe_sb_hex(&sb, pages, 16);
        probe_sb_putc(&sb, ' ');
        probe_sb_hex(&sb, attr, 16);
        probe_sb_putc(&sb, ' ');
        probe_sb_puts(&sb, probe_memtype_name(type));
        probe_sb_putc(&sb, '\n');
    }
    put_text(d, "memmap.txt", &sb);
}

static void write_tables_txt(dump_state *d)
{
    const probe_dump_ctx *c = d->c;
    const probe_acpi_scan *s = c->scan;
    probe_sb sb;

    probe_sb_init(&sb, c->text, PROBE_DUMP_TEXT_BYTES);
    probe_sb_puts(&sb, "# SP11PROBE tables v1\n");
    kv_str(&sb, "# rsdp_status", probe_acpi_status_name(s->rsdp_status));
    kv_hex(&sb, "# rsdp_addr", s->rsdp_addr, 16);
    probe_sb_puts(&sb, "# idx origin sig rev length addr file status\n");
    for (unsigned i = 0; i < s->count; i++) {
        const probe_acpi_entry *e = &s->entries[i];

        probe_sb_hex(&sb, i, 2);
        probe_sb_putc(&sb, ' ');
        probe_sb_puts(&sb, origin_name(e->origin));
        probe_sb_putc(&sb, ' ');
        probe_sb_puts(&sb, e->sig);
        probe_sb_putc(&sb, ' ');
        probe_sb_dec(&sb, e->revision);
        probe_sb_putc(&sb, ' ');
        probe_sb_hex(&sb, e->length, 8);
        probe_sb_putc(&sb, ' ');
        probe_sb_hex(&sb, e->addr, 16);
        probe_sb_putc(&sb, ' ');
        probe_sb_puts(&sb, e->status == PROBE_ACPI_OK ? e->name : "-");
        probe_sb_putc(&sb, ' ');
        probe_sb_puts(&sb, probe_acpi_status_name(e->status));
        probe_sb_putc(&sb, '\n');
    }
    put_text(d, "tables.txt", &sb);
}

/* No SMBIOS string is copied here: the raw SMBIOS table is dumped as a binary
 * file for offline use, but the text summary stays free of serial numbers and
 * UUIDs so that it is safe to paste into a bug report. */
static void write_summary_txt(dump_state *d)
{
    const probe_dump_ctx *c = d->c;
    const probe_facts *f = c->facts;
    const efi_system_table *st = c->st;
    probe_sb sb;

    probe_sb_init(&sb, c->text, PROBE_DUMP_TEXT_BYTES);
    probe_sb_puts(&sb, "# SP11PROBE summary v1\n");
    kv_str(&sb, "build", f->build_id);
    kv_str(&sb, "variant", f->variant_psci ? "psci" : "default");
    kv_str(&sb, "reset", f->reset_enabled ? "on" : "off");
    kv_hex(&sb, "uefi_revision", st->hdr.revision, 8);
    kv_str(&sb, "fw_vendor", f->fw_vendor);
    kv_hex(&sb, "fw_revision", f->fw_revision, 8);
    if (f->have_preebs_el)
        kv_hex(&sb, "preebs_currentel", f->preebs_el_raw, 16);
    if (f->have_gop) {
        kv_dec(&sb, "gop_width", f->gop_width);
        kv_dec(&sb, "gop_height", f->gop_height);
        kv_dec(&sb, "gop_stride_pixels", f->gop_stride_pixels);
        kv_hex(&sb, "gop_format", f->gop_format, 1);
        kv_hex(&sb, "gop_base", f->gop_base, 16);
        kv_hex(&sb, "gop_size", f->gop_size, 16);
        kv_str(&sb, "gop_select", probe_gop_rule_name(f->gop_rule));
    } else {
        kv_str(&sb, "gop", "none");
    }
    if (f->have_layout) {
        kv_dec(&sb, "layout_scale", f->layout_scale);
        kv_dec(&sb, "layout_cols", f->layout_cols);
        kv_dec(&sb, "layout_rows", f->layout_rows);
        kv_dec(&sb, "layout_pages", f->layout_pages);
    }
    kv_dec(&sb, "config_tables", f->cfg_count);
    for (uint64_t i = 0; i < f->cfg_count && i < PROBE_DUMP_MAX_CFG_LINES; i++) {
        const efi_configuration_table *t = &st->configuration_table[i];

        probe_sb_puts(&sb, "cfg=");
        put_guid(&sb, &t->vendor_guid);
        probe_sb_puts(&sb, " 0x");
        probe_sb_hex(&sb, (uint64_t)(uintptr_t)t->vendor_table, 16);
        probe_sb_putc(&sb, '\n');
    }
    kv_str(&sb, "acpi_rsdp", probe_acpi_status_name(f->rsdp_status));
    kv_str(&sb, "acpi_root", f->root_is_xsdt ? "xsdt" : "rsdt");
    kv_str(&sb, "acpi_root_status", probe_acpi_status_name(f->root_status));
    kv_dec(&sb, "acpi_ok", f->acpi_ok);
    kv_dec(&sb, "acpi_rejected", f->acpi_rejected);
    kv_dec(&sb, "acpi_duplicate", f->acpi_dup);
    if (f->fadt_valid) {
        kv_dec(&sb, "fadt_revision", f->fadt_revision);
        kv_dec(&sb, "fadt_minor", f->fadt_minor);
        if (f->has_arm_boot_arch)
            kv_hex(&sb, "fadt_arm_boot_arch", f->arm_boot_arch, 4);
    }
    kv_str(&sb, "psci_conduit", probe_psci_conduit_name((enum probe_psci_conduit)f->psci_conduit));
    kv_str(&sb, "smbios", probe_smbios_status_name(f->smbios_status));
    if (f->smbios_status == PROBE_SMBIOS_OK) {
        kv_dec(&sb, "smbios_major", f->smbios_major);
        kv_dec(&sb, "smbios_minor", f->smbios_minor);
        kv_dec(&sb, "smbios_table_len", f->smbios_table_len);
    }
    kv_dec(&sb, "memmap_count", f->memmap_count);
    kv_dec(&sb, "memmap_descriptor_size", f->memmap_desc_size);
    if (f->have_ram) {
        kv_hex(&sb, "ram_lo", f->ram_lo, 16);
        kv_hex(&sb, "ram_hi", f->ram_hi, 16);
    }
    kv_dec(&sb, "dump_run", d->res->run);
    kv_dec(&sb, "dump_files_before_summary", d->res->files);
    kv_dec(&sb, "dump_bytes_before_summary", d->res->bytes);
    kv_str(&sb, "dump_complete_so_far", d->incomplete || d->write_failed ? "no" : "yes");
    put_text(d, "summary.txt", &sb);
}

/* ---- Volume and run directory ---- */

static int select_volume(const probe_dump_ctx *c, efi_file **root)
{
    efi_boot_services *bs = c->st->boot_services;
    efi_guid li_guid = EFI_GUID_LOADED_IMAGE;
    efi_guid dp_guid = EFI_GUID_DEVICE_PATH;
    efi_guid fs_guid = EFI_GUID_SIMPLE_FS;
    efi_loaded_image *li = NULL;
    uint8_t *dp = NULL;
    efi_simple_fs *fs = NULL;

    if (EFI_IS_ERROR(bs->handle_protocol(c->image, &li_guid, (void **)&li)) || li == NULL ||
        li->device_handle == NULL)
        return PROBE_DUMP_REFUSED_NO_VOLUME;
    if (EFI_IS_ERROR(bs->handle_protocol(li->device_handle, &dp_guid, (void **)&dp)) || dp == NULL)
        return PROBE_DUMP_REFUSED_NO_VOLUME;

    switch (probe_dp_classify(dp, PROBE_DUMP_DP_MAX_LEN)) {
    case PROBE_DP_USB:
        break;
    case PROBE_DP_NO_USB:
        return PROBE_DUMP_REFUSED_NOT_USB;
    case PROBE_DP_AMBIGUOUS:
        return PROBE_DUMP_REFUSED_AMBIGUOUS;
    default:
        return PROBE_DUMP_REFUSED_MALFORMED;
    }

    if (EFI_IS_ERROR(bs->handle_protocol(li->device_handle, &fs_guid, (void **)&fs)) || fs == NULL)
        return PROBE_DUMP_REFUSED_NO_VOLUME;
    if (EFI_IS_ERROR(fs->open_volume(fs, root)) || *root == NULL)
        return PROBE_DUMP_REFUSED_NO_VOLUME;
    return PROBE_DUMP_OK;
}

static int make_run_dir(dump_state *d)
{
    efi_char16 n16[16];
    char name[8];

    to_utf16(n16, sizeof n16 / sizeof n16[0], "SP11DUMP");
    if (EFI_IS_ERROR(d->root->open(d->root, &d->base, n16,
                                   EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE | EFI_FILE_MODE_CREATE,
                                   EFI_FILE_DIRECTORY)) ||
        d->base == NULL)
        return PROBE_DUMP_ERROR;

    for (unsigned run = 1; run <= PROBE_DUMP_MAX_RUN; run++) {
        efi_file *h = NULL;
        efi_status st;
        probe_sb sb;

        probe_sb_init(&sb, name, sizeof name);
        probe_sb_puts(&sb, "RUN");
        if (run < 10)
            probe_sb_putc(&sb, '0');
        probe_sb_dec(&sb, run);
        to_utf16(n16, sizeof n16 / sizeof n16[0], name);

        st = d->base->open(d->base, &h, n16, EFI_FILE_MODE_READ, 0);
        if (!EFI_IS_ERROR(st)) {
            if (h != NULL)
                h->close(h);
            continue;
        }
        if (st != EFI_NOT_FOUND)
            return PROBE_DUMP_ERROR;
        if (EFI_IS_ERROR(d->base->open(d->base, &d->run, n16,
                                       EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE |
                                           EFI_FILE_MODE_CREATE,
                                       EFI_FILE_DIRECTORY)) ||
            d->run == NULL)
            return PROBE_DUMP_ERROR;
        d->res->run = run;
        return PROBE_DUMP_OK;
    }
    return PROBE_DUMP_NO_RUN_SLOT;
}

/* Flushes and closes the run directory, the SP11DUMP directory and the volume
 * root, in that order, and returns -1 if any step failed. The directory entries
 * and the root flush are what make the new files findable after the reset, and
 * ExitBootServices follows, so a failure here means the dump may not be on the
 * stick even though every file write reported success. */
static int close_all(dump_state *d)
{
    efi_file *handles[3] = {d->run, d->base, d->root};
    int rc = 0;

    for (int i = 0; i < 3; i++) {
        if (handles[i] == NULL)
            continue;
        if (EFI_IS_ERROR(handles[i]->flush(handles[i])))
            rc = -1;
        if (EFI_IS_ERROR(handles[i]->close(handles[i])))
            rc = -1;
    }
    d->run = d->base = d->root = NULL;
    return rc;
}

void probe_dump_run(const probe_dump_ctx *c, probe_dump_result *out)
{
    dump_state d;
    const probe_acpi_scan *s = c->scan;
    int rc;

    probe_memset(out, 0, sizeof *out);
    probe_memset(&d, 0, sizeof d);
    d.c = c;
    d.res = out;

    rc = select_volume(c, &d.root);
    if (rc != PROBE_DUMP_OK) {
        out->state = rc;
        return;
    }
    rc = make_run_dir(&d);
    if (rc != PROBE_DUMP_OK) {
        out->state = rc;
        (void)close_all(&d);
        return;
    }

    if (s->rsdp_status == PROBE_ACPI_OK)
        put_buffer(&d, "RSDP.bin", s->rsdp_raw, s->rsdp_length);
    for (unsigned i = 0; i < s->count; i++) {
        const probe_acpi_entry *e = &s->entries[i];

        if (e->status == PROBE_ACPI_OK)
            put_phys(&d, e->name, e->addr, e->length);
    }
    if (c->smbios->status == PROBE_SMBIOS_OK) {
        put_buffer(&d, "smbios-ep.raw", c->smbios->ep, c->smbios->ep_len);
        put_phys(&d, "smbios-table.raw", c->smbios->table_addr, c->smbios->table_len);
    }
    put_buffer(&d, "memmap.raw", c->memmap_raw, c->memmap_size);
    write_memmap_txt(&d);
    write_tables_txt(&d);
    write_summary_txt(&d);
    if (close_all(&d) != 0)
        d.write_failed = 1;

    if (d.write_failed)
        out->state = out->files != 0 ? PROBE_DUMP_PARTIAL : PROBE_DUMP_ERROR;
    else if (d.incomplete)
        out->state = PROBE_DUMP_PARTIAL;
    else
        out->state = PROBE_DUMP_OK;
}
