/* SPDX-License-Identifier: MIT */
/* What a device test is allowed to touch.
 *
 * Every test works through this interface and has no other way to reach
 * memory outside the image: a guarded 32-bit read and a guarded system
 * register read. There is no write operation at all, so "read-only by
 * construction" does not depend on the tests being careful. A read is made only
 * inside a window that the test first justified (an ACPI table said so, or a
 * register it had just read said so); an address outside every window is
 * refused without being touched. On the image the bus is backed by the guarded
 * access code in sys_aarch64.c; the host tests back it with a model that
 * records every access.
 */
#ifndef TESTS_ENV_H
#define TESTS_ENV_H

#include <stddef.h>
#include <stdint.h>

#include "acpi.h"

typedef struct {
    void *ctx;
    /* A guarded 32-bit read. 0 on success; non-zero if the access faulted, in
     * which case *v is all ones. addr is 4-byte aligned. */
    int (*rd32)(void *ctx, uint64_t addr, uint32_t *v);
    /* A guarded system register read by enum tests_sysreg_id. */
    int (*rd_sys)(void *ctx, int sysreg, uint64_t *v);
    /* Faults skipped so far (monotonic), for the report. */
    unsigned (*faults)(void *ctx);
} tests_bus;

#define TESTS_ALLOW_MAX 16u

typedef struct {
    unsigned n;
    unsigned refused;  /* device windows tests_allow_add_device turned away */
    unsigned unlisted; /* admitted, but the memory map does not show them as MMIO */
    struct {
        uint64_t base;
        uint64_t len;
        const char *why; /* where the window came from, for the report */
    } r[TESTS_ALLOW_MAX];
} tests_allow;

void tests_allow_init(tests_allow *a);
/* Adds a window. -1 if the list is full, the window is empty, sits at address 0,
 * wraps, or reaches past TESTS_PA_LIMIT. */
int tests_allow_add(tests_allow *a, uint64_t base, uint64_t len, const char *why);
/* 1 only if [addr, addr + len) lies inside one single window. */
int tests_allow_covers(const tests_allow *a, uint64_t addr, uint64_t len);

/* Device windows.
 *
 * The base of a device window comes out of a firmware table (MADT, MCFG, IORT)
 * or out of a BAR that firmware programmed, so it is data and may be zero,
 * misaligned, wrapping, or the address of ordinary RAM. A RAM address answers a
 * read with whatever is stored there, which reads as a register and yields an
 * observation that never came from a device. A window is therefore admitted only
 * after both checks below, and a refusal names its reason instead of leaving the
 * window silently unreadable. */
enum {
    TESTS_WIN_OK = 0,
    TESTS_WIN_ZERO,       /* base 0 */
    TESTS_WIN_MISALIGNED, /* base is not aligned to the frame the table names */
    TESTS_WIN_RANGE,      /* empty, wrapping, or reaching past TESTS_PA_LIMIT */
    TESTS_WIN_FULL,       /* the allow-list has no room left */
    TESTS_WIN_NO_MAP,     /* no validated UEFI memory map to judge the window by */
    TESTS_WIN_CONFLICT,   /* a descriptor that is not MMIO (RAM, firmware, ACPI, reserved) overlaps it */
    TESTS_WIN_NOT_MMIO    /* strict policy only: not wholly inside MMIO descriptors */
};

/* No Armv8 implementation issues a physical address of 52 bits or more, so a
 * table value at or above it is not an address. */
#define TESTS_PA_LIMIT (1ull << 52)

/* EfiMemoryMappedIO (11) and EfiMemoryMappedIOPortSpace (12). */
#define TESTS_MMIO_TYPES_MASK ((1u << 11) | (1u << 12))

/* Alignment of the frame a table entry points at: the GICv3 distributor and the
 * ITS are 64 KiB frames, redistributor discovery ranges and NVMe BARs are page
 * granular, an ECAM window is whole buses of 1 MiB each, and an SMMUv3 register
 * page is 64 KiB. */
#define TESTS_ALIGN_GICD 0x10000ull
#define TESTS_ALIGN_GICR 0x1000ull
#define TESTS_ALIGN_ITS 0x10000ull
#define TESTS_ALIGN_ECAM 0x100000ull
#define TESTS_ALIGN_SMMU 0x10000ull
#define TESTS_ALIGN_NVME 0x1000ull

/* Short upper-case reason for a row, at most 19 characters. */
const char *tests_window_name(int status);

/* Pure checks on the numbers alone: TESTS_WIN_OK, _ZERO, _RANGE or _MISALIGNED.
 * align is a power of two, or 0 for none. */
int tests_window_check(uint64_t base, uint64_t len, uint64_t align);

/* What the UEFI memory map says about [base, base + len): TESTS_WIN_OK,
 * _NO_MAP, _CONFLICT or (when strict_mmio) _NOT_MMIO.
 *
 * A descriptor of any other type than MMIO overlapping the window is always a
 * refusal, however many descriptors also claim it. Device registers that the
 * map does not mention at all pass unless strict_mmio is set. OBSERVED: the map
 * of the QEMU machine the model image runs on lists MMIO only for its flash and
 * RTC windows and none of the GIC, ECAM, SMMU or NVMe windows, so requiring MMIO
 * coverage by default would leave T05 to T07 reading nothing there; whether the
 * Surface Pro 11 firmware describes them is for the first hardware run to show
 * (the probe's memmap.txt). strict_mmio is the build-time option for an operator
 * who wants that rule anyway. */
int tests_window_policy(const probe_memmap *map, int strict_mmio, uint64_t base, uint64_t len);

/* Checks, applies the memory-map policy and adds the window. Returns
 * TESTS_WIN_OK when it was added, else the reason it was not; allow->refused
 * counts the refusals and allow->unlisted the windows admitted without MMIO
 * coverage in the map. */
int tests_allow_add_device(tests_allow *a, const probe_memmap *map, int strict_mmio, uint64_t base,
                           uint64_t len, uint64_t align, const char *why);

enum {
    RD_OK = 0,
    RD_FAULT = -1,       /* the access faulted; the value is all ones */
    RD_UNJUSTIFIED = -2  /* not inside a justified window, or misaligned: not touched */
};

int tests_rd32(const tests_bus *bus, const tests_allow *allow, uint64_t addr, uint32_t *v);
/* Two 32-bit reads of a 64-bit register (low word first), so that no 64-bit
 * access is ever made to a device that might not support one. */
int tests_rd64(const tests_bus *bus, const tests_allow *allow, uint64_t addr, uint64_t *v);

#endif /* TESTS_ENV_H */
