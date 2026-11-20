#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Keeps docs/SP11-MONITOR-PORT.md honest. Run from the repository root:  python3 -B tools/test-sp11-monitor-port.py

  citation   `path:line[-last]` (`q1n1:` = the sibling q1n1 checkout): the file and the lines exist; `fragment`[path:line]: the
             fragment occurs on the cited lines; a bare `dir/file.ext` must exist.
  labels     the six labels only, an OBSERVED row names its source, no look-alikes.     tables  header, separator, row width.
  claims     statements that were wrong once stay refused (REFUTED); the restart numbers, the survey, the secondaries' entry
             state and the ID registers are pinned.
  state      statements about the tree held against their sources: guard status values, the five bundle files in order, the
             work items' statuses (built is not run) and plan graph, the dry run, the dead low placement, MADT and MCFG.

Skipped, visibly (a skip is never a pass): q1n1 citations without a checkout (Q1N1_SP11_TREE, commit e448f9a) and figures read
from the git-ignored dumps under downloads/. SeededBad feeds damaged copies of the document to the same checkers: each must fail.
"""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'SP11-MONITOR-PORT.md'
LABELS = ('VERIFIED', 'OBSERVED', 'INFERRED', 'EXPERIMENTAL', 'UNIMPLEMENTED', 'UNKNOWN')
LOOKALIKES = ('ASSUMED CONFIRMED MEASURED PROVEN PROVED TESTED VALIDATED ESTABLISHED HYPOTHESIS GUESSED LIKELY SUSPECTED '
              'UNVERIFIED PRESUMED PROBABLE CERTAIN DEMONSTRATED QUALIFIED ASSERTED CLAIMED OBVIOUS TRIVIAL').split()
MAX_BYTES = 120000      # raised from 110,000 on purpose for boot 28 (the eighth monitor run, rung 9: about 9.7 KB of boot 28 text); from 104,000 for boot 27, 96,000 for boot 26, 90,000 for boot 24, 80,000 for boot 23, 70,000 for boot 21; nothing was cut
SECTIONS = tuple(enumerate(('reading|milestone m1|entry|delivery|memory|timer|interrupts|id registers|platform contract|console|'
                            'thermal|safety|ordered work plan|first hardware run|decisions i need from the operator|'
                            'what i could not establish').split('|'), 1))
LOCAL_ONLY = ('downloads/', 'build/')
PATH = r'((?:q1n1:)?[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_.+-]+)+\.[A-Za-z0-9]+)'
CITE = re.compile(r'`' + PATH + r':(\d+)(?:-(\d+))?`')
QUOTE = re.compile(r'`([^`\n]+)`\[' + PATH + r':(\d+)(?:-(\d+))?\]')
BARE = re.compile(r'`' + PATH + r'`(?!\[)')
SOURCE_OF_OBSERVED = re.compile(r'\bboots? \d|restart (?:probe|control)|X1 probe|\bsteps? [123]\b|operator report|\bT0\d|\bX2\b'
                                r'|\bdump\b|\bboot-\d', re.I)
SEPARATOR = re.compile(r'^\|(\s*:?-{3,}:?\s*\|)+\s*$')
# Statements that were wrong once: the restart, the dry run, the timer probes, X2 T02, ECVEn, the 2026-11-16 decisions.
REFUTED = (
    (r'(?:under|below|within|inside|at most|less than)\s+(?:that\s+|the\s+|about\s+)?(?:16|30)\s*s\b',
     'a 16 s or 30 s UPTIME budget has no basis'),
    (r'\b(?:16|30) s (?:bound|budget|limit)\b', 'a 16 s or 30 s UPTIME budget has no basis'),
    (r'least uptime at which a restart', 'the UPTIME 16 S photograph is not a restart time'),
    (r'seconds after (?:its|the) chain', 'the restart came after boot 7\'s last photograph at an unrecorded time'),
    (r'\b40 to 60 ?s\b', 'the DONE2 to DONE3 gap holds the operator\'s power cycle'),
    (r'unattended plan|one unattended|writes? the next marker|restarts? produces? the next marker',
     'after a self-restart the firmware does not return to the stick'),
    (r'(?:unobserved|not observed|observed only|only observed|restarts?)\b[^.|]{0,60}\bUPTIME 10 S',
     'the restart control saw the read-only flow alive at UPTIME 224 S and 268 S'),
    (r'U5 DFR0|values not in my sources|(?:DFR0|ISAR0)[^.|]{0,40}T01 page only|ISAR0 \(RNDR\), FGT and AMU values',
     'the ID registers, OS lock, MDCR_EL2 and ICC_SRE_EL2 were read (X1 probe page, boots 8, 9)'),
    (r'writability UNKNOWN|UNKNOWN until the dry run|STAGED on the stick|boot is NOT RUN|hazard reads?[^.|]{0,40}(?:still|to be run)',
     'the dry run ran (boot 10): the bank is writable; the hazard reads returned at EL2'),
    (r'from_hamoa`? (?:accepts|passes|is no longer blocked|is unblocked)', 'the adapter still refuses the unit\'s tables (D11)'),
    (r'traps (?:are|were) inert', 'boot 11: the ECV traps work for an EL1 stub'),
    (r'(?-i:cnthctl|el1trap|T-HW[12])[^|]{0,80}NOT RUN', 'cnthctl and el1trap ran in boot 11'),
    (r'U2 [^;.|]{0,30}UNKNOWN|O1 (?:is|was) (?:decided|approved)', 'U2 is answered for an EL1 stub; O1 is only supported'),
    (r'first code (?:ever )?(?:run )?below EL2|no code (?:has |had |ever )*run below EL2|no code below EL2 (?:has|had) (?:ever )?run',
     'X2 T02 ran an EL1 stub first: el1trap is the first runner payload below EL2'),
    (r'ECVEn`?\s+(?:is|was)\s+set|confirm\w*[^.|;]{0,25}ECVEn', 'whether the traps need ECVEn is UNKNOWN'),
    (r'(?<!INFERRED )E2H (?:= )?1\b', 'D6: an E2H 1 origin of the record is INFERRED (run 4 printed no HCR_EL2), never a fact'),
    (r'stop by polling|or a polling stop|polling stop in the wrapper|polling fallback',
     'D2: a polling stop cannot exist once the monitor owns the CPU'),
    (r'`real`\s+(?:is\s+)?(?:written and\s+)?(?:NOT RUN|UNIMPLEMENTED)|`real`[^|]{0,200}is written and NOT RUN|U9 stands',
     '`real` ran on the kernelcache (host only): U9 is answered'),
    (r'\bD9\b[^|.;]{0,30}\b(?:undecided|open|not decided|still to decide)\b|D9 `SNAP_NO_FB=1`', 'D9 was decided 2026-11-16'),
    (r'4b -> 6 -> 8', 'WI 8 comes before 4b: its arm half is 4b\'s GIC step'),
    (r'(?:flat PIC|position independent)[^.|]{0,40}VERIFIED|VERIFIED[^.|]{0,40}(?:flat PIC|position independent)',
     'no relocation check exists: position independence is INFERRED'),
    (r'guest (?:takes|gets|owns) the panel|panel (?:is )?handed to the guest|HVC patch (?:is )?on\b|--sptm-hvc-calls 1\b',
     'D9: the guest is denied the panel, the kernel is stock'),
    (r'MMFR4[^.|]{0,20}unread', 'the record holds MMFR4 0 (X2 run 4 T01)'),
)
# The unit's ID registers (X1 probe page, passive reads at EL2): the document's hex values and decoded fields must agree.
DFR0, ISAR0, ISAR1 = 0x000F01F010305719, 0x1221111110212120, 0x0110111101211052
FIELDS = {'DebugVer': (DFR0, 0), 'BRPs': (DFR0, 12), 'WRPs': (DFR0, 20), 'RNDR': (ISAR0, 60), 'APA': (ISAR1, 4),
          'GPA': (ISAR1, 24)}
STATED = ('DebugVer', 'BRPs', 'RNDR', 'APA')      # fields the document must state; the others are checked when it does
COUNT_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
# Phrases the restart section and the survey must keep.
REQUIRED = ('first heartbeat after the chain', 'not at one fixed UPTIME', 'UPTIME 123 S', 'UPTIME 36 S',
            'UPTIME 224 S and 268 S', '2 of 3 cpuon boots', 'Volume-Down + Power', 'no plan runs unattended',
            'M1 starts no secondary (x4 = 0)', 'INFERRED, strongly', 'correlation over five boots', 'about 11 %',
            'WI 10 is off the path')
SECONDARY_STATE = ('0X200 0X300 0X10000 0X10100 0X10300 0X20000 0X20100 0x80000000 0X100 0X10200 0x1488000038'.split() +
                   ['survived its own CPU_OFF and CPU_ON', '0X20200 and 0X20300 UNKNOWN',
                    'must not assume VBAR_EL2 or HCR_EL2 are reset'])
GUARD_HEADER, BUNDLE_DOC = 'compat/sp11-timer-guard.h', 'docs/SP11-STICK-BUNDLE.md'
BUNDLE_FILES = ('SNAPMON.BIN', 'SNAPREP.BIN', 'GUEST.DAT', 'PIN.DAT', 'REPORT.DAT')
BUNDLE_SIZES = (('`PIN.DAT` (1024 bytes', 1024), ('`REPORT.DAT` (528,384 zero bytes', 528384))
# Built is not run: what each work item may claim (its row must keep these words). WI 7, 9 and 8 ran (boots 10, 11 and 12 to 14); WI 8's passes 5 and 6 ran in boot 14, the exception of pass 6 did not; WI 11 (rungs 1 to 3) ran in boots 20, 21 and 23.
# The row of M1's acceptance rule g, worded as the host build comparison that it is (VERIFIED, host; says nothing about hardware).
ROW_G = ('held: a host build comparison on 2026-11-18 showed the default A16 monitor build (four flag sets) byte-identical between revisions 87da59673c9a and ea593683db78 '
         '(VERIFIED, host; says nothing about hardware); the monitor sources probes/ compat/ shadow/ config/ gpu/ drivers/ are also unchanged by git diff between 1e938a7 and ea59368')
WORK_ITEM_STATUS = {1: ('DONE, EXPERIMENTAL (b8e8f81',),
                    2: ('DONE (q1n1 d8a5d2b; ran twice, boots 8 and 9, OBSERVED)', 'all returned at EL2'),
                    3: ('`real` RAN end to end on the real kernelcache, host only, not a hardware result', 'answered U9',
                        'Still blocking a bundle for hardware: the monitor payload (WI 4b)'),
                    4: ('4a BUILT for the dry run, EXPERIMENTAL, RAN once on the unit (WI 7; its refusal paths NOT RUN',
                        '4b, the monitor entry after WI 8', 'BUILT, EXPERIMENTAL (`docs/SP11-STICK-MONITOR.md`), RAN eight times on the unit (boots 20, 21, 23, 24, 25, 26, 27 and 28, OBSERVED; design below)',
                        'D2 (decided), D4 (decided for rungs 1 to 7 and 9)'),
                    5: ('BUILT, EXPERIMENTAL, host side', 'its monitor image ran eight times on the unit as part of M1 (boots 20, 21, 23, 24, 25, 26, 27 and 28; boot 28\'s is the root-carry build of the same monitor)'),
                    6: ('MET for rungs 1 to 7 and 9 (D4, 2026-11-18 to 20, each by its own record)',),
                    7: ('DONE, boot 10 (2026-11-16 10:21, plan DRYRUN-A, OBSERVED)', 'calls no monitor', '`DONE RETURN=0`',
                        '`AT S1E2W` accepted 337 of 337 probes', 'matched 13 of 13 digests',
                        "the stick unchanged but for the Shell's marker (nothing written by the unit)",
                        'answered U1 (writability), U6 (base); U1 stays open for `DC CVAC` alone'),
                    8: ('BUILT host-side, EXPERIMENTAL', 'RAN three times on the unit (boots 12, 13 and 14, 2026-11-16 and 2026-11-17, OBSERVED', 'pass 2 done',
                        'pass 3 done', 'ran in boot 14 (OBSERVED: the quarantine held', 'before 4b',
                        'resolves U3; D2 (decided), D4', 'U3 answered by boot 14', "the monitor's own stop path ran once, in boot 20: WI 11",
                        'that was its timer stop, and boot 21 ended at a stage-2 fault before the box'),
                    9: ('DONE (q1n1 b875620; boot 11, 2026-11-16 11:44, OBSERVED', 'first runner payload to run code below EL2',
                        '`BITS13_14_STUCK=YES`', '`VERDICT=TRAPS_EFFECTIVE`', 'answered U2 (EL1 stub only)',
                        'resolved D3 for these two payloads'),
                    11: ('rung 1 RAN once on the unit (boot 20, 2026-11-18, plan `M1RUN-RUNG1`; OBSERVED, one unit, one boot',
                         "rung 2 RAN once on the unit (boot 21, 2026-11-18, plan `M1RUN-CONSOLE-RUNG2`; OBSERVED, one unit, one boot; section 19) and PASSED by the record's rule",
                         'a, c, d, e and f held; b was not exercised (nothing mismatched)', ROW_G,
                         'Row e holds by the stop kind: `TICKS=96720173` is 720,173 ticks above the box and the stop is `WATCHDOG`',
                         "A pass of the rows and of nothing else: the guest was, INFERRED, stuck in an early loop of the kernel's I/O Kit start-up (section 14)",
                         'Rung 2 (boot 21): rows a, c, d, e and f held again (b not exercised; g by an empty source diff between ea59368 and eb6ba3b, VERIFIED, host, the byte comparison not repeated: NOT RUN)',
                         'the stop was `MON STOP S2_FAULT` with `PC=0XFFFFFE00097A352C` and `A=0X300734004`, 2.04 s into the call (`TICKS=39245286`, inside the box of 5 s)',
                         "the plan's expected failure 5 (OBSERVED once; the match of the address with XNU's first CPU-start write is INFERRED)",
                         'the loop of boot 20 is gone (its INFERRED cause chain is consistent with the run, not proven); one vCPU, no CPU bring-up',
                         '`python3 tools/monitor_baseline.py capture NAME`, then `compare EARLIER LATER`: IDENTICAL',
                         '1 to 7 and 9: eight done (boot 20, rung 1; boot 21, rung 2; boot 23, rung 3; boot 24, rung 4; boot 25, rung 5; boot 26, rung 6; boot 27, rung 7; boot 28, rung 9; rung 8 was skipped)',
                         'answers U7 (the guest ran from the bank in boots 20 and 21: OBSERVED, one boot each), and U8 and U10 in part',
                         'U10: the execute-never fault of the first native call not seen, `ABORTS=0`; one stage-2 data fault, the stop of boot 21, seen',
                         "U3's monitor stop path ran three times (`MON STOP WATCHDOG`: boots 20, 25 and 27, OBSERVED), and boot 21 ended at a stage-2 fault before the box",
                         'the NV2 gate passed with the first-image record in all eight boots (-91 not seen), its regime still not recorded',
                         'D4 decided for rung 1 (2026-11-18) and for rung 2 (2026-11-18 about 09:46, "Approve 1ad122b3 (rung 2)")',
                         "D9's panel-handed choice decided by the rung 2 approval; the next run needs its own record and the operator's decision",
                         # rung 3 (boot 23): what ran, how it ended, what it does not settle
                         "rung 3 RAN once on the unit (boot 23, 2026-11-18, plan `M1RUN-CPUS1-RUNG3`; OBSERVED, one unit, one boot; section 21): M1 PASSED by its own rows, rung 3's own verdict NOT PASSED (c) by the letter of its record",
                         'Rung 3 (boot 23): rows a, c, d, e and f held (b not exercised; g by an empty source diff between ea59368 and 3f48b0a, VERIFIED, host, the byte comparison not repeated: NOT RUN)',
                         "the stop was `MON STOP GUEST_EXC` with `PC=0XFFFFFE000B8C2288` (the guest's `x30`), `A=0X2000000` (`ESR_EL1`, class 0) and `C=0XFFFFFE000B8C22` (`ELR_EL1`, two digits cut), 2.17 s into the call (`TICKS=41593665`, inside the box of 5 s)",
                         "`cpus=1` was honoured and the CPU-start stop is gone; the exception is INFERRED (high confidence, a host analysis) to be the kernel's own debugger trap, so that the kernel had entered its panic or debugger funnel, and its cause is UNKNOWN",
                         'and for rung 3 (2026-11-18 about 15:05, "Approve 749361cf (rung 3)", which also kept the panel choice of D9 for it)',
                         'and boot 23 at a guest exception (`GUEST_EXC`) before the box', 'boot 23: `ABORTS=0` again and its stop was a guest exception',
                         # rung 4 (boot 24): what ran, how it ended, what it does not settle
                         "rung 4 RAN once on the unit (boot 24, 2026-11-18, plan `M1RUN-LETPANIC-RUNG4`; OBSERVED, one unit, one boot; section 22): M1 PASSED by its own rows, rung 4's class PANIC TEXT",
                         'Rung 4 (boot 24): rows a, c, d, e and f held (b not exercised; g for the pair that ran, the source directories differing from boot 23\'s tree by two added files and no changed file, VERIFIED, host, the byte comparison not repeated: NOT RUN)',
                         "the stop was `MON STOP S2_FAULT` with `PC=0XFFFFFE0008BA7358` and `A=0X3082BC224`, 2.2465 s into the call (`TICKS=43133334`, inside the box of 5 s), after the kernel's own handler had printed a panic text (an ApplePMGR assertion, then nested panics)",
                         "the decode of the stop (INFERRED) is a 32-bit read at an unmapped address in the kernel's chip-reset path; M1 PASSED by its own rows (5a), rung 4's class PANIC TEXT, a result that is neither a pass nor a failure of the record",
                         'and for rung 4 (2026-11-18 about 21:31, "Approve 67d814f6 (R6.2) and stage the stick", by the SHA-256 of its own record)',
                         'boot 24 at a stage-2 fault (`S2_FAULT`) before the box', 'boot 24: `ABORTS=0` again and its stop was a stage-2 data fault again, a read',
                         # rung 7 (boot 27): what ran, how it ended, what it does not settle
                         'rung 7 RAN once on the unit (boot 27, 2026-11-20, plan `M1RUN-ACKPANIC-Q-RUNG7`; OBSERVED, one unit, one boot; section 25)',
                         'Rung 7 (boot 27): rows a, c, d, e, f and g held (b not exercised; g by a `git diff` of the monitor sources between rungs 6 and 7, VERIFIED, host: the only difference is the builder-side data module `probes/guestprofile_m1.py`; the A16 baseline comparison ran on the record\'s first version and was NOT RUN again on the committed tree)',
                         '`MON STOP WATCHDOG` at 120.185 s (`TICKS=2307553890`, 3,553,890 ticks above the box of 120 s: the monitor\'s own deadline), the kernel waiting for a root device after `bsd_init` (INFERRED from its last line)',
                         "M1 PASSED, rung 7's class RUNG 7 KERNEL WAITING",
                         'and for rung 7 (by the SHA-256 of its own record; the date, the hour and the wording are not in the notes this entry was written from)',
                         # rung 9 (boot 28): what ran, how it ended, what it does not settle
                         'rung 9 RAN once on the unit (boot 28, 2026-11-20, plan `M1RUN-ACKPANIC-M-RUNG9`, the first root-mount form; OBSERVED, one unit, one boot; section 26); rung 8, the clock alone, was skipped by the operator and never run',
                         'Rung 9 (boot 28): rows a, c, d, e and f held (b not exercised; g not re-checked in this reading: NOT RUN); `MON STOP S2_FAULT` with `PC=0XFFFFFE0008BA7358` (boot 24\'s) at 40.08 s (`TICKS=769573015`, 13.4 % of the box of 300 s)',
                         "M1 PASSED, rung 9's class RUNG 9 PANIC TEXT",
                         'boot 28 answered the carry\'s unknowns for a file of 1.93 GB (the firmware\'s placement at `0XF8D2A1000` in one descriptor, a USB read into memory above 4 GiB, the stick\'s read rate of about 40 MiB/s, no firmware-watchdog reset in about two minutes: OBSERVED, one boot; `docs/SP11-STICK-LOG.md` section 26.2; the guest\'s WD1 was not tested)',
                         'and for rung 9 (by the SHA-256 of its own record, which this entry does not repeat; the date, the hour and the wording are not in the notes this entry was written from)')}
# The tree as the document states it (whitespace-collapsed); the sources that decide it are read below.
STATE_REQUIRED = '''\
is five files, the runner's whole manifest
bits that stick and trap nothing pass
T-HW2 saw them trap for an EL1 stub only
neither holds the monitor
11 = plan timers
SUPPORTED by boot 11, not decided
U2 trap effect (answered for an EL1 stub, WI 9)
after X2 T02's EL1 stub
ECVEn` clear, is INFERRED (X2 T03) and neither confirmed nor refuted by boot 11
whether they depend on `SCR_EL3.ECVEn`
by SHA-256 for one boot
(not met: boot 11)
alive at UPTIME 49 S
Placement A, low, is dead: 446 MiB remain below the 144 MiB file
passes on 0 of 6 local maps (REFUSED=PLACE_DECLARED
384 MiB passes 6 of 6 and ran in the dry run
then showed the bank writable: `AT S1E2W` accepted all 337 probes
13 of 13 data-chunk digests matched on re-read from DRAM after `DC CIVAC`
UNKNOWN: whether `DC CVAC` alone reaches DRAM (CIVAC cleans first)
the 138 MiB `GUEST.DAT` at 0xC446E000 (boot 10)
The wrapper took 2.92 s (19.2 MHz ticks)
DminLine 4; IDC 1, DIC 1 INFERRED
only the happy path ran
the bank, writable at EL2 (boot 10), is what is left
U1 `DC CVAC` alone reaching DRAM (bank writability: answered, WI 7)
U6 GUEST.DAT's base (answered, WI 7)
showed no restart in its 67.5 s video
Time budget: the wrapper 2.92 s as measured
APPROVED, for the dry run only
the dry run ran under it (WI 7)
Not the M1 run's decision
12 GICC entries of 80 bytes
one GICD structure of 25 bytes (ACPI: 24; the extra byte is 0)
then the ITS structure, no GICR-range structure
MCFG: eight allocations, segments 0 to 7
segment 2 at 0x6000000000
RESOLVED in e3844fa
a1fb91c (redistributors from the per-CPU GICC bases
then `check_hamoa_platform` refuses at `ecam-mismatch`
a descriptor holds at most four ECAM entries
the adapter demands set equality
the MCFG lists eight allocations, a descriptor holds four
an eight-entry descriptor is refused
the monitor entry (WI 4b) is built on them and ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28 (OBSERVED, one boot each); the bundle builder ran `real` on the kernelcache, host only
probably position independent (INFERRED: no relocation check exists, WI 4b adds one)
a polling stop cannot exist, as the wrapper does not run while the monitor owns the CPU
the boot CPU writes only CNTHCTL_EL2
`q1n1:platform/uefi/gicv3.c:95-165`
head is q1n1's payload stub
holds the FIRST 1,024 bytes of guest output
the monitor's window [+0x1000, +0x11000)
extension records from +0x11000
D9, decided 2026-11-16 ("Withheld and stock")
D2 Stop: DECIDED 2026-11-16 with D5
D5 The M1 time box: DECIDED 2026-11-16 (with D2): 5 s
N2 Descriptor policy (new): DECIDED 2026-11-16 ("Approve")
five devices (debug UART, keyboard, touchpad, touchscreen, NVMe) absent
N1 Boot seed: DECIDED 2026-11-17 ("Accept all four (Recommended)")
D11 proper stays OPEN
U9 kernelcache (answered, WI 3)
WI 4b, the monitor entry: BUILT, EXPERIMENTAL, RAN eight times on the unit (boots 20, 21, 23, 24, 25, 26, 27 and 28, OBSERVED).
`SNAP_SP11_STICK_MONITOR`, default 0
Gaps G1 to G9
8 -> 4b -> 6 -> 11
its MMFR4 is 0 as read in X2 run 4 T01, and the gate passed with it on the unit in boot 20 (`STATUS=0X0`, OBSERVED, one boot; the record's regime is still not recorded, D6)
D6 NV2 record: DECIDED 2026-11-17 ("Accept all four (Recommended)")
so INFERRED E2H = 1 for the record
NV2 at E2H = 0 is OBSERVED in X2 runs 6 and 7
Whether a record from an INFERRED E2H = 1 image describes the E2H = 0 regime is UNKNOWN
D8 Thermal: DECIDED 2026-11-17 ("Accept all four (Recommended)")
as an SP11-only opt-in: DECIDED 2026-11-17 ("Accept all four (Recommended)")
APPROVED for the GIC dry run only, by SHA-256, three times
for the monitor entry, rung 1 only, DECIDED 2026-11-18 01:18 ("Approve 31e5d4c2 (rung 1)", by the SHA-256 of the M1 record)
nor does the GIC dry run, WI 8, which ran three times, boots 12 to 14
12 to 14 = plans GICDRY-A, GICDRY-B and GICDRY-C
SGI 0 and PPIs 23, 25, 26, 27 and 30 enabled by the firmware
PPI 26's own delivery was OBSERVED in boot 14
quarantines this CPU's other enabled interrupts first
GUEST.DAT 118,870,336 B (44 chunks)
Hardware status for the port itself: NOT RUN, except eight runs of the monitor entry (boots 20, 21, 23, 24, 25, 26, 27 and 28, M1 rungs 1 to 7 and 9, 2026-11-18 to 20; rung 8 was skipped and never run).
The guest was, INFERRED, stuck in an early loop of the kernel's I/O Kit start-up
Boot 21 handed the guest the panel as its console (rung 2, WI 11): the kernel's boot log was on the panel
CPU-start write (`MON STOP S2_FAULT`, 2.04 s into the call; OBSERVED, one unit, one boot; section 19 of the log; that the write is the CPU-start write is INFERRED, by address)
20 = plan M1RUN-RUNG1, the first run of the monitor entry, and 21 = plan M1RUN-CONSOLE-RUNG2, rung 2 with the panel as the console (both 2026-11-18)
**Boot 21, rung 2 (2026-11-18, plan `M1RUN-CONSOLE-RUNG2`; OBSERVED, one unit, one boot).**
two guest inputs changed, the video block (the unit's panel) and the console route (`serial=2`), so the page has no `MON CON` line
boot 21's, `MON STOP S2_FAULT`, ended it earlier, at a stage-2 fault (OBSERVED, one boot)
the NV2 gate passed with the embedded first-image record (D6; boot 20, OBSERVED, one boot; the record's regime is not recorded) and again in boot 21 (OBSERVED, one boot)
the gate passed with it on the unit in boot 20 (`STATUS=0X0`, no -90 to -95: OBSERVED, one boot), which does not record the regime; it passed again in boot 21 (`STATUS=0X0`, OBSERVED, one boot), the regime still not recorded
boot 20 ran its native calls with no stage-2 abort
Rung 2 (boot 21) changed the panel choice by the operator's approval of 2026-11-18 (D9, section 15): with the panel as its console the kernel ran on, `ABORTS=0` again
(which one: IPA `0x300734004` in boot 21, a 32-bit write, OBSERVED, one boot; that it is XNU's first CPU-start write to the PMGR block is INFERRED, by address)
the monitor's own stop path: WATCHDOG stop OBSERVED once, boot 20, WI 11; boot 21 ended at a stage-2 fault before the box
U7 guest in the bank (the guest ran from the bank in boots 20 and 21: OBSERVED, one boot each)
U10 stage-2 syndromes (boot 20: no stage-2 abort, so none seen); in boot 21 one stage-2 fault was seen, the stop (a write and a translation fault at level 1: INFERRED decode of `ESR` `0x93970045`), with an IPA in the report
so what `HPFAR_EL2` holds on Oryon v1 stays UNKNOWN, and the execute-never fault of the first native call was not seen in any of boots 20, 21, 23 and 24 (`ABORTS=0`)
the monitor entry's stores (RAM, EL2 system registers and the GIC) ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28, and were put back
The path was walked eight times, for rungs 1 to 7 and 9 (boots 20, 21, 23, 24, 25, 26, 27 and 28); a further run needs a record of its own.
SNAPREP prints six lines of 64 columns (`RP_CONSOLE_LINES`), so the page is a prefix of what the guest printed
boot 20 ran the monitor and its guest for 5 s, with no restart in its 45 s film, alive at UPTIME 28 S
boot 21 ran them for 2.04 s, with no restart in its 58.8 s film, alive at UPTIME 36 S on the closer view at its end
In boot 21 (`serial=2`) the page had no `MON CON` line (OBSERVED, one boot; INFERRED: the console text went to the panel and not to the window)
guest MMIO limited by stage 2 to RAM (rung 1: no panel, D9; rung 2: RAM and the panel's range, which the guest writes)
What boot 20 showed (rung 1; OBSERVED, one unit, one boot;
the 26A428 source is not public, so every match with the public XNU source at tag `xnu-12377.121.6` is structural
the guest was **stuck, not slow**
It settles (OBSERVED, one boot) that the monitor entry works on Oryon v1, that the guest executed for 5 s under it, and that the early SPTM, TXM and GIC paths ran with `ABORTS=0`; it does not settle that the kernel boots
is the head of a five-instruction register-only loop in `PE_init_iokit`, the kernel's I/O Kit start-up
so the display size was 0 (VERIFIED, host: the staged file; the register itself was never read)
the quarantined guest timers are not the cause
`ptr-packing max` was the reporter's cap of six console lines (`RP_CONSOLE_LINES`), not the guest's last line
the same stop PC again once the panel is handed or `-noprogress` is added
`x10` not 0 or `x21` not 1 at the stop
a longer box on the withheld image only repeats it
the next run hands the panel to the guest (rung 2) or adds `-noprogress` (hand-off bytes only: a new `GUEST.DAT` digest), a new decision and record
most likely XNU's first CPU-start write to the PMGR block, which counts as success for M1
a `cpu N failed to boot` panic (it says `waited 1 seconds`) about a second later
`tools/kc-symbolicate.py` needs the KDK, which is absent on the machine used (NOT RUN)
Not reached in boot 20: the guest was, INFERRED, stuck in an early loop first (above)
Reached in boot 21 (OBSERVED once): `MON STOP S2_FAULT` with `A=0X300734004`, a 32-bit write, 2.04 s into the call (above); that this address is XNU's first CPU-start write to the PMGR block is INFERRED, by address
Not seen in boot 21 either (`ABORTS=0`); its one stage-2 fault was the stop, at a device address (item 5)
Boot 21: the same (`STATUS=0X0`), and the stop was the monitor's own, not a guest panic
Not seen in boot 20 (alive at UPTIME 28 S) nor in boot 21 (alive at UPTIME 36 S on the closer view)
It passed again in boot 21 (`STATUS=0X0`, OBSERVED, one boot)
Boot 20: the stop timer fired at the box (`MON STOP WATCHDOG`; OBSERVED, one boot)
Boot 21 ended at a stage-2 fault before the box, so the timer did not end it (OBSERVED, one boot)
the monitor's own stop path ran once, in boot 20: `MON STOP WATCHDOG` at the 5 s box
was RELAXED for rung 1 by the approval of the M1 record
and for rung 2 by the approval of its record (boot 21: the call took 2.04 s and UPTIME read 36 S on the closer view, with no restart)
Boot 20 (2026-11-18): the gate passed with this record on the unit
The gate passed with this record again in boot 21 (`STATUS=0X0`; OBSERVED, one boot) and in boots 23, 24, 25, 26, 27 and 28 (OBSERVED, one boot each); nothing changes for the regime question
Boot 20 ran under it: one CPU for a call of 5.04 s (the record requires AC power; no temperature was read)
Boot 21 ran under it too: one CPU for a call of 2.04 s (no temperature was read)
Rung 2, DECIDED 2026-11-18 about 09:46 ("Approve 1ad122b3 (rung 2)", by the SHA-256 of its own record, which also decided D9's panel choice for it)
The panel-handed choice was then DECIDED for rung 2 by the approval of 2026-11-18 about 09:46 ("Approve 1ad122b3 (rung 2)", by the SHA-256 of its own record; D4)
GUEST.DAT 118,908,480 B in 46 chunks, and no `-noprogress`, so that the run tests the analysis of boot 20
its code was inspected for the stop site only, by structure
What the kernel does under the monitor past its first CPU-start write: UNKNOWN
SMP, how the guest behaves above 4 GiB, the kernel past its first CPU-start write with a CPU started (boots 25, 26, 27 and 28 passed it with the stores dropped) and stage-2 fault syndromes beyond the three that ended boots 21, 24 and 26 (a write, a read and a write, all translation faults at level 1: INFERRED decodes; boot 28's stop repeats boot 24's): none has run
What boot 21 showed (rung 2; OBSERVED, one unit, one boot;
`MON STOP S2_FAULT` with `PC=0XFFFFFE00097A352C`, `A=0X300734004`, `B=0X93970045` and `C=0XFFFFFE60A0178004`, 39,245,286 ticks for the whole call (2.04 s, inside the box of 5 s)
`ABORTS=0`, `TRAPS=136424` and `CALLS=65955` (cumulative counts, not rates)
the falsifier of its analysis (the same stop PC again) did not occur, and the panel shows phases that follow `PE_init_iokit`
it is not separated from the other input that changed with it (`serial=2`) and stays INFERRED
as a 32-bit write from EL1 with a translation fault at level 1: an IPA with no stage-2 mapping, whose page offset `0x004` equals the `FAR`'s
`A=` is exactly the first register of the CPU-start window that the monitor's source names (`probes/xnuboot/trap_dispatch.c:430-444`: a write there reaches the release code only with a host CPU set, which the M1 profile has not) and is DT window 24 at `0x300730000` plus `0x4004` (`probes/devicemodel.py:752-757`; VERIFIED, read in the sources)
the offset that the A16 sources document for XNU's first CPU-start write (`compat/boot-relocate.h:147-153`: PMGR block 24, offset `0x4004`); that the guest's write is that write is INFERRED, by address: the value written (`x23`) and the writing function were not printed
that CPU bring-up works (it was not reached), SMP, a root device, storage, userspace, stability, performance
why 925 single-steps were counted (UNKNOWN)
a guest-input `cpus=1` (no monitor, wrapper or reporter change; the A16 record says that a reduced CPU set trips a PMGR assertion earlier in boot, which one is UNKNOWN)
a monitor that accepts the CPU-start write and starts nothing, with a guest word that makes the kernel wait longer for the CPU (then a `cpu N failed to boot` panic about a second later: INFERRED from the public source)
real secondaries last (the wrapper cannot build the secondary configuration: UNIMPLEMENTED
Each needs a record of its own and the operator's decision.
Boot 23 (rung 3, WI 11) asked the kernel for one CPU (`cpus=1`): it honoured it, ran on one vCPU for 2.17 s without the CPU-start stop, and the monitor stopped it at an exception it took at EL1 (`MON STOP GUEST_EXC`), INFERRED (high confidence) to be the kernel's own debugger trap, the cause UNKNOWN
**Boot 23, rung 3 (2026-11-18, plan `M1RUN-CPUS1-RUNG3`; OBSERVED, one unit, one boot).**
the monitor stopped the guest itself, 2.17 s into the call, as `MON STOP GUEST_EXC` (`A=0X2000000`: `ESR_EL1` class 0, an undefined instruction taken at EL1; the last two digits of `C=` were cut by the page's width)
M1 PASSED by its own rows (the stop kinds of row d include `GUEST_EXC`); rung 3's own verdict is NOT PASSED (c) by the letter of its record, because a guest exception is not among the ends that its PASSED result allows.
Rows a, c, d, e and f held (f by the host check of the stick, read only: the 18 staged files unchanged and `REPORT.DAT` not written back, so the page is the only record of the run), row b was not exercised and row g held as a host source comparison (no monitor source changed between ea59368 and 3f48b0a: VERIFIED, host; the byte comparison of the builds was not repeated: NOT RUN).
which a host analysis after the boot INFERS (high confidence) to be the guest kernel's own debugger trap, so that the kernel had entered its panic or debugger funnel; its text is printed only after the trap, which the monitor stopped on, so the panel shows none, and the cause is UNKNOWN
boot 23's, `MON STOP GUEST_EXC`, ended it earlier too, at an exception that the guest took at EL1 (OBSERVED, one boot)
and again in boot 21 (OBSERVED, one boot) and in boots 23, 24, 25, 26, 27 and 28 (OBSERVED, one boot each), six vCPUs refuse
and again in boot 23 (`STATUS=0X0`, OBSERVED, one boot)
Rung 3 (boot 23) kept that choice and added `cpus=1`: the kernel ran on, `ABORTS=0` again, to an exception at EL1 that the monitor stopped on (section 14)
boot 23 ended at a guest exception (`MON STOP GUEST_EXC`) before the box; boot 24 ended at a stage-2 fault (`MON STOP S2_FAULT`) before the box); U4
in boot 23 `ABORTS=0` again and the stop was a guest exception, not a stage-2 fault
boot 23 ran them for 2.17 s, with no restart in the 7 s after the report page of its 25.19 s film, alive at UPTIME 18 S at its end
Boot 23 ran under it too: one CPU for a call of 2.17 s (no temperature was read)
Rung 3, DECIDED 2026-11-18 about 15:05 ("Approve 749361cf (rung 3)", by the SHA-256 of its own record, which also kept D9's panel choice for it)
Rung 3 kept the choice by its own approval of 2026-11-18 about 15:05 ("Approve 749361cf (rung 3)", by the SHA-256 of its own record; D4), with `cpus=1` added to the guest's command line; boot 23 ran under it (section 14)
**What boot 23 showed (rung 3, `cpus=1`; OBSERVED, one unit, one boot; `docs/SP11-STICK-LOG.md` section 21).**
A host analysis after the boot INFERS (high confidence; the public XNU source, tag `xnu-12377.121.6`, and the local kernelcache read as a file; no KDK, so no symbolication: NOT RUN) that the exception is the guest kernel's own debugger trap, a public constant, and not a missing CPU feature
so that hiding a feature in the ID registers does not apply and emulating an instruction in the monitor is the wrong tool
The next option (INFERRED; none is a decision, none is authorised): let the guest's own panic handler run through the monitor's existing `let_panic` diagnostic, a data word, so that its text reaches the panel; whether the panic path survives on this stack is UNKNOWN (boot 24 took this option: the next paragraph).
Not shown: that any driver started, CPU bring-up, SMP, a root device, stability, performance, or what the cause is.
22 = plan nvmesnap-s1, the read-only storage survey, no monitor stage, 23 = plan M1RUN-CPUS1-RUNG3, rung 3 with `cpus=1`, 24 = plan M1RUN-LETPANIC-RUNG4, rung 4 with the monitor's `let_panic` word set (all 2026-11-18), 25 = plan M1RUN-ACKPANIC-RUNG5, rung 5 with the CPU-start stores acknowledged, 26 = plan M1RUN-ACKPANIC-RUNG6, rung 6 with `cpu_boot_timeout_secs=90` and a box of 120 s (both 2026-11-19), and 27 = plan M1RUN-ACKPANIC-Q-RUNG7, rung 7 with the `quiet` device tree and `cpu_boot_timeout_secs=120` (2026-11-20)
Boot 24 (rung 4, WI 11) set the monitor's `let_panic` word: the kernel's own handler ran and printed a panic text, an ApplePMGR assertion (`enableCPUComplex`, `complex < _cpuComplexCount`), then nested panics, and the monitor stopped the run at a stage-2 fault 2.25 s into the call (OBSERVED, one unit, one boot; section 22 of the log; why the assertion failed is UNKNOWN); the rung's class is PANIC TEXT, M1's PASSED.
**Boot 24, rung 4 (2026-11-18, plan `M1RUN-LETPANIC-RUNG4`; OBSERVED, one unit, one boot).**
the one new input is the monitor's `let_panic` PARAMS word, set to 1 (data only: `docs/SP11-STICK-MONITOR.md` K12)
Rows a, c, d, e and f held (f by the host check of the stick, read only: the 18 staged files unchanged and `REPORT.DAT` not written back), row b was not exercised and row g held for the pair that ran
The kernel's own exception handler ran where boot 23's run had been stopped and printed a panic text on the panel, an ApplePMGR assertion (`enableCPUComplex(UInt32, bool)`, `REQUIRE failed: complex < _cpuComplexCount`), then nested panics up to `Nested panic count exceeds limit 5, machine will reset or spin` and `wdog: panic chipreset`
the monitor's console ring carried the first lines of the text onto the report page (six `MON CON` lines; boots 21 and 23 had none)
and the monitor stopped the run itself, 2.2465 s into the call, as `MON STOP S2_FAULT` with `A=0X3082BC224` (INFERRED: a 32-bit read of an address with no stage-2 mapping, in the kernel's chip-reset path), on a clean report
M1 PASSED by its own rows; rung 4's class is PANIC TEXT (the record's first result that fits: a result, neither a pass nor a failure).
Why the assertion failed and the rest of 22.6's questions are UNKNOWN. One vCPU, early boot only, nothing about SMP, a root device, stability, performance or the NV2 regime; no second boot.
**What boot 24 showed (rung 4, `let_panic` set; OBSERVED, one unit, one boot; `docs/SP11-STICK-LOG.md` section 22).**
The page ended in `MON STOP S2_FAULT` with `PC=0XFFFFFE0008BA7358`, `A=0X3082BC224`, `B=0X938A0005` and `C=0XFFFFFE612A564224`, 43,133,334 ticks for the whole call (2.2465 s, inside the box of 5 s), with `ABORTS=0`, `TRAPS=143889` and `CALLS=64986`
its six `MON CON` lines carried the first lines of the panic text (OBSERVED; boots 21 and 23 had none; INFERRED: the panic path writes its text through the serial-putc call that the monitor logs)
a hypothesis, not verified: `_cpuComplexCount` counts the complexes of the CPUs that stay enabled, while the boot CPU's complex index is not below that count
the assertion is new with `cpus=1`, since boot 21 passed the driver with six CPUs
that the frozen monitor's refusal after a panic entry did not stop the run (no `MON STOP UNNAMED`; INFERRED: no refused call was made)
Not shown: that the kernel gets further with any change, CPU bring-up, SMP, a root device, stability, performance, or that macOS boots; there is no second boot.
In boot 24 (`serial=2`) the window held the first six lines of the kernel's panic text (OBSERVED, one boot; INFERRED: the panic path writes its text through the serial-putc call that the monitor logs, and the ordinary log does not).
in boot 24 (`serial=2`) the page had six, the first lines of the kernel's panic text
Rung 4 (boot 24) kept the choice and set `let_panic`: the kernel's own handler ran and printed its panic text, `ABORTS=0` again, to a stage-2 fault (section 14).
boot 24 ran them for 2.25 s, with no restart in the 26 s after the report page of its 56.2 s film, the heartbeat read up to UPTIME 33 S
Boot 23 ended at a guest exception and boot 24 at a stage-2 fault, both before the box (OBSERVED, one boot each); boot 25's stop timer fired at the box of 60 s (`MON STOP WATCHDOG`; OBSERVED, one boot); boot 26 ended at a stage-2 fault before its box of 120 s (OBSERVED, one boot); boot 27's stop timer fired at the box of 120 s (`MON STOP WATCHDOG`; OBSERVED, one boot); boot 28 ended at a stage-2 fault before its box of 300 s, after the guest's own panic (OBSERVED, one boot).
Nor in boots 23, 24, 25, 26, 27 and 28 (`ABORTS=0`); the one stage-2 fault of boot 24, that of boot 26 and that of boot 28 were the stops too (item 5).
Reached again in boot 24 (OBSERVED once), after the kernel's own panic text: `MON STOP S2_FAULT` with `A=0X3082BC224`, a 32-bit read, 2.2465 s into the call (above); the device is the watchdog's reset register (VERIFIED, host; [SP11-KERNEL-STOPS.md](SP11-KERNEL-STOPS.md) section 6). Not reached in boot 25.
7. A kernel panic on a platform assertion: the design note after boot 21
Reached in boot 24 (OBSERVED once; `docs/SP11-STICK-LOG.md` section 22), with `let_panic` set so that the kernel's handler ran: an ApplePMGR assertion in `enableCPUComplex` (`complex < _cpuComplexCount`), then nested panics, `machine will reset or spin` and `wdog: panic chipreset`, ended by item 5's stage-2 fault.
Rung 4, DECIDED 2026-11-18 about 21:31 ("Approve 67d814f6 (R6.2) and stage the stick", by the SHA-256 of its own record): the same store classes and the same two programs, one more word in the guest, the monitor's `let_panic` PARAMS word, the run happened (boot 24), and the approval covered that one boot.
Rung 4 kept it again by its own approval of 2026-11-18 about 21:31 ("Approve 67d814f6 (R6.2) and stage the stick", by the SHA-256 of its own record; D4), with the monitor's `let_panic` word set; boot 24 ran under it (section 14).
Boot 24 ran under it too: one CPU for a call of 2.25 s (no temperature was read).
boots 23 and 24 (`cpus=1`) did not make that write, ending at an exception and at the kernel's own panic text
the monitor entry, WI 4b, ran eight times: boot 20, 5 s, one vCPU, INFERRED stuck in an early loop;
boot 24, 2.25 s, one vCPU, to the kernel's own panic text, an ApplePMGR assertion, and a stage-2 fault
boot 24's, `MON STOP S2_FAULT` after the kernel's own panic text, ended it at 2.2465 s, at a stage-2 fault (OBSERVED, one boot).
boot 23 at a guest exception, boot 24 at a stage-2 fault)
**Boot 25, rung 5 (2026-11-19, plan `M1RUN-ACKPANIC-RUNG5`; OBSERVED, one unit, one boot).** The monitor acknowledges and drops the CPU-start stores
rung 5's class is WATCHDOG, NO TEXT, with PROGRESS (section 14;
**What boot 25 showed (rung 5; OBSERVED, one unit, one boot;
`MON STOP WATCHDOG` with `PC=0XFFFFFE000B8716A8` after 1,155,520,917 ticks (60.18 s: the stop timer ended the run at the box)
The PC follows a WFI in the kernel's idle path with interrupts masked (VERIFIED, host; INFERRED: CPU 0 in its idle thread;
whether any was delivered is UNKNOWN
rung 5 RAN once on the unit (boot 25, 2026-11-19, plan `M1RUN-ACKPANIC-RUNG5`; OBSERVED, one unit, one boot; section 23)
Rung 5 (boot 25): rows a, c, d, e and f held (b not exercised; g by a host baseline comparison, IDENTICAL); `MON STOP WATCHDOG` at 60.18 s; M1 PASSED, rung 5's class WATCHDOG, NO TEXT, with PROGRESS
Rung 5, DECIDED 2026-11-19 about 05:50 ("Approve 64d87443 (rung 5, one boot)"): the same store classes, the run happened (boot 25), and the approval covered that one boot.
Boot 25 used it: 60.18 s.
Boot 25 ran under it: one CPU for a call of 60.18 s (no temperature was read).
| 12 | **Kernel stops**: host reads of boots 20, 21, 23, 24, 25 and 26, the reading of boot 27's wait and the reading of boot 28's userspace failure, EXPERIMENTAL (`docs/SP11-KERNEL-STOPS.md`) | 11 |
**Boot 26, rung 6 (2026-11-19, plan `M1RUN-ACKPANIC-RUNG6`; OBSERVED, one unit, one boot).**
rung 6's class is NEW STOP (section 14; `docs/SP11-STICK-LOG.md` section 24)
**What boot 26 showed (rung 6; OBSERVED, one unit, one boot;
`MON STOP S2_FAULT` with `PC=0XFFFFFE00095FF9AC`, `A=0X291000004`, `B=0X93880045` and `C=0XFFFFFE63AA8A0004` after 1,182,753,011 ticks (61.60 s, inside the box of 120 s)
the counters show guest timer interrupts delivered (the counters are OBSERVED; the delivery is INFERRED from them, since no other CPU runs)
It does not settle why that resource is never published
D5'': the operator chose a box of 120 s in the app on 2026-11-19
Rung 6, DECIDED 2026-11-19 (by the SHA-256 of its own record, 8-digit prefix d9d79c87;
Boot 26 ran with the box of 120 s of D5'': one CPU for a call of 61.60 s.
Rung 6 (boot 26): rows a, c, d, e and f held (b not exercised; g by a host baseline comparison, IDENTICAL); `MON STOP S2_FAULT` with `A=0X291000004` at 61.60 s
rung 6 RAN once on the unit (boot 26, 2026-11-19, plan `M1RUN-ACKPANIC-RUNG6`; OBSERVED, one unit, one boot; section 24)
Reached in boot 26 (OBSERVED once), in the kernel's normal start-up and not in a panic path: `MON STOP S2_FAULT` with `A=0X291000004`
Boot 26: the same (`STATUS=0X0`), and no panic text.
nor in boot 26 (the unit stood on the page to the end of its film, about 12 s after the page appeared)
**Boot 27, rung 7 (2026-11-20, plan `M1RUN-ACKPANIC-Q-RUNG7`; OBSERVED, one unit, one boot).**
rung 7's class is RUNG 7 KERNEL WAITING (section 14; `docs/SP11-STICK-LOG.md` section 25)
Boot 27 (rung 7, WI 11) ran the whole box of 120 s with the `quiet` device tree (43 nodes withdrawn by data)
and g held on the host (VERIFIED, `git diff`: the monitor sources of rungs 6 and 7 are identical, the only difference being the builder-side data module `probes/guestprofile_m1.py`; the A16 baseline comparison ran on the record's first version and was not run again on the committed tree)
**What boot 27 showed (rung 7; OBSERVED, one unit, one boot; `docs/SP11-STICK-LOG.md` section 25).**
`MON STOP WATCHDOG` with `PC=0XFFFFFE000B8716A8` (boot 25's), `A=0X96000047`, `B=0XFFFFFE110E00C040` and `C=0XFFFFFE000BA87EA0` after 2,307,553,890 ticks (120.185 s, the box's end)
the kernel waits for a root device (INFERRED; the guest has no storage)
only the two timeout messages cannot
Rung 7, DECIDED (by the SHA-256 of its own record; the date, the hour and the wording are not in the notes this entry was written from)
Boot 27 ran with it too: one CPU for a call of 120.185 s (no temperature was read).
boot 27 used it again (rung 7; approving its record confirms it): the call took 120.185 s, ended by the monitor's own watchdog at the box's end
Boot 27: the same (`STATUS=0X0`), and no panic text.
Not reached in boot 27: with the scaler withdrawn by data the run ended in the kernel's wait for a root device, at the monitor's own box.
nor in boot 27 (the report page was on the panel from 148 s; the film ends at 152.4 s)
boot 27, 120.185 s, one vCPU, through the BSD layer to the kernel's wait for a root device and the monitor's own watchdog
**Boot 28, rung 9 (2026-11-20, plan `M1RUN-ACKPANIC-M-RUNG9`; OBSERVED, one unit, one boot; rung 8, the clock alone, was skipped by the operator and never run).**
rung 9's class is RUNG 9 PANIC TEXT (section 14; `docs/SP11-STICK-LOG.md` section 26)
**What boot 28 showed (rung 9; OBSERVED, one unit, one boot; `docs/SP11-STICK-LOG.md` section 26).**
the firmware placed it at `0XF8D2A1000` in one descriptor, the wrapper copied it in 26.1 s (`TICKS HZ=19200000 PLACED=40419 COPIED=501263277`)
`launchd`'s first boot task `mount-phase-1` found no volume of role 0x10 or 0xC0, waited ten times for an APFS node and failed, and the guest printed `userspace panic: boot task failure: mount-phase-1 - exited due to SIGABRT`
That the volumes `mount-phase-1` asks for are the ISC container's is INFERRED, strongly.
Rung 9, the first root-mount form, DECIDED (by the SHA-256 of its own record, which this entry does not repeat;
is the box of 300 s that rung 9's record names
Boot 28 ran the carry (about two minutes of one CPU at EL2: the runner's read and hash, the wrapper's digests and copy) and then a call of 40.08 s inside the box of 300 s that rung 9's record names (no temperature was read).
Boot 28: the same (`STATUS=0X0`); the guest did panic, in userspace (item 7), and `launchd`, an arm64e process, ran for about 36 s with no PAC refusal seen
Reached again in boot 28 (OBSERVED once), after `launchd`'s first boot task failed and the guest panicked in userspace: `MON STOP S2_FAULT` with `PC=0XFFFFFE0008BA7358`, boot 24's
nor in boot 28 (the report page was drawn, and film 2 ends 5 s after it appeared).
Boot 28's first panic is a userspace panic (`userspace panic: boot task failure: mount-phase-1 - exited due to SIGABRT`), the text of the panic that follows `launchd`'s failed boot task and not a platform assertion
boot 28, 40.08 s, one vCPU, through the root mount to `launchd`'s failed first boot task, the guest's userspace panic and the monitor's stop at boot 24's address (INFERRED: the kernel's chip-reset read)
'''.splitlines()


def q1n1_tree():
    candidates = [Path(os.environ['Q1N1_SP11_TREE'])] if os.environ.get('Q1N1_SP11_TREE') else []
    for base in (ROOT.parent, ROOT.parent.parent, ROOT.parent.parent.parent):
        candidates.append(base / 'q1n1')
        candidates.extend(sorted(base.glob('q1n1-worktrees/*sp11*')))
    for tree in candidates:
        if (tree / 'platform/uefi/main.c').is_file() and (tree / 'docs/SP11-PAYLOAD.md').is_file():
            return tree
    return None


def resolve(rel, tree):
    """(path, skip_reason): the file a citation names, or why it cannot be checked here."""
    if rel.startswith('q1n1:'):
        return (tree / rel[5:], None) if tree else (None, 'no q1n1 checkout')
    path = ROOT / rel
    if not path.exists() and rel.startswith(LOCAL_ONLY):
        return None, 'local-only file absent'
    return path, None


def lines_of(path):
    return path.read_text(errors='replace').splitlines()


def check_citations(text, tree):
    """(problems, checked): every citation, quotation and bare path in `text`, held against the files."""
    problems, checked = [], 0
    refs = [(m[1], int(m[2]), int(m[3] or m[2]), None) for m in CITE.finditer(text)]
    refs += [(m[2], int(m[3]), int(m[4] or m[3]), m[1].replace('\\|', '|')) for m in QUOTE.finditer(text)]
    refs += [(m[1], None, None, None) for m in BARE.finditer(text)]
    for rel, first, last, needle in refs:
        path, skip = resolve(rel, tree)
        if skip:
            continue
        checked += 1
        if not path.is_file():
            problems.append(f'no such file: {rel}')
        elif first is not None:
            lines = lines_of(path)
            if not 1 <= first <= last <= len(lines):
                problems.append(f'line out of range: {rel}:{first}-{last} (file has {len(lines)} lines)')
            elif needle is not None and needle not in '\n'.join(lines[first - 1:last]):
                problems.append(f'needle not found: `{needle}` at {rel}:{first}-{last}')
    return problems, checked


def table_rows(text):
    """Yield (header cells, data row cells, raw row) for every table row, tables found by their `| --- |` rule."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith('|') and i + 1 < len(lines) and re.match(r'^\|(\s*:?-{3,}:?\s*\|)+\s*$', lines[i + 1]):
            header = split_row(lines[i])
            j = i + 2
            while j < len(lines) and lines[j].startswith('|'):
                yield header, split_row(lines[j]), lines[j]
                j += 1
            i = j
        else:
            i += 1


