# SPDX-License-Identifier: MIT
"""Rung 4 of the monitor entry run: rung 3 (the panel as the kernel's console with `cpus=1`) with one word of the monitor's own parameters set, `let_panic`
(docs/SP11-STICK-MONITOR.md section 20, K12; `--let-panic` of tools/sp11_stick_bundle_build.py). Boot 23 (rung 3) honoured cpus=1 and ran 2.166 s into the storage drivers'
probes, and the monitor stopped it at XNU's own debugger-trap word, which the kernel executes when it panics: its text is printed after that trap, which the monitor
intercepted, so the panel showed none. let_panic is PARAMS word 23 (offset 0xB8), data that the monitor already reads at one place (probes/xnuboot/trap_dispatch.c:370-387): a
guest EL1 exception that reaches that branch runs XNU's own handler instead of ending the run. This record changes that one word in GUEST.DAT (and PIN.DAT's label and
digests): the programs, the box, the panel and the command line are rung 3's (tools/sp11_stick_bundle_panel_rung3.py has rung 3's words, which this module reuses
where they still hold, and tools/sp11_stick_bundle_panel_rung2.py the words that all three share).

What is here is what the record says of this rung and the guide's table PANEL takes by key (`console+cpus=1+let_panic`, tools/sp11_stick_bundle_panel.py GUIDE_WORDS): why rung
4 (section 0), what boots 21 and 23 showed (section 0a), the hazard of the word, the evidence of section 3c that is not rung 3's (the summary, the introduction, the label, the
word, what it does and still stops at, what the run may end as) and section 5d, in the operator's words: what is new, what to film, what the film decides and what counts as
a result. Boot 23's numbers are the operator's film, read at full resolution by the coordinator (OBSERVED, one unit, one boot); the analysis of its stop (files/stopsite-r3.md of
the session) was read on the host from the kernelcache file and the public XNU source, without a run, and is INFERRED where it says so; what the let_panic branch does is read
in the monitor's source and what XNU does then is INFERRED.

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402

BOOT23_RECORD = '749361cf...a1a7'       # the approved record of rung 3: SHA-256 749361cf153a8990f8da870c9e44fd86da13a5cb28789c7af5c369079329a1a7
BOOT23_TICKS = 41_593_665               # MON RET TICKS= of boot 23, in ticks of 19.2 MHz (OBSERVED once)
BOOT23_S = f'{BOOT23_TICKS / 19.2e6:.3f}'
BOOT23_STOP = dict(pc=0xFFFFFE000B8C2288, a=0x2000000, b=0xFFFFFEA937FDC000)      # MON STOP GUEST_EXC PC= A= B= of boot 23 (OBSERVED once); C= lost its last two digits
BOOT23_C = '0XFFFFFE000B8C22'           # what the page printed of C=: the runner clips the line at 100 columns
BOOT23_EXIT = 'MON EXIT PC=0XFFFFFE000B86C000 SPSR=0X604003C5 ESR=0X8200000F FAR=0X0'
BOOT23_GUEST = 'A7E159AD6C18832C'       # rung 3's GUEST.DAT as the runner prints its first 16 digits (PAYLOAD LOADED INDEX=2)
BOOT23_PIN = '339D64BE6D857808'         # rung 3's PIN.DAT as the runner prints its first 16 digits (PAYLOAD LOADED INDEX=3)
BOOT23_LABEL = 'M1RUN-CPUS1-RUNG3'
STOP23 = f'MON STOP GUEST_EXC PC={r2.pc_text(BOOT23_STOP["pc"])} A={r3.page_hex(BOOT23_STOP["a"])}'
LET_PANIC_AT = '0xB8'                   # offset of word 23 of struct params (probes/xnuboot/report_layout.c:23)
LINE = r3.LINE                          # the command line is rung 3's, word for word
BOX = 5
HEADER = ('3c. The panel as the kernel\'s console with cpus=1 and let_panic (rung 4: D9 panel handed, serial=2, cpus=1, let_panic set, stock kernel; '
          'docs/SP11-STICK-MONITOR.md section 20)')
RUNG = ('rung 1, the first run with the panel withheld, rung 2, the panel as the console (boot 21), and rung 3, the same with cpus=1 (boot 23), whose records have their own approvals, '
        'spent: this record is rung 4, the panel handed to the guest as the kernel\'s console (serial=2) with cpus=1 in its command line and the monitor\'s let_panic word set, and no '
        'approval covers another\'s boot')
DECIDES = ('APPROVING THIS TEXT ALSO DECIDES A CHOICE OF D9 THAT IS NOT DECIDED YET: the guest is handed the unit\'s panel as the kernel\'s console (serial=2), with the kernel '
           'stock, cpus=1 in its command line as in rung 3, and one word of the monitor\'s own parameters set that no run has set, let_panic (section 3c). The first run withheld '
           'the panel, rung 2 had no extra word and rung 3 had cpus=1 only.')
CONSOLE = ('The guest is given the panel (section 3c) and its command line is serial=2 with cpus=1, and its let_panic word is set: its console is the panel (boot 21, rung 2: the '
           'kernel\'s log was on it, OBSERVED once) and the MON CON lines are not expected to carry it (serial=2: boot 21 had none, OBSERVED once); boot 23 honoured cpus=1 '
           '(OBSERVED once), and what let_panic does to this run is INFERRED and has not been seen.')
RAN = 'ran three times on this machine (boots 20, 21 and 23: section 0a).'
STOP_SEEN = {**r3.STOP_SEEN, 'GUEST_EXC': 'OBSERVED once (boot 23, one unit, one boot).',
             'PANIC_ENTRY': 'Never seen (UNKNOWN). With serial=2 the panic text is expected on the panel, not in the MON CON lines (INFERRED: whether kprintf text reaches them is UNKNOWN).'}
STAND_HEAD = '0a. Where things stand (boots 20, 21, 23; 2026-11-18; one unit, one boot each; OBSERVED unless a sentence says INFERRED)'
STEP6 = (r3.STEP6 + ' Before that FORCE OFF, and after those 60 seconds, take three sharp still photographs of the part of the panel left of the report page (about its left 55 percent), '
         'close and without glare, one of its top band, one of its middle and one of its bottom, so that the text on it can be read (asked, not required; section 5d).')
PARAMS_ROW = ('1 (word 23 of struct params, offset 0xB8: probes/xnuboot/report_layout.c:23); every other PARAMS word is rung 3\'s: the builder compares the finished PARAMS chunk with the one it '
              'makes for rung 3 and refuses a difference elsewhere (PARAMS_LAUNCH), and holds permissive and skip_undef at 0')
# What section 3c and the sections after it cite, by path:line (held to the sources, in both directions, by tools/test-sp11-stick-monitor-panic.py) and, by heading, in the documents.
CITES = (
    ('probes/xnuboot/report_layout.c', 23, 23, ('uint64_t let_panic;', 'let XNU run its own exception handler and print')),
    ('probes/xnuboot/report_layout.c', 177, 177, ('#define EV_PANIC_STATE_FAILED 43', 'a = x16, b = panic owner, c = CPU')),
    ('probes/xnuboot.py', 1806, 1806, ("words.append(1 if built.get('let_panic') else 0)",)),
    ('probes/xnuboot/trap_dispatch.c', 370, 387, ('p->permissive || p->let_panic', 'vcpu_of(c)->fault_esr = 0;', 'guard_open(c, g);', 'return 0;', 'return 1;')),
    ('probes/xnuboot/trap_dispatch.c', 472, 472, ('record(c, EV_S2_FAULT, *elr, ipa, esr, far);',)),
    ('probes/xnuboot/sptm_dispatch.c', 35, 37, ('#ifndef SNAP_PANIC_SERVE', '#define SNAP_PANIC_SERVE 0')),
    ('probes/xnuboot/sptm_dispatch.c', 59, 67, ('sptm_panic_dispatch(&c->panic_state', 'record(c,EV_PANIC_STATE_FAILED', 'return 0;')),
    ('probes/xnuboot/sptm_dispatch.c', 117, 194, ('if (cmd == SPTM_CMD_PANIC_ENTRY) {', 'sptm_panic_enter(&c->panic_state', 'record(c, EV_PANIC_ENTRY', 'return 1;')),
    ('compat/panic-state.c', 24, 30, ('int sptm_panic_dispatch(', 'x16==UINT64_C(0x0200000000000000))return 1;', 'return 0;')),
    ('probes/xnuboot/async.c', 144, 157, ('which == ASYNC_SERROR', 'record(c, EV_SERROR, elr, hyp_esr, esr, el1);')),
    ('probes/sp11-stick-monitor.c', 118, 139, ('static unsigned mon_params(struct sw_mon *m)', 'r->box_s = sp11sb_rd64(p + SM_PARAMS_WATCHDOG);', 'r->gpu_flags = sp11sb_rd64(p + SM_PARAMS_GPU_FLAGS);')),
    ('probes/sp11-stick-reporter.c', 247, 271, ('case 6: return "WATCHDOG";', 'case 9: return "PANIC_ENTRY";', 'default: return "UNNAMED";')),
)
DOC_CITES = (*r3.DOC_CITES,
             ('docs/APPLE-ENCODINGS.md, section "The debugger traps"', 'docs/APPLE-ENCODINGS.md', '## The debugger traps',
              ('`TRAP_DEBUGGER`', 'a deliberately undefined word', 'delivers it to XNU\'s vector and reads the panic record back after a run.')),
             ('docs/APERTURE-FAULT.md, section "After `MAP_PAGE`"', 'docs/APERTURE-FAULT.md', '## After `MAP_PAGE`',
              ('it is XNU\'s `TRAP_DEBUGGER`, executed on purpose after deciding to panic,', 'Reading the panic record after')),
             ('docs/RUNNING.md, section "Run flags"', 'docs/RUNNING.md', '## Run flags', ('`--let-panic`, `--panic-serve`', '--panic-serve --root-miss-fast')),
             ('docs/KERNEL-PATCHES.md, section "Not kernel patches"', 'docs/KERNEL-PATCHES.md', '## Not kernel patches',
              ('(`SNAP_PANIC_SERVE`): after XNU\'s panic entry the monitor', 'keeps serving ordinary calls so the panic string is printed.')))


def hazard(real, box_s, wait_s):
    """The panel's bullet of 0b for this record: the console on the panel is boots 21's and 23's, and let_panic sends the kernel down its own panic path."""
    return ('The guest can write the unit\'s framebuffer, which the display scans out, and the kernel draws its console there (OBSERVED once: boots 21 and 23): from the MON CALL line the '
            'panel is cleared and fills with the kernel\'s log (the kernel\'s console clears the whole panel when it takes it: public source, not this kernel\'s own), and its text '
            'can paint over the page, so that the report can be hard to read. The film is the only record of that text and of the lines printed before the guest ran (section 5d). '
            'let_panic lets the kernel go down a path that no run on this unit has taken, its own panic handler (INFERRED to be the path that boot 23 stopped at): it may print its '
            'text, then spin, halt or stop at a call or a device that the monitor has no model for, and the monitor ends the run at the box or at its own stop either way; the text may '
            'scroll and is on the film only (section 5d). The monitor reads the let_panic word while the guest runs, like the PARAMS words of the last bullet below, but it is 1 here: '
            'a guest that wrote it could only change whether an exception stops the run (INFERRED: section 3c). '
            f'{r2.force_off(wait_s)}.')


