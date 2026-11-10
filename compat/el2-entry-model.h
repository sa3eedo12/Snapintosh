/* SPDX-License-Identifier: MIT */
/* Host model of the Surface Pro 11 EL2 entry transition (plan todo C1).
 *
 * UNIMPLEMENTED target, EXPERIMENTAL model: nothing here runs on hardware and
 * nothing links this file into a monitor build. It checks an ordered list of
 * EL2 operations against the architectural rules in docs/SP11-EL2-ENTRY.md, so
 * that the first measurement on the unit (gate X1/X2) only has to select a
 * branch. slbounce returns at EL2 with HCR_EL2.{E2H,TGE} set and the MMU on;
 * the monitor is a non-VHE host. The two ways out are a de-VHE trampoline
 * (modelled as data below) and a VHE-native monitor (modelled as the register
 * redirection functions at the end).
 *
 * Every rule carries an evidence label. VERIFIED means read in the Arm
 * Architecture Reference Manual (DDI 0487 M.d) or the register descriptions
 * (DDI 0601, 2026-09) and named in el2m_rule_citation(); INFERRED means a
 * conservative reading that no sentence of public text states outright. The
 * model fails closed: an unknown operation, register or state is a refusal,
 * never an acceptance.
 *
 * Owned by the SP11 effort; compiled only under SNAP_PLATFORM_SP11. Pure
 * computation: no I/O, no allocation, no global state, safe from any thread. */
#ifndef SNAP_COMPAT_EL2_ENTRY_MODEL_H
#define SNAP_COMPAT_EL2_ENTRY_MODEL_H

#include "platform.h"

#if SNAP_PLATFORM_SP11

#include <stddef.h>
#include <stdint.h>

/* HCR_EL2 (DDI 0601, HCR_EL2): TGE bit 27, RW bit 31, E2H bit 34. */
#define EL2M_HCR_TGE (UINT64_C(1) << 27)
#define EL2M_HCR_RW  (UINT64_C(1) << 31)
#define EL2M_HCR_E2H (UINT64_C(1) << 34)
/* SCTLR_EL2: M bit 0, C bit 2, I bit 12 in both layouts. */
#define EL2M_SCTLR_M (UINT64_C(1) << 0)
#define EL2M_SCTLR_C (UINT64_C(1) << 2)
#define EL2M_SCTLR_I (UINT64_C(1) << 12)

/* The HCR_EL2 q1n1 sees on the A16 (docs/A16-Q1N1-PROXY.md in the q1n1 tree):
 * RW, TGE and IMO, E2H clear. The de-VHE trampoline's goal is to give q1n1 and
 * the monitor the same EL2 state on the SP11 (INFERRED sufficient). */
#define EL2M_HCR_A16_Q1N1 UINT64_C(0x88000010)
/* HCR_EL2 as slbounce leaves it (src/trans.s, c090a8c): TGE and E2H only. */
#define EL2M_HCR_SLBOUNCE (EL2M_HCR_TGE | EL2M_HCR_E2H)

/* Register layouts. TCR_EL2, TTBR0_EL2, CNTHCTL_EL2 and CPTR_EL2 are
 * interpreted differently by the Effective value of HCR_EL2.E2H (VERIFIED:
 * DDI 0487 D8.13.1 rule QXCQP and the four register pages). */
enum el2m_layout { EL2M_NVHE = 0, EL2M_VHE = 1 };

enum el2m_reg {
    EL2M_HCR, EL2M_SCTLR, EL2M_TCR, EL2M_TTBR0, EL2M_TTBR1, EL2M_MAIR,
    EL2M_CNTHCTL, EL2M_CPTR, EL2M_VBAR, EL2M_REG_COUNT
};

