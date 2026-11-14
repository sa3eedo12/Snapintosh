/* SPDX-License-Identifier: MIT */
/* Host tests of the firmware's PCI I/O view (probes/uefi/tests/pciio.c).
 *
 * The collector runs on the machine before ExitBootServices, through the firmware's own protocol,
 * and must never do more than read: GetLocation, 32-bit Pci.Read and, for an NVMe function, 32-bit
 * Mem.Read of seven registers. The fake protocol below gives every member the module does not use
 * a trap that counts the call, records every offset that is read, hands out device paths in
 * buffers of exactly their size (so a read past the end is a sanitizer report), and can be made to
 * fail at any one offset. Every register image is a synthetic fixture; none is a measurement.
 */
#include "xdev.h"

#include "pciio.h"

#include "xt06.h"

/* ---- A fake EFI_PCI_IO_PROTOCOL whose unused members are traps ---- */

enum {
    SLOT_POLL_MEM,
    SLOT_POLL_IO,
    SLOT_MEM_WRITE,
    SLOT_IO_READ,
    SLOT_IO_WRITE,
    SLOT_PCI_WRITE,
    SLOT_COPY_MEM,
    SLOT_MAP,
    SLOT_UNMAP,
    SLOT_ALLOCATE_BUFFER,
    SLOT_FREE_BUFFER,
    SLOT_FLUSH,
    SLOT_ATTRIBUTES,
    SLOT_GET_BAR_ATTRIBUTES,
    SLOT_SET_BAR_ATTRIBUTES,
    SLOT_COUNT
};

static unsigned g_trap[SLOT_COUNT];

#define TRAP(name, slot)                    \
    static efi_status name(void)            \
    {                                       \
        g_trap[slot]++;                     \
        return EFI_DEVICE_ERROR;            \
    }
TRAP(trap_poll_mem, SLOT_POLL_MEM)
TRAP(trap_poll_io, SLOT_POLL_IO)
TRAP(trap_mem_write, SLOT_MEM_WRITE)
TRAP(trap_io_read, SLOT_IO_READ)
TRAP(trap_io_write, SLOT_IO_WRITE)
TRAP(trap_pci_write, SLOT_PCI_WRITE)
TRAP(trap_copy_mem, SLOT_COPY_MEM)
TRAP(trap_map, SLOT_MAP)
TRAP(trap_unmap, SLOT_UNMAP)
TRAP(trap_allocate_buffer, SLOT_ALLOCATE_BUFFER)
TRAP(trap_free_buffer, SLOT_FREE_BUFFER)
TRAP(trap_flush, SLOT_FLUSH)
TRAP(trap_attributes, SLOT_ATTRIBUTES)
TRAP(trap_get_bar_attributes, SLOT_GET_BAR_ATTRIBUTES)
TRAP(trap_set_bar_attributes, SLOT_SET_BAR_ATTRIBUTES)

#define FK_CFG_DWORDS 64u
#define FK_REG_DWORDS 32u
#define FK_LOG 64u

typedef struct {
    efi_pci_io io; /* first: a pointer to the structure is the protocol interface */
    int present;   /* HandleProtocol gives this interface */
    int loc_fail;
    efi_uintn seg, bus, dev, fn;
    uint32_t cfg[FK_CFG_DWORDS];
    int cfg_fail_at; /* byte offset whose read fails, -1 for none */
    unsigned cfg_log[FK_LOG];
    unsigned ncfg;
    uint32_t reg[FK_REG_DWORDS];
    int mem_fail_at;
    unsigned mem_log[FK_LOG];
    unsigned nmem;
    unsigned bad_calls; /* a read with a width, count, BAR index or alignment the module must not use */
    unsigned loc_calls;
    uint8_t *dp; /* an exact-size heap copy, NULL for no device path protocol */
} fakefn;

typedef struct {
    fakefn fn[TESTS_PCIIO_MAX + 8];
    unsigned nhandles;
    efi_status lhb_status;
    unsigned lhb_calls, free_calls, hp_calls, bad_args;
    int pool_live;
} fakebs;

static fakebs g_fbs;

static efi_status fk_cfg_read(efi_pci_io *self, uint32_t width, uint32_t offset, efi_uintn count,
                              void *buffer)
{
    fakefn *f = (fakefn *)self;

    if (width != EFI_PCI_IO_WIDTH_UINT32 || count != 1 || (offset & 3u) != 0 ||
        offset >= 4u * FK_CFG_DWORDS) {
        f->bad_calls++;
        return EFI_INVALID_PARAMETER;
    }
    if (f->ncfg < FK_LOG)
        f->cfg_log[f->ncfg] = offset;
    f->ncfg++;
    if ((int)offset == f->cfg_fail_at)
        return EFI_DEVICE_ERROR;
    memcpy(buffer, &f->cfg[offset / 4u], 4);
    return EFI_SUCCESS;
}

static efi_status fk_mem_read(efi_pci_io *self, uint32_t width, uint8_t bar, uint64_t offset,
                              efi_uintn count, void *buffer)
{
    fakefn *f = (fakefn *)self;

    if (width != EFI_PCI_IO_WIDTH_UINT32 || bar != 0 || count != 1 || (offset & 3u) != 0 ||
        offset >= 4u * FK_REG_DWORDS) {
        f->bad_calls++;
        return EFI_INVALID_PARAMETER;
    }
    if (f->nmem < FK_LOG)
        f->mem_log[f->nmem] = (unsigned)offset;
    f->nmem++;
    if ((int)offset == f->mem_fail_at)
        return EFI_DEVICE_ERROR;
    memcpy(buffer, &f->reg[offset / 4u], 4);
    return EFI_SUCCESS;
}

