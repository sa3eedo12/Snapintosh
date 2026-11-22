<!-- SPDX-License-Identifier: MIT -->
# SP11 input plan: the Type Cover (Surface Aggregator over UART2) to the stock macOS guest (host-only plan; hardware NOT RUN)

**Status: EXPERIMENTAL plan. Nothing here authorises a boot, a write or a hardware client. Hardware: boots 1 to 31; boot 29 is the only run that touched the input path, and it only read (the engine's clock words, the interrupt words and the pin controller); nothing newer has run.** The plan extends [SP11-INPUT-STUDY.md](SP11-INPUT-STUDY.md), whose protocol facts, options table and boot 29 reading stay valid, and replaces the study's G1 phases 1 to 3 (study sections 5.1 to 5.3) by the staged plan I0 to I3 of section 4. The host tools it describes are committed (section 7); the plan went through two independent fact-checks (section 8) and every correction of both is applied.

Labels. VERIFIED: read in a pinned public source (a fact about the source, never about the unit) or shown by a host test that ran. OBSERVED: seen on the unit (boot 29), declared in the unit's own firmware tables (read offline from the dumps; the firmware's declaration, not a read of the hardware), or reported by a third party on one unit (stated as such; not checked). INFERRED, EXPERIMENTAL and UNIMPLEMENTED as in [AGENTS.md](../AGENTS.md). UNKNOWN: settled by a named measurement; the items are U1 to U24 of section 5. POLICY marks a choice of this project that is not Linux's behaviour (it is marked the same way in the code).

Pins. Linux `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` (tag 7.4-rc2), fetched raw and hash-checked against the q1n1 registers document; this repository `0a75501` (the plan was written at `08a208d`; the tools were added at `0a75501`); the q1n1 fork `3d1c7ba` (the pin of the reading; the fork has moved since); SnapI2CHID `f0b7984`, the recorded submodule pin (`git ls-tree 0a75501 drivers/snap-i2c-hid`). Linux is GPL-2.0: facts, names, numbers and file and line citations are used; no code or prose is copied.

## 0. Verdict

1. **Protocol (section 1).** Re-verified at the pin. There is **no link-level handshake**: the first frames are three ordinary requests (firmware version, D0 entry, display on). The Linux notes say that a class of events has to be requested by the host before the EC sends it, and that HID input events "seem to be the exception" (INT:476-480, reverse engineered); Linux itself always enables them, so whether the cover streams unprompted is UNKNOWN, settled by a *passive capture* (I1b). The HID events-on payload is `15 00 15 00 iid` (flags **0**, as the Linux HID client passes it); only the KIP hub's enable is sequenced.
2. **Hardware (section 2).** The engine-2 clock source is a **DFS clock** in the pinned Linux. If its flag (RCG+0x14 bit 0) is set, no RCG word is written and `GENI_CLK_SEL` is a firmware level, not table index 6; if clear, the plain six-row sequence applies. The I0 survey **reads the flag, the 24 level words and GPLL0 first**; `geni_uart_plan.py plan` turns them into rows or a STOP, and a guard row re-checks the flag before any write. 4 Mbps needs a 64 MHz engine clock (QUP version 2.5 or later).
3. **Flow control (2.4).** A busy-poll keeps up even with RTS ignored; an 8 ms poll does not. A slice bound counts the ACK in front of the response: 10 + `28 + L` <= 64 gives **L <= 26**.
4. **Guest (section 3), stages (section 4), tools (section 7).** One hardware-independent SAM core with three shells; the kext owns protocol and HID, EL2 the clock rows and a one-page grant. I0 survey, I1a clock on, I1b passive capture, I2a to I2e handshake, I3a to I3e guest HID, each with a host model now, one approval's experiment and an abort rule. Six host files are committed (section 7): two test files of 82 tests in all (54 and 28), each of which seeds bugs that must be caught, and the older 16 frame tests; all pass.

## 1. The protocol, from public facts

Keys: `SSH` = Documentation/driver-api/surface_aggregator/ssh.rst; `INT` = internal.rst, `OVR` = overview.rst; `SH` = include/linux/surface_aggregator/serial_hub.h; `CH` = .../controller.h; `CORE`, `CTL`, `PTL`, `RTL`, `MSGB` = drivers/platform/surface/aggregator/{core.c, controller.c, ssh_packet_layer.c, ssh_request_layer.c, ssh_msgb.h}; `REG` = surface_aggregator_registry.c; `HUB` = surface_aggregator_hub.c; `SHID` = drivers/hid/surface-hid/surface_hid.c, `SHC` = surface_hid_core.c and .h. Everything in sections 1.1 to 1.3 is VERIFIED (read at the pin) unless marked; none of it was seen on an SP11 wire (UNKNOWN until I1b).

### 1.1 Framing, acknowledgement, timing

| Fact | Source |
| --- | --- |
| A message is `AA 55`, the frame (type u8, length u16, seq u8), the CRC-16 of those 4 bytes, `length` payload bytes, the CRC-16 of the payload; little-endian; an empty payload still has its CRC (0xFFFF) | SSH:42-102, MSGB:106-137, SH:176-192 |
| CRC = `crc_itu_t(0xffff, ...)`: polynomial 0x1021, MSB first, no reflection, no final XOR (check value 0x29B1) | SSH:42, SH:189-192 |
| Types: DATA_SEQ 0x80 (needs an ACK), DATA_NSQ 0x00, ACK 0x40, NAK 0x04; ACK and NAK carry no payload, data frames must | SH:45-50, SSH:104-134 |
| An ACK echoes the data frame's seq; a NAK's seq is 0 and means "re-send everything unacknowledged"; the seq counters are independent per direction | SSH:137-190, MSGB:137-210 |
| One un-ACKed sequenced frame per direction: the host's `SSH_PTL_MAX_PENDING` is 1 (**PTL:212-218**); the EC re-sends after 1 s, three transmissions in all; a repeat is detected against the *last* seq only | PTL:184,201,212-218, SSH:306-346 |
| Host receive: an ACK is queued for every DATA_SEQ **before** the repeat check; repeats are filtered against the last 8 seqs; DATA_NSQ is never filtered (some models send all with seq 0); garbage before a SYN or a bad frame makes the host NAK | PTL:1599-1647, 1697-1792, 2057-2060 |
| Requests: at most 3 outstanding (`SSH_RTL_MAX_PENDING`), timeout 3 s **counted from transmission in Linux** (the model counts from submit: POLICY), a target id other than 0 is dropped; request ids count from 0 and skip the event range 1 to 38 (0, 39, 40, ...) | RTL:29-60, 909-937, SH:194-247, CTL:40-97 |
| The command payload is `80 tc tid sid iid rqid(2) cid` and data; a response swaps tid and sid and keeps tc, iid, cid and rqid; events carry the id chosen at enable time (the controller uses `rqid = tc`) | SSH:193-304, SH:96-106, CTL:1873-1900 |
| Targets: host 0, SAM 1, KIP 2; categories SAM 0x01, KIP 0x0e, HID 0x15, REG 0x21 | SH:289-347 |

