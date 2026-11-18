#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the sections of the record of the monitor entry run that tell the operator what he decides, what the pages say and what he does
(tools/sp11_stick_bundle_record_m1_guide.py, docs/SP11-STICK-MONITOR.md section 19): the lines of the pages, the kinds of stop and the statuses against the C
sources of the wrapper and the reporter, the expected ends against the plan, the decisions and their dates, the digits the operator compares, the text's
independence of the path the NV2 header was read from, and the size of the approved text against the builder's cap.

    python3 tools/test-sp11-stick-monitor-text.py

Every check has a faulty case that it rejects: a prefix, a kind, a status or a digit that the sources do not hold, a stale sentence, a digest that carries a
path. The fixtures of tools/test-sp11-stick-monitor-auth.py are fabricated builds (no toolchain). The runner's own lines are read from the q1n1 checkout
($Q1N1_SP11_TREE) and SKIP without it, which is not a pass. No hardware: NOT RUN."""
import importlib.util
import os
import re
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import nv2_record as NV2  # noqa: E402
import sp11_stick_bundle_build as bb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_bundle_record_m1_guide as m1g  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_monitor_auth_tests', TOOLS / 'test-sp11-stick-monitor-auth.py')
AUTH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AUTH)
sha = AUTH.sha
SCRATCH = ROOT / 'build' / 'sp11-monitor-text-test-scratch'
REAL = dict(kernelcache_sha256='5' * 64, guest_profile='j700ap-26A428', panel='withheld', sptm_hvc_calls=0, seed='sp11-stick-bundle', descriptor_sha256='6' * 64,
            board_id='sp11', board_status='EXPERIMENTAL', ecam_windows=0)
PROBES = ('sp11-stick-wrapper.c', 'sp11-stick-report.c', 'sp11-stick-reporter.c', 'sp11-stick-monitor-record.h', 'sp11-stick-gic-record.h', 'sp11-stick-gic.c')
SOURCES = '\n'.join((ROOT / 'probes' / name).read_text() for name in PROBES)
REPORTER = (ROOT / 'probes/sp11-stick-reporter.c').read_text()
# The sentences that said "open" or described a state that later decisions or boot 14 changed; none may be in the text of the monitor entry run.
STALE = ('still open', 'is open', 'D6 (which NV2 record', 'MON STATUS -9', 'MON STATUS -24', 'a default, not an operator decision', '50% battery',
         'reaches EL2 at all is UNKNOWN', 'two boots found', 'Neither the quarantine nor the check has run on the unit',
         # what draft 2 said and the review found wrong (M1 to M4, m2, m3, m10): none may come back
         'The operator\'s own check of the stick is the control', 'D7 every file is named by its SHA-256', 'Nothing in the run writes any disk or the stick',
         'hold Power about 20 s to power off', 'that writes nothing.', 'nothing of M1 has run. Do not retry', 'power off in those 5 s (nothing has been written)',
         'Two restarts at the same last line stop the series', 'not a stop of the series', 'nothing else on the USB ports', 'AC power is REQUIRED (D8): the charger is in before the boot',
         'only the operator\'s power cycle ends', 'the operator\'s power cycle (hold Power about 20 s)', 'and 146 WFE words, so it does', 'it reads that much more than the guest',
         # what draft 3 said and the review of draft 3 found wrong (N1 to N4 and the nits): none may come back
         'it is no power-off); if Windows starts, shut it down from Windows', 'If the Shell then prints ALL STEPS DONE, the marker is working: do nothing, take the stick out.',
         'at least 16.5 s', 'the known parts alone are at least', 'so the figure is conservative', 'PLACEMENT WRITTEN, CHUNKS',
         'Rung 2, another file, kernel, patch value, time box or store class needs its own record.',
         # what draft 4 replaced when the box became a parameter and the first run's prepare and clean were measured
         'whose duration is UNKNOWN (never run on this unit)', 'about 0.6 s', 'monitor prepare UNKNOWN never run on this unit', 'its prepare is UNKNOWN (never run on this unit)',
         'prepare (UNKNOWN)', 'so this run does not meet it:', 'duration UNKNOWN: never run on this unit',
         # what draft 5 replaced when boot 20 had run: the facts of the first run of the monitor entry, labelled
         'under a monitor that has never run', 'stop path is NOT RUN', 'stop path NOT RUN', 'INFERRED from the sources, never run). No disk',
         'read from the monitor\'s sources (INFERRED: never run)', 'The time box should end as MON STOP WATCHDOG', 'and a pass of the gate is INFERRED', 'and a pass is INFERRED',
         'and has not run on the unit (hardware NOT RUN)', 'the first M1 run', 'together in the one run', 'and the guest is given RAM only (INFERRED; never run)',
         'maps it RAM only (INFERRED from the sources; never run)', 'no write below was run by the monitor on the unit')
# The sentences of the review of draft 2 that the text must carry (M1 to M4 and m2 first, then the minor ones): a phrase pin each, and a deletion mutant of each that the
# pin must reject. The phrases are in the words of the text, with the line breaks gone.
PINS = {
    'M1 the operator cannot check the EFI files': 'No check at boot compares them with this record, and the operator cannot on the unit',
    'M1 the control is the coordinator\'s': 'The control is the coordinator\'s, before the boot: the stager\'s verify and these two digests compared with the entries of '
                                              'STICK-FILES.TXT (section 5c)',
    'M1 D7 is named files only': 'D7 the payloads and data files that run are named by their SHA-256 (section 1), and the stick\'s other files are checked by the coordinator '
                                 '(section 5c)',
    'M1 5c makes the control': 'The control of the stick is the coordinator\'s, and it is made here, before the boot: each staged file is compared with the entries of '
                               'STICK-FILES.TXT, and so are the two EFI digests of section 1b, which nothing checks at boot.',
    'M1 the EFI files among the hazards': 'The two EFI files that run at EL2 before the wrapper (section 1b) are not checked at boot against this record, and the operator '
                                          'cannot check them on the unit: the control is the coordinator\'s, before the boot (section 5c).',
    'M2 no disk write is meant to be possible': 'No disk write is meant to be possible: the code of the wrapper, the monitor and the reporter stores to RAM, EL2 system '
                                                'registers and GIC registers only (section 3),',
    'M2 stage 2 is all that stops the guest': 'Stage 2 is all that stops the guest: an SMC would not be trapped and would reach firmware (section 3; the kernelcache holds '
                                              'none: OBSERVED on a host file). The one certain write to the stick is the Shell\'s marker file SP11\\DONE1.TXT, made before '
                                              'the step starts (section 5c).',
    'M3 the runaway case in 0b': 'If the stop timer never fires (the plan\'s second most likely failure) one CPU stays loaded, at worst, until the operator acts: 60 s of '
                                 'silence (section 5) plus about 20 s holding Power, 80 s or more, with no temperature read; approving this text accepts that too.',
    'M3 the runaway case in section 4': 'If the stop timer never fires (the plan\'s second most likely failure, section 5a) one CPU stays loaded, at worst, until the operator '
                                        'acts: 60 s of silence (section 5) plus about 20 s holding Power, 80 s or more, with no temperature read; approving this text accepts '
                                        'that too.',
    'M3 the hot rule outranks touch nothing': 'If at any time the Surface is too hot to hold comfortably, FORCE OFF at once and tell the coordinator: that outranks "touch '
                                              'nothing" and every wait of these steps, and once PAYLOAD CALL 0 has printed, the approval is spent.',
    'M3 the hot rule among the stop rules': 'if the Surface is too hot to hold comfortably, FORCE OFF at once.',
    'M4 FORCE OFF is defined': 'FORCE OFF, used below, means: hold Power alone for about 20 s by the clock until the Surface logo or Windows appears, then release',
    'N4 the restart is INFERRED for the Pro 11': '(that restarts the Surface; it is no power-off: INFERRED for the Pro 11)',
    'M4 the Volume-Up fallback': 'if nothing appears after 30 s, hold Volume-Up and Power together for about 15 s (Microsoft lists that for earlier models only; its effect on '
                                 'the Pro 11 is UNKNOWN), then release both.',
    'M4 the Microsoft source': '"Force a shutdown and restart your Surface", Surface Pro 5th Gen and later (the Pro 11 is not named there).',
    'N1 a restart goes to the firmware\'s boot order': 'A restart, FORCE OFF\'s or the Surface\'s own, goes to the boot order that the firmware has; which device the Pro 11 '
                                                        'tries first with the stick in is UNKNOWN.',
    'N1 a second start of the stick': 'If the stick starts again, the marker (written before the step) makes the Shell print ALL STEPS DONE and run nothing.',
    'N1 no Windows with Secure Boot off': 'Do not let Windows run with Secure Boot off (docs/RUNNING.md, Way A: a Windows boot by accident only asks for the BitLocker '
                                          'recovery key; if Windows does start, shut it down from Windows);',
    'N1 take the stick out at the logo': 'take the stick out as soon as the logo appears, so that nothing mounts it (row f);',
    'N1 a recovery-key screen': 'at a recovery-key screen type and choose nothing.',
    'M4 step 3 starts FORCE OFF at once': 'if either differs, start FORCE OFF at once and say what differs on the video: the 5 s end before a 20 s hold does, so the step may '
                                          'still start',
    'M4 step 4 the call may have begun': 'start FORCE OFF at once and tell the coordinator; the call may already have begun. Do not retry.',
    'M4 step 6 writes nothing of ours': 'FORCE OFF: that writes nothing of ours.',
    'M4 step 7': 'then FORCE OFF, DO NOT RETRY, and tell the coordinator.',
    'M4 the hardware rule': 'A spinning guest is ended by the operator\'s FORCE OFF (section 5c), which writes nothing of ours',
    'M4 the silence rule': 'note the last line and FORCE OFF (section 5c). The 60 s is an operator-safe wait',
    'm1 GUEST_EXC prints x30': 'PC= is the guest\'s x30, not its PC; A=, B= and C= are its ESR, FAR and ELR, and C= is the address that row c checks',
    'm1 row c': 'except on GUEST_EXC, where PC= is the guest\'s x30 and C= is the address to check',
    'm1 a pass has A= not 0': 'with PC= a kernel address (C= on GUEST_EXC) and A= not 0 on S2_FAULT',
    'm2 one boot': 'THIS APPROVAL COVERS ONE BOOT. A boot that printed PAYLOAD CALL 0 {mon} has spent it, whatever follows.',
    'n6 a film that does not show it counts as printed': 'A boot whose film does not show whether PAYLOAD CALL 0 printed counts as printed: the approval is spent.',
    'm2 a boot that did not print the line': 'A boot that did not print that line ran nothing of M1 (the marker may exist: the coordinator deletes it) and is repeated only with '
                                             'the same files, after the coordinator has found the cause.',
    'n7 rung 2 is defined': 'Another file, kernel, patch value, time box or store class needs its own record, and so does rung 2, the run with the panel handed to the guest.',
    'm2 the rule of 5 names the one boot': 'A boot that printed PAYLOAD CALL 0 has spent the approval, and the same bundle is not run again; a boot that did not is the '
                                           'coordinator\'s to repeat or not (section 5c, THIS APPROVAL COVERS ONE BOOT).',
    'm3 the charger': 'the charger may be in Surface Connect or a USB-C port, and stays in from before the boot until the end',
    'm3 no other device': 'NO cable to the Mac, no Mac logger, no other device on the USB ports',
    'm4 the GIC': 'The GIC: from the first GIC store until after the monitor returns, this CPU\'s SGI 0 and PPIs 23, 25, 27 and 30, the guest\'s own timers (INTID 27, 30) among '
                  'them, are switched off and put back afterwards; if the call never returns they stay off until the restart (INFERRED: the firmware sets the GIC up again).',
    'm5 what the operator reads': 'The operator reads and acts on sections 0, 0a, 0b, 4, 5, 5a and 5c',
    'n8 the D6 paragraph is in the reading list': ', and reads in section 1b the paragraph on the NV2 record (D6); section 5b is the reference for the lines of the pages.',
    'm5 who the coordinator is': 'The rest of sections 1 to 3b is evidence that the coordinator (the assistant that builds the files and the stick and reads the film with the '
                                 'operator) checks against the files.',
    'N2 the pointer says one boot': 'Approving this text covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT).',
    'N2 section 0 says one boot': 'Approving this text covers ONE boot of this stick. A boot that printed PAYLOAD CALL 0 has spent the approval, and so has a boot whose film '
                                  'does not show whether it did (section 5c, THIS APPROVAL COVERS ONE BOOT).',
    'm5 what to hand back after a pass': 'Then give the coordinator every video (the countdown to the first UPTIME heartbeat after STAGE=DONE), say the time of day, and hand '
                                         'over the stick untouched: open, mount, edit or delete nothing on it before the coordinator has read it (row f).',
    'm7 row e': 'so TICKS <= 96,000,000 proves row e, and a larger value neither proves nor fails it (row e is then UNKNOWN unless the stop is MON STOP WATCHDOG, the '
                'monitor\'s own deadline);',
    'm8 the prefixes': 'Every line of payload 0 starts with SNAPMON and every line of payload 1 with SNAPREP; this guide leaves the prefix out. A line that this text does not '
                       'name is a finding for the coordinator, not a result.',
    'm8 the runner\'s DONE line': 'PAYLOAD DONE INDEX=n RETURN=0X.. STACK=OK|OVERFLOW LOADS= FAULTS= REFUSED= LINES= DROPPED=',
    'm8 the SNAPREP head lines': 'VERDICTS PIN= GUEST= POLICY= LINES=, two REGS lines',
    'm9 the basis of the regime': 'run 4 ran under the test-only no-sweep slbounce (docs/SP11-X2-TESTS.md:811-812), a plain slbounce whose return leaves E2H and TGE set (read '
                                  'in the source: docs/SURFACE-PRO-11.md E6, a host reading; OBSERVED once for the scoped-sweep build, 2026-11-12: docs/SP11-EL2-ENTRY.md M2)',
    'm10 the 2.9 s is conservative': 'was measured in the dry run with a 144.7 MB GUEST.DAT; this one is 118.9 MB, so that figure is conservative: it goes into the high sum, '
                                     'and the low sum uses the 2.4 s that it scales to (INFERRED: the copy and the verify take time in proportion to the size).',
    'N3 the low sum': 'the known parts, low sum about 15.4 s (2.4 + 8 + 5 + 0.04)',
    'N3 the high sum': 'the known parts, high sum at most 18.8 s (2.9 + 8 + 5 + 2.9), plus the prepare',
    'N3 the known parts against the 20 s': 'this run does not meet it, whatever the box: the known parts alone are about 15.4 to 18.8 s, and the runner\'s own load comes before '
                                           'them (UPTIME, below).',
    'N3 the exposure in 0b': 'one CPU is loaded for about 15.4 to 18.8 s plus the monitor\'s prepare',
    'N3 the exposure in section 4': 'time of one CPU, about 15.4 to 18.8 s plus the prepare in all',
    'b20 the prepare and the clean in the table': 'monitor prepare and stop clean 0.04 s OBSERVED once (boot 20: TICKS less the box)',
    'b20 the clean as a bound': 'stop: clean of 432 MiB, the bound at most 2.9 s (INFERRED: the dry run verified 141.4 MiB in 0.96 s, re-hash included)',
    'b20 what the box does not bound': 'nor the stop\'s cache clean (together about 0.04 s in boot 20, OBSERVED once; the prepare alone is UNKNOWN)',
    'b20 row e counts the prepare and the clean': 'the prepare and the stop\'s clean included (about 0.04 s together in boot 20, OBSERVED once: section 4)',
    'b20 the MON CALL line': 'and the stop with its cache clean (the prepare and the clean took about 0.04 s together in boot 20: section 4).',
    'b20 the gic class': 'The prepare and the clean took about 0.04 s together in boot 20 (OBSERVED once), the clean is bounded in section 4 (INFERRED), and the prepare '
                         'alone is UNKNOWN.',
    'b20 the silence rule\'s reason': 'its prepare and clean are known from one run only (about 0.04 s together: section 4)',
    'b20 a panic\'s text is filmed': 'the panic text, if it is in the first 1 KiB of the console buffer, is in the MON CON lines',
    'n9 the GIC put-back exception': 'If both group 1 put-backs fail after the return they stay off as well, and the page says GIC RESTORE CLEAN=NO (host model only; never run).',
    'n10 S2_FAULT with A=0 is a finding': 'A MON STOP S2_FAULT with A=0 is a finding too: no IPA could be named, so it is not the expected end above, though rows c and d hold.',
    'm1 A=0 is not the expected end': 'A=0 means that no IPA could be named, and that is a finding, not this end.',
    'n3 RAM UNTOUCHED and PLACEMENT WRITTEN only on a refusal': 'RAM UNTOUCHED BEYOND REPORT.DAT or PLACEMENT WRITTEN only when the wrapper refused (the first: before the '
                                                               'copy, and the page ends there; the second: at or after it)',
    'n5 silence before PAYLOAD CALL 0': 'No page change for 2 minutes at any point after the Shell\'s AUTORUN line and before the runner\'s PAYLOAD CALL 0 line: nothing of M1 '
                                        'has run; note the last line and FORCE OFF (section 5c).',
    'n5 the 2 minutes are no measurement': 'The 2 minutes is an operator-safe wait and not a measurement: the runner\'s own loading and hashing of the files is UNKNOWN in '
                                           'length. The approval is not spent (section 5c, THIS APPROVAL COVERS ONE BOOT).',
    'n5 silence after STAGE=DONE': 'The same 60 s holds after the STAGE=DONE line: a lit page that has stopped there, with or without the SNAPREP page, is FORCE OFF too '
                                   '(section 5c); without the SNAPREP page it is no result (section 5a).',
    'm6 the WFE words of the two segments': 'the same scan finds, in the two segments together, 1 SVC, 1 ERET and 148 WFE words, 146 of them in __TEXT_EXEC, so it does find '
                                            'instructions',
    'm2 not a result: the approval is spent only after PAYLOAD CALL 0': 'Whether the approval is spent depends on whether PAYLOAD CALL 0 printed (section 5c, THIS APPROVAL '
                                                                        'COVERS ONE BOOT).',
    'm8 no usable record of the wrapper': 'MON NO RECORD: THE STAGE DID NOT RUN, MON RECORD DAMAGED CODE=n, REPORT UNREADABLE and REPORT EMPTY: THE WRAPPER WROTE NOTHING: '
                                          'SNAPREP found no usable record of the wrapper. None is a result of the guest; the live page and the film say how far the wrapper got.',
    'm10 the pointer rebase': 'with only the loader\'s pointer rebase applied',
    # draft 5: the facts of boot 20 with their labels, and the block that says where things stand
    'w0 where things stand: the heading': '0a. Where things stand (boot 20, 2026-11-18, one unit, one boot; OBSERVED unless a sentence says INFERRED)',
    'w1 where things stand: the run': 'Boot 20 (record 31e5d4c2...a802) ran the monitor entry: the guest executed for its 5 s box, with 3,127 traps, 1,443 monitor calls and no '
                                      'stage-2 abort, and the run ended as MON STOP WATCHDOG at the kernel address 0xFFFFFE000C1EEF00.',
    'w2 where things stand: the cause is inferred': 'Why the guest was still at that address when the time box ended the run is INFERRED: an early loop in the guest. The same stop '
                                                    'address again, after a change meant to end the loop, would falsify that.',
    'w3 where things stand: the stick': 'The stick was unchanged but for the Shell\'s marker.',
    'w4 where things stand: no more': 'The hardware status of everything else is NOT RUN. This text claims no more: not that macOS boots, and not that anything is stable.',
    'd9 a withheld panel': 'a withheld panel leaves the guest\'s video description all zero, and stock XNU then spins in an early console loop (INFERRED, high: boot 20\'s stop '
                           'PC and the public XNU source), so a longer box on that image only repeats it',
    'f1 a monitor that ran once': 'under a monitor that ran once on this machine (boot 20: section 0a). What the kernel does there beyond what section 0a says is UNKNOWN.',
    'f2 stage 2 held (what the guest is given)': '(INFERRED from the sources; no stage-2 abort in boot 20\'s 5 s, OBSERVED once)',
    'f3 stage 2 held (RAM only)': 'and the guest is given RAM only (INFERRED; no stage-2 abort in boot 20\'s 5 s, OBSERVED once)',
    'f4 stage 2 held (the ram class)': 'INFERRED from the sources; no stage-2 abort in boot 20\'s 5 s, OBSERVED once). No disk and no RAM',
    'f5 no S2_FAULT has been seen': 'INFERRED: no run has stopped that way yet, UNKNOWN).',
    'f6 a whole box ends as WATCHDOG': 'A run that uses its whole time box ends as MON STOP WATCHDOG, and MON EXIT PC= beside it (OBSERVED once: boot 20, one unit, one boot).',
    'f7 the NV2 gate passed (5a)': 'the gate passed with the embedded record in boot 20: OBSERVED once; the regime that record was built in is still not recorded, INFERRED '
                                   'E2H = 1: section 1b)',
    'f8 the stop timer in 5a': 'the monitor\'s own stop path ran at the time box in boot 20, OBSERVED once, so this failure is possible and has not been seen)',
    'f9 WATCHDOG in the PASS bullet': 'WATCHDOG is the time box ending a run that used all of it (OBSERVED once: boot 20).',
    'f10 the NV2 gate passed (stop rules)': 'the record is embedded, and the gate passed with it once (OBSERVED, boot 20: MON STATUS=OK ENTERED=0X1, section 5a).',
    'f11 the NV2 gate passed (D6)': 'The gate passed with this record once (OBSERVED, boot 20: MON STATUS=OK), which shows that the register values matched and not which '
                                    'regime the record describes.',
    'f12 the stop path (section 4)': 'and the monitor\'s own stop path ended the guest at its box in boot 20: both OBSERVED, one unit, one boot each)',
    'f13 the stop path (the gic class)': 'and the monitor\'s own stop path ended the guest at its time box in boot 20 (OBSERVED once).',
    'f14 the arm half ran in boot 20': 'This build is the same source built for the monitor entry: it ran once in boot 20 (OBSERVED, one unit, one boot: GIC NEW ISEN=0X4000000, '
                                       'GIC RESTORE CLEAN=YES). The both-gates case was not exercised in boot 14 or boot 20: it stays host model only.',
    'f15 the stop path (the gic facts)': 'U3 answered; the monitor\'s own stop path ran later, in boot 20 (OBSERVED once);',
    'f16 each kind of stop says whether a run has shown it': 'The forms of the kinds below are read from the monitor\'s sources (INFERRED), and each says whether a run has shown it:',
    'f17 WATCHDOG has been seen': '. An expected end of M1. OBSERVED once (boot 20, one unit, one boot).',
    'f18 the other expected ends have not': '. An expected end of M1. Never seen (UNKNOWN).',
    'f19 a panic has not': '(film them). A finding (section 5a). Never seen (UNKNOWN).',
    'f20 an ASYNC or an SERROR stop has not': 'ELR_EL1. A finding (section 5a). Never seen (UNKNOWN).',
    'f21 the writes of the monitor on the unit are inferred': 'The monitor ran once on the unit (boot 20, OBSERVED once): that it wrote some of the registers below is INFERRED (the page shows MON '
                                                             'REGS RESTORED=YES, no write) and which, UNKNOWN (boot 11\'s payload wrote CNTHCTL_EL2 and HCR_EL2: OBSERVED).'}


def where(text, phrase):
    """The offset of `phrase` in `text`, wherever the lines were broken."""
    return re.search(r'\s+'.join(map(re.escape, phrase.split())), text).start()


def pin_problems(text, mon='1' * 16):
    """The pins that the text does not carry (`{mon}` stands for the 16 digits of payload 0)."""
    f = flat(text)
    return [name for name, phrase in PINS.items() if flat(phrase).replace('{mon}', mon) not in f]


def text_of(facts=None, real=REAL, **over):
    return rec.record_text({**AUTH.FIXED, 'm1': facts or AUTH.m1_facts(), 'real': real, **over})


def flat(text):
    return ' '.join(text.split())


def page_problems(prefixes, sources):
    """The prefixes of the page guide that no source prints: each is the start of a quoted string."""
    return [p for p in prefixes if f'"{p}' not in sources]


def names_of(function):
    """{case: name} of the `case N: return "NAME";` lines of one function of the reporter, a range test (`if (s >= ...`) not included."""
    body = REPORTER.split(f'static const char *{function}(')[1].split('\n}\n')[0]
    return {int(n): name for n, name in re.findall(r'case (-?\d+): return "([A-Z0-9_]+)";', body)}


def stale_problems(text):
    return [s for s in STALE if s in flat(text)]


POWER_CYCLE_OK = 'POWER CYCLE appears only after MON CALL'      # the wrapper's own words on the quarantine page, which 5b quotes


def power_cycle_problems(text):
    """Where the text says `power cycle` in any case, but in the words of the quarantine page that 5b quotes: FORCE OFF is the operator's one word for it (5c)."""
    f = flat(text)
    return [m.start() for m in re.finditer(r'power[ -]cycle', f, re.I) if not f.startswith(POWER_CYCLE_OK, m.start())]


