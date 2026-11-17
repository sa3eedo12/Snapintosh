# SPDX-License-Identifier: MIT
"""The authorisation record of point A1 for the xHCI S1 No-Op DMA test (docs/SP11-XHCI-S1.md): the text the operator approves, in writing, before the
payload `xhci` of the q1n1 fork runs on the unit, and its deterministic generation.

A1 is the first write to an xHC register at EL2 (docs/SP11-STICK-LOG.md sections 5, 6 and 11). It was approved by the operator on 2026-11-17 02:10 for
a supervised test; A2 (the first sector write to the stick) is NOT granted and nothing here asks for it. This module renders the text from the facts
that bind it: the registers, the groups and the budgets of the payload (checked against the payload's header by tools/test-sp11-xhci-s1-record.py),
and the names, sizes and SHA-256 of the files: the payload files and the runbook docs/SP11-XHCI-S1.md, whose outcome tree the stop rules of the text
refer to, so that the text the operator approves names the exact runbook it is read with. It writes no file and approves nothing: a digest in the
text is the operator's to compare with the staged file, and no run is authorised by this module, by its test, or by the fact that a file is on a stick.

The text is ASCII, at most 120 characters a line, and identical for identical inputs (the payload files and the runbook). Its own SHA-256 is what the
operator names when approving it. The runbook therefore cannot name that digest, its size or its line count: they depend on the runbook's own bytes.

The same module renders the supplement for boot 17 (`--supplement`): XHCI0.BIN, unchanged, on usb_1_ss0 after the screen went off in boots 15 and 16
(docs/SP11-STICK-LOG.md section 16). The supplement approves one boot, amends rule 9 of the A1 record for that boot only and keeps the rest of the A1 record
and the runbook as they are: it pins the A1 record's files, runbook and text (`A1`), refuses any other XHCI0.BIN or runbook, and is not rendered if the A1
text no longer regenerates from those inputs byte for byte. The A1 output is the same with or without it.

Evidence: EXPERIMENTAL (host only). Hardware NOT RUN. Facts marked OBSERVED are from docs/SP11-STICK-LOG.md sections 12 and 13 (one unit, one boot each).
"""
from dataclasses import dataclass
import argparse
import hashlib
from pathlib import Path
import re
import sys
import textwrap

WIDTH = 120
OPERATOR_APPROVAL = '2026-11-17 02:10'
PLAN_NAME = 'a1-xhci-s1'
PLAN_NAME_SS0 = PLAN_NAME + '-ss0'      # the plan of XHCI0.BIN: the second boot of the A1 record, and boot 17 of the supplement
RUNBOOK_NAME = 'docs/SP11-XHCI-S1.md'
DEFAULT_RUNBOOK = Path(__file__).resolve().parent.parent / RUNBOOK_NAME
# The files that run besides the payload, as pinned for the plan `timers` (boot 11, 2026-11-16, OBSERVED): the operator compares them with the staged files.
SLBOUNCE_NAME, SLBOUNCE_SHA256 = 'SLBOUNCE-DEVHE-MARK.EFI', '4107c28c194e2f2fd3517b2081f1b2bd9224571a7b037163e9e27c3ba3c8fcf7'
RUNNER_NAME, RUNNER_SHA256 = 'Q1N1-PAYLOAD.EFI', 'c20579093ec7b1506faca3c0d33520561716510d7932ac1c9d340f432c88bf6e'

# The controllers: the file a boot names, the device tree name, the window, the SMMU stream (docs/SP11-STICK-LOG.md section 12, OBSERVED).
CONTROLLERS = {
    1: ('XHCI1.BIN', 'usb_1_ss1', 0x0a800000, 0x1460),
    0: ('XHCI0.BIN', 'usb_1_ss0', 0x0a600000, 0x1420),
}

# The registers the payload may store to: name, where (op = operational registers at base + CAPLENGTH 0x20, ir0 = interrupter 0 at base + RTSOFF + 0x20,
# db = doorbell 0 at base + DBOFF), the offset there, and the groups it is stored in. tools/test-sp11-xhci-s1-record.py compares this table with the
# header of the payload (payloads/xhci-regs.h of the q1n1 fork) and with the audit of the host harness.
REGISTERS = (
    ('USBCMD', 'op', 0x00, 'RESET (HCRST), RUN (RS), and to halt (RS clear)'),
    ('CRCR_LO', 'op', 0x18, 'PROGRAM'), ('CRCR_HI', 'op', 0x1c, 'PROGRAM'),
    ('DCBAAP_LO', 'op', 0x30, 'PROGRAM'), ('DCBAAP_HI', 'op', 0x34, 'PROGRAM'),
    ('CONFIG', 'op', 0x38, 'PROGRAM'),
    ('ERSTSZ', 'ir0', 0x08, 'PROGRAM'),
    ('ERSTBA_LO', 'ir0', 0x10, 'PROGRAM'), ('ERSTBA_HI', 'ir0', 0x14, 'PROGRAM'),
    ('ERDP_LO', 'ir0', 0x18, 'PROGRAM, NOOP'), ('ERDP_HI', 'ir0', 0x1c, 'PROGRAM, NOOP'),
    ('IMAN', 'ir0', 0x00, 'NOOP'),
    ('DB0', 'db', 0x00, 'NOOP'),
)
NOMINAL_STORES = 17        # HCRST, ten of PROGRAM, RS, the doorbell, ERDP twice and IMAN, the halt


@dataclass(frozen=True)
class PayloadFile:
    controller: int
    size: int
    sha256: str


@dataclass(frozen=True)
class RunbookFile:
    size: int
    sha256: str


class RecordError(Exception):
    pass


def sha256_text(text):
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def check_payloads(payloads):
    """RecordError unless the files are one or two payloads, XHCI1.BIN first, each with a plausible size and a SHA-256 in lower-case hex."""
    if not 1 <= len(payloads) <= 2:
        raise RecordError('one payload per boot: XHCI1.BIN, and optionally XHCI0.BIN for a second boot')
    if [p.controller for p in payloads] not in ([1], [1, 0]):
        raise RecordError('the first payload is XHCI1.BIN (usb_1_ss1) and the optional second XHCI0.BIN (usb_1_ss0)')
    for p in payloads:
        if not 1 <= p.size <= 65536 or not re.fullmatch(r'[0-9a-f]{64}', p.sha256):
            raise RecordError(f'{CONTROLLERS[p.controller][0]}: a size of 1 to 65536 bytes and a SHA-256 of 64 lower-case hex digits are required')


def check_runbook(runbook):
    """RecordError unless the runbook is a RunbookFile with a plausible size and a SHA-256 in lower-case hex."""
    if not isinstance(runbook, RunbookFile) or not 1 <= runbook.size <= 1 << 20 or not re.fullmatch(r'[0-9a-f]{64}', runbook.sha256):
        raise RecordError('the runbook: a size of 1 to 1048576 bytes and a SHA-256 of 64 lower-case hex digits are required')


def para(text, indent='   '):
    """One paragraph wrapped to the record's width with a hanging indent (deterministic: textwrap, no hyphen breaks)."""
    return textwrap.wrap(text, width=WIDTH - 2, initial_indent=indent, subsequent_indent=indent, break_long_words=False, break_on_hyphens=False)


def _files(payloads, runbook):
    lines = para(f'plan {PLAN_NAME}: one autorun step, `load` {SLBOUNCE_NAME}, then {RUNNER_NAME} --el2; one boot, one controller per boot.')
    lines += [f'   {SLBOUNCE_NAME:<24} sha256 {SLBOUNCE_SHA256}', f'   {RUNNER_NAME:<24} sha256 {RUNNER_SHA256}']
    lines += para('The two files above are as pinned for plan timers (boot 11, 2026-11-16, OBSERVED); the payload files are the ones below.')
    for p in payloads:
        name, device, base, stream = CONTROLLERS[p.controller]
        boot = 'boot 1' if p.controller == 1 else 'boot 2, only after the page of boot 1 was read'
        lines += [f'   {name:<24} sha256 {p.sha256}', f'      {p.size} bytes; {device}, window 0x{base:08x}, SMMU stream {stream:#x}; {boot}.']
    lines += para('Both payload files are one source (payloads/xhci*.c of the q1n1 fork) built twice; one build macro names the controller. PAYLOAD.MAN '
                  'lists exactly one payload, the one of the boot: the file name tells the operator which controller is under test.')
    lines += _runbook_lines(runbook, 'the runbook that sections 9 and 10 refer to (outcome tree, actions in order); read, not staged.')
    return lines


