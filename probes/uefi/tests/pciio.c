/* SPDX-License-Identifier: MIT */
/* Collects the firmware's own view of the PCI functions (see pciio.h for why and for the exact
 * list of what may be touched). The reads below are the whole of what this file does to the
 * machine: GetLocation, 32-bit Pci.Read and, for an NVMe function, 32-bit Mem.Read of seven
 * controller registers. Nothing is written, mapped, allocated, flushed or reconfigured; the only
 * boot services used are LocateHandleBuffer, HandleProtocol and FreePool (for the handle list).
 */
#include "pciio.h"

#include "logic.h"

/* ACPI device path node, UEFI 2.10 section 10.3.3: type 2 (ACPI), subtype 1, 12 bytes:
 * header (type, subtype, 16-bit length), HID, UID. */
#define DP_TYPE_ACPI 0x02u
#define DP_SUB_ACPI 0x01u
#define DP_HEADER_BYTES 4u
#define DP_ACPI_NODE_BYTES 12u

#define PCI_SEGMENT_MAX 0xFFFFu
#define PCI_BUS_MAX 0xFFu
#define PCI_DEVICE_MAX 0x1Fu
#define PCI_FUNCTION_MAX 0x7u

/* efi_types.h keeps LocateHandleBuffer an untyped placeholder, like every slot the probe never
 * called, and that shared header is left alone: it is part of every image's preprocessed text.
 * The one call is typed here instead. The slot offset is pinned in efi_types.h (312). */
typedef efi_status (*locate_handle_buffer_fn)(uint32_t search_type, const efi_guid *protocol,
                                              void *search_key, efi_uintn *no_handles,
                                              efi_handle **buffer);

const char *tests_pciio_state_name(int state)
{
    static const char *const names[TESTS_PCIIO_STATE_COUNT] = {
        "NOT COLLECTED", "OK", "NO BOOT SERVICES", "NO PROTOCOL", "LOCATE FAILED",
    };

    return state >= 0 && state < TESTS_PCIIO_STATE_COUNT ? names[state] : "?";
}

const char *tests_pciio_regs_name(int regs)
{
    static const char *const names[TESTS_PCIIO_REGS_COUNT] = {
        "NONE", "OK", "NO BAR0", "DECODE OFF", "READ FAILED",
    };

    return regs >= 0 && regs < TESTS_PCIIO_REGS_COUNT ? names[regs] : "?";
}

int tests_pciio_hid_is_pci_root(uint32_t hid)
{
    return hid == TESTS_PCIIO_HID_PCI_ROOT || hid == TESTS_PCIIO_HID_PCIE_ROOT;
}

int tests_pciio_dp_root(const uint8_t *path, size_t max_len, uint32_t *hid, uint32_t *uid)
{
    size_t node_len;

    *hid = 0;
    *uid = 0;
    if (path == NULL || max_len < DP_HEADER_BYTES)
        return TESTS_PCIIO_ROOT_NO_PATH;
    if (path[0] != DP_TYPE_ACPI || path[1] != DP_SUB_ACPI)
        return TESTS_PCIIO_ROOT_NOT_ACPI;
    node_len = probe_le16(path + 2);
    if (node_len != DP_ACPI_NODE_BYTES || node_len > max_len)
        return TESTS_PCIIO_ROOT_BAD_LENGTH;
    *hid = probe_le32(path + 4);
    *uid = probe_le32(path + 8);
    return TESTS_PCIIO_ROOT_OK;
}

uint64_t tests_pciio_bar_base(uint32_t lo, uint32_t hi)
{
    if (lo & 1u)
        return 0; /* an I/O BAR */
    switch ((lo >> 1) & 3u) {
    case 0u: /* 32-bit memory BAR */
        return lo & ~0xFull;
    case 2u: /* 64-bit memory BAR: the next BAR dword holds the upper half */
        return ((uint64_t)hi << 32) | (lo & ~0xFull);
    default: /* 1: below 1 MiB (legacy), 3: reserved */
        return 0;
    }
}

/* ---- Reads ---- */

static int cfg_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)
{
    uint32_t dw = 0;

    if (EFI_IS_ERROR(io->pci.read(io, EFI_PCI_IO_WIDTH_UINT32, offset, 1, &dw)))
        return 0;
    *v = dw;
    return 1;
}

/* One dword of the NVMe registers behind BAR 0. The BAR index is the PCI I/O protocol's own
 * (0 is BAR0), not an address: the firmware decides where it is. */
static int nvme_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)
{
    uint32_t dw = 0;

    if (io->mem.read == NULL ||
        EFI_IS_ERROR(io->mem.read(io, EFI_PCI_IO_WIDTH_UINT32, 0, offset, 1, &dw)))
        return 0;
    *v = dw;
    return 1;
}

