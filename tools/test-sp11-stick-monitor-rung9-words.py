#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The words of rung 9 of the monitor entry run (tools/sp11_stick_bundle_panel_rung9.py and ..._rung9d.py; docs/SP11-STICK-ROOT.md section 9, K17; docs/SP11-M1-GUEST-PROFILES.md section 8): a
phrase pin and a deletion mutant for each sentence that rung 9 says again or adds, in sections 0, 3c and 5d (the guest with a root, the carry, the failure names of the carry, the waits and what
to film); the classes of section 5d are in the order that the aim gives, exclusive, and leave no film without one; the stale sentences of rung 8's record (its aim, its single change, its
class names, the wait of 120 s and the CPU-boot panic at 122 s as a margin of 2 s) are in no part of rung 9's, and the older records say nothing of rung 9. The mechanism (the closed list of five
forms, the pair, the label, the comparison, the streaming writer, the ten kinds against each other, the sizes and K17) is tools/test-sp11-stick-monitor-rung9.py, whose fixtures run here.

    python3 tools/test-sp11-stick-monitor-rung9-words.py

The record is built here by the stand-in loader with the numbers of the real guest (339 chunks, 291 of them the root): the text of the carry is INFERRED from boot rates (docs/SP11-STICK-ROOT.md
section 9), and no boot of this form has run. Nothing here runs the unit: Hardware NOT RUN."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung9_mechanism', TOOLS / 'test-sp11-stick-monitor-rung9.py')
M9 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M9)
STATE, M5, LABEL9 = M9.STATE, M9.M5, M9.LABEL9
import sp11_stick_bundle_panel_rung9 as r9  # noqa: E402

FIVE_D = '5d. Rung 9: the aim'
HEAD3C = '3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic, a wait of 360 s'
CLASSES = ('CARRY STOPPED', 'PANIC TEXT', 'NOT A RESULT', 'NOT PASSED, (a)', 'WD1', 'MOUNTED', 'NEW STOP', 'WAITING FOR THE MEDIA', 'SILENT WAIT', 'NOT PASSED, (b)')


def setUpModule():
    M9.setUpModule()


def tearDownModule():
    M9.tearDownModule()


def flat(text):
    return ' '.join(text.split())


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
    return dict(section0=f.split('0. What approving this text decides (read this first)')[1].split('0a. Where things stand')[0],
                section0a=f.split('0a. Where things stand')[1].split('0b. The hazards')[0],
                section3c=HEAD3C + f.split(HEAD3C)[1].split('4. Time box')[0], section5d=f.split(FIVE_D)[1])


PINS_0A = {
    'head': 'Rung 8 (record 54c1dff7...c717b; its boot is called boot 28 in this text; no film of it is read here): RUNG 8 RESULT PENDING.',
    'placeholder': 'THIS PARAGRAPH IS A PLACEHOLDER, AND NOTHING IN THIS RECORD DEPENDS ON RUNG 8\'S RESULT.',
    'not known': 'Whether boot 28 has run, and what its film showed, are not in this text and not known to it (NOT RUN as far as the text goes',
    'without': 'A record that carries this paragraph was approved without rung 8\'s result, and the two cases below say what holds either way.',
    'case a': 'CASE A, THE CLOCK TOOK (rung 8\'s prediction: INFERRED, and never seen)',
    'assumes': 'THIS RECORD ASSUMES CASE A for every time that it gives to the kernel\'s log',
    'case b': 'CASE B, THE CLOCK DID NOT TAKE (rung 8\'s class THE CLOCK DID NOT TAKE, which is boot 27\'s course again',
    'later': 'Then every time that this record gives to the kernel\'s log is about 50 s later (INFERRED), the mount\'s stages get about 240 s of the box and not 290 s',
    'same classes': 'THE CLASSES OF SECTION 5d ARE THE SAME IN BOTH CASES',
    'case c': 'CASE C, RUNG 8 ENDED ANOTHER WAY OR HAS NOT RUN (a panic, a new stop, a film that is no result, no film: UNKNOWN): this text claims nothing for it, and approving the text does not '
              'decide it, nor does it approve rung 8\'s boot.',
    'observed unless inferred': 'OBSERVED unless INFERRED; boot 28 pending) Boot 20 (record 31e5d4c2...a802)',
    'rung 8 is': 'Rung 8 is a separate record (the quiet guest',
    'run is the': "run is the coordinator's call, made from rung 8's",
}


