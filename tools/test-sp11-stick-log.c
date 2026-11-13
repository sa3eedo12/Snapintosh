/* SPDX-License-Identifier: MIT */
/* tools/sp11-stick-log.{h,c} (the stick log region, its map and the guarded writer) over tools/sp11-stick-bot.c and the fake
 * device of tools/sp11-stick-fakedev.h, natively:
 *
 *   cc -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -DSNAP_PLATFORM_SP11=1 -Itools \
 *      tools/test-sp11-stick-log.c tools/sp11-stick-log.c tools/sp11-stick-bot.c -o build/sp11-stick-log/test-sp11-stick-log
 *
 * The device in these tests is laid out like a boot stick: a "file system" area (sectors 0 to 8191), the log region (8192
 * to 10239, header sector first), and "other files" after it. The fake logs every write and a test names the sectors that
 * must never be touched; a write to one of them is a violation and fails the test. What it proves: the map parser rejects
 * every field a bad map can get wrong; a wrong or stale map (pointing into the file system, off by a sector, a forged
 * header over foreign data, a region with one foreign sector in the middle) ends in a refusal with no write to anything the
 * region does not own; every device failure disarms the writer for good; the ring wraps without touching the header;
 * the write budget stops a runaway. With --image it runs the whole flow against a disk image made by the Mac tool, and
 * with --stdin it answers one command per line for the Python oracles. Nothing here runs on hardware. */
#include <inttypes.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "sp11-stick-fakedev.h"
#include "sp11-stick-log.h"

static unsigned long checks;
#define CHECK(x) do { checks++; if (!(x)) { \
    fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #x); exit(1); } } while (0)

#define DEV_SECTORS 16384u
#define REGION_START 8192u
#define REGION_SECTORS 2048u
#define NONCE UINT64_C(0x1122334455667788)

static void put32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
static void put64(uint8_t *p, uint64_t v) { put32(p, (uint32_t)v); put32(p + 4, (uint32_t)(v >> 32)); }
static uint32_t get32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }

/* The header sector, built here from the layout in the design document, not by the code under test. */
static void build_header(uint8_t *s, uint64_t nonce, uint32_t region, uint32_t start, uint32_t partition, uint32_t capacity)
{
    memset(s, 0, 512);
    memcpy(s, "SNAPLOG1", 8);
    put32(s + 8, 1);
    put32(s + 12, 64);
    put64(s + 16, nonce);
    put32(s + 24, 512);
    put32(s + 28, region);
    put32(s + 32, start);
    put32(s + 36, partition);
    put32(s + 40, capacity);
    put32(s + 60, slog_crc32(0, s, 60));
}

static struct slog_map good_map(void)
{
    struct slog_map m;
    memset(&m, 0, sizeof(m));
    m.nonce = NONCE;
    m.sector_size = 512;
    m.capacity_sectors = DEV_SECTORS;
    m.partition_lba = 2048;
    m.cluster_sectors = 8;
    m.data_lba = 2304;
    m.region_sectors = REGION_SECTORS;
    m.run_count = 1;
    m.run_start[0] = REGION_START;
    m.run_sectors[0] = REGION_SECTORS;
    return m;
}

/* A stick laid out like a boot stick. Sectors outside the log sectors are forbidden to every write. */
static void make_stick(struct fake_stick *f)
{
    uint8_t sector[512];
    fake_init(f, DEV_SECTORS, 0);
    memset(f->mem, 0xFA, (size_t)REGION_START * 512);
    memset(f->mem + (size_t)(REGION_START + REGION_SECTORS) * 512, 0xBB, (size_t)(DEV_SECTORS - REGION_START - REGION_SECTORS) * 512);
    build_header(sector, NONCE, REGION_SECTORS, REGION_START, 2048, DEV_SECTORS);
    memcpy(f->mem + (size_t)REGION_START * 512, sector, 512);
    for (uint32_t i = 1; i < REGION_SECTORS; i++) {
        slog_sector_encode(sector, NONCE, i, 0, 0, NULL, 0);
        memcpy(f->mem + (size_t)(REGION_START + i) * 512, sector, 512);
    }
    fake_forbid(f, 0, REGION_START);                                   /* the file system area and the header sector */
    fake_forbid(f, REGION_START + REGION_SECTORS, DEV_SECTORS - 1);    /* the other files */
}

static void map_bytes(uint8_t *raw, const struct slog_map *m) { slog_map_encode(raw, m); }

static struct slog *new_log(void)
{
    struct slog *l = malloc(sizeof(*l));
    if (!l)
        abort();
    return l;
}

static void t_crc(void)
{
    const uint8_t digits[9] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    CHECK(slog_crc32(0, digits, 9) == 0xCBF43926u);                       /* the standard check value */
    CHECK(slog_crc32(slog_crc32(0, digits, 4), digits + 4, 5) == 0xCBF43926u);
    CHECK(slog_crc32(0, digits, 0) == 0);
}