def refusal_only_problems(source):
    """The two head lines that 5b says come only on a refusal: each must be printed under an `if (refusal` of the reporter and nowhere else."""
    guards = (('RAM UNTOUCHED BEYOND REPORT.DAT', r'if \(refusal && stage < SP11SB_STAGE_COPYING\) \{'), ('PLACEMENT WRITTEN', r'if \(refusal\) \{'))
    return [line for line, guard in guards if not re.search(guard + r'\s*rp_begin\(&l\);\s*pl_text\(&l, "' + re.escape(line) + r'"\);', source)]


def stand_problems(text):
    """What section 0a must hold: at most 12 lines of at most 120 columns, the facts of boot 20 with their labels, and no claim beyond them (macOS booting, stability)."""
    block = ('0a. Where things stand' + text.split('\n0a. Where things stand')[1].split('\n\n0b. ')[0]).splitlines()
    f = flat('\n'.join(block))
    need = ('31e5d4c2...a802', '3,127 traps', '1,443 monitor calls', 'no stage-2 abort', 'MON STOP WATCHDOG', '0xFFFFFE000C1EEF00', 'is INFERRED', 'falsify', 'Shell\'s marker', 'NOT RUN')
    return ([f'{len(block)} lines'] if len(block) > 12 else []) + (['a line over 120 columns'] if max(map(len, block)) > 120 else []) + [f'missing {n}' for n in need if n not in f] \
        + [f'claims {c}' for c in re.findall(r'(?<!not that )(macOS boots|macOS booted|anything is stable|stability)', f)]


