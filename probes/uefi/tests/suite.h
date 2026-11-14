/* SPDX-License-Identifier: MIT */
/* The context every test of the X2 image receives, and the test entry points.
 *
 * Each run_* function fills one tests_result: a verdict, a label, the detail
 * rows and the one-line "proves" / "does not prove" statements. A test that
 * cannot justify an address does not touch it: it says UNKNOWN and why.
 */
#ifndef TESTS_SUITE_H
#define TESTS_SUITE_H

#include <stddef.h>
#include <stdint.h>

#include "acpi.h"
#include "acpix.h"
#include "decode.h"
#include "desc.h"
#include "env.h"
#include "nv2.h"
#include "pciio.h"
#include "tests_types.h"

/* How long the image shows a finished page before T06 makes the first read of an ECAM window and
 * again before the first read of the NVMe BAR0 window: long enough to photograph the screen
 * (OBSERVED, boot 3: the board resets itself within seconds of a read that never returns, and the
 * page was nearly lost). These two waits are the only delay the third version of the image adds,
 * and T06 never waits when it has nothing to touch. */
#define TESTS_PHOTOGRAPH_WAIT_SECONDS 8u

/* What the photograph callback returns: 0 after the full wait, else why it could not make it. A
 * wait that cannot be measured is not made, and T06 then reads nothing; the code is on the page. */
enum {
    TESTS_PHOTO_OK = 0,
    TESTS_PHOTO_REFUSED = 1,   /* no validated layout to draw on, or a number of seconds out of range */
    TESTS_PHOTO_BACKWARDS = 2, /* the counter ran backwards */
    TESTS_PHOTO_STALLED = 3    /* the counter did not advance over a long run of reads */
};

/* DC ZVA test operations: one buffer owned by the image and a guarded DC ZVA. */
typedef struct {
    void *ctx;
    uint8_t *buffer;     /* TESTS_ZVA_BUFFER_BYTES, aligned to TESTS_ZVA_BUFFER_BYTES / 3 or better */
    int (*zva)(void *ctx, uint8_t *addr); /* 0 on success, non-zero if it faulted */
    void (*sync)(void *ctx);              /* make the buffer visible: clean, invalidate, dsb */
} tests_zva_ops;

#define TESTS_ZVA_MAX_BLOCK 2048u
#define TESTS_ZVA_BUFFER_BYTES (3u * TESTS_ZVA_MAX_BLOCK)

typedef struct {
    const tests_bus *bus;
    const probe_acpi_scan *scan;
    const probe_phys_ops *phys;
    void *phys_ctx;
    const probe_memmap *memmap;
    const tests_desc *desc;
    const tests_zva_ops *zva;
    int model; /* a run under an emulator */
    /* Strict device-window policy: a window is read only if the UEFI memory map
     * lists all of it as MMIO (see tests_window_policy). 0 admits a window the
     * map does not mention, and still refuses one the map says is something else. */
    int require_mmio_map;
    /* T03 reads CNTPOFF_EL2 only if this is set, and then only when ID_AA64MMFR0_EL1.ECV says the
     * register exists. EL2 access to it is also gated at EL3 (SCR_EL3.ECVEn): where firmware
     * leaves that bit clear the access is taken to EL3, and a monitor that does not handle it can
     * hang the machine. INFERRED from one frozen page on the Surface Pro 11, not tested there, so
     * the default is never to read it. Zero unless the build asked (TESTS_READ_CNTPOFF, which only
     * the emulator images may carry). */
    int read_cntpoff;
    /* What the firmware's own PCI I/O showed before ExitBootServices (pciio.h). T06 reads an ECAM
     * window only for a segment where this table holds an NVMe function; NULL, or a table that was
     * not collected, means T06 touches nothing. */
    const tests_pciio *pciio;
    uint8_t *acpi_buf; /* TESTS_ACPI_BUF_BYTES scratch */
    tests_idset ids;   /* filled by T01, read by T02 and T04 */
    /* Called before each device window is read, with a short text naming it, so
     * that a hang can be attributed to one address (the image draws it as the
     * footer). May be NULL. */
    void (*progress)(void *ctx, const char *text);
    void *progress_ctx;
    /* Called by T06 before the first read of an ECAM window and before the first read of the NVMe
     * BAR0 window, with the footer text naming the address and the number of seconds to wait. The
     * image draws the finished page of the other tests with that footer and returns only after the
     * wait, which it measures on the physical counter (no sleeping primitive, no interrupt).
     * Returns 0 after a full wait; non-zero (TESTS_PHOTO_*) means it could not wait, and the caller
     * then does not make the read. NULL (host tests that do not model the screen) means no wait:
     * progress is called instead, as for every later window. */
    int (*photograph)(void *ctx, const char *text, unsigned seconds);
    void *photograph_ctx;
} tests_ctx;