PINS_0 = {
    '0 d4': 'THE SHA-256 OF EXACTLY THIS TEXT, WHEN THE OPERATOR APPROVES IT, ALSO DECIDES D4',
    '0 four': 'APPROVING THIS TEXT ALSO DECIDES FOUR THINGS.',
    '0 box': '(1) D5\'\'\': A TIME BOX OF 300 s, which is new.',
    '0 root': '(2) A GUEST WITH A ROOT.',
    '0 ram': 'its RAM is 6,144 MiB where rung 8\'s is 384',
    '0 cmd': 'the command line ends with cpu_boot_timeout_secs=360 and acm_fastsim=1 (the credential manager\'s software mode, the guest profile quiet-rtc-acm: EXPERIMENTAL, whether the mount needs it is '
             'UNKNOWN)',
    '0 trust': '(26,496 bytes; three words that rung 8\'s guest does not have, INFERRED to be its address, its virtual address and its size)',
    '0 size': 'GUEST.DAT is therefore 1.93 GB, and no earlier run has carried or run a file of that size.',
    '0 pair': '(3) A PAIR OF THE ROOT CARRY: the wrapper and the reporter are built with the file limit of 2 GiB in place of 256 MiB and the box cap of 1200 s in place of 120 s',
    '0 runner': 'Q1N1-PAYLOAD.EFI af76e0bf..., 779,264 bytes: it reads and hashes files of up to 2 GiB, and allocates them at the top of RAM',
    '0 neither': 'Neither has run on the unit; the wrapper and the runner are EXPERIMENTAL',
    '0 reach': '(4) WHAT THE GUEST CAN REACH is rung 8\'s, plus a guest RAM of 6,144 MiB at 0X880000000 with the RAM disk inside it, with no new register, no new system-register class and no new store class',
    '0 why': 'WHY RUNG 9. Rung 7 (boot 27, record e42f451d...3b01) ran its whole box of 120 s through bsd_init to a wait for a root device',
    '0 one boot': 'THIS RECORD CARRIES THE ROOT AND RUNS THE KERNEL ON IT IN ONE BOOT: GUEST.DAT is',
    '0 wait': 'THE BOX IS 300 s AND THE WAIT FOR THE CPU THAT NEVER COMES IS 360 s, OUTSIDE IT',
    '0 covers': 'Approving this text covers ONE boot of this stick.',
    '0 d5': 'THIS RECORD\'S TIME BOX IS 300 s, LONGER THAN THE 5 s THAT D5 AND D8 DECIDED',
    '0 extra': 'the extra seconds are there for the root mount, which no run has reached',
    'd5 and d8': "D5 and D8 decided 5 s, D5' 60 s,",
    'of 120 s': 'of 120 s (61.6 s and 120.2 s, OBSERVED',
    'kernels run of': "kernel's run of 300 s is UNKNOWN (section 4).",
    'film this text': "film this text reads (rung 8's boot, boot 28,",
    'even slowly and': 'even slowly, and the one run that can show',
    'the aim the': 'THE AIM, THE ORDER OF THE CLASSES, THE PREDICTION, ITS FALSIFIERS AND',
    'd8 the thermal': 'D8 the thermal exception: accepted for ONE CPU, a',
    'd5 the time': "D5'' the time box of rungs 6 to 8 was 120",
    'this record asks': "This record asks 300 s (D5''', above), which no",
    '20 s uptime': '20 s UPTIME precaution that D5 left open (section 4); D7',
}
PINS_3C = {
    'short': 'In short. This record is rung 9, the first root-mount run, after rung 8 (boot 28: section 0a)',
    'label': "Label: PIN.DAT's build_label is M1RUN-ACKPANIC-M-",
    'label mark': 'the mark M- of the guest profile quiet-rtc-acm (rung 8\'s label, M1RUN-ACKPANIC-R-RUNG8, had the mark R-)',
    'pair': 'rung 9 is built with this pair and with no other, and the records of rungs 5 to 8 refuse it',
    'cmd': 'command line -v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpu_boot_timeout_secs=360 acm_fastsim=1',
    'handoff': 'HANDOFF (chunks 0 and 1: the SPTM handoff page and boot_args) differs in 38 bytes in 14 places',
    'params': 'PARAMS (chunk 2) differs in 9 words',
    'resized': 'chunk 35 (25,200 to 26,736 bytes), chunk 39 (442,368 to 6,701,056 bytes), chunk 40 (507,904 to 3,522,560 bytes), chunk 44 (49,152 to 98,304 bytes)',
    'new': 'there are 3 new chunks: kind 7 at 0X887806870 (3,364,752 bytes), kind 2 at 0X88DB5C000 (212,992 bytes), kind 2 at 0X88DB90000 (98,304 bytes)',
    'root': 'the root is 291 kind-OTHER chunks (143 of them ZERO, 1,798,293,088 bytes of data and 63,977,888 of zeros) that cover the RAM disk, 0X88ED00000 to 0X8FDD00000, without a gap',
    'room': 'the table has 685 entries of its 1,024 to spare',
    'the runners progress': "the runner's progress lines for 1.9 GB, a page",
    'of 120 s': 'of 120 s, record 54c1dff7...c717b, boot 28 (section 0a:',
    'its guest is': "its guest is rung 8's with 6,144 MiB of RAM,",
    'section 5c this': "(section 5c, THIS APPROVAL COVERS ONE BOOT); rung 8's",
    'place of 120': 'place of 120 s: SNAP_SP11_STICK_ROOT_CARRY and SP11_STICK_MONITOR_BOX_MAX_S, section 4).',
    'the reporter v3': 'the reporter v3 of rungs 6 to 8 rebuilt',
    'snapmonbin and snaprepbin': "SNAPMON.BIN and SNAPREP.BIN differ from rung 8's; GUEST.DAT, PIN.DAT",
    'guestdat is 1926621440': "GUEST.DAT is 1,926,621,440 bytes, 1,807,735,872 more than rung 8's,",
    'cpuboottimeoutsecs360 and acmfastsim1': 'cpu_boot_timeout_secs=360 and acm_fastsim=1 (-v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpu_boot_timeout_secs=360 acm_fastsim=1):',
    'cpu 1 first': 'CPU 1 first (processor_wait_for_start: a timed block of the',
    '24s pmgr panic': "24's PMGR panic at 2.25 s suggests: INFERRED), 62",
    'boots 25 26': 'boots 25, 26 and 27 went on on the boot CPU (OBSERVED',
    'records of rungs': 'records of rungs 4 to 9 only (--let-panic: LET_PANIC_REFUSED',
    'boot 26 616': 'boot 26 61.6 s and boot 27 120.2 s (OBSERVED',
    '6144 mib at': '6,144 MiB at 0X880000000 where rungs 1 to 8 had 384',
    'the device tree': "THE DEVICE TREE OF THIS GUEST IS A PROFILE'S, NOT THE BASE",
    'against rung 8s': "Against rung 8's own guest it has 339 chunks, 37",
    'the jpeg roots': 'the jpeg roots (jpeg0/1-h17a, jpeg0/1-h17p) have no A16 pin',
    'the root experimental': 'THE ROOT (EXPERIMENTAL; the guest profile j700ap-26A428-m1-quiet-rtc-acm, mark M-,',
    'apples basesystem root': "Apple's BaseSystem root, 1,862,270,976 bytes (SHA-256 7cab6d69..., the digest",
    'a9037730 in the': 'A9037730... in the profile), the root hash (208 bytes, SHA-256 e3d407cc...',
    'trust cache 26496': 'trust cache (26,496 bytes, SHA-256 09c90cd2...) travels in PARAMS',
    'panelmismatch and the': "(PANEL_MISMATCH) and the placement to the runner's data files (ROOT_PLACEMENT).",
    'the a16 whose': "the A16, whose kernel is this one): the A16's",
    'what the root': 'WHAT THE ROOT CANNOT SHOW (UNKNOWN unless a film',
    '118885568 bytes shae2df284b53dff262': "118885568 bytes, SHA=E2DF284B53DFF262); the SNAPMON STAGE=PIN_OK line shows CHUNKS=339; INDEX=3's SHA",
    'words what the': 'words (WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT),',
    'm1 section 5a': 'M1 (section 5a) and NOT PASSED for rung 9 (section 5d);',
}
PINS_5D = {
    'new': 'WHAT CHANGES: (1) THE PAIR, the root-carry pair (the wrapper accepts a file of 2 GiB and a box of up to 1200 s, of which this record asks 300 s)',
    'not': 'WHAT DOES NOT CHANGE. THE STICK IS READ ONLY BY THE RUNNER',
    'aim': 'THE AIM. One question, and one film answers it: how far does the stock kernel get with a root to mount?',
    'sees': 'WHAT THE OPERATOR SEES, IN ORDER, from the Shell\'s AUTORUN line.',
    'together': 'only now, after the last file has been read and hashed, the runner prints the five lines PAYLOAD LOADED INDEX=0 to INDEX=4 (INDEX=2 is GUEST.DAT, with its digits)',
    'force off': 'if the page has not changed for 4 minutes before PAYLOAD CALL 0, or for 120 s between PAYLOAD CALL 0 and MON CALL (section 5), it is a stall',
    'film': 'WHAT TO FILM is the whole panel from the Shell\'s countdown to 120 s after the SNAPREP page (about 10 minutes)',
    'refusal': 'A REFUSAL OF THE CARRY comes before the guest: nothing of the kernel is on the panel, and its NAME is the result.',
    'no memory': 'FILE_NO_MEMORY: the runner\'s: the firmware has no free range for GUEST.DAT at or below the top of RAM (2^36 - 1)',
    'descriptor': 'NOT_IN_ONE_DESCRIPTOR: the runner\'s, before the point of no return',
    'unknown': 'UNKNOWN, and never seen: where the firmware places a 1.8 GiB allocation, whether it is one descriptor, whether the stick\'s USB path reads into memory above 4 GiB, whether an 8 MiB flush '
               'operation is safe',
    'reset': 'A RESTART OF THE SURFACE DURING THE CARRY is a result of the carry and not of the guest',
    'expect': 'The wait for the root device that ended boot 27 should NOT come back as boot 27 printed it (an IOMedia of content Apple_HFS): the RAM disk is the IOMedia.',
    'assumed clock': 'THE TIMES OF THIS SECTION ASSUME THAT THE CLOCK TOOK (CASE A of section 0a: rung 8\'s result is not in this text)',
    'case b times': 'in CASE B (the clock did not take) it comes at about 60 s as in boot 27 (OBSERVED once), every later time of this section is about 50 s later and about 240 s of the box are left for the mount',
    'force 360': "come back. Whatever the guest paints, if the SNAPREP page is not on the screen 360 seconds after the MON CALL line, FORCE OFF",
    'predict': 'the most likely ends, in no order of likelihood that this text claims, are a PANIC TEXT of the import or of the mount',
    'clean end': 'THE CLEAN END THAT THIS RECORD CAN GIVE: MON STOP WATCHDOG at the end of the box (300 s after the call)',
    'cpu panic': 'would come about 362 s after the call (INFERRED), 62 s after the box',
    'wd1 falsifier': 'a MON STOP DEVICE_RESET (class WD1)',
    'clock falsifier': 'a MON STOP UNNAMED with B= in 0X308700000..0X308718000, the clock model\'s window (the model refused an access: NEW STOP, section 5d)',
    'clock refused': 'THE CLOCK MODEL REFUSED AN ACCESS: MON STOP UNNAMED with B= in 0X308700000..0X308718000, the window of the clock model, and A= 0X30 and eight more hex digits, the last of which is the device result',
    'two readings': 'Two readings of an S2_FAULT and one of an UNNAMED stop are named.',
    'clock finding': 'a MON STOP UNNAMED with B= in the clock\'s window is a finding about the clock model',
    'order': 'a film is in the first class below that fits, in this order, and every film fits one.',
    'wd1': 'RUNG 9 WD1: no panic text and MON STOP DEVICE_RESET: a device model of the monitor asked for a reset of the guest, and the guest\'s virtual watchdog, WD1, is INFERRED to be the one',
    'wd1 armed': 'WD1 stops the run only when the guest armed it (control bit 2) and its u32 counter, which counts the guest\'s clock of 24 MHz, has reached the threshold that the guest wrote',
    'wd1 wrap': 'a wrap alone is 178.96 s, about 3,435,973,837 ticks of the monitor\'s 19.2 MHz clock, and stops nothing: the model compares before it reduces the counter to 32 bits',
    'wd1 span': 'this box of 300 s is the first to span the wrap: if the guest did not arm WD1 the page shows nothing at 178.96 s and the run goes on to MON STOP WATCHDOG at the end of the box',
    'mounted': 'RUNG 9 MOUNTED: the line "BSD root:" is on the film (the kernel announced its root) and there is no panic text.',
    'first el0': 'is THE FIRST EL0 (the first user-mode code of the guest)',
    'media dict': '(2) a dict that names IOResources and boot-uuid-media: the kernel took the UUID path and waits for the volume\'s media',
    'silent wait': 'RUNG 9 SILENT WAIT: MON STOP WATCHDOG at the end of the 300 s box, no panic text, no "BSD root:" and no "Waiting on <dict>" line standing at the end',
    'carry stopped': 'RUNG 9 CARRY STOPPED: the run ended before the guest ran, and no kernel text is on the panel',
    '300 s what': '300 s) WHAT IS NEW beyond rung 8 (record 54c1dff7...c717b, boot 28:',
    '352 of 395': "(352 of 395 nodes) are rung 8's: the acknowledge is EXPERIMENTAL",
    'nothing else changes': 'NOTHING ELSE changes: no register, no system-register class, no',
    'the store classes': 'THE STORE CLASSES ARE THOSE OF EVERY RECORD OF THIS KIND,',
    'ac power is': 'AC POWER IS REQUIRED for the whole run, the',
    'this approval covers': 'THIS APPROVAL COVERS ONE BOOT (section 5c): PAYLOAD CALL 0',
    'the volumes uuid': "the volume's UUID, then AES, then the ISC; nothing",
    '1 the start': "(1) THE START: boot 27's text, with the clock's",
    '2 the media': '(2) THE MEDIA: bsd_init asks IOKit for a root',
    '3 the import': '(3) THE IMPORT: the APFS driver probes the raw',
    '4 the services': "(4) THE SERVICES: the kernel's AES, credential and SEP",
    '5 the mount': '(5) THE MOUNT: the line "BSD root:" and the',
    '6 userspace the': '(6) USERSPACE: the first user process waits for the',
    'the film names': 'The film names the stage that the kernel reached',
    'the carry inferred': 'THE CARRY (INFERRED: nothing of it has run; the',
    '1 the read': '(1) THE READ, about 75 s: the runner reads',
    '3 s at': '3 s at an ASSUMED 24 MiB/s (UNKNOWN: boots 8 and 9',
    '2 the hash': '(2) THE HASH, about 10 s: with the whole',
    'payload ready payload': 'PAYLOAD READY, PAYLOAD BIGFILE CHUNK=... and the SP11 EBS',
    'if the panel': 'IF THE PANEL DRAWS NO PROGRESS LINE (UNKNOWN), the',
    'which is not': 'which is not a refusal, and FORCE OFF then',
    'first payload hash': 'first PAYLOAD HASH line, of the five PAYLOAD LOADED',
    '21 and 24': '21 and 24 to 27: from the MON CALL',
    'what is new': 'WHAT IS NEW comes after the block: the lines',
    'inferred and so': '(INFERRED), and so are the AES, credential and SEP',
    'what the record': "camera's timer. WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED;",
    'the log and': 'THE LOG AND THE TIME OF ITS LAST CHANGE are a',
    'force off of': 'FORCE OFF of step 5 of section 5c (360',
    'the times of': 'THE TIMES OF THE FIRST AND OF THE LAST PAYLOAD READ LINE',
    '40 lines and': '40 lines and its MON TIM, MON CNT and MON ACK',
    'first the carry': "First the carry: the runner's and the wrapper's lines",
    'then the panel': 'Then the panel: the coordinator transcribes the first lines',
    'mon call line': 'MON CALL line and read to its LAST LINE',
    'the mon ret': 'The MON RET TICKS= of any stop that is not WATCHDOG',
    'not the failure': 'NOT THE FAILURE: the noise lines of section 0a (the SPMI',
    'wrong and is': 'wrong and is the class RUNG 9 PANIC TEXT',
    'a driver that': 'a driver that needs the SEP or the AES',
    'm1s own verdict': "M1's own verdict is section 5a's and is reported",
    'waiting for the': 'WAITING FOR THE MEDIA, SILENT WAIT and PANIC TEXT',
    'carry stopped not': 'CARRY STOPPED, NOT A RESULT and the two NOT PASSED',
    'the result is': 'THE RESULT IS THE NAME OR THE LAST LINE AND ITS TIME:',
    'spent only if': 'spent only if PAYLOAD CALL 0 printed (section 5c);',
    'the guest was': 'The guest was not tested: nothing is concluded about',
    'rung 9 panic': 'RUNG 9 PANIC TEXT: the panel shows a panic',
    'it is the': 'It is the stage that the text names (the',
    'applepmgr assertion of': 'ApplePMGR assertion of boot 24 again, or "cpu 1',
    'rung 9 not': 'RUNG 9 NOT A RESULT: a film that does',
    'rung 9 not 2': 'RUNG 9 NOT PASSED, (a): no panic text and MON STOP S2_FAULT',
    'the result is 2': "THE RESULT IS THAT THE RUN WAS CUT BY A DEVICE MODEL'S",
    'the mount worked': 'THE MOUNT WORKED (INFERRED to be what the line',
    'the results are': 'THE RESULTS ARE, each with its time: (1) the',
    'mon stop watchdog': 'MON STOP WATCHDOG at the end of the box with',
    'bsd root and': '"BSD root:" and a later panic text is PANIC TEXT,',
    'pc a the': "PC=, A=, the page's MON ESR, MON WVAL and MON",
    'the profile did': 'THE PROFILE DID NOT TAKE: A= lies in a',
    'the stop moved': 'THE STOP MOVED: an S2_FAULT with A= anywhere else,',
    '2 it is': "2) it is not M1's expected end (section 5a).",
    'rung 9 waiting': 'RUNG 9 WAITING FOR THE MEDIA: MON STOP WATCHDOG',
    'the result is 3': 'THE RESULT IS THE DICT, transcribed whole, with the',
    'mon stop watchdog 2': 'MON STOP WATCHDOG, no new text, and no such',
    'the result is 4': 'THE RESULT IS THE LAST LINE AND ITS TIME,',
    '26 and 27': '26 and 27; CVAL less 1.25 times LAST of the MON CLK',
    'layer that the': 'layer that the last line points at (AES, SEP',
    'mon call a': 'MON CALL, a REFUSED line after it, MON PREPARE FAILED,',
    'read lines': 'one such line for each further 64 MiB read (',
    'hash lines': 'one such line for each further 64 MiB hashed (',
    'about 70 lines': 'lines of console text from the first READ line, which scroll the Shell\'s text off',
    'stand still': 'the console then stands still while the firmware sweeps the declared ranges and the bounce runs (230 operations of 8 MiB for GUEST.DAT: the time is UNKNOWN and never run)',
    'page again': 'the page is drawn again, dark with its own text: the replayed PAYLOAD LOADED, READY, BIGFILE and EBS lines (the READ and HASH lines are not replayed)',
    'wrapper times': '(5) THE WRAPPER: its lines to STAGE=PIN_OK, a pause of about 25 s (its digests of the file), STAGE=GUEST_OK, STAGE=COPYING, about 13 s (the copy and the verify), STAGE=COPIED',
    'dark panel': 'a dark panel at 100 s is NOT a failure and NOT a result, and only the waits below end the run',
    'a stall': 'a new READ line is due about every 3 s and a HASH line about every 0.3 s; if the lines are shown and none has come for 120 s (about 40 times the gap',
    'note the last line': 'note the last line (its MIB says how far it had got) and FORCE OFF',
    'slower stick': 'a slower stick makes the lines further apart and the carry longer,',
    'film camera': 'with the main camera and no stop for stills in the carry: the READ lines scroll off as the later lines come, so the film, and not a photograph, is the record of them',
    'firmware watchdog': 'whether the firmware\'s 5-minute watchdog is armed during the read (the runner clears it before the first read, and as always before the bounce)',
    'progress lines slow': 'whether the console draws 56 progress lines without slowing the read, and the stick\'s read rate',
    'restart spent': 'the approval is spent only if PAYLOAD CALL 0 printed, and the film says when the Shell\'s countdown or the logo came back and what the last line was',
    'file too big': 'FILE_TOO_BIG: the runner\'s (PAYLOAD REFUSED=FILE_TOO_BIG INDEX=2 GOT= WANT=)',
    'file read': 'FILE_READ, HASH: the runner\'s: a read that failed, or a SHA-256 that differs from PAYLOAD.MAN\'s',
    'place declared': 'PLACE_DECLARED, PLACE_*: the wrapper\'s (SNAPMON REFUSED=PLACE_DECLARED ...)',
    'chunk guest': 'CHUNK_*, GUEST_*: the wrapper\'s (SNAPMON REFUSED=CHUNK_RANGE or another name of the table',
    'digest names': 'INDEX_DIGEST, CHUNK_DIGEST, FILE_DIGEST, COPY_VERIFY: the wrapper\'s digests of the file in memory, or of the copy',
    'dark expected': 'A dark panel is expected for part of the time (the kernel clears it); it is a restart only if the Shell\'s countdown or the runner\'s first lines',
}


