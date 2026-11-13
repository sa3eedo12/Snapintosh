/* SPDX-License-Identifier: MIT */
/* A fake USB mass-storage device for tools/test-sp11-stick-bot.c and tools/test-sp11-stick-log.c. Test support only.
 *
 * It is a Bulk-Only Transport state machine (idle, data in, data out, status, wedged) over a block store, answering the
 * seven SCSI commands of tools/sp11-stick-bot.c, with the failures a real stick or a real controller produces injected
 * on demand: stalls, short packets, a wrong tag or signature, a short status, residue, a phase error, timeouts, a device
 * that wedges until it is reset, a capacity that lies, write protection, writes that are dropped or corrupted.
 *
 * It models what the BOT specification and Linux/EDK2 host behaviour make a host handle. It is not a conformance model
 * of any real device: the first real stick will teach more (EXPERIMENTAL, host only).
 *
 * The block store is memory, a file (an image, through pread and pwrite), or a black hole (reads return a pattern, writes
 * are only recorded), so a test can address the whole 32-bit LBA space without a 2 TiB buffer. Every write is logged with
 * its LBA and block count, and a test can name forbidden sector ranges: a write that touches one sets `violation`. */
#ifndef SNAP_STICK_FAKEDEV_H
#define SNAP_STICK_FAKEDEV_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "sp11-stick-bot.h"

/* A header of helpers that each test uses a different subset of: unused ones are not a defect here. */
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wunused-const-variable"

enum fake_fault_kind {
    FK_NONE = 0,
    FK_STALL_DATA,       /* the data stage stalls and the command fails (CHECK CONDITION, MEDIUM ERROR) */
    FK_STALL_CSW_ONCE,   /* the first status read stalls; after the halt is cleared the status is delivered */
    FK_STALL_CSW_ALWAYS, /* every status read stalls, even after the halt is cleared */
    FK_SHORT_DATA,       /* a data-in stage delivers only `arg` bytes; the status passes and reports the residue */
    FK_SHORT_DATA_QUIET, /* a data-in stage delivers only `arg` bytes and the status claims a residue of zero */
    FK_BAD_TAG,
    FK_BAD_SIGNATURE,
    FK_SHORT_CSW,        /* the status wrapper is 12 bytes */
    FK_PHASE_ERROR,      /* status 2 */
    FK_RESERVED_STATUS,  /* status 3 */
    FK_RESIDUE,          /* status 0 with residue `arg` although all data moved */
    FK_RESIDUE_TOO_BIG,  /* residue above the request */
    FK_CMD_FAILED,       /* status 1 with sense key `arg`, no data */
    FK_TIMEOUT_CBW,
    FK_TIMEOUT_DATA,
    FK_TIMEOUT_CSW,
    FK_WEDGE             /* the device wedges: every transfer stalls until the Bulk-Only reset */
};

struct fake_fault {
    uint32_t on_command;   /* 1-based count of CBWs the device has received; 0 = unused slot */
    enum fake_fault_kind kind;
    uint32_t arg;
};

enum fake_state { FS_IDLE = 0, FS_DATA_IN, FS_DATA_OUT, FS_STATUS, FS_WEDGED };

#define FAKE_MAX_FAULTS 8
#define FAKE_MAX_WRITES 16384
#define FAKE_MAX_FORBIDDEN 8
#define FAKE_DBUF 8192

struct fake_stick {
    /* the store */
    uint8_t *mem;
    int fd;                    /* >= 0: an image file */
    int blackhole;
    uint64_t sectors;
    /* what the device reports */
    int64_t capacity_lie;      /* added to the reported last LBA */
    uint32_t block_len;        /* reported block length (512) */
    uint8_t inquiry_type;
    uint32_t inquiry_bytes;    /* how many of the 36 INQUIRY bytes it returns */
    uint32_t last_lba_override;/* nonzero: report exactly this last LBA */
    /* behaviour knobs */
    int write_protected, drop_writes, corrupt_readback, sync_unsupported, fail_sync;
    uint32_t ua_left, not_ready_left, ua_after_reset;
    int allow_clear_halt;      /* clear_halt succeeds (default 1) */
    int allow_reset;
    struct fake_fault fault[FAKE_MAX_FAULTS];
    /* state */
    enum fake_state state;
    int halt_in, halt_out, csw_stalled_once;
    uint32_t commands, cur_tag, host_len, dev_len, pos, residue;
    int host_in, dev_in, stall_data, csw_status, csw_short, bad_tag, bad_sig;
    uint8_t cdb[16], last_cdb[16];
    uint32_t cdb_len, last_cdb_len;
    uint8_t dbuf[FAKE_DBUF];
    uint8_t sense_key, sense_asc, sense_ascq;
    uint32_t pending_lba, pending_blocks;
    int pending_write;
    /* evidence */
    uint32_t resets, clear_in, clear_out, out_calls, in_calls;
    uint32_t n_writes;
    uint32_t write_lba[FAKE_MAX_WRITES], write_blocks[FAKE_MAX_WRITES];
    uint64_t forbidden_lo[FAKE_MAX_FORBIDDEN], forbidden_hi[FAKE_MAX_FORBIDDEN];
    uint32_t n_forbidden;
    int violation;
};

