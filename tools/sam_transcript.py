#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Transcripts of the Surface Aggregator UART link: a text format, an analyser, a capture classifier and a FIFO word unpacker.

EXPERIMENTAL and host-only. This is what reads the evidence of the first hardware runs on a host: a passive receive capture
(raw bytes the engine's FIFO gave up, possibly as 32-bit words), or a two-way conversation. It never guesses silently: it
reports every violation of the written protocol rules it finds (a data frame nobody acknowledged, a response nobody asked
for, a message not addressed to the host, a request id in the event range on a request), and for a raw capture it says which
of the plausible readings produced valid frames. Frame policy is tools/sam_frames.py and tools/sam_session.py (their
evidence labels apply: read in pinned public sources, UNKNOWN on a real controller until the first capture).

Text format, one record per line, '#' starts a comment:

    <t_us> <rx|tx> <hex bytes, spaces allowed>        # rx = controller to host, tx = host to controller

    python3 tools/sam_transcript.py analyze capture.txt
    python3 tools/sam_transcript.py classify capture.txt
    python3 tools/sam_transcript.py words words.txt [--last-valid N]     # one 32-bit word per token, hex
"""
import argparse
import collections
import dataclasses
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sam_frames as fr  # noqa: E402
import sam_session as ss  # noqa: E402

ACK_GRACE_US = 500_000     # an ACK should follow its data frame well inside the 1 s re-send timer (ssh_packet_layer.c:195-201)

COMMANDS = {
    (ss.TC_SAM, ss.CID_FW_VERSION): 'fw-version', (ss.TC_SAM, ss.CID_DISPLAY_OFF): 'display-off',
    (ss.TC_SAM, ss.CID_DISPLAY_ON): 'display-on', (ss.TC_SAM, ss.CID_D0_EXIT): 'd0-exit',
    (ss.TC_SAM, ss.CID_D0_ENTRY): 'd0-entry', (ss.TC_SAM, ss.CID_SAM_EVENT_ENABLE): 'sam-event-enable',
    (ss.TC_SAM, ss.CID_SAM_EVENT_DISABLE): 'sam-event-disable', (ss.TC_KIP, ss.CID_KIP_STATE): 'kip-state',
    (ss.TC_REG, ss.CID_REG_EVENT_ENABLE): 'reg-event-enable', (ss.TC_REG, ss.CID_REG_EVENT_DISABLE): 'reg-event-disable',
    (ss.TC_HID, ss.HID_OUTPUT): 'hid-output', (ss.TC_HID, ss.HID_GET_FEATURE): 'hid-get-feature',
    (ss.TC_HID, ss.HID_SET_FEATURE): 'hid-set-feature', (ss.TC_HID, ss.HID_GET_DESCRIPTOR): 'hid-get-descriptor',
}


class TranscriptError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Record:
    t_us: int
    direction: str            # 'rx' or 'tx'
    data: bytes


def parse_transcript(text):
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) != 3 or parts[1] not in ('rx', 'tx'):
            raise TranscriptError(f'line {number}: expected "<t_us> <rx|tx> <hex>"')
        try:
            t_us = int(parts[0])
            data = bytes.fromhex(parts[2].replace(' ', ''))
        except ValueError:
            raise TranscriptError(f'line {number}: bad number or hex') from None
        if t_us < 0 or (records and t_us < records[-1].t_us):
            raise TranscriptError(f'line {number}: time must not go backwards')
        if not data:
            raise TranscriptError(f'line {number}: empty record')
        records.append(Record(t_us, parts[1], data))
    return records


def format_transcript(records, header=()):
    lines = [f'# {h}' for h in header]
    lines += [f'{r.t_us} {r.direction} {r.data.hex()}' for r in records]
    return '\n'.join(lines) + '\n'


def from_wire(log):
    return [Record(t, d, data) for t, d, data in log]


@dataclasses.dataclass
class Report:
    frames: dict = dataclasses.field(default_factory=lambda: {'rx': 0, 'tx': 0})
    junk: dict = dataclasses.field(default_factory=lambda: {'rx': 0, 'tx': 0})
    bytes_skipped: dict = dataclasses.field(default_factory=lambda: {'rx': 0, 'tx': 0})
    retransmits: int = 0
    requests: list = dataclasses.field(default_factory=list)      # dicts: t_us, name, rqid, seq, answered_after_us
    events: list = dataclasses.field(default_factory=list)        # dicts: t_us, tc, iid, cid, length
    violations: list = dataclasses.field(default_factory=list)    # strings, in time order
    lines: list = dataclasses.field(default_factory=list)         # the annotated listing


def _describe(direction, ftype, seq, payload):
    name = fr.TYPE_NAMES[ftype]
    cmd = ss.command_fields(payload) if payload else None
    if cmd is None:
        return f'{direction} {name} seq={seq}' + (f' payload={len(payload)}B (not a command)' if payload else '')
    tc, tid, sid, iid, rqid, cid, data = cmd
    kind = 'event' if fr.rqid_is_event(rqid) else ('request' if direction == 'tx' else 'response')
    label = COMMANDS.get((tc, cid), f'tc={tc:#04x} cid={cid:#04x}')
    return f'{direction} {name} seq={seq} {kind} {label} tid={tid} sid={sid} iid={iid} rqid={rqid} data={len(data)}B'


def analyze(records):
    """Annotates a transcript and lists every protocol-rule violation. Never raises on bad input: junk is data."""
    rep = Report()
    scanners = {'rx': ss.FrameScanner(), 'tx': ss.FrameScanner()}
    data_seen = {'rx': {}, 'tx': {}}               # seq -> (t_us, payload) of the last data frame with that seq
    unacked = {'rx': {}, 'tx': {}}                 # seq -> t_us of a sequenced frame nobody has acknowledged
    asked = {}                                     # rqid -> request dict
    for rec in records:
        other = 'tx' if rec.direction == 'rx' else 'rx'
        for item in scanners[rec.direction].feed(rec.data, rec.t_us):
            if item[0] == 'junk':
                rep.junk[rec.direction] += 1
                rep.violations.append(f'{rec.t_us} {rec.direction}: junk ({item[1]})')
                rep.lines.append(f'{rec.t_us} {rec.direction} JUNK {item[1]}')
                continue
            _, ftype, seq, payload = item
            rep.frames[rec.direction] += 1
            rep.lines.append(f'{rec.t_us} ' + _describe(rec.direction, ftype, seq, payload))
            if ftype == fr.TYPE_ACK:
                if unacked[other].pop(seq, None) is None:
                    rep.violations.append(f'{rec.t_us} {rec.direction}: ACK for seq {seq} that is not outstanding')
            elif ftype == fr.TYPE_NAK:
                continue
            else:
                if ftype == fr.TYPE_DATA_SEQ:
                    previous = data_seen[rec.direction].get(seq)
                    if previous and previous[1] == payload and seq in unacked[rec.direction]:
                        rep.retransmits += 1
                    elif unacked[rec.direction]:
                        # One un-ACKed sequenced frame per direction is the rule both ends rely on (ssh.rst, "Limitations").
                        rep.violations.append(f'{rec.t_us} {rec.direction}: sequenced frame {seq} sent while '
                                              f'{sorted(unacked[rec.direction])} was unacknowledged')
                    unacked[rec.direction].setdefault(seq, rec.t_us)
                data_seen[rec.direction][seq] = (rec.t_us, payload)
                _command(rep, rec, ftype, payload, asked)
    for direction, pending in unacked.items():
        for seq, t_us in sorted(pending.items()):
            late = records[-1].t_us - t_us if records else 0
            if late >= ACK_GRACE_US:
                rep.violations.append(f'{t_us} {direction}: data frame seq {seq} never acknowledged ({late} us before the end)')
    for rq in asked.values():
        if rq['answered_after_us'] is None:
            rep.violations.append(f"{rq['t_us']} tx: request {rq['name']} rqid {rq['rqid']} never answered")
    return rep


def _command(rep, rec, ftype, payload, asked):
    cmd = ss.command_fields(payload)
    if cmd is None:
        rep.violations.append(f'{rec.t_us} {rec.direction}: data frame payload is not a command')
        return
    tc, tid, sid, iid, rqid, cid, data = cmd
    if rec.direction == 'tx':
        if fr.rqid_is_event(rqid):
            rep.violations.append(f'{rec.t_us} tx: request uses event id {rqid}')
        if sid != ss.TID_HOST:
            rep.violations.append(f'{rec.t_us} tx: request source id is {sid}, not the host')
        entry = dict(t_us=rec.t_us, name=COMMANDS.get((tc, cid), f'{tc:#04x}/{cid:#04x}'), rqid=rqid,
                     answered_after_us=None)
        if rqid in asked and asked[rqid]['answered_after_us'] is None:
            rep.violations.append(f'{rec.t_us} tx: request id {rqid} reused while outstanding')
        asked[rqid] = entry
        rep.requests.append(entry)
        return
    if tid != ss.TID_HOST:
        rep.violations.append(f'{rec.t_us} rx: message for target {tid}, not the host (the host drops it)')
    if fr.rqid_is_event(rqid):
        rep.events.append(dict(t_us=rec.t_us, tc=tc, iid=iid, cid=cid, length=len(data), sequenced=ftype == fr.TYPE_DATA_SEQ))
    elif rqid in asked and asked[rqid]['answered_after_us'] is None:
        asked[rqid]['answered_after_us'] = rec.t_us - asked[rqid]['t_us']
    else:
        rep.violations.append(f'{rec.t_us} rx: response with request id {rqid} that was not asked')


# ---- raw captures ------------------------------------------------------------------------------------------------

def unpack_words(words, order, last_valid=4):
    """Bytes of 32-bit FIFO words. order 'le' = the low byte is the first received (what four 8-bit characters packed from
    bit 0 give when the word is loaded as an integer); 'be' = the high byte first. last_valid counts the bytes of the last word.
    Only the LAST word may be partial here, as in Linux's readers (qcom_geni_serial.c:412-422,519-531). Whether a polled reader
    can see a partial word in the middle of the FIFO (the stale timer is 160 bit times = 40 us at 4 Mbps) is UNKNOWN: the capture
    must record the FIFO status once per drain, not once per capture."""
    if order not in ('le', 'be') or not 1 <= last_valid <= 4:
        raise ValueError('order must be le or be, last_valid 1..4')
    out = bytearray()
    for index, word in enumerate(words):
        raw = (word & 0xFFFFFFFF).to_bytes(4, 'little' if order == 'le' else 'big')
        # The first received byte is the first of `raw` in both orders, so the short last word keeps its head.
        out += raw[:last_valid] if index == len(words) - 1 else raw
    return bytes(out)


def count_valid_frames(data):
    frames, stats = fr.parse_stream(data, 4086)
    return len(frames), stats


def best_word_order(words, last_valid=4):
    """Which byte order inside the words gives valid frames. 'ambiguous' when both do or neither does: say so, do not pick."""
    scores = {o: count_valid_frames(unpack_words(words, o, last_valid))[0] for o in ('le', 'be')}
    if scores['le'] and not scores['be']:
        return 'le', scores
    if scores['be'] and not scores['le']:
        return 'be', scores
    return 'ambiguous', scores


def classify_capture(data):
    """A first reading of a passive receive capture, in decreasing order of how much it proves."""
    if not data:
        return {'verdict': 'SILENT', 'why': 'no bytes: link idle, clock or format wrong, or the controller sends nothing unprompted'}
    frames, stats = fr.parse_stream(data, 4086)
    if len(set(data)) == 1:
        return {'verdict': 'STUCK', 'why': f'every byte is {data[0]:#04x}: a floating or held line, not a data stream'}
    if frames:
        seqs = collections.Counter((t, s, p) for t, s, p in frames if t in fr.DATA_TYPES)
        again = sum(n - 1 for n in seqs.values())
        why = f'{len(frames)} valid frames'
        if again:
            why += f', {again} of them repeats: the controller is re-sending frames nobody acknowledged'
        return {'verdict': 'FRAMES', 'why': why, 'frames': len(frames), 'repeats': again, 'stats': stats}
    if fr.SYN in data:
        return {'verdict': 'SYN_NO_FRAME', 'why': 'SYN bytes without one valid frame: baud, format, byte order or CRC differs', 'stats': stats}
    return {'verdict': 'NOISE', 'why': 'no SYN anywhere: wrong baud or format, or not this protocol', 'stats': stats}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('analyze', 'classify', 'words'):
        p = sub.add_parser(name)
        p.add_argument('file')
        if name == 'words':
            p.add_argument('--last-valid', type=int, default=4)
    args = ap.parse_args(argv)
    text = Path(args.file).read_text()
    if args.cmd == 'words':
        words = [int(tok, 16) for tok in re.findall(r'[0-9a-fA-F]{1,8}', text)]
        order, scores = best_word_order(words, args.last_valid)
        print(order, scores)
        if order != 'ambiguous':
            print(classify_capture(unpack_words(words, order, args.last_valid)))
        return 0 if order != 'ambiguous' else 1
    if args.cmd == 'classify':
        data = b''.join(r.data for r in parse_transcript(text) if r.direction == 'rx')
        result = classify_capture(data)
        print(result)
        return 0 if result['verdict'] == 'FRAMES' else 1
    rep = analyze(parse_transcript(text))
    print('\n'.join(rep.lines))
    print(f'frames {rep.frames} junk {rep.junk} retransmits {rep.retransmits} events {len(rep.events)} '
          f'requests {len(rep.requests)}')
    for v in rep.violations:
        print('VIOLATION', v)
    return 1 if rep.violations else 0


if __name__ == '__main__':
    sys.exit(main())
