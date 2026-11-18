/* SPDX-License-Identifier: MIT */
/* Host-less NVMe takeover, stage 1 (SP11): the unit-side form of q1n1's tools/nvme_m3.py sequence, read only.
 *
 * Status: EXPERIMENTAL, host-tested only (tools/test-nvme-takeover.py). Nothing here is linked into the monitor, a
 * bundle or any payload, and none of it has run on a Surface Pro 11. Hardware: NOT RUN.
 *
 * What it does, in order: read CAP and refuse a controller the contract cannot use; quiesce (a normal shutdown if the
 * firmware left the controller enabled and ready, then CC.EN 0 and RDY 0); own admin queues in a reserved DRAM extent;
 * CC.EN 1 with IOSQES 6, IOCQES 4, MPS 4 KiB, NVM command set; Identify controller and namespace 1 (a 4096-byte or
 * metadata LBA format is refused, never converted); Number of Queues, Create I/O CQ 1, Create I/O SQ 1; polled reads of
 * absolute 512-byte LBAs; delete the queues, normal shutdown, CC.EN 0. Everything polls: no interrupt is taken or armed.
 *
 * READ ONLY. The only I/O opcode this unit can build is Read: it has no Write, Flush, Dataset Management, Format,
 * Sanitize or Firmware opcode, and the gates in front of both queues refuse every other value (nvmet_io_gate; the admin
 * gate is the SP11 takeover-phase allow-list of nvme-host.c, so the three allow-lists of the tree stay one). No write
 * stage exists (UNIMPLEMENTED). What keeps it so: cmd() is the one function that builds a command and it runs both
 * gates before it stores anything; the harness's controller model fails on any opcode outside the lists and counts
 * every register it may not write. tools/test-nvme-takeover.py also greps the code for write-class words, but that is
 * a tripwire a refactor can walk past, not a proof: every later diff of this unit needs a re-review. It is a property
 * of this unit only: a linked build still has nvme-host.c's Write and Flush, reachable after the hand-off.
 *
 * Reads are bounded by the namespace, not by a window: the GPT at LBA 0 and the backup GPT must be read to find the
 * window. A write stage, when one is designed, must keep every write [first, first + count) inside the window the
 * record names and outside both GPT zones, in the subtraction form that cannot wrap (count - 1 <= last - first), and
 * must refuse an empty or inverted window. It belongs after the hand-off, in nvme-host.c's write path, which already
 * confines writes to the record's window (nvme-host.c:86-95, nvmeh_sp11_check), not in a second copy here.
 *
 * What a run writes (the named authorisation a later supervised run needs): BAR0 CC (SHN, 0, 0x00460001), AQA, ASQ,
 * ACQ and the four doorbells of queues 0 and 1; DRAM inside the one extent; admin commands Set Features (Number of
 * Queues), Identify x2, Create I/O CQ, Create I/O SQ, Delete I/O SQ, Delete I/O CQ. It never writes PCI configuration
 * space, INTMS/INTMC, the SMMU or any other register, and never a medium.
 *
 * Two risks stay open on the SP11 (decision D3: the window is in the INTERNAL NVMe). D11: whether Windows boots after
 * our controller reset is UNKNOWN, and a reset of the controller of the disk that holds the operator's Windows and
 * BitLocker metadata is the thing the first run does. And every later write would land on that same disk.
 *
 * DMA. The controller addresses the extent as `bus_base`, the CPU as `cpu_base`; they are equal when the PCIe SMMU
 * bypasses or maps the extent identity. Whether the SMMU lets the controller reach the extent is UNKNOWN until the S1
 * survey runs, so nvmet_init refuses a config that does not carry NVMET_C_DMA_PROVEN, before any register is touched:
 * bypass and an identity stage 2 are the caller's proof; a translating stage 2 that maps the extent elsewhere needs
 * bus_base != cpu_base and NVMET_C_TRANSLATED together (a mistyped address must not become a translating SMMU; the A16
 * serving code needs identity, and nvmet_make_params refuses otherwise); abort and an unreadable SMMU never set the
 * flag. If the proof is wrong the first admin command does not complete (E_T_CMD) or Identify returns an untouched
 * page (E_ID_ZERO), and the controller is disabled again.
 *
 * Coherency: the unit treats the DMA as non-coherent (the IORT marks the root complexes cache coherent, but that is
 * INFERRED for the unit): ops->clean (DC CIVAC, then DSB) runs on every line it hands to the controller before a
 * doorbell and on every line it takes from it afterwards, and on the whole PRP page and data buffer before the first
 * command and on the data range before every read, so a dirty line left by a loader is never written back over the
 * data of a read.
 *
 * After a failure check BOTH flags. NVMET_F_DMA_LIVE: we wrote CC.EN 1 and could not show RDY 0 again, so the controller
 * may still fetch from and post into the extent. NVMET_F_FW_QUEUES_LIVE: CC.EN or RDY was set when we arrived (or the
 * registers read all ones) and RDY 0 has not been seen since, so the controller may still use the firmware's own queues
 * in boot-services memory (cc_before, asq_before and acq_before say where). It is set before any refusal of
 * nvmet_quiesce: E_CAP, E_MQES, E_FATAL_ENTRY and E_GONE write nothing and leave the firmware's controller running, so
 * "both clear" means it was neither enabled nor ready on arrival, or was shown disabled. While either flag is set the
 * caller must neither reuse the extent or that memory nor start a guest. nvmet_abort writes CC 0 and waits once more
 * (one more CAP.TO window): it helps a reset that was only slow. A controller that stays up needs a reset this unit does
 * not have (NSSR, or a PERST of the link), and the flags stay set. Call nvmet_abort once, and never nvmet_init a struct
 * that still has a flag set: init clears it.
 */
