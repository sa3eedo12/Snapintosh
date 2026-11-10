/* SPDX-License-Identifier: MIT */
/* Pure, host-testable logic for the SP11 EL2 probe.
 *
 * Everything here is plain C over caller-supplied buffers: no UEFI call, no
 * system register, no allocation. The same translation unit is built for the
 * EFI image (freestanding, AArch64) and for the native sanitizer test, which
 * is what lets the fail-closed policies be exercised with deliberately
 * hostile inputs instead of being trusted on the first hardware run.
 */
#ifndef PROBE_LOGIC_H
#define PROBE_LOGIC_H

#include <stddef.h>
#include <stdint.h>

/* Freestanding primitives. The compiler may emit calls to memcpy/memset for
 * struct copies even under -ffreestanding, so mem.c exports the libc names as
 * thin forwarders to these. They are marked no_builtin so the loop idiom
 * recogniser cannot turn the loops back into calls to themselves. */
void *probe_memcpy(void *dst, const void *src, size_t n);
void *probe_memmove(void *dst, const void *src, size_t n);
void *probe_memset(void *dst, int c, size_t n);
int probe_memcmp(const void *a, const void *b, size_t n);
size_t probe_strlen(const char *s);

uint16_t probe_le16(const uint8_t *p);
uint32_t probe_le32(const uint8_t *p);
uint64_t probe_le64(const uint8_t *p);
uint8_t probe_sum8(const uint8_t *p, size_t n);

/* CRC-8/SMBUS: polynomial 0x07, init 0, no reflection, no final xor.
 * crc8("123456789") == 0xF4. Printed at the end of every on-screen line so a
 * photographed line can be verified offline (probes/uefi/verify-lines.py). */
uint8_t probe_crc8(const void *data, size_t n);

/* Bounded string builder. buf is always NUL terminated when cap > 0; any
 * write that does not fit sets overflow and is dropped. Callers treat
 * overflow as a bug in a fixed-format line, never as silent truncation. */
typedef struct {
    char *buf;
    size_t cap;
    size_t len;
    int overflow;
} probe_sb;

void probe_sb_init(probe_sb *sb, char *buf, size_t cap);
void probe_sb_putc(probe_sb *sb, char c);
void probe_sb_puts(probe_sb *sb, const char *s);
/* Appends exactly `digits` (1..16) uppercase hex digits of v. */
void probe_sb_hex(probe_sb *sb, uint64_t v, unsigned digits);
void probe_sb_dec(probe_sb *sb, uint64_t v);
/* Pads with spaces until the builder holds `column` characters. */
void probe_sb_pad(probe_sb *sb, size_t column);

/* ---- EFI device path: may this volume receive a dump? ----
 *
 * The probe writes only to a volume that the firmware reports as USB mass
 * storage. Classification walks the raw device path with explicit bounds and
 * fails closed: anything malformed, ambiguous or non-USB is a refusal. */
enum probe_dp_class {
    PROBE_DP_MALFORMED = 0,
    PROBE_DP_NO_USB = 1,
    PROBE_DP_USB = 2,
    PROBE_DP_AMBIGUOUS = 3,
};

#define PROBE_DP_MAX_NODES 64u

/* p/max_len describe the whole device path buffer including the end node.
 * USB means a MESSAGING node of subtype 0x05 (USB, 6 bytes) or 0x0F (USB
 * class, 11 bytes) and no SATA/NVMe/SCSI/UFS/SD/eMMC/ATAPI transport node. */
enum probe_dp_class probe_dp_classify(const uint8_t *p, size_t max_len);
const char *probe_dp_class_name(enum probe_dp_class c);

/* ---- EFI memory map ---- */

#define PROBE_MEMMAP_MAX_DESC 8192u
#define PROBE_MEMMAP_MIN_DESC_SIZE 40u
#define PROBE_MEMMAP_MAX_DESC_SIZE 256u
#define PROBE_MEMMAP_MAX_HOPS 64u

/* Memory types the probe will read firmware tables from: loader, boot and
 * runtime data/code, ACPI reclaim and ACPI NVS. Reserved, MMIO, conventional,
 * unusable and persistent regions are refused: reserved ranges may be fenced
 * off by a hardware firewall, so a stray pointer into one is an external abort
 * rather than a failed read. */
#define PROBE_READABLE_TYPES_MASK                                                               \
    ((1u << 2) | (1u << 3) | (1u << 4) | (1u << 5) | (1u << 6) | (1u << 9) | (1u << 10))

/* Types counted as RAM for the on-screen low/high summary. */
#define PROBE_RAM_TYPES_MASK                                                                    \
    ((1u << 1) | (1u << 2) | (1u << 3) | (1u << 4) | (1u << 5) | (1u << 6) | (1u << 7) |        \
     (1u << 9) | (1u << 10))