def split_row(row):
    return [c.strip() for c in re.split(r'(?<!\\)\|', row.strip())[1:-1]]


def check_labels(text):
    problems = []
    for word in LOOKALIKES:
        if re.search(r'\b' + word + r'\b', text):
            problems.append(f'look-alike label: {word}')
    for header, row, raw in table_rows(text):
        if 'Label' not in header:
            continue
        index = header.index('Label')
        if len(row) != len(header):
            problems.append(f'row has {len(row)} cells, header {len(header)}: {raw[:60]}')
            continue
        cell = re.sub(r'\([^)]*\)', '', row[index])
        tokens = [t.strip() for t in re.split(r'/|,|;|\band\b', cell) if t.strip()]
        if not tokens:
            problems.append(f'empty label: {raw[:60]}')
        for token in tokens:
            if token not in LABELS:
                problems.append(f'unknown label {token!r}: {raw[:60]}')
        if 'OBSERVED' in tokens and not SOURCE_OF_OBSERVED.search(raw):
            problems.append(f'OBSERVED row without a source: {raw[:60]}')
    return problems


def check_structure(text):
    problems = []
    data = text.encode()
    if len(data) >= MAX_BYTES:
        problems.append(f'{len(data)} bytes, limit {MAX_BYTES}')
    if not text.isascii():
        problems.append('not ASCII')
    if not text.startswith('<!-- SPDX-License-Identifier: MIT -->\n'):
        problems.append('first line is not the MIT SPDX line')
    for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
        if prefix in text:
            problems.append('machine path in the document')
    if 'Hardware status for the port itself: NOT RUN' not in text[:900]:
        problems.append('the port is not stated NOT RUN at the top')
    headings = re.findall(r'^## (\d+)\. (.+)$', text, re.M)
    numbers = [int(n) for n, _ in headings]
    if numbers != [n for n, _ in SECTIONS]:
        problems.append(f'section numbers are {numbers}')
    else:
        for (n, keyword), (_, title) in zip(SECTIONS, headings):
            if keyword not in title.lower():
                problems.append(f'section {n} is {title!r}, expected {keyword!r}')
    return problems