def why_lines(real):
    """Section 0's paragraph for this rung: why rung 4, the one change, the prediction and what falsifies it. Boot 23's facts are section 0a's (stand_extra)."""
    run = real.get('panel_facts', {}).get('run')
    programs = '' if run is None else (' The command line, the programs and the time box are rung 3\'s (section 3c).' if not r2.differs(run) else
                                       f' The programs and the box are NOT all rung 3\'s: {"; ".join(r2.differs(run))}. Section 3c and section 4 say what that adds.')
    return m1t.wrap(f'WHY RUNG 4. Boot 23 (rung 3) honoured cpus=1 and the kernel ran {BOOT23_S} s, through the platform drivers into the storage drivers\' probes (OBSERVED once); the '
                    f'monitor then stopped it as {STOP23} (section 0a). The host analysis of that stop (INFERRED; read on the host, a file and not a run) says that it is XNU\'s own '
                    'debugger-trap word, which the kernel executes on purpose when it panics: so the kernel most likely panicked, the cause is UNKNOWN, and its text is printed only after '
                    'that trap, which the monitor stopped at, so that the panel showed none. THIS RECORD CHANGES ONE THING IN THE GUEST\'S INPUTS: the monitor\'s PARAMS word let_panic (word '
                    '23, offset 0xB8) is 1 where rung 3\'s is 0. It is data that the monitor already reads, at one place (section 3c): a guest EL1 exception that reaches that branch runs '
                    'XNU\'s own handler instead of ending the run.' + programs + ' PREDICTION (INFERRED): XNU\'s own panic text appears on the panel, and the run ends later. FALSIFIER: '
                    f'{STOP23} again and no text: the word was not honoured, and rung 4 is NOT PASSED. Section 3c has the other ends and section 5d what the film decides.')


