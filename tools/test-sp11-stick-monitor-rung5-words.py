#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The words of rung 5 of the monitor entry run (tools/sp11_stick_bundle_panel_rung5.py and ..._rung5d.py; docs/SP11-STICK-MONITOR.md section 20, K13): a phrase pin and a deletion mutant for each
sentence of the record, every time-dependent sentence follows the box, the keyed block of rung 4's result is held to the record's rules, and the findings of the reviews are in the text. The
mechanism (the one combination that builds it, the named refusals, the cap of the pair, the label, the guest, the six kinds against each other, the size of the files and K13) is
tools/test-sp11-stick-monitor-rung5.py, whose fixtures run here.

    python3 tools/test-sp11-stick-monitor-rung5-words.py

Boot 24's numbers are the coordinator's reading of the operator's film (OBSERVED, one unit, one boot); what the acknowledge does is read in the monitor's source and is INFERRED. Nothing here
runs the unit: Hardware NOT RUN."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung5_mechanism', TOOLS / 'test-sp11-stick-monitor-rung5.py')
M5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M5)
PS, PT, STATE, flat, r5d, m1t, m1g = M5.PS, M5.PT, M5.STATE, M5.flat, M5.r5d, M5.m1t, M5.m1g
pn, rp, r2, r3, r5, m1, rec, BT, ch, sb, bb, BLD, AUTH = M5.pn, M5.rp, M5.r2, M5.r3, M5.r5, M5.m1, M5.rec, M5.BT, M5.ch, M5.sb, M5.bb, M5.BLD, M5.AUTH
LABEL, P, F, BOXES, OLD, NAMES, HELD_LIMIT = M5.LABEL, M5.P, M5.F, M5.BOXES, M5.OLD, M5.NAMES, M5.HELD_LIMIT
record5, build5, chunks5 = M5.record5, M5.build5, M5.chunks5
FIVE_D = '5d. Rung 5: what is new'


def setUpModule():
    M5.setUpModule()


def tearDownModule():
    M5.tearDownModule()


def pin_problems(text, pins):
    """The names of the pins whose phrase is not in `text` (line breaks gone)."""
    f = flat(text)
    return [name for name, phrase in pins.items() if flat(phrase) not in f]


def mutants_rejected(test, text, pins):
    """`text` holds each phrase of `pins` once, and without one phrase it is rejected by the pin of that phrase alone."""
    f = flat(text)
    test.assertEqual(pin_problems(f, pins), [])
    for name, phrase in pins.items():
        with test.subTest(pin=name):
            test.assertEqual(f.count(flat(phrase)), 1, 'each sentence is in the text once')
            test.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), pins), [name])


def sections(text):
    f = flat(text)
    head3c = '3c. The panel as the kernel\'s console with the CPU-start stores acknowledged and let_panic'
    return dict(section0=f.split('0. What approving this text decides (read this first)')[1].split('0a. Where things stand')[0],
                section0a=f.split('0a. Where things stand')[1].split('0b. The hazards, in plain words')[0],
                section3c=head3c + f.split(head3c)[1].split('4. Time box')[0], section5d=f.split(FIVE_D)[1])


PINS_0 = {
    'decides': 'APPROVING THIS TEXT ALSO DECIDES THREE THINGS THAT NO RUN HAS HAD TOGETHER. (1) A MONITOR THAT ACKNOWLEDGES THE GUEST\'S CPU-START STORES, WHICH IS EXPERIMENTAL AND NOT A MODEL OF THE '
               'HARDWARE (section 3c): no CPU is started, the three stores are dropped, and no store class is added (section 3).',
    'decides words': '(2) A guest command line with cpu_boot_timeout_secs=120 and without cpus=1, so that the guest keeps its six CPUs.',
    'decides reach': 'what it can reach there is what every run of this monitor can reach, the existing stage-2 map and the monitor\'s models and report window, with no new register, no new '
                     'system-register class and no new store class (read on the host in compat/cpu-start-ack.h, a file and not a run; the census of section 3 is the plain monitor\'s but for the '
                     'addresses of six data words), and this approval is for that reach for up to the box.',
    'decides run': 'The panel as the console (boot 21) and the monitor\'s let_panic word (boot 24) have run; this combination has not.',
    'why': 'WHY RUNG 5. Rung 4 (boot 24) printed the kernel\'s panic text: an ApplePMGR assertion on the number of CPU complexes, 2.247 s into the call,',
    'why topology': 'SO THIS RECORD DROPS cpus=1: the guest has its six CPUs again, as in boot 21, which passed that driver and stopped at the first CPU-start write, IPA 0X300734004 (INFERRED to be '
                    'XNU starting CPU 1).',
    'why three': 'THIS RECORD CHANGES THREE THINGS FROM RUNG 4, TOGETHER. (1) THE MONITOR ACKNOWLEDGES THE CPU-START STORES:',
    'why dropped': 'NO CPU IS STARTED, nothing ever answers "started", and the three stores are DROPPED.',
    'why model': 'That is an EXPERIMENTAL DIAGNOSTIC and NOT A MODEL OF THE HARDWARE: on the silicon the write starts a core, and the kernel then waits for it to announce itself (INFERRED from the '
                 'public XNU source, not read in this kernel).',
    'why count': 'How many stores the monitor took is UNKNOWN to the page and to the film (the plain reporter does not print it).',
    'why timeout': 'so that XNU waits for the CPU that never announces itself for 120 s, longer than the {box} s box, instead of panicking about 1 s after the start',
    'why d5': 'D5\' (decided by the operator on 2026-11-18, the answer "Yes, up to 60 s"): this record, the one after rung 4, may use a time box of up to 60 s, with AC power, one CPU running (the '
              'guest\'s tree lists six, and the acknowledge starts none) and no temperature readout (D8\'); it is approved by its own SHA-256, and its box is {box} s',
    'prediction pointer': 'THE PREDICTION, ITS FALSIFIERS AND WHAT THE FILM DECIDES are in section 5d, in the operator\'s words (INFERRED; section 3c has the evidence).'}
