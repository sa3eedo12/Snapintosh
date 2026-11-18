# SPDX-License-Identifier: MIT
"""Rung 3 of the monitor entry run: the panel as the kernel's console (rung 2) with one more word in the guest's command line, `cpus=1`, which asks XNU to run with one CPU
(docs/SP11-STICK-MONITOR.md section 20, K10). Rung 2 (boot 21) stopped itself at the kernel's first CPU-start write; the monitor runs one CPU and the guest's tree lists six. This
record is the first experiment of the CPU question and changes the guest's input only: the same programs, box, panel and store classes (tools/sp11_stick_bundle_panel_rung2.py
has rung 2's words, and the words that both share).

What is here is what the record says of this rung and the guide's table PANEL takes by key (`console+cpus=1`, tools/sp11_stick_bundle_panel.py GUIDE_WORDS): why rung 3 (section
0), what boot 21 showed (section 0a), the hazard of the word, the evidence of section 3c that is not rung 2's (the summary, the introduction, the label, the word itself, what the
run may end as) and section 5d, in the operator's words: what is new, what to film, what the film decides (the processor lines of the replayed log and the stop's A=, which the
track-base note files/next-after-cpu.md sets as the pass and the three failures), what a panic text would look like, and what counts as a result. Boot 21's numbers are the
operator's film, read at full resolution by the coordinator (OBSERVED, one unit, one boot); the decode of its stop is INFERRED. What XNU does with `cpus=1` is read in the design
note files/next-after-loop.md from the public source (tag xnu-12377.121.6, not this kernel's own) and is INFERRED here; the A16's record of the word is two sentences and no log.

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402

WORD = 'cpus=1'
BOOT21_RECORD = '1ad122b3...fc3b'       # the approved record of rung 2: SHA-256 1ad122b3cd9e43afea147797183df48c43e4e510daac24e74efbea1d3bd4fc3b
BOOT21_TICKS = 39_245_286               # MON RET TICKS= of boot 21, in ticks of 19.2 MHz (OBSERVED once)
BOOT21_S = f'{BOOT21_TICKS / 19.2e6:.3f}'
BOOT21_STOP = dict(pc=0xFFFFFE00097A352C, a=0x300734004, b=0x93970045, c=0xFFFFFE60A0178004)      # MON STOP S2_FAULT PC= A= B= C= of boot 21 (OBSERVED once)
BOOT21_GUEST = 'ACAD28623AEBB068'       # rung 2's GUEST.DAT as the runner prints its first 16 digits (PAYLOAD LOADED INDEX=2)
BOOT21_LABEL = 'M1RUN-CONSOLE-RUNG2'
LINE = '-v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpus=1'      # the command line of this record (probes/xnuboot.py BOOT_CMDLINE with serial=2 and the word)


def page_hex(value):
    """A value as the runner's hex op prints it: 0X and the fewest digits, capitals."""
    return f'0X{value:X}'


CPU_START_WINDOW = (0x300734004, 0x30073400C)      # offsets 4, 8 and 12 of the PMGR CPU-start block: a stop with A= in it is the CPU-start stop (the design note files/next-after-cpu.md, section 5)
WINDOW = f'A= in {page_hex(CPU_START_WINDOW[0])}..{page_hex(CPU_START_WINDOW[1])}'
CPU_LINES = '"processor 0 belongs ..."'     # the replayed early log's line for CPU 0, as the verdicts name it (boot 21's log also has the lines of the processors 1 to 5)
LOG_CPU0 = '"[processor_init] processor 0 belongs to processor set 0 of type 0"'     # boot 21's log, as the film shows it (OBSERVED once)
LOG_CPUN = '"[processor_init] processor N belongs to processor set T of type T"'      # the lines of the processors 1 to 5 in the same log
LOG_T = 'T is 0 for 1 to 3 and 1 for 4 and 5'
NOISE = ('the SPMI lines ("parity error", "_readRegs failed"), "Couldn\'t alloc class AFKFirmwareService" and the RTKit lines "General panic behavior: debugFacility = ..."')
# The documents that this rung's text cites, by section and not by line (as DOC_CITES of tools/sp11_stick_bundle_panel.py): (the words of the cite, the file, the heading that opens
# the section, the fragments that must all be in that section). Held to the text by tools/test-sp11-stick-monitor-rung3.py.
DOC_CITES = (
    ('docs/BOOT-CHAIN.md section 7', 'docs/BOOT-CHAIN.md', '## 7. ', ('the topology with `cpus=1` is not an option for this kernel: it trips a PMGR', 'CPU-complex assertion earlier in boot.',
                                                                       'is UNKNOWN until X4 of the Surface Pro 11 plan.')),)