static int nvme_qword(efi_pci_io *io, uint32_t offset, uint64_t *v)
{
    uint32_t lo, hi;

    if (!nvme_dword(io, offset, &lo) || !nvme_dword(io, offset + 4u, &hi))
        return 0;
    *v = ((uint64_t)hi << 32) | lo;
    return 1;
}

/* The seven registers or none: a value read before a later read failed is not kept, so that a
 * half-filled record cannot be taken for a controller state. */
static void read_nvme_registers(efi_pci_io *io, tests_pciio_fn *f)
{
    uint64_t cap, asq, acq;
    uint32_t vs, cc, csts, aqa;

    if (f->bar0_base == 0 || (f->bar[0] & 1u) != 0) {
        f->regs_state = TESTS_PCIIO_REGS_NO_BAR0;
        return;
    }
    /* With memory decode off a BAR read has nothing to answer it: on this machine class that is
     * an access the firmware's own driver would never make, so this one is not made either. */
    if ((f->cmd_status & TESTS_PCIIO_CMD_MEMORY) == 0) {
        f->regs_state = TESTS_PCIIO_REGS_DECODE_OFF;
        return;
    }
    if (!nvme_qword(io, TESTS_NVME_CAP, &cap) || !nvme_dword(io, TESTS_NVME_VS, &vs) ||
        !nvme_dword(io, TESTS_NVME_CC, &cc) || !nvme_dword(io, TESTS_NVME_CSTS, &csts) ||
        !nvme_dword(io, TESTS_NVME_AQA, &aqa) || !nvme_qword(io, TESTS_NVME_ASQ, &asq) ||
        !nvme_qword(io, TESTS_NVME_ACQ, &acq)) {
        f->regs_state = TESTS_PCIIO_REGS_READ_FAILED;
        return;
    }
    f->cap = cap;
    f->vs = vs;
    f->cc = cc;
    f->csts = csts;
    f->aqa = aqa;
    f->asq = asq;
    f->acq = acq;
    f->regs_state = TESTS_PCIIO_REGS_OK;
}

static void read_config(efi_pci_io *io, tests_pciio_fn *f)
{
    unsigned want = 0;

    if (!cfg_dword(io, TESTS_PCIIO_CFG_ID, &f->id) ||
        !cfg_dword(io, TESTS_PCIIO_CFG_COMMAND, &f->cmd_status) ||
        !cfg_dword(io, TESTS_PCIIO_CFG_CLASS_REV, &f->class_rev) ||
        !cfg_dword(io, TESTS_PCIIO_CFG_HEADER, &f->hdr))
        return;
    f->base_ok = 1;
    f->header_type = (uint8_t)((f->hdr >> 16) & 0x7Fu);
    if (f->header_type == TESTS_PCIIO_HDR_ENDPOINT)
        want = TESTS_PCIIO_BARS;
    else if (f->header_type == TESTS_PCIIO_HDR_BRIDGE)
        want = TESTS_PCIIO_BARS_BRIDGE;
    for (unsigned i = 0; i < want; i++) {
        if (!cfg_dword(io, TESTS_PCIIO_CFG_BAR0 + 4u * i, &f->bar[i]))
            return;
        f->bars_read++;
    }
    if (f->header_type == TESTS_PCIIO_HDR_BRIDGE &&
        !cfg_dword(io, TESTS_PCIIO_CFG_BUSES, &f->buses))
        return;
    f->cfg_ok = 1;
}

/* Returns 0 when the handle gave a PCI I/O interface (the entry is then valid, with whatever
 * could be read), -1 when it did not (nothing is kept). */
static int read_function(efi_boot_services *bs, efi_handle handle, tests_pciio_fn *f)
{
    static const efi_guid pci_io_guid = EFI_GUID_PCI_IO;
    static const efi_guid dp_guid = EFI_GUID_DEVICE_PATH;
    efi_pci_io *io = NULL;
    uint8_t *path = NULL;
    efi_uintn segment = 0, bus = 0, device = 0, function = 0;

    probe_memset(f, 0, sizeof *f);
    if (EFI_IS_ERROR(bs->handle_protocol(handle, &pci_io_guid, (void **)&io)) || io == NULL ||
        io->get_location == NULL || io->pci.read == NULL)
        return -1;

    if (!EFI_IS_ERROR(io->get_location(io, &segment, &bus, &device, &function)) &&
        segment <= PCI_SEGMENT_MAX && bus <= PCI_BUS_MAX && device <= PCI_DEVICE_MAX &&
        function <= PCI_FUNCTION_MAX) {
        f->located = 1;
        f->segment = (uint16_t)segment;
        f->bus = (uint8_t)bus;
        f->device = (uint8_t)device;
        f->function = (uint8_t)function;
    }
    /* Only the first node is read, and only as far as it says it extends. */
    if (!EFI_IS_ERROR(bs->handle_protocol(handle, &dp_guid, (void **)&path)) && path != NULL)
        f->have_root = tests_pciio_dp_root(path, DP_ACPI_NODE_BYTES, &f->root_hid,
                                           &f->root_uid) == TESTS_PCIIO_ROOT_OK;

    read_config(io, f);
    if (f->bars_read >= 1)
        f->bar0_base = tests_pciio_bar_base(f->bar[0], f->bars_read >= 2 ? f->bar[1] : 0);
    f->is_nvme = f->cfg_ok && f->header_type == TESTS_PCIIO_HDR_ENDPOINT &&
                 tests_pciio_class_is_nvme(f->class_rev);
    if (f->is_nvme)
        read_nvme_registers(io, f);
    return 0;
}

