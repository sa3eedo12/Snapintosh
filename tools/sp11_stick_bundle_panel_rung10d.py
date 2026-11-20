# SPDX-License-Identifier: MIT
"""Section 5d of the record of rung 10 (docs/SP11-STICK-ROOT.md section 11), in the operator's words: the aim, what is new, the carry as boot 28 showed it, what to expect, what the
record predicts and what falsifies it, what to film, what the film and the page decide, what a panic looks like and what counts as a result. Rung 10 is rung 9's form with the ISC (tools/sp11_stick_bundle_rung10.py):
boot 28 ran rung 9's record and the stock kernel mounted the root, ran launchd and launchd's first boot task, mount-phase-1, failed (tools/sp11_stick_bundle_panel_rung10.py, the keyed block of section 0a). The
host reading of the BaseSystem image (files/boottasks-note.md of the session: a file read on the host, INFERRED throughout) says that the task's mandatory step is the xART volume of the ISC, which it finds by the GPT
partition type, so the one change of this rung is the RAM disk: a GPT medium of the root and the ISC.

The aim is ONE question: does mount-phase-1 pass with an ISC in the RAM disk, and where is the next stop? The results are read in order and a film is in the first class that fits (CARRY STOPPED, THE ISC DID NOT
TAKE, NEW USERSPACE STOP, PANIC TEXT, NOT A RESULT, NOT PASSED (a), WD1, THE ROOT DID NOT MOUNT, NEW STOP, LAUNCHD RUNNING, SILENT WAIT, NOT PASSED (b)): the classes are exclusive and leave no film without one.
Every time-dependent sentence takes the box from the record (`box_s`, and `wait_s`, the silence rule's wait for it).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung7d as d7  # noqa: E402
import sp11_stick_bundle_panel_rung8 as r8  # noqa: E402
import sp11_stick_bundle_panel_rung8d as d8  # noqa: E402
import sp11_stick_bundle_panel_rung9 as r9  # noqa: E402
import sp11_stick_bundle_panel_rung9d as d9  # noqa: E402
import sp11_stick_bundle_rung10 as r10b  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

BOOT28_COPY_TICKS, HZ = 501_263_277, 19_200_000         # the page of boot 28: TICKS HZ=19200000 PLACED=40419 COPIED=501263277 (26.1 s), counted from the wrapper's entry: placement + digests of the file + copy, no verify (probes/sp11-stick-wrapper.c:370, 417-419)
PLACED9 = sum(size for _, _, size, _ in r10b.CHUNKS9) + r10b.ROOT_BYTES      # what the 339 chunks of rung 9's table place, zero chunks included: 2,036,053,472 bytes
COPY9_S = BOOT28_COPY_TICKS / HZ
RATE9_MIB = PLACED9 / COPY9_S / (1 << 20)                # 74.4 MiB/s: a floor for the copy's own rate, because COPY9_S holds the digests too
READ_S, READ_MIB_S, HASH_S, HASH_MIB_S = 46, 40, 8, 200  # boot 28's films: the READ lines, MIB=64 at about 3 s to the last at about 48 s; the HASH lines from about 49 s
GIC_ARM_S = 8
LINE_WAIT_S, CARRY_WAIT_MIN, CALL_WAIT_S = 120, 4, 180   # the operator's waits (INFERRED margins, not measurements): after the first READ line, before PAYLOAD CALL 0, and after it
PROGRESS_MIB = r9.PROGRESS_MIB
CLOCK_ADDRESSES = d8.CLOCK_ADDRESSES
CARRY_REFUSALS = d9.CARRY_REFUSALS
NOT_THE_FAILURE = (d9.NOT_THE_FAILURE + ', and the lines about the roles 10 and c0 on the root device ("failed to find volume with role = 10", "No volume with role 16 present", "Update volume mount '
                   'failed ... Ignoring": boot 28, OBSERVED once; those volumes are optional and are INFERRED to stay absent from the root container)')


def extra_s(diff):
    """The seconds that the copy of the medium's extra bytes takes at the floor rate of boot 28 (RATE9_MIB): the medium's growth over the root alone (zero chunks are stored like data: probes/sp11-stick-load.c copy_chunk)."""
    return (diff['medium']['bytes'] - r10b.ROOT_BYTES) / (RATE9_MIB * (1 << 20))


def dc_s(diff):
    """The wrapper's digests and copy for this guest, an INFERRED upper figure: boot 28's 26.1 s (both, counted from the entry) plus the extra bytes at the floor rate, and a second of rounding."""
    return round(COPY9_S + extra_s(diff)) + 1


def wrapper_s(diff):
    """The wrapper's digests, copy and verify for this guest, an INFERRED upper figure for the film's length: the digests and the copy, and a verify as long again."""
    return 2 * dc_s(diff)


def film_min(diff, box_s):
    return -(-(READ_S + HASH_S + wrapper_s(diff) + GIC_ARM_S + 90 + box_s + r5.FILM_AFTER_S) // 60)


def heading(box_s):
    return f'5d. Rung 10: the aim, what is new, the carry, what to film, what counts as a result (root and ISC: wait of {r9.CPU_WAIT_S} s, box of {box_s} s)'


def page_legend(m1, box_s, real=None):
    """The lines that the guide's section 5b gets after MON EXIT: rung 7's legend of the reporter's window lines, then the runner's notes of its big-file mode and the lines of the carry, as boot 28 showed them."""
    extra = (
        '- PAYLOAD BIGFILE CHUNK=<bytes> MAXOPS=<n> MAXFILE=<bytes> TOP=<address> (the runner of the big-file mode, on the page after the files\' lines): the unit of one flush operation that the sweep will use '
        '(0X800000: 8 MiB, which ran in boot 28: OBSERVED once), the most operations that any declared range needs (the film is read against it), the file limit (0X80000000) and the highest address that a file may '
        'end at (0XFFFFFFFFF); and PAYLOAD READ INDEX=<n> MIB=<m> OF=<total MiB>, PAYLOAD HASH INDEX=<n> MIB=<m>: one line for each 64 MiB read or hashed, on the firmware\'s text console (the one that shows the Shell\'s lines), '
        'which showed them on the panel in boot 28, scrolling (OBSERVED once), before the page and not replayed on it.',
        '- SNAPMON STAGE=GUEST_OK CHUNKS=<n> POLICY=<p> TICKS=<t>, then STAGE=COPYING and STAGE=COPIED COPIED=<n> BYTES=<b> HASHED=<n> HASHED_BYTES=<b>: the wrapper\'s digests of the file, and its copy and verify; '
        'the page is silent between the lines, and their times are in section 5d, THE CARRY.')
    return (*d7.page_legend(m1, box_s, real), *(line for text in extra for line in m1t.wrap(text, 3, 5)))


def new_text(panel):
    """WHAT IS NEW: rung 9's form with the GPT medium, the numbers of the builder's comparison with rung 9's own guest."""
    d = panel['rung10']['diff']
    m, i = d['medium'], d['isc']
    return (f'WHAT IS NEW beyond rung 9 (record {r10b.RUNG9_RECORD[:8]}...{r10b.RUNG9_RECORD[-3:]}, boot 28: section 0a). The monitor (053a239f..., inside SNAPMON-M1-ACK.BIN), the root-carry pair, the runner (Q1N1-PAYLOAD.EFI af76e0bf...), the '
            f'reporter\'s lines, the panel, serial=2, the three store classes, let_panic, the acknowledge, the PMU\'s clock, the device tree ({d["nodes"][1]} of {d["nodes"][0]} nodes), the command line, the 6,144 MiB of RAM and the box '
            f'of {panel["run"]["box_s"]} s are rung 9\'s, byte for byte in GUEST.DAT but for the words that section 3c lists. WHAT CHANGES is data in GUEST.DAT alone: (1) THE RAM DISK IS A GPT MEDIUM OF TWO PARTITIONS, {m["bytes"]:,} bytes '
            f'where rung 9\'s was the root alone, {r10b.ROOT_BYTES:,}: the root (Apple\'s BaseSystem, byte for byte rung 9\'s RAM disk, now {d["root"]["at"] >> 20} MiB into the medium) and the ISC ({i["bytes"]:,} bytes, a three-role APFS '
            'container of three EMPTY volumes, the roles xART, Preboot and Hardware, as an ISC has them, of which the host reading says only xART is mandatory for launchd\'s first boot task: THE AIM). (2) The device tree\'s /chosen names the whole medium: boot-ramdmg-size and '
            f'boot-ramdmg-extents, two properties and no other (the builder reads the tree back by parsing it), and the loader\'s free-memory start moves from 0X{r10b.RUNG9_END:X} to 0X{d["avail"][1]:X}, the end of the medium, in the three '
            f'places that hold it (twice in HANDOFF and in PARAMS word 46). (3) GUEST.DAT is {d["guest_bytes"][1]:,} bytes in {d["count"]} chunks (rung 9\'s: {d["guest_bytes"][0]:,} in {r10b.RUNG9_CHUNK_COUNT}): the ISC is '
            f'almost all zeros, and its {i["bytes"]:,} bytes cost {d["guest_bytes"][1] - d["guest_bytes"][0]:,} bytes of file. NOTHING ELSE changes: no register, no system-register class, no store class, no host link, no write to the stick.')


