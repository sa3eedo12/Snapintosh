/* SPDX-License-Identifier: MIT */
/* The log region of a boot stick: its map, its sector layout, and the guarded writer that appends text to it.
 *
 * EXPERIMENTAL host model; UNIMPLEMENTED on hardware (docs/SP11-STICK-LOG.md). This is the safety-critical half of the
 * stick log: tools/sp11-stick-bot.c moves bytes, this file decides which bytes may go where.
 *
 * The idea. The Mac makes a contiguous 1 MiB file on the stick (tools/sp11_stick_logmap.py) and a 128-byte map file that
 * names its absolute LBAs. Every sector of the file carries a tag: a magic, its own index and a random 64-bit nonce. At EL2
 * q1n1 writes sectors straight into those LBAs and the Mac later reads the log by copying the file. A raw write can only
 * hurt the stick if the map is wrong or stale (a file that was copied elsewhere, a different stick, an operator mistake),
 * so the rule is that a sector is overwritten only after it was read back and still carries our tag. Foreign data, the
 * FAT, a directory and another file cannot carry a 64-bit nonce, so a wrong map ends in a refusal, not in damage.
 *
 * Guards, in the order they apply (each is a test case in tools/test-sp11-stick-log.c):
 *   1. the map must parse: size, magic, version, CRC, reserved bytes, one run, geometry inside the device;
 *   2. the device's READ CAPACITY(10) and the map's capacity must agree, in 512-byte blocks;
 *   3. the header sector read from the map's first LBA must carry the signature, the nonce and the map's numbers;
 *   4. a write targets a log sector (index 1 to region_sectors - 1) by INDEX: no API takes an LBA from the caller, and the
 *      header sector is never written;
 *   5. before the first write of a sector in a session, the sector is read and must carry magic, index and nonce;
 *   6. every write is read back and compared; a mismatch ends the log;
 *   7. any failure disarms the writer for good (sticky) and records why, so a wedged device is not retried in a loop;
 *   8. a write budget bounds the wear and the time a runaway caller can cost.
 *
 * Layouts (all little-endian; the Python side is tools/sp11_stick_logmap.py, and both are tabulated in the design):
 *   region sector 0, the header (64 bytes, rest zero): "SNAPLOG1", version 1, header_bytes 64, nonce, sector_size 512,
 *       region_sectors, start_lba, partition_lba, capacity_sectors, flags 0, 12 reserved, CRC-32 of bytes 0 to 59.
 *   log sector, index 1 to region_sectors - 1 (512 bytes): "SLG1", index, nonce, boot_id, seq, used, flags, CRC-32, then
 *       480 payload bytes. The CRC covers bytes 0 to 27 and payload[0..used). boot_id 0 means never written.
 *   map file (128 bytes): "SNAPLMAP", version 1, map_bytes 128, nonce, sector_size, capacity_sectors, partition_lba,
 *       cluster_sectors, data_lba, region_sectors, run_count, flags, four (start_lba, sectors) runs, 36 reserved, CRC-32.
 *
 * Ordering of the log: sectors are written in a ring from `first_index`; the Mac orders them by (boot_id, seq).
 *
 * Gate and purity: like sp11-stick-bot.h. No libc, no allocation, no globals. The struct holds the sector buffers, so in a
 * real build it must live in the xHCI DMA arena (64-byte aligned), where the glue can hand the buffers to the controller. */
#ifndef SNAP_STICK_LOG_H
#define SNAP_STICK_LOG_H

#include "sp11-stick-bot.h"

#if SNAP_PLATFORM_SP11

#define SLOG_SECTOR_BYTES 512u
#define SLOG_MAP_BYTES 128u
#define SLOG_HEADER_BYTES 64u
#define SLOG_TAG_BYTES 32u
#define SLOG_PAYLOAD_BYTES 480u
#define SLOG_MAX_RUNS 4u
#define SLOG_MIN_REGION_SECTORS 2u
#define SLOG_MAX_REGION_SECTORS 0x10000u
#define SLOG_VERSION 1u
/* A boot writes a few dozen sectors. Twice the largest region is a generous bound that still stops a runaway loop. */
#define SLOG_WRITE_BUDGET 4096u

