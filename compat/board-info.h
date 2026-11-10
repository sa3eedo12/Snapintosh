/* SPDX-License-Identifier: MIT */
#ifndef SNAP_COMPAT_BOARD_INFO_H
#define SNAP_COMPAT_BOARD_INFO_H
/* Board-info blob, ABI 1.x: the compact binary form of a board descriptor (tools/board_descriptor.py,
 * the JSON files under config/boards), written by probes/boardinfo.py and checked here before the monitor trusts
 * one field of it.
 *
 * Why a blob at all: the monitor is freestanding C and must not parse JSON, and a board's SoC, GIC and physical map
 * must reach it from outside the monitor image so that one image can serve several boards. Today the Mac host places
 * the blob in q1n1 heap memory over the proxy (tools/q1n1_boardinfo.py in the q1n1 scratch tree). That carrier is
 * transitional: the standalone design moves the same bytes into the monitor image or a UEFI stub, and nothing here
 * depends on who delivers them.
 *
 * Ownership and lifetime. The blob is input from outside the monitor's trust boundary. The monitor copies it into
 * memory only it can write, then validates that copy; board_info_validate() reads every byte it uses once, but a host
 * that can still write the blob can change it between the check and the use. The validated copy is read-only data
 * for the rest of the run. Nothing in it is a pointer.
 *
 * Wire format. Little-endian, 8-byte aligned, one fixed-size layout per ABI minor (1.0 is BOARD_INFO_V1_SIZE bytes
 * with every array at its maximum), explicit padding, and no field that depends on another section's size. A later
 * minor may only append bytes after the 1.0 layout, so a 1.0 reader always finds its own fields at the same offsets.
 * crc32 is the IEEE 802.3 CRC-32 (reflected, polynomial 0xedb88320, initial value and final xor 0xffffffff, the
 * zlib/PNG variant) of the first total_length bytes with the crc32 field read as zero; it catches transport
 * damage, not tampering.
 *
 * Version policy.
 *  - abi_major differs from BOARD_INFO_ABI_MAJOR: refused. A major bump is allowed to change anything.
 *  - abi_minor is newer than ours: the 1.0 prefix is still parsed, and trailing bytes are ignored, but flags_required
 *    may only carry bits this reader knows (none in 1.0). An unknown required bit means "you must understand this to
 *    use the blob safely", so it is refused. Unknown flags_optional bits are ignored from a newer minor and refused
 *    at the same minor, where nothing may define them.
 *  - A newer minor must keep every 1.0 field's meaning; a change that would make a 1.0 reader misinterpret the blob
 *    sets a required flag or bumps the major.
 *
 * Unknown values. The descriptor says "unknown" as an explicit null; the blob says it with a cleared bit in `known`
 * and an all-zero group. A group that is not marked known must be zero, so a stale value cannot hide behind a cleared
 * bit, and a consumer that forgets to look at `known` reads zeros rather than a guess. board_info_unready() names
 * every group a boot still depends on that is unknown; a board is usable only when it returns zero.
 *
 * What the blob does not carry: provenance (source and evidence labels stay in the JSON descriptor). `status` is the
 * descriptor's board-level evidence label; UNIMPLEMENTED marks a template that board_info_unready() always reports.
 *
 * Evidence: layout and rules are EXPERIMENTAL (host-tested only, tools/test-board-info.py; never run in the monitor
 * or on hardware).
 */
#include <stddef.h>
#include <stdint.h>

#if !defined(__BYTE_ORDER__) || __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "the board-info wire format is little-endian and read in place"
#endif

#define BOARD_INFO_MAGIC "SNAPBRDI"
#define BOARD_INFO_MAGIC_LEN 8u
#define BOARD_INFO_ABI_MAJOR 1u
#define BOARD_INFO_ABI_MINOR 0u
/* Upper bound on total_length for any minor; a consumer sizes its snapshot buffer with it. */
#define BOARD_INFO_MAX_LENGTH 4096u