def aim_text(panel):
    """THE AIM: the one question, what boot 28 showed, what the host reading of the image says and the stages that the classes follow."""
    return ('THE AIM. One question: does launchd\'s first boot task, mount-phase-1, pass when the RAM disk also holds an ISC, and where is the next stop? Boot 28 (rung 9) answered the question of rung 9: the stock kernel mounted the '
            'root, ran /sbin/launchd, and launchd\'s first boot task failed (section 0a, OBSERVED once). The host reading of the BaseSystem image (files/boottasks-note.md of the session: a file read on the host, not a run; INFERRED '
            'throughout) says why. The task runs apfs_boot_util, whose mandatory step is the xART volume (role 0x100) of a SECOND APFS container, the ISC, which the tool finds by the GPT PARTITION TYPE '
            '69646961-6700-11AA-AA11-00306543ECAC and not by a UUID or by the volume group: a RAM disk with no partition map cannot give it, so the lookup times out ten times, in rounds of 3 s (the ten `Retrying` lines of boot 28), and the '
            'task fails. Preboot (role 0x10) and Update (0xC0) of the root\'s own container, and FactoryLogs, iSCPreboot and Hardware, are optional (boot 28 printed "No volume with role 16 present" and "Ignoring" for the first '
            'two). THIS RECORD GIVES THE LOOKUP WHAT IT ASKS FOR and changes nothing else. The stages, in the order the film meets them (INFERRED; nothing of this has run with an ISC): '
            '(0) THE CARRY, before the guest, as boot 28 showed it (below). (1) THE START AND THE MOUNT as in boot 28: the noise of the missing models, the clock\'s lines (the clock took: OBSERVED once), the bsd_init block, the mount of the '
            'root and `load_init_program: attempting to load /sbin/launchd`; the root is now a partition of a GPT medium, which boot 28 did not have (UNKNOWN whether the kernel finds it as it found the raw image; the A16 route\'s '
            'RAM-disk root was this medium). (2) THE ISC: apfs_boot_util finds the container by the type GUID and mounts the xART volume at /System/Volumes/xarts. THE FILM\'S TEST THAT THE ISC DID ITS JOB is that the ten rounds of '
            '`wait_for_node:266: APFS IOKit node lookup timed out` / `Retrying (N retries left)` do NOT come; the lines about the roles 10 and c0 on the root device are expected to be as in boot 28 (the volumes are optional and not on '
            'that container). EMPTY volumes (no xART records, no Preboot directories) are INFERRED to be enough, because the task finds a volume by its role and mounts it and the code reads nothing from it; UNKNOWN whether the '
            'kernel\'s APFS accepts the xART role, which the image has by a byte stamp because the macOS formatter refused to make it (OBSERVED on the host): only a boot answers. (3) THE BOOT TASKS AFTER IT, in launchd\'s order '
            '(INFERRED): data-protection (seputil) should pass without a word of its own because sepfw-load-at-boot is 0 ("Skipping SEP firmware load" or "No SEP present"); restore-datapartition is the likeliest first real '
            'stop, a script run with `set -ex` that prints each command after a `+` to the console (tmpfs mounts, a copy of the data template, then prebootensurer); fips (cc_fips_test) is the first heavy EL0 compute, '
            'and a failure there is `boot task failure: fips`. (4) BEYOND THEM: `Early boot complete`, then the daemons; WindowServer waits about 30 s for a Metal device, aborts and is started again, as on the A16 (a document, '
            'not a run here); no panic is expected after it, and a box of 300 s should end in MON STOP WATCHDOG with launchd\'s text on the panel. The film names the stage that the guest reached and the last line it printed there.')


