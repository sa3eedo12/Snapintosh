/* SPDX-License-Identifier: MIT */
/* SP11 EL2 probe: UEFI entry point and control flow.
 *
 * The operator boots this image after slbounce has moved the machine to EL2.
 * Everything that needs UEFI happens first and is recorded as plain data:
 *
 *   1. firmware facts, configuration tables, the framebuffer and a memory map;
 *   2. the ACPI and SMBIOS tables, read only through the memory map;
 *   3. a dump of those tables to the USB stick the image was loaded from, if
 *      and only if that volume is USB (see dump.h);
 *   4. ExitBootServices, called here so that the first post-exit instruction
 *      is ours and nothing else has touched the machine.
 *
 * After step 4 the screen is the only output channel, so the report is drawn
 * after each register tier (see report.h) and the probe ends with a visible
 * reset countdown. No UEFI service is called after ExitBootServices except
 * ResetSystem.
 *
 * The control flow is split into small functions so that a later test image
 * can reuse the gathering and display stages and replace the post-EL2 part.
 */
#include "acpi.h"
#include "cpu.h"
#include "dump.h"
#include "efi_types.h"
#include "fbtext.h"
#include "logic.h"
#include "psci.h"
#include "report.h"

#ifndef PROBE_BUILD_ID
#define PROBE_BUILD_ID "DEV000000000"
#endif
#ifndef PROBE_PSCI
#define PROBE_PSCI 0
#endif
#ifndef PROBE_NO_RESET
#define PROBE_NO_RESET 0
#endif
#ifndef PROBE_FAST_TEST
#define PROBE_FAST_TEST 0
#endif
#ifndef PROBE_TESTS_IMAGE
#define PROBE_TESTS_IMAGE 0
#endif

#if PROBE_TESTS_IMAGE
#include "tests/tests_hook.h"
#endif

_Static_assert(sizeof(PROBE_BUILD_ID) == PROBE_BUILD_ID_LEN + 1,
               "PROBE_BUILD_ID must be exactly 12 characters");

/* The build id as bytes in .rdata. The optimiser folds every other use of
 * PROBE_BUILD_ID into instruction immediates, which would leave nothing for
 * `strings`, a hash manifest or an audit of the stick to find. `used` keeps
 * the object even though no code refers to it. */
static const char g_build_tag[] __attribute__((used)) = "SP11PROBE-BUILD-ID=" PROBE_BUILD_ID;

/* Timings. The fast values exist only for the emulator test image. Its
 * countdown covers one dwell per page of the largest possible layout, so an
 * emulator run sees every page before ResetSystem ends the virtual machine. */
#if PROBE_FAST_TEST
#define PROBE_PRE_EBS_STALL_S 0u
#define PROBE_PAGE_DWELL_S 2u
#define PROBE_RESET_COUNTDOWN_S (PROBE_PAGE_DWELL_S * PROBE_LAYOUT_MAX_PAGES)
#else
#define PROBE_PRE_EBS_STALL_S 3u
#define PROBE_PAGE_DWELL_S 12u
#define PROBE_RESET_COUNTDOWN_S 60u
#endif

/* Used when CNTFRQ_EL0 reads as zero: a fixed busy loop per displayed second
 * instead of the generic timer. */
#define PROBE_FALLBACK_SECOND_SPINS 100000000ull

#define PROBE_EBS_MAX_ATTEMPTS 8u
#define PROBE_MEMMAP_BUF_BYTES (128u * 1024u)
#define PROBE_MADT_BUF_BYTES (16u * 1024u)
#define PROBE_MAX_GOP_HANDLES 32u
#define PROBE_MAX_CFG_SCAN 4096u
#define PROBE_FW_VENDOR_UNITS 64u

/* All working storage is static: nothing is allocated between the memory map
 * snapshot taken for the dump and ExitBootServices, except the optional larger
 * map buffer in the exit loop. */
static uint8_t g_memmap_buf[PROBE_MEMMAP_BUF_BYTES] __attribute__((aligned(16)));
static uint8_t g_ebs_map_buf[PROBE_MEMMAP_BUF_BYTES] __attribute__((aligned(16)));
static uint8_t g_bounce[PROBE_DUMP_BOUNCE_BYTES];
static char g_text[PROBE_DUMP_TEXT_BYTES];