def kind_label_problems(text):
    """Each kind of stop that 5b lists says whether a run has shown it: WATCHDOG, seen in boot 20, and no other."""
    part = ' ' + flat(text).split('whether a run has shown it: ')[1].split(' Any other name is a kind')[0]
    pieces = re.split(r' (?=(?:' + '|'.join(m1g.STOP_KINDS) + r'): )', part)[1:]
    wrong = [p.split(':')[0] for p in pieces if not p.endswith(m1g.STOP_SEEN.get(p.split(':')[0], m1g.STOP_NEVER))]
    return wrong + ([] if len(pieces) == len(m1g.STOP_KINDS) else ['a kind is missing'])


def decision_problems(text):
    """What the decisions block must say: D4 in capitals, the four decisions of 2026-11-17 and the earlier ones with their dates, and no AC power or 5 s left out."""
    section = text.split('0b. ')[0]
    flat_section = flat(section)
    need = ('THE SHA-256 OF EXACTLY THIS TEXT, WHEN THE OPERATOR APPROVES IT, ALSO DECIDES D4', 'the three classes ram, el2_sysreg and gic exactly as section 3 lists them',
            'Decided by the operator on 2026-11-17 (the answer "Accept all four (Recommended)", quoted from the app)', 'D6 the NV2 record: embed the first-image record',
            'D8 the thermal exception: accepted for ONE CPU, a time box of 5 s and AC power REQUIRED', 'D10 SNAP_FAIR_TOKEN=1 stays in the monitor\'s profile',
            'N1 the boot seed: the default label stays', 'Decided on 2026-11-16: D2 the stop is the monitor\'s own interrupt of the EL2 timer; D5 the time box is 5 s at most',
            'relaxes the 20 s UPTIME precaution that D5 left open', 'D7 the payloads and data files that run are named by their SHA-256 (section 1)', 'D9 the panel is withheld from the guest and the kernel is stock')
    return [n for n in need if n not in flat_section]


