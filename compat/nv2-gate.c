/* SPDX-License-Identifier: MIT */
/* The NV/NV2 launch gate: record validation and the allow decision. The format
 * and the policy are described in nv2-gate.h; this file only implements them.
 *
 * Freestanding integer code. It runs inside the position-independent probe blob,
 * where there is no libc and absolute pointers must not appear, so there are no
 * string or pointer tables: the verdict names live in the host tooling. */
#include "nv2-gate.h"

#ifdef SNAP_NV2_RECORD_HEADER
#include SNAP_NV2_RECORD_HEADER
#endif

/* Explicit byte loaders: an embedded record is a byte array with no alignment
 * guarantee, and its layout is little-endian whatever the host is. */
static uint32_t load16(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8;
}

static uint32_t load32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
           (uint32_t)p[3] << 24;
}

static uint64_t load64(const uint8_t *p)
{
    return (uint64_t)load32(p) | (uint64_t)load32(p + 4) << 32;
}

uint32_t snap_nv2_crc32(const void *data, size_t length)
{
    const uint8_t *p = data;
    uint32_t crc = 0xffffffffu;
    size_t i;
    int bit;

    for (i = 0; i < length; i++) {
        crc ^= p[i];
        for (bit = 0; bit < 8; bit++)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static int tool_char_valid(uint8_t c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-';
}

/* The tool name is the only free text in the record. It must be a short word,
 * NUL padded, so that nothing but a name can hide in those 16 bytes. */
static int tool_field_valid(const uint8_t *tool)
{
    size_t i = 0;

    while (i < SNAP_NV2_TOOL_LEN && tool[i] != 0) {
        if (!tool_char_valid(tool[i]))
            return 0;
        i++;
    }
    if (i == 0 || i == SNAP_NV2_TOOL_LEN)
        return 0;
    for (; i < SNAP_NV2_TOOL_LEN; i++)
        if (tool[i] != 0)
            return 0;
    return 1;
}

static int bytes_all_zero(const uint8_t *p, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++)
        if (p[i] != 0)
            return 0;
    return 1;
}

static void view_clear(struct snap_nv2_view *view)
{
    view->soc = 0;
    view->build_id = 0;
    view->midr_el1 = 0;
    view->revidr_el1 = 0;
    view->id_aa64mmfr2_el1 = 0;
    view->id_aa64mmfr4_el1 = 0;
    view->vncr_redirect = 0;
    view->negative_control_trapped = 0;
    view->evidence = 0;
}

enum snap_nv2_verdict snap_nv2_record_parse(const void *record, size_t length,
                                            struct snap_nv2_view *view)
{
    const uint8_t *p = record;

    if (view)
        view_clear(view);
    if (!p || length == 0 || !view)
        return SNAP_NV2_DENY_NO_RECORD;
    if (length != SNAP_NV2_RECORD_SIZE)
        return SNAP_NV2_DENY_LENGTH;
    if (load32(p + offsetof(struct snap_nv2_record, magic)) != SNAP_NV2_RECORD_MAGIC)
        return SNAP_NV2_DENY_MAGIC;
    if (load16(p + offsetof(struct snap_nv2_record, version)) != SNAP_NV2_RECORD_VERSION)
        return SNAP_NV2_DENY_VERSION;
    if (snap_nv2_crc32(p, offsetof(struct snap_nv2_record, crc32)) !=
        load32(p + offsetof(struct snap_nv2_record, crc32)))
        return SNAP_NV2_DENY_CRC;

    if (load16(p + offsetof(struct snap_nv2_record, flags)) != 0 ||
        p[offsetof(struct snap_nv2_record, reserved0)] != 0 ||
        load32(p + offsetof(struct snap_nv2_record, reserved1)) != 0 ||
        !bytes_all_zero(p + offsetof(struct snap_nv2_record, reserved2),
                        SNAP_NV2_RESERVED2_LEN) ||
        !tool_field_valid(p + offsetof(struct snap_nv2_record, tool)))
        return SNAP_NV2_DENY_RESERVED;
    if (load32(p + offsetof(struct snap_nv2_record, soc)) > SNAP_NV2_SOC_X2E_96_100 ||
        p[offsetof(struct snap_nv2_record, vncr_redirect)] > SNAP_NV2_FAIL ||
        p[offsetof(struct snap_nv2_record, negative_control_trapped)] > SNAP_NV2_FAIL ||
        p[offsetof(struct snap_nv2_record, evidence)] > SNAP_NV2_EVIDENCE_UNIMPLEMENTED)
        return SNAP_NV2_DENY_RESERVED;

    view->soc = load32(p + offsetof(struct snap_nv2_record, soc));
    view->build_id = load32(p + offsetof(struct snap_nv2_record, build_id));
    view->midr_el1 = load64(p + offsetof(struct snap_nv2_record, midr_el1));
    view->revidr_el1 = load64(p + offsetof(struct snap_nv2_record, revidr_el1));
    view->id_aa64mmfr2_el1 = load64(p + offsetof(struct snap_nv2_record, id_aa64mmfr2_el1));
    view->id_aa64mmfr4_el1 = load64(p + offsetof(struct snap_nv2_record, id_aa64mmfr4_el1));
    view->vncr_redirect = p[offsetof(struct snap_nv2_record, vncr_redirect)];
    view->negative_control_trapped = p[offsetof(struct snap_nv2_record, negative_control_trapped)];
    view->evidence = p[offsetof(struct snap_nv2_record, evidence)];
    return SNAP_NV2_PARSED;
}

/* ID_AA64MMFR2_EL1.NV is [27:24]: 0 not implemented, 1 FEAT_NV, 2 FEAT_NV2 as
 * well. ID_AA64MMFR4_EL1.NV_frac is [23:20] (E2H0 is [27:24] there), where 0
 * means the MMFR2 value stands and the other values name NV2-only or later NV
 * extensions. This is the one combination the repository has measured on real
 * silicon (the A16 reports NV 2); everything else, including NV2-without-NV and
 * every reserved value, is unmeasured for the monitor's HCR_EL2.NV/NV2 use and
 * is therefore not advertising. */
int snap_nv2_id_advertises_nv2(uint64_t mmfr2, uint64_t mmfr4)
{
    return ((mmfr2 >> 24) & 0xf) == 2 && ((mmfr4 >> 20) & 0xf) == 0;
}

enum snap_nv2_verdict snap_nv2_gate_decide(const void *record, size_t length,
                                           uint32_t expect_soc,
                                           uint64_t midr, uint64_t revidr,
                                           uint64_t mmfr2, uint64_t mmfr4)
{
    struct snap_nv2_view v;
    enum snap_nv2_verdict parsed = snap_nv2_record_parse(record, length, &v);

    if (parsed != SNAP_NV2_PARSED)
        return parsed;
    if (expect_soc == SNAP_NV2_SOC_UNKNOWN || v.soc != expect_soc)
        return SNAP_NV2_DENY_SOC;
    /* An all-zero MIDR is an unread register, not a core: never let two zeros
     * agree. */
    if (midr == 0 || v.midr_el1 != midr)
        return SNAP_NV2_DENY_MIDR;
    if (v.revidr_el1 != revidr)
        return SNAP_NV2_DENY_REVIDR;
    if (!snap_nv2_id_advertises_nv2(mmfr2, mmfr4))
        return SNAP_NV2_DENY_NOT_ADVERTISED;
    if (v.id_aa64mmfr2_el1 != mmfr2 || v.id_aa64mmfr4_el1 != mmfr4)
        return SNAP_NV2_DENY_ID_MISMATCH;
    if (v.vncr_redirect == SNAP_NV2_NOT_RUN)
        return SNAP_NV2_DENY_VNCR_NOT_RUN;
    if (v.vncr_redirect != SNAP_NV2_PASS)
        return SNAP_NV2_DENY_VNCR_FAIL;
    if (v.negative_control_trapped == SNAP_NV2_NOT_RUN)
        return SNAP_NV2_DENY_NEGCTL_NOT_RUN;
    if (v.negative_control_trapped != SNAP_NV2_PASS)
        return SNAP_NV2_DENY_NEGCTL_FAIL;
    if (v.evidence != SNAP_NV2_EVIDENCE_VERIFIED &&
        v.evidence != SNAP_NV2_EVIDENCE_OBSERVED)
        return SNAP_NV2_DENY_EVIDENCE;
    return SNAP_NV2_ALLOW;
}

#ifdef SNAP_NV2_RECORD_HEADER
const uint8_t *snap_nv2_embedded_record(size_t *length)
{
    *length = sizeof(snap_nv2_record_bytes);
    return snap_nv2_record_bytes;
}
#else
const uint8_t *snap_nv2_embedded_record(size_t *length)
{
    *length = 0;
    return NULL;
}
#endif

enum snap_nv2_verdict snap_nv2_gate_decide_embedded(uint32_t expect_soc,
                                                    uint64_t midr, uint64_t revidr,
                                                    uint64_t mmfr2, uint64_t mmfr4)
{
    size_t length;
    const uint8_t *record = snap_nv2_embedded_record(&length);

    return snap_nv2_gate_decide(record, length, expect_soc, midr, revidr, mmfr2, mmfr4);
}

int64_t snap_nv2_verdict_status(enum snap_nv2_verdict verdict)
{
    switch (verdict) {
    case SNAP_NV2_ALLOW:
        return 0;
    case SNAP_NV2_DENY_SOC:
    case SNAP_NV2_DENY_MIDR:
    case SNAP_NV2_DENY_REVIDR:
    case SNAP_NV2_DENY_ID_MISMATCH:
        return SNAP_NV2_STATUS_MISMATCH;
    case SNAP_NV2_DENY_NOT_ADVERTISED:
        return SNAP_NV2_STATUS_NO_NV2;
    case SNAP_NV2_DENY_VNCR_NOT_RUN:
    case SNAP_NV2_DENY_VNCR_FAIL:
    case SNAP_NV2_DENY_NEGCTL_NOT_RUN:
    case SNAP_NV2_DENY_NEGCTL_FAIL:
    case SNAP_NV2_DENY_EVIDENCE:
        return SNAP_NV2_STATUS_RESULT;
    default:
        /* The record-damage reasons, SNAP_NV2_PARSED and anything that is not a
         * verdict at all: none of them is permission. */
        return SNAP_NV2_STATUS_RECORD;
    }
}