static probe_acpi_scan g_scan;
static probe_smbios g_smbios;
static probe_memmap g_memmap;
static probe_facts g_facts;
static probe_regs g_regs;
static probe_report g_report;
static probe_fb g_fb;
static probe_layout g_layout;
static void (*g_reset_system)(uint32_t, efi_status, efi_uintn, void *);

#if PROBE_PSCI
static uint8_t g_madt[PROBE_MADT_BUF_BYTES];
static probe_cpu_list g_cpus;
static probe_psci_cell g_psci_cell __attribute__((aligned(64)));
#endif
static efi_system_table *g_st;

typedef struct {
    uint64_t acpi20;
    uint64_t acpi10;
    uint64_t smbios;
    uint64_t smbios3;
} cfg_ptrs;

/* ---- Console (before ExitBootServices only) ---- */

static void con_write(const char *s)
{
    efi_simple_text_output *out = g_st->con_out;
    efi_char16 buf[96];
    size_t n = 0;

    if (out == NULL)
        return;
    for (; *s != 0; s++) {
        /* Leave room for a CR, the character and the terminator. */
        if (n + 3 > sizeof buf / sizeof buf[0]) {
            buf[n] = 0;
            out->output_string(out, buf);
            n = 0;
        }
        if (*s == '\n')
            buf[n++] = '\r';
        buf[n++] = (efi_char16)(uint8_t)*s;
    }
    buf[n] = 0;
    out->output_string(out, buf);
}

typedef struct {
    probe_sb sb;
    char buf[192];
} con_line;

static void line_begin(con_line *l, const char *tag)
{
    probe_sb_init(&l->sb, l->buf, sizeof l->buf);
    probe_sb_puts(&l->sb, "SP11PROBE ");
    probe_sb_puts(&l->sb, tag);
}

static void line_key(con_line *l, const char *key)
{
    probe_sb_putc(&l->sb, ' ');
    probe_sb_puts(&l->sb, key);
    probe_sb_putc(&l->sb, '=');
}

static void line_str(con_line *l, const char *key, const char *value)
{
    line_key(l, key);
    probe_sb_puts(&l->sb, value);
}

static void line_hex(con_line *l, const char *key, uint64_t v, unsigned digits)
{
    line_key(l, key);
    probe_sb_puts(&l->sb, "0x");
    probe_sb_hex(&l->sb, v, digits);
}

static void line_dec(con_line *l, const char *key, uint64_t v)
{
    line_key(l, key);
    probe_sb_dec(&l->sb, v);
}

static void line_end(con_line *l)
{
    probe_sb_putc(&l->sb, '\n');
    con_write(l->buf);
}

static const char *pixel_format_name(uint32_t fmt)
{
    if (fmt == EFI_GOP_RGBX)
        return "RGBX";
    if (fmt == EFI_GOP_BGRX)
        return "BGRX";
    return "OTHER";
}

/* ---- Firmware memory access, backed by the UEFI memory map ---- */

static int ops_covers(void *ctx, uint64_t addr, uint64_t len)
{
    return probe_memmap_covers((const probe_memmap *)ctx, addr, len, PROBE_READABLE_TYPES_MASK);
}

static int ops_read(void *ctx, uint64_t addr, void *dst, size_t len)
{
    if (!ops_covers(ctx, addr, len))
        return -1;
    probe_memcpy(dst, (const void *)(uintptr_t)addr, len);
    return 0;
}

/* ---- Fact gathering ---- */

static void init_facts(probe_facts *f)
{
    const char *id = PROBE_BUILD_ID;

    probe_memset(f, 0, sizeof *f);
    for (size_t i = 0; i < PROBE_BUILD_ID_LEN; i++)
        f->build_id[i] = id[i];
    f->variant_psci = PROBE_PSCI ? 1 : 0;
    f->reset_enabled = PROBE_NO_RESET ? 0 : 1;
    f->psci.enabled = PROBE_PSCI ? 1 : 0;
    f->psci.state = PROBE_PSCI_RUN_NOT_ATTEMPTED;
    f->rsdp_status = PROBE_ACPI_ABSENT;
    f->root_status = PROBE_ACPI_ABSENT;
    f->smbios_status = PROBE_SMBIOS_NULL_PTR;
    f->dump_state = PROBE_DUMP_NOT_ATTEMPTED;
}

/* CurrentEL is the only system register the probe reads before
 * ExitBootServices. */