def digit_problems(text, monitor_sha, reporter_sha):
    f = flat(text)
    need = (f'INDEX=0 ... SHA={monitor_sha[:16].upper()} (SNAPMON.BIN)', f'INDEX=1 ... SHA={reporter_sha[:16].upper()} (SNAPREP.BIN)', f'PAYLOAD CALL 0 {monitor_sha[:16].upper()}')
    return [n for n in need if n not in f]


def path_problems(text, facts):
    """The digests of the build that would make the approved text depend on where the monitor was built."""
    return [d for d in (facts['monitor']['build_record_sha256'],) if d in text]


class Pages(unittest.TestCase):
    def test_every_line_that_the_page_guide_names_is_in_the_c_sources(self):
        wrapper_lines = [p for p in m1g.PAGE_LINES if not p.startswith('PAYLOAD')]
        self.assertEqual(page_problems(wrapper_lines, SOURCES), [])
        self.assertEqual(page_problems([*wrapper_lines, 'MON RETURNED=', 'STAGE:'], SOURCES), ['MON RETURNED=', 'STAGE:'], 'a prefix that no source prints is found')

    def test_the_runner_lines_are_in_the_q1n1_payload_document(self):
        tree = os.environ.get('Q1N1_SP11_TREE') or os.environ.get('Q1N1_DIR')
        doc = Path(tree or '/nonexistent') / 'docs/SP11-PAYLOAD.md'
        if not doc.is_file():
            self.skipTest('no q1n1 checkout ($Q1N1_SP11_TREE): the runner\'s lines are not read')
        runner = [p for p in m1g.PAGE_LINES if p.startswith('PAYLOAD')]
        self.assertEqual([p for p in runner if f'`{p}' not in doc.read_text()], [])
        self.assertEqual([p for p in (*runner, 'PAYLOAD LOADED=') if f'`{p}' not in doc.read_text()], ['PAYLOAD LOADED='])

    def test_the_kinds_of_stop_and_the_statuses_are_the_reporters(self):
        events, statuses = names_of('rp_event_name'), names_of('rp_status_name')
        self.assertEqual([k for k in m1g.STOP_KINDS if k not in events.values()], [])
        self.assertEqual([c for c in (*m1g.GATE_STATUSES, *m1g.SP11_STATUSES) if statuses.get(c[0]) != c[1]], [])
        self.assertEqual([k for k in (*m1g.STOP_KINDS, 'WATCHDOG_EXPIRED') if k not in events.values()], ['WATCHDOG_EXPIRED'])
        self.assertEqual([c for c in ((-91, 'NV2_OTHER_CORE'), (-91, 'NV2_NO_RECORD'), (-99, 'NV2_FEAT')) if statuses.get(c[0]) != c[1]], [(-91, 'NV2_NO_RECORD'), (-99, 'NV2_FEAT')])

    def test_the_quarantine_codes_and_the_stage_names_are_the_wrappers(self):
        header = (ROOT / 'probes/sp11-stick-monitor-record.h').read_text()
        for code in re.findall(r'\b0X[0-9A-F]{8}\b', '\n'.join(m1g.page_lines(AUTH.m1_facts(), 5))):
            self.assertIn(f'0x{code[2:]}'.lower(), header.lower(), 'the quarantine codes of the page guide are the monitor record\'s')
        stages = ' '.join((ROOT / 'probes/sp11-stick-bundle.h').read_text().split())
        guide = flat('\n'.join(m1g.page_lines(AUTH.m1_facts(), 5)))
        named = [n.strip() for n in re.search(r'STAGE=<name> says how far the wrapper got \(([A-Z_, ]+)\)', guide).group(1).split(',')]
        self.assertEqual([n for n in named if f'X({n}, ' not in stages], [])
        self.assertEqual([n for n in (*named, 'COPIED_OK') if f'X({n}, ' not in stages], ['COPIED_OK'])

    def test_the_numbers_of_the_page_come_from_the_build(self):
        facts = AUTH.m1_facts()
        guide = '\n'.join(m1g.page_lines(facts, 4))
        self.assertIn(f'MON IMAGE OFF=0X{facts["monitor"]["offset"]:X} BYTES={facts["monitor"]["bytes"]} SHA=0X{int(facts["monitor"]["sha256"][:16], 16):X} BOX_S=4', guide)
        self.assertIn(f'MON IDS MMFR2=0X{int(facts["nv2"]["id_aa64mmfr2_el1"], 16):X} MMFR4=0X0 REVIDR=0X0', guide)
        self.assertNotIn('BOX_S=5', guide, 'the time box is the record\'s')