def _runbook_lines(runbook, role):
    """The lines that name the runbook by size and SHA-256 (`role` says which sections of the record read it) and say what a runbook of other bytes is."""
    lines = []
    lines += [f'   {RUNBOOK_NAME:<24} sha256 {runbook.sha256}',
              f'      {runbook.size} bytes; {role}']
    lines += para('A runbook of other bytes is another document: the operator compares this digest with the SHA-256 of the file at that path, as committed, '
                  'and runs nothing if they differ.')
    return lines


def render(payloads, runbook):
    """The text of the record for `payloads` (PayloadFile, XHCI1.BIN first) and the `runbook` (RunbookFile) it names by digest, as a str."""
    check_payloads(payloads)
    check_runbook(runbook)
    t = ['RECORD OF AUTHORISATION POINT A1: THE xHCI S1 NO-OP DMA TEST, PAYLOAD xhci (EXPERIMENTAL; hardware NOT RUN)', '']
    t += para(f'Approved in principle by the operator on {OPERATOR_APPROVAL} for a supervised test. This text, with the digests it names (the files '
              'of section 1), is what the operator approves by name in writing before the run. Nothing else authorises it: not a file, a test, '
              'a tool, or the presence of a file on a stick. A2 (the first sector write to the stick) is NOT granted. Nothing in this record asks for '
              'it, and the payload never touches a disk.', '')
    t += ['', '1. THE FILES (the SHA-256 of the staged bytes and of the runbook; the operator compares them with these lines)'] + _files(payloads, runbook) + ['']
    t += ['2. WHAT THE PAYLOAD WRITES to the controller of its file']
    t += para('Each group is written only after its NEXT line and eight seconds of the runner\'s counter, in every attempt: RESET is announced again in each. '
              'base = the window above; op = base + CAPLENGTH 0x20; ir0 = base + RTSOFF + 0x20; db = base + DBOFF. DBOFF and RTSOFF are read from the '
              'controller and printed in the pre-flight lines only when the reads and checks of the controller\'s own words pass (the unit\'s values are '
              'not recorded: 0x2000 and 0x1000, which the simulator and QEMU use, are INFERRED). The payload refuses, with nothing written, a DBOFF or '
              'RTSOFF with reserved low bits, a doorbell or interrupter outside the window or inside the operational registers, and any doorbell or '
              'interrupter that would put one of the registers below at or above 0xc100 (the DWC3 global registers). A refusal there (OFFSET, and '
              'likewise CAPLENGTH, HCIVERSION, DWC3_ID, NOT_XHCI or a failed read of those words) prints the VERDICT line alone, with no DBOFF or '
              'RTSOFF line: on OFFSET its VALUE is the DBOFF or RTSOFF that has reserved low bits; or DBOFF, whichever of the two is at fault, when the '
              'doorbell or interrupter is outside the window or inside the operational registers; or the offset from the window base of the first '
              'register below that would lie at or above 0xc100. Thirteen registers:')
    t += [f'   {name:<10} {where:<3} + {offset:#04x}   groups: {groups}' for name, where, offset, groups in REGISTERS]
    t += para('Values: USBCMD differs from the value last read only in RS (bit 0) and HCRST (bit 1), never both. CONFIG = HCSPARAMS1.MaxSlots. DCBAAP, CRCR '
              '(with RCS = 1), ERSTBA and the first ERDP are addresses inside the arena (section 3). ERSTSZ = 1. The last ERDP is the dequeue pointer with '
              'EHB. IMAN = 1 only (IP, write 1 to clear). Doorbell 0 is written with 0 only. A 64-bit register is two 32-bit stores, the low word first. '
              'CONFIG, DCBAAP, ERSTSZ, ERSTBA and ERDP are read back after the PROGRAM group; CRCR, the doorbell and IMAN are not.')
    t += para(f'A nominal run makes {NOMINAL_STORES} stores: HCRST, ten of PROGRAM, RS, the doorbell, ERDP twice and IMAN, then the halt. The order is xHCI 1.2 '
              'section 4.2 (INFERRED from memory: the sections are cited, not re-read; QEMU\'s independent xHC accepts it): halt, HCRST, CONFIG, DCBAAP, '
              'CRCR, ERSTSZ, ERDP, ERSTBA, RS, one No Op Command TRB (section 4.6.2), the doorbell, the Command Completion Event (sections 4.9.4 and '
              '6.4.2.2), ERDP with EHB, halt. The payload returns 0 to the runner.')
    t += ['', '3. WHAT THE PAYLOAD WRITES to RAM']
    t += para('One arena at a time, of 1 + HCSPARAMS2 scratchpad-count pages: one page of structures and one page per scratchpad buffer. '
              'Written: zeros, the scratchpad array in DCBAA[0] and its page addresses, a command ring of 16 TRBs closed by a Link TRB, a segment table of '
              'one entry, an event ring of 64 TRBs (zeroed, written by the xHC), the one No Op TRB, last its cycle bit. Cleaned to memory (DC CVAC, DSB '
              'SY) after the CPU writes; the event TRB is invalidated (DC CIVAC) before every read. Zeroed again once the controller is confirmed halted. '
              'The arenas, in this order:')
    t += para('1  usb_1_ss0 only: the firmware\'s own xHCI memory, 0xCF412000, four pages, if the memory map calls it boot-services or free memory '
              '(the S0 addresses, OBSERVED).', '   ')
    t += para('2  inside the runner\'s payload stack: its second page onward, at most 9 pages, the guard page and the last 24 KiB of the stack left alone.')
    t += para('3  the top of the highest block of free memory below 4 GiB, where the runner puts its big files.')
    t += para('4  the first pages of the 30 GiB bank at 0x880000000, only if HCCPARAMS1.AC64 is 1 and the memory map calls them free memory.')
    t += para('Only arena 2 is RAM that the runner owns and declared to the payload. Arenas 1, 3 and 4 are RAM that the memory map calls free, and they are the '
              'fallbacks after an attempt whose DMA may have gone astray (hazard (c)).')
    t += para('At the RUN or NOOP stage a silence (no event), a Host System Error or Host Controller Error (HSE or HCE), or a controller that does not '
              'start (HCH still set after RS), with '
              'or without a change of the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit, halts the controller, and the next '
              'arena is tried from the reset with every group '
              'announced again, up to four attempts: that is the SC2 experiment. The first attempt that completes its No Op ends the run. A bad '
              'completion ends it too, after the halt, with no further attempt and no further DMA in this boot: an event with a wrong completion code, '
              'pointer or cycle bit, or a REDIRECT (the controller says it posted an event, USBSTS.EINT, and the ring holds none; recognised only when '
              'EINT was clear before the doorbell, otherwise it reads as a silence: INFERRED, the only signal the payload has). A controller that will '
              'not reset or hold its registers, a refused store, a failed read or a counter that stops ends the run as well. So does any failure at '
              'the RESET or PROGRAM stage, an HSE or HCE that the reset itself ends with included: it is the controller\'s answer, not the arena\'s.')
    t += para('No byte of RAM outside the arena of the attempt is written, and no cache operation is made outside it. At the end the controller is left '
              'halted but still programmed: its DCBAAP, CRCR, ERSTBA and ERDP point into the arena, which is zero again (arena 2 is the runner\'s stack); '
              'no store beyond the thirteen undoes it.')
    t += ['', '4. WHAT IT READS (no write)']
    t += para('GSNPSID (0xc120), the capability registers (DBOFF and RTSOFF included), USBCMD, USBSTS, PAGESIZE, CRCR, DCBAAP, CONFIG and the PORTSC of ports 1 '
              'and 2 of both controllers (the plug watch); the SMMU ID0, ID1, the stream match and S2CR registers for the controller\'s stream; the global '
              'fault registers and context bank 3\'s FSR, FAR and FSYNR0, and the DWC3 GSTS, GBUSERRADDR0 and GBUSERRADDR1, before the first attempt and '
              'after each (the line of an attempt says whether the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit changed since '
              'the end of the one before); and the UEFI memory map the runner holds. Every read '
              'goes through the runner\'s guarded load; the first abort of a window ends all reads of that window.')
    t += para('What follows is INFERRED from the architecture (ARM IHI 0062), not re-read. The SMMU records only the first fault of a context bank until '
              'its FSR is cleared, which A1 never does: SMMU=SAME does not prove that an attempt did not fault, and an SMMU1 FAR or FSYNR0 may belong '
              'to an earlier fault. The baseline already reads FSR=0X400 (OBSERVED, S0b; its meaning is UNKNOWN).')
    t += ['', '5. WHAT IT DOES NOT WRITE (every one of these is read only or untouched)']
    t += para('Any DWC3 core or glue register; any SMMU register, not even a fault status clear; USBSTS, even its write-1-to-clear bits; PORTSC of any port '
              '(no port reset, no power change, no link control); anything of the other controller; any clock, GDSC or GCC register; any PHY, repeater, '
              'retimer, I2C, SPMI, GPIO or UCSI register; any SMC or HVC; the stick, any disk or NVMe; any UEFI variable. In its binary only two stores use a '
              'pointer that is not rooted in the payload\'s own stack, context or line buffers: the two sinks, one for the thirteen registers and one for '
              'the arena, each called from one gate that checks the register, the group and the arena; every other store has its base in those buffers '
              '(the test follows each pointer in the compiled code to its root: it proves the base of a store and not its offset, and does not pin the '
              'count of stores, which the compiler decides). The offsets are checked on the paths the host tests run: the harness\'s audit of every register store '
              'and every RAM store of its model, and AddressSanitizer and UBSan on every run. Every cache instruction is behind an arena gate too '
              '(docs/SP11-PAYLOAD-XHCI.md in the q1n1 fork, and its tests).')
    t += ['', '6. THE ORDER AND THE TIME on the page (a hang or a reset leaves the last line as the answer)']
    t += para('Reads and the refusal checks (nothing is written on any refusal); a 30 s read-only plug watch with a countdown line (the operator connects the '
              'Mac\'s USB-C cable to the free port at WATCH 30 S); then per attempt NEXT ARENA, NEXT RESET, NEXT PROGRAM, NEXT RUN and NEXT NOOP, each '
              'followed by eight seconds on the counter before its first store; a result line per attempt; VERDICT; END. Bounded waits: halt 50 ms, HCRST '
              '1 s, CNR 1 s and run 50 ms, polled on the counter every 1 ms for the first 100 ms of a wait and every 10 ms after; the Command Completion '
              'Event 200 ms, polled every 1 ms throughout. A slow reset must not be a verdict: Linux\'s driver waits longer than the 100 ms the first '
              'design gave it (INFERRED, from memory), so HCRST and CNR get a second each.')
    t += para('Duration. A nominal run takes about 71 s: the 30 s watch, five waits of 8 s and about a second of bounded polls. A run whose attempts fail takes '
              'longer, because every attempt waits 5 x 8 s: XHCI1.BIN (three arenas) 30 s + 15 x 8 s = 150 s plus at most 7.9 s of bounded waits (3 x '
              '2.3 s, and 1 s for a CNR wait in the pre-flight), about 158 s; XHCI0.BIN (four arenas) 30 s + 20 x 8 s = 190 s plus at most 10.2 s, about '
              '200 s. It is "about" because a wait polled every 10 ms may end up to 10 ms late (about 0.1 s over a run) and the time the runner takes '
              'to draw the lines is UNKNOWN. The two read-only control boots of the restart '
              'probe ran to UPTIME 224 s and 268 s without a restart; a restart was reported after two of the three cpuon boots, cause UNKNOWN '
              '(docs/SP11-STICK-LOG.md section 14.1).')
    t += ['', '7. THE BUDGET (the runner\'s limits per call)']
    t += para('40 lines and 4096 loads (the payload stops at 3800). Every wait is bounded by the counter, and a counter that stands still ends the plug watch '
              'and the run at its first wait. The page keeps two lines for the verdict and the END line, and a write group is opened only by an announcement '
              'that was printed. Every eligible attempt keeps six lines (its five announcements and its result line): the watch\'s optional lines (CHG and '
              'the countdown) and the SMMU reports of earlier attempts (the SMMU1 R<k> lines) give way to them, and a later attempt starts only if its '
              'six lines and the closing two still fit. Only those optional lines are lost this way, never the first line of a result, the VERDICT or the '
              'END line; a report that is not printed is a cut line, and END says LINES_CUT. A result of 100 characters or more takes a second line '
              '(R<k> STS=..), which no budget reserves: an attempt that starts with 32 lines on the page, the most with which one starts, loses it, END '
              'says LINES_CUT and the R<k> line ends before its STS= field (host model, quiet watch: a bad completion in the last attempt of XHCI0.BIN, '
              'after three failed attempts with their reports). That is a visibly cut line, and nothing accounts for it (section 9).')
    t += para('In the host model\'s budget matrix (EXPERIMENTAL), whose results are one line each and whose sGFSR is 0 before and after every attempt, so '
              'that a report is one line (S0b read the sGFSR 0 before any attempt: OBSERVED): '
              'XHCI1.BIN (three arenas) prints every report, whatever the number of port changes in the watch (it prints at most four CHG lines), and '
              'never says LINES_CUT. XHCI0.BIN (four '
              'arenas), when all four arenas fail with a change of the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit, leaves '
              '3, 2, 1 or 0 of the four SMMU1 reports on the page '
              'after 0, 1, 2 or 3 or more CHG lines, and END says LINES_CUT every time. A success after earlier attempts that failed with a change of '
              'the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit says LINES_CUT too, on XHCI0.BIN: after a success at arena 4 '
              'from one CHG line on, at arena 3 from two, at arena 2 from '
              'three, never at arena 1. So on XHCI0.BIN a VERDICT NOOP_OK may be followed by an END line with LINES_CUT: what was dropped is SMMU1 '
              'reports of the failed attempts, which their R<k> lines (SMMU=CHANGED) still announce, and the result is whole. The model overwrites FAR '
              'at every fault, so it assumes that every failed attempt changes the SMMU\'s fault registers and the DWC3\'s bus-error address and valid '
              'bit; an SMMU that keeps the first fault (section 4) may '
              'read SAME for later attempts, and then fewer SMMU1 reports compete for the lines (INFERRED). With an sGFSR that is not 0 already at the '
              'pre-flight (not observed) a report is two lines and more give way: XHCI1.BIN says LINES_CUT only when all three arenas fail and the watch '
              'printed three or more CHG lines; XHCI0.BIN after a success at arena 3 or 4 always, and at arena 2 from one CHG line on. Section 9 reads '
              'LINES_CUT after NOOP_OK.')
    t += ['', '8. THE HAZARDS, said plainly']
    t += para('(a) An unclocked block hangs the bus and costs a power cycle; the clocks and GDSC of both controllers read ON at EL2 (OBSERVED, S0), so none is '
              'expected, but a hang is not recoverable by the payload. (b) An XPU or SMMU violation may reset the SoC. (c) What stage 2 of the SMMU maps for '
              'the controller\'s stream is UNKNOWN (context bank 3 is nested, stage 1 off: S0b, OBSERVED): the xHC\'s DMA may fault, which is the answer, or '
              'reach a page that is not the arena, which is the worst case: it can read or write RAM that is not ours. Only arena 2 is RAM the runner owns '
              'and declared; arenas 1, 3 and 4 are RAM the memory map calls free, and they are the fallbacks after an attempt whose DMA may have gone '
              'astray. A bad completion or a REDIRECT stops the run, so that no further arena is given to a controller that answered wrongly; a silence, '
              'a Host System Error or Host Controller Error, or a controller that does not start, at the RUN or NOOP stage, does not, because the '
              'next arena is the experiment (a failure '
              'at the RESET or PROGRAM stage stops the run too). Nothing else is written by the payload itself.')
    t += para('(d) The controller is left halted but still programmed (section 3), not reset again: A1 covers thirteen registers. A USB stick behind that '
              'controller loses the firmware\'s state of it, which does not matter after ExitBootServices: the runner holds the payload in memory. S0 '
              'observed the stick\'s controller as usb_1_ss0, the one that held the firmware\'s rings behind the hub, with the stick in the port it was in '
              'that boot (OBSERVED: docs/SP11-STICK-LOG.md section 3.2 and section 12). S0 was the hub topology. S0b, the direct topology that A1 '
              'uses, read both controllers idle with nothing connected while the stick was in and the firmware had just booted from it (OBSERVED, one '
              'boot: docs/SP11-STICK-LOG.md section 13). That reading does not show where the stick was (the link was down at EL2, cause UNKNOWN); '
              'the device path of boot 3, a direct boot, carried S0\'s PCI node Pci(0x80,0x0), which makes usb_1_ss0 likely (INFERRED: '
              'docs/SP11-STICK-LOG.md section 13, last paragraph), so, if the stick is in the same port as in boot 3, XHCI0.BIN is the file that resets '
              'its controller. Which connector it is on in this boot is not recorded (UNKNOWN: '
              'the operator says on the video which port holds the stick, and the plug watch says which controller serves the free port), so whether '
              'XHCI0.BIN or XHCI1.BIN touches the stick\'s controller depends on where the stick is. (e) USBSTS may keep EINT set after the run: '
              'harmless, and not written.')
    t += para('(f) A cable between two USB-C machines negotiates power and data roles and VBUS (INFERRED: USB Power Delivery, not seen in this unit\'s logs). '
              'HCRST ends the traffic on the controller\'s ports (the stick, if it is on that controller), and with HCCPARAMS1.PPC = 1 it returns '
              'PORTSC.PP to 0, which removes the port\'s power until something sets it again (INFERRED from memory of the xHCI specification). The '
              'unit\'s PPC is not recorded (UNKNOWN). The Mac\'s cable, plugged during the watch, may be negotiating when the reset comes.')
    t += para('(g) The payload\'s stores are not guarded the way its reads are: a read goes through the runner\'s guarded load, which turns a fault into an '
              'error code, but a store is a plain str. A permission fault or an SError on a store is an exception at EL2, a fault page or a hang and not a '
              'refusal, and the reads of S0 prove readability only. (h) A restart alone is not an xHC result: a restart was reported after two of the '
              'three cpuon boots and after none of the read-only control boots, cause UNKNOWN (docs/SP11-STICK-LOG.md section 14.1).')
    t += ['', '9. THE STOP RULES']
    t += para('The operator reads the page before anything else is run, and the sequence stops, and the unit is power-cycled, on any of: a page that ends at '
              'a NEXT line, a fault page, a board restart, END HALTED=NO or HALTED=UNKNOWN, STORE_REFUSED on the END line, a WHY= of READ, CLOCK, BUDGET '
              'or LINES, LINES_CUT on the END line after any VERDICT except NOOP_OK, a VERDICT NOOP_BAD_COMPLETION, any REFUSED that the runbook of '
              'section 1 does not list, any line that its outcome tree does not list. LINES_CUT on the END line after VERDICT NOOP_OK does not stop '
              'the sequence when it is accounted for (section 7: on XHCI0.BIN, an '
              'R<k> line that says SMMU=CHANGED has no SMMU1 R<k> line after it, and the result is whole): it is read, recorded, and the page is kept. '
              'A LINES_CUT that nothing accounts for, because every SMMU=CHANGED attempt has its report or a line is visibly cut or missing (an R<k> '
              'line that ends before its STS= field, section 7), stops '
              'the sequence. Reading it that way is the operator\'s decision, taken by approving this record; XHCI0.BIN, the only file on which the host '
              'model has it follow a success, is the optional second boot. XHCI0.BIN is run only after the page of XHCI1.BIN was read and the operator '
              'named the second file. There is no retry of a payload in the same boot.')
    t += ['', '10. THE OPERATOR\'S ACTIONS (docs/SP11-XHCI-S1.md has them in order)']
    t += para('AC power or at least 50% battery; one hardware client at a time; start the Mac-side logger (with --allow-empty-usb if the Mac has no other '
              'USB device); start filming at the Shell countdown and keep filming until the first UPTIME heartbeat after the END line (one video, with '
              'the VERDICT and END lines in it); at the runner\'s lines PAYLOAD LOADED ... SHA=<16 hex digits>, PAYLOAD VERIFY=OK and PAYLOAD CALL 0 '
              '<the same 16 digits>, compare the digits with the first 16 of the digest of that boot\'s payload in section 1: a mismatch, or no '
              'VERIFY=OK, means '
              'power off, and nothing of A1 has been written (the marker SP11\\DONE1.TXT has; delete it to run again); plug the Mac\'s cable only at '
              'the on-screen cue; the eight seconds after each NEXT line are '
              'the cancel window, and nothing of that group is written yet: in it the operator powers off only if the page itself is wrong, as the '
              'runbook says; apart from that, touch nothing until END; power-cycle afterwards.')
    t += ['', '11. NOT AUTHORISED BY THIS RECORD']
    t += para('A2; any write beyond sections 2 and 3; the port reset, the enumeration of the Mac, the NCM driver, the SMMU programming and every register '
              'class docs/SP11-XHCI-S1.md section 7 lists: each needs its own named authorisation.')
    return _checked(t)