PINS_0A = {
    'boot 21': 'Boot 21 (rung 2, record 1ad122b3...fc3b; one unit, one boot; the operator\'s film, read at full resolution) ran the monitor entry with the panel as the kernel\'s console',
    'boot 21 pc': 'that PC is the one store of ApplePMGR\'s base register-write routine, on the path cpu_boot_thread, processor_boot, cpu_start, enableCPUCore and configMiscCores (read on the host in '
                  'the kernelcache, a file and not a run).',
    'boot 23': 'Boot 23 (rung 3, record 749361cf...a1a7; one unit, one boot; the operator\'s film) added cpus=1: it was honoured',
    'boot 23 stop': 'XNU\'s own debugger-trap word, which the kernel executes when it panics (INFERRED, read on the host); the monitor stopped there, so no text was printed.',
    'keyed': 'Rung 4 (record 67d814f6...42fa, boot 24; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 4 PANIC TEXT.',
    'k panic': 'The panel showed the kernel\'s panic text (OBSERVED once; the coordinator\'s transcription of the film, the digits checked in two frames): panic(cpu 0 caller 0xfffffe00097ddef0): '
               '"ApplePMGR: virtual void ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount" @ApplePMGR.cpp:4861',
    'k nested': '"Nested panic count exceeds limit 5, machine will reset or spin" and "wdog: panic chipreset". The monitor\'s console ring carried the first lines of the same text onto the page as '
                'MON CON lines (OBSERVED once; boots 21 and 23 had none;',
    'k stop': 'The call returned 2.247 s in (TICKS=43133334 at 19.2 MHz; boot 23: 2.166 s) and the page said MON STOP S2_FAULT PC=0XFFFFFE0008BA7358 A=0X3082BC224 B=0X938A0005 '
              'C=0XFFFFFE612A564224, on a clean report (OBSERVED once).',
    'k refused': 'No SPTM call was refused after a panic entry (INFERRED: none was made, or none that this monitor refuses): the frozen monitor let the text print.',
    'k cause': 'The cause, read on the host from the kernelcache file, the guest\'s device tree and the public XNU source (a file and not a run; no kernelcache bytes are quoted; INFERRED where it '
               'says so): the assertion follows XNU\'s topology and not a hardware read.',
    'k topology': 'With cpus=1 XNU has one cluster (INFERRED: the kernelcache\'s parser has the skip and limit tests of the public source), so complex 1 is out of range and asserts; boot 21, with '
                  'six CPUs and two clusters, passed this driver.',
    'k no change': 'so no guest-data change keeps a one-CPU topology past this driver (INFERRED): cpus=2 to 4 leave one cluster as well (a prediction of the same host replay, not run), and cpus=5 '
                   'or cpumask=0x11 pass the assertion but start a CPU and meet the CPU-start write again (INFERRED).',
    'k wdt': 'and the host analysis puts that address in the watchdog node (wdt) of the guest\'s device tree, as one 32-bit register (INFERRED; read on the host, a file and not a run).',
    'k unknown': 'UNKNOWN: the value of the complex count at the stop (nothing read it) and whether the hardware itself, with cpus=1, asserts the same way.',
    'k repeat': 'each nested panic repeats because the kernel\'s panic-record routine stores through a pointer that stays NULL when the guest\'s device tree has no panic-log region, so it faults the '
                'same way each time, and the monitor lets the kernel\'s own page faults through (probes/xnuboot/trap_dispatch.c:370-387): the repetition is the kernel\'s and not an artefact of '
                'the monitor.',
    'stick': 'The stick after boots 21, 23 and 24 was unchanged but for the Shell\'s marker and the Mac\'s own .fseventsd entry'}
