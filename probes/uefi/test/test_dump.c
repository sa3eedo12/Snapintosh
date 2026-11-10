/* SPDX-License-Identifier: MIT */
/* Host-native tests of the USB dump's safety rules (probes/uefi/dump.c).
 *
 * Checked here: the three caps (files, bytes per file, bytes overall) at their
 * exact boundaries; that a cap skips one file and reports PARTIAL instead of
 * ending the run; that nothing already on the stick is ever opened for
 * writing; that a UEFI error ends the dump instead of being papered over;
 * that every handle the dump opens is closed again; and what probe_dump_run
 * reports for each of those outcomes and for each refusal.
 *
 * dump.c is #included instead of linked because the functions under test are
 * static, and the caps cannot be reached through probe_dump_run with real
 * firmware (a laptop's ACPI tables total a few MiB). For the same reason the
 * build line in tools/test-uefi-probe-logic.c lists this file and not dump.c.
 *
 * The firmware side is an in-memory model of EFI_FILE_PROTOCOL: a flat table
 * of paths, handles that must be closed again, and counters. File contents are
 * not modelled, only how many bytes reached which path. Error injection points
 * are 1-based call numbers, so "the second write fails" is exact. The model
 * serves both the direct tests of put_buffer/put_phys (a bare directory
 * handle) and the end-to-end tests of probe_dump_run (a loaded image, a device
 * path, a simple file system and a root directory).
 */
#include "../dump.c"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "harness.h"

#define PHYS_BASE 0x80000000ull
#define MOCK_NODES 512u
#define MOCK_PATH 160u

/* The cap tests build their files from these two relations. */
_Static_assert(PROBE_DUMP_MAX_TOTAL_BYTES == 2u * PROBE_DUMP_MAX_FILE_BYTES,
               "the total-cap tests compose files from the per-file cap");
_Static_assert(MOCK_NODES > PROBE_DUMP_MAX_FILES + 8u, "the model must outlast the file cap");
_Static_assert(PROBE_DUMP_MAX_FILE_BYTES >= PROBE_ACPI_MAX_TABLE_LEN,
               "a table the scan accepts must not exceed the per-file cap, or it is always skipped");
/* The file cap is a backstop that a valid scan cannot reach: RSDP, every ACPI
 * table, the SMBIOS entry point and table, memmap.raw and three text files. If
 * the ACPI limit is ever raised past this bound a real scan could trip it. */
_Static_assert(1u + PROBE_ACPI_MAX_ENTRIES + 2u + 1u + 3u <= PROBE_DUMP_MAX_FILES,
               "a full ACPI scan plus the fixed files must fit under the file cap");

typedef struct {
    char path[MOCK_PATH];
    int is_dir;
    uint64_t bytes;
} mock_node;

typedef struct {
    mock_node node[MOCK_NODES];
    unsigned count;
    unsigned creates;      /* CREATE opens of a regular file, new or existing */
    unsigned writes;       /* write calls, including the injected failure */
    unsigned flushes;
    unsigned closes;
    unsigned open_handles; /* opened and not yet closed */
    unsigned volume_opens;
    unsigned fail_write_at;
    unsigned short_write_at;
    unsigned fail_flush_at;
    unsigned fail_close_at;
    efi_status existence_check_error; /* instead of NOT_FOUND for an absent path */
    efi_status create_error;
} mock_fs;

typedef struct {
    efi_file file; /* first member: the dump only ever holds &file */
    mock_fs *fs;
    char path[MOCK_PATH];
    int node;
} mock_handle;

typedef struct {
    uint64_t next; /* where a read that continues the previous one starts */
    unsigned calls;
    unsigned fail_at;
    uint64_t bytes;
    size_t largest;
    unsigned starts; /* reads that did not continue the previous one */
} phys_log;

typedef struct {
    mock_fs fs;
    phys_log phys;
    probe_phys_ops ops;
    probe_dump_ctx ctx;
    probe_dump_result res;
    dump_state d;
    efi_system_table st;
    efi_boot_services bs;
    efi_loaded_image li;
    efi_simple_fs sfs;
    char image_token;
    char device_token;
    int no_filesystem;
    uint8_t dp[64];
    probe_acpi_scan scan;
    probe_smbios smb;
    probe_memmap mm;
    probe_facts facts;
    uint8_t memmap_raw[96];
    uint8_t bounce[PROBE_DUMP_BOUNCE_BYTES];
    char text[PROBE_DUMP_TEXT_BYTES];
} rig;

static rig *g_rig; /* the firmware callbacks carry no context pointer */
static uint8_t *g_big;