def stand_extra(real):
    """The paragraphs that section 0a gets for this record (its `stand` words): what boots 21 and 23 showed, with their labels."""
    return (*m1t.wrap(f'Boot 21 (rung 2, record {r3.BOOT21_RECORD}; one unit, one boot; the operator\'s film, read at full resolution) ran the monitor entry with the panel as the kernel\'s '
                      f'console: the call returned after {r3.BOOT21_S} s and the run ended as MON STOP S2_FAULT A={r3.page_hex(r3.BOOT21_STOP["a"])}, INFERRED to be the kernel\'s first '
                      'CPU-start write (compat/boot-relocate.h:147-153). The panel showed the kernel\'s own boot log from its start, with a processor line for each of the six CPUs of the '
                      f'guest\'s tree; no MON CON line (serial=2). The log is noisy, and the noise is not the failure: {r3.NOISE} were on the film (OBSERVED once: boot 21); that they '
                      'come from the monitor\'s empty models of those devices is INFERRED. Rung 2 PASSED.'),
            *m1t.wrap(f'Boot 23 (rung 3, record {BOOT23_RECORD}; one unit, one boot; the operator\'s film, read at full resolution) ran the same programs with cpus=1 added to the command '
                      'line. cpus=1 was honoured: the replayed log has "processor 0" and no line for the processors 1 to 5, and its log-queue line went from 54 slots to 9, which fits one '
                      'CPU (OBSERVED once; the fit is INFERRED). The CPU-start stop is gone. The kernel ran '
                      f'{BOOT23_S} s (TICKS={BOOT23_TICKS}) through the platform drivers into the storage drivers\' probes, past boot 21\'s last log line, and the call ended as '
                      f'{STOP23} B={r2.pc_text(BOOT23_STOP["b"])} C={BOOT23_C} and two digits that the page does '
                      'not show (the runner clips the line at 100 columns, and nothing else kept them), with MON REGS RESTORED=YES and GIC RESTORE CLEAN=YES, and '
                      f'{BOOT23_EXIT}. No panic text was on the film (OBSERVED once). M1 PASSED by section 5a; rung 3\'s own verdict was NOT PASSED, by the letter of its section 5d: an '
                      'end that none of its named results covered.'),
            *m1t.wrap('What that stop is (INFERRED; read on the host, from the kernelcache file and the public XNU source, a file and not a run; no kernelcache bytes are quoted): A= is '
                      'ESR_EL1 0x02000000, exception class 0, an unallocated instruction taken at EL1; of the 64 words around the last call\'s return address (x30, which the page prints as '
                      'PC=) exactly one is unallocated, and it is XNU\'s own debugger-trap word, which the kernel executes on purpose in its debugger funnel when it panics or enters the '
                      'debugger: not a missing CPU feature, not an Apple extension. So the kernel most likely PANICKED (a stackshot or profiling entry is less likely), the cause is UNKNOWN, '
                      'and its text is printed only after that trap, which the monitor stopped at: the panel showed none. The trap word\'s own address is INFERRED (the page lost two '
                      'digits).'),
            *m1t.wrap('The stick after boot 23 was unchanged but for the Shell\'s marker and the Mac\'s own .fseventsd entry, and REPORT.DAT kept its all-zero staged digest: a run does not '
                      'write the report back, so the page on the panel is its only record (OBSERVED once each: boots 20, 21 and 23).'))


