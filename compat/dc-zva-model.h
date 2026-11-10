/* SPDX-License-Identifier: MIT */
/* Host model of the DC ZVA question on the SP11 (plan todo C6). UNIMPLEMENTED
 * target, EXPERIMENTAL model: pure computation, nothing links it into a
 * monitor build, and the A16 admission in probes/xnuboot/prepare.c is untouched.
 *
 * What is established (docs/SP11-EL2-ENTRY.md, section C6):
 *  - DCZID_EL0 = 0x14 is DZP = 1 (bit 4) with BS = 4 (bits 3:0), a 64-byte
 *    block. DZP is not a property of the silicon: it reads 1 when DC ZVA is
 *    trapped or disabled for the reader, through HCR_EL2.TDZ and SCTLR_ELx.DZE
 *    (VERIFIED: DDI 0601 2026-09, DCZID_EL0 and HCR_EL2.TDZ). Only BS is
 *    IMPLEMENTATION DEFINED. The A16 admission requires the whole register to
 *    equal 4, so on the SP11 it could refuse a working instruction.
 *  - XNU's public source (xnu-12377.121.6, osfmk/arm64/bzero.s and
 *    loose_ends.c) issues DC ZVA at a hard-coded 64-byte stride and never reads
 *    DCZID_EL0 (VERIFIED: read; the guest build 26A428 is not public, so for it
 *    this is INFERRED). A different block size therefore corrupts or skips
 *    memory, which is why the model requires BS = 4.
 *  - Whether the Oryon X1 instruction is functionally broken rests on one
 *    third-party Linux patch that disables it without a stated symptom
 *    (OBSERVED, single unit, UNKNOWN mechanism). Upstream Linux at
 *    7475ea8b58616b33c04afb976cf0fbaad9dd63fb has no such workaround.
 *
 * The model therefore decides from two X2 results, the raw DCZID_EL0 and a
 * functional DC ZVA test, and if the instruction is broken it supplies the
 * trap decode, the block-confined zeroing and the policy for emulating it.
 * Disabling DC ZVA in the guest is not an option: XNU's kernel and the user
 * space zeroing loop execute it unconditionally.
 *
 * Compiled only under SNAP_PLATFORM_SP11. No I/O, no allocation, no global
 * state. The emulation operates on a caller-provided window of bytes. */
#ifndef SNAP_COMPAT_DC_ZVA_MODEL_H
#define SNAP_COMPAT_DC_ZVA_MODEL_H

#include "platform.h"

#if SNAP_PLATFORM_SP11

#include <stddef.h>
#include <stdint.h>

/* ESR_EL2 (DDI 0601 ESR_EL2): EC [31:26], IL [25], ISS [24:0]. A trapped system
 * instruction is EC 0x18; its ISS is Op0 [21:20], Op2 [19:17], Op1 [16:14], CRn
 * [13:10], Rt [9:5], CRm [4:1], Direction [0] (0 = write, including SYS). */
#define DZV_EC_SYSTEM 0x18u
/* DC ZVA, Xt is SYS #3, C7, C4, #1, Xt (DDI 0602 DC alias): op0 = 1, op1 = 3, CRn = 7, CRm = 4, op2 = 1. */
#define DZV_ISS_DC_ZVA ((UINT32_C(1) << 20) | (UINT32_C(1) << 17) | (UINT32_C(3) << 14) | (UINT32_C(7) << 10) | (UINT32_C(4) << 1))
#define DZV_ISS_FIELDS (UINT32_C(0x3fffff) & ~(UINT32_C(0x1f) << 5))   /* ISS [21:0] except Rt */
/* The 32-bit instruction word, with Rt in bits 4:0. */
#define DZV_INSN_DC_ZVA UINT32_C(0xd50b7420)

/* The block size the monitor and XNU assume: 4 << 4 = 64 bytes. */
#define DZV_BS_REQUIRED 4u

