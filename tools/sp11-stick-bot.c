/* SPDX-License-Identifier: MIT */
/* See sp11-stick-bot.h. */
#include "sp11-stick-bot.h"

#if SNAP_PLATFORM_SP11

/* Byte helpers instead of casts: the wrappers are unaligned on the wire, and nothing here may depend on a libc. */
static uint32_t get_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t get_be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static void put_be32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

void bot_init(struct bot *b, const struct bot_ops *ops, void *ctx, uint8_t lun)
{
    b->ops = ops;
    b->ctx = ctx;
    b->tag = 0;
    b->lun = lun;
    b->needs_reset = 0;
    b->stalls_cleared = 0;
    b->resets = 0;
    b->commands = 0;
}

void bot_build_cbw(uint8_t out[BOT_CBW_BYTES], uint32_t tag, uint32_t data_length, enum bot_dir dir, uint8_t lun,
                   const uint8_t *cdb, uint32_t cdb_length)
{
    uint32_t i;
    for (i = 0; i < BOT_CBW_BYTES; i++)
        out[i] = 0;
    put_le32(out, BOT_CBW_SIGNATURE);
    put_le32(out + 4, tag);
    put_le32(out + 8, data_length);
    out[12] = dir == BOT_DIR_IN ? BOT_FLAG_IN : 0;
    out[13] = (uint8_t)(lun & 0x0Fu);
    out[14] = (uint8_t)(cdb_length & 0x1Fu);
    for (i = 0; i < cdb_length && i < BOT_MAX_CDB; i++)
        out[15 + i] = cdb[i];
}

enum bot_status bot_check_csw(const uint8_t *csw, uint32_t length, uint32_t expected_tag, uint32_t requested,
                              uint8_t *status, uint32_t *residue)
{
    uint32_t left;
    if (!csw || length != BOT_CSW_BYTES)
        return BOT_CSW_INVALID;
    if (get_le32(csw) != BOT_CSW_SIGNATURE || get_le32(csw + 4) != expected_tag)
        return BOT_CSW_INVALID;
    if (csw[12] > BOT_CSW_PHASE_ERROR)
        return BOT_CSW_INVALID;
    if (csw[12] == BOT_CSW_PHASE_ERROR)
        return BOT_PHASE_ERROR;
    left = get_le32(csw + 8);
    /* The device cannot leave more unmoved than the host asked for: a larger residue is a broken status, not a short read. */
    if (left > requested)
        return BOT_CSW_INVALID;
    *status = csw[12];
    *residue = left;
    return BOT_OK;
}

static void setup_packet(uint8_t out[BOT_SETUP_BYTES], uint8_t type, uint8_t request, uint16_t value, uint16_t index,
                         uint16_t length)
{
    out[0] = type;
    out[1] = request;
    out[2] = (uint8_t)value;
    out[3] = (uint8_t)(value >> 8);
    out[4] = (uint8_t)index;
    out[5] = (uint8_t)(index >> 8);
    out[6] = (uint8_t)length;
    out[7] = (uint8_t)(length >> 8);
}

void bot_setup_get_max_lun(uint8_t out[BOT_SETUP_BYTES], uint16_t interface_number)
{
    setup_packet(out, 0xA1u, 0xFEu, 0, interface_number, 1);   /* device to host, class, interface */
}

void bot_setup_reset(uint8_t out[BOT_SETUP_BYTES], uint16_t interface_number)
{
    setup_packet(out, 0x21u, 0xFFu, 0, interface_number, 0);   /* host to device, class, interface */
}

void bot_setup_clear_halt(uint8_t out[BOT_SETUP_BYTES], uint8_t endpoint_address)
{
    setup_packet(out, 0x02u, 0x01u, 0, endpoint_address, 0);   /* standard CLEAR_FEATURE, ENDPOINT_HALT, endpoint */
}

