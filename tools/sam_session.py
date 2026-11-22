#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host reference model of the Surface Aggregator transport and request layers, a fake controller, and the bring-up script.

EXPERIMENTAL and host-only. It models, as one deterministic simulation with an injected clock, what a host-side SAM
driver has to do on the UART link: acknowledge, re-send, filter repeats, number requests, match responses, enable the
event sources the keyboard cover needs and read its HID descriptors. The other end is `FakeEc`, a controller written
from the same public facts, with switchable faults. It is the oracle that a C implementation in a kext or an EL2 payload
is checked against before any boot, and the script (`bring_up`) is the exact request order the first hardware talk would
send, so each hardware authorisation can name a step of it.

Evidence: every protocol constant below was read in the pinned Linux tree (v7.4-rc2, 7475ea8b58616b33c04afb976cf0fbaad9dd63fb)
and is named where it is used. Nothing here has seen a byte from a Surface Pro 11: a pass shows that the model matches the
written sources and its own tests, not that a real controller behaves like `FakeEc` (UNKNOWN until the first capture, see
docs/SP11-INPUT-STUDY.md section 5). Behaviour that is this project's own policy and not Linux's is marked POLICY.

No code or prose was copied; the CRC and the frame codec are tools/sam_frames.py.
"""
import collections
import dataclasses
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sam_frames as fr  # noqa: E402

# serial_hub.h:289-295 (target ids) and :308-348 (target categories).
TID_HOST, TID_SAM, TID_KIP = 0x00, 0x01, 0x02
TC_SAM, TC_KIP, TC_HID, TC_REG = 0x01, 0x0E, 0x15, 0x21
# controller.c:1819-1851 (internal requests), :1893-1897 (event enable), controller.h registries (SAM 0x0b/0x0c, REG 0x01/0x02).
CID_FW_VERSION, CID_DISPLAY_OFF, CID_DISPLAY_ON, CID_D0_EXIT, CID_D0_ENTRY = 0x13, 0x15, 0x16, 0x33, 0x34
CID_SAM_EVENT_ENABLE, CID_SAM_EVENT_DISABLE = 0x0B, 0x0C
CID_REG_EVENT_ENABLE, CID_REG_EVENT_DISABLE = 0x01, 0x02
CID_KIP_STATE = 0x2C                    # surface_aggregator_hub.c:291-299 (the hub file is drivers/platform/surface/)
HID_OUTPUT, HID_GET_FEATURE, HID_SET_FEATURE, HID_GET_DESCRIPTOR = 0x01, 0x02, 0x03, 0x04   # surface_hid.c:35-40
DESC_HID, DESC_REPORT, DESC_ATTRS = 0, 1, 2                                                  # surface_hid_core.h:19-23
SLICE_DATA = 0x76                       # surface_hid.c:51-57: the slice length the Windows driver uses
EVENT_SEQUENCED = 1                     # controller.h:29
HID_EVENT_FLAGS = 0                     # surface_hid.c:216: HID input events are enabled UNSEQUENCED (DATA_NSQ, no ACK pacing)
KIP_EVENT_FLAGS = EVENT_SEQUENCED       # surface_aggregator_hub.c:179: only the KIP hub asks for sequenced events
PACKET_TIMEOUT_US = 1_000_000           # ssh_packet_layer.c:195-201
PACKET_TRIES = 3                        # ssh_packet_layer.c:176-184
REQUEST_TIMEOUT_US = 3_000_000          # ssh_request_layer.c:29-36
MAX_PENDING_REQUESTS = 3                # ssh_request_layer.c:53
SEEN_SEQS = 8                           # ssh_packet_layer.h:107; initialised to an impossible 0xFFFF (ssh_packet_layer.c:2057-2059)
KIP_CONNECT_DELAY_US = 250_000          # surface_aggregator_hub.c:283-289
NAK_MIN_INTERVAL_US = 50_000            # POLICY: Linux sends one NAK per bad scan; a noisy line must not fill the transmitter.
STALL_FLUSH_US = 100_000                # POLICY: a frame that has not grown for this long is abandoned so a lying length cannot hold the parser.
REPORT_DESC_MAX = 4096                  # POLICY: the same bound as tools/sam_hid_desc.MAX_BYTES; a longer claim is a lie or corruption.


class SamError(Exception):
    """A request or the bring-up failed; .step names where."""

    def __init__(self, message, step=None):
        super().__init__(message)
        self.step = step


@dataclasses.dataclass
class Event:
    tc: int
    sid: int
    iid: int
    cid: int
    rqid: int
    data: bytes
    t_us: int


@dataclasses.dataclass
class Request:
    tc: int
    tid: int
    iid: int
    cid: int
    payload: bytes
    wants_response: bool
    rqid: int = 0
    seq: int = -1
    tries: int = 0
    state: str = 'queued'     # queued -> sent -> (acked) -> done | failed
    deadline_us: int = 0
    request_deadline_us: int = 0
    response: bytes = b''
    error: str = ''

    @property
    def finished(self):
        return self.state in ('done', 'failed')


class FrameScanner:
    """Incremental receive parser. Policy matches compat/sam-frame.h: a rejected candidate discards its two SYN bytes
    and rescans; garbage between frames is one incident however it was chunked."""

    def __init__(self, max_payload=4086, cap=8192):
        self.max_payload, self.cap = max_payload, cap
        self.buf = bytearray()
        self.stats = collections.Counter()
        self._in_garbage = False
        self._partial_since = None
        self._partial_len = 0

    def feed(self, data, now_us):
        self.buf += data
        out = []
        if len(self.buf) > self.cap:
            del self.buf[:len(self.buf) - self.cap]
            self.stats['overflow'] += 1
            out.append(('junk', 'overflow'))
            self._partial_since, self._partial_len = None, 0      # the armed timer belonged to bytes that are gone
        return out + self._scan(now_us)

    def poll(self, now_us):
        if self._partial_since is not None and now_us - self._partial_since >= STALL_FLUSH_US:
            self._partial_since = None
            del self.buf[:2]
            self.stats['stalled'] += 1
            return [('junk', 'stalled')] + self._scan(now_us)
        return []

    def _skip(self, count, out):
        self.stats['bytes_skipped'] += count
        if not self._in_garbage:
            self._in_garbage = True
            self.stats['bad_sync'] += 1
            out.append(('junk', 'bad-sync'))
        del self.buf[:count]
        self._partial_since, self._partial_len = None, 0         # a new candidate starts at the new front

    def _scan(self, now_us):
        out = []
        while self.buf:
            at = self.buf.find(fr.SYN)
            if at < 0:
                # A trailing 0xAA may still become a SYN with the next chunk.
                drop = len(self.buf) - (1 if self.buf[-1] == fr.SYN[0] else 0)
                if drop:
                    self._skip(drop, out)
                break
            if at:
                self._skip(at, out)
                continue
            status, frame = fr.judge(self.buf, 0, self.max_payload)
            if status == 'need-more':
                # The timer measures silence, not age: a frame that keeps growing, even slowly, is not stalled.
                if self._partial_since is None or len(self.buf) > self._partial_len:
                    self._partial_since, self._partial_len = now_us, len(self.buf)
                break
            self._partial_since = None
            self._in_garbage = False
            if status == 'ok':
                ftype, seq, payload = frame
                self.stats[fr.TYPE_NAMES[ftype]] += 1
                out.append(('frame', ftype, seq, payload))
                del self.buf[:fr.MESSAGE_OVERHEAD + len(payload)]
            else:
                self.stats[status.replace('-', '_')] += 1
                out.append(('junk', status))
                del self.buf[:2]
        return out


def command_fields(payload):
    """(tc, tid, sid, iid, rqid, cid, data) of a command payload, or None when it is not one (ssh_command, serial_hub.h:96-104)."""
    if len(payload) < fr.COMMAND_HEADER_LEN or payload[0] != fr.PAYLOAD_TYPE_CMD:
        return None
    (tc, tid, sid, iid, rqid, cid), data = fr.decode_command(payload)
    return tc, tid, sid, iid, rqid, cid, data


class HostStack:
    """The host's packet and request layers over a byte link. Time is passed in; nothing sleeps."""

    def __init__(self, max_payload=4086):
        self.scanner = FrameScanner(max_payload)
        self.stats = collections.Counter()
        self.events = collections.deque()
        self.rqid = 0
        self.tx_seq = 0
        self.pending = None           # the one sequenced frame awaiting its ACK
        self.inflight = []            # requests awaiting a response
        self.data_q = collections.deque()
        self.ctrl_q = collections.deque()
        self.seen = collections.deque([0xFFFF] * SEEN_SEQS, maxlen=SEEN_SEQS)
        self._resend = False
        self._last_nak_us = None

    # -- request layer ----------------------------------------------------------------------------------------

    def submit(self, tc, tid, iid, cid, payload=b'', wants_response=True, now_us=0):
        req = Request(tc, tid, iid, cid, bytes(payload), wants_response)
        req.rqid = self.rqid
        self.rqid = fr.rqid_next_request(self.rqid)           # skips the event ids 1..38 (serial_hub.h:217-220)
        req.request_deadline_us = now_us + REQUEST_TIMEOUT_US   # POLICY: Linux starts its 3 s at transmission (RTL:36), this model at submit
        self.data_q.append(req)
        return req

    def _frame_of(self, req):
        return fr.build_command(fr.TYPE_DATA_SEQ, req.seq, req.tc, req.tid, TID_HOST, req.iid, req.rqid, req.cid, req.payload)

    def _fail(self, req, why):
        req.state, req.error = 'failed', why
        self.stats['request_failed_' + why] += 1
        if req in self.inflight:
            self.inflight.remove(req)
        if self.pending is req:
            self.pending = None

    # -- receive ----------------------------------------------------------------------------------------------

    def feed(self, data, now_us):
        for item in self.scanner.feed(data, now_us):
            self._handle(item, now_us)

    def _handle(self, item, now_us):
        if item[0] == 'junk':
            self.stats['junk_' + item[1].replace('-', '_')] += 1
            # A NAK asks the sender to re-send everything unacknowledged (ssh.rst, "Flow Sequence").
            if self._last_nak_us is None or now_us - self._last_nak_us >= NAK_MIN_INTERVAL_US:
                self._last_nak_us = now_us
                self.ctrl_q.append(fr.build_nak())
                self.stats['nak_sent'] += 1
            return
        _, ftype, seq, payload = item
        if ftype == fr.TYPE_ACK:
            if self.pending is not None and self.pending.seq == seq:
                self._acked(self.pending)
            else:
                self.stats['ack_unmatched'] += 1
        elif ftype == fr.TYPE_NAK:
            self.stats['nak_received'] += 1
            if self.pending is not None:
                self._resend = True
        else:
            if ftype == fr.TYPE_DATA_SEQ:
                # The ACK goes out before the repeat check, so a re-sent frame is always acknowledged (ssh_packet_layer.c:1777-1783).
                self.ctrl_q.append(fr.build_ack(seq))
                if seq in self.seen:
                    self.stats['repeat_ignored'] += 1
                    return
                self.seen.append(seq)
            self._command(payload, now_us)

    def _acked(self, req):
        self.pending = None
        if req.state == 'sent':
            req.state = 'acked'
        if not req.wants_response and req.state != 'failed':
            req.state = 'done'

    def _command(self, payload, now_us):
        fields = command_fields(payload)
        if fields is None:
            self.stats['command_malformed'] += 1
            return
        tc, tid, sid, iid, rqid, cid, data = fields
        if tid != TID_HOST:                                   # ssh_request_layer.c:928-932
            self.stats['not_for_us'] += 1
            return
        if fr.rqid_is_event(rqid):
            self.events.append(Event(tc, sid, iid, cid, rqid, data, now_us))
            return
        for req in list(self.inflight):
            if req.rqid == rqid:
                self.inflight.remove(req)
                req.state, req.response = 'done', data
                if self.pending is req:                       # the response proves delivery; stop re-sending the request
                    self.pending = None
                    self.stats['implicit_ack'] += 1
                return
        self.stats['response_unmatched'] += 1

    # -- transmit ---------------------------------------------------------------------------------------------

    def poll_tx(self, now_us):
        self._timeouts(now_us)
        self.scanner_poll(now_us)
        out = bytearray()
        while self.ctrl_q:
            out += self.ctrl_q.popleft()
        if self.pending is not None and self._resend:
            self._resend = False
            if self.pending.tries >= PACKET_TRIES:
                self._fail(self.pending, 'packet-tries')
            else:
                self.pending.tries += 1
                self.pending.deadline_us = now_us + PACKET_TIMEOUT_US
                out += self._frame_of(self.pending)
                self.stats['resent'] += 1
        # One un-ACKed sequenced frame at a time, and at most three requests awaiting responses.
        while self.pending is None and self.data_q and len(self.inflight) < MAX_PENDING_REQUESTS:
            req = self.data_q.popleft()
            req.seq, self.tx_seq = self.tx_seq, (self.tx_seq + 1) & 0xFF
            req.tries, req.state = 1, 'sent'
            req.deadline_us = now_us + PACKET_TIMEOUT_US
            self.pending = req
            if req.wants_response:
                self.inflight.append(req)
            out += self._frame_of(req)
        return bytes(out)

    def scanner_poll(self, now_us):
        for item in self.scanner.poll(now_us):
            self._handle(item, now_us)

    def _timeouts(self, now_us):
        for req in list(self.inflight):
            if now_us >= req.request_deadline_us:
                self._fail(req, 'request-timeout')
        for req in list(self.data_q):
            if now_us >= req.request_deadline_us:
                self.data_q.remove(req)
                self._fail(req, 'request-timeout')
        if self.pending is not None and now_us >= self.pending.deadline_us and not self._resend:
            self._resend = True
            self.stats['ack_timeout'] += 1