HEADER = '3c. The panel as the kernel\'s console with cpus=1 (rung 3: D9 panel handed, serial=2, one more word, stock kernel; docs/SP11-STICK-MONITOR.md section 20)'
RUNG = ('rung 1, the first run with the panel withheld, and rung 2, the panel as the console (boot 21), whose records have their own approvals, spent: this record is rung 3, the '
        'panel handed to the guest as the kernel\'s console (serial=2) with cpus=1 in its command line, and no approval covers another\'s boot')
DECIDES = ('APPROVING THIS TEXT ALSO DECIDES A CHOICE OF D9 THAT IS NOT DECIDED YET: the guest is handed the unit\'s panel as the kernel\'s console (serial=2), with the kernel '
           'stock and one word more in its command line, cpus=1, the first boot-arg that is not the loader\'s (section 3c). The first run withheld the panel, and rung 2 had no such word.')
CONSOLE = ('The guest is given the panel (section 3c) and its command line is serial=2 with cpus=1: its console is the panel (boot 21, rung 2: the kernel\'s log was on it, OBSERVED '
           'once) and the MON CON lines are not expected to carry it (serial=2: boot 21 had none, OBSERVED once); what cpus=1 does is UNKNOWN.')
RAN = 'ran twice on this machine (boots 20 and 21: section 0a).'
FAULT_SEEN = f'OBSERVED once: boot 21 stopped that way, at A={page_hex(BOOT21_STOP["a"])}, which section 0a INFERS to be the first CPU-start write'
STOP_SEEN = {'S2_FAULT': 'OBSERVED once (boot 21, one unit, one boot).',
             'PANIC_ENTRY': 'Never seen (UNKNOWN). With serial=2 the panic text is on the panel, not in the MON CON lines.'}
FILM_UNTIL = 'until 60 seconds after the SNAPREP report page has appeared, or, if it never appears, until {wait} s after the last page change'
STEP6 = ' For this record wait until 60 seconds after the SNAPREP page appeared, by the film, before that FORCE OFF (a margin, not a measurement).'
STAND_HEAD = '0a. Where things stand (boots 20 and 21, 2026-11-18, one unit, one boot each; OBSERVED unless a sentence says INFERRED)'
STAND_LOOP = 'Boot 21 did not stop there again, which fits it (INFERRED).'
# What boots 20 and 21 showed of the map and of the monitor's two reads of the panel range (F6 of the review of R3): the guest ran 2.044 s with the panel range and its one stage-2
# abort was outside RAM and the panel range, so these statements are no longer "never run".
RAM_SHOWN = (f'boot 20\'s 5 s, with the map of RAM only, showed no stage-2 abort, and boot 21\'s {BOOT21_S} s, with the panel range, one, outside RAM and the panel range, '
             'OBSERVED once each')
RAM_NOTE = f'(INFERRED; {RAM_SHOWN})'
GETS_NOTE = '(INFERRED from the sources; boots 20 and 21 ran it: the bullet on disk writes below)'      # the same facts as RAM_NOTE, which that bullet says in full
READS_ROW = f'OBSERVED once  inside boot 21\'s call of {BOOT21_S} s (not told apart)'
READS_SEEN = f'; both reads ran inside boot 21\'s call of {BOOT21_S} s (OBSERVED once, not told apart)'
ROW_E_COST = 'and their cost is UNKNOWN)'


def row_e(real, box_s, wait_s):
    """5a, row e as the console has it, with the cost of the monitor's two reads of the panel range said as boot 21 showed it."""
    text = r2.row_e(real, box_s, wait_s)
    assert not text or ROW_E_COST in text, 'row e of rung 2 has changed under the words of rung 3'
    return text.replace(ROW_E_COST, f'and their cost is OBSERVED once inside boot 21\'s call of {BOOT21_S} s (not told apart))')