static enum bot_status bot_exec(struct bot *b, const uint8_t *cdb, uint32_t cdb_length, enum bot_dir dir,
                                const uint8_t *out, uint8_t *in, uint32_t length, uint32_t *moved)
{
    uint8_t cbw[BOT_CBW_BYTES], csw[BOT_CSW_BYTES], status = 0;
    uint32_t sent = 0, got = 0, got_csw = 0, residue = 0, tag, attempt;
    enum bot_status verdict;
    int io;

    if (moved)
        *moved = 0;
    if (!b || !b->ops || !b->ops->bulk_out || !b->ops->bulk_in || !cdb || cdb_length < 1 || cdb_length > BOT_MAX_CDB ||
        b->lun > BOT_MAX_LUN)
        return BOT_ARG;
    if ((length == 0) != (dir == BOT_DIR_NONE) || (dir == BOT_DIR_IN && !in) || (dir == BOT_DIR_OUT && !out))
        return BOT_ARG;
    /* A device that already answered wrongly may be waiting in any phase. Another command would be read as data. */
    if (b->needs_reset)
        return BOT_NEEDS_RESET;

    b->commands++;
    b->tag++;
    if (!b->tag)
        b->tag++;
    tag = b->tag;
    bot_build_cbw(cbw, tag, length, dir, b->lun, cdb, cdb_length);

    io = b->ops->bulk_out(b->ctx, cbw, BOT_CBW_BYTES, &sent);
    if (io != BOT_IO_OK || sent != BOT_CBW_BYTES) {
        b->needs_reset = 1;
        return BOT_CBW_FAILED;
    }

    if (dir != BOT_DIR_NONE) {
        io = dir == BOT_DIR_IN ? b->ops->bulk_in(b->ctx, in, length, &got) : b->ops->bulk_out(b->ctx, out, length, &got);
        if (io == BOT_IO_STALL) {
            /* BOT 6.7: a data-stage stall is cleared and the status stage still follows. Without a way to clear it the
             * endpoint stays halted and every later transfer would stall too. */
            if (!b->ops->clear_halt || b->ops->clear_halt(b->ctx, dir == BOT_DIR_IN) != 0) {
                b->needs_reset = 1;
                return BOT_DATA_FAILED;
            }
            b->stalls_cleared++;
        } else if (io != BOT_IO_OK || got > length) {
            b->needs_reset = 1;
            return BOT_DATA_FAILED;
        }
    }

    /* The status stage is tried twice: once, and once more after clearing a stall of the bulk-in endpoint. A halt left by
     * the second failure is cleared by reset recovery, which clears both endpoints anyway. */
    io = BOT_IO_ERROR;
    for (attempt = 0; attempt < 2; attempt++) {
        got_csw = 0;
        io = b->ops->bulk_in(b->ctx, csw, BOT_CSW_BYTES, &got_csw);
        if (io != BOT_IO_STALL || attempt == 1)
            break;
        if (!b->ops->clear_halt || b->ops->clear_halt(b->ctx, 1) != 0)
            break;
        b->stalls_cleared++;
    }
    if (io != BOT_IO_OK) {
        b->needs_reset = 1;
        return BOT_CSW_FAILED;
    }

    verdict = bot_check_csw(csw, got_csw, tag, length, &status, &residue);
    if (verdict != BOT_OK) {
        b->needs_reset = 1;
        return verdict;
    }
    if (moved)
        *moved = got;
    if (status == BOT_CSW_FAILED_STATUS)
        return BOT_CMD_FAILED;
    if (residue != 0 || got != length)
        return BOT_SHORT;
    return BOT_OK;
}

enum bot_status bot_exec_none(struct bot *b, const uint8_t *cdb, uint32_t cdb_length)
{
    return bot_exec(b, cdb, cdb_length, BOT_DIR_NONE, 0, 0, 0, 0);
}

enum bot_status bot_exec_in(struct bot *b, const uint8_t *cdb, uint32_t cdb_length, uint8_t *data, uint32_t length,
                            uint32_t *moved)
{
    return bot_exec(b, cdb, cdb_length, BOT_DIR_IN, 0, data, length, moved);
}

enum bot_status bot_exec_out(struct bot *b, const uint8_t *cdb, uint32_t cdb_length, const uint8_t *data, uint32_t length,
                             uint32_t *moved)
{
    return bot_exec(b, cdb, cdb_length, BOT_DIR_OUT, data, 0, length, moved);
}