enum dzv_status {
    DZV_OK = 0,
    DZV_E_PARAMS,              /* a null or malformed argument */
    DZV_E_RESERVED_BITS,       /* DCZID_EL0 bits above 4 are not zero (RES0 bits [63:9], TBS needs FEAT_MTETC) */
    DZV_E_BLOCK_SIZE,          /* BS is not the 64-byte block (or above the architectural maximum 9) */
    DZV_E_UNTESTED,            /* no functional DC ZVA result: fail closed, X2 must measure it */
    DZV_E_BROKEN_NO_EMULATION, /* the instruction misbehaves and emulation is not enabled */
    DZV_E_NOT_SYSTEM_TRAP,     /* EC is not 0x18 */
    DZV_E_IL,                  /* a trapped system instruction is always 32-bit: IL must be 1 */
    DZV_E_ISS_RES0,            /* ISS bits [24:22] are RES0 */
    DZV_E_NOT_DC_ZVA,          /* another system instruction: DC CIVAC, DC GVA, DC GZVA, an MSR, ... */
    DZV_E_READ,                /* Direction is 1: DC ZVA is a write-direction system instruction */
    DZV_E_RANGE,               /* the block is not inside the window the guest may write */
    DZV_E_DEVICE,              /* Device memory: the architecture gives an Alignment fault, not a zeroing */
    DZV_STATUS_COUNT
};

/* DZP as the architecture describes it for an EL0 or EL1 reader (DDI 0601 DCZID_EL0): 1 when DC ZVA is
 * prohibited, from HCR_EL2.TDZ (whose Effective value is 0 when {E2H, TGE} is {1, 1}) and SCTLR_EL1.DZE, or
 * SCTLR_EL2.DZE when {E2H, TGE} is {1, 1}. Whether an EL2 reader sees the same is INFERRED, not stated. */
unsigned dzv_dzp(unsigned e2h, unsigned tge, unsigned tdz, unsigned sctlr_el1_dze, unsigned sctlr_el2_dze);

/* The block size N = 4 << BS bytes, or an error. Does not require BS = 4. */
enum dzv_status dzv_block_bytes(uint64_t dczid, uint32_t *bytes);

enum dzv_zva_result { DZV_ZVA_UNTESTED = 0, DZV_ZVA_WORKS, DZV_ZVA_BROKEN };
enum dzv_mode { DZV_MODE_NATIVE, DZV_MODE_EMULATE };

/* What the monitor programs for the guest. NATIVE grants DC ZVA at EL0 (SCTLR_EL1.DZE = 1) and does not trap
 * it (HCR_EL2.TDZ = 0). EMULATE keeps DZE = 1 so that an EL0 execution reaches EL2 rather than EL1, and sets
 * TDZ = 1 so that EL0 and EL1 both trap to EL2. */
struct dzv_plan {
    uint8_t mode, sctlr_el1_dze, hcr_tdz;
    uint32_t block_bytes;
};

/* Decide from the raw DCZID_EL0 seen at EL2 and the functional result. DZP is deliberately ignored: it says how
 * DC ZVA is configured for the reader, not what the silicon does. */
enum dzv_status dzv_admit(uint64_t dczid_seen, enum dzv_zva_result functional, unsigned emulation_enabled,
                          struct dzv_plan *plan);

/* Decode an ESR_EL2 value for a trapped DC ZVA; *rt is the register holding the address (31 reads as XZR). */
enum dzv_status dzv_decode_trap(uint64_t esr, unsigned *rt);
/* The same for the instruction word. */
enum dzv_status dzv_decode_insn(uint32_t insn, unsigned *rt);

/* A window of guest-writable bytes, backing guest addresses base_va .. base_va + len - 1. */
struct dzv_window {
    uint8_t *bytes;
    uint64_t base_va, len;
    uint8_t device;          /* the memory is Device: DC ZVA would raise an Alignment fault */
};

/* Zero the naturally aligned block of `block_bytes` that contains `va` (no alignment is required of the address;
 * VERIFIED: DDI 0487 M.d D7.5.10 and C5.3.38). Nothing outside that block is written. *block_va is its address. */
enum dzv_status dzv_emulate(const struct dzv_window *w, uint64_t va, uint32_t block_bytes, uint64_t *block_va);

const char *dzv_status_name(enum dzv_status s);
/* "label: reference" for a refusal, NULL for DZV_OK. */
const char *dzv_rule_citation(enum dzv_status s);

#endif /* SNAP_PLATFORM_SP11 */
#endif /* SNAP_COMPAT_DC_ZVA_MODEL_H */