/* Flag bits this reader understands. 1.0 defines none. */
#define BOARD_INFO_FLAGS_REQUIRED_KNOWN 0u
#define BOARD_INFO_FLAGS_OPTIONAL_KNOWN 0u

#define BOARD_INFO_NAME_LEN 16u         /* [a-z0-9][a-z0-9-]{0,14}, NUL padded; same rule as the descriptor */
#define BOARD_INFO_MAX_MIDR 4u
#define BOARD_INFO_MAX_CPUS 32u
#define BOARD_INFO_MAX_ECAM 4u
#define BOARD_INFO_MAX_SMMU 8u
#define BOARD_INFO_MAX_RANGES 32u

/* The monitor's stage-2 geometry (probes/xnuboot/stage2.c, S2_TXSZ = 28, VERIFIED). A window is the half-open range
 * [base, base + size); it must start below 1 << 36 and end at or below it, so its last byte may be (1 << 36) - 1.
 * That is how shadow/stage2.c checks a range (hi > limit is refused). */
#define BOARD_INFO_IPA_BITS 36u
#define BOARD_INFO_PAGE 0x1000ull

/* Index = evidence label order of the descriptor (tools/board_descriptor.py EVIDENCE_LABELS). */
enum board_info_status {
    BOARD_INFO_STATUS_VERIFIED = 0,
    BOARD_INFO_STATUS_OBSERVED = 1,
    BOARD_INFO_STATUS_INFERRED = 2,
    BOARD_INFO_STATUS_EXPERIMENTAL = 3,
    BOARD_INFO_STATUS_UNIMPLEMENTED = 4,
    BOARD_INFO_STATUS_COUNT = 5
};

/* Ids are never reused or renumbered (board_descriptor.SOC_IDS). */
enum board_info_soc {
    BOARD_INFO_SOC_NONE = 0,
    BOARD_INFO_SOC_X1E80100 = 1,        /* Snapdragon X Elite, "Hamoa" */
    BOARD_INFO_SOC_X2E_96_100 = 2,      /* Snapdragon X2 Elite, "Glymur" */
    BOARD_INFO_SOC_COUNT = 3
};

/* Bit positions in device_present/device_absent/device_mmio = DEVICE_VOCABULARY order in the descriptor module.
 * Append only, with a minor bump: the guest-DT generator and this ABI must agree on the names. */
enum board_info_device {
    BOARD_INFO_DEV_GPU = 0,
    BOARD_INFO_DEV_GPU_GMU = 1,
    BOARD_INFO_DEV_DISPLAY = 2,
    BOARD_INFO_DEV_UART_DEBUG = 3,
    BOARD_INFO_DEV_USB_HOST = 4,
    BOARD_INFO_DEV_KEYBOARD = 5,
    BOARD_INFO_DEV_TOUCHPAD = 6,
    BOARD_INFO_DEV_TOUCHSCREEN = 7,
    BOARD_INFO_DEV_WIFI = 8,
    BOARD_INFO_DEV_NVME = 9,
    BOARD_INFO_DEV_COUNT = 10
};
#define BOARD_INFO_DEVICE_NONE 0xffu    /* range.device: the window belongs to no device */

enum board_info_device_state {
    BOARD_INFO_DEVICE_UNKNOWN = 0,      /* neither bit set: unresolved, and boot-readiness refuses it */
    BOARD_INFO_DEVICE_PRESENT = 1,
    BOARD_INFO_DEVICE_ABSENT = 2        /* withdrawn from the guest device tree, never faked */
};

enum board_info_range_kind { BOARD_INFO_RANGE_DRAM = 0, BOARD_INFO_RANGE_MMIO = 1 };
enum board_info_smmu_kind { BOARD_INFO_SMMU_V3 = 0, BOARD_INFO_SMMU_MMU500 = 1 };