struct map_case {
    const char *name;
    unsigned offset;
    uint32_t bytes;            /* how many bytes to overwrite (1, 4 or 8) */
    uint64_t value;
    int fix_crc;
    enum slog_status want;
};

static const struct map_case map_cases[] = {
    {"magic", 0, 1, 'X', 1, SLOG_E_MAP_MAGIC},
    {"version", 8, 4, 2, 1, SLOG_E_MAP_VERSION},
    {"map bytes", 12, 4, 129, 1, SLOG_E_MAP_VERSION},
    {"crc (nonce bit flipped, crc stale)", 16, 1, 0x89, 0, SLOG_E_MAP_CRC},
    {"flags", 0x34, 4, 1, 1, SLOG_E_MAP_RESERVED},
    {"reserved byte", 0x60, 1, 1, 1, SLOG_E_MAP_RESERVED},
    {"nonce zero", 16, 8, 0, 1, SLOG_E_MAP_NONCE},
    {"sector size 4096", 0x18, 4, 4096, 1, SLOG_E_MAP_GEOMETRY},
    {"sector size 0", 0x18, 4, 0, 1, SLOG_E_MAP_GEOMETRY},
    {"capacity below the region end", 0x1C, 4, REGION_START + REGION_SECTORS - 1, 1, SLOG_E_MAP_GEOMETRY},
    {"capacity 0xFFFFFFFF", 0x1C, 4, 0xFFFFFFFFu, 1, SLOG_E_MAP_GEOMETRY},
    {"capacity 0", 0x1C, 4, 0, 1, SLOG_E_MAP_GEOMETRY},
    {"cluster sectors 3", 0x24, 4, 3, 1, SLOG_E_MAP_GEOMETRY},
    {"cluster sectors 0", 0x24, 4, 0, 1, SLOG_E_MAP_GEOMETRY},
    {"cluster sectors 256", 0x24, 4, 256, 1, SLOG_E_MAP_GEOMETRY},
    {"data lba above the region", 0x28, 4, REGION_START + 8, 1, SLOG_E_MAP_GEOMETRY},
    {"data lba below the partition", 0x28, 4, 2047, 1, SLOG_E_MAP_GEOMETRY},
    {"region off by one sector (start +1)", 0x38, 4, REGION_START + 1, 1, SLOG_E_MAP_GEOMETRY},
    {"region off by one sector (start -1)", 0x38, 4, REGION_START - 1, 1, SLOG_E_MAP_GEOMETRY},
    {"region size 1", 0x2C, 4, 1, 1, SLOG_E_MAP_GEOMETRY},
    {"region size 65537", 0x2C, 4, 0x10001, 1, SLOG_E_MAP_GEOMETRY},
    {"run sectors differ from the region", 0x3C, 4, REGION_SECTORS - 1, 1, SLOG_E_MAP_GEOMETRY},
    {"run count 0", 0x30, 4, 0, 1, SLOG_E_MAP_RUNS},
    {"run count 5", 0x30, 4, 5, 1, SLOG_E_MAP_RUNS},
    {"run count 2 (v1 refuses fragments)", 0x30, 4, 2, 1, SLOG_E_MAP_RUNS},
    {"unused run entry set", 0x48, 4, 7, 1, SLOG_E_MAP_RUNS},
};

static void t_map(void)
{
    struct slog_map m = good_map(), parsed;
    uint8_t raw[SLOG_MAP_BYTES];
    map_bytes(raw, &m);
    CHECK(slog_map_parse(raw, 128, &parsed) == SLOG_OK);
    CHECK(parsed.nonce == NONCE && parsed.capacity_sectors == DEV_SECTORS && parsed.partition_lba == 2048 &&
          parsed.cluster_sectors == 8 && parsed.data_lba == 2304 && parsed.region_sectors == REGION_SECTORS &&
          parsed.run_count == 1 && parsed.run_start[0] == REGION_START && parsed.run_sectors[0] == REGION_SECTORS);
    CHECK(slog_map_parse(raw, 127, &parsed) == SLOG_E_MAP_SIZE);
    CHECK(slog_map_parse(raw, 129, &parsed) == SLOG_E_MAP_SIZE);
    CHECK(slog_map_parse(raw, 0, &parsed) == SLOG_E_MAP_SIZE);
    CHECK(slog_map_parse(NULL, 128, &parsed) == SLOG_E_ARG && slog_map_parse(raw, 128, NULL) == SLOG_E_ARG);
    for (unsigned i = 0; i < sizeof(map_cases) / sizeof(map_cases[0]); i++) {
        const struct map_case *c = &map_cases[i];
        uint8_t copy[SLOG_MAP_BYTES];
        memcpy(copy, raw, sizeof(copy));
        for (uint32_t k = 0; k < c->bytes; k++)
            copy[c->offset + k] = (uint8_t)(c->value >> (8 * k));
        if (c->fix_crc)
            put32(copy + 124, slog_crc32(0, copy, 124));
        enum slog_status got = slog_map_parse(copy, 128, &parsed);
        if (got != c->want) {
            fprintf(stderr, "map case '%s': %s (want %s)\n", c->name, slog_status_name(got), slog_status_name(c->want));
            exit(1);
        }
        checks++;
    }
    /* 64-bit arithmetic: the end of this region is 0x100000700, which wraps to 0x700 in 32 bits and would pass as small. */
    m = good_map();
    m.data_lba = m.run_start[0] = 0xFFFFFF00u;
    m.capacity_sectors = 0xFFFFFFFEu;
    map_bytes(raw, &m);
    CHECK(slog_map_parse(raw, 128, &parsed) == SLOG_E_MAP_GEOMETRY);
    /* A geometry the encoder can produce but the parser must still accept: another cluster size and a later region. */
    m = good_map();
    m.cluster_sectors = 128;
    m.data_lba = 2048 + 4;
    m.run_start[0] = m.data_lba + 128 * 40;
    m.capacity_sectors = m.run_start[0] + REGION_SECTORS + 5;
    map_bytes(raw, &m);
    CHECK(slog_map_parse(raw, 128, &parsed) == SLOG_OK);
}

