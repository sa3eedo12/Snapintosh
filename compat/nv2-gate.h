/* SPDX-License-Identifier: MIT */
/* SNAP_PLATFORM_SP11: the fail-closed gate in front of HCR_EL2.NV/NV2.
 *
 * XNU runs as a nested virtual-EL2 kernel, which only works where FEAT_NV2
 * redirects its EL2 register accesses into the VNCR page. The A16 has that
 * (measured, docs/FEASIBILITY.md). Nothing comparable is known for the Oryon v1
 * cores of the Surface Pro 11 (docs/SURFACE-PRO-11.md E1, E11, E12): their ID
 * registers have never been read on the unit, and an ID register only says what
 * a core claims, not that VNCR_EL2 redirection works under the firmware EL2
 * layer the monitor would run in. Writing VNCR_EL2 on a core without NV2 is an
 * undefined access taken at EL2, inside the monitor itself.
 *
 * So NV/NV2 may be set only when BOTH hold:
 *   1. the live ID registers advertise NV2 (decode below), and
 *   2. an operator-supplied record of a functional test on this SoC, MIDR,
 *      REVIDR and ID-register state says VNCR redirection worked and the
 *      NV2-off negative control trapped.
 * A missing, malformed, mismatched or not-plainly-passing record denies. An ID
 * register alone never allows: docs/SURFACE-PRO-11.md assumes NV2 ABSENT until a
 * hardware test says otherwise.
 *
 * The record is produced by the hardware-side test (wave 2) and converted by
 * tools/nv2_record.py, which owns the text, JSON and binary forms. This file
 * defines the binary form the monitor validates and nothing else; it is pure
 * integer code (no libc, no allocation, no pointer tables, so it stays
 * position independent inside the probe blob).
 *
 * Integrity: the CRC-32 and the zero-reserved fields detect accidental damage
 * and unknown future layouts. They are not authentication. The record is a
 * local, operator-trusted input that is never committed (tools/nv2_record.py
 * writes it under build/), and a forged record defeats the gate.
 *
 * Wire layout, version 1: little-endian, 96 bytes, naturally aligned, no
 * padding.
 *   0x00 u32 magic        bytes 4e 56 32 52 ("NV2R"), SNAP_NV2_RECORD_MAGIC
 *   0x04 u16 version      1
 *   0x06 u16 flags        must be 0
 *   0x08 u32 soc          enum snap_nv2_soc
 *   0x0c u32 build_id     test tool build; informational, covered by the CRC
 *   0x10 u64 midr_el1     compared exactly with this CPU
 *   0x18 u64 revidr_el1   compared exactly with this CPU
 *   0x20 u64 id_aa64mmfr2_el1   compared exactly with this CPU
 *   0x28 u64 id_aa64mmfr4_el1   compared exactly with this CPU
 *   0x30 u8  vncr_redirect            enum snap_nv2_result
 *   0x31 u8  negative_control_trapped enum snap_nv2_result
 *   0x32 u8  evidence                 enum snap_nv2_evidence
 *   0x33 u8  reserved0    must be 0
 *   0x34 u32 reserved1    must be 0
 *   0x38 char tool[16]    [A-Za-z0-9._-]{1,15}, NUL padded, byte 15 is 0
 *   0x48 u8  reserved2[20] must be 0
 *   0x5c u32 crc32        CRC-32 (zlib/IEEE) of bytes 0x00..0x5b
 *
 * The status words are the monitor's prepare status, which the host decodes
 * through PREPARE_FAILURES in probes/run.py; the -90..-95 band is shared with
 * compat/oryon-idregs.h and is a proposal until the coordinator reconciles it
 * with the other agents' bands. */
#pragma once
#include <stddef.h>
#include <stdint.h>

#define SNAP_NV2_RECORD_MAGIC    UINT32_C(0x5232564e)
#define SNAP_NV2_RECORD_VERSION  1
#define SNAP_NV2_RECORD_SIZE     96
#define SNAP_NV2_TOOL_LEN        16
#define SNAP_NV2_RESERVED2_LEN   20

/* Prepare status, all negative like the existing prepare refusals. */
#define SNAP_NV2_STATUS_RECORD    (-90)  /* record absent, damaged or unknown layout */
#define SNAP_NV2_STATUS_MISMATCH  (-91)  /* record is for another SoC, core or ID state */
#define SNAP_NV2_STATUS_NO_NV2    (-92)  /* ID registers do not advertise NV2 */
#define SNAP_NV2_STATUS_RESULT    (-93)  /* functional result, control or evidence refused */