def film_until(real, box_s, wait_s):
    """Step 2 of section 5c: the film runs until 60 s after the report page (the first heartbeat is not the end), as the coordinator reads the panel from the film alone."""
    return FILM_UNTIL.format(wait=wait_s)


def step4(guest, label):
    """5c, step 4: the programs' digits are rung 1's and rung 2's too, so the GUEST.DAT line, the label and the chunk count are compared, and rung 2's bundle would pass the rest."""
    return (f' Then compare, on the PAYLOAD LOADED line, INDEX=2 ... BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, '
            f'LABEL={label} CHUNKS={guest["chunks"]} (boot 21\'s page had LABEL={BOOT21_LABEL} CHUNKS=46 and INDEX=2 ... SHA={BOOT21_GUEST}: rung 2\'s bundle staged under this plan has '
            'the same programs, the same size and the same chunk count, and only that SHA and the label tell it from this one).')


def hazard(real, box_s, wait_s):
    """The panel's bullet of 0b for this record: the console on the panel is boot 21's, and the word is a path that no run on this unit has taken."""
    return ('The guest can write the unit\'s framebuffer, which the display scans out, and the kernel draws its console there (OBSERVED once: boot 21): from the MON CALL line the '
            'panel is cleared and fills with the kernel\'s log (the kernel\'s console clears the whole panel when it takes it: public source, not this kernel\'s own), and its text '
            'can paint over the page, so that the report can be hard to read. The film is the only record of that text and of the lines printed before the guest ran (section 5d). '
            'cpus=1 sends the kernel down a path that no run on this unit has taken: it may panic or spin there, and the monitor ends the run at the box or at its own stop either '
            f'way (section 5d). {r2.force_off(wait_s)}.')


def why_lines(real):
    """Section 0's paragraph for this rung: why rung 3, the one change, the prediction and what falsifies it. Boot 21's facts are section 0a's (stand_extra)."""
    run = real.get('panel_facts', {}).get('run')
    programs = '' if run is None else (' The programs and the time box are rung 2\'s (section 3c).' if not r2.differs(run) else
                                       f' The programs and the box are NOT all rung 2\'s: {"; ".join(r2.differs(run))}. Section 3c and section 4 say what that adds.')
    return m1t.wrap('WHY RUNG 3. Boot 21 (rung 2) PASSED: the kernel printed its boot log on the panel and the monitor stopped it at '
                    f'{BOOT21_S} s on a stage-2 fault at IPA {page_hex(BOOT21_STOP["a"])}, INFERRED to be the first CPU-start write (section 0a). The guest\'s tree lists six CPUs and the monitor '
                    'runs one. THIS RECORD CHANGES ONE THING IN THE GUEST: its command line gets one more word, cpus=1, which asks XNU to run with one CPU, so that it registers only '
                    'processor 0 and does not start the others (INFERRED from the public XNU source; the A16 once tripped a PMGR CPU-complex assertion with it: UNKNOWN which; section '
                    '3c).' + programs + ' PREDICTION (INFERRED): the replayed early log shows only '
                    f'{CPU_LINES} (boot 21\'s showed processors 0 to 5), the stop is no longer S2_FAULT with {WINDOW}, and the kernel runs on. FALSIFIER: "processor 1..5" lines '
                    'still printed and boot 21\'s stop again: cpus=1 was not honoured, and rung 3 is NOT PASSED. A panic text from the PMGR or CLPC code is another possible end. '
                    'Section 3c has the other ends and section 5d what the film decides.')