def in_short(panel):
    """The summary of section 3c."""
    return ('In short. This record is rung 4: rung 3 (boot 23) again with one word of the monitor\'s PARAMS changed, let_panic, from 0 to 1, so that a guest EL1 exception that the '
            'monitor would stop at runs XNU\'s own handler. Why: boot 23 stopped at XNU\'s own debugger-trap word, which section 0a INFERS to be a panic whose text the monitor kept '
            'from the panel. Expected, INFERRED: XNU\'s panic text is on the panel, and the run ends later, at a call or a device that the monitor does not model, or at the box. The '
            'programs, the box, the panel, serial=2 and cpus=1 are rung 3\'s. What to film and what the film decides are in section 5d, in the operator\'s words.')


def intro(panel):
    """The introduction of section 3c: rung 3 is the run before, this record changes one PARAMS word and nothing else."""
    diff = r2.differs(panel['run'])
    shared = ('The word needs no change of the monitor, the wrapper or the reporter (it is data in the PARAMS chunk that GUEST.DAT carries, which the monitor already reads; the '
              'wrapper\'s own check reads three other words: probes/sp11-stick-monitor.c:118-139), and this record changes none of them: SNAPMON-M1.BIN and SNAPREP-M1.BIN have the '
              f'SHA-256 that boots 20, 21 and 23 ran ({r2.RUNG1_MONITOR[:8]}... and {r2.RUNG1_REPORTER[:8]}..., in full in section 1), the time box is {r2.RUNG1_BOX_S} s and the store '
              'classes are rung 3\'s. Only GUEST.DAT (eight bytes of its PARAMS chunk) and PIN.DAT (the label and the digests) differ from rung 3\'s, and PAYLOAD.MAN, which lists '
              'them; everything of section 1b is the first run\'s. Section 5 has the rule that rung 3 had.'
              if not diff else
              f'The word needs no change of the monitor, the wrapper or the reporter, but this record is NOT rung 3 with the PARAMS word changed alone: {"; ".join(diff)}. Section 0 and '
              'section 4 say what that adds; everything of section 1b is the first run\'s. Section 5 has the rule that rung 3 had.')
    return (f'Rung 3 is the run before this one: the panel handed as the kernel\'s console (serial=2) with cpus=1, record {BOOT23_RECORD}, boot 23 (section 0a); its approval is spent. '
            'This record is rung 4, rung 3 with one input of the guest changed: PARAMS word 23, let_panic, is 1 where rung 3\'s is 0. It is not decided: approving the SHA-256 of this '
            'record is that decision, and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); rung 3\'s approval covers none of it. '
            f'{shared} At run time the guest differs from rung 3\'s in one thing: its own exception handler runs where the monitor would have stopped the run (INFERRED: section 3c).')