static uint32_t fk_le32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
static void fk_put32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
static void fk_putbe32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v; }

static void fake_init(struct fake_stick *f, uint64_t sectors, int blackhole)
{
    memset(f, 0, sizeof(*f));
    f->fd = -1;
    f->sectors = sectors;
    f->blackhole = blackhole;
    f->block_len = 512;
    f->inquiry_bytes = 36;
    f->allow_clear_halt = 1;
    f->allow_reset = 1;
    if (!blackhole)
        f->mem = calloc((size_t)sectors, 512);
}

static void fake_free(struct fake_stick *f)
{
    free(f->mem);
    f->mem = NULL;
}

static void fake_forbid(struct fake_stick *f, uint64_t lo, uint64_t hi)
{
    if (f->n_forbidden < FAKE_MAX_FORBIDDEN) {
        f->forbidden_lo[f->n_forbidden] = lo;
        f->forbidden_hi[f->n_forbidden++] = hi;
    }
}

static void fake_fault_add(struct fake_stick *f, uint32_t command, enum fake_fault_kind kind, uint32_t arg)
{
    for (int i = 0; i < FAKE_MAX_FAULTS; i++)
        if (!f->fault[i].on_command) {
            f->fault[i].on_command = command;
            f->fault[i].kind = kind;
            f->fault[i].arg = arg;
            return;
        }
}

/* The fault active for a given command and kind, or NULL. */
static const struct fake_fault *fake_fault_for(const struct fake_stick *f, uint32_t command, enum fake_fault_kind kind)
{
    for (int i = 0; i < FAKE_MAX_FAULTS; i++)
        if (f->fault[i].on_command == command && f->fault[i].kind == kind)
            return &f->fault[i];
    return NULL;
}

static void fake_store_read(struct fake_stick *f, uint32_t lba, uint32_t blocks, uint8_t *out)
{
    if (f->blackhole) {
        for (uint32_t i = 0; i < blocks * 512u; i++)
            out[i] = (uint8_t)((uint64_t)lba * 31u + i);
    } else if (f->fd >= 0) {
        ssize_t got = pread(f->fd, out, (size_t)blocks * 512, (off_t)lba * 512);
        if (got != (ssize_t)blocks * 512)
            abort();
    } else {
        memcpy(out, f->mem + (size_t)lba * 512, (size_t)blocks * 512);
    }
}

static void fake_store_write(struct fake_stick *f, uint32_t lba, uint32_t blocks, const uint8_t *in)
{
    if (f->n_writes < FAKE_MAX_WRITES) {
        f->write_lba[f->n_writes] = lba;
        f->write_blocks[f->n_writes] = blocks;
    }
    f->n_writes++;
    for (uint32_t i = 0; i < f->n_forbidden; i++)
        if ((uint64_t)lba <= f->forbidden_hi[i] && (uint64_t)lba + blocks - 1 >= f->forbidden_lo[i])
            f->violation = 1;
    if (f->drop_writes || f->blackhole)
        return;
    if (f->fd >= 0) {
        if (pwrite(f->fd, in, (size_t)blocks * 512, (off_t)lba * 512) != (ssize_t)blocks * 512)
            abort();
    } else {
        memcpy(f->mem + (size_t)lba * 512, in, (size_t)blocks * 512);
    }
}

static void fake_set_sense(struct fake_stick *f, uint8_t key, uint8_t asc, uint8_t ascq)
{
    f->sense_key = key;
    f->sense_asc = asc;
    f->sense_ascq = ascq;
}

