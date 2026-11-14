/* SPDX-License-Identifier: MIT */
/* What the firmware itself knows about the PCI functions of the machine, read through its own
 * EFI_PCI_IO_PROTOCOL before ExitBootServices, and the compact table T06 uses after it.
 *
 * Why this exists. T06 used to walk every ECAM window the MCFG lists. On the Surface Pro 11 the
 * walk reached segment 3 (base 0x740000000), the read never returned and the board reset itself
 * (OBSERVED, boot 3; docs/SP11-X2-V3.md). The MCFG lists eight root complexes and the firmware
 * used one of them for the NVMe: segment 6, whose device path is
 * PcieRoot(0x6)/Pci(0x0,0x0)/Pci(0x0,0x0)/NVMe(...) (OBSERVED in the firmware's block device
 * path; "segment 6 is ECAM 0x70000000" is INFERRED from _UID = _SEG in the DSDT). A root complex
 * the firmware never used is a root complex whose power, clock and link state nobody has seen
 * work, so T06 now reads only a segment where the firmware's own PCI I/O shows an NVMe controller.
 *
 * What this module may do. Collect, once, read-only, through the firmware's protocol:
 *   - EFI_PCI_IO_PROTOCOL.GetLocation, to learn (segment, bus, device, function);
 *   - EFI_PCI_IO_PROTOCOL.Pci.Read, 32-bit, at the configuration offsets listed in
 *     pciio.c (identification, command and status, class code, header type, the BARs, and for
 *     a PCI-to-PCI bridge the bus numbers);
 *   - EFI_PCI_IO_PROTOCOL.Mem.Read, 32-bit, on BAR 0 of a function whose class code says NVMe,
 *     at seven NVMe register offsets and no others.
 * It never writes. The structure below declares every member the module does not call as an
 * untyped placeholder, so a call is a compile error, and the host tests give a fake protocol whose
 * every other member fails the test when it is called.
 *
 * Layouts: UEFI Specification 2.10 section 14.4 (EFI_PCI_IO_PROTOCOL), section 10.3.3 (ACPI device
 * path node), PCI Firmware / PCI Local Bus configuration header offsets as listed in Linux v6.12
 * include/uapi/linux/pci_regs.h, NVMe register offsets as listed in include/linux/nvme.h. Nothing
 * is copied from those files; the offsets are facts.
 */
#ifndef TESTS_PCIIO_H
#define TESTS_PCIIO_H

#include <stddef.h>
#include <stdint.h>

#include "efi_types.h"

/* EFI_PCI_IO_PROTOCOL_GUID, section 14.4. */
#define EFI_GUID_PCI_IO \
    { 0x4CF5B200, 0x68B8, 0x4CA5, { 0x9E, 0xEC, 0xB2, 0x3E, 0x3F, 0x50, 0x02, 0x9A } }

/* EFI_PCI_IO_PROTOCOL_WIDTH: only the 32-bit access is ever requested. NVMe's 64-bit registers
 * are read as two dwords, low word first, which the NVMe base specification allows for reads. */
#define EFI_PCI_IO_WIDTH_UINT32 2u

typedef struct efi_pci_io efi_pci_io;

typedef efi_status (*efi_pci_io_mem_read_fn)(efi_pci_io *self, uint32_t width, uint8_t bar_index,
                                             uint64_t offset, efi_uintn count, void *buffer);
typedef efi_status (*efi_pci_io_cfg_read_fn)(efi_pci_io *self, uint32_t width, uint32_t offset,
                                             efi_uintn count, void *buffer);
typedef efi_status (*efi_pci_io_location_fn)(efi_pci_io *self, efi_uintn *segment, efi_uintn *bus,
                                             efi_uintn *device, efi_uintn *function);

/* The members are in the order of the specification and every one of their offsets is pinned
 * below: a swapped or missing member would turn the call this module means as Pci.Read into
 * Pci.Write or SetBarAttributes, a write to a PCI function, with no compiler diagnostic. */
struct efi_pci_io {
    void *poll_mem;
    void *poll_io;
    struct {
        efi_pci_io_mem_read_fn read;
        void *write;
    } mem;
    struct {
        void *read;
        void *write;
    } io;
    struct {
        efi_pci_io_cfg_read_fn read;
        void *write;
    } pci;
    void *copy_mem;
    void *map;
    void *unmap;
    void *allocate_buffer;
    void *free_buffer;
    void *flush;
    efi_pci_io_location_fn get_location;
    void *attributes;
    void *get_bar_attributes;
    void *set_bar_attributes;
    uint64_t rom_size;
    void *rom_image;
};