def label(panel, label_text):
    """The label paragraph of section 3c."""
    return (f'Label: PIN.DAT\'s build_label is {label_text}. The builder puts LETPANIC- after M1RUN- for this record only, in place of CPUS1- (rung 3\'s), and refuses a facts label that '
            'starts with it, with CPUS1-, CONSOLE- or PANEL-, so that the labels of the other choices cannot read as this one (PIN.DAT\'s label holds 23 characters); the page prints it '
            'on its SNAPMON STAGE=PIN_OK line (LABEL=; probes/sp11-stick-wrapper.c:199-202). The wrapper checks M1RUN- and no more (docs/SP11-STICK-MONITOR.md section 14, D2): the rest '
            'of the label is the builder\'s rule and the operator\'s check, and an edited PIN.DAT carries any label.')


def other_differences(panel):
    """The sentence of section 3c on what else differs: from rung 3's guest one PARAMS word."""
    return ('From rung 3\'s guest this one differs in one word of the PARAMS chunk only (let_panic, eight bytes at offset 0xB8): the same size and the same chunks, and its own SHA-256 '
            '(docs/SP11-STICK-REAL.md section 2).')


def word_text():
    """The paragraph of section 3c on the command line: rung 3's word, which boot 23 honoured."""
    return (f'The words of the command line. The line is the loader\'s with serial=2 and, after it, cpus=1 ({LINE}), rung 3\'s exactly: one word, from the builder\'s list (--boot-arg: '
            'cpus=1 and no other word, BOOT_ARG_REFUSED for any other), read back from boot_args by the builder (PANEL_MISMATCH for any other line). Boot 23 honoured it (OBSERVED once: '
            'section 0a). Whether the panic that boot 23 stopped at is the PMGR CPU-complex assertion that the A16 recorded with cpus=1 (docs/BOOT-CHAIN.md section 7) is UNKNOWN: no log '
            'names that assertion, the kernel ran 2.166 s first, and the film showed no text.')


