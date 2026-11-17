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
    lines += [f'   {RUNBOOK_NAME:<24} sha256 {runbook.sha256}',
              f'      {runbook.size} bytes; the runbook that sections 9 and 10 refer to (outcome tree, actions in order); read, not staged.']
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
    for number, line in enumerate(t, 1):
        if len(line) > WIDTH or not line.isascii():
            raise RecordError(f'line {number} is {len(line)} characters or not ASCII: {line[:60]!r}')
    return '\n'.join(t) + '\n'


def digest(payloads, runbook):
    """(text, byte size, SHA-256 of the text) of the record."""
    text = render(payloads, runbook)
    return text, len(text.encode('ascii')), sha256_text(text)


def payload_file(controller, path):
    """The PayloadFile of the bytes at `path`, which is the file of `controller` (1: XHCI1.BIN, 0: XHCI0.BIN). Reads the file; writes nothing."""
    data = Path(path).read_bytes()
    return PayloadFile(controller, len(data), hashlib.sha256(data).hexdigest())


def runbook_file(path):
    """The RunbookFile of the bytes at `path`, as they are on disk (no newline conversion): the digest the operator computes on the committed file."""
    data = Path(path).read_bytes()
    return RunbookFile(len(data), hashlib.sha256(data).hexdigest())


def staging_command(plan_files):
    """The argument vector of `tools/stage-sp11-boot-media.py autorun` that stages the plan, for the operator or a reviewer to run on a scratch directory.
    `plan_files` maps the stick names to (path, sha256): the slbounce build, the runner, the manifest and the payload. Nothing is run here."""
    argv = ['autorun', '--dest', '<scratch>', '--name', PLAN_NAME, '--step', f'{SLBOUNCE_NAME} {RUNNER_NAME} --el2']
    for name in (SLBOUNCE_NAME, RUNNER_NAME):
        path, sha = plan_files[name]
        argv += ['--file', name, path, sha]
    for name in sorted(n for n in plan_files if n not in (SLBOUNCE_NAME, RUNNER_NAME)):
        path, sha = plan_files[name]
        argv += ['--data', name, path, sha]
    return argv


def main(argv=None):
    """The record text of the given payload files and runbook on standard output, or with --digest its size and SHA-256. Exit status 1 for a refused input."""
    parser = argparse.ArgumentParser(description='Print the authorisation record of point A1 (the xHCI S1 No-Op DMA test) for the payload files and the runbook; '
                                     'writes nothing.')
    parser.add_argument('--xhci1', required=True, help='XHCI1.BIN (usb_1_ss1), the file of the first boot')
    parser.add_argument('--xhci0', help='XHCI0.BIN (usb_1_ss0), the file of the optional second boot')
    parser.add_argument('--runbook', default=str(DEFAULT_RUNBOOK), help=f'the runbook whose SHA-256 the record names (default: {RUNBOOK_NAME} of this repository)')
    parser.add_argument('--digest', action='store_true', help='print "<bytes> <sha256>" of the record text instead of the text')
    args = parser.parse_args(argv)
    try:
        payloads = [payload_file(1, args.xhci1)] + ([payload_file(0, args.xhci0)] if args.xhci0 else [])
        text, size, sha = digest(payloads, runbook_file(args.runbook))
    except (OSError, RecordError) as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    sys.stdout.write(f'{size} {sha}\n' if args.digest else text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