_Static_assert(offsetof(efi_pci_io, poll_mem) == 0, "PollMem slot");
_Static_assert(offsetof(efi_pci_io, poll_io) == 8, "PollIo slot");
_Static_assert(offsetof(efi_pci_io, mem.read) == 16, "Mem.Read slot");
_Static_assert(offsetof(efi_pci_io, mem.write) == 24, "Mem.Write slot");
_Static_assert(offsetof(efi_pci_io, io.read) == 32, "Io.Read slot");
_Static_assert(offsetof(efi_pci_io, io.write) == 40, "Io.Write slot");
_Static_assert(offsetof(efi_pci_io, pci.read) == 48, "Pci.Read slot");
_Static_assert(offsetof(efi_pci_io, pci.write) == 56, "Pci.Write slot");
_Static_assert(offsetof(efi_pci_io, copy_mem) == 64, "CopyMem slot");
_Static_assert(offsetof(efi_pci_io, map) == 72, "Map slot");
_Static_assert(offsetof(efi_pci_io, unmap) == 80, "Unmap slot");
_Static_assert(offsetof(efi_pci_io, allocate_buffer) == 88, "AllocateBuffer slot");
_Static_assert(offsetof(efi_pci_io, free_buffer) == 96, "FreeBuffer slot");
_Static_assert(offsetof(efi_pci_io, flush) == 104, "Flush slot");
_Static_assert(offsetof(efi_pci_io, get_location) == 112, "GetLocation slot");
_Static_assert(offsetof(efi_pci_io, attributes) == 120, "Attributes slot");
_Static_assert(offsetof(efi_pci_io, get_bar_attributes) == 128, "GetBarAttributes slot");
_Static_assert(offsetof(efi_pci_io, set_bar_attributes) == 136, "SetBarAttributes slot");
_Static_assert(offsetof(efi_pci_io, rom_size) == 144, "RomSize slot");
_Static_assert(offsetof(efi_pci_io, rom_image) == 152, "RomImage slot");
_Static_assert(sizeof(efi_pci_io) == 160, "EFI_PCI_IO_PROTOCOL size");

/* ---- What is read, named ---- */

/* Configuration space (PCI Local Bus Specification header layout). */
#define TESTS_PCIIO_CFG_ID 0x00u        /* vendor id in 15:0, device id in 31:16 */
#define TESTS_PCIIO_CFG_COMMAND 0x04u   /* command in 15:0, status in 31:16 */
#define TESTS_PCIIO_CFG_CLASS_REV 0x08u /* class code in 31:8, revision in 7:0 */
#define TESTS_PCIIO_CFG_HEADER 0x0Cu    /* header type in 23:16 (bit 7: multifunction) */
#define TESTS_PCIIO_CFG_BAR0 0x10u      /* BAR0..BAR5 at 0x10, 0x14, ... 0x24 */
#define TESTS_PCIIO_CFG_BUSES 0x18u     /* header type 1: primary, secondary, subordinate bus */
#define TESTS_PCIIO_BARS 6u
#define TESTS_PCIIO_BARS_BRIDGE 2u      /* a PCI-to-PCI bridge has BAR0 and BAR1 only */
#define TESTS_PCIIO_HDR_ENDPOINT 0u
#define TESTS_PCIIO_HDR_BRIDGE 1u

#define TESTS_PCIIO_CMD_MEMORY 0x2u /* command register: memory space enable */

/* Class code (base 0x01 mass storage, sub-class 0x08 non-volatile memory controller, programming
 * interface 0x02 NVM Express). */
#define TESTS_PCIIO_CLASS_NVME 0x010802u

/* NVMe controller registers (NVM Express Base Specification, controller registers), byte offsets
 * from BAR0. These seven, and no other, are read: they have no side effect on a read. */
#define TESTS_NVME_CAP 0x00u  /* 64 bit, constant */
#define TESTS_NVME_VS 0x08u   /* 32 bit, constant */
#define TESTS_NVME_CC 0x14u
#define TESTS_NVME_CSTS 0x1Cu
#define TESTS_NVME_AQA 0x24u
#define TESTS_NVME_ASQ 0x28u  /* 64 bit */
#define TESTS_NVME_ACQ 0x30u  /* 64 bit */

/* The ACPI HID of a PCI root bridge as an EISA id: PNP0A03 (PciRoot) and PNP0A08 (PcieRoot). */
#define TESTS_PCIIO_HID_PCI_ROOT 0x0A0341D0u
#define TESTS_PCIIO_HID_PCIE_ROOT 0x0A0841D0u

/* ---- The table ---- */

/* Handles looked at. The unit's firmware has far fewer PCI functions; a firmware with more is
 * recorded as truncated, never read past the table. */
#define TESTS_PCIIO_MAX 64u

enum tests_pciio_state {
    TESTS_PCIIO_NOT_COLLECTED = 0, /* zero: a table nobody filled says so */
    TESTS_PCIIO_OK,                /* the handle list was read; it may hold no NVMe */
    TESTS_PCIIO_NO_BOOT_SERVICES,
    TESTS_PCIIO_NO_PROTOCOL,       /* LocateHandleBuffer: no handle has a PCI I/O protocol */
    TESTS_PCIIO_LOCATE_FAILED,     /* LocateHandleBuffer returned some other error */
    TESTS_PCIIO_STATE_COUNT
};