/* Execute the command in f->cdb and decide the device's intent (dev_len, dev_in, status). */
static void fake_decide(struct fake_stick *f, uint32_t command)
{
    const uint8_t *c = f->cdb;
    uint32_t lba, blocks, last;
    f->dev_len = 0;
    f->dev_in = 0;
    f->csw_status = 0;
    f->pending_write = 0;
    memcpy(f->last_cdb, c, 16);
    f->last_cdb_len = f->cdb_len;
    switch (c[0]) {
    case SCSI_OP_TEST_UNIT_READY:
        if (f->ua_left) {
            f->ua_left--;
            fake_set_sense(f, SCSI_KEY_UNIT_ATTENTION, 0x29, 0x00);
            f->csw_status = 1;
        } else if (f->not_ready_left) {
            f->not_ready_left--;
            fake_set_sense(f, SCSI_KEY_NOT_READY, 0x04, 0x01);
            f->csw_status = 1;
        }
        break;
    case SCSI_OP_REQUEST_SENSE:
        memset(f->dbuf, 0, 18);
        f->dbuf[0] = 0x70;
        f->dbuf[2] = (uint8_t)(f->sense_key | 0x20);        /* ILI is set by real devices; the key is the low nibble */
        f->dbuf[7] = 10;
        f->dbuf[12] = f->sense_asc;
        f->dbuf[13] = f->sense_ascq;
        f->dev_len = c[4] < 18 ? c[4] : 18;
        f->dev_in = 1;
        fake_set_sense(f, 0, 0, 0);
        break;
    case SCSI_OP_INQUIRY:
        memset(f->dbuf, 0, 36);
        f->dbuf[0] = f->inquiry_type;
        f->dbuf[1] = 0x80;
        f->dbuf[4] = 31;
        memcpy(f->dbuf + 8, "SNAPTEST", 8);
        memcpy(f->dbuf + 16, "FAKE STICK      ", 16);
        memcpy(f->dbuf + 32, "0001", 4);
        f->dev_len = f->inquiry_bytes < 36 ? f->inquiry_bytes : 36;
        f->dev_in = 1;
        break;
    case SCSI_OP_READ_CAPACITY_10:
        last = f->last_lba_override ? f->last_lba_override : (uint32_t)((int64_t)f->sectors - 1 + f->capacity_lie);
        fk_putbe32(f->dbuf, last);
        fk_putbe32(f->dbuf + 4, f->block_len);
        f->dev_len = 8;
        f->dev_in = 1;
        break;
    case SCSI_OP_READ_10:
    case SCSI_OP_WRITE_10:
        lba = ((uint32_t)c[2] << 24) | ((uint32_t)c[3] << 16) | ((uint32_t)c[4] << 8) | c[5];
        blocks = ((uint32_t)c[7] << 8) | c[8];
        if (blocks == 0 || blocks * 512 > FAKE_DBUF || (uint64_t)lba + blocks > f->sectors) {
            fake_set_sense(f, SCSI_KEY_ILLEGAL_REQUEST, 0x21, 0x00);     /* LBA out of range */
            f->csw_status = 1;
            break;
        }
        if (c[0] == SCSI_OP_READ_10) {
            fake_store_read(f, lba, blocks, f->dbuf);
            if (f->corrupt_readback && f->n_writes)
                f->dbuf[5] ^= 0x40;
            f->dev_in = 1;
        } else if (f->write_protected) {
            fake_set_sense(f, SCSI_KEY_DATA_PROTECT, 0x27, 0x00);
            f->csw_status = 1;
            break;
        } else {
            f->pending_write = 1;
            f->pending_lba = lba;
            f->pending_blocks = blocks;
        }
        f->dev_len = blocks * 512;
        break;
    case SCSI_OP_SYNCHRONIZE_CACHE_10:
        if (f->sync_unsupported) {
            fake_set_sense(f, SCSI_KEY_ILLEGAL_REQUEST, 0x20, 0x00);
            f->csw_status = 1;
        } else if (f->fail_sync) {
            fake_set_sense(f, SCSI_KEY_MEDIUM_ERROR, 0x03, 0x00);
            f->csw_status = 1;
        }
        break;
    default:
        fake_set_sense(f, SCSI_KEY_ILLEGAL_REQUEST, 0x20, 0x00);         /* invalid command operation code */
        f->csw_status = 1;
        break;
    }
    (void)command;
}

/* Reconcile the device's intent with what the host announced in the CBW: the thirteen cases of BOT 6.7, as far as the
 * host-side code can tell them apart. */