/* Messaging node, USB (6 bytes), then end of entire path. */
static const uint8_t k_usb_path[] = {0x03, 0x05, 0x06, 0x00, 0x00, 0x00, 0x7F, 0xFF, 0x04, 0x00};
/* Messaging node, NVMe namespace (16 bytes), then end of entire path. */
static const uint8_t k_nvme_path[] = {0x03, 0x17, 0x10, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00,
                                      0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7F, 0xFF,
                                      0x04, 0x00};
static const uint8_t k_usb_and_nvme_path[] = {
    0x03, 0x05, 0x06, 0x00, 0x00, 0x00, 0x03, 0x17, 0x10, 0x00, 0x01, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7F, 0xFF, 0x04, 0x00};
/* A node whose length is shorter than its own header. */
static const uint8_t k_bad_path[] = {0x03, 0x05, 0x02, 0x00, 0x7F, 0xFF, 0x04, 0x00};

/* ---- The model of EFI_FILE_PROTOCOL ---- */

static void narrow(char *dst, size_t cap, const efi_char16 *src)
{
    size_t i = 0;

    for (; src[i] != 0 && i + 1 < cap; i++)
        dst[i] = (char)src[i];
    dst[i] = 0;
}

static int mock_find(const mock_fs *fs, const char *path)
{
    for (unsigned i = 0; i < fs->count; i++)
        if (strcmp(fs->node[i].path, path) == 0)
            return (int)i;
    return -1;
}

static uint64_t file_bytes(const mock_fs *fs, const char *path)
{
    int n = mock_find(fs, path);

    return n < 0 ? UINT64_MAX : fs->node[n].bytes;
}

static unsigned count_files(const mock_fs *fs, const char *prefix)
{
    unsigned n = 0;

    for (unsigned i = 0; i < fs->count; i++)
        if (!fs->node[i].is_dir && strncmp(fs->node[i].path, prefix, strlen(prefix)) == 0)
            n++;
    return n;
}

static uint64_t sum_file_bytes(const mock_fs *fs)
{
    uint64_t sum = 0;

    for (unsigned i = 0; i < fs->count; i++)
        if (!fs->node[i].is_dir)
            sum += fs->node[i].bytes;
    return sum;
}

static void mock_seed(mock_fs *fs, const char *path, int is_dir, uint64_t bytes)
{
    mock_node *n;

    if (fs->count == MOCK_NODES) {
        failed(__LINE__, "the model is full");
        return;
    }
    n = &fs->node[fs->count++];
    snprintf(n->path, sizeof n->path, "%s", path);
    n->is_dir = is_dir;
    n->bytes = bytes;
}

static efi_file *mock_handle_new(mock_fs *fs, const char *path, int node);

static efi_status mock_open(efi_file *self, efi_file **out, const efi_char16 *name, uint64_t mode,
                            uint64_t attributes)
{
    mock_handle *dir = (mock_handle *)self;
    mock_fs *fs = dir->fs;
    char path[MOCK_PATH];
    size_t used = strlen(dir->path);
    int is_dir = (attributes & EFI_FILE_DIRECTORY) != 0;
    int n;

    memcpy(path, dir->path, used);
    if (used != 0)
        path[used++] = '/';
    narrow(path + used, sizeof path - used, name);

    if ((mode & EFI_FILE_MODE_CREATE) && !is_dir)
        fs->creates++;
    n = mock_find(fs, path);
    if (n < 0) {
        if (!(mode & EFI_FILE_MODE_CREATE))
            return fs->existence_check_error ? fs->existence_check_error : EFI_NOT_FOUND;
        if (fs->create_error)
            return fs->create_error;
        if (fs->count == MOCK_NODES)
            return EFI_OUT_OF_RESOURCES;
        mock_seed(fs, path, is_dir, 0);
        n = (int)fs->count - 1;
    }
    *out = mock_handle_new(fs, path, n);
    return EFI_SUCCESS;
}

static efi_status mock_close(efi_file *self)
{
    mock_handle *h = (mock_handle *)self;
    mock_fs *fs = h->fs;

    fs->open_handles--;
    fs->closes++;
    free(h);
    /* The handle is gone either way: UEFI closes it in all cases. */
    return fs->fail_close_at == fs->closes ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}

static efi_status mock_write(efi_file *self, efi_uintn *size, const void *buffer)
{
    mock_handle *h = (mock_handle *)self;
    mock_fs *fs = h->fs;

    (void)buffer; /* contents are not modelled */
    fs->writes++;
    if (fs->fail_write_at == fs->writes) {
        *size = 0;
        return EFI_DEVICE_ERROR;
    }
    if (fs->short_write_at == fs->writes)
        *size /= 2;
    fs->node[h->node].bytes += *size;
    return EFI_SUCCESS;
}