# The rest of the record (the header, sections 0b to 5c and 6 on): a phrase for each sentence that rung 9 says again or adds and that is in none of the four sections above; each is in the whole text once.
PINS_REST = {
    'snapintosh sp11 stick': 'SNAPINTOSH SP11 STICK BUNDLE - AUTHORISATION RECORD (format 1,',
    'what is new': 'what is new beyond section 0b and what counts',
    'sptm hvc call': 'SPTM HVC call patch 0: docs/SP11-STICK-REAL.md section 5) runs at EL1',
    'the wrapper and': 'The wrapper and the reporter that this record names',
    '2 maps it': '2 maps it its RAM (6,144 MiB, the RAM',
    'section 3c and': '(section 3c) and no CPU starts: in boot 27',
    '152 s observed': '152 s (OBSERVED once): a run of 300 s is UNTESTED.',
    'either way section': 'either way (section 5d). Whatever the guest paints,',
    'the mon call': 'the MON CALL line, OBSERVED once). - THE CARRY.',
    'the runner at': 'the runner at an ASSUMED 24 MiB/s (UNKNOWN: boots 8 and 9',
    'the panel its': 'THE PANEL: its lines PAYLOAD READ and PAYLOAD HASH,',
    'one cpu is': 'one CPU is busy at EL2 throughout.',
    'section 5d says': 'section 5d says what to wait for and what',
    'panel by itself': 'panel by itself is not a result (section 5d).',
    '4 gib unknown': '4 GiB (UNKNOWN), an 8 MiB cache-flush operation (UNKNOWN),',
    '300 s of': '300 s of it by the guest (D8, section 4). D8 accepted the exposure of one CPU',
    'that scaling': 'which agrees with that scaling. D8 accepted the exposure of one CPU',
    'sections 3c and 5d': 'Panel as the console (sections 3c and 5d): Whatever the guest paints',
    'text that is expected': 'fill with text: that is expected. Whatever the guest paints',
    'camera timer': "on a watch or the camera's timer. If at any time the Surface is too hot",
    'snapmon and index1': '(SNAPMON.BIN) and INDEX=1 ...',
    'this cpus sgi': "this CPU's SGI 0 and PPIs 23, 25, 27 and 30,",
    'for up to': 'for up to 300 s is UNKNOWN (section 3,',
    'interrupts observed once': 'interrupts (OBSERVED once: boots 26 and 27, section 0a),',
    'reportdat 528384 bytes': 'REPORT.DAT 528384 bytes, all zero, sha256 2af25e8ac647a88541cca4385bb95be923757a14201a6b756b5f8002593f6709 data PIN.DAT 1024',
    'letpanic1 in params': 'let_panic=1 in PARAMS: section 3c boot seed label sp11-stick-bundle',
    'the monitor entry': 'The monitor entry run (docs/SP11-STICK-MONITOR.md): payload 0 is SNAPMON-M1-ACK.BIN,',
    'the monitors elf': "(the monitor's ELF: tools/sp11_monitor_census.py checks section 3 against it)",
    'with this record': 'with this record once (OBSERVED, boot 20: MON STATUS=OK),',
    'workbase 0xa19000000 worksize': 'work_base 0xa19000000 work_size 240 MiB place_base 0x880000000 place_end 0xa28000000 (limit 2^36) panel_base',
    'as the kernels': "as the kernel's console (serial=2) with cpu_boot_timeout_secs=360 and acm_fastsim=1",
    'cnthp ppi 26': '(CNTHP, PPI 26) at the time box on CNTPCT_EL0,',
    'cntpctel0 carried in': 'CNTPCT_EL0, carried in the PARAMS chunk (watchdog_seconds, word 24).',
    '20 ticks hashed': '20 (TICKS HASHED) 2.41 s OBSERVED once (46,208,412 ticks of 19.2',
    'a precaution of': 'a precaution of low weight (docs/SP11-MONITOR-PORT.md section 10, D5);',
    'approving this text': 'APPROVING THIS TEXT RELAXES THE 20 s PRECAUTION OF D5',
    'observed one unit': 'OBSERVED, one unit, one boot; boot 20: UPTIME 28',
    'of a 19': 'of a 1.9 GB read at EL2, is UNTESTED,',
    'the exposure is': 'The exposure is the CPU time of one CPU,',
    'd8 was decided': 'D8 was decided on 2026-11-17 for ONE CPU, a',
    'section 5a one': 'section 5a) one CPU stays loaded, at worst, until',
    'no new payload': 'no new PAYLOAD READ or PAYLOAD HASH line for 120',
    'read at an': 'read, at an ASSUMED 24 MiB/s (UNKNOWN: boots 8 and 9',
    'a slower stick': 'A slower stick than 24 MiB/s makes the lines',
    'if the panel': 'If the panel draws none of them (UNKNOWN) this',
    '5c this approval': '5c, THIS APPROVAL COVERS ONE BOOT). - No change',
    'the first payload': "the first PAYLOAD READ line (the runner's start: INFERRED,",
    'the 4 minutes': 'The 4 minutes are about three times the estimate',
    '5c this approval 2': '5c, THIS APPROVAL COVERS ONE BOOT). - No page',
    '8 s that': '8 s that the NEXT GIC STORES AFTER 8',
    'the 360 s': 'The 360 s is the time box of 300',
    '300  446': '300 + 44.6 = 344.6), less the prepare (UNKNOWN): a FORCE OFF',
    'force off section': 'FORCE OFF (section 5c); never continue on that RAM.',
    'when inside the': 'when, inside the 300 s, is UNKNOWN), and it',
    '7 of section': '7 of section 5c or of the NOT A RESULT',
    'the panel boots': 'the panel (boots 21, 24, 25, 26 and 27,',
    'a mon stop': 'a MON STOP line (WATCHDOG, S2_FAULT or GUEST_EXC), never',
    'dc civac over': 'DC CIVAC over 345600 lines: probes/xnuboot/prepare.c:578-581, probes/xnuboot/report.c:243-250, probes/xnuboot/context.c:799-806, probes/xnuboot/mmu_access.c:193-198;',
    'refused line no': 'REFUSED line (no guest ran); MON PREPARE FAILED CODE=-n',
    'next gic stores': 'NEXT GIC STORES AFTER 8 S G=1 R=1 C=1 T=0 V=0 E=0:',
    'mon call x0': 'MON CALL X0= X1= X2= X3= X4= SP=: the',
    'and payload read': 'and PAYLOAD READ INDEX=<n> MIB=<m> OF=<total MiB>, PAYLOAD HASH INDEX=<n> MIB=<m>:',
    'and has one': 'and has one step: load SP11\\SLBOUNCE-DEVHE-MARK.EFI, then run SP11\\Q1N1-PAYLOAD.EFI --el2.',
    'force off of': 'FORCE OFF of step 5 (360 s after the MON CALL',
    'the call of': 'the call of 300 s, the stop and 120',
    'step 1 of': 'STEP 1 OF 1, CMD1: load SP11\\SLBOUNCE-DEVHE-MARK.EFI, CMD2: SP11\\Q1N1-PAYLOAD.EFI --el2',
    'snaprepbin then payload': '(SNAPREP.BIN), then PAYLOAD VERIFY=OK and PAYLOAD CALL 0',
    'and not rung': "and not rung 8's (rung 8's record names INDEX=0",
    'sha533d004c27a11da9 a page': 'SHA=533D004C27A11DA9: a page that shows these has the pair',
    'payload read line': 'PAYLOAD READ line (section 5d); INDEX=2 is GUEST.DAT: INDEX=2',
    'labelm1runackpanicrrung8 chunks46 this': 'LABEL=M1RUN-ACKPANIC-R-RUNG8 CHUNKS=46; this record cannot name its own PIN.DAT SHA,',
    'top is on': 'TOP=... is on the page too (section 5d).',
    'payload call 0': 'PAYLOAD CALL 0), the 8 s after NEXT GIC STORES AFTER 8',
    'unknown time and': '(UNKNOWN time), and after PAYLOAD CALL 0 about 47',
    'section 5d the': 'section 5d, THE CARRY), or is still dark 360',
    '360 seconds after': '360 seconds after the MON CALL line (section 5).',
    'has spent it': 'has spent it, whatever follows.',
    'boot 23 with': 'boot 23), with cpus=1 and let_panic (rung 4, boot 24),',
}