static efi_status fk_location(efi_pci_io *self, efi_uintn *seg, efi_uintn *bus, efi_uintn *dev,
                              efi_uintn *fn)
{
    fakefn *f = (fakefn *)self;

    f->loc_calls++;
    if (f->loc_fail)
        return EFI_DEVICE_ERROR;
    *seg = f->seg;
    *bus = f->bus;
    *dev = f->dev;
    *fn = f->fn;
    return EFI_SUCCESS;
}

static void fk_wire(fakefn *f)
{
    memset(&f->io, 0, sizeof f->io);
    f->io.poll_mem = (void *)trap_poll_mem;
    f->io.poll_io = (void *)trap_poll_io;
    f->io.mem.read = fk_mem_read;
    f->io.mem.write = (void *)trap_mem_write;
    f->io.io.read = (void *)trap_io_read;
    f->io.io.write = (void *)trap_io_write;
    f->io.pci.read = fk_cfg_read;
    f->io.pci.write = (void *)trap_pci_write;
    f->io.copy_mem = (void *)trap_copy_mem;
    f->io.map = (void *)trap_map;
    f->io.unmap = (void *)trap_unmap;
    f->io.allocate_buffer = (void *)trap_allocate_buffer;
    f->io.free_buffer = (void *)trap_free_buffer;
    f->io.flush = (void *)trap_flush;
    f->io.get_location = fk_location;
    f->io.attributes = (void *)trap_attributes;
    f->io.get_bar_attributes = (void *)trap_get_bar_attributes;
    f->io.set_bar_attributes = (void *)trap_set_bar_attributes;
}

static efi_status fake_locate_handle_buffer(uint32_t type, const efi_guid *guid, void *key,
                                            efi_uintn *count, efi_handle **buffer)
{
    static const efi_guid want = EFI_GUID_PCI_IO;
    efi_handle *h;

    g_fbs.lhb_calls++;
    if (type != EFI_LOCATE_BY_PROTOCOL || key != NULL || memcmp(guid, &want, sizeof want) != 0) {
        g_fbs.bad_args++;
        return EFI_INVALID_PARAMETER;
    }
    if (EFI_IS_ERROR(g_fbs.lhb_status))
        return g_fbs.lhb_status;
    h = xalloc(sizeof(efi_handle) * (g_fbs.nhandles != 0 ? g_fbs.nhandles : 1u));
    for (unsigned i = 0; i < g_fbs.nhandles; i++)
        h[i] = (efi_handle)(uintptr_t)(i + 1u);
    *count = g_fbs.nhandles;
    *buffer = h;
    g_fbs.pool_live++;
    return EFI_SUCCESS;
}

static efi_status fake_free_pool(void *p)
{
    g_fbs.free_calls++;
    if (p != NULL) {
        free(p);
        g_fbs.pool_live--;
    }
    return EFI_SUCCESS;
}

static efi_status fake_handle_protocol(efi_handle h, const efi_guid *guid, void **iface)
{
    static const efi_guid pci_io = EFI_GUID_PCI_IO;
    static const efi_guid dp = EFI_GUID_DEVICE_PATH;
    unsigned idx = (unsigned)(uintptr_t)h - 1u;

    g_fbs.hp_calls++;
    if (idx >= g_fbs.nhandles)
        return EFI_INVALID_PARAMETER;
    if (memcmp(guid, &pci_io, sizeof pci_io) == 0) {
        if (!g_fbs.fn[idx].present)
            return EFI_UNSUPPORTED;
        *iface = &g_fbs.fn[idx].io;
        return EFI_SUCCESS;
    }
    if (memcmp(guid, &dp, sizeof dp) == 0) {
        if (g_fbs.fn[idx].dp == NULL)
            return EFI_UNSUPPORTED;
        *iface = g_fbs.fn[idx].dp;
        return EFI_SUCCESS;
    }
    return EFI_UNSUPPORTED;
}

static void fbs_reset(efi_boot_services *bs)
{
    for (unsigned i = 0; i < sizeof g_fbs.fn / sizeof g_fbs.fn[0]; i++)
        free(g_fbs.fn[i].dp);
    memset(&g_fbs, 0, sizeof g_fbs);
    memset(g_trap, 0, sizeof g_trap);
    memset(bs, 0, sizeof *bs);
    bs->handle_protocol = fake_handle_protocol;
    bs->free_pool = fake_free_pool;
    bs->locate_handle_buffer = (void *)fake_locate_handle_buffer;
}

static unsigned traps_hit(void)
{
    unsigned n = 0;

    for (unsigned i = 0; i < SLOT_COUNT; i++)
        n += g_trap[i];
    return n;
}

/* ---- Function fixtures ---- */

#define DP_ACPI_LEN 12u

/* The first node of a device path: ACPI (type 2, subtype 1), HID, UID, then an end node, in a heap
 * block of exactly that size. */
static uint8_t *dp_acpi(uint32_t hid, uint32_t uid, size_t *len)
{
    uint8_t *p = xalloc(DP_ACPI_LEN + 4u);

    p[0] = 2;
    p[1] = 1;
    st16(p + 2, DP_ACPI_LEN);
    st32(p + 4, hid);
    st32(p + 8, uid);
    p[12] = 0x7F;
    p[13] = 0xFF;
    st16(p + 14, 4);
    *len = DP_ACPI_LEN + 4u;
    return p;
}

