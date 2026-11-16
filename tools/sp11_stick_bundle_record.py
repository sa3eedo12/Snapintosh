# SPDX-License-Identifier: MIT
"""The authorisation record of an SP11 stick bundle (docs/SP11-STICK-BUILD.md section 6): the text the operator approves, whose SHA-256 is
PIN.DAT's authorisation_sha256, and the rules of the note that can be appended to it. It is here and not in tools/sp11_stick_bundle_build.py
so that the text the operator reads, and what every test and document says about it, has one home; the builder imports it and keeps the file
reading, the approval check and the pipeline.

The text is wrapped by hand, never by a library: its digest is the operator's approval, so it must not depend on how a library version breaks
lines, and a changed byte is another record. A record of a synthetic build must not change when a line about a real build is added: the
lines of section 1a exist only for kind `real`.

Evidence: EXPERIMENTAL, hardware NOT RUN. Nothing here approves anything."""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402

ch.KNOWN_REFUSALS.add('NOTE_BAD')       # the builder lists it among its own refusals too
MIB = sb.MIB
STORES = {'ram': sb.STORE_RAM, 'el2_sysreg': sb.STORE_EL2_SYSREG, 'gic': sb.STORE_GIC}       # the classes --stores names, in the order the record lists them
DEFAULT_SEED = 'sp11-stick-bundle'      # the label `real` uses when --seed is not given: a default of the tool, not a decision of the operator
NOTE_LINES, NOTE_COLUMNS = 40, 120
PANEL_WORDS = {'withheld': 'the guest is given no framebuffer', 'handed': 'the guest is given the unit\'s framebuffer'}
HVC_WORDS = {0: 'a stock kernel', 1: 'the loader replaces the kernel\'s SPTM call words'}

# The sentences about the dry-run wrapper restate docs/SP11-STICK-WRAPPER.md section 4 and name it as their source; a build whose
# SNAPMON.BIN does more than that wrapper has to say so in a note (section 6 of the record).
TOUCHES = {
    'ram': ('RAM inside the placement only: the copy of the chunks, zero fills, the monitor work area and out buffer, and',
            'the report written into REPORT.DAT. No disk, no device, no RAM outside the placement. What the dry-run wrapper',
            'does to the machine is in docs/SP11-STICK-WRAPPER.md section 4: RAM stores only to page 0 of REPORT.DAT and,',
            'after the stage COPYING, to the chunk destinations; cache maintenance by VA; AT S1E2W and AT S1E2R (they set',
            'PAR_EL1); MRS reads; nothing else.'),
    'el2_sysreg': ('required in every PIN.DAT by the interface (STORE_REQUIRED), so always listed. The dry-run wrapper',
                   'stores no EL2 system register (docs/SP11-STICK-WRAPPER.md section 4: MRS reads, no MSR). A monitor entry',
                   'would write and restore HCR_EL2, VBAR_EL2, TPIDR_EL2, VTCR_EL2, VTTBR_EL2, CNTHCTL_EL2, MDCR_EL2 and the',
                   'OS lock (docs/SP11-MONITOR-PORT.md section 12; read from the sources, not run).'),
    'gic': ('GIC registers for the timer stop (PPI 26). Which registers is UNKNOWN until the wrapper (work item 4) and its',
            'register list (work item 6) exist: do not approve this class before they do.')}