#ifndef SNAP_COMPAT_NVME_TAKEOVER_H
#define SNAP_COMPAT_NVME_TAKEOVER_H
#include "nvme-host.h"

#ifndef SNAP_NVME_TAKEOVER
#define SNAP_NVME_TAKEOVER 0
#endif

#if SNAP_NVME_TAKEOVER
#if !SNAP_PLATFORM_SP11
#error "SNAP_NVME_TAKEOVER needs SNAP_PLATFORM_SP11: the admin gate is nvmeh_sp11_admin_verdict"
#endif

/* The extent's layout is nvme-host.h's (ASQ, ACQ, IOSQ, IOCQ, PRP, DATA); the Identify page sits in the free page
 * after the I/O completion queue, below the PRP list (q1n1 tools/nvme_m3.py Layout.ident). */
#define NVMET_IDENT 0x4000u
#define NVMET_PAGE 4096u
#define NVMET_NSID 1u
#define NVMET_LBA_BYTES 512u
/* CC for an enabled controller: EN, IOSQES 6 (64 B), IOCQES 4 (16 B), MPS 0 (4 KiB), CSS 0 (NVM), AMS 0. It equals
 * the value the firmware left in CC (OBSERVED twice, identical: the X2 image's run of 2026-11-15 and S1 boot 22 of
 * 2026-11-18) and nvme_m3.py's (4 << 20 | 6 << 16 | 1). */
#define NVMET_CC_ENABLE 0x00460001u
#define NVMET_CC_SHN_MASK (3u << 14)    /* 01 normal, 10 abrupt, 11 reserved: written as one of the first two or not at all */
#define NVMET_CC_SHN_NORMAL (1u << 14)
#define NVMET_CMD_SECONDS 5u            /* nvme_m3.py _reap */
#define NVMET_SHUTDOWN_SECONDS 10u      /* nvme_m3.py takeover and shutdown waits */
#define NVMET_POLL_MAX (1u << 24)       /* a backstop for a counter that does not advance */