class Meaning(unittest.TestCase):
    def test_the_expected_ends_are_the_plans_and_a_stage_2_fault_with_an_ipa_is_success(self):
        plan = (ROOT / 'docs/SP11-MONITOR-PORT.md').read_text()
        for words in ('watchdog, stage-2 fault with an IPA, guest exception', 'that is success for M1', 'M1 passes only if every row holds'):
            self.assertIn(words, plan)
        self.assertEqual(m1g.EXPECTED_ENDS, ('WATCHDOG', 'S2_FAULT', 'GUEST_EXC'))
        text = text_of()
        self.assertIn('A STAGE-2 FAULT WITH AN IPA IS SUCCESS', text)
        self.assertIn('MON STOP line (WATCHDOG, S2_FAULT or GUEST_EXC)', flat(text))
        faulty = text.replace('A STAGE-2 FAULT WITH AN IPA IS SUCCESS', 'A STAGE-2 FAULT IS A FAILURE')
        self.assertNotIn('A STAGE-2 FAULT WITH AN IPA IS SUCCESS', faulty)
        self.assertNotIn('is a finding, not a retry', flat(text), 'the plan\'s rule is worded so that it does not read as the opposite of the expected end')
        self.assertIn('When stage 2 stops the guest\'s access (MON STOP S2_FAULT, the IPA in A=) it is the expected end of M1', flat(text))

    def test_the_four_kinds_of_page_and_the_seven_rows_are_all_there(self):
        f = flat(text_of())
        for needle in ('M1 PASSED:', 'A FINDING,', 'M1 FAILED, a result', 'NOT A RESULT:', 'ONE RESTART BEFORE THE REPORT IS NOT A RESULT', *(f'{row} ' for row in 'abcdefg')):
            self.assertIn(needle, f)
        self.assertEqual(len(re.findall(r'\n   [a-g]    ', text_of())), 7, 'the rows a to g of docs/SP11-MONITOR-PORT.md section 2')

    def test_the_decisions_are_written_in_with_their_dates_and_no_sentence_is_stale(self):
        text = text_of()
        self.assertEqual(decision_problems(text), [])
        self.assertEqual(stale_problems(text), [])
        self.assertIn('Approving this text decides D4 for exactly these three classes, ram, el2_sysreg and gic, and no other (section 0).', flat(text.split('3. Store classes')[1]),
                      'section 3 says it again where the classes are listed')
        self.assertTrue(decision_problems(text.replace('ALSO DECIDES D4', 'also decides D4')), 'D4 is said in capitals: a lower case sentence is rejected')
        self.assertTrue(decision_problems(text.replace('AC power REQUIRED', 'AC power or at least 50% battery')))
        for stale in STALE:
            with self.subTest(stale):
                self.assertEqual(stale_problems(text + f'\n   {stale}\n'), [stale])

    def test_each_sentence_of_the_review_is_pinned_and_a_text_without_it_is_rejected(self):
        text = text_of()
        self.assertEqual(pin_problems(text), [])
        f = flat(text)
        for name, phrase in PINS.items():
            with self.subTest(name):
                target = flat(phrase).replace('{mon}', '1' * 16)
                self.assertIn(target, f)
                self.assertEqual(pin_problems(f.replace(target, 'DAMAGED')), [name], 'the pin of this sentence, and no other, rejects the text without it')

    def test_the_operator_is_told_force_off_and_never_power_cycle_but_in_the_words_of_the_quarantine_page(self):
        f = flat(text_of())
        self.assertEqual(power_cycle_problems(f), [])
        self.assertIn(POWER_CYCLE_OK, f)
        self.assertEqual(len(power_cycle_problems(f.replace(POWER_CYCLE_OK, 'Power cycle appears only after MON CALL'))), 1, 'the quoted words are the only ones allowed')
        # the four places where a text that went back to the old word was not found by any pin (the review of draft 3)
        for place in ('note the last line and FORCE OFF (section 5c). The 15 s is the silence rule', 'the monitor\'s window is partial. FORCE OFF (section 5c); never continue',
                      'RET=0X51554E01 or 0X51554E02: FORCE OFF (section 5c), and read nothing else of that page', 'ends only by the operator\'s FORCE OFF (section 5c), and this stage'):
            with self.subTest(place):
                self.assertIn(place, f)
                self.assertTrue(power_cycle_problems(f.replace(place, place.replace('FORCE OFF', 'power cycle'))), 'a text that says power cycle here is rejected')

    def test_the_two_head_lines_that_come_only_on_a_refusal_are_printed_only_under_one(self):
        self.assertEqual(refusal_only_problems(REPORTER), [])
        always = REPORTER.replace('if (refusal) {\n        rp_begin(&l);\n        pl_text(&l, "PLACEMENT WRITTEN");', 'if (1) {\n        rp_begin(&l);\n        pl_text(&l, "PLACEMENT WRITTEN");')
        self.assertNotEqual(always, REPORTER)
        self.assertEqual(refusal_only_problems(always), ['PLACEMENT WRITTEN'])
        untouched = REPORTER.replace('if (refusal && stage < SP11SB_STAGE_COPYING) {', 'if (stage < SP11SB_STAGE_COPYING) {')
        self.assertEqual(refusal_only_problems(untouched), ['RAM UNTOUCHED BEYOND REPORT.DAT'])

    def test_the_coordinator_is_defined_at_its_first_use_and_force_off_before_the_steps_that_use_it(self):
        text = text_of()
        first = text.index('coordinator')
        self.assertLess(first, text.index('0. What approving'))
        self.assertIn('the assistant that builds the files and the stick and reads the film with the operator', flat(text[first:first + 300]))
        steps = text.split('\n5c. The operator')[1]
        defined = where(steps, 'FORCE OFF, used below, means')
        for n in (1, 3, 4, 6, 7):
            self.assertLess(defined, steps.index(f'\n   {n}.  '), f'step {n} uses FORCE OFF after its definition')
        self.assertGreater(where(steps, 'THIS APPROVAL COVERS ONE BOOT. '), steps.index('\n   7.  '), 'the paragraph comes after the last step')

    def test_the_new_sections_stay_within_120_columns(self):
        text = text_of()
        for name, part in (('0 and 0b', text.split('0. What approving')[1].split('1. What may run')[0]), ('5 to 5c', text.split('5. Refusal')[1])):
            with self.subTest(name):
                self.assertLessEqual(max(map(len, part.splitlines())), 120)
        self.assertLessEqual(max(len(l) for l in text.splitlines() if l.startswith('   data       GUEST.DAT')), 120, 'the table digest has its own line (m10)')

    def test_the_gic_facts_of_boot_14_are_cited_with_their_labels(self):
        f = flat(text_of())
        for needle in ('docs/SP11-STICK-GIC.md section 12', 'docs/SP11-MONITOR-PORT.md section 13, WI 8', 'four times in window 1 of boot 14', 'once from EL1',
                       'ICENABLER0 written 0x4A800001', 'the put-back was whole', 'CNTV_CTL_EL0 read 0x5', 'SPI LINES=30 EN=3 PEND=9 BOTH=0', 'three boots, 12 to 14',
                       'The both-gates case was not exercised in boot 14'):
            self.assertIn(needle, f)

    def test_the_time_section_says_what_uptime_reads_and_that_approving_relaxes_the_precaution(self):
        f = flat(text_of())
        for needle in ('What UPTIME reads: it is q1n1\'s clock from before the runner loads and hashes the', 'it stays at 0 S while the runner\'s chain runs', 'docs/SP11-STICK-LOG.md section 14',
                       'reads well above 17 s (boot 14: 37 and 38 S', 'APPROVING THIS TEXT RELAXES THE 20 s PRECAUTION OF D5',
                       'No page change for 15 seconds at any point after the runner\'s PAYLOAD CALL 0 line and before the MON CALL line'):
            self.assertIn(needle, f)

    def test_the_digits_the_operator_compares_are_the_payloads_and_a_wrong_digest_is_found(self):
        text = text_of()
        self.assertEqual(digit_problems(text, AUTH.FIXED['monitor'][1], AUTH.FIXED['reporter'][1]), [])
        self.assertEqual(len(digit_problems(text, '3' * 64, '4' * 64)), 3)

    def test_the_plan_is_named_after_the_label_and_the_marker_is_the_coordinators(self):
        text = text_of()
        self.assertIn('The plan is named after the label, M1-FIXED,', flat(text))
        self.assertIn('The coordinator deletes the marker SP11\\DONE1.TXT before the boot', flat(text))
        self.assertIn('STICK-FILES.TXT, the list of every file of the stick with its SHA-256; this text does not list it', flat(text))

    def test_the_sections_come_in_the_order_the_operator_reads_them(self):
        text = text_of(note=['a note'])
        marks = ['0. What approving', '0a. Where things stand', '0b. The hazards', '1. What may run', '1a. ', '1b. ', '2. Placement', '3. Store classes', '3b. ', '4. Time box',
                 '5. Refusal', '5a. ', '5b. ', '5c. ', '6. Notes']
        at = [text.index(m) for m in marks]
        self.assertEqual(at, sorted(at))

    def test_section_0a_says_where_things_stand_in_twelve_lines_and_claims_no_more_than_boot_20_showed(self):
        text = text_of()
        self.assertEqual(stand_problems(text), [])
        f = re.sub(r'not that macOS boots, and not that\s+anything is stable', 'that macOS boots and that\n   anything is stable', text)
        self.assertEqual(stand_problems(f), ['claims macOS boots', 'claims anything is stable'], 'a claim beyond the run is found')
        self.assertTrue(stand_problems(text.replace('\n\n0b. ', '\n   a\n   b\n   c\n   d\n   e\n   f\n   g\n   h\n\n0b. ', 1)), 'a longer block is found')
        self.assertTrue(stand_problems(text.replace('3,127 traps', '3,000 traps')), 'a changed fact is found')
        self.assertTrue(stand_problems(text.replace('NOT RUN. This text', 'run. This text')), 'a block that drops the hardware status is found')

    def test_each_kind_of_stop_says_whether_a_run_has_shown_it_and_only_watchdog_has_been_seen(self):
        text = text_of()
        self.assertEqual(kind_label_problems(text), [])
        self.assertEqual(sorted(m1g.STOP_SEEN), ['WATCHDOG'])
        f = flat(text)
        self.assertEqual(kind_label_problems(f.replace('An expected end of M1. OBSERVED once (boot 20, one unit, one boot).', 'An expected end of M1. ' + m1g.STOP_NEVER)), ['WATCHDOG'])
        self.assertEqual(kind_label_problems(f.replace('C= the guest VA. An expected end of M1. Never seen (UNKNOWN).', 'C= the guest VA. An expected end of M1. ' + m1g.STOP_SEEN['WATCHDOG'])),
                         ['S2_FAULT'])

    def test_the_withheld_panel_sentence_is_in_every_real_record_and_only_in_those(self):
        sentence = flat(rec.PANEL_CONSEQUENCE['withheld'])
        for panel, kwargs, expected in (('withheld', {'real': REAL}, True), ('handed', {'real': {**REAL, 'panel': 'handed'}}, False), ('none', {}, False),
                                        ('withheld, GIC and stop', {'real': REAL, 'gic_stop': True}, True)):
            with self.subTest(panel):
                self.assertEqual(sentence in flat(rec.record_text({**AUTH.FIXED, **kwargs})), expected)
        with mock.patch.dict(rec.PANEL_CONSEQUENCE, clear=True):
            self.assertNotIn(sentence, flat(rec.record_text({**AUTH.FIXED, 'real': REAL})), 'a generator without the sentence says so')

    def test_a_panel_that_the_table_does_not_know_is_a_key_error_and_never_the_words_of_another(self):
        for panel in rec.PANEL_WORDS:
            with self.subTest(panel):
                if panel in m1g.PANEL:
                    self.assertIn(flat(m1g.PANEL[panel]['guest_gets']), flat(text_of(real={**REAL, 'panel': panel})))
                else:
                    with self.assertRaises(KeyError):
                        text_of(real={**REAL, 'panel': panel})
        with self.assertRaises(KeyError):
            text_of(real={**REAL, 'sptm_hvc_calls': 1})

    def test_every_panel_has_its_words_for_the_other_rung_and_a_panel_without_them_is_a_key_error(self):
        for panel, entry in m1g.PANEL.items():
            with self.subTest(panel):
                self.assertIn(flat(entry['rung']), flat(text_of(real={**REAL, 'panel': panel})))
        short = {key: value for key, value in m1g.PANEL['withheld'].items() if key != 'rung'}
        with mock.patch.dict(m1g.PANEL, {'withheld': short}), self.assertRaises(KeyError):
            text_of()