/* `known` bits: which groups of the blob are populated. */
#define BOARD_INFO_KNOWN_SOC (1u << 0)          /* soc_id (name and family are both implied by it) */
#define BOARD_INFO_KNOWN_MIDR (1u << 1)         /* midr_count, midr_value, midr_mask */
#define BOARD_INFO_KNOWN_COUNTS (1u << 2)       /* cluster_count and cores_per_cluster, both */
#define BOARD_INFO_KNOWN_AFFINITY (1u << 3)     /* affinity_mask and cluster_mask, both */
#define BOARD_INFO_KNOWN_MPIDRS (1u << 4)       /* mpidr_count, mpidr */
#define BOARD_INFO_KNOWN_GIC_VERSION (1u << 5)
#define BOARD_INFO_KNOWN_GICD (1u << 6)
#define BOARD_INFO_KNOWN_GICR (1u << 7)
#define BOARD_INFO_KNOWN_ITS (1u << 8)          /* its_present is meaningful: absent is a known fact too */
#define BOARD_INFO_KNOWN_SMMU (1u << 9)         /* smmu_count is meaningful; zero means "none" */
#define BOARD_INFO_KNOWN_TIMER_HZ (1u << 10)
#define BOARD_INFO_KNOWN_TIMER_PPI (1u << 11)   /* the section is known; an individual 0 entry is not recorded */
#define BOARD_INFO_KNOWN_ECAM (1u << 12)        /* ecam_count is meaningful; zero means "no PCIe segment" */
#define BOARD_INFO_KNOWN_RANGES (1u << 13)
#define BOARD_INFO_KNOWN_IPA (1u << 14)
#define BOARD_INFO_KNOWN_ALL ((1u << 15) - 1u)

/* Result of board_info_validate(). Distinct codes so a refusal can be diagnosed without a debugger; the order of the
 * checks (and so which code a multiply-broken blob reports) is part of the contract tools/test-board-info.py holds
 * both implementations to: null, align, truncated, magic, major, length, CRC, flags, counts, reserved/canonical
 * zeros, values, cross-field consistency, window limits, window overlap. */
enum board_info_error {
    BOARD_INFO_OK = 0,
    BOARD_INFO_ERR_NULL = 1,            /* null blob or null out pointer */
    BOARD_INFO_ERR_ALIGN = 2,           /* blob is not 8-byte aligned */
    BOARD_INFO_ERR_TRUNCATED = 3,       /* shorter than the header, or than total_length says */
    BOARD_INFO_ERR_MAGIC = 4,
    BOARD_INFO_ERR_MAJOR = 5,           /* an ABI major this reader does not implement */
    BOARD_INFO_ERR_LENGTH = 6,          /* total_length misaligned, below the 1.0 layout or above the maximum */
    BOARD_INFO_ERR_CRC = 7,
    BOARD_INFO_ERR_REQUIRED_FLAGS = 8,  /* a required flag this reader does not know */
    BOARD_INFO_ERR_OPTIONAL_FLAGS = 9,  /* an unknown optional flag at the minor that cannot define one */
    BOARD_INFO_ERR_COUNT = 10,          /* an array count above its bound */
    BOARD_INFO_ERR_RESERVED = 11,       /* nonzero reserved bytes, or a value outside a group marked unknown */
    BOARD_INFO_ERR_VALUE = 12,          /* a field outside its legal set */
    BOARD_INFO_ERR_CONSISTENCY = 13,    /* two fields that contradict each other */
    BOARD_INFO_ERR_WINDOW = 14,         /* a physical window that is empty, unaligned or extends beyond 1 << 36 */
    BOARD_INFO_ERR_OVERLAP = 15,        /* two physical windows that overlap */
    BOARD_INFO_ERR_COUNT_ALL = 16
};