static efi_status mock_flush(efi_file *self)
{
    mock_fs *fs = ((mock_handle *)self)->fs;

    fs->flushes++;
    return fs->fail_flush_at == fs->flushes ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}

static efi_file *mock_handle_new(mock_fs *fs, const char *path, int node)
{
    mock_handle *h = xalloc(sizeof *h);

    h->file.open = mock_open;
    h->file.close = mock_close;
    h->file.write = mock_write;
    h->file.flush = mock_flush;
    h->fs = fs;
    h->node = node;
    snprintf(h->path, sizeof h->path, "%s", path);
    fs->open_handles++;
    return &h->file;
}

/* ---- Firmware services around probe_dump_run ---- */

static int mock_phys_read(void *ctx, uint64_t addr, void *dst, size_t len)
{
    phys_log *log = ctx;

    log->calls++;
    if (log->fail_at == log->calls)
        return -1;
    if (addr != log->next)
        log->starts++;
    log->next = addr + len;
    log->bytes += len;
    if (len > log->largest)
        log->largest = len;
    memset(dst, 0xA5, len);
    return 0;
}

static efi_status mock_handle_protocol(efi_handle handle, const efi_guid *guid, void **iface)
{
    static const efi_guid li_guid = EFI_GUID_LOADED_IMAGE;
    static const efi_guid dp_guid = EFI_GUID_DEVICE_PATH;
    static const efi_guid fs_guid = EFI_GUID_SIMPLE_FS;
    rig *r = g_rig;

    if (handle == &r->image_token && memcmp(guid, &li_guid, sizeof li_guid) == 0) {
        *iface = &r->li;
        return EFI_SUCCESS;
    }
    if (handle != NULL && handle == r->li.device_handle) {
        if (memcmp(guid, &dp_guid, sizeof dp_guid) == 0) {
            *iface = r->dp;
            return EFI_SUCCESS;
        }
        if (memcmp(guid, &fs_guid, sizeof fs_guid) == 0 && !r->no_filesystem) {
            *iface = &r->sfs;
            return EFI_SUCCESS;
        }
    }
    return EFI_UNSUPPORTED;
}

static efi_status mock_open_volume(efi_simple_fs *self, efi_file **root)
{
    (void)self;
    g_rig->fs.volume_opens++;
    *root = mock_handle_new(&g_rig->fs, "", -1);
    return EFI_SUCCESS;
}

static rig *rig_new(void)
{
    rig *r = xalloc(sizeof *r);

    g_rig = r;
    r->ops.read = mock_phys_read;
    r->phys.next = PHYS_BASE;

    r->bs.handle_protocol = mock_handle_protocol;
    r->sfs.open_volume = mock_open_volume;
    r->li.device_handle = &r->device_token;
    r->st.hdr.revision = 0x0002000Au;
    r->st.boot_services = &r->bs;
    memcpy(r->dp, k_usb_path, sizeof k_usb_path);

    r->scan.rsdp_status = PROBE_ACPI_OK;
    r->scan.rsdp_addr = PHYS_BASE - 0x1000u;
    r->scan.rsdp_length = 36;
    r->smb.status = PROBE_SMBIOS_NULL_PTR;
    st32(r->memmap_raw, 7);
    st64(r->memmap_raw + 8, PHYS_BASE);
    st64(r->memmap_raw + 24, 0x100);
    st32(r->memmap_raw + 48, 9);
    st64(r->memmap_raw + 56, 0x90000000ull);
    st64(r->memmap_raw + 72, 0x10);
    CHECK_EQ(probe_memmap_init(&r->mm, r->memmap_raw, sizeof r->memmap_raw, sizeof r->memmap_raw, 48),
             PROBE_MM_OK);
    snprintf(r->facts.build_id, sizeof r->facts.build_id, "TESTBUILD001");

    r->ctx.st = &r->st;
    r->ctx.image = &r->image_token;
    r->ctx.ops = &r->ops;
    r->ctx.ops_ctx = &r->phys;
    r->ctx.scan = &r->scan;
    r->ctx.smbios = &r->smb;
    r->ctx.memmap = &r->mm;
    r->ctx.memmap_raw = r->memmap_raw;
    r->ctx.memmap_size = sizeof r->memmap_raw;
    r->ctx.memmap_desc_version = 1;
    r->ctx.facts = &r->facts;
    r->ctx.bounce = r->bounce;
    r->ctx.text = r->text;

    r->d.c = &r->ctx;
    r->d.res = &r->res;
    return r;
}