enum snap_nv2_soc {
    SNAP_NV2_SOC_UNKNOWN = 0,
    SNAP_NV2_SOC_X1E80100 = 1,
    SNAP_NV2_SOC_X2E_96_100 = 2,
};

/* The functional test reports each outcome as pass, fail or not run; zero is
 * "not run" so a zeroed field can never be read as a pass. */
enum snap_nv2_result {
    SNAP_NV2_NOT_RUN = 0,
    SNAP_NV2_PASS = 1,
    SNAP_NV2_FAIL = 2,
};

/* The repository's evidence labels. The gate accepts only VERIFIED and
 * OBSERVED: an inferred, experimental or unimplemented result is not evidence
 * that the redirection works. */
enum snap_nv2_evidence {
    SNAP_NV2_EVIDENCE_NONE = 0,
    SNAP_NV2_EVIDENCE_VERIFIED = 1,
    SNAP_NV2_EVIDENCE_OBSERVED = 2,
    SNAP_NV2_EVIDENCE_INFERRED = 3,
    SNAP_NV2_EVIDENCE_EXPERIMENTAL = 4,
    SNAP_NV2_EVIDENCE_UNIMPLEMENTED = 5,
};

/* Every deny reason is a small value starting at zero, and the two non-deny
 * values are distinctive, so an uninitialised or zeroed verdict can never be
 * mistaken for permission. Only SNAP_NV2_ALLOW allows. */
enum snap_nv2_verdict {
    SNAP_NV2_DENY_NO_RECORD = 0,
    SNAP_NV2_DENY_LENGTH,
    SNAP_NV2_DENY_MAGIC,
    SNAP_NV2_DENY_VERSION,
    SNAP_NV2_DENY_CRC,
    SNAP_NV2_DENY_RESERVED,
    SNAP_NV2_DENY_SOC,
    SNAP_NV2_DENY_MIDR,
    SNAP_NV2_DENY_REVIDR,
    SNAP_NV2_DENY_NOT_ADVERTISED,
    SNAP_NV2_DENY_ID_MISMATCH,
    SNAP_NV2_DENY_VNCR_NOT_RUN,
    SNAP_NV2_DENY_VNCR_FAIL,
    SNAP_NV2_DENY_NEGCTL_NOT_RUN,
    SNAP_NV2_DENY_NEGCTL_FAIL,
    SNAP_NV2_DENY_EVIDENCE,
    SNAP_NV2_DENY_COUNT,             /* number of deny reasons, not a verdict */
    SNAP_NV2_PARSED = 0x4e325052,    /* structurally valid; says nothing about permission */
    SNAP_NV2_ALLOW = 0x4e32414c,
};

/* The record's layout, for the static assertions only. Fields are read at
 * their offsets with byte loads: an embedded array need not be 8-byte aligned. */
struct snap_nv2_record {
    uint32_t magic;
    uint16_t version;
    uint16_t flags;
    uint32_t soc;
    uint32_t build_id;
    uint64_t midr_el1;
    uint64_t revidr_el1;
    uint64_t id_aa64mmfr2_el1;
    uint64_t id_aa64mmfr4_el1;
    uint8_t vncr_redirect;
    uint8_t negative_control_trapped;
    uint8_t evidence;
    uint8_t reserved0;
    uint32_t reserved1;
    char tool[SNAP_NV2_TOOL_LEN];
    uint8_t reserved2[SNAP_NV2_RESERVED2_LEN];
    uint32_t crc32;
};

_Static_assert(sizeof(struct snap_nv2_record) == SNAP_NV2_RECORD_SIZE, "record size");
_Static_assert(offsetof(struct snap_nv2_record, version) == 0x04, "version offset");
_Static_assert(offsetof(struct snap_nv2_record, flags) == 0x06, "flags offset");
_Static_assert(offsetof(struct snap_nv2_record, soc) == 0x08, "soc offset");
_Static_assert(offsetof(struct snap_nv2_record, build_id) == 0x0c, "build_id offset");
_Static_assert(offsetof(struct snap_nv2_record, midr_el1) == 0x10, "midr offset");
_Static_assert(offsetof(struct snap_nv2_record, revidr_el1) == 0x18, "revidr offset");
_Static_assert(offsetof(struct snap_nv2_record, id_aa64mmfr2_el1) == 0x20, "mmfr2 offset");
_Static_assert(offsetof(struct snap_nv2_record, id_aa64mmfr4_el1) == 0x28, "mmfr4 offset");
_Static_assert(offsetof(struct snap_nv2_record, vncr_redirect) == 0x30, "vncr offset");
_Static_assert(offsetof(struct snap_nv2_record, negative_control_trapped) == 0x31, "control offset");
_Static_assert(offsetof(struct snap_nv2_record, evidence) == 0x32, "evidence offset");
_Static_assert(offsetof(struct snap_nv2_record, reserved0) == 0x33, "reserved0 offset");
_Static_assert(offsetof(struct snap_nv2_record, reserved1) == 0x34, "reserved1 offset");
_Static_assert(offsetof(struct snap_nv2_record, tool) == 0x38, "tool offset");
_Static_assert(offsetof(struct snap_nv2_record, reserved2) == 0x48, "reserved2 offset");
_Static_assert(offsetof(struct snap_nv2_record, crc32) == 0x5c, "crc offset");

