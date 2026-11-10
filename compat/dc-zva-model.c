/* SPDX-License-Identifier: MIT */
/* Host model of the DC ZVA question on the SP11. See dc-zva-model.h for scope,
 * evidence labels and the decision this supports. */
#include "dc-zva-model.h"

#if SNAP_PLATFORM_SP11

unsigned dzv_dzp(unsigned e2h, unsigned tge, unsigned tdz, unsigned sctlr_el1_dze, unsigned sctlr_el2_dze)
{
    const int host = e2h && tge;
    /* TDZ has Effective value 0 when {E2H, TGE} is {1, 1}. */
    if (!host && tdz) return 1;
    return host ? !sctlr_el2_dze : !sctlr_el1_dze;
}

enum dzv_status dzv_block_bytes(uint64_t dczid, uint32_t *bytes)
{
    if (!bytes) return DZV_E_PARAMS;
    /* Bits [63:9] are RES0 and [8:5] (TBS) are defined only with FEAT_MTETC, which this model does not claim. */
    if (dczid >> 5) return DZV_E_RESERVED_BITS;
    const unsigned bs = (unsigned)(dczid & 0xf);
    /* The architectural maximum is 2 KiB, BS = 9. */
    if (bs > 9) return DZV_E_BLOCK_SIZE;
    *bytes = UINT32_C(4) << bs;
    return DZV_OK;
}

enum dzv_status dzv_admit(uint64_t dczid_seen, enum dzv_zva_result functional, unsigned emulation_enabled,
                          struct dzv_plan *plan)
{
    uint32_t bytes;
    if (!plan) return DZV_E_PARAMS;
    enum dzv_status s = dzv_block_bytes(dczid_seen, &bytes);
    if (s != DZV_OK) return s;
    /* XNU zeroes with DC ZVA at a fixed 64-byte stride: any other block overwrites a neighbour or leaves a gap. */
    if ((dczid_seen & 0xf) != DZV_BS_REQUIRED) return DZV_E_BLOCK_SIZE;
    if (functional == DZV_ZVA_UNTESTED) return DZV_E_UNTESTED;
    if (functional != DZV_ZVA_WORKS && functional != DZV_ZVA_BROKEN) return DZV_E_PARAMS;
    *plan = (struct dzv_plan){ .block_bytes = bytes, .sctlr_el1_dze = 1 };
    if (functional == DZV_ZVA_WORKS) {
        plan->mode = DZV_MODE_NATIVE;
        return DZV_OK;
    }
    if (!emulation_enabled) return DZV_E_BROKEN_NO_EMULATION;
    plan->mode = DZV_MODE_EMULATE;
    plan->hcr_tdz = 1;
    return DZV_OK;
}

enum dzv_status dzv_decode_trap(uint64_t esr, unsigned *rt)
{
    if (!rt) return DZV_E_PARAMS;
    if (((esr >> 26) & 0x3f) != DZV_EC_SYSTEM) return DZV_E_NOT_SYSTEM_TRAP;
    if (!((esr >> 25) & 1)) return DZV_E_IL;
    /* ISS [24:22] are RES0; ESR [55:32] (ISS2) is taken as RES0 for this class (INFERRED, fail closed). */
    const uint32_t iss = (uint32_t)(esr & UINT32_C(0x1ffffff));
    if ((esr >> 32) || (iss >> 22)) return DZV_E_ISS_RES0;
    if ((iss & DZV_ISS_FIELDS & ~UINT32_C(1)) != DZV_ISS_DC_ZVA) return DZV_E_NOT_DC_ZVA;
    if (iss & 1) return DZV_E_READ;
    *rt = (iss >> 5) & 0x1f;
    return DZV_OK;
}

enum dzv_status dzv_decode_insn(uint32_t insn, unsigned *rt)
{
    if (!rt) return DZV_E_PARAMS;
    if ((insn & ~UINT32_C(0x1f)) != DZV_INSN_DC_ZVA) return DZV_E_NOT_DC_ZVA;
    *rt = insn & 0x1f;
    return DZV_OK;
}

