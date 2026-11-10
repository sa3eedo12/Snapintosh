/* SPDX-License-Identifier: MIT */
/* See board-info.h. No libc: this links into the monitor only when SNAP_PLATFORM_SP11 is in the build flags
 * (probes/run.py OPTIONAL) and into tools/test-board-info.c. Every loop is bounded by a constant, so validation time
 * does not depend on the blob. The blob is read in place; the caller owns the snapshot. */
#include "board-info.h"

#define MPIDR_AFFINITY 0xff00ffffffull          /* Aff3:Aff2:Aff1:Aff0 of MPIDR_EL1 (bits 39:32 and 23:0) */
#define MIDR_PINNED_MASK 0xff00fff0u            /* implementer 31:24 and part number 15:4 must be matched */
#define MIDR_ARCH_ID_DEFINED 0xfu               /* architecture 19:16 = 0xf: Armv8+, features in the ID registers */
#define GIC_FRAME 0x10000ull                    /* the distributor and each redistributor frame are 64 KiB units */
#define GICR_STRIDE_V3 0x20000ull               /* RD_base + SGI_base */
#define GICR_STRIDE_V4 0x40000ull               /* adds VLPI_base and a reserved frame */
#define ECAM_BUS_SHIFT 20                       /* one bus of ECAM configuration space is 1 MiB */
#define IPA_LIMIT (1ull << BOARD_INFO_IPA_BITS)
#define WINDOW_SLOTS (3u + BOARD_INFO_MAX_SMMU + BOARD_INFO_MAX_ECAM + BOARD_INFO_MAX_RANGES)

/* MIDR_EL1 implementer byte of each SoC id; 0x51 is Qualcomm (Arm ARM MIDR_EL1.Implementer). */
static uint32_t soc_implementer(uint32_t soc_id)
{
    return soc_id == BOARD_INFO_SOC_X1E80100 || soc_id == BOARD_INFO_SOC_X2E_96_100 ? 0x51u : 0xffffffffu;
}