static void gather_firmware(const efi_system_table *st, probe_facts *f)
{
    f->have_preebs_el = 1;
    f->preebs_el_raw = probe_cpu_read_sysreg(PSR_CURRENTEL);
    f->fw_revision = st->firmware_revision;
    if (st->firmware_vendor != NULL)
        probe_sanitize_utf16(f->fw_vendor, sizeof f->fw_vendor, st->firmware_vendor,
                             PROBE_FW_VENDOR_UNITS);
}

static int guid_equal(const efi_guid *a, const efi_guid *b)
{
    return probe_memcmp(a, b, sizeof *a) == 0;
}

static void gather_config_tables(const efi_system_table *st, probe_facts *f, cfg_ptrs *p)
{
    static const efi_guid acpi20 = EFI_GUID_ACPI_20;
    static const efi_guid acpi10 = EFI_GUID_ACPI_10;
    static const efi_guid smbios = EFI_GUID_SMBIOS;
    static const efi_guid smbios3 = EFI_GUID_SMBIOS3;
    uint64_t n = st->number_of_table_entries;

    if (st->configuration_table == NULL)
        n = 0;
    if (n > PROBE_MAX_CFG_SCAN)
        n = PROBE_MAX_CFG_SCAN;
    f->cfg_count = (unsigned)n;
    for (uint64_t i = 0; i < n; i++) {
        const efi_configuration_table *t = &st->configuration_table[i];
        uint64_t ptr = (uint64_t)(uintptr_t)t->vendor_table;

        if (guid_equal(&t->vendor_guid, &acpi20)) {
            f->cfg_acpi20 = 1;
            if (p->acpi20 == 0)
                p->acpi20 = ptr;
        } else if (guid_equal(&t->vendor_guid, &acpi10)) {
            f->cfg_acpi10 = 1;
            if (p->acpi10 == 0)
                p->acpi10 = ptr;
        } else if (guid_equal(&t->vendor_guid, &smbios)) {
            f->cfg_smbios = 1;
            if (p->smbios == 0)
                p->smbios = ptr;
        } else if (guid_equal(&t->vendor_guid, &smbios3)) {
            f->cfg_smbios3 = 1;
            if (p->smbios3 == 0)
                p->smbios3 = ptr;
        }
    }
}

/* The framebuffer this image draws into is the one the firmware console is already
 * drawing into, chosen by probe_gop_select: the GOP installed on ConsoleOutHandle
 * when the system table identifies it, otherwise, when the UEFI Shell has replaced
 * that handle, the one framebuffer that every usable GOP agrees on. A Blt-only or
 * bitmask device, a range outside the UEFI memory map and a disagreement between
 * usable GOPs are never chosen. Returns the number of candidates examined (rule 1
 * stops at the one it chooses); *status receives the reason when none qualifies. */
static unsigned gather_gop(efi_system_table *st, probe_facts *f, probe_fb *fb, int *status)
{
    static const efi_guid gop_guid = EFI_GUID_GOP;
    efi_boot_services *bs = st->boot_services;
    efi_handle handles[PROBE_MAX_GOP_HANDLES];
    probe_gop_candidate cand[PROBE_MAX_GOP_HANDLES];
    probe_gop_choice choice;
    efi_uintn size = sizeof handles;
    size_t count = 0;

    *status = PROBE_GOP_ABSENT;
    if (EFI_IS_ERROR(bs->locate_handle(EFI_LOCATE_BY_PROTOCOL, &gop_guid, NULL, &size, handles)))
        return 0;
    for (size_t i = 0; i < size / sizeof(efi_handle) && i < PROBE_MAX_GOP_HANDLES; i++) {
        efi_gop *gop = NULL;
        const efi_gop_mode *m;
        const efi_gop_mode_info *info;

        if (EFI_IS_ERROR(bs->handle_protocol(handles[i], &gop_guid, (void **)&gop)) || gop == NULL ||
            gop->mode == NULL || gop->mode->info == NULL)
            continue;
        m = gop->mode;
        info = m->info;
        cand[count].is_console = handles[i] == st->console_out_handle;
        cand[count].base = m->frame_buffer_base;
        cand[count].size_bytes = m->frame_buffer_size;
        cand[count].width = info->horizontal_resolution;
        cand[count].height = info->vertical_resolution;
        cand[count].pixels_per_scanline = info->pixels_per_scan_line;
        cand[count].format = info->pixel_format;
        count++;
    }
    probe_gop_select(&g_memmap, cand, count, &choice);
    *status = choice.status;
    if (choice.status == PROBE_GOP_OK) {
        const probe_gop_candidate *c = &cand[choice.index];

        *fb = choice.fb;
        f->have_gop = 1;
        f->gop_width = c->width;
        f->gop_height = c->height;
        f->gop_stride_pixels = c->pixels_per_scanline;
        f->gop_format = c->format;
        f->gop_base = c->base;
        f->gop_size = c->size_bytes;
        f->gop_rule = choice.rule;
    }
    return (unsigned)choice.examined;
}

