/* SPDX-License-Identifier: MIT */
/*
 * Surface Aggregator Module (SAM) serial-hub frame codec, for the Surface Pro 11 input path.
 *
 * Status: EXPERIMENTAL host-tested codec. No driver exists (UNIMPLEMENTED), nothing links this file, and it
 * has never seen a byte from a real controller. It is portable C11 with no allocation, no recursion, no
 * locks and no libc at all (stdint, stddef and stdbool are compiler headers), so a monitor, a kext or a host
 * tool can all use it.
 *
 * Where the facts come from. The framing, the CRC and the command header are VERIFIED only in the sense of
 * "read, and two public sources agree": Linux v7.4-rc2 (7475ea8b58616b33c04afb976cf0fbaad9dd63fb)
 * include/linux/surface_aggregator/serial_hub.h and Documentation/driver-api/surface_aggregator/ssh.rst
 * (GPL-2.0+), and linux-surface/surface-aggregator-module doc/ssh-protocol.txt at
 * de6d403852f33f5445c25971a3f25e6ebafbf824 (GPL-2.0). The same bytes are UNKNOWN on a Surface Pro 11 wire
 * until the G1 spike reads some (docs/SP11-INPUT-STUDY.md, sections 3 and 5). The code was written from
 * those facts alone: no Linux code or prose is copied, and the identifiers are this project's own. The study lists
 * every place where this codec deliberately behaves differently from the reference driver.
 *
 * Wire format (all multi-byte values little-endian, no padding):
 *
 *   AA 55 | type len(2) seq | crc16(type..seq) | payload[len] | crc16(payload)
 *
 * The CRC is CRC-16/CCITT-FALSE: polynomial 0x1021, initial value 0xFFFF, most significant bit first, no
 * reflection, no final XOR (check value 0x29B1 for "123456789"). The payload CRC of an empty payload is
 * therefore 0xFFFF and is still sent, so every message is at least 10 bytes.
 *
 * Streaming policy (the contract the tests pin down; chunking never changes the result):
 *   - Bytes before a SYN are one garbage run (rej_bad_sync counts runs, bytes_skipped counts bytes).
 *   - After a SYN, a bad header CRC, an unknown type, a length above the configured limit, or a type/length
 *     mismatch (a control frame with a payload, or a data frame without one) rejects the candidate. A
 *     candidate whose payload CRC fails is rejected too. Every rejection discards exactly the two SYN
 *     bytes and rescans from the next byte, so a real frame that starts inside a rejected one is still
 *     found. Bytes skipped on the way to that next SYN belong to the same incident and are not counted
 *     as a second garbage run.
 *   - A frame that is merely incomplete waits for more bytes. It is counted as truncated only when the
 *     caller declares the stream ended or idle with sam_parser_flush().
 *   - Nothing is delivered unless both CRCs match and the length rules hold. The sequence number is not
 *     checked here: it belongs to the transport layer.
 *
 * Threading: a parser belongs to one context at a time. There is no internal lock and no callback.
 */
#ifndef SNAP_COMPAT_SAM_FRAME_H
#define SNAP_COMPAT_SAM_FRAME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SAM_SYN_0 0xAAu
#define SAM_SYN_1 0x55u
#define SAM_SYN_LEN 2u
#define SAM_HEADER_LEN 4u  /* type, length (2), sequence */
#define SAM_CRC_LEN 2u
#define SAM_MESSAGE_OVERHEAD (SAM_SYN_LEN + SAM_HEADER_LEN + SAM_CRC_LEN + SAM_CRC_LEN)
#define SAM_PAYLOAD_MAX 0xFFFFu /* the length field is 16 bits; callers normally set a lower limit */

#define SAM_CRC_INIT 0xFFFFu
#define SAM_CRC_CHECK_123456789 0x29B1u

enum sam_frame_type {
    SAM_FRAME_DATA_NSQ = 0x00, /* data, no acknowledgement expected */
    SAM_FRAME_NAK = 0x04,      /* receive error report; carries no payload */
    SAM_FRAME_ACK = 0x40,      /* acknowledges a DATA_SEQ by its sequence number; no payload */
    SAM_FRAME_DATA_SEQ = 0x80, /* data that must be acknowledged */
};

/* Result of every function here: zero or positive means "no error", negative is a refusal. */
enum sam_status {
    SAM_OK = 0,
    SAM_NEED_MORE = 1, /* the bytes so far are a valid prefix; not an error */
    SAM_ERR_ARG = -1,  /* NULL, zero limit, undersized storage, or an unconfigured parser */
    SAM_ERR_NOSPACE = -2,
    SAM_ERR_BAD_SYNC = -3,
    SAM_ERR_HEADER_CRC = -4,
    SAM_ERR_UNKNOWN_TYPE = -5,
    SAM_ERR_OVERSIZE = -6,   /* payload length above the configured limit */
    SAM_ERR_BAD_LENGTH = -7, /* length inconsistent with the frame type */
    SAM_ERR_PAYLOAD_CRC = -8,
    SAM_ERR_TRUNCATED = -9,
    SAM_ERR_TRAILING = -10,
    SAM_ERR_CMD_SHORT = -11, /* command payload shorter than its 8-byte header */
    SAM_ERR_CMD_TYPE = -12,  /* command payload does not start with the command type byte */
};