### 1.2 What the EC expects first: the bring-up order

The Linux probe is the only written handshake: serdev setup (baud, RTS/CTS and parity from the ACPI resource, or 4 Mbps, RTS/CTS and no parity from the device tree), then **firmware version, D0 entry, display on** as ordinary requests, then the wake-line interrupt (CORE:307-377, 636-720). The flow section lists only DATA, ACK and NAK ("no longer exchanges are known", SSH:189-190); the older linux-surface note `de6d4038` has none either.

| Step | tc and cid, payload | Response | Source |
| --- | --- | --- | --- |
| fw-version | 0x01 / 0x13 | u32 | CTL:1819-1825, 1998-2015 |
| D0 entry | 0x01 / 0x34, only if `d3_closes_handle` (gate CTL:2170; exit 0x33, gate CTL:2128) | u8, 0 = ok | CTL:1847-1851 |
| display on | 0x01 / 0x16 (off 0x15 holds events back and raises the wake line; on releases them) | u8 = 0 | CTL:1826-1837, 2019-2102 |
| KIP events on | 0x01 / 0x0b, payload `0e 01 0e 00 00` (tc, flags = **sequenced**, rqid le16, iid) | u8 = 0 | CH:914-921, CTL:1855-1950, HUB:176-179, 305-345 |
| KIP state | tc 0x0e / 0x2c | u8, non-zero = attached; also an event; devices need about 250 ms after a connect | HUB:289-345 |
| HID descriptors | tc 0x15, **tid 2**, iid 1 or 3 (2 and 5 exist) / 0x04, payload `entry u8, offset u32, length u32, end u8`; entries 0 (the 9-byte HID descriptor), 2 (the 32-byte attributes), 1 (the report descriptor); the response repeats the header with the slice's real offset, length and end, then data; Linux asks for 118-byte slices | | SHID:25-111, SHC:19-97, 189-225 |
| HID events on | tc 0x21 / 0x01 (off 0x02), **tid 2**, payload **`15 00 15 00 iid`: flags 0**, passed unchanged by CTL:2361 | u8 = 0 | CH:920-921, **SHID:216** |
| HID input event | tc 0x15, sid 2, iid, cid 0x00, rqid 0x15, the raw report as data (report-id byte first if numbered); with flags 0 it arrives as **DATA_NSQ: no ACK, so no pacing by the host** | none | SHID:157-166 |
| HID commands | cid 0x01 output report (no response), 0x02 get feature, 0x03 set feature | | SHID:35-40, 113-155 |

The HID client's order is: descriptor 0, attributes (2), `hid_add_device` parses (descriptor 1) **before** events are enabled (SHC:193-225); `SSAM_RETRY` is 3 tries (CH:286-287). The script is `bring_up` in `tools/sam_session.py`; Linux's shutdown order (events off, display off, D0 exit: CORE:383-409) is `tear_down`.

**The unit's own description (OBSERVED once: the unit's firmware tables, read offline; the SSDT is byte-identical in the six local dumps and one was decoded with `iasl`).** The SSDT device `\_SB.SSH` has `_HID` MSHW0084, `_DEP` on UAR3 and GIO0, a UART resource of 4,000,000 baud, 8N1, hardware flow control and FIFO fields 0x40 and 0x40, and a `_DSM` (the GUID of CTL:1013-1026) returning for function 5 a runtime variable, 6 = 0x1388, 7 = 0x1E, **8 = 1**, 9 = 0x30, 0xA = 2. The DSDT device `\_SB.UAR3` is QCOM0C16, named `"QUP_0_SE_2,4W,DP"`, at 0x00B88000 and 0x4000 bytes, interrupt 616, with no `_PS0` or `_PS3`; the PEP table lists only I2C1, I2C4, I2C5, I2C8, IC10, SAR1 and SAR2 (no UART). SPCR and DBG2 name `QUP_2_SE_5` (0x00894000) as the console, so q1n1's `--uart` cannot write into the EC. `SDEV` lists only `\_SB.SISP`. This replaces step 0 of the study's G1 (reading the DSDT resource).

### 1.3 The hub and the HID registry

The KIP hub is `ssam:00:00:01:0e:00`; the SP11 group (`microsoft,denali`) lists the HID children `ssam:01:15:02:01:00` keyboard, `:02:00` pen stash (events, not the digitizer), `:03:00` touchpad and `:05:00` Type Cover firmware update, plus battery, AC, thermal, fan and the tablet switch (REG:43-46, 194-216, 408-422, 514). The hub queries the connection state at once and 250 ms after each connect event (HUB:67-196, 283-345); a guest kext must remove and re-add the HID children on detach and attach.

### 1.4 Report descriptors (UNKNOWN) and what is public

No pinned source holds the Type Cover's descriptors and no public SP11 dump was found. Third parties report the cover as "Microsoft Surface 045E:09AE Keyboard" on an SP8 (linux-surface issues 2294 and 2382) and a touchpad that needs the Windows Precision Touchpad mode switch before it reports (OBSERVED, third-party report, one unit each, not pinned and not checked). Expected (INFERRED): the keyboard is Generic Desktop/Keyboard (modifiers, a key array or bitmap, Consumer keys, LEDs); the touchpad is Digitizers/Touch Pad (13/5) with Device Configuration (13/14, Input Mode). They are read in I2d; `tools/sam_hid_desc.py` is ready.

### 1.5 Power, wake, and what the cover does when nothing talks to it

- **Wake line gpio91** (OBSERVED, boot 29: `F91=1`, `L91=0`, not enabled at hand-off). With the display off the EC holds events and raises gpio91 until released (CTL:2704-2790); a polled receiver with the display on does not need it.
- **Unprompted streaming (UNKNOWN).** Events need an enable request (INT:476-480, OVR:73-75, reverse engineered); Linux always enables HID events, so the exception is untested. The EC re-sends an un-ACKed sequenced frame at 1 s, three times (SSH:322-327): a capture that never ACKs shows *repeats*, a sign of a live EC. Boot 29 read RTS (gpio9) and RX (gpio11) high with no armed read: if the EC honours CTS it cannot speak until reception is armed, so "nothing happened" proves nothing (I1b).
- **EC state may outlive the session (OBSERVED, third-party report):** a cover not re-initialised after hibernate stays dead until re-attached (linux-surface issue 2294), so teardown must end in a known state and the first request must not depend on state.

## 2. The hardware path to a running GENI UART at 4 Mbps