void tests_pciio_collect(efi_boot_services *bs, tests_pciio *out)
{
    static const efi_guid pci_io_guid = EFI_GUID_PCI_IO;
    efi_handle *handles = NULL;
    efi_uintn count = 0;
    efi_status status;

    probe_memset(out, 0, sizeof *out);
    if (bs == NULL || bs->locate_handle_buffer == NULL || bs->handle_protocol == NULL ||
        bs->free_pool == NULL) {
        out->state = TESTS_PCIIO_NO_BOOT_SERVICES;
        return;
    }
    status = ((locate_handle_buffer_fn)bs->locate_handle_buffer)(EFI_LOCATE_BY_PROTOCOL,
                                                                 &pci_io_guid, NULL, &count,
                                                                 &handles);
    out->status = status;
    if (status == EFI_NOT_FOUND) {
        out->state = TESTS_PCIIO_NO_PROTOCOL;
        return;
    }
    if (EFI_IS_ERROR(status) || handles == NULL) {
        out->state = TESTS_PCIIO_LOCATE_FAILED;
        return;
    }
    out->state = TESTS_PCIIO_OK;
    out->located = count > 0xFFFFFFFFull ? 0xFFFFFFFFu : (uint32_t)count;
    for (efi_uintn i = 0; i < count; i++) {
        tests_pciio_fn *f;

        if (out->n >= TESTS_PCIIO_MAX)
            break;
        f = &out->f[out->n];
        if (read_function(bs, handles[i], f) != 0) {
            out->skipped++;
            continue;
        }
        out->n++;
        if (f->is_nvme)
            out->n_nvme++;
    }
    /* The list is the firmware's pool allocation; it is freed here, before anything else runs,
     * so that nothing is left allocated between the last memory-map snapshot and the exit. */
    (void)bs->free_pool(handles);
}

/* ---- Text ---- */

static void put_hex_min(probe_sb *sb, uint64_t v)
{
    unsigned digits = 1;

    while (digits < 16u && (v >> (4u * digits)) != 0)
        digits++;
    probe_sb_hex(sb, v, digits);
}

void tests_pciio_root_name(char *out, size_t cap, uint32_t hid, uint32_t uid)
{
    probe_sb sb;

    probe_sb_init(&sb, out, cap);
    if (hid == TESTS_PCIIO_HID_PCIE_ROOT) {
        probe_sb_puts(&sb, "PcieRoot(0x");
    } else if (hid == TESTS_PCIIO_HID_PCI_ROOT) {
        probe_sb_puts(&sb, "PciRoot(0x");
    } else {
        probe_sb_puts(&sb, "Acpi(0x");
        probe_sb_hex(&sb, hid, 8);
        probe_sb_puts(&sb, ",0x");
    }
    put_hex_min(&sb, uid);
    probe_sb_putc(&sb, ')');
}

static void put_key_hex(probe_sb *sb, const char *key, uint64_t v, unsigned digits)
{
    probe_sb_putc(sb, ' ');
    probe_sb_puts(sb, key);
    probe_sb_putc(sb, '=');
    probe_sb_hex(sb, v, digits);
}

static void put_key_dec(probe_sb *sb, const char *key, uint64_t v)
{
    probe_sb_putc(sb, ' ');
    probe_sb_puts(sb, key);
    probe_sb_putc(sb, '=');
    probe_sb_dec(sb, v);
}