def _checked(t):
    """The text of the lines `t`, or RecordError for a line over the width or not ASCII."""
    for number, line in enumerate(t, 1):
        if len(line) > WIDTH or not line.isascii():
            raise RecordError(f'line {number} is {len(line)} characters or not ASCII: {line[:60]!r}')
    return '\n'.join(t) + '\n'


def digest(payloads, runbook):
    """(text, byte size, SHA-256 of the text) of the record."""
    text = render(payloads, runbook)
    return text, len(text.encode('ascii')), sha256_text(text)


# ---- The supplement for boot 17: XHCI0.BIN, unchanged, after the screen went off in boots 15 and 16 -------------------------------------------------

@dataclass(frozen=True)
class A1Facts:
    """The A1 record as the operator approved it, each part by size and SHA-256: the two payload files, the runbook it pins and the text itself. The
    supplement keeps XHCI0.BIN and the runbook unchanged, so it refuses any other; it amends the A1 text, so it is not rendered unless that text still
    regenerates from these inputs byte for byte."""
    xhci1: PayloadFile
    xhci0: PayloadFile
    runbook: RunbookFile
    record_bytes: int
    record_sha256: str


# The A1 record as approved on 2026-11-17 (boots 15 and 16 ran under it with XHCI1.BIN): its files, its runbook, and its own size and SHA-256.
A1 = A1Facts(PayloadFile(1, 19798, '989324b52e66c2128c13dcb84703c29b4b718b3326075935542b9ea541a16777'),
             PayloadFile(0, 19870, '63b11f901acbb4300dcd49a809841ccb5c1f626c777166547dc9967693f81e7f'),
             RunbookFile(51154, '88d96c587f493b3a83683cffa2c4e24eb8bb0468a649a6a890feb5a373ab319d'),
             21751, '6e21c40a0a0938860cf48d30be4a1a3e5ac398c94b511d322fe8791538e8f1ce')