static fakefn *fk_add(unsigned seg, unsigned bus, unsigned dev, unsigned fn, uint32_t id,
                      uint32_t class_rev, uint32_t cmd, unsigned hdr_type)
{
    fakefn *f = &g_fbs.fn[g_fbs.nhandles++];
    size_t len;

    fk_wire(f);
    f->present = 1;
    f->seg = seg;
    f->bus = bus;
    f->dev = dev;
    f->fn = fn;
    f->cfg_fail_at = -1;
    f->mem_fail_at = -1;
    f->cfg[0] = id;
    f->cfg[1] = (0x0010u << 16) | cmd;
    f->cfg[2] = class_rev;
    f->cfg[3] = (uint32_t)hdr_type << 16;
    f->dp = dp_acpi(TESTS_PCIIO_HID_PCIE_ROOT, seg, &len);
    return f;
}

/* The NVMe registers every fixture shows. */
#define FK_CAP_LO 0x0107FFFFu
#define FK_CAP_HI 0x00400820u
#define FK_VS 0x00020000u
#define FK_CC 0x00460001u
#define FK_CSTS 0x00000001u
#define FK_AQA 0x001F001Fu
#define FK_ASQ_LO 0xF6A00000u
#define FK_ASQ_HI 0x00000001u
#define FK_ACQ_LO 0xF6A01000u
#define FK_ACQ_HI 0x00000001u

static void fk_nvme_regs(fakefn *f)
{
    f->reg[0x00 / 4] = FK_CAP_LO;
    f->reg[0x04 / 4] = FK_CAP_HI;
    f->reg[0x08 / 4] = FK_VS;
    f->reg[0x14 / 4] = FK_CC;
    f->reg[0x1C / 4] = FK_CSTS;
    f->reg[0x24 / 4] = FK_AQA;
    f->reg[0x28 / 4] = FK_ASQ_LO;
    f->reg[0x2C / 4] = FK_ASQ_HI;
    f->reg[0x30 / 4] = FK_ACQ_LO;
    f->reg[0x34 / 4] = FK_ACQ_HI;
    /* Registers the module must not read hold a value that would show if it did. */
    f->reg[0x0C / 4] = 0xDEAD0C0Cu;
    f->reg[0x10 / 4] = 0xDEAD1010u;
    f->reg[0x18 / 4] = 0xDEAD1818u;
    f->reg[0x20 / 4] = 0xDEAD2020u;
    f->reg[0x38 / 4] = 0xDEAD3838u;
}

static int logged(const unsigned *log, unsigned n, unsigned offset)
{
    for (unsigned i = 0; i < n && i < FK_LOG; i++)
        if (log[i] == offset)
            return 1;
    return 0;
}

static int log_is(const unsigned *log, unsigned n, const unsigned *want, unsigned nwant)
{
    if (n != nwant)
        return 0;
    for (unsigned i = 0; i < n; i++)
        if (log[i] != want[i])
            return 0;
    return 1;
}

/* A root port, an NVMe endpoint behind it and an unrelated function on another segment. */
static void fixture_basic(void)
{
    fakefn *rp, *nv, *other;

    rp = fk_add(6, 0, 0, 0, 0x01201234u, 0x06040001u, 0x0006u, 1);
    rp->cfg[0x10 / 4] = 0;
    rp->cfg[0x14 / 4] = 0;
    rp->cfg[0x18 / 4] = 0x00010100u;
    nv = fk_add(6, 1, 0, 0, 0x00101B36u, 0x01080201u, 0x0006u, 0);
    nv->cfg[0x10 / 4] = 0x7A000004u; /* 64-bit memory BAR */
    nv->cfg[0x14 / 4] = 0;
    fk_nvme_regs(nv);
    other = fk_add(0, 0, 4, 1, 0x91A0144Du, 0x0C033001u, 0x0002u, 0);
    other->cfg[0x10 / 4] = 0x12340000u;
}

static const tests_pciio_fn *fn_at(const tests_pciio *t, unsigned i)
{
    return &t->f[i];
}

/* The same firmware for the hook tests (test_x_hook.c), which run the whole driver over it: the
 * basic fixture, or with_nvme == 0 the root port and the unrelated function only. */
efi_boot_services *x_pciio_fake_bs(int with_nvme)
{
    static efi_boot_services bs;

    fbs_reset(&bs);
    if (with_nvme) {
        fixture_basic();
    } else {
        fakefn *rp = fk_add(6, 0, 0, 0, 0x01201234u, 0x06040001u, 0x0006u, 1);

        rp->cfg[0x18 / 4] = 0x00010100u;
        fk_add(0, 0, 4, 1, 0x91A0144Du, 0x0C033001u, 0x0002u, 0)->cfg[0x10 / 4] = 0x12340000u;
    }
    return &bs;
}

/* ---- The text of the table ---- */

/* The file the basic fixture makes, word for word. Every line of it is checked again, independently,
 * by probes/uefi/tests/host/pciio_text.py over the raw dwords (--emit-pciio). */