static void put_function_line(probe_sb *sb, unsigned index, const tests_pciio_fn *f)
{
    char root[48];

    probe_sb_puts(sb, "pci");
    put_key_dec(sb, "idx", index);
    if (f->located) {
        put_key_dec(sb, "seg", f->segment);
        put_key_hex(sb, "bus", f->bus, 2);
        put_key_hex(sb, "dev", f->device, 2);
        put_key_dec(sb, "func", f->function);
    } else {
        probe_sb_puts(sb, " loc=BAD");
    }
    probe_sb_puts(sb, " root=");
    if (f->have_root) {
        tests_pciio_root_name(root, sizeof root, f->root_hid, f->root_uid);
        probe_sb_puts(sb, root);
    } else {
        probe_sb_puts(sb, "NONE");
    }
    if (!f->base_ok) {
        probe_sb_puts(sb, " cfg=FAILED\n");
        return;
    }
    probe_sb_puts(sb, f->cfg_ok ? " cfg=ok" : " cfg=PARTIAL");
    probe_sb_putc(sb, ' ');
    probe_sb_puts(sb, "id=");
    probe_sb_hex(sb, f->id & 0xFFFFu, 4);
    probe_sb_putc(sb, ':');
    probe_sb_hex(sb, f->id >> 16, 4);
    put_key_hex(sb, "class", f->class_rev >> 8, 6);
    put_key_hex(sb, "rev", f->class_rev & 0xFFu, 2);
    put_key_hex(sb, "hdr", (f->hdr >> 16) & 0xFFu, 2);
    put_key_hex(sb, "cmd", f->cmd_status & 0xFFFFu, 4);
    put_key_hex(sb, "sts", f->cmd_status >> 16, 4);
    if (f->bars_read != 0) {
        probe_sb_puts(sb, " bar=");
        for (unsigned i = 0; i < f->bars_read; i++) {
            if (i != 0)
                probe_sb_putc(sb, ',');
            probe_sb_hex(sb, f->bar[i], 8);
        }
    }
    if (f->header_type == TESTS_PCIIO_HDR_BRIDGE && f->cfg_ok) {
        probe_sb_puts(sb, " buses=");
        probe_sb_hex(sb, f->buses & 0xFFu, 2);
        probe_sb_putc(sb, ':');
        probe_sb_hex(sb, (f->buses >> 8) & 0xFFu, 2);
        probe_sb_putc(sb, ':');
        probe_sb_hex(sb, (f->buses >> 16) & 0xFFu, 2);
    }
    probe_sb_putc(sb, '\n');
}

static void put_nvme_line(probe_sb *sb, unsigned index, const tests_pciio_fn *f)
{
    probe_sb_puts(sb, "nvme");
    put_key_dec(sb, "idx", index);
    put_key_hex(sb, "bar0", f->bar0_base, 16);
    probe_sb_puts(sb, " regs=");
    probe_sb_puts(sb, tests_pciio_regs_name(f->regs_state));
    if (f->regs_state == TESTS_PCIIO_REGS_OK) {
        put_key_hex(sb, "cap", f->cap, 16);
        put_key_hex(sb, "vs", f->vs, 8);
        put_key_hex(sb, "cc", f->cc, 8);
        put_key_hex(sb, "csts", f->csts, 8);
        put_key_hex(sb, "aqa", f->aqa, 8);
        put_key_hex(sb, "asq", f->asq, 16);
        put_key_hex(sb, "acq", f->acq, 16);
    }
    probe_sb_putc(sb, '\n');
}

int tests_pciio_text(const tests_pciio *t, const char *build_id, const char *kind, char *buf,
                     size_t cap, size_t *len)
{
    probe_sb sb;

    probe_sb_init(&sb, buf, cap);
    probe_sb_puts(&sb, "# SP11X2 pciio v1: the firmware's PCI I/O view, read before ExitBootServices"
                       " (reads only)\n");
    probe_sb_puts(&sb, "build=");
    probe_sb_puts(&sb, build_id);
    probe_sb_puts(&sb, "\nkind=");
    probe_sb_puts(&sb, kind);
    probe_sb_puts(&sb, "\nstate=");
    probe_sb_puts(&sb, tests_pciio_state_name(t->state));
    probe_sb_puts(&sb, " status=0x");
    probe_sb_hex(&sb, t->status, 16);
    probe_sb_puts(&sb, "\nlocated=");
    probe_sb_dec(&sb, t->located);
    probe_sb_puts(&sb, " kept=");
    probe_sb_dec(&sb, t->n);
    probe_sb_puts(&sb, " nvme=");
    probe_sb_dec(&sb, t->n_nvme);
    probe_sb_puts(&sb, " skipped=");
    probe_sb_dec(&sb, t->skipped);
    probe_sb_puts(&sb, " truncated=");
    probe_sb_putc(&sb, t->located > t->n + t->skipped ? '1' : '0');
    probe_sb_putc(&sb, '\n');
    for (unsigned i = 0; i < t->n && i < TESTS_PCIIO_MAX; i++)
        put_function_line(&sb, i, &t->f[i]);
    for (unsigned i = 0; i < t->n && i < TESTS_PCIIO_MAX; i++)
        if (t->f[i].is_nvme)
            put_nvme_line(&sb, i, &t->f[i]);
    probe_sb_puts(&sb, "end\n");
    *len = sb.len;
    return sb.overflow ? -1 : 0;
}
