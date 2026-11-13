/* SPDX-License-Identifier: MIT */
/* USB Mass Storage Bulk-Only Transport (BOT) and the seven SCSI commands a boot-stick log writer needs.
 *
 * EXPERIMENTAL host model; UNIMPLEMENTED on hardware. Nothing links it into q1n1 or a monitor build and it has never met a
 * USB controller. docs/SP11-STICK-LOG.md is the design. tools/test-sp11-stick-bot.c is the proof, against a fake device
 * (tools/sp11-stick-fakedev.h) that injects short packets, stalls, wrong tags, residue, a lying capacity and more.
 *
 * Why this exists before any hardware run: EL2 has no file system and no firmware USB stack, so a write to the boot stick
 * is a BOT command on a bulk pair that q1n1's own xHCI driver moves. The logic around it (which failure needs a reset
 * recovery, when a short transfer is an error, that a command beyond the device capacity is never sent) is where a bug
 * would corrupt a stick or wedge a bus, and all of it can be checked without a stick.
 *
 * Layouts (VERIFIED by reading Linux include/linux/usb/storage.h and EDK2 UsbMassBot.h at the revisions pinned in the
 * design document; they are the USB Mass Storage Class Bulk-Only Transport 1.0 layouts): the command block wrapper is 31
 * bytes (signature 0x43425355, tag, data length, flags with the direction in bit 7, LUN, CDB length, 16 CDB bytes), the
 * command status wrapper is 13 bytes (signature 0x53425355, the same tag, residue, status 0 passed, 1 failed, 2 phase
 * error). Everything is little-endian on the wire except the SCSI fields, which are big-endian.
 *
 * Ownership and context: the glue owns the transfers, their timeouts and the DMA buffers. This code never blocks, never
 * allocates, never loops without a bound (the one retry loop is capped) and keeps no state outside the caller's structs,
 * so it can run from a polled EL2 loop with every interrupt masked. It is single-threaded: one command at a time.
 *
 * Gate: compiled only under SNAP_PLATFORM_SP11, like the other SP11 models; with the gate off the header and the source
 * preprocess to nothing. */
#ifndef SNAP_STICK_BOT_H
#define SNAP_STICK_BOT_H

#include <stddef.h>
#include <stdint.h>

#ifndef SNAP_PLATFORM_SP11
#define SNAP_PLATFORM_SP11 0
#endif

#if SNAP_PLATFORM_SP11

#define BOT_CBW_BYTES 31u
#define BOT_CSW_BYTES 13u
#define BOT_CBW_SIGNATURE UINT32_C(0x43425355) /* "USBC" */
#define BOT_CSW_SIGNATURE UINT32_C(0x53425355) /* "USBS" */
#define BOT_MAX_CDB 16u
#define BOT_MAX_LUN 15u
#define BOT_FLAG_IN 0x80u
#define BOT_CSW_PASSED 0u
#define BOT_CSW_FAILED_STATUS 1u
#define BOT_CSW_PHASE_ERROR 2u
#define BOT_SETUP_BYTES 8u

/* Result of one bounded transfer, reported by the glue. A stall is not an error by itself: BOT recovers from it. */
enum bot_io { BOT_IO_OK = 0, BOT_IO_STALL, BOT_IO_TIMEOUT, BOT_IO_ERROR };
enum bot_dir { BOT_DIR_NONE = 0, BOT_DIR_IN, BOT_DIR_OUT };

enum bot_status {
    BOT_OK = 0,        /* CSW passed, tag matched, residue 0 and every requested byte moved */
    BOT_ARG,           /* refused before any transfer: bad pointer, length or LUN */
    BOT_NEEDS_RESET,   /* an earlier failure left the pipes in an unknown state; nothing was sent */
    BOT_CBW_FAILED,    /* the command block did not go out; reset recovery is required */
    BOT_DATA_FAILED,   /* the data stage timed out, errored or stalled with no way to clear it; reset recovery required */
    BOT_CSW_FAILED,    /* the status stage failed twice; reset recovery required */
    BOT_CSW_INVALID,   /* wrong length, signature, tag or status value, or residue above the request; reset required */
    BOT_PHASE_ERROR,   /* the device reported status 2; reset recovery required */
    BOT_CMD_FAILED,    /* status 1: the command failed; a REQUEST SENSE says why; no reset needed */
    BOT_SHORT,         /* status 0 but residue is not 0 or fewer bytes moved than asked; no reset needed */
    BOT_RESET_FAILED   /* reset recovery itself failed or is not available; the device stays unusable */
};