/* A rig for the direct tests: put_buffer and put_phys write into a bare directory. */
static rig *rig_with_dir(void)
{
    rig *r = rig_new();

    r->d.run = mock_handle_new(&r->fs, "", -1);
    return r;
}

static void rig_free(rig *r)
{
    if (r->d.run != NULL)
        r->d.run->close(r->d.run);
    CHECK_EQ(r->fs.open_handles, 0); /* every handle the dump opened was closed again */
    free(r);
}

static void add_table(rig *r, const char *sig, const char *name, uint32_t length)
{
    probe_acpi_entry *e = &r->scan.entries[r->scan.count++];

    e->addr = PHYS_BASE + 0x100000ull * r->scan.count;
    e->length = length;
    e->revision = 2;
    e->origin = PROBE_ORIGIN_ROOT_ENTRY;
    e->status = PROBE_ACPI_OK;
    memcpy(e->sig, sig, 4);
    snprintf(e->name, sizeof e->name, "%s", name);
}

/* ---- The caps, through both ways of writing a file ---- */

typedef void (*put_fn)(rig *r, const char *name, uint64_t len);

static void put_by_buffer(rig *r, const char *name, uint64_t len)
{
    put_buffer(&r->d, name, g_big, (size_t)len);
}

static void put_by_phys(rig *r, const char *name, uint64_t len)
{
    r->phys.next = PHYS_BASE;
    put_phys(&r->d, name, PHYS_BASE, (uint32_t)len);
}

static void caps_file_count(put_fn put)
{
    rig *r = rig_with_dir();
    char name[16];

    for (unsigned i = 0; i < PROBE_DUMP_MAX_FILES; i++) {
        snprintf(name, sizeof name, "F%03u", i);
        put(r, name, 1);
    }
    CHECK_EQ(r->res.files, PROBE_DUMP_MAX_FILES);
    CHECK_EQ(r->res.bytes, PROBE_DUMP_MAX_FILES);
    CHECK(!r->d.incomplete); /* exactly at the cap is still a complete dump */
    CHECK(!r->d.write_failed);

    put(r, "TOO-MANY-1", 1);
    put(r, "TOO-MANY-2", 1);
    CHECK(r->d.incomplete);
    CHECK(!r->d.write_failed); /* a cap is a skip, not an error */
    CHECK_EQ(r->res.files, PROBE_DUMP_MAX_FILES);
    CHECK_EQ(r->fs.count, PROBE_DUMP_MAX_FILES); /* nothing past the cap was created */
    CHECK_EQ(mock_find(&r->fs, "TOO-MANY-1"), -1);
    rig_free(r);
}

static void caps_file_size(put_fn put)
{
    rig *r = rig_with_dir();

    put(r, "AT-THE-CAP", PROBE_DUMP_MAX_FILE_BYTES);
    CHECK(!r->d.incomplete);
    CHECK_EQ(r->res.files, 1);
    CHECK_EQU(r->res.bytes, PROBE_DUMP_MAX_FILE_BYTES);
    CHECK_EQU(file_bytes(&r->fs, "AT-THE-CAP"), PROBE_DUMP_MAX_FILE_BYTES);
    rig_free(r);

    r = rig_with_dir();
    put(r, "ONE-BYTE-OVER", PROBE_DUMP_MAX_FILE_BYTES + 1u);
    CHECK(r->d.incomplete);
    CHECK(!r->d.write_failed);
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->res.bytes, 0);
    CHECK_EQ(r->fs.count, 0); /* not even an empty file is left behind */
    CHECK_EQ(r->fs.writes, 0);
    CHECK_EQ(r->phys.calls, 0); /* and no firmware memory was read for it */
    rig_free(r);
}

static void caps_total(put_fn put)
{
    rig *r = rig_with_dir();

    put(r, "A", PROBE_DUMP_MAX_FILE_BYTES);
    put(r, "B", PROBE_DUMP_MAX_FILE_BYTES - 1u);
    put(r, "C", 1);
    CHECK_EQ(r->res.files, 3);
    CHECK_EQU(r->res.bytes, PROBE_DUMP_MAX_TOTAL_BYTES); /* the budget is used exactly */
    CHECK_EQU(sum_file_bytes(&r->fs), PROBE_DUMP_MAX_TOTAL_BYTES);
    CHECK(!r->d.incomplete);

    put(r, "D", 1);
    CHECK(r->d.incomplete);
    CHECK_EQ(r->res.files, 3);
    CHECK_EQU(r->res.bytes, PROBE_DUMP_MAX_TOTAL_BYTES);
    CHECK_EQ(mock_find(&r->fs, "D"), -1);
    rig_free(r);

    /* A skipped file reserves nothing: later files that still fit are written. */
    r = rig_with_dir();
    put(r, "LARGE", PROBE_DUMP_MAX_FILE_BYTES);
    put(r, "MEDIUM", 4u << 20);
    put(r, "SKIPPED", PROBE_DUMP_MAX_FILE_BYTES);
    CHECK(r->d.incomplete);
    CHECK_EQ(mock_find(&r->fs, "SKIPPED"), -1);
    put(r, "SMALL", 4u << 20);
    CHECK(mock_find(&r->fs, "SMALL") >= 0);
    CHECK_EQ(r->res.files, 3);
    CHECK_EQU(r->res.bytes, (16u << 20) + (4u << 20) + (4u << 20));
    CHECK(r->d.incomplete); /* the shortfall stays reported */
    rig_free(r);
}