# docs/SP11-STICK-LOG.md is not pinned by digest (it grows with every boot): the supplement cites it as of this commit of the Snapintosh fork, the head that holds
# the correction of the reading of FSR 0x400.
STICK_LOG_COMMIT = '42017e2'
DIGEST_COMMAND = 'python3 tools/sp11_xhci_s1_record.py --supplement --xhci0 XHCI0.BIN --digest'


def item(label, text):
    """One list item wrapped to the record's width: `label` at the left, the continuation lines indented under the text."""
    return textwrap.wrap(text, width=WIDTH - 2, initial_indent=f'   {label} ', subsequent_indent=' ' * (4 + len(label)), break_long_words=False,
                         break_on_hyphens=False)


# The operator's extra actions of boot 17, short enough for one screen; section 8 of the supplement carries them.
E0 = (["   E0, the extra actions of boot 17 (the operator's own; the payload writes nothing more for them):",
       '   Before the boot:'] +
      item('(e)', 'Put the stick straight into the unit (no hub) and see which physical port holds it.') +
      item('(a)', 'After the stick has left the Mac (otherwise its removal shows as USB_REMOVED), start the Mac-side logger: python3 tools/sp11_mac_usb_watch.py '
                  '(add --allow-empty-usb if the Mac has no other USB device), as for A1. Keep the Mac awake and unlocked, and the logger running, '
                  'through (c).') +
      item('(b)', 'Film as before, at 60 frames per second if the phone allows: the whole panel in frame, from the Shell countdown to the first UPTIME '
                  'heartbeat after the END line. Say on the video which physical port holds the stick.') +
      ['   After a black-out:'] +
      item('(c)', 'applies only to a black panel before any VERDICT or END line. A logo or boot screen means that the unit restarted: skip (c), say the time, '
                  'the last line of the page and the last UPTIME shown. A lit page that does not change for 30 s after a NEXT line: skip (c), say the last '
                  'line. Otherwise say '
                  'the Mac\'s clock on the video and touch no cable for 4 minutes (a payload that is alive in the dark runs on for up to about 200 s, and a '
                  'running xHC writes event TRBs that a moved cable or stick would change); force the unit off at once, as below, only if it feels hot, '
                  'and then skip the rest of (c). After the 4 minutes, unplug the stick (hazard (m) says why that is safe) and plug the Mac\'s cable into '
                  'THAT port (move it from the free port). Wait '
                  '60 s and read the logger and System Information for any NEW USB device: vendor 05C6 (Qualcomm), for example product 9008 (its '
                  'download mode; a Mac may list it as QHSUSB__BULK or QUSB__BULK), or any device you do not know. macOS enumerates any device by '
                  'itself; add nothing to that: no driver, no flashing or Terminal tool, no Disk Utility; do not open, mount, initialise, erase or copy '
                  'from any disk that appears (eject it unopened) and accept no app, driver, profile or Photos prompt. If macOS asks "Allow accessory to '
                  'connect", choose Allow once (INFERRED: it lets macOS read the descriptors only, while Don\'t Allow would blind the logger) and say it on '
                  'the video. Report what the logger and System Information show (sanitise both: they list the Mac\'s own accessories). Then force the '
                  'unit off: hold Volume-Up and Power together for about 15 s until the screen stays off (the Surface logo may flash: keep holding), then '
                  'release both. Holding Power alone for about 20 s restarts the Surface to its logo instead (Microsoft\'s "Force a shutdown and restart '
                  'your Surface"); that is also acceptable, Windows may then start: shut it down from Windows.') +
      ['   Optional:'] +
      item('(d)', 'Only if the charger is not on a USB-C port: an inline power meter on the charger; note the draw while the screen is dark, and after the '
                  'power-off if you can.'))