enum bot_status bot_reset_recovery(struct bot *b)
{
    if (!b || !b->ops)
        return BOT_ARG;
    if (!b->ops->reset || !b->ops->clear_halt)
        return BOT_RESET_FAILED;
    if (b->ops->reset(b->ctx) != 0)
        return BOT_RESET_FAILED;
    /* The reset leaves the halt conditions in place; the host clears both, in-endpoint first. */
    if (b->ops->clear_halt(b->ctx, 1) != 0 || b->ops->clear_halt(b->ctx, 0) != 0)
        return BOT_RESET_FAILED;
    b->needs_reset = 0;
    b->resets++;
    return BOT_OK;
}

const char *bot_status_name(enum bot_status s)
{
    switch (s) {
    case BOT_OK: return "OK";
    case BOT_ARG: return "BAD ARGUMENT";
    case BOT_NEEDS_RESET: return "NEEDS RESET";
    case BOT_CBW_FAILED: return "CBW FAILED";
    case BOT_DATA_FAILED: return "DATA STAGE FAILED";
    case BOT_CSW_FAILED: return "CSW FAILED";
    case BOT_CSW_INVALID: return "CSW INVALID";
    case BOT_PHASE_ERROR: return "PHASE ERROR";
    case BOT_CMD_FAILED: return "COMMAND FAILED";
    case BOT_SHORT: return "SHORT";
    case BOT_RESET_FAILED: return "RESET FAILED";
    }
    return "UNKNOWN";
}

/* ---- SCSI ------------------------------------------------------------------------------------------------------- */

static enum scsi_status scsi_run(struct scsi_dev *d, const uint8_t *cdb, uint32_t cdb_length, enum bot_dir dir,
                                 const uint8_t *out, uint8_t *in, uint32_t length, uint32_t *moved, int sense_on_check)
{
    enum bot_status st;
    if (dir == BOT_DIR_IN)
        st = bot_exec_in(&d->bot, cdb, cdb_length, in, length, moved);
    else if (dir == BOT_DIR_OUT)
        st = bot_exec_out(&d->bot, cdb, cdb_length, out, length, moved);
    else
        st = bot_exec_none(&d->bot, cdb, cdb_length);
    d->last_bot = st;
    if (st == BOT_OK)
        return SCSI_OK;
    if (st == BOT_SHORT)
        return SCSI_SHORT;
    if (st != BOT_CMD_FAILED)
        return SCSI_TRANSPORT;
    d->sense.valid = 0;
    if (sense_on_check)
        (void)scsi_request_sense(d);
    return SCSI_CHECK;
}

static void clear_sense(struct scsi_dev *d)
{
    d->sense.valid = d->sense.key = d->sense.asc = d->sense.ascq = 0;
}

enum scsi_status scsi_request_sense(struct scsi_dev *d)
{
    uint8_t cdb[6] = {SCSI_OP_REQUEST_SENSE, 0, 0, 0, 18, 0}, buf[18];
    uint32_t i, moved = 0;
    enum scsi_status st;
    for (i = 0; i < sizeof(buf); i++)
        buf[i] = 0;
    clear_sense(d);
    st = scsi_run(d, cdb, sizeof(cdb), BOT_DIR_IN, 0, buf, sizeof(buf), &moved, 0);
    if (st == SCSI_SHORT && moved >= 8)
        st = SCSI_OK;
    if (st != SCSI_OK)
        return st == SCSI_SHORT ? st : SCSI_TRANSPORT;
    if ((buf[0] & 0x7Fu) == 0x70u || (buf[0] & 0x7Fu) == 0x71u) {      /* fixed format */
        d->sense.valid = 1;
        d->sense.key = buf[2] & 0x0Fu;
        if (moved >= 14) {
            d->sense.asc = buf[12];
            d->sense.ascq = buf[13];
        }
    } else if ((buf[0] & 0x7Fu) == 0x72u || (buf[0] & 0x7Fu) == 0x73u) { /* descriptor format */
        d->sense.valid = 1;
        d->sense.key = buf[1] & 0x0Fu;
        d->sense.asc = buf[2];
        d->sense.ascq = buf[3];
    }
    return SCSI_OK;
}

