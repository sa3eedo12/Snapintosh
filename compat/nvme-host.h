/* SPDX-License-Identifier: MIT */
/* The A16's NVMe SSD behind the virtual ANS2 (full-install M6, docs/FULL-INSTALL-DESIGN.md §4).
 *
 * q1n1's tools/nvme_m3.py --adopt-dma takes the controller over from the host, creates its admin
 * queue and I/O queue 1 in a 1 MiB DMA window inside the loader's ANS reservation, checks the
 * Snapintosh partition and its window header, and leaves the controller running. The monitor
 * adopts that state: it re-proves the controller registers against the record, then reads the
 * window through I/O queue 1 on the guest's behalf.
 *
 * Read defaults to the window [first_lba, last_lba] (real 512-byte LBAs). Explicit
 * NVMEH_F_WRITE also admits FUA Write inside [write_first_lba, write_last_lba].
 * Namespace Flush is a real command in either mode. Completions have a bounded wait; the
 * controller raises no interrupt (Create I/O CQ without IEN).
 *
 * Coherency: the SSD's DMA goes through the PCIe SMMU's identity stage 2, not through the CPU
 * caches. Submission entries and PRP lists are cleaned to the point of coherency before the
 * doorbell; completion entries and read data are cleaned and invalidated before they are read.
 * The private payload is cleaned before every command and invalidated after reads;
 * the CPU never dirties a completion line after adoption. */
#ifndef SNAP_COMPAT_NVME_HOST_H
#define SNAP_COMPAT_NVME_HOST_H
#include <stdint.h>

/* The DMA window layout (q1n1 tools/nvme_m3.py ADOPT_LAYOUT, offsets from dma_base). */
#define NVMEH_WINDOW_BYTES 0x100000u
#define NVMEH_ASQ   0x0000u
#define NVMEH_ACQ   0x1000u
#define NVMEH_IOSQ  0x2000u
#define NVMEH_IOCQ  0x3000u
#define NVMEH_PRP   0x8000u        /* one 4 KiB PRP list page */
#define NVMEH_DATA  0x10000u       /* private read/write data */
#define NVMEH_DATA_BYTES 0x20000u  /* 128 KiB: one command, the virtual ANS2's MDTS */
#define NVMEH_ADMIN_ENTRIES 32u
#define NVMEH_IO_ENTRIES 64u

/* Controller registers (NVMe 2.0 base 3.1.4). */
#define NVMEH_CAP 0x00u
#define NVMEH_CC 0x14u
#define NVMEH_CSTS 0x1cu
#define NVMEH_AQA 0x24u
#define NVMEH_ASQ_REG 0x28u
#define NVMEH_ACQ_REG 0x30u

#define NVMEH_F_ENABLE 1u
#define NVMEH_F_WRITE 2u
#define NVMEH_F_ALL 3u

/* probes/nvmeh.py builds this word by word (NVMEH_PARAMS_WORDS) from q1n1's adopt record. */
struct nvmeh_params {
    uint64_t flags;
    uint64_t bar, stride;                 /* BAR0; CAP.DSTRD */
    uint64_t dma_base;                    /* NVMEH_WINDOW_BYTES, 1 MiB aligned */
    uint64_t io_tail, io_head, io_phase, io_next_cid;   /* I/O queue 1 as the tool left it */
    uint64_t lba_bytes;                   /* 512 */
    uint64_t first_lba, last_lba;         /* the window, real LBAs, inclusive */
    uint64_t max_transfer;                /* bytes per data command (MDTS) */
    uint64_t timeout_ticks;               /* per command, in CNTPCT ticks */
    uint64_t write_first_lba, write_last_lba; /* narrower real LBAs; zero when read-only */
    uint64_t reserved;
};
#define NVMEH_PARAMS_WORDS 16

