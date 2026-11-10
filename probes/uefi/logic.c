/* SPDX-License-Identifier: MIT */
#include "logic.h"

#include "efi_types.h"

#define PROBE_NO_BUILTIN __attribute__((no_builtin))

PROBE_NO_BUILTIN void *probe_memcpy(void *dst, const void *src, size_t n)
{
    uint8_t *d = dst;
    const uint8_t *s = src;
    while (n--)
        *d++ = *s++;
    return dst;
}

PROBE_NO_BUILTIN void *probe_memmove(void *dst, const void *src, size_t n)
{
    uint8_t *d = dst;
    const uint8_t *s = src;
    if (d == s || n == 0)
        return dst;
    if (d < s) {
        while (n--)
            *d++ = *s++;
    } else {
        d += n;
        s += n;
        while (n--)
            *--d = *--s;
    }
    return dst;
}

PROBE_NO_BUILTIN void *probe_memset(void *dst, int c, size_t n)
{
    uint8_t *d = dst;
    while (n--)
        *d++ = (uint8_t)c;
    return dst;
}

PROBE_NO_BUILTIN int probe_memcmp(const void *a, const void *b, size_t n)
{
    const uint8_t *x = a;
    const uint8_t *y = b;
    while (n--) {
        if (*x != *y)
            return (int)*x - (int)*y;
        x++;
        y++;
    }
    return 0;
}

size_t probe_strlen(const char *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}

uint16_t probe_le16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

uint32_t probe_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

uint64_t probe_le64(const uint8_t *p)
{
    return (uint64_t)probe_le32(p) | ((uint64_t)probe_le32(p + 4) << 32);
}

uint8_t probe_sum8(const uint8_t *p, size_t n)
{
    uint8_t s = 0;
    while (n--)
        s = (uint8_t)(s + *p++);
    return s;
}

uint8_t probe_crc8(const void *data, size_t n)
{
    const uint8_t *p = data;
    uint8_t crc = 0;
    while (n--) {
        crc ^= *p++;
        for (int bit = 0; bit < 8; bit++)
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    }
    return crc;
}

void probe_sb_init(probe_sb *sb, char *buf, size_t cap)
{
    sb->buf = buf;
    sb->cap = cap;
    sb->len = 0;
    sb->overflow = 0;
    if (cap)
        buf[0] = 0;
}

void probe_sb_putc(probe_sb *sb, char c)
{
    if (sb->cap == 0 || sb->len + 1 >= sb->cap) {
        sb->overflow = 1;
        return;
    }
    sb->buf[sb->len++] = c;
    sb->buf[sb->len] = 0;
}

void probe_sb_puts(probe_sb *sb, const char *s)
{
    while (*s)
        probe_sb_putc(sb, *s++);
}

void probe_sb_hex(probe_sb *sb, uint64_t v, unsigned digits)
{
    static const char hex[] = "0123456789ABCDEF";
    if (digits == 0 || digits > 16) {
        sb->overflow = 1;
        return;
    }
    for (unsigned i = digits; i-- > 0;)
        probe_sb_putc(sb, hex[(v >> (4 * i)) & 0xF]);
}

void probe_sb_dec(probe_sb *sb, uint64_t v)
{
    char tmp[20];
    size_t n = 0;
    do {
        tmp[n++] = (char)('0' + (v % 10));
        v /= 10;
    } while (v);
    while (n)
        probe_sb_putc(sb, tmp[--n]);
}

void probe_sb_pad(probe_sb *sb, size_t column)
{
    while (sb->len < column && !sb->overflow)
        probe_sb_putc(sb, ' ');
}

/* Device path node types and subtypes, UEFI 2.10 section 10.3. */
#define DP_TYPE_MESSAGING 0x03u
#define DP_TYPE_END 0x7Fu
#define DP_SUB_END_ENTIRE 0xFFu
#define DP_MSG_ATAPI 0x01u
#define DP_MSG_SCSI 0x02u
#define DP_MSG_USB 0x05u
#define DP_MSG_USB_CLASS 0x0Fu
#define DP_MSG_SATA 0x12u
#define DP_MSG_NVME 0x17u
#define DP_MSG_UFS 0x19u
#define DP_MSG_SD 0x1Au
#define DP_MSG_EMMC 0x1Du

enum probe_dp_class probe_dp_classify(const uint8_t *p, size_t max_len)
{
    size_t off = 0;
    unsigned nodes = 0;
    int usb = 0;
    int other_storage = 0;

    if (p == NULL)
        return PROBE_DP_MALFORMED;

