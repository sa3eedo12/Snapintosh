#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of rung 3 of the monitor entry run (tools/sp11_stick_bundle_panel_rung3.py; docs/SP11-STICK-MONITOR.md section 20, K10): `real --m1-run --panel console --boot-arg cpus=1
--sptm-hvc-calls 0`. The mechanism: the list of words (cpus=1, for the panel as the console only; any other is BOOT_ARG_REFUSED), the label (M1RUN-CPUS1-), the read-back of the command
line from the bytes of GUEST.DAT (PANEL_MISMATCH), the four kinds against each other and the record's size. The words: each sentence that rung 3 says of its own has a phrase pin and a
deletion mutant that its pin alone rejects, and a generator without a block makes a record that its pin rejects. The fixtures of tools/test-sp11-stick-monitor-panel.py are loaded here
with a scratch directory of their own (build/sp11-monitor-rung3-test-scratch/, deleted at the end).

    python3 tools/test-sp11-stick-monitor-rung3.py

The real guest, the J700 26A428 kernelcache, is the end-to-end test of tools/test-sp11-stick-monitor-builder.py. Boot 21's numbers are the coordinator's reading of the operator's film
(OBSERVED, one unit, one boot); what XNU does with cpus=1 is INFERRED. Nothing here runs the unit: Hardware NOT RUN."""
import importlib.util
import re
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_panel_tests', TOOLS / 'test-sp11-stick-monitor-panel.py')
PT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PT)
SCRATCH = ROOT / 'build' / 'sp11-monitor-rung3-test-scratch'
PT.SCRATCH = PT.BLD.SCRATCH = PT.AUTH.SCRATCH = SCRATCH
pn, r2, r3, m1, rec, BT, ch, sb, bb, BLD = PT.pn, PT.pn.r2, PT.pn.r3, PT.m1, PT.rec, PT.BT, PT.ch, PT.sb, PT.bb, PT.BLD
STATE, record, build, chunks_of, spying = PT.STATE, PT.record, PT.build, PT.chunks_of, PT.spying
flat = lambda text: ' '.join(text.split())    # noqa: E731
LABEL_R3 = 'r3-test'            # 7 characters: M1RUN-CPUS1- and these are 19 of the 23
WAIT = 60                       # the silence rule's wait for the fixture's box of 5 s
# The four kinds of record: (the D9 choice, the word). The record of each has its own guest, label and text.
KINDS = (('withheld', None), ('handed', None), ('console', None), ('console', 'cpus=1'))
NAMES = {('withheld', None): 'withheld', ('handed', None): 'handed', ('console', None): 'console', ('console', 'cpus=1'): 'rung3'}


def setUpModule():
    PT.setUpModule()
    made = []
    with spying(made):
        code, text, err = record('setup-rung3', 'console', facts=BLD.facts_with(LABEL_R3), boot_arg='cpus=1')
    assert code == 0, err
    STATE['rung3'], STATE['rung3_panel'] = text, made[0]


def panel3():
    """The dict that the text of rung 3 is made from, with the `run` entry that the builder adds (the first run's programs are the stand-in pair's here)."""
    return {**STATE['rung3_panel'], 'run': r2.facts(5, r2.RUNG1_MONITOR, r2.RUNG1_REPORTER)}


def section3c_pins():
    """The pins of section 3c; the digits of the programs and the panel's own words come from the fixture (the stand-in pair and the stand-in GOP), as in the builder."""
    return {**SECTION3C_PINS, 'unchanged': SECTION3C_PINS['unchanged'].replace('1d32f507... and ee866760...', f'{r2.RUNG1_MONITOR[:8]}... and {r2.RUNG1_REPORTER[:8]}...')}


def hook_pins():
    p = STATE['rung3_panel']
    return {**HOOK_PINS, 'step 4': step4_pin(STATE['rung3']), '1a panel': f'panel {p["fb_pa"]:#x} + {p["fb_span"]:#x} bytes, {p["gop"]["width"]} x {p["gop"]["height"]}, text mode, the kernel\'s '
            'console (serial=2) and cpus=1 in its command line: section 3c'}


def all_pins():
    """Every pin of the record, with the section in the name: two sections have a pin called 'passed', and a merge by name would drop one of them."""
    return {f'{section}/{name}': phrase for section, pins in (('0', SECTION0_PINS), ('0a', SECTION0A_PINS), ('3c', section3c_pins()), ('5d', FIVE_D_PINS), ('hooks', hook_pins()))
            for name, phrase in pins.items()}


def tearDownModule():
    PT.tearDownModule()


def over(kind):
    choice, word = kind
    return dict(facts=BLD.facts_with(LABEL_R3), boot_arg=word) if word else {}


def pin_problems(text, pins):
    """The names of the pins whose phrase is not in `text` (line breaks gone)."""
    f = flat(text)
    return [name for name, phrase in pins.items() if flat(phrase) not in f]


def mutants_rejected(test, text, pins):
    """`text` holds each phrase of `pins` once, and the text without one phrase is rejected by the pin of that phrase and by no other."""
    f = flat(text)
    test.assertEqual(pin_problems(f, pins), [])
    for name, phrase in pins.items():
        with test.subTest(pin=name):
            test.assertEqual(f.count(flat(phrase)), 1, 'each sentence is in the text once')
            test.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), pins), [name])


# ---- the words: a phrase pin each, in the words of the text (line breaks gone) --------------------------------------------------------------------------------------------------
SECTION0_PINS = {
    'decides': 'APPROVING THIS TEXT ALSO DECIDES A CHOICE OF D9 THAT IS NOT DECIDED YET: the guest is handed the unit\'s panel as the kernel\'s console (serial=2), with the kernel stock and one '
               'word more in its command line, cpus=1, the first boot-arg that is not the loader\'s (section 3c). The first run withheld the panel, and rung 2 had no such word.',
    'why': 'WHY RUNG 3. Boot 21 (rung 2) PASSED: the kernel printed its boot log on the panel and the monitor stopped it at 2.044 s on a stage-2 fault at IPA 0X300734004, INFERRED to be the first '
           'CPU-start write (section 0a). The guest\'s tree lists six CPUs and the monitor runs one.',
    'one change': 'THIS RECORD CHANGES ONE THING IN THE GUEST: its command line gets one more word, cpus=1, which asks XNU to run with one CPU, so that it registers only processor 0 and does not start the '
                  'others (INFERRED from the public XNU source; the A16 once tripped a PMGR CPU-complex assertion with it: UNKNOWN which; section 3c).',
    'programs': 'The programs and the time box are rung 2\'s (section 3c).',
    'prediction': 'PREDICTION (INFERRED): the replayed early log shows only "processor 0 belongs ..." (boot 21\'s showed processors 0 to 5), the stop is no longer S2_FAULT with A= in '
                  '0X300734004..0X30073400C, and the kernel runs on.',
    'falsifier': 'FALSIFIER: "processor 1..5" lines still printed and boot 21\'s stop again: cpus=1 was not honoured, and rung 3 is NOT PASSED. A panic text from the PMGR or CLPC code is another '
                 'possible end.',
    'pointer': 'Section 3c has the other ends and section 5d what the film decides.'}
SECTION0A_PINS = {
    'run': 'Boot 21 (rung 2, record 1ad122b3...fc3b; one unit, one boot; the operator\'s film, read at full resolution) ran the monitor entry with the panel as the kernel\'s console: the call '
           'returned after 2.044 s (TICKS=39245286 at 19.2 MHz; the 5 s box was not used)',
    'stop': 'and the run ended as MON STOP S2_FAULT PC=0XFFFFFE00097A352C A=0X300734004 B=0X93970045 C=0XFFFFFE60A0178004, with MON REGS RESTORED=YES and GIC RESTORE CLEAN=YES.',
    'decode': 'The decode of B (ESR_EL2) is INFERRED: a data abort from a lower EL, a 32-bit write, a translation fault at level 1: the guest wrote a 32-bit value to the unmapped IPA '
              '0x300734004, which matches the first CPU-start write that the sources document for the A16 (compat/boot-relocate.h:147-153) and the plan\'s expected failure 5; the value written '
              'was not printed.',
    'panel': 'The panel showed the kernel\'s own boot log from its start (the first frames after the clear replay its message buffer): past PE_init_iokit (INFERRED: boot 20\'s loop is gone), the MAC '
             'policy and the platform-driver matching, with the line "[processor_init] processor 0 belongs to processor set 0 of type 0" early and, later, a line "[processor_init] processor N belongs '
             'to processor set T of type T" for each of the processors 1 to 5 (T is 0 for 1 to 3 and 1 for 4 and 5; the six CPUs of the guest\'s tree; only CPU 0 running); no MON CON line (serial=2).',
    'noise': 'The log is noisy, and the noise is not the failure: the SPMI lines ("parity error", "_readRegs failed"), "Couldn\'t alloc class AFKFirmwareService" and the RTKit lines "General panic '
             'behavior: debugFacility = ..." were on the film (OBSERVED once: boot 21); that they come from the monitor\'s empty models of those devices is INFERRED.',
    'passed': 'Rung 2 PASSED. As after boot 20, the stick after boot 21 was unchanged but for the Shell\'s marker and the Mac\'s own .fseventsd entry, and REPORT.DAT stayed the all-zero staged file.'}
SECTION3C_PINS = {
    'header': '3c. The panel as the kernel\'s console with cpus=1 (rung 3: D9 panel handed, serial=2, one more word, stock kernel; docs/SP11-STICK-MONITOR.md section 20)',
    'in short': 'In short. This record is rung 3: rung 2 (boot 21) again with one word more in the guest\'s command line, cpus=1, so that XNU runs with one CPU. Why: boot 21 stopped at a write that '
                'section 0a INFERS to be the kernel\'s first CPU-start write. Expected, INFERRED: the replayed early log shows only "processor 0 belongs ..." and the stop is not that write again; a panic '
                'from the PMGR or CLPC code is possible (the A16 once tripped a PMGR CPU-complex assertion with cpus=1: UNKNOWN which).',
    'rung 2': 'Rung 2 is the run before this one: the panel handed as the kernel\'s console (serial=2), record 1ad122b3...fc3b, boot 21 (section 0a); its approval is spent. This record is rung 3, rung 2 '
              'with one input of the guest changed: its command line has cpus=1 after rung 2\'s.',
    'not decided': 'It is not decided: approving the SHA-256 of this record is that decision, and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); rung 2\'s approval covers none of it.',
    'unchanged': 'SNAPMON-M1.BIN and SNAPREP-M1.BIN have the SHA-256 that boots 20 and 21 ran (1d32f507... and ee866760..., in full in section 1), the time box is 5 s and the store classes are '
                 'rung 2\'s.',
    'differ': 'Only GUEST.DAT (one page of boot_args: the command line) and PIN.DAT (the label and the digests) differ from rung 2\'s, and PAYLOAD.MAN, which lists them; everything of section 1b is the '
              'first run\'s. Section 5 has the rule that rung 2 had. At run time the guest differs from rung 2\'s in one thing: XNU is asked to run with one CPU.',
    'label': 'Label: PIN.DAT\'s build_label is M1RUN-CPUS1-r3-test. The builder puts CPUS1- after M1RUN- for this record only, in place of CONSOLE-, and refuses a facts label that starts with it',
    'command line': 'command line -v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpus=1',
    'other differences': 'From rung 2\'s guest this one differs in the command line only, which is one page of boot_args: the same size and the same chunks, and its own SHA-256 (docs/SP11-STICK-REAL.md section 2).',
    'console open': 'Console. What the operator saw in boot 21 (OBSERVED once: boot 21, rung 2) and expects to see again (INFERRED): XNU\'s own boot text',
    'console early': 'printed before the video console takes over: INFERRED; boot 21 had none, OBSERVED once)',
    'settled': 'What boot 21 settled (OBSERVED once, rung 2, one unit, one boot; the operator\'s film): the kernel\'s text is on the panel. From the MON CALL line the panel was cleared and filled with the '
               'kernel\'s log over about 1.2 s, replayed from the start of its message buffer, as the public source says (below);',
    'settled video': 'the video block the kernel printed (initialize_screen: b=E4800000 w=B40 h=780 r=2D00 d=0) is the one the loader gave;',
    'settled labels': 'Where the paragraphs of this section say INFERRED of these, they were written before boot 21 and it has shown them once; the rest stays INFERRED.',
    'word list': 'The extra word. The command line is the loader\'s with serial=2 and, after it, cpus=1 (-v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpus=1): one word, from the builder\'s list (--boot-arg: '
                 'cpus=1 and no other word, BOOT_ARG_REFUSED for any other), for the panel as the console only, read back from boot_args by the builder (PANEL_MISMATCH for any other line, rung 2\'s included).',
    'word xnu': 'from the public XNU source (tag xnu-12377.121.6, not this kernel\'s own: macOS 27 is xnu-13432, whose source is not public) and not re-read here, so it is INFERRED for this kernel:',
    'word effect': 'cpus=N cuts the CPU topology before any CPU is registered and keeps the boot CPU; with one CPU the kernel registers one (the replayed early log then has only "processor 0 belongs '
                   '...": the film\'s check) and makes no PE_cpu_start_internal call, so there is no CPU-start write to the PMGR block and no "failed to boot" panic; the scheduler and the SMP start '
                   'check are written for it; cpus= together with cpumask= makes the configuration incorrect (a later panic), so no record of this tool carries both.',
    'word string': 'The kernelcache has the strings "cpus" (twice: one in the kernel\'s topology parser and one standalone in the CLPC kext, of unknown use: the design note) and "cpumask" (once), read '
                   'on the host (a file, not a run); that this kernel parses them as the public source does stays INFERRED.',
    'word a16': 'What the A16 recorded is two sentences, "Reducing the topology with cpus=1 is not an option for this kernel: it trips a PMGR CPU-complex assertion earlier in boot" and "Whether the J700 '
                'guest tolerates a reduced or enlarged cpu set is UNKNOWN until X4 of the Surface Pro 11 plan" (docs/BOOT-CHAIN.md section 7)',
    'word tree': 'The guest\'s device tree still lists six CPUs, which cpus=1 does not trim (UNKNOWN: whether PMGR or the interrupt controller objects). This record is EXPERIMENTAL, and a new operator '
                 'decision: the first boot-arg that is not the loader\'s.',
    'ends': 'What the run may end as (INFERRED from the design notes and boot 21: none of it has been seen, and which comes first, and when, is UNKNOWN). Honoured: the replayed log has only '
            '"processor 0 belongs ..." and the stop is not the CPU-start stop: MON STOP S2_FAULT with another A= (the next device that the monitor does not model: progress, and a new question; '
            'boot 21\'s was A=0X300734004 at 2.044 s) or MON STOP WATCHDOG at the end of the 5 s box with the kernel still running (progress: the film shows how far it got).',
    'ends not': 'Not honoured: "processor 1..5" lines still printed and the CPU-start stop again, MON STOP S2_FAULT with A= in 0X300734004..0X30073400C (UNKNOWN why: 26A428 does not honour the '
                'word, or a path of PMGR starts a CPU anyway).',
    'ends panic': 'A panic: an ApplePMGR or CLPC assertion, or another panic text on the panel, with MON STOP PANIC_ENTRY or GUEST_EXC, or only WATCHDOG, or an S2_FAULT at a reset register (the text is '
                  'the result; it may not reach the panel before the monitor stops the guest: UNKNOWN).',
    'reads seen': 'UNKNOWN; both reads ran inside boot 21\'s call of 2.044 s (OBSERVED once, not told apart). Both reads lie outside the time box.',
    'ends early': 'Another early end: a stall earlier than boot 21\'s last log line, or an S2_FAULT before the CPU point. A restart of the Surface: NOT A RESULT for M1 (section 5a), NOT PASSED for rung 3 '
                  '(section 5d). In every case the operator does nothing but film (section 5d).'}
FIVE_D_PINS = {
    'new': 'WHAT IS NEW. One word: the guest\'s command line has cpus=1 after rung 2\'s (boot 21): -v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpus=1. It asks XNU to run with one CPU, so that only '
           'processor 0 exists and the kernel does not make the write that stopped boot 21, INFERRED to be a CPU-start write (section 3c).',
    'same': 'Everything else is rung 2\'s: the same programs (SNAPMON-M1.BIN and SNAPREP-M1.BIN have the SHA-256 that boots 20 and 21 ran), the 5 s box, the panel, serial=2, one CPU, AC power',
    'film': 'THE FILM is rung 2\'s, from the Shell\'s countdown until 60 seconds after the SNAPREP report page has appeared: the whole panel with the main camera, 30 frames a second or more, steady, and if '
            'there is a second camera the TOP-LEFT quarter close up (step 2 of section 5c).',
    'film shows': 'It must show the AUTORUN lines, PAYLOAD CALL 0 (only the film shows it once the kernel has cleared the panel: if the film does not show it, the approval is spent)',
    'lowercase': 'Lowercase letters on the panel are the kernel\'s (boot 21: OBSERVED once). A photograph taken afterwards is not enough.',
    'expect': 'WHAT TO EXPECT. As in boot 21: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the guest has stopped',
    'count': f'Count those {WAIT} seconds from the moment the MON CALL line is on the screen, on a watch or the camera\'s timer.',
    'decides': 'WHAT THE FILM DECIDES (the effect of cpus=1 is INFERRED and has not been seen; boot 21 is the comparison, OBSERVED once). First the kernel\'s log, which the first frames after the MON '
               'CALL line replay from its start: boot 21\'s had "[processor_init] processor 0 belongs to processor set 0 of type 0" early and, later, a line "[processor_init] processor N belongs to '
               'processor set T of type T" for each of the processors 1 to 5 (T is 0 for 1 to 3 and 1 for 4 and 5); with cpus=1 only the first is expected.',
    'decides report': 'Then the report page: boot 21\'s stop was MON STOP S2_FAULT A=0X300734004 B=0X93970045 at 2.044 s, and the CPU-start stop is a MON STOP S2_FAULT with A= in '
                      '0X300734004..0X30073400C; an S2_FAULT at another address, or a run longer than boot 21\'s, is progress. Boot 21\'s log ended in platform-driver lines: the coordinator compares the '
                      'film with its frames.',
    'decides noise': 'NOT THE FAILURE: the noise lines of section 0a (OBSERVED once in boot 21; INFERRED to come from the monitor\'s empty models).',
    'panic': 'WHAT A PANIC LOOKS LIKE (INFERRED from XNU\'s panic format; none has been seen on this unit): a line that starts "panic(cpu" and a caller address, the panic string (an assertion names its '
             'file and line), "Debugger message: panic", more lines and maybe a backtrace; the word "panic" alone is not one.',
    'panic entry': 'The monitor answers the panic entry and lets the guest run on; it stops at the first later call that it does not implement, which can come before the text is printed (read from '
                   'probes/xnuboot/sptm_dispatch.c, INFERRED): the report then says MON STOP UNNAMED or, if no such call comes, MON STOP WATCHDOG, or an S2_FAULT at a reset register, and the panel '
                   'shows what was printed before it. Either way, film it.',
    'results head': 'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 3\'s, with one more condition: the panel did not end the run.',
    'passed': 'RUNG 3 PASSED: "processor 0 belongs ..." is the only line of that kind in the replayed log, the report\'s MON STOP is not an S2_FAULT with A= in 0X300734004..0X30073400C, there is no panic '
              'text on the panel, the log reaches boot 21\'s last line or goes beyond it, and the stop is a later device fault (MON STOP S2_FAULT at another address, A= not 0, outside the panel range) '
              'or the box end (MON STOP WATCHDOG, the kernel still running), on a clean report (none of MON REGS RESTORED=NO, WINDOW OVERRUN, STACK FULL or GIC RESTORE CLEAN=NO is on the page).',
    'not passed a': 'RUNG 3 NOT PASSED, (a): "processor 1..5" lines still printed and boot 21\'s stop again: 26A428 did not honour cpus=1 (the falsifier of section 0; UNKNOWN why).',
    'panic text': 'RUNG 3 PANIC TEXT, (b): an ApplePMGR or CLPC assertion, or another panic, is on the panel (the page may say only MON STOP UNNAMED, WATCHDOG, an S2_FAULT at a reset register, PANIC_ENTRY or '
                  'GUEST_EXC): the text is the result, whatever else is on the page. It is neither a pass nor a failure of the record: film it and hand it back.',
    'not passed c': f'RUNG 3 NOT PASSED, (c): a stall earlier than boot 21\'s last log line (MON STOP WATCHDOG with the log ending sooner), an S2_FAULT before the CPU point, a MON STOP S2_FAULT with A= '
                    f'inside the panel range (section 2), the panel still dark {WAIT} seconds after the MON CALL line, a restart of the Surface before the report, no report (a page that stops at MON '
                    'CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1), or any other end that none of the results above names, for example MON STOP UNNAMED: the coordinator reads it first.',
    'partial': 'RUNG 3 PARTIAL: only "processor 0 belongs ..." in the log and still the CPU-start stop: cpus=1 had an effect and did not prevent the write.',
    'not a result': 'RUNG 3 NOT A RESULT: a film that does not let the coordinator read MON STOP or count the processor lines of the replayed log (too soft, too dark, or the report unreadable under the '
                    'kernel\'s text): nothing is concluded from it, and the one-boot rule of section 5c says whether the approval is spent.',
    'separate': 'A run that ends as boot 21 did is still M1 PASSED (section 5a) and rung 3 not passed: the two verdicts are separate.'}
HOOK_PINS = {
    'kind': 'This record is of the choice that hands the guest the panel as the kernel\'s console (serial=2) with cpus=1 in its command line: the ram class names its range (section 3c)',
    'reading': 'This text also asks the operator to read section 5d: it says what to film, what is new beyond section 0b and what counts as a result of rung 3.',
    '0a head': '0a. Where things stand (boots 20 and 21, 2026-11-18, one unit, one boot each; OBSERVED unless a sentence says INFERRED)',
    '0a loop': 'an early loop in the guest. Boot 21 did not stop there again, which fits it (INFERRED).',
    '0b ran': 'under a monitor that ran twice on this machine (boots 20 and 21: section 0a).',
    '0b gets': 'and stage 2 maps it RAM and that range only (INFERRED from the sources; boots 20 and 21 ran it: the bullet on disk writes below).',
    '0b hazard': 'and the kernel draws its console there (OBSERVED once: boot 21): from the MON CALL line the panel is cleared and fills with the kernel\'s log (the kernel\'s console clears the whole panel',
    '0b hazard path': 'cpus=1 sends the kernel down a path that no run on this unit has taken: it may panic or spin there, and the monitor ends the run at the box or at its own stop either way (section 5d).',
    '0b ram': '(INFERRED; boot 20\'s 5 s, with the map of RAM only, showed no stage-2 abort, and boot 21\'s 2.044 s, with the panel range, one, outside RAM and the panel range, OBSERVED once each). '
              'Stage 2 is all that stops',
    '3 ram': '(probes/xnuboot/stage2.c:186-199; INFERRED from the sources; boot 20\'s 5 s, with the map of RAM only, showed no stage-2 abort, and boot 21\'s 2.044 s, with the panel range, one, outside '
             'RAM and the panel range, OBSERVED once each). No disk and no RAM',
    '4 reads': 'framebuffer: two reads of the panel OBSERVED once inside boot 21\'s call of 2.044 s (not told apart)',
    '5a console': 'its console is the panel (boot 21, rung 2: the kernel\'s log was on it, OBSERVED once) and the MON CON lines are not expected to carry it',
    '5a fault seen': 'OBSERVED once: boot 21 stopped that way, at A=0X300734004, which section 0a INFERS to be the first CPU-start write). Rows c and d hold for it,',
    '5a fault success': 'A STAGE-2 FAULT WITH AN IPA IS SUCCESS (not when A= lies inside the panel range: section 5d): MON STOP S2_FAULT, with the guest PC, the IPA in A= and ESR_EL2 in B=,',
    '5a row e': 'and their cost is OBSERVED once inside boot 21\'s call of 2.044 s (not told apart)), so TICKS <= 96,000,000 proves row e',
    '5b s2': 'An expected end of M1. OBSERVED once (boot 21, one unit, one boot).',
    '5b panic': 'finding (section 5a). Never seen (UNKNOWN). With serial=2 the panic text is on the panel, not in the MON CON lines.',
    'step 2': 'Film 30 frames a second or more (29.97 is fine: boot 20\'s film had it; 60 if the camera can) from the Shell\'s countdown until 60 seconds after the SNAPREP report page has appeared',
    'step 5': f'From the MON CALL line the screen may go black and fill with text: that is expected. {r2.force_off(WAIT)}. Count those {WAIT} seconds from the moment the MON CALL line is on the screen, on a '
              'watch or the camera\'s timer. If at any time the Surface is too hot',
    'step 6': 'For this record wait until 60 seconds after the SNAPREP page appeared, by the film, before that FORCE OFF (a margin, not a measurement).',
    'rung': 'this record is rung 3, the panel handed to the guest as the kernel\'s console (serial=2) with cpus=1 in its command line, and no approval covers another\'s boot.'}


def step4_pin(text):
    """Step 4's sentence for the guest and the label of `text`: the digits are the record's own (section 1), the words are rung 3's."""
    f = flat(text)
    size, sha, chunks = re.search(r'data GUEST\.DAT (\d+) bytes sha256 ([0-9a-f]{64}) chunks (\d+)', f).groups()
    label = re.search(r'\(format 1, label (\S+?)\)', f).group(1)
    return (f'Then compare, on the PAYLOAD LOADED line, INDEX=2 ... BYTES={size} SHA={sha[:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, LABEL={label} CHUNKS={chunks} (boot 21\'s page had '
            'LABEL=M1RUN-CONSOLE-RUNG2 CHUNKS=46 and INDEX=2 ... SHA=ACAD28623AEBB068: rung 2\'s bundle staged under this plan has the same programs, the same size and the same chunk count, and only that '
            'SHA and the label tell it from this one).')


class Options(unittest.TestCase):
    def raises(self, name, function, *args):
        with self.assertRaises(ch.Refused) as caught:
            function(*args)
        self.assertEqual(caught.exception.name, name)
        return caught.exception

    def test_the_list_has_one_word_for_the_panel_as_the_console_and_nothing_else_is_passed_on(self):
        self.assertEqual((m1.BOOT_ARGS, m1.BOOT_ARG_PANEL), ({'cpus=1': 'CPUS1-'}, 'console'))
        m1.check_options(7, False, 5, 'console', 0, 'cpus=1')
        m1.check_options(7, False, 5, 'console', 0, None)
        for word in ('cpus=2', 'cpus=0', 'cpus=1 ', ' cpus=1', 'CPUS=1', 'cpus=01', 'cpus==1', 'cpus=1;', 'cpus=1\n', 'cpumask=1', 'maxcpus=1', '', 'cpus=1 cpumask=1', 'cpus=1 serial=3',
                     '-noprogress', 'serial=3', 'cluster_power=1', 'debug=0x8'):
            with self.subTest(word=word):
                caught = self.raises('BOOT_ARG_REFUSED', m1.check_options, 7, False, 5, 'console', 0, word)
                self.assertIn('cpus=1', caught.detail, 'the message names the list')
                self.raises('BOOT_ARG_REFUSED', pn.command_line, 'console', word)

    def test_the_word_belongs_to_the_panel_as_the_console_and_to_the_monitor_entry_run_only(self):
        for panel in ('withheld', 'handed'):
            with self.subTest(panel=panel):
                self.raises('BOOT_ARG_REFUSED', m1.check_options, 7, False, 5, panel, 0, 'cpus=1')
                self.raises('BOOT_ARG_REFUSED', pn.command_line, panel, 'cpus=1')
                self.raises('BOOT_ARG_REFUSED', m1.pinned_label, 'RUNG3', True, panel, 'cpus=1')
        self.raises('BOOT_ARG_REFUSED', m1.check_boot_arg, 'cpus=1', 'console', False)
        self.raises('BOOT_ARG_REFUSED', m1.check_options, 7, False, 5, None, None, 'cpus=1')
        args = PT.real_args('dry-run-word', 'console', boot_arg='cpus=1')
        args.update(m1_run=None, m1_efi=None)           # the kind of the dry run: no monitor is called
        for panel in ('withheld', 'handed'):
            args['panel'] = panel
            code, text, err = BT.template('real', **args)
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=BOOT_ARG_REFUSED'), (panel, err))
        code, text, err = record('word-with-handed', 'handed', boot_arg='cpus=1')
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=BOOT_ARG_REFUSED'), err)
        code, text, err = record('word-bad', 'console', boot_arg='cpus=2')
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=BOOT_ARG_REFUSED'), err)

    def test_the_synthetic_kind_has_no_such_option_and_the_real_kind_names_it_in_its_help(self):
        with self.assertRaises(SystemExit):
            BT.cli(['authorisation-template', 'synthetic', *BT.opts(stores='ram,el2_sysreg'), '--boot-arg', 'cpus=1'])
        source = (TOOLS / 'sp11_stick_bundle_build.py').read_text()
        self.assertIn("p.add_argument('--boot-arg'", source.split("p.add_argument('--kernelcache', required=True)")[1][:1200], 'the real kind has the option, after its kernelcache')
        self.assertNotIn('--boot-arg', source.split("if kind == 'synthetic':\n        for flag, default")[1].split("p.add_argument('--kernelcache'")[0])

    def test_the_refusal_is_a_known_one_and_the_reader_has_its_own(self):
        self.assertIn('BOOT_ARG_REFUSED', ch.KNOWN_REFUSALS)
        self.assertIn('PANEL_MISMATCH', pn.REFUSALS)


class Label(unittest.TestCase):
    def test_the_label_of_the_record_has_its_own_marker_in_place_of_the_panels(self):
        self.assertEqual(m1.pinned_label('RUNG3', True, 'console', 'cpus=1'), 'M1RUN-CPUS1-RUNG3')
        self.assertEqual(m1.pinned_label('RUNG2', True, 'console'), 'M1RUN-CONSOLE-RUNG2')
        self.assertEqual(m1.pinned_label('x', False, 'withheld'), 'x', 'the other kinds keep the facts label')
        marks = [m for m in m1.PANEL_MARKS.values() if m] + list(m1.BOOT_ARGS.values())
        self.assertEqual(len(marks), len(set(marks)))
        self.assertFalse([a for a in marks for b in marks if a != b and a.startswith(b)], 'no marker is the start of another: one label, one kind of record')
        self.assertEqual(len('M1RUN-CONSOLE-CPUS1-RUNG3'), 25, 'the label that one would write does not fit the 23 characters of PIN.DAT (sp11sb_string_ok)')
        self.assertEqual(m1.PIN_LABEL_MAX, 23)

    def test_a_facts_label_that_carries_any_marker_is_refused_under_every_choice_and_the_word(self):
        for mark in [m for m in m1.PANEL_MARKS.values() if m] + list(m1.BOOT_ARGS.values()):
            for choice, word in KINDS:
                with self.subTest(mark=mark, choice=choice, word=word):
                    with self.assertRaises(ch.Refused) as caught:
                        m1.pinned_label(f'{mark}x', True, choice, word)
                    self.assertEqual(caught.exception.name, 'PIN_STRING')
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('M1RUN-x', True, 'console', 'cpus=1')
        self.assertEqual(caught.exception.name, 'PIN_STRING')

    def test_the_facts_label_holds_eleven_characters_under_the_marker(self):
        self.assertEqual(m1.pinned_label('x' * 11, True, 'console', 'cpus=1'), 'M1RUN-CPUS1-' + 'x' * 11)
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('x' * 12, True, 'console', 'cpus=1')
        self.assertEqual(caught.exception.name, 'PIN_STRING')
        self.assertIn('holds 11', caught.exception.detail)

    def test_the_label_is_in_the_record_the_pin_and_the_page_words(self):
        self.assertIn('(format 1, label M1RUN-CPUS1-r3-test)', STATE['rung3'].splitlines()[0])
        code, text, err, out = build('label-rung3', 'console', **over(('console', 'cpus=1')))
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(BLD.label_of(BLD.pin_of(out)).decode(), 'M1RUN-CPUS1-r3-test')
        self.assertEqual(sb.check_pin(BLD.pin_of(out)), 0)


class Reader(unittest.TestCase):
    def test_each_of_the_four_guests_is_read_as_what_it_is_and_nothing_else(self):
        for kind in KINDS:
            chunks, lay, facts, board = chunks_of(kind[0], **over(kind))
            for read_as in KINDS:
                with self.subTest(guest=NAMES[kind], read_as=NAMES[read_as]):
                    if read_as == kind:
                        self.assertEqual(bool(pn.read_panel(chunks, lay, facts, read_as[0], True, board, read_as[1])), kind[0] != 'withheld')
                    else:
                        with self.assertRaises(ch.Refused) as caught:
                            pn.read_panel(chunks, lay, facts, read_as[0], True, board, read_as[1])
                        self.assertEqual(caught.exception.name, 'PANEL_MISMATCH')

    def test_the_guest_of_rung_3_differs_from_the_guest_of_rung_2_in_the_command_line_only(self):
        (two, lay, _, _), (three, _, _, _) = chunks_of('console'), chunks_of('console', **over(('console', 'cpus=1')))
        by = {name: {c['dest']: bytes(c['data']) for c in chunks if c.get('data') is not None} for name, chunks in (('two', two), ('three', three))}
        self.assertEqual(sorted(by['two']), sorted(by['three']))
        differing = [dest for dest in by['two'] if by['two'][dest] != by['three'][dest]]
        self.assertEqual(len(differing), 1, 'one chunk differs: the one that holds the boot_args page')
        dest, page = differing[0], lay.handoff_pa + pn.BOOT_ARGS_AT
        self.assertTrue(dest <= page < dest + len(by['two'][dest]))
        a, b = by['two'][dest], by['three'][dest]
        start = page - dest + pn.CMDLINE_AT + len(pn.CMDLINES['console'])
        self.assertEqual([i for i in range(len(a)) if a[i] != b[i]], list(range(start, start + len(' cpus=1'))), 'the seven bytes of " cpus=1" after the console line')
        self.assertEqual(b[start:start + 8], b' cpus=1\0')

    def test_the_dict_the_text_is_made_from_holds_the_word_and_the_line(self):
        panel = STATE['rung3_panel']
        self.assertEqual((panel['choice'], panel['boot_arg'], panel['cmdline']), ('console', 'cpus=1', pn.command_line('console', 'cpus=1')))
        self.assertEqual(panel['cmdline'], r3.LINE)
        self.assertEqual(panel['cmdline'], pn.CMDLINES['console'] + ' cpus=1')
        console = STATE['panel']['console'][0]
        self.assertEqual({k: v for k, v in panel.items() if k not in ('boot_arg', 'cmdline')}, {k: v for k, v in console.items() if k not in ('boot_arg', 'cmdline')})
        self.assertIsNone(console['boot_arg'])

    def test_a_guest_whose_command_line_is_not_the_one_its_record_says_is_refused_for_each_seeded_fault(self):
        def line(value):
            def fn(built, xb, lay):
                page = bytearray(built['chunks'][lay.handoff_pa + pn.BOOT_ARGS_AT])
                page[pn.CMDLINE_AT:pn.CMDLINE_AT + len(value)] = value
                built['chunks'][lay.handoff_pa + pn.BOOT_ARGS_AT] = bytes(page)
            return fn
        console = pn.CMDLINES['console']
        cases = {'rung 2\'s line for rung 3': (True, console), 'another count': (True, console + ' cpus=2'), 'the count of none': (True, console + ' cpus=0'), 'the word twice': (True, console + ' cpus=1 cpus=1'),
                 'two spaces before the word': (True, console + '  cpus=1'), 'a space after the word': (True, console + ' cpus=1 '), 'the word first': (True, '-v cpus=1 debug=0x8 serial=2 cluster_power=0 wdt=-1'),
                 'a mask for the word': (True, console + ' cpumask=1'), 'the word and a mask': (True, console + ' cpus=1 cpumask=1'), 'maxcpus': (True, console + ' maxcpus=1'),
                 'the word in capitals': (True, console + ' CPUS=1'), 'the word and the loader\'s route': (True, pn.LOADER_CMDLINE + ' cpus=1'), 'the word and no -v': (True, console[3:] + ' cpus=1'),
                 'the word and another': (True, console + ' cpus=1 extra=1'),
                 'the word for rung 2': (False, console + ' cpus=1'), 'the word for rung 2, twice': (False, console + ' cpus=1 cpus=1')}
        for name, (word, text) in cases.items():
            with self.subTest(name):
                kind = ('console', 'cpus=1') if word else ('console', None)
                code, out, err = record('seeded-word', 'console', line(text.encode() + b'\0'), **over(kind))
                self.assertEqual((code, BT.refusal(out)), (1, 'REFUSED=PANEL_MISMATCH'), (name, err))


class Kinds(unittest.TestCase):
    def test_the_guest_the_pin_and_the_approved_record_of_one_kind_belong_to_no_other(self):
        pins = {}
        for kind in KINDS:
            code, text, err, out = build(f'belongs-{NAMES[kind]}', kind[0], **over(kind))
            self.assertEqual(code, 0, (text, err))
            pins[kind] = (BLD.pin_of(out), (out / 'bundle' / 'SP11' / 'GUEST.DAT').read_bytes())
        records = {kind: STATE[NAMES[kind]] for kind in KINDS}

        def belongs(record_text, pin):
            fields = sb.unpack(pin, sb.PIN_LAYOUT)
            return bytes(fields['authorisation_sha256']).hex() == PT.sha(record_text.encode()) and f'label {BLD.label_of(pin).decode()})' in record_text.splitlines()[0]
        for kind in KINDS:
            for other in KINDS:
                with self.subTest(record=NAMES[kind], pin=NAMES[other]):
                    self.assertEqual(belongs(records[kind], pins[other][0]), kind == other, 'the PIN.DAT of one kind belongs to the record of that kind and no other')
        for kind in KINDS:
            for other in KINDS:
                if kind != other:
                    with self.subTest(guest=NAMES[kind], pin=NAMES[other]):
                        pin, guest = pins[other][0], pins[kind][1]
                        self.assertNotEqual(sb.check_guest(guest, pin) or sb.check_digests(guest, pin, sb.HASH_CHUNKS | sb.HASH_FILE)[0], 0)

    def test_a_build_with_the_approved_record_of_another_kind_is_refused_whichever_is_the_guest(self):
        for kind in KINDS:
            for other in KINDS:
                if kind != other and ('console', 'cpus=1') in (kind, other):
                    with self.subTest(guest=NAMES[kind], approved=NAMES[other]):
                        code, text, err, out = build(f'cross-{NAMES[kind]}-{NAMES[other]}', kind[0], approved=STATE[NAMES[other]], **over(kind))
                        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                        self.assertFalse(out.exists(), 'nothing is written for a record that is not this build\'s')

    def test_a_facts_label_that_carries_a_marker_is_refused_end_to_end_for_the_record_of_the_word(self):
        for mark in [m for m in m1.PANEL_MARKS.values() if m] + list(m1.BOOT_ARGS.values()):
            code, text, err = record('marked-word', 'console', facts=BLD.facts_with(f'{mark}x'), boot_arg='cpus=1')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PIN_STRING'), (mark, err))

    def test_the_other_three_records_say_nothing_of_the_word_and_this_one_says_it_where_they_do_not(self):
        for name in ('withheld', 'handed', 'console'):
            f = flat(STATE[name])
            with self.subTest(name):
                for word in ('cpus=1', 'CPUS1', 'WHY RUNG 3', 'RUNG 3 PASSED', 'Boot 21', 'boot 21'):
                    self.assertNotIn(word, f)
        f = flat(STATE['rung3'])
        stick = 'The stick was unchanged but for the Shell\'s marker and the Mac\'s own .fseventsd entry.'
        self.assertTrue(all(stick in flat(STATE[name]) for name in ('withheld', 'handed', 'console')), 'the other kinds keep the shared 0a line')
        for word in ('WHY THE PANEL IS HANDED NOW', 'RUNG 2 PASSED', 'RUNG 2 PARTIAL', 'The stop-site analysis of that address', 'the loop is gone', '(high confidence)'):
            self.assertNotIn(word, f, 'rung 2\'s own words are not rung 3\'s')
        for word in (stick, 'has not run', 'never run here', 'would falsify that', 'stopped itself at the kernel', 'set 0 of type T', 'will see again'):
            self.assertNotIn(word, f, 'boot 21 and the review of R3 made these words false or double')


class Words(unittest.TestCase):
    def sections(self):
        f = flat(STATE['rung3'])
        head3c = '3c. The panel as the kernel\'s console with cpus=1'
        return dict(section0=f.split('0. What approving this text decides (read this first)')[1].split('0a. Where things stand')[0],
                    section0a=f.split('0a. Where things stand')[1].split('0b. The hazards, in plain words')[0],
                    section3c=head3c + f.split(head3c)[1].split('4. Time box')[0],
                    section5d=f.split('5d. Rung 3: what is new')[1])

    def test_each_sentence_of_sections_0_0a_3c_and_5d_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = self.sections()
        mutants_rejected(self, parts['section0'], SECTION0_PINS)
        mutants_rejected(self, parts['section0a'], SECTION0A_PINS)
        mutants_rejected(self, parts['section3c'], section3c_pins())
        mutants_rejected(self, parts['section5d'], FIVE_D_PINS)

    def test_each_sentence_that_a_hook_puts_in_the_record_is_pinned_and_a_record_without_it_is_rejected(self):
        pins = hook_pins()
        f = flat(STATE['rung3'])
        self.assertEqual(pin_problems(f, pins), [])
        for name, phrase in pins.items():
            with self.subTest(pin=name):
                self.assertEqual(f.count(flat(phrase)), 1, 'each sentence is in the text once')
                self.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), pins), [name])

    def test_section_5d_is_at_most_60_lines_and_shorter_than_rung_2s_and_the_reading_pointer_names_it(self):
        lines = r3.section_5d(panel3(), WAIT)
        self.assertLessEqual(len(lines), 60)
        self.assertLess(len(lines), len(r2.section_5d(panel3(), WAIT)) + 6)
        self.assertTrue(all(len(line) <= 118 and line.isascii() for line in lines))
        text = STATE['rung3']
        self.assertEqual(len(text.split('\n5d. ')[1].splitlines()), len(lines))
        self.assertIn('sections 0, 0a, 0b, 4, 5, 5a, 5c and 5d', flat(text))

    def test_a_generator_without_one_of_the_blocks_makes_a_record_that_the_pins_reject(self):
        guide = rec.m1g
        pins = all_pins()
        keys = guide.PANEL['console+cpus=1']
        dropped = lambda key, value: mock.patch.dict(keys, {key: value})      # noqa: E731
        for what, (patch, pin) in {'no why': (dropped('why', lambda real: ()), '0/why'), 'no boot 21 facts in 0a': (dropped('stand', lambda real: ()), '0a/run'),
                                   'no hazard in 0b': (dropped('hazard', ''), 'hooks/0b hazard'), 'no decision': (dropped('decides', ''), '0/decides'),
                                   'the first run\'s count of runs': (dropped('ran', ''), 'hooks/0b ran'), 'no stop seen': (dropped('stop_seen', {}), 'hooks/5b s2'),
                                   'no fault seen': (dropped('fault_seen', ''), 'hooks/5a fault seen'), 'no film end': (dropped('film_until', ''), 'hooks/step 2'),
                                   'no step 6 margin': (dropped('step6', ''), 'hooks/step 6'), 'no 5d': (mock.patch.object(r3, 'section_5d', lambda panel, wait: ()), '5d/new'),
                                   'no word paragraph': (mock.patch.object(r3, 'word_text', lambda: 'x'), '3c/word list'),
                                   'no boot 21 paragraph': (mock.patch.object(r3, 'boot21_text', lambda: 'x'), '3c/settled'),
                                   'rung 2\'s ends': (mock.patch.object(r3, 'expect_text', r2.expect_text), '3c/ends'), 'no console note': (dropped('console', ''), 'hooks/5a console'),
                                   'no fault note': (dropped('fault_note', ''), 'hooks/5a fault success'), 'no 0a heading': (dropped('stand_head', 'x'), 'hooks/0a head'),
                                   'no 0a loop': (dropped('stand_loop', 'x'), 'hooks/0a loop'), 'old guest words': (dropped('guest_gets', 'x'), 'hooks/0b gets'),
                                   'old map note': (dropped('ram_note', 'x'), 'hooks/0b ram'), 'old row e': (dropped('row_e', r2.row_e), 'hooks/5a row e'),
                                   'old ram class': (mock.patch.object(pn, 'RAM_NEW3', pn.RAM_NEW), 'hooks/3 ram'), 'old time row': (mock.patch.object(r3, 'READS_ROW', 'x'), 'hooks/4 reads'),
                                   'no reads sentence': (mock.patch.object(r3, 'READS_SEEN', ''), '3c/reads seen')}.items():
            with self.subTest(what), patch:
                code, text, err = record('mutant-rung3', 'console', **over(('console', 'cpus=1')))
                self.assertEqual(code, 0, err)
                self.assertNotEqual(text, STATE['rung3'])
                self.assertIn(pin, pin_problems(text, pins), what)

    def test_boot_21s_numbers_are_the_operators_film_and_the_line_is_the_loaders_with_the_word(self):
        self.assertEqual((r3.BOOT21_TICKS, r3.BOOT21_S), (39_245_286, '2.044'))
        self.assertEqual(round(39_245_286 / 19.2e6, 3), 2.044)
        self.assertEqual(r3.BOOT21_STOP, dict(pc=0xFFFFFE00097A352C, a=0x300734004, b=0x93970045, c=0xFFFFFE60A0178004))
        self.assertEqual(r3.page_hex(r3.BOOT21_STOP['a']), '0X300734004')
        self.assertEqual(r3.CPU_START_WINDOW, (0x300734004, 0x30073400C))
        self.assertTrue(r3.CPU_START_WINDOW[0] <= r3.BOOT21_STOP['a'] <= r3.CPU_START_WINDOW[1], 'boot 21\'s stop is the CPU-start stop that the verdicts name')
        self.assertEqual(r3.WINDOW, 'A= in 0X300734004..0X30073400C')
        self.assertEqual(r3.LINE, pn.command_line('console', 'cpus=1'))
        self.assertEqual(r3.WORD, 'cpus=1')
        self.assertEqual(sorted(m1.BOOT_ARGS), [r3.WORD])
        esr = r3.BOOT21_STOP['b']          # the decode of the film's reader: EC 0x24, ISV, word, SRT 23, WnR, DFSC 0x05
        self.assertEqual((esr >> 26, (esr >> 24) & 1, (esr >> 22) & 3, (esr >> 16) & 0x1F, (esr >> 6) & 1, esr & 0x3F), (0x24, 1, 2, 23, 1, 5))

    def test_the_documents_it_cites_are_cited_by_section_and_no_line_of_a_document_is_cited(self):
        f = flat(STATE['rung3'])
        operator = f.split('3b. Device code in the monitor')[0] + ' ' + f.split('3c. The panel as the kernel\'s console with cpus=1')[1]
        for ref, path, heading, fragments in r3.DOC_CITES:
            lines, fenced, at, end, level = (ROOT / path).read_text().splitlines(), False, None, None, len(heading.split()[0])
            for number, line in enumerate(lines):
                if line.lstrip().startswith('```'):
                    fenced = not fenced
                elif fenced:
                    continue
                elif at is None and line.startswith(heading):
                    at = number
                elif at is not None and re.match(rf'#{{1,{level}}} ', line):
                    end = number
                    break
            with self.subTest(ref):
                self.assertIsNotNone(at, f'{path} has no section that opens with {heading!r}')
                body = '\n'.join(lines[at:end])
                for fragment in fragments:
                    self.assertIn(fragment, body)
                self.assertIn(ref, operator, 'in the table, and the text does not cite it')
        section3c = f.split('3c. The panel as the kernel\'s console with cpus=1')[1].split('4. Time box')[0]
        self.assertEqual(re.findall(r'docs/[A-Za-z0-9_-]+\.md:\d+', section3c), [], 'the documents move: a section is cited, not a line')
        pattern = r'((?:probes|shadow|compat|tools)/[A-Za-z0-9_./+-]+\.(?:c|h|py)):(\d+)(?:-(\d+))?'
        cited = {(p, int(a), int(b or a)) for p, a, b in re.findall(pattern, section3c)}
        self.assertLessEqual(cited, {(p, a, b) for p, a, b, _ in pn.CITES}, 'every path:line of section 3c is in the table that holds its fragments')
        self.assertIn('compat/boot-relocate.h:147-153', flat(' '.join(r3.stand_extra({}))))

    def test_nothing_is_called_verified_and_a_reading_of_a_file_is_never_called_observed(self):
        f = STATE['rung3']
        self.assertNotIn('VERIFIED', f)
        self.assertTrue(f.isascii())
        tails = (' on the A16', ': docs/', ' once', ', one unit, one boot', ' (B1 attempts 1 to 5, operator report)', ' unless a sentence says INFERRED')
        parts = self.sections()
        for name in ('section0', 'section0a', 'section3c', 'section5d'):
            text = parts[name]
            for m in re.finditer('OBSERVED', text):
                with self.subTest(name, at=text[m.start():m.start() + 40]):
                    self.assertTrue(any(text[m.end():].startswith(tail) for tail in tails))
        for rel in ('tools/sp11_stick_bundle_panel_rung3.py', 'tools/sp11_stick_bundle_panel.py'):
            self.assertNotIn('OBSERVED on a host', (ROOT / rel).read_text())
        for part in (f.split('0. What approving')[1].split('1. What may run')[0], f.split('5d. Rung 3')[1]):
            self.assertTrue(all(len(line) <= 120 for line in part.splitlines()))

    def test_the_record_leaves_a_sixteenth_of_the_limit_free_and_the_stop_rule_is_rung_2s(self):
        self.assertLess(len(STATE['rung3'].encode()), bb.AUTHORISATION_CAP * 15 // 16)
        self.assertEqual(flat(STATE['rung3']).count(f'if the SNAPREP page is not on the screen {WAIT} seconds after the MON CALL line, FORCE OFF'), 4)
        self.assertEqual(flat(STATE['rung3']).count('- Panel as the console (sections 3c and 5d):'), 1)

    def test_the_notes_hold_k10_and_the_rows_of_the_guest_and_the_option(self):
        texts = {name: (ROOT / f'docs/SP11-STICK-{name}.md').read_text() for name in ('MONITOR', 'REAL', 'BUILD')}
        monitor, real, build = (' '.join(texts[name].split()) for name in ('MONITOR', 'REAL', 'BUILD'))
        k10 = ' '.join(texts['MONITOR'].split('**K10, rung 3')[1].split('\n\n')[0].split())
        self.assertLess(monitor.index('**K9, boot 21'), monitor.index('**K10, rung 3'), 'K9 is boot 21\'s, K10 is rung 3\'s')
        for needle in ('`--boot-arg cpus=1`', '`BOOT_ARG_REFUSED`', '`M1RUN-CPUS1-`', '`PIN_STRING`', 'H17', 'Caps raised on purpose, nothing cut'):
            self.assertIn(needle, k10)
        self.assertNotIn('VERIFIED', k10)
        self.assertIn('| H17 | test-sp11-stick-monitor-rung3.py |', monitor)
        self.assertRegex(real, r'\| GUEST\.DAT, panel console with `cpus=1` \(rung 3\) \| 118,908,480 bytes, 46 chunks')
        self.assertRegex(real, r'\| GUEST\.DAT SHA-256, console, cpus=1, 0 \| [0-9a-f]{64} \|')
        self.assertIn('`--boot-arg cpus=1` (rung 3', build)


if __name__ == '__main__':
    unittest.main()