PINS_3C = {
    'in short': 'In short. This record is rung 5, after boot 24 (rung 4), whose panic in the PMGR driver the host analysis reads as XNU\'s one-cluster topology under cpus=1',
    'intro': 'Rung 4 is the run before this one: the panel as the console with cpus=1 and let_panic, record 67d814f6...42fa, boot 24 (section 0a); its approval is spent. This record is rung 5:',
    'programs': 'the wrapper with the acknowledging monitor (053a239f..., 608 bytes longer than the plain one, ad4ebaa2...), built with the box cap at 120 s in place of 5 s '
                '(SP11_STICK_MONITOR_BOX_MAX_S, section 4).',
    'reporter': 'SNAPREP-M1-ACK.BIN is the bytes of SNAPREP-M1.BIN, the reporter of the earlier runs',
    'differ': 'SNAPMON.BIN (the acknowledging monitor), GUEST.DAT (the command line in boot_args and the time box in PARAMS), PIN.DAT (the label and the digests) and PAYLOAD.MAN, which lists '
              'them, differ from rung 4\'s; SNAPREP.BIN does not.',
    'label': 'The builder puts ACKPANIC- after M1RUN- for this record, in place of CONSOLE-, and refuses a facts label that starts with it, with CONSOLE-, CPUS1-, LETPANIC- or PANEL-',
    'params row': 'every other PARAMS word is that of the same guest without the word: the builder compares the finished PARAMS chunk with the one it makes without it and refuses a difference '
                  'elsewhere (PARAMS_LAUNCH), and holds permissive and skip_undef at 0',
    'other': 'From rung 4\'s guest this one differs in the command line in boot_args (cpu_boot_timeout_secs=120 where rung 4\'s has cpus=1) and the time box in PARAMS, and in nothing else',
    'console': 'Console. What the operator saw in boots 21 and 24 (OBSERVED once each) and expects to see again (INFERRED): XNU\'s own boot text, drawn by the kernel itself into the panel while the '
               'guest runs.',
    'console mon con': 'held, in boot 24, the first lines of the panic text (OBSERVED once); whether kprintf text reaches them is UNKNOWN.',
    'word': 'The extra word. The command line is the loader\'s with serial=2 and, after it, cpu_boot_timeout_secs=120 ({line}): one word, from the builder\'s list',
    'word chain': 'the kernel\'s CPU-boot thread calls processor_boot for all five secondary CPUs first, and processor_boot does not wait; only then, in that dedicated thread, '
                  'sched_cpu_init_completed waits for each CPU in turn, CPU 1 first',
    'word iokit': 'kernel_bootstrap_thread does not wait for the CPUs, so IOKit matching, bsd_init and the root mount are not blocked by that wait in the source; what waits is the resource '
                  'IOAllCPUInitialized',
    'ack': 'The acknowledge. Boot 21\'s stop was a 32-bit store to IPA 0X300734004 at PC 0XFFFFFE00097A352C (section 0a), the one store of ApplePMGR\'s base register-write routine',
    'ack none after': 'and after the write there is no read, poll, retry or wait in it (read on the host), so the acknowledge is enough.',
    'ack count': 'N=10 AT4=5 AT8=3 AT12=2 in the monitor\'s window (INFERRED, a prediction that nothing in this run can confirm or refute: the page does not print the count; 10 to 15 stores in all, '
                 'the order per CPU not established).',
    'ack match': 'The monitor takes such a store only when it is a valid-syndrome 32-bit store at exactly one of the three addresses, as a stage-2 translation fault, with no secondary CPU '
                 'configured; any other access keeps the stop',
    'ack isv': 'That only a single-register load or store gives a valid syndrome (a pair, a writeback form, an atomic, a SIMD store or DC ZVA does not) is INFERRED from the Arm architecture '
               'text as remembered, not read here.',
    'ack writes': 'The monitor changes nothing of the guest but its resume address (ELR, 4 bytes on) and writes nothing outside its own RAM and report window',
    'reach': 'What the acknowledge lets the guest reach. A guest that is not stopped at the CPU-start write runs on past a point that no run has passed, for up to the box.',
    'reach census': 'the acknowledging build adds no register, no system-register class and no store class (read on the host: its census is the plain monitor\'s but for the addresses of six data '
                    'words)',
    'reach rank': 'Which device the kernel touches first after the acknowledged stores is UNKNOWN (the host analysis expects the video decoder\'s window at IPA 0X380001000 first, INFERRED): the '
                  'six CoreSight lock models of the staged PARAMS cover the key write that cpu_start makes for CPUs 1 to 5,',
    'still stops': 'What still stops the run (read on the host, in the sources named; whether the guest reaches each is INFERRED):',
    'still stops boot 24': 'In boot 24 the text printed with no such stop before it (INFERRED: no call was refused after the panic entry).',
    'panic path': 'What the panic path may reach (boot 24 showed it once, OBSERVED; the rest is INFERRED): XNU draws its panic text with the video console into the panel range after its panic-entry '
                  'call, and boot 24 printed it, then nested panics with an empty string,',
    'page': 'What the page can and cannot show. It prints the first five events of the monitor\'s log, the counts and the stop line, which the runner clips at 100 columns (C= may lose its last '
            'digits), and the first lines of the console ring as MON CON lines (boot 24: the first lines of the panic text, OBSERVED once).',
    'ends': 'What the run may end as. The prediction (INFERRED from the host analysis of boots 21 and 24: none of it has been seen with the acknowledge), its falsifiers and the other ends are in '
            'section 5d, in the operator\'s words',
    'screen': 'so the report that SNAPREP-M1-ACK.BIN draws after the stop only adds glyph pixels over whatever the kernel left in its region and clears nothing',
    'screen cell': 'Its cell is 8 x 16 pixels, read on the host (a file, not a run) in the kernelcache (initialize_screen, at 0xfffffe000ba55740), so 120 rows of 360 columns on this panel'}