enum el2m_op_kind {
    EL2M_OP_MSR,        /* reg, value, layout (layout used by TCR, TTBR0, CNTHCTL, CPTR) */
    EL2M_OP_TLBI_ALLE2, /* TLBI ALLE2 */
    EL2M_OP_DSB,        /* DSB ISH, which completes a preceding TLBI */
    EL2M_OP_ISB,        /* a context synchronization event */
    EL2M_OP_DATA        /* a load or store made by the sequence's own code */
};

struct el2m_op {
    uint8_t kind, reg, layout;
    uint64_t value;
};

/* Why an operation list was refused. Each value has a rule citation and an
 * evidence label (el2m_rule_citation). Order is part of the test oracle. */
enum el2m_status {
    EL2M_OK = 0,
    EL2M_E_ARRIVAL_STATE,         /* the CPU arrived in a state the sequence does not support */
    EL2M_E_E2H_CHANGE_MMU_ON,     /* the Effective E2H changes while SCTLR_EL2.M is 1 */
    EL2M_E_E2H_NOT_PROGRAMMABLE,  /* E2H cleared on a CPU where it is RES1 (no FEAT_E2H0) */
    EL2M_E_LAYOUT,                /* a register image written in, or left in, the other E2H's layout */
    EL2M_E_IMAGE_INVALID,         /* a value that breaks RES0 or RES1 of its declared layout */
    EL2M_E_CONFIG_WRITE_MMU_ON,   /* TCR, TTBR0, TTBR1 or MAIR written while the MMU is on */
    EL2M_E_MMU_TOGGLE_NOT_IDENTITY, /* M changes while the executing code is not identity mapped */
    EL2M_E_MMU_ON_STALE_TLB,      /* M 0->1 while the TLB may hold entries from another configuration */
    EL2M_E_MMU_ON_UNSYNC,         /* M 0->1 before an ISB made the configuration writes effective */
    EL2M_E_TLBI_UNSYNC_HCR,       /* TLBI ALLE2 while an HCR_EL2 write is not synchronized (strict) */
    EL2M_E_DATA_ACCESS_MMU_OFF,   /* a load or store while the MMU is off */
    EL2M_E_USE_BEFORE_SYNC,       /* a data access before an ISB made earlier writes effective */
    EL2M_E_FINAL_STATE,           /* the sequence ended short of its goal */
    EL2M_E_CPU_DIVERGE,           /* two CPUs ended in different configurations */
    EL2M_E_BAD_OP,                /* malformed operation */
    EL2M_STATUS_COUNT
};

/* Capabilities read from the ID registers (X2 evidence). */
struct el2m_platform {
    uint8_t e2h0_implemented;     /* ID_AA64MMFR4_EL1.E2H0 == 0: HCR_EL2.E2H can be cleared */
};

/* Strictness. The defaults (all 1) are the conservative reading. The two
 * relaxations exist so that the model can be checked against two sequences
 * known to run on real hardware (slbounce's VHE entry, the A16's
 * secondary_entry): each is accepted only with the relaxation named in the
 * test, which documents exactly what it relies on. */
struct el2m_policy {
    uint8_t strict_tlbi_after_config;  /* a config write at M=0 dirties the TLB even after a clean TLBI */
    uint8_t strict_isb_before_tlbi;    /* an HCR_EL2 write must be synchronized before TLBI ALLE2 */
};
#define EL2M_POLICY_STRICT ((struct el2m_policy){1, 1})

enum el2m_role { EL2M_ROLE_BOOT, EL2M_ROLE_SECONDARY };

/* The state of one CPU. Registers keep the image as last written; the layout
 * arrays remember which E2H layout each image was written for, because the
 * hardware reinterprets the stored bits when E2H changes. */
struct el2m_cpu {
    uint32_t cpu;
    uint8_t role;
    uint8_t code_identity;        /* the executing code has VA == PA in the current regime */
    uint8_t tlb_dirty;            /* the TLB may hold entries that disagree with the registers */
    uint8_t tlbi_state;           /* 0 none, 1 issued, 2 DSB done, 3 complete */
    uint32_t pending_isb;         /* one bit per register written since the last ISB */
    uint8_t layout[EL2M_REG_COUNT];
    uint64_t reg[EL2M_REG_COUNT];
};