static const char BASIC_TEXT[] =
    "# SP11X2 pciio v1: the firmware's PCI I/O view, read before ExitBootServices (reads only)\n"
    "build=TESTBUILD001\n"
    "kind=HARDWARE\n"
    "state=OK status=0x0000000000000000\n"
    "located=3 kept=3 nvme=1 skipped=0 truncated=0\n"
    "pci idx=0 seg=6 bus=00 dev=00 func=0 root=PcieRoot(0x6) cfg=ok id=1234:0120 class=060400 "
    "rev=01 hdr=01 cmd=0006 sts=0010 bar=00000000,00000000 buses=00:01:01\n"
    "pci idx=1 seg=6 bus=01 dev=00 func=0 root=PcieRoot(0x6) cfg=ok id=1B36:0010 class=010802 "
    "rev=01 hdr=00 cmd=0006 sts=0010 "
    "bar=7A000004,00000000,00000000,00000000,00000000,00000000\n"
    "pci idx=2 seg=0 bus=00 dev=04 func=1 root=PcieRoot(0x0) cfg=ok id=144D:91A0 class=0C0330 "
    "rev=01 hdr=00 cmd=0002 sts=0010 "
    "bar=12340000,00000000,00000000,00000000,00000000,00000000\n"
    "nvme idx=1 bar0=000000007A000000 regs=OK cap=004008200107FFFF vs=00020000 cc=00460001 "
    "csts=00000001 aqa=001F001F asq=00000001F6A00000 acq=00000001F6A01000\n"
    "end\n";

static void pciio_text_tests(efi_boot_services *bs, tests_pciio *t)
{
    char text[8192];
    size_t len = 0;

    section("pciio text");
    fbs_reset(bs);
    fixture_basic();
    tests_pciio_collect(bs, t);
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, sizeof text, &len), 0);
    CHECK_EQ(len, strlen(BASIC_TEXT));
    CHECK_STR(text, BASIC_TEXT);
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "MODEL", text, sizeof text, &len), 0);
    CHECK(strstr(text, "\nkind=MODEL\n") != NULL);

    /* A buffer that is too small: the text stops where it stops, as valid lines, and the call says
     * so. Not one byte past the buffer is written (the buffer is exact: a sanitizer would report). */
    for (size_t cap = 1; cap < 220; cap += 37) {
        char *small = xalloc(cap);

        CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", small, cap, &len), -1);
        CHECK(len < cap);
        CHECK(strncmp(small, BASIC_TEXT, len) == 0);
        free(small);
    }
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, strlen(BASIC_TEXT), &len), -1);
    CHECK(strstr(text, "end\n") == NULL); /* the missing closing line is how a cut file shows */
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, strlen(BASIC_TEXT) + 1, &len), 0);

    /* The states and what they carry. */
    fbs_reset(bs);
    g_fbs.lhb_status = EFI_NOT_FOUND;
    tests_pciio_collect(bs, t);
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, sizeof text, &len), 0);
    CHECK(strstr(text, "state=NO PROTOCOL status=0x800000000000000E\n") != NULL);
    CHECK(strstr(text, "located=0 kept=0 nvme=0 skipped=0 truncated=0\nend\n") != NULL);
    tests_pciio_collect(NULL, t);
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, sizeof text, &len), 0);
    CHECK(strstr(text, "state=NO BOOT SERVICES status=0x0000000000000000\n") != NULL);
    memset(t, 0, sizeof *t);
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, sizeof text, &len), 0);
    CHECK(strstr(text, "state=NOT COLLECTED") != NULL); /* a table nobody filled says so */

    /* More handles than the table keeps: the file says it is cut. */
    fbs_reset(bs);
    for (unsigned i = 0; i < TESTS_PCIIO_MAX + 3u; i++)
        (void)fk_add(1, i % 200, i % 32, 0, 0x1000u + i, 0x02000000u, 0x0006u, 0);
    tests_pciio_collect(bs, t);
    {
        char *big = xalloc(64u * 1024u);

        CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", big, 64u * 1024u, &len), 0);
        CHECK(strstr(big, "truncated=1\n") != NULL);
        CHECK(strstr(big, "kept=64 ") != NULL);
        free(big);
    }

    /* A partially read function and one that could not be located are written as such. */
    fbs_reset(bs);
    fixture_basic();
    g_fbs.fn[1].cfg_fail_at = 0x18;
    g_fbs.fn[2].loc_fail = 1;
    tests_pciio_collect(bs, t);
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, sizeof text, &len), 0);
    CHECK(strstr(text, "root=PcieRoot(0x6) cfg=PARTIAL id=1B36:0010") != NULL);
    CHECK(strstr(text, "pci idx=2 loc=BAD root=PcieRoot(0x0) cfg=ok") != NULL);
    CHECK(strstr(text, "\nnvme idx=") == NULL);
    fbs_reset(bs);
    fixture_basic();
    g_fbs.fn[1].cfg_fail_at = 0x00;
    tests_pciio_collect(bs, t);
    CHECK_EQ(tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, sizeof text, &len), 0);
    CHECK(strstr(text, "pci idx=1 seg=6 bus=01 dev=00 func=0 root=PcieRoot(0x6) cfg=FAILED\n") != NULL);
}