PINS_5D = {
    'new': 'WHAT IS NEW. The monitor ACKNOWLEDGES the guest\'s CPU-start stores (EXPERIMENTAL; not a model of the hardware: NO CPU IS STARTED and the three stores are dropped: section 3c).',
    'new box': 'The box is {box} s (D5\' and D8\': one CPU loaded for about {box} s, AC power REQUIRED, no temperature read).',
    'expect': 'WHAT TO EXPECT. As in boots 21 and 24: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the '
              'guest has stopped, about {box} s after the MON CALL line if the box is used, sooner on a stop.',
    'static': 'The panel may stand still for most of that time while the kernel waits for the CPU that never comes (UNKNOWN): that is not a stop: only the report page (about {box} s) or, if it '
              'does not come, the {wait} s rule of section 5c ends the wait.',
    'count': 'Count those {wait} seconds from the moment the MON CALL line is on the screen, on a watch or the camera\'s timer.',
    'predicts': 'WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED; no probabilities; none of it has been seen; read on the host in the kernelcache, a file and not a run, and in the public '
                'source). PREDICTION: the PMGR driver\'s start passes, as in boot 21 (two clusters);',
    'predicts no wait': 'and after the write there is no read, poll, retry or wait in that path, so the acknowledge is enough; no CPU starts;',
    'predicts iokit': 'IOKit matching and the root mount are not blocked by that wait in the source (INFERRED; whatever waits for all CPUs to be initialised waits, UNKNOWN which), so the kernel '
                      'goes on on the boot CPU;',
    'predicts ends': 'and the run ends at the box (MON STOP WATCHDOG, the log showing how far it got), at the first device that the monitor does not model (MON STOP S2_FAULT with another A=: '
                     'progress, and a new question; the host analysis names the video decoder\'s window at IPA 0X380001000 as the likeliest first one, INFERRED, so the run may well end within '
                     'seconds of the acknowledged stores and leave most of the {box} s box unused, which is no failure of this record; the others and the order are UNKNOWN) or in a new driver '
                     'panic whose text now prints.',
    'falsifiers': 'FALSIFIERS: the same ApplePMGR assertion (the topology reading is wrong); a MON STOP S2_FAULT with A= in 0X300734004..0X30073400C (the acknowledge did not take: either an '
                  'access that the monitor does not take by design, section 3c, or a fault of the monitor: B= on the page is the syndrome, and the coordinator decodes it before saying which), a '
                  'READ of that block being new and unexpected, since nothing after the write reads; a panic text "cpu 1 failed to boot for the first time, waited N seconds" (the timeout word was '
                  'not honoured).',
    'film': 'THE FILM is rung 4\'s, from the Shell\'s countdown until 120 seconds after the SNAPREP report page has appeared, or, if it never appears, until 120 s after the last page change '
            '(about {film} s in all for this box)',
    'film shows': 'It must show PAYLOAD CALL 0 (only the film shows it once the kernel has cleared the panel: if it does not, the approval is spent), the kernel\'s log, any panic text and the '
                  'report with MON STOP and MON EXIT.',
    'film part': 'THE PANIC TEXT, if there is one, is the result and may scroll off the part of the panel left of the report page (about its left 55 percent) (INFERRED): nobody touches the '
                 'keyboard, the screen or the buttons while it runs (except FORCE OFF as step 5 of section 5c says).',
    'stills': 'After those 120 seconds, and BEFORE FORCE OFF (only when the SNAPREP page is on the screen; if it is not, FORCE OFF {wait} seconds after the MON CALL line as step 5 says, no stills '
              'first), take three sharp still photographs of that part of the panel, close and without glare, one of its top band, one of its middle and one of its bottom (asked, not required, '
              'and valuable).',
    'lowercase': 'Lowercase letters on the panel are the kernel\'s (boots 21, 23 and 24: OBSERVED once each).',
    'page': 'WHAT THE PAGE SHOWS. The first five events and the last stop, not the whole run (the monitor keeps at most the first 512 events: section 5b).',
    'page count': 'how many the monitor took is UNKNOWN to the page and the film, and no result rests on it.',
    'page con': 'boot 24\'s carried the first lines of its panic text (OBSERVED once), boots 21 and 23 had none; the rest of a panic text is on the panel only (INFERRED).',
    'decides': 'WHAT THE FILM DECIDES (what this record changes has not been seen; boots 21 and 24 are the comparison, OBSERVED once each).',
    'decides assert': 'the two falsifiers above read from it, and any other panic is the result.',
    'decides read': 'a MON STOP S2_FAULT with A= in 0X300734004..0X30073400C (a read there, B= saying WnR 0, would be new and unexpected)',
    'decides noise': 'NOT THE FAILURE: the noise lines of section 0a (the SPMI and RTKit lines OBSERVED once in boots 21 and 23, the AFKFirmwareService line in boot 21; INFERRED to come from the '
                     'monitor\'s empty models), which the film shows before any panic text.',
    'panic looks': 'WHAT A PANIC LOOKS LIKE (boot 24 showed one, OBSERVED once; the rest is INFERRED from XNU\'s panic format): a line that contains "panic(cpu" (the kernel may put its timestamp '
                   'in front) and a caller address',
    'panic not': 'The word "panic" alone is not one (the RTKit lines "General panic behavior" and the early line "debug_log_init: Error!! gPanicBase is still not initialized", OBSERVED twice in '
                 'boot 23\'s replay, are not a panic).',
    'panic cpu': 'with the word at 120 it cannot come inside the box.',
    'results': 'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 5\'s. The panel is read first and then the stop; a film is in the first class below that fits.',
    'results neither': 'NOT PASSED (a) and (b) are the failures. A WATCHDOG whose log stood still is a result of its own (below), neither.',
    'success': 'Success of this rung is progress: the kernel gets past the PMGR driver and the CPU-start write and the film shows where it goes on.',
    'r panic': 'RUNG 5 PANIC TEXT: the panel shows the kernel\'s panic text, whatever the page says and whatever the stop is. THE TEXT IS THE RESULT:',
    'r not a result': 'RUNG 5 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP',
    'r a': 'RUNG 5 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with A= in 0X300734004..0X30073400C again: the acknowledge did not take. Either the access is one that the monitor does '
           'not take by design (a read, another width, a syndrome that does not describe it: section 3c) or it is a fault of the monitor: B= on the page is the syndrome, and the coordinator '
           'decodes it before saying which. It is the first thing to find; the page does not say why.',
    'r a read': 'A read there would be new and unexpected: nothing after the write reads (section 3c).',
    'r watchdog': 'RUNG 5 WATCHDOG, NO TEXT: MON STOP WATCHDOG at the end of the {box} s box and no panic text:',
    'r progress': 'A log that was still growing, or went beyond boot 21\'s last line, is PROGRESS and not a failure;',
    'r new stop': 'RUNG 5 NEW STOP: no panic text and a MON STOP of another kind (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, DEVICE_RESET or another name)',
    'r b': 'RUNG 5 NOT PASSED, (b): a restart of the Surface before the report, no report',
    'r dark': 'the panel still dark {wait} seconds after the MON CALL line'}