/* board_info_unready() bits: groups a boot depends on that the blob leaves unknown. */
#define BOARD_INFO_UNREADY_STATUS (1u << 0)         /* status UNIMPLEMENTED: a template, not a boot profile */
#define BOARD_INFO_UNREADY_SOC (1u << 1)
#define BOARD_INFO_UNREADY_MIDR (1u << 2)
#define BOARD_INFO_UNREADY_COUNTS (1u << 3)
#define BOARD_INFO_UNREADY_AFFINITY (1u << 4)
#define BOARD_INFO_UNREADY_MPIDRS (1u << 5)
#define BOARD_INFO_UNREADY_GIC_VERSION (1u << 6)
#define BOARD_INFO_UNREADY_GICD (1u << 7)
#define BOARD_INFO_UNREADY_GICR (1u << 8)
#define BOARD_INFO_UNREADY_ITS (1u << 9)
#define BOARD_INFO_UNREADY_SMMU (1u << 10)
#define BOARD_INFO_UNREADY_TIMER_HZ (1u << 11)
#define BOARD_INFO_UNREADY_TIMER_PPI (1u << 12)     /* unknown, or one of the four INTIDs is unrecorded */
#define BOARD_INFO_UNREADY_ECAM (1u << 13)
#define BOARD_INFO_UNREADY_RANGES (1u << 14)        /* unknown, no DRAM window, or a DRAM window without geometry */
#define BOARD_INFO_UNREADY_DEVICES (1u << 15)       /* some device is neither present nor absent */
#define BOARD_INFO_UNREADY_IPA (1u << 16)

struct board_info_header {
    uint8_t magic[8];                   /* "SNAPBRDI", no terminator */
    uint32_t abi_major;
    uint32_t abi_minor;
    uint32_t total_length;              /* bytes covered by the CRC; a multiple of 8, at most BOARD_INFO_MAX_LENGTH */
    uint32_t crc32;
    uint32_t flags_required;
    uint32_t flags_optional;
};

struct board_info_ecam {                /* one PCIe ECAM window: (end_bus + 1) MiB at base */
    uint64_t base;
    uint16_t segment;
    uint8_t start_bus;
    uint8_t end_bus;
    uint32_t reserved;
};

struct board_info_smmu {
    uint8_t name[BOARD_INFO_NAME_LEN];
    uint64_t base;
    uint64_t size;
    uint8_t kind;                       /* enum board_info_smmu_kind */
    uint8_t reserved[7];
};

struct board_info_range {
    uint8_t name[BOARD_INFO_NAME_LEN];
    uint64_t base;                      /* zero unless geometry_known */
    uint64_t size;
    uint8_t kind;                       /* enum board_info_range_kind */
    uint8_t geometry_known;             /* 0 only for a DRAM window whose extent has not been measured */
    uint8_t device;                     /* enum board_info_device or BOARD_INFO_DEVICE_NONE */
    uint8_t reserved[5];
};

struct board_info {
    struct board_info_header header;
    uint32_t status;                    /* enum board_info_status */
    uint32_t soc_id;                    /* enum board_info_soc; zero unless KNOWN_SOC */
    uint8_t board_id[BOARD_INFO_NAME_LEN];
    uint32_t known;
    uint32_t reserved0;

    uint32_t midr_count;                /* MIDR_EL1 set: (midr_value[i], midr_mask[i]) matches a core */
    uint32_t reserved1;
    uint32_t midr_value[BOARD_INFO_MAX_MIDR];
    uint32_t midr_mask[BOARD_INFO_MAX_MIDR];

    uint32_t cluster_count;
    uint32_t cores_per_cluster;
    uint64_t affinity_mask;             /* MPIDR_EL1 bits that identify a core */
    uint64_t cluster_mask;              /* the subset of affinity_mask that identifies its cluster */
    uint32_t mpidr_count;
    uint32_t reserved2;
    uint64_t mpidr[BOARD_INFO_MAX_CPUS];