/* What a sequence supports on arrival; -1 is "either". */
struct el2m_arrival { int8_t mmu, e2h, tge; };
struct el2m_goal { uint8_t e2h, tge, mmu; };

struct el2m_seq {
    const char *name;
    struct el2m_arrival pre;
    struct el2m_goal goal;
    const struct el2m_op *ops;
    size_t n;
};

/* Effective E2H of an HCR_EL2 image: RES1 and behaving as 1 without FEAT_E2H0,
 * even where a direct read returns the written 0 (DDI 0601, HCR_EL2.E2H). */
unsigned el2m_effective_e2h(const struct el2m_platform *plat, uint64_t hcr);
enum el2m_layout el2m_layout_of(const struct el2m_platform *plat, uint64_t hcr);

/* Put a CPU in an arrival state. The registers other than HCR_EL2 and
 * SCTLR_EL2 hold images in the layout of the arrival E2H, as firmware wrote
 * them; the TLB is unknown (DDI 0487 D8.16.1), so it is dirty. */
void el2m_cpu_arrive(struct el2m_cpu *c, const struct el2m_platform *plat, enum el2m_role role,
                     uint32_t cpu, unsigned mmu, unsigned e2h, unsigned tge, unsigned code_identity);

/* One step. A refusal leaves the CPU unchanged except for the operation that
 * was refused not being applied. */
enum el2m_status el2m_step(struct el2m_cpu *c, const struct el2m_platform *plat,
                           const struct el2m_policy *policy, const struct el2m_op *op);

/* Run a sequence from arrival to goal. On refusal, *fail_index is the index of
 * the operation (n for a final-state or arrival refusal). */
enum el2m_status el2m_run(struct el2m_cpu *c, const struct el2m_platform *plat,
                          const struct el2m_policy *policy, const struct el2m_seq *seq,
                          size_t *fail_index);

/* CPUs that ran their own sequences must end in one configuration, because
 * the secondaries reuse the boot CPU's tables and the monitor assumes one
 * EL2 regime on every CPU. */
enum el2m_status el2m_cpus_consistent(const struct el2m_cpu *cpus, size_t n);

/* Register image validity and conversion (VERIFIED field positions, DDI 0601). */
int el2m_image_valid(enum el2m_reg reg, enum el2m_layout layout, uint64_t value);
/* TCR_EL2: the TTBR0 half of a VHE image as a non-VHE image. Returns 0 and
 * fills *out, or -1 when the VHE image cannot be represented (TTBR0 walks
 * disabled by EPD0, or the image is invalid). TTBR1-only fields are dropped. */
int el2m_tcr_vhe_to_nvhe(uint64_t vhe, uint64_t *out);
/* TTBR0_EL2: drop the ASID, which is RES0 when E2H is 0. */
uint64_t el2m_ttbr0_vhe_to_nvhe(uint64_t vhe);
/* CNTHCTL_EL2: carry the EL1 physical counter and timer controls (bits 0 and 1
 * in the non-VHE layout, bits 10 and 11 in the VHE layout) and every field both
 * layouts share. Returns 0, or -1 for an invalid source image. VHE-only EL0
 * controls become "not trapped"; non-VHE-only fields have no VHE meaning. */
int el2m_cnthctl_convert(enum el2m_layout from, enum el2m_layout to, uint64_t value, uint64_t *out);