/* A record's fields after structural validation, in host byte order. */
struct snap_nv2_view {
    uint32_t soc;
    uint32_t build_id;
    uint64_t midr_el1;
    uint64_t revidr_el1;
    uint64_t id_aa64mmfr2_el1;
    uint64_t id_aa64mmfr4_el1;
    uint8_t vncr_redirect;
    uint8_t negative_control_trapped;
    uint8_t evidence;
};

/* The one SoC the SP11 monitor build is for. */
#define SNAP_NV2_PLATFORM_SOC SNAP_NV2_SOC_X1E80100

/* CRC-32 as zlib computes it (reflected, polynomial 0xedb88320, initial value
 * and final xor 0xffffffff); crc32("123456789") is 0xcbf43926. */
uint32_t snap_nv2_crc32(const void *data, size_t length);

/* Whether the ID registers advertise the nested-virtualization combination the
 * monitor uses: ID_AA64MMFR2_EL1.NV == 2 (FEAT_NV2, with FEAT_NV) and
 * ID_AA64MMFR4_EL1.NV_frac == 0. NV at MMFR2 [27:24] is read from the
 * repository's own measurement of the A16 (NV 2); NV_frac at MMFR4 [23:20] is
 * the architectural position as listed by Linux's arch/arm64/tools/sysreg, and
 * the repository's existing host decode reads [27:24] instead (E2H0 there). An
 * NV2-only core (NV 0 with NV_frac 1), NV 1, and every reserved value do not
 * advertise: the monitor has never run in those states. This is a necessary
 * condition for the gate, never a sufficient one. */
int snap_nv2_id_advertises_nv2(uint64_t mmfr2, uint64_t mmfr4);

/* Structural validation only: length, magic, version, CRC and every reserved
 * or out-of-range field. Returns SNAP_NV2_PARSED and fills *view, or a deny
 * reason and a zeroed *view. Never a permission. */
enum snap_nv2_verdict snap_nv2_record_parse(const void *record, size_t length,
                                            struct snap_nv2_view *view);

/* The decision. `midr`, `revidr`, `mmfr2` and `mmfr4` are this CPU's own
 * MIDR_EL1, REVIDR_EL1, ID_AA64MMFR2_EL1 and ID_AA64MMFR4_EL1, read by the
 * caller on the CPU that is about to set NV/NV2: every CPU decides for itself.
 * `expect_soc` is the SoC this build is for. Evaluation order is the order of
 * the deny reasons in enum snap_nv2_verdict. The live registers must advertise
 * NV2 (snap_nv2_id_advertises_nv2) as well as equal the recorded ones, and the
 * record must say that both the VNCR redirection and the negative control
 * passed with VERIFIED or OBSERVED evidence; a recorded result alone is never
 * enough, and neither are the ID registers.
 * Returns SNAP_NV2_ALLOW only when everything agrees. */
enum snap_nv2_verdict snap_nv2_gate_decide(const void *record, size_t length,
                                           uint32_t expect_soc,
                                           uint64_t midr, uint64_t revidr,
                                           uint64_t mmfr2, uint64_t mmfr4);

/* The record built into this monitor by -DSNAP_NV2_RECORD_HEADER=\"path\", or
 * NULL with *length set to 0 when the build carries none (the default, which
 * the gate treats as no record). */
const uint8_t *snap_nv2_embedded_record(size_t *length);

/* snap_nv2_gate_decide() on the embedded record. */
enum snap_nv2_verdict snap_nv2_gate_decide_embedded(uint32_t expect_soc,
                                                    uint64_t midr, uint64_t revidr,
                                                    uint64_t mmfr2, uint64_t mmfr4);

/* The prepare status for a verdict: 0 only for SNAP_NV2_ALLOW, otherwise one
 * of the SNAP_NV2_STATUS_* values. Any value that is not a known verdict maps
 * to SNAP_NV2_STATUS_RECORD. */
int64_t snap_nv2_verdict_status(enum snap_nv2_verdict verdict);
