/* SPDX-License-Identifier: MIT */
/* IPA/PA geometry of the stage-2 identity map, as a model that runs natively.
 *
 * The monitor maps guest RAM and every device window it hands over as an
 * identity (IPA == PA) stage-2 tree whose width is fixed when the monitor is
 * built: S2_TXSZ=28 in probes/xnuboot/stage2.c is a 36-bit IPA (VERIFIED,
 * source), and shadow/stage2.c refuses any range that ends above
 * 1 << (64 - txsz). Nothing the A16 maps comes near that line. The X1E80100
 * ships with 16, 32 or 64 GiB of DRAM (INFERRED: public Linux platform
 * conventions; the real memory map is measured at gate X2 and is UNIMPLEMENTED
 * here), and the public layout keeps its high DRAM window above 0x8_8000_0000,
 * so a 32 GiB part ends exactly on 1 << 36 and a 64 GiB part ends 32 GiB past
 * it. Whether such a layout can be identity-mapped has to be answered before a
 * guest runs, with a reason, rather than by a stage-2 build that can only say
 * STAGE2_BAD_RANGES.
 *
 * Two entry points share one set of rules:
 *
 *  - ipag_check_spans(): the check the monitor makes on the stage-2 range list
 *    it is about to build (compiled in when SNAP_PLATFORM_SP11 links this unit,
 *    probes/run.py OPTIONAL['xnuboot']). It adds what stage2_build cannot say:
 *    the IPA width taken from S2_TXSZ, the PARange the hardware reports, and a
 *    specific reason.
 *  - ipag_plan_stage2(): the model. Physical DRAM windows and MMIO windows in,
 *    either the guest-RAM carve-out with its mapping plan or one refusal reason
 *    out. Host tooling and the native test use it; the freestanding monitor
 *    build leaves it out (IPAG_WITH_PLANNER), which keeps its O(n^2) loops and
 *    result structure out of the probe image.
 *
 * Both are pure: no globals, no libc, no I/O, nothing retained from the
 * caller's arrays. A parameter that is out of range, a window that is empty,
 * unaligned, wrapped, overlapping or beyond the IPA width or the PARange, and a
 * carve-out that does not fit are all refusals with a distinct reason; nothing
 * is clamped or repaired, and no fabricated success is returned.
 *
 * Why the limit is "end <= 1 << ipa_bits" and not "<": the IPA space is
 * [0, 1 << ipa_bits), so a window whose exclusive end is exactly 1 << ipa_bits
 * is the last mappable page range (shadow/stage2.c: hi > limit refuses).
 * Every comparison below is in difference form so that no sum can wrap.
 *
 * The geometry that is not a function of S2_TXSZ is deliberately not modelled
 * here: the emulated guest's own 36-bit physical width (compat/dart.c,
 * compat/sptm-dart.c, compat/device/device_core.c) is a contract with the
 * T8140 guest, not with the host SoC. tools/test-stage2-geometry.py pins the
 * constants this model mirrors to the real sources so they cannot drift apart.
 */
#pragma once
#include <stdint.h>

/* The stage-2 granule: every window is a whole number of these. */
#define IPAG_PAGE_SIZE 0x1000UL
#define IPAG_BLOCK_2M (1UL << 21)
#define IPAG_BLOCK_1G (1UL << 30)

/* IPA widths the model accepts. 48 is the largest input size without
 * FEAT_LPA2 (shadow/stage2.c stops at txsz 16). 32 is the model's own floor,
 * not the builder's, which goes down to 25 bits: the lowest DRAM the plan
 * maps starts at 0x8000_0000 (INFERRED), so a narrower IPA space could not
 * hold any DRAM map and would only produce refusals that say nothing new. The
 * monitor's own width is 64 - S2_TXSZ; this default mirrors it and the drift
 * guard in tools/test-stage2-geometry.py fails if the two ever differ. */
#define IPAG_MIN_IPA_BITS 32u
#define IPAG_MAX_IPA_BITS 48u
#define IPAG_DEFAULT_IPA_BITS 36u

