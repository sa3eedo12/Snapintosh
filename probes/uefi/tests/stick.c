/* SPDX-License-Identifier: MIT */
#include "stick.h"

#include "logic.h"

static const char *const state_names[STICK_STATE_COUNT] = {
    "OK",           "NOT ATTEMPTED", "REFUSED NO VOLUME", "REFUSED NOT USB", "REFUSED AMBIGUOUS",
    "REFUSED MALFORMED PATH", "NO RUN DIR", "REFUSED ARGS", "FILE EXISTS", "ERROR", "PARTIAL",
};

const char *tests_stick_state_name(int state)
{
    if (state < 0 || state >= STICK_STATE_COUNT)
        return "?";
    return state_names[state];
}

static void to_utf16(efi_char16 *dst, size_t cap, const char *src)
{
    size_t i = 0;

    for (; src[i] != 0 && i + 1 < cap; i++)
        dst[i] = (efi_char16)(uint8_t)src[i];
    dst[i] = 0;
}

static int select_volume(efi_system_table *st, efi_handle image, efi_file **root)
{
    efi_boot_services *bs = st->boot_services;
    efi_guid li_guid = EFI_GUID_LOADED_IMAGE;
    efi_guid dp_guid = EFI_GUID_DEVICE_PATH;
    efi_guid fs_guid = EFI_GUID_SIMPLE_FS;
    efi_loaded_image *li = NULL;
    uint8_t *dp = NULL;
    efi_simple_fs *fs = NULL;

    if (EFI_IS_ERROR(bs->handle_protocol(image, &li_guid, (void **)&li)) || li == NULL ||
        li->device_handle == NULL)
        return STICK_REFUSED_NO_VOLUME;
    if (EFI_IS_ERROR(bs->handle_protocol(li->device_handle, &dp_guid, (void **)&dp)) || dp == NULL)
        return STICK_REFUSED_NO_VOLUME;
    switch (probe_dp_classify(dp, TESTS_STICK_DP_MAX_LEN)) {
    case PROBE_DP_USB:
        break;
    case PROBE_DP_NO_USB:
        return STICK_REFUSED_NOT_USB;
    case PROBE_DP_AMBIGUOUS:
        return STICK_REFUSED_AMBIGUOUS;
    default:
        return STICK_REFUSED_MALFORMED;
    }
    if (EFI_IS_ERROR(bs->handle_protocol(li->device_handle, &fs_guid, (void **)&fs)) || fs == NULL)
        return STICK_REFUSED_NO_VOLUME;
    if (EFI_IS_ERROR(fs->open_volume(fs, root)) || *root == NULL)
        return STICK_REFUSED_NO_VOLUME;
    return STICK_OK;
}

/* Opens an existing directory for writing. Never creates it. */
static int open_dir(efi_file *parent, const char *name, efi_file **out)
{
    efi_char16 n16[16];
    efi_status s;

    to_utf16(n16, sizeof n16 / sizeof n16[0], name);
    s = parent->open(parent, out, n16, EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE, 0);
    if (s == EFI_NOT_FOUND)
        return STICK_NO_RUN_DIR;
    return EFI_IS_ERROR(s) || *out == NULL ? STICK_ERROR : STICK_OK;
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

/* Creates one file only if its name is free, writes it and flushes it. */
static int put_file(efi_file *dir, const tests_file *f)
{
    efi_char16 n16[TESTS_STICK_NAME_MAX + 1];
    efi_file *h = NULL;
    efi_status s;
    int ok;

    to_utf16(n16, sizeof n16 / sizeof n16[0], f->name);
    s = dir->open(dir, &h, n16, EFI_FILE_MODE_READ, 0);
    if (!EFI_IS_ERROR(s)) {
        if (h != NULL)
            h->close(h);
        return STICK_EXISTS;
    }
    if (s != EFI_NOT_FOUND)
        return STICK_ERROR;
    h = NULL;
    s = dir->open(dir, &h, n16, EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE | EFI_FILE_MODE_CREATE, 0);
    if (EFI_IS_ERROR(s) || h == NULL)
        return STICK_ERROR;
    ok = write_all(h, f->data, f->len) == 0 && !EFI_IS_ERROR(h->flush(h));
    /* Close writes out whatever the flush left cached. */
    if (EFI_IS_ERROR(h->close(h)))
        ok = 0;
    return ok ? STICK_OK : STICK_ERROR;
}

int tests_stick_write(efi_system_table *st, efi_handle image, unsigned run,
                      const tests_file *files, unsigned n, unsigned *written)
{
    efi_file *root = NULL, *base = NULL, *dir = NULL;
    char name[8];
    probe_sb sb;
    uint64_t total = 0;
    int state, result = STICK_OK;

    *written = 0;
    if (run < 1u || run > 99u || n == 0 || n > TESTS_STICK_MAX_FILES)
        return STICK_REFUSED_ARGS;
    for (unsigned i = 0; i < n; i++) {
        if (files[i].name == NULL || probe_strlen(files[i].name) == 0 ||
            probe_strlen(files[i].name) > TESTS_STICK_NAME_MAX ||
            files[i].len > TESTS_STICK_MAX_FILE_BYTES || (files[i].len != 0 && files[i].data == NULL))
            return STICK_REFUSED_ARGS;
        total += files[i].len;
    }
    if (total > TESTS_STICK_MAX_TOTAL_BYTES)
        return STICK_REFUSED_ARGS;

    state = select_volume(st, image, &root);
    if (state != STICK_OK)
        return state;
    state = open_dir(root, "SP11DUMP", &base);
    if (state == STICK_OK) {
        probe_sb_init(&sb, name, sizeof name);
        probe_sb_puts(&sb, "RUN");
        if (run < 10u)
            probe_sb_putc(&sb, '0');
        probe_sb_dec(&sb, run);
        state = open_dir(base, name, &dir);
    }
    if (state != STICK_OK) {
        result = state;
    } else {
        for (unsigned i = 0; i < n; i++) {
            int rc = put_file(dir, &files[i]);

            if (rc != STICK_OK) {
                result = *written != 0 ? STICK_PARTIAL : rc;
                break;
            }
            (*written)++;
        }
    }
    {
        efi_file *handles[3] = {dir, base, root};
        int sync_failed = 0;

        /* The directory entries and the volume root are what make the new files
         * findable after the reset; if either cannot be flushed or closed, the
         * files may be absent, so success must not be reported. */
        for (int i = 0; i < 3; i++) {
            if (handles[i] == NULL)
                continue;
            sync_failed |= EFI_IS_ERROR(handles[i]->flush(handles[i]));
            sync_failed |= EFI_IS_ERROR(handles[i]->close(handles[i]));
        }
        if (sync_failed && result == STICK_OK)
            result = STICK_PARTIAL;
    }
    return result;
}
