/* SPDX-License-Identifier: MIT */
/*
 * Is the display pipeline the firmware left running still active? A pure decision over a register snapshot.
 *
 * Status: EXPERIMENTAL, host-tested only (tools/test-display-pipeline-check.py). Nothing in the monitor links this
 * file and no hardware has run it. Every statement about what a register means on the Surface Pro 11 (SP11,
 * X1E80100 "Hamoa") is INFERRED from public Linux sources until the X2 functional test has read a real unit; the
 * pinned evidence and the exact read list are in docs/SP11-DISPLAY-STUDY.md. The A16 default build never includes
 * this header.
 *
 * What it is. The reader (the X2 test now, later the monitor) reads a short list of registers from the MDSS blocks
 * that the firmware's display driver already programmed, in a fixed order, and fills a snapshot. This module turns
 * the snapshot into one verdict:
 *   ACTIVE        every required evidence class was affirmatively established and nothing contradicts it;
 *   INACTIVE      an affirmative reading says the pipeline is off (power domain, a clock or the timing engine);
 *   UNKNOWN       the snapshot cannot decide: a register was unreadable, not read, all-ones, or the snapshot or the
 *                 parameters are malformed or truncated;
 *   INCONSISTENT  two readings, or a reading and the parameters, contradict each other.
 * Only ACTIVE permits reusing the firmware pipeline. The other three mean "do not draw, report the reason".
 *
 * What it never does. No MMIO, no allocation, no libc, no globals, no I/O: it reads the two buffers it is given
 * and writes the result record. The same input always gives the same output.
 *
 * Nothing about the hardware is a constant here. Block bases, every register offset, the expected INTF index, the
 * DPU version and the expected mode all arrive in struct dpc_params, so the SP11 values come from a descriptor or
 * the pinned device tree and a different SoC needs no code change. What stays in this file is the meaning of a few
 * bits that Linux treats as architectural for these IP blocks (cited next to each), and the decision rules.
 *
 * Reader contract (the verdict is only as safe as the order the reader followed):
 *   1. Read DPC_REG_GCC_DISP_AHB first: it is the clock that makes the display clock controller's registers
 *      reachable, and the pinned Linux GCC driver keeps it always on. Then DPC_REG_GDSC, DPC_REG_AHB_CBCR and
 *      DPC_REG_MDP_CBCR, in that order. STOP at the first one that reports "off" or cannot be read. An MDSS read
 *      with the power domain or a bus clock off can raise an SError or hang the bus (OBSERVED on the A16,
 *      tools/survey-dpu-intf.py); a stopped reader leaves the remaining slots NOT_TRIED and the verdict is INACTIVE
 *      (a gate that reads off) or UNKNOWN (a gate that could not be read), which is the correct answer.
 *   2. Only then read the MDSS version, the INTF registers and the CTL registers.
 *   3. Store every register's physical address, so the parameters are cross-checked against what was really read.
 *   4. A faulting access is recorded as DPC_ST_FAULT with the value ignored; never substitute a value.
 *
 * Threading: reentrant. The caller owns both buffers and must not modify them during the call.
 */
#ifndef SNAP_DISPLAY_PIPELINE_CHECK_H
#define SNAP_DISPLAY_PIPELINE_CHECK_H

#include <stddef.h>
#include <stdint.h>