    uint32_t gic_version;               /* 3 or 4; decides the redistributor frame size */
    uint32_t its_present;
    uint64_t gicd_base;
    uint64_t gicd_size;
    uint64_t gicr_base;
    uint64_t gicr_stride;               /* per-core redistributor stride, >= 128 KiB (v3) or 256 KiB (v4) */
    uint64_t gicr_size;                 /* the whole redistributor region, a multiple of the stride */
    uint64_t its_base;
    uint64_t its_size;

    uint32_t timer_hz;
    uint8_t timer_ppi[4];               /* INTID of secure phys, phys, virt, hyp timer; 0 = not recorded */

    uint32_t ecam_count;
    uint32_t reserved3;
    struct board_info_ecam ecam[BOARD_INFO_MAX_ECAM];

    uint32_t smmu_count;
    uint32_t reserved4;
    struct board_info_smmu smmu[BOARD_INFO_MAX_SMMU];

    uint32_t range_count;
    uint32_t reserved5;
    struct board_info_range range[BOARD_INFO_MAX_RANGES];

    uint32_t device_present;            /* bit i: device i is present */
    uint32_t device_absent;             /* bit i: device i is absent; neither bit = unknown */
    uint32_t device_mmio;               /* bit i: the guest reaches device i through windows of its own */
    uint32_t ipa_bits;                  /* BOARD_INFO_IPA_BITS, or zero unless KNOWN_IPA */
};

#define BOARD_INFO_V1_SIZE 2168u

/* Every offset is asserted: probes/boardinfo.py repeats this table and tools/test-board-info.py compares both. */
_Static_assert(sizeof(struct board_info_header) == 32, "header size");
_Static_assert(sizeof(struct board_info_ecam) == 16, "ecam entry size");
_Static_assert(sizeof(struct board_info_smmu) == 40, "smmu entry size");
_Static_assert(sizeof(struct board_info_range) == 40, "range entry size");
_Static_assert(sizeof(struct board_info) == BOARD_INFO_V1_SIZE, "1.0 layout size");
_Static_assert(BOARD_INFO_V1_SIZE % 8u == 0 && BOARD_INFO_V1_SIZE <= BOARD_INFO_MAX_LENGTH, "1.0 fits the maximum");
_Static_assert(offsetof(struct board_info_header, abi_major) == 8 && offsetof(struct board_info_header, abi_minor) == 12 &&
               offsetof(struct board_info_header, total_length) == 16 && offsetof(struct board_info_header, crc32) == 20 &&
               offsetof(struct board_info_header, flags_required) == 24 &&
               offsetof(struct board_info_header, flags_optional) == 28, "header fields");
_Static_assert(offsetof(struct board_info_ecam, segment) == 8 && offsetof(struct board_info_ecam, start_bus) == 10 &&
               offsetof(struct board_info_ecam, end_bus) == 11 && offsetof(struct board_info_ecam, reserved) == 12,
               "ecam fields");
_Static_assert(offsetof(struct board_info_smmu, base) == 16 && offsetof(struct board_info_smmu, size) == 24 &&
               offsetof(struct board_info_smmu, kind) == 32 && offsetof(struct board_info_smmu, reserved) == 33,
               "smmu fields");
_Static_assert(offsetof(struct board_info_range, base) == 16 && offsetof(struct board_info_range, size) == 24 &&
               offsetof(struct board_info_range, kind) == 32 && offsetof(struct board_info_range, geometry_known) == 33 &&
               offsetof(struct board_info_range, device) == 34 && offsetof(struct board_info_range, reserved) == 35,
               "range fields");
_Static_assert(offsetof(struct board_info, status) == 32 && offsetof(struct board_info, soc_id) == 36 &&
               offsetof(struct board_info, board_id) == 40 && offsetof(struct board_info, known) == 56 &&
               offsetof(struct board_info, reserved0) == 60, "identity");
