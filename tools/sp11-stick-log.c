/* SPDX-License-Identifier: MIT */
/* See sp11-stick-log.h. */
#include "sp11-stick-log.h"

#if SNAP_PLATFORM_SP11

static uint32_t get_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t get_le64(const uint8_t *p)
{
    return (uint64_t)get_le32(p) | ((uint64_t)get_le32(p + 4) << 32);
}

static uint32_t get_le16(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8);
}

static void put_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static void put_le64(uint8_t *p, uint64_t v)
{
    put_le32(p, (uint32_t)v);
    put_le32(p + 4, (uint32_t)(v >> 32));
}

static void put_le16(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static int bytes_equal(const uint8_t *a, const uint8_t *b, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++)
        if (a[i] != b[i])
            return 0;
    return 1;
}

static int all_zero(const uint8_t *p, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++)
        if (p[i])
            return 0;
    return 1;
}

static void copy_bytes(uint8_t *dst, const uint8_t *src, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++)
        dst[i] = src[i];
}

static const uint8_t map_magic[8] = {'S', 'N', 'A', 'P', 'L', 'M', 'A', 'P'};
static const uint8_t header_magic[8] = {'S', 'N', 'A', 'P', 'L', 'O', 'G', '1'};
static const uint8_t sector_magic[4] = {'S', 'L', 'G', '1'};