static void t_sector(void)
{
    uint8_t s[512], payload[480];
    struct slog_sector_info info;
    for (int i = 0; i < 480; i++)
        payload[i] = (uint8_t)(i * 3 + 1);
    slog_sector_encode(s, NONCE, 17, 5, 9, payload, 480);
    slog_sector_decode(s, 17, NONCE, &info);
    CHECK(info.owned && info.valid && info.boot_id == 5 && info.seq == 9 && info.used == 480);
    CHECK(!memcmp(s, "SLG1", 4) && get32(s + 4) == 17 && get32(s + 0x10) == 5 && get32(s + 0x14) == 9);
    CHECK(!memcmp(s + 32, payload, 480));
    slog_sector_decode(s, 18, NONCE, &info);
    CHECK(!info.owned && !info.valid);                                  /* the wrong index */
    slog_sector_decode(s, 17, NONCE ^ 1, &info);
    CHECK(!info.owned && !info.valid);                                  /* the wrong nonce */
    s[0] = 'X';
    slog_sector_decode(s, 17, NONCE, &info);
    CHECK(!info.owned);
    s[0] = 'S';
    s[40] ^= 1;                                                          /* a torn payload: still ours, not valid */
    slog_sector_decode(s, 17, NONCE, &info);
    CHECK(info.owned && !info.valid);
    s[40] ^= 1;
    put32(s + 0x18, 481);                                                /* used above 480 */
    slog_sector_decode(s, 17, NONCE, &info);
    CHECK(info.owned && !info.valid);
    slog_sector_encode(s, NONCE, 3, 0, 0, NULL, 0);                     /* a pristine sector is valid and empty */
    slog_sector_decode(s, 3, NONCE, &info);
    CHECK(info.owned && info.valid && info.used == 0 && info.boot_id == 0);
    slog_sector_encode(s, NONCE, 3, 1, 1, payload, 481);                /* an over-long payload is refused, not clipped */
    slog_sector_decode(s, 3, NONCE, &info);
    CHECK(info.valid && info.used == 0);
    memset(s, 0xA5, sizeof(s));                                          /* nothing of the old buffer may leak into a sector */
    slog_sector_encode(s, NONCE, 5, 2, 3, payload, 100);
    CHECK(s[0x1A] == 0 && s[0x1B] == 0);
    for (int i = 32 + 100; i < 512; i++)
        CHECK(s[i] == 0);
}

static void t_header(void)
{
    struct slog_map m = good_map();
    uint8_t h[512], x[512];
    build_header(h, NONCE, REGION_SECTORS, REGION_START, 2048, DEV_SECTORS);
    CHECK(slog_header_check(h, &m) == SLOG_OK);
    static const struct { unsigned off; uint8_t xor_; int fix; } flips[] = {
        {0, 1, 1}, {8, 1, 1}, {12, 1, 1}, {16, 1, 1}, {24, 1, 1}, {28, 1, 1}, {32, 1, 1}, {36, 1, 1}, {40, 1, 1}, {44, 1, 1},
        {48, 1, 1}, {59, 1, 1}, {60, 1, 0}, {64, 1, 0}, {511, 1, 0}, {100, 0x80, 0},
    };
    for (unsigned i = 0; i < sizeof(flips) / sizeof(flips[0]); i++) {
        memcpy(x, h, 512);
        x[flips[i].off] ^= flips[i].xor_;
        if (flips[i].fix)
            put32(x + 60, slog_crc32(0, x, 60));
        CHECK(slog_header_check(x, &m) == SLOG_E_HEADER);
    }
    m.run_start[0] = REGION_START + 1;                                   /* the map and the header disagree */
    CHECK(slog_header_check(h, &m) == SLOG_E_HEADER);
    CHECK(slog_header_check(NULL, &m) == SLOG_E_ARG);
}

