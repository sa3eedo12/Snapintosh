/* SPDX-License-Identifier: MIT */
/* Writes the firmware tables the probe found to a USB stick, before
 * ExitBootServices, as a numbered run directory:
 *
 *   \SP11DUMP\RUNnn\RSDP.bin  XSDT.bin  FACP.bin  APIC.bin  DSDT.bin ...
 *                    smbios-ep.raw  smbios-table.raw
 *                    memmap.raw  memmap.txt  tables.txt  summary.txt
 *
 * Safety rules, all enforced here and nowhere else:
 *  - Only the volume the image itself was loaded from is ever opened, and only
 *    if its device path contains a USB messaging node and no other transport.
 *    No other file system or block device is touched. A refusal is reported
 *    and the passive probe continues.
 *  - Nothing is overwritten: the run directory is the first RUNnn that does
 *    not exist, and every file is created only if its name is absent.
 *  - Totals are capped (files, bytes per file, bytes overall); a table that
 *    would exceed a cap is skipped and the dump is reported PARTIAL.
 *  - Every file is flushed and closed, then the directories and the volume
 *    root are flushed and closed, before the caller exits boot services.
 *
 * Firmware memory is read only through probe_phys_ops, which the caller backs
 * with the UEFI memory map, never by dereferencing a firmware pointer.
 */
#ifndef PROBE_DUMP_H
#define PROBE_DUMP_H

#include <stddef.h>
#include <stdint.h>

#include "acpi.h"
#include "efi_types.h"
#include "logic.h"
#include "report.h"

#define PROBE_DUMP_MAX_FILES 200u
#define PROBE_DUMP_MAX_FILE_BYTES (16u * 1024u * 1024u)
#define PROBE_DUMP_MAX_TOTAL_BYTES (32u * 1024u * 1024u)
#define PROBE_DUMP_MAX_RUN 99u
#define PROBE_DUMP_BOUNCE_BYTES (64u * 1024u)
#define PROBE_DUMP_TEXT_BYTES (1024u * 1024u)
#define PROBE_DUMP_DP_MAX_LEN 2048u
#define PROBE_DUMP_MAX_CFG_LINES 64u

typedef struct {
    efi_system_table *st;
    efi_handle image;
    const probe_phys_ops *ops;
    void *ops_ctx;
    const probe_acpi_scan *scan;
    const probe_smbios *smbios;
    const probe_memmap *memmap;
    const void *memmap_raw;
    size_t memmap_size;
    uint32_t memmap_desc_version;
    const probe_facts *facts;
    /* Caller-provided scratch so that the dump itself allocates nothing. */
    uint8_t *bounce;
    char *text;
} probe_dump_ctx;

typedef struct {
    int state; /* enum probe_dump_state */
    unsigned run;
    unsigned files;
    uint64_t bytes;
} probe_dump_result;

void probe_dump_run(const probe_dump_ctx *c, probe_dump_result *out);

#endif /* PROBE_DUMP_H */