/* Takes the memory map that backs every table read and the dump. A map that
 * fails validation leaves g_memmap empty, so no table is then readable. */
static efi_uintn gather_memmap(efi_boot_services *bs, probe_facts *f, uint32_t *desc_version)
{
    efi_uintn size = sizeof g_memmap_buf;
    efi_uintn key = 0;
    efi_uintn desc_size = 0;
    efi_status status;

    probe_memset(&g_memmap, 0, sizeof g_memmap);
    status = bs->get_memory_map(&size, (efi_memory_descriptor *)g_memmap_buf, &key, &desc_size,
                                desc_version);
    if (EFI_IS_ERROR(status))
        return 0;
    if (probe_memmap_init(&g_memmap, g_memmap_buf, size, sizeof g_memmap_buf, desc_size) !=
        PROBE_MM_OK) {
        probe_memset(&g_memmap, 0, sizeof g_memmap);
        return size;
    }
    f->memmap_ok = 1;
    f->memmap_count = (unsigned)g_memmap.count;
    f->memmap_desc_size = (unsigned)g_memmap.desc_size;
    f->have_ram = probe_memmap_ram_bounds(&g_memmap, &f->ram_lo, &f->ram_hi) != 0;
    return size;
}

static void gather_acpi(probe_facts *f, const cfg_ptrs *p, const probe_phys_ops *ops, void *ctx)
{
    uint64_t rsdp = p->acpi20 != 0 ? p->acpi20 : p->acpi10;

    (void)probe_acpi_scan_run(&g_scan, ops, ctx, rsdp);
    if (rsdp == 0)
        g_scan.rsdp_status = PROBE_ACPI_ABSENT;

    f->rsdp_status = g_scan.rsdp_status;
    f->rsdp_revision = g_scan.rsdp_revision;
    f->root_is_xsdt = g_scan.root_is_xsdt;
    f->root_status = g_scan.root_status;
    f->acpi_ok = g_scan.n_ok;
    f->acpi_rejected = g_scan.n_rejected;
    f->acpi_dup = g_scan.n_dup;
    f->fadt_valid = g_scan.fadt.valid;
    f->fadt_revision = g_scan.fadt.revision;
    f->fadt_minor = g_scan.fadt.minor;
    f->has_arm_boot_arch = g_scan.fadt.has_arm_boot_arch;
    f->arm_boot_arch = g_scan.fadt.arm_boot_arch;
    f->psci_conduit = PROBE_PSCI_NONE;
    if (g_scan.fadt.valid && g_scan.fadt.has_arm_boot_arch)
        f->psci_conduit = probe_psci_conduit_from_fadt(g_scan.fadt.revision, g_scan.fadt.length,
                                                       g_scan.fadt.arm_boot_arch);
}

static void gather_smbios(probe_facts *f, const cfg_ptrs *p, const probe_phys_ops *ops, void *ctx)
{
    int rc = -1;

    if (p->smbios3 != 0)
        rc = probe_smbios_locate(&g_smbios, ops, ctx, p->smbios3, 1);
    if (rc != 0 && p->smbios != 0)
        rc = probe_smbios_locate(&g_smbios, ops, ctx, p->smbios, 0);
    if (rc != 0 && p->smbios3 == 0 && p->smbios == 0)
        (void)probe_smbios_locate(&g_smbios, ops, ctx, 0, 0);

    f->smbios_status = g_smbios.status;
    f->smbios_is64 = g_smbios.is64;
    f->smbios_major = g_smbios.major;
    f->smbios_minor = g_smbios.minor;
    f->smbios_table_len = g_smbios.table_len;
}

