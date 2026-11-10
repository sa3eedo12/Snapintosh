/* SPDX-License-Identifier: MIT */
/*
 * Surface Aggregator Module frame codec: implementation. Scope, evidence labels, wire format and the
 * streaming policy are in sam-frame.h; docs/SP11-INPUT-STUDY.md has the citations.
 *
 * Uses no libc at all: byte copies are explicit loops because the monitor's freestanding runtime provides
 * memcpy and memset but neither <string.h> nor memmove. Every read is bounded by the byte count passed in, and
 * no function recurses or allocates.
 */
#include "sam-frame.h"

/* Forward byte copy: safe for overlapping ranges only when dst <= src, which is how compaction uses it. */
static void copy_bytes(uint8_t *dst, const uint8_t *src, size_t n)
{
    for (size_t i = 0; i < n; i++)
        dst[i] = src[i];
}

static void stats_clear(struct sam_stats *s)
{
    s->bytes_in = 0;
    s->bytes_frames = 0;
    s->bytes_skipped = 0;
    s->frames_data_seq = 0;
    s->frames_data_nsq = 0;
    s->frames_ack = 0;
    s->frames_nak = 0;
    s->rej_bad_sync = 0;
    s->rej_header_crc = 0;
    s->rej_unknown_type = 0;
    s->rej_oversize = 0;
    s->rej_bad_length = 0;
    s->rej_payload_crc = 0;
    s->rej_truncated = 0;
    s->rej_trailing = 0;
}

/* CRC-16/CCITT-FALSE advanced four bits at a time: entry n is the remainder of n shifted into the top of the
 * register, i.e. n times the polynomial 0x1021 over GF(2). A nibble table keeps the data small, and the tests
 * check it against a bitwise implementation and Python's binascii. */
static const uint16_t crc_nibble[16] = {
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
};

uint16_t sam_crc16_update(uint16_t crc, const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        crc = (uint16_t)((crc << 4) ^ crc_nibble[(crc >> 12) ^ (data[i] >> 4)]);
        crc = (uint16_t)((crc << 4) ^ crc_nibble[(crc >> 12) ^ (data[i] & 0x0Fu)]);
    }
    return crc;
}

const char *sam_status_name(int status)
{
    switch (status) {
    case SAM_OK: return "ok";
    case SAM_NEED_MORE: return "need-more";
    case SAM_ERR_ARG: return "bad-argument";
    case SAM_ERR_NOSPACE: return "no-space";
    case SAM_ERR_BAD_SYNC: return "bad-sync";
    case SAM_ERR_HEADER_CRC: return "header-crc";
    case SAM_ERR_UNKNOWN_TYPE: return "unknown-type";
    case SAM_ERR_OVERSIZE: return "oversize";
    case SAM_ERR_BAD_LENGTH: return "bad-length";
    case SAM_ERR_PAYLOAD_CRC: return "payload-crc";
    case SAM_ERR_TRUNCATED: return "truncated";
    case SAM_ERR_TRAILING: return "trailing";
    case SAM_ERR_CMD_SHORT: return "command-short";
    case SAM_ERR_CMD_TYPE: return "command-type";
    default: return "unknown";
    }
}

static uint16_t get_le16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

static void put_le16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)(v >> 8);
}

static bool known_type(uint8_t type)
{
    return type == SAM_FRAME_DATA_NSQ || type == SAM_FRAME_NAK || type == SAM_FRAME_ACK ||
           type == SAM_FRAME_DATA_SEQ;
}

static bool carries_payload(uint8_t type)
{
    return type == SAM_FRAME_DATA_NSQ || type == SAM_FRAME_DATA_SEQ;
}