/* The bounce buffer bounds every firmware read, whatever the table size. */
static void phys_copy_is_bounded(void)
{
    const uint32_t len = 3u * PROBE_DUMP_BOUNCE_BYTES + 1234u;
    rig *r = rig_with_dir();

    put_phys(&r->d, "TABLE", PHYS_BASE, len);
    CHECK_EQ(r->res.files, 1);
    CHECK_EQU(file_bytes(&r->fs, "TABLE"), len);
    CHECK_EQU(r->phys.bytes, len);
    CHECK_EQ(r->phys.calls, 4);
    CHECK(r->phys.largest <= PROBE_DUMP_BOUNCE_BYTES);
    CHECK_EQ(r->phys.starts, 0); /* every chunk continues where the last one ended */
    CHECK_EQU(r->phys.next, PHYS_BASE + len);
    rig_free(r);
}

void test_dump_caps(void)
{
    section("dump caps");
    g_big = xalloc(PROBE_DUMP_MAX_FILE_BYTES + 1u);
    for (int phys = 0; phys < 2; phys++) {
        put_fn put = phys ? put_by_phys : put_by_buffer;

        caps_file_count(put);
        caps_file_size(put);
        caps_total(put);
    }
    phys_copy_is_bounded();
    free(g_big);
    g_big = NULL;
}

/* ---- Nothing already on the stick is touched ---- */

void test_dump_overwrite(void)
{
    uint8_t data[100] = {0};
    rig *r;

    section("dump overwrite");
    r = rig_with_dir();
    mock_seed(&r->fs, "RSDP.bin", 0, 7);
    mock_seed(&r->fs, "DSDT.bin", 0, 9);
    put_buffer(&r->d, "RSDP.bin", data, sizeof data);
    put_phys(&r->d, "DSDT.bin", PHYS_BASE, 5000);
    CHECK(r->d.incomplete); /* a collision is reported, not hidden */
    CHECK(!r->d.write_failed);
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->res.bytes, 0);
    CHECK_EQ(r->fs.creates, 0); /* no CREATE open of an existing name */
    CHECK_EQ(r->fs.writes, 0);
    CHECK_EQ(r->phys.calls, 0); /* the second table's source was not even read */
    CHECK_EQU(file_bytes(&r->fs, "RSDP.bin"), 7);
    CHECK_EQU(file_bytes(&r->fs, "DSDT.bin"), 9);
    CHECK_EQ(r->fs.open_handles, 1); /* only the directory: the probing handles are closed */

    put_buffer(&r->d, "NEW.bin", data, sizeof data); /* one collision does not stop the dump */
    CHECK_EQ(r->res.files, 1);
    CHECK_EQ(r->fs.creates, 1);
    rig_free(r);

    /* If the existence check itself fails the name may be taken: do not create. */
    r = rig_with_dir();
    r->fs.existence_check_error = EFI_DEVICE_ERROR;
    put_buffer(&r->d, "NEW.bin", data, sizeof data);
    CHECK(r->d.write_failed);
    CHECK_EQ(r->fs.creates, 0);
    CHECK_EQ(r->fs.count, 0);
    put_buffer(&r->d, "OTHER.bin", data, sizeof data);
    CHECK_EQ(r->fs.creates, 0); /* and the dump stops there */
    CHECK_EQ(r->res.files, 0);
    rig_free(r);
}

/* ---- A UEFI error ends the dump ---- */