/* ---- Registers in the snapshot ----------------------------------------------------------------------------------- */
enum dpc_reg {
    DPC_REG_GCC_DISP_AHB,       /* gcc: GCC_DISP_AHB_CLK branch clock, the dispcc register clock; bit 31 CLK_OFF (Linux
                                 * gcc-x1e80100.c:7444-7447, always on in that driver) */
    DPC_REG_GDSC,               /* dispcc: MDSS GDSC control/status; bit 31 PWR_ON (Linux gdsc.c:21, 87-92) */
    DPC_REG_AHB_CBCR,           /* dispcc: MDSS AHB branch clock; bit 31 CLK_OFF (Linux clk-branch.h:63) */
    DPC_REG_MDP_CBCR,           /* dispcc: MDSS MDP core branch clock; bit 31 CLK_OFF */
    DPC_REG_HW_VERSION,         /* mdss: HW_VERSION, major 31:28, minor 27:16, step 15:0 (Linux msm_mdss.c:241-244) */
    DPC_REG_INTF_EN,            /* intf: TIMING_ENGINE_EN, bit 0 (Linux dpu_hw_intf.c:17, 275-282) */
    DPC_REG_INTF_STATUS,        /* intf: STATUS, bit 0 = engine running on DPU >= 5 (dpu_hw_intf.c:75, 332-333) */
    DPC_REG_INTF_CONFIG,        /* intf: CONFIG; bit 29 ACTIVE_H_EN, bit 30 ACTIVE_V_EN (dpu_hw_intf.c:95-96) */
    DPC_REG_INTF_HSYNC_CTL,     /* intf: HSYNC_CTL = htotal << 16 | hsync pulse width (dpu_hw_intf.c:167) */
    DPC_REG_INTF_VSYNC_PERIOD,  /* intf: VSYNC_PERIOD_F0 = vtotal * htotal (dpu_hw_intf.c:244) */
    DPC_REG_INTF_DISP_V_START,  /* intf: DISPLAY_V_START_F0 */
    DPC_REG_INTF_DISP_V_END,    /* intf: DISPLAY_V_END_F0 */
    DPC_REG_INTF_ACT_V_START,   /* intf: ACTIVE_V_START_F0 */
    DPC_REG_INTF_ACT_V_END,     /* intf: ACTIVE_V_END_F0 */
    DPC_REG_INTF_DISP_HCTL,     /* intf: DISPLAY_HCTL = end << 16 | start */
    DPC_REG_INTF_ACT_HCTL,      /* intf: ACTIVE_HCTL = end << 16 | start */
    DPC_REG_INTF_CONFIG2,       /* intf: CONFIG2; bit 0 DATABUS_WIDEN halves DP horizontal values (dpu_hw_intf.c:98) */
    DPC_REG_INTF_FLC_EN,        /* intf: FRAME_LINE_COUNT_EN, nonzero = counters claimed enabled (Linux writes 3) */
    DPC_REG_INTF_FRAME_A,       /* intf: FRAME_COUNT, first sample */
    DPC_REG_INTF_FRAME_B,       /* intf: FRAME_COUNT, second sample, read sample_gap_us later */
    DPC_REG_COUNT
};

enum dpc_blk { DPC_BLK_GCC, DPC_BLK_DISPCC, DPC_BLK_MDSS, DPC_BLK_INTF, DPC_BLK_COUNT };

#define DPC_MAX_CTL         8u      /* CTL paths a snapshot may carry; the pinned Hamoa catalog lists 6 */
#define DPC_MAX_INTF_INDEX  31u     /* CTL_INTF_ACTIVE is 32 bits wide, bit n = INTF_n (Linux dpu_hw_ctl.c:599) */
#define DPC_MAX_DIMENSION   16384u  /* a width or height above this is a corrupt report, not a panel */

#define DPC_CBCR_CLK_OFF    (1u << 31)
#define DPC_GDSC_PWR_ON     (1u << 31)
#define DPC_INTF_EN_BIT     (1u << 0)
#define DPC_INTF_STATUS_EN  (1u << 0)
#define DPC_INTF_CFG_ACTIVE_H_EN (1u << 29)
#define DPC_INTF_CFG_ACTIVE_V_EN (1u << 30)
#define DPC_INTF_CFG2_WIDEN (1u << 0)

/* Per-register outcome as the reader saw it. */
#define DPC_ST_NOT_TRIED    0u      /* the reader never attempted it (it stopped earlier, or skipped it) */
#define DPC_ST_OK           1u      /* the access completed and `value` is what it returned */
#define DPC_ST_FAULT        2u      /* the access trapped or timed out: `value` carries no information */

/* ---- Evidence classes ---------------------------------------------------------------------------------------------- */
#define DPC_EV_GATES        (1u << 0)   /* power domain on, AHB and MDP clocks running */
#define DPC_EV_VERSION      (1u << 1)   /* the version register decodes to the expected major.minor */
#define DPC_EV_TIMING       (1u << 2)   /* engine enabled (EN and STATUS agree) with coherent programmed timing */
#define DPC_EV_GEOMETRY     (1u << 3)   /* programmed active area equals the GOP/EDID mode */
#define DPC_EV_ROUTING      (1u << 4)   /* a CTL path lists the expected INTF as active */
#define DPC_EV_COUNTERS     (1u << 5)   /* the frame counter advanced at a plausible rate */
#define DPC_EV_ALL          0x3fu
/* Never waivable: without these nothing says the firmware's pipeline is running the mode we think it is. */
#define DPC_EV_CORE         (DPC_EV_GATES | DPC_EV_TIMING | DPC_EV_GEOMETRY)