Keys: `GSER` = drivers/tty/serial/qcom_geni_serial.c, `GSE` = drivers/soc/qcom/qcom-geni-se.c, `GSEH` = include/linux/soc/qcom/geni-se.h, `GCC` = drivers/clk/qcom/gcc-x1e80100.c, `RCG` = clk-rcg2.c, `COM` = clk/qcom/common.c, `ALPHA` = clk-alpha-pll.c, `CB` = clk-branch.c, `MSM` and `PX` = pinctrl-msm.c and pinctrl-x1e80100.c, `HAM` = hamoa.dtsi, `DEN` = x1-microsoft-denali.dtsi. "B29" is boot 29's words (OBSERVED).

### 2.1 What must be on, what boot 29 saw, what a write must do

| Need | State at hand-off | Action | Source |
| --- | --- | --- | --- |
| Wrapper 0 AHB master and slave, core and core_2x clocks | OBSERVED (B29): `MA0 SA0 CR0 C20` ON; the vote word `VT0 = 0x810223D0` has bits 6 to 9 set and bit 12 clear | none | GCC:4336-4361, 4954-4982 |
| Engine 2 clock branch (halt 0x142274, vote 0x152020 bit 12) | OBSERVED (B29): `SE0 = 0x80004000`, bit 31 = off | **a fresh read, then set vote bit 12** (a shared word); poll bit 31 clear, at most **200 us** | GCC:4434-4450, CB:62-92 |
| **RCG mode: DFS or plain** (RCG at 0x142288: CMD +0, CFG +4, M +8, N +0xC, D +0x10, **DFS flag +0x14 = 0x14229C**) | **UNKNOWN: never read** | **I0 reads 0x14229C.** VERIFIED: the s2 RCG is in `gcc_dfs_clocks[]` (GCC:7409-7425, 7468-7469; COM:394-398); the probe reads RCG+0x14 and, if bit 0 is set, swaps in `clk_rcg2_dfs_ops` (is_enabled, get_parent, determine_rate, recalc_rate: **no set_rate**; RCG:1736-1768) | below |
| *DFS flag set* | UNKNOWN | **No RCG row is ever written.** Read the eight levels: CFG 0x1422A4+4n, M 0x1422E4+4n, N 0x142324+4n (n = 0 to 7; the current level is `(RCG+0x14 >> 1) & 0xF`); a level's rate is the parent through `2/(hid_div+1)` and, if its mode field is non-zero, `m/n` (`rcg2_decode_hz`). **`GENI_CLK_SEL` is the perf-table index** that Linux's `geni_se_clk_tbl_get` builds by rounding up from `freq+1` over the levels *in their own order* (GSE:679-716, at most 32 entries), not in sorted order (for example, levels of 64, 80, 7.37, ... MHz, in that order, name level 0). The plan STOPs unless **the level the selector names itself runs at the rate** (`hz[idx] == perf[idx]`, `idx` < 8) and some level gives 64 MHz x k exactly (k the in-engine divider); unused levels after it may hold anything. An earlier, stricter rule (the levels before the selected one must equal the perf table) stopped 12,489 of 60,000 random level sets that Linux would run, by the second fact-check's model; they now plan | GSE:679-716, RCG:1736-1768 |
| *DFS flag clear* | | The plain rows: M=0x0010, N=0xFFC4, D=0xFFB4, CFG field 0x2601 (mask 0x10371F; source GPLL0_EVEN = 6; 64 MHz = EVEN x 16/75), then UPDATE bit 0 and a poll of at most 500 us. `GENI_CLK_SEL` = table index 6 | GCC:1142-1180, RCG:26-47, 114-138, 517-598 |
| Order | | Vote, poll the CBCR, **then** the RCG rows (Linux enables the branch before it sets the rate). Whether UPDATE completes on an idle RCG with the root off is **UNKNOWN** (U18; shared RCGs force the root with CMD_ROOT_EN, RCG:1322-1354) | |
| Divider below the RCG (0x142284) | UNKNOWN; read-only in Linux (firmware-owned) | Read; it must be 0 (/1) or the table's rates are wrong: STOP | GCC:2237-2250 |
| GPLL0 (mode 0x100000: lock bit 31, active bit 30; L 0x100010, **ALPHA 0x100014**, user 0x100018: OUT_EVEN = /2; apps vote 0x152030 bit 0) | UNKNOWN, expected locked | Read; STOP unless locked, 600 MHz (INFERRED: the table's 150 MHz = EVEN/2 and 200 MHz = MAIN/3 rows both imply it; read as 31.25 x 19.2 MHz from L and ALPHA), a /2 output and the **vote bit 0 already set**; never write. OUTCTRL and RESET_N (Linux's `trion_pll_is_enabled` wants them) are **reported in the plan's notes, not required**, until U19 shows what a running GPLL0 reads on the unit | GCC:113-150, ALPHA:17-42, 187-201 |
| QUP version (wrapper 0x00BC0000 + 4) | UNKNOWN | Read; **4 Mbps exists only at 2.5 or later**; below it the nearest rate is 3.125 Mbps (-21.9 %), an inexact baud: STOP | GSE:188,299-310; GSER:1428-1452; GSEH:347 |
| Pins gpio8 to gpio11 (CTS, RTS, TX, RX) | OBSERVED (B29): `F8..F11 = 0x205 0x207 0x207 0x205`, function 1 on all four | none (the pulls differ from Linux's bias-disable, HAM:6692-6720; DEN:1316-1329 is the EC node) | PX:36,1575-1578, MSM:326-330 |
| Engine firmware (protocol at +0x68 bits 15:8; 0xFF = none) | UNKNOWN (gated) | Read after clock-on; 0xFF: **STOP**. Linux loads firmware only if the protocol reads 0xFF **and** the wrapper has `firmware-name`; HAM and DEN set none | GSEH:40, GSER:1342-1352, GSE:1552-1611 |
| RPMh and interconnect votes, the CX level, the qup-core vote | not readable or writable from EL2 | Linux votes the 50 MHz class for baud > 115200 (GSER:1472-1475, GSEH:349-362); core_2x's rate is unreadable (U21) | HAM:670-682, 2337-2360 |
| GIC INTID 616 (SPI 584, level high) | OBSERVED (B29): not enabled, pending or routed | Polled first; later `compat/irq-forward.c` | HAM:2337-2342 |

### 2.2 The write set, as data, and its undo

`python3 tools/geni_uart_plan.py reads` prints the I0 survey (**41** words: the engine branch, the vote, the GPLL0 vote, the divider, the **DFS flag, RCG CMD, CFG, M, N and D, 24 level words**, GPLL0 mode, opmode, status, L, **alpha** and user, the QUP version) and the engine's **39** reads (it adds MANUAL_RFR 0x2AC, LOOPBACK 0x22C and, read only, `SE_GSI_EVENT_EN` 0xE18 and `SE_IRQ_EN` 0xE1C; it never reads a FIFO port). `plan words.json` turns a survey into the clock steps and the DFS-or-plain decision, or a STOP with its reason; `baud` prints the arithmetic. `clock_rows(target_hz, dfs_word)` **raises on a DFS flag** (or on none supplied). Row kinds: `rmw` (a masked read-modify-write, the readback compared), `w1c` (a status clear: not compared), `latch` (the RCG UPDATE: it self-clears, expect 0) and `cmd` (the S-sequencer start). `test_every_table_row_decodes_to_its_frequency` ties the rate table to GPLL0 = 600 MHz (INFERRED).

**Survey words.** A word is an integer (never a bool) or a numeric string; anything else, a missing word, a survey that is not an object or a missing recorded word is a `PlanError`, never another exception (3,000 random corruptions, 0 other exceptions: the author's run). `gpll0_vote` joins the identity words (an all-ones identity word is a bus-fault suspicion: STOP). The CLI verifies the rows **before** it prints a step, and a bad file or bad JSON prints one `STOP:` line.

**Engine rows** (`uart_rows(plan, rx_fifo_words)`), in the order of Linux's setup: rx stale count 160 (160 bit times = 40 us at 4 Mbps), packing 0x0004380E and 0x000C3E0E, byte granularity, the **W1C clears of the stale M and S status (0x618 and 0x648) before** the clock-gating and IO init (GSE:345-352,368 precede 328-343), CGC 0x28 |= 0x7F, DMA_CGC 0xE30 |= 0xF, output control, `force_default`, FIFO mode, watermarks 2 and depth - 2, clock config 0x11 with the plan's `GENI_CLK_SEL` and divider, tx and rx config (8N1, hardware CTS), parity, **`manual_rfr` 0x2AC <- 0 and `loopback` 0x22C <- 0** (GSER:253-270, 1574-1575), word length; `start_read` last (Linux starts it earlier, in startup: POLICY). M_IRQ_EN, S_IRQ_EN and SE_IRQ_EN stay **as found** (the A16 polls with M_IRQ_EN = 0). TX rows (TX_TRANS_LEN 0x270, TX_WM 0x80C, M_CMD0 0x600, the M_IRQ_STATUS poll and clear) **do not exist until I2**. `force_default`'s effect on RTS and TX with the EC attached is UNKNOWN (Linux's abort path also uses it, GSER:389): it may be dropped from the first experiment.

**The guard is an exact allow-list.** Forward rows are exact (block, offset, mask, value, kind): `allowlist()` and `rows_digest()` give what the operator's record must list, `GCC_ALLOWED`, `ENGINE_ALLOWED` and `ENGINE_FORBIDDEN` are the reviewed shapes that `verify_rows` checks, with the value's form, a `cmd` row being the S_CMD0 start-read opcode and a `w1c` row being non-zero. `ENGINE_FORBIDDEN` names M_CMD0, M_CMD_CTRL, S_CMD_CTRL and 0x700 to 0x7FF; a weakened row (a wider mask, a changed kind, another offset) fails (test and mutant). **Undo rows cannot be in that list**, because their values are the words read before the run: they are a shape plus a value rule. `UNDO_FIXED` names every fixed undo store with its only values (S_CMD_CTRL cancel 4 or abort 2, the status clears, FORCE_DEFAULT 1, the RCG latch, the vote clear 0), a restore must equal the recorded word under the forward mask, and `undo_shapes(plan, rows)` is the (block, offset, mask, kind) set that the carrier's undo guard is built from.

**Undo.** Clearing the vote undoes only the gate. In plain mode the RCG's M, N, D and CFG **keep** the 64 MHz values: the undo rewrites the recorded words, runs a **second UPDATE** and polls; a DFS plan can never write the RCG latch. **Engine rows stay written** unless restored from the recorded values (`undo_steps` does, for `rmw` rows); **W1C rows cannot be undone**. A started S command needs S_CMD_CTRL (0x634) cancel, a poll of S_IRQ_STATUS (0x640) bit 4, an RX FIFO drain and a status clear (GSER:946-974; the cancel poll is :960-961 and the status clear :970), all **before** any word is restored. When `rx_idle` times out, the abort fallback of `qcom_geni_serial_abort_rx()` (GSER:381-391) is four steps, not prose: `rx_abort` (S_CMD_CTRL = 2), `rx_abort_done` (a poll), `rx_abort_clear` (S_IRQ_CLEAR = DONE and ABORT) and `rx_force_default`, all of them before any restore. The vote is dropped **last**, and not at all if the recorded vote word already had bit 12 (another master): `recorded['vote']` is required (a missing word is a STOP, not a blind `vote_clear`) and `verify_undo` refuses a `vote_clear` when the vote was held when the run began. `verify_undo(plan, rows, steps, recorded, started_rx)` checks **every store of every kind** against `UNDO_FIXED` or against the word read first: a redirected or re-valued cancel, an inserted M_CMD0 row, a status clear of 0, a wrong restore value, a repeated step, a poll aimed at a FIFO port or without a bound and a conditional restore are all refused. Afterwards the CBCR may still read ON (another voter; Linux waits only 10 us after dropping a voted branch, CB:75-76). No undo restores W1C clears, `force_default`'s pad effect or the EC's own state (U11).

### 2.3 FIFO or DMA, depth, partial words

FIFO, polled: every FIFO-mode register lies below +0xE40 (GSEH:96-155), inside one 4 KiB page. **Linux's own SAM link does not run this way:** a non-console `qcom,geni-uart` uses DMA with a 2 KiB RX buffer and RFR_OPEN (GSER:2152-2159), so the FIFO, RFR and overrun case is **not exercised by Linux** (U4). `IF_DISABLE` (0x64) bit 0 set means the FIFO interface is disabled, and its enable (+0x2008) is outside the granted page (GSE:1538-1541): **STOP**. Depth: `HW_PARAM_1` 0xE28 bits 21:16 (23:16 at QUP 3.10 or later, GSEH:152-155, 319-331); 16 words is only Linux's earlycon default (GSER:1920-1922); the unit's ACPI says 0x40 bytes (OBSERVED once, the firmware's declaration). The TX frames fit (fw-version 18 B, a descriptor request 28, an ACK 10).

