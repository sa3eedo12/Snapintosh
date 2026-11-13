/* SPDX-License-Identifier: MIT */
/* The declared-ranges table of the X2 image's `declared` variant
 * (-DTESTS_DECLARE_EBS_RANGES=1, tools/build-uefi-tests.py --variant declared).
 *
 * Why it exists. The reviewed slbounce fork, built with SLBOUNCE_SCOPED_SWEEP=1, no longer flushes
 * every loader and firmware range through the data cache before its Secure-Launch bounce. It
 * flushes only the physical ranges the application declared in the volatile variable named below,
 * and its ExitBootServices hook returns EFI_ABORTED, printing nothing, when the variable is absent,
 * malformed or names a range it cannot justify against the memory map of that moment. The default
 * X2 image declares nothing, so that build would refuse it. q1n1 writes the same table
 * (platform/uefi/main.c of the q1n1 tree, sp11_declare_ranges); this variant declares the one
 * range it can name, which is explained where it is declared (probe_main.c).
 *
 * Wire format. The layout below is an independently implemented copy of the one q1n1 and the fork
 * share (sp11-ebs-ranges.h in each tree, version 1, 160 bytes). It is deliberately not included
 * from either, because this tree has no build in common with theirs. The repository keeps such
 * copies in agreement with tests, the way nv2rec.c is kept in agreement with tools/nv2_record.py:
 * tools/test-uefi-tests.py checks the exact bytes, decodes them with the rules of
 * tools/sp11_ebs_sweep_model.py and, when SLBOUNCE_SP11_DIR names the fork, has the fork's own
 * validator accept them. Change TESTS_EBS_VERSION only together with both trees.
 *
 * No EFI type appears in this interface: the file is built natively for the host tests and
 * for the image alike. The variable's GUID and name are macros, not objects, for the same
 * reason the shared header makes them macros: each side wraps them in its own types.
 */
#ifndef TESTS_EBS_RANGES_H
#define TESTS_EBS_RANGES_H

#include <stddef.h>
#include <stdint.h>

#include "logic.h"

/* "SP11EBS1": the bytes 53 50 31 31 45 42 53 31, lowest first. */
#define TESTS_EBS_MAGIC UINT64_C(0x3153424531315053)
#define TESTS_EBS_VERSION 1u
#define TESTS_EBS_MAX_RANGES 8u
#define TESTS_EBS_TABLE_BYTES 160u

/* The fork issues no single cache operation longer than this for a declared range. 1 MiB is the
 * project's own cap on one GPU copy: a bounded unit of work, not a measurement of any hang. */
#define TESTS_EBS_CHUNK_CAP (1u << 20)

/* The fork reads at most this many descriptors from the map of the final GetMemoryMap and
 * refuses a larger map outright (src/bounce_main.c, SP11_SWEEP_MAX_DESCRIPTORS). */
#define TESTS_EBS_MAX_DESCRIPTORS 512u

/* The descriptor types the fork sweeps: loader, boot and runtime code and data, and ACPI
 * reclaim (1 to 6 and 9). Every other type (reserved 0, conventional 7, unusable 8, ACPI NVS,
 * memory-mapped I/O, PAL code, persistent) is never swept, so a range in one is refused. */
#define TESTS_EBS_TYPES_MASK ((1u << 1) | (1u << 2) | (1u << 3) | (1u << 4) | (1u << 5) | (1u << 6) | (1u << 9))

/* EFI_MEMORY_WB, attribute bit 3: the region can be write-back cacheable, so cache maintenance
 * on it means something. The fork refuses a descriptor without it. */
#define TESTS_EBS_ATTR_WB UINT64_C(0x8)

#define TESTS_EBS_PAGE_BYTES UINT64_C(4096)

/* VariableName is u"Q1N1Sp11EbsRanges" and VendorGuid is the initializer below. The attributes are
 * EFI_VARIABLE_BOOTSERVICE_ACCESS (UEFI section 8.2) and nothing else: the fork refuses any other
 * value, and a volatile, boot-services-only variable is what its hook reads, before it calls the
 * real ExitBootServices. */