PINS_HOOKS = {
    '0a heading': '0a. Where things stand (boots 20, 21, 23 and 24, 2026-11-18; one unit, one boot each; OBSERVED unless marked INFERRED)',
    'rung': 'this record is rung 5, the panel handed to the guest as the kernel\'s console (serial=2) with the CPU-start stores acknowledged, cpu_boot_timeout_secs=120 in its command line and the '
            'monitor\'s let_panic word set, and no approval covers another\'s boot',
    'ran': 'under a monitor that has never run on this machine in its acknowledging build; the plain build ran in boots 20, 21, 23 and 24 (section 0a).',
    'hazard': 'The monitor acknowledges the guest\'s CPU-start stores (section 3c) and no CPU starts: what a stock kernel does when its CPU never announces itself, for up to {box} s, is UNKNOWN '
              '(it may wait, panic or spin),',
    'hazard panic': 'and with let_panic set a panic runs the kernel\'s own panic path, which boot 24 showed printing its text (OBSERVED once) before the monitor stopped it at a device that has no '
                    'model.',
    'console 5a': 'its console is the panel (boots 21 and 24: the kernel\'s log and panic text were on it, OBSERVED once each) and the MON CON lines are expected to carry at most the first lines '
                  'of a panic text (boot 24: OBSERVED once; boots 21 and 23 had none)',
    'fault seen': 'OBSERVED twice: boot 21 stopped that way at A=0X300734004, which section 0a INFERS to be the first CPU-start write, and boot 24 at A=0X3082BC224, after its panic text',
    'fault note': 'A STAGE-2 FAULT WITH AN IPA IS SUCCESS (not when A= lies inside the panel range or in the three CPU-start words 0X300734004..0X30073400C: section 5d)',
    'stop seen': 'OBSERVED twice (boots 21 and 24, one unit, one boot each).',
    'film step': 'The film runs about {film} s for this box (the runner\'s loading, the call of {box} s, the stop and 120 s after the report page: an estimate, not a measurement): have the '
                 'camera\'s storage and battery ready for it.',
    'film until': '60 if the camera can) from the Shell\'s countdown until 120 seconds after the SNAPREP report page has appeared, or, if it never appears, until 120 s after the last page change',
    'step 6': 'For this record wait until 120 seconds after the SNAPREP page appeared, by the film, before that FORCE OFF (a margin, not a measurement). Before that FORCE OFF, and after those 120 '
              'seconds, take three sharp still photographs of the part of the panel left of the report page (about its left 55 percent)',
    'step 4': 'Then compare, on the PAYLOAD LOADED line, INDEX=0 ... SHA=the first sixteen digits of SNAPMON-M1-ACK.BIN in section 1 (boots 20, 21, 23 and 24 had SHA=1D32F5071506A00C: a page '
              'that still says that has the old monitor)',
    'kind lines': 'This record is of the choice that hands the guest the panel as the kernel\'s console (serial=2) with cpu_boot_timeout_secs=120 in its command line, with the acknowledging monitor and '
                  'the monitor\'s let_panic word set (section 3c)',
    'reading pointer': 'what counts as a result of rung 5. Section 3c is the evidence for it, which the coordinator reads.',
    'silence tail': 'The wait is 120 s after the STAGE=DONE line for this record (the film of the kernel\'s text: step 6 of section 5c): a lit page that has stopped there, with or without the '
                    'SNAPREP page, is FORCE OFF then (section 5c); without the SNAPREP page it is no result (section 5a).',
    'cite 491': 'MON STOP S2_FAULT with the address in A= (probes/xnuboot/trap_dispatch.c:491)',
    '5b d6': '(read from probes/xnuboot/report_layout.c, telemetry.c and probes/sp11-stick-reporter.c, INFERRED)'}