/* The one I/O opcode (NVM command set, Read) and the admin opcodes this unit builds. No other I/O opcode is named. */
#define NVMET_IO_READ 0x02u
#define NVMET_ADMIN_DELETE_SQ 0x00u
#define NVMET_ADMIN_CREATE_SQ 0x01u
#define NVMET_ADMIN_DELETE_CQ 0x04u
#define NVMET_ADMIN_CREATE_CQ 0x05u
#define NVMET_ADMIN_IDENTIFY 0x06u
#define NVMET_ADMIN_SET_FEATURES 0x09u
#define NVMET_FEAT_NUMBER_OF_QUEUES 0x07u

#define NVMET_C_DMA_PROVEN 1u           /* the caller proved the extent reachable for the controller's stream */
#define NVMET_C_TRANSLATED 2u           /* the controller's address of the extent differs from the CPU's, on purpose */
#define NVMET_C_ALL 3u

/* Codes. A band of its own, clear of the NVMe window (-80..-145), the vCPU and timer guards (-146..-148) and the
 * monitor-stage2 band (-257..-274); a linked build must reserve it in probes/run.py first (not linked today). */
enum {
    NVMET_E_CONFIG = -160,       /* a config word is out of contract */
    NVMET_E_GUEST = -161,        /* the extent overlaps guest memory */
    NVMET_E_DMA_UNPROVEN = -162, /* the caller did not prove the extent reachable: nothing was touched */
    NVMET_E_GONE = -163,         /* all ones from CAP or CSTS: no device behind the BAR */
    NVMET_E_CAP = -164,          /* CAP: no NVM command set, a page size above 4 KiB, or a doorbell stride above 4 */
    NVMET_E_MQES = -165,         /* CAP.MQES allows fewer entries than the queues */
    NVMET_E_FATAL_ENTRY = -166,  /* CSTS.CFS was already set when we arrived: nothing was written */
    NVMET_E_T_SHUTDOWN = -167,   /* CSTS.SHST never reached complete */
    NVMET_E_T_QUIESCE = -168,    /* CSTS.RDY never cleared after CC.EN 0 */
    NVMET_E_T_RDY = -169,        /* CSTS.RDY never set after CC.EN 1 */
    NVMET_E_CFS = -170,          /* CSTS.CFS during bring-up or while a command was in flight */
    NVMET_E_T_CMD = -171,        /* no valid completion within the command timeout */
    NVMET_E_CID = -172,          /* a completion for another command identifier */
    NVMET_E_DUP = -173,          /* a second valid completion behind the one consumed */
    NVMET_E_QID = -174,          /* a completion for another queue, or a head pointer outside the queue */
    NVMET_E_STATUS = -175,       /* the completion carried an error status */
    NVMET_E_ID_ZERO = -176,      /* Identify returned an all-zero page: the DMA did not land, or nothing is there */
    NVMET_E_ID_CTRL = -177,      /* Identify controller: no namespace, or queue entry sizes that exclude 64/16 */
    NVMET_E_ID_NS = -178,        /* Identify namespace: zero or impossible size, or a format index past NLBAF */
    NVMET_E_LBA_SIZE = -179,     /* the current LBA format is not 512 bytes */
    NVMET_E_METADATA = -180,     /* the current LBA format carries metadata */
    NVMET_E_EUI64 = -181,        /* another namespace than the pinned one */
    NVMET_E_OPCODE = -182,       /* a command the allow-lists do not name: nothing was submitted */
    NVMET_E_RANGE = -183,        /* a read outside the namespace, empty, or over the maximum transfer */
    NVMET_E_PRP = -184,          /* a PRP outside the extent's data buffer or PRP page */
    NVMET_E_STATE = -185,        /* a call in the wrong stage */
    NVMET_E_DEAD = -186,         /* an earlier failure, an abort, a stop or the hand-off closed the unit */
    NVMET_E_CLOCK = -187,        /* the counter did not advance: the poll cap ended a wait */
    NVMET_E_NOT_IDENTITY = -188  /* nvmet_make_params: bus_base is not cpu_base */
};