enum scsi_status scsi_test_unit_ready(struct scsi_dev *d)
{
    static const uint8_t cdb[6] = {SCSI_OP_TEST_UNIT_READY, 0, 0, 0, 0, 0};
    return scsi_run(d, cdb, sizeof(cdb), BOT_DIR_NONE, 0, 0, 0, 0, 1);
}

enum scsi_status scsi_inquiry(struct scsi_dev *d)
{
    uint8_t cdb[6] = {SCSI_OP_INQUIRY, 0, 0, 0, 36, 0}, buf[36];
    uint32_t i, moved = 0;
    enum scsi_status st;
    for (i = 0; i < sizeof(buf); i++)
        buf[i] = 0;
    st = scsi_run(d, cdb, sizeof(cdb), BOT_DIR_IN, 0, buf, sizeof(buf), &moved, 1);
    /* A device may return less than the 36 bytes it was offered room for. The five-byte header says what it is. */
    if (st == SCSI_SHORT && moved >= 5)
        st = SCSI_OK;
    if (st != SCSI_OK)
        return st;
    d->device_type = buf[0] & 0x1Fu;
    if ((buf[0] >> 5) != 0 || d->device_type != 0)
        return SCSI_NOT_DISK;
    for (i = 0; i < 8; i++)
        d->vendor[i] = moved >= 16 && buf[8 + i] >= 0x20 && buf[8 + i] < 0x7F ? (char)buf[8 + i] : ' ';
    for (i = 0; i < 16; i++)
        d->product[i] = moved >= 32 && buf[16 + i] >= 0x20 && buf[16 + i] < 0x7F ? (char)buf[16 + i] : ' ';
    d->vendor[8] = d->product[16] = 0;
    for (i = 8; i > 0 && d->vendor[i - 1] == ' '; i--)
        d->vendor[i - 1] = 0;
    for (i = 16; i > 0 && d->product[i - 1] == ' '; i--)
        d->product[i - 1] = 0;
    return SCSI_OK;
}

enum scsi_status scsi_read_capacity10(struct scsi_dev *d)
{
    uint8_t cdb[10] = {SCSI_OP_READ_CAPACITY_10, 0, 0, 0, 0, 0, 0, 0, 0, 0}, buf[8];
    uint32_t i, moved = 0, last;
    enum scsi_status st;
    for (i = 0; i < sizeof(buf); i++)
        buf[i] = 0;
    st = scsi_run(d, cdb, sizeof(cdb), BOT_DIR_IN, 0, buf, sizeof(buf), &moved, 1);
    if (st != SCSI_OK)
        return st;
    last = get_be32(buf);
    /* 0xFFFFFFFF means the capacity does not fit 32 bits. A boot stick is far below that; refuse, do not guess. */
    if (last == 0xFFFFFFFFu)
        return SCSI_CAPACITY;
    d->last_lba = last;
    d->block_len = get_be32(buf + 4);
    return SCSI_OK;
}

enum scsi_status scsi_open(struct scsi_dev *d, const struct bot_ops *ops, void *ctx, uint8_t lun)
{
    uint8_t *raw = (uint8_t *)d;
    uint32_t i, tries;
    int ready = 0;
    enum scsi_status st = SCSI_OK;
    if (!d || !ops)
        return SCSI_ARG;
    for (i = 0; i < sizeof(*d); i++)
        raw[i] = 0;
    bot_init(&d->bot, ops, ctx, lun);
    for (tries = 0; tries < SCSI_TUR_TRIES && !ready; tries++) {
        st = scsi_test_unit_ready(d);
        if (st == SCSI_OK)
            ready = 1;
        else if (st != SCSI_CHECK ||
                 !d->sense.valid || (d->sense.key != SCSI_KEY_UNIT_ATTENTION && d->sense.key != SCSI_KEY_NOT_READY))
            return st;
    }
    if (!ready)
        return d->sense.valid && d->sense.key == SCSI_KEY_NOT_READY ? SCSI_NOT_READY : st;
    st = scsi_inquiry(d);
    if (st != SCSI_OK)
        return st;
    st = scsi_read_capacity10(d);
    if (st != SCSI_OK)
        return st;
    return d->block_len == SCSI_BLOCK_BYTES ? SCSI_OK : SCSI_BLOCK_SIZE;
}