def check_supplement(payload, runbook, a1):
    """RecordError unless `payload` is the A1 record's XHCI0.BIN unchanged, `runbook` is the runbook that record pins unchanged, and the A1 text still
    regenerates from `a1` byte for byte. Each refusal has its own message: the checks do not stand in for one another."""
    if not isinstance(payload, PayloadFile):
        raise RecordError('boot 17: the payload is XHCI0.BIN, the file of the A1 record, unchanged')
    if payload.controller != 0 or (payload.size, payload.sha256) == (a1.xhci1.size, a1.xhci1.sha256):
        raise RecordError('boot 17 runs XHCI0.BIN (usb_1_ss0): XHCI1.BIN is the file of boots 15 and 16 and is not the payload of this supplement')
    if not 1 <= payload.size <= 65536 or not re.fullmatch(r'[0-9a-f]{64}', payload.sha256):
        raise RecordError('XHCI0.BIN: a size of 1 to 65536 bytes and a SHA-256 of 64 lower-case hex digits are required')
    if payload != a1.xhci0:
        raise RecordError(f'XHCI0.BIN must be the file of the A1 record, unchanged ({a1.xhci0.size} bytes, sha256 {a1.xhci0.sha256}): another file needs '
                          'a record of its own')
    check_runbook(runbook)
    if runbook != a1.runbook:
        raise RecordError(f'the runbook must be the one the A1 record pins, unchanged ({a1.runbook.size} bytes, sha256 {a1.runbook.sha256}): '
                          'another runbook needs a record of its own')
    _, size, sha = digest([a1.xhci1, a1.xhci0], a1.runbook)
    if (size, sha) != (a1.record_bytes, a1.record_sha256):
        raise RecordError(f'the A1 record no longer regenerates byte for byte from its pinned inputs ({size} bytes, sha256 {sha}, not {a1.record_bytes} '
                          f'bytes, sha256 {a1.record_sha256}): the supplement amends the text the operator approved, so it is not rendered')


def _supplement_files(payload, runbook, a1):
    name, device, base, stream = CONTROLLERS[payload.controller]
    other, other_device, other_base, other_stream = CONTROLLERS[a1.xhci1.controller]
    lines = para(f'plan {PLAN_NAME_SS0}: one autorun step, `load` {SLBOUNCE_NAME}, then {RUNNER_NAME} --el2; one boot, one controller.')
    lines += [f'   {SLBOUNCE_NAME:<24} sha256 {SLBOUNCE_SHA256}', f'   {RUNNER_NAME:<24} sha256 {RUNNER_SHA256}']
    lines += para('The two files above are as pinned for plan timers (boot 11, 2026-11-16, OBSERVED) and as the A1 record names them; the payload files '
                  'are the ones below.')
    lines += [f'   {name:<24} sha256 {payload.sha256}', f'      {payload.size} bytes; {device}, window 0x{base:08x}, SMMU stream {stream:#x}; boot 17.']
    lines += [f'   {other:<24} sha256 {a1.xhci1.sha256}',
              f'      {a1.xhci1.size} bytes; {other_device}, window 0x{other_base:08x}, SMMU stream {other_stream:#x}; boots 15 and 16; not staged, does not run.']
    lines += para(f'{name} is the file the A1 record names, byte for byte: no byte of the payload is new for boot 17. PAYLOAD.MAN lists exactly one '
                  'payload, the one of the boot: the file name tells the operator which controller is under test.')
    lines += _runbook_lines(runbook, 'the runbook of sections 2, 6 and 8, UNCHANGED since the A1 record; read, not staged.')
    lines += [f'   {"the A1 record":<24} sha256 {a1.record_sha256}',
              f'      {a1.record_bytes} bytes; the text that boots 15 and 16 ran under, which this record amends and keeps (section 2).']
    lines += para(f'docs/SP11-STICK-LOG.md is cited as of commit {STICK_LOG_COMMIT} of the Snapintosh fork (its sections 10, 12, 13, 14.2 and 16), not by digest.')
    return lines