**Partial words (UNKNOWN, U22).** Linux's FIFO readers assume that only the *last* word is partial and take the status from the RX_LAST interrupt (GSER:412-422, 519-531); the stale timer is 40 us. A slower polled reader can see a partial word in mid-FIFO; the model handles one `last_valid`, so I1b reads the FIFO status once **per drain**.

### 2.4 Poll arithmetic and the flow-control risk (INFERRED)

At 8N1 a byte is 10 bit times = 2.5 us; **the 64-byte FIFO fills in 160 us**; RTS drops at the RFR watermark (14 words = 56 B, 140 us). With RTS ignored the FIFO overruns **unless a word is read at least every 10 us or so (one 4-byte word per 10 us at 4 Mbps: a busy-poll can, an 8 ms poll cannot)**. Windows configures hardware flow control (OBSERVED once, the ACPI), so the EC is expected to honour it (INFERRED); U4 settles it. A slice mitigation must bound **the bytes the EC can send within one poll period, not one frame**: after a request the EC sends its ACK (10 B) and then the response (`10 + 8 + 10 + L`), so `38 + L <= 64` gives **L <= 26** (exactly 64 at 26, no margin; **L <= 18** stays under the 56 B RFR mark). The test records the peak bytes per poll tick: 64 at L = 26, 68 at 30 and 156 at the default 118. Whether the EC accepts other lengths is UNKNOWN (U24). Rules: (1) the EL2 experiments (I1b, I2) **busy-poll**; (2) requests are **serialised** (the next waits until the previous response is drained and ACKed); (3) HID input events are DATA_NSQ with **no ACK pacing**, so a touchpad burst can exceed the FIFO inside one 8 ms period: the kext needs the forwarded interrupt or a fast-drain timer unless U4 shows that RTS works (I3e).