def plan_rows(text):
    rows = {}
    for header, row, raw in table_rows(text):
        if header[:2] == ['WI', 'Work'] and len(row) == len(header):
            rows[int(row[0])] = dict(zip(header, row))
    return rows


def check_plan(text):
    problems = []
    rows = plan_rows(text)
    if sorted(rows) != list(range(1, len(rows) + 1)) or len(rows) < 8:
        problems.append(f'work items are {sorted(rows)}')
    decisions = set(re.findall(r'^- (D\d+) ', text.split('## 15.')[-1].split('## 16.')[0], re.M))
    unknown_defs = set(re.findall(r'\b(U\d+) ', text.split('Unknowns referenced:')[-1].split('| WI |')[0]))
    resolved, edges = set(), {}
    for number, row in rows.items():
        edges[number] = [int(d) for d in re.findall(r'\b\d+\b', row['Depends'])]
        for dep in edges[number]:
            if dep not in rows:
                problems.append(f'WI {number} depends on missing WI {dep}')
        for decision in re.findall(r'\bD\d+\b', row['Depends'] + ' ' + row['UNKNOWN / decision']):
            if decision not in decisions:
                problems.append(f'WI {number} names {decision}, which section 15 does not define')
        for unknown in re.findall(r'\bU\d+\b', row['UNKNOWN / decision']):
            resolved.add(unknown)
        if not row['Acceptance (can fail)'] or not row['Host-first evidence']:
            problems.append(f'WI {number} has no acceptance or no host-first evidence')
    state = {}

    def visit(node, path):
        if state.get(node) == 'done' or node not in rows:
            return
        if state.get(node) == 'open':
            problems.append(f'dependency cycle through WI {node}: {path}')
            return
        state[node] = 'open'
        for dep in edges[node]:
            visit(dep, path + [dep])
        state[node] = 'done'

    for number in rows:
        visit(number, [number])
    if unknown_defs - resolved:
        problems.append(f'unknowns with no work item: {sorted(unknown_defs - resolved)}')
    if len(decisions) < 8:
        problems.append(f'only {len(decisions)} decisions listed')
    return problems