enum slog_status {
    SLOG_OK = 0,
    SLOG_E_ARG,
    SLOG_E_MAP_SIZE,
    SLOG_E_MAP_MAGIC,
    SLOG_E_MAP_VERSION,
    SLOG_E_MAP_CRC,
    SLOG_E_MAP_RESERVED,
    SLOG_E_MAP_NONCE,
    SLOG_E_MAP_RUNS,
    SLOG_E_MAP_GEOMETRY,
    SLOG_E_CAPACITY,   /* the device's capacity or block size is not the map's */
    SLOG_E_DEVICE,     /* a transport or SCSI failure: see slog.scsi and slog.dev->last_bot */
    SLOG_E_PROTECTED,  /* the device refused a write as write-protected */
    SLOG_E_HEADER,     /* the header sector is not the one the map describes */
    SLOG_E_NOT_OWNED,  /* the target sector does not carry our tag: the map is wrong or stale */
    SLOG_E_BOUNDS,     /* an index outside 1 to region_sectors - 1 */
    SLOG_E_READBACK,   /* the sector read back is not the sector written */
    SLOG_E_BUDGET,     /* the write budget is spent */
    SLOG_E_NOT_ARMED   /* the writer is not open, or an earlier failure disarmed it */
};

struct slog_map {
    uint64_t nonce;
    uint32_t sector_size, capacity_sectors, partition_lba, cluster_sectors, data_lba, region_sectors, run_count;
    uint32_t run_start[SLOG_MAX_RUNS], run_sectors[SLOG_MAX_RUNS];
};

struct slog_sector_info {
    int owned;     /* magic, index and nonce are ours: the rule a writer applies */
    int valid;     /* owned, and the used count and the CRC are consistent: the rule a reader applies */
    uint32_t boot_id, seq, used;
};

struct slog_stats {
    uint32_t writes, reads, syncs, sync_refused, sectors_started;
};

struct slog {
    struct scsi_dev *dev;
    struct slog_map map;
    uint32_t boot_id, seq, index;       /* identity of the sector being filled */
    uint32_t owned_index;               /* the index whose tag was verified this session; 0 = none */
    uint32_t used, flushed_used;        /* pending payload bytes; the count the sector on the stick already holds */
    uint32_t dirty;                     /* a write has happened since the last SYNCHRONIZE CACHE */
    uint8_t armed;
    enum slog_status error;             /* the first failure; sticky */
    enum scsi_status scsi;              /* the SCSI status behind SLOG_E_DEVICE or SLOG_E_PROTECTED */
    struct slog_stats stats;
    uint8_t payload[SLOG_PAYLOAD_BYTES];
    _Alignas(64) uint8_t sector[SLOG_SECTOR_BYTES];
    _Alignas(64) uint8_t scratch[SLOG_SECTOR_BYTES];
};

/* CRC-32 (IEEE 802.3, reflected, as zlib): pass the previous result to continue over more bytes, 0 to start. */
uint32_t slog_crc32(uint32_t crc, const uint8_t *data, uint32_t length);

/* Pure parsers and encoders. */
enum slog_status slog_map_parse(const uint8_t *raw, uint32_t length, struct slog_map *out);
void slog_map_encode(uint8_t out[SLOG_MAP_BYTES], const struct slog_map *m);
enum slog_status slog_header_check(const uint8_t *sector, const struct slog_map *m);
void slog_sector_encode(uint8_t out[SLOG_SECTOR_BYTES], uint64_t nonce, uint32_t index, uint32_t boot_id, uint32_t seq,
                        const uint8_t *payload, uint32_t used);
void slog_sector_decode(const uint8_t *sector, uint32_t index, uint64_t nonce, struct slog_sector_info *info);
/* Before ExitBootServices, with the whole region read through the file system: pick where this boot starts. Refuses a
 * region with a sector that is not ours. Returns the index after the newest valid sector (1 when none) and the boot id
 * after the highest one seen. */
enum slog_status slog_scan(const uint8_t *region, uint32_t sectors, uint64_t nonce, uint32_t *next_index,
                           uint32_t *next_boot_id);

/* Arm the writer on a device that scsi_open accepted. `map_raw` is the map file's bytes. */
enum slog_status slog_open(struct slog *l, struct scsi_dev *dev, const uint8_t *map_raw, uint32_t map_length,
                           uint32_t boot_id, uint32_t first_index);
/* Buffer text; a full sector is written at once. After a failure everything returns the sticky error and writes nothing. */
enum slog_status slog_append(struct slog *l, const uint8_t *data, uint32_t length);
/* Write the pending partial sector (if it changed) and SYNCHRONIZE CACHE (if anything was written since the last one). */
enum slog_status slog_flush(struct slog *l);
const char *slog_status_name(enum slog_status s);

#endif /* SNAP_PLATFORM_SP11 */
#endif /* SNAP_STICK_LOG_H */