/* The two prefix checks use avail so that a lone 0xAA is a valid prefix and a lone other byte is not. */
int sam_frame_parse(const uint8_t *buf, size_t avail, uint16_t max_payload, struct sam_frame *out)
{
    const size_t header_end = SAM_SYN_LEN + SAM_HEADER_LEN + SAM_CRC_LEN;
    size_t total;
    uint16_t len;

    if (!out || (!buf && avail))
        return SAM_ERR_ARG;
    if (avail >= 1 && buf[0] != SAM_SYN_0)
        return SAM_ERR_BAD_SYNC;
    if (avail >= 2 && buf[1] != SAM_SYN_1)
        return SAM_ERR_BAD_SYNC;
    if (avail < header_end)
        return SAM_NEED_MORE;
    if (sam_crc16_update(SAM_CRC_INIT, buf + SAM_SYN_LEN, SAM_HEADER_LEN) != get_le16(buf + SAM_SYN_LEN + SAM_HEADER_LEN))
        return SAM_ERR_HEADER_CRC;
    if (!known_type(buf[2]))
        return SAM_ERR_UNKNOWN_TYPE;
    len = get_le16(buf + 3);
    if (len > max_payload)
        return SAM_ERR_OVERSIZE;
    if (carries_payload(buf[2]) != (len != 0))
        return SAM_ERR_BAD_LENGTH;
    total = SAM_MESSAGE_OVERHEAD + (size_t)len;
    if (avail < total)
        return SAM_NEED_MORE;
    if (sam_crc16_update(SAM_CRC_INIT, buf + header_end, len) != get_le16(buf + header_end + len))
        return SAM_ERR_PAYLOAD_CRC;
    out->type = buf[2];
    out->seq = buf[5];
    out->payload_len = len;
    out->payload = buf + header_end;
    out->message_len = total;
    return SAM_OK;
}

int sam_frame_decode_exact(const uint8_t *buf, size_t len, uint16_t max_payload, struct sam_frame *out)
{
    int rc = sam_frame_parse(buf, len, max_payload, out);

    if (rc == SAM_NEED_MORE)
        return SAM_ERR_TRUNCATED;
    if (rc == SAM_OK && out->message_len != len)
        return SAM_ERR_TRAILING;
    return rc;
}

/* Writes a message whose payload is two spans (a command header and its data, or one span and nothing),
 * so a command needs no staging copy. The caller has validated type, sizes and capacity. */
static size_t put_message(uint8_t *out, uint8_t type, uint8_t seq, const uint8_t *a, size_t a_len,
                          const uint8_t *b, size_t b_len)
{
    const size_t payload_len = a_len + b_len;
    uint16_t crc;
    size_t pos = SAM_SYN_LEN + SAM_HEADER_LEN + SAM_CRC_LEN;

    out[0] = SAM_SYN_0;
    out[1] = SAM_SYN_1;
    out[2] = type;
    put_le16(out + 3, (uint16_t)payload_len);
    out[5] = seq;
    put_le16(out + 6, sam_crc16_update(SAM_CRC_INIT, out + SAM_SYN_LEN, SAM_HEADER_LEN));
    if (a_len) {
        copy_bytes(out + pos, a, a_len);
        pos += a_len;
    }
    if (b_len) {
        copy_bytes(out + pos, b, b_len);
        pos += b_len;
    }
    crc = sam_crc16_update(SAM_CRC_INIT, out + SAM_SYN_LEN + SAM_HEADER_LEN + SAM_CRC_LEN, payload_len);
    put_le16(out + pos, crc);
    return pos + SAM_CRC_LEN;
}

int sam_frame_build(uint8_t *out, size_t cap, uint8_t type, uint8_t seq, const uint8_t *payload,
                    size_t payload_len, size_t *written)
{
    if (written)
        *written = 0;
    if (!out || !written || (!payload && payload_len) || !known_type(type))
        return SAM_ERR_ARG;
    if (payload_len > SAM_PAYLOAD_MAX)
        return SAM_ERR_OVERSIZE;
    if (carries_payload(type) != (payload_len != 0))
        return SAM_ERR_BAD_LENGTH;
    if (cap < SAM_MESSAGE_OVERHEAD + payload_len)
        return SAM_ERR_NOSPACE;
    *written = put_message(out, type, seq, payload, payload_len, NULL, 0);
    return SAM_OK;
}