static void rw10_cdb(uint8_t cdb[10], uint8_t opcode, uint8_t flags, uint32_t lba, uint32_t blocks)
{
    cdb[0] = opcode;
    cdb[1] = flags;
    put_be32(cdb + 2, lba);
    cdb[6] = 0;                         /* group number */
    cdb[7] = (uint8_t)(blocks >> 8);
    cdb[8] = (uint8_t)blocks;
    cdb[9] = 0;                         /* control */
}

/* The device-reported capacity bounds every transfer, so a caller's arithmetic mistake can never address past the medium. */
static enum scsi_status rw10_check(const struct scsi_dev *d, uint32_t lba, uint32_t blocks, const void *buffer)
{
    if (!d || !buffer || blocks < 1 || blocks > SCSI_MAX_BLOCKS)
        return SCSI_ARG;
    if (d->block_len != SCSI_BLOCK_BYTES)
        return SCSI_BLOCK_SIZE;
    if ((uint64_t)lba + blocks > (uint64_t)d->last_lba + 1u)
        return SCSI_RANGE;
    return SCSI_OK;
}

enum scsi_status scsi_read10(struct scsi_dev *d, uint32_t lba, uint32_t blocks, uint8_t *buffer)
{
    uint8_t cdb[10];
    uint32_t moved = 0;
    enum scsi_status st = rw10_check(d, lba, blocks, buffer);
    if (st != SCSI_OK)
        return st;
    rw10_cdb(cdb, SCSI_OP_READ_10, 0, lba, blocks);
    return scsi_run(d, cdb, sizeof(cdb), BOT_DIR_IN, 0, buffer, blocks * SCSI_BLOCK_BYTES, &moved, 1);
}

enum scsi_status scsi_write10(struct scsi_dev *d, uint32_t lba, uint32_t blocks, const uint8_t *buffer, int fua)
{
    uint8_t cdb[10];
    uint32_t moved = 0;
    enum scsi_status st = rw10_check(d, lba, blocks, buffer);
    if (st != SCSI_OK)
        return st;
    rw10_cdb(cdb, SCSI_OP_WRITE_10, fua ? SCSI_FLAG_FUA : 0, lba, blocks);
    st = scsi_run(d, cdb, sizeof(cdb), BOT_DIR_OUT, buffer, 0, blocks * SCSI_BLOCK_BYTES, &moved, 1);
    if (st == SCSI_CHECK && d->sense.valid && d->sense.key == SCSI_KEY_DATA_PROTECT)
        return SCSI_PROTECTED;
    return st;
}

enum scsi_status scsi_sync10(struct scsi_dev *d)
{
    static const uint8_t cdb[10] = {SCSI_OP_SYNCHRONIZE_CACHE_10, 0, 0, 0, 0, 0, 0, 0, 0, 0};
    if (!d)
        return SCSI_ARG;
    return scsi_run(d, cdb, sizeof(cdb), BOT_DIR_NONE, 0, 0, 0, 0, 1);
}

const char *scsi_status_name(enum scsi_status s)
{
    switch (s) {
    case SCSI_OK: return "OK";
    case SCSI_ARG: return "BAD ARGUMENT";
    case SCSI_TRANSPORT: return "TRANSPORT";
    case SCSI_CHECK: return "CHECK CONDITION";
    case SCSI_NOT_READY: return "NOT READY";
    case SCSI_NOT_DISK: return "NOT A DISK";
    case SCSI_BLOCK_SIZE: return "BLOCK SIZE";
    case SCSI_CAPACITY: return "CAPACITY OVER 32 BITS";
    case SCSI_RANGE: return "OUT OF RANGE";
    case SCSI_SHORT: return "SHORT";
    case SCSI_PROTECTED: return "WRITE PROTECTED";
    }
    return "UNKNOWN";
}

#endif /* SNAP_PLATFORM_SP11 */
