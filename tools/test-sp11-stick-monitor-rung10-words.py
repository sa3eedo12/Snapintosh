#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The words of rung 10 of the monitor entry run (tools/sp11_stick_bundle_panel_rung10.py and ..._rung10d.py; docs/SP11-STICK-ROOT.md section 11, K18): a phrase pin and a deletion mutant for each sentence that
rung 10 says again or adds to rung 9's record (v2, dd2f39f4...) in sections 0, 0a, 3c, 4, 5 and 5d: the keyed block of boot 28 (the carry, the guest as the film showed it, the failure, the page), the ISC (what
the host reading says launchd's first boot task needs), the carry as boot 28 measured it and what the ISC's zeros add, and the classes of 5d, which are in the order that the aim gives, exclusive, and leave no film
without one; the stale sentences of rung 9's record (its pending boot, its three cases, its UNKNOWNs about the carry, its "no ISC") are in no part of rung 10's, and the older records say nothing of rung 10. The mechanism
(the form, the refusals, the comparison with rung 9's own guest, the streaming writer, the cross uses, the sizes) is tools/test-sp11-stick-monitor-rung10.py, whose fixtures run here.

    python3 tools/test-sp11-stick-monitor-rung10-words.py

The record is built here by the stand-in loader with the numbers of the real guest (347 chunks, 299 of them the RAM disk, an ISC of 1025 MiB): what the text says of the carry is OBSERVED in boot 28 (one unit, one boot) or
INFERRED from boot 28's rate, and no boot of this form has run. Nothing here runs the unit: Hardware NOT RUN."""
import hashlib
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung10_mechanism', TOOLS / 'test-sp11-stick-monitor-rung10.py')
M10 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M10)
STATE = M10.STATE
import sp11_stick_bundle_panel_rung10 as r10  # noqa: E402
import sp11_stick_bundle_panel_rung10d as d10  # noqa: E402
import sp11_stick_bundle_rung10 as r10b  # noqa: E402

FIVE_D = '5d. Rung 10: the aim'
LABEL_COUNTS = {'EXPERIMENTAL': 7, 'INFERRED': 157, 'NOT RUN': 3, 'OBSERVED': 152, 'UNIMPLEMENTED': 3, 'UNKNOWN': 64}
LABEL_SEQUENCE_SHA256 = 'caebdf8f984fabf9ce1cb6174f783dfc076a01383cd3fd7ed924b61f1f5111b3'
HEAD3C = '3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic, a wait of 360 s'
CLASSES = ('CARRY STOPPED', 'THE ISC DID NOT TAKE', 'NEW USERSPACE STOP', 'PANIC TEXT', 'NOT A RESULT', 'NOT PASSED, (a)', 'WD1', 'THE ROOT DID NOT MOUNT', 'NEW STOP', 'LAUNCHD RUNNING', 'SILENT WAIT',
           'NOT PASSED, (b)')


def setUpModule():
    M10.setUpModule()


def tearDownModule():
    M10.tearDownModule()


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
                section0b=f.split('0b. The hazards')[1].split('1. What may run')[0],
                section3c=HEAD3C + f.split(HEAD3C)[1].split('4. Time box')[0],
                section4='4. Time box' + f.split('4. Time box')[1].split('5. Refusal and stop rules')[0], section5d=f.split(FIVE_D)[1])


PINS_0A = {
    'launchd date clock took': "THE CLOCK MODEL TOOK (OBSERVED once: launchd's date), and IORTC was published (INFERRED from it",
    'read by the': 'read by the coordinator): RUNG 9 PANIC TEXT.',
    'ramdisk root ran': "RAM-DISK ROOT, RAN /sbin/launchd, AND LAUNCHD'S FIRST BOOT TASK, mount-phase-1, FAILED: THIS",
    'to 5 s': 'to 5 s after the report page: THE MIDDLE,',
    'guestdat b7aeddff pindat': 'GUEST.DAT b7aeddff..., PIN.DAT 68204251..., PAYLOAD.MAN 2868960b..., REPORT.DAT 2af25e8a...',
    'as staged snapmon': 'as staged), SNAPMON 6af26a84..., SNAPREP 562b9b6e..., the runner af76e0bf...,',
    '5d the carry': "5d (THE CARRY): the wrapper's placement, digests and copy took 26.1 s from its entry (OBSERVED once)",
    'the guest observed': 'THE GUEST (OBSERVED once; lines transcribed from the film).',
    'bootargs  v': '`boot-args = -v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpu_boot_timeout_secs=360 acm_fastsim=1`,',
    'user path the': 'user path (the monitor of rungs 5 to 8,',
    '10 in': '= 10 in APFS container with volcnt = 0',
    'mountphase1 panicking in': 'mount-phase-1`, `Panicking in 3 seconds.` and `panic(cpu 0 caller 0xfffffe000c246d2c):',
    'are apfs role': 'are APFS role words (INFERRED from the numbers): 0x10',
    'gic restore cleanyes': '`GIC RESTORE CLEAN=YES`, `STAGE=DONE`, `VERDICTS PIN=OK GUEST=OK POLICY=0X3 LINES=29`, `MON COUNTS EVENTS=512',
    'as in boot': 'as in boot 24 (INFERRED): a later stop, and',
    'what it settles': 'WHAT IT SETTLES (OBSERVED once, one unit, one boot):',
    'task failed is': 'task failed is in section 5d (THE AIM, INFERRED).',
    'unknown what the': 'UNKNOWN: what the kernel printed from the MON CALL',
    'rung 8 record': 'Rung 8 (record 54c1dff7...c717b): its own boot is not',
    'observed once nothing': "OBSERVED once); nothing here approves or assumes rung 8's",
    '21 23 24': '21, 23, 24, 25, 26, 27 and 28 was',
}