void test_dump_errors(void)
{
    uint8_t *data = xalloc(3u << 20);
    rig *r;

    section("dump errors");
    r = rig_with_dir();
    r->fs.fail_write_at = 2;
    put_buffer(&r->d, "FIRST", data, 10);
    put_buffer(&r->d, "SECOND", data, 20);
    put_buffer(&r->d, "THIRD", data, 30);
    CHECK(r->d.write_failed);
    CHECK_EQ(r->res.files, 1); /* the failed file is not counted */
    CHECK_EQ(r->res.bytes, 10);
    CHECK_EQ(r->fs.creates, 2); /* THIRD was never attempted */
    CHECK_EQ(mock_find(&r->fs, "THIRD"), -1);
    CHECK_EQ(r->fs.open_handles, 1); /* the failed file's handle was closed */
    rig_free(r);

    r = rig_with_dir();
    r->fs.short_write_at = 1;
    put_buffer(&r->d, "SHORT", data, 100);
    CHECK(r->d.write_failed); /* fewer bytes than asked is a failure, not a success */
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->res.bytes, 0);
    rig_free(r);

    r = rig_with_dir();
    r->fs.fail_flush_at = 1;
    put_buffer(&r->d, "UNFLUSHED", data, 100);
    CHECK(r->d.write_failed); /* data that was not flushed is not counted as written */
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->res.bytes, 0);
    rig_free(r);

    r = rig_with_dir();
    r->fs.create_error = EFI_VOLUME_FULL;
    put_buffer(&r->d, "NOSPACE", data, 100);
    put_buffer(&r->d, "NOSPACE2", data, 100);
    CHECK(r->d.write_failed);
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->fs.count, 0);
    rig_free(r);

    /* Failure in the middle of a multi-chunk file: the 1 MiB chunk loop stops. */
    r = rig_with_dir();
    r->fs.fail_write_at = 2;
    put_buffer(&r->d, "BIG", data, 3u << 20);
    CHECK(r->d.write_failed);
    CHECK_EQ(r->fs.writes, 2);
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->res.bytes, 0);
    CHECK_EQ(r->fs.open_handles, 1);
    rig_free(r);

    /* A failed read of firmware memory abandons the file and ends the dump. */
    r = rig_with_dir();
    r->phys.fail_at = 3;
    put_phys(&r->d, "TABLE", PHYS_BASE, 300000);
    put_buffer(&r->d, "AFTER", data, 10);
    CHECK(r->d.write_failed);
    CHECK_EQ(r->phys.calls, 3);
    CHECK_EQ(r->fs.writes, 2);
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(mock_find(&r->fs, "AFTER"), -1);
    CHECK_EQ(r->fs.open_handles, 1);
    rig_free(r);
    free(data);
}

/* ---- probe_dump_run, end to end ---- */

static void run_complete_dump(void)
{
    static const char *const expect[] = {"RSDP.bin",         "FACP.bin",   "DSDT.bin",
                                         "APIC.bin",         "smbios-ep.raw", "smbios-table.raw",
                                         "memmap.raw",       "memmap.txt", "tables.txt",
                                         "summary.txt"};
    rig *r = rig_new();
    char path[MOCK_PATH];

    add_table(r, "FACP", "FACP.bin", 276);
    add_table(r, "DSDT", "DSDT.bin", 200000);
    add_table(r, "APIC", "APIC.bin", 4000);
    r->smb.status = PROBE_SMBIOS_OK;
    r->smb.ep_len = 31;
    r->smb.table_addr = PHYS_BASE + 0x4000000ull;
    r->smb.table_len = 1500;
    probe_dump_run(&r->ctx, &r->res);

    CHECK_EQ(r->res.state, PROBE_DUMP_OK);
    CHECK_EQ(r->res.run, 1);
    CHECK_EQ(r->res.files, 10);
    CHECK_EQ(r->fs.volume_opens, 1);
    CHECK_EQ(r->fs.open_handles, 0); /* run directory, SP11DUMP and the root are closed */
    CHECK_EQ(r->fs.flushes, r->res.files + 3); /* every file, then three directories */
    CHECK_EQ(count_files(&r->fs, ""), 10);
    CHECK_EQ(count_files(&r->fs, "SP11DUMP/RUN01/"), 10); /* nothing is written elsewhere */
    for (size_t i = 0; i < sizeof expect / sizeof expect[0]; i++) {
        snprintf(path, sizeof path, "SP11DUMP/RUN01/%s", expect[i]);
        CHECK(mock_find(&r->fs, path) >= 0);
    }
    CHECK_EQU(file_bytes(&r->fs, "SP11DUMP/RUN01/DSDT.bin"), 200000);
    CHECK_EQU(file_bytes(&r->fs, "SP11DUMP/RUN01/RSDP.bin"), 36);
    CHECK_EQU(file_bytes(&r->fs, "SP11DUMP/RUN01/smbios-ep.raw"), 31);
    CHECK_EQU(file_bytes(&r->fs, "SP11DUMP/RUN01/memmap.raw"), sizeof r->memmap_raw);
    CHECK_EQU(r->res.bytes, sum_file_bytes(&r->fs)); /* the report matches what was written */
    CHECK_EQU(r->phys.bytes, 276u + 200000u + 4000u + 1500u); /* only declared lengths are read */
    CHECK_EQ(r->phys.starts, 4); /* three tables and the SMBIOS table, each read front to back */
    rig_free(r);
}