def values(box):
    return dict(box=box, wait=box + 60, film=r5.film_total_s(box), line=r5.LINE)


def formatted(pins, box):
    return {name: phrase.format(**values(box)) for name, phrase in pins.items()}


class Words(unittest.TestCase):
    def test_each_sentence_of_sections_0_0a_3c_and_5d_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = sections(STATE['r5-60'])
        for name, pins in (('section0', PINS_0), ('section0a', PINS_0A), ('section3c', PINS_3C), ('section5d', PINS_5D)):
            mutants_rejected(self, parts[name], formatted(pins, 60))

    def test_each_hook_sentence_is_pinned_and_a_record_without_it_is_rejected(self):
        pins, f = formatted(PINS_HOOKS, 60), flat(STATE['r5-60'])
        for name, phrase in pins.items():
            with self.subTest(pin=name):
                self.assertGreaterEqual(f.count(flat(phrase)), 1, 'in the text')
                self.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), {name: phrase}), [name])

    def test_every_time_dependent_sentence_follows_the_box(self):
        for box in BOXES:
            parts = sections(STATE[f'r5-{box}'])
            for name, pins in (('section0', PINS_0), ('section0a', PINS_0A), ('section3c', PINS_3C), ('section5d', PINS_5D)):
                with self.subTest(box=box, section=name):
                    self.assertEqual(pin_problems(parts[name], formatted(pins, box)), [])
            self.assertEqual(pin_problems(STATE[f'r5-{box}'], formatted(PINS_HOOKS, box)), [])
        f10, f60 = flat(STATE['r5-10']), flat(STATE['r5-60'])
        self.assertIn('Count those 70 seconds from the moment the MON CALL line is on the screen', f10)
        self.assertIn('Count those 120 seconds from the moment the MON CALL line is on the screen', f60)
        self.assertIn('about 190 s in all for this box', f10)
        self.assertIn('about 240 s in all for this box', f60)
        self.assertNotIn('70 seconds after the MON CALL line', f60)
        for text in (f10, f60):
            self.assertNotIn('the 5 s box', text.replace('the 5 s box was not used', ''), 'no sentence of its own calls the box 5 s')

    def test_the_findings_of_the_reviews_are_in_the_words(self):
        f = flat(STATE['r5-60'])
        for bad in ('That is a fault of the monitor and the first thing to find', '(the acknowledge did not take: a fault of the monitor)', '60 seconds after the SNAPREP report page',
                    'until 60 seconds after the SNAPREP page', 'a fault of the monitor (INFERRED reasons', 'any lowercase letter on the panel is the kernel\'s (INFERRED from section 5b'):
            self.assertNotIn(bad, f, 'the wording that the review of the first text asked to change')
        self.assertEqual(f.count('the coordinator decodes it before saying which'), 2, 'the falsifier and class (a) of section 5d: the syndrome is decoded before the monitor is blamed')
        self.assertEqual(f.count('until 120 seconds after the SNAPREP report page has appeared'), 2, 'step 2 of section 5c and section 5d')
        self.assertEqual(f.count('a valid-syndrome 32-bit store at exactly one of the three addresses, as a stage-2 translation fault, with no secondary CPU configured; any other access keeps '
                                 'the stop'), 1)
        self.assertEqual(f.count('INFERRED, a prediction that nothing in this run can confirm or refute: the page does not print the count'), 1)
        self.assertEqual(f.count('contains "panic(cpu"'), 1)
        self.assertNotIn('starts "panic(cpu"', f)
        self.assertNotIn('VERIFIED', f)

    def test_rung_4s_result_is_a_keyed_block_that_a_wrong_verdict_or_a_bad_paragraph_cannot_fill(self):
        text = flat(' '.join(r5.rung4_lines()))
        self.assertIn('RUNG 4 PANIC TEXT.', text)
        self.assertEqual(r5.VERDICTS, ('PANIC TEXT',))
        for verdict in ('PASSED', 'NOT PASSED (a)', 'WATCHDOG NO TEXT', None):
            with mock.patch.dict(r5.RUNG4, verdict=verdict), self.assertRaises(AssertionError):
                r5.rung4_lines()
        for what, words in {'non-ASCII': ('A paragraph with a \u00e9 (OBSERVED once).',), 'the word that M1 records never use': ('Read in the film, VERIFIED once (OBSERVED).',),
                            'no label': ('A paragraph that says what happened and does not say how it is known.',)}.items():
            with self.subTest(what), mock.patch.dict(r5.RUNG4, words=words), self.assertRaises(AssertionError):
                r5.rung4_lines()
        self.assertEqual((r5.BOOT24_TICKS, r5.BOOT24_S), (43_133_334, '2.247'))
        self.assertEqual(r5.BOOT24_STOP, dict(pc=0xFFFFFE0008BA7358, a=0x3082BC224, b=0x938A0005, c=0xFFFFFE612A564224))
        self.assertEqual((0x938A0005 >> 26, (0x938A0005 >> 6) & 1, 0x938A0005 & 0x3F), (0x24, 0, 5), 'B= of boot 24: EC 0x24, WnR 0 (a read), DFSC 5 (a translation fault, level 1)')

    def test_section_5d_is_at_most_110_lines_and_the_reading_pointer_names_it(self):
        for box in BOXES:
            lines = r5.section_5d(STATE[f'r5-{box}-panel'], box + 60)
            self.assertLessEqual(len(lines), 110)
            self.assertTrue(all(len(line) <= 118 and line.isascii() for line in lines))
            self.assertEqual(len(STATE[f'r5-{box}'].split('\n5d. ')[1].splitlines()), len(lines))
        self.assertIn('sections 0, 0a, 0b, 4, 5, 5a, 5c and 5d', flat(STATE['r5-60']))

    def test_a_generator_without_one_of_the_blocks_makes_a_record_that_the_pins_reject(self):
        pins = {**{f'0/{k}': v for k, v in formatted(PINS_0, 60).items()}, **{f'0a/{k}': v for k, v in formatted(PINS_0A, 60).items()}, **{f'3c/{k}': v for k, v in formatted(PINS_3C, 60).items()},
                **{f'5d/{k}': v for k, v in formatted(PINS_5D, 60).items()}, **{f'hooks/{k}': v for k, v in formatted(PINS_HOOKS, 60).items()}}
        keys = m1g.PANEL['console+cpu_boot_timeout_secs=120+let_panic']
        dropped = lambda key, value: mock.patch.dict(keys, {key: value})      # noqa: E731
        for what, (patch, pin) in {'no why': (dropped('why', lambda real: ()), '0/why'), 'no boot facts in 0a': (dropped('stand', lambda real: ()), '0a/boot 21'),
                                   'no hazard in 0b': (dropped('hazard', ''), 'hooks/hazard'), 'no decision': (dropped('decides', ''), '0/decides'),
                                   'rung 4\'s count of runs': (dropped('ran', rp.RAN), 'hooks/ran'), 'no stop seen': (dropped('stop_seen', {}), 'hooks/stop seen'),
                                   'no 5d': (mock.patch.object(r5, 'section_5d', lambda panel, wait: ()), '5d/new'), 'rung 4\'s word text': (mock.patch.object(r5, 'word_text', rp.word_text), '3c/word'),
                                   'no extra paragraphs': (mock.patch.object(r5, 'extra_paragraphs', lambda panel: ()), '3c/ack'), 'rung 4\'s ends': (mock.patch.object(r5, 'expect_text', rp.expect_text), '3c/ends'),
                                   'no console note': (dropped('console', ''), 'hooks/console 5a'), 'no 0a heading': (dropped('stand_head', 'x'), 'hooks/0a heading'),
                                   'rung 4\'s silence bullet': (dropped('silence_tail', ''), 'hooks/silence tail'),
                                   'rung 4\'s step 6': (dropped('step6', rp.STEP6), 'hooks/step 6'), 'rung 4\'s step 4': (dropped('step4', rp.step4), 'hooks/step 4'),
                                   'rung 4\'s rung': (dropped('rung', rp.RUNG), 'hooks/rung'), 'rung 4\'s film': (dropped('film_until', r3.film_until), 'hooks/film until'),
                                   'rung 4\'s params row': (mock.patch.object(r5, 'PARAMS_ROW', rp.PARAMS_ROW), '3c/params row'), 'no fault note': (dropped('fault_note', ''), 'hooks/fault note')}.items():
            with self.subTest(what), patch:
                code, text, err = record5('mutant-r5')
                self.assertEqual(code, 0, err)
                self.assertNotEqual(text, STATE['r5-60'])
                self.assertIn(pin, pin_problems(text, pins), what)

    def test_every_source_line_it_cites_has_its_fragment_and_every_citation_is_in_the_table(self):
        pattern = r'((?:probes|shadow|compat|tools)/[A-Za-z0-9_./+-]+\.(?:c|h|py)):(\d+)(?:-(\d+))?'
        cited = {(p, int(a), int(b or a)) for p, a, b in re.findall(pattern, ' '.join(sections(STATE['r5-60']).values()))}
        mine = {(p, a, b) for p, a, b, _ in r5.CITES}
        known = {(p, a, b) for p, a, b, _ in (*pn.CITES, *rp.CITES)}
        self.assertLessEqual(cited - mine, known, 'cited, and no table holds the fragments of it')
        self.assertEqual(mine - cited, set(), 'every entry of the table is cited')
        for path, first, last, fragments in r5.CITES:
            held = '\n'.join((ROOT / path).read_text().splitlines()[first - 1:last])
            for fragment in fragments:
                with self.subTest(f'{path}:{first}-{last} {fragment}'):
                    self.assertIn(fragment, held)
        self.assertEqual(r5.CITE_DRIFT, {('probes/xnuboot/trap_dispatch.c', 472): 19})
        f = flat(STATE['r5-60'])
        self.assertTrue('(probes/xnuboot/trap_dispatch.c:491)' in f and 'trap_dispatch.c:472' not in f, 'rung 5 cites the moved line, rung 4\'s approved text keeps :472 for its own suite')
        self.assertIn('record(c, EV_S2_FAULT, *elr, ipa, esr, far);', (ROOT / 'probes/xnuboot/trap_dispatch.c').read_text().splitlines()[472 + 19 - 1], 'rung 4\'s cite, moved by the acknowledge')

    def test_nothing_is_called_verified_and_each_paragraph_says_what_it_is(self):
        f = STATE['r5-60']
        self.assertTrue(f.isascii() and 'VERIFIED' not in f)
        for rel in ('tools/sp11_stick_bundle_panel_rung5.py', 'tools/sp11_stick_bundle_panel_rung5d.py', 'tools/test-sp11-stick-monitor-rung5.py'):
            self.assertTrue((ROOT / rel).read_text().isascii(), rel)
        self.assertNotIn('VERIFIED', (ROOT / 'tools/sp11_stick_bundle_panel_rung5d.py').read_text())      # the module of 5a's keyed block holds the word once, in the rule that refuses it
        panel = STATE['r5-60-panel']
        for paragraph in (*r5.extra_paragraphs(panel), r5.word_text(), r5.expect_text(panel['run']), ' '.join(r5.stand_extra({})), ' '.join(r5.why_lines({})), *r5d.results_paragraphs(panel['run'], 120)):
            with self.subTest(paragraph[:40]):
                self.assertTrue(re.search(r'OBSERVED|INFERRED|UNKNOWN|THE RESULTS|RUNG 5', paragraph), 'each paragraph is labelled or is a result')
        self.assertTrue(all(len(line) <= 120 for line in f.split('0. What approving')[1].split('1. What may run')[0].splitlines() + f.split(FIVE_D)[1].splitlines()))


if __name__ == '__main__':
    unittest.main()