static void t_scan(void)
{
    static uint8_t region[REGION_SECTORS * 512];
    uint8_t s[512], payload[8] = "abcdefg";
    uint32_t next, boot;
    for (uint32_t i = 1; i < REGION_SECTORS; i++)
        slog_sector_encode(region + (size_t)i * 512, NONCE, i, 0, 0, NULL, 0);
    CHECK(slog_scan(region, REGION_SECTORS, NONCE, &next, &boot) == SLOG_OK && next == 1 && boot == 1);
    for (uint32_t k = 0; k < 5; k++) {
        slog_sector_encode(s, NONCE, 10 + k, 3, k, payload, 7);
        memcpy(region + (size_t)(10 + k) * 512, s, 512);
    }
    CHECK(slog_scan(region, REGION_SECTORS, NONCE, &next, &boot) == SLOG_OK && next == 15 && boot == 4);
    slog_sector_encode(s, NONCE, 2047, 2, 0, payload, 7);               /* an older boot at the very end of the ring */
    memcpy(region + (size_t)2047 * 512, s, 512);
    CHECK(slog_scan(region, REGION_SECTORS, NONCE, &next, &boot) == SLOG_OK && next == 15 && boot == 4);
    slog_sector_encode(s, NONCE, 2047, 9, 1, payload, 7);               /* the newest sector is the last one: wrap to 1 */
    memcpy(region + (size_t)2047 * 512, s, 512);
    CHECK(slog_scan(region, REGION_SECTORS, NONCE, &next, &boot) == SLOG_OK && next == 1 && boot == 10);
    slog_sector_encode(s, NONCE, 20, 99, 0, payload, 7);                /* torn: ours but its CRC is wrong; ignored */
    s[33] ^= 1;                                                          /* inside the 7 used payload bytes */
    memcpy(region + (size_t)20 * 512, s, 512);
    CHECK(slog_scan(region, REGION_SECTORS, NONCE, &next, &boot) == SLOG_OK && next == 1 && boot == 10);
    slog_sector_encode(s, NONCE, 21, 5, 0, payload, 7);                 /* a foreign sector: the region is not ours */
    s[8] ^= 1;
    memcpy(region + (size_t)21 * 512, s, 512);
    CHECK(slog_scan(region, REGION_SECTORS, NONCE, &next, &boot) == SLOG_E_NOT_OWNED);
    slog_sector_encode(region + (size_t)21 * 512, NONCE, 21, 0xFFFFFFFFu, 0, payload, 7);
    CHECK(slog_scan(region, REGION_SECTORS, NONCE, &next, &boot) == SLOG_E_BUDGET);
    CHECK(slog_scan(NULL, REGION_SECTORS, NONCE, &next, &boot) == SLOG_E_ARG && slog_scan(region, 1, NONCE, &next, &boot) == SLOG_E_ARG);
}

/* Every sector the writer produced, decoded, in write order of (boot_id, seq): the log text. */
static size_t read_log(const uint8_t *mem, uint8_t *text, size_t max)
{
    size_t n = 0;
    uint32_t best_boot = 0, best_seq;
    int used[REGION_SECTORS] = {0};
    for (;;) {
        uint32_t pick = 0;
        best_seq = 0;
        best_boot = 0;
        for (uint32_t i = 1; i < REGION_SECTORS; i++) {
            struct slog_sector_info info;
            slog_sector_decode(mem + (size_t)(REGION_START + i) * 512, i, NONCE, &info);
            if (used[i] || !info.valid || !info.boot_id)
                continue;
            if (!pick || info.boot_id < best_boot || (info.boot_id == best_boot && info.seq < best_seq)) {
                pick = i;
                best_boot = info.boot_id;
                best_seq = info.seq;
            }
        }
        if (!pick)
            return n;
        used[pick] = 1;
        const uint8_t *s = mem + (size_t)(REGION_START + pick) * 512;
        uint32_t u = (uint32_t)s[0x18] | ((uint32_t)s[0x19] << 8);
        if (n + u > max)
            abort();
        memcpy(text + n, s + 32, u);
        n += u;
    }
}

