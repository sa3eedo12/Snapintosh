/* SPDX-License-Identifier: MIT */
/* The probe's report: which registers to read and in what order, and the text
 * the operator will photograph. Pure code so that both halves can be tested
 * with a mock register reader and fuzzed facts.
 *
 * Reading policy (why it is staged): on the first run the EL2 state is
 * unknown and some reads can fault into a vector table nobody installed, so
 * the probe reads in tiers and redraws the screen after each tier. The tiers
 * only contain registers the Armv8.0 ID scheme allocates; the ID registers of
 * newer revisions (MMFR3/4, ISAR2, DFR1) are not read here at all, because a
 * fault in this passive image has no recovery path. The guarded X2 tests
 * image reads them behind an exception guard.
 *   stage 0  CurrentEL only. Anything but EL2 ends the probe here.
 *   stage 1  registers that exist on every Armv8.0 EL2, plus the two ID
 *            registers that decide which optional registers may be read.
 *   stage 2  the remaining ID registers (2a).
 *
 * Every report line is at most 38 characters, uppercase, one fact each; the
 * renderer appends " |" and a CRC-8. The number of lines never depends on
 * register values, so the layout can be fixed before ExitBootServices.
 */
#ifndef PROBE_REPORT_H
#define PROBE_REPORT_H

#include <stddef.h>
#include <stdint.h>

#include "fbtext.h"
#include "logic.h"
#include "sysreg.h"

#define PROBE_REPORT_MAX_LINES 96u
#define PROBE_BUILD_ID_LEN 12u
#define PROBE_VENDOR_MAX 28u

/* Extracts bits [hi:lo] of v. */
uint64_t probe_bits(uint64_t v, unsigned hi, unsigned lo);

enum probe_reg_state {
    PROBE_REG_NOT_READ = 0,
    PROBE_REG_READ = 1,
    PROBE_REG_SKIPPED = 2, /* deliberately not read; skip_reason says why */
};

#define PROBE_EL_NOT_READ (-2)

enum probe_stage {
    PROBE_STAGE_NONE = 0,
    PROBE_STAGE_EL,  /* stage 0 done */
    PROBE_STAGE_1,
    PROBE_STAGE_2A,
};

typedef struct {
    uint8_t state[PSR_COUNT];
    uint64_t value[PSR_COUNT];
    const char *skip_reason[PSR_COUNT];
    int el;    /* probe_currentel_decode() of CurrentEL, PROBE_EL_NOT_READ before stage 0 */
    int stage; /* highest completed enum probe_stage */
} probe_regs;

typedef uint64_t (*probe_reg_reader)(void *ctx, int reg);

void probe_regs_init(probe_regs *r);

/* Stage 0. Returns the decoded exception level (0-3), -1 for a malformed
 * CurrentEL value. Reads nothing but CurrentEL. */
int probe_collect_el(probe_regs *r, probe_reg_reader rd, void *ctx);

/* Stages 1 and 2a. Each returns 0, or -1 without reading anything when the
 * stored exception level is not EL2 (or stage 0 was not run). Stage 2a must
 * follow stage 1; calling out of order returns -1. */
int probe_collect_stage1(probe_regs *r, probe_reg_reader rd, void *ctx);
int probe_collect_stage2a(probe_regs *r, probe_reg_reader rd, void *ctx);

enum probe_dump_state {
    PROBE_DUMP_NOT_ATTEMPTED = 0,
    PROBE_DUMP_OK,
    PROBE_DUMP_REFUSED_NO_VOLUME,
    PROBE_DUMP_REFUSED_NOT_USB,
    PROBE_DUMP_REFUSED_AMBIGUOUS,
    PROBE_DUMP_REFUSED_MALFORMED,
    PROBE_DUMP_NO_RUN_SLOT,
    PROBE_DUMP_ERROR,
    PROBE_DUMP_PARTIAL,
    PROBE_DUMP_DISABLED,
    PROBE_DUMP_STATE_COUNT
};

const char *probe_dump_state_name(int state);

enum probe_psci_run {
    PROBE_PSCI_RUN_NOT_ATTEMPTED = 0,
    PROBE_PSCI_RUN_DONE,
    PROBE_PSCI_RUN_NO_CONDUIT,
    PROBE_PSCI_RUN_NO_TARGET,
    PROBE_PSCI_RUN_NOT_EL2,
    PROBE_PSCI_RUN_HVC_REFUSED, /* conduit is HVC: never issued from EL2 */
    PROBE_PSCI_RUN_STATE_COUNT
};