/* T01: the raw ID dump. Also fills c->ids. */
void tests_run_id(tests_ctx *c, tests_result *r);
/* T03 timer, T04 DC ZVA, T05 GIC plus MADT, T06 PCIe and NVMe state, T07 PCIe SMMU
 * state, T08 ADSP and PMIC-GLINK liveness, T09 DRAM map. */
void tests_run_timer(tests_ctx *c, tests_result *r);
void tests_run_zva(tests_ctx *c, tests_result *r);
void tests_run_gic(tests_ctx *c, tests_result *r);
void tests_run_pcie(tests_ctx *c, tests_result *r);
void tests_run_smmu(tests_ctx *c, tests_result *r);
void tests_run_adsp(tests_ctx *c, tests_result *r);
void tests_run_dram(tests_ctx *c, tests_result *r);

/* T02 fills the row part of the result; the record is returned separately, and
 * *have_record says whether there is one. */
void tests_run_nv2(tests_ctx *c, const tests_nv2_ops *ops, uint32_t soc, uint32_t build_id,
                   tests_result *r, tests_nv2_record *rec, int *have_record);

/* The static statements each test carries (proves, does not prove). */
void tests_init_result(tests_result *r, int id);

/* Rows shared by T05 to T07 for device windows. tests_row_refused writes
 * "<what><index> REFUSED: <reason>" (no index when index < 0); tests_row_windows
 * writes how many admitted windows the memory map lists as MMIO, how many it
 * does not mention (never any under the strict policy), and how many were
 * refused (those the policy turned away plus parse_refused, the table entries
 * dropped before the policy saw them). */
void tests_row_refused(tests_result *r, const char *what, int index, int status);
void tests_row_windows(tests_result *r, const tests_allow *a, unsigned parse_refused, int strict);

/* Pure helpers exposed for the host tests. */
typedef struct {
    int smem; /* -1 not observed, 0 bad, 1 good */
    int glink;
    int ipcc;
} tests_adsp_obs;
int tests_adsp_decide(const tests_adsp_obs *o, int *label);

/* What T06 counted, and the verdict it makes of it. PASS needs a segment that was read, no fault,
 * an endpoint that answered, ids and BAR0 equal to the firmware's record, the registers read at
 * EL2 and before, CAP and VS equal, and nothing the firmware knew about left unchecked. *why is a
 * static reason for any other verdict. */
typedef struct {
    unsigned scanned;         /* segments whose window was admitted and read */
    unsigned faulted;         /* guarded reads that faulted, or were not justified */
    unsigned no_endpoint;     /* scanned segments where no endpoint answered behind a bridge */
    unsigned view_mismatch;   /* ids, class or BAR0 base differ from the firmware's record */
    unsigned const_mismatch;  /* CAP or VS differ from the firmware's reading */
    unsigned regs_incomplete; /* the controller registers were not read, at EL2 or before */
    unsigned unverified;      /* firmware NVMe functions this run could not check */
    unsigned refused;         /* windows and BARs the window policy turned away */
    unsigned unsafe;          /* reads not made because the photograph wait could not be made */
} tests_pcie_tally;
int tests_pcie_decide(const tests_pcie_tally *t, const char **why);

typedef struct {
    int ok;
    uint64_t total_bytes;
    uint64_t low;
    uint64_t high;
    unsigned extents;
    uint64_t ext_base[8];
    uint64_t ext_end[8];
    unsigned truncated;
} tests_dram;
void tests_dram_summarize(const probe_memmap *m, tests_dram *out);
/* PASS when every RAM byte is below 1 << ipa_bits, FAIL when not. */
int tests_dram_verdict(const tests_dram *d, unsigned ipa_bits);

#endif /* TESTS_SUITE_H */