/* context.nvme: decoded word by word by the host (probes/nvmeh.py STATE_FIELDS). */
struct nvmeh_state {
    uint64_t status;                      /* NVMEH_S_* */
    uint64_t prepare;                     /* 0 or the adopt refusal */
    uint64_t tail, head, phase, next_cid;
    uint64_t reads, read_bytes, errors, timeouts, refused;
    uint64_t last_status;                 /* status << 16 | cid of the last failed completion */
    uint64_t last_lba, max_ticks, total_ticks;
    uint64_t csts;                        /* CSTS at the last failure or at stop */
    uint64_t writes, write_bytes, flushes, last_opcode;
};
#define NVMEH_STATE_WORDS 20

#define NVMEH_S_ADOPTED 1u
#define NVMEH_S_FAILED 2u     /* a timeout or fatal status: no further commands */

enum {
    NVMEH_E_PARAMS = -80,     /* a params word is out of contract */
    NVMEH_E_GUEST = -81,      /* the DMA window overlaps guest memory */
    NVMEH_E_CTRL = -82,       /* CC/CSTS not enabled-and-ready, or fatal */
    NVMEH_E_QUEUES = -83,     /* AQA/ASQ/ACQ are not the adopted admin queue */
    NVMEH_E_RANGE = -84,      /* outside the relevant window or larger than one command */
    NVMEH_E_TIMEOUT = -85,    /* no completion within timeout_ticks */
    NVMEH_E_STATUS = -86,     /* the completion carried an error status */
    NVMEH_E_DEAD = -87,       /* an earlier timeout or fatal status stopped the queue */
    NVMEH_E_READONLY = -88    /* write flag absent; nothing submitted */
};

#ifndef NVMEH_DIRECT
#if defined(__aarch64__) && defined(__ELF__) && !defined(__APPLE__) && !defined(__linux__)
#define NVMEH_DIRECT 1
#else
#define NVMEH_DIRECT 0
#endif
#endif
struct nvmeh_ops {
    void *ctx;
    uint32_t (*rd)(void *ctx, uint64_t pa);               /* 32-bit, register or DRAM word */
    void (*wr)(void *ctx, uint64_t pa, uint32_t value);
    void (*clean)(void *ctx, uint64_t pa, uint64_t size); /* DC CIVAC to PoC, then DSB */
    uint64_t (*now)(void *ctx);                           /* CNTPCT */
};

/* Check params against guest memory [mem_low, mem_high) and re-prove the adopted controller from
 * its registers (read-only). 0 or NVMEH_E_*. */
int nvmeh_adopt(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
                uint64_t mem_low, uint64_t mem_high);

/* Read `count` real LBAs starting `wlba` LBAs into the window into dma_base + NVMEH_DATA + at.
 * `at` is 4 KiB aligned and at + count * lba_bytes <= NVMEH_DATA_BYTES. 0 or NVMEH_E_*. */
int nvmeh_read(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
               uint64_t wlba, uint32_t count, uint32_t at);

/* Same buffer/range units as Read. Caller fills the entire private payload before
 * this call. Every Write uses FUA, even if the guest did not request it. */
int nvmeh_write(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
                uint64_t wlba, uint32_t count, uint32_t at);
int nvmeh_flush(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s);

/* Record CSTS for the stop report. */
void nvmeh_stop(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s);

#ifndef SNAP_ANS_ASYNC
#define SNAP_ANS_ASYNC 0
#endif
#if SNAP_ANS_ASYNC
/* SNAP_ANS_ASYNC (opt-in; agents/nvme-async/FAILURE-MODES.md N5, N6): the synchronous command split at its SQ
 * doorbell. nvmeh_submit is the command up to and including the SQ 1 tail doorbell; nvmeh_reap is one pass of its
 * completion wait and, once the phase matches or timeout_ticks (from the submit) has passed, the rest of the
 * command unchanged: the same counters, the same NVMEH_S_FAILED rules, the same returns. One command at a time
 * (the 128 KiB data buffer and the PRP page are single); the flight is the caller's. tools/test-nvme-host.c runs
 * both against the synchronous command on the same fake controller. */