static void t_writer_basic(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    struct slog *l = new_log();
    struct slog_map m = good_map();
    uint8_t raw[128], text[4000], got[4000], header[512];
    size_t len = 0;
    make_stick(&f);
    memcpy(header, f.mem + (size_t)REGION_START * 512, 512);
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    map_bytes(raw, &m);
    CHECK(slog_open(l, &d, raw, 128, 7, 1) == SLOG_OK && l->armed);
    for (int i = 0; i < 1500; i++)
        text[len++] = (uint8_t)('A' + (i * 7) % 26);
    CHECK(slog_append(l, text, 100) == SLOG_OK);
    CHECK(f.n_writes == 0);                                              /* buffered, not written */
    CHECK(slog_append(l, text + 100, 1400) == SLOG_OK);
    CHECK(f.n_writes == 3);                                              /* three full sectors went out */
    CHECK(slog_flush(l) == SLOG_OK && f.n_writes == 4 && l->stats.syncs == 1);
    uint32_t commands = f.commands;
    CHECK(slog_flush(l) == SLOG_OK && f.commands == commands);          /* nothing new: nothing sent */
    CHECK(slog_append(l, (const uint8_t *)"tail", 4) == SLOG_OK && slog_flush(l) == SLOG_OK);
    memcpy(text + len, "tail", 4);
    len += 4;
    CHECK(f.n_writes == 5);                                              /* the partial sector was rewritten */
    CHECK(f.write_lba[3] == REGION_START + 4 && f.write_lba[4] == REGION_START + 4);
    CHECK(read_log(f.mem, got, sizeof(got)) == len && !memcmp(got, text, len));
    for (uint32_t k = 1; k <= 4; k++) {                                   /* one sequence number per started sector */
        struct slog_sector_info info;
        slog_sector_decode(f.mem + (size_t)(REGION_START + k) * 512, k, m.nonce, &info);
        CHECK(info.valid && info.boot_id == 7 && info.seq == k - 1);
    }
    CHECK(f.violation == 0);
    for (uint32_t i = 0; i < f.n_writes; i++)
        CHECK(f.write_lba[i] > REGION_START && f.write_lba[i] < REGION_START + REGION_SECTORS && f.write_blocks[i] == 1);
    CHECK(!memcmp(header, f.mem + (size_t)REGION_START * 512, 512));      /* the header sector is never rewritten */
    CHECK(l->stats.reads >= 1 + 4 + 5);                                  /* header, pre-reads of new sectors, read-backs */
    CHECK(slog_append(l, NULL, 0) == SLOG_OK && slog_append(l, NULL, 1) == SLOG_E_ARG);
    free(l);
    fake_free(&f);
}

static void t_ring_wrap(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    struct slog *l = new_log();
    struct slog_map m = good_map();
    uint8_t raw[128], text[480 * 3];
    make_stick(&f);
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    map_bytes(raw, &m);
    CHECK(slog_open(l, &d, raw, 128, 3, REGION_SECTORS - 2) == SLOG_OK);
    memset(text, 'x', sizeof(text));
    CHECK(slog_append(l, text, sizeof(text)) == SLOG_OK);
    CHECK(f.n_writes == 3 && f.write_lba[0] == REGION_START + REGION_SECTORS - 2 &&
          f.write_lba[1] == REGION_START + REGION_SECTORS - 1 && f.write_lba[2] == REGION_START + 1);   /* never the header */
    CHECK(f.violation == 0 && l->index == 2 && l->seq == 3);
    free(l);
    fake_free(&f);
}

static enum slog_status open_with(struct fake_stick *f, struct scsi_dev *d, struct slog *l, const struct slog_map *m,
                                  uint32_t boot, uint32_t first)
{
    uint8_t raw[128];
    map_bytes(raw, m);
    CHECK(scsi_open(d, &fake_ops, f, 0) == SCSI_OK);
    return slog_open(l, d, raw, 128, boot, first);
}