PINS_0 = {
    '1 d5 a': "(1) D5''': A TIME BOX OF 300 s, as",
    'd5 and d8': "D5 and D8 decided 5 s, D5' 60 s,",
    'ac power required': 'AC power REQUIRED, one CPU loaded for up to 300',
    '9s ram disk': "9's RAM disk) at 1 MiB and the ISC (1,074,790,400",
    'guestdat is about': 'GUEST.DAT is about 1.93 GB again: the ISC is',
    '3 the rootcarry': '(3) THE ROOT-CARRY PAIR AND THE BIG-FILE RUNNER, which',
    'ran in boots': 'ran in boots 25 to 28) and Q1N1-PAYLOAD.EFI af76e0bf...,',
    '4 what the': "(4) WHAT THE GUEST CAN REACH is rung 9's:",
    'boot 28 rung': 'Boot 28 (rung 9, record dd2f39f4...b6b) carried the root,',
    'its gpt partition': 'its GPT partition type (INFERRED: section 5d, THE AIM).',
    'this record gives': 'THIS RECORD GIVES THE RAM DISK AN ISC AND CHANGES NOTHING ELSE:',
    'the monitor the': 'THE MONITOR, THE REPORTER, THE RUNNER, THE CLOCK, THE PROFILE, THE COMMAND',
    'the aim the': 'THE AIM, THE ORDER OF THE CLASSES, THE PREDICTION, ITS FALSIFIERS AND',
    'longer than the': 'LONGER THAN THE 5 s THAT D5 AND D8 DECIDED:',
    'a box of': 'a box of 300 s is UNKNOWN (section 4).',
    'rung 9s approval': "Rung 9's approval took 300 s for boot 28 (D5'''),",
}


# D8's decision beside the exposure is in 0b and again in section 4.
PINS_EXPOSURE = {
    'the wrapper step': "boot 28 measured the wrapper's placement, digests and copy together at 26.1 s (the TICKS line's COPIED, counted from the wrapper's entry, so it holds the digests of the file: OBSERVED once)",
    'the wrapper step upper': "so its digests and copy take at most about 41 s (INFERRED: 26.1 s plus the ISC's zeros at no less than the 74 MiB/s that 26.1 s allows)",
    'the wrapper step estimate': 'the high sum above is an estimate and not a bound for this record',
}


PINS_3C = {
    'isc rung 10': 'ISC (rung 10: D9 panel handed, serial=2, cpu_boot_timeout_secs=360, let_panic',
    'an isc after': 'an ISC, after rung 9 (boot 28: section 0a):',
    'the runners progress': "the runner's progress lines for 1.9 GB, a page",
    '300 s record': '300 s, record dd2f39f4...b6b, boot 28 (section 0a: OBSERVED',
    '9s with an': "9's with an ISC in the RAM disk and the /chosen",
    'it covers one': 'it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS',
    'snapmonbin and snaprepbin': "SNAPMON.BIN and SNAPREP.BIN are rung 9's (SNAPMON-M1-ACK.BIN 6af26a84... and SNAPREP-M1-ACK.BIN 562b9b6e...,",
    'label pindats buildlabel': "Label: PIN.DAT's build_label is M1RUN-ACKPANIC-M-RUNG10.",
    'with console cpus1': 'with CONSOLE-, CPUS1-, LETPANIC- or PANEL- or with the',
    'vdisplay 0 text': 'v_display 0 (text mode), v_rowBytes 11520, v_width 2880, v_height 1920, v_depth 32',
    'probesbootmediapy 299 kindother': 'probes/bootmedia.py: 299 kind-OTHER chunks (147 of them ZERO, 1,798,655,472',
    'the table has': 'The table has 677 entries of its 1,024 to',
    'guestdat is 1926984384': "GUEST.DAT is 1,926,984,384 bytes, 362,944 more than rung 9's,",
    'surface pro 11': 'Surface Pro 11 in boots 24 to 28 (OBSERVED',
    'records of rungs': 'records of rungs 4 to 10 only (--let-panic: LET_PANIC_REFUSED',
    'the device tree': "THE DEVICE TREE OF THIS GUEST IS A PROFILE'S, NOT THE BASE",
    'the guest has': "the guest has 347 chunks, 48 of them rung 9's",
    'the jpeg roots': 'the jpeg roots (jpeg0/1-h17a, jpeg0/1-h17p) have no A16 pin',
    'the ram disk': 'THE RAM DISK (EXPERIMENTAL; the guest profile j700ap-26A428-m1-quiet-rtc-acm, mark M-,',
    'partition 1 is': "partition 1 is Apple's BaseSystem root, 1,862,270,976 bytes (SHA-256 7cab6d69...,",
    'hold three volumes': 'hold three volumes (1 GiB and a MiB: OBSERVED',
    'rung 9s the': "rung 9's, the root hash (208 bytes, SHA-256 e3d407cc...)",
    'for the allocator': "for the allocator (rung 9's RAM disk left 4,131",
    'guestprofilereadback holds the': '(GUEST_PROFILE_READBACK), holds the command line to the form (PANEL_MISMATCH)',
    'what the isc': 'WHAT THE ISC CANNOT SHOW (UNKNOWN unless a film',
    'shab7aeddff38fd6ad8 the snapmon': "SHA=B7AEDDFF38FD6AD8); the SNAPMON STAGE=PIN_OK line shows CHUNKS=347 (rung 9's: 339); INDEX=3's SHA",
    'words what the': 'words (WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT),',
    'm1 section 5a': 'M1 (section 5a) and NOT PASSED for rung 10 (section 5d);',
}