#define NVMEH_PENDING 1           /* nvmeh_reap: no completion yet, timeout not reached */
#define NVMEH_E_BUSY (-89)        /* nvmeh_submit: a command is already in flight; nothing submitted */
struct nvmeh_flight {
    uint64_t active, op, lba, bytes, data, cid, start;
};
int nvmeh_submit(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
                 struct nvmeh_flight *f, unsigned op, uint64_t wlba, uint32_t count, uint32_t at);
int nvmeh_reap(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
               struct nvmeh_flight *f);
/* Read-only peek, safe without exclusion: 1 when the completion entry at the current head carries the expected
 * phase (a stale answer only moves one poll). */
int nvmeh_ready(const struct nvmeh_ops *o, const struct nvmeh_params *p, const struct nvmeh_state *s);
#endif

#if SNAP_PLATFORM_SP11
#include "platform.h"
#include "board-info.h"
/* ---- SP11 takeover contract (Surface Pro 11, X1E80100) -------------------------------------------------------
 * Status: EXPERIMENTAL. Host-tested only (tools/test-nvme-window-hamoa.py); never run on a Surface Pro. Everything in
 * this block is compiled only under -DSNAP_PLATFORM_SP11=1, so the A16 monitor is unchanged. docs/SP11-NVME-WINDOW.md
 * has the audit of the A16 assumptions, the descriptor fields this contract still needs, and the per-machine hardware
 * questions it cannot answer.
 *
 * The physical takeover (reset, admin queue, Identify, I/O queue 1) stays q1n1's, as on the A16. What changes for the
 * SP11 is what the monitor refuses to trust. The A16 path believes the adoption record's BAR and window and re-proves
 * only the controller registers. The SP11 path first compares the record with the board descriptor and the prepared run,
 * and re-reads the endpoint's PCI configuration header from the descriptor's ECAM window, so a record for another
 * device, another disk, another run or another window geometry is refused before any command is built.
 *
 * Nothing here may be fabricated: a group the descriptor leaves unknown has its `known` bit clear and is refused with a
 * code of its own (NVMEH_E_SP11_UNKNOWN_*). Windows and their guards are in 512-byte LBAs because the window code
 * (ans-window.c) converts with ANS_BLOCK / 512; a namespace formatted otherwise is refused, never converted, and the LBA
 * format is never switched (that would need Format NVM, which nvmeh_sp11_admin_verdict refuses). */
#define NVMEH_SP11_ABI 1u
#define NVMEH_SP11_LBA_BYTES 512u
#define NVMEH_SP11_MIB_LBAS 2048u               /* 1 MiB of 512-byte LBAs */
#define NVMEH_SP11_ALIGN_LBAS NVMEH_SP11_MIB_LBAS /* window start and size, as the A16 plan aligns its partition */
#define NVMEH_SP11_GUARD_LBAS NVMEH_SP11_MIB_LBAS /* free gap to every other extent and to the GPT zones */
/* The virtual ANS2 serves the first and last MiB of the window from memory (compat/ans.h ANS_WINDOW_EDGE_BLOCKS =
 * 256 blocks of 4 KiB) and the window header lives in the first MiB, so a write window never reaches either. */
#define NVMEH_SP11_EDGE_LBAS NVMEH_SP11_MIB_LBAS
#define NVMEH_SP11_MIN_WINDOW_LBAS 4096u        /* two edges: below this no data block would remain */
/* Protective MBR, GPT header and the 16,384-byte minimum entry array in 512-byte LBAs (UEFI GPT format). The backup copy
 * is the same size, so usable LBAs lie in [34, nsze - 34] at the least. */
#define NVMEH_SP11_GPT_LBAS 34u
#define NVMEH_SP11_MAX_PROTECTED 32u
#define NVMEH_SP11_CLASS_NVME 0x010802u         /* PCI class code: mass storage, NVM, NVM Express interface */
#define NVMEH_SP11_IPA_LIMIT (1ull << 36)       /* the monitor's stage-2 geometry, as BOARD_INFO_IPA_BITS */