/* ---- Parameters ---------------------------------------------------------------------------------------------------- */
#define DPC_PARAMS_ABI      1u

struct dpc_block {
    uint64_t base;              /* physical address of the block's first register */
    uint64_t length;            /* bytes; every register of the block must fit inside */
};

struct dpc_params {
    uint32_t abi;               /* DPC_PARAMS_ABI */
    uint32_t required;          /* DPC_EV_* classes that must be established; must include DPC_EV_CORE */
    uint32_t intf_index;        /* INTF that feeds the eDP controller, from the catalog and the device tree */
    uint32_t dpu_major;         /* expected DPU version (catalog); nonzero when DPC_EV_VERSION is required */
    uint32_t dpu_minor;
    uint32_t gop_width;         /* the mode to confirm, from GOP/EDID (tools/sp11_display_mode.py), pixels */
    uint32_t gop_height;
    uint32_t ctl_count;         /* CTL paths in ctl_base[]; at least 1 when DPC_EV_ROUTING is required */
    uint32_t ctl_length;        /* bytes of one CTL page that contain ctl_intf_active_off */
    uint32_t ctl_intf_active_off;
    uint32_t refresh_hz_min;    /* plausible refresh range for the counter check; both 0 = counters unusable */
    uint32_t refresh_hz_max;
    uint32_t counter_gap_min_us;/* a gap below this proves nothing about the counter */
    uint32_t reserved;          /* must be 0 */
    struct dpc_block gcc, dispcc, mdss, mdp, intf;  /* intf and every CTL page lie inside mdp */
    uint64_t ctl_base[DPC_MAX_CTL];
    uint32_t reg_off[DPC_REG_COUNT];                /* offset of each register inside its block, 4-byte aligned */
};
_Static_assert(DPC_REG_COUNT == 20, "the reader and the study's dump list name exactly these registers");
_Static_assert(sizeof(struct dpc_params) == 14 * 4 + 5 * 16 + 8 * DPC_MAX_CTL + 4 * DPC_REG_COUNT,
               "dpc_params has no implicit padding");

/* ---- Snapshot wire format (little-endian, read byte by byte, so alignment and host endianness never matter) ---- */
#define DPC_SNAP_MAGIC      0x53435044u     /* bytes 'D' 'P' 'C' 'S' */
#define DPC_SNAP_ABI_MAJOR  1u
#define DPC_SNAP_ABI_MINOR  0u
#define DPC_SNAP_HEADER     32u             /* magic u32, abi_major u16, abi_minor u16, total_length u32, intf_index u32,
                                             * reg_count u32, ctl_count u32, sample_gap_us u32, reserved u32 (0) */
#define DPC_SNAP_ENTRY      16u             /* pa u64, value u32, status u32; reg_count entries, then ctl_count */
#define DPC_SNAP_OFF_TOTAL  8u
#define DPC_SNAP_OFF_INTF   12u
#define DPC_SNAP_OFF_REGS   16u
#define DPC_SNAP_OFF_CTLS   20u
#define DPC_SNAP_OFF_GAP    24u

/* ---- Verdict ------------------------------------------------------------------------------------------------------- */
enum dpc_verdict { DPC_ACTIVE = 1, DPC_INACTIVE = 2, DPC_UNKNOWN = 3, DPC_INCONSISTENT = 4 };