PINS_5D = {
    '300 s what': '300 s) WHAT IS NEW beyond rung 9 (record dd2f39f4...b6b, boot 28:',
    'inside snapmonm1ackbin the': 'inside SNAPMON-M1-ACK.BIN), the root-carry pair, the runner (Q1N1-PAYLOAD.EFI af76e0bf...),',
    '1 the ram': '(1) THE RAM DISK IS A GPT MEDIUM OF TWO PARTITIONS, 2,939,158,528',
    'it twice in': 'it (twice in HANDOFF and in PARAMS word 46).',
    'only xart mandatory': "as an ISC has them, of which the host reading says only xART is mandatory for launchd's first boot task",
    '3 guestdat is': "(3) GUEST.DAT is 1,926,984,384 bytes in 347 chunks (rung 9's: 1,926,621,440",
    'mountphase1 pass when': 'mount-phase-1, pass when the RAM disk also holds an ISC,',
    'the host reading': 'The host reading of the BaseSystem image (files/boottasks-note.md of',
    'gpt partition type': 'GPT PARTITION TYPE 69646961-6700-11AA-AA11-00306543ECAC and not by a UUID',
    'preboot role 0x10': "Preboot (role 0x10) and Update (0xC0) of the root's",
    'this record gives': 'THIS RECORD GIVES THE LOOKUP WHAT IT ASKS FOR',
    '0 the carry': '(0) THE CARRY, before the guest, as boot 28',
    '1 the start': '(1) THE START AND THE MOUNT as in boot 28:',
    '2 the isc': '(2) THE ISC: apfs_boot_util finds the container by the type GUID',
    'the films test': "THE FILM'S TEST THAT THE ISC DID ITS JOB",
    'unknown whether the': "UNKNOWN whether the kernel's APFS accepts the xART role,",
    '3 the boot': "(3) THE BOOT TASKS AFTER IT, in launchd's order (INFERRED):",
    'of 300 s': 'of 300 s should end in MON STOP WATCHDOG',
    'the film names': 'The film names the stage that the guest reached',
    'the carry boot': "THE CARRY (boot 28 ran it once, for rung 9's",
    '40 mibs from': '40 MiB/s from the stick: `PAYLOAD READ INDEX=2 MIB=64 OF=1838`',
    '2 the hash': '(2) THE HASH, about 8 s: `PAYLOAD HASH INDEX=2 MIB=64`',
    'el2 confirmed after': '`EL2 CONFIRMED AFTER EXITBOOTSERVICES`, the range table, `BOOT FOOTHOLD ONLY',
    '4 the page': '(4) THE PAGE THAT STANDS STILL: that page did',
    'inferred place base0x880000000': 'read on the host), `PLACE BASE=0X880000000 SIZE=0X1A8000000 DESC=219`, `GUEST.DAT BASE=0XF8D2A1000 BYTES=1926621440`, `VERDICTS PIN=OK GUEST=OK POLICY=0X3',
    'what this record': "WHAT THIS RECORD CHANGES (INFERRED; the arithmetic is boot 28's):",
    'the wait before': 'THE WAIT BEFORE THE MON CALL LINE IS THEREFORE ABOUT 90 s at most from PAYLOAD CALL 0 (the digests and the copy at most 41 s together, the verify at most about as long again: UNKNOWN, and the GIC arm',
    'copy alone ran faster': "(26.1 s counted from the wrapper's entry: it holds the placement, the wrapper's digests of the file and the copy, and not the verify, so the copy alone ran at more than 74.4 MiB/s",
    'digests and copy at most 41': "the digests and the copy take at most about 41 s together, about 14 s more than boot 28's 26.1 s",
    'verify as long as the two': 'INFERRED at most about as long as the digests and the copy.',
    'wrapper lines if drawn': "IF THE PANEL DRAWS THEM: boot 28's first film saw the bring-up page unchanged for 38 s or more after it appeared (OBSERVED once), so whether they are drawn is UNKNOWN; if not, the 360 s of step 5 count from the first frame in which the panel is cleared and shows the kernel's text",
    'a dark panel': 'A DARK PANEL BY ITSELF IS NOT A RESULT,',
    'payload call 0': 'PAYLOAD CALL 0, or for 180 s between PAYLOAD CALL 0',
    'and force off': 'and FORCE OFF then spends a boot that did',
    'of the first': 'of the first HASH line, of PAYLOAD CALL 0',
    'the table are': "the table are rung 9's, so PLACE_* and CHUNK_*",
    '21 and 24': '21 and 24 to 28: from the MON CALL',
    'appledialogspmipmurtc started and': '`AppleDialogSPMIPMURTC started!` and `Read RTC offset from leg_scrpad`, the SPMI',
    'what is new': 'WHAT IS NEW comes at the third task, mount-phase-1: boot 28',
    'a silent stretch': 'A silent stretch is not a failure by itself:',
    'what the record': 'WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED;',
    'prediction the carry': 'PREDICTION: the carry ends with PAYLOAD CALL 0 and MON CALL',
    'the clean end': 'THE CLEAN END THAT THIS RECORD CAN GIVE: MON STOP WATCHDOG',
    'would come about': 'would come about 362 s after the call (INFERRED), 62',
    'new stop a': 'NEW STOP); a MON STOP DEVICE_RESET (class WD1); a MON STOP',
    'force off of': 'FORCE OFF of step 5 of section 5c (360',
    'the times of': 'THE TIMES OF THE FIRST AND OF THE LAST PAYLOAD READ LINE',
    '21 23 24': '21, 23, 24, 25, 26, 27 and 28: OBSERVED',
    'what the film': 'WHAT THE FILM AND THE PAGE DECIDE (what this',
    'from the film': "from the film, against boot 28's (THE CARRY).",
    'apfs iokit node': 'APFS IOKit node lookup timed out` come (THE ISC DID NOT TAKE),',
    'panic mon esr': 'panic (`MON ESR EC=0X24 ISV=1 SIZE=4 SRT=10 WNR=0`, A=0X3082BC',
    'monitors clock 192': "monitor's clock (19.2 MHz): boot 28's was 769573015, 40.082",
    'not the failure': 'NOT THE FAILURE: the noise lines of section 0a (the SPMI',
    'boot 28 observed': 'boot 28, OBSERVED once; those volumes are optional and are INFERRED',
    'wrong and is': 'wrong and is the class RUNG 10 PANIC TEXT',
    'a userspace panic': "A USERSPACE PANIC looks like boot 28's (OBSERVED once):",
    'the name and': 'THE NAME and the lines before it are the',
    'm1s own verdict': "M1's own verdict is section 5a's and is reported",
    'the carry is': 'The carry is read first, then the panel, then',
    'the isc did': 'THE ISC DID NOT TAKE, NEW USERSPACE STOP, THE ROOT DID NOT',
    'carry stopped not': 'CARRY STOPPED, NOT A RESULT and the two NOT PASSED',
    'rung 10 carry': 'RUNG 10 CARRY STOPPED: the run ended before the',
    'the result is': 'THE RESULT IS THE NAME OR THE LAST LINE AND ITS TIME:',
    'rung 10 the': 'RUNG 10 THE ISC DID NOT TAKE: the panel',
    'the text is': 'THE TEXT IS THE RESULT, and the finding is',
    'that the root': 'that the root partition was found inside the GPT,',
    'the next record': 'The next record changes the image or the medium',
    'a result of': 'a result of its own (boots 24 and 28).',
    'rung 10 new': "RUNG 10 NEW USERSPACE STOP: launchd's panic names another",
    'the result is 2': 'THE RESULT IS THE NAME, the `+` lines of',
    'or prebootensurer exiting': 'or prebootensurer exiting 70 or 71), then fips (INFERRED);',
    'that does not': 'that does not fail but waits is SILENT WAIT.',
    'the s2fault after': 'The S2_FAULT after the panic is a later stop',
    'and boot 24s': "and boot 24's and 28's end, MON STOP S2_FAULT",
    'the text is 2': 'THE TEXT IS THE RESULT: the coordinator transcribes its',
    'rung 10 not': 'RUNG 10 NOT A RESULT: a film that does',
    'rung 10 not 2': 'RUNG 10 NOT PASSED, (a): no panic text and MON STOP S2_FAULT',
    'rung 10 wd1': 'RUNG 10 WD1: no panic text and MON STOP DEVICE_RESET:',
    'rung 10 the 2': 'RUNG 10 THE ROOT DID NOT MOUNT: no panic',
    'the result is 3': 'THE RESULT IS THE DICT, transcribed whole, with the',
    '1 the dict': '(1) the dict of boot 27 (an IOMedia of content Apple_HFS):',
    'it is a': 'It is a finding about the medium and the',
    'pc a the': "PC=, A=, the page's MON ESR, MON WVAL and MON",
    'a stop after': 'A STOP AFTER LAUNCHD HAS RUN (an S2_FAULT, an UNDEF',
    'rung 10 launchd': "RUNG 10 LAUNCHD RUNNING: no panic text, launchd's text",
    'the results are': 'THE RESULTS ARE, each with its time: (1) the',
    'inferred from the': 'INFERRED from the A16); (4) the stop and its',
    'this is the': 'THIS IS THE BEST END THAT THIS RECORD CAN HAVE:',
    'rung 10 silent': 'RUNG 10 SILENT WAIT: MON STOP WATCHDOG at the',
    'the result is 4': 'THE RESULT IS THE LAST LINE, ITS TIME AND THE NAME',
    'boots 26 and': 'boots 26 and 27); a page with VECTORS 0',
    'the next record 2': 'The next record adds the layer that the last',
    'mon call a': 'MON CALL, a REFUSED line after it, MON PREPARE FAILED,',
}