int sam_build_ack(uint8_t *out, size_t cap, uint8_t seq, size_t *written)
{
    return sam_frame_build(out, cap, SAM_FRAME_ACK, seq, NULL, 0, written);
}

int sam_build_nak(uint8_t *out, size_t cap, size_t *written)
{
    return sam_frame_build(out, cap, SAM_FRAME_NAK, 0, NULL, 0, written);
}

void sam_command_encode(uint8_t out[SAM_COMMAND_HEADER_LEN], const struct sam_command *cmd)
{
    out[0] = SAM_PAYLOAD_TYPE_CMD;
    out[1] = cmd->tc;
    out[2] = cmd->tid;
    out[3] = cmd->sid;
    out[4] = cmd->iid;
    put_le16(out + 5, cmd->rqid);
    out[7] = cmd->cid;
}

int sam_command_decode(const uint8_t *payload, size_t len, struct sam_command *cmd, const uint8_t **data,
                       size_t *data_len)
{
    if (!cmd || !data || !data_len || (!payload && len))
        return SAM_ERR_ARG;
    if (len < SAM_COMMAND_HEADER_LEN)
        return SAM_ERR_CMD_SHORT;
    if (payload[0] != SAM_PAYLOAD_TYPE_CMD)
        return SAM_ERR_CMD_TYPE;
    cmd->tc = payload[1];
    cmd->tid = payload[2];
    cmd->sid = payload[3];
    cmd->iid = payload[4];
    cmd->rqid = get_le16(payload + 5);
    cmd->cid = payload[7];
    *data = payload + SAM_COMMAND_HEADER_LEN;
    *data_len = len - SAM_COMMAND_HEADER_LEN;
    return SAM_OK;
}

int sam_build_command(uint8_t *out, size_t cap, uint8_t type, uint8_t seq, const struct sam_command *cmd,
                      const uint8_t *data, size_t data_len, size_t *written)
{
    uint8_t header[SAM_COMMAND_HEADER_LEN];

    if (written)
        *written = 0;
    if (!out || !written || !cmd || (!data && data_len) || !carries_payload(type))
        return SAM_ERR_ARG;
    if (data_len > SAM_COMMAND_DATA_MAX)
        return SAM_ERR_OVERSIZE;
    if (cap < SAM_MESSAGE_OVERHEAD + SAM_COMMAND_HEADER_LEN + data_len)
        return SAM_ERR_NOSPACE;
    sam_command_encode(header, cmd);
    *written = put_message(out, type, seq, header, sizeof header, data, data_len);
    return SAM_OK;
}

uint64_t sam_stats_rejects(const struct sam_stats *s)
{
    return s->rej_bad_sync + s->rej_header_crc + s->rej_unknown_type + s->rej_oversize + s->rej_bad_length +
           s->rej_payload_crc + s->rej_truncated + s->rej_trailing;
}

static bool parser_configured(const struct sam_parser *p)
{
    return p && p->magic == SAM_PARSER_MAGIC && p->buf && p->head <= p->fill && p->fill <= p->cap;
}

int sam_parser_init(struct sam_parser *p, uint8_t *storage, size_t storage_len, uint16_t max_payload)
{
    if (!p)
        return SAM_ERR_ARG;
    p->magic = 0;
    p->max_payload = 0;
    p->in_garbage = false;
    p->drained = false;
    p->buf = NULL;
    p->cap = 0;
    p->head = 0;
    p->fill = 0;
    stats_clear(&p->stats);
    if (!storage || max_payload == 0 || storage_len < SAM_MESSAGE_OVERHEAD + (size_t)max_payload)
        return SAM_ERR_ARG;
    p->buf = storage;
    p->cap = storage_len;
    p->max_payload = max_payload;
    p->drained = true;
    p->magic = SAM_PARSER_MAGIC;
    return SAM_OK;
}