def extra_paragraphs(panel):
    """The paragraphs of section 3c that only this record has, after the command line's: the word, what it does, what still stops the run, the panic path and what the page can show."""
    if not panel.get('let_panic'):
        return ()
    return (
        'The PARAMS word. let_panic is word 23 of struct params, at offset 0xB8 (probes/xnuboot/report_layout.c:23); the loader writes 1 there for a build that sets it '
        '(probes/xnuboot.py:1806), and the A16\'s runner sets it for its own runs (--let-panic: docs/RUNNING.md, section "Run flags"). It is an existing diagnostic of the monitor, '
        'EXPERIMENTAL, used here for the first time on the Surface Pro 11, and not a model of anything. The monitor reads it at one place only (read on the host in probes/, compat/ '
        'and shadow/, a search of the sources and not a run): when the guest takes a synchronous exception at EL1 the monitor records it and stops the run unless the exception is one '
        'that it lets through, a kernel page fault, a user-mode exception, or any exception when permissive or let_panic is set; it then clears the stop syndrome, opens the vector '
        'page again and returns to the guest, whose own handler runs (probes/xnuboot/trap_dispatch.c:370-387; what XNU then does is INFERRED). The builder sets the word for this '
        'record only (--let-panic: LET_PANIC_REFUSED for any other combination), holds permissive and skip_undef at 0 for every record of this kind, and compares the finished PARAMS '
        'chunk with the one it makes for rung 3: they differ in this word and in no other (PARAMS_LAUNCH).',
        'What still stops the run (read on the host, in the sources named; whether the guest reaches each is INFERRED): a monitor, SPTM or TXM call that the monitor does not implement '
        'and, once XNU\'s panic-entry call has been answered, every other call (compat/panic-state.c:24-30 and probes/xnuboot/sptm_dispatch.c:59-67; SNAP_PANIC_SERVE, which would serve '
        'them, is off: probes/xnuboot/sptm_dispatch.c:35-37 and the profile of the M1 monitor), which the page prints as MON STOP MONITOR or DISPATCH_CMD for a call with no '
        'implementation and as MON STOP UNNAMED for a call refused after the panic entry (A= its x16, B= the panicking CPU, C= this CPU: probes/xnuboot/report_layout.c:177; '
        'probes/sp11-stick-reporter.c:247-271); others end the run too (ASYNC, MMIO_STOP, DEVICE_RESET, OTHER: the page names each); a '
        'stage-2 fault that no model answers, MON STOP S2_FAULT with the address in A= (probes/xnuboot/trap_dispatch.c:472); an SError at EL2, MON STOP SERROR '
        '(probes/xnuboot/async.c:144-157); and the monitor\'s own timer at the box end, MON STOP WATCHDOG (OBSERVED once: boot 20).',
        'What the panic path may reach (INFERRED; none of it has run on this unit): XNU draws its panic text with the video console into the panel range, after its panic-entry '
        'call and one more SPTM call that asks for the panicking CPU (public source, tag xnu-12377.121.6, not this kernel\'s xnu-13432: made before the trap on the panic() funnel '
        'and again after it); the first is one that the monitor answers by recording it and returning to the guest (probes/xnuboot/sptm_dispatch.c:117-194), whether the second is a '
        'monitor call that this monitor refuses is UNKNOWN. What follows is the kernel\'s panic-log, NVRAM and halt or restart path (the stop-site analysis, INFERRED): each device access on it goes through the '
        'same stage-2 map and models as every access of the run, so an access that has no model is a stage-2 fault and ends the run. The A16\'s desktop configuration sets let_panic '
        'with --panic-serve, which serves calls after the panic entry (docs/RUNNING.md, section "Run flags"; docs/KERNEL-PATCHES.md, section "Not kernel patches"), and its runs read '
        'the panic record back (docs/APERTURE-FAULT.md, section "After `MAP_PAGE`"; docs/APPLE-ENCODINGS.md, section "The debugger traps"): this monitor does not serve after the entry '
        'and nothing here prints that record, so the film is the only reading. The documents of this tree name no device of the A16\'s panic or halt path (UNKNOWN which it touched).',
        'What the page can and cannot show. It prints the first five events of the monitor\'s log, the counts and the stop line, which the runner clips at 100 columns: C= may lose its last '
        'digits, as in boot 23. It does not print the panic text or the number of exceptions that the monitor let through (they are in the RAM window and in no line of this reporter: '
        'UNKNOWN to the page and the film).')


def expect_text(run):
    """The paragraph of section 3c on what the run may end as, now that the exception that boot 23 stopped at is given to the kernel's own handler."""
    return ('What the run may end as (INFERRED from the host analysis and boot 23: none of it has been seen with let_panic, and which comes first, and when, is UNKNOWN). Honoured: XNU\'s '
            f'panic text is on the panel (the text is the result) and the run ends later than boot 23\'s {BOOT23_S} s: at the box end (MON STOP WATCHDOG, after {run["box_s"]} s, the kernel '
            'in its halt or spin), at the first call that the monitor refuses after the panic entry (MON STOP UNNAMED) or at a device that the panic path touches (MON STOP S2_FAULT '
            f'with another A=, perhaps at a reset or power register). Not honoured: {STOP23} again and no text. Otherwise: no text and a later stop, or none before the box end (the panic '
            'path does not draw on the panel: UNKNOWN), or another panic or an earlier stop, if the kernel\'s own handler runs code that the monitor does not model: the film shows how '
            'far the log went. A restart of the Surface: NOT A RESULT for M1 (section 5a); section 5d says what it is for rung 4. In every case the operator does nothing but film '
            '(section 5d).')