#define TESTS_EBS_VARIABLE_ATTRIBUTES 0x00000002u
#define TESTS_EBS_GUID_INIT { 0x1a3c9e02, 0x7b6f, 0x4d81, { 0x9c, 0x2e, 0x53, 0x50, 0x31, 0x31, 0x45, 0x52 } }
#define TESTS_EBS_VARIABLE_NAME_U16 \
    { 'Q', '1', 'N', '1', 'S', 'p', '1', '1', 'E', 'b', 's', 'R', 'a', 'n', 'g', 'e', 's', 0 }

typedef struct {
    uint64_t base;
    uint64_t size;
} tests_ebs_range;

/* Written as a struct, so the host must be little-endian like the image; the asserts below keep
 * the layout the fork's validator reads. */
typedef struct {
    uint64_t magic;
    uint32_t version;
    uint32_t count;     /* ranges in use, 1 to TESTS_EBS_MAX_RANGES */
    uint32_t chunk_cap; /* bytes, not zero */
    uint32_t reserved;  /* the fork's validator refuses anything but 0 */
    tests_ebs_range range[TESTS_EBS_MAX_RANGES]; /* slots from count on must be all zero */
    uint64_t checksum;  /* FNV-1a over every preceding byte */
} tests_ebs_table;

_Static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "the table is written in the host's byte order");
_Static_assert(sizeof(tests_ebs_range) == 16, "one declared range is 16 bytes");
_Static_assert(sizeof(tests_ebs_table) == TESTS_EBS_TABLE_BYTES, "the declared-ranges table is 160 bytes");
_Static_assert(offsetof(tests_ebs_table, magic) == 0, "magic offset");
_Static_assert(offsetof(tests_ebs_table, version) == 8, "version offset");
_Static_assert(offsetof(tests_ebs_table, count) == 12, "count offset");
_Static_assert(offsetof(tests_ebs_table, chunk_cap) == 16, "chunk_cap offset");
_Static_assert(offsetof(tests_ebs_table, reserved) == 20, "reserved offset");
_Static_assert(offsetof(tests_ebs_table, range) == 24, "range offset");
_Static_assert(offsetof(tests_ebs_table, checksum) == 152, "checksum offset");

/* Why a range was refused. The checks run in this order, so a range with two defects reports
 * the first. */
enum tests_ebs_status {
    TESTS_EBS_OK = 0,
    TESTS_EBS_SIZE_ZERO,
    TESTS_EBS_UNALIGNED,             /* base is not a multiple of 4 KiB */
    TESTS_EBS_WRAPS,                 /* base + size does not fit in 64 bits */
    TESTS_EBS_NO_MAP,                /* no map, or a map with no descriptor */
    TESTS_EBS_MAP_TOO_LARGE,         /* more descriptors than the fork reads */
    TESTS_EBS_MAP_INVALID,           /* a descriptor with no pages, or whose end wraps */
    TESTS_EBS_NOT_IN_ONE_DESCRIPTOR, /* inside none, or inside more than one */
    TESTS_EBS_WRONG_TYPE,
    TESTS_EBS_NOT_WRITE_BACK,
};

/* The reason as the console line prints it: upper case, no '=' and no other key-like text. */
const char *tests_ebs_status_name(int status);

uint64_t tests_ebs_fnv1a(const void *data, size_t n);

/* TESTS_EBS_OK iff [base, base + size) would be swept by the fork's rules, which are the rules
 * of tools/sp11_ebs_sweep_model.py's scoped_sweep(): the whole range lies in exactly one
 * descriptor, that descriptor has a swept type, and it has EFI_MEMORY_WB. The refusals that are
 * this image's own make a refusal clearer than the fork's silent one: a base that is not page
 * aligned, a range that wraps, and a map that is empty, larger than the fork reads or malformed.
 * `map` is the image's own parsed map (probe_memmap_init has already accepted it); descriptors
 * are read at the firmware's stride. Nothing here writes. */
int tests_ebs_check_range(const probe_memmap *map, uint64_t base, uint64_t size);

/* Fills *t with the table that declares exactly [base, base + size) and returns TESTS_EBS_OK,
 * or returns the check's refusal. A refused call leaves *t all zero, which the fork's validator
 * rejects (no magic), so a caller that ignores the result still cannot declare anything. */
int tests_ebs_build(tests_ebs_table *t, const probe_memmap *map, uint64_t base, uint64_t size);

#endif /* TESTS_EBS_RANGES_H */