def render_supplement(payload, runbook, a1=None):
    """The text of the supplement record for boot 17, as a str. `payload` (PayloadFile) must be the A1 record's XHCI0.BIN unchanged and `runbook`
    (RunbookFile) the runbook that record pins, unchanged; `a1` (A1Facts, default A1) is what the A1 record named. RecordError otherwise, and when the
    A1 text no longer regenerates from `a1` byte for byte."""
    a1 = a1 or A1
    check_supplement(payload, runbook, a1)
    name, device, base, stream = CONTROLLERS[payload.controller]
    digits = payload.sha256[:16].upper()
    t = ['RECORD OF AUTHORISATION POINT A1, SUPPLEMENT FOR BOOT 17: XHCI0.BIN ON usb_1_ss0 (EXPERIMENTAL; hardware NOT RUN)', '']
    t += para(f'This is a supplement to the record of authorisation point A1 (the A1 record). It approves ONE more boot, boot 17, with {name} only, and it '
              f'amends one rule of the A1 record, for that boot only. The operator approved point A1 in principle on {OPERATOR_APPROVAL} and approved the '
              'A1 record by name; boots 15 and 16 ran under it with XHCI1.BIN (boot 16 repeated boot 15, which was not filmed). This text, with the '
              'digests it names (the files of section 1), is what the operator approves, before boot 17, by quoting in writing the SHA-256 of this '
              f'text ({DIGEST_COMMAND} prints it) and the 16 digits {digits}. '
              'Nothing else authorises it: not a file, a test, a tool, a plan, the approval of the A1 record, or the presence of a file on a stick. '
              'A2 (the first sector write to the stick) is NOT granted. Nothing in this record asks for it, and the payload never touches a disk.', '')
    t += ['', '1. THE FILES (SHA-256 of the staged bytes, the runbook and the A1 record; the operator compares them with these lines)']
    t += _supplement_files(payload, runbook, a1) + ['']
    t += ['2. WHAT THIS SUPPLEMENT APPROVES, WHAT IT AMENDS AND WHAT IT KEEPS']
    t += para(f'It approves ONE boot, boot 17: the plan {PLAN_NAME_SS0} with {name} ({device}, SMMU stream {stream:#x}), started once, with the Mac\'s USB-C '
              'cable connected to the free port at the on-screen cue, as in the A1 record, on AC power or at least 50% battery, with one hardware client '
              f'at a time. Boot 17 is the boot in which the runner prints PAYLOAD CALL 0 {digits} (section 8). It approves nothing else: not a '
              'second boot, not a retry, not XHCI1.BIN again, not another file, arena order or controller, not any other write.')
    t += para('It amends rule 9 of the A1 record (its section 9, the stop rules), for boot 17 only. That rule says that the sequence stops, and the unit is '
              'power-cycled, on a page that ends at a NEXT line, and that XHCI0.BIN is run only after the page of XHCI1.BIN was read and the operator '
              'named the second file. The page of boot 16 ended at a NEXT line (section 3), so under that rule the sequence stopped there. '
              'This record waives that stop, for boot 17 only: '
              'the page of XHCI1.BIN was read (section 16 of docs/SP11-STICK-LOG.md transcribes it from the video; boot 15\'s page was not read, and '
              'this record takes boot 16\'s page as the page of XHCI1.BIN), and by approving this record the operator names XHCI0.BIN as the second '
              'file. The waiver covers that page and nothing else: every stop rule applies to the page of boot 17 (section 7). '
              'The runbook says the same in 4.3 and 4.4, and says in O10 "No further xHC write until it is understood": it stays as it is, and for '
              'boot 17 only this record is the later word on those clauses.')
    t += para('Why this waiver, and why XHCI0.BIN (INFERRED): the firmware initialised usb_1_ss0 and used it for the stick. The firmware\'s PCI I/O view lists four '
              'controller instances and 0x0A800000, the window of usb_1_ss1, is not among them (OBSERVED: section 14.2 of docs/SP11-STICK-LOG.md; '
              'section 12 reads usb_1_ss1 as never started by the firmware). So XHCI0.BIN asks the question of boot 16 of a controller that firmware '
              'brought up, with the same payload bytes as the A1 record and so no new code. '
              'Boot 17 changes TWO things from boot 16: the controller, and the first arena (the firmware\'s own memory, not the runner\'s stack). '
              'A stop separates a cause that belongs to usb_1_ss1, which UEFI never brought up (PHY, glue, a vote or a power state; the clocks and '
              'GDSC of both controllers read ON in S0), from one that does not (the SMMU\'s context bank, an XPU, the EL2 environment, or any start '
              'of an xHC at EL2); a success does not by itself separate them (section 6).')
    t += para('Everything else of the A1 record stands as written, for XHCI0.BIN: its sections 2 to 8, 10 and 11, with the additions of sections 5 and 8 '
              'below to its sections 8 and 10; the runbook docs/SP11-XHCI-S1.md by the size and SHA-256 of section 1, UNCHANGED, whose outcome tree '
              'O1 to O15 applies with U0 and U1 swapped for XHCI0.BIN, except that the Next column of its rows (O1, O9, O13) '
              'is advice for a later request and authorises nothing: for boot 17 section 7 is the only word on what follows; and every ban: no write '
              'to a DWC3 core or glue register, an SMMU register, a clock, GDSC or GCC register, a PHY, repeater, retimer, I2C, SPMI, GPIO or UCSI '
              'register, any SMC or HVC, a UEFI variable, the stick, a disk or the NVMe. A2 is not granted.')
    t += ['', '3. WHAT BOOTS 15 AND 16 SHOWED (section 16 of docs/SP11-STICK-LOG.md has the page and the labels)']
    t += para('OBSERVED, boot 16 (filmed in two videos; the page is cumulative and every line was read on several frames): the plan a1-xhci-s1 ran '
              'XHCI1.BIN on usb_1_ss1; the digits on the page were those of the authorised file and the runner said PAYLOAD VERIFY=OK. The pre-flight '
              'read the controller (hazards (j) and (n)) and found it halted (STS 0x1) with CMD, CRCR, DCBAAP and CONFIG all 0. After the Mac\'s cable '
              'was plugged the PORTSC of port 2 of usb_1_ss1 changed twice, a second apart, and no other port changed (the decode, Polling and then '
              'Inactive, is INFERRED; so is that the free port is wired to usb_1_ss1, and a mode that uses another port is not excluded: section 16). '
              'The page advanced through NEXT ARENA 2, NEXT RESET and NEXT PROGRAM to NEXT RUN AFTER 8 S, eight seconds apart. The screen went off '
              '8.15 s (7.95 to 8.35 s) after that line was drawn and stayed off to the end of the video (2.6 s). No R1 line, VERDICT, END or fault page '
              'followed.')
    t += para('INFERRED: that the screen followed the RS store, the first store of the RUN group (the twelfth register store of the run), because 8 s is '
              'the payload\'s own wait before that store; and that the ARENA, RESET and PROGRAM groups completed (11 register stores), from the shape '
              'of the page.')
    t += para('OBSERVED, boot 15 (by the operator, from memory only, not filmed; his first answer was that it ran to END, a later one that both boots '
              'ended with the screen off and that he is unsure of the details): the screen went off too. Its page, its VERDICT, its arena and the '
              'moment the screen went off are UNKNOWN.')
    t += para('OBSERVED, afterwards (the operator\'s report, from memory): the unit stayed dark and he never turned it off; it was still a little warm '
              'about 30 minutes later (an impression, not a measurement); when and how it was powered off are not recorded (docs/SP11-STICK-LOG.md '
              'section 16). No restart was seen. The Mac-side logger (989 ticks) saw no USB device or network interface added or removed, with the '
              'Mac\'s cable in the free port.')
    t += para('UNKNOWN: the cause; whether the SoC stayed alive (nothing probes it: the panel was the only channel); whether a hardware watchdog was armed '
              '(nothing restarted the unit in 30 minutes or more); whether the RS store completed and the xHC started (HCH after the store was never '
              'read); whether it fetched DCBAA[0] or the scratchpad array (no DMA was observed); the HSE and HCE bits, the SMMU fault registers and the '
              'DWC3\'s bus-error address after the RS store (never read); boot 15\'s page.')
    t += para('SC3 of docs/SP11-STICK-LOG.md (five boots at S1 without a No-Op completing, or two boots with an unexplained Host System Error): boot 16 is '
              'one such boot; boot 15 is a second if it ended the same way (UNKNOWN: its page was not read), so the count is 1 or 2 of 5. No HSE was '
              'read in either boot. A boot 17 in which no No-Op completes (any verdict but NOOP_OK, or none) makes it 3 of 5 (2 if boot 15 does not '
              'count); an HSE in boot 17 would also be the first of SC3\'s two unexplained HSEs.')
    t += ['', '4. WHAT IS SPECIFIC TO XHCI0.BIN (sections 2 to 7 of the A1 record stand; only what differs from boot 16 is said here)']
    t += para(f'The payload is the A1 record\'s, started on {device}: window 0x{base:08x}, SMMU stream {stream:#x}. It writes the same thirteen registers of '
              'that controller and the words of one RAM arena at a time, as sections 2 and 3 of the A1 record say, and nothing else. Its reads are those '
              'of section 4 of the A1 record.')
    t += para('The arenas, in this order: 1, the firmware\'s own xHCI memory, 0xCF412000, four pages, if the memory map calls it boot-services or free memory '
              '(the S0 addresses: hub topology, OBSERVED; the direct topology\'s addresses were never read); then 2, 3 and 4 as in section 3 of the A1 '
              'record. XHCI1.BIN has no arena 1, so, when the ARENAS line says 1=OK, the first attempt of boot 17 uses RAM that the firmware used in '
              'S0, not the runner\'s stack as in boot 16. The first attempt that completes its No Op ends the run: VERDICT NOOP_OK ARENA=1 with END '
              'WRITES=17 is the shortest run.')
    t += para(f'On the page the pre-flight lines are U0 lines (boot 16\'s were U1 lines), the plug watch is the same, and the runner prints SHA=<16 digits> '
              f'of the payload (the page folds case: expect {digits}). The nominal run takes about 71 s. A run whose attempts fail takes up to about '
              '200 s (30 s + 20 x 8 s = 190 s plus at most 10.2 s, about 200 s, as section 6 of the A1 record says), and the first UPTIME heartbeat '
              'after it reads up to about 210 S.')
    t += para('On XHCI0.BIN a success at arena 1 is the first attempt, with no earlier failed attempt whose report could be dropped: the host model never says '
              'LINES_CUT after it (section 7 of the A1 record: after a success at arena 4 from one CHG line on, at arena 3 from two, at arena 2 from three, '
              'never at arena 1). An END line with LINES_CUT after NOOP_OK ARENA=1 is therefore not accounted for, and stops (section 7 below).')
    t += ['', '5. THE HAZARDS (section 8 of the A1 record stands in full; what is new or changed)']
    t += para('(i) The screen may go off again and the unit stay dark (a black-out, below), as in boot 16 and, by the operator\'s memory, boot 15. '
              'Nothing the payload does can recover it, and the page will not say what happened: the operator follows E0 in section 8, and section 6 '
              'says how it is read.')
    t += para('(j) HCCPARAMS1 read 0x110FFC5 on usb_1_ss1 in boot 16 (OBSERVED). Its bit 3, PPC, is clear (INFERRED: bit 3 is HCC_PPC in Linux '
              'drivers/usb/host/xhci-caps.h, read on master, not pinned; not run), so HCRST is not expected to remove port power there: this replaces the '
              'UNKNOWN about PPC in hazard (f) of the A1 record for usb_1_ss1 only. The HCCPARAMS1 of usb_1_ss0 is not recorded (UNKNOWN); both '
              'controllers read the same DWC3 identity in S0 (OBSERVED: section 12 of docs/SP11-STICK-LOG.md), so a clear PPC there is INFERRED, not known.')
    t += para('(k) FSR 0x400 (OBSERVED in S0b and in boot 16): bits 10:9 of a context bank\'s FSR are the FORMAT field, which the Linux driver\'s header keeps '
              'out of its fault mask (VERIFIED by reading arm-smmu.h at the Linux revision pinned in section 10 of docs/SP11-STICK-LOG.md; not run). '
              'So no fault bit is set and the bank records no fault (INFERRED), and the FAR and FSYNR0 beside it, which differed between S0b and boot '
              '16, are stale or undefined while no fault bit is set (INFERRED; they may hold an earlier fault whose FSR bits were cleared, as section 4 '
              'of the A1 record says). This replaces "its meaning is UNKNOWN" in section 4 of the A1 record; for this boot it also replaces the '
              'runbook\'s sentence that their meaning is UNKNOWN (docs/SP11-XHCI-S1.md section 5). Read an SMMU=CHANGED with its SMMU1 line: a fault bit beyond FORMAT in FSR, a '
              'nonzero GFSR or a BERR= is a fault; a change of FAR or FSYNR0 alone, with FSR still 0x400, is a change that nothing explains: SC2 is '
              'not excluded (as for SAME); report it, do not call it a fault.')
    t += para('(l) The HCRST of XHCI0.BIN resets the controller that the stick is probably on (INFERRED: the device path of boot 3, section 13 of '
              'docs/SP11-STICK-LOG.md, last paragraph): harmless after the runner loaded everything (hazard (d) of the A1 record). Arena 1 is RAM '
              'that the memory map calls free, not RAM the runner owns and declared (hazard (c) of the A1 record); the firmware used it in S0 (OBSERVED '
              'there, hub topology), and what it holds in this boot is UNKNOWN: the payload overwrites it and zeroes it again, and nothing reads it '
              'after ExitBootServices (INFERRED).')
    t += para('(m) E0(c) connects the Mac to a port after a black-out, and the Mac may then see a device there (a Qualcomm download-mode device, for '
              'example). Connecting the Mac to a download-mode device is read-only on our side: we add nothing to what macOS does by itself, install '
              'no driver and run no tool against it. The stick is unplugged for E0(c) only after a black-out and the wait of 4 minutes: nothing writes '
              'it (A2 is not granted, and the script wrote its marker before the step ran) and the runner holds everything in RAM, so removing it '
              'changes nothing of the verdict (INFERRED: the payload skips port events), but a running xHC may write event TRBs, which is why (c) '
              'waits.')
    t += para('(n) DBOFF 0x2000, RTSOFF 0x1000 and two scratchpad buffers (HCS2 0x140000F1) were read on usb_1_ss1 only (OBSERVED, boot 16). For usb_1_ss0 '
              'DBOFF, RTSOFF, HCS2 and HCCPARAMS1 are not recorded, so section 2 of the A1 record and the runbook\'s sentences that they are not '
              'recorded (docs/SP11-XHCI-S1.md sections 2, 5 and 10) stand for it; REFUSED=OFFSET, SCRATCHPADS or HCE is possible, and a refusal writes nothing.')
    t += ['', '6. THE OUTCOME TREE FOR BOOT 17 (runbook O1 to O15 with U0 and U1 swapped; their Next columns authorise nothing)']
    t += para('Every reading below is INFERRED until the unit has run. Section 16 of docs/SP11-STICK-LOG.md is the later word on O10\'s reading of the cause: no '
              'restart was seen, so of O10\'s two readings, hang or reset, the hang fits, and a dead display with a live SoC is a third that the runbook '
              'does not list; a page that ends at a NEXT line with the screen going off is read so, and the cause stays UNKNOWN.')
    t += item('-', 'NOOP_OK ARENA=1 (END HALTED=YES WRITES=17): the xHC of usb_1_ss0 read a TRB from the firmware\'s own memory and wrote the event back: '
                   'DMA to that memory works through stream 0x1420 (O1). It does not say why boot 16 stopped: a cause specific to usb_1_ss1 and a cause '
                   'tied to the runner-stack memory class (an SMMU stage 2 or an XPU range) both give this page, and a success ends the run before arena '
                   '2 is tried (INFERRED: one boot of each file, one unit). A stop on arena 1 is the informative branch. Next, not authorised here: the '
                   'runner\'s own stack as the first arena on usb_1_ss0 (a changed payload, a new record) would separate the two.')
    t += item('-', 'NOOP_OK on another arena: if the ARENAS line did not offer arena 1, arena 2 is the first attempt (WRITES=17), the memory class of boot 16, '
                   'and the answer is that of the first reading above, for that class; if WRITES is above 17, an earlier arena failed first, and DMA works '
                   'for that memory class and not for the ones before it: read the R<k> lines of those attempts as O2, O3 and O7 say.')
    t += item('-', 'No VERDICT and no END, the page ending at a NEXT line, with the screen going off again (as in boot 16 and, by the operator\'s memory, '
                   'boot 15) or frozen and lit (O10): the cause is not specific to usb_1_ss1 alone. A systemic cause (the SMMU\'s context bank 3, an XPU, '
                   'the EL2 environment), the firmware\'s memory as the arena, or any start of an xHC at EL2 fits; which of them is UNKNOWN. Say which '
                   'NEXT line it followed (the RUN wait, as in boot 16, or another). A board restart is recorded with its time and the last line of the '
                   'page, and a restart alone is not an xHC result (docs/SP11-XHCI-S1.md, 4.3). Next, not authorised here: the read-only snapshot chain, a payload that reads the clock, '
                   'GDSC, DWC3, glue and SMMU state and prints it before the RS store (a new record).')
    t += item('-', 'NOOP_TIMEOUT or NOOP_HSE with SMMU=CHANGED: SC2 (O2, O7): context bank 3 (streams 0x1420 and 0x1460) faulted or redirected the xHC\'s DMA '
                   'for the arena of that attempt. At the RUN or NOOP stage the payload goes on to the next arena, which is the experiment; at RESET or '
                   'PROGRAM the run ends (O4, O5, O7). Read hazard (k) with it. SMMU programming needs its own named authorisation: neither the A1 '
                   'record nor this one covers it.')
    t += item('-', 'NOOP_TIMEOUT or NOOP_HSE with SMMU=SAME or UNREAD: SC2 is not excluded (the SMMU records only the first fault of a context bank until '
                   'its FSR is cleared, which A1 never does: INFERRED); the stage and the WHY decide (O3 to O8).')
    t += item('-', 'NOOP_BAD_COMPLETION, or any other stop of section 7: the payload ended the run itself, or the page is not one the tree lists: stop, '
                   'and report the page (O8, O11).')
    t += item('-', 'REFUSED=<name> (O9): nothing was written. REFUSED=OFFSET, SCRATCHPADS or HCE is possible on usb_1_ss0 (hazard (n); S0 saw HCE there '
                   'behind a hub: docs/SP11-XHCI-S1.md, 6.2), and the payload does not reset a controller found in error.')
    t += item('-', 'WATCH DONE: O12 to O15 are written for XHCI1.BIN, whose own controller is U1; for XHCI0.BIN the own controller is U0 and the other is '
                   'U1. If the Mac\'s cable is in the same port as in boot 16 (OBSERVED there: U1P2 changed after the plug), expect CHANGED=U1P2: that '
                   'is O13, the other controller, and this boot\'s verdict is about usb_1_ss0\'s DMA only. CHANGED=U0P1 or U0P2 is O12: the free port is '
                   'on the controller under test, the Mac\'s cable sat on the controller being reset (hazard (f) of the A1 record), the stick is not on '
                   'usb_1_ss0, and the premise of section 2 fails. CHANGED=NONE is O14; FAULT= is O15: stop.')
    t += ['', '7. THE STOP RULES AFTER BOOT 17 (rule 9 of the A1 record, amended as section 2 says)']
    t += para('The stop rules of section 9 of the A1 record apply to the page of boot 17 as written, with STORE added for boot 17 to its WHY= values (the '
              'runbook\'s O11 lists it): the sequence stops, and the unit is power-cycled, on a page that ends at a NEXT line, a fault page, a board '
              'restart, END HALTED=NO or HALTED=UNKNOWN, STORE_REFUSED on the END line, a WHY= of READ, CLOCK, STORE, BUDGET or LINES, LINES_CUT on the '
              'END line after any VERDICT except NOOP_OK, or after NOOP_OK when nothing accounts for it, a VERDICT NOOP_BAD_COMPLETION, any REFUSED '
              'that the runbook does not list, and any line that its outcome tree does not list. The waiver of section 2 covers the page of boot 16 '
              'only.')
    t += para(f'Whatever the boot that printed PAYLOAD CALL 0 {digits} shows, it is the last boot of A1 and of this record: there is no retry of a payload in '
              'the same boot, no second boot of this record, no XHCI1.BIN again and no other file, controller or arena order. Any further xHC write, in '
              'any boot, needs a new record that the operator approves by name. After a black-out, a restart, a fault page or a line the tree does not '
              'list the unit is power-cycled (for a black-out E0 says how); after a success the sequence also ends. SC3 counts as section 3 says.')
    t += ['', '8. THE OPERATOR\'S ACTIONS (section 10 of the A1 record and the runbook have them in order; E0 is new)']
    t += para(f'The actions of section 10 of the A1 record stand, and the runbook has them in order: read its plan a1-xhci-s1 as {PLAN_NAME_SS0}, its XHCI1.BIN '
              f'as {name}, and its second boot as boot 17. The stick is straight in one USB-C port with no hub, in the port where it was in boot 16 if '
              'the operator knows which that was, and the other port, the free port, is empty until the cue; it holds SP11\\PLAN.TXT of the plan '
              f'{PLAN_NAME_SS0}, the four files of the plan (the two EFI files and {name} of section 1, and PAYLOAD.MAN) and no SP11\\DONE1.TXT. The '
              f'Shell\'s line reads AUTORUN {PLAN_NAME_SS0} STEP 1 OF 1 (another name means that the stick is not the one of this record: power off in '
              'the 5 s the Shell gives). At the runner\'s lines PAYLOAD LOADED ... SHA=<16 hex digits>, PAYLOAD VERIFY=OK and PAYLOAD CALL 0 <the same '
              f'16 digits>, compare the digits with {digits}, the first 16 of the digest of {name} in section 1: a mismatch, or no VERIFY=OK, means '
              'power off, and nothing of A1 has been written (the marker SP11\\DONE1.TXT has been written; '
              f'a boot that did not print PAYLOAD CALL 0 {digits} did not run the payload and is not boot 17: delete the marker and start again; '
              'once that line has printed, the approval is spent). Plug the Mac\'s cable only at the on-screen cue. The eight seconds after each NEXT '
              'line are the cancel window, and nothing of that group is written yet: in it the operator powers off only if the page itself is wrong, '
              'as the runbook says. '
              'If you did not plug the Mac\'s cable by WATCH DONE, power off in the 8 s after NEXT ARENA: a run without the cable is not approved, and '
              'that counts as the page being wrong. Apart from that, touch nothing until END; power-cycle afterwards.')
    t += E0 + ['']
    t += ['9. NOT AUTHORISED BY THIS RECORD']
    t += para('A2; any write beyond sections 2 and 3 of the A1 record; a second boot of this record, a retry, XHCI1.BIN again, any other payload, build, '
              'controller or arena order; the port reset, the enumeration of the Mac, the NCM driver, the SMMU programming and every register class '
              'docs/SP11-XHCI-S1.md section 7 lists: each needs its own named authorisation. Also not covered: a run without the Mac\'s cable; the '
              'read-only snapshot payload of section 6, a payload that restarts the unit by itself, or any read of a watchdog, PMIC or TCSR register; '
              'sending anything to a device that appears on the Mac after a black-out (E0(c) only looks).')
    return _checked(t)