static uint32_t crc32_zeroed(const uint8_t *p, size_t n)
{
    uint32_t crc = 0xffffffffu;
    size_t i;
    int bit;

    for (i = 0; i < n; i++) {
        uint32_t byte = i >= offsetof(struct board_info_header, crc32) && i < offsetof(struct board_info_header, crc32) + 4
                            ? 0u : p[i];
        crc ^= byte;
        for (bit = 0; bit < 8; bit++)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static int all_zero(const void *p, size_t n)
{
    const uint8_t *b = p;
    uint8_t acc = 0;

    while (n--)
        acc |= *b++;
    return acc == 0;
}

/* [a-z0-9][a-z0-9-]{0,14}, then NUL padding to the end: the descriptor's NAME_RE, with room for a terminator. */
static int name_valid(const uint8_t *name)
{
    unsigned i;

    for (i = 0; i < BOARD_INFO_NAME_LEN && name[i]; i++) {
        uint8_t c = name[i];
        int alnum = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');

        if (!alnum && !(c == '-' && i))
            return 0;
    }
    if (i == 0 || i == BOARD_INFO_NAME_LEN)
        return 0;
    return all_zero(name + i, BOARD_INFO_NAME_LEN - i);
}

static int name_equal(const uint8_t *a, const uint8_t *b)
{
    unsigned i;

    for (i = 0; i < BOARD_INFO_NAME_LEN; i++)
        if (a[i] != b[i])
            return 0;
    return 1;
}

static int check_counts(const struct board_info *b)
{
    if (b->midr_count > BOARD_INFO_MAX_MIDR || b->mpidr_count > BOARD_INFO_MAX_CPUS ||
        b->ecam_count > BOARD_INFO_MAX_ECAM || b->smmu_count > BOARD_INFO_MAX_SMMU ||
        b->range_count > BOARD_INFO_MAX_RANGES)
        return BOARD_INFO_ERR_COUNT;
    return BOARD_INFO_OK;
}

/* Reserved bytes, bits outside the defined sets, and groups marked unknown must read as zero: a stale value cannot
 * hide behind a cleared `known` bit, and an array slot past its count cannot carry data a later reader might use. */
static int check_reserved(const struct board_info *b)
{
    uint32_t known = b->known;
    unsigned i;

    if ((known & ~BOARD_INFO_KNOWN_ALL) || b->reserved0 || b->reserved1 || b->reserved2 || b->reserved3 ||
        b->reserved4 || b->reserved5 ||
        ((b->device_present | b->device_absent | b->device_mmio) >> BOARD_INFO_DEV_COUNT))
        return BOARD_INFO_ERR_RESERVED;
    if ((!(known & BOARD_INFO_KNOWN_SOC) && b->soc_id) ||
        (!(known & BOARD_INFO_KNOWN_MIDR) && b->midr_count) ||
        (!(known & BOARD_INFO_KNOWN_COUNTS) && (b->cluster_count || b->cores_per_cluster)) ||
        (!(known & BOARD_INFO_KNOWN_AFFINITY) && (b->affinity_mask || b->cluster_mask)) ||
        (!(known & BOARD_INFO_KNOWN_MPIDRS) && b->mpidr_count) ||
        (!(known & BOARD_INFO_KNOWN_GIC_VERSION) && b->gic_version) ||
        (!(known & BOARD_INFO_KNOWN_GICD) && (b->gicd_base || b->gicd_size)) ||
        (!(known & BOARD_INFO_KNOWN_GICR) && (b->gicr_base || b->gicr_stride || b->gicr_size)) ||
        (!(known & BOARD_INFO_KNOWN_ITS) && b->its_present) ||
        (!b->its_present && (b->its_base || b->its_size)) ||
        (!(known & BOARD_INFO_KNOWN_TIMER_HZ) && b->timer_hz) ||
        (!(known & BOARD_INFO_KNOWN_TIMER_PPI) &&
         (b->timer_ppi[0] || b->timer_ppi[1] || b->timer_ppi[2] || b->timer_ppi[3])) ||
        (!(known & BOARD_INFO_KNOWN_ECAM) && b->ecam_count) ||
        (!(known & BOARD_INFO_KNOWN_SMMU) && b->smmu_count) ||
        (!(known & BOARD_INFO_KNOWN_RANGES) && b->range_count) ||
        (!(known & BOARD_INFO_KNOWN_IPA) && b->ipa_bits))
        return BOARD_INFO_ERR_RESERVED;

    for (i = b->midr_count; i < BOARD_INFO_MAX_MIDR; i++)
        if (b->midr_value[i] || b->midr_mask[i])
            return BOARD_INFO_ERR_RESERVED;
    for (i = b->mpidr_count; i < BOARD_INFO_MAX_CPUS; i++)
        if (b->mpidr[i])
            return BOARD_INFO_ERR_RESERVED;
    for (i = 0; i < BOARD_INFO_MAX_ECAM; i++)
        if (i >= b->ecam_count ? !all_zero(&b->ecam[i], sizeof b->ecam[i]) : b->ecam[i].reserved != 0)
            return BOARD_INFO_ERR_RESERVED;
    for (i = 0; i < BOARD_INFO_MAX_SMMU; i++)
        if (i >= b->smmu_count ? !all_zero(&b->smmu[i], sizeof b->smmu[i])
                               : !all_zero(b->smmu[i].reserved, sizeof b->smmu[i].reserved))
            return BOARD_INFO_ERR_RESERVED;
    for (i = 0; i < BOARD_INFO_MAX_RANGES; i++) {
        const struct board_info_range *r = &b->range[i];

        if (i >= b->range_count ? !all_zero(r, sizeof *r)
                                : (!all_zero(r->reserved, sizeof r->reserved) ||
                                   (!r->geometry_known && (r->base || r->size))))
            return BOARD_INFO_ERR_RESERVED;
    }
    return BOARD_INFO_OK;
}

static int check_values(const struct board_info *b)
{
    uint32_t known = b->known;
    unsigned i, j;

    if (b->status >= BOARD_INFO_STATUS_COUNT || !name_valid(b->board_id))
        return BOARD_INFO_ERR_VALUE;
    if ((known & BOARD_INFO_KNOWN_SOC) && (b->soc_id == BOARD_INFO_SOC_NONE || b->soc_id >= BOARD_INFO_SOC_COUNT))
        return BOARD_INFO_ERR_VALUE;

    if (known & BOARD_INFO_KNOWN_MIDR) {
        if (!b->midr_count)
            return BOARD_INFO_ERR_VALUE;
        for (i = 0; i < b->midr_count; i++) {
            uint32_t value = b->midr_value[i], mask = b->midr_mask[i];

            /* A mask that ignores the implementer or part number would match any core. */
            if ((value & ~mask) || (mask & MIDR_PINNED_MASK) != MIDR_PINNED_MASK ||
                ((value >> 16) & 0xfu) != MIDR_ARCH_ID_DEFINED)
                return BOARD_INFO_ERR_VALUE;
            for (j = 0; j < i; j++)
                if (b->midr_value[j] == value && b->midr_mask[j] == mask)
                    return BOARD_INFO_ERR_VALUE;
        }
    }
    if (known & BOARD_INFO_KNOWN_COUNTS) {
        if (b->cluster_count < 1 || b->cluster_count > 8 || b->cores_per_cluster < 1 || b->cores_per_cluster > 16)
            return BOARD_INFO_ERR_VALUE;
        if (b->cluster_count * b->cores_per_cluster > BOARD_INFO_MAX_CPUS)
            return BOARD_INFO_ERR_VALUE;
    }
    if ((known & BOARD_INFO_KNOWN_AFFINITY) &&
        (!b->affinity_mask || (b->affinity_mask & ~MPIDR_AFFINITY) || !b->cluster_mask ||
         (b->cluster_mask & ~b->affinity_mask)))
        return BOARD_INFO_ERR_VALUE;
    if (known & BOARD_INFO_KNOWN_MPIDRS) {
        if (!b->mpidr_count)
            return BOARD_INFO_ERR_VALUE;
        for (i = 1; i < b->mpidr_count; i++)
            for (j = 0; j < i; j++)
                if (b->mpidr[i] == b->mpidr[j])
                    return BOARD_INFO_ERR_VALUE;
    }

    if ((known & BOARD_INFO_KNOWN_GIC_VERSION) && b->gic_version != 3 && b->gic_version != 4)
        return BOARD_INFO_ERR_VALUE;
    if ((known & BOARD_INFO_KNOWN_GICD) &&
        (b->gicd_size < GIC_FRAME || (b->gicd_size & (GIC_FRAME - 1)) || (b->gicd_base & (GIC_FRAME - 1))))
        return BOARD_INFO_ERR_VALUE;
    if (known & BOARD_INFO_KNOWN_GICR) {
        /* The stride may be padded beyond the frames the version defines, never smaller. */
        uint64_t minimum = b->gic_version == 4 ? GICR_STRIDE_V4 : GICR_STRIDE_V3;

        if (b->gicr_stride < minimum || (b->gicr_stride & (GIC_FRAME - 1)))
            return BOARD_INFO_ERR_VALUE;
        if (!b->gicr_size || b->gicr_size % b->gicr_stride || (b->gicr_base & (GIC_FRAME - 1)) ||
            b->gicr_size / b->gicr_stride > BOARD_INFO_MAX_CPUS)
            return BOARD_INFO_ERR_VALUE;
    }
    if (known & BOARD_INFO_KNOWN_ITS) {
        if (b->its_present > 1)
            return BOARD_INFO_ERR_VALUE;
        if (b->its_present && ((b->its_base & (GIC_FRAME - 1)) || (b->its_size & (GIC_FRAME - 1)) || !b->its_size))
            return BOARD_INFO_ERR_VALUE;
    }

    for (i = 0; i < b->smmu_count; i++) {
        if (!name_valid(b->smmu[i].name) || b->smmu[i].kind > BOARD_INFO_SMMU_MMU500)
            return BOARD_INFO_ERR_VALUE;
        for (j = 0; j < i; j++)
            if (name_equal(b->smmu[i].name, b->smmu[j].name))
                return BOARD_INFO_ERR_VALUE;
    }

    if ((known & BOARD_INFO_KNOWN_TIMER_HZ) && (b->timer_hz < 1000000u || b->timer_hz > 1000000000u))
        return BOARD_INFO_ERR_VALUE;
    if (known & BOARD_INFO_KNOWN_TIMER_PPI) {
        uint32_t seen = 0;

        for (i = 0; i < 4; i++) {
            uint32_t intid = b->timer_ppi[i];

            if (!intid)
                continue;
            if (intid < 16 || intid > 31 || ((seen >> intid) & 1u))
                return BOARD_INFO_ERR_VALUE;
            seen |= 1u << intid;
        }
        if (!seen)
            return BOARD_INFO_ERR_VALUE;
    }

    for (i = 0; i < b->ecam_count; i++) {
        if ((b->ecam[i].base & ((1ull << ECAM_BUS_SHIFT) - 1)) || b->ecam[i].end_bus < b->ecam[i].start_bus)
            return BOARD_INFO_ERR_VALUE;
        for (j = 0; j < i; j++)
            if (b->ecam[i].segment == b->ecam[j].segment)
                return BOARD_INFO_ERR_VALUE;
    }

    for (i = 0; i < b->range_count; i++) {
        const struct board_info_range *r = &b->range[i];

        if (!name_valid(r->name) || r->kind > BOARD_INFO_RANGE_MMIO || r->geometry_known > 1)
            return BOARD_INFO_ERR_VALUE;
        if (!r->geometry_known && r->kind != BOARD_INFO_RANGE_DRAM)
            return BOARD_INFO_ERR_VALUE;
        if (r->device != BOARD_INFO_DEVICE_NONE && (r->device >= BOARD_INFO_DEV_COUNT || r->kind == BOARD_INFO_RANGE_DRAM))
            return BOARD_INFO_ERR_VALUE;
        for (j = 0; j < i; j++)
            if (name_equal(r->name, b->range[j].name))
                return BOARD_INFO_ERR_VALUE;
    }

    if ((known & BOARD_INFO_KNOWN_IPA) && b->ipa_bits != BOARD_INFO_IPA_BITS)
        return BOARD_INFO_ERR_VALUE;
    return BOARD_INFO_OK;
}

static int check_consistency(const struct board_info *b)
{
    uint32_t known = b->known;
    unsigned i, j, groups;

    if (known & BOARD_INFO_KNOWN_MIDR) {
        if (!(known & BOARD_INFO_KNOWN_SOC))
            return BOARD_INFO_ERR_CONSISTENCY;
        for (i = 0; i < b->midr_count; i++)
            if ((b->midr_value[i] >> 24) != soc_implementer(b->soc_id))
                return BOARD_INFO_ERR_CONSISTENCY;
    }
    if (known & BOARD_INFO_KNOWN_MPIDRS) {
        if (!(known & BOARD_INFO_KNOWN_AFFINITY))
            return BOARD_INFO_ERR_CONSISTENCY;
        for (i = 0; i < b->mpidr_count; i++)
            if (b->mpidr[i] & ~b->affinity_mask)
                return BOARD_INFO_ERR_CONSISTENCY;
        if (known & BOARD_INFO_KNOWN_COUNTS) {
            if (b->mpidr_count != b->cluster_count * b->cores_per_cluster)
                return BOARD_INFO_ERR_CONSISTENCY;
            /* The cluster mask must split the cores into exactly the stated clusters of equal size. */
            for (i = 0, groups = 0; i < b->mpidr_count; i++) {
                unsigned members = 0, first = 1;

                for (j = 0; j < b->mpidr_count; j++) {
                    if ((b->mpidr[j] & b->cluster_mask) != (b->mpidr[i] & b->cluster_mask))
                        continue;
                    members++;
                    if (j < i)
                        first = 0;
                }
                if (members != b->cores_per_cluster)
                    return BOARD_INFO_ERR_CONSISTENCY;
                groups += first;
            }
            if (groups != b->cluster_count)
                return BOARD_INFO_ERR_CONSISTENCY;
        }
    }
    if ((known & BOARD_INFO_KNOWN_GICR) && (known & BOARD_INFO_KNOWN_COUNTS) &&
        b->gicr_size / b->gicr_stride < b->cluster_count * b->cores_per_cluster)
        return BOARD_INFO_ERR_CONSISTENCY;

    if ((b->device_present & b->device_absent) || (b->device_mmio & b->device_absent))
        return BOARD_INFO_ERR_CONSISTENCY;
    for (i = 0; i < b->range_count; i++) {
        unsigned dev = b->range[i].device;

        /* An absent device is withdrawn from the guest device tree; nothing may still map memory for it. */
        if (dev != BOARD_INFO_DEVICE_NONE && ((b->device_absent >> dev) & 1u))
            return BOARD_INFO_ERR_CONSISTENCY;
    }
    for (i = 0; i < BOARD_INFO_DEV_COUNT; i++) {
        int owns = 0;

        if (!((b->device_present >> i) & 1u))
            continue;
        for (j = 0; j < b->range_count; j++)
            owns |= b->range[j].device == i;
        if (owns != (int)((b->device_mmio >> i) & 1u))
            return BOARD_INFO_ERR_CONSISTENCY;
    }
    return BOARD_INFO_OK;
}

/* Windows in a fixed order: gicd, gicr, its, smmu[], ecam[], range[]. Returns 0 for a slot that holds no window. */
static int window_at(const struct board_info *b, unsigned slot, uint64_t *base, uint64_t *size)
{
    uint32_t known = b->known;

    if (slot == 0) {
        *base = b->gicd_base;
        *size = b->gicd_size;
        return (known & BOARD_INFO_KNOWN_GICD) != 0;
    }
    if (slot == 1) {
        *base = b->gicr_base;
        *size = b->gicr_size;
        return (known & BOARD_INFO_KNOWN_GICR) != 0;
    }
    if (slot == 2) {
        *base = b->its_base;
        *size = b->its_size;
        return (known & BOARD_INFO_KNOWN_ITS) && b->its_present;
    }
    slot -= 3;
    if (slot < BOARD_INFO_MAX_SMMU) {
        *base = b->smmu[slot].base;
        *size = b->smmu[slot].size;
        return slot < b->smmu_count;
    }
    slot -= BOARD_INFO_MAX_SMMU;
    if (slot < BOARD_INFO_MAX_ECAM) {
        /* The MCFG base address is bus 0's: the window runs to the end of end_bus. */
        *base = b->ecam[slot].base;
        *size = ((uint64_t)b->ecam[slot].end_bus + 1u) << ECAM_BUS_SHIFT;
        return slot < b->ecam_count;
    }
    slot -= BOARD_INFO_MAX_ECAM;
    *base = b->range[slot].base;
    *size = b->range[slot].size;
    return slot < b->range_count && b->range[slot].geometry_known;
}

static int check_windows(const struct board_info *b)
{
    unsigned i, j;
    uint64_t base, size, other_base, other_size;

    for (i = 0; i < WINDOW_SLOTS; i++) {
        if (!window_at(b, i, &base, &size))
            continue;
        if (!size || ((base | size) & (BOARD_INFO_PAGE - 1)))
            return BOARD_INFO_ERR_WINDOW;
        /* base + size > limit, without forming a sum that could wrap. The end is exclusive, so it may equal the limit
         * (shadow/stage2.c refuses only hi > limit). */
        if (base >= IPA_LIMIT || size > IPA_LIMIT - base)
            return BOARD_INFO_ERR_WINDOW;
    }
    for (i = 1; i < WINDOW_SLOTS; i++) {
        if (!window_at(b, i, &base, &size))
            continue;
        for (j = 0; j < i; j++)
            if (window_at(b, j, &other_base, &other_size) && base < other_base + other_size &&
                other_base < base + size)
                return BOARD_INFO_ERR_OVERLAP;
    }
    return BOARD_INFO_OK;
}

int board_info_validate(const void *blob, size_t len, const struct board_info **out)
{
    const struct board_info *b;
    uint32_t total;
    unsigned i;
    int rc;

    if (!out)
        return BOARD_INFO_ERR_NULL;
    *out = 0;
    if (!blob)
        return BOARD_INFO_ERR_NULL;
    if ((uintptr_t)blob & 7u)
        return BOARD_INFO_ERR_ALIGN;
    if (len < sizeof(struct board_info_header))
        return BOARD_INFO_ERR_TRUNCATED;
    b = blob;
    for (i = 0; i < BOARD_INFO_MAGIC_LEN; i++)
        if (b->header.magic[i] != (uint8_t)BOARD_INFO_MAGIC[i])
            return BOARD_INFO_ERR_MAGIC;
    if (b->header.abi_major != BOARD_INFO_ABI_MAJOR)
        return BOARD_INFO_ERR_MAJOR;
    total = b->header.total_length;
    if ((total & 7u) || total > BOARD_INFO_MAX_LENGTH || total < BOARD_INFO_V1_SIZE)
        return BOARD_INFO_ERR_LENGTH;
    if (total > len)
        return BOARD_INFO_ERR_TRUNCATED;
    if (crc32_zeroed(blob, total) != b->header.crc32)
        return BOARD_INFO_ERR_CRC;
    if (b->header.flags_required & ~BOARD_INFO_FLAGS_REQUIRED_KNOWN)
        return BOARD_INFO_ERR_REQUIRED_FLAGS;
    if (b->header.abi_minor <= BOARD_INFO_ABI_MINOR && (b->header.flags_optional & ~BOARD_INFO_FLAGS_OPTIONAL_KNOWN))
        return BOARD_INFO_ERR_OPTIONAL_FLAGS;

    rc = check_counts(b);
    if (!rc)
        rc = check_reserved(b);
    if (!rc)
        rc = check_values(b);
    if (!rc)
        rc = check_consistency(b);
    if (!rc)
        rc = check_windows(b);
    if (rc)
        return rc;
    *out = b;
    return BOARD_INFO_OK;
}

uint32_t board_info_unready(const struct board_info *b)
{
    uint32_t known = b->known, mask = 0;
    unsigned i;
    int dram = 0, blind = 0;

    if (b->status == BOARD_INFO_STATUS_UNIMPLEMENTED)
        mask |= BOARD_INFO_UNREADY_STATUS;
    if (!(known & BOARD_INFO_KNOWN_SOC))
        mask |= BOARD_INFO_UNREADY_SOC;
    if (!(known & BOARD_INFO_KNOWN_MIDR))
        mask |= BOARD_INFO_UNREADY_MIDR;
    if (!(known & BOARD_INFO_KNOWN_COUNTS))
        mask |= BOARD_INFO_UNREADY_COUNTS;
    if (!(known & BOARD_INFO_KNOWN_AFFINITY))
        mask |= BOARD_INFO_UNREADY_AFFINITY;
    if (!(known & BOARD_INFO_KNOWN_MPIDRS))
        mask |= BOARD_INFO_UNREADY_MPIDRS;
    if (!(known & BOARD_INFO_KNOWN_GIC_VERSION))
        mask |= BOARD_INFO_UNREADY_GIC_VERSION;
    if (!(known & BOARD_INFO_KNOWN_GICD))
        mask |= BOARD_INFO_UNREADY_GICD;
    if (!(known & BOARD_INFO_KNOWN_GICR))
        mask |= BOARD_INFO_UNREADY_GICR;
    if (!(known & BOARD_INFO_KNOWN_ITS))
        mask |= BOARD_INFO_UNREADY_ITS;
    if (!(known & BOARD_INFO_KNOWN_SMMU))
        mask |= BOARD_INFO_UNREADY_SMMU;
    if (!(known & BOARD_INFO_KNOWN_TIMER_HZ))
        mask |= BOARD_INFO_UNREADY_TIMER_HZ;
    if (!(known & BOARD_INFO_KNOWN_TIMER_PPI))
        mask |= BOARD_INFO_UNREADY_TIMER_PPI;
    else
        for (i = 0; i < 4; i++)
            if (!b->timer_ppi[i])
                mask |= BOARD_INFO_UNREADY_TIMER_PPI;
    if (!(known & BOARD_INFO_KNOWN_ECAM))
        mask |= BOARD_INFO_UNREADY_ECAM;
    if (!(known & BOARD_INFO_KNOWN_RANGES)) {
        mask |= BOARD_INFO_UNREADY_RANGES;
    } else {
        for (i = 0; i < b->range_count; i++) {
            if (b->range[i].kind != BOARD_INFO_RANGE_DRAM)
                continue;
            if (b->range[i].geometry_known)
                dram = 1;
            else
                blind = 1;
        }
        if (!dram || blind)
            mask |= BOARD_INFO_UNREADY_RANGES;
    }
    if ((b->device_present | b->device_absent) != (1u << BOARD_INFO_DEV_COUNT) - 1u)
        mask |= BOARD_INFO_UNREADY_DEVICES;
    if (!(known & BOARD_INFO_KNOWN_IPA))
        mask |= BOARD_INFO_UNREADY_IPA;
    return mask;
}

const char *board_info_strerror(int code)
{
    switch (code) {
    case BOARD_INFO_OK: return "ok";
    case BOARD_INFO_ERR_NULL: return "null pointer";
    case BOARD_INFO_ERR_ALIGN: return "blob not 8-byte aligned";
    case BOARD_INFO_ERR_TRUNCATED: return "blob shorter than its header or total_length";
    case BOARD_INFO_ERR_MAGIC: return "bad magic";
    case BOARD_INFO_ERR_MAJOR: return "unknown ABI major";
    case BOARD_INFO_ERR_LENGTH: return "total_length out of bounds";
    case BOARD_INFO_ERR_CRC: return "CRC mismatch";
    case BOARD_INFO_ERR_REQUIRED_FLAGS: return "unknown required flag";
    case BOARD_INFO_ERR_OPTIONAL_FLAGS: return "unknown optional flag at this minor";
    case BOARD_INFO_ERR_COUNT: return "array count above its bound";
    case BOARD_INFO_ERR_RESERVED: return "nonzero reserved or unknown-group data";
    case BOARD_INFO_ERR_VALUE: return "field outside its legal set";
    case BOARD_INFO_ERR_CONSISTENCY: return "contradicting fields";
    case BOARD_INFO_ERR_WINDOW: return "window empty, unaligned or beyond 1<<36";
    case BOARD_INFO_ERR_OVERLAP: return "overlapping windows";
    default: return "unknown error code";
    }
}