/* XNU rounds the guest DRAM it is given to this boundary (probes/xnuboot.py
 * REGION_ALIGN), so a carve-out is placed and sized in multiples of it unless
 * the caller asks for another power of two in [2 MiB, 1 GiB]. The upper bound
 * also keeps the rounding arithmetic far from wrapping. */
#define IPAG_DEFAULT_CARVE_ALIGN (32UL << 20)
#define IPAG_MIN_CARVE_ALIGN IPAG_BLOCK_2M
#define IPAG_MAX_CARVE_ALIGN IPAG_BLOCK_1G

/* Bounds the quadratic overlap checks. The monitor's stage-2 list is a few
 * dozen entries; a real firmware memory map is merged before it gets here. */
#define IPAG_MAX_WINDOWS 64u
#define IPAG_NO_INDEX 0xffffffffu

/* probes/xnuboot/stage2.c returns -(IPAG_HOOK_RETURN_BASE + reason) from
 * vcpu_stage2_prepare when the SP11 check refuses. The base sits above every
 * code xnuboot_prepare returns today (-1..-82), so the reason survives to the
 * host as one number. */
#define IPAG_HOOK_RETURN_BASE 256

enum ipag_reason {
    IPAG_OK = 0,
    IPAG_NULL_INPUT,
    IPAG_BAD_IPA_BITS,          /* outside [32, 48] */
    IPAG_BAD_PARANGE,           /* ID_AA64MMFR0_EL1.PARange encoding not decoded (> 6) */
    IPAG_BAD_POLICY,
    IPAG_NO_WINDOWS,            /* an empty list: nothing would be mapped */
    IPAG_TOO_MANY_WINDOWS,      /* more than IPAG_MAX_WINDOWS */
    IPAG_ZERO_SIZE,
    IPAG_INVERTED,              /* hi < lo: base + size wrapped before it got here */
    IPAG_OVERFLOW,              /* base + size does not fit in 64 bits */
    IPAG_UNALIGNED,             /* base or size not a multiple of 4 KiB */
    IPAG_UNSORTED,              /* windows must be strictly ascending; nothing is sorted for the caller */
    IPAG_OVERLAP,
    IPAG_MMIO_IN_DRAM,
    IPAG_ABOVE_PARANGE,         /* ends beyond the physical address space the CPU reports */
    IPAG_WINDOW_ABOVE_LIMIT,    /* starts at or above 1 << ipa_bits */
    IPAG_WINDOW_CROSSES_LIMIT,  /* starts below 1 << ipa_bits and ends above it */
    IPAG_CARVE_UNALIGNED,       /* carve alignment not a power of two in range, or want not a multiple of it */
    IPAG_CARVE_NO_FIT,          /* no DRAM below the limit holds the requested carve-out */
    IPAG_REASON_COUNT
};

/* ID_AA64MMFR0_EL1.PARange to physical address bits: 0 -> 32, 1 -> 36, 2 -> 40,
 * 3 -> 42, 4 -> 44, 5 -> 48. Encoding 6 is 52 bits (FEAT_LPA) in hardware; the
 * monitor caps the field at 5 (probes/xnuboot/prepare.c VTCR_EL2.PS,
 * probes/xnuboot/stage2.c shadow_tcr) because the shadow decodes 48-bit output
 * addresses only, so it is 48 here too. Returns 0 for an encoding that is not
 * decoded. */
unsigned ipag_pa_bits(unsigned parange);

/* A span [lo, hi) of intermediate (== physical) addresses, the shape of
 * struct stage2_range's first two fields. */
struct ipag_span {
    uint64_t lo, hi;
};

/* Check the list stage2_build is about to map: every span non-empty, 4 KiB
 * aligned, ending at or below 1 << ipa_bits and 1 << (PARange bits), and
 * disjoint from every other. Order does not matter (stage2_build takes the list
 * in any order). Returns IPAG_OK or the first reason found; `bad_index`, when
 * not NULL, receives the offending span (IPAG_NO_INDEX for a list-level
 * reason). Nothing is written to the spans. */
int ipag_check_spans(const struct ipag_span *spans, unsigned count,
                     unsigned ipa_bits, unsigned parange, unsigned *bad_index);

/* The planner is host-side modelling. The freestanding monitor build (which
 * defines __STDC_HOSTED__ as 0) needs only the span check above and leaves the
 * planner out of the probe image; -DIPAG_WITH_PLANNER=1 forces it in. */