def supplement_digest(payload, runbook, a1=None):
    """(text, byte size, SHA-256 of the text) of the supplement record."""
    text = render_supplement(payload, runbook, a1)
    return text, len(text.encode('ascii')), sha256_text(text)


def payload_file(controller, path):
    """The PayloadFile of the bytes at `path`, which is the file of `controller` (1: XHCI1.BIN, 0: XHCI0.BIN). Reads the file; writes nothing."""
    data = Path(path).read_bytes()
    return PayloadFile(controller, len(data), hashlib.sha256(data).hexdigest())


def runbook_file(path):
    """The RunbookFile of the bytes at `path`, as they are on disk (no newline conversion): the digest the operator computes on the committed file."""
    data = Path(path).read_bytes()
    return RunbookFile(len(data), hashlib.sha256(data).hexdigest())


def staging_command(plan_files, plan_name=PLAN_NAME):
    """The argument vector of `tools/stage-sp11-boot-media.py autorun` that stages the plan, for the operator or a reviewer to run on a scratch directory.
    `plan_files` maps the stick names to (path, sha256): the slbounce build, the runner, the manifest and the payload; `plan_name` is the plan the record
    names (PLAN_NAME for the A1 record; PLAN_NAME_SS0 for the supplement of boot 17, whose XHCI0.BIN plan the stage tool must not name a1-xhci-s1).
    Nothing is run here."""
    argv = ['autorun', '--dest', '<scratch>', '--name', plan_name, '--step', f'{SLBOUNCE_NAME} {RUNNER_NAME} --el2']
    for name in (SLBOUNCE_NAME, RUNNER_NAME):
        path, sha = plan_files[name]
        argv += ['--file', name, path, sha]
    for name in sorted(n for n in plan_files if n not in (SLBOUNCE_NAME, RUNNER_NAME)):
        path, sha = plan_files[name]
        argv += ['--data', name, path, sha]
    return argv