#if PROBE_PSCI
/* The PSCI variant picks its CPU_ON target from the MADT, read through the
 * same memory-map-checked path as every other table. */
static void gather_cpu_list(const probe_phys_ops *ops, void *ctx)
{
    probe_memset(&g_cpus, 0, sizeof g_cpus);
    for (unsigned i = 0; i < g_scan.count; i++) {
        const probe_acpi_entry *e = &g_scan.entries[i];

        if (e->status != PROBE_ACPI_OK || probe_memcmp(e->sig, "APIC", 4) != 0)
            continue;
        if (e->length <= sizeof g_madt && ops->read(ctx, e->addr, g_madt, e->length) == 0)
            (void)probe_madt_parse(g_madt, e->length, &g_cpus);
        break;
    }
}
#endif

/* Chooses the page layout from a preview of the report. The report has the
 * same number of lines whatever the register values are, so the layout can be
 * fixed now and the post-exit code never has to decide anything. */
static void plan_layout(probe_facts *f, const probe_fb *fb)
{
    probe_regs preview;
    probe_layout layout;
    unsigned count;

    probe_regs_init(&preview);
    probe_report_build(&g_report, f, &preview);
    if (g_report.overflowed)
        return;
    count = g_report.count;
    if (probe_layout_choose(fb->width, fb->height, count, &layout) != PROBE_LAYOUT_OK ||
        probe_layout_fits(&layout, fb->width, fb->height) != 0)
        return;

    g_layout = layout;
    f->have_layout = 1;
    f->layout_scale = layout.scale;
    f->layout_cols = layout.cols;
    f->layout_rows = layout.body_rows;
    f->layout_pages = layout.pages;

    probe_report_build(&g_report, f, &preview);
    if (g_report.overflowed || g_report.count != count)
        f->have_layout = 0;
}

static void run_dump(efi_handle image, efi_system_table *st, const probe_phys_ops *ops,
                     void *ops_ctx, efi_uintn map_size, uint32_t desc_version, probe_facts *f)
{
    probe_dump_ctx c;
    probe_dump_result r;

    probe_memset(&c, 0, sizeof c);
    c.st = st;
    c.image = image;
    c.ops = ops;
    c.ops_ctx = ops_ctx;
    c.scan = &g_scan;
    c.smbios = &g_smbios;
    c.memmap = &g_memmap;
    c.memmap_raw = g_memmap_buf;
    c.memmap_size = map_size;
    c.memmap_desc_version = desc_version;
    c.facts = f;
    c.bounce = g_bounce;
    c.text = g_text;
    probe_dump_run(&c, &r);
    f->dump_state = r.state;
    f->dump_run = r.run;
    f->dump_files = r.files;
    f->dump_bytes = r.bytes;
}