PINS_REST = {
    'snapintosh sp11 stick': 'SNAPINTOSH SP11 STICK BUNDLE - AUTHORISATION RECORD (format 1, label M1RUN-ACKPANIC-M-RUNG10)',
    'what is new': 'what is new beyond section 0b and what counts',
    'where things stand': 'Where things stand (boots 20, 21, 23 to 28;',
    'sptm hvc call': 'SPTM HVC call patch 0: docs/SP11-STICK-REAL.md section 5) runs at EL1',
    'which ran in': 'which ran in boot 28 (OBSERVED once).',
    '2 maps it': '2 maps it its RAM (6,144 MiB, the RAM',
    'boots 21 24': 'boots 21, 24, 25, 26, 27 and 28): from the MON CALL',
    'with an isc': 'with an ISC in the RAM disk, is UNKNOWN',
    'which boots 24': 'which boots 24 and 28 showed printing its text (OBSERVED',
    '152 s observed': '152 s (OBSERVED once): a run of 300 s is UNTESTED',
    'the runner reads': 'the runner reads GUEST.DAT, 1.9 GB, from the stick',
    'rung 9s file': "rung 9's file (OBSERVED once: section 5d, THE CARRY);",
    'el2 system registers': 'EL2 system registers and GIC registers only (section 3),',
    'run of 300': 'run of 300 s is UNTESTED (UNKNOWN: section 4).',
    'guest d8 section': 'guest (D8, section 4). D8 accepted the exposure',
    'reportdat 528384 bytes': 'REPORT.DAT 528384 bytes, all zero, sha256 2af25e8ac647a88541cca4385bb95be923757a14201a6b756b5f8002593f6709 data PIN.DAT 1024',
    'params and a': 'PARAMS and a RAM disk of 2.94 GB in its RAM',
    '20 ticks hashed': '20 (TICKS HASHED) 2.41 s OBSERVED once (46,208,412 ticks of 19.2',
    'observed one unit': 'OBSERVED, one unit, one boot; boot 20: UPTIME 28',
    'a box of': 'a box of 120 s once, in boot 27,',
    'a run of': 'A run of 300 s is UNTESTED, and the',
    'observed once no': 'OBSERVED once): no new PAYLOAD READ or PAYLOAD HASH',
    'sticks rate of': "stick's rate of boot 28 (about 40 MiB/s, OBSERVED",
    'a slower stick': 'A slower stick makes the lines further apart and',
    '230 operations of': '(230 operations of 8 MiB for GUEST.DAT: the time is UNKNOWN)',
    'the 4 minutes': 'The 4 minutes are several times the whole carry',
    '5c this approval': '5c, THIS APPROVAL COVERS ONE BOOT). - No page',
    '8 s that': '8 s that the NEXT GIC STORES AFTER 8',
    'the panel boots': 'the panel (boots 21, 24, 25, 26 and 27,',
    'm1runackpanicmrung10 and has': 'M1RUN-ACKPANIC-M-RUNG10, and has one step: load SP11\\SLBOUNCE-DEVHE-MARK.EFI, then run SP11\\Q1N1-PAYLOAD.EFI --el2.',
    'force off of rest': 'FORCE OFF of step 5 (360 s after the MON CALL',
    'the call of': 'the call of 300 s, the stop and 120',
    'autorun m1runackpanicmrung10 step': 'AUTORUN M1RUN-ACKPANIC-M-RUNG10 STEP 1 OF 1, CMD1: load SP11\\SLBOUNCE-DEVHE-MARK.EFI, CMD2: SP11\\Q1N1-PAYLOAD.EFI --el2',
    '9s the rootcarry': "9's (the root-carry pair, which ran in boot 28): INDEX=0",
    'payload loaded lines': 'PAYLOAD LOADED lines TOGETHER, after the last of its PAYLOAD HASH',
    'boot 28 and': 'boot 28), and on the SNAPMON STAGE=PIN_OK line LABEL=M1RUN-ACKPANIC-M-RUNG10',
    'payload call 0 rest': 'PAYLOAD CALL 0), the 8 s after NEXT GIC STORES AFTER 8',
    '4 minutes without': '4 minutes without any change before PAYLOAD CALL 0 and 180',
    'boot 23 with': 'boot 23), with cpus=1 and let_panic (rung 4, boot 24),',
    'flush op ran': '(0X800000: 8 MiB, which ran in boot 28: OBSERVED once)',
    'ebs lines then ready': 'after the last HASH line, the SP11 EBS lines, then PAYLOAD READY and PAYLOAD BIGFILE, the stand-still of the sweep and the bounce (UNKNOWN time)',
    'console lines bigfile': "after the last of the runner's console lines, PAYLOAD BIGFILE, while the firmware sweeps the declared ranges",
    'wait of 5 no result': 'a panel that is black and shows no text for the wait of section 5 that applies (4 minutes before PAYLOAD CALL 0, 180 s between it and the MON CALL line; a dark panel at 100 s with nothing else changed is not this case',
    'wait of 5 step 7': 'the panel goes black and shows no text for the wait of section 5 that applies (4 minutes before PAYLOAD CALL 0, 180 s between it and the MON CALL line; a dark panel at 100 s with nothing else changed is not a failure by itself',
    'lines on panel': "which showed them on the panel in boot 28, scrolling (OBSERVED once), before the page and not replayed on it",
    'copy about 40': 'the page is silent between the lines, and their times are in section 5d, THE CARRY',
    'pre call digests copy': "the wrapper's digests of the file and its copy (together 26.1 s in boot 28 for rung 9's chunks, at most about 41 s for this guest's, INFERRED), after STAGE=PIN_OK",
    'pre call twice': 'the 180 s are about twice the sum of those upper figures',
    'checks digests and copy': "(INFERRED from boot 28's 26.1 s for digests and copy: section 5d, THE CARRY",
    'step 5 digests and copy': "(INFERRED from boot 28's 26.1 s for digests and copy); section 5 gives the waits before FORCE OFF",
    'carry 0b digests and copy': 'so its digests and copy take at most about 14 s more and its verify up to as long again (INFERRED)',
    'isc full digest': 'made by tools/make-isc-fixture.py --build-three-role on the host (SHA-256 ',
}