@dataclasses.dataclass
class HidDevice:
    iid: int
    vendor: int
    product: int
    version: int
    report_descriptor: bytes
    country: int = 0

    def hid_descriptor(self):
        """The 9-byte HID class descriptor of surface_hid_core.h:25-35."""
        return bytes((9, 0x21)) + (0x0111).to_bytes(2, 'little') + bytes((self.country, 1, 0x22)) + \
            len(self.report_descriptor).to_bytes(2, 'little')

    def attributes(self):
        return (32).to_bytes(4, 'little') + self.vendor.to_bytes(2, 'little') + self.product.to_bytes(2, 'little') + \
            self.version.to_bytes(2, 'little') + bytes(22)

    def entry(self, which):
        return {DESC_HID: self.hid_descriptor(), DESC_REPORT: self.report_descriptor, DESC_ATTRS: self.attributes()}[which]


@dataclasses.dataclass
class Faults:
    drop_acks: int = 0                 # the EC does not acknowledge the next n host frames
    corrupt_tx: int = 0                # flip one bit in each of the EC's next n frames
    duplicate_tx: int = 0              # send each of the next n frames twice
    silent: bool = False               # acknowledge, never answer
    deaf: bool = False                 # neither acknowledge nor answer
    wrong_tid: int = 0                 # answer the next n requests with a target id that is not the host
    wrong_rqid: int = 0                # answer the next n requests with an unrelated request id
    descriptor_slice_over: int = 0     # a descriptor slice claims more data than the buffer can hold
    descriptor_overlong: int = 0       # every slice carries this many bytes more than the request asked for (and says so)
    descriptor_short_total: int = 0    # the last slice ends early: the total is shorter than the descriptor said
    nonzero_status: int = 0            # u8 results of the next n internal requests are non-zero
    descriptor_stall: bool = False     # every slice answers length 0, end 0: the read makes no progress
    descriptor_rewind: bool = False    # every slice answers from offset 0: the read goes round in circles