/* Short name of a status for logs; "unknown" for any other value. */
const char *sam_status_name(int status);

/* CRC-16/CCITT-FALSE continuation: pass SAM_CRC_INIT for the first span, then each result for the next. */
uint16_t sam_crc16_update(uint16_t crc, const uint8_t *data, size_t len);

/* A decoded message. payload points into the buffer that was parsed and is only as valid as that buffer. */
struct sam_frame {
    uint8_t type;           /* enum sam_frame_type */
    uint8_t seq;
    uint16_t payload_len;
    const uint8_t *payload; /* not to be dereferenced when payload_len is zero */
    size_t message_len;     /* bytes the message occupies on the wire */
};

/*
 * Judges one candidate message that must start at buf[0].
 *
 * Returns SAM_OK and fills *out; SAM_NEED_MORE when buf is a valid prefix (avail may be zero); or the
 * first failing check in this fixed order: SAM_ERR_BAD_SYNC, SAM_ERR_HEADER_CRC, SAM_ERR_UNKNOWN_TYPE,
 * SAM_ERR_OVERSIZE (len > max_payload), SAM_ERR_BAD_LENGTH, SAM_ERR_PAYLOAD_CRC. Reads only
 * buf[0..avail). *out is untouched unless SAM_OK is returned. Pure: no state, no allocation.
 */
int sam_frame_parse(const uint8_t *buf, size_t avail, uint16_t max_payload, struct sam_frame *out);

/*
 * Same judgement for a buffer that must hold exactly one message: an incomplete one is SAM_ERR_TRUNCATED
 * (an empty buffer too) and bytes after a valid message are SAM_ERR_TRAILING.
 */
int sam_frame_decode_exact(const uint8_t *buf, size_t len, uint16_t max_payload, struct sam_frame *out);

/*
 * Builds one message into out[0..cap). Refuses (and writes nothing) an unknown type, a control frame with a
 * payload (SAM_ERR_BAD_LENGTH), a data frame without one (SAM_ERR_BAD_LENGTH), a payload above
 * SAM_PAYLOAD_MAX (SAM_ERR_OVERSIZE) and too small a buffer (SAM_ERR_NOSPACE). payload may be NULL only
 * when payload_len is zero. *written receives the message length on success and zero otherwise.
 */
int sam_frame_build(uint8_t *out, size_t cap, uint8_t type, uint8_t seq, const uint8_t *payload,
                    size_t payload_len, size_t *written);

/* An acknowledgement of the data frame numbered seq. */
int sam_build_ack(uint8_t *out, size_t cap, uint8_t seq, size_t *written);

/* A negative acknowledgement. Its sequence number is zero by convention: the sender's own is unreliable. */
int sam_build_nak(uint8_t *out, size_t cap, size_t *written);

/* Receive-side statistics. Every input byte is exactly one of: in a delivered frame, skipped, or buffered:
 * bytes_in == bytes_frames + bytes_skipped + (bytes still buffered). Counters never wrap in practice (64 bit). */
struct sam_stats {
    uint64_t bytes_in;
    uint64_t bytes_frames;
    uint64_t bytes_skipped;
    uint64_t frames_data_seq;
    uint64_t frames_data_nsq;
    uint64_t frames_ack;
    uint64_t frames_nak;
    uint64_t rej_bad_sync;     /* garbage runs between frames (a run is one event however long) */
    uint64_t rej_header_crc;
    uint64_t rej_unknown_type;
    uint64_t rej_oversize;
    uint64_t rej_bad_length;
    uint64_t rej_payload_crc;
    uint64_t rej_truncated;    /* an incomplete frame abandoned by sam_parser_flush() */
    uint64_t rej_trailing;     /* a lone possible SYN byte abandoned by sam_parser_flush() */
};

/* Total rejection events: the sum of every rej_* counter. A transport that answers errors with a NAK can
 * compare this before and after a call. */
uint64_t sam_stats_rejects(const struct sam_stats *stats);

#define SAM_PARSER_MAGIC 0x31464d53u /* "SMF1" in memory order */

/* Treat as opaque. A zeroed parser is unconfigured and every operation refuses it. */
struct sam_parser {
    uint32_t magic;
    uint16_t max_payload;
    bool in_garbage;
    bool drained;
    uint8_t *buf;
    size_t cap;
    size_t head; /* first unconsumed byte */
    size_t fill; /* one past the last buffered byte */
    struct sam_stats stats;
};

/*
 * Configures a parser. storage (storage_len bytes, owned by the caller for the parser's whole life) holds
 * the bytes of one incomplete message, so storage_len must be at least SAM_MESSAGE_OVERHEAD + max_payload;
 * a smaller buffer would let a legal frame stall forever, and is refused. max_payload must be 1..SAM_PAYLOAD_MAX
 * and has no default on purpose: it is the receive-buffer limit of the integration (the reference driver
 * evaluates messages in a 4096-byte buffer, a payload limit of 4086).
 * Returns SAM_OK, or SAM_ERR_ARG with the parser left unconfigured.
 */
