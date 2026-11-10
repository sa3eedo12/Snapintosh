/* SPDX-License-Identifier: MIT */
/* ACPI and SMBIOS discovery for the SP11 EL2 probe, as pure code.
 *
 * Firmware tables are untrusted input here: the probe runs on a machine whose
 * firmware it has never seen, before the operating system exists, and one
 * wild read can be an external abort. Every pointer is therefore reached only
 * through probe_phys_ops, which refuses anything the UEFI memory map does not
 * prove readable, and every length, count and checksum is validated before it
 * is used to size a read or a loop.
 *
 * Layout references: ACPI 6.5 sections 5.2.5 (RSDP), 5.2.6 (headers), 5.2.9
 * (FADT), 5.2.12 (MADT, GICC) and SMBIOS 3.x/2.x entry point structures.
 */
#ifndef PROBE_ACPI_H
#define PROBE_ACPI_H

#include <stddef.h>
#include <stdint.h>

#include "logic.h"

/* Access to firmware memory. read() copies len bytes from physical address
 * addr and returns 0, or non-zero if any byte of the range is not provably
 * readable. covers() answers the same question without copying. */
typedef struct {
    int (*read)(void *ctx, uint64_t addr, void *dst, size_t len);
    int (*covers)(void *ctx, uint64_t addr, uint64_t len);
} probe_phys_ops;

#define PROBE_ACPI_HDR_LEN 36u
#define PROBE_ACPI_MAX_ROOT_ENTRIES 128u
#define PROBE_ACPI_MAX_ENTRIES 144u
#define PROBE_ACPI_MAX_TABLE_LEN (16u * 1024u * 1024u)
#define PROBE_RSDP_RAW_MAX 256u
#define PROBE_FACS_MAX_LEN 4096u
#define PROBE_SMBIOS_MAX_TABLE (1u * 1024u * 1024u)

enum probe_acpi_status {
    PROBE_ACPI_OK = 0,
    PROBE_ACPI_ABSENT,
    PROBE_ACPI_NULL_PTR,
    PROBE_ACPI_UNREADABLE,
    PROBE_ACPI_BAD_SIGNATURE,
    PROBE_ACPI_BAD_LENGTH,
    PROBE_ACPI_BAD_CHECKSUM,
    PROBE_ACPI_OVERLAP,
    PROBE_ACPI_NO_ROOT,
    PROBE_ACPI_BAD_COUNT,
    PROBE_ACPI_STATUS_COUNT
};

const char *probe_acpi_status_name(int status);

enum probe_acpi_origin {
    PROBE_ORIGIN_ROOT = 0,
    PROBE_ORIGIN_ROOT_ENTRY = 1,
    PROBE_ORIGIN_FADT_DSDT = 2,
    PROBE_ORIGIN_FADT_FACS = 3,
};

typedef struct {
    uint64_t addr;
    uint32_t length;
    uint8_t revision;
    uint8_t origin;
    uint8_t status;
    char sig[5];
    char name[16]; /* dump file name, "<SIG>.bin" or "<SIG>_<k>.bin" */
} probe_acpi_entry;

/* Fields of the FADT the probe needs, extracted with explicit length checks:
 * X_ pointers exist only in tables long enough to contain them. */
typedef struct {
    int valid;
    uint8_t revision;
    uint8_t minor;
    uint32_t length;
    uint32_t flags;
    int has_flags;
    uint16_t arm_boot_arch;
    int has_arm_boot_arch;
    uint64_t dsdt;
    uint64_t facs;
} probe_fadt_info;

/* Parses a FADT held in buf (len bytes, the table length). Returns 0 when the
 * buffer is a plausible FADT. */
int probe_fadt_parse(const uint8_t *buf, size_t len, probe_fadt_info *out);

typedef struct {
    int rsdp_status;
    uint64_t rsdp_addr;
    uint8_t rsdp_revision;
    uint32_t rsdp_length;
    uint8_t rsdp_raw[PROBE_RSDP_RAW_MAX];
    int root_is_xsdt;
    int root_status;
    unsigned count;
    unsigned n_ok;
    unsigned n_rejected;
    unsigned n_dup;
    probe_fadt_info fadt;
    probe_acpi_entry entries[PROBE_ACPI_MAX_ENTRIES];
} probe_acpi_scan;

/* Walks RSDP -> XSDT/RSDT -> tables -> FADT's DSDT/FACS. Returns 0 if the
 * root table validated (individual tables may still be rejected and are
 * listed with their status); non-zero if the RSDP or root was unusable, in
 * which case nothing below it is trusted. */
int probe_acpi_scan_run(probe_acpi_scan *s, const probe_phys_ops *ops, void *ctx,
                        uint64_t rsdp_addr);

/* Processor topology from the MADT, used only by the PSCI variant. */
#define PROBE_MAX_GICC 32u
#define PROBE_MPIDR_AFF_MASK 0xFF00FFFFFFull

typedef struct {
    uint64_t mpidr;
    uint32_t flags;
} probe_gicc;

typedef struct {
    unsigned count;
    unsigned total_seen;
    probe_gicc cpu[PROBE_MAX_GICC];
} probe_cpu_list;

int probe_madt_parse(const uint8_t *madt, size_t len, probe_cpu_list *out);
/* First enabled processor whose affinity differs from own_mpidr; 1 if found. */
int probe_pick_psci_target(const probe_cpu_list *cpus, uint64_t own_mpidr, uint64_t *target);

enum probe_smbios_status {
    PROBE_SMBIOS_OK = 0,
    PROBE_SMBIOS_NULL_PTR,
    PROBE_SMBIOS_UNREADABLE,
    PROBE_SMBIOS_BAD_ANCHOR,
    PROBE_SMBIOS_BAD_LENGTH,
    PROBE_SMBIOS_BAD_CHECKSUM,
    PROBE_SMBIOS_BAD_TABLE,
};

typedef struct {
    int status;
    int is64;
    uint8_t ep[32];
    uint32_t ep_len;
    uint64_t table_addr;
    uint32_t table_len;
    uint8_t major;
    uint8_t minor;
} probe_smbios;

const char *probe_smbios_status_name(int status);

/* expect64 selects which configuration-table GUID supplied ep_addr; an anchor
 * that disagrees with the GUID is rejected rather than reinterpreted. */
int probe_smbios_locate(probe_smbios *out, const probe_phys_ops *ops, void *ctx, uint64_t ep_addr,
                        int expect64);

#endif /* PROBE_ACPI_H */