class FakeEc:
    """The controller side of the same protocol, written from the public sources. Deterministic."""

    def __init__(self, devices=(), fw_version=0x00010203, kip_connected=True, hid_events_without_enable=False,
                 sequenced_default=True, faults=None, max_slice=SLICE_DATA):
        self.devices = {d.iid: d for d in devices}
        self.fw_version, self.kip_connected = fw_version, kip_connected
        self.hid_events_without_enable = hid_events_without_enable
        self.sequenced_default = sequenced_default
        self.faults = faults or Faults()
        self.max_slice = max_slice
        self.scanner = FrameScanner()
        self.stats = collections.Counter()
        self.d0 = False
        self.display_off = False       # the display-off state holds events back and raises the wake line (controller.c:2019-2039)
        self.wake_line = False
        self.held = []
        self.sam_events = {}           # tc -> (flags, rqid)
        self.reg_events = {}           # (tc, iid) -> (flags, rqid)
        self.log = []                  # (what, ...) of every request handled, for assertions
        self.ctrl_q = collections.deque()
        self.out_q = collections.deque()       # data frames waiting for the single pending slot
        self.pending = None            # (frame bytes, seq, tries, deadline)
        self.resend_now = False
        self.tx_seq = 0
        self.last_rx_seq = None        # the EC filters repeats against the last sequence id only (ssh.rst, Limitations)
        self.feature_reports = {}

    # -- receive ----------------------------------------------------------------------------------------------

    def feed(self, data, now_us):
        for item in self.scanner.feed(data, now_us):
            self._handle(item, now_us)

    def _handle(self, item, now_us):
        if item[0] == 'junk':
            self.stats['junk'] += 1
            self.ctrl_q.append(fr.build_nak())
            return
        _, ftype, seq, payload = item
        if ftype == fr.TYPE_ACK:
            if self.pending and self.pending[1] == seq:
                self.pending = None
            return
        if ftype == fr.TYPE_NAK:
            if self.pending:
                self.resend_now = True
            return
        if self.faults.deaf:
            return
        if ftype == fr.TYPE_DATA_SEQ:
            if self.faults.drop_acks:
                self.faults.drop_acks -= 1
                self.stats['ack_dropped'] += 1
            else:
                self.ctrl_q.append(fr.build_ack(seq))
            if seq == self.last_rx_seq:
                self.stats['repeat_ignored'] += 1
                return
            self.last_rx_seq = seq
        fields = command_fields(payload)
        if fields is None or self.faults.silent:
            return
        self._request(*fields, now_us)

    # -- requests ---------------------------------------------------------------------------------------------

    def _status(self):
        if self.faults.nonzero_status:
            self.faults.nonzero_status -= 1
            return b'\x01'
        return b'\x00'

    def _answer(self, tc, tid, iid, rqid, cid, data, now_us):
        sid_for_host, rq = tid, rqid
        host_tid = TID_HOST
        if self.faults.wrong_tid:
            self.faults.wrong_tid -= 1
            host_tid = 0x03
        if self.faults.wrong_rqid:
            self.faults.wrong_rqid -= 1
            rq = 0x7777
        frame = fr.build_command(fr.TYPE_DATA_SEQ, 0, tc, host_tid, sid_for_host, iid, rq, cid, data)
        self.out_q.append(frame)

    def _request(self, tc, tid, sid, iid, rqid, cid, data, now_us):
        self.log.append((tc, tid, iid, cid, bytes(data)))
        ans = lambda d: self._answer(tc, tid, iid, rqid, cid, d, now_us)
        if tc == TC_SAM and tid == TID_SAM and iid == 0:
            if cid == CID_FW_VERSION:
                return ans(self.fw_version.to_bytes(4, 'little'))
            if cid in (CID_D0_ENTRY, CID_D0_EXIT):
                self.d0 = cid == CID_D0_ENTRY
                return ans(self._status())
            if cid in (CID_DISPLAY_ON, CID_DISPLAY_OFF):
                self.display_off = cid == CID_DISPLAY_OFF
                if not self.display_off:
                    self.wake_line = False
                    while self.held:                       # display-on releases everything that accumulated
                        self._event(*self.held.pop(0))
                return ans(self._status())
            if cid in (CID_SAM_EVENT_ENABLE, CID_SAM_EVENT_DISABLE) and len(data) == 5:
                ev_tc, flags, ev_rqid, _inst = data[0], data[1], int.from_bytes(data[2:4], 'little'), data[4]
                if cid == CID_SAM_EVENT_ENABLE:
                    self.sam_events[ev_tc] = (flags, ev_rqid)
                else:
                    self.sam_events.pop(ev_tc, None)
                return ans(self._status())
        if tc == TC_KIP and tid == TID_SAM and cid == CID_KIP_STATE:
            return ans(b'\x01' if self.kip_connected else b'\x00')
        if tc == TC_REG and tid == TID_KIP and cid in (CID_REG_EVENT_ENABLE, CID_REG_EVENT_DISABLE) and len(data) == 5:
            ev_tc, flags, ev_rqid, inst = data[0], data[1], int.from_bytes(data[2:4], 'little'), data[4]
            if cid == CID_REG_EVENT_ENABLE:
                self.reg_events[(ev_tc, inst)] = (flags, ev_rqid)
            else:
                self.reg_events.pop((ev_tc, inst), None)
            return ans(self._status())
        if tc == TC_HID and tid == TID_KIP and iid in self.devices:
            dev = self.devices[iid]
            if cid == HID_GET_DESCRIPTOR and len(data) == 10:
                return ans(self._slice(dev, data))
            if cid == HID_OUTPUT:
                return                                      # an output report has no response (surface_hid.c:124-134)
            if cid == HID_SET_FEATURE:
                self.feature_reports[(iid, data[0])] = bytes(data)
                return
            if cid == HID_GET_FEATURE and len(data) == 1:
                return ans(self.feature_reports.get((iid, data[0]), bytes((data[0],))))
        self.stats['unknown_request'] += 1                 # ACKed, never answered (ssh.rst: "ACKed frame but no command response")

    def _slice(self, dev, data):
        entry, offset, length, _end = data[0], int.from_bytes(data[1:5], 'little'), int.from_bytes(data[5:9], 'little'), data[9]
        blob = dev.entry(entry) if entry in (0, 1, 2) else b''
        if self.faults.descriptor_stall:
            return bytes((entry,)) + offset.to_bytes(4, 'little') + bytes(4) + b'\x00'
        if self.faults.descriptor_rewind:
            offset = 0
        if self.faults.descriptor_short_total and offset >= len(blob) - self.faults.descriptor_short_total:
            blob = blob[:offset]
        chunk = blob[offset:offset + min(length, self.max_slice) + self.faults.descriptor_overlong]
        end = 1 if offset + len(chunk) >= len(blob) else 0
        claimed = len(chunk)
        if self.faults.descriptor_slice_over:
            claimed = self.faults.descriptor_slice_over
            self.faults.descriptor_slice_over = 0
        return bytes((entry,)) + offset.to_bytes(4, 'little') + claimed.to_bytes(4, 'little') + bytes((end,)) + chunk

    # -- events -----------------------------------------------------------------------------------------------

    def press(self, iid, report, now_us):
        """The cover sends a HID input report. Held while the display is off (the wake line goes high instead)."""
        flags, rqid = self.reg_events.get((TC_HID, iid), (None, None))
        if flags is None:
            if not self.hid_events_without_enable:
                self.stats['event_not_enabled'] += 1
                return
            flags, rqid = EVENT_SEQUENCED if self.sequenced_default else 0, TC_HID
        if self.display_off:
            self.wake_line = True
            self.stats['event_held'] += 1
            self.held.append((TC_HID, TID_KIP, iid, 0x00, rqid, flags, report))
            return
        self._event(TC_HID, TID_KIP, iid, 0x00, rqid, flags, report)

    def set_kip(self, connected, now_us):
        self.kip_connected = connected
        if TC_KIP in self.sam_events:
            flags, rqid = self.sam_events[TC_KIP]
            self._event(TC_KIP, TID_SAM, 0, CID_KIP_STATE, rqid, flags, b'\x01' if connected else b'\x00')

    def _event(self, tc, sid, iid, cid, rqid, flags, data):
        ftype = fr.TYPE_DATA_SEQ if flags & EVENT_SEQUENCED else fr.TYPE_DATA_NSQ
        self.out_q.append(fr.build_command(ftype, 0, tc, TID_HOST, sid, iid, rqid, cid, data))

    # -- transmit ---------------------------------------------------------------------------------------------

    def _wire(self, frame):
        frame = bytearray(frame)
        if self.faults.corrupt_tx:
            self.faults.corrupt_tx -= 1
            frame[len(frame) // 2] ^= 0x01
        out = bytes(frame)
        if self.faults.duplicate_tx:
            self.faults.duplicate_tx -= 1
            out += out
        return out

    def _resend(self, now_us):
        frame, seq, tries, deadline = self.pending
        if not self.resend_now and now_us < deadline:
            return b''
        self.resend_now = False
        if tries >= PACKET_TRIES:
            self.stats['gave_up'] += 1            # the EC drops the frame after three transmissions (ssh.rst, Limitations)
            self.pending = None
            return b''
        self.pending = (frame, seq, tries + 1, now_us + PACKET_TIMEOUT_US)
        self.stats['resent'] += 1
        return self._wire(frame)

    def poll_tx(self, now_us):
        self.scanner_poll(now_us)
        out = bytearray()
        while self.ctrl_q:
            out += self._wire(self.ctrl_q.popleft())
        if self.pending:
            out += self._resend(now_us)
        while self.pending is None and self.out_q:
            frame = bytearray(self.out_q.popleft())
            ftype = frame[2]
            if ftype == fr.TYPE_DATA_SEQ:
                # Re-stamp the sequence id here: the EC's own counter runs per sequenced frame it sends.
                seq = self.tx_seq
                self.tx_seq = (self.tx_seq + 1) & 0xFF
                payload = bytes(frame[8:-2])
                frame = bytearray(fr.build_frame(fr.TYPE_DATA_SEQ, seq, payload))
                self.pending = (bytes(frame), seq, 1, now_us + PACKET_TIMEOUT_US)
            out += self._wire(bytes(frame))
        return bytes(out)

    def scanner_poll(self, now_us):
        for item in self.scanner.poll(now_us):
            self._handle(item, now_us)


class Wire:
    """Connects a host stack to an EC model and records every byte with its time (the transcript's source)."""

    def __init__(self, host, ec, chunk=0):
        self.host, self.ec, self.chunk = host, ec, chunk
        self.t_us = 0
        self.log = []                 # (t_us, 'tx' | 'rx', bytes): tx is host to EC
        self.hooks = {'tx': None, 'rx': None}   # hook(t_us, data) -> data: lets a test damage the bytes in flight

    def _deliver(self, sink, data, direction):
        if data and self.hooks[direction]:
            data = self.hooks[direction](self.t_us, data)
        if not data:
            return
        self.log.append((self.t_us, direction, bytes(data)))
        step = self.chunk or len(data) or 1
        for i in range(0, len(data), step):
            sink.feed(data[i:i + step], self.t_us)

    def step(self, dt_us=1000):
        self.t_us += dt_us
        self._deliver(self.ec, self.host.poll_tx(self.t_us), 'tx')
        self._deliver(self.host, self.ec.poll_tx(self.t_us), 'rx')

    def run_until(self, done, timeout_us, dt_us=1000):
        end = self.t_us + timeout_us
        while not done():
            if self.t_us >= end:
                return False
            self.step(dt_us)
        return True

    def advance(self, duration_us, dt_us=1000):
        end = self.t_us + duration_us
        while self.t_us < end:
            self.step(dt_us)


def do_request(wire, tc, tid, iid, cid, payload=b'', step='', wants_response=True, timeout_us=REQUEST_TIMEOUT_US + 500_000):
    req = wire.host.submit(tc, tid, iid, cid, payload, wants_response, wire.t_us)
    wire.run_until(lambda: req.finished, timeout_us)
    if req.state != 'done':
        raise SamError(f'{step or hex(cid)}: {req.error or req.state}', step)
    return req


def status_ok(req, step):
    """A u8 result of an internal request; non-zero is a refusal (controller.c:2084-2102, and :1929-1950 for event enables)."""
    if len(req.response) != 1 or req.response[0] != 0:
        raise SamError(f'{step}: unexpected result {req.response.hex()}', step)


def read_descriptor(wire, iid, entry, total, step, slice_len=SLICE_DATA):
    """The sliced descriptor read of surface_hid.c:42-111, with the same distrust of the response header, and bounded: Linux's
    loop (surface_hid.c:77-102) trusts the offset and length the controller answers, so a controller that answers length 0 or
    the same offset forever would hold it. Here every slice must start where the request asked, must carry data or end the
    read, and the number of requests is bounded by twice what the descriptor needs plus a margin (POLICY, not Linux's: Linux
    accepts any positive slice size, so a controller that answers half-size slices still gets through; one that answers less
    than about a third of the asked size is refused)."""
    out = bytearray(total)
    offset, got_end = 0, False
    budget = 2 * -(-total // slice_len) + 3
    while not got_end and offset < total:
        if budget <= 0:
            raise SamError(f'{step}: descriptor read does not finish', step)
        budget -= 1
        payload = bytes((entry,)) + offset.to_bytes(4, 'little') + slice_len.to_bytes(4, 'little') + b'\x00'
        req = do_request(wire, TC_HID, TID_KIP, iid, HID_GET_DESCRIPTOR, payload, step)
        r = req.response
        if len(r) < 10:
            raise SamError(f'{step}: short descriptor slice', step)
        off, length, end = int.from_bytes(r[1:5], 'little'), int.from_bytes(r[5:9], 'little'), r[9]
        if length > slice_len or off > total or length != len(r) - 10:
            raise SamError(f'{step}: descriptor slice lies about its size', step)
        if off != offset or (length == 0 and not end):
            raise SamError(f'{step}: descriptor slice does not advance', step)
        length = min(length, total - off)
        out[off:off + length] = r[10:10 + length]
        offset, got_end = off + length, bool(end)
    if offset != total:
        raise SamError(f'{step}: descriptor ended at {offset}, expected {total}', step)
    return bytes(out)


@dataclasses.dataclass
class BringUpResult:
    fw_version: int = 0
    kip_connected: bool = False
    devices: dict = dataclasses.field(default_factory=dict)      # iid -> dict(vendor, product, version, report_descriptor)
    steps: list = dataclasses.field(default_factory=list)


def hid_events_payload(iid):
    """The event-enable payload of a HID client: category, flags, request id (little endian), instance. Linux passes flags 0
    (surface_hid.c:216, carried unchanged by controller.c:2361), so HID input reports arrive as DATA_NSQ frames."""
    return bytes((TC_HID, HID_EVENT_FLAGS, TC_HID, 0, iid))


def bring_up(wire, iids=(1, 3), enable_events=True, send_d0_display=True, slice_len=SLICE_DATA):
    """The request order of surface_aggregator/core.c:700-706 (firmware version, D0 entry, display on) followed by the
    keyboard-cover path of the hub driver (enable KIP connection events, query the state) and of the HID client
    (descriptor entries 0 and 2, report descriptor, then the event enable that starts the input reports, as hid_add_device
    parses before it starts). Raises SamError at the first refusal: the caller's abort rule is 'stop talking and tear down'."""
    res = BringUpResult()
    note = res.steps.append

    def sam(cid, step, payload=b'', tc=TC_SAM):
        note(step)
        return do_request(wire, tc, TID_SAM, 0, cid, payload, step)

    req = sam(CID_FW_VERSION, 'fw-version')
    if len(req.response) != 4:
        raise SamError('fw-version: response is not 4 bytes', 'fw-version')
    res.fw_version = int.from_bytes(req.response, 'little')
    if send_d0_display:
        status_ok(sam(CID_D0_ENTRY, 'd0-entry'), 'd0-entry')
        status_ok(sam(CID_DISPLAY_ON, 'display-on'), 'display-on')
    # surface_aggregator_hub.c: the notifier registration enables the KIP connection event with the SAM registry.
    status_ok(sam(CID_SAM_EVENT_ENABLE, 'kip-events', bytes((TC_KIP, KIP_EVENT_FLAGS, TC_KIP, 0, 0))), 'kip-events')
    req = sam(CID_KIP_STATE, 'kip-state', tc=TC_KIP)
    if len(req.response) != 1:
        raise SamError('kip-state: response is not 1 byte', 'kip-state')
    res.kip_connected = bool(req.response[0])
    if not res.kip_connected:
        return res
    for iid in iids:
        step = f'hid{iid}'
        note(step + '-hid-descriptor')
        raw = read_descriptor(wire, iid, DESC_HID, 9, step + '-hid-descriptor', slice_len)
        if raw[0] != 9 or raw[1] != 0x21 or raw[5] != 1 or raw[6] != 0x22:
            raise SamError(f'{step}: unexpected HID descriptor', step)
        report_len = int.from_bytes(raw[7:9], 'little')
        if not 0 < report_len <= REPORT_DESC_MAX:
            raise SamError(f'{step}: report descriptor length {report_len} out of bounds', step)
        note(step + '-attributes')
        attrs = read_descriptor(wire, iid, DESC_ATTRS, 32, step + '-attributes', slice_len)
        if int.from_bytes(attrs[0:4], 'little') != 32:
            raise SamError(f'{step}: unexpected attribute length', step)
        note(step + '-report-descriptor')
        rdesc = read_descriptor(wire, iid, DESC_REPORT, report_len, step + '-report-descriptor', slice_len)
        res.devices[iid] = dict(vendor=int.from_bytes(attrs[4:6], 'little'), product=int.from_bytes(attrs[6:8], 'little'),
                                version=int.from_bytes(attrs[8:10], 'little'), report_descriptor=rdesc)
        if enable_events:
            status_ok(do_request(wire, TC_REG, TID_KIP, 0, CID_REG_EVENT_ENABLE,
                                 hid_events_payload(iid), step + '-events'), step + '-events')
            note(step + '-events')
    return res


def tear_down(wire, iids=(1, 3), power=True):
    """Best-effort reverse of bring_up in the order of core.c:402-409 (notifiers off, display off, D0 exit). Never raises."""
    steps = []
    for iid in iids:
        try:
            do_request(wire, TC_REG, TID_KIP, 0, CID_REG_EVENT_DISABLE, hid_events_payload(iid), 'hid-off')
            steps.append(f'hid{iid}-events-off')
        except SamError:
            steps.append(f'hid{iid}-events-off-failed')
    try:
        do_request(wire, TC_SAM, TID_SAM, 0, CID_SAM_EVENT_DISABLE, bytes((TC_KIP, KIP_EVENT_FLAGS, TC_KIP, 0, 0)), 'kip-off')
        steps.append('kip-events-off')
    except SamError:
        steps.append('kip-events-off-failed')
    if power:
        for cid, name in ((CID_DISPLAY_OFF, 'display-off'), (CID_D0_EXIT, 'd0-exit')):
            try:
                do_request(wire, TC_SAM, TID_SAM, 0, cid, b'', name)
                steps.append(name)
            except SamError:
                steps.append(name + '-failed')
    return steps