int sam_parser_init(struct sam_parser *p, uint8_t *storage, size_t storage_len, uint16_t max_payload);

/*
 * Appends up to len bytes and returns how many it took: fewer than len only when the storage is full, in
 * which case sam_parser_next() must be drained before pushing the rest (it always makes progress on a full
 * buffer). Returns 0 for an unconfigured parser. Invalidates every sam_frame payload pointer returned earlier.
 */
size_t sam_parser_push(struct sam_parser *p, const uint8_t *data, size_t len);

/*
 * Returns the next valid frame (SAM_OK, *out filled; the payload points into the parser's storage and
 * stays valid until the next push or flush), or SAM_NEED_MORE when the buffered bytes hold no complete
 * frame, or SAM_ERR_ARG for an unconfigured parser. Rejected bytes are skipped and counted internally;
 * the call loops until it can return one of the two results above.
 */
int sam_parser_next(struct sam_parser *p, struct sam_frame *out);

/*
 * Declares the stream ended or idle: an incomplete frame is counted as truncated, a lone possible SYN byte
 * as trailing, and the buffer is emptied. Only valid after sam_parser_next() has returned SAM_NEED_MORE
 * since the last push; otherwise it refuses with SAM_ERR_ARG and changes nothing.
 */
int sam_parser_flush(struct sam_parser *p);

/* ------------------------------------------------------------------------------------------------------
 * Command payload (the only known payload of a data frame): an 8-byte header followed by command data.
 * Layout VERIFIED by reading, UNKNOWN on a Surface Pro 11 wire.
 */
#define SAM_PAYLOAD_TYPE_CMD 0x80u
#define SAM_COMMAND_HEADER_LEN 8u
#define SAM_COMMAND_DATA_MAX (SAM_PAYLOAD_MAX - SAM_COMMAND_HEADER_LEN)

struct sam_command {
    uint8_t tc;    /* target category */
    uint8_t tid;   /* target id: who the message is for */
    uint8_t sid;   /* source id: who sent it */
    uint8_t iid;   /* instance id */
    uint16_t rqid; /* request id: pairs a response with its request, and marks an event */
    uint8_t cid;   /* command id */
};

/* Writes the 8 header bytes (type byte 0x80 first). */
void sam_command_encode(uint8_t out[SAM_COMMAND_HEADER_LEN], const struct sam_command *cmd);

/*
 * Splits a frame payload into header and data. SAM_ERR_CMD_SHORT below 8 bytes, SAM_ERR_CMD_TYPE when the
 * first byte is not 0x80. On SAM_OK *data points into payload and *data_len may be zero. Meaning of the
 * tc/tid/cid values is the caller's business: this does not validate them.
 */
int sam_command_decode(const uint8_t *payload, size_t len, struct sam_command *cmd, const uint8_t **data,
                       size_t *data_len);

/* Builds a whole data-frame message carrying a command. type must be DATA_SEQ or DATA_NSQ. */
int sam_build_command(uint8_t *out, size_t cap, uint8_t type, uint8_t seq, const struct sam_command *cmd,
                      const uint8_t *data, size_t data_len, size_t *written);

/*
 * Request ids 1..SAM_EVENT_ID_COUNT are reserved for events; the controller is told the id to use for an
 * event source when the source is enabled (VERIFIED by reading: the id equals the target category). Every
 * other value, including 0, is a request id. The count is 38 in the pinned Linux because the highest known
 * target category is 0x26: a newer category above 38 would need a larger count, so treat it as a version
 * of the protocol knowledge rather than a law of the wire.
 */
#define SAM_EVENT_ID_COUNT 38u

static inline bool sam_rqid_is_event(uint16_t rqid)
{
    return (uint16_t)(rqid - 1u) < SAM_EVENT_ID_COUNT;
}

/*
 * The request id to use after rqid: increments, wraps from 0xFFFF to 0 and jumps over the event range
 * (0 -> SAM_EVENT_ID_COUNT + 1). A starting value inside the event range is a caller bug; it also yields
 * SAM_EVENT_ID_COUNT + 1 here, so the result is never an event id (the reference driver would step to the next
 * event id).
 */
static inline uint16_t sam_rqid_next_request(uint16_t rqid)
{
    if (rqid == 0xFFFFu)
        return 0;
    if (rqid == 0 || sam_rqid_is_event(rqid))
        return (uint16_t)(SAM_EVENT_ID_COUNT + 1u);
    return (uint16_t)(rqid + 1u);
}

_Static_assert(SAM_MESSAGE_OVERHEAD == 10u, "SYN 2 + header 4 + header CRC 2 + payload CRC 2");
_Static_assert(SAM_COMMAND_HEADER_LEN == 8u, "type, tc, tid, sid, iid, rqid(2), cid");
_Static_assert(SAM_PAYLOAD_MAX <= UINT16_MAX, "the length field is 16 bits");

#endif