def results_paragraphs(run, wait_s):
    """The results of rung 4, one paragraph each, in the operator's words, read in this order from the film of either camera and the stills: the first class that fits is the film's."""
    return (
        'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 4\'s. The panel is read first and then the stop; the classes below are read in this order, and a film '
        'is in the first that fits.',
        'RUNG 4 PANIC TEXT: the panel shows the kernel\'s panic text (above), whatever the page says and whatever the stop is. THE TEXT IS THE RESULT: the coordinator transcribes its first '
        'lines verbatim from the film and the stills and hands them back. It is neither a pass nor a failure of the record; M1\'s verdict is separate and is section 5a\'s.',
        'RUNG 4 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP (too soft, too dark, the report unreadable under the kernel\'s text, the '
        'text too small to read): nothing is concluded from it, and the one-boot rule of section 5c says whether the approval is spent.',
        f'RUNG 4 NOT PASSED, (a): no panic text and {STOP23} again, boot 23\'s stop: the word was not honoured, or the monitor stopped at this exception for a reason that this record '
        'does not know. It is a finding about the PARAMS word or the monitor, not about the kernel.',
        f'RUNG 4 WATCHDOG, NO TEXT: MON STOP WATCHDOG at the end of the {run["box_s"]} s box and no panic text on the panel: the kernel was still running (its log may still be growing) or '
        'spinning, with no panic text (UNKNOWN which; INFERRED: the panic path may not draw on the panel). The last lines of the log say how far it went.',
        'RUNG 4 NEW STOP: no panic text and a MON STOP of another kind (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, DEVICE_RESET or another name) or of the kind GUEST_EXC at another PC= or '
        'A=: its kind, PC=, A= and the last line of the log are the result, and the coordinator reads them first. An S2_FAULT with A= inside the panel range (section 2) is not M1\'s '
        'expected end (section 5a).',
        f'RUNG 4 NOT PASSED, (b): a restart of the Surface before the report, no report (a page that stops at MON CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1), the '
        f'panel still dark {wait_s} seconds after the MON CALL line, or any other end that none of the results above names, and no panic text: a restart is NOT A RESULT for M1 (section '
        '5a) and NOT PASSED for rung 4, which has not reached its aim.',
        'A run that ends as boot 23 did is still M1 PASSED (section 5a) and rung 4 not passed: the two verdicts are separate.')