enum dzv_status dzv_emulate(const struct dzv_window *w, uint64_t va, uint32_t block_bytes, uint64_t *block_va)
{
    /* A power of two between the 16-byte minimum with FEAT_MTE2 and the 2 KiB maximum. */
    if (!w || !w->bytes || !block_va || block_bytes < 16 || block_bytes > 2048 || (block_bytes & (block_bytes - 1)))
        return DZV_E_PARAMS;
    const uint64_t block = va & ~((uint64_t)block_bytes - 1);
    /* The block is naturally aligned and no larger than a page, so one translation covers it: the whole block is
     * inside the window or none of it is. */
    if (block < w->base_va || block - w->base_va > w->len || w->len - (block - w->base_va) < block_bytes)
        return DZV_E_RANGE;
    if (w->device) return DZV_E_DEVICE;
    uint8_t *p = w->bytes + (block - w->base_va);
    for (uint32_t i = 0; i < block_bytes; i++) p[i] = 0;
    *block_va = block;
    return DZV_OK;
}

static const struct { const char *name, *cite; } rules[DZV_STATUS_COUNT] = {
    [DZV_OK] = { "OK", 0 },
    [DZV_E_PARAMS] = { "PARAMS", "model: a null or malformed argument, or an unknown functional result" },
    [DZV_E_RESERVED_BITS] = { "RESERVED_BITS",
        "VERIFIED: DDI 0601 DCZID_EL0, bits [63:9] RES0 and TBS [8:5] only with FEAT_MTETC" },
    [DZV_E_BLOCK_SIZE] = { "BLOCK_SIZE",
        "VERIFIED: DDI 0601 DCZID_EL0.BS is log2 of the block in words, maximum 9; XNU osfmk/arm64/bzero.s and "
        "loose_ends.c (xnu-12377.121.6, APSL-2.0) issue DC ZVA at a fixed 64-byte stride" },
    [DZV_E_UNTESTED] = { "UNTESTED",
        "model: fail closed; DZP is not evidence of the silicon (DDI 0601 DCZID_EL0) and the Oryon X1 claim is one "
        "third-party patch (OBSERVED, UNKNOWN mechanism)" },
    [DZV_E_BROKEN_NO_EMULATION] = { "BROKEN_NO_EMULATION",
        "model: XNU and the user-space zeroing loop execute DC ZVA unconditionally, so a broken instruction without "
        "emulation cannot run the guest (INFERRED for 26A428 from the public 12377 source)" },
    [DZV_E_NOT_SYSTEM_TRAP] = { "NOT_SYSTEM_TRAP", "VERIFIED: DDI 0601 ESR_EL2, EC 0x18 is a trapped MSR, MRS or System instruction" },
    [DZV_E_IL] = { "IL", "VERIFIED: DDI 0601 ESR_EL2, a trapped System instruction is a 32-bit instruction, IL = 1" },
    [DZV_E_ISS_RES0] = { "ISS_RES0", "VERIFIED: DDI 0601 ESR_EL2, ISS [24:22] are RES0 for EC 0x18" },
    [DZV_E_NOT_DC_ZVA] = { "NOT_DC_ZVA",
        "VERIFIED: DDI 0602 DC alias, DC ZVA is SYS #3, C7, C4, #1 (op0 1, op1 3, CRn 7, CRm 4, op2 1); DC GVA is op2 3 "
        "and DC GZVA op2 4" },
    [DZV_E_READ] = { "READ", "VERIFIED: DDI 0601 ESR_EL2, Direction 0 is a write access including System instructions" },
    [DZV_E_RANGE] = { "RANGE",
        "model: a block outside the writable window; the monitor must inject the guest's translation or permission "
        "fault (DDI 0487 D7.5.10: DC ZVA behaves as a set of stores)" },
    [DZV_E_DEVICE] = { "DEVICE",
        "VERIFIED: DDI 0487 D7.5.10 and C5.3.38, Device memory that does not support unaligned accesses gives an "
        "Alignment fault; XNU's loose_ends.c avoids DC ZVA for Device memory" },
};

const char *dzv_status_name(enum dzv_status s)
{
    return (unsigned)s < DZV_STATUS_COUNT && rules[s].name ? rules[s].name : "UNKNOWN_STATUS";
}

const char *dzv_rule_citation(enum dzv_status s)
{
    return (unsigned)s < DZV_STATUS_COUNT ? rules[s].cite : 0;
}

#else
typedef int snap_dc_zva_model_gate_off;
#endif /* SNAP_PLATFORM_SP11 */