/* struct nvmeh_sp11_target.known: which groups are measured. */
#define NVMEH_SP11_K_ECAM 1u                    /* segment, ecam_base, start_bus, end_bus */
#define NVMEH_SP11_K_BDF 2u
#define NVMEH_SP11_K_SMMU 4u                    /* smmu_base */
#define NVMEH_SP11_K_STREAM 8u                  /* stream_base, rid_base, rid_count */
#define NVMEH_SP11_K_EUI64 16u
#define NVMEH_SP11_K_GENERATION 32u
#define NVMEH_SP11_K_ALL 63u

/* struct nvmeh_sp11_record.flags */
#define NVMEH_SP11_R_CLEAN 1u                   /* q1n1 left the controller running after a clean hand-off */
#define NVMEH_SP11_R_DMA_PROVEN 2u              /* q1n1 proved every DMA window page reachable read/write for the SSD */
#define NVMEH_SP11_R_WRITE_AUTH 4u              /* the operator authorised NVMe writes for this run (S4, per run) */
#define NVMEH_SP11_R_ALL 7u

/* struct nvmeh_sp11_record.smmu_mode: the only two states q1n1 accepts (tools/nvme_m3.py, Smmu.mode). Anything else is
 * refused: abort, stage 1, unconfigured and invalid streams would fault or mistranslate the SSD's DMA. */
#define NVMEH_SP11_SMMU_BYPASS 1u
#define NVMEH_SP11_SMMU_S2_IDENTITY 2u          /* stage 2 only, identity map proven over the DMA window */

#define NVMEH_SP11_PHASE_TAKEOVER 0u            /* host-side sequence before the hand-off (tools/nvme_m3.py) */
#define NVMEH_SP11_PHASE_ADOPTED 1u             /* the monitor after nvmeh_adopt() */

/* Distinct codes, so a refusal is diagnosable from nvmeh_state.prepare alone. The order of checks in
 * nvmeh_sp11_check() is part of the contract: tools/test-nvme-window-hamoa.py holds the C code and the host model to
 * it, case by case and on random corruption. */