enum {
    PROBE_MM_OK = 0,
    PROBE_MM_BAD_ARGS = -1,
    PROBE_MM_BAD_DESC_SIZE = -2,
    PROBE_MM_BAD_MAP_SIZE = -3,
    PROBE_MM_TOO_MANY = -4,
    PROBE_MM_BAD_DESC = -5,
};

typedef struct {
    const uint8_t *base;
    size_t desc_size; /* firmware stride, not sizeof(efi_memory_descriptor) */
    size_t count;
} probe_memmap;

/* Validates a raw map of map_size bytes held in a buffer of buf_len bytes.
 * Every descriptor must be page aligned, non-empty and free of address
 * overflow; the descriptor stride must be plausible and divide map_size. */
int probe_memmap_init(probe_memmap *m, const void *map, size_t map_size, size_t buf_len,
                      size_t desc_size);
/* Extracts descriptor i; returns 0 on success, -1 if out of range. */
int probe_memmap_get(const probe_memmap *m, size_t i, uint32_t *type, uint64_t *phys,
                     uint64_t *virt, uint64_t *pages, uint64_t *attr);
/* 1 iff [addr, addr+len) is fully inside descriptors whose type bit is set in
 * allowed_mask, and no descriptor of any other type touches it. A range may
 * span several adjacent descriptors. */
int probe_memmap_covers(const probe_memmap *m, uint64_t addr, uint64_t len, uint32_t allowed_mask);
/* 1 iff some descriptor whose type is NOT in allowed_mask overlaps
 * [addr, addr+len). Unlike probe_memmap_covers it does not require the range
 * to be described at all: it answers "does the firmware say this is something
 * else", which is what a device window must not be. */
int probe_memmap_overlaps_other(const probe_memmap *m, uint64_t addr, uint64_t len,
                                uint32_t allowed_mask);
/* Type of the descriptor containing addr, or -1 (none, or descriptors that
 * contain it disagree on the type). */
int probe_memmap_type_at(const probe_memmap *m, uint64_t addr);
/* Lowest start and highest end over the RAM types; returns 0 if none. */
int probe_memmap_ram_bounds(const probe_memmap *m, uint64_t *lo, uint64_t *hi);
const char *probe_memtype_name(uint32_t type);

/* Data-cache maintenance over [addr, addr + len): op is called once with the
 * address of each cache line (`line` bytes, a power of two from 16 to 4096)
 * the range touches, and the number of calls is returned. A request that
 * cannot be served safely makes no call and returns 0: no op, zero length, a
 * bad line size, or a range longer than PROBE_CACHE_MAX_BYTES (the largest
 * framebuffer this image would ever clean). A range running past the top of
 * the address space is clipped to it. The loop is bounded by a line count
 * computed without address wrap, never by comparing a running address with
 * the end: near the top of the address space that comparison never becomes
 * false and turns the maintenance loop into an endless one. */
#define PROBE_CACHE_MAX_BYTES (256ull << 20)
typedef void (*probe_cache_op)(uint64_t line_addr);
uint64_t probe_cache_walk(uint64_t addr, uint64_t len, uint64_t line, probe_cache_op op);

/* ---- Decision tables ---- */

/* Exception level in a raw CurrentEL value, or -1 when a RES0 bit is set. */
int probe_currentel_decode(uint64_t raw);

enum probe_psci_conduit {
    PROBE_PSCI_NONE = 0,
    PROBE_PSCI_SMC = 1,
    PROBE_PSCI_HVC = 2,
};

/* FADT ARM_BOOT_ARCH (offset 129, ACPI 5.1+): bit0 PSCI_COMPLIANT, bit1
 * PSCI_USE_HVC, bits 2-15 reserved. A short table, an old revision or any
 * reserved bit yields NONE rather than a guess. */
enum probe_psci_conduit probe_psci_conduit_from_fadt(unsigned fadt_revision, size_t fadt_length,
                                                     uint16_t arm_boot_arch);
const char *probe_psci_conduit_name(enum probe_psci_conduit c);

/* ExitBootServices is attempted only with a usable framebuffer, a layout that
 * fits the report and a validated memory map: after it the screen is the only
 * output channel. */
int probe_ebs_allowed(int have_framebuffer, int have_layout, int memmap_ok);
/* Retry only for EFI_INVALID_PARAMETER (stale map key) and only while
 * attempts remain; every other error is final. */
int probe_ebs_retry(uint64_t status, unsigned attempt, unsigned max_attempts);

#endif /* PROBE_LOGIC_H */