def check_tables(text):
    """Every block of `|` lines needs a header, an exact separator row and rows as wide as the header (a lost newline would
    turn a table into prose and escape the label checks)."""
    problems, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        if not lines[i].startswith('|'):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].startswith('|'):
            j += 1
        block = lines[i:j]
        if len(block) < 3 or not SEPARATOR.match(block[1]):
            problems.append(f'table without a separator row at line {i + 1}: {block[0][:50]}')
        else:
            width = len(split_row(block[0]))
            problems += [f'line {i + 1 + k} has {len(split_row(row))} cells, the header {width}'
                         for k, row in enumerate(block) if len(split_row(row)) != width]
        i = j
    return problems


def squash(text):
    return ' '.join(text.split())


def section(text, number):
    found = re.search(rf'^## {number}\. .*?(?=^## {number + 1}\. |\Z)', text, re.M | re.S)
    return found.group(0) if found else ''


def check_claims(text):
    """The restart section and the survey: refuted statements stay out, the numbers and the order stay in."""
    flat, problems = squash(text), []
    for pattern, why in REFUTED:
        if re.search(pattern, flat, re.I):
            problems.append(f'refuted ({why}): /{pattern}/')
    for sentence in re.split(r'(?<=\.)\s+', flat):
        if re.search(r'\b16 ?s\b', sentence, re.I) and 'first heartbeat' not in sentence:
            problems.append(f'a 16 s figure that is not the first-heartbeat note: {sentence[:70]}')
    problems += [f'missing: {needle}' for needle in REQUIRED if needle not in flat]
    if len(re.findall(r'20 s of UPTIME', flat)) < 3:
        problems.append('the 20 s UPTIME budget must stand in sections 10, 14 and 15')
    if len(re.findall(r'precaution of low weight', flat)) < 3:
        problems.append('the 20 s UPTIME budget is a precaution of low weight in section 10, stop rule 4 and D5')
    if len(re.findall(r'UPTIME 17 S', flat)) < 2:
        problems.append('the earliest plausible restart, UPTIME about 17 S, must stand in sections 10 and 15')
    restart = squash(section(text, 10))
    if 'UPTIME 224 S and 268 S' not in restart:
        problems.append('section 10 must give the restart control: the read-only runner flow alive at UPTIME 224 S and 268 S')
    rows = plan_rows(text)
    row = rows.get(10, {})
    survey = (row.get('Work', '') + ' ' + row.get('Acceptance (can fail)', '')).strip()
    if not re.search(r'before SMP work.*M1 does not need.*operator in the loop.*DONE.*psci-only.*UPTIME 224 S and 268 S'
                     r'.*\(a\).*cpuon page until it restarts.*\(b\).*ONE core only.*\(c\).*cores running.*2 of 3',
                     survey, re.S):
        problems.append('work item 10 must say: before SMP work only, operator in the loop, psci-only control DONE (UPTIME 224 S '
                        'and 268 S), left (a) film cpuon, (b) one core, (c) cores left running; cpuon 2 of 3')
    if re.search(r'\b10\b', rows.get(11, {}).get('Depends', '')):
        problems.append('the M1 run (WI 11) must not wait for the restart survey (WI 10): M1 starts no secondary')
    secondaries = squash(section(text, 7))
    problems += [f'section 7 lacks: {needle}' for needle in SECONDARY_STATE if needle not in secondaries]
    return problems