void test_x_pciio(void)
{
    static const unsigned bridge_cfg[] = {0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18};
    static const unsigned endpoint_cfg[] = {0x00, 0x04, 0x08, 0x0C, 0x10, 0x14,
                                            0x18, 0x1C, 0x20, 0x24};
    static const unsigned nvme_mem[] = {0x00, 0x04, 0x08, 0x14, 0x1C, 0x24, 0x28, 0x2C, 0x30, 0x34};
    efi_boot_services *bs = xalloc(sizeof *bs);
    tests_pciio *t = xalloc(sizeof *t);
    uint8_t path[16];
    uint32_t hid, uid;

    section("pciio collect");
    fbs_reset(bs);

    /* The normal case: what is read, and exactly what is read. */
    fixture_basic();
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->state, TESTS_PCIIO_OK);
    CHECK_EQ(t->located, 3);
    CHECK_EQ(t->n, 3);
    CHECK_EQ(t->n_nvme, 1);
    CHECK_EQ(t->skipped, 0);
    CHECK_EQ(g_fbs.lhb_calls, 1);
    CHECK_EQ(g_fbs.bad_args, 0);
    CHECK_EQ(g_fbs.free_calls, 1); /* the firmware's handle list is returned to it */
    CHECK_EQ(g_fbs.pool_live, 0);
    CHECK_EQ(traps_hit(), 0); /* nothing but GetLocation, Pci.Read and Mem.Read was called */
    CHECK_EQ(g_fbs.fn[0].bad_calls + g_fbs.fn[1].bad_calls + g_fbs.fn[2].bad_calls, 0);
    CHECK(fn_at(t, 0)->located && fn_at(t, 0)->segment == 6 && fn_at(t, 0)->bus == 0);
    CHECK(fn_at(t, 1)->located && fn_at(t, 1)->bus == 1 && fn_at(t, 1)->device == 0);
    CHECK_EQ(fn_at(t, 2)->segment, 0);
    CHECK_EQ(fn_at(t, 2)->device, 4);
    CHECK_EQ(fn_at(t, 2)->function, 1);
    CHECK(fn_at(t, 0)->have_root && fn_at(t, 0)->root_hid == TESTS_PCIIO_HID_PCIE_ROOT &&
          fn_at(t, 0)->root_uid == 6);
    CHECK_EQ(fn_at(t, 2)->root_uid, 0);
    /* The bridge: BAR0 and BAR1 and the bus numbers, no more. */
    CHECK(log_is(g_fbs.fn[0].cfg_log, g_fbs.fn[0].ncfg, bridge_cfg, 7));
    CHECK_EQ(fn_at(t, 0)->header_type, 1);
    CHECK_EQ(fn_at(t, 0)->bars_read, 2);
    CHECK_EQU(fn_at(t, 0)->buses, 0x00010100u);
    CHECK(fn_at(t, 0)->cfg_ok && !fn_at(t, 0)->is_nvme);
    CHECK_EQ(g_fbs.fn[0].nmem, 0); /* no memory read of a function that is not NVMe */
    /* The endpoint: six BARs, and the seven NVMe registers behind BAR0. */
    CHECK(log_is(g_fbs.fn[1].cfg_log, g_fbs.fn[1].ncfg, endpoint_cfg, 10));
    CHECK(fn_at(t, 1)->is_nvme && fn_at(t, 1)->cfg_ok);
    CHECK_EQ(fn_at(t, 1)->bars_read, 6);
    CHECK_EQU(fn_at(t, 1)->bar0_base, 0x7A000000ull);
    CHECK(log_is(g_fbs.fn[1].mem_log, g_fbs.fn[1].nmem, nvme_mem, 10));
    CHECK_EQ(fn_at(t, 1)->regs_state, TESTS_PCIIO_REGS_OK);
    CHECK_EQU(fn_at(t, 1)->cap, ((uint64_t)FK_CAP_HI << 32) | FK_CAP_LO);
    CHECK_EQU(fn_at(t, 1)->vs, FK_VS);
    CHECK_EQU(fn_at(t, 1)->cc, FK_CC);
    CHECK_EQU(fn_at(t, 1)->csts, FK_CSTS);
    CHECK_EQU(fn_at(t, 1)->aqa, FK_AQA);
    CHECK_EQU(fn_at(t, 1)->asq, ((uint64_t)FK_ASQ_HI << 32) | FK_ASQ_LO);
    CHECK_EQU(fn_at(t, 1)->acq, ((uint64_t)FK_ACQ_HI << 32) | FK_ACQ_LO);
    for (unsigned off = 0; off < 0x40; off += 4) { /* the registers it must leave alone */
        int wanted = off == 0x00 || off == 0x04 || off == 0x08 || off == 0x14 || off == 0x1C ||
                     off == 0x24 || off == 0x28 || off == 0x2C || off == 0x30 || off == 0x34;

        CHECK_EQ(logged(g_fbs.fn[1].mem_log, g_fbs.fn[1].nmem, off), wanted);
    }
    CHECK_EQ(g_fbs.fn[2].nmem, 0);
    CHECK_EQ(fn_at(t, 2)->regs_state, TESTS_PCIIO_REGS_NONE);

    /* Every other member of the protocol fails the test when it is called: a mutant that
     * writes, maps or flushes would show here (the traps below are each tried once, so that a
     * trap that cannot fire is a failure of the harness, not a silent pass). */
    for (int slot = 0; slot < SLOT_COUNT; slot++)
        CHECK_EQ(g_trap[slot], 0);
    {
        fakefn *f = &g_fbs.fn[0];

        (void)((efi_status (*)(void))f->io.pci.write)();
        (void)((efi_status (*)(void))f->io.mem.write)();
        (void)((efi_status (*)(void))f->io.set_bar_attributes)();
        CHECK_EQ(g_trap[SLOT_PCI_WRITE], 1);
        CHECK_EQ(g_trap[SLOT_MEM_WRITE], 1);
        CHECK_EQ(g_trap[SLOT_SET_BAR_ATTRIBUTES], 1);
    }

    /* The configuration read of the class code fails: nothing after it is read, and the
     * function is not an NVMe function. */
    fbs_reset(bs);
    fixture_basic();
    g_fbs.fn[1].cfg_fail_at = 0x08;
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->n_nvme, 0);
    CHECK(!fn_at(t, 1)->base_ok && !fn_at(t, 1)->cfg_ok && !fn_at(t, 1)->is_nvme);
    CHECK_EQ(g_fbs.fn[1].ncfg, 3); /* 0x00, 0x04 and the one that failed */
    CHECK_EQ(g_fbs.fn[1].nmem, 0);
    CHECK_EQ(t->n, 3); /* the entry is kept, with what could not be read marked */

    /* A BAR read fails: the function is not trusted as an NVMe function and its registers are
     * not asked for. */
    fbs_reset(bs);
    fixture_basic();
    g_fbs.fn[1].cfg_fail_at = 0x18;
    tests_pciio_collect(bs, t);
    CHECK(fn_at(t, 1)->base_ok && !fn_at(t, 1)->cfg_ok && !fn_at(t, 1)->is_nvme);
    CHECK_EQ(fn_at(t, 1)->bars_read, 2);
    CHECK_EQ(g_fbs.fn[1].nmem, 0);
    CHECK_EQ(t->n_nvme, 0);

    /* A register read fails midway: none of the seven is kept. */
    fbs_reset(bs);
    fixture_basic();
    g_fbs.fn[1].mem_fail_at = 0x1C;
    tests_pciio_collect(bs, t);
    CHECK(fn_at(t, 1)->is_nvme);
    CHECK_EQ(fn_at(t, 1)->regs_state, TESTS_PCIIO_REGS_READ_FAILED);
    CHECK_EQU(fn_at(t, 1)->cap, 0);
    CHECK_EQU(fn_at(t, 1)->cc, 0);
    CHECK_EQ(g_fbs.fn[1].nmem, 5); /* CAP lo, CAP hi, VS, CC and the CSTS that failed */
    CHECK_EQ(traps_hit(), 0);

    /* No memory decode, no BAR, an I/O BAR and a reserved BAR type: the registers are not asked
     * for, and why is recorded. */
    for (int variant = 0; variant < 4; variant++) {
        fbs_reset(bs);
        fixture_basic();
        if (variant == 0)
            g_fbs.fn[1].cfg[1] = (0x0010u << 16) | 0x0004u; /* memory space enable clear */
        else if (variant == 1)
            g_fbs.fn[1].cfg[0x10 / 4] = 0;
        else if (variant == 2)
            g_fbs.fn[1].cfg[0x10 / 4] = 0x7A000001u; /* an I/O BAR */
        else
            g_fbs.fn[1].cfg[0x10 / 4] = 0x7A000002u; /* type 01: below 1 MiB, reserved here */
        tests_pciio_collect(bs, t);
        CHECK(fn_at(t, 1)->is_nvme);
        CHECK_EQ(fn_at(t, 1)->regs_state,
                 variant == 0 ? TESTS_PCIIO_REGS_DECODE_OFF : TESTS_PCIIO_REGS_NO_BAR0);
        CHECK_EQ(g_fbs.fn[1].nmem, 0);
        CHECK_EQ(traps_hit(), 0);
    }

    /* A 32-bit memory BAR0 decodes without the next dword; a 64-bit one uses it. */
    CHECK_EQU(tests_pciio_bar_base(0x7A000000u, 0x55), 0x7A000000ull);
    CHECK_EQU(tests_pciio_bar_base(0x7A000008u, 0x55), 0x7A000000ull); /* prefetchable, 32-bit */
    CHECK_EQU(tests_pciio_bar_base(0x7A00000Cu, 0x5), 0x57A000000ull); /* prefetchable, 64-bit */
    CHECK_EQU(tests_pciio_bar_base(0x7A000004u, 0x5), 0x57A000000ull);
    CHECK_EQU(tests_pciio_bar_base(0x7A000001u, 0), 0);
    CHECK_EQU(tests_pciio_bar_base(0x7A000006u, 0), 0);
    CHECK_EQU(tests_pciio_bar_base(0, 0), 0);

    /* GetLocation fails or answers out of range: the function is kept, not located. */
    for (int variant = 0; variant < 5; variant++) {
        fbs_reset(bs);
        fixture_basic();
        if (variant == 0)
            g_fbs.fn[1].loc_fail = 1;
        else if (variant == 1)
            g_fbs.fn[1].seg = 0x10000;
        else if (variant == 2)
            g_fbs.fn[1].bus = 256;
        else if (variant == 3)
            g_fbs.fn[1].dev = 32;
        else
            g_fbs.fn[1].fn = 8;
        tests_pciio_collect(bs, t);
        CHECK_EQ(fn_at(t, 1)->located, 0);
        CHECK(fn_at(t, 1)->is_nvme); /* still read, still marked */
        CHECK_EQ(t->n, 3);
    }

    /* A handle without a PCI I/O interface is counted and left out. */
    fbs_reset(bs);
    fixture_basic();
    g_fbs.fn[0].present = 0;
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->located, 3);
    CHECK_EQ(t->n, 2);
    CHECK_EQ(t->skipped, 1);
    CHECK_EQ(t->n_nvme, 1);
    CHECK_EQ(g_fbs.free_calls, 1);

    /* The device path: absent, not ACPI, a bad length, a bare end node. Never read past it. */
    for (int variant = 0; variant < 4; variant++) {
        fbs_reset(bs);
        fixture_basic();
        free(g_fbs.fn[1].dp);
        g_fbs.fn[1].dp = NULL;
        if (variant == 1) {
            g_fbs.fn[1].dp = xalloc(4);
            g_fbs.fn[1].dp[0] = 0x7F;
            g_fbs.fn[1].dp[1] = 0xFF;
            st16(g_fbs.fn[1].dp + 2, 4);
        } else if (variant == 2) {
            g_fbs.fn[1].dp = xalloc(8);
            g_fbs.fn[1].dp[0] = 2;
            g_fbs.fn[1].dp[1] = 1;
            st16(g_fbs.fn[1].dp + 2, 8); /* an ACPI node must be 12 bytes */
        } else if (variant == 3) {
            g_fbs.fn[1].dp = xalloc(4);
            g_fbs.fn[1].dp[0] = 1; /* hardware, PCI: not an ACPI node */
            g_fbs.fn[1].dp[1] = 1;
            st16(g_fbs.fn[1].dp + 2, 6);
        }
        tests_pciio_collect(bs, t);
        CHECK_EQ(fn_at(t, 1)->have_root, 0);
        CHECK(fn_at(t, 1)->is_nvme && fn_at(t, 1)->regs_state == TESTS_PCIIO_REGS_OK);
    }

    /* A header type that has no BAR (CardBus): no BAR is read. */
    fbs_reset(bs);
    fixture_basic();
    g_fbs.fn[2].cfg[3] = 2u << 16;
    tests_pciio_collect(bs, t);
    CHECK_EQ(fn_at(t, 2)->header_type, 2);
    CHECK_EQ(fn_at(t, 2)->bars_read, 0);
    CHECK_EQ(g_fbs.fn[2].ncfg, 4);
    CHECK(fn_at(t, 2)->cfg_ok);

    /* A class code that says NVMe on a function whose header is not an endpoint is not trusted. */
    fbs_reset(bs);
    fixture_basic();
    g_fbs.fn[1].cfg[3] = 1u << 16;
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->n_nvme, 0);
    CHECK_EQ(g_fbs.fn[1].nmem, 0);

    /* The protocol is absent, the call fails, there are no boot services: each is a state. */
    fbs_reset(bs);
    g_fbs.lhb_status = EFI_NOT_FOUND;
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->state, TESTS_PCIIO_NO_PROTOCOL);
    CHECK_EQU(t->status, EFI_NOT_FOUND);
    CHECK_EQ(t->n, 0);
    CHECK_EQ(g_fbs.free_calls, 0);
    fbs_reset(bs);
    g_fbs.lhb_status = EFI_OUT_OF_RESOURCES;
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->state, TESTS_PCIIO_LOCATE_FAILED);
    CHECK_EQU(t->status, EFI_OUT_OF_RESOURCES);
    fbs_reset(bs);
    tests_pciio_collect(NULL, t);
    CHECK_EQ(t->state, TESTS_PCIIO_NO_BOOT_SERVICES);
    bs->locate_handle_buffer = NULL;
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->state, TESTS_PCIIO_NO_BOOT_SERVICES);
    CHECK_EQ(g_fbs.lhb_calls, 0);
    fbs_reset(bs);
    tests_pciio_collect(bs, t); /* a handle list of zero is a list */
    CHECK_EQ(t->state, TESTS_PCIIO_OK);
    CHECK_EQ(t->located, 0);
    CHECK_EQ(g_fbs.free_calls, 1);

    /* More handles than the table holds: the first 64 are read, the rest are counted. */
    fbs_reset(bs);
    for (unsigned i = 0; i < TESTS_PCIIO_MAX + 6u; i++) {
        fakefn *f = fk_add(1, i % 200, i % 32, 0, 0x1000u + i, 0x02000000u, 0x0006u, 0);

        f->cfg[0x10 / 4] = 0x20000000u + 0x1000u * i;
    }
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->located, TESTS_PCIIO_MAX + 6u);
    CHECK_EQ(t->n, TESTS_PCIIO_MAX);
    CHECK_EQ(g_fbs.fn[TESTS_PCIIO_MAX].ncfg, 0); /* the 65th was not touched */
    CHECK_EQ(g_fbs.free_calls, 1);
    CHECK_EQ(g_fbs.pool_live, 0);
    CHECK_EQ(traps_hit(), 0);

    /* A table that was cleared by the collector: a second call forgets the first. */
    fbs_reset(bs);
    fixture_basic();
    tests_pciio_collect(bs, t);
    fbs_reset(bs);
    g_fbs.lhb_status = EFI_NOT_FOUND;
    tests_pciio_collect(bs, t);
    CHECK_EQ(t->n_nvme, 0);
    CHECK_EQ(t->n, 0);
    CHECK_EQ(t->located, 0);

    section("pciio helpers");
    /* The device path root. */
    memset(path, 0, sizeof path);
    path[0] = 2;
    path[1] = 1;
    st16(path + 2, 12);
    st32(path + 4, TESTS_PCIIO_HID_PCIE_ROOT);
    st32(path + 8, 6);
    CHECK_EQ(tests_pciio_dp_root(path, 12, &hid, &uid), TESTS_PCIIO_ROOT_OK);
    CHECK_EQU(hid, 0x0A0841D0u);
    CHECK_EQ(uid, 6);
    CHECK_EQ(tests_pciio_dp_root(path, 16, &hid, &uid), TESTS_PCIIO_ROOT_OK);
    CHECK_EQ(tests_pciio_dp_root(path, 11, &hid, &uid), TESTS_PCIIO_ROOT_BAD_LENGTH);
    CHECK_EQ(hid, 0); /* an answer that was refused leaves nothing behind */
    CHECK_EQ(tests_pciio_dp_root(path, 3, &hid, &uid), TESTS_PCIIO_ROOT_NO_PATH);
    CHECK_EQ(tests_pciio_dp_root(NULL, 12, &hid, &uid), TESTS_PCIIO_ROOT_NO_PATH);
    path[1] = 2; /* expanded ACPI: its UID may be a string, so it is not read */
    CHECK_EQ(tests_pciio_dp_root(path, 12, &hid, &uid), TESTS_PCIIO_ROOT_NOT_ACPI);
    path[1] = 1;
    path[0] = 1;
    CHECK_EQ(tests_pciio_dp_root(path, 12, &hid, &uid), TESTS_PCIIO_ROOT_NOT_ACPI);
    path[0] = 2;
    st16(path + 2, 13);
    CHECK_EQ(tests_pciio_dp_root(path, 16, &hid, &uid), TESTS_PCIIO_ROOT_BAD_LENGTH);
    /* Root bridge ids, and the names the Shell would print. */
    CHECK(tests_pciio_hid_is_pci_root(TESTS_PCIIO_HID_PCI_ROOT));
    CHECK(tests_pciio_hid_is_pci_root(TESTS_PCIIO_HID_PCIE_ROOT));
    CHECK(!tests_pciio_hid_is_pci_root(0x0A0141D0u));
    CHECK(!tests_pciio_hid_is_pci_root(0));
    {
        char name[48];

        tests_pciio_root_name(name, sizeof name, TESTS_PCIIO_HID_PCIE_ROOT, 6);
        CHECK_STR(name, "PcieRoot(0x6)");
        tests_pciio_root_name(name, sizeof name, TESTS_PCIIO_HID_PCI_ROOT, 0);
        CHECK_STR(name, "PciRoot(0x0)");
        tests_pciio_root_name(name, sizeof name, TESTS_PCIIO_HID_PCIE_ROOT, 0x1A);
        CHECK_STR(name, "PcieRoot(0x1A)");
        tests_pciio_root_name(name, sizeof name, 0x12345678u, 0x10);
        CHECK_STR(name, "Acpi(0x12345678,0x10)");
    }
    CHECK(tests_pciio_class_is_nvme(0x01080201u));
    CHECK(tests_pciio_class_is_nvme(0x010802FFu));
    CHECK(!tests_pciio_class_is_nvme(0x01080001u)); /* a different programming interface */
    CHECK(!tests_pciio_class_is_nvme(0x06040001u));
    CHECK_STR(tests_pciio_state_name(TESTS_PCIIO_NO_PROTOCOL), "NO PROTOCOL");
    CHECK_STR(tests_pciio_state_name(99), "?");
    CHECK_STR(tests_pciio_regs_name(TESTS_PCIIO_REGS_DECODE_OFF), "DECODE OFF");
    CHECK_STR(tests_pciio_regs_name(-1), "?");

    pciio_text_tests(bs, t);

    fbs_reset(bs);
    free(bs);
    free(t);
}