/* Canonical sequences (data) and reference sequences replicated from source. */
extern const struct el2m_seq el2m_seq_boot_devhe;      /* slbounce return: MMU on, E2H=TGE=1 */
extern const struct el2m_seq el2m_seq_universal_devhe; /* any arrival state, boot or secondary */
extern const struct el2m_seq el2m_seq_secondary_devhe; /* PSCI arrival: MMU off */
extern const struct el2m_seq el2m_seq_slbounce_devhe;  /* slbounce's restore, converted: MMU already off in tb_entry */
extern const struct el2m_seq el2m_ref_slbounce_vhe;    /* slbounce tb_longjmp (VHE direction) */
extern const struct el2m_seq el2m_ref_a16_secondary;   /* probes/secondary.S secondary_entry */
/* Values the canonical sequences write; the layouts match the sequence.
 * TCR_EL2 non-VHE image: T0SZ 16 (48-bit VA), IRGN0 = ORGN0 = write-back, SH0
 * inner shareable, TG0 4 KiB, PS 44 bits, RES1 bits 31 and 23 (VERIFIED field
 * positions, DDI 0601 TCR_EL2). EL2M_TEST_TCR_VHE is the same translation as a
 * TCR_EL1-format (VHE) image, with the TTBR1 half programmed as UEFI would. */
#define EL2M_CANON_SCTLR_ON  (EL2M_SCTLR_M | EL2M_SCTLR_C | EL2M_SCTLR_I)
#define EL2M_CANON_TCR_NVHE  UINT64_C(0x80843510)
#define EL2M_TEST_TCR_VHE    UINT64_C(0x4b5103510)
#define EL2M_CANON_TTBR0     UINT64_C(0x0000000080000000)
#define EL2M_CANON_MAIR      UINT64_C(0x000000ffbb440400)
#define EL2M_CANON_CNTHCTL   UINT64_C(0x0000000000006000) /* EL1TVT, EL1TVCT; EL1 physical trapped */
#define EL2M_CANON_CPTR      UINT64_C(0x00000000000022ff) /* RES1 only: nothing trapped */

const char *el2m_status_name(enum el2m_status s);
/* "label: Arm ARM or source reference" for a refusal, or NULL for EL2M_OK. */
const char *el2m_rule_citation(enum el2m_status s);

/* ---- VHE-native track: register-access redirection --------------------- *
 * With E2H = 1 at EL2, an EL1-named system register access reaches the EL2
 * register, and the _EL12 alias reaches the real EL1 register (VERIFIED:
 * DDI 0487 D8.13.2 and D8.13.3). A VHE-native save and restore routine must
 * therefore use the alias for guest state. */
enum el2m_acc_name { EL2M_NAME_EL1, EL2M_NAME_EL12, EL2M_NAME_EL2, EL2M_NAME_EL0, EL2M_NAME_EL02 };
enum el2m_acc_intent { EL2M_INTENT_HOST, EL2M_INTENT_GUEST };
enum el2m_acc_status {
    EL2M_ACC_OK = 0,
    EL2M_ACC_REDIRECTED_TO_HOST,  /* guest intent, EL1 or EL0 name, E2H = 1: reaches the host's EL2 register */
    EL2M_ACC_ALIAS_UNDEFINED,     /* an _EL12 or _EL02 name with E2H = 0, or on a register without one */
    EL2M_ACC_UNORDERED_NAMES,     /* one register reached through two names with no ISB between them */
    EL2M_ACC_UNKNOWN_REGISTER
};
struct el2m_acc {
    const char *reg;       /* base name without the exception-level suffix, e.g. "TCR" */
    uint8_t name, intent;
    uint8_t isb_before;    /* an ISB precedes this access */
};
/* What the register description table says about a base name: 1 if an EL1-named
 * access is redirected to EL2 under E2H = 1 and the _EL12 alias exists, 2 for
 * the EL0 timer registers (redirected to CNTHP/CNTHV, alias _EL02), 0 for a
 * register that is not redirected, -1 for a name the table does not know. */
int el2m_redirect_class(const char *reg);
enum el2m_acc_status el2m_check_accesses(unsigned e2h, const struct el2m_acc *acc, size_t n,
                                         size_t *fail_index);

#endif /* SNAP_PLATFORM_SP11 */
#endif /* SNAP_COMPAT_EL2_ENTRY_MODEL_H */