def check_id_registers(text):
    """The unit's ID registers as pinned above: the document's hex values and the fields it decodes must agree."""
    flat, problems = squash(text), []
    for name, value in (('ID_AA64DFR0_EL1', DFR0), ('ID_AA64ISAR0_EL1', ISAR0)):
        if f'{name} 0x{value:016X}' not in flat:
            problems.append(f'missing {name} 0x{value:016X}')
        problems += [f'{name} 0x{quoted} contradicts the pinned value' for quoted in
                     re.findall(name + r' 0x([0-9A-Fa-f]{16})', flat) if int(quoted, 16) != value]
    for field, (register, lsb) in FIELDS.items():
        claimed = re.findall(rf'\b{field} (\d+)(?!\s+to\s+\d)', flat)      # "DebugVer 6 to 11" is the monitor's range
        if not claimed and field in STATED:
            problems.append(f'the document states no {field}')
        problems += [f'{field} {n} contradicts the register ({(register >> lsb) & 15})'
                     for n in claimed if int(n) != (register >> lsb) & 15]
    breakpoints = ((DFR0 >> 12) & 15) + 1
    problems += [f'{word} breakpoints contradicts BRPs {breakpoints - 1} ({breakpoints} breakpoints)'
                 for word, number in COUNT_WORDS.items() if f'{word} breakpoints' in flat and number != breakpoints]
    return problems


def check_port_state(text):
    """The document's statements about the tree as it is, held against the sources that decide them."""
    flat = squash(text)
    problems = [f'state lacks: {needle}' for needle in STATE_REQUIRED if needle not in flat]
    header, interface = (ROOT / GUARD_HEADER).read_text(), (ROOT / BUNDLE_DOC).read_text()
    for define, shown in (('SNAP_SP11_TIMER_STATUS_CNTHCTL', '-147'), ('SNAP_SP11_TIMER_STATUS_CNTVOFF', '-148'),
                          ('SNAP_SP11_EVENT_TIMER', '0x5443')):
        if not re.search(rf'{define}\s+\(?{shown}\)?', header) or shown not in flat:
            problems.append(f'{GUARD_HEADER} and the document disagree on {define} = {shown}')
    names = tuple(re.findall(r'^\| \d \| `([A-Z0-9]+\.[A-Z]+)` \|', interface, re.M))
    if names != BUNDLE_FILES:
        problems.append(f'{BUNDLE_DOC} lists {names}, not the five files the document names')
    for needle, size in BUNDLE_SIZES:
        if f'exactly {size} bytes' not in interface or needle not in flat:
            problems.append(f'the interface and the document disagree on {needle}')
    start = flat.find('is five files, the runner\'s whole manifest')
    places = [flat.find(f'`{name}`', start) for name in BUNDLE_FILES]
    if start < 0 or -1 in places or places != sorted(places):
        problems.append('the bundle paragraph must name the five files in the interface\'s order')
    rows = plan_rows(text)
    for number, needles in WORK_ITEM_STATUS.items():
        row = squash(' '.join(rows.get(number, {}).values()))
        problems += [f'WI {number} lacks: {needle}' for needle in needles if needle not in row]
    graph = {4: '1, 8', 6: '4, 5', 7: '3, 4a, D1, D7', 8: '4a, 7', 11: '3, 4, 5, 6, 8, 9'}   # WI 8's arm half precedes 4b
    problems += [f'WI {n} must depend on {d}' for n, d in graph.items() if rows.get(n, {}).get('Depends') != d]
    if 'D11' not in rows.get(3, {}).get('UNKNOWN / decision', '') or '- D11 The ECAM rule' not in text.split('## 15.')[-1]:
        problems.append('WI 3 is gated by D11 and section 15 keeps it: the ECAM rule')
    return problems


def load_adapter():
    """(board_descriptor, bootcontract, the descriptor tests' boot-ready fixture)."""
    import importlib.util
    import sys
    for sub in ('probes', 'tools'):
        if str(ROOT / sub) not in sys.path:
            sys.path.insert(0, str(ROOT / sub))
    import board_descriptor
    import bootcontract
    spec = importlib.util.spec_from_file_location('descriptor_fixture', ROOT / 'tools' / 'test-board-descriptor.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return board_descriptor, bootcontract, fixture


TEXT = DOC.read_text()
TREE = q1n1_tree()


class Structure(unittest.TestCase):
    def test_size_hygiene_sections_and_not_run(self):
        self.assertEqual(check_structure(TEXT), [])

    def test_tables_are_well_formed(self):
        self.assertEqual(check_tables(TEXT), [])

    def test_the_test_file_follows_its_own_rules(self):
        me = Path(__file__)
        text = me.read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]))
        self.assertTrue(text.isascii())
        self.assertLess(me.stat().st_size, 112 * 1024)      # raised from 104 KiB on purpose for the pins of boot 28 (from 96 KiB for boot 26, 80 KiB for boot 23, 64 KiB for boot 21, 52 KiB for boot 20)
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
            self.assertNotIn(prefix, text)

    def test_m1_has_its_seven_acceptance_rows_and_the_decisions_and_gaps_end_the_document(self):
        rows = [r for h, r, _ in table_rows(TEXT) if h[:2] == ['#', 'Acceptance (falsifiable)']]
        self.assertEqual([r[0] for r in rows], list('abcdefg'))
        tail = TEXT.split('## 15.')[-1]
        self.assertTrue(tail.startswith(' Decisions I need from the operator'))
        self.assertIn('## 16. What I could not establish', tail)


class Labels(unittest.TestCase):
    def test_label_tables_and_lookalikes(self):
        self.assertEqual(check_labels(TEXT), [])

    def test_every_label_is_used_somewhere(self):
        for label in ('VERIFIED', 'OBSERVED', 'INFERRED', 'UNKNOWN', 'UNIMPLEMENTED'):
            self.assertIn(label, TEXT)


class Citations(unittest.TestCase):
    def test_repository_citations_and_quotations(self):
        problems, checked = check_citations(TEXT, None)
        self.assertEqual(problems, [])
        self.assertGreater(checked, 80)
        self.assertGreater(len(QUOTE.findall(TEXT)), 40)

    def test_q1n1_citations(self):
        if TREE is None:
            self.skipTest('no q1n1 checkout found (set Q1N1_SP11_TREE): the q1n1 citations are not checked')
        problems, checked = check_citations(TEXT, TREE)
        self.assertEqual(problems, [])
        self.assertGreater(len(re.findall(r'q1n1:[\w/.+-]+:\d+', TEXT)), 3)


class Plan(unittest.TestCase):
    def test_work_items_dependencies_decisions_and_unknowns(self):
        self.assertEqual(check_plan(TEXT), [])


class Claims(unittest.TestCase):
    def test_the_restart_numbers_the_survey_and_the_secondary_entry_state(self):
        self.assertEqual(round(100 * (1 - 2 / 3) ** 2), 11)        # both restart controls alive if 2 of 3 boots restarted
        self.assertEqual(check_claims(TEXT), [])

    def test_the_id_registers_the_document_decodes(self):
        self.assertEqual(check_id_registers(TEXT), [])

    def test_the_state_of_the_tree_as_the_document_states_it(self):
        self.assertEqual(check_port_state(TEXT), [])

    def test_the_restart_probe_is_a_source_for_observed_rows(self):
        table = '| Item | Label |\n| --- | --- |\n| entry state | OBSERVED (restart probe, step 3) |\n'
        self.assertEqual(check_labels(table), [])
        self.assertEqual(check_labels(table.replace('restart probe, step 3', 'restart control')), [])
        self.assertTrue(check_labels(table.replace('restart probe, step 3', 'a restart')))