enum dpc_why {
    DPC_WHY_OK,                 /* ACTIVE */
    DPC_WHY_BAD_PARAMS,         /* UNKNOWN; detail = enum dpc_param_fault */
    DPC_WHY_TRUNCATED,          /* UNKNOWN; fewer bytes than the snapshot claims, or fewer entries than needed */
    DPC_WHY_MALFORMED,          /* UNKNOWN; magic, ABI, counts, lengths or a status value are invalid */
    DPC_WHY_NOT_READ,           /* UNKNOWN; detail = enum dpc_reg */
    DPC_WHY_READ_FAULT,         /* UNKNOWN; detail = enum dpc_reg */
    DPC_WHY_ALL_ONES,           /* UNKNOWN; detail = enum dpc_reg: 0xffffffff is what an unclocked block returns */
    DPC_WHY_MISSING_EVIDENCE,   /* UNKNOWN; detail = the DPC_EV_* classes required and not established */
    DPC_WHY_DISPCC_NO_ACCESS,   /* UNKNOWN: the dispcc register clock is off, so the gates cannot be read */
    DPC_WHY_POWER_OFF,          /* INACTIVE: the MDSS power domain is off */
    DPC_WHY_AHB_CLOCK_OFF,      /* INACTIVE */
    DPC_WHY_MDP_CLOCK_OFF,      /* INACTIVE */
    DPC_WHY_TIMING_OFF,         /* INACTIVE: gates on, but EN and STATUS both say the engine is stopped */
    DPC_WHY_ADDRESS_MISMATCH,   /* INCONSISTENT; detail = enum dpc_reg: the snapshot read another address */
    DPC_WHY_INTF_MISMATCH,      /* INCONSISTENT: the reader read a different INTF than the parameters name */
    DPC_WHY_VERSION_MISMATCH,   /* INCONSISTENT */
    DPC_WHY_ENABLE_STATUS,      /* INCONSISTENT: EN and STATUS disagree */
    DPC_WHY_NO_TIMING,          /* INCONSISTENT: engine enabled but no timing is programmed */
    DPC_WHY_TIMING_INCOHERENT,  /* INCONSISTENT; detail = enum dpc_timing_fault */
    DPC_WHY_GEOMETRY_MISMATCH,  /* INCONSISTENT: the programmed active area is not the GOP/EDID mode */
    DPC_WHY_ROUTING_MISMATCH,   /* INCONSISTENT: the engine runs but no CTL path lists this INTF */
    DPC_WHY_COUNTER_STALLED,    /* INCONSISTENT: counters claimed enabled, but the count did not move */
    DPC_WHY_COUNTER_FAST,       /* INCONSISTENT: the count moved faster than any plausible refresh rate */
    DPC_WHY_COUNT
};

enum dpc_param_fault {
    DPC_PF_NONE, DPC_PF_NULL, DPC_PF_ABI, DPC_PF_RESERVED, DPC_PF_REQUIRED, DPC_PF_INTF_INDEX, DPC_PF_VERSION,
    DPC_PF_MODE, DPC_PF_BLOCK, DPC_PF_OVERLAP, DPC_PF_OFFSET, DPC_PF_CTL, DPC_PF_COUNTER
};

enum dpc_timing_fault {
    DPC_TF_NONE, DPC_TF_HTOTAL, DPC_TF_PERIOD, DPC_TF_VTOTAL, DPC_TF_V_WINDOW, DPC_TF_H_WINDOW, DPC_TF_OUTSIDE_FRAME
};

struct dpc_result {
    uint32_t verdict;           /* enum dpc_verdict; DPC_UNKNOWN is what an unusable call returns */
    uint32_t why;               /* enum dpc_why */
    uint32_t detail;            /* meaning depends on why, see enum dpc_why; 0 when unused */
    uint32_t evidence;          /* DPC_EV_* classes established before the decision was made */
};

/* Decide. `snap` is `snap_len` bytes of the wire format above. Always fills *out (UNKNOWN until a rule decides) and
 * returns 0; returns -1 only when out is NULL. Unreadable, truncated, out-of-range and contradictory inputs never
 * yield DPC_ACTIVE. */
int display_pipeline_check(const struct dpc_params *params, const void *snap, size_t snap_len, struct dpc_result *out);

/* Short, stable, lower-case names for logs ("unknown" for an out-of-range code). */
const char *dpc_verdict_name(uint32_t verdict);
const char *dpc_why_name(uint32_t why);

/* Snapshot writer for the reader. dpc_snapshot_init zero-fills the whole snapshot (header plus every entry, so each
 * entry starts as DPC_ST_NOT_TRIED) and returns its length in bytes; it returns 0 when buf is NULL, cap is too small,
 * ctl_count exceeds DPC_MAX_CTL or intf_index exceeds DPC_MAX_INTF_INDEX. The put functions return 0, or -1 for a bad
 * index or a buffer shorter than the snapshot; they never write outside buf[0..len). */
size_t dpc_snapshot_init(void *buf, size_t cap, uint32_t intf_index, uint32_t ctl_count);
int dpc_snapshot_put(void *buf, size_t len, uint32_t reg, uint64_t pa, uint32_t value, uint32_t status);
int dpc_snapshot_put_ctl(void *buf, size_t len, uint32_t ctl, uint64_t pa, uint32_t value, uint32_t status);
int dpc_snapshot_set_gap(void *buf, size_t len, uint32_t gap_us);

#endif