typedef struct {
    int enabled; /* image built with -DPROBE_PSCI=1 */
    int state;   /* enum probe_psci_run */
    int version_called;
    uint32_t version;
    int have_target;
    uint64_t target_mpidr;
    int cpu_on_called;
    uint32_t cpu_on_ret;
    int arrived;
    uint64_t arrived_el_raw;
    uint64_t arrived_mpidr;
} probe_psci_facts;

/* Everything learned before ExitBootServices, as plain data. */
typedef struct {
    char build_id[PROBE_BUILD_ID_LEN + 1];
    int variant_psci;
    int reset_enabled;

    int have_preebs_el;
    uint64_t preebs_el_raw;
    uint32_t fw_revision;
    char fw_vendor[PROBE_VENDOR_MAX + 1];

    int have_gop;
    uint32_t gop_width;
    uint32_t gop_height;
    uint32_t gop_stride_pixels;
    uint32_t gop_format;
    uint64_t gop_base;
    uint64_t gop_size;
    int gop_rule; /* enum probe_gop_rule: which rule chose this GOP */

    int have_layout;
    uint32_t layout_scale;
    uint32_t layout_cols;
    uint32_t layout_rows;
    uint32_t layout_pages;

    unsigned cfg_count;
    int cfg_acpi20;
    int cfg_acpi10;
    int cfg_smbios;
    int cfg_smbios3;

    int rsdp_status;
    uint8_t rsdp_revision;
    int root_is_xsdt;
    int root_status;
    unsigned acpi_ok;
    unsigned acpi_rejected;
    unsigned acpi_dup;
    int fadt_valid;
    uint8_t fadt_revision;
    uint8_t fadt_minor;
    int has_arm_boot_arch;
    uint16_t arm_boot_arch;
    int psci_conduit; /* enum probe_psci_conduit */

    int smbios_status;
    int smbios_is64;
    uint8_t smbios_major;
    uint8_t smbios_minor;
    uint32_t smbios_table_len;

    int memmap_ok;
    unsigned memmap_count;
    unsigned memmap_desc_size;
    int have_ram;
    uint64_t ram_lo;
    uint64_t ram_hi;

    int dump_state; /* enum probe_dump_state */
    unsigned dump_run;
    unsigned dump_files;
    uint64_t dump_bytes;

    unsigned ebs_attempts;
    probe_psci_facts psci;
} probe_facts;

typedef struct {
    unsigned count;
    int overflowed; /* a line was too long or the report too large */
    probe_payload line[PROBE_REPORT_MAX_LINES];
    char scratch[PROBE_LINE_PAYLOAD_MAX + 1];
} probe_report;

/* Builds the full body. EL2 and not-EL2 reports differ only in the register
 * sections; the pre-EBS facts and (in the PSCI variant) the PSCI lines
 * always follow. The mode is taken from regs->el. */
void probe_report_build(probe_report *r, const probe_facts *f, const probe_regs *regs);

void probe_report_header0(probe_payload out, const probe_facts *f);
void probe_report_header1(probe_payload out, const probe_regs *regs, unsigned page,
                          unsigned pages);

enum probe_footer_kind {
    PROBE_FOOTER_STAGE0 = 0,
    PROBE_FOOTER_STAGE1,
    PROBE_FOOTER_STAGE2A,
    PROBE_FOOTER_PSCI,
    PROBE_FOOTER_COUNTDOWN,
    PROBE_FOOTER_HOLD,
    PROBE_FOOTER_RESETTING,
    PROBE_FOOTER_RESET_FAILED,
    PROBE_FOOTER_HALTED,
    PROBE_FOOTER_KIND_COUNT
};

void probe_report_footer(probe_payload out, int kind, unsigned seconds);

/* Sanitises a firmware-supplied UTF-16 string into at most PROBE_VENDOR_MAX
 * uppercase printable characters for the screen. Anything outside printable
 * ASCII becomes '?'. */
void probe_sanitize_utf16(char *out, size_t cap, const uint16_t *s, size_t max_units);

#endif /* PROBE_REPORT_H */