uint32_t slog_crc32(uint32_t crc, const uint8_t *data, uint32_t length)
{
    uint32_t i, bit;
    crc = ~crc;
    for (i = 0; i < length; i++) {
        crc ^= data[i];
        for (bit = 0; bit < 8; bit++)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

/* Map layout: 0x00 magic, 0x08 version, 0x0C map_bytes, 0x10 nonce, 0x18 sector_size, 0x1C capacity, 0x20 partition_lba,
 * 0x24 cluster_sectors, 0x28 data_lba, 0x2C region_sectors, 0x30 run_count, 0x34 flags, 0x38 four (start, sectors) pairs,
 * 0x58 reserved (36 bytes), 0x7C CRC-32 of bytes 0 to 0x7B. */
enum slog_status slog_map_parse(const uint8_t *raw, uint32_t length, struct slog_map *m)
{
    uint32_t i, cs, runs;
    if (!raw || !m)
        return SLOG_E_ARG;
    if (length != SLOG_MAP_BYTES)
        return SLOG_E_MAP_SIZE;
    if (!bytes_equal(raw, map_magic, 8))
        return SLOG_E_MAP_MAGIC;
    if (get_le32(raw + 8) != SLOG_VERSION || get_le32(raw + 12) != SLOG_MAP_BYTES)
        return SLOG_E_MAP_VERSION;
    if (get_le32(raw + 0x7C) != slog_crc32(0, raw, 0x7C))
        return SLOG_E_MAP_CRC;
    if (get_le32(raw + 0x34) != 0 || !all_zero(raw + 0x58, 36))
        return SLOG_E_MAP_RESERVED;
    runs = get_le32(raw + 0x30);
    if (runs < 1 || runs > SLOG_MAX_RUNS)
        return SLOG_E_MAP_RUNS;
    for (i = runs; i < SLOG_MAX_RUNS; i++)
        if (get_le32(raw + 0x38 + 8 * i) || get_le32(raw + 0x3C + 8 * i))
            return SLOG_E_MAP_RUNS;
    m->nonce = get_le64(raw + 0x10);
    m->sector_size = get_le32(raw + 0x18);
    m->capacity_sectors = get_le32(raw + 0x1C);
    m->partition_lba = get_le32(raw + 0x20);
    m->cluster_sectors = get_le32(raw + 0x24);
    m->data_lba = get_le32(raw + 0x28);
    m->region_sectors = get_le32(raw + 0x2C);
    m->run_count = runs;
    for (i = 0; i < SLOG_MAX_RUNS; i++) {
        m->run_start[i] = get_le32(raw + 0x38 + 8 * i);
        m->run_sectors[i] = get_le32(raw + 0x3C + 8 * i);
    }
    if (m->nonce == 0)
        return SLOG_E_MAP_NONCE;
    /* Version 1 is one contiguous run. A fragmented file is refused here and by the Mac tool, never written around. */
    if (m->run_count != 1)
        return SLOG_E_MAP_RUNS;
    if (m->sector_size != SLOG_SECTOR_BYTES || m->region_sectors < SLOG_MIN_REGION_SECTORS ||
        m->region_sectors > SLOG_MAX_REGION_SECTORS || m->run_sectors[0] != m->region_sectors)
        return SLOG_E_MAP_GEOMETRY;
    if (m->capacity_sectors < 1 || m->capacity_sectors == 0xFFFFFFFFu ||
        (uint64_t)m->run_start[0] + m->run_sectors[0] > m->capacity_sectors)
        return SLOG_E_MAP_GEOMETRY;
    cs = m->cluster_sectors;
    if (cs < 1 || cs > 128 || (cs & (cs - 1)) || m->data_lba < m->partition_lba || m->run_start[0] < m->data_lba ||
        (m->run_start[0] - m->data_lba) % cs)
        return SLOG_E_MAP_GEOMETRY;
    return SLOG_OK;
}

void slog_map_encode(uint8_t out[SLOG_MAP_BYTES], const struct slog_map *m)
{
    uint32_t i;
    for (i = 0; i < SLOG_MAP_BYTES; i++)
        out[i] = 0;
    copy_bytes(out, map_magic, 8);
    put_le32(out + 8, SLOG_VERSION);
    put_le32(out + 12, SLOG_MAP_BYTES);
    put_le64(out + 0x10, m->nonce);
    put_le32(out + 0x18, m->sector_size);
    put_le32(out + 0x1C, m->capacity_sectors);
    put_le32(out + 0x20, m->partition_lba);
    put_le32(out + 0x24, m->cluster_sectors);
    put_le32(out + 0x28, m->data_lba);
    put_le32(out + 0x2C, m->region_sectors);
    put_le32(out + 0x30, m->run_count);
    for (i = 0; i < SLOG_MAX_RUNS; i++) {
        put_le32(out + 0x38 + 8 * i, m->run_start[i]);
        put_le32(out + 0x3C + 8 * i, m->run_sectors[i]);
    }
    put_le32(out + 0x7C, slog_crc32(0, out, 0x7C));
}

/* Header layout: 0x00 magic, 0x08 version, 0x0C header_bytes, 0x10 nonce, 0x18 sector_size, 0x1C region_sectors,
 * 0x20 start_lba, 0x24 partition_lba, 0x28 capacity_sectors, 0x2C flags, 0x30 reserved, 0x3C CRC-32 of bytes 0 to 0x3B. */
enum slog_status slog_header_check(const uint8_t *s, const struct slog_map *m)
{
    if (!s || !m)
        return SLOG_E_ARG;
    if (!bytes_equal(s, header_magic, 8) || get_le32(s + 8) != SLOG_VERSION || get_le32(s + 12) != SLOG_HEADER_BYTES)
        return SLOG_E_HEADER;
    if (get_le32(s + 0x3C) != slog_crc32(0, s, 0x3C) || get_le32(s + 0x2C) != 0 || !all_zero(s + 0x30, 12) ||
        !all_zero(s + SLOG_HEADER_BYTES, SLOG_SECTOR_BYTES - SLOG_HEADER_BYTES))
        return SLOG_E_HEADER;
    /* The header names the stick's geometry again on purpose: a stale map that points at another copy of this file, or at
     * the same sector of another stick, disagrees with it. */
    if (get_le64(s + 0x10) != m->nonce || get_le32(s + 0x18) != m->sector_size ||
        get_le32(s + 0x1C) != m->region_sectors || get_le32(s + 0x20) != m->run_start[0] ||
        get_le32(s + 0x24) != m->partition_lba || get_le32(s + 0x28) != m->capacity_sectors)
        return SLOG_E_HEADER;
    return SLOG_OK;
}

/* Log sector layout: 0x00 "SLG1", 0x04 index, 0x08 nonce, 0x10 boot_id, 0x14 seq, 0x18 used (16 bits), 0x1A flags (16 bits),
 * 0x1C CRC-32 of bytes 0 to 0x1B and payload[0..used), 0x20 payload (480 bytes). */
void slog_sector_encode(uint8_t out[SLOG_SECTOR_BYTES], uint64_t nonce, uint32_t index, uint32_t boot_id, uint32_t seq,
                        const uint8_t *payload, uint32_t used)
{
    uint32_t i;
    if (used > SLOG_PAYLOAD_BYTES || (used && !payload))
        used = 0;
    for (i = 0; i < SLOG_SECTOR_BYTES; i++)
        out[i] = 0;
    copy_bytes(out, sector_magic, 4);
    put_le32(out + 4, index);
    put_le64(out + 8, nonce);
    put_le32(out + 0x10, boot_id);
    put_le32(out + 0x14, seq);
    put_le16(out + 0x18, used);
    put_le16(out + 0x1A, 0);
    if (used)
        copy_bytes(out + SLOG_TAG_BYTES, payload, used);
    put_le32(out + 0x1C, slog_crc32(slog_crc32(0, out, 0x1C), out + SLOG_TAG_BYTES, used));
}

void slog_sector_decode(const uint8_t *s, uint32_t index, uint64_t nonce, struct slog_sector_info *info)
{
    info->owned = bytes_equal(s, sector_magic, 4) && get_le32(s + 4) == index && get_le64(s + 8) == nonce;
    info->boot_id = get_le32(s + 0x10);
    info->seq = get_le32(s + 0x14);
    info->used = get_le16(s + 0x18);
    info->valid = info->owned && info->used <= SLOG_PAYLOAD_BYTES &&
                  get_le32(s + 0x1C) == slog_crc32(slog_crc32(0, s, 0x1C), s + SLOG_TAG_BYTES, info->used);
}

enum slog_status slog_scan(const uint8_t *region, uint32_t sectors, uint64_t nonce, uint32_t *next_index,
                           uint32_t *next_boot_id)
{
    uint32_t index, newest = 0, newest_boot = 0, newest_seq = 0, top_boot = 0;
    struct slog_sector_info info;
    if (!region || !next_index || !next_boot_id || sectors < SLOG_MIN_REGION_SECTORS)
        return SLOG_E_ARG;
    for (index = 1; index < sectors; index++) {
        slog_sector_decode(region + (uint64_t)index * SLOG_SECTOR_BYTES, index, nonce, &info);
        if (!info.owned)
            return SLOG_E_NOT_OWNED;
        if (!info.valid || !info.boot_id)
            continue;
        if (info.boot_id > top_boot)
            top_boot = info.boot_id;
        if (info.boot_id > newest_boot || (info.boot_id == newest_boot && info.seq >= newest_seq)) {
            newest = index;
            newest_boot = info.boot_id;
            newest_seq = info.seq;
        }
    }
    if (top_boot == 0xFFFFFFFFu)
        return SLOG_E_BUDGET;
    *next_boot_id = top_boot + 1;
    *next_index = !newest || newest + 1 >= sectors ? 1 : newest + 1;
    return SLOG_OK;
}

static enum slog_status fail(struct slog *l, enum slog_status why, enum scsi_status st)
{
    if (!l->error) {
        l->error = why;
        l->scsi = st;
    }
    l->armed = 0;
    return l->error;
}

enum slog_status slog_open(struct slog *l, struct scsi_dev *dev, const uint8_t *map_raw, uint32_t map_length,
                           uint32_t boot_id, uint32_t first_index)
{
    uint8_t *raw = (uint8_t *)l;
    struct slog_map m;
    enum slog_status st;
    enum scsi_status sst;
    uint32_t i;
    if (!l)
        return SLOG_E_ARG;
    for (i = 0; i < sizeof(*l); i++)
        raw[i] = 0;
    if (!dev || !map_raw)
        return SLOG_E_ARG;
    st = slog_map_parse(map_raw, map_length, &m);
    if (st != SLOG_OK)
        return st;
    if (!boot_id || first_index < 1 || first_index >= m.region_sectors)
        return SLOG_E_ARG;
    /* The Mac wrote the capacity it believes the stick has; the stick says what it has. A swapped stick differs. */
    if (dev->block_len != SLOG_SECTOR_BYTES || (uint64_t)dev->last_lba + 1u != m.capacity_sectors)
        return SLOG_E_CAPACITY;
    l->dev = dev;
    l->map = m;
    l->boot_id = boot_id;
    l->index = first_index;
    l->flushed_used = 0xFFFFFFFFu;
    sst = scsi_read10(dev, m.run_start[0], 1, l->scratch);
    l->stats.reads++;
    if (sst != SCSI_OK) {
        l->error = SLOG_E_DEVICE;
        l->scsi = sst;
        return SLOG_E_DEVICE;
    }
    if (slog_header_check(l->scratch, &m) != SLOG_OK) {
        l->error = SLOG_E_HEADER;
        return SLOG_E_HEADER;
    }
    l->armed = 1;
    return SLOG_OK;
}

/* The only place a write is issued. `index` is a region index, the LBA is derived here, and a sector is overwritten only
 * if the copy on the stick still carries our tag. */
static enum slog_status slog_put(struct slog *l, uint32_t index, const uint8_t *sector)
{
    struct slog_sector_info info;
    enum scsi_status st;
    uint32_t lba;
    if (!l->armed)
        return l->error ? l->error : SLOG_E_NOT_ARMED;
    if (index < 1 || index >= l->map.region_sectors)
        return fail(l, SLOG_E_BOUNDS, SCSI_OK);
    if (l->stats.writes >= SLOG_WRITE_BUDGET)
        return fail(l, SLOG_E_BUDGET, SCSI_OK);
    lba = l->map.run_start[0] + index;
    if (l->owned_index != index) {
        st = scsi_read10(l->dev, lba, 1, l->scratch);
        l->stats.reads++;
        if (st != SCSI_OK)
            return fail(l, SLOG_E_DEVICE, st);
        slog_sector_decode(l->scratch, index, l->map.nonce, &info);
        if (!info.owned)
            return fail(l, SLOG_E_NOT_OWNED, SCSI_OK);
        l->owned_index = index;
    }
    st = scsi_write10(l->dev, lba, 1, sector, 1);
    if (st == SCSI_PROTECTED)
        return fail(l, SLOG_E_PROTECTED, st);
    if (st != SCSI_OK)
        return fail(l, SLOG_E_DEVICE, st);
    l->stats.writes++;
    l->dirty = 1;
    /* FUA and a clean status say the device took the write, not that it is the data we meant. Read it back. */
    st = scsi_read10(l->dev, lba, 1, l->scratch);
    l->stats.reads++;
    if (st != SCSI_OK)
        return fail(l, SLOG_E_DEVICE, st);
    if (!bytes_equal(l->scratch, sector, SLOG_SECTOR_BYTES))
        return fail(l, SLOG_E_READBACK, SCSI_OK);
    return SLOG_OK;
}

static enum slog_status slog_write_current(struct slog *l)
{
    enum slog_status st;
    slog_sector_encode(l->sector, l->map.nonce, l->index, l->boot_id, l->seq, l->payload, l->used);
    st = slog_put(l, l->index, l->sector);
    if (st != SLOG_OK)
        return st;
    l->flushed_used = l->used;
    if (l->used == SLOG_PAYLOAD_BYTES) {
        /* Full: the next sector, wrapping from the last log sector to the first (index 0 is the header, never written). */
        l->index = l->index + 1 >= l->map.region_sectors ? 1 : l->index + 1;
        l->seq++;
        l->used = 0;
        l->flushed_used = 0xFFFFFFFFu;
        l->stats.sectors_started++;
    }
    return SLOG_OK;
}

enum slog_status slog_append(struct slog *l, const uint8_t *data, uint32_t length)
{
    enum slog_status st;
    uint32_t n;
    if (!l || (length && !data))
        return SLOG_E_ARG;
    if (!l->armed)
        return l->error ? l->error : SLOG_E_NOT_ARMED;
    while (length) {
        n = SLOG_PAYLOAD_BYTES - l->used;
        if (n > length)
            n = length;
        copy_bytes(l->payload + l->used, data, n);
        l->used += n;
        data += n;
        length -= n;
        if (l->used == SLOG_PAYLOAD_BYTES) {
            st = slog_write_current(l);
            if (st != SLOG_OK)
                return st;
        }
    }
    return SLOG_OK;
}

enum slog_status slog_flush(struct slog *l)
{
    enum slog_status st;
    enum scsi_status sst;
    if (!l)
        return SLOG_E_ARG;
    if (!l->armed)
        return l->error ? l->error : SLOG_E_NOT_ARMED;
    if (l->used && l->used != l->flushed_used) {
        st = slog_write_current(l);
        if (st != SLOG_OK)
            return st;
    }
    if (l->dirty) {
        sst = scsi_sync10(l->dev);
        l->stats.syncs++;
        if (sst == SCSI_CHECK && l->dev->sense.valid && l->dev->sense.key == SCSI_KEY_ILLEGAL_REQUEST)
            l->stats.sync_refused++;      /* no volatile cache to flush: the FUA writes were already stable */
        else if (sst != SCSI_OK)
            return fail(l, SLOG_E_DEVICE, sst);
        l->dirty = 0;
    }
    return SLOG_OK;
}

const char *slog_status_name(enum slog_status s)
{
    switch (s) {
    case SLOG_OK: return "OK";
    case SLOG_E_ARG: return "BAD ARGUMENT";
    case SLOG_E_MAP_SIZE: return "MAP SIZE";
    case SLOG_E_MAP_MAGIC: return "MAP MAGIC";
    case SLOG_E_MAP_VERSION: return "MAP VERSION";
    case SLOG_E_MAP_CRC: return "MAP CRC";
    case SLOG_E_MAP_RESERVED: return "MAP RESERVED";
    case SLOG_E_MAP_NONCE: return "MAP NONCE";
    case SLOG_E_MAP_RUNS: return "MAP RUNS";
    case SLOG_E_MAP_GEOMETRY: return "MAP GEOMETRY";
    case SLOG_E_CAPACITY: return "CAPACITY MISMATCH";
    case SLOG_E_DEVICE: return "DEVICE ERROR";
    case SLOG_E_PROTECTED: return "WRITE PROTECTED";
    case SLOG_E_HEADER: return "HEADER MISMATCH";
    case SLOG_E_NOT_OWNED: return "SECTOR NOT OURS";
    case SLOG_E_BOUNDS: return "OUT OF REGION";
    case SLOG_E_READBACK: return "READBACK MISMATCH";
    case SLOG_E_BUDGET: return "WRITE BUDGET";
    case SLOG_E_NOT_ARMED: return "NOT ARMED";
    }
    return "UNKNOWN";
}

#endif /* SNAP_PLATFORM_SP11 */