def stand_extra(real):
    """The paragraphs that section 0a gets for this record (its `stand` words): what boot 21 showed, with its labels."""
    s = BOOT21_STOP
    return (*m1t.wrap(f'Boot 21 (rung 2, record {BOOT21_RECORD}; one unit, one boot; the operator\'s film, read at full resolution) ran the monitor entry with the panel as the '
                      f'kernel\'s console: the call returned after {BOOT21_S} s (TICKS={BOOT21_TICKS} at 19.2 MHz; the 5 s box was not used) and the run ended as MON STOP S2_FAULT '
                      f'PC={r2.pc_text(s["pc"])} A={page_hex(s["a"])} B={page_hex(s["b"])} C={r2.pc_text(s["c"])}, with MON REGS RESTORED=YES and GIC RESTORE CLEAN=YES. The '
                      'decode of B (ESR_EL2) is INFERRED: a data abort from a lower EL, a 32-bit write, a translation fault at level 1: the guest wrote a 32-bit value to the unmapped '
                      f'IPA {s["a"]:#x}, which matches the first CPU-start write that the sources document for the A16 (compat/boot-relocate.h:147-153) and the plan\'s expected failure '
                      '5; the value written was not printed.'),
            *m1t.wrap('The panel showed the kernel\'s own boot log from its start (the first frames after the clear replay its message buffer): past PE_init_iokit (INFERRED: boot 20\'s '
                      f'loop is gone), the MAC policy and the platform-driver matching, with the line {LOG_CPU0} early and, later, a line {LOG_CPUN} for each of the processors 1 to 5 '
                      f'({LOG_T}; the six CPUs of the guest\'s tree; only CPU 0 running); no MON CON line (serial=2). The log is noisy, and the noise is not the failure: ' + NOISE + ' were on '
                      'the film (OBSERVED once: boot 21); that they come from the monitor\'s empty models of those devices is INFERRED.'),
            *m1t.wrap('Rung 2 PASSED. As after boot 20, the stick after boot 21 was unchanged but for the Shell\'s marker and the Mac\'s own .fseventsd entry, and REPORT.DAT stayed the '
                      'all-zero staged file.'))


def in_short(panel):
    """The summary of section 3c."""
    return ('In short. This record is rung 3: rung 2 (boot 21) again with one word more in the guest\'s command line, cpus=1, so that XNU runs with one CPU. Why: boot 21 stopped at a write that section 0a INFERS to be the kernel\'s first CPU-start write. Expected, INFERRED: the replayed early log shows only "processor 0 belongs ..." and the stop is not that '
            'write again; a panic from the PMGR or CLPC code is possible (the A16 once tripped a PMGR CPU-complex assertion with cpus=1: UNKNOWN which). The programs, the box, the '
            'panel and serial=2 are rung 2\'s. What to film, what is new and what the film decides are in section 5d, in the operator\'s words; this section is the evidence for them.')


def intro(panel):
    """The introduction of section 3c: rung 2 is the run before, this record changes the command line and nothing else."""
    run = panel['run']
    diff = r2.differs(run)
    shared = ('The word needs no change of the monitor, the wrapper or the reporter (it is an input of the guest-image build, in boot_args, and no flag of the monitor), and this '
              f'record changes none of them: SNAPMON-M1.BIN and SNAPREP-M1.BIN have the SHA-256 that boots 20 and 21 ran ({r2.RUNG1_MONITOR[:8]}... and {r2.RUNG1_REPORTER[:8]}..., in full '
              f'in section 1), the time box is {r2.RUNG1_BOX_S} s and the store classes are rung 2\'s. Only GUEST.DAT (one page of boot_args: the command line) and PIN.DAT (the label and '
              'the digests) differ from rung 2\'s, and PAYLOAD.MAN, which lists them; everything of section 1b is the first run\'s. Section 5 has the rule that rung 2 had.'
              if not diff else
              f'The word needs no change of the monitor, the wrapper or the reporter, but this record is NOT rung 2 with the command line changed alone: {"; ".join(diff)}. Section 0 and '
              'section 4 say what that adds; everything of section 1b is the first run\'s. Section 5 has the rule that rung 2 had.')
    return (f'Rung 2 is the run before this one: the panel handed as the kernel\'s console (serial=2), record {BOOT21_RECORD}, boot 21 (section 0a); its approval is spent. This record is '
            f'rung 3, rung 2 with one input of the guest changed: its command line has cpus=1 after rung 2\'s. It is not decided: approving the SHA-256 of this record is that decision, '
            f'and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); rung 2\'s approval covers none of it. {shared} At run time the guest differs from rung 2\'s '
            'in one thing: XNU is asked to run with one CPU.')


def label(panel, label_text):
    """The label paragraph of section 3c."""
    return (f'Label: PIN.DAT\'s build_label is {label_text}. The builder puts CPUS1- after M1RUN- for this record only, in place of CONSOLE-, and refuses a facts label that starts '
            'with it, with CONSOLE- or with PANEL-, so that the labels of the other choices cannot read as this one (PIN.DAT\'s label holds 23 characters, so M1RUN-CONSOLE-CPUS1- would '
            'leave three for the facts\' label); the page prints it on its SNAPMON STAGE=PIN_OK line (LABEL=; probes/sp11-stick-wrapper.c:199-202). The wrapper checks M1RUN- and no '
            'more (docs/SP11-STICK-MONITOR.md section 14, D2): the rest of the label is the builder\'s rule and the operator\'s check, and an edited PIN.DAT carries any label.')