    for (;;) {
        if (nodes >= PROBE_DP_MAX_NODES || max_len - off < 4)
            return PROBE_DP_MALFORMED;
        uint8_t type = p[off];
        uint8_t sub = p[off + 1];
        size_t len = probe_le16(p + off + 2);
        if (len < 4 || len > max_len - off || (type & 0x80))
            return PROBE_DP_MALFORMED;
        nodes++;

        if (type == DP_TYPE_END) {
            /* A device handle's path is a single instance; an end-of-instance
             * node (subtype 0x01) means something unexpected. */
            if (sub != DP_SUB_END_ENTIRE || len != 4)
                return PROBE_DP_MALFORMED;
            break;
        }

        if (type == DP_TYPE_MESSAGING) {
            switch (sub) {
            case DP_MSG_USB:
                if (len != 6)
                    return PROBE_DP_MALFORMED;
                usb = 1;
                break;
            case DP_MSG_USB_CLASS:
                if (len != 11)
                    return PROBE_DP_MALFORMED;
                usb = 1;
                break;
            case DP_MSG_ATAPI:
            case DP_MSG_SCSI:
            case DP_MSG_SATA:
            case DP_MSG_NVME:
            case DP_MSG_UFS:
            case DP_MSG_SD:
            case DP_MSG_EMMC:
                other_storage = 1;
                break;
            default:
                break;
            }
        }
        off += len;
    }

    if (usb && other_storage)
        return PROBE_DP_AMBIGUOUS;
    return usb ? PROBE_DP_USB : PROBE_DP_NO_USB;
}

const char *probe_dp_class_name(enum probe_dp_class c)
{
    switch (c) {
    case PROBE_DP_NO_USB:
        return "NOT USB";
    case PROBE_DP_USB:
        return "USB";
    case PROBE_DP_AMBIGUOUS:
        return "AMBIGUOUS";
    default:
        return "MALFORMED";
    }
}

int probe_memmap_get(const probe_memmap *m, size_t i, uint32_t *type, uint64_t *phys,
                     uint64_t *virt, uint64_t *pages, uint64_t *attr)
{
    if (m == NULL || m->base == NULL || i >= m->count)
        return -1;
    const uint8_t *d = m->base + i * m->desc_size;
    if (type)
        *type = probe_le32(d);
    if (phys)
        *phys = probe_le64(d + 8);
    if (virt)
        *virt = probe_le64(d + 16);
    if (pages)
        *pages = probe_le64(d + 24);
    if (attr)
        *attr = probe_le64(d + 32);
    return 0;
}

int probe_memmap_init(probe_memmap *m, const void *map, size_t map_size, size_t buf_len,
                      size_t desc_size)
{
    m->base = NULL;
    m->desc_size = 0;
    m->count = 0;

    if (map == NULL || map_size == 0)
        return PROBE_MM_BAD_ARGS;
    if (desc_size < PROBE_MEMMAP_MIN_DESC_SIZE || desc_size > PROBE_MEMMAP_MAX_DESC_SIZE ||
        (desc_size & 7))
        return PROBE_MM_BAD_DESC_SIZE;
    if (map_size > buf_len || map_size % desc_size)
        return PROBE_MM_BAD_MAP_SIZE;
    size_t count = map_size / desc_size;
    if (count > PROBE_MEMMAP_MAX_DESC)
        return PROBE_MM_TOO_MANY;

    probe_memmap tmp = {map, desc_size, count};
    for (size_t i = 0; i < count; i++) {
        uint64_t phys, pages;
        probe_memmap_get(&tmp, i, NULL, &phys, NULL, &pages, NULL);
        if (pages == 0 || pages > (UINT64_MAX >> 12) || (phys & 0xFFF))
            return PROBE_MM_BAD_DESC;
        if (phys + (pages << 12) < phys)
            return PROBE_MM_BAD_DESC;
    }
    *m = tmp;
    return PROBE_MM_OK;
}

int probe_memmap_overlaps_other(const probe_memmap *m, uint64_t addr, uint64_t len,
                                uint32_t allowed_mask)
{
    uint64_t end;

    if (m == NULL || len == 0)
        return 0;
    end = addr + len;
    if (end < addr)
        end = UINT64_MAX; /* a wrapping range reaches the top: every descriptor above addr overlaps */
    for (size_t i = 0; i < m->count; i++) {
        uint32_t type;
        uint64_t phys, pages, dend;

        probe_memmap_get(m, i, &type, &phys, NULL, &pages, NULL);
        if (type < 32 && ((allowed_mask >> type) & 1u))
            continue;
        dend = phys + (pages << 12);
        if (phys < end && dend > addr)
            return 1;
    }
    return 0;
}