static void print_pre_ebs_summary(const probe_facts *f, unsigned gop_candidates, int gop_status)
{
    con_line l;

    line_begin(&l, "BEGIN");
    line_str(&l, "BUILD", f->build_id);
    line_str(&l, "VARIANT", f->variant_psci ? "PSCI" : "DEFAULT");
    line_str(&l, "RESET", f->reset_enabled ? "ON" : "OFF");
    line_dec(&l, "FAST", PROBE_FAST_TEST ? 1 : 0);
    line_end(&l);

    line_begin(&l, "PREEBS");
    line_hex(&l, "CURRENTEL", f->preebs_el_raw, 16);
    line_end(&l);

    line_begin(&l, "FW");
    line_hex(&l, "REV", f->fw_revision, 8);
    line_str(&l, "VENDOR", f->fw_vendor);
    line_end(&l);

    line_begin(&l, "GOP");
    if (f->have_gop) {
        line_dec(&l, "W", f->gop_width);
        line_dec(&l, "H", f->gop_height);
        line_dec(&l, "STRIDE", f->gop_stride_pixels);
        line_str(&l, "FMT", pixel_format_name(f->gop_format));
        line_hex(&l, "BASE", f->gop_base, 16);
        line_hex(&l, "SIZE", f->gop_size, 8);
        line_str(&l, "SEL", probe_gop_rule_name(f->gop_rule));
    } else {
        line_str(&l, "STATE", "NONE");
        line_str(&l, "WHY", probe_gop_status_name(gop_status));
    }
    line_dec(&l, "CANDIDATES", gop_candidates);
    line_end(&l);

    line_begin(&l, "LAYOUT");
    if (f->have_layout) {
        line_dec(&l, "SCALE", f->layout_scale);
        line_dec(&l, "COLS", f->layout_cols);
        line_dec(&l, "ROWS", f->layout_rows);
        line_dec(&l, "PAGES", f->layout_pages);
    } else {
        line_str(&l, "STATE", "NONE");
    }
    line_end(&l);

    line_begin(&l, "CFG");
    line_dec(&l, "N", f->cfg_count);
    line_dec(&l, "ACPI20", f->cfg_acpi20);
    line_dec(&l, "ACPI10", f->cfg_acpi10);
    line_dec(&l, "SMBIOS", f->cfg_smbios);
    line_dec(&l, "SMBIOS3", f->cfg_smbios3);
    line_end(&l);

    line_begin(&l, "ACPI");
    line_str(&l, "RSDP", probe_acpi_status_name(f->rsdp_status));
    line_dec(&l, "OK", f->acpi_ok);
    line_dec(&l, "REJ", f->acpi_rejected);
    line_dec(&l, "DUP", f->acpi_dup);
    line_str(&l, "PSCI", probe_psci_conduit_name((enum probe_psci_conduit)f->psci_conduit));
    line_end(&l);

    line_begin(&l, "SMBIOS");
    line_str(&l, "STATE", probe_smbios_status_name(f->smbios_status));
    line_end(&l);

    line_begin(&l, "MEMMAP");
    line_dec(&l, "OK", f->memmap_ok);
    line_dec(&l, "COUNT", f->memmap_count);
    line_dec(&l, "DESC", f->memmap_desc_size);
    line_end(&l);

    line_begin(&l, "DUMP");
    line_str(&l, "STATE", probe_dump_state_name(f->dump_state));
    line_dec(&l, "RUN", f->dump_run);
    line_dec(&l, "FILES", f->dump_files);
    line_dec(&l, "BYTES", f->dump_bytes);
    line_end(&l);
}

/* ---- ExitBootServices ---- */

static efi_status exit_boot_services_loop(efi_handle image, efi_system_table *st, probe_facts *f)
{
    efi_boot_services *bs = st->boot_services;
    uint8_t *buf = g_ebs_map_buf;
    efi_uintn cap = sizeof g_ebs_map_buf;
    efi_status status = EFI_ABORTED;

    for (unsigned attempt = 1; attempt <= PROBE_EBS_MAX_ATTEMPTS; attempt++) {
        efi_uintn size = cap;
        efi_uintn key = 0;
        efi_uintn desc_size = 0;
        uint32_t desc_version = 0;

        f->ebs_attempts = attempt;
        status = bs->get_memory_map(&size, (efi_memory_descriptor *)buf, &key, &desc_size,
                                    &desc_version);
        if (status == EFI_BUFFER_TOO_SMALL) {
            void *bigger = NULL;
            efi_uintn want = size + 8u * (desc_size != 0 ? desc_size : 48u);

            /* The allocation itself changes the map, hence the slack and the
             * fresh GetMemoryMap on the next iteration. */
            if (EFI_IS_ERROR(bs->allocate_pool(EFI_LOADER_DATA, want, &bigger)) || bigger == NULL)
                return status;
            buf = bigger;
            cap = want;
            continue;
        }
        if (EFI_IS_ERROR(status))
            return status;

        status = bs->exit_boot_services(image, key);
        if (!EFI_IS_ERROR(status))
            return EFI_SUCCESS;
        if (!probe_ebs_retry(status, attempt, PROBE_EBS_MAX_ATTEMPTS))
            return status;
    }
    return status;
}

/* ---- After ExitBootServices: display only ---- */

static uint64_t read_register(void *ctx, int reg)
{
    (void)ctx;
    return probe_cpu_read_sysreg(reg);
}

static void draw_page(unsigned page, int footer_kind, unsigned seconds)
{
    probe_payload header0;
    probe_payload header1;
    probe_payload footer;

    probe_report_header0(header0, &g_facts);
    probe_report_header1(header1, &g_regs, page + 1, g_layout.pages);
    probe_report_footer(footer, footer_kind, seconds);
    probe_draw_page(&g_fb, &g_layout, header0, header1, g_report.line, g_report.count, page,
                    footer);
}