class Arithmetic(unittest.TestCase):
    """Numbers the document quotes, recomputed from the sources and the local dump."""

    def source(self, rel):
        return (ROOT / rel).read_text()

    def test_the_placement_recipe_is_672_mib(self):
        work = int(re.search(r'^WORK_PAGES = (\d+)', self.source('probes/monitor_work.py'), re.M).group(1)) << 12
        guest = int(re.search(r'^DEFAULT_GUEST_MIB = (\d+)', self.source('probes/xnuboot.py'), re.M).group(1))
        formula = 'return max(0x3000000, ((guest_span+0xfffffff)//0x10000000+1)*0x1000000)'
        self.assertIn(formula, self.source('probes/xnuboot.py'))
        span = guest << 20
        reserve = max(0x3000000, ((span + 0xfffffff) // 0x10000000 + 1) * 0x1000000)
        self.assertEqual((work >> 20, guest, reserve >> 20), (240, 384, 48))
        self.assertEqual((span + reserve + work) >> 20, 672)
        for needle in ('672 MiB', '240 work', '48 reserve', '384 guest'):
            self.assertIn(needle, TEXT)

    def test_the_low_placement_is_dead_and_the_dry_run_figures_agree(self):
        """A 288 MiB guest (576 MiB with reserve and work area) cannot fit below the runner's files at the top of the 590 MiB
        low extent (boots 8 to 10); the dry run's figures (boot 10) and the real GUEST.DAT's PARAMS agree."""
        reserve = lambda guest: max(0x3000000, (((guest << 20) + 0xfffffff) // 0x10000000 + 1) * 0x1000000) >> 20
        low, end, file_at, guest_at, guest_size = 0xA8000000, 0xCCE65000, 0xC3E65000, 0xC446E000, 144665408
        self.assertEqual(file_at + (144 << 20), end)
        self.assertEqual(-(-(guest_at + guest_size) // 4096) * 4096, end)         # GUEST.DAT's pages end at the extent end
        self.assertEqual(((file_at - low) >> 20, (guest_at - low) >> 20, 288 + reserve(288) + 240), (446, 452, 576))
        hz, guest_ok, hashed = 19200000, 36519004, 56114326                       # the wrapper's TICKS lines
        self.assertEqual((round(hashed / hz, 2), round((144664080 + 144665408) / (guest_ok / hz) / 2**20)), (2.92, 145))
        self.assertEqual(((337 - 1) * 2, 0x880000000 + 0x2A000000, 0x880000000 + (0x780000 << 12)), (672, 0x8AA000000, 1 << 36))
        self.assertEqual(0x89b000000 - 0x880000000, (384 + reserve(384)) << 20)   # the real GUEST.DAT: guest and reserve
        ctr = 0xB444C004
        self.assertEqual(((1 << ((ctr >> 16) & 15)) * 4, (ctr >> 28) & 1, (ctr >> 29) & 1), (64, 1, 1))    # DminLine, IDC, DIC
        flat = squash(TEXT)
        for needle in ('(452 below `GUEST.DAT`)', 'accepted all 337 probes', 'about 145 MiB/s', 'The wrapper took 2.92 s',
                       '`mem_high` 0x89b000000'):
            self.assertIn(needle, flat)

    def test_the_el2_regime_decodes_as_the_document_says(self):
        tcr = 0x80843514
        self.assertEqual(tcr & 0x3f, 20)           # T0SZ
        self.assertEqual((tcr >> 14) & 3, 0)       # TG0: 4 KiB
        self.assertTrue(17 <= tcr & 0x3f <= 24)    # compat/pauth-native.S accepts 17 to 24
        self.assertIn('T0SZ 20, TG0 4 KiB', TEXT)

    def test_the_firmware_map_figures(self):
        dump = ROOT / 'downloads/sp11/x2-run12/memmap.txt'
        if not dump.is_file():
            self.skipTest('the local boot-6 dump is absent: the memory-map figures are not recomputed')
        conv, end = [], 0
        for line in dump.read_text().splitlines():
            parts = line.split()
            if line.startswith('#') or len(parts) < 7:
                continue
            base, pages = int(parts[2], 16), int(parts[4], 16)
            end = max(end, base + pages * 4096)
            if parts[1] == '07':
                conv.append((base, base + pages * 4096))
        low = [(a, b) for a, b in conv if b <= 1 << 32]
        largest = max(low, key=lambda e: e[1] - e[0])
        aligned = (largest[1] // (32 << 20)) * (32 << 20) - ((largest[0] + (32 << 20) - 1) // (32 << 20)) * (32 << 20)
        bank = max(conv, key=lambda e: e[1] - e[0])
        self.assertEqual(end, 1 << 36)
        self.assertEqual((largest, (largest[1] - largest[0]) >> 20), ((0xA8000000, 0xCCE65000), 590))
        self.assertEqual(aligned >> 20, 576)
        self.assertEqual((bank[0], bank[1], (bank[1] - bank[0]) >> 20), (0x880000000, 1 << 36, 30720))
        self.assertEqual(sum(b - a for a, b in conv) >> 20, 32105)
        for needle in ('590 MiB', '576 MiB', '30720 MiB', '32105 MiB'):
            self.assertIn(needle, TEXT)

    @staticmethod
    def acpi_dumps():
        names = ('RSDP', 'XSDT', 'APIC', 'MCFG', 'GTDT')
        return [d for d in sorted((ROOT / 'downloads/sp11').glob('x*-run*')) if all((d / f'{n}.bin').is_file() for n in names)]

    @staticmethod
    def mcfg_rows(dump):
        """(base, segment, first bus, last bus) of every MCFG allocation, by segment."""
        import struct
        mcfg = (dump / 'MCFG.bin').read_bytes()
        rows = (struct.unpack_from('<QHBBI', mcfg, 44 + 16 * i)[:4] for i in range((len(mcfg) - 44) // 16))
        return sorted(rows, key=lambda row: row[1])

    def test_the_acpi_tables_of_the_local_dumps(self):
        """Every local dump: 12 GICC entries (GICR 0x17080000 + n x 0x40000), a 25-byte GICD at 0x17000000, the ITS, no GICR
        structure, GTDT GSIV 26, eight MCFG allocations."""
        import struct
        dumps = self.acpi_dumps()
        if not dumps:
            self.skipTest('no local ACPI dump: the MADT, MCFG and GTDT figures are not recomputed')
        for dump in dumps:
            madt, offset, found = (dump / 'APIC.bin').read_bytes(), 44, []
            while offset < len(madt):
                found.append((madt[offset], madt[offset + 1], offset))
                offset += madt[offset + 1]
            base = lambda at: struct.unpack_from('<Q', madt, at)[0]
            gicc, (gicd,), (its,) = ([f for f in found if f[0] == kind] for kind in (0xB, 0xC, 0xF))
            self.assertEqual((madt[8], [kind for kind, _, _ in found]), (5, [0xB] * 12 + [0xC, 0xF]), dump.name)
            self.assertEqual({length for _, length, _ in gicc}, {80})
            self.assertEqual([base(at + 60) for _, _, at in gicc], [0x17080000 + n * 0x40000 for n in range(12)])
            self.assertEqual((gicd[1], madt[gicd[2] + 24], its[1]), (25, 0, 20))
            self.assertEqual((base(gicd[2] + 8), base(its[2] + 8)), (0x17000000, 0x17040000))
            self.assertEqual(struct.unpack_from('<I', (dump / 'GTDT.bin').read_bytes(), 72)[0], 26)
            rows = self.mcfg_rows(dump)
            self.assertEqual([(s, f, l) for _, s, f, l in rows], [(n, 0, 255 if n < 3 else 1) for n in range(8)])
            self.assertEqual(rows[2][0], 0x6000000000)
            self.assertGreaterEqual(rows[2][0], 1 << 36)
        for needle in ('0x17380000', 'GSIV 26'):
            self.assertIn(needle, TEXT)

    def test_the_adapter_reads_the_units_tables_up_to_the_ecam_rule(self):
        """A boot-ready board in memory: the adapter passes the CPU, GICD, GICR and ITS rules on every local dump, then refuses
        the MCFG's eight allocations (raising the limit alone fails: segment 2 lies beyond 1<<36). If the rule changes, update D11."""
        dumps = self.acpi_dumps()
        if not dumps:
            self.skipTest('no local ACPI dump: the adapter is not run on the unit tables')
        descriptor, adapter, fixture = load_adapter()
        board = fixture.board()
        for dump in dumps:
            tables = {n: (dump / f'{n}.bin').read_bytes() for n in ('RSDP', 'XSDT', 'APIC', 'MCFG')}
            with self.assertRaises(adapter.BootContractError) as caught:
                adapter.check_hamoa_platform(board, tables)
            self.assertEqual(caught.exception.code, 'ecam-mismatch', dump.name)
        saved, description = descriptor.MAX_ECAM, fixture.synthetic()
        description['pcie']['ecam'] = [{'segment': s, 'base': f'{b:#x}', 'start_bus': f, 'end_bus': l}
                                       for b, s, f, l in self.mcfg_rows(dumps[0])]
        descriptor.MAX_ECAM = 16
        try:
            with self.assertRaises(descriptor.BoardDescriptorError) as refused:
                descriptor.validate(description)
        finally:
            descriptor.MAX_ECAM = saved
        self.assertEqual(saved, 4)
        self.assertIn('pcie.ecam2: [0x6000000000', str(refused.exception))
        self.assertIn('extends beyond 1<<36', str(refused.exception))

    def test_the_id_registers_decode_and_the_monitors_gates_pass(self):
        field = lambda value, lsb: (value >> lsb) & 15
        self.assertEqual((field(DFR0, 0), field(DFR0, 12), field(DFR0, 20), field(DFR0, 36)), (9, 5, 3, 15))
        self.assertEqual(field(DFR0, 12) + 1, 6)                                   # BRPs 5: six breakpoints
        self.assertEqual((field(ISAR0, 60), field(ISAR1, 4), field(ISAR1, 24)), (1, 5, 1))   # RNDR, APA, GPA
        # The gates the document says should pass: a changed expression must be re-examined.
        gate = 'if(version<6 || version>11 || ((dfr0>>12)&15)<5)return 0;'
        self.assertIn(gate, self.source('compat/native-writer-debug.h'))
        self.assertFalse((DFR0 & 15) < 6 or (DFR0 & 15) > 11 or ((DFR0 >> 12) & 15) < 5)
        pauth = 'if(!(isar0>>60) || !(((isar1>>4)&255)|((isar2>>12)&15)))return 0;'
        prepare = self.source('probes/xnuboot/prepare.c')
        self.assertIn(pauth, prepare)
        self.assertIn('retry<64', prepare)                                         # the RNDR reads are still untried
        mmfr1, isar2, oslsr, icc_sre = 0x111111312120, 0x10000012, 0x8, 0xF        # boots 8, 9 (el2mem)
        self.assertEqual((field(mmfr1, 20), field(mmfr1, 8), field(mmfr1, 0)), (3, 1, 0))      # PAN, VH, HAFDBS
        self.assertEqual((field(isar2, 12), field(isar2, 8)), (0, 0))              # APA3, GPA3: the gate rests on ISAR1 APA 5
        self.assertEqual(((oslsr >> 1) & 1, icc_sre & 1, (icc_sre >> 3) & 1), (0, 1, 1))       # OSLK clear; SRE, Enable set
        self.assertFalse((ISAR0 >> 60) == 0 or (((ISAR1 >> 4) & 255) | ((isar2 >> 12) & 15)) == 0)
        flat = squash(TEXT)
        for needle in ('MMFR1 0x111111312120: PAN 3, VH 1, HAFDBS 0', 'ISAR2 0x10000012', 'OSLSR_EL1 0x8 (OS lock not set)',
                       'MDCR_EL2 0x6', 'ICC_SRE_EL2 0xF at the call'):
            self.assertIn(needle, flat)


class SeededBad(unittest.TestCase):
    """Deliberately damaged copies: the checkers must say no."""

    def test_a_missing_file_a_line_beyond_the_end_and_a_missing_needle_are_refused(self):
        for tail, problem in (('`probes/does-not-exist.c:12`', 'no such file'), ('`compat/clock.c:99999`', 'out of range'),
                              ('`this text is not in that file`[compat/clock.c:5]', 'needle not found')):
            self.assertTrue([p for p in check_citations(TEXT + f'\nSee {tail}.\n', TREE)[0] if problem in p], tail)

    def test_a_needle_moved_off_its_line(self):
        match = next(m for m in QUOTE.finditer(TEXT) if not m.group(2).startswith('q1n1:'))
        first = int(match.group(3))
        moved = TEXT.replace(match.group(0), match.group(0).replace(f':{first}]', f':{first + 400}]'), 1)
        self.assertTrue(check_citations(moved, TREE)[0])

    def test_an_unknown_label(self):
        bad = TEXT.replace('| VERIFIED |\n', '| ASSUMED |\n', 1)
        self.assertNotEqual(bad, TEXT)
        self.assertTrue([p for p in check_labels(bad) if 'ASSUMED' in p])
        table = '| Item | Label |\n| --- | --- |\n| x | VERIFIED, SORT OF |\n'
        self.assertTrue(check_labels(table))

    def test_an_observed_row_without_a_source(self):
        table = '| Item | Label |\n| --- | --- |\n| it was seen | OBSERVED |\n'
        self.assertTrue([p for p in check_labels(table) if 'without a source' in p])
        self.assertEqual(check_labels(table.replace('it was seen', 'it was seen (boot 5)')), [])

    def test_a_document_over_the_size_limit_or_without_not_run(self):
        self.assertTrue([p for p in check_structure(TEXT + 'x' * MAX_BYTES) if 'limit' in p])
        self.assertTrue([p for p in check_structure(TEXT.replace('Hardware status for the port itself: NOT RUN',
                                                                 'Hardware status for the port itself: PASS'))
                         if 'NOT RUN' in p])

    def test_a_missing_section(self):
        bad = TEXT.replace('## 6. Timer', '## 6x. Timer', 1)
        self.assertTrue([p for p in check_structure(bad) if 'section' in p])

    def test_a_work_item_that_closes_a_cycle_or_depends_on_a_missing_one(self):
        row = next(line for line in TEXT.splitlines() if line.startswith('| 1 |'))
        self.assertEqual(split_row(row)[2], 'none')
        for depends, what in (('11', 'cycle'), ('40', 'missing WI'), ('D99', 'D99')):
            bad = TEXT.replace(row, row.replace('| none |', f'| {depends} |', 1), 1)
            self.assertTrue([p for p in check_plan(bad) if what in p], depends)
        self.assertGreaterEqual(len(plan_rows(TEXT)), 8)

    @staticmethod
    def damaged(old, new):
        """TEXT with the first occurrence of `old` (words in any line wrapping) replaced; the seed must still apply."""
        pattern = r'\s+'.join(re.escape(word) for word in old.split())
        bad, count = re.subn(pattern, lambda _: new, TEXT, count=1)
        assert count == 1, f'the document no longer contains the text this seeded defect replaces: {old[:50]}'
        return bad

    @staticmethod
    def seeds(block):
        """`old => new [=> needle|needle]` lines: damaged text, and what every refusal must show (default: refuted)."""
        parts = [line.split(' => ') for line in block.splitlines()]
        return [(p[0], p[1], p[2].split('|') if len(p) > 2 else ['refuted']) for p in parts]

    # Refused by check_claims (the first occurrence of the document's text is replaced by the wrong text).
    CLAIM_SEEDS = '''\
Boot 7's UPTIME 16 S was the first heartbeat after the chain => Boot 7's UPTIME 16 S was when it restarted => a 16 s figure
well under 20 s of UPTIME as a precaution => at most 16 s => refuted|20 s UPTIME budget
well under 20 s of UPTIME as a precaution => under about 30 s of UPTIME as a precaution
so, INFERRED, not at one fixed UPTIME. => so, INFERRED, the restart is at about UPTIME 40 to 60 S. => refuted|not at one fixed UPTIME
operator in the loop, one boot per sample. => one unattended plan (each self-restart writes the next marker). => refuted|work item 10
(c) a variant that leaves cores running => (a) a variant that leaves cores running => work item 10
must not assume VBAR_EL2 or HCR_EL2 are reset on a restarted core => may assume the EL2 state is reset on a restarted core => section 7 lacks
0X20200 and 0X20300 UNKNOWN => 0X20200 and 0X20300 as the others => section 7 lacks
was photographed alive at UPTIME 224 S and 268 S => was observed only to UPTIME 10 S (boot 5) => refuted|section 10 must give the restart control
| 3, 4, 5, 6, 8, 9 | the seven rows of section 2 => | 3, 4, 5, 6, 8, 9, 10 | the seven rows of section 2 => the M1 run (WI 11)
as a precaution of low weight (earliest => as a bound (earliest => precaution of low weight
U5 debug and RNDR behaviour, other cores => U5 DFR0, OS lock
(bank writability: answered, WI 7) => (bank writability UNKNOWN until the dry run)
DONE, boot 10 (2026-11-16 10:21, => BUILT and STAGED on the stick, the boot is NOT RUN
all returned at EL2 => are the hazard reads, still to be run
not through `from_hamoa`. => `from_hamoa` is no longer blocked.
`VERDICT=TRAPS_EFFECTIVE` => the ECV traps are inert
DONE (q1n1 b875620; boot 11 => BUILT, NOT RUN (q1n1 b875620; boot 11
(answered for an EL1 stub, WI 9) => (UNKNOWN)
O1, the A16 recipe => O1 is decided, the A16 recipe
the first runner payload to run code => the first code
neither confirmed nor refuted by boot 11 => wrong: `SCR_EL3.ECVEn` is set
so INFERRED E2H = 1 for the record => so E2H = 1 for the record
the GIC timer interrupt; a polling stop cannot exist once the monitor owns the CPU. => the GIC timer interrupt, or stop by polling.
`real` RAN end to end on the real kernelcache, host only, not a hardware result => `real` NOT RUN
D9 Panel and kernel: DECIDED => D9 Panel and kernel: still undecided
8 -> 4b -> 6 -> 11 => 4b -> 6 -> 8 -> 11
probably position independent (INFERRED: no relocation check exists, WI 4b adds one) => a flat PIC binary (VERIFIED)
the guest is denied the panel (`SNAP_NO_FB`, `--panel withheld`) and the kernel stays unmodified (`--sptm-hvc-calls 0`) => the guest takes the panel and the HVC patch is on (`--sptm-hvc-calls 1`)
its MMFR4 is 0 as read in X2 run 4 T01 => MMFR4 is unread
'''
    ID_SEEDS = '''\
BRPs 5 (six breakpoints) => BRPs 4 (five breakpoints) => BRPs 4|five breakpoints
RNDR 1; => RNDR 0; => RNDR 0
0x000F01F010305719 => 0x000F01F010304719 => ID_AA64DFR0_EL1
'''

    def test_the_claims_that_were_wrong_once_stay_refused(self):
        for check, block in ((check_claims, self.CLAIM_SEEDS), (check_id_registers, self.ID_SEEDS)):
            for old, new, shown in self.seeds(block):
                problems = check(self.damaged(old, new))
                for what in shown:
                    self.assertTrue([p for p in problems if what in p], (old[:40], what))

    def test_a_table_that_lost_a_newline_or_a_cell_is_refused(self):
        glued = TEXT.replace('| --- | --- | --- |\n| a |', '| --- | --- | --- || a |', 1)
        self.assertNotEqual(glued, TEXT)
        self.assertTrue([p for p in check_tables(glued) if 'separator' in p])
        row = next(line for line in TEXT.splitlines() if line.startswith('| a |'))
        short = TEXT.replace(row, row.rsplit('|', 2)[0] + '|', 1)
        self.assertTrue([p for p in check_tables(short) if 'cells' in p])

    # Each wrong state contradicts a source or a measurement: check_port_state must refuse it.
    STATE_CASES = '''\
then `check_hamoa_platform` refuses at `ecam-mismatch` => then `check_hamoa_platform` accepts them
one GICD structure of 25 bytes (ACPI: 24; the extra byte is 0) => one GICD structure of 24 bytes
then the ITS structure, no GICR-range structure => then the ITS structure and a GICR-range structure
MCFG: eight allocations, segments 0 to 7 => MCFG: four allocations, segments 0 to 3
bits that stick and trap nothing pass => bits that stick prove the traps work
`real` RAN end to end on the real kernelcache => `real` PASS
4a BUILT for the dry run, EXPERIMENTAL, RAN once on the unit => 4a DONE
4b, the monitor entry after WI 8 => 4b, the monitor entry before WI 8
`el1trap` is the first runner payload to run code below EL2 => `el1trap` is the first code below EL2
SUPPORTED by boot 11, not decided => DECIDED by boot 11
after X2 T02's EL1 stub => on the unit
and neither confirmed nor refuted by boot 11 => and confirmed by boot 11
`BITS13_14_STUCK=YES`, `RESTORED=YES` => `BITS13_14_STUCK=NO`, `RESTORED=YES`
(not met: boot 11) => (met)
and calls no monitor => and calls the monitor
then showed the bank writable => left it open whether the bank is writable
Placement A, low, is dead => Placement A, low, works
UNKNOWN: whether `DC CVAC` alone reaches DRAM (CIVAC cleans first) => `DC CVAC` alone reaches DRAM
(nothing written by the unit) => (the unit wrote the report to it)
U6 GUEST.DAT's base (answered, WI 7) => U6 GUEST.DAT's base
Not the M1 run's decision => The M1 run's decision too
is five files, the runner's whole manifest => is four files, the runner's whole manifest
`REPORT.DAT` (528,384 zero bytes => `REPORT.DAT` (524,288 zero bytes
| 4a, 7 | => | 4, 6, 7 |
| 1, 8 | => | 1 |
| 3, 4, 5, 6, 8, 9 | => | 3, 5, 8, 9 |
ran in boot 14 (OBSERVED: the quarantine held => is host-tested only (OBSERVED: the quarantine held
RAN three times on the unit (boots 12, 13 and 14 => BUILT, NOT RUN (boots 12, 13 and 14
APPROVED for the GIC dry run only, by SHA-256, three times => OPEN, asked by SHA-256 with the dry-run record
U3 answered by boot 14 => U3 open
PPI 26's own delivery was OBSERVED in boot 14 => PPI 26's own delivery is UNKNOWN
D2 Stop: DECIDED 2026-11-16 with D5 => D2 Stop: OPEN with D5
N2 Descriptor policy (new): DECIDED => N2 Descriptor policy (new): proposed
N1 Boot seed: DECIDED 2026-11-17 => N1 Boot seed: proposed
D6 NV2 record: DECIDED 2026-11-17 => D6 NV2 record: OPEN
NV2 at E2H = 0 is OBSERVED in X2 runs 6 and 7 => NV2 at E2H = 0 is OBSERVED in X2 run 4
D8 Thermal: DECIDED 2026-11-17 => D8 Thermal: OPEN
as an SP11-only opt-in: DECIDED 2026-11-17 => as an SP11-only opt-in: OPEN
D11 proper stays OPEN => D11 proper is settled
U9 kernelcache (answered, WI 3) => U9 kernelcache
BUILT, EXPERIMENTAL, RAN eight times on the unit (boots 20, 21, 23, 24, 25, 26, 27 and 28, OBSERVED). => BUILT, EXPERIMENTAL, hardware PASS (boots 20, 21, 23, 24, 25, 26, 27 and 28, VERIFIED).
holds the FIRST 1,024 bytes of guest output and counts the rest => is a ring of the LAST 1,024 bytes of guest output
extension records from +0x11000 => extension records from +0x10000
the boot CPU writes only CNTHCTL_EL2 => the boot CPU writes TCR, CNTHCTL and CPTR
head is q1n1's payload stub => head is the monitor's own
the gate passed with it on the unit in boot 20 (`STATUS=0X0`, OBSERVED, one boot; => the gate refused it on the unit in boot 20 (`STATUS=-91`, OBSERVED, one boot;
for the monitor entry, rung 1 only, DECIDED 2026-11-18 01:18 => for the monitor entry OPEN, asked 2026-11-18 01:18
except eight runs of the monitor entry (boots 20, 21, 23, 24, 25, 26, 27 and 28, M1 rungs 1 to 7 and 9, 2026-11-18 to 20; rung 8 was skipped and never run). => except none (boots 20, 21, 23, 24, 25, 26, 27 and 28, M1 rungs 1 to 7 and 9, 2026-11-18 to 20).
The guest was, INFERRED, stuck in an early loop of the kernel's I/O Kit start-up => The guest was, VERIFIED, stuck in an early loop of the kernel's I/O Kit start-up
the guest was **stuck, not slow** => the guest was **slow, not stuck**
it does not settle that the kernel boots => it settles that the kernel boots
a longer box on the withheld image only repeats it => a longer box on the withheld image reaches the next stop
the next run hands the panel to the guest (rung 2) or adds `-noprogress` (hand-off bytes only: a new `GUEST.DAT` digest), a new decision and record => the next run repeats the same image with a longer box, a new decision and record
most likely XNU's first CPU-start write to the PMGR block, which counts as success for M1 => most likely a panic with no CPU-start write, which counts as failure for M1
`x10` not 0 or `x21` not 1 at the stop => `x10` not 0 at the stop
so the display size was 0 (VERIFIED, host: the staged file; the register itself was never read) => so the display size was 0 (VERIFIED, host: the register was read)
the monitor's own stop path: WATCHDOG stop OBSERVED once, boot 20, WI 11 => the monitor's own stop path: NOT RUN
U7 guest in the bank (the guest ran from the bank in boots 20 and 21: OBSERVED, one boot each) => U7 guest in the bank (the guest ran from the bank in boots 20 and 21: VERIFIED)
was RELAXED for rung 1 by the approval of the M1 record => was kept for rung 1 despite the approval of the M1 record
Boot 20 (2026-11-18): the gate passed with this record on the unit => Boot 20 (2026-11-18): the gate refused this record on the unit
which is absent on the machine used (NOT RUN) => which is present on the machine used (PASS)
the NV2 gate passed with the embedded first-image record (D6; boot 20, OBSERVED, one boot; the record's regime is not recorded) => the NV2 gate passed with the embedded first-image record (D6; boot 20, OBSERVED, one boot; the record's regime is recorded)
the monitor entry (WI 4b) is built on them and ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28 (OBSERVED, one boot each) => the monitor entry (WI 4b) is a plan
U10 stage-2 syndromes (boot 20: no stage-2 abort, so none seen) => U10 stage-2 syndromes (boot 20: a stage-2 abort was seen)
ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28, and were put back => never ran, and were put back
rung 1 RAN once on the unit (boot 20, 2026-11-18, plan `M1RUN-RUNG1`; OBSERVED => rung 1 PASSED on the unit (boot 20, 2026-11-18, plan `M1RUN-RUNG1`; VERIFIED
(VERIFIED, host; says nothing about hardware) => (OBSERVED, host; says nothing about hardware)
a, c, d, e and f held; b was not exercised => a, c, d, e and f held; b was exercised and refused
D4 decided for rung 1 (2026-11-18) => D4 still open (2026-11-18)
its code was inspected for the stop site only, by structure => its code was inspected in full
the monitor's own stop path ran once, in boot 20: WI 11 => the monitor's own stop path is NOT RUN: WI 11
`SNAP_SP11_STICK_MONITOR`, default 0 => `SNAP_SP11_STICK_MONITOR`, default 1
Boot 21 handed the guest the panel as its console (rung 2, WI 11) => Boot 21 withheld the panel from the guest (rung 2, WI 11)
that the write is the CPU-start write is INFERRED, by address) => that the write is the CPU-start write is VERIFIED)
**Boot 21, rung 2 (2026-11-18, plan `M1RUN-CONSOLE-RUNG2`; OBSERVED, one unit, one boot).** => **Boot 21, rung 2 (2026-11-18, plan `M1RUN-CONSOLE-RUNG2`; VERIFIED, one unit, one boot).**
so the page has no `MON CON` line => so the page has six `MON CON` lines
boot 21's, `MON STOP S2_FAULT`, ended it earlier, at a stage-2 fault (OBSERVED, one boot) => boot 21's, `MON STOP S2_FAULT`, ended it at the box (OBSERVED, one boot)
and again in boot 21 (OBSERVED, one boot) => and not again in boot 21 (OBSERVED, one boot)
it passed again in boot 21 (`STATUS=0X0`, OBSERVED, one boot), the regime still not recorded => it refused in boot 21 (`STATUS=-91`, OBSERVED, one boot), the regime still not recorded
(which one: IPA `0x300734004` in boot 21, a 32-bit write, OBSERVED, one boot => (which one: IPA `0x300734000` in boot 21, a 32-bit write, OBSERVED, one boot
Rung 2 (boot 21) changed the panel choice by the operator's approval => Rung 2 (boot 21) kept the panel choice despite the operator's approval
and boot 21 ended at a stage-2 fault before the box, boot 23 at a guest exception, boot 24 at a stage-2 fault) => and boot 21 ended at the watchdog at the box, boot 23 at a guest exception, boot 24 at the watchdog at the box)
that was its timer stop, and boot 21 ended at a stage-2 fault before the box => that was its timer stop, and boot 21 ended at its timer too
in boot 21 one stage-2 fault was seen, the stop (a write and => in boot 21 no stage-2 fault was seen, the stop (a write and
so what `HPFAR_EL2` holds on Oryon v1 stays UNKNOWN => so what `HPFAR_EL2` holds on Oryon v1 is VERIFIED
rung 2 RAN once on the unit (boot 21, 2026-11-18, plan `M1RUN-CONSOLE-RUNG2`; OBSERVED => rung 2 PASSED on the unit (boot 21, 2026-11-18, plan `M1RUN-CONSOLE-RUNG2`; VERIFIED
the byte comparison not repeated: NOT RUN) => the byte comparison repeated: PASS)
the stop was `MON STOP S2_FAULT` with `PC=0XFFFFFE00097A352C` and `A=0X300734004` => the stop was `MON STOP WATCHDOG` with `PC=0XFFFFFE00097A352C` and `A=0X300734004`
the match of the address with XNU's first CPU-start write is INFERRED) => the match of the address with XNU's first CPU-start write is OBSERVED)
(its INFERRED cause chain is consistent with the run, not proven) => (its cause chain is proven)
1 to 7 and 9: eight done (boot 20, rung 1; boot 21, rung 2; boot 23, rung 3; boot 24, rung 4; boot 25, rung 5; boot 26, rung 6; boot 27, rung 7; boot 28, rung 9; rung 8 was skipped) => 1 to 7: three done
the stop of boot 21, seen => the stop of boot 21, not seen
D4 decided for rung 1 (2026-11-18) and for rung 2 => D4 decided for rung 1 (2026-11-18) and still open for rung 2
D9's panel-handed choice decided by the rung 2 approval => D9's panel-handed choice still undecided
alive at UPTIME 36 S on the closer view at its end => restarted at UPTIME 36 S on the closer view at its end
the page had no `MON CON` line (OBSERVED, one boot; => the page had six `MON CON` lines (OBSERVED, one boot;
rung 2: RAM and the panel's range, which the guest writes => rung 2: RAM only
The path was walked eight times, for rungs 1 to 7 and 9 (boots 20, 21, 23, 24, 25, 26, 27 and 28) => The path was walked once, for rung 1 (boot 20)
Reached in boot 21 (OBSERVED once) => Not reached in boot 21 (OBSERVED once)
Not seen in boot 21 either (`ABORTS=0`) => Seen in boot 21 (`ABORTS=1`)
and the stop was the monitor's own, not a guest panic => and the stop was a guest panic
so the timer did not end it (OBSERVED, one boot) => so the timer ended it (OBSERVED, one boot)
Boot 21 ran under it too: one CPU for a call of 2.04 s => Boot 21 ran under it too: one CPU for a call of 5.04 s
Rung 2, DECIDED 2026-11-18 about 09:46 => Rung 2, OPEN, asked 2026-11-18 about 09:46
The panel-handed choice was then DECIDED for rung 2 => The panel-handed choice was then PROPOSED for rung 2
and no `-noprogress`, so that the run tests the analysis of boot 20 => and `-noprogress` added, so that the run fixes boot 20
What boot 21 showed (rung 2; OBSERVED, one unit, one boot; => What boot 21 showed (rung 2; VERIFIED, one unit, one boot;
`A=0X300734004`, `B=0X93970045` and `C=0XFFFFFE60A0178004`, 39,245,286 ticks for the whole call (2.04 s, inside the box of 5 s) => `A=0X300734004`, `B=0X93970045` and `C=0XFFFFFE60A0178004`, 39,245,286 ticks for the whole call (5.04 s, past the box of 5 s)
`TRAPS=136424` and `CALLS=65955` (cumulative counts, not rates) => `TRAPS=136424` and `CALLS=65955` (rates)
(the same stop PC again) did not occur => (the same stop PC again) occurred
and stays INFERRED. The stop decodes => and is VERIFIED. The stop decodes
as a 32-bit write from EL1 with a translation fault at level 1 => as a 64-bit read from EL1 with a permission fault at level 3
`A=` is exactly the first register of the CPU-start window => `A=` is not in the CPU-start window
that the guest's write is that write is INFERRED, by address => that the guest's write is that write is VERIFIED, by address
the value written (`x23`) and the writing function were not printed => the value written (`x23`) and the writing function were printed
that CPU bring-up works (it was not reached), SMP => that CPU bring-up works (it was reached), SMP
why 925 single-steps were counted (UNKNOWN) => why 925 single-steps were counted (VERIFIED)
which one is UNKNOWN); a monitor that accepts => which one is VERIFIED); a monitor that accepts
(then a `cpu N failed to boot` panic about a second later: INFERRED from the public source) => (then the kernel boots on one CPU: INFERRED from the public source)
real secondaries last (the wrapper cannot build the secondary configuration: UNIMPLEMENTED => real secondaries first (the wrapper builds the secondary configuration: IMPLEMENTED
Each needs a record of its own and the operator's decision. => Each needs no record.
What the kernel does under the monitor past its first CPU-start write: UNKNOWN => What the kernel does under the monitor past its first CPU-start write: OBSERVED
beyond the three that ended boots 21, 24 and 26 (a write, a read and a write, all translation faults at level 1: INFERRED decodes; boot 28's stop repeats boot 24's): none has run => beyond the three that ended boots 21, 24 and 26 (a write, a read and a write, all translation faults at level 1: INFERRED decodes; boot 28's stop repeats boot 24's): all have run
MON STOP GUEST_EXC`), INFERRED (high confidence) to be the kernel's own debugger trap, the cause UNKNOWN => MON STOP GUEST_EXC`), VERIFIED to be the kernel's own debugger trap, the cause known
rung 3's own verdict is NOT PASSED (c) by the letter of its record => rung 3's own verdict is PASSED by the letter of its record
M1 PASSED by its own rows (the stop kinds of row d include `GUEST_EXC`) => M1 FAILED by its own rows (the stop kinds of row d include `GUEST_EXC`)
Rows a, c, d, e and f held (f by the host check of the stick => Rows a, c, d and e held (f not read back
`REPORT.DAT` not written back, so the page is the only record of the run => `REPORT.DAT` written back, so the stick holds the record of the run
the 18 staged files unchanged and => the 17 staged files unchanged and
Rung 3 (boot 23): rows a, c, d, e and f held (b not exercised => Rung 3 (boot 23): rows a, c, d and e held (b not exercised; f NOT RUN
the cause is UNKNOWN (`docs/SP11-STICK-LOG.md` section 21 has the page => the cause is known (`docs/SP11-STICK-LOG.md` section 21 has the page
ended it earlier too, at an exception that the guest took at EL1 (OBSERVED, one boot) => ended it at the box too (OBSERVED, one boot)
and again in boot 23 (`STATUS=0X0`, OBSERVED, one boot) => and not in boot 23 (`STATUS=0X0`, OBSERVED, one boot)
boot 21 ended at a stage-2 fault before the box; boot 23 ended at a guest exception => boot 21 ended at the watchdog at the box; boot 23 ended at a guest exception
boot 23 ended at a guest exception (`MON STOP GUEST_EXC`) before the box => boot 23 ended at the watchdog at the box
in boot 23 `ABORTS=0` again and the stop was a guest exception, not a stage-2 fault => in boot 23 `ABORTS=1` and the stop was a stage-2 fault
Rung 3, DECIDED 2026-11-18 about 15:05 => Rung 3, OPEN, asked 2026-11-18 about 15:05
Rung 3 kept the choice by its own approval => Rung 3 kept the choice despite its own approval
that the exception is the guest kernel's own debugger trap, a public constant, and not a missing CPU feature => that the exception is a missing CPU feature
so that hiding a feature in the ID registers does not apply and emulating an instruction in the monitor is the wrong tool => so that hiding a feature in the ID registers applies and emulating an instruction in the monitor is the right tool
let the guest's own panic handler run through the monitor's existing `let_panic` diagnostic, a data word => let the monitor skip the instruction through its `skip_undef` diagnostic, a data word
whether the panic path survives on this stack is UNKNOWN => whether the panic path survives on this stack is VERIFIED
Not shown: that any driver started, CPU bring-up, SMP, a root device, stability, performance, or what the cause is. => Shown: that every driver started, CPU bring-up, SMP, a root device, stability and performance.
22 = plan nvmesnap-s1, the read-only storage survey, no monitor stage => 22 = plan nvmesnap-s1, a monitor stage
Boot 23 ran under it too: one CPU for a call of 2.17 s => Boot 23 ran under it too: one CPU for a call of 5.04 s
boot 23 ran them for 2.17 s, with no restart in the 7 s after the report page => boot 23 ran them for 2.17 s, restarted in the 7 s after the report page
rungs 1 to 7 and 9 (boots 20, 21, 23, 24, 25, 26, 27 and 28); a further run needs a record of its own => rungs 1 to 7 and 9 (boots 20, 21, 23, 24, 25, 26, 27 and 28); a further run needs no record
the one new input is the monitor's `let_panic` PARAMS word, set to 1 => the one new input is the monitor's `let_panic` PARAMS word, set to 0
and `REPORT.DAT` not written back), row b was not exercised and row g held for the pair that ran => and `REPORT.DAT` written back), row b was not exercised and row g held for the pair that ran
printed a panic text on the panel, an ApplePMGR assertion (`enableCPUComplex(UInt32, bool)`, `REQUIRE failed: complex < _cpuComplexCount`) => printed a panic text on the panel, a GIC assertion (`enableCPUComplex(UInt32, bool)`, `REQUIRE failed: complex < _cpuComplexCount`)
then nested panics up to `Nested panic count exceeds limit 5, machine will reset or spin` and `wdog: panic chipreset` => then nested panics up to `Nested panic count exceeds limit 9, machine will reset or spin` and `wdog: panic chipreset`
the monitor's console ring carried the first lines of the text onto the report page (six `MON CON` lines; boots 21 and 23 had none) => the monitor's console ring carried none of the text onto the report page (no `MON CON` line; boots 21 and 23 had none)
as `MON STOP S2_FAULT` with `A=0X3082BC224` (INFERRED: a 32-bit read of an address with no stage-2 mapping, in the kernel's chip-reset path) => as `MON STOP S2_FAULT` with `A=0X3082BC224` (VERIFIED: a 32-bit write to an address with a stage-2 mapping, in the kernel's chip-reset path)
rung 4's class is PANIC TEXT (the record's first result that fits: a result, neither a pass nor a failure) => rung 4's class is NOT PASSED (a) (the record's first result that fits: a failure)
Why the assertion failed and the rest of 22.6's questions are UNKNOWN => Why the assertion failed and the rest of 22.6's questions are known
43,133,334 ticks for the whole call (2.2465 s, inside the box of 5 s) => 43,133,334 ticks for the whole call (2.2465 s, past the box of 5 s)
INFERRED: the panic path writes its text through the serial-putc call that the monitor logs) => VERIFIED: the panic path writes its text through the serial-putc call that the monitor logs)
a hypothesis, not verified: `_cpuComplexCount` counts the complexes of the CPUs that stay enabled => a verified fact: `_cpuComplexCount` counts the complexes of the CPUs that stay enabled
the assertion is new with `cpus=1`, since boot 21 passed the driver with six CPUs => the assertion is old without `cpus=1`, since boot 21 failed the driver with six CPUs
did not stop the run (no `MON STOP UNNAMED`; INFERRED: no refused call was made) => stopped the run (`MON STOP UNNAMED`; OBSERVED: a refused call was made)
or that macOS boots; there is no second boot. => and macOS boots; there is a second boot.
and the ordinary log does not). => and the ordinary log does too).
Rung 4, DECIDED 2026-11-18 about 21:31 => Rung 4, OPEN, asked 2026-11-18 about 21:31
Rung 4 kept it again by its own approval => Rung 4 kept it again despite its own approval
Boot 24 ran under it too: one CPU for a call of 2.25 s => Boot 24 ran under it too: one CPU for a call of 5.04 s
boots 23 and 24 (`cpus=1`) did not make that write => boots 23 and 24 (`cpus=1`) made that write
ended it at 2.2465 s, at a stage-2 fault (OBSERVED, one boot). => ended it at the box, at the watchdog (OBSERVED, one boot).
an ApplePMGR assertion in `enableCPUComplex` (`complex < _cpuComplexCount`) => a CLPC assertion in `enableCPUComplex` (`complex < _cpuComplexCount`)
Boot 23 ended at a guest exception and boot 24 at a stage-2 fault, both before the box (OBSERVED, one boot each); boot 25's stop timer fired => Boot 23 ended at a guest exception and boot 24 at the watchdog, both at the box (OBSERVED, one boot each); boot 25's stop timer fired
Reached again in boot 24 (OBSERVED once), after the kernel's own panic text: => Not reached in boot 24, before the kernel's own panic text:
the heartbeat read up to UPTIME 33 S => the heartbeat stopped at UPTIME 14 S, with a restart
the rung's class is PANIC TEXT, M1's PASSED. => the rung's class is NOT PASSED (a), M1's PASSED.
boot 24: `ABORTS=0` again and its stop was a stage-2 data fault again, a read => boot 24: `ABORTS=0` again and its stop was a guest exception, not a stage-2 fault
in boot 24 (`serial=2`) the page had six, the first lines of the kernel's panic text => in boot 24 (`serial=2`) the page had none
rung 5's class is WATCHDOG, NO TEXT, with PROGRESS (section 14; => rung 5's class is PANIC TEXT (section 14;
`MON STOP WATCHDOG` with `PC=0XFFFFFE000B8716A8` after 1,155,520,917 ticks => `MON STOP S2_FAULT` with `PC=0XFFFFFE000B8716A8` after 1,155,520,917 ticks
INFERRED: CPU 0 in its idle thread; => VERIFIED: CPU 0 in its idle thread;
whether any was delivered is UNKNOWN => whether any was delivered is VERIFIED
Rung 5, DECIDED 2026-11-19 about 05:50 => Rung 5, OPEN, asked 2026-11-19 about 05:50
Boot 25 used it: 60.18 s. => Boot 25 used it: 5.04 s.
Boot 25 ran under it: one CPU for a call of 60.18 s => Boot 25 ran under it: one CPU for a call of 5.04 s
Boot 26, rung 6 (2026-11-19, plan `M1RUN-ACKPANIC-RUNG6`; OBSERVED => Boot 26, rung 6 (2026-11-19, plan `M1RUN-ACKPANIC-RUNG5`; OBSERVED
rung 6's class is NEW STOP (section 14; => rung 6's class is TIMER PANIC (section 14;
`A=0X291000004`, `B=0X93880045` and `C=0XFFFFFE63AA8A0004` after 1,182,753,011 ticks => `A=0X3082BC224`, `B=0X93880045` and `C=0XFFFFFE63AA8A0004` after 1,182,753,011 ticks
the delivery is INFERRED from them, since no other CPU runs => the delivery is OBSERVED, since no other CPU runs
Rung 6, DECIDED 2026-11-19 (by the SHA-256 => Rung 6, OPEN, asked 2026-11-19 (by the SHA-256
Boot 26 ran with the box of 120 s of D5'': one CPU for a call of 61.60 s. => Boot 26 ran with the box of 120 s of D5'': one CPU for a call of 5.04 s.
`MON STOP S2_FAULT` with `A=0X291000004` at 61.60 s => `MON STOP WATCHDOG` with `A=0X291000004` at 61.60 s
Reached in boot 26 (OBSERVED once), in the kernel's normal start-up => Not reached in boot 26, in the kernel's normal start-up
the kernel waits for a root device (INFERRED; the guest has no storage) => the kernel waits for a root device (OBSERVED; the guest has no storage)
Rung 7, DECIDED (by the SHA-256 of its own record; => Rung 7, OPEN (by the SHA-256 of its own record;
Boot 27 ran with it too: one CPU for a call of 120.185 s => Boot 27 ran with it too: one CPU for a call of 5.04 s
only the two timeout messages cannot => every RTC message cannot
rung 7's class is RUNG 7 KERNEL WAITING (section 14; => rung 7's class is NEW STOP (section 14;
`A=0X96000047`, `B=0XFFFFFE110E00C040` => `A=0X9600004F`, `B=0XFFFFFE110E00C040`
after 2,307,553,890 ticks (120.185 s, the box's end) => after 2,307,553,890 ticks (12.185 s, the box's end)
Not reached in boot 27: with the scaler withdrawn by data => Reached in boot 27: with the scaler withdrawn by data
and g held on the host (VERIFIED, `git diff`: the monitor sources of rungs 6 and 7 are identical => and g is a host check that the reading of the film does not report (the monitor sources of rungs 6 and 7 are identical
'''

    def test_a_wrong_state_of_the_tree_is_refused(self):
        for old, new, _ in self.seeds(self.STATE_CASES):
            self.assertTrue(check_port_state(self.damaged(old, new)), old)

    def test_the_bundle_in_the_wrong_order_is_refused(self):
        swapped = self.damaged('`SNAPMON.BIN` (payload 0', '`REPORT.DAT` (payload 0')
        self.assertTrue([p for p in check_port_state(swapped) if 'interface' in p])

    def test_a_dropped_ecam_decision_is_refused(self):
        dropped = self.damaged('- D11 The ECAM rule', '- D12 The ECAM rule')
        self.assertTrue([p for p in check_plan(dropped) if 'D11' in p])
        self.assertTrue([p for p in check_port_state(dropped) if 'D11' in p])


if __name__ == '__main__':
    unittest.main(verbosity=1)