/* Two tables of nearly the per-file cap leave 8156 bytes of the 32 MiB: the
 * next table is skipped and the small files that describe the run still fit. */
static void run_byte_cap_gives_partial(void)
{
    rig *r = rig_new();

    add_table(r, "DSDT", "DSDT.bin", PROBE_DUMP_MAX_FILE_BYTES - 4096u);
    add_table(r, "SSDT", "SSDT.bin", PROBE_DUMP_MAX_FILE_BYTES - 4096u);
    add_table(r, "APIC", "APIC.bin", 16384);
    probe_dump_run(&r->ctx, &r->res);

    CHECK_EQ(r->res.state, PROBE_DUMP_PARTIAL); /* never a silent OK */
    CHECK_EQ(r->res.run, 1);
    CHECK_EQ(r->res.files, 7); /* RSDP, two tables, memmap.raw and three text files */
    CHECK_EQ(mock_find(&r->fs, "SP11DUMP/RUN01/APIC.bin"), -1);
    CHECK(mock_find(&r->fs, "SP11DUMP/RUN01/DSDT.bin") >= 0);
    CHECK(mock_find(&r->fs, "SP11DUMP/RUN01/SSDT.bin") >= 0);
    CHECK(mock_find(&r->fs, "SP11DUMP/RUN01/summary.txt") >= 0);
    CHECK(r->res.bytes <= PROBE_DUMP_MAX_TOTAL_BYTES);
    CHECK_EQU(r->res.bytes, sum_file_bytes(&r->fs));
    CHECK_EQ(r->fs.open_handles, 0);
    rig_free(r);
}

static void run_write_error_states(void)
{
    rig *r = rig_new();

    add_table(r, "FACP", "FACP.bin", 1000);
    add_table(r, "DSDT", "DSDT.bin", 1000);
    r->fs.fail_write_at = 1; /* the very first file */
    probe_dump_run(&r->ctx, &r->res);
    CHECK_EQ(r->res.state, PROBE_DUMP_ERROR); /* nothing usable was written */
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->fs.open_handles, 0);
    rig_free(r);

    r = rig_new();
    add_table(r, "FACP", "FACP.bin", 1000);
    add_table(r, "DSDT", "DSDT.bin", 1000);
    r->fs.fail_write_at = 3; /* RSDP and the first table are on the stick */
    probe_dump_run(&r->ctx, &r->res);
    CHECK_EQ(r->res.state, PROBE_DUMP_PARTIAL);
    CHECK_EQ(r->res.files, 2);
    CHECK_EQ(count_files(&r->fs, ""), 3); /* the abandoned third file is on the stick, uncounted */
    CHECK_EQ(r->fs.writes, 3); /* nothing is attempted after the error */
    CHECK_EQ(r->fs.open_handles, 0);
    rig_free(r);
}

/* The directory entries and the volume root are flushed and closed after the
 * last file, and ExitBootServices follows: a failure in any of those steps must
 * not leave DUMP OK behind, and neither must a file whose close failed. */
static void run_final_sync_failures(void)
{
    rig *r = rig_new();
    unsigned files;

    add_table(r, "FACP", "FACP.bin", 276);
    probe_dump_run(&r->ctx, &r->res);
    CHECK_EQ(r->res.state, PROBE_DUMP_OK);
    files = r->res.files;
    CHECK_EQ(r->fs.flushes, files + 3); /* every file, then run directory, SP11DUMP, root */
    CHECK_EQ(r->fs.closes, files + 3);
    rig_free(r);

    for (unsigned k = 1; k <= 3; k++) {
        r = rig_new();
        add_table(r, "FACP", "FACP.bin", 276);
        r->fs.fail_flush_at = files + k;
        probe_dump_run(&r->ctx, &r->res);
        CHECK_EQ(r->res.state, PROBE_DUMP_PARTIAL);
        CHECK_EQ(r->res.files, files); /* every file was written; their entries may not be */
        CHECK_EQ(r->fs.open_handles, 0);
        rig_free(r);

        r = rig_new();
        add_table(r, "FACP", "FACP.bin", 276);
        r->fs.fail_close_at = files + k;
        probe_dump_run(&r->ctx, &r->res);
        CHECK_EQ(r->res.state, PROBE_DUMP_PARTIAL);
        CHECK_EQ(r->res.files, files);
        CHECK_EQ(r->fs.open_handles, 0);
        rig_free(r);
    }

    /* A file that cannot be closed is a lost write: nothing after it is tried. */
    for (unsigned k = 1; k <= files; k++) {
        r = rig_new();
        add_table(r, "FACP", "FACP.bin", 276);
        r->fs.fail_close_at = k;
        probe_dump_run(&r->ctx, &r->res);
        CHECK_EQ(r->res.state, k == 1 ? PROBE_DUMP_ERROR : PROBE_DUMP_PARTIAL);
        CHECK_EQ(r->res.files, k - 1);
        CHECK_EQ(r->fs.open_handles, 0);
        rig_free(r);
    }
}