static void fake_reconcile(struct fake_stick *f, uint32_t command)
{
    uint32_t host = f->host_len;
    f->residue = 0;
    f->stall_data = 0;
    if (f->csw_status == 1 && f->dev_len) {
        /* A failed command moves no data: the device stalls whatever endpoint the host expects to use. */
        f->dev_len = 0;
    }
    if (f->dev_len == 0 && host == 0) {
        f->state = FS_STATUS;
    } else if (f->dev_len == 0) {
        f->stall_data = 1;                  /* Hi > Dn or Ho > Dn: stall, residue is the whole request */
        f->residue = host;
        f->state = f->host_in ? FS_DATA_IN : FS_DATA_OUT;
    } else if (host == 0 || f->dev_in != f->host_in || host < f->dev_len) {
        f->csw_status = BOT_CSW_PHASE_ERROR;   /* Hn < Di, Hn < Do, direction mismatch, Hi < Di, Ho < Do */
        f->state = FS_STATUS;
        f->pending_write = 0;
    } else {
        f->residue = host - f->dev_len;     /* Hi = Di or Hi > Di (short), Ho = Do or Ho > Do */
        f->state = f->dev_in ? FS_DATA_IN : FS_DATA_OUT;
        if (f->residue && !f->dev_in)
            f->stall_data = 0;
    }
    f->pos = 0;
    /* Injected faults on top of the protocol-correct answer. */
    const struct fake_fault *x;
    if ((x = fake_fault_for(f, command, FK_STALL_DATA)) && host) {
        f->stall_data = 1;
        f->state = f->host_in ? FS_DATA_IN : FS_DATA_OUT;
        f->csw_status = 1;
        f->residue = host;
        f->pending_write = 0;
        fake_set_sense(f, SCSI_KEY_MEDIUM_ERROR, 0x11, 0x00);
    }
    if ((x = fake_fault_for(f, command, FK_SHORT_DATA)) && f->dev_in && x->arg < f->dev_len) {
        f->residue = host - x->arg;
        f->dev_len = x->arg;
    }
    if ((x = fake_fault_for(f, command, FK_SHORT_DATA_QUIET)) && f->dev_in && x->arg < f->dev_len) {
        f->residue = 0;
        f->dev_len = x->arg;
    }
    if ((x = fake_fault_for(f, command, FK_CMD_FAILED))) {
        f->csw_status = 1;
        fake_set_sense(f, (uint8_t)x->arg, 0x11, 0x00);
        f->dev_len = 0;
        f->pending_write = 0;
        if (host) {
            f->stall_data = 1;
            f->residue = host;
            f->state = f->host_in ? FS_DATA_IN : FS_DATA_OUT;
        } else {
            f->state = FS_STATUS;
        }
    }
    if (fake_fault_for(f, command, FK_PHASE_ERROR))
        f->csw_status = BOT_CSW_PHASE_ERROR;
    if (fake_fault_for(f, command, FK_RESERVED_STATUS))
        f->csw_status = 3;
    if ((x = fake_fault_for(f, command, FK_RESIDUE)))
        f->residue = x->arg;
    if (fake_fault_for(f, command, FK_RESIDUE_TOO_BIG))
        f->residue = host + 1;
    f->bad_tag = fake_fault_for(f, command, FK_BAD_TAG) != NULL;
    f->bad_sig = fake_fault_for(f, command, FK_BAD_SIGNATURE) != NULL;
    f->csw_short = fake_fault_for(f, command, FK_SHORT_CSW) != NULL;
    if (fake_fault_for(f, command, FK_WEDGE))
        f->state = FS_WEDGED;
}

static int fake_bulk_out(void *ctx, const uint8_t *data, uint32_t length, uint32_t *transferred)
{
    struct fake_stick *f = ctx;
    *transferred = 0;
    f->out_calls++;
    if (f->state == FS_WEDGED) {
        f->halt_out = 1;
        return BOT_IO_STALL;
    }
    if (f->halt_out)
        return BOT_IO_STALL;
    switch (f->state) {
    case FS_IDLE: {
        uint32_t command = f->commands + 1;
        if (fake_fault_for(f, command, FK_TIMEOUT_CBW)) {
            f->commands++;
            return BOT_IO_TIMEOUT;
        }
        if (length != BOT_CBW_BYTES || fk_le32(data) != BOT_CBW_SIGNATURE) {
            f->halt_in = f->halt_out = 1;       /* an invalid CBW: the device wedges until reset recovery */
            f->state = FS_WEDGED;
            *transferred = length;
            return BOT_IO_OK;
        }
        f->commands = command;
        f->cur_tag = fk_le32(data + 4);
        f->host_len = fk_le32(data + 8);
        f->host_in = (data[12] & BOT_FLAG_IN) != 0;
        f->cdb_len = data[14] & 0x1F;
        memset(f->cdb, 0, 16);
        memcpy(f->cdb, data + 15, f->cdb_len);
        fake_decide(f, command);
        fake_reconcile(f, command);
        *transferred = BOT_CBW_BYTES;
        return BOT_IO_OK;
    }
    case FS_DATA_OUT: {
        uint32_t n = length;
        if (fake_fault_for(f, f->commands, FK_TIMEOUT_DATA))
            return BOT_IO_TIMEOUT;
        if (f->stall_data) {
            f->halt_out = 1;
            f->state = FS_STATUS;
            return BOT_IO_STALL;
        }
        if (n > f->dev_len)
            n = f->dev_len;
        if (f->pending_write && n == f->dev_len) {
            memcpy(f->dbuf, data, n);
            fake_store_write(f, f->pending_lba, f->pending_blocks, f->dbuf);
        }
        *transferred = n;
        f->state = FS_STATUS;
        if (n < length) {
            f->halt_out = 1;                    /* Ho > Do: the device stalls the excess */
            return BOT_IO_STALL;
        }
        return BOT_IO_OK;
    }
    default:
        return BOT_IO_TIMEOUT;                  /* the device is not listening: NAK until the host gives up */
    }
}