### 2.5 Hazards that stay

An access to an unclocked engine can hang the bus with no recoverable abort: every engine read stays gated on the branch reading ON. A clock write is AGENTS.md section 5 territory (explicit approval, AC power, the thermal guard).

1. **DFS** (2.1): with the flag set, `clk_sel` = 6 would select an arbitrary firmware level (a wrong baud or an over-clock); a guard row re-reads the flag in the I1a record.
2. **Other users:** the RCG also parents `gcc_qupv3_wrap0_qspi_s2_clk` (halt 0x142280, GCC:4362-4371), unused by the UART; no other engine's RCG or branch and no wrapper vote bit is written; the vote word is shared: a **fresh read at execution, never the I0 snapshot**.
3. **Not undone:** see 2.2.
4. **Engine mid-transfer:** clock-on may release a command that the firmware left active (RTS asserted, bytes flowing); a W1C row erases an active transfer's status; `force_default` may glitch RTS and TX. Rule: the first read after clock-on is `STATUS` (0x40): **M_CMD_ACTIVE (bit 0) or S_CMD_ACTIVE (bit 12) set means a firmware-owned engine: STOP, no row written**.
5. **RCG UPDATE with the root off** (2.1, U18): a stuck update leaves a half-configured RCG, so the plain undo re-writes M, N, D and CFG and runs a second UPDATE; dropping the vote is not enough.
6. **Voltage and core clock:** the CX level (low_svs or higher at 64 MHz) and the qup-core vote are not reproducible from EL2 (U21).
7. **GPLL0:** Linux's enable chain also sets the apps vote 0x152030 bit 0 (GCC:117-118); "never write" is acceptable only because the plan STOPs unless I0 shows it set.

**What Linux's UART path does that this plan does not.** No resets (none in the UART path); the interconnect, OPP and CX votes (U21) and the GPLL0 apps vote (a STOP unless set) are not reproducible from EL2; the delays are in the steps (CBCR at most 200 us, UPDATE at most 500 us, the S-cancel poll); the stop path is the undo of 2.2; DMA mode is 2.3; the TX rows come with I2.

## 3. The guest side

### 3.1 What SnapI2CHID shows (read at `f0b7984`, the recorded pin; as its comments record)