int probe_memmap_covers(const probe_memmap *m, uint64_t addr, uint64_t len, uint32_t allowed_mask)
{
    if (m == NULL || m->count == 0 || len == 0)
        return 0;
    uint64_t end = addr + len;
    if (end < addr)
        return 0;

    /* UEFI forbids overlapping descriptors, but a map that has them is not
     * trusted to say which side is right: any descriptor of a type outside the
     * mask that touches the range makes the whole range unprovable, even when
     * another descriptor claims the same bytes with an allowed type. */
    if (probe_memmap_overlaps_other(m, addr, len, allowed_mask))
        return 0;

    uint64_t cur = addr;
    for (unsigned hop = 0; hop < PROBE_MEMMAP_MAX_HOPS; hop++) {
        int found = 0;
        for (size_t i = 0; i < m->count; i++) {
            uint32_t type;
            uint64_t phys, pages;
            probe_memmap_get(m, i, &type, &phys, NULL, &pages, NULL);
            if (type >= 32 || !((allowed_mask >> type) & 1u))
                continue;
            uint64_t dend = phys + (pages << 12);
            if (cur >= phys && cur < dend) {
                cur = dend;
                found = 1;
                break;
            }
        }
        if (!found)
            return 0;
        if (cur >= end)
            return 1;
    }
    return 0;
}

int probe_memmap_type_at(const probe_memmap *m, uint64_t addr)
{
    int type_found = -1;

    for (size_t i = 0; m != NULL && i < m->count; i++) {
        uint32_t type;
        uint64_t phys, pages;
        probe_memmap_get(m, i, &type, &phys, NULL, &pages, NULL);
        if (addr >= phys && addr - phys < (pages << 12)) {
            /* Two descriptors that claim the address with different types: no answer. */
            if (type_found >= 0 && type_found != (int)type)
                return -1;
            type_found = (int)type;
        }
    }
    return type_found;
}

int probe_memmap_ram_bounds(const probe_memmap *m, uint64_t *lo, uint64_t *hi)
{
    int any = 0;
    uint64_t low = 0;
    uint64_t high = 0;
    for (size_t i = 0; m != NULL && i < m->count; i++) {
        uint32_t type;
        uint64_t phys, pages;
        probe_memmap_get(m, i, &type, &phys, NULL, &pages, NULL);
        if (type >= 32 || !((PROBE_RAM_TYPES_MASK >> type) & 1u))
            continue;
        uint64_t end = phys + (pages << 12);
        if (!any || phys < low)
            low = phys;
        if (!any || end > high)
            high = end;
        any = 1;
    }
    if (any) {
        *lo = low;
        *hi = high;
    }
    return any;
}

const char *probe_memtype_name(uint32_t type)
{
    static const char *const names[] = {
        "RESERVED",     "LDR CODE", "LDR DATA", "BS CODE",   "BS DATA",
        "RT CODE",      "RT DATA",  "CONV",     "UNUSABLE",  "ACPI RECLAIM",
        "ACPI NVS",     "MMIO",     "MMIO PORT", "PAL CODE", "PERSISTENT",
    };
    return type < sizeof names / sizeof names[0] ? names[type] : "UNKNOWN";
}

uint64_t probe_cache_walk(uint64_t addr, uint64_t len, uint64_t line, probe_cache_op op)
{
    uint64_t last, p, n;

    if (op == NULL || len == 0 || len > PROBE_CACHE_MAX_BYTES || line < 16 || line > 4096 ||
        (line & (line - 1)))
        return 0;
    last = len - 1 > UINT64_MAX - addr ? UINT64_MAX : addr + (len - 1);
    p = addr & ~(line - 1);
    n = ((last & ~(line - 1)) - p) / line + 1;
    for (uint64_t i = 0; i < n; i++, p += line)
        op(p);
    return n;
}

int probe_currentel_decode(uint64_t raw)
{
    if (raw & ~(uint64_t)0xC)
        return -1;
    return (int)(raw >> 2);
}

enum probe_psci_conduit probe_psci_conduit_from_fadt(unsigned fadt_revision, size_t fadt_length,
                                                     uint16_t arm_boot_arch)
{
    if (fadt_revision < 5 || fadt_length < 131)
        return PROBE_PSCI_NONE;
    if (!(arm_boot_arch & 1u))
        return PROBE_PSCI_NONE;
    if (arm_boot_arch & 0xFFFCu)
        return PROBE_PSCI_NONE;
    return (arm_boot_arch & 2u) ? PROBE_PSCI_HVC : PROBE_PSCI_SMC;
}

const char *probe_psci_conduit_name(enum probe_psci_conduit c)
{
    switch (c) {
    case PROBE_PSCI_SMC:
        return "SMC";
    case PROBE_PSCI_HVC:
        return "HVC";
    default:
        return "NONE";
    }
}

int probe_ebs_allowed(int have_framebuffer, int have_layout, int memmap_ok)
{
    return have_framebuffer && have_layout && memmap_ok;
}

int probe_ebs_retry(uint64_t status, unsigned attempt, unsigned max_attempts)
{
    /* Compared as attempt < max - 1 so that a huge attempt counter cannot
     * wrap to zero and authorize an unbounded retry loop. */
    return status == EFI_INVALID_PARAMETER && max_attempts != 0 && attempt < max_attempts - 1;
}