#ifndef IPAG_WITH_PLANNER
#define IPAG_WITH_PLANNER __STDC_HOSTED__
#endif

#if IPAG_WITH_PLANNER

/* A physical window [base, base + size). */
struct ipag_window {
    uint64_t base, size;
};

enum ipag_policy {
    /* Any DRAM window that crosses or lies above 1 << ipa_bits refuses the
     * whole plan. The default: the identity-mapped guest could not see that
     * RAM, and shrinking a machine's memory is an operator decision, not a
     * side effect. */
    IPAG_POLICY_REFUSE = 0,
    /* DRAM at or above the limit is dropped and DRAM across it is cut at it,
     * then the carve-out is placed in what remains. The decision is reported
     * (plan.dram_dropped, plan.trimmed_windows); MMIO is never trimmed, so an
     * MMIO window that crosses or lies above the limit still refuses. */
    IPAG_POLICY_CARVE_LOW = 1
};

struct ipag_input {
    unsigned ipa_bits;                  /* 64 - S2_TXSZ for the monitor */
    unsigned parange;                   /* raw ID_AA64MMFR0_EL1.PARange */
    unsigned policy;                    /* enum ipag_policy */
    uint64_t want_bytes;                /* carve-out size; 0 = the largest span that fits.
                                         * Includes the bootstrap reserve above guest DRAM. */
    uint64_t align;                     /* carve-out base/size granularity; 0 = IPAG_DEFAULT_CARVE_ALIGN */
    const struct ipag_window *dram;     /* strictly ascending, disjoint */
    unsigned dram_count;
    const struct ipag_window *mmio;     /* strictly ascending, disjoint, outside DRAM; may be empty */
    unsigned mmio_count;
};

struct ipag_plan {
    unsigned ipa_bits, pa_bits;
    uint64_t ipa_limit, pa_limit;       /* exclusive ends of the IPA space and the PA space */

    /* The guest-RAM carve-out: one contiguous identity range inside one DRAM
     * window, below ipa_limit, aligned and sized in multiples of `align`.
     * Placed in the lowest window that fits when want_bytes is given (carve
     * low, docs/SURFACE-PRO-11.md R8), else in the largest span. */
    uint64_t carve_base, carve_size;

    uint64_t dram_total;                /* every DRAM window given */
    uint64_t dram_usable;               /* the part below ipa_limit */
    uint64_t dram_dropped;              /* dram_total - dram_usable: what CARVE_LOW gave up */
    unsigned trimmed_windows;           /* windows CARVE_LOW cut or removed */

    /* What stage2_build emits for the carve-out and the MMIO windows (not the
     * monitor's own work-area ranges): the root level and its entry count for
     * ipa_bits, the leaf mix of shadow_map's largest-descriptor rule, and the
     * tables allocated below the root. */
    unsigned start_level, root_entries;
    uint64_t leaves_1g, leaves_2m, leaves_4k;
    uint64_t table_pages;

    /* For a refusal: the window the reason is about, and for a window across
     * or above the limit how many bytes lie beyond it. */
    unsigned bad_index;
    unsigned bad_is_mmio;
    uint64_t excess;
};

/* Level the 4K-granule walk starts at for an IPA width, the way
 * shadow/mmu.c mmu_start_level(12, 64 - ipa_bits) computes it; -1 outside
 * [IPAG_MIN_IPA_BITS, IPAG_MAX_IPA_BITS]. */
int ipag_start_level(unsigned ipa_bits);

/* Plan the carve-out and mapping for the given windows. Returns IPAG_OK with a
 * complete plan, or a reason with `out` holding whatever was established
 * before the refusal: the limits, the offending window (bad_index, excess),
 * and once the windows are well formed the DRAM totals, so a refusal for
 * DRAM above the limit still says how much of it the IPA could address. The
 * carve-out and mapping fields are filled only for IPAG_OK. `out` is always
 * written. */
int ipag_plan_stage2(const struct ipag_input *in, struct ipag_plan *out);

const char *ipag_reason_name(int reason);

#endif /* IPAG_WITH_PLANNER */
