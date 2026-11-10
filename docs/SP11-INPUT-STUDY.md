# Surface Pro 11 input study: the Surface Aggregator link, the G1 spike plan and the G2 decision

**Evidence study only. UNIMPLEMENTED; hardware status NOT RUN.** Nothing here ran on a Surface Pro. The one piece of
code that backs it is host-side: the frame codec `compat/sam-frame.{h,c}` with its tests
(`python3 tools/test-sam-frames.py`). It is not linked into any build, so the A16 monitor is unchanged.

Labels are those of [SURFACE-PRO-11.md](SURFACE-PRO-11.md): VERIFIED means "read in a pinned public source or in this
repository", never "seen on a Surface Pro". OBSERVED is a third-party report on one unit, INFERRED is reasoning
that was not checked, UNKNOWN is resolved by a named measurement. Sources are cited as `[Ln]`, `[Sn]`, `[Qn]`,
`[Rn]` and `[Tn]` and listed in [section 7](#7-citations). All of it was read as facts. Linux is GPL-2.0: no code or
prose from it is copied here or in the codec.

## 1. Result in one reading

**What reaches the SoC.** The Type Cover keyboard and touchpad reach the SoC through the Surface Aggregator Module
(SAM), an embedded controller on UART2: the only path the pinned Linux describes for them (VERIFIED,
[section 2](#2-how-the-input-hardware-reaches-the-soc)). The touchscreen and pen do not use that link. The todo text
is mostly right and needs three corrections:

1. `0x00b88000` is serial engine 2 of QUP wrapper 0, and the same window is also declared as I2C2 and SPI2 in the
   same device tree. Which protocol it speaks is a property of the firmware loaded into that engine, so G1 must read
   it, not assume it (VERIFIED, [L2]).
2. "4 Mbps" is in the device tree, but the Linux driver does not read it on the device-tree path: it hard-codes
   4 Mbps, hardware RTS/CTS flow control and no parity, with a comment that the values may differ per board
   (VERIFIED, [L17]). Data bits and stop bits are not stated there (INFERRED 8N1).
3. "Touch and pen are HID-over-SPI" is not in the pinned Linux. Its Denali device tree describes no touch or pen
   device, and its tree has no generic or Qualcomm HID-over-SPI transport and no binding for one (the SPI-related
   HID code present is Goodix-specific, plus Intel's THC) (VERIFIED, [L1], [L23]). Two community repositories
   report an SPI touch digitizer on the engine at `0x00a88000` (OBSERVED, one unit, [T1], [T2]). The pen digitizer
   transport is UNKNOWN. Both stay stretch scope.

**G2 recommendation (a default, not a decision).** Aim at a *native* driver, run in the guest (option A2, a macOS
kext driving one stage-2-mapped page of the UART engine, the way SnapI2CHID drives the A16's I2C engines), after a
read-only G1 spike. Keep the *transitional host-forwarded virtual HID* (option B) as a pre-agreed fallback, built
only if G1 says the link is not reachable or X10 is close and A2 is not ready. Put the new driver in a new
`drivers/` component and keep the codec in `compat/`. [Section 6](#6-g2-the-options-and-a-recommended-default) gives
the table and the evidence at X2/X3 that would change this.

**Decisions needed from the owner:** G2 (A1, A2 or B), and the repository location (new component or extend
SnapI2CHID). Neither is needed before the G1 spike, which only reads.

## 2. How the input hardware reaches the SoC

| Device | Path | Label | Cite |
| --- | --- | --- | --- |
| Type Cover keyboard | SAM on UART2, then the KIP hub; HID category `0x15`, target `0x02`, instance `0x01` | VERIFIED | [L18] |
| Type Cover touchpad | the same, instance `0x03` | VERIFIED | [L18] |
| Type Cover firmware-update HID | the same, instance `0x05` (not needed) | VERIFIED | [L18] |
| Pen stash (pen taken or stowed events) | the same, instance `0x02`; this is not the digitizer | VERIFIED | [L18] |
| Touchscreen | GENI SPI engine at `0x00a88000` (`spi10`, wrapper 1), HID over SPI, quad SPI, a GPIO ready line | OBSERVED (one unit, may be AI-assisted) | [T1], [T2], [L2] |
| Pen digitizer | not described anywhere pinned; probably the same SPI controller | UNKNOWN (INFERRED) | none |
| Volume keys, lid switch | PMIC GPIOs and TLMM GPIO 2 as `gpio-keys` | VERIFIED | [L1] |

The SP11 entry in the Linux client registry (`microsoft,denali`) lists the KIP hub and four HID nodes under it, plus
battery, AC, thermal, fan and a tablet-mode switch. It lists no touchscreen or pen digitizer [L18]. Registry nodes
are named `ssam:<domain>:<category>:<target>:<instance>:<function>`; the HID ones are domain `01`, category `15`.

### 2.1 The SAM link on the SoC

| Fact | Value | Label | Cite |
| --- | --- | --- | --- |
| Block | GENI serial engine 2 of QUP wrapper 0; engine window `0x00b88000`, `0x4000` bytes; wrapper window `0x00bc0000`, `0x2000` bytes | VERIFIED | [L2] |
| Same window, other roles | also `i2c2` and `spi2`; only one is real, chosen by engine firmware | VERIFIED | [L2] |
| Interrupt | SPI 584, level high (INTID 616) | VERIFIED | [L2] |
| Clock and power | `GCC_QUPV3_WRAP0_S2_CLK` ("se"), wrapper AHB clocks, power domain `RPMHPD_CX` with a 100 MHz OPP table, interconnect votes "qup-core" and "qup-config" | VERIFIED | [L2] |
| Pins | gpio8 CTS, gpio9 RTS, gpio10 TX, gpio11 RX; function `qup0_se2` (index 1 in each pin's function list); drive 2, bias off | VERIFIED | [L2], [L5] |
| SAM node | `microsoft,surface-sam` under `&uart2`; `current-speed = <4000000>`; interrupt from TLMM gpio91, rising edge; pin state `ssam_state` (gpio91 as plain GPIO, bias off) | VERIFIED | [L1], [L4] |
| TLMM and wake | TLMM at `0x0f100000`, summary SPI 208; gpio91 maps to PDC wake interrupt 157 | VERIFIED | [L2], [L5] |
| Wrapper enabled by the board | `qupv3_0`, `qupv3_1`, `qupv3_2` | VERIFIED | [L1] |
| Console alias | `serial0 = &uart2`: the SoC's first serial alias is the SAM link | VERIFIED | [L1] |
| Meaning of gpio91 | Linux: only usable while the EC is in the display-off state, where the line is held high until the host releases events one by one or sends display-on. Not a data line | OBSERVED by Linux developers on other models, INFERRED for SP11 | [L16] |

Two consequences. First, a polled receiver does not need gpio91 for normal operation (events arrive on the UART
while the display is on); it matters only for wake from sleep, which is out of scope. Second, `serial0 = &uart2` is a
hazard for this project: q1n1's optional polled console (`--uart`) writes bytes to whatever UART the firmware's SPCR
table names [Q1]. If the SP11's SPCR names `0x00b88000`, console text would be sent into the embedded controller.
The SPCR content is UNKNOWN until X1 dumps the ACPI tables, so G1 treats `--uart` as forbidden on the SP11.

## 3. The protocol, as facts

Everything in this section is VERIFIED only by reading two sources that agree: the Linux Surface Aggregator driver
and its protocol notes at the pin [L10], [L12], [L13], [L21], and the older reverse-engineering notes of the same
project [S1]. None of it has been seen on an SP11 wire (UNKNOWN until G1 phase 2). The EC firmware may differ.

### 3.1 Framing and CRC

| Item | Fact | Cite |
| --- | --- | --- |
| Message | `AA 55`, then type, length (16 bit), sequence, a CRC of those four bytes, `length` payload bytes, a CRC of the payload. All multi-byte values little-endian, no padding | [L10], [L21] |
| Overhead | 10 bytes, so the smallest message is 10 bytes: an empty payload still has its CRC, `0xFFFF` | [L21], [S1] |
| CRC | CRC-16 with polynomial `0x1021`, initial value `0xFFFF`, most significant bit first, no reflection, no final XOR. Check value `0x29B1` for "123456789". Catalogue name CRC-16/IBM-3740, alias CRC-16/CCITT-FALSE. The Linux code calls its table-driven routine with initial value `0xFFFF`; the older notes name `crc_ccitt_false(0xffff, ...)` | [L10], [L22], [S1], [S2] |
| Frame types | `0x80` data, sequenced (must be acknowledged); `0x00` data, unsequenced; `0x40` ACK; `0x04` NAK | [L10] |
| Control frames | ACK and NAK carry no payload. A data frame carries a payload | [L21] |
| ACK | echoes the sequence number of the data frame it acknowledges | [L21] |
| NAK | sent on a CRC or framing error; its own sequence number is unreliable and is sent as zero; the sender then re-sends everything not yet acknowledged | [L21], [L13] |
| Limits in practice | the driver evaluates messages in a 4096-byte buffer (payload up to 4086); the protocol field allows 65535 | [L14], [L10] |
| Timing | a sequenced frame is re-sent after 1 s, at most 3 sends; one un-ACKed frame at a time per direction; at most 3 requests outstanding; request timeout 3 s | [L14], [L15], [L21] |
| Repeats | for sequenced data the receiver ignores a sequence number seen among the last 8; unsequenced frames are never filtered (some models send them all with sequence 0) | [L14] |

The CRC identity is the strongest fact here: the pinned Linux header states polynomial `0x1021` and the byte update
`(crc << 8) ^ table[(crc >> 8) ^ byte]` (most significant bit first) for its routine [L22], the protocol code seeds it
with `0xFFFF` [L10], and the public CRC catalogue [S2] lists that exact parameter set with the check value the codec
tests use. Known answers computed independently with `binascii.crc_hqx` are in `tools/test-sam-frames.c`.

### 3.2 Command payload

A data frame's payload is a command: 8 header bytes, then command data. The payload type byte is `0x80`, the only
known payload type [L10], [L21].

| Byte | Field | Meaning | Cite |
| --- | --- | --- | --- |
| 0 | type | `0x80` | [L10] |
| 1 | tc | target category | [L10] |
| 2 | tid | target id: who the message is for (host `0x00`, SAM `0x01`, KIP `0x02`) | [L10] |
| 3 | sid | source id | [L10] |
| 4 | iid | instance id | [L10] |
| 5-6 | rqid | request id, little-endian | [L10] |
| 7 | cid | command id | [L10] |

- A response carries the request's category, command and instance, with source and target swapped; the request id
  pairs them. Frame sequence numbers of a request and its response are unrelated [L21].
- Request ids 1 to 38 are reserved for events; a message whose id is in that range is an event. The host chooses an
  event's id when it enables the source, and the driver uses the target category as the id. Request ids count up,
  wrap at 65535 and skip 1 to 38; the counter starts at 0, so the first request has id 0 [L10], [L16].
  38 is a function of the highest known category (`0x26`), so treat it as the protocol knowledge at the pin [L10].
- A message not addressed to the host (`tid != 0`) is dropped by the host [L15]. An event is handed up with its
  category from `tc`, its source from `sid`, its command from `cid`, its instance from `iid` and the command data as
  payload [L16].
- The sequence number counter starts at 0 and wraps at 255 [L16].

### 3.3 What the input path needs

| Step | Request (`tc`, `tid`, `iid`, `cid`) and payload | Cite |
| --- | --- | --- |
| Firmware version | SAM `0x01`, SAM `0x01`, 0, `0x13`; response u32, shown as bits 31..24 `.` 23..8 `.` 7..0 | [L16], [L17] |
| D0 entry / exit | SAM, SAM, 0, `0x34` / `0x33`; response u8 | [L16] |
| Display on / off | SAM, SAM, 0, `0x16` / `0x15`; response u8. Display-off quiets events and arms the wake line | [L16] |
| Probe order in Linux | firmware version, D0 entry, display on, then the wake interrupt and the clients | [L17] |
| Enable an event source | registry category `0x21`, target of the device (KIP `0x02`), 0, `0x01` (enable) or `0x02` (disable); payload 5 bytes: category, flags, request id (2), instance; response u8, non-zero is a failure. Flags bit 0 asks for sequenced events | [L16], [L11] |
| SAM-level registry | category `0x01`, SAM, 0, `0x0b`/`0x0c`; KIP-level `0x27`/`0x28` (declared by Linux) | [L11] |
| Cover attached? | KIP `0x0e`, SAM `0x01`, 0, `0x2c`; response u8 (non-zero connected); the same command id arrives as an event with the state in byte 0; devices need about 250 ms after connect | [L19] |
| HID input report | event, category `0x15`, command `0x00`, payload = the raw report | [L20] |
| HID commands | category `0x15`, the device's target and instance; `0x01` output report, `0x02` get feature, `0x03` set feature, `0x04` get descriptor | [L20] |
| HID descriptor read | request payload 10 bytes: entry (0 HID descriptor of 9 bytes, 1 report descriptor, 2 attributes of 32 bytes), offset (32 bit), length (32 bit), end flag; the response repeats that header and appends the data; the Windows driver uses 118-byte slices (a 128-byte payload) | [L20] |

HID instances behind the KIP hub (target `0x02`): 1 keyboard, 2 pen stash, 3 touchpad, 5 firmware update [L18]. The
report descriptors are UNKNOWN until read; the A16 touchpad is a precision touchpad that the guest presents as an
Apple multitouch device [R3], and a Type Cover touchpad may share that shape (INFERRED).

### 3.4 What the codec implements

`compat/sam-frame.{h,c}` implements section 3.1, the 8-byte command header and the request-id rules of 3.2, as a
pure codec: a single-message judge, a streaming parser with a caller-supplied buffer and one counter per rejection
reason, and a builder for data, ACK and NAK messages and commands. It has no transport policy (acknowledging,
re-sending, repeat filtering, timeouts), which belongs to the driver. Its policy for the stream is written in the
header and pinned by tests:

- Garbage before a SYN is one `bad_sync` event per run, however it was chunked.
- Each rejected candidate (bad header CRC, unknown type, length above the configured limit, a control frame with a
  payload or a data frame without one, bad payload CRC) discards exactly the two SYN bytes and rescans, so a real
  frame that starts inside a rejected one is found. The rest of that frame is skipped silently as part of the same
  incident.
- Truncation is counted only when the caller declares the stream ended or idle (`sam_parser_flush`); a lone possible
  SYN byte at that point is counted as trailing.
- The payload limit has no default: a parser refuses to run unless it is given a limit and storage for one maximal
  message, and a zeroed parser refuses every call.

**Where independent work can differ from the reference driver** (each is a deliberate choice, all UNKNOWN on a real
EC until G1 shows real frames):

1. The codec rejects an ACK or NAK that carries a payload, and a data frame without one. The reference parser accepts
   any combination at the frame layer and drops an empty data payload one layer up. If a real EC sends such frames
   this check would drop them; relax it only on evidence.
2. An unknown frame type is rejected at the header, before the payload is awaited, and rescanning continues inside
   it. The reference consumes the whole message and logs a warning. A frame with an unknown type and a bad payload CRC
   is therefore counted as an unknown type here and as a CRC error there.
3. The payload limit is configuration, not 4086.
4. Truncated input is only counted on `flush`; the reference simply waits for more bytes.
5. No NAK or ACK is generated by the parser, and there is no repeat filter.
6. `sam_rqid_next_request` never returns an event id, even for a bad input; the reference would step to the next
   event id.
7. Memory: no allocation, no recursion, and no libc, where the reference uses kernel helpers.
8. The CRC is computed four bits at a time from a 16-entry table instead of 256 entries; the result is the same
   function.

## 4. Test evidence for the codec

`python3 tools/test-sam-frames.py` runs, with no hardware: known-answer vectors computed independently (the CRC
check value `0x29B1` and `binascii.crc_hqx`); one negative test per rejection reason; every two-way split and many
chunkings of the same stream; a differential check of the C codec against `tools/sam_frames.py` on 21,010 generated
cases (10,000 streams, 5,000 exact decodes, and builder, command, CRC and request-id cases); 200,000 deterministic
mutated frames and short streams under AddressSanitizer and UndefinedBehaviorSanitizer, each judged by a second
model inside the native driver; a freestanding aarch64 build that
needs no runtime symbol; and a list of seeded bugs, each of which must be caught. A pass proves the implementations
agree with the written policy and the CRC standard. It does not prove the policy matches a real controller (UNKNOWN
until G1) and it says nothing about SP11 hardware.

## 5. G1: the UART2 spike plan

G1 asks whether raw SAM frames can be read from UART2 at EL2 or the monitor can be given the link, and what is
unknown if not. Because q1n1 already offers guarded MMIO from the Mac [Q2], the first phase needs no new code in the
monitor: it is a read-only snapshot taken over the X3 proxy session, then decoded with `tools/sam_frames.py`. The
overlap rule of the plan allows it once X3 closes. Everything below that has not been measured is UNKNOWN.

**Preconditions.** X3 closed (a proxy session at EL2 with guarded reads, where `P_SET_EXC_GUARD` in its marker mode
returns a marker value for a faulting read instead of stopping the run [Q2]); AC power; one hardware client; no
`--uart`; two snapshots, Type Cover attached and detached; results written to the ignored `build/` with labels and
no unit identifiers.

**Step 0, cross-check the device tree against this unit's own ACPI.** The X1 probe already writes the ACPI tables
to the USB stick (plan item N6). The SAM device is `MSHW0084`; Linux reads its serial resource for the baud rate,
flow control and parity, and its two GPIOs [L17]. Read the same resource in the DSDT dump: it replaces the three
device-tree assumptions of [section 1](#1-result-in-one-reading) with this unit's values. A host-only task, no
hardware.

### 5.1 Phase 1: read-only snapshot

All reads are 32-bit and guarded. Never read the FIFO data ports (`+0x700` transmit, `+0x780` receive): a receive
read consumes bytes. `B` is `0x00b88000`.

| Where | What (name as in [L7], [L8]) | Expected | Meaning |
| --- | --- | --- | --- |
| `B+0x68` | firmware revision, bits 15:8 = protocol | 2 = UART (INFERRED) | 2: UART firmware resident (q1n1's A16 transmit path also requires it [Q1]). 255: no firmware loaded. 1, 3, 4, 5: the engine is another protocol. |
| `B+0x64` | interface disable, bit 0 | 0 | 1: FIFO interface disabled |
| `B+0x258` | DMA mode enable, bit 0 | 0 (UNKNOWN) | 0: FIFO mode, enough for a polled driver (all FIFO-mode registers lie below `+0xe40`, inside one 4 KiB page; the DMA enable at `+0x2004` does not, [L7b]). 1: DMA mode |
| `B+0x40` | status, bit 0 main command active, bit 12 secondary (receive) active | UNKNOWN | bit 12 set: a receive command was left running by firmware |
| `B+0x48`, `+0x4c`, `+0x7c` | clock config (enable, divider in bits 15:4), clock select | enabled; baud = engine clock / (divider x oversampling) with oversampling 32, or 16 on QUP version 2.5 or later | tells whether the generator can make 4 Mbps; pair with the GCC reads below |
| `B+0x60` | clock control status | UNKNOWN | |
| `B+0x25c`, `+0x268`, `+0x26c`, `+0x2a4` | transmit config (bit 1 clear = hardware CTS flow control), word length, stop bits, parity | 8 data, 1 stop, no parity (INFERRED); hardware flow control (VERIFIED for Linux) | a different format means framing errors even with a good clock |
| `B+0x280`, `+0x28c`, `+0x294`, `+0x2a8` | receive config, word length, stale count, parity | as above | |
| `B+0x2ac` | manual RFR (bit 31 = software controls RTS) | UNKNOWN | whether RTS follows the FIFO or software |
| `B+0x240` | IO macro control | UNKNOWN | whether RX/TX or CTS/RTS are swapped |
| `B+0x908` | IO status, bit 1 = CTS input level (low = asserted) | UNKNOWN | whether the EC is allowing the host to transmit |
| `B+0x804`, `+0x800` | receive and transmit FIFO status (word count, last-word fields) | UNKNOWN | non-zero receive count: bytes are waiting; the FIFO status read is a status read (INFERRED non-destructive) |
| `B+0x810`, `+0x814` | receive and RFR watermarks | UNKNOWN | the level where RTS drops |
| `B+0xe20`, `+0xe24`, `+0xe28` | DMA interface enable, hardware parameters 0 and 1 | UNKNOWN | FIFO depth and width (Linux's default is 16 words of 32 bits) |
| `B+0x610`, `+0x640`, `+0x614`, `+0x644` | main and secondary IRQ status and enable | UNKNOWN | status only, never cleared in this phase |

The A16's input driver gets by with one 4 KiB page of each engine, polls it and routes no interrupt [R1]; the same
page rule would hold here if the table says FIFO mode.

| Where | What | Expected | Meaning |
| --- | --- | --- | --- |
| TLMM pin control at `0x0f108000`, `0x0f109000`, `0x0f10a000`, `0x0f10b000` (gpio8..11) | function bits 5:2, pull bits 1:0, drive bits 8:6, output enable bit 9 | function 1 (`qup0_se2`) (INFERRED from the device tree) | function other than 1: the pins are not routed to the engine |
| the same plus `0x4` | pin I/O: bit 0 level in | UNKNOWN | RX idle level, RTS and CTS levels |
| TLMM gpio91 `0x0f15b000`, `+0x4`, `+0x8`, `+0xc` | control, I/O, interrupt config (enable bit 0, polarity bit 1, detection bits 3:2), interrupt status (bit 0) | function 0, input | level: is the EC holding the line (pending events) or not; interrupt config shows whether firmware armed it |
| GIC INTID 616 and 240 | enable (`GICD+0x0100+4n`), pending (`+0x0200+4n`), route (`+0x6000+8*INTID`): `n` = INTID/32 | UNKNOWN | whether firmware left the engine interrupt (616) or the TLMM summary (240) enabled or pending. Architecture formulas, Arm GICv3 specification. |
| GCC `0x00142274` | halt status of the engine clock, bit 31 = clock off ([L9b]) | bit 31 clear | set: the engine clock is gated; further engine reads would not be trustworthy |
| GCC `0x00123000`, `0x00123004` | halt status of the wrapper AHB clocks | bit 31 clear | set: the wrapper is not clocked |
| GCC `0x00152020` | application-processor vote register: bit 12 engine clock, bits 6 and 7 wrapper AHB | UNKNOWN | whose vote keeps the clocks on after the OS-less hand-off; a clear bit does not mean off, since other masters vote elsewhere |
| GCC `0x00142284`, `0x00142288` | divider (bits 3:0, read-only in Linux, so firmware-owned) and the rate generator command register | UNKNOWN | the engine clock rate for the baud check |

GCC register addresses are `0x00100000` plus the offsets in [L9] (halt `0x42274`, `0x23000`, `0x23004`; vote
`0x52020`; divider `0x42284`; rate generator `0x42288`). RPMh and interconnect votes (the "qup-core" and
"qup-config" paths and the `CX` power domain, [L2]) cannot be read this way: whether firmware's votes survive
ExitBootServices and the slbounce hand-off is UNKNOWN, and is inferred only from whether the clocks and registers
above behave.

**Reading the result.**

| Observation | Conclusion |
| --- | --- |
| protocol 2, clocks running, FIFO mode, pins on function 1, receive FIFO plausible | the link is reachable; go to phase 2 |
| protocol 255 or not 2 | the engine has no UART firmware. Loading one needs a Qualcomm blob the project does not have: stop; fall back to option B |
| a guarded read returns the marker, or clock halt bits are set | the engine or its clock is unreachable; bring-up would need clock and power writes that are not authorized: stop and ask the owner |
| reads succeed but receive FIFO never changes and CTS stays de-asserted | the EC may be quiet until initialised: go to phase 3 only after phase 2 |
| the engine's status or FIFO count changes between two snapshots with no host action | something else is still driving the engine: settle ownership before choosing any option |

**Not feasible**, in order of what would justify the word: (a) no UART firmware in the engine and no way to obtain
one; (b) the engine window or its clock cannot be read at EL2 even with a bounded retry and the clocks reported on
(protection by a higher authority); (c) after phases 2 and 3 with the Type Cover attached, no valid frame ever
arrives and the firmware-version request never gets an ACK, with pins, format and clock confirmed correct. Phase 1
alone can only show (a) and (b). A result of "unreachable" must record the exact guarded reads, not a summary.

### 5.2 Phase 2: observe receive data (needs the owner's authorization)

Draining the receive FIFO consumes bytes: a sequenced frame that is not acknowledged is re-sent by the EC after 1 s,
but an unsequenced event is lost. Windows stays installed, and the EC's state left by an experiment is UNKNOWN
(D7). Steps: (1) watch the receive count with the Type Cover attached and keys pressed, without draining; (2) with
authorization, drain words and unpack the four characters of each word (their order inside a word is UNKNOWN until
seen: Linux asks for four 8-bit characters per word with its `msb_to_lsb` option off, [L8], but the resulting order
was not derived here, so try both) and run `python3 tools/sam_frames.py parse <hex>`. **The first valid frame with
matching CRCs is the first VERIFIED-on-SP11 evidence for section 3.1.** If frames arrive with bad CRCs, test the
other byte order and the difference list of [section 3.4](#34-what-the-codec-implements) before concluding anything.

### 5.3 Phase 3: one request (a separate authorization)

A single read-only query, firmware version (`0x01`, `0x01`, 0, `0x13`), built with `sam_build_command`, sent with a
polled transmit modelled on q1n1's A16 sequence [Q1], and the response decoded. This is the first write to a system
controller, so it needs its own authorization and no other command is sent: no event enable, no D0 or display
change. Success means the EC answered a host that did not run the Windows initialization. Whether UEFI left event
sources enabled is a separate unknown.

**What stays UNKNOWN after G1 even if it works:** the descriptors and report formats of the keyboard and touchpad
(a later phase reads them), hot-plug behaviour, behaviour after a guest reboot, the effect on Windows, and
everything about touch and pen.

## 6. G2: the options and a recommended default

Option A is a *native* SAM driver. A1 puts it at EL2 in the monitor, which then presents a virtual HID to the guest;
A2 puts it in a guest kext that is given one stage-2 page of the UART engine and polls it. Option B is a transitional
*host-forwarded virtual HID*: input events captured on the Mac are carried to the guest over the existing q1n1 call
link and presented by a guest kext as an `IOHIDDevice`. B does not read the Type Cover at all. (Forwarding the Type
Cover's own reports through the Mac would add a hop for no benefit and still needs everything A needs.)

| | A1: native, at EL2 | A2: native, in the guest | B: host-forwarded virtual HID |
| --- | --- | --- | --- |
| What must be true | G1 succeeds; the monitor can poll the engine without blocking trap paths (a bounded timer slice, AGENTS.md section 3); a monitor-to-guest virtual HID carrier exists | G1 succeeds; one 4 KiB page maps safely (an allow-list like `compat/i2c-input.h`); the kext can take the engine over from whatever state firmware left | the host link works while the guest runs (X3 and R4, R19 hold); a Mac tool can capture input |
| Unknowns | everything under G1; the carrier ABI; EL2 timing for acknowledgements | everything under G1; whether the kext must program clocks and baud (needs more than a poll-only page); arm64e kext load of a new class | latency (the existing console input is drained every 16 ms [R2]); capture permissions on the Mac (INFERRED) |
| Risks (plan) | R15 (spike), R17 (shared monitor code), R3 and R6 if the monitor times acknowledgements, R5 | R15, R17, R16 (a UEFI update changes the starting state) | R4 and R19 (the link must work at runtime); R15 is sidestepped. It breaks the standalone-first rule, so only D8's "transitional" exception covers it |
| Effect on the A16 default | none by construction: `#if SNAP_PLATFORM_SP11`, an opt-in define, params appended only when enabled; to be proven by the baseline compare once code exists | none: a new kext, loaded only by an SP11 profile, plus the same gated params | none: opt-in define and a versioned ring ABI (a property like `snapintosh,guest-net-abi` [R2]) |
| Who owns UART2 after ExitBootServices | the monitor, alone; q1n1 must not touch it | the guest kext, alone, through its page; the monitor and q1n1 must not touch the page | nobody: the engine stays as firmware left it and the EC's behaviour with an unread link is UNKNOWN |
| Standalone-first | yes | yes | no (transitional, D8) |
| Work left (rough, INFERRED) | EL2 GENI UART poller, SAM state machine at EL2, virtual HID carrier, guest kext | GENI UART poller and SAM state machine in the kext, page grant, HID presentation, hot-plug via the KIP hub | ring ABI and monitor service, host capture tool, guest kext |
| Removal plan | delete the poller and carrier when A2 replaces it, if ever | none (this is the target) | when A reaches X9, remove the define, the kext class, the host tool and the ABI number in one change; keep the number reserved |

**Why A2 is the default.** AGENTS.md puts privileged mediation at EL2 and the operating-system contract in a kernel
driver; a SAM state machine at EL2 would enlarge the privileged surface for no gain. The A16's input path is the
precedent: the monitor grants one device page and the kext polls it [R1]. A needs G1 either way, so G1 first costs
nothing extra.

**Why B is kept in reserve rather than dropped.** G1 may fail for reasons outside the project's control (no UART
firmware, a protected engine, an EC that will not answer). B needs no G1, reuses the call-service carriers the
repository already has for console input and guest networking [R2], and can unblock the login window (X10) in
the meantime. It is not the default because it needs the host link at runtime and leaves the Type Cover unused.

**Evidence at X2/X3 that would change the recommendation.**

| Evidence | Change |
| --- | --- |
| protocol not UART, or a protected or unreachable engine (G1 phase 1) | choose B now; record A as blocked on the named evidence |
| engine clock gated after ExitBootServices | A needs clock bring-up writes: ask for that authorization, or take B for X10 |
| the EC never answers phase 3 | B for X10; keep the spike open |
| the host link is unreliable at X3 (K3 territory) | B is not viable at runtime: A is the only path |
| the monitor must run protocol timing (acknowledgements) and the guest cannot be trusted to | A1 over A2 |
| NV2 absent (K1) | everything stops; moot |

**Repository location.**

| Option | Consequences |
| --- | --- |
| L1: a new component, for example `drivers/snap-sam-hid/`, in a new Snapintosh repository added as a submodule (the pointer moves in a separate `repo:` commit) | keeps SP11 EXPERIMENTAL code out of the A16-qualified SnapI2CHID kext; separate tests and releases; needs an organisation repository (an owner action); duplicates the HID and touchpad plumbing unless a shared layer is factored out; a new AuxKC bundle and loader entry for the SP11 profile only |
| L2: extend SnapI2CHID with a SAM transport | reuses its HID presentation, precision-touchpad translation and polling; one kext to load; but it changes an A16-qualified kext (R17: needs the kext-section comparison identical with the gate off), the name and scope no longer fit, and the cover's hot-plug lifecycle differs from fixed I2C devices |
| The codec | stays in `compat/` either way, shared by monitor, kext and host tests as `compat/guest-net.h` is [R2]. How a submodule build consumes a `compat/` file cannot be checked here: the submodule trees are not initialised in this worktree (UNKNOWN) |

Recommended default: L1 for the driver, the codec in `compat/`; revisit L2 once SnapI2CHID's source has been read and
its HID layer is shown to be transport-agnostic.

## 7. Citations

Pins: Linux `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` (tag Linux 7.4-rc2, the pin of
[SURFACE-PRO-11.md section 12](SURFACE-PRO-11.md#12-pinned-sources)); q1n1 `7872398694ca8322e9cdebcc4ca2380491295f24`.
Line numbers are those of the file at the pin.

| ID | Project, commit | File | Lines | Licence | Used for |
| --- | --- | --- | --- | --- | --- |
| L1 | Linux, pin | `arch/arm64/boot/dts/qcom/x1-microsoft-denali.dtsi` | 16-17, 20-49, 777-783, 843-849, 1092-1102, 1198, 1221-1225, 1316-1329 | BSD-3-Clause | aliases, `gpio-keys`, unknown I2C devices, wrappers enabled, `ssam_state`, the SAM node; no touch, pen or HID node anywhere in the file |
| L2 | Linux, pin | `arch/arm64/boot/dts/qcom/hamoa.dtsi` | 758-761, 1491, 1688-1722, 2141-2150, 2301-2335, 2337-2360, 2362-2396, 6048, 6119-6132, 6692-6717 | BSD-3-Clause | GCC, wrapper 1 and `spi10`, wrapper 0, `i2c2`/`uart2`/`spi2` at one window, PDC, TLMM, UART2 pins |
| L3 | Linux, pin | `arch/arm64/boot/dts/qcom/x1e80100-microsoft-denali-oled.dts` | 10-16 | BSD-3-Clause | the board compatible chain (`microsoft,denali`) |
| L4 | Linux, pin | `Documentation/devicetree/bindings/embedded-controller/microsoft,surface-sam.yaml` | 1-44 | GPL-2.0-only OR BSD-2-Clause | the SAM binding and its example |
| L5 | Linux, pin | `drivers/pinctrl/qcom/pinctrl-x1e80100.c` | 12-53, 1575-1578, 1658, 1811-1828, 1837 | GPL-2.0-only | TLMM per-pin register layout, function lists, PDC map |
| L6 | Linux, pin | `drivers/pinctrl/qcom/pinctrl-msm.c` | 169-190, 617-626 | GPL-2.0-only | function select field width, level-in bit |
| L7 | Linux, pin | `include/linux/soc/qcom/geni-se.h` | 33-41, 84-159, 162-281, 375-382 | GPL-2.0 | protocol enumeration, register offsets, fields |
| L7b | Linux, pin | `drivers/soc/qcom/qcom-geni-se.c` | 385-406, 456-475, 1522-1540 | GPL-2.0 | FIFO-mode selection touches only registers below `+0x1000`; the `+0x2004` DMA enable is written only when loading engine firmware |
| L8 | Linux, pin | `drivers/tty/serial/qcom_geni_serial.c` | 36-92, 235-249, 511-531, 976-985, 1330-1390, 1430-1480, 1542-1556 | GPL-2.0 | UART registers, receive count arithmetic, flow control, baud rule |
| L9 | Linux, pin | `drivers/clk/qcom/gcc-x1e80100.c` | 1166, 2237-2250, 4434-4450, 4954-4985 | GPL-2.0-only | engine and wrapper clock branches and vote bits |
| L9b | Linux, pin | `drivers/clk/qcom/clk-branch.h`, `clk-branch.c` | 32, 63; 44-60 | GPL-2.0 | the branch status register's clock-off bit (bit 31) and the voted-halt rule |
| L10 | Linux, pin | `include/linux/surface_aggregator/serial_hub.h` | 14-65, 73-106, 123, 179-192, 202-247, 289-297, 308-347 | GPL-2.0+ | frame and command structures, types, SYN, CRC call, id helpers, target and category numbers |
| L11 | Linux, pin | `include/linux/surface_aggregator/controller.h` | 25-30, 914-921 | GPL-2.0+ | event flags, event registries |
| L12 | Linux, pin | `drivers/platform/surface/aggregator/ssh_parser.c` | 29-48, 69-95, 117-176 | GPL-2.0+ | reference parsing order and limits |
| L13 | Linux, pin | `drivers/platform/surface/aggregator/ssh_msgb.h` | 106-210 | GPL-2.0+ | message construction (CRC, frame, ACK, NAK, command) |
| L14 | Linux, pin | `drivers/platform/surface/aggregator/ssh_packet_layer.c` | 184-223, 1599-1637, 1649-1694, 1697-1792 | GPL-2.0+ | timeouts, repeat filter, ACK/NAK, unknown type handling |
| L15 | Linux, pin | `drivers/platform/surface/aggregator/ssh_request_layer.c` | 36, 53, 909-955 | GPL-2.0+ | request timeout, target check, event or response |
| L16 | Linux, pin | `drivers/platform/surface/aggregator/controller.c` | 44-98, 984-1000, 1819-1852, 1864-1935, 2705-2770 | GPL-2.0+ | counters, event decoding, internal requests, event enable, wake interrupt notes |
| L17 | Linux, pin | `drivers/platform/surface/aggregator/core.c` | 300-377, 637-760, 827-829 | GPL-2.0+ | UART setup on both paths, probe order, binding match |
| L18 | Linux, pin | `drivers/platform/surface/surface_aggregator_registry.c` | 43, 117-216, 408-423, 510-521 | GPL-2.0+ | KIP hub, HID nodes, the SP11 group |
| L19 | Linux, pin | `drivers/platform/surface/surface_aggregator_hub.c` | 284-355 | GPL-2.0+ | KIP connection state and delay |
| L20 | Linux, pin | `drivers/hid/surface-hid/surface_hid.c`, `surface_hid_core.c`, `surface_hid_core.h` | 25-100, 157-166, 205-216; 39-100, 135-155, 190-210; 20-48 | GPL-2.0+ | HID commands, descriptor slices, event handler, descriptor structures |
| L21 | Linux, pin | `Documentation/driver-api/surface_aggregator/ssh.rst` | 42-45, 47-135, 137-190, 193-304, 306-346 | GPL-2.0+ | protocol notes: framing, CRC, flow, commands, limits |
| L22 | Linux, pin | `include/linux/crc-itu-t.h`, `lib/crc/crc-itu-t.c` | 1-26; 1-12 | GPL-2.0-only | the CRC routine's polynomial and update rule |
| L23 | Linux, pin | directory listings of `drivers/hid/` and `Documentation/devicetree/bindings/input/` | n/a | n/a | no generic or Qualcomm HID-over-SPI driver or binding (negative evidence, by listing; `hid-goodix-spi.c` and `intel-thc-hid/` are device-specific) |
| S1 | linux-surface/surface-aggregator-module, `de6d403852f33f5445c25971a3f25e6ebafbf824` | `doc/ssh-protocol.txt` | 25, 41-53 | GPL-2.0 | an independent statement of the CRC, the empty-payload CRC and the SYN bytes |
| S2 | RevEng CRC catalogue (public), the entry's own date is 4 August 2023 | `crc-catalogue/16.htm`, entry CRC-16/IBM-3740 | 464-465 | public reference | the parameter set and the check value `0x29b1` |
| Q1 | q1n1, `7872398694ca8322e9cdebcc4ca2380491295f24` | `platform/uefi/main.c` | 190-222, 1188-1190, 1280 | MIT | polled GENI UART transmit on the A16 (protocol 2, FIFO mode, idle), the SPCR-driven console, `--uart` opt-in |
| Q2 | the same | `docs/A16-Q1N1-PROXY.md` | 114-145 | MIT | guarded MMIO from the Mac, exception guards |
| R1 | this repository | `compat/i2c-input.h` | 1-60 | MIT | the A16 input page grant and polling |
| R2 | this repository | `compat/console-link.h`, `compat/guest-net.h` | 1-20 each | MIT | host-forwarded carriers that already exist |
| R3 | this repository | `docs/ARCHITECTURE.md` | section 6 (Input) | MIT | SnapI2CHID and the precision touchpad |
| T1 | denisix/ubuntu-surface-pro-11, `049b1caccf153ccf7aa5d0f6b824a4dc48e8b02d` | `TOUCHSCREEN.md` | 3-17 | none stated in the file read | OBSERVED: an SPI HID digitizer, vendor `045E`, product `0C83`, engine `a88000`, quad SPI. One unit |
| T2 | geocausa/SP11X1e-touchscreen, `527e4aed93009851a6c9cf9ba50bb5485801bb6d` | `README.md`; `docs/PROTOCOL.md` | 1-5, 94-96, 468, 473-475; 1-12, 32-41, 53-57 | GPL-2.0 (its README) | OBSERVED: touch controller `MSHW0485` on a GENI engine, quad SPI, ready line gpio51, the Microsoft HID-over-SPI protocol. Its notes say they derive from static analysis of Surface UEFI drivers: nothing is reused here (R20) |

The Microsoft HID-over-SPI specification that [T2] cites was not read. Nothing in this study depends on it.

## 8. Unknowns and what must still be measured

| Unknown | Measured by |
| --- | --- |
| the engine's protocol, mode, clock and pin state after ExitBootServices | G1 phase 1 |
| this unit's baud, format, flow control and GPIOs | G1 step 0 (the DSDT `MSHW0084` resource) |
| whether the CRC, framing and policy match real frames | G1 phase 2 (first valid frame) |
| whether the EC answers a host that skipped the Windows initialization | G1 phase 3 |
| whether firmware's RPMh, interconnect and power-domain votes survive the hand-off | indirectly, by the clock and register reads |
| keyboard and touchpad report descriptors; hot-plug behaviour | after G1, by a descriptor read |
| the pen digitizer transport and the touchscreen details | a separate stretch spike, not before G1 closes |
| the effect of any of this on Windows' next boot | an owner-supervised check after phase 2 |