enum {
    NVMEH_E_SP11_ABI = -96,            /* a block carries another ABI revision */
    NVMEH_E_SP11_RESERVED = -97,       /* a reserved word, an unknown `known` bit or an unknown flag is set */
    NVMEH_E_SP11_UNKNOWN_ECAM = -98,   /* the descriptor has no measured ECAM window for the SSD's segment */
    NVMEH_E_SP11_UNKNOWN_BDF = -99,    /* the endpoint's bus/device/function is not pinned */
    NVMEH_E_SP11_UNKNOWN_SMMU = -100,  /* the PCIe SMMU is unknown or ambiguous */
    NVMEH_E_SP11_UNKNOWN_STREAM = -101, /* the RID -> StreamID mapping is not pinned */
    NVMEH_E_SP11_UNKNOWN_EUI64 = -102, /* the namespace identity is not pinned */
    NVMEH_E_SP11_UNKNOWN_GENERATION = -103, /* the run identity is not pinned */
    NVMEH_E_SP11_UNKNOWN_DEVICE = -104, /* the descriptor does not say the NVMe device is present */
    NVMEH_E_SP11_TARGET_INVALID = -105, /* a pinned value is zero, unaligned or outside the monitor's 36-bit map */
    NVMEH_E_SP11_RECORD_UNCLEAN = -106, /* the hand-off was not clean */
    NVMEH_E_SP11_STALE = -107,         /* the record belongs to another run */
    NVMEH_E_SP11_SEGMENT = -108,       /* another PCI segment than the descriptor's */
    NVMEH_E_SP11_ECAM = -109,          /* another ECAM base than the descriptor's */
    NVMEH_E_SP11_BUS_RANGE = -110,     /* the endpoint's bus is outside the descriptor's MCFG range */
    NVMEH_E_SP11_BDF = -111,           /* another bus/device/function than the descriptor pins */
    NVMEH_E_SP11_CFG_ADDRESS = -112,   /* the config address q1n1 read is not ecam_base + the BDF's offset */
    NVMEH_E_SP11_CLASS = -113,         /* not an NVM Express controller */
    NVMEH_E_SP11_EUI64 = -114,         /* another namespace than the pinned one */
    NVMEH_E_SP11_SMMU_BASE = -115,     /* another SMMU than the descriptor's */
    NVMEH_E_SP11_SMMU_MODE = -116,     /* the SSD's stream is not bypass or an identity stage 2 */
    NVMEH_E_SP11_DMA_UNPROVEN = -117,  /* q1n1 did not prove the DMA window reachable */
    NVMEH_E_SP11_STREAM = -118,        /* the StreamID is not the descriptor's mapping of the endpoint's RID */
    NVMEH_E_SP11_LBA_FORMAT = -119,    /* the current LBA format is not 512 bytes */
    NVMEH_E_SP11_METADATA = -120,      /* the current LBA format carries per-block metadata */
    NVMEH_E_SP11_PARAMS = -121,        /* the params disagree with the record about the LBA size */
    NVMEH_E_SP11_GEOMETRY = -122,      /* namespace size or GPT usable range is impossible */
    NVMEH_E_SP11_PROTECTED = -123,     /* the protected-extent list is empty, malformed or too long */
    NVMEH_E_SP11_WINDOW_ORDER = -124,  /* zero size, wrapped size, or first after last */
    NVMEH_E_SP11_WINDOW_SIZE = -125,   /* smaller than NVMEH_SP11_MIN_WINDOW_LBAS */
    NVMEH_E_SP11_WINDOW_RANGE = -126,  /* reaches past the end of the namespace */
    NVMEH_E_SP11_WINDOW_GPT = -127,    /* reaches into the primary or backup GPT zone */
    NVMEH_E_SP11_WINDOW_ALIGN = -128,  /* start or size not a whole number of MiB */
    NVMEH_E_SP11_WINDOW_GUARD = -129,  /* closer than the guard to another extent or to a GPT zone */
    NVMEH_E_SP11_OVERLAP = -130,       /* shares an LBA with another partition */
    NVMEH_E_SP11_WRITE_WINDOW = -131,  /* write words wrong for the flag, or reaching an edge MiB */
    NVMEH_E_SP11_WRITE_UNAUTHORISED = -132, /* NVMEH_F_WRITE without the per-run authorisation */
    NVMEH_E_SP11_NO_DEVICE = -133,     /* config space reads as no device */
    NVMEH_E_SP11_DEVICE_DISABLED = -134, /* Memory Space or Bus Master is off (this code never changes them) */
    NVMEH_E_SP11_HEADER_TYPE = -135,   /* not a type 0 (endpoint) header */
    NVMEH_E_SP11_BAR = -136,           /* BAR0 is not the 64/32-bit memory BAR the params name */
    NVMEH_E_SP11_ADMIN_PHASE = -137,
    NVMEH_E_SP11_ADMIN_OPCODE = -138,  /* not on the allow-list */
    NVMEH_E_SP11_ADMIN_CNS = -139,     /* Identify with another CNS than namespace or controller */
    NVMEH_E_SP11_ADMIN_LOG = -140,     /* Get Log Page for a log that is not error, SMART or firmware slot */
    NVMEH_E_SP11_ADMIN_FEATURE = -141, /* a feature outside the allow-list for that command */
    NVMEH_E_SP11_ADMIN_SAVE = -142,    /* Set Features with the Save bit */
    NVMEH_E_SP11_IO_OPCODE = -143,
    NVMEH_E_SP11_IO_READONLY = -144    /* Write while the window is read-only */
};