_Static_assert(offsetof(struct board_info, midr_count) == 64 && offsetof(struct board_info, reserved1) == 68 &&
               offsetof(struct board_info, midr_value) == 72 && offsetof(struct board_info, midr_mask) == 88, "midr");
_Static_assert(offsetof(struct board_info, cluster_count) == 104 && offsetof(struct board_info, cores_per_cluster) == 108 &&
               offsetof(struct board_info, affinity_mask) == 112 && offsetof(struct board_info, cluster_mask) == 120 &&
               offsetof(struct board_info, mpidr_count) == 128 && offsetof(struct board_info, reserved2) == 132 &&
               offsetof(struct board_info, mpidr) == 136, "clusters");
_Static_assert(offsetof(struct board_info, gic_version) == 392 && offsetof(struct board_info, its_present) == 396 &&
               offsetof(struct board_info, gicd_base) == 400 && offsetof(struct board_info, gicd_size) == 408 &&
               offsetof(struct board_info, gicr_base) == 416 && offsetof(struct board_info, gicr_stride) == 424 &&
               offsetof(struct board_info, gicr_size) == 432 && offsetof(struct board_info, its_base) == 440 &&
               offsetof(struct board_info, its_size) == 448, "gic");
_Static_assert(offsetof(struct board_info, timer_hz) == 456 && offsetof(struct board_info, timer_ppi) == 460, "timer");
_Static_assert(offsetof(struct board_info, ecam_count) == 464 && offsetof(struct board_info, reserved3) == 468 &&
               offsetof(struct board_info, ecam) == 472, "ecam");
_Static_assert(offsetof(struct board_info, smmu_count) == 536 && offsetof(struct board_info, reserved4) == 540 &&
               offsetof(struct board_info, smmu) == 544, "smmu");
_Static_assert(offsetof(struct board_info, range_count) == 864 && offsetof(struct board_info, reserved5) == 868 &&
               offsetof(struct board_info, range) == 872, "ranges");
_Static_assert(offsetof(struct board_info, device_present) == 2152 && offsetof(struct board_info, device_absent) == 2156 &&
               offsetof(struct board_info, device_mmio) == 2160 && offsetof(struct board_info, ipa_bits) == 2164,
               "devices and limits");
_Static_assert(BOARD_INFO_DEV_COUNT <= 32 && BOARD_INFO_STATUS_COUNT == 5 && BOARD_INFO_SOC_COUNT == 3, "enum bounds");

/* Validate `blob` (len bytes, 8-byte aligned, a snapshot nobody else can write) as a board-info blob. On success
 * returns BOARD_INFO_OK and sets *out to the same memory viewed as struct board_info; on any failure returns an
 * enum board_info_error and sets *out to NULL. Bytes after total_length are ignored. Reads only; never allocates;
 * time is bounded by BOARD_INFO_MAX_LENGTH and the fixed array bounds. A blob that validates is well formed, not
 * necessarily boot-ready: ask board_info_unready(). */
int board_info_validate(const void *blob, size_t len, const struct board_info **out);

/* Groups a boot depends on that this (validated) blob leaves unknown, as BOARD_INFO_UNREADY_* bits; zero means
 * every such group is populated, every device has a state, the board is not a template, and DRAM is measured. */
uint32_t board_info_unready(const struct board_info *info);

/* Short fixed description of a board_info_error, for the monitor's diagnostic log; never NULL. */
const char *board_info_strerror(int code);

static inline int board_info_device_state(const struct board_info *info, unsigned device)
{
    if (device >= BOARD_INFO_DEV_COUNT)
        return BOARD_INFO_DEVICE_UNKNOWN;
    if (info->device_present >> device & 1u)
        return BOARD_INFO_DEVICE_PRESENT;
    if (info->device_absent >> device & 1u)
        return BOARD_INFO_DEVICE_ABSENT;
    return BOARD_INFO_DEVICE_UNKNOWN;
}

#endif