static int fake_bulk_in(void *ctx, uint8_t *data, uint32_t capacity, uint32_t *transferred)
{
    struct fake_stick *f = ctx;
    *transferred = 0;
    f->in_calls++;
    if (f->state == FS_WEDGED) {
        f->halt_in = 1;
        return BOT_IO_STALL;
    }
    if (f->halt_in)
        return BOT_IO_STALL;
    switch (f->state) {
    case FS_DATA_IN: {
        uint32_t n;
        if (fake_fault_for(f, f->commands, FK_TIMEOUT_DATA))
            return BOT_IO_TIMEOUT;
        if (f->stall_data) {
            f->halt_in = 1;
            f->state = FS_STATUS;
            return BOT_IO_STALL;
        }
        n = f->dev_len - f->pos;
        if (n > capacity)
            n = capacity;
        memcpy(data, f->dbuf + f->pos, n);
        f->pos += n;
        *transferred = n;
        if (f->pos == f->dev_len)
            f->state = FS_STATUS;
        return BOT_IO_OK;
    }
    case FS_STATUS: {
        uint8_t csw[BOT_CSW_BYTES];
        uint32_t n = f->csw_short ? BOT_CSW_BYTES - 1 : BOT_CSW_BYTES;
        if (fake_fault_for(f, f->commands, FK_TIMEOUT_CSW))
            return BOT_IO_TIMEOUT;
        if (fake_fault_for(f, f->commands, FK_STALL_CSW_ALWAYS)) {
            f->halt_in = 1;
            return BOT_IO_STALL;
        }
        if (fake_fault_for(f, f->commands, FK_STALL_CSW_ONCE) && !f->csw_stalled_once) {
            f->csw_stalled_once = 1;
            f->halt_in = 1;
            return BOT_IO_STALL;
        }
        fk_put32(csw, f->bad_sig ? 0x53425354u : BOT_CSW_SIGNATURE);
        fk_put32(csw + 4, f->bad_tag ? f->cur_tag ^ 1u : f->cur_tag);
        fk_put32(csw + 8, f->residue);
        csw[12] = (uint8_t)f->csw_status;
        if (n > capacity)
            n = capacity;
        memcpy(data, csw, n);
        *transferred = n;
        f->state = FS_IDLE;
        f->csw_stalled_once = 0;
        return BOT_IO_OK;
    }
    default:
        return BOT_IO_TIMEOUT;
    }
}

static int fake_clear_halt(void *ctx, int endpoint_in)
{
    struct fake_stick *f = ctx;
    if (!f->allow_clear_halt)
        return -1;
    if (endpoint_in) {
        f->halt_in = 0;
        f->clear_in++;
    } else {
        f->halt_out = 0;
        f->clear_out++;
    }
    return 0;
}

static int fake_reset(void *ctx)
{
    struct fake_stick *f = ctx;
    if (!f->allow_reset)
        return -1;
    f->resets++;
    f->state = FS_IDLE;
    f->csw_stalled_once = 0;
    if (f->ua_after_reset)
        f->ua_left = 1;
    return 0;
}

static const struct bot_ops fake_ops = {fake_bulk_out, fake_bulk_in, fake_clear_halt, fake_reset};
static const struct bot_ops fake_ops_no_recovery = {fake_bulk_out, fake_bulk_in, NULL, NULL};

#endif /* SNAP_STICK_FAKEDEV_H */