size_t sam_parser_push(struct sam_parser *p, const uint8_t *data, size_t len)
{
    size_t room, take;

    if (!parser_configured(p) || (!data && len))
        return 0;
    if (p->head) {
        copy_bytes(p->buf, p->buf + p->head, p->fill - p->head);
        p->fill -= p->head;
        p->head = 0;
    }
    room = p->cap - p->fill;
    take = len < room ? len : room;
    if (take) {
        copy_bytes(p->buf + p->fill, data, take);
        p->fill += take;
        p->stats.bytes_in += take;
        p->drained = false;
    }
    return take;
}

/* Index of the first complete SYN in b[0..n); else n - 1 when the last byte could start one; else n.
 * Everything before the returned index is garbage. */
static size_t find_syn(const uint8_t *b, size_t n)
{
    for (size_t i = 0; i + 1 < n; i++)
        if (b[i] == SAM_SYN_0 && b[i + 1] == SAM_SYN_1)
            return i;
    return (n && b[n - 1] == SAM_SYN_0) ? n - 1 : n;
}

static void skip_bytes(struct sam_parser *p, size_t n)
{
    p->head += n;
    p->stats.bytes_skipped += n;
}

/* Skips non-SYN bytes. A run of them is one rej_bad_sync event even when it arrives in several pushes, so the
 * counters do not depend on how the stream was chunked. */
static void skip_garbage(struct sam_parser *p, size_t n)
{
    if (!n)
        return;
    if (!p->in_garbage) {
        p->in_garbage = true;
        p->stats.rej_bad_sync++;
    }
    skip_bytes(p, n);
}

int sam_parser_next(struct sam_parser *p, struct sam_frame *out)
{
    if (!parser_configured(p) || !out)
        return SAM_ERR_ARG;
    for (;;) {
        size_t avail = p->fill - p->head;
        int rc;

        skip_garbage(p, find_syn(p->buf + p->head, avail));
        avail = p->fill - p->head;
        if (avail < SAM_SYN_LEN) {
            p->drained = true;
            return SAM_NEED_MORE;
        }
        p->in_garbage = false;
        rc = sam_frame_parse(p->buf + p->head, avail, p->max_payload, out);
        if (rc == SAM_NEED_MORE) {
            p->drained = true;
            return SAM_NEED_MORE;
        }
        if (rc == SAM_OK) {
            p->head += out->message_len;
            p->stats.bytes_frames += out->message_len;
            switch (out->type) {
            case SAM_FRAME_DATA_SEQ: p->stats.frames_data_seq++; break;
            case SAM_FRAME_DATA_NSQ: p->stats.frames_data_nsq++; break;
            case SAM_FRAME_ACK: p->stats.frames_ack++; break;
            default: p->stats.frames_nak++; break;
            }
            return SAM_OK;
        }
        switch (rc) {
        case SAM_ERR_HEADER_CRC: p->stats.rej_header_crc++; break;
        case SAM_ERR_UNKNOWN_TYPE: p->stats.rej_unknown_type++; break;
        case SAM_ERR_OVERSIZE: p->stats.rej_oversize++; break;
        case SAM_ERR_BAD_LENGTH: p->stats.rej_bad_length++; break;
        case SAM_ERR_PAYLOAD_CRC: p->stats.rej_payload_crc++; break;
        default: return SAM_ERR_ARG; /* unreachable: the head is a SYN, so no other result is possible */
        }
        /* The rest of a rejected frame is skipped as part of this one incident: it is not a second sync loss. */
        p->in_garbage = true;
        skip_bytes(p, SAM_SYN_LEN);
    }
}

int sam_parser_flush(struct sam_parser *p)
{
    size_t avail;

    if (!parser_configured(p) || !p->drained)
        return SAM_ERR_ARG;
    avail = p->fill - p->head;
    if (avail >= SAM_SYN_LEN)
        p->stats.rej_truncated++;
    else if (avail == 1)
        p->stats.rej_trailing++;
    p->stats.bytes_skipped += avail;
    p->head = 0;
    p->fill = 0;
    p->in_garbage = false;
    p->drained = true;
    return SAM_OK;
}