static void run_picks_the_first_free_slot(void)
{
    char path[MOCK_PATH];
    unsigned before;
    rig *r = rig_new();

    mock_seed(&r->fs, "SP11DUMP", 1, 0);
    mock_seed(&r->fs, "SP11DUMP/RUN01", 1, 0);
    mock_seed(&r->fs, "SP11DUMP/RUN01/RSDP.bin", 0, 7);
    mock_seed(&r->fs, "SP11DUMP/RUN02", 1, 0);
    add_table(r, "FACP", "FACP.bin", 276);
    probe_dump_run(&r->ctx, &r->res);
    CHECK_EQ(r->res.state, PROBE_DUMP_OK);
    CHECK_EQ(r->res.run, 3); /* earlier runs are kept */
    CHECK(mock_find(&r->fs, "SP11DUMP/RUN03/FACP.bin") >= 0);
    CHECK_EQU(file_bytes(&r->fs, "SP11DUMP/RUN01/RSDP.bin"), 7);
    CHECK_EQ(count_files(&r->fs, "SP11DUMP/RUN01/"), 1);
    CHECK_EQ(count_files(&r->fs, "SP11DUMP/RUN02/"), 0);
    rig_free(r);

    r = rig_new();
    mock_seed(&r->fs, "SP11DUMP", 1, 0);
    for (unsigned n = 1; n <= PROBE_DUMP_MAX_RUN; n++) {
        snprintf(path, sizeof path, "SP11DUMP/RUN%02u", n);
        mock_seed(&r->fs, path, 1, 0);
    }
    before = r->fs.count;
    add_table(r, "FACP", "FACP.bin", 276);
    probe_dump_run(&r->ctx, &r->res);
    CHECK_EQ(r->res.state, PROBE_DUMP_NO_RUN_SLOT);
    CHECK_EQ(r->res.run, 0);
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->fs.count, before); /* nothing is created on a full stick */
    CHECK_EQ(r->fs.open_handles, 0);
    rig_free(r);
}

static void run_refused(const uint8_t *path, size_t len, int expect)
{
    rig *r = rig_new();

    memset(r->dp, 0, sizeof r->dp);
    memcpy(r->dp, path, len);
    add_table(r, "FACP", "FACP.bin", 276);
    probe_dump_run(&r->ctx, &r->res);
    CHECK_EQ(r->res.state, expect);
    CHECK_EQ(r->res.files, 0);
    CHECK_EQ(r->fs.volume_opens, 0); /* a refused device is never opened */
    CHECK_EQ(r->fs.count, 0);
    CHECK_EQ(r->phys.calls, 0);
    rig_free(r);
}

static void run_refusals(void)
{
    rig *r;

    run_refused(k_nvme_path, sizeof k_nvme_path, PROBE_DUMP_REFUSED_NOT_USB);
    run_refused(k_usb_and_nvme_path, sizeof k_usb_and_nvme_path, PROBE_DUMP_REFUSED_AMBIGUOUS);
    run_refused(k_bad_path, sizeof k_bad_path, PROBE_DUMP_REFUSED_MALFORMED);

    r = rig_new();
    r->li.device_handle = NULL;
    probe_dump_run(&r->ctx, &r->res);
    CHECK_EQ(r->res.state, PROBE_DUMP_REFUSED_NO_VOLUME);
    CHECK_EQ(r->fs.volume_opens, 0);
    rig_free(r);

    r = rig_new();
    r->no_filesystem = 1;
    probe_dump_run(&r->ctx, &r->res);
    CHECK_EQ(r->res.state, PROBE_DUMP_REFUSED_NO_VOLUME);
    CHECK_EQ(r->fs.volume_opens, 0);
    rig_free(r);
}

void test_dump_run(void)
{
    section("dump run");
    run_complete_dump();
    run_byte_cap_gives_partial();
    run_write_error_states();
    run_final_sync_failures();
    run_picks_the_first_free_slot();
    run_refusals();
}