class Provenance(unittest.TestCase):
    def test_the_text_does_not_depend_on_the_path_of_the_nv2_header(self):
        a = AUTH.m1_facts()
        b = {**a, 'monitor': {**a['monitor'], 'build_record_sha256': sha(b'the same build, from another path')}}
        self.assertNotEqual(a['monitor']['build_record_sha256'], b['monitor']['build_record_sha256'])
        self.assertEqual(text_of(a), text_of(b))
        self.assertEqual(path_problems(text_of(a), a), [])
        self.assertEqual(path_problems(text_of(a) + a['monitor']['build_record_sha256'], a), [a['monitor']['build_record_sha256']], 'a text that carried it is found')

    def test_the_header_digest_is_a_function_of_the_record_and_another_record_has_another(self):
        fx = AUTH.fabricate()
        facts = m1.read_report(AUTH.json.dumps(fx['report']).encode(), fx['image'], fx['reporter'], dict(AUTH.EFI))
        self.assertEqual(facts['nv2']['header_sha256'], sha(NV2.emit_header(fx['record']).encode()))
        other = AUTH.fabricate(record=AUTH.nv2_bytes('nv2-ingest', 0x1235))
        facts2 = m1.read_report(AUTH.json.dumps(other['report']).encode(), other['image'], other['reporter'], dict(AUTH.EFI))
        self.assertNotEqual(facts['nv2']['header_sha256'], facts2['nv2']['header_sha256'])
        self.assertIn(f'NV2 header   sha256 {facts["nv2"]["header_sha256"]}', text_of(facts))

    def test_the_approved_text_fits_the_builders_cap_and_a_longer_file_is_refused(self):
        self.assertEqual(bb.AUTHORISATION_CAP, 1 << 17, 'raised from 64 KiB on purpose; docs/SP11-STICK-MONITOR.md section 19 says why')
        size = len(text_of().encode())
        self.assertGreater(size, 1 << 16, 'the record outgrew the old cap: that is why it was raised')
        self.assertLess(size, bb.AUTHORISATION_CAP)
        shutil.rmtree(SCRATCH, ignore_errors=True)
        try:
            for name, length, expected in (('fits', size, None), ('cap', bb.AUTHORISATION_CAP, None), ('over', bb.AUTHORISATION_CAP + 1, 'FILE_SIZE')):
                path = SCRATCH / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x' * length)
                with self.subTest(name):
                    if expected:
                        with self.assertRaises(ch.Refused) as caught:
                            bb.read_regular(str(path), '--authorisation', cap=bb.AUTHORISATION_CAP)
                        self.assertEqual(caught.exception.name, expected)
                    else:
                        self.assertEqual(len(bb.read_regular(str(path), '--authorisation', cap=bb.AUTHORISATION_CAP)), length)
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


class Hygiene(unittest.TestCase):
    def test_the_new_files_are_ascii_with_an_spdx_line_within_the_size_limits_and_hold_no_machine_data(self):
        for rel in ('tools/sp11_stick_bundle_record_m1_guide.py', 'tools/test-sp11-stick-monitor-text.py', 'tools/test-sp11-stick-monitor-box.py'):
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                self.assertLess(len(data), 51200)
                self.assertLess(data.count(b'\n'), 1500)
                for needle in AUTH.BT.MACHINE_PATHS:
                    self.assertNotIn(needle.encode(), data)

    def test_the_record_of_the_kind_has_no_label_of_a_host_result_and_no_person(self):
        text = text_of()
        self.assertNotIn('VERIFIED', text)
        self.assertTrue(text.isascii())
        for needle in AUTH.BT.MACHINE_PATHS:
            self.assertNotIn(needle, text)


if __name__ == '__main__':
    unittest.main()