def section_5d(panel, wait_s):
    """Section 5d of the record of rung 4, after 5c: what is new, what to film, what to expect, what the film decides, what a panic looks like and what counts as a result, in the
    operator's words. `wait_s` is the silence rule's wait for the box."""
    new = (f'WHAT IS NEW. One input of the guest: the monitor\'s PARAMS word let_panic is 1 where rung 3\'s was 0 (section 3c); the command line is rung 3\'s ({LINE}). It lets the kernel\'s '
           'own exception handler run where the monitor stopped boot 23, so that the kernel prints its panic text on the panel (INFERRED: section 3c). Everything else is rung 3\'s: the '
           'same programs (SNAPMON-M1.BIN and SNAPREP-M1.BIN have the SHA-256 that boots 20, 21 and 23 ran), the 5 s box, the panel, serial=2, one CPU, AC power, the same three store '
           'classes and the rules of sections 0b, 4 and 5. Nothing is added to the monitor, the wrapper or the reporter.')
    film = ('THE FILM is rung 3\'s, from the Shell\'s countdown until 60 seconds after the SNAPREP report page has appeared: the whole panel with the main camera, 30 frames a second or '
            'more, steady, and if there is a second camera the TOP-LEFT quarter close up (step 2 of section 5c). It must show the AUTORUN lines, PAYLOAD CALL 0 (only the film shows it '
            'once the kernel has cleared the panel: if the film does not show it, the approval is spent), the kernel\'s log while the guest runs, and the report with MON STOP and MON EXIT. '
            'THE PANIC TEXT, if there is one, is the result and may scroll off the panel: nobody touches the keyboard, the screen or the buttons while it runs (except FORCE OFF as step 5 '
            'of section 5c says), and the film is its record. The console scrolls, so the text can be anywhere in the part of the panel left of the report page (about its left 55 '
            'percent) (INFERRED). After those 60 seconds, and BEFORE FORCE OFF (only when the SNAPREP page is on the screen; if it is not, FORCE OFF '
            f'{wait_s} seconds after the MON CALL line as step 5 says, no stills first: D8\'s 80 s bound), take three sharp still photographs of the part of the panel left of the '
            'report page (about its left 55 percent), close and without glare, one of its top band, one of its middle and one of its bottom, with the text readable (asked, not '
            'required, and valuable). The panel is the only place that text can be read: the MON CON lines are not expected to carry it (INFERRED: serial=2; whether kprintf text reaches them is '
            'UNKNOWN). Lowercase letters on the panel are the kernel\'s (boots 21 and 23: OBSERVED once each).')
    expect = ('WHAT TO EXPECT. As in boots 21 and 23: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when '
              'the guest has stopped (boot 23: about 2.2 s after the MON CALL line; with let_panic the run may last up to the 5 s box, so the page can come later). The panic text, if '
              'there is one, comes after the last log lines of boot 23\'s kind (the storage drivers\'): how long it takes is UNKNOWN; INFERRED, it is printed inside the run, which the '
              f'monitor ends at the box at the latest. The operator does nothing but film. {r2.force_off(wait_s)}. {r2.COUNT.format(wait=wait_s)}')
    decides = (f'WHAT THE FILM DECIDES (what let_panic does to this run is INFERRED and has not been seen; boot 23 is the comparison, OBSERVED once). First the panel: is there a panic text, a line '
               'that contains "panic(cpu" (the kernel may put its timestamp in front), and what are its first lines, which the coordinator transcribes from the film and the stills? Then the report page: boot 23\'s stop was '
               f'{STOP23} at {BOOT23_S} s, and the same stop again with no text is the falsifier of section 0. Any other stop, or a run longer than boot 23\'s, is progress: the coordinator '
               'reads its kind, PC= and A= and the last line of the log. NOT THE FAILURE: the noise lines of section 0a (the SPMI and RTKit lines OBSERVED once in boots 21 and 23, the '
               'AFKFirmwareService line in boot 21; INFERRED to come from the monitor\'s empty models), which the film shows before any panic text.')
    panic = ('WHAT A PANIC LOOKS LIKE (INFERRED from XNU\'s panic format; none has been seen on this unit): a line that contains "panic(cpu" (the kernel may put its timestamp in front) and a '
             'caller address, the panic string (an assertion names its file and line), "Debugger message: panic", more lines and maybe a backtrace; the word "panic" alone is not one '
             '(the RTKit lines "General panic behavior" or the early line "debug_log_init: Error!! gPanicBase is still not initialized", OBSERVED twice in boot 23\'s replay, are not a '
             'panic). The monitor answers the panic entry and lets the guest run on; it refuses every later call (section 3c), and that stop can come '
             '(read from probes/xnuboot/sptm_dispatch.c, INFERRED) before the text is printed: the report then says MON STOP UNNAMED or, if no call comes, MON STOP WATCHDOG, or an '
             'S2_FAULT at a device register, and the panel shows what was printed before it. Either way, film it.')
    return ('5d. Rung 4: what is new, what to film, and what counts as a result (the console, with cpus=1 and let_panic)',
            *(line for text in (new, film, expect, decides, panic, *results_paragraphs(panel['run'], wait_s)) for line in m1t.wrap(text, 3, 6)))


def guide_words(base):
    """The guide's PANEL entry of this record: rung 3's (`base`), with the words that this module says again."""
    return {**base, 'decides': DECIDES, 'hazard': hazard, 'rung': RUNG, 'why': why_lines, 'stand': stand_extra, 'stand_head': STAND_HEAD, 'console': CONSOLE, 'ran': RAN,
            'stop_seen': STOP_SEEN, 'step4': step4, 'step6': STEP6}


def step4(guest, label):
    """5c, step 4: the programs' digits are rung 3's too, so the GUEST.DAT line, the label and the chunk count are compared; rung 3's bundle differs in the SHA of GUEST.DAT and of PIN.DAT
    and in the label. PIN.DAT carries this record's SHA-256, so its own SHA is not in this text (section 1)."""
    return (f' Then compare, on the PAYLOAD LOADED line, INDEX=2 ... BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, '
            f'LABEL={label} CHUNKS={guest["chunks"]} (boot 23\'s page had LABEL={BOOT23_LABEL} CHUNKS=46, INDEX=2 ... SHA={BOOT23_GUEST} and INDEX=3 ... SHA={BOOT23_PIN}, the SHA of its '
            'PIN.DAT: rung 3\'s bundle staged under this plan has the same programs, the same size and the same chunk count, and only those two SHAs and the label tell it from this one; '
            'this record cannot name its own PIN.DAT SHA, which depends on this record\'s SHA-256).')