/* The transport the glue provides. All four are synchronous and bounded: the glue owns the deadline. `transferred` is the
 * number of bytes that really moved (a short packet is BOT_IO_OK with transferred < the request). bulk_in's `capacity` is
 * the most it may take. clear_halt resets the host side of the endpoint (on xHCI that is a Reset Endpoint plus a Set TR
 * Dequeue Pointer command) and sends CLEAR_FEATURE(ENDPOINT_HALT); reset sends the Bulk-Only Mass Storage Reset request.
 * Either of the last two may be NULL: a stall or a protocol error then fails closed (the device stays marked unusable). */
struct bot_ops {
    int (*bulk_out)(void *ctx, const uint8_t *data, uint32_t length, uint32_t *transferred);
    int (*bulk_in)(void *ctx, uint8_t *data, uint32_t capacity, uint32_t *transferred);
    int (*clear_halt)(void *ctx, int endpoint_in);
    int (*reset)(void *ctx);
};

struct bot {
    const struct bot_ops *ops;
    void *ctx;
    uint32_t tag;          /* last tag used; 0 is skipped so an all-zero CSW tag can never match */
    uint8_t lun;
    uint8_t needs_reset;   /* set by every failure that BOT answers with reset recovery; cleared only by it */
    uint32_t stalls_cleared, resets, commands;
};

void bot_init(struct bot *b, const struct bot_ops *ops, void *ctx, uint8_t lun);

/* Pure encoders, exposed so the glue and the tests build the very same bytes. */
void bot_build_cbw(uint8_t out[BOT_CBW_BYTES], uint32_t tag, uint32_t data_length, enum bot_dir dir, uint8_t lun,
                   const uint8_t *cdb, uint32_t cdb_length);
/* Judge a received status wrapper against the tag and the request. Returns BOT_OK, BOT_CSW_INVALID or BOT_PHASE_ERROR
 * and, for BOT_OK, the device's status byte (0 or 1) and residue. */
enum bot_status bot_check_csw(const uint8_t *csw, uint32_t length, uint32_t expected_tag, uint32_t requested,
                              uint8_t *status, uint32_t *residue);
/* The three class and standard control requests BOT needs, as 8-byte SETUP packets (bmRequestType, bRequest, wValue,
 * wIndex, wLength, little-endian): Get Max LUN, Bulk-Only Mass Storage Reset, CLEAR_FEATURE(ENDPOINT_HALT). */
void bot_setup_get_max_lun(uint8_t out[BOT_SETUP_BYTES], uint16_t interface_number);
void bot_setup_reset(uint8_t out[BOT_SETUP_BYTES], uint16_t interface_number);
void bot_setup_clear_halt(uint8_t out[BOT_SETUP_BYTES], uint8_t endpoint_address);

/* One command: CBW, optional data stage, CSW. `moved` (may be NULL) is the data-stage byte count. */
enum bot_status bot_exec_none(struct bot *b, const uint8_t *cdb, uint32_t cdb_length);
enum bot_status bot_exec_in(struct bot *b, const uint8_t *cdb, uint32_t cdb_length, uint8_t *data, uint32_t length,
                            uint32_t *moved);
enum bot_status bot_exec_out(struct bot *b, const uint8_t *cdb, uint32_t cdb_length, const uint8_t *data, uint32_t length,
                             uint32_t *moved);
/* Reset recovery (BOT 5.3.4): the reset request, then CLEAR_FEATURE(ENDPOINT_HALT) on bulk-in and bulk-out. Returns BOT_OK
 * and clears needs_reset only when all three succeeded. */
enum bot_status bot_reset_recovery(struct bot *b);
const char *bot_status_name(enum bot_status s);