def carry_text(panel):
    """THE CARRY: what boot 28 showed of it (OBSERVED once), what this record's medium changes (INFERRED from boot 28's rate), what to wait for and what each refusal would be."""
    d = panel['rung10']['diff']
    lines = panel['rung10']['guest_bytes'] // (PROGRESS_MIB << 20)
    of = -(-panel['rung10']['guest_bytes'] // (1 << 20))
    extra = extra_s(d)
    placed = PLACED9 + d['medium']['bytes'] - r10b.ROOT_BYTES
    return ('THE CARRY (boot 28 ran it once, for rung 9\'s file of 1,926,621,440 bytes; the lines and their order are read on the host in the runner\'s source, q1n1 9ea1bdc, a file and not a run). WHAT BOOT 28 SHOWED '
            '(OBSERVED once; two films of the panel, one stick; the middle of the run, from the wrapper\'s end to launchd, is on neither): (1) THE READ, about ' + f'{READ_S} s at about {READ_MIB_S} MiB/s from the stick: '
            f'`PAYLOAD READ INDEX=2 MIB=64 OF=1838` and one such line for each further 64 MiB, ON THE PANEL (MIB=64 at about 3 s, MIB=640 at 18 s, the last, MIB=1792, at about 48 s). '
            f'(2) THE HASH, about {HASH_S} s: `PAYLOAD HASH INDEX=2 MIB=64` and so on from about 49 s, MIB=1536 at 55 s, about {HASH_MIB_S} MiB/s (INFERRED from the lines). (3) The PAYLOAD LOADED lines and then the bring-up page '
            '(`Q1N1 / SNAPDRAGON BRINGUP`, `EL2 CONFIRMED AFTER EXITBOOTSERVICES`, the range table, `BOOT FOOTHOLD ONLY - NO GUEST OR ASP YET`) at about 57 s on the film\'s clock; the panel was not dark. (4) THE PAGE THAT STANDS STILL: that page '
            'did not change for the rest of the first film, 38 s or more, while the wrapper digested, copied and verified; how long that stretch took in all is UNKNOWN (its end is on no film), and the operator took it for a hang. '
            'The page of the end gave `CHUNKS TOTAL=339 COPIED=339 HASHED=176`, `TICKS HZ=19200000 PLACED=40419 COPIED=501263277` (26.1 s counted from the wrapper\'s entry: it holds the placement, the wrapper\'s digests of the file and the copy, '
            'and not the verify, so the copy alone ran at more than ' + f'{RATE9_MIB:.1f} MiB/s over the {PLACED9:,} bytes that the 339 chunks place, '
            'zero chunks included: INFERRED from probes/sp11-stick-wrapper.c:370, 417-419, read on the host), `PLACE BASE=0X880000000 SIZE=0X1A8000000 DESC=219`, `GUEST.DAT BASE=0XF8D2A1000 BYTES=1926621440`, `VERDICTS PIN=OK GUEST=OK POLICY=0X3 LINES=29`, `PAYLOAD CALL 1 562B9B6EEAF97B87` and '
            '`STACK=OK`: THE FIRMWARE PUT THE FILE AT 0XF8D2A1000 (about 62.2 GiB, below the allocation bound 0XFFFFFFFFF) IN ONE DESCRIPTOR, A USB READ INTO MEMORY ABOVE 4 GiB WORKED (the digests agreed), and no reset, refusal or '
            'timeout came in the carry\'s two minutes: the UNKNOWNs of rung 9\'s record (the firmware\'s placement of a 1.8 GiB allocation, one descriptor, the read above 4 GiB, an 8 MiB flush operation, the firmware watchdog, the '
            'stick\'s rate) are OBSERVED once each, for that file and that stick. WHAT THIS RECORD CHANGES (INFERRED; the arithmetic is boot 28\'s): the file is ' + f'{d["guest_bytes"][1] - d["guest_bytes"][0]:,} bytes longer, '
            f'so the runner\'s read and hash take what they took ({lines} lines of each, MIB=64 to {lines * PROGRESS_MIB}, OF={of}) and the sweep has 230 operations of 8 MiB again; but the wrapper stores a ZERO chunk as it stores data, '
            'eight bytes at a time and then a cache clean of the range (probes/sp11-stick-load.c copy_chunk and sw_copy_all, read on the host), so the ISC\'s zeros are not free: this guest\'s ' + f'{d["count"]} chunks place {placed:,} '
            f'bytes, {d["medium"]["bytes"] - r10b.ROOT_BYTES:,} more than boot 28\'s, and the digests and the copy take at most about {dc_s(d)} s together, about {extra:.0f} s more than boot 28\'s 26.1 s (the rate of zero stores is UNKNOWN and may be higher); the '
            'verify flushes every chunk and scans each ZERO chunk (sw_verify_all), which boot 28 did not time apart: UNKNOWN, INFERRED at most about as long as the digests and the copy. THE WAIT BEFORE THE MON CALL LINE IS THEREFORE ABOUT ' + f'{wrapper_s(d) + GIC_ARM_S} s '
            f'at most from PAYLOAD CALL 0 (the digests and the copy at most {dc_s(d)} s together, the verify at most about as long again: UNKNOWN, and the GIC arm\'s {GIC_ARM_S} s), a page that is silent but for the wrapper\'s '
            'lines. WHAT THE OPERATOR SEES, IN ORDER, from the Shell\'s AUTORUN line: the READ lines, the HASH lines, the five PAYLOAD LOADED lines TOGETHER after the last HASH line, the SP11 EBS lines, then PAYLOAD READY and PAYLOAD BIGFILE, '
            'the stand-still of the sweep and the bounce (UNKNOWN time), the bring-up page, PAYLOAD VERIFY=OK, PAYLOAD CALL 0 (which spends the approval: section 5c) and the wrapper\'s lines to MON CALL, with a pause after '
            'STAGE=PIN_OK (its digests) and after STAGE=COPYING (the copy and the verify), IF THE PANEL DRAWS THEM: boot 28\'s first film saw the bring-up page unchanged for 38 s or more after it appeared (OBSERVED once), so '
            'whether they are drawn is UNKNOWN; if not, the 360 s of step 5 count from the first frame in which the panel is cleared and shows the kernel\'s text. A DARK PANEL BY ITSELF IS NOT A RESULT, and boot 28\'s panel was not dark in the carry. WHAT A STALL LOOKS LIKE: a READ line is due about every ' + f'{PROGRESS_MIB / READ_MIB_S:.1f} s '
            f'and a HASH line about every {PROGRESS_MIB / HASH_MIB_S:.1f} s; if none has come for {LINE_WAIT_S} s (about {LINE_WAIT_S / (PROGRESS_MIB / READ_MIB_S):.0f} times the observed gap), or if the page has not changed for {CARRY_WAIT_MIN} minutes '
            f'before PAYLOAD CALL 0, or for {CALL_WAIT_S} s between PAYLOAD CALL 0 and MON CALL (section 5), it is a stall: note the last line (its MIB says how far it had got) and FORCE OFF. These waits are several times the '
            'estimates and not measurements, and FORCE OFF then spends a boot that did not run the guest, which the coordinator may repeat with the same files (section 5c). WHAT TO FILM is the whole panel from the Shell\'s countdown to '
            f'{r5.FILM_AFTER_S} s after the SNAPREP page (about {film_min(d, panel["run"]["box_s"])} minutes), with the main camera: the READ lines scroll off, so the film and not a photograph is their record, and the times of the first and of '
            'the last READ line, of the first HASH line, of PAYLOAD CALL 0 and of each of the wrapper\'s lines to MON CALL measure this record\'s carry against boot 28\'s. A REFUSAL OF THE CARRY comes before the guest: nothing of the kernel is on the panel, and its NAME is the result. The names and what each would mean: '
            + '; '.join(f'{name}: {text}' for name, text in CARRY_REFUSALS) + '. Boot 28\'s file passed the first two, the read and the hash; a refusal that is new would come from what this record changes (the guest\'s '
            'placement and the table are rung 9\'s, so PLACE_* and CHUNK_* are not expected). A RESTART OF THE SURFACE DURING THE CARRY is a result of the carry and not of the guest: the guest has not run, the approval is spent only if '
            'PAYLOAD CALL 0 printed, and the film says when the Shell\'s countdown or the logo came back and what the last line was.')


def expect_text(panel, wait_s):
    """WHAT TO EXPECT: the course of the run from the MON CALL line, in the stages of THE AIM."""
    box_s = panel['run']['box_s']
    return ('WHAT TO EXPECT. As in boots 21 and 24 to 28: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the guest has stopped: at the end of the '
            f'box ({box_s} s) if the guest goes on, sooner on another stop. The first lines are boot 28\'s (OBSERVED once): the noise of the missing models ({d8.START_LINES}, the SPMI parity-error lines of the registers that the clock does not serve), '
            'the bsd_init block, the mount of the root, `load_init_program: attempting to load /sbin/launchd`, libignition\'s `Darwin Ignition Sequence` lines, `BaseSystem environment starting.` and launchd\'s boot tasks, each as '
            '`(NAME) Doing boot task` or `(NAME) Skipping boot-task`. WHAT IS NEW comes at the third task, mount-phase-1: boot 28 printed the lines about the roles 10 and c0 and then ten rounds of '
            '`wait_for_node` about 3.2 s apart (about 30 s); with the ISC the ten rounds are not expected (INFERRED) and launchd\'s next lines are the later tasks, in the order of THE AIM, with the xtrace of the shell script of '
            'restore-datapartition (every command after a `+`) as the likeliest place for the first real stop (INFERRED). A silent stretch is not a failure by itself: a boot task that waits is silent. A dark panel is expected for part of the '
            'time (the kernel clears it); it is a restart only if the Shell\'s countdown or the runner\'s first lines come back. '
            f'{r2.force_off(wait_s)}. {r2.COUNT.format(wait=wait_s)}')


def predicts_text(panel):
    """WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT, for the guest with the ISC."""
    box_s = panel['run']['box_s']
    return ('WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED; no probabilities; read on the host in the loader, the monitor\'s ELF, the BaseSystem image and boot 28\'s films, a file and not a run). PREDICTION: the carry '
            'ends with PAYLOAD CALL 0 and MON CALL a little over two minutes after the first READ line (boot 28\'s, plus the ISC\'s zeros: THE CARRY); the kernel mounts the root as in boot 28 and runs launchd; mount-phase-1 finds '
            'the ISC, mounts its xART volume and passes (EMPTY volumes: INFERRED; the xART role by a byte stamp: UNKNOWN); the tasks after it go in launchd\'s order, data-protection in silence, and the first stop that the host reading '
            'expects is restore-datapartition (a `+` line of its script, then `boot task failure: restore-datapartition`) or prebootensurer\'s exit inside it, then fips; if all of them pass, `Early boot complete`, the daemons '
            'and WindowServer\'s abort and restart. THE CLEAN END THAT THIS RECORD CAN GIVE: MON STOP WATCHDOG at the end of the box '
            f'({box_s} s after the call) with launchd\'s text on the panel, wdt=-1 keeping XNU\'s own watchdog off. A userspace panic ends the guest sooner (boot 28: 40.08 s after the call), and the kernel\'s own panic for the CPU that never '
            f'comes, "cpu 1 failed to boot for the first time", would come about {r9.CPU_PANIC_AT_S} s after the call (INFERRED), {r9.CPU_PANIC_AT_S - box_s} s after the box: it is not expected inside it. FALSIFIERS: the old failure again, mount-phase-1 with '
            'its ten rounds (THE ISC DID NOT TAKE); a root that does not mount (the medium changed what the kernel finds); the carry taking more than the waits of section 5 (a finding about the stick or the zeros); a MON STOP S2_FAULT with '
            f'{r5.WINDOW} {r5.FALSIFIER_ACK}; the ApplePMGR assertion again; a MON STOP S2_FAULT in the window of a node that the profile withdrew; a MON STOP UNNAMED with B= in {d9.clock_window_text()}, the clock model\'s window (the model '
            'refused an access: NEW STOP); a MON STOP DEVICE_RESET (class WD1); a MON STOP of any other kind. THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE are a result in every class that has no panic text.')


def decides_text():
    """WHAT THE FILM AND THE PAGE DECIDE: the carry, the panel, launchd's tasks in order with the test of the ISC, then the page."""
    return ('WHAT THE FILM AND THE PAGE DECIDE (what this record changes has not been seen; boots 21 and 24 to 28 are the comparison, OBSERVED once each). First the carry: the runner\'s and the wrapper\'s lines and the time of each, '
            'from the film, against boot 28\'s (THE CARRY). Then the panel: the coordinator transcribes the first lines of any panic text from the film and the stills, and the classes below read from it. Then the kernel\'s log, replayed '
            'from its start by the first frames after the MON CALL line and read to its LAST LINE and the time of that line on the film\'s clock, with THE QUESTIONS answered from it, each with its time: (1) the line `load_init_program: '
            'attempting to load /sbin/launchd` and launchd\'s own `hello, launchd` line with its date (boot 28: the model\'s seed plus 4 s); (2) launchd\'s tasks in order, each `(NAME) Doing boot task` or `Skipping boot-task` with its '
            'time; (3) AT mount-phase-1: do the ten rounds of `wait_for_node:266: APFS IOKit node lookup timed out` come (THE ISC DID NOT TAKE), and are the lines about the roles 10 and c0 as in boot 28; (4) the first task after '
            'mount-phase-1 and every `+` line of a script that follows it, and the NAME in `Boot task failed: NAME` if there is one; (5) any line about the ISC, the xART volume, /System/Volumes/xarts, a mount error or SEP, and any panic '
            'text; (6) the last line of the log and the time of its last change. With six CPUs the processor lines of boot 21\'s log are expected again and are not the failure. Then the report page: boot 27\'s stop was '
            f'{d9.STOP27} at the end of its box (SPSR {r8.BOOT27_SPSR}: EL1 with every interrupt masked, idle INFERRED), boot 28\'s was MON STOP S2_FAULT at a 32-bit '
            f'read in the kernel\'s chip-reset path after a userspace panic (`MON ESR EC=0X24 ISV=1 SIZE=4 SRT=10 WNR=0`, A=0X3082BC followed by digits that the film did not show; OBSERVED once, INFERRED to be boot 24\'s A=0X3082BC224 again), and '
            f'the CPU-start stop is a MON STOP S2_FAULT with {r5.WINDOW} (a read there, MON ESR saying WNR 0, would be new and unexpected). An S2_FAULT at another address is progress, and a MON STOP UNNAMED with B= in the clock\'s window is a finding '
            'about the clock model. The MON RET TICKS= of any stop that is not WATCHDOG says its time on the monitor\'s clock (19.2 MHz): boot 28\'s was 769573015, 40.082 s. NOT THE FAILURE: ' + NOT_THE_FAILURE + '.')


def filming_text(panel, wait_s):
    """THE FILM: rung 9's, with the carry as boot 28 showed it and the numbers of this record."""
    text = d9.filming_text(panel, wait_s)
    return r8.patched(text, ('THE FILM is rung 8\'s, from the Shell\'s countdown through THE CARRY (about two minutes: the runner\'s progress lines and then a still page, which the film times)',
                             'THE FILM is rung 9\'s and boot 28\'s, from the Shell\'s countdown through THE CARRY (the runner\'s progress lines, then a still page, which the film times)'),
                      (f'(about {r9.FILM_MIN} minutes in all for this box of {panel["run"]["box_s"]} s)',
                       f'(about {film_min(panel["rung10"]["diff"], panel["run"]["box_s"])} minutes in all for this box of {panel["run"]["box_s"]} s)'),
                      ('(the clock driver\'s lines about 7 s in, the time of the bsd_init block and THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE are results in every class that has no panic text)',
                       '(launchd\'s first line and the time of each of its tasks, the ten rounds of mount-phase-1 if they come, the last `+` line of a script, and THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE, '
                       'which are results in every class that has no panic text)'),
                      ('(boots 21, 23, 24, 25, 26 and 27: OBSERVED once each)', '(boots 21, 23, 24, 25, 26, 27 and 28: OBSERVED once each)'))


def panic_text():
    """WHAT A PANIC LOOKS LIKE: rung 9's words, for this record, with the userspace panic that boot 28 showed."""
    text = r8.patched(d9.panic_text(), ('is the class RUNG 9 PANIC TEXT below', 'is the class RUNG 10 PANIC TEXT below'))
    return text + (' A USERSPACE PANIC looks like boot 28\'s (OBSERVED once): launchd prints `Boot task failed: NAME` and `Panicking in 3 seconds.`, and the kernel panics with `userspace panic: boot task failure: NAME - exited due to '
                   'SIGABRT` (or another reason after the dash); nested panics in the RTBuddy path, `Nested panic count exceeds limit 5, machine will reset or spin` and `wdog: panic chipreset` follow, and about a second later MON STOP S2_FAULT at the '
                   'kernel\'s chip-reset path (boots 24 and 28): a later stop and not a result of its own. THE NAME and the lines before it are the result.')


def patched_once(text, *pairs):
    """`text` with each (old, new) put in place once, AssertionError unless `old` is in it exactly once (text that is not flattened keeps its spaces: the words are one line each)."""
    for old, new in pairs:
        assert text.count(old) == 1, f'rung 9\'s words have changed under rung 10: {old[:60]!r} is not in them exactly once'
        text = text.replace(old, new)
    return text


def results_paragraphs(panel, run, wait_s):
    """The results of rung 10, one paragraph each, in the operator's words, read in this order from the film of either camera, the stills and the page: the first class that fits is the film's. The classes of
    rung 9 that still hold are rung 9's own words (tools/sp11_stick_bundle_panel_rung9d.py), with the rung's number changed."""
    box_s = run['box_s']
    old = d9.results_paragraphs(panel, run, wait_s)
    again = [text.replace('RUNG 9 ', 'RUNG 10 ', 1) for text in old]
    carry, not_a_result, not_passed_a, wd1, new_stop, not_passed_b = again[1], again[3], again[4], again[5], again[7], again[10]
    assert carry.startswith('RUNG 10 CARRY STOPPED') and not_a_result.startswith('RUNG 10 NOT A RESULT') and not_passed_a.startswith('RUNG 10 NOT PASSED, (a)'), 'the classes of rung 9 have changed'
    assert wd1.startswith('RUNG 10 WD1') and new_stop.startswith('RUNG 10 NEW STOP') and not_passed_b.startswith('RUNG 10 NOT PASSED, (b)'), 'the classes of rung 9 have changed'
    carry = patched_once(carry, ('from THE CARRY\'s UNKNOWNs (where the firmware places 1.8 GiB, one descriptor or several, the stick\'s rate, the memory above 4 GiB, an 8 MiB flush, the firmware watchdog)',
                                 'from THE CARRY (boot 28 passed the read, the hash, the placement, the descriptor, the read above 4 GiB and the flush: a refusal that is new points at what this record changes, the ISC\'s zeros in the copy '
                                 'and the verify)'))
    not_a_result = patched_once(not_a_result, ('read the clock driver\'s lines and the last lines of the log', 'read launchd\'s lines and the last lines of the log'))
    not_passed_b = patched_once(not_passed_b, ('NOT PASSED for rung 9, which has not reached its aim', 'NOT PASSED for rung 10, which has not reached its aim'))
    new_stop = new_stop.replace('RUNG 10 NEW STOP: no panic text, no "BSD root:" and a MON STOP', 'RUNG 10 NEW STOP: no panic text and a MON STOP', 1) + (
        ' A STOP AFTER LAUNCHD HAS RUN (an S2_FAULT, an UNDEF or an SPTM refusal at a PC that is a user address, with launchd\'s lines on the film) is the monitor\'s user path failing under launchd\'s processes: boot 28 ran '
        'launchd, dyld and the shared cache to a panic of its own without one (OBSERVED once), so such a stop is new, with more processes (INFERRED: the ASID and TLBI scope of the old user path, PAC in user space); its PC=, A= '
        'and the last line of the log with its time are the result.')
    head = ('THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 10\'s. The carry is read first, then the panel, then the stop, and for a stop at the end of the box the last line of the log and the time '
            'of its last change; a film is in the first class below that fits, in this order, and every film fits one. The aim of this rung is the answer to ONE question, whether mount-phase-1 passes with an ISC in '
            'the RAM disk, and where the next stop is: THE ISC DID NOT TAKE, NEW USERSPACE STOP, THE ROOT DID NOT MOUNT, LAUNCHD RUNNING and SILENT WAIT read it, each as a stage of THE AIM, and a stop of any kind is read for '
            'what it is. CARRY STOPPED, NOT A RESULT and the two NOT PASSED classes are the failures of the record: nothing is learnt about the guest, or the record did not reach its aim.')
    isc = ('RUNG 10 THE ISC DID NOT TAKE: the panel shows launchd\'s first boot task failing as in boot 28: the lines about the roles 10 and c0, THE TEN ROUNDS of `wait_for_node:266: APFS IOKit node lookup timed out...` '
           'and `Retrying (N retries left)` down to 1, then `Boot task failed: mount-phase-1`, `Panicking in 3 seconds.` and `userspace panic: boot task failure: mount-phase-1 - exited due to SIGABRT`. THE TEXT IS THE '
           'RESULT, and the finding is that the lookup did not find the ISC (INFERRED causes, UNKNOWN which: the kernel did not publish the ISC slice of a RAM-disk medium to APFS, the container did not show a volume of the xART role, '
           'or the extents that the kernel took are not the medium). That launchd ran at all says that the root partition was found inside the GPT, which is a result about the medium. The next record changes the image or '
           'the medium and not the form. The S2_FAULT at the kernel\'s chip-reset path after the panic is a later stop and not a result of its own (boots 24 and 28).')
    userspace = ('RUNG 10 NEW USERSPACE STOP: launchd\'s panic names another task: `Boot task failed: NAME` and `userspace panic: boot task failure: NAME - ...` with a NAME that is not mount-phase-1, or mount-phase-1 with NONE of the ten rounds '
                 '(the lookup found the ISC and the task failed on something else: the line before the failure, transcribed whole, is the result; a mount error of the xART volume would mean that the kernel refuses the stamped role: '
                 'UNKNOWN). THE RESULT IS THE NAME, the `+` lines of a script that came before it and the lines of the failed command. The host reading expects restore-datapartition first (a command of its `set -ex` script that fails, '
                 'or prebootensurer exiting 70 or 71), then fips (INFERRED); each is the next work, named by the film. A boot task that does not fail but waits is SILENT WAIT. The S2_FAULT after the panic is a later stop and not a result '
                 'of its own.')
    panic = ('RUNG 10 PANIC TEXT: the panel shows any other panic text, whatever the page says and whatever the stop is: a kernel panic, one of the mount of the root, of a driver or of a service that needs the SEP or the AES engine; nested '
             'panics and boot 24\'s and 28\'s end, MON STOP S2_FAULT at a read in the kernel\'s chip-reset path, follow within about a second and are a later stop and not a result of their own. THE TEXT IS THE RESULT: the coordinator '
             'transcribes its first lines verbatim from the film and the stills and hands them back; it names the stage that answers the aim. It is also a falsifier if it is the ApplePMGR assertion of boot 24 again, or '
             f'"cpu 1 failed to boot for the first time, waited N seconds" inside the box (it is expected about {r9.CPU_PANIC_AT_S} s in, with N={r9.CPU_WAIT_S}, after the box).')
    root = (f'RUNG 10 THE ROOT DID NOT MOUNT: no panic text, no launchd line and MON STOP WATCHDOG at the end of the {box_s} s box, and the last line of the log is a `Waiting on <dict>` for a root device that stands to the end. THE RESULT IS THE DICT, '
            'transcribed whole, with the time it appeared: the kernel did not find the root in the GPT medium, which boot 28 mounted as a raw image (INFERRED: the /chosen extents name the medium and the root hash covers the '
            'root partition only; whether the kernel finds a partition of a RAM disk this way is UNKNOWN). Three readings: (1) the dict of boot 27 (an IOMedia of content Apple_HFS): the RAM-disk data of /chosen did not take; (2) a dict '
            'that names IOResources and boot-uuid-media: the kernel took the UUID path and waits for a volume that the medium\'s partition scheme did not publish; (3) another dict: transcribed, and read from its words. It is a finding about '
            'the medium and the next record changes it.')
    running = (f'RUNG 10 LAUNCHD RUNNING: no panic text, launchd\'s text on the panel at the end and MON STOP WATCHDOG at the end of the {box_s} s box, the last lines being launchd\'s or a daemon\'s and not a task that waits: mount-phase-1 passed '
               '(the ten rounds did not come) and the boot tasks went on. THE RESULTS ARE, each with its time: (1) the end of mount-phase-1 and the lines of the ISC if there are any; (2) the last boot task named '
               '(`Doing boot task`) and whether `Early boot complete. Continuing system boot.` is on the film; (3) the daemons\' lines at the end (WindowServer\'s abort and restart: INFERRED from the A16); (4) the stop and its page. THIS '
               'IS THE BEST END THAT THIS RECORD CAN HAVE: macOS ran as far as launchd\'s daemons under the monitor, which is not a desktop and claims nothing of it.')
    silent = (f'RUNG 10 SILENT WAIT: MON STOP WATCHDOG at the end of the {box_s} s box, no panic text, and the log was silent for the last stretch (say how long, from the time of its last change), or its last line is a boot task\'s '
              '`Doing boot task` line or a `+` line of a script that stands: a task that waits. THE RESULT IS THE LAST LINE, ITS TIME AND THE NAME OF THE TASK, with the guess that the film allows (INFERRED: data-protection waiting for '
              'AppleSEPManager, which prints a line "Waiting N seconds for AppleSEPManager" if it does; a long command of restore-datapartition; fips computing; a driver that waits for a device that has no model). MON TIM says whether the timer is '
              'delivered (VECTORS above 0 is the normal page of a guest that waits, as in boots 26 and 27); a page with VECTORS 0 would contradict them and is itself a finding. The MON ACK, MON CNT and MON CON lines are findings to '
              'read, not classes. The next record adds the layer that the last line points at.')
    return (head, carry, isc, userspace, panic, not_a_result, not_passed_a, wd1, root, new_stop, running, silent, not_passed_b)


def section_5d(panel, wait_s):
    """Section 5d of the record of rung 10, after 5c, in the operator's words. `wait_s` is the silence rule's wait for the box (the guide's silence_s)."""
    box_s = panel['run']['box_s']
    texts = (new_text(panel), d9.fixed_text(panel), aim_text(panel), carry_text(panel), expect_text(panel, wait_s), predicts_text(panel), filming_text(panel, wait_s), d7.page_text(), decides_text(), panic_text(),
             *results_paragraphs(panel, panel['run'], wait_s))
    return (heading(box_s), *(line for text in texts for line in m1t.wrap(text, 3, 6)))


def step4_text(guest, label):
    """5c, step 4: the digits that tell this bundle from rung 9's (GUEST.DAT's size and digits, PIN.DAT's, the label, the chunk count and PAYLOAD.MAN's differ; the programs and the runner are rung 9's, boot 28's)."""
    return (f' The monitor\'s, the reporter\'s and the runner\'s digits are rung 9\'s (the root-carry pair, which ran in boot 28): INDEX=0 ... SHA={m1.ROOT_CARRY_MONITOR_SHA256[:16].upper()} and INDEX=1 ... SHA={m1.ROOT_CARRY_REPORTER_SHA256[:16].upper()}. The runner prints the '
            'five PAYLOAD LOADED lines TOGETHER, after the last of its PAYLOAD HASH lines and not before (OBSERVED once in boot 28 for rung 9: they came after the READ and HASH lines, about '
            f'{READ_S + HASH_S} s after the first READ line), so INDEX=0 and INDEX=1 are on the screen only then; INDEX=2 is GUEST.DAT: INDEX=2 ... BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (rung 9\'s: '
            f'{r10b.RUNG9_GUEST_BYTES} bytes, SHA={r10b.RUNG9_GUEST[:16].upper()}, read from the stick after boot 28), and on the SNAPMON STAGE=PIN_OK line LABEL={label} CHUNKS={guest["chunks"]} (rung 9\'s record gave '
            f'LABEL={r10b.RUNG9_LABEL} CHUNKS={r10b.RUNG9_CHUNK_COUNT}; this record cannot name its own PIN.DAT SHA, which depends on this record\'s SHA-256). The line PAYLOAD BIGFILE CHUNK=... MAXOPS=... MAXFILE=... TOP=... is on the page too.')


def step5_text(panel):
    """5c, step 5, before the base words' sentences: the carry's lines and waits of section 5, with boot 28's numbers."""
    d = panel['rung10']['diff']
    return (f' The carry comes first (section 5d, THE CARRY): about {READ_S + HASH_S} s of progress lines from the first PAYLOAD READ line (boot 28: {READ_S} s of READ lines, then '
            f'{HASH_S} s of HASH lines), the five PAYLOAD LOADED lines together, a page that stands still for the sweep and the bounce, and after PAYLOAD CALL 0 up to about {wrapper_s(d) + GIC_ARM_S} s more while the wrapper checks, copies and verifies the '
            f'file and the ISC\'s zeros (INFERRED from boot 28\'s 26.1 s for digests and copy); section 5 gives the waits before FORCE OFF ({LINE_WAIT_S} s without a new READ or HASH line, {CARRY_WAIT_MIN} minutes without any change before '
            f'PAYLOAD CALL 0 and {CALL_WAIT_S} s after it), which are several times those figures and not measurements.')


def pre_call_lines(panel):
    """The bullets of section 5 on the waits before the call, for the carry: a stall of the progress lines, no change of the page at all before the call, and the wrapper's digests, copy and verify after it."""
    d = panel['rung10']['diff']
    ratio = CALL_WAIT_S / (wrapper_s(d) + GIC_ARM_S)
    times = 'twice' if round(ratio) == 2 else f'{ratio:.1f} times'
    return (
        *m1t.wrap(f'- If the progress lines are shown (they were in boot 28: OBSERVED once): no new PAYLOAD READ or PAYLOAD HASH line for {LINE_WAIT_S} seconds while the runner loads GUEST.DAT (after the first of them and before the five '
                  f'PAYLOAD LOADED lines): note the last line, whose MIB says how far it had got, and FORCE OFF (section 5c). A line is due about every {PROGRESS_MIB / READ_MIB_S:.1f} s while the file is read, at the stick\'s rate of boot 28 '
                  f'(about {READ_MIB_S} MiB/s, OBSERVED once for one stick), and about every {PROGRESS_MIB / HASH_MIB_S:.1f} s while it is hashed; the {LINE_WAIT_S} s are about '
                  f'{LINE_WAIT_S / (PROGRESS_MIB / READ_MIB_S):.0f} times that gap. A slower stick makes the lines further apart and is no refusal. The approval is not spent (section 5c, THIS APPROVAL COVERS ONE BOOT).', first=3, rest=5),
        *m1t.wrap(f'- No change of the page for {CARRY_WAIT_MIN} minutes at any point after the Shell\'s AUTORUN line and before the runner\'s PAYLOAD CALL 0 line: nothing of M1 has run; note the last line and FORCE OFF '
                  '(section 5c). Besides the progress lines, the page stands still before the first PAYLOAD READ line (the runner\'s start, seconds) and after the last of the runner\'s console lines, PAYLOAD BIGFILE, while the firmware '
                  'sweeps the declared ranges (230 operations of 8 MiB for GUEST.DAT: the time is UNKNOWN) and the bounce runs; the page is then drawn again (in boot 28 at about 57 s on the film\'s clock, about 9 s after the last READ line). '
                  f'The {CARRY_WAIT_MIN} minutes are several times the whole carry up to that page, an operator-safe wait and not a measurement. The approval is not spent (section 5c, THIS APPROVAL COVERS ONE BOOT).', first=3, rest=5),
        *m1t.wrap(f'- No page change for {CALL_WAIT_S} seconds at any point after the runner\'s PAYLOAD CALL 0 line and before the MON CALL line: note the last line and FORCE OFF (section 5c). The healthy silences in that '
                  f'stretch are the wrapper\'s digests of the file and its copy (together 26.1 s in boot 28 for rung 9\'s chunks, at most about {dc_s(d)} s for this guest\'s, INFERRED), after STAGE=PIN_OK, and its verify '
                  f'(UNKNOWN, at most about as long as the two), after STAGE=COPYING, and the {GIC_ARM_S} s that the NEXT GIC STORES AFTER 8 S line announces; the {CALL_WAIT_S} s are about {times} the sum of those upper figures, an '
                  'operator-safe wait and not a measurement (boot 28\'s bring-up page was still the first page 38 s after it appeared, and the end of that stretch is on no film).', first=3, rest=5))