def main(argv=None):
    """The record text of the given payload files and runbook on standard output, or with --digest its size and SHA-256; with --supplement, the supplement
    for boot 17 (XHCI0.BIN) instead. Exit status 1 for a refused input."""
    parser = argparse.ArgumentParser(description='Print the authorisation record of point A1 (the xHCI S1 No-Op DMA test) for the payload files and the runbook, '
                                     'or with --supplement the supplement for boot 17; writes nothing.')
    parser.add_argument('--xhci1', help='XHCI1.BIN (usb_1_ss1), the file of the first boot (required, except with --supplement)')
    parser.add_argument('--xhci0', help='XHCI0.BIN (usb_1_ss0), the file of the optional second boot; with --supplement, the file of boot 17')
    parser.add_argument('--supplement', action='store_true', help='the supplement record for boot 17 (XHCI0.BIN unchanged, the runbook of the A1 record): '
                        'needs --xhci0, takes no --xhci1')
    parser.add_argument('--runbook', default=str(DEFAULT_RUNBOOK), help=f'the runbook whose SHA-256 the record names (default: {RUNBOOK_NAME} of this repository)')
    parser.add_argument('--digest', action='store_true', help='print "<bytes> <sha256>" of the record text instead of the text')
    args = parser.parse_args(argv)
    if args.supplement and (args.xhci1 or not args.xhci0):
        parser.error('--supplement needs --xhci0 and takes no --xhci1: the file of boots 15 and 16 is named by the supplement itself')
    if not args.supplement and not args.xhci1:
        parser.error('--xhci1 is required, except with --supplement')
    try:
        payloads = ([payload_file(1, args.xhci1)] if not args.supplement else []) + ([payload_file(0, args.xhci0)] if args.xhci0 else [])
        runbook = runbook_file(args.runbook)
        text, size, sha = supplement_digest(payloads[0], runbook) if args.supplement else digest(payloads, runbook)
    except (OSError, RecordError) as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    sys.stdout.write(f'{size} {sha}\n' if args.digest else text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