static void t_hostile_maps(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    struct slog *l = new_log();
    struct slog_map m;
    uint8_t text[600];
    memset(text, 'y', sizeof(text));

    /* A map that points into the file system, internally consistent (it parses): refused at the header, nothing written. */
    make_stick(&f);
    m = good_map();
    m.partition_lba = 0;
    m.data_lba = 0;
    m.cluster_sectors = 1;
    m.run_start[0] = 16;
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_E_HEADER && f.n_writes == 0 && !l->armed);
    fake_free(&f);

    /* Off by one sector in either direction: the sector there is not the header. */
    for (int delta = -1; delta <= 1; delta += 2) {
        make_stick(&f);
        m = good_map();
        m.run_start[0] = REGION_START + delta;
        m.data_lba = 2304;                                              /* keep it cluster-aligned for the parser */
        m.cluster_sectors = 1;
        CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_E_HEADER && f.n_writes == 0);
        fake_free(&f);
    }

    /* The map's capacity is not the stick's: refused before any read of the region. */
    make_stick(&f);
    m = good_map();
    m.capacity_sectors = DEV_SECTORS + 1;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    uint32_t commands = f.commands;
    uint8_t raw[128];
    map_bytes(raw, &m);
    CHECK(slog_open(l, &d, raw, 128, 1, 1) == SLOG_E_CAPACITY && f.commands == commands && f.n_writes == 0);
    m.capacity_sectors = DEV_SECTORS - 1;
    map_bytes(raw, &m);
    CHECK(slog_open(l, &d, raw, 128, 1, 1) == SLOG_E_CAPACITY);
    fake_free(&f);

    /* The stick lies about its capacity (an off-by-one in READ CAPACITY): the map no longer matches. */
    make_stick(&f);
    f.capacity_lie = -1;
    CHECK(open_with(&f, &d, l, &(struct slog_map){.nonce = NONCE, .sector_size = 512, .capacity_sectors = DEV_SECTORS,
          .partition_lba = 2048, .cluster_sectors = 8, .data_lba = 2304, .region_sectors = REGION_SECTORS, .run_count = 1,
          .run_start = {REGION_START}, .run_sectors = {REGION_SECTORS}}, 1, 1) == SLOG_E_CAPACITY);
    fake_free(&f);

    /* A header with a different nonce than the map. */
    make_stick(&f);
    m = good_map();
    m.nonce ^= 2;
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_E_HEADER && f.n_writes == 0);
    fake_free(&f);

    /* A valid header over foreign data: the open succeeds, the first write is refused by the tag check. */
    make_stick(&f);
    memset(f.mem + (size_t)(REGION_START + 1) * 512, 0x5A, (size_t)(REGION_SECTORS - 1) * 512);
    m = good_map();
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    CHECK(slog_append(l, text, 600) == SLOG_E_NOT_OWNED && f.n_writes == 0 && !l->armed);
    CHECK(slog_append(l, text, 1) == SLOG_E_NOT_OWNED && slog_flush(l) == SLOG_E_NOT_OWNED);   /* sticky */
    commands = f.commands;
    CHECK(slog_append(l, text, 600) == SLOG_E_NOT_OWNED && f.commands == commands);
    fake_free(&f);

    /* A fragmented file: sector 3 of the region belongs to something else. Writes stop there. */
    make_stick(&f);
    memset(f.mem + (size_t)(REGION_START + 3) * 512, 0xC3, 512);
    fake_forbid(&f, REGION_START + 3, REGION_START + 3);
    m = good_map();
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    uint8_t big[480 * 4];
    memset(big, 'z', sizeof(big));
    CHECK(slog_append(l, big, sizeof(big)) == SLOG_E_NOT_OWNED);
    CHECK(f.n_writes == 2 && f.violation == 0 && l->index == 3);       /* sectors 1 and 2 only */
    fake_free(&f);

    /* Arguments. */
    make_stick(&f);
    m = good_map();
    CHECK(open_with(&f, &d, l, &m, 0, 1) == SLOG_E_ARG);
    CHECK(open_with(&f, &d, l, &m, 1, 0) == SLOG_E_ARG);
    CHECK(open_with(&f, &d, l, &m, 1, REGION_SECTORS) == SLOG_E_ARG);
    CHECK(slog_open(l, NULL, raw, 128, 1, 1) == SLOG_E_ARG && slog_open(l, &d, NULL, 128, 1, 1) == SLOG_E_ARG);
    CHECK(slog_open(NULL, &d, raw, 128, 1, 1) == SLOG_E_ARG);
    CHECK(slog_append(l, text, 1) == SLOG_E_NOT_ARMED && slog_flush(l) == SLOG_E_NOT_ARMED);
    CHECK(f.n_writes == 0);
    free(l);
    fake_free(&f);
}

static void t_device_failures(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    struct slog *l = new_log();
    struct slog_map m = good_map();
    uint8_t text[500];
    memset(text, 'q', sizeof(text));

    make_stick(&f);                                                      /* writes that are accepted and dropped */
    f.drop_writes = 1;
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    CHECK(slog_append(l, text, 500) == SLOG_E_READBACK && !l->armed);
    uint32_t commands = f.commands;
    CHECK(slog_append(l, text, 500) == SLOG_E_READBACK && slog_flush(l) == SLOG_E_READBACK && f.commands == commands);
    fake_free(&f);

    make_stick(&f);                                                      /* a read-back that differs */
    f.corrupt_readback = 1;
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    CHECK(slog_append(l, text, 500) == SLOG_E_READBACK);
    fake_free(&f);

    make_stick(&f);                                                      /* write-protected media */
    f.write_protected = 1;
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    CHECK(slog_append(l, text, 500) == SLOG_E_PROTECTED && l->scsi == SCSI_PROTECTED && f.n_writes == 0);
    fake_free(&f);

    make_stick(&f);                                                      /* a transport failure on the write itself */
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    fake_fault_add(&f, f.commands + 2, FK_TIMEOUT_CSW, 0);               /* the pre-read is next, then the write */
    CHECK(slog_append(l, text, 500) == SLOG_E_DEVICE && l->scsi == SCSI_TRANSPORT && d.bot.needs_reset);
    commands = f.commands;
    CHECK(slog_flush(l) == SLOG_E_DEVICE && f.commands == commands);
    fake_free(&f);

    make_stick(&f);                                                      /* the read before the write fails */
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    fake_fault_add(&f, f.commands + 1, FK_CMD_FAILED, SCSI_KEY_MEDIUM_ERROR);
    CHECK(slog_append(l, text, 500) == SLOG_E_DEVICE && f.n_writes == 0);
    fake_free(&f);

    make_stick(&f);                                                      /* a header read that fails */
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    fake_fault_add(&f, f.commands + 1, FK_CMD_FAILED, SCSI_KEY_MEDIUM_ERROR);
    uint8_t raw[128];
    map_bytes(raw, &m);
    CHECK(slog_open(l, &d, raw, 128, 1, 1) == SLOG_E_DEVICE && !l->armed);
    fake_free(&f);

    make_stick(&f);                                                      /* SYNCHRONIZE CACHE: refused is fine, failed is not */
    f.sync_unsupported = 1;
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    CHECK(slog_append(l, text, 10) == SLOG_OK && slog_flush(l) == SLOG_OK && l->stats.sync_refused == 1 && l->armed);
    f.sync_unsupported = 0;
    f.fail_sync = 1;
    CHECK(slog_append(l, text, 10) == SLOG_OK && slog_flush(l) == SLOG_E_DEVICE && !l->armed);
    fake_free(&f);

    make_stick(&f);                                                      /* a stick swapped for another between open and write */
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    memset(f.mem + (size_t)(REGION_START + 1) * 512, 0, 512);
    CHECK(slog_append(l, text, 10) == SLOG_OK && slog_flush(l) == SLOG_E_NOT_OWNED && f.n_writes == 0);
    free(l);
    fake_free(&f);
}