def other_differences(panel):
    """The sentence of section 3c on what else differs: from rung 2's guest the command line only."""
    return ('Other differences from the withheld guest follow from the panel\'s address, which lies below the bank: the guest\'s frame map and tables are wider (docs/SP11-STICK-REAL.md '
            'section 2: 46 chunks against 44 in the host run that table records). From rung 2\'s guest this one differs in the command line only, which is one page of boot_args: the '
            'same size and the same chunks, and its own SHA-256 (docs/SP11-STICK-REAL.md section 2).')


CONSOLE_OPEN = 'Console. What the operator will see, INFERRED and never run on this unit: XNU\'s own boot text'
CONSOLE_OPEN3 = 'Console. What the operator saw in boot 21 (OBSERVED once: boot 21, rung 2) and expects to see again (INFERRED): XNU\'s own boot text'
CONSOLE_EARLY = 'INFERRED from the stop-site analysis of boot 20, section 0)'
CONSOLE_EARLY3 = 'INFERRED; boot 21 had none, OBSERVED once)'


def boot21_text():
    """What boot 21 settled of the console and the screen, before the paragraphs of section 3c that were written before it."""
    return ('What boot 21 settled (OBSERVED once, rung 2, one unit, one boot; the operator\'s film): the kernel\'s text is on the panel. From the MON CALL line the panel was cleared and '
            'filled with the kernel\'s log over about 1.2 s, replayed from the start of its message buffer, as the public source says (below); the video block the kernel printed '
            '(initialize_screen: b=E4800000 w=B40 h=780 r=2D00 d=0) is the one the loader gave; the runner\'s page was cleared and the SNAPREP page was drawn on the right of the '
            'kernel\'s text after the stop, with the heartbeat going on; the MON CON lines were empty (serial=2). Where the paragraphs of this section say INFERRED of these, they were '
            'written before boot 21 and it has shown them once; the rest stays INFERRED.')


def word_text():
    """The paragraph of section 3c on the word itself: the exact line, the list, what XNU does with it (INFERRED) and what the A16 recorded (UNKNOWN which assertion)."""
    return (f'The extra word. The command line is the loader\'s with serial=2 and, after it, cpus=1 ({LINE}): one word, from the builder\'s list (--boot-arg: cpus=1 and no other word, '
            'BOOT_ARG_REFUSED for any other), for the panel as the console only, read back from boot_args by the builder (PANEL_MISMATCH for any other line, rung 2\'s included). What XNU '
            'does with it is read in the coordinator\'s design note (files/next-after-loop.md of this session, section 2: not in the repository), from the public XNU source (tag '
            'xnu-12377.121.6, not this kernel\'s own: macOS 27 is xnu-13432, whose source is not public) and not re-read here, so it is INFERRED for this kernel: cpus=N cuts the CPU '
            'topology before any CPU is registered and keeps the boot CPU; with '
            'one CPU the kernel registers one (the replayed early log then has only "processor 0 belongs ...": the film\'s check) and makes no PE_cpu_start_internal call, so there is no '
            'CPU-start write to the PMGR block and no "failed to boot" panic; the scheduler and '
            'the SMP start check are written for it; cpus= together with cpumask= makes the configuration incorrect (a later panic), so no record of this tool carries both. The '
            'kernelcache has the strings "cpus" (twice: one in the kernel\'s topology parser and one standalone in the CLPC kext, of unknown use: the design note) and "cpumask" (once), read '
            'on the host (a file, not a run); that this kernel parses them as the public source does stays INFERRED. What the A16 recorded is two sentences, "Reducing the topology with cpus=1 is '
            'not an option for this kernel: it trips a PMGR CPU-complex assertion earlier in boot" and "Whether the J700 guest tolerates a reduced or enlarged cpu set is UNKNOWN until '
            'X4 of the Surface Pro 11 plan" (docs/BOOT-CHAIN.md section 7): no log names the assertion, and whether the project\'s own PMGR and device-tree models rather than the kernel '
            'caused it is UNKNOWN; this '
            'unit runs the same tree, kernel and PMGR models, so a repeat is possible (INFERRED). The guest\'s device tree still lists six CPUs, which cpus=1 does not trim (UNKNOWN: '
            'whether PMGR or the interrupt controller objects). This record is EXPERIMENTAL, and a new operator decision: the first boot-arg that is not the loader\'s.')