enum { NVMET_ST_NEW, NVMET_ST_QUIESCED, NVMET_ST_ENABLED, NVMET_ST_IDENTIFIED, NVMET_ST_READY, NVMET_ST_STOPPED,
       NVMET_ST_FAILED, NVMET_ST_HANDED_OFF };

#define NVMET_F_REGS_WRITTEN 1u         /* we wrote CC, AQA, ASQ or ACQ: the firmware's controller state is gone (D11) */
#define NVMET_F_DMA_LIVE 2u             /* the controller may fetch from or post into the extent */
#define NVMET_F_FW_QUEUES_LIVE 4u       /* CC.EN or RDY was set on arrival and RDY 0 has not been seen since: the firmware's queues may be in use */

/* Everything the caller supplies, validated by nvmet_init. All plain integers. */
struct nvmet_config {
    uint64_t bar;               /* BAR0 as a CPU address, 16 KiB aligned (nvmeh_adopt's rule) */
    uint64_t cpu_base;          /* the extent as the CPU addresses it: 1 MiB aligned, NVMEH_WINDOW_BYTES long */
    uint64_t bus_base;          /* the same memory as the controller addresses it; equal to cpu_base unless NVMET_C_TRANSLATED */
    uint64_t mem_low, mem_high; /* guest memory [low, high): the extent must not overlap it */
    uint64_t ticks_per_s;       /* the counter's frequency (CNTFRQ_EL0) */
    uint64_t max_transfer;      /* bytes per read, a cap: 4096 .. NVMEH_DATA_BYTES, whole LBAs */
    uint64_t eui64_pin;         /* the namespace identity: Identify bytes 120..127 as a big-endian integer; 0 = report only */
    uint64_t flags;             /* NVMET_C_* */
    uint64_t reserved;          /* zero */
};

struct nvmet_queue {
    uint64_t sq_off, cq_off;    /* offsets in the extent */
    uint32_t qid, entries, tail, head, phase, next_cid;
};

/* The state and the report in one: plain integers, so a later REPORT can print it word by word. */
struct nvmet {
    uint64_t stage, flags, last_error, last_status, last_opcode;
    uint64_t bar, cpu_base, bus_base, ticks_per_s, max_transfer, eui64_pin;
    uint64_t cap, vs, cc_before, csts_before, aqa_before, asq_before, acq_before, csts;
    uint64_t stride, rdy_ticks, cmd_ticks, shutdown_ticks;
    struct nvmet_queue adm, io;
    uint64_t vid, mdts, nn, sqes, cqes, oncs, vwc, hmpre, hmmin, ctrl_max_transfer, qalloc;
    uint64_t nsze, ncap, nuse, nlbaf, flbas, lba_shift, metadata, eui64, nguid_lo, nguid_hi;
    uint64_t commands, reads, read_bytes, errors, refused, polls;
};

/* Validate the config and start a state. No hardware access. 0, or NVMET_E_CONFIG / GUEST / DMA_UNPROVEN; the checks
 * run in that order, so a missing proof is reported only for a config that is otherwise sound. NVMET_C_TRANSLATED must
 * be set exactly when bus_base differs from cpu_base: either mismatch is E_CONFIG. It clears the whole struct, flags
 * included: call it on a fresh struct, or after nvmet_abort returned 0, never on one that still has a flag set. */
int nvmet_init(struct nvmet *t, const struct nvmet_config *c);
/* The arrival state (CAP, VS, CC, CSTS, AQA, ASQ, ACQ) is read and NVMET_F_FW_QUEUES_LIVE set if the controller was
 * enabled or ready, before any refusal; then the quiesce. Writes CC only. */