static void t_budget(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    struct slog *l = new_log();
    struct slog_map m = good_map();
    make_stick(&f);
    CHECK(open_with(&f, &d, l, &m, 1, 1) == SLOG_OK);
    enum slog_status st = SLOG_OK;
    for (int i = 0; i < 6000 && st == SLOG_OK; i++)
        st = slog_append(l, (const uint8_t *)"x", 1) == SLOG_OK ? slog_flush(l) : SLOG_E_BUDGET;
    CHECK(st == SLOG_E_BUDGET && f.n_writes == SLOG_WRITE_BUDGET && !l->armed && f.violation == 0);
    free(l);
    fake_free(&f);
}

static void t_two_boots(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    struct slog *l = new_log();
    struct slog_map m = good_map();
    uint32_t next, boot;
    uint8_t text[2000], expect[4000], got[4000];
    size_t len = 0;
    make_stick(&f);
    for (int round = 0; round < 3; round++) {
        CHECK(slog_scan(f.mem + (size_t)REGION_START * 512, REGION_SECTORS, NONCE, &next, &boot) == SLOG_OK);
        CHECK(boot == (uint32_t)round + 1);
        CHECK(open_with(&f, &d, l, &m, boot, next) == SLOG_OK);
        for (int i = 0; i < 700; i++)
            text[i] = (uint8_t)('a' + (i + round) % 26);
        CHECK(slog_append(l, text, 700) == SLOG_OK && slog_flush(l) == SLOG_OK);
        memcpy(expect + len, text, 700);
        len += 700;
    }
    CHECK(read_log(f.mem, got, sizeof(got)) == len && !memcmp(got, expect, len));
    CHECK(f.violation == 0);
    free(l);
    fake_free(&f);
}

static int hex_nibble(int c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

static int hex_parse(const char *s, uint8_t *out, size_t max)
{
    size_t n = 0;
    while (s[0] && s[1] && n < max) {
        int a = hex_nibble(s[0]), b = hex_nibble(s[1]);
        if (a < 0 || b < 0)
            return -1;
        out[n++] = (uint8_t)(a * 16 + b);
        s += 2;
    }
    return (int)n;
}

static void hex_print(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++)
        printf("%02x", p[i]);
    printf("\n");
}