def expect_text(run):
    """The paragraph of section 3c on what the run may end as, now that the CPU-start write is the stop to beat (the ends are those of the design note files/next-after-cpu.md)."""
    return ('What the run may end as (INFERRED from the design notes and boot 21: none of it has been seen, and which comes first, and when, is UNKNOWN). Honoured: the replayed log has '
            f'only {CPU_LINES} and the stop is not the CPU-start stop: MON STOP S2_FAULT with another A= (the next device that the monitor does not model: progress, and a new '
            f'question; boot 21\'s was A={page_hex(BOOT21_STOP["a"])} at {BOOT21_S} s) or MON STOP WATCHDOG at the end of the {run["box_s"]} s box with the kernel still running '
            f'(progress: the film shows how far it got). Not honoured: "processor 1..5" lines still printed and the CPU-start stop again, MON STOP S2_FAULT with {WINDOW} '
            '(UNKNOWN why: 26A428 does not honour the word, or a path of PMGR starts a CPU anyway). A panic: an ApplePMGR or CLPC assertion, or another panic text on the panel, with MON '
            'STOP PANIC_ENTRY or GUEST_EXC, or only WATCHDOG, or an S2_FAULT at a reset register (the text is the result; it may not reach the panel before the monitor stops the guest: '
            'UNKNOWN). Another early end: a stall earlier than boot 21\'s last log line, or an S2_FAULT before the CPU point. A restart of the Surface: NOT A RESULT for M1 (section 5a), '
            'NOT PASSED for rung 3 (section 5d). In every '
            'case the operator does nothing but film (section 5d).')


def results_paragraphs(run, wait_s):
    """The results of rung 3, one paragraph each, in the operator's words: M1's verdict is section 5a's; the named results are in terms of the replayed log's processor lines, the
    report's MON STOP and the panel, which the coordinator reads from the film of either camera and compares with boot 21's frames."""
    return (
        'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 3\'s, with one more condition: the panel did not end the run.',
        f'RUNG 3 PASSED: {CPU_LINES} is the only line of that kind in the replayed log, the report\'s MON STOP is not an S2_FAULT with {WINDOW}, there is no panic text on the panel, the '
        'log reaches boot 21\'s last line or goes beyond it, and the stop is a later device fault (MON STOP S2_FAULT at another address, A= not 0, outside the panel range) or the box end '
        '(MON STOP WATCHDOG, the kernel still running), on a clean report (none of MON REGS RESTORED=NO, WINDOW OVERRUN, STACK FULL or GIC RESTORE CLEAN=NO is on the page).',
        'RUNG 3 NOT PASSED, (a): "processor 1..5" lines still printed and boot 21\'s stop again: 26A428 did not honour cpus=1 (the falsifier of section 0; UNKNOWN why).',
        'RUNG 3 PANIC TEXT, (b): an ApplePMGR or CLPC assertion, or another panic, is on the panel (the page may say only MON STOP UNNAMED, WATCHDOG, an S2_FAULT at a reset register, '
        'PANIC_ENTRY or GUEST_EXC): the text is the result, whatever else is on the page. It is neither a pass nor a failure of the record: film it and hand it back.',
        'RUNG 3 NOT PASSED, (c): a stall earlier than boot 21\'s last log line (MON STOP WATCHDOG with the log ending sooner), an S2_FAULT before the CPU point, a MON STOP S2_FAULT with '
        f'A= inside the panel range (section 2), the panel still dark {wait_s} seconds after the MON CALL line, a restart of the Surface before the report, no report (a page that stops at '
        'MON CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1), or any other end that none of the results above names, for example MON STOP UNNAMED: the coordinator reads '
        'it first.',
        f'RUNG 3 PARTIAL: only {CPU_LINES} in the log and still the CPU-start stop: cpus=1 had an effect and did not prevent the write.',
        'RUNG 3 NOT A RESULT: a film that does not let the coordinator read MON STOP or count the processor lines of the replayed log (too soft, too dark, or the report unreadable under '
        'the kernel\'s text): nothing is concluded from it, and the one-boot rule of section 5c says whether the approval is spent.',
        'A run that ends as boot 21 did is still M1 PASSED (section 5a) and rung 3 not passed: the two verdicts are separate.')