int nvmet_quiesce(const struct nvmeh_ops *o, struct nvmet *t);
/* Admin queues, CC.EN 1. Writes AQA, ASQ, ACQ, CC and the extent. After a failure the controller is disabled again. */
int nvmet_enable(const struct nvmeh_ops *o, struct nvmet *t);
/* Identify controller and namespace 1; the maximum transfer is the smallest of the config, MDTS and the data buffer. */
int nvmet_identify(const struct nvmeh_ops *o, struct nvmet *t);
/* Number of Queues, Create I/O CQ 1, Create I/O SQ 1. */
int nvmet_ioq(const struct nvmeh_ops *o, struct nvmet *t);
/* The four stages above in order. */
int nvmet_takeover(const struct nvmeh_ops *o, struct nvmet *t, const struct nvmet_config *c);
/* Read `count` real LBAs from `lba` (anywhere in the namespace) into the data buffer, cpu_base + NVMEH_DATA. A refusal
 * (E_RANGE) sends nothing; an error status (E_STATUS) leaves the queue usable; every other failure disables the
 * controller and closes it. */
int nvmet_read(const struct nvmeh_ops *o, struct nvmet *t, uint64_t lba, uint32_t count);
/* Delete the queues, normal shutdown, CC.EN 0. From a failed state it returns E_DEAD: use nvmet_abort there. */
int nvmet_stop(const struct nvmeh_ops *o, struct nvmet *t);
/* UNWIRED: nothing calls this, and the hand-off to nvmeh_adopt is not designed yet (which window, whose stop, what the
 * serving code does with a controller it refuses). It is host-tested as a contract only; do not wire it without a
 * review of that design. The READY state as the params nvmeh_adopt takes (the A16 serving code): identity DMA only. The
 * window is the caller's. 0, E_STATE, E_NOT_IDENTITY or E_RANGE. On 0 the unit is closed (HANDED_OFF): the serving code
 * owns the queues and their tail, head and phase from here, so read, stop and every other call return E_DEAD and touch
 * nothing; the flags keep describing the controller (DMA_LIVE stays set until the serving code's own stop). Only
 * nvmet_abort stays open (see there): an adoption the serving code refuses leaves the controller enabled. */
int nvmet_make_params(struct nvmet *t, uint64_t first_lba, uint64_t last_lba, uint64_t timeout_ticks,
                      struct nvmeh_params *p);
/* After a failure, a refused adoption or a refusal that wrote nothing: show the controller disabled if it can still be
 * shown. While NVMET_F_DMA_LIVE or NVMET_F_FW_QUEUES_LIVE is set it writes CC 0 and waits for RDY 0 again (one CAP.TO
 * window) and clears both flags when it is seen; with neither set it does nothing. It is also allowed after the
 * hand-off, since CC 0 uses no queue state; there it resets a controller the serving code may already use, so the
 * caller owns that decision. 0 when neither flag is set afterwards, else E_T_QUIESCE, E_GONE or E_CLOCK. */
int nvmet_abort(const struct nvmeh_ops *o, struct nvmet *t);
/* The gate in front of every I/O command: 0 for Read, NVMET_E_OPCODE for every other value. */
int nvmet_io_gate(unsigned opcode);
/* The PRPs of a read of `bytes` at `buf_off` in the data buffer: pure. prp2 is 0, the second page, or the PRP page. 0
 * or NVMET_E_PRP when the transfer leaves the data buffer, is empty, is not page aligned or wraps. */
int nvmet_prp(const struct nvmet *t, uint64_t buf_off, uint64_t bytes, uint64_t *prp1, uint64_t *prp2);
/* Fixed text of a code, for the diagnostic log; never NULL. */
const char *nvmet_strerror(int code);

#ifdef NVMET_TEST_HOOKS
/* Test builds only: one command with an arbitrary opcode through the gates and the submit path, so the harness can show
 * that every other opcode is refused before a doorbell. Not part of any other build. */
int nvmet_test_command(const struct nvmeh_ops *o, struct nvmet *t, unsigned qid, unsigned opcode, uint64_t prp1,
                       uint64_t prp2, uint32_t cdw10);
#endif
#endif
#endif