static void draw_footer_only(int footer_kind, unsigned seconds)
{
    probe_payload footer;

    probe_report_footer(footer, footer_kind, seconds);
    probe_draw_footer(&g_fb, &g_layout, footer);
}

/* Rebuilds the body from the registers read so far and shows page one, so a
 * fault in the next tier leaves this tier on screen. */
static void show_progress(int footer_kind)
{
    probe_report_build(&g_report, &g_facts, &g_regs);
    draw_page(0, footer_kind, 0);
}

/* The only wait that reads no register: its length depends on the core and the
 * compiler, so it is used where reading the counter is not allowed (see
 * halt_not_el2) or its frequency is unknown, never to measure anything. */
static void spin_one_second(void)
{
    for (volatile uint64_t i = 0; i < PROBE_FALLBACK_SECOND_SPINS; i++)
        ;
}

__attribute__((noreturn)) static void halt_not_el2(void)
{
    probe_layout compact;
    unsigned pages;

    if (g_facts.psci.enabled)
        g_facts.psci.state = PROBE_PSCI_RUN_NOT_EL2;
    probe_report_build(&g_report, &g_facts, &g_regs);
    if (probe_layout_choose(g_fb.width, g_fb.height, g_report.count, &compact) ==
            PROBE_LAYOUT_OK &&
        probe_layout_fits(&compact, g_fb.width, g_fb.height) == 0)
        g_layout = compact;
    pages = g_layout.pages != 0 ? g_layout.pages : 1;
    /* No flush hook is installed here: this path reads CurrentEL and nothing
     * else, and cache maintenance would read CTR_EL0. */
    g_fb.flush = NULL;
    draw_page(0, PROBE_FOOTER_HALTED, 0);
    /* A small screen needs several pages even for this short report; the
     * header announces them, so they must all be shown. */
    if (pages > 1) {
        for (unsigned page = 1;; page = (page + 1) % pages) {
            for (unsigned s = 0; s < PROBE_PAGE_DWELL_S; s++)
                spin_one_second();
            draw_page(page, PROBE_FOOTER_HALTED, 0);
        }
    }
    probe_cpu_park();
}

static void wait_one_second(uint64_t freq)
{
    if (freq != 0) {
        uint64_t start = probe_cpu_counter();

        while (probe_cpu_counter() - start < freq)
            ;
    } else {
        spin_one_second();
    }
}

/* Shows every page in turn for PROBE_PAGE_DWELL_S seconds each. With reset
 * enabled the footer counts down and ResetSystem ends the probe; with
 * -DPROBE_NO_RESET=1 the loop never ends. */
__attribute__((noreturn)) static void final_display(void)
{
    uint64_t freq = probe_cpu_counter_freq();
    unsigned pages = g_layout.pages != 0 ? g_layout.pages : 1;
    unsigned shown = ~0u;

    for (unsigned sec = 0;; sec++) {
        unsigned page = (sec / PROBE_PAGE_DWELL_S) % pages;
        int kind = PROBE_FOOTER_HOLD;
        unsigned remaining = 0;

        if (g_facts.reset_enabled) {
            if (sec >= PROBE_RESET_COUNTDOWN_S)
                break;
            kind = PROBE_FOOTER_COUNTDOWN;
            remaining = PROBE_RESET_COUNTDOWN_S - sec;
        }
        if (page != shown) {
            draw_page(page, kind, remaining);
            shown = page;
        } else {
            draw_footer_only(kind, remaining);
        }
        wait_one_second(freq);
    }

    draw_footer_only(PROBE_FOOTER_RESETTING, 0);
    g_reset_system(EFI_RESET_COLD, EFI_SUCCESS, 0, NULL);
    draw_footer_only(PROBE_FOOTER_RESET_FAILED, 0);
    probe_cpu_park();
}

__attribute__((noreturn)) static void post_exit_boot_services(void)
{
    probe_regs_init(&g_regs);
    (void)probe_collect_el(&g_regs, read_register, NULL);
    if (g_regs.el != 2)
        halt_not_el2();

    g_fb.flush = probe_cpu_flush_dcache;
    g_fb.flush_ctx = NULL;

    show_progress(PROBE_FOOTER_STAGE0);
    (void)probe_collect_stage1(&g_regs, read_register, NULL);
    show_progress(PROBE_FOOTER_STAGE1);
    (void)probe_collect_stage2a(&g_regs, read_register, NULL);
    show_progress(PROBE_FOOTER_STAGE2A);

#if PROBE_PSCI
    show_progress(PROBE_FOOTER_PSCI);
    probe_psci_run(&g_facts.psci, g_facts.psci_conduit, &g_cpus, g_regs.value[PSR_MPIDR_EL1],
                   &g_psci_cell);
    probe_report_build(&g_report, &g_facts, &g_regs);
#endif
    final_display();
}