/* The prepare status of an SP11 launch that asks for the SSD window while nothing yet carries the target, the record and
 * the per-run pins to the monitor (UNIMPLEMENTED). ans_window_prepare (probes/xnuboot/ans_glue.c) returns it, and stores it
 * in nvmeh_state.prepare, instead of calling nvmeh_adopt(): that re-proves the controller registers only, so it would adopt
 * a disk the descriptor never named. The next number after the enumerators above; probes/run.py reserves -96 .. -145 for this
 * block and names each one (tools/test-sp11-integration.py, StatusBands). Not an NVMEH_E_SP11_* enumerator because
 * nvmeh_sp11_check() never returns it. */
#define NVMEH_SP11_STATUS_UNWIRED (-145)

struct nvmeh_sp11_extent { uint64_t first_lba, last_lba; };   /* inclusive, real 512-byte LBAs */

/* What the board descriptor and the prepared run pin. Built from a validated board-info blob by
 * nvmeh_sp11_target_from_board() plus the per-run pins; every group is all-or-nothing under its `known` bit. All
 * words are plain integers (no pointers) so the block can be carried by the same host-to-monitor channel as the
 * params. Ownership: read-only input; the caller keeps it alive for the call. */
struct nvmeh_sp11_target {
    uint64_t abi;                       /* NVMEH_SP11_ABI */
    uint64_t known;                     /* NVMEH_SP11_K_* */
    uint64_t segment;                   /* PCI segment of the root complex that hosts the SSD */
    uint64_t ecam_base;                 /* MCFG allocation base for that segment, 1 MiB aligned */
    uint64_t start_bus, end_bus;        /* the allocation's bus range */
    uint64_t bdf;                       /* the endpoint's RID: bus << 8 | device << 3 | function */
    uint64_t smmu_base;                 /* PCIe SMMUv3 register base, 64 KiB aligned */
    uint64_t stream_base;               /* StreamID of RID rid_base (an iommu-map entry's output base) */
    uint64_t rid_base, rid_count;       /* the RID window that map entry covers */
    uint64_t eui64;                     /* namespace identity pinned from the pre-ExitBootServices survey */
    uint64_t generation;                /* the prepared run's generation; a record from another run is stale */
    uint64_t reserved[3];
};
_Static_assert(sizeof(struct nvmeh_sp11_target) == 16 * 8, "SP11 target block");

/* What q1n1's takeover recorded (tools/nvme_m3.py: record['pci'], ['smmu'], ['adopt'], the decoded GPT). The host
 * packs it; the monitor treats it as untrusted input. */
struct nvmeh_sp11_record {
    uint64_t abi;
    uint64_t flags;                     /* NVMEH_SP11_R_* */
    uint64_t generation;
    uint64_t segment, bdf, ecam_base;   /* where q1n1 found the endpoint */
    uint64_t cfg_pa;                    /* the config-space address q1n1 read it at */
    uint64_t class_code;                /* 24-bit PCI class code of the endpoint */
    uint64_t smmu_base, smmu_mode, stream_id;
    uint64_t eui64;
    uint64_t lba_bytes, metadata_bytes; /* the namespace's current LBA format as Identify reported it */
    uint64_t nsze;                      /* Identify Namespace NSZE: LBAs in the namespace */
    uint64_t first_usable, last_usable; /* the GPT header's usable range, real LBAs */
    uint64_t protected_count;
    struct nvmeh_sp11_extent protected_extents[NVMEH_SP11_MAX_PROTECTED];   /* every other partition */
};
_Static_assert(sizeof(struct nvmeh_sp11_record) == (18 + 2 * NVMEH_SP11_MAX_PROTECTED) * 8, "SP11 record block");