/* M maphex | S index nonce sectorhex | E index nonce boot seq payloadhex | K datahex | H maphex sectorhex */
static int stdin_mode(void)
{
    static char line[4096];
    while (fgets(line, sizeof(line), stdin)) {
        static char w1[2200], w2[2200];
        unsigned long idx, boot, seq;
        unsigned long long nonce;
        uint8_t a[1100], b[1100];
        struct slog_map m;
        if (line[0] == 'M' && sscanf(line + 1, "%2199s", w1) == 1) {
            int n = hex_parse(w1, a, sizeof(a));
            printf("%s\n", slog_status_name(slog_map_parse(a, (uint32_t)n, &m)));
        } else if (line[0] == 'S' && sscanf(line + 1, "%lu %llx %2199s", &idx, &nonce, w1) == 3 &&
                   hex_parse(w1, a, sizeof(a)) == 512) {
            struct slog_sector_info info;
            slog_sector_decode(a, (uint32_t)idx, nonce, &info);
            printf("%d %d %u %u %u\n", info.owned, info.valid, info.boot_id, info.seq, info.used);
        } else if (line[0] == 'E' && sscanf(line + 1, "%lu %llx %lu %lu %2199s", &idx, &nonce, &boot, &seq, w1) == 5) {
            int n = (int)strlen(w1) == 1 && w1[0] == '-' ? 0 : hex_parse(w1, a, 480);
            uint8_t out[512];
            memset(out, 0xA5, sizeof(out));            /* the encoder must write every byte, not rely on a clean buffer */
            slog_sector_encode(out, nonce, (uint32_t)idx, (uint32_t)boot, (uint32_t)seq, a, (uint32_t)n);
            hex_print(out, 512);
        } else if (line[0] == 'K' && sscanf(line + 1, "%2199s", w1) == 1) {
            int n = (int)strlen(w1) == 1 && w1[0] == '-' ? 0 : hex_parse(w1, a, sizeof(a));
            printf("%08x\n", slog_crc32(0, a, (uint32_t)n));
        } else if (line[0] == 'H' && sscanf(line + 1, "%2199s %2199s", w1, w2) == 2) {
            int n = hex_parse(w1, a, sizeof(a));
            if (slog_map_parse(a, (uint32_t)n, &m) != SLOG_OK || hex_parse(w2, b, sizeof(b)) != 512)
                printf("MAP\n");
            else
                printf("%s\n", slog_status_name(slog_header_check(b, &m)));
        } else {
            printf("?\n");
        }
        fflush(stdout);
    }
    return 0;
}

static long file_size(const char *path)
{
    struct stat st;
    return stat(path, &st) ? -1 : (long)st.st_size;
}

/* --image IMG --map MAP --boot-id N --first-index K --text FILE [--chunk N]: the whole flow on a disk image. */
static int image_mode(int argc, char **argv)
{
    const char *image = NULL, *map = NULL, *text = NULL;
    unsigned long boot = 1, first = 1, chunk = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--image")) image = argv[i + 1];
        else if (!strcmp(argv[i], "--map")) map = argv[i + 1];
        else if (!strcmp(argv[i], "--text")) text = argv[i + 1];
        else if (!strcmp(argv[i], "--boot-id")) boot = strtoul(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--first-index")) first = strtoul(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--chunk")) chunk = strtoul(argv[i + 1], NULL, 0);
    }
    if (!image || !map || !text)
        return 2;
    long size = file_size(image), map_size = file_size(map), text_size = file_size(text);
    if (size < 512 || map_size != 128 || text_size < 0)
        return 2;
    int fd = open(image, O_RDWR);
    FILE *mf = fopen(map, "rb"), *tf = fopen(text, "rb");
    if (fd < 0 || !mf || !tf)
        return 2;
    uint8_t raw[128];
    uint8_t *data = malloc((size_t)text_size + 1);
    if (fread(raw, 1, 128, mf) != 128 || (text_size && fread(data, 1, (size_t)text_size, tf) != (size_t)text_size))
        return 2;
    struct fake_stick f;
    struct scsi_dev d;
    struct slog *l = new_log();
    fake_init(&f, (uint64_t)size / 512, 1);
    f.blackhole = 0;
    f.fd = fd;
    enum scsi_status ss = scsi_open(&d, &fake_ops, &f, 0);
    enum slog_status st = ss == SCSI_OK ? slog_open(l, &d, raw, 128, (uint32_t)boot, (uint32_t)first) : SLOG_E_DEVICE;
    if (ss != SCSI_OK)
        printf("RESULT scsi_open=%s\n", scsi_status_name(ss));
    for (long at = 0; st == SLOG_OK && at < text_size;) {
        long n = chunk ? (long)chunk : text_size;
        if (at + n > text_size)
            n = text_size - at;
        st = slog_append(l, data + at, (uint32_t)n);
        at += n;
        if (st == SLOG_OK && chunk)
            st = slog_flush(l);
    }
    if (st == SLOG_OK)
        st = slog_flush(l);
    printf("RESULT status=%s writes=%u reads=%u syncs=%u sectors=%u violation=%d\n", slog_status_name(st), l->stats.writes,
           l->stats.reads, l->stats.syncs, l->stats.sectors_started, f.violation);
    close(fd);
    return st == SLOG_OK ? 0 : 1;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--stdin"))
        return stdin_mode();
    if (argc > 1 && !strcmp(argv[1], "--image"))
        return image_mode(argc, argv);
    if (argc > 1 && argv[1][0] == '-' && argv[1][1] == '-' && strcmp(argv[1], "--self-test"))
        return 2;
    t_crc();
    t_map();
    t_sector();
    t_header();
    t_scan();
    t_writer_basic();
    t_ring_wrap();
    t_hostile_maps();
    t_device_failures();
    t_budget();
    t_two_boots();
    printf("%lu checks\nPASS\n", checks);
    return 0;
}