/* Why a function's NVMe registers carry no value. */
enum tests_pciio_regs {
    TESTS_PCIIO_REGS_NONE = 0,  /* not an NVMe function: nothing was asked */
    TESTS_PCIIO_REGS_OK,
    TESTS_PCIIO_REGS_NO_BAR0,    /* BAR0 is not an assigned memory BAR */
    TESTS_PCIIO_REGS_DECODE_OFF, /* memory space enable is off: a read would be a stray access */
    TESTS_PCIIO_REGS_READ_FAILED,
    TESTS_PCIIO_REGS_COUNT
};

typedef struct {
    uint8_t located;     /* GetLocation answered and every number is in range */
    uint16_t segment;
    uint8_t bus, device, function;
    uint8_t base_ok;     /* the identification, command, class and header dwords were read */
    uint8_t cfg_ok;      /* every configuration dword asked for was read */
    uint8_t header_type; /* bits 6:0 of the header type byte */
    uint8_t bars_read;   /* leading entries of bar[] that were read */
    uint32_t id;         /* 0x00 */
    uint32_t cmd_status; /* 0x04 */
    uint32_t class_rev;  /* 0x08: class code << 8 | revision */
    uint32_t hdr;        /* 0x0C, the whole dword */
    uint32_t bar[TESTS_PCIIO_BARS];
    uint32_t buses;      /* 0x18, meaningful for a header type 1 function only */
    uint8_t have_root;   /* the device path starts with an ACPI device path node */
    uint32_t root_hid, root_uid;
    uint8_t is_nvme;     /* the class code says NVMe */
    uint8_t regs_state;  /* enum tests_pciio_regs */
    uint64_t bar0_base;  /* the memory BAR0 base the BAR dwords decode to, 0 if none */
    uint64_t cap, asq, acq;
    uint32_t vs, cc, csts, aqa;
} tests_pciio_fn;

typedef struct {
    int state;         /* enum tests_pciio_state */
    uint64_t status;   /* the EFI status behind NO_PROTOCOL or LOCATE_FAILED */
    uint32_t located;  /* handles the firmware listed */
    uint32_t n;        /* entries kept: at most TESTS_PCIIO_MAX */
    uint32_t n_nvme;   /* kept entries whose class code says NVMe */
    uint32_t skipped;  /* listed handles with no usable PCI I/O interface */
    tests_pciio_fn f[TESTS_PCIIO_MAX];
} tests_pciio;

/* Reads the table through the firmware's boot services. Call before ExitBootServices, with the
 * table zero-initialised or not: it clears it first. A NULL bs, or a bs without the services
 * used, leaves state NO_BOOT_SERVICES. */
void tests_pciio_collect(efi_boot_services *bs, tests_pciio *out);

/* The first node of a device path as a root bridge: 0 and *hid, *uid when it is an ACPI device
 * path node (type 2, subtype 1, 12 bytes) lying inside max_len bytes, else a negative reason. The
 * node header is validated before any further byte is read, so a path that is only an end node is
 * never read past. */
enum {
    TESTS_PCIIO_ROOT_OK = 0,
    TESTS_PCIIO_ROOT_NO_PATH = -1,
    TESTS_PCIIO_ROOT_NOT_ACPI = -2,
    TESTS_PCIIO_ROOT_BAD_LENGTH = -3
};
int tests_pciio_dp_root(const uint8_t *path, size_t max_len, uint32_t *hid, uint32_t *uid);

/* True for a root bridge HID: PNP0A03 or PNP0A08. */
int tests_pciio_hid_is_pci_root(uint32_t hid);

/* The base a memory BAR decodes to (lo is the BAR dword, hi the next one for a 64-bit BAR), or 0
 * for an I/O BAR, a reserved memory type or an unassigned (zero) BAR. */
uint64_t tests_pciio_bar_base(uint32_t lo, uint32_t hi);

static inline int tests_pciio_class_is_nvme(uint32_t class_rev)
{
    return (class_rev >> 8) == TESTS_PCIIO_CLASS_NVME;
}

const char *tests_pciio_state_name(int state);
const char *tests_pciio_regs_name(int regs);

/* The decoded text of the table, as the file pciio.txt next to the probe's dump. build_id is the
 * image's 12-character id, kind "HARDWARE" or "MODEL". *len receives the bytes written; the
 * result is 0 when everything fitted, -1 when cap cut it short (what fitted is still valid text,
 * and the closing line is missing). */
int tests_pciio_text(const tests_pciio *t, const char *build_id, const char *kind, char *buf,
                     size_t cap, size_t *len);

/* "PcieRoot(0x6)", "PciRoot(0x0)" or "Acpi(0x<hid>,0x<uid>)": how the UEFI Shell prints the
 * node, so a line of the file can be set next to a Shell's device path. */
void tests_pciio_root_name(char *out, size_t cap, uint32_t hid, uint32_t uid);

#endif /* TESTS_PCIIO_H */