class Pins(unittest.TestCase):
    def test_each_sentence_that_rung_9_says_again_or_adds_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = dict(sections(STATE['r9']), rest=STATE['r9'])
        for name, pins in (('section0', PINS_0), ('section0a', PINS_0A), ('section3c', PINS_3C), ('section5d', PINS_5D), ('rest', PINS_REST)):
            with self.subTest(section=name):
                mutants_rejected(self, parts[name], pins)

    def test_nothing_in_the_record_says_that_rung_8_has_run_or_what_it_showed(self):
        f = flat(STATE['r9'])
        for claim in ('its approval is spent', 'the one that boot 28 ran', 'boot 28 had INDEX', 'boot 28 showed', 'the operator\'s film, read by the coordinator): RUNG 8', 'ran only in boot 28',
                      'ec9a9e84', 'in boot 28, and in its plain build', 'RUNG 8 KERNEL WAITING', 'RUNG 8 PANIC TEXT', 'RUNG 8 THE CLOCK DID NOT TAKE'):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, f)
        block = sections(STATE['r9'])['section0a'].split('RUNG 8 RESULT PENDING.')[1]
        for case in ('CASE A', 'CASE B', 'CASE C'):
            self.assertEqual(block.count(case), 1 if case != 'CASE A' else 2, case)       # CASE A is named once as a case and once as the assumption
        self.assertEqual(r9.RUNG8['verdict'], 'RESULT PENDING')

    def test_the_carry_counts_the_progress_lines_of_the_real_guest_file_and_the_waits_are_those_of_the_constants(self):
        """The stand-in guest of this suite is 9 MiB, so the counts that depend on the file's size are asserted for the real size here: 1,926,621,440 bytes make 28 full slices of 64 MiB, OF=1838, and the
        runner prints 28 READ and 28 HASH lines, then the five LOADED lines, READY, BIGFILE and the EBS lines (about 70 lines of console text in all)."""
        d9 = importlib.import_module('sp11_stick_bundle_panel_rung9d')
        real = 1_926_621_440
        self.assertEqual((real // (64 << 20), -(-real // (1 << 20))), (28, 1838))
        text = flat(d9.carry_text({'rung9': {'guest_bytes': real}, 'run': {'box_s': 300}}))
        for phrase in ('OF=1838 and one such line for each further 64 MiB read (28 lines, MIB=64 to 1792)', 'hashed (28 lines, about 0.3 s apart at that 194 MiB/s)', 'about 70 lines of console text',
                       'whether the console draws 56 progress lines', 'a dark panel at 100 s is NOT a failure', 'none has come for 120 s', 'page has not changed for 4 minutes before PAYLOAD CALL 0',
                       'or for 120 s between PAYLOAD CALL 0 and MON CALL'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertEqual((r9.LINE_WAIT_S, r9.READ_GAP_S, r9.PROGRESS_MIB, r9.CARRY_WAIT_MIN, r9.CALL_WAIT_S), (120, 3, 64, 4, 120))

    def test_the_wait_of_360_s_is_the_box_and_the_clean_bound_of_section_4_with_the_margin_that_the_record_says(self):
        f = flat(STATE['r9'])
        self.assertIn('300 + 44.6 = 344.6', f)
        self.assertIn('clean of 6565 MiB', f)
        self.assertEqual(r9.CPU_WAIT_S, 360)

    def test_the_older_records_say_nothing_of_rung_9(self):
        for name in ('r5-60', 'r6-120', 'r7', 'r8'):
            text = flat(STATE[name])
            for needle in ('RUNG 9', 'root-carry', 'quiet-rtc-acm', 'acm_fastsim', 'cpu_boot_timeout_secs=360', 'THE CARRY'):
                with self.subTest(record=name, needle=needle):
                    self.assertNotIn(needle, text)


class Classes(unittest.TestCase):
    def test_the_classes_of_section_5d_are_in_the_order_of_the_aim_and_each_is_once(self):
        f = flat(STATE['r9'])
        at = [f.index(f'RUNG 9 {name}: ') for name in CLASSES]
        self.assertEqual(at, sorted(at), 'the classes are read in this order')
        for name in CLASSES:
            self.assertEqual(f.count(f'RUNG 9 {name}: '), 1, name)

    def test_the_carry_class_comes_before_the_panic_text_and_the_panic_text_before_every_stop_class(self):
        f = flat(STATE['r9'])
        for later in CLASSES[2:]:
            self.assertLess(f.index('RUNG 9 PANIC TEXT: '), f.index(f'RUNG 9 {later}: '))
        self.assertLess(f.index('RUNG 9 CARRY STOPPED: '), f.index('RUNG 9 PANIC TEXT: '))

    def test_every_class_says_what_the_film_has_to_give(self):
        f = flat(STATE['r9'])
        for name in CLASSES:
            body = f.split(f'RUNG 9 {name}: ')[1].split(' RUNG 9 ')[0]
            with self.subTest(name):
                self.assertTrue(re.search(r'(?i)result|transcribe|NOT PASSED|nothing is concluded|did not take', body), 'the class says what the film has to give')
                self.assertGreater(len(body), 200)

    def test_the_wd1_wrap_is_that_of_the_guests_24_mhz_counter_and_the_cpu_wait_and_box_are_the_forms(self):
        d9 = importlib.import_module('sp11_stick_bundle_panel_rung9d')
        self.assertEqual(round(2 ** 32 / 24_000_000, 2), 178.96)
        self.assertEqual((round(d9.WRAP_S, 2), d9.WRAP_TICKS), (178.96, 3_435_973_837))
        self.assertEqual((r9.CPU_WAIT_S, r9.BOX_S), (360, 300))

    def test_a_wd1_stop_is_a_device_reset_when_the_guest_armed_it_and_the_wrap_is_no_stop_each_read_in_its_source(self):
        """The class WD1 stands on these lines: the model counts the guest clock (24 MHz), checks the threshold before it reduces the counter to 32 bits and only when the enable bit is set, the only model
        that can ask for a reset is WD1, and the monitor stops the run on it as EV 36, which the reporter names DEVICE_RESET."""
        read = lambda *path: (ROOT.joinpath(*path)).read_text()    # noqa: E731
        watchdog = read('compat', 'watchdog.c')
        self.assertIn('/* Check before reducing the counter to 32 bits: even a late poll must\n     * observe a threshold crossed before one or more counter wraps. */', watchdog)
        self.assertIn('if ((s->control & 4) && (s->current >= s->bite ||\n                             elapsed >= (uint64_t)s->bite - s->current))\n        s->reset = 1;\n    s->current += (uint32_t)elapsed;', watchdog)
        self.assertIn('virtual_counter_hz=24000000,', read('probes', 'guestprofile.py'))
        self.assertIn("'/device-tree/arm-io/wdt', b'wdt,t8101',\n                                   2, 4, 24000000),", read('probes', 'guestprofile.py'))
        self.assertIn('TIMEBASE_HZ = GUEST_PROFILE.virtual_counter_hz', read('probes', 'xnuboot.py'))
        self.assertIn('model->kind == DEVICE_WATCHDOG_WD1_V1 &&\n           state->watchdog.reset;', read('compat', 'device', 'device_core.c'))
        self.assertIn('record(c, EV_DEVICE_RESET, elr, c->device_reset_mask[0], c->device_reset_mask[1], 0);', read('probes', 'xnuboot', 'clock_timer.c'))
        self.assertIn('#define EV_DEVICE_RESET 36', read('probes', 'xnuboot', 'report_layout.c'))
        reporter = read('probes', 'sp11-stick-reporter.c')
        self.assertIn('case 36: return "DEVICE_RESET";', reporter.split('static const char *rp_event_name(uint64_t kind)')[1].split('\n}\n')[0])
        f = flat(STATE['r9'])
        self.assertNotIn('223.7', f)
        self.assertNotIn('4,294,967,296', f)
        self.assertEqual(f.count('(class WD1)'), 1, 'the falsifier names the class')

    def test_a_refusal_by_a_device_model_is_a_stop_that_the_reporter_does_not_name_with_the_ipa_in_b_and_rung_9_says_so_everywhere_it_names_the_clock_window(self):
        reporter = (ROOT / 'probes' / 'sp11-stick-reporter.c').read_text()
        names = reporter.split('static const char *rp_event_name(uint64_t kind)')[1].split('\n}\n')[0]
        self.assertNotIn('case 34:', names)
        self.assertIn('default: return "UNNAMED";', names)
        self.assertIn('rp_put_hex(a, &l, " A=", sp11sb_rd64(w + RP_W_TERMINAL + 16));', reporter)
        self.assertIn('rp_put_hex(a, &l, " B=", sp11sb_rd64(w + RP_W_TERMINAL + 24));', reporter)
        self.assertIn('#define EV_DEVICE_FAILED 34    /* a = kind << 32 | device_result, b = IPA, c = ESR */', (ROOT / 'probes' / 'xnuboot' / 'report_layout.c').read_text())
        self.assertIn('else record(c, EV_DEVICE_FAILED, *elr, (model->kind << 32) | result, ipa, esr);', (ROOT / 'probes' / 'xnuboot' / 'mmio_dispatch.c').read_text())
        f = flat(sections(STATE['r9'])['section5d'])
        self.assertEqual(f.count('MON STOP UNNAMED with B= in'), 3, 'the falsifier, the class and the finding name it')
        self.assertNotIn('S2_FAULT with A= in 0X308700000', f)
        self.assertNotIn('THE CLOCK MODEL FAULTED', f)
        self.assertNotIn('Three readings of an S2_FAULT', f)


class Hygiene(unittest.TestCase):
    def test_nothing_is_called_verified_and_the_record_is_ascii_and_within_the_line_width(self):
        text = STATE['r9']
        self.assertNotIn('VERIFIED', text)
        self.assertTrue(text.isascii())
        f = flat(text)
        self.assertIn('0a. Where things stand (boots 20, 21, 23 to 27; one unit, one boot each; OBSERVED unless INFERRED; boot 28 pending)', f)
        self.assertEqual(f.count(FIVE_D), 1)
        body = text.split('0. What approving')[1].split('1. What may run')[0].splitlines() + text.split(FIVE_D)[1].splitlines()
        self.assertTrue(all(len(line) <= 120 for line in body), 'the lines of sections 0 and 5d are within the width of the record')

    def test_the_carry_numbers_are_inferred_and_say_so(self):
        f = flat(STATE['r9'])
        carry = f.split('THE CARRY (INFERRED')[1].split('WHAT TO EXPECT.')[0]
        self.assertIn('the numbers are estimates and not measurements', f.split('THE CARRY (INFERRED')[1][:200])
        self.assertIn('UNKNOWN, and never seen', carry)
        self.assertEqual((r9.CARRY_READ_S, r9.CARRY_HASH_S, r9.CARRY_WRAPPER_S, r9.CALL_WAIT_S), (75, 10, 39, 120))

    def test_the_stale_sentences_of_rung_8s_record_are_in_no_part_of_rung_9s(self):
        f = flat(STATE['r9'])
        for stale in ('THIS RECORD CHANGES ONE THING FROM RUNG 7', 'RUNG 8 NOT PASSED', 'RUNG 8 PANIC TEXT', 'a margin of about 2 s', 'with the word at 120 it is expected about 122 s in',
                      'THE PMU\'S REAL-TIME CLOCK IS BOUND at nub-spmi0 (the guest profile quiet-rtc)'):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, f)

    def test_the_cap_and_the_room_of_the_record_are_the_ones_of_k17(self):
        self.assertEqual(M9.bb.AUTHORISATION_CAP, 13 << 14)
        self.assertLessEqual(len(STATE['r9'].encode()), M9.HELD_LIMIT)
        self.assertGreater(M9.HELD_LIMIT - len(STATE['r9'].encode()), 4096, 'the coordinator\'s edit of the keyed block of boot 28 has room')


if __name__ == '__main__':
    unittest.main()