/* `--emit-pciio`: for each fixture, the raw dwords the fake firmware held and the text the module
 * made of them, so that probes/uefi/tests/host/pciio_text.py can decode the dwords itself and
 * compare. The raw lines come from the fake's own arrays, not from the module's record. */
int x_emit_pciio(void)
{
    static const char *const names[] = {"basic", "no-nvme"};

    for (int variant = 0; variant < 2; variant++) {
        efi_boot_services *bs = x_pciio_fake_bs(variant == 0);
        tests_pciio *t = xalloc(sizeof *t);
        char *text = xalloc(32768);
        size_t len = 0;

        tests_pciio_collect(bs, t);
        (void)tests_pciio_text(t, "TESTBUILD001", "HARDWARE", text, 32768, &len);
        printf("FIXTURE %s\n", names[variant]);
        for (unsigned i = 0; i < g_fbs.nhandles; i++) {
            const fakefn *f = &g_fbs.fn[i];

            printf("RAW %u seg=%u bus=%u dev=%u func=%u hid=%08X uid=%u cfg=", i, (unsigned)f->seg,
                   (unsigned)f->bus, (unsigned)f->dev, (unsigned)f->fn, (unsigned)f->dp[4] |
                   ((unsigned)f->dp[5] << 8) | ((unsigned)f->dp[6] << 16) | ((unsigned)f->dp[7] << 24),
                   (unsigned)f->dp[8] | ((unsigned)f->dp[9] << 8) | ((unsigned)f->dp[10] << 16) |
                       ((unsigned)f->dp[11] << 24));
            for (unsigned k = 0; k < 10; k++)
                printf("%s%08X", k ? "," : "", f->cfg[k]);
            printf(" reg=");
            for (unsigned k = 0; k < 14; k++)
                printf("%s%08X", k ? "," : "", f->reg[k]);
            printf("\n");
        }
        printf("TEXT ");
        for (size_t i = 0; i < len; i++)
            printf("%02X", (unsigned)(unsigned char)text[i]);
        printf("\n");
        free(text);
        free(t);
    }
    return 0;
}