def section_5d(panel, wait_s):
    """Section 5d of the record of rung 3, after 5c: what is new, what to film, what to expect, what the film decides, what a panic looks like and what counts as a result, in the
    operator's words. It is shorter than rung 2's: the operator knows the panel (at most 60 lines). `wait_s` is the silence rule's wait for the box."""
    s = BOOT21_STOP
    new = (f'WHAT IS NEW. One word: the guest\'s command line has cpus=1 after rung 2\'s (boot 21): {LINE}. It asks XNU to run with one CPU, so that only processor 0 exists and the '
           'kernel does not make the write that stopped boot 21, INFERRED to be a CPU-start write (section 3c). Everything else is rung 2\'s: the same programs (SNAPMON-M1.BIN and SNAPREP-M1.BIN '
           'have the SHA-256 that boots 20 and 21 ran), the 5 s box, the panel, serial=2, one CPU, AC power, the same three store classes and the rules of sections 0b, 4 and 5. '
           'Nothing is added to the monitor, the wrapper or the reporter.')
    film = ('THE FILM is rung 2\'s, from the Shell\'s countdown until 60 seconds after the SNAPREP report page has appeared: the whole panel with the main camera, 30 frames a '
            'second or more, steady, and if there is a second camera the TOP-LEFT quarter close up (step 2 of section 5c). It must show the AUTORUN lines, PAYLOAD CALL 0 (only the '
            'film shows it once the kernel has cleared the panel: if the film does not show it, the approval is spent), the kernel\'s log while the guest runs, and the report with MON '
            'STOP and MON EXIT, whose PC= and A= decide the result. Lowercase letters on the panel are the kernel\'s (boot 21: OBSERVED once). A photograph taken afterwards is not enough.')
    expect = ('WHAT TO EXPECT. As in boot 21: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the '
              'guest has stopped (boot 21: about 2 s after the MON CALL line). The operator does nothing but film. '
              f'{r2.force_off(wait_s)}. {r2.COUNT.format(wait=wait_s)}')
    decides = ('WHAT THE FILM DECIDES (the effect of cpus=1 is INFERRED and has not been seen; boot 21 is the comparison, OBSERVED once). First the kernel\'s log, which the first frames '
               f'after the MON CALL line replay from its start: boot 21\'s had {LOG_CPU0} early and, later, a line {LOG_CPUN} for each of the processors 1 to 5 ({LOG_T}); with cpus=1 only '
               f'the first is expected. Then the report page: boot 21\'s stop was MON STOP S2_FAULT A={page_hex(s["a"])} B={page_hex(s["b"])} at {BOOT21_S} s, and the CPU-start stop is a MON STOP '
               f'S2_FAULT with {WINDOW}; an S2_FAULT at another address, or a run longer than boot 21\'s, is progress. Boot 21\'s log ended in platform-driver lines: the coordinator compares '
               'the film with its frames. NOT THE FAILURE: the noise lines of section 0a (OBSERVED once in boot 21; INFERRED to come from the monitor\'s empty models).')
    panic = ('WHAT A PANIC LOOKS LIKE (INFERRED from XNU\'s panic format; none has been seen on this unit): a line that starts "panic(cpu" and a caller address, the panic string (an '
             'assertion names its file and line), "Debugger message: panic", more lines and maybe a backtrace; the word "panic" alone is not one. The monitor answers the panic entry '
             'and lets the guest run on; it stops at the first later call that it does not implement, which can come before the text is printed (read from probes/xnuboot/sptm_dispatch.c, '
             'INFERRED): the report then says MON STOP UNNAMED or, if no such call comes, MON STOP WATCHDOG, or an S2_FAULT at a reset register, and the panel shows what was printed '
             'before it. Either way, film it.')
    return ('5d. Rung 3: what is new, what to film, and what counts as a result (the panel as the console, with cpus=1)',
            *(line for text in (new, film, expect, decides, panic, *results_paragraphs(panel['run'], wait_s)) for line in m1t.wrap(text, 3, 6)))