/* ---- Entry point ---- */

efi_status efi_main(efi_handle image, efi_system_table *st)
{
    efi_boot_services *bs = st->boot_services;
    probe_phys_ops ops = {ops_read, ops_covers};
    cfg_ptrs cfg = {0, 0, 0, 0};
    efi_uintn map_size;
    uint32_t desc_version = 0;
    unsigned gop_candidates;
    int gop_status;
    efi_status status;
    con_line l;
#if PROBE_TESTS_IMAGE
    tests_hook_ctx tctx;
#endif

    g_st = st;
    /* The default five-minute watchdog would reset a slow dump. */
    (void)bs->set_watchdog_timer(0, 0, 0, NULL);

    init_facts(&g_facts);
    gather_firmware(st, &g_facts);
    gather_config_tables(st, &g_facts, &cfg);
    /* The memory map comes first: a framebuffer is accepted only when the map
     * describes all of it. */
    map_size = gather_memmap(bs, &g_facts, &desc_version);
    gop_candidates = gather_gop(st, &g_facts, &g_fb, &gop_status);
    gather_acpi(&g_facts, &cfg, &ops, &g_memmap);
    gather_smbios(&g_facts, &cfg, &ops, &g_memmap);
#if PROBE_PSCI
    gather_cpu_list(&ops, &g_memmap);
#endif
    if (g_facts.have_gop)
        plan_layout(&g_facts, &g_fb);
    g_reset_system = st->runtime_services->reset_system;

    run_dump(image, st, &ops, &g_memmap, map_size, desc_version, &g_facts);
    print_pre_ebs_summary(&g_facts, gop_candidates, gop_status);

#if PROBE_TESTS_IMAGE
    tctx.st = st;
    tctx.image = image;
    tctx.facts = &g_facts;
    tctx.scan = &g_scan;
    tctx.memmap = &g_memmap;
    tctx.ops = &ops;
    tctx.ops_ctx = &g_memmap;
    tctx.fb = &g_fb;
    tctx.reset_system = g_reset_system;
    if (tests_hook_pre_ebs(&tctx) != 0) {
        line_begin(&l, "EBS");
        line_str(&l, "STATE", "SKIPPED");
        line_str(&l, "REASON", "TESTS REFUSED");
        line_end(&l);
        (void)bs->stall(10u * 1000000u);
        return EFI_ABORTED;
    }
#endif

    if (!probe_ebs_allowed(g_facts.have_gop, g_facts.have_layout, g_facts.memmap_ok)) {
        line_begin(&l, "EBS");
        line_str(&l, "STATE", "SKIPPED");
        line_str(&l, "REASON",
                 !g_facts.have_gop      ? "NO USABLE GOP"
                 : !g_facts.have_layout ? "NO LAYOUT"
                                        : "MEMORY MAP INVALID");
        line_end(&l);
        (void)bs->stall(10u * 1000000u);
        return EFI_ABORTED;
    }

    if (PROBE_PRE_EBS_STALL_S != 0) {
        line_begin(&l, "EBS");
        line_dec(&l, "IN_SECONDS", PROBE_PRE_EBS_STALL_S);
        line_end(&l);
        (void)bs->stall(PROBE_PRE_EBS_STALL_S * 1000000u);
    }
    line_begin(&l, "EBS");
    line_str(&l, "STATE", "CALLING");
    line_end(&l);

    status = exit_boot_services_loop(image, st, &g_facts);
    if (EFI_IS_ERROR(status)) {
        line_begin(&l, "EBS");
        line_str(&l, "STATE", "FAILED");
        line_hex(&l, "STATUS", status, 16);
        line_dec(&l, "ATTEMPTS", g_facts.ebs_attempts);
        line_end(&l);
        (void)bs->stall(10u * 1000000u);
        return status;
    }

#if PROBE_TESTS_IMAGE
    tests_hook_post_ebs(&tctx);
#else
    post_exit_boot_services();
#endif
}
