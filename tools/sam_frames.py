#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Independent Python reference for the Surface Aggregator Module frame codec (compat/sam-frame.{h,c}).

EXPERIMENTAL and host-only. It exists so the C codec can be checked against a second implementation that was
written separately and is structured differently: a bitwise CRC instead of a nibble table, a whole-buffer
scan with bytes.find instead of a streaming state machine, and exceptions instead of status codes. Both
follow the same written policy (compat/sam-frame.h, docs/SP11-INPUT-STUDY.md section 4), so agreement shows
the two are consistent with each other and with the CRC standard, not that the policy matches a real
controller: nothing here has seen a byte from an SP11 (UNKNOWN until the G1 spike, gate X9).

Facts only were taken from the public protocol sources (Linux v7.4-rc2 7475ea8b58616b33c04afb976cf0fbaad9dd63fb
serial_hub.h and ssh.rst; linux-surface/surface-aggregator-module de6d403852f33f5445c25971a3f25e6ebafbf824
doc/ssh-protocol.txt). No code or prose was copied.

    python3 tools/sam_frames.py crc 313233343536373839        # 0x29b1
    python3 tools/sam_frames.py build ack 1                    # aa55400000017dfaffff
    python3 tools/sam_frames.py parse <hex> [--max-payload N]  # frames and counters; also decodes a capture
"""
import argparse
import binascii
import sys

SYN = b'\xaa\x55'
CRC_INIT = 0xFFFF
CRC_POLY = 0x1021
CRC_CHECK_123456789 = 0x29B1
PAYLOAD_MAX = 0xFFFF
MESSAGE_OVERHEAD = 10

TYPE_DATA_NSQ = 0x00
TYPE_NAK = 0x04
TYPE_ACK = 0x40
TYPE_DATA_SEQ = 0x80
KNOWN_TYPES = (TYPE_DATA_NSQ, TYPE_NAK, TYPE_ACK, TYPE_DATA_SEQ)
DATA_TYPES = (TYPE_DATA_NSQ, TYPE_DATA_SEQ)
TYPE_NAMES = {TYPE_DATA_NSQ: 'data_nsq', TYPE_NAK: 'nak', TYPE_ACK: 'ack', TYPE_DATA_SEQ: 'data_seq'}

PAYLOAD_TYPE_CMD = 0x80
COMMAND_HEADER_LEN = 8
EVENT_ID_COUNT = 38

# The counter names are shared with the native driver's canonical output (tools/test-sam-frames.c).
STAT_ORDER = ('bytes_in', 'bytes_frames', 'bytes_skipped', 'data_seq', 'data_nsq', 'ack', 'nak', 'bad_sync',
              'header_crc', 'unknown_type', 'oversize', 'bad_length', 'payload_crc', 'truncated', 'trailing')


class FrameError(Exception):
    """A refusal; the message is the same short name the C codec's sam_status_name() returns."""