/* Pure checks of the target, the record and the params against each other and against the geometry; reads nothing
 * from hardware. 0, or the first NVMEH_E_SP11_* in this order: ABI, reserved, unknown groups (ECAM, BDF, SMMU, stream,
 * EUI64, generation), invalid pins, unclean record, stale record, identity (segment, ECAM base, bus range, BDF, config
 * address, class, EUI64, SMMU base, SMMU mode, DMA proof, stream), LBA format, metadata, params LBA size, geometry,
 * protected list, window (order, size, range, GPT zone, overlap, alignment, guard to the usable edges, guard to the
 * other extents), then the write policy (words without the flag, authorisation, order, edge MiB). */
int nvmeh_sp11_check(const struct nvmeh_sp11_target *t, const struct nvmeh_sp11_record *r, const struct nvmeh_params *p);

/* Re-read the endpoint's PCI configuration header at t->ecam_base + the BDF's ECAM offset (32-bit reads through
 * o->rd) and require an NVM Express endpoint with Memory Space and Bus Master on, whose BAR0 is p->bar. Never writes
 * config space. 0 or NVMEH_E_SP11_NO_DEVICE / CLASS / DEVICE_DISABLED / HEADER_TYPE / BAR. Call after
 * nvmeh_sp11_check(), which validated the pins this reads. */
int nvmeh_sp11_prove_endpoint(const struct nvmeh_ops *o, const struct nvmeh_sp11_target *t, const struct nvmeh_params *p);

/* The SP11 adoption: nvmeh_sp11_check, nvmeh_sp11_prove_endpoint, then the A16 nvmeh_adopt (controller registers
 * re-proved against the record). A refusal is also stored as s->prepare. Do not call nvmeh_adopt() directly on an SP11
 * build: it knows nothing of the descriptor. Nothing calls this yet: until the monitor receives a target and a record,
 * ans_window_prepare refuses with NVMEH_SP11_STATUS_UNWIRED. */
int nvmeh_sp11_adopt(const struct nvmeh_ops *o, const struct nvmeh_sp11_target *t, const struct nvmeh_sp11_record *r,
                     const struct nvmeh_params *p, struct nvmeh_state *s, uint64_t mem_low, uint64_t mem_high);

/* Fill the descriptor-derived groups (ECAM for `segment`, the one SMMUv3) of *t from a blob that board_info_validate()
 * accepted, and require the NVMe device to be present. The blob has no field naming the SSD's segment, BDF, stream
 * mapping or namespace, so `segment` is the caller's claim and the other groups stay unknown until the caller pins them
 * (docs/SP11-NVME-WINDOW.md, "Proposed descriptor additions"). 0, or NVMEH_E_SP11_UNKNOWN_DEVICE / ECAM / SMMU; on
 * an error *t is all zero. */
int nvmeh_sp11_target_from_board(const struct board_info *b, uint64_t segment, struct nvmeh_sp11_target *t);

/* The gate for every admin command the physical controller may be sent. The monitor issues none today: the admin queue
 * is q1n1's, and tools/nvme_m3.py carries the same lists (ADMIN_ALLOWED, SET_FEATURES_ALLOWED; the host test compares
 * them). TAKEOVER allows queue create/delete, Identify (CNS 0 and 1), Get Log Page (error, SMART, firmware slot), Get
 * Features (the architectural features 0x01..0x14, 0x16, 0x17) and Set Features of Number of Queues without Save.
 * ADOPTED allows only Identify, Get Log Page and Get Features. Everything else, including Format NVM, Sanitize,
 * Firmware Commit, Namespace Management and every unknown opcode, is refused. 0 or NVMEH_E_SP11_ADMIN_*. */
int nvmeh_sp11_admin_verdict(unsigned phase, unsigned opcode, uint32_t cdw10);

/* The same for I/O commands: Read and Flush always, Write only when `writable`. 0, NVMEH_E_SP11_IO_*. */
int nvmeh_sp11_io_verdict(unsigned opcode, unsigned writable);

/* Fixed description of an NVMEH_E_SP11_* code, or of NVMEH_SP11_STATUS_UNWIRED, for the diagnostic log; never NULL. */
const char *nvmeh_sp11_strerror(int code);
#endif
#endif