# The same three classes for the GIC and stop dry run (--gic-stop; docs/SP11-STICK-GIC.md sections 4 and 5), where payload 0 is the wrapper
# built with SNAP_SP11_STICK_GIC=1. A register named here is one the binary's census lists (tools/test-sp11-stick-gic-build.py checks it).
GIC_STOP_TOUCHES = {
    'ram': ('RAM inside the placement only, as the dry-run wrapper: the copy of the chunks, zero fills and the report in',
            'REPORT.DAT. One more RAM store of the GIC and stop stage: its record, 1376 bytes at REPORT.DAT offset 0x11000',
            '(after the monitor\'s 64 KiB window), which is inside the report. No other RAM.'),
    'el2_sysreg': ('required in every PIN.DAT by the interface (STORE_REQUIRED). The GIC and stop wrapper writes these',
                   'registers of the boot CPU, each read first, put back and verified by read-back: class C ICC_PMR_EL1,',
                   'ICC_BPR1_EL1, ICC_IGRPEN1_EL1; class T CNTHP_CVAL_EL2 and CNTHP_CTL_EL2 (the EL2 physical timer, armed',
                   'for a time box of 2 s in the default build, in each of two windows, then disabled); class V VBAR_EL2 (the',
                   'wrapper\'s own vector table) and DAIF.I (cleared only inside a window of at most twice the time box); in',
                   'the handlers ICC_EOIR1_EL1 (ICC_DIR_EL1 when EOImode is set), SPSR_EL2 and ELR_EL2. Class E, only after a',
                   'first window passed and a second announcement and 8 s: HCR_EL2 (RW and IMO, TGE clear), VBAR_EL1 and',
                   'SCTLR_EL1 (MMU and caches off) for one ERET to a stub at EL1 that spins on CNTPCT_EL0 for at most twice',
                   'the time box and ends by HVC; its lines are made visible with DC CIVAC and IC IVAU. It writes no',
                   'CNTHCTL_EL2, TPIDR_EL2 or other register, executes no SMC, SVC or TLBI, and calls no monitor. Total',
                   'time of the two windows and the two 8 s waits: about 20 s.'),
    'gic': ('memory-mapped GIC registers in the pinned frames only (PIN.DAT gicd_base, gicr_first, gicr_stride,',
            'gicr_count). Class G: GICD_CTLR, only when the value read shows group 1 disabled: EnableGrp1A is set, and',
            'EnableGrp1 too unless DS is set (a read of 0x10 becomes 0x13); the old value is saved and put back. Class R: in',
            'this CPU\'s own redistributor (found by matching GICR_TYPER to MPIDR; the other',
            'frames are read for GICR_TYPER only) the SGI frame of INTID 26: IPRIORITYR byte, ISENABLER0 bit, ICFGR1',
            'field if it reads edge, IGROUPR0 bit if the distributor shows one security state, and the put-backs',
            'ICENABLER0 and ICPENDR0. Never the ITS, another interrupt, SMMU, PCIe, USB or the framebuffer.')}


def note_lines(data):
    """The lines of a --note file's bytes. They are appended to the record the operator approves, so they must read on a terminal exactly as
    they are stored: printable ASCII and line feeds only (a carriage return, a backspace or an escape sequence could hide or overwrite a
    word), at most NOTE_LINES lines of NOTE_COLUMNS characters. A refusal quotes no byte of the file."""
    if (bad := next((i for i, byte in enumerate(data) if byte != 10 and not 32 <= byte <= 126), None)) is not None:
        raise ch.Refused('NOTE_BAD', f'--note byte {bad} is not printable ASCII or a line feed')
    lines = data.decode('ascii').split('\n')
    if lines[-1] == '':
        lines.pop()         # the terminator of the last line, not a line of its own
    if not any(line.strip() for line in lines):
        raise ch.Refused('NOTE_BAD', '--note holds no text')
    if len(lines) > NOTE_LINES:
        raise ch.Refused('NOTE_BAD', f'--note has {len(lines)} lines; at most {NOTE_LINES}')
    if long := next((i for i, line in enumerate(lines, 1) if len(line) > NOTE_COLUMNS), None):
        raise ch.Refused('NOTE_BAD', f'--note line {long} has {len(lines[long - 1])} characters; at most {NOTE_COLUMNS}')
    return lines


def real_lines(real):
    """Section 1a of the record of a `real` build: what GUEST.DAT and PIN.DAT were made from, as used and not as decided. `real` holds
    kernelcache_sha256, guest_profile, panel, sptm_hvc_calls, seed, descriptor_sha256, board_id, board_status and ecam_windows (the number of
    config windows the descriptor lists). A descriptor that is EXPERIMENTAL and lists none is the guest view of docs/SP11-STICK-REAL.md
    section 3: it does not describe the Surface, and the board adapter's equality rule on the MCFG (D11 proper) is not satisfied by it."""
    seed = real['seed']
    lines = ['1a. What this build is (the host-side real path of docs/SP11-STICK-REAL.md: GUEST.DAT is made from a kernelcache)',
             f'   kernelcache  guest profile {real["guest_profile"]}  sha256 {real["kernelcache_sha256"]}',
             f'   D9 as built  panel {real["panel"]}: {PANEL_WORDS[real["panel"]]}',
             f'                SPTM HVC call patch {real["sptm_hvc_calls"]}: {HVC_WORDS[real["sptm_hvc_calls"]]}',
             f'   boot seed    label {seed} ' + ('(a default, not an operator decision; boot entropy UNIMPLEMENTED)' if seed == DEFAULT_SEED else '(given with --seed)'),
             f'   descriptor   sha256 {real["descriptor_sha256"]} (PIN.DAT descriptor_sha256)',
             f'                board {real["board_id"]}  status {real["board_status"]}']
    if real['board_status'] == 'EXPERIMENTAL' and real['ecam_windows'] == 0:
        lines.append('                guest view: pcie.ecam empty; D11 proper open')
    return lines