/* ---- The SCSI commands: all that a log writer sends --------------------------------------------------------------- */
#define SCSI_OP_TEST_UNIT_READY 0x00u
#define SCSI_OP_REQUEST_SENSE 0x03u
#define SCSI_OP_INQUIRY 0x12u
#define SCSI_OP_READ_CAPACITY_10 0x25u
#define SCSI_OP_READ_10 0x28u
#define SCSI_OP_WRITE_10 0x2Au
#define SCSI_OP_SYNCHRONIZE_CACHE_10 0x35u
#define SCSI_FLAG_FUA 0x08u            /* byte 1 of READ(10) and WRITE(10): force unit access */
#define SCSI_KEY_NOT_READY 2u
#define SCSI_KEY_MEDIUM_ERROR 3u
#define SCSI_KEY_ILLEGAL_REQUEST 5u
#define SCSI_KEY_UNIT_ATTENTION 6u
#define SCSI_KEY_DATA_PROTECT 7u
#define SCSI_BLOCK_BYTES 512u          /* the only logical block size this writer accepts */
#define SCSI_MAX_BLOCKS 8u             /* one command moves at most 4 KiB: the size of the glue's DMA buffer */
#define SCSI_TUR_TRIES 4u

enum scsi_status {
    SCSI_OK = 0,
    SCSI_ARG,        /* refused before any transfer */
    SCSI_TRANSPORT,  /* the bot layer failed: see scsi_dev.last_bot */
    SCSI_CHECK,      /* CHECK CONDITION: see scsi_dev.sense */
    SCSI_NOT_READY,  /* not ready after SCSI_TUR_TRIES attempts */
    SCSI_NOT_DISK,   /* INQUIRY says this is not a connected direct-access block device */
    SCSI_BLOCK_SIZE, /* the logical block size is not 512 */
    SCSI_CAPACITY,   /* READ CAPACITY(10) reports 0xFFFFFFFF: the device needs READ CAPACITY(16), which is not supported */
    SCSI_RANGE,      /* the command would reach past the last LBA the device reported: never sent */
    SCSI_SHORT,      /* fewer bytes than the command needs */
    SCSI_PROTECTED   /* the device refused a write as DATA PROTECT (write-protected media) */
};

struct scsi_sense {
    uint8_t valid, key, asc, ascq;
};

struct scsi_dev {
    struct bot bot;
    struct scsi_sense sense;           /* from the last CHECK CONDITION */
    enum bot_status last_bot;          /* from the last command */
    uint32_t last_lba, block_len;      /* READ CAPACITY(10): the last addressable LBA and the logical block size */
    uint8_t device_type;               /* INQUIRY peripheral device type */
    char vendor[9], product[17];       /* printable INQUIRY text, trailing blanks removed */
};

/* Open a device: TEST UNIT READY (a UNIT ATTENTION left by the port reset is cleared by retrying, up to SCSI_TUR_TRIES),
 * INQUIRY (must be a connected direct-access device), READ CAPACITY(10) (must be 512-byte blocks and a 32-bit capacity). */
enum scsi_status scsi_open(struct scsi_dev *d, const struct bot_ops *ops, void *ctx, uint8_t lun);
enum scsi_status scsi_test_unit_ready(struct scsi_dev *d);
enum scsi_status scsi_inquiry(struct scsi_dev *d);
enum scsi_status scsi_request_sense(struct scsi_dev *d);
enum scsi_status scsi_read_capacity10(struct scsi_dev *d);
/* READ(10) and WRITE(10) of 1 to SCSI_MAX_BLOCKS blocks. A range past last_lba is SCSI_RANGE and nothing is sent. `fua`
 * asks the device to complete the write on stable media before it reports status. */
enum scsi_status scsi_read10(struct scsi_dev *d, uint32_t lba, uint32_t blocks, uint8_t *buffer);
enum scsi_status scsi_write10(struct scsi_dev *d, uint32_t lba, uint32_t blocks, const uint8_t *buffer, int fua);
/* SYNCHRONIZE CACHE(10) of the whole medium. A device without a volatile cache may refuse it as ILLEGAL REQUEST. */
enum scsi_status scsi_sync10(struct scsi_dev *d);
const char *scsi_status_name(enum scsi_status s);

#endif /* SNAP_PLATFORM_SP11 */
#endif /* SNAP_STICK_BOT_H */