- One `IOService` per device-tree node (`reg[0]` the engine's 4 KiB page, optional `reg[1]` a read-only TLMM page, poll period, irq policy); the transport and state machines are hardware-independent C behind `ioRead32`, `ioWrite32`, `now` and `delay` hooks that host tests drive with a model.
- It publishes an `IOHIDDevice` **and**, because 26A428's generic keyboard drivers are `IOMatchDefer` and none attached on the A16, its **own `IOHIDEventService`** (country code 33), a pointer service and, for a Precision Touchpad, an **Apple multitouch `IOHIDDevice`** (VID 0x05AC, PID 641, so `AppleMultitouchTrackpadHIDEventDriver` matches). `hid-ptp.c` takes the layout from the device's descriptor and does the mode switch (Input Mode = 3) through feature GET and SET hooks that a SAM transport supplies (HID cids 0x02 and 0x03). `hid-keyboard.c` is fixed to the A16's reports 54 and 55: another cover needs a descriptor-driven translation (`KeyboardModel` is its host oracle).

### 3.2 What a SAM kext needs, and the node

A SAM core in the SnapI2CHID style (GENI UART hooks, `compat/sam-frame.{h,c}` and the rules of `tools/sam_session.py`, tested against `FakeEc`); a bus service per node and one HID child per KIP instance, created and destroyed on connect and disconnect; a descriptor-driven keyboard (the country code from the descriptor's byte 4; the Fn row UNKNOWN until read); the touchpad through `hid-ptp` and `hid-mt2`; the pen stash (iid 2) ignored and the firmware update (iid 5) never touched. The kext matches on a node that this plan would add (UNIMPLEMENTED, a design), built where the A16 loader builds its input node (`probes/i2cinput.py`; the SP11 tree `probes/guestprofile_m1.py`): `snapintosh,geni-sam-uart`, `reg[0]` = the 4 KiB page 0x00B88000 (stage 2 Device-nGnRE, RW, XN, an allow-list of exactly that address like `compat/i2c-input.h`, refusing -41 otherwise), an optional read-only TLMM page 0x0F15B000, `uart-baud` 4000000, `poll-us`, `hid-instances` {1, 3}, `irq-policy` 1 (masked) until an AIC line exists (all properties `snapintosh,`-prefixed); a default-off gate (for example `SNAP_SP11_INPUT`) and a census row. The A16's addresses are **not** reused (0x00B80000 is the SP11's I2C1).

### 3.3 Who owns the UART: three options, weighed

**E (EL2 owns the UART and a virtual HID; the study's A1):** the whole SAM state machine runs at EL2 (AGENTS.md sections 2 and 3 forbid blocking in trap and timer paths), needs a monitor-to-guest HID ABI (carriers exist only for console and net) and a kext anyway, and a wedge freezes every guest vCPU; standalone-first yes. **K (recommended; the study's A2): the guest kext owns protocol, and EL2 does the reviewed clock rows and one page grant:** protocol bugs stay in the guest (a kext bug wedges one thread); standalone-first yes; it reuses SnapI2CHID's hardware-independent split, hid-ptp, hid-mt2 and hid-pointer. **B (host-forwarded virtual HID):** no privileged surface, but the cover is unused and the Mac must stay connected: **not** standalone-first, a login-window fallback only. The hardware dependence (the clock rows, the EC's behaviour: UNKNOWN) is identical for E and K.

**Recommendation: K, one core with three shells** (host tests; a q1n1 payload for I1 and I2 at EL2; the guest kext for I3), so no parallel implementation appears (AGENTS.md section 1). Repository: L1 (a new `drivers/` component) still holds; sharing SnapI2CHID's HID layers (L2) changes an A16-qualified kext. Both the path and the repository location are open decisions (section 4.5).

## 4. Staged plan

**The write carrier (UNIMPLEMENTED).** The plan assumes a payload-runner operation that takes the address, mask and value of one row from a table the runner validates; that framework is a design that is not committed, and its row format was not final when this plan was written. What follows are requirements on it, not a design of it. (R1) A masked RMW with a pre-read and a recorded undo value. (R2) A poll-until row, bounded in microseconds, for the CBCR and the UPDATE. (R3) A *validated frame table* for I2: a TX row writes at most 64 fixed, precomputed bytes to the TX FIFO port and starts the primary sequencer, checkable by hash. (R4) **Forward rows are exact (block, offset, mask, value, kind)** (`allowlist()`), not a block range; **undo rows are a shape plus a value rule** (`UNDO_FIXED`, `undo_shapes`; 2.2). (R5) **Guard rows in two groups.** Group 1, before the first write: read a word, compare under a mask and STOP on a mismatch (the DFS flag equals the state the record was built for, divider 0, GPLL0 locked and voted, identity words not all ones; the GPLL0 vote word is an identity word). Group 2, between the CBCR poll and the first engine row: STATUS bits 0 and 12 clear, protocol 2, IF_DISABLE bit 0 clear and a receive depth of 4 to 256 words, which exist only once the engine's own clock runs; `engine_rows(plan, words, qup)` raises unless `engine_stops()` is empty, so the engine rows cannot be produced after a stop. The payload never branches on what it reads: the host decides DFS or plain from the I0 words and the record carries their digest. (R6) The kinds `w1c`, `latch` and `cmd`, and the undo's cancel and drain sequence, as data.

### I0 read-only survey (one boot, no write)

- **Host now:** `geni_uart_plan.py reads` and `plan` and the decoders (`decode_dfs_cmd`, `decode_dfs_levels`, `decode_gpll0`, `decode_cbcr`, `decode_qup_version`); the 41-word list of 2.2 plus boot 29's TLMM words. Wrapper AHB words stay gated on `MA0 SA0 CR0` ON, as the engine blocks are. (The q1n1 table and its test belong to the q1n1 fork.)
- **Hardware:** one read-only boot like boots 29 to 31. **Settles** U2, U17, U19 and decides the rows (DFS or plain, the eight levels, GPLL0 locked, 600 MHz and voted, QUP 2.5 or later, divider 0, RCG already 64 MHz). **Abort:** stop at the first all-ones word in GCC (a bus-fault suspicion) and report.

### I1a clock on, read the engine (one boot)

- **Host now:** `build_clock_plan` (the STOP list: an identity word all ones, divider not 0, GPLL0 not a locked 600 MHz with a /2 output, the GPLL0 vote clear, QUP below 2.5, a DFS level out of range, no exact level, a selected level that does not run at the rate), `verify_rows`, `engine_stops`, the engine page layout (the INPUTSNAP U2 list), read twice 8 s apart.
- **Hardware:** the group 1 guard rows (R5); a fresh vote read; vote bit 12; a CBCR poll (at most 200 us); **plain only:** M, N, D, CFG, UPDATE, a poll of at most 500 us, a read back; **DFS: no RCG write**. Then the group 2 guard (the engine reads: STATUS first, protocol, IF_DISABLE, FW_REV, `HW_PARAM`); wait 8 s; read again; **undo** (2.2); read the CBCR. No engine write, no command, no TX, RX not armed. Settles U1, U3, U18, U20, U23 and the FIFO depth.
- **Abort and undo:** the CBCR still off after 200 us; protocol not 2 (0xFF = none: record it, the blob question is an open decision); `IF_DISABLE` bit 0; `STATUS` bit 0 or 12 set; any identity word all ones; a readback that differs (`latch` rows read 0, `w1c` rows are not compared): undo and stop. AC power and the thermal guard (AGENTS.md section 5). A hang cannot be aborted in software: the I0 gating and the clock-on check make it unlikely, and approving the record's SHA-256 accepts the risk.

### I1b arm reception, capture passively (one boot, no TX)

- **Host now:** `sam_transcript.classify_capture` (SILENT, STUCK, NOISE, SYN_NO_FRAME or FRAMES with the repeat count), `best_word_order` (which end of a FIFO word is first is UNKNOWN: "ambiguous" rather than a guess), `analyze` (every rule violation). The payload prints the first 128 captured bytes as words and a CRC-16 of the whole capture, to check a transcribed hex.
- **Hardware:** I1a, then the engine rows of 2.2 (rows already equal are adopted); `start_read` (it asserts RTS); a busy-poll drain of at most 8 s or 4 KiB with **the FIFO status read once per drain** (U22); **never send, never ACK**; cancel (2.2), undo, clocks last. Variants: the cover attached and idle, attached with keys pressed (someone present), detached. Settles U5, U7 (the first valid frame is the first VERIFIED-on-SP11 evidence), U8, U12, U22.
- **Abort:** any FIFO write-error, framing or parity status: stop reception and record; a capture limit; no byte for 8 s is SILENT (not a failure); always cancel S and undo last. Rows beyond the clock set are listed separately in the approved record.

### I2 the handshake, one authorisation per step

`tools/sam_session.py:bring_up` is the exact script (`slice_len` selects the slice); the hardware does at most the next step and stops.

| Step | Sends | Changes in the EC | Authorisation |
| --- | --- | --- | --- |
| I2a | fw-version (18 B) and an ACK of the response | nothing (read-only) | its own: the first write to a system controller |
| I2b | D0 entry, display on | the power state; undone by display off and D0 exit | its own |
| I2c | KIP events on (`0e 01 0e 00 00`), KIP state | an event source | its own; the cover is attached and detached by someone present |
| I2d | descriptors 0, 2 and 1 for iid 1 and 3 (default 118-byte slices at EL2 busy-poll; **26 B or less, preferably 18 B or less** for a polled kext, U24) | none (read-only) | with I2c; settles U9, U4, U24 |
| I2e | HID events on for iid 1 and 3 (`15 00 15 00 iid`); keys pressed; then `tear_down` | event sources; the teardown leaves the EC as Linux's shutdown does, not necessarily as found (U11) | its own |

- **Host now:** the model; `FakeEc` with faults (deaf, silent, corrupt, duplicate, wrong target, wrong request id, lying descriptor, refusal, **descriptor_stall, descriptor_rewind, descriptor_overlong**); 54 tests; the R3 frame table can be generated from the script.
- **Abort rule R-I2 (fail closed):** no ACK after three transmissions (3 s); a NAK; a response whose target is not the host; a non-zero status; a slice longer than requested (`descriptor_overlong`) or longer than its data; **a slice that does not advance** (length 0 without end, or an offset other than the one asked); **more requests than the descriptor needs: the budget is 2 x ceil(total / L) + 3** (an earlier budget of ceil + 3 refused a controller that answers with slices under about a sixth of the descriptor, although Linux accepts any positive slice: for a 334-byte descriptor at L = 118, 56 B passed and 55 B was refused, and at L = 26, 21 B and 20 B; the new budget passes 38-byte slices at L = 118 and refuses 37, and at L = 26 the limits are 12 and 11; the bound is POLICY); a report descriptor over 4096 B; or a total different from the HID descriptor's length. Then **send nothing more**, run `tear_down` only for the steps already taken, and report. Linux has the same unbounded loop (SHID:77-102); this one is bounded (POLICY). At most the approved steps are sent; no retry beyond the protocol's three.
- **Settles** U6, U10, U15.

### I3 guest HID

- **I3a** (host; NOT RUN on hardware): the SAM core in C and a stand-in harness for the kext (as `tools/test-snap-i2c-hid-*`) against `FakeEc` and a fake engine; the descriptor-driven keyboard translator in C against `KeyboardModel`. The models exist; the C and the harness are I3's first work.
- **I3b:** the kext in the M1 guest with a synthetic report source to prove the IOHID path (loader node, gate, profile tests); it needs rung 10 or later (the root with its ISC: [SP11-AFTER-ISC.md](SP11-AFTER-ISC.md)) and the AuxKC path.
- **I3c:** the real engine in the guest: the monitor applies the clock rows at start, undoes them at stop and maps the one page; the kext runs the I2 script, keyboard first. One supervised boot; the abort is the monitor's stop; the kext goes quiet after its first protocol failure.
- **I3d:** the touchpad through `hid-ptp` and `hid-mt2`, feature GET and SET over cids 0x02 and 0x03 (host: `ptp_layout`); after I3c.
- **I3e:** interrupt-driven RX through `compat/irq-forward.c` if U4 or the NSQ burst of 2.4 says that polling overruns; a GIC route write is approved separately.

### 4.5 Open decisions and facts to supply (none blocks the host work; none is made here)

Q1 Does the attached cover type in the firmware's own shell? Q2 Which cover (model) is attached? Q3 Is the driver a new component (L1) or an extension of SnapI2CHID (L2), and is K, E or B the path (3.3)? Q4 Is I0 approved after rung 10? Q5 After I2, may the cover keep the state that the teardown leaves until a power cycle, and will the firmware and Windows be checked?

## 5. UNKNOWNs and the measurement that settles each

| # | UNKNOWN | Settled by |
| --- | --- | --- |
| U1 | does the engine hold UART firmware (protocol 2) | I1a: `FW_REV` after clock-on |
| U2 | the divider and the QUP version | I0 |
| U3 | what the boot chain left (baud, format, flow control, packing, FIFO mode) | I1a reads |
| U4 | does the EC honour RTS (poll viability, overrun at 8 ms; Linux itself uses DMA) | I1b and I2d overrun status on a 146-byte response; a poll-period sweep in I3c |
| U5 | does the cover send unprompted; the EC's state after hand-off | I1b (repeats of un-ACKed frames, with and without keys) |
| U6 | does the EC accept fw-version before D0 entry; what D0 and display do | I2a, I2b |
| U7 | do CRC, framing and sequence rules match real frames | the first valid frame (I1b) |
| U8 | the byte order inside the FIFO words | I1b (`best_word_order`) |
| U9 | the keyboard, touchpad and pen-stash descriptors; country code; report ids | I2d |
| U10 | does the touchpad need the PTP mode switch; its features | I2d and I3d |
| U11 | the effect on the firmware's and Windows' next boot; the EC's state across our run | a check by someone with the unit (Q5), after I2 |
| U12 | pin levels with the cover detached (does the EC drive RX?) | I1b variants |
| U13 | the CX and RPMh level at 64 MHz | a stable clock and reads, no hang |
| U14 | wake-line behaviour (gpio91) | TLMM reads in I1b and I2e |
| U15 | detach and attach events and the 250 ms settle | I2c, the cover moved by someone present |
| U16 | the AIC line and node properties for the forwarded interrupt | I3e design, after U4 |
| U17 | is the s2 RCG in DFS mode on this unit; the eight levels; the current level | I0: 0x14229C and the 24 level words, through `plan` |
| U18 | does RCG UPDATE complete on an idle RCG with the root off (plain path only) | I1a poll of CMD bit 0; a stuck update triggers the undo |
| U19 | GPLL0's rate (600 MHz is INFERRED), ALPHA, OUTCTRL and RESET_N of a running PLL, the apps vote | I0: mode, L, alpha, user and vote words |
| U20 | `IF_DISABLE` bit 0 and the active STATUS bits after clock-on | I1a first engine reads |
| U21 | core_2x's rate and the qup-core vote in force | not readable at I1a; a stable clock, sane engine reads, a valid received frame (I2) |
| U22 | partial words in mid-FIFO for a polled reader | I1b: status read per drain |
| U23 | an engine mid-transfer at clock-on | I1a: STATUS read before any engine write |
| U24 | does the EC accept descriptor slices other than 118 B | I2d |

Out of scope: touch and pen (HID-over-SPI, engine 0x00A88000, INTID 834): I0 can read their RCG words at no cost.

## 6. Citations and fetches

Linux files at `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` from `https://raw.githubusercontent.com/torvalds/linux/<pin>/<path>`, fetched on 2026-11-22 into a scratch directory outside the tree (the SHA-256 prefixes of eight key files equal the q1n1 registers document's; `drivers/clk/qcom/common.c` is `ebefa181`): the surface_aggregator docs, headers and drivers, surface-hid, the registry and hub, and `drivers/clk/qcom/{gcc-x1e80100.c, common.c, clk-branch.c, clk-rcg2.c, clk-rcg.h, clk-alpha-pll.c, clk-alpha-pll.h}`. Older note: linux-surface/surface-aggregator-module `de6d403852f33f5445c25971a3f25e6ebafbf824`, `doc/ssh-protocol.txt`. Third party (OBSERVED, one unit each, not pinned, not checked by either review): linux-surface issues 2294 and 2382, the `docs/linux-support.md` of the lain3d/surface-pro-11-research repository and LWN article 1029848. SnapI2CHID at `f0b7984`, read only. The unit's tables: the dumps under `downloads/sp11/`, decoded with `iasl` into a scratch directory; only addresses, names, counts and the quoted strings leave it. In this repository: `compat/i2c-input.h`, `probes/i2cinput.py`, `compat/irq-forward.h`; in the q1n1 fork: `docs/SP11-PAYLOAD-INPUTSNAP.md` and `docs/SP11-PAYLOAD-INPUTSNAP-REGISTERS.md`.

## 7. The host tools (committed at `0a75501`; EXPERIMENTAL, host only, hardware NOT RUN)

| File | Lines and bytes | What |
| --- | --- | --- |
| `tools/geni_uart_plan.py` | 783 / 44,927 | the DFS-aware survey, `build_clock_plan`, the rows, the allow-list, `verify_undo`, the engine gate and the decoders |
| `tools/sam_session.py` | 756 / 35,830 | the scanner (the stall timer restarts while the frame grows), `HostStack`, `FakeEc` with faults, the bounded `read_descriptor`, `hid_events_payload`, `bring_up(slice_len)` and `tear_down` |
| `tools/sam_hid_desc.py` | 454 / 21,216 | a bounded parser (count 4096, field and report bits, usage range, cumulative usages), a builder, `KeyboardModel`, `ptp_layout` |
| `tools/sam_transcript.py` | 269 / 13,672 | the transcript format, `analyze`, `classify_capture`, the FIFO word unpacker |
| `tools/test-sam-session.py` | 852 / 48,580 | 54 tests (a fuzzed wire, 3,000 and 1,500 random descriptors); one of them seeds 20 bugs that must each be caught |
| `tools/test-sam-session-rows.py` | 688 / 40,885 | 28 tests; one of them seeds 13 bugs that must each be caught (below) |
| `tools/test-sam-frames.py` (older) | 495 / 25,275 | the 16 frame-codec tests of `compat/sam-frame.{h,c}` (study section 4) |

**Commands and results.** `python3 tools/test-sam-session.py` PASS (54 tests), `python3 tools/test-sam-session-rows.py` PASS (28 tests) and `python3 tools/test-sam-frames.py` PASS (16 tests; it builds the codec natively under sanitizers): the counts that the commit and the plan report, and the counts of a run at `0a75501` for this note (maximum resident sets 36, 37 and 312 MiB; the third is the largest process and takes about 20 s, the others under 1 s). NOT RUN: hardware; any q1n1 payload (none is written); the 2^32 parser case (a crash risk).

**Row pins.** `test-sam-session-rows.py` re-derives every value from the pinned C, transcribed in the test and sharing no code with the plan: the packing words (`geni_se_config_packing`, GSE:539-591), the clock-table walk and the baud match (GSE:679-779, GSER:1426-1450), the RCG M, N, D and CFG encoding (RCG:517-562), the C constants of the 28 engine rows (names, order, offsets, masks, values, kinds, absolute addresses) and of the 6 clock rows, with FIFO depths 4, 16, 64 and 256, plain and DFS (including a 128 MHz level with the divider at 2), and the two polls (200 and 500 us). A seeded change of the value **or** the mask of any one row (34 rows x 2, both modes) is caught. The DFS, malformed-word, CLI, undo-guard, engine-gate, scanner, descriptor-read and parser-limit cases of that file each pin one correction of section 8.

**Tests that pin each earlier correction.** Each correction of section 8 has a test that fails without its fix (for example `test_a_slice_that_does_not_advance_fails_closed`, `test_a_growing_frame_is_not_flushed`, `test_hid_events_are_enabled_unsequenced`, `test_the_undo_is_complete`, `test_the_allow_list_is_exact`, the DFS tests and `test_the_descriptor_read_is_bounded_by_what_it_needs`); `test_declared_sizes_are_bounded` pins the parser's limits at 2^16 and never builds a 2^22 or 2^32 case.

**Scanner.** The stall timer is reset when an overflow trim discards bytes and when bytes are skipped, so that it never outlives the frame it was started for. **Parser.** The usage-range cap (4096) applies to VAR fields only; an ARRAY field keeps two usages however wide its range.

**Reported by the plan's author and reviewers, not re-run for this note.** The 82 tests of the new files run against the modules of the previous draft: 23 fail (the rest pin behaviour that those modules already had). A mutation spot check in memory (61 one-statement mutants, among them the reviewer's 12): 57 killed; 4 survive, namely the S_CMD_CTRL ban (forbidden twice over), `D = N` against the clamp for every table row (both equivalent mutants, as the reviewer says), and each of the two stall-timer resets alone (they are redundant: removing both is killed). The second fact-check's model, over 150,000 random near-valid survey word sets, emitted no row when a STOP was due; its 4,000 differential frame seeds forged nothing and accepted no frame with a bad CRC.

**Limits.** The fixtures are computed, never captured; `FakeEc` is written from the same sources as the model and can share their misreadings; the POLICY choices (the NAK rate limit, the stall flush, the slice checks, the request-timeout start, the read bounds, `start_read` last) are marked in the code and are not Linux's. The reviewers re-derived the arithmetic for a plain RCG; the DFS decoding is checked only against the pinned C text: hardware (U17) decides.

## 8. What the two independent fact-checks changed

The first fact-check (64 claims) and the second (a delta check) are host-only notes that are not in this repository; both ended SOUND WITH CORRECTIONS and every correction is applied above: the DFS clock, the survey that reads its flag, levels and GPLL0 first, and the guard row that re-reads the flag (2.1, 2.2, R5, I0, I1a, U17); the HID events-on flags 0, where an earlier draft had 1 (1.2, I2e); the bounded descriptor read with its budget, and the refusal of a slice that does not advance or is longer than asked (R-I2); the stall timer (7); a complete undo, checked store by store against fixed shapes and values or the recorded word, with the abort fallback as four steps (2.2, I1a); the slice bound that counts the ACK, L <= 26 where an earlier draft said 30 (2.4, U24); an exact forward allow-list, the undo's shape-plus-value rule and two guard groups (2.2, R4, R5); the extra reads and aborts (IF_DISABLE, STATUS, SE_GSI_EVENT_EN, SE_IRQ_EN), `manual_rfr`, `loopback` and the W1C and `cmd` row kinds (2.2, 2.3, 2.5, U20, U23); the bounded parser and golden values for every row (7); partial words, the FIFO depth and Linux's DMA mode as UNKNOWN (2.3, U4, U22); the selector rule of the selected level itself and GPLL0's OUTCTRL and RESET_N reported, not required (2.1, U19); survey words that are integers or numeric strings, a missing word being a STOP, and the 39-word engine read list (2.2); and citation fixes (1, 2).

The reviewers could not check third-party reports, the unit's DFS flag, level words, GPLL0, FIFO depth and the EC's behaviour (U17 to U24 stay open), the boot 29 words (taken from the plan; they checked what the pinned source says they mean) or the write carrier (requirements, not code).