def record_text(r):
    """The text the operator approves (its SHA-256 is PIN.DAT's authorisation_sha256): deterministic, ASCII, no personal name. `r` holds place
    (the interface's placement), stores (the mask), time_box_s, label, monitor and reporter ((size, sha256)), guest (size, sha256, chunks,
    index), note (lines or None), gic_stop (the record of the GIC and stop dry run) and real (None for a synthetic build, else real_lines'
    argument)."""
    p, g = r['place'], r['guest']
    lines = [
        f'SNAPINTOSH SP11 STICK BUNDLE - AUTHORISATION RECORD (format 1, label {r["label"]})', '',
        'Status: operator decision NOT RECORDED. Hardware: NOT RUN. This text is a template until the operator approves it.',
        'Approval is the operator\'s alone: the SHA-256 of exactly this text, recorded as authorisation_sha256 of PIN.DAT; a changed byte is another',
        'record. It names no person and stays local: it is never committed or published.', '',
        '1. What may run (named by SHA-256; the runner hashes the payloads before each call)',
        f'   payload 0  SNAPMON.BIN  {r["monitor"][0]} bytes  sha256 {r["monitor"][1]}',
        f'   payload 1  SNAPREP.BIN  {r["reporter"][0]} bytes  sha256 {r["reporter"][1]}',
        f'   data       GUEST.DAT    {g["size"]} bytes  sha256 {g["sha256"]}  chunks {g["chunks"]}  table sha256 {g["index"]}',
        f'   data       REPORT.DAT   {sb.REPORT_BYTES} bytes, all zero, sha256 {hashlib.sha256(bytes(sb.REPORT_BYTES)).hexdigest()}',
        '   data       PIN.DAT      1024 bytes; it carries this record\'s digest, so its own digest cannot be named here', '']
    if r.get('real'):
        lines += [*real_lines(r['real']), '']
    lines += [
        '2. Placement (the one range the run may store to)',
        f'   guest_base {p["guest_base"]:#x}  guest_size {p["guest_size"] // MIB} MiB  reserve {p["reserve_size"] // MIB} MiB  '
        f'work_base {p["work_base"]:#x}  work_size {p["work_size"] // MIB} MiB',
        f'   place_base {p["place_base"]:#x}  place_end {p["place_base"] + p["place_size"]:#x}  (limit 2^36)', '',
        f'3. Store classes authorised (PIN.DAT authorised_stores = {r["stores"]})']
    touches = GIC_STOP_TOUCHES if r.get('gic_stop') else TOUCHES
    if r.get('gic_stop'):
        lines.append('   Record of the GIC and stop dry run (docs/SP11-STICK-GIC.md): payload 0 is SNAPMON-GIC.BIN; no monitor is called.')
    for name in STORES:
        if r['stores'] & STORES[name]:
            first, *rest = touches[name]
            lines += [f'   {name}: {first}', *(f'      {line}' for line in rest)]
    lines += ['   Not authorised: any other store class, a disk or NVMe write, a firmware write, a clock, voltage or DVFS change, a secondary CPU.', '',
              f'4. Time box: {r["time_box_s"]} s of wall time on CNTPCT_EL0, carried in the PARAMS chunk (watchdog_seconds); the stop is the monitor\'s own.', '',
              '5. Refusal and stop rules (docs/SP11-MONITOR-PORT.md section 14)',
              '   - A wrapper REFUSED line ends the boot: fix the cause, never retry the same bundle.',
              '   - No page change for twice the time box after ENTER: note the last line and power cycle.',
              '   - Two resets at the same last line stop the series; a touch outside RAM is a finding, not a retry.',
              '   - One hardware client; AC power or at least 50% battery; the operator present and filming. A spinning guest is ended by the',
              '     operator\'s power cycle, which writes nothing.']
    if r['note']:
        lines += ['', '6. Notes of this build (part of the digest)', *r['note']]
    text = '\n'.join(lines) + '\n'
    text.encode('ascii')
    return text