def crc16(data, crc=CRC_INIT):
    """CRC-16/CCITT-FALSE, one bit at a time: polynomial 0x1021, most significant bit first."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc16_hqx(data, crc=CRC_INIT):
    """The same algorithm through the standard library (binascii.crc_hqx is CRC-CCITT, MSB first)."""
    return binascii.crc_hqx(bytes(data), crc)


def le16(value):
    return bytes((value & 0xFF, value >> 8))


def build_frame(ftype, seq, payload=b''):
    """One message. Refusal order matches sam_frame_build(): type, then size, then type/length consistency."""
    if ftype not in KNOWN_TYPES or not 0 <= seq <= 0xFF:
        raise FrameError('bad-argument')
    if len(payload) > PAYLOAD_MAX:
        raise FrameError('oversize')
    if (ftype in DATA_TYPES) != (len(payload) != 0):
        raise FrameError('bad-length')
    header = bytes((ftype,)) + le16(len(payload)) + bytes((seq,))
    return SYN + header + le16(crc16(header)) + bytes(payload) + le16(crc16(payload))


def build_ack(seq):
    return build_frame(TYPE_ACK, seq)


def build_nak():
    return build_frame(TYPE_NAK, 0)


def encode_command(tc, tid, sid, iid, rqid, cid):
    return bytes((PAYLOAD_TYPE_CMD, tc, tid, sid, iid)) + le16(rqid) + bytes((cid,))


def decode_command(payload):
    """Returns ((tc, tid, sid, iid, rqid, cid), data)."""
    if len(payload) < COMMAND_HEADER_LEN:
        raise FrameError('command-short')
    if payload[0] != PAYLOAD_TYPE_CMD:
        raise FrameError('command-type')
    rqid = payload[5] | (payload[6] << 8)
    return (payload[1], payload[2], payload[3], payload[4], rqid, payload[7]), bytes(payload[COMMAND_HEADER_LEN:])


def build_command(ftype, seq, tc, tid, sid, iid, rqid, cid, data=b''):
    if ftype not in DATA_TYPES:
        raise FrameError('bad-argument')
    if len(data) > PAYLOAD_MAX - COMMAND_HEADER_LEN:
        raise FrameError('oversize')
    return build_frame(ftype, seq, encode_command(tc, tid, sid, iid, rqid, cid) + bytes(data))


def rqid_is_event(rqid):
    return 1 <= rqid <= EVENT_ID_COUNT


def rqid_next_request(rqid):
    if rqid == 0xFFFF:
        return 0
    if rqid == 0 or rqid_is_event(rqid):
        return EVENT_ID_COUNT + 1
    return rqid + 1


def decode_exact(data, max_payload):
    """One message that must fill the buffer; returns (type, seq, payload). Same order of checks as C."""
    status, frame = judge(data, 0, max_payload)
    if status == 'need-more':
        raise FrameError('truncated')
    if status != 'ok':
        raise FrameError(status)
    if len(data) != MESSAGE_OVERHEAD + len(frame[2]):
        raise FrameError('trailing')
    return frame


def judge(data, pos, max_payload):
    """Judges the candidate at data[pos:]; returns ('ok', frame) or a status name. Mirrors sam_frame_parse()."""
    avail = len(data) - pos
    if avail >= 1 and data[pos] != SYN[0]:
        return 'bad-sync', None
    if avail >= 2 and data[pos + 1] != SYN[1]:
        return 'bad-sync', None
    if avail < 8:
        return 'need-more', None
    header = data[pos + 2:pos + 6]
    if crc16(header) != int.from_bytes(data[pos + 6:pos + 8], 'little'):
        return 'header-crc', None
    ftype, length, seq = header[0], header[1] | (header[2] << 8), header[3]
    if ftype not in KNOWN_TYPES:
        return 'unknown-type', None
    if length > max_payload:
        return 'oversize', None
    if (ftype in DATA_TYPES) != (length != 0):
        return 'bad-length', None
    if avail < MESSAGE_OVERHEAD + length:
        return 'need-more', None
    payload = bytes(data[pos + 8:pos + 8 + length])
    if crc16(payload) != int.from_bytes(data[pos + 8 + length:pos + 10 + length], 'little'):
        return 'payload-crc', None
    return 'ok', (ftype, seq, payload)


def parse_stream(data, max_payload):
    """Parses a complete stream and then declares it ended. Returns (frames, stats).

    Chunking cannot change the result of the streaming C parser, so one pass over the whole buffer is the
    reference for every chunking."""
    data = bytes(data)
    stats = dict.fromkeys(STAT_ORDER, 0)
    stats['bytes_in'] = len(data)
    frames = []
    reject_names = {'header-crc': 'header_crc', 'unknown-type': 'unknown_type', 'oversize': 'oversize',
                    'bad-length': 'bad_length', 'payload-crc': 'payload_crc'}
    pos = 0
    in_garbage = False
    while pos < len(data):
        found = data.find(SYN, pos)
        if found < 0:
            # A final 0xAA could still become a SYN, so it is not garbage yet.
            keep = len(data) - 1 if data[-1] == SYN[0] else len(data)
            found = max(keep, pos)
        if found > pos:
            if not in_garbage:
                in_garbage = True
                stats['bad_sync'] += 1
            stats['bytes_skipped'] += found - pos
            pos = found
        if len(data) - pos < 2:
            break
        in_garbage = False
        status, frame = judge(data, pos, max_payload)
        if status == 'need-more':
            break
        if status == 'ok':
            ftype, _, payload = frame
            frames.append(frame)
            stats['bytes_frames'] += MESSAGE_OVERHEAD + len(payload)
            stats[TYPE_NAMES[ftype]] += 1
            pos += MESSAGE_OVERHEAD + len(payload)
            continue
        stats[reject_names[status]] += 1
        in_garbage = True  # what is left of the rejected frame belongs to the same incident
        stats['bytes_skipped'] += 2
        pos += 2
    leftover = len(data) - pos
    if leftover >= 2:
        stats['truncated'] += 1
    elif leftover == 1:
        stats['trailing'] += 1
    stats['bytes_skipped'] += leftover
    return frames, stats


def canonical(frames, stats):
    """The text the native driver prints for the same input (frames in order, then the counters)."""
    lines = ['F %02x %02x %s' % (ftype, seq, payload.hex() or '-') for ftype, seq, payload in frames]
    lines.append('S ' + ' '.join('%s=%d' % (name, stats[name]) for name in STAT_ORDER))
    return '\n'.join(lines)


def _main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    crc = sub.add_parser('crc')
    crc.add_argument('hex')
    build = sub.add_parser('build')
    build.add_argument('type', choices=('data_seq', 'data_nsq', 'ack', 'nak'))
    build.add_argument('seq', type=lambda s: int(s, 0))
    build.add_argument('payload_hex', nargs='?', default='')
    parse = sub.add_parser('parse')
    parse.add_argument('hex')
    parse.add_argument('--max-payload', type=lambda s: int(s, 0), default=4086)
    args = parser.parse_args(argv)
    if args.command == 'crc':
        print('0x%04x' % crc16(bytes.fromhex(args.hex)))
    elif args.command == 'build':
        types = {name: value for value, name in TYPE_NAMES.items()}
        try:
            print(build_frame(types[args.type], args.seq, bytes.fromhex(args.payload_hex)).hex())
        except FrameError as error:
            print('refused: %s' % error, file=sys.stderr)
            return 1
    else:
        print(canonical(*parse_stream(bytes.fromhex(args.hex), args.max_payload)))
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