class Pins(unittest.TestCase):
    def test_each_sentence_that_rung_10_says_again_or_adds_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = dict(sections(STATE['r10']), rest=STATE['r10'])
        for name, pins in (('section0', PINS_0), ('section0a', PINS_0A), ('section0b', PINS_EXPOSURE), ('section3c', PINS_3C), ('section4', PINS_EXPOSURE), ('section5d', PINS_5D), ('rest', PINS_REST)):
            with self.subTest(section=name):
                mutants_rejected(self, parts[name], pins)

    def test_nothing_in_the_record_is_pending_or_says_what_boot_28_has_not_shown(self):
        f = flat(STATE['r10'])
        for stale in ('RESULT PENDING', 'boot 28 pending', 'THIS PARAGRAPH IS A PLACEHOLDER', 'CASE A, THE CLOCK TOOK', 'CASE B, THE CLOCK DID NOT TAKE', 'CASE C, RUNG 8', 'its boot is called boot 28',
                      'the image has no ISC', 'which this image does not carry', 'Neither has run on the unit', 'has never run: UNKNOWN', 'the root-carry pair, which has not run', 'rung 8\'s result is not in this text',
                      'RUNG 9 MOUNTED', 'RUNG 9 WAITING FOR THE MEDIA', 'This record is rung 9', 'WHY RUNG 9', 'an ASSUMED 24 MiB/s', 'about 132 s in all',
                      'PAYLOAD READY, PAYLOAD BIGFILE and the SP11 EBS lines', 'minutes before the MON CALL line',
                      # v1's copy-alone claim (review R14-1, M1): COPIED counts from the wrapper's entry, so 26.1 s holds the digests too
                      'its copy alone at 26.1 s', 'measured its copy alone', 'the copy of 26.1 s', "boot 28's copy of 26.1 s", 'a low bound for this record', 'the copy takes about', 'the copy about 40 s',
                      'about 113 s', 'about 105 s', 'about 1.6 times', 'the digests 25 s', 'about 25 s, INFERRED', 'about 74.4 MiB/s of the', 'its copy takes about 14 s more'):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, f)
        self.assertEqual(r10.RUNG9['verdict'], 'PANIC TEXT')

    def test_the_carry_counts_the_progress_lines_of_the_real_guest_file_and_the_times_follow_boot_28s_rate(self):
        """The stand-in guest of this suite is 9 MiB, so the counts that depend on the file's size are asserted for the real size here: 1,926,984,384 bytes make 28 full slices of 64 MiB and OF=1838 (as
        boot 28's 1,926,621,440), and the placed bytes grow by the medium's 1,076,887,552, which the floor rate of 74.4 MiB/s copies in at most about 14 s (26.1 s holds the digests too: the copy alone is faster)."""
        real = 1_926_984_384
        self.assertEqual((real // (64 << 20), -(-real // (1 << 20))), (28, 1838))
        self.assertEqual((d10.PLACED9, d10.BOOT28_COPY_TICKS, round(d10.COPY9_S, 2), round(d10.RATE9_MIB, 1)), (2_036_053_472, 501_263_277, 26.11, 74.4))
        diff = dict(M10.REAL_DIFF10, guest_bytes=(r10b.RUNG9_GUEST_BYTES, real))
        self.assertEqual((round(d10.extra_s(diff), 1), round(d10.COPY9_S + d10.extra_s(diff)), d10.dc_s(diff), d10.wrapper_s(diff)), (13.8, 40, 41, 82))
        self.assertFalse(hasattr(d10, 'DIGEST_S'), 'the digests are inside the 26.1 s: no separate 25 s is added to them')
        self.assertEqual(d10.film_min(diff, 300), 11)
        text = flat(d10.carry_text({'rung10': {'guest_bytes': real, 'diff': diff}, 'run': {'box_s': 300}}))
        for phrase in ('OF=1838', 'one such line for each further 64 MiB, ON THE PANEL', 'about 14 s more', 'THE FIRMWARE PUT THE FILE AT 0XF8D2A1000', 'A DARK PANEL BY ITSELF IS NOT A RESULT',
                       'none has come for 120 s', 'before PAYLOAD CALL 0, or for 180 s between PAYLOAD CALL 0 and MON CALL'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertEqual((d10.LINE_WAIT_S, d10.PROGRESS_MIB, d10.CARRY_WAIT_MIN, d10.CALL_WAIT_S, d10.READ_S, d10.HASH_S), (120, 64, 4, 180, 46, 8))

    def test_the_isc_sha256_is_printed_in_full_and_the_labels_are_the_reviewed_ones_in_the_reviewed_order(self):
        """m6 of review R14-1: the 64 digits of the ISC image (the stand-in's, here), not 16; and m3: the ordered list of the label words of the record is pinned (a flip INFERRED -> OBSERVED, UNKNOWN -> INFERRED
        and the like changes it), by its counts and its SHA-256. A deliberate change of a label updates both."""
        f = flat(STATE['r10'])
        digest = M10.ISC_INFO['sha256']
        self.assertEqual((len(digest), f.count(f'(SHA-256 {digest}; the macOS formatter')), (64, 1))
        self.assertIsNone(re.search(r'SHA-256 [0-9a-f]{16}\.\.\.; the macOS formatter', f))
        labels = re.findall(r'\b(OBSERVED|INFERRED|UNKNOWN|ASSUMED|EXPERIMENTAL|UNIMPLEMENTED|NOT RUN)\b', f)
        counts = {name: labels.count(name) for name in sorted(set(labels))}
        self.assertEqual(counts, LABEL_COUNTS)
        self.assertEqual(hashlib.sha256(' '.join(labels).encode()).hexdigest(), LABEL_SEQUENCE_SHA256, 'the order of the labels changed')

    def test_the_older_records_say_nothing_of_rung_10(self):
        for name in ('r5-60', 'r6-120', 'r7', 'r8', 'r9'):
            text = flat(STATE[name])
            for needle in ('RUNG 10', 'RUNG10', 'ISC', 'GPT medium', 'boottasks', '+isc'):
                if name == 'r9' and needle == 'ISC':
                    continue  # rung 9's record says that its image has no ISC
                with self.subTest(record=name, needle=needle):
                    self.assertNotIn(needle, text)


class Classes(unittest.TestCase):
    def test_the_classes_of_section_5d_are_in_the_order_of_the_aim_and_each_is_once(self):
        f = flat(STATE['r10'])
        at = [f.index(f'RUNG 10 {name}: ') for name in CLASSES]
        self.assertEqual(at, sorted(at), 'the classes are read in this order')
        for name in CLASSES:
            self.assertEqual(f.count(f'RUNG 10 {name}: '), 1, name)

    def test_the_carry_class_comes_first_and_the_three_panic_classes_before_every_stop_class(self):
        f = flat(STATE['r10'])
        self.assertLess(f.index('RUNG 10 CARRY STOPPED: '), f.index('RUNG 10 THE ISC DID NOT TAKE: '))
        for later in CLASSES[4:]:
            self.assertLess(f.index('RUNG 10 PANIC TEXT: '), f.index(f'RUNG 10 {later}: '))

    def test_every_class_says_what_the_film_has_to_give(self):
        f = flat(STATE['r10'])
        for name in CLASSES:
            body = f.split(f'RUNG 10 {name}: ')[1].split(' RUNG 10 ')[0]
            with self.subTest(name):
                self.assertTrue(re.search(r'(?i)result|transcribe|NOT PASSED|nothing is concluded|did not take|finding', body), 'the class says what the film has to give')
                self.assertGreater(len(body), 200)

    def test_the_old_failure_is_told_by_its_ten_rounds_and_the_next_stops_are_named_as_inferred(self):
        f = flat(sections(STATE['r10'])['section5d'])
        isc = f.split('RUNG 10 THE ISC DID NOT TAKE: ')[1].split(' RUNG 10 ')[0]
        for phrase in ('THE TEN ROUNDS', 'wait_for_node:266: APFS IOKit node lookup timed out', 'Retrying (N retries left)', 'Boot task failed: mount-phase-1', 'exited due to SIGABRT'):
            self.assertIn(phrase, isc)
        userspace = f.split('RUNG 10 NEW USERSPACE STOP: ')[1].split(' RUNG 10 ')[0]
        for phrase in ('restore-datapartition', 'prebootensurer', 'fips', 'NONE of the ten rounds', 'UNKNOWN'):
            self.assertIn(phrase, userspace)
        aim = f.split('THE AIM. ')[1].split('THE CARRY (boot 28')[0]
        for phrase in ('data-protection (seputil)', 'restore-datapartition is the likeliest first real stop', 'fips (cc_fips_test)', 'WindowServer waits about 30 s', 'EMPTY volumes', 'UNKNOWN whether the kernel\'s APFS accepts the xART role'):
            self.assertIn(phrase, aim)
        self.assertIn('INFERRED', aim)

    def test_the_wd1_wrap_and_the_cpu_wait_and_box_are_the_forms(self):
        self.assertEqual((r10.CPU_WAIT_S, r10.BOX_S), (360, 300))
        f = flat(STATE['r10'])
        self.assertEqual(f.count('(class WD1)'), 1, 'the falsifier names the class')
        self.assertIn('RUNG 10 WD1: no panic text and MON STOP DEVICE_RESET', f)
        self.assertIn('178.96 s', f)

    def test_a_refusal_by_a_device_model_is_still_a_stop_that_the_reporter_does_not_name_and_rung_10_says_so(self):
        f = flat(sections(STATE['r10'])['section5d'])
        self.assertGreaterEqual(f.count('MON STOP UNNAMED with B= in'), 2, 'the falsifier and the class name it')
        self.assertNotIn('THE CLOCK MODEL FAULTED', f)


class Hygiene(unittest.TestCase):
    def test_nothing_is_called_verified_and_the_record_is_ascii_and_within_the_line_width(self):
        text = STATE['r10']
        self.assertNotIn('VERIFIED', text)
        self.assertTrue(text.isascii())
        f = flat(text)
        self.assertIn('0a. Where things stand (boots 20, 21, 23 to 28; one unit, one boot each; OBSERVED unless INFERRED)', f)
        self.assertEqual(f.count(FIVE_D), 1)
        body = text.split('0. What approving')[1].split('1. What may run')[0].splitlines() + text.split(FIVE_D)[1].splitlines()
        self.assertTrue(all(len(line) <= 120 for line in body), 'the lines of sections 0 and 5d are within the width of the record')

    def test_every_paragraph_of_the_keyed_block_of_boot_28_is_labelled_and_the_block_has_the_rules_of_a_keyed_block(self):
        for paragraph in r10.RUNG9['words']:
            self.assertTrue(paragraph.isascii())
            self.assertTrue(any(label in paragraph for label in ('OBSERVED', 'INFERRED', 'UNKNOWN', 'NOT RUN')), paragraph[:60])
            self.assertNotIn('VERIFIED', paragraph)
        self.assertEqual((r10.RUNG9['record'], r10.RUNG9['boot'], r10.RUNG9['verdict']), ('dd2f39f4...b6b', 'boot 28', 'PANIC TEXT'))

    def test_the_stale_sentences_of_rung_9s_record_are_in_no_part_of_rung_10s(self):
        f = flat(STATE['r10'])
        for stale in ('THIS RECORD CARRIES THE ROOT AND RUNS THE KERNEL ON IT IN ONE BOOT', '(2) A GUEST WITH A ROOT.', 'THE CLASSES OF SECTION 5d ARE THE SAME IN BOTH CASES', 'MON STOP WATCHDOG at the end of the box, with the last line '
                      'of the log a wait or "BSD root:"', 'a map of 6,144 MiB has never run: UNKNOWN'):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, f)

    def test_the_cap_and_the_room_of_the_record_are_the_ones_of_k17(self):
        self.assertEqual(M10.bb.AUTHORISATION_CAP, 13 << 14)
        size = len(STATE['r10'].encode())
        self.assertLessEqual(size, M10.M5.HELD_LIMIT)
        self.assertGreater(M10.M5.HELD_LIMIT - size, 0, 'the held limit of the record is the cap less 1 KiB, and the record is under it')


if __name__ == '__main__':
    unittest.main()
