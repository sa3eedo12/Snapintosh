# SPDX-License-Identifier: MIT
"""Section 5d of the record of rung 9 (docs/SP11-STICK-ROOT.md; docs/SP11-STICK-MONITOR.md section 20), in the operator's words: the aim, what is new, the carry (the first minutes, in which the page is
silent), what to expect, what the record predicts and what falsifies it, what to film, what the film and the page decide, what a panic looks like and what counts as a result. The legend of the reporter's lines is
rung 7's (tools/sp11_stick_bundle_panel_rung7d.py page_legend) with the runner's notes of its big-file mode and the refusals of the carry added. The rest of the record's words for this rung are
tools/sp11_stick_bundle_panel_rung9.py's, which this module takes its constants from; its `section_5d` reaches this module.

The aim of this rung is ONE question: how far does the stock kernel's mount of the RAM-disk root go? The results are read in order and a film is in the first class that fits (CARRY STOPPED, PANIC TEXT,
NOT A RESULT, NOT PASSED (a), WD1, MOUNTED, NEW STOP, WAITING FOR THE MEDIA, SILENT WAIT, NOT PASSED (b)): the classes are exclusive and leave no film without one. The order of the kernel's stages that
the classes follow is the host reading of the root-mount path (files/root-profile-note.md of the session): INFERRED throughout, nothing of it has run. Every time-dependent sentence takes the box from the
record (`box_s`, and `wait_s`, the silence rule's wait for it).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung7 as r7  # noqa: E402
import sp11_stick_bundle_panel_rung7d as d7  # noqa: E402
import sp11_stick_bundle_panel_rung8 as r8  # noqa: E402
import sp11_stick_bundle_panel_rung8d as d8  # noqa: E402
import sp11_stick_bundle_panel_rung9 as r9  # noqa: E402
import sp11_stick_bundle_rung8 as r8b  # noqa: E402
import sp11_stick_bundle_rung9 as r9b  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

STOP27 = d8.STOP27
WRAP_S = 2 ** 32 / 24_000_000       # 178.96 s: the u32 wrap of the guest's virtual WD1 counter, which counts the guest's own clock at 24 MHz (probes/guestprofile.py virtual_counter_hz, compat/watchdog.c)
WRAP_TICKS = round(WRAP_S * 19_200_000)    # the same instant in ticks of the monitor's 19.2 MHz clock, which MON RET TICKS= counts: 3,435,973,837
CARRY_REFUSALS = (
    ('FILE_NO_MEMORY', 'the runner\'s: the firmware has no free range for GUEST.DAT at or below the top of RAM (2^36 - 1), and the runner makes no second try elsewhere; the line names the file, the firmware\'s status '
                       'and the size wanted (PAYLOAD REFUSED=FILE_NO_MEMORY INDEX=2 EFI=<status> GOT=0 WANT=<bytes>); nothing was declared or bounced'),
    ('NOT_IN_ONE_DESCRIPTOR', 'the runner\'s, before the point of no return (PAYLOAD REFUSED=RANGES REASON=NOT_IN_ONE_DESCRIPTOR RANGE=<n>): the firmware lists the 1.8 GiB allocation as more than one memory-map '
                              'descriptor, and the bounce needs exactly one for each range'),
    ('FILE_TOO_BIG', 'the runner\'s (PAYLOAD REFUSED=FILE_TOO_BIG INDEX=2 GOT= WANT=): the runner is not the one of the big-file mode, whose limit for a file is 256 operations of 8 MiB, 2 GiB; the pinned runner '
                     'stops at 256 MiB'),
    ('FILE_READ, HASH', 'the runner\'s: a read that failed, or a SHA-256 that differs from PAYLOAD.MAN\'s (PAYLOAD REFUSED=HASH INDEX=2 ...): the stick, the file or a read into memory above 4 GiB (UNKNOWN which)'),
    ('PLACE_DECLARED, PLACE_*', 'the wrapper\'s (SNAPMON REFUSED=PLACE_DECLARED ...): the guest\'s placement overlaps a range that the runner declared, GUEST.DAT\'s buffer among them; PLACE_FB, PLACE_AVOID and '
                                'PLACE_NOT_CONV are the same check against the framebuffer, the avoid ranges and the memory map'),
    ('CHUNK_*, GUEST_*', 'the wrapper\'s (SNAPMON REFUSED=CHUNK_RANGE or another name of the table: CHUNK_EOF, CHUNK_OVERLAP, GUEST_SIZE ...): the file that the runner loaded is not a guest that fits the placement'),
    ('INDEX_DIGEST, CHUNK_DIGEST, FILE_DIGEST, COPY_VERIFY', 'the wrapper\'s digests of the file in memory, or of the copy: a digest that differs from PIN.DAT\'s (the buffer was damaged between the runner\'s hash and '
                                                           'the wrapper\'s, or the copy is not the file)'))
CLOCK_ADDRESSES = d8.CLOCK_ADDRESSES
NOT_THE_FAILURE = ('the noise lines of section 0a (the SPMI parity-error lines of the registers that the clock model does not serve, the PMU and RTKit lines, "Couldn\'t alloc class AFKResource", the AFKFirmwareService line, '
                   'the USB "failed to find" lines: boots 21 to 27, OBSERVED once each), "Load request for com.apple.nke.pptp failed: <err>" (INFERRED: the kernelcache has no pptp; harmless), and, with 6,144 MiB of '
                   'RAM, any "memorystatus" line (boot 27 had 9,094 free pages of 16 KiB in 384 MiB; whether this guest\'s count is a finding is UNKNOWN); INFERRED to come from the monitor\'s empty models, which the film '
                   'shows before any panic text')


def clock_window_text():
    """The window of the clock model, first byte and one past the last (the controller of nub-spmi0: 0X18000 bytes at tools/sp11_stick_bundle_rung8.py CLOCK_BASE, which rung 8's builder reads from PARAMS)."""
    return f'0X{r8b.CLOCK_BASE:X}..0X{r8b.CLOCK_BASE + 0x18000:X}'


CARRY = ('THE CARRY. Before the guest runs, the runner reads GUEST.DAT, 1.9 GB, from the stick into memory and hashes it, and the wrapper digests it, copies it into the guest\'s placement and verifies the copy: '
         f'about {r9.CARRY_READ_S + r9.CARRY_HASH_S + r9.CARRY_WRAPPER_S + 8} s in all (INFERRED: the runner at an ASSUMED 24 MiB/s (UNKNOWN: boots 8 and 9 measured the hash alone, at 194 MiB/s), then its hash '
         f'at about 194 MiB/s, then the wrapper\'s {r9.CARRY_WRAPPER_S} s of section 4 and the GIC arm\'s 8 s; the stick\'s rate at this size is UNKNOWN). The runner\'s progress is ON THE PANEL: its lines PAYLOAD READ and '
         f'PAYLOAD HASH, one for each {r9.PROGRESS_MIB} MiB of GUEST.DAT, go to the firmware\'s text console, the one that also shows the Shell\'s own lines and the runner\'s other PAYLOAD lines (read on the host in the '
         f'runner\'s source, a file and not a run), so the page changes about every {r9.READ_GAP_S} s while the file is read (INFERRED) and a stall shows as a line that does not come. The page stands still after the '
         'runner\'s last line, while the firmware sweeps the declared ranges and the bounce runs, and then while the wrapper digests and copies; one CPU is busy at EL2 throughout. A pause in the carry is not a hang: '
         'section 5d says what to wait for and what each refusal would be. A restart of the Surface during the carry is a result of the carry and not of the guest (the guest has not run); a dark panel by itself is '
         'not a result (section 5d). The places where it may '
         'fail, none of which has run: where the firmware allocates 1.8 GiB (UNKNOWN), whether it is one memory-map descriptor (UNKNOWN), a USB read into memory above 4 GiB (UNKNOWN), an 8 MiB cache-flush operation '
         '(UNKNOWN), the firmware watchdog (UNKNOWN), and the stick\'s read rate (UNKNOWN).')


def step4_text(guest, label):
    """5c, step 4: the programs' digits that tell this bundle from rung 8's (GUEST.DAT's digits and size, PIN.DAT's, the label, the chunk count and PAYLOAD.MAN's differ; the programs are the root-carry pair),
    compared on the runner's lines. The runner makes every PAYLOAD LOADED note after the last file is read and hashed (sp11-payload-load.c add_notes, called at the end of q1n1_payload_prepare; main.c
    prints them then), so the lines of all five files come together, after the READ and HASH lines of GUEST.DAT and not before them."""
    return (f' The monitor\'s and the reporter\'s digits are the root-carry pair\'s, and not rung 8\'s (rung 8\'s record names INDEX=0 ... SHA={r7.BOOT26_MONITOR} and INDEX=1 ... SHA={r7.BOOT26_REPORTER}: a page '
            'that shows these has the pair of the older records, whose wrapper refuses a file over 256 MiB). The runner prints the five PAYLOAD LOADED lines TOGETHER, after the last of its PAYLOAD HASH lines and not '
            f'before (it makes none of them until the last file has been read and hashed: read on the host, a file and not a run), so INDEX=0 and INDEX=1 are on the screen only about {r9.CARRY_READ_S + r9.CARRY_HASH_S} s '
            f'after the first PAYLOAD READ line (section 5d); INDEX=2 is GUEST.DAT: INDEX=2 ... BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (rung 8\'s: {r9b.RUNG8_GUEST_BYTES} bytes, SHA={r9.BOOT28_GUEST}, '
            f'read on the host from its bundle), and on the SNAPMON STAGE=PIN_OK line LABEL={label} CHUNKS={guest["chunks"]} (rung 8\'s record gives LABEL={r9b.RUNG8_LABEL} CHUNKS=46; this record cannot name its '
            'own PIN.DAT SHA, which depends on this record\'s SHA-256). The line PAYLOAD BIGFILE CHUNK=... MAXOPS=... MAXFILE=... TOP=... is on the page too (section 5d).')


def step5_text():
    """5c, step 5, before the base words' sentences: the carry's lines and the waits of section 5."""
    return (f' The carry comes first (section 5d, THE CARRY): about {r9.CARRY_READ_S + r9.CARRY_HASH_S} s of progress lines from the first PAYLOAD READ line (one about every {r9.READ_GAP_S} s while the file is read, '
            f'then the HASH lines in about {r9.CARRY_HASH_S} s: INFERRED), the five PAYLOAD LOADED lines together, a page that may stand still for the sweep and the bounce (UNKNOWN time), and after PAYLOAD CALL 0 about '
            f'{r9.CARRY_WRAPPER_S + 8} s more while the wrapper checks, copies and verifies the file; section 5 gives the waits before FORCE OFF ({r9.LINE_WAIT_S} s without a new READ or HASH line, '
            f'{r9.CARRY_WAIT_MIN} minutes without any change before PAYLOAD CALL 0 and {r9.CALL_WAIT_S} s after it), which are several times those estimates and not measurements.')


def pre_call_lines():
    """The bullets of section 5 on the waits before the call, for the carry: a stall of the progress lines while the runner loads GUEST.DAT, no change of the page at all before the call, and the wrapper's
    digests, copy and verify after it (between PAYLOAD CALL 0 and MON CALL)."""
    return (
        *m1t.wrap(f'- If the progress lines are shown: no new PAYLOAD READ or PAYLOAD HASH line for {r9.LINE_WAIT_S} seconds while the runner loads GUEST.DAT (after the first of them and before the five PAYLOAD LOADED lines): '
                  f'note the last line, whose MIB says how far it had got, and FORCE OFF (section 5c). A line is due about every {r9.READ_GAP_S} s while the file is read, at an ASSUMED 24 MiB/s (UNKNOWN: boots 8 and 9 measured '
                  f'the hash alone, at 194 MiB/s), and about every 0.3 s while it is hashed; the {r9.LINE_WAIT_S} s are about 40 times that gap, a rate under 0.5 MiB/s, which would take over an hour for the file. A slower stick '
                  'than 24 MiB/s makes the lines further apart and is no refusal. If the panel draws none of them (UNKNOWN) this bullet does not apply and the next one is the wait. The approval is not spent (section 5c, '
                  'THIS APPROVAL COVERS ONE BOOT).', first=3, rest=5),
        *m1t.wrap(f'- No change of the page for {r9.CARRY_WAIT_MIN} minutes at any point after the Shell\'s AUTORUN line and before the runner\'s PAYLOAD CALL 0 line: nothing of M1 has run; note the last line and FORCE OFF '
                  '(section 5c). Besides the progress lines, the page stands still before the first PAYLOAD READ line (the runner\'s start: INFERRED, seconds) and after the last of the runner\'s console lines, the SP11 EBS '
                  'lines, while the firmware sweeps the declared ranges (230 operations of 8 MiB for GUEST.DAT, which has never run: UNKNOWN) and the bounce runs; the page is then drawn again. '
                  f'The {r9.CARRY_WAIT_MIN} minutes are about three times the estimate of the whole carry, an operator-safe wait and not a measurement. The approval is not spent (section 5c, THIS APPROVAL COVERS ONE BOOT).',
                  first=3, rest=5),
        *m1t.wrap(f'- No page change for {r9.CALL_WAIT_S} seconds at any point after the runner\'s PAYLOAD CALL 0 line and before the MON CALL line: note the last line and FORCE OFF (section 5c). The healthy silences '
                  f'in that stretch are the wrapper\'s digests of the file (about 25 s, after STAGE=PIN_OK), its copy and verify (about 13 s, after STAGE=COPYING: section 4) and the 8 s that the NEXT GIC STORES AFTER 8 S '
                  f'line announces; the {r9.CALL_WAIT_S} s are about twice the sum, an operator-safe wait and not a measurement.', first=3, rest=5))


def heading(box_s):
    """The heading of section 5d: it names the aim, the wait and the box of this record."""
    return (f'5d. Rung 9: the aim, what is new, the carry, what to film, what counts as a result (a root mount: wait of {r9.CPU_WAIT_S} s, box of {box_s} s)')


def page_legend(m1, box_s, real=None):
    """The lines that the guide's section 5b gets after MON EXIT for this record: rung 7's legend of the reporter's window lines, then the runner's notes of its big-file mode and the lines of the carry."""
    extra = (
        '- PAYLOAD BIGFILE CHUNK=<bytes> MAXOPS=<n> MAXFILE=<bytes> TOP=<address> (the runner of the big-file mode, on the page after the files\' lines): the unit of one flush operation that the sweep will use '
        '(0X800000: 8 MiB, which has never run: UNKNOWN), the most operations that any declared range needs (the film is read against it), the file limit (0X80000000) and the highest address that a file may '
        'end at (0XFFFFFFFFF); and PAYLOAD READ INDEX=<n> MIB=<m> OF=<total MiB>, PAYLOAD HASH INDEX=<n> MIB=<m>: one line for each 64 MiB read or hashed, on the firmware\'s text console (the one that shows the Shell\'s lines; '
        'INFERRED to be on the panel and to scroll, UNKNOWN until a run), before the page and not replayed on it.',
        '- SNAPMON STAGE=GUEST_OK CHUNKS=<n> POLICY=<p> TICKS=<t>, then STAGE=COPYING and STAGE=COPIED COPIED=<n> BYTES=<b> HASHED=<n> HASHED_BYTES=<b>: the wrapper\'s digests of the file, and its copy and verify; '
        f'the digests take about 25 s and the copy and verify about 13 s for a file of this size (INFERRED: section 4), and the page is silent between the lines.')
    return (*d7.page_legend(m1, box_s, real), *(line for text in extra for line in m1t.wrap(text, 3, 5)))


def new_text(panel):
    """WHAT IS NEW: rung 8's form, with the root and its pair."""
    d = panel['rung9']['diff']
    return (f'WHAT IS NEW beyond rung 8 (record {r9.BOOT28_RECORD}, boot 28: section 0a). The monitor (053a239f..., inside SNAPMON-M1-ACK.BIN), the reporter\'s lines, the panel, serial=2, the three store classes, '
            f'let_panic, the acknowledge, the PMU\'s clock and the device tree ({d["nodes"][1]} of {d["nodes"][0]} nodes) are rung 8\'s: the acknowledge is EXPERIMENTAL and not a model of the hardware, NO CPU IS STARTED and '
            f'the three stores are dropped (section 3c). WHAT CHANGES: (1) THE PAIR, the root-carry pair (the wrapper accepts a file of 2 GiB and a box of up to 1200 s, of which this record asks {panel["run"]["box_s"]} s); (2) THE RUNNER, the big-file '
            'one (Q1N1-PAYLOAD.EFI af76e0bf...), which reads GUEST.DAT, 1.9 GB, in 1 MiB requests and hashes it before anything is called; (3) GUEST.DAT: 6,144 MiB of RAM, Apple\'s 1.86 GB root as the RAM '
            f'disk ({d["root"]["chunks"]} of its {d["count"]} chunks), the root\'s /chosen properties, the static trust cache, cpu_boot_timeout_secs={r9.CPU_WAIT_S} and acm_fastsim=1 (the guest profile quiet-rtc-acm); '
            f'(4) THE BOX, {panel["run"]["box_s"]} s (D5\'\'\': section 0). NOTHING ELSE changes: no register, no system-register class, no store class, no host link, no write to the stick.')


def fixed_text(panel):
    """WHAT DOES NOT CHANGE: the rules of every record of this kind that a 1.9 GB file might be thought to loosen."""
    return ('WHAT DOES NOT CHANGE. THE STICK IS READ ONLY BY THE RUNNER: nothing of this record writes to it but the Shell\'s marker file (section 0b), no disk is written and there is no host link or cable (section '
            '5c). THE STORE CLASSES ARE THOSE OF EVERY RECORD OF THIS KIND, ram, el2_sysreg and gic as section 3 lists them, and no other (D4: PIN.DAT authorised_stores stays 7); the 1.9 GB is carried by the wrapper\'s RAM '
            'copy inside the placement and by the runner\'s own reads, neither of which is a new class. AC POWER IS REQUIRED for the whole run, the carry included, and one CPU is loaded for all of it (section 4). '
            'THIS APPROVAL COVERS ONE BOOT (section 5c): PAYLOAD CALL 0 spends it, a restart or a reset before the report is NOT A RESULT for M1 (section 5a) and the boot is not retried; a boot that did not print '
            'PAYLOAD CALL 0 is repeated only with the same files, after the coordinator has found the cause.')


def aim_text(panel):
    """THE AIM: the one question of this rung, the operator's aim and the stages of the mount that the classes follow."""
    return ('THE AIM. One question, and one film answers it: how far does the stock kernel get with a root to mount? The operator\'s aim is to see macOS boot, even slowly; this boot is the smallest one that can '
            'show how far it goes, and it is not expected to show a desktop. The stages, in the order the kernel meets them (INFERRED from names in the kernelcache read on the host and from the order in which '
            'the A16 route met its own failures: the volume\'s UUID, then AES, then the ISC; nothing of this has run on the unit): (0) THE CARRY, before the guest: the runner\'s progress lines, then a page that stands still, section below. (1) THE START: '
            'boot 27\'s text, with the clock\'s lines if the clock takes (rung 8\'s question, whose result is not in this text: section 0a). (2) THE MEDIA: bsd_init asks IOKit for a root device; the RAM disk named in /chosen becomes an IOMedia, and '
            'the wait that boot 27 printed, "Waiting on <dict>" for an IOMedia of content Apple_HFS, is replaced by a wait for the volume\'s UUID (a dict that names IOResources and boot-uuid-media: INFERRED from a '
            'literal of the kernelcache). (3) THE IMPORT: the APFS driver probes the raw container (the image has no partition map: INFERRED) and imports the root hash; the A16\'s first raw root stopped in a '
            'PANIC here (INFERRED from the A16 notes). (4) THE SERVICES: the kernel\'s AES, credential and SEP waits (the credential manager reads acm_fastsim and sepfw-load-at-boot together); a silent root '
            'wait if one blocks. (5) THE MOUNT: the line "BSD root:" and the exec of the first user process, launchd, which is the first EL0 code of the guest and is outside the mount: the monitor\'s user-address-space '
            'path has never run on the unit (UNKNOWN). (6) USERSPACE: the first user process waits for the volumes of the ISC, which this image does not carry (INFERRED): beyond this record. The film names the '
            'stage that the kernel reached and the last line it printed there.')


def carry_text(panel):
    """THE CARRY: what the operator sees and waits for from the Shell's countdown to the MON CALL line, and what each refusal would be."""
    lines = panel['rung9']['guest_bytes'] // (r9.PROGRESS_MIB << 20)           # the full 64 MiB slices of GUEST.DAT: one READ line and one HASH line for each
    of = -(-panel['rung9']['guest_bytes'] // (1 << 20))                        # the OF= of the READ lines: the file's size in whole MiB
    wait = r9.CARRY_READ_S + r9.CARRY_HASH_S
    return ('THE CARRY (INFERRED: nothing of it has run; the numbers are estimates and not measurements; the lines and their order are read on the host in the runner\'s source, q1n1 9ea1bdc, a file and not a run). '
            'WHAT THE OPERATOR SEES, IN ORDER, from the Shell\'s AUTORUN line. (1) THE READ, about ' + f'{r9.CARRY_READ_S} s: the runner reads the five files in turn, GUEST.DAT (1.9 GB) in requests of 1 MiB, and prints '
            f'nothing for the four small ones. For GUEST.DAT it prints PAYLOAD READ INDEX=2 MIB=64 OF={of} and one such line for each further 64 MiB read ({lines} lines, MIB=64 to {lines * r9.PROGRESS_MIB}): they go to the '
            f'same text console as the Shell\'s lines and the runner\'s other PAYLOAD lines, so they are INFERRED to be on the panel, scrolling its text up, one about every {r9.READ_GAP_S} s at an ASSUMED 24 MiB/s '
            '(UNKNOWN: boots 8 and 9 measured the hash alone, at 194 MiB/s), and whether the panel draws them is UNKNOWN until a run. (2) THE HASH, about ' + f'{r9.CARRY_HASH_S} s: with the whole file in memory the runner '
            f'prints PAYLOAD HASH INDEX=2 MIB=64 and one such line for each further 64 MiB hashed ({lines} lines, about 0.3 s apart at that 194 MiB/s). (3) THE LINES OF THE FILES, TOGETHER: only now, after the last file has been '
            'read and hashed, the runner prints the five lines PAYLOAD LOADED INDEX=0 to INDEX=4 (INDEX=2 is GUEST.DAT, with its digits): no INDEX line is on the screen before them, so the lines of SNAPMON.BIN and SNAPREP.BIN '
            f'are NOT early. After them come PAYLOAD READY, PAYLOAD BIGFILE CHUNK=... and the SP11 EBS lines: about {round(2 * lines + 15, -1)} lines of console text from the first READ line, which scroll the Shell\'s text off. '
            '(4) THE STAND-STILL: the console then stands still while the firmware sweeps the declared ranges and the bounce runs (230 operations of 8 MiB for GUEST.DAT: the time is UNKNOWN and never run), and the page is '
            'drawn again, dark with its own text: the replayed PAYLOAD LOADED, READY, BIGFILE and EBS lines (the READ and HASH lines are not replayed), then PAYLOAD VERIFY=OK and PAYLOAD CALL 0, which spends the approval '
            '(section 5c). (5) THE WRAPPER: its lines to STAGE=PIN_OK, a pause of about 25 s (its digests of the file), STAGE=GUEST_OK, STAGE=COPYING, about 13 s (the copy and the verify), STAGE=COPIED, the GIC arm\'s '
            f'announcement and its 8 s, and MON CALL. IF THE PANEL DRAWS NO PROGRESS LINE (UNKNOWN), the console shows the Shell\'s text and nothing new for about {wait} s, then the five PAYLOAD LOADED lines: that is as '
            'normal as a console that scrolls, and so is a dark panel with nothing else changed (UNKNOWN what the firmware does with the display during a read of this length): a dark panel at 100 s is NOT a failure and NOT a '
            f'result, and only the waits below end the run. WHAT A STALL LOOKS LIKE: a new READ line is due about every {r9.READ_GAP_S} s and a HASH line about every 0.3 s; if the lines are shown and none has come for '
            f'{r9.LINE_WAIT_S} s (about 40 times the gap: a rate under 0.5 MiB/s, which would take over an hour for the file), or, whether they are shown or not, if the page has not changed for {r9.CARRY_WAIT_MIN} minutes '
            f'before PAYLOAD CALL 0, or for {r9.CALL_WAIT_S} s between PAYLOAD CALL 0 and MON CALL (section 5), it is a stall: note the last line (its MIB says how far it had got) and FORCE OFF. These waits are several times '
            'the estimates, operator-safe and not measurements (the stick\'s rate at this size is UNKNOWN: a slower stick makes the lines further apart and the carry longer, which is not a refusal, and FORCE OFF then spends '
            'a boot that did not run the guest, which the coordinator may repeat with the same files: section 5c). WHAT TO FILM is the whole panel from the Shell\'s countdown to 120 s after the SNAPREP page (about '
            f'{r9.FILM_MIN} minutes), with the main camera and no stop for stills in the carry: the READ lines scroll off as the later lines come, so the film, and not a photograph, is the record of them. It must show the '
            'time of the first and of the last PAYLOAD READ line (they measure the stick\'s read), of the first PAYLOAD HASH line, of the five PAYLOAD LOADED lines, of the page after the stand-still (how long the sweep and '
            'the bounce took), of PAYLOAD CALL 0 and of each of the wrapper\'s lines to MON CALL. A REFUSAL OF THE CARRY comes before the guest: nothing of the kernel is on the panel, and its NAME is the result. The names '
            'and what each would mean: ' + '; '.join(f'{name}: {text}' for name, text in CARRY_REFUSALS) + '. UNKNOWN, and never seen: where the firmware places a 1.8 GiB allocation, whether it is one descriptor, '
            'whether the stick\'s USB path reads into memory above 4 GiB, whether an 8 MiB flush operation is safe, whether the firmware\'s 5-minute watchdog is armed during the read (the runner clears it '
            'before the first read, and as always before the bounce), whether the console draws 56 progress lines without slowing the read, and the stick\'s read rate. A RESTART OF THE SURFACE DURING THE CARRY is a result of the '
            'carry and not of the guest: the guest has not run, the approval is spent only if PAYLOAD CALL 0 printed, and the film says when the Shell\'s countdown or the logo came back and what the last line was.')


def expect_text(panel, wait_s):
    """WHAT TO EXPECT: the course of the run from the MON CALL line, in the stages of THE AIM."""
    box_s = panel['run']['box_s']
    return ('WHAT TO EXPECT. As in boots 21 and 24 to 27: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the guest has stopped: '
            f'at the end of the box ({box_s} s) if the kernel waits, sooner on another stop. The first seconds are boot 27\'s: the SPMI parity-error lines of the registers that the clock does not serve, the '
            f'PMU lines and the clock driver\'s start lines ({d8.START_LINES}); THE TIMES OF THIS SECTION ASSUME THAT THE CLOCK TOOK (CASE A of section 0a: rung 8\'s result is not in this text), so with the clock bound the bsd_init block follows within about 10 s of the MON CALL line (INFERRED); '
            'in CASE B (the clock did not take) it comes at about 60 s as in boot 27 (OBSERVED once), every later time of this section is about 50 s later and about 240 s of the box are left for the mount; the '
            'USB "failed to find" lines are expected at about 31 s in either way (INFERRED). WHAT IS NEW comes after the block: the lines of the RAM disk and of the root\'s probing, which the host cannot predict (UNKNOWN), and one of '
            'the courses of the classes below. The wait for the root device that ended boot 27 should NOT come back as boot 27 printed it (an IOMedia of content Apple_HFS): the RAM disk is the IOMedia. A silent '
            'stretch of the log is not a failure by itself: the root device\'s loop of 60 s waits is silent (INFERRED), and so are the AES, credential and SEP waits. All of it is NOT A FAILURE and not a result '
            'of its own. A dark panel is expected for part of the time (the kernel clears it); it is a restart only if the Shell\'s countdown or the runner\'s first lines come back. '
            f'{r2.force_off(wait_s)}. {r2.COUNT.format(wait=wait_s)}')


def predicts_text(panel):
    """WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT, for the root guest."""
    box_s = panel['run']['box_s']
    return ('WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED; no probabilities; none of it has been seen with this guest; read on the host in the kernelcache, the loader and the monitor\'s ELF, a file '
            'and not a run: files/root-profile-note.md of the session, and boots 24 to 27). PREDICTION: the carry ends with PAYLOAD CALL 0 and MON CALL about two minutes after the first PAYLOAD READ line; the '
            'kernel goes as in boot 27 through its first seconds; the RAM disk replaces the wait of boot 27 by a wait for the volume\'s UUID or by the import of the root hash, which is a panic or a wait '
            '(the A16\'s first raw root panicked there: INFERRED); the most likely ends, in no order of likelihood that this text claims, are a PANIC TEXT of the import or of the mount (a literal of the kernelcache '
            'names the root hash, the seal of the system volume and "cannot mount root"), a silent wait that stands to the end of the box (the media, the UUID, AES, credential or SEP), or, if everything '
            'goes, "BSD root:" and the first user process, which the monitor\'s user path has never run and may stop at (UNKNOWN). THE CLEAN END THAT THIS RECORD CAN GIVE: MON STOP WATCHDOG at the end of the '
            f'box ({box_s} s after the call), with the last line of the log a wait or "BSD root:" with launchd\'s lines, wdt=-1 keeping XNU\'s own watchdog off. XNU\'s own panic for the CPU that never comes, "cpu 1 failed to '
            f'boot for the first time", would come about {r9.CPU_PANIC_AT_S} s after the call (INFERRED), {r9.CPU_PANIC_AT_S - box_s} s after the box: it is not expected inside the box, and one there would mean that the margin was wrong '
            '(class PANIC TEXT). FALSIFIERS: the wait of boot 27 (an IOMedia of content Apple_HFS) as the last line of the log, which means that the /chosen data of the RAM disk did not take (the kernel did not see '
            'the UUID: INFERRED), a guest that stops before the call returns to the page, and: the carry taking more than the waits of section 5 (a finding about the stick); a MON STOP S2_FAULT with '
            f'{r5.WINDOW} {r5.FALSIFIER_ACK}; the ApplePMGR assertion again; a MON STOP S2_FAULT in the window of a node that the profile withdrew; a MON STOP UNNAMED with B= in {clock_window_text()}, the clock model\'s '
            'window (the model refused an access: NEW STOP, section 5d); a MON STOP DEVICE_RESET (class WD1); a MON STOP of any other kind. THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE are a result in every class that has no panic text.')


def filming_text(panel, wait_s):
    """THE FILM: rung 7's, with the carry in it."""
    box_s = panel['run']['box_s']
    return (f'THE FILM is rung 8\'s, from the Shell\'s countdown through THE CARRY (about two minutes: the runner\'s progress lines and then a still page, which the film times) and the box until {r5.FILM_AFTER_S} seconds after the SNAPREP report page has '
            f'appeared, or, if it never appears, until the FORCE OFF of step 5 of section 5c ({wait_s} s after the MON CALL line) has been filmed (about {r9.FILM_MIN} minutes in all for this box of {box_s} s): the whole panel '
            'with the main camera, 30 frames a second or more, steady, and if there is a second camera the TOP-LEFT quarter close up (step 2 of section 5c). It must show PAYLOAD CALL 0 (only the film shows it once '
            'the kernel has cleared the panel: if it does not, the approval is spent), the runner\'s lines and the time of each, THE TIMES OF THE FIRST AND OF THE LAST PAYLOAD READ LINE above all (they measure the read of '
            'the stick) and of the five PAYLOAD LOADED lines, the '
            'wrapper\'s lines to MON CALL, the kernel\'s log with its time, frame by frame from the MON CALL line to 20 s after it and then at the pace of its changes (the clock driver\'s lines about 7 s in, the '
            'time of the bsd_init block and THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE are results in every class that has no panic text), any panic text and the report with MON STOP, MON EXIT and the MON '
            'lines. THE PANIC TEXT, if there is one, is the result and may scroll off the part of the panel left of the report page (about its left 55 percent) (INFERRED): nobody touches the keyboard, the screen or '
            'the buttons while it runs (except FORCE OFF as step 5 of section 5c says). After those '
            f'{r5.FILM_AFTER_S} seconds, and BEFORE FORCE OFF (only when the SNAPREP page is on the screen; if it is not, FORCE OFF {wait_s} seconds after the MON CALL line as step 5 says, no stills first), take three sharp '
            'still photographs of the part of the panel left of the report page, close and without glare, one of its top band, one of its middle and one of its bottom, and a fourth of the whole report page (it has up '
            'to 40 lines and its MON TIM, MON CNT and MON ACK lines are read with the film: asked, not required, and valuable). Lowercase letters on the panel are the kernel\'s (boots 21, 23, 24, 25, 26 and 27: '
            'OBSERVED once each).')


def decides_text():
    """WHAT THE FILM AND THE PAGE DECIDE: the carry, the panel, the log to its last line with the mount's questions, then the page, with boots 21 and 24 to 27 as the comparison."""
    return ('WHAT THE FILM AND THE PAGE DECIDE (what this record changes has not been seen; boots 21 and 24 to 27 are the comparison, OBSERVED once each). First the carry: the runner\'s and the wrapper\'s lines and the time '
            'of each, from the film. Then the panel: the coordinator transcribes the first lines of any panic text from the film and the stills, and the classes below read from it. Then the kernel\'s log, replayed '
            'from its start by the first frames after the MON CALL line and read to its LAST LINE and the time of that line on the film\'s clock, with THE MOUNT QUESTIONS answered from it, each with its time: '
            '(1) the clock driver\'s lines and the time of the bsd_init block, as for rung 8 (boot 27: about 60 s; predicted within about 10 s); (2) the last line that names a root device: is it boot 27\'s wait '
            '(an IOMedia of content Apple_HFS), a wait that names IOResources and boot-uuid-media, or another dict (transcribed whole), and when; (3) any line about the RAM disk, the root hash, the volume, AES, a '
            'credential or the SEP, and any panic text; (4) the line "BSD root:" and what follows it (launchd\'s lines or none); (5) the last line of the log and the time of its last change. With six CPUs the '
            'processor lines of boot 21\'s log are expected again and are not the failure. Then the report page: '
            f'boot 25\'s stop was {d7.STOP25} at the end of its box, boot 26\'s was {d7.STOP26} (a write at the scaler\'s window), boot 27\'s was {STOP27} at the end of its box (SPSR {r8.BOOT27_SPSR}: EL1 with every '
            'interrupt masked, idle INFERRED), and the CPU-start stop is a MON STOP S2_FAULT with '
            f'{r5.WINDOW} (a read there, MON ESR saying WNR 0, would be new and unexpected). An S2_FAULT at another address is progress, and a MON STOP UNNAMED with B= in the clock\'s window is a finding about the clock model. '
            'The MON RET TICKS= of any stop that is not WATCHDOG says its time on the monitor\'s clock (19.2 MHz). '
            'NOT THE FAILURE: ' + NOT_THE_FAILURE + '.')


def panic_text():
    """WHAT A PANIC LOOKS LIKE: rung 8's words, with the panics that a root can give."""
    return r8.patched(d8.panic_text(), ('is the class RUNG 8 PANIC TEXT below', 'is the class RUNG 9 PANIC TEXT below'),
                      ('with the word at 120 it is expected about 122 s in, outside this record\'s box by a margin of about 2 s',
                       f'with the word at {r9.CPU_WAIT_S} it is expected about {r9.CPU_PANIC_AT_S} s in, {r9.CPU_PANIC_AT_S - r9.BOX_S} s after this record\'s box')
                      ) + (' A root can give panics of its own (INFERRED from literals of the kernelcache, read on the host): one that names the root hash or the seal of the system volume ("ssv" '
                           'in the text), one that says that the root cannot be mounted ("mount" with an error number) and one of a driver that needs the SEP or the AES engine; each is a text to '
                           'transcribe whole, because its wording is the result.')


def results_paragraphs(panel, run, wait_s):
    """The results of rung 9, one paragraph each, in the operator's words, read in this order from the film of either camera, the stills and the page: the first class that fits is the film's."""
    box_s = run['box_s']
    return (
        'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 9\'s. The carry is read first, then the panel, then the stop, and for a stop at the end of the box the last line of the log '
        'and the time of its last change; beside every class that has kernel text the clock\'s answers are read as rung 8\'s record reads them (the line `AppleARMRTC registering service!`, no parity error at '
        f'{CLOCK_ADDRESSES} after the clock\'s start lines, and the time of the bsd_init block: within about 10 s if the clock took, at about 60 s if it did not); a film is in the first class below that fits, in this order, and every film fits one. The aim of this rung is the answer to ONE question, how far the mount goes: the '
        'classes MOUNTED, WAITING FOR THE MEDIA, SILENT WAIT and PANIC TEXT read it, each as a stage of THE AIM, and a stop of any kind is read for what it is. CARRY STOPPED, NOT A RESULT and the two NOT PASSED '
        'classes are the failures of the record: nothing is learnt about the mount, or the record did not reach its aim.',
        'RUNG 9 CARRY STOPPED: the run ended before the guest ran, and no kernel text is on the panel: a PAYLOAD REFUSED line of the runner or a SNAPMON REFUSED= line of the wrapper (the NAMES are in THE CARRY, '
        'each with what it would mean), or a restart of the Surface, or a page that stopped changing for the waits of section 5 during the carry (no line names it; a dark panel by itself is not this class: THE CARRY). THE RESULT IS THE NAME OR THE LAST LINE AND ITS TIME: the coordinator '
        'transcribes the refusal verbatim (name, INDEX, EFI, GOT, WANT, the wrapper\'s numbers) and says what it can of the cause from THE CARRY\'s UNKNOWNs (where the firmware places 1.8 GiB, one descriptor or '
        'several, the stick\'s rate, the memory above 4 GiB, an 8 MiB flush, the firmware watchdog); the stage of the carry that the last line was in is the finding (the MIB of a PAYLOAD READ or HASH line says how far it had got). The approval is spent only if PAYLOAD CALL 0 '
        'printed (section 5c); a refusal before it ran nothing of M1, and the coordinator repeats only after finding the cause. The guest was not tested: nothing is concluded about the mount.',
        'RUNG 9 PANIC TEXT: the panel shows a panic text, whatever the page says and whatever the stop is: nested panics and boot 24\'s end, MON STOP S2_FAULT at a read in the kernel\'s chip-reset path, follow '
        'within about a second and are a later stop and not a result of their own. THE TEXT IS THE RESULT: the coordinator transcribes its first lines verbatim from the film and the stills and hands them back. It '
        'is the stage that the text names (the root hash, the seal, the mount, a driver) that answers the aim. It is also a falsifier if it is the ApplePMGR assertion of boot 24 again, or "cpu 1 failed to boot for the '
        f'first time, waited N seconds" inside the box (it is expected about {r9.CPU_PANIC_AT_S} s in, with N={r9.CPU_WAIT_S}, after the box).',
        'RUNG 9 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP (too soft, too dark, the report unreadable under the kernel\'s text, the text too small to read), or '
        'time the carry and read the clock driver\'s lines and the last lines of the log: nothing is concluded from it, and the one-boot rule of section 5c says whether the approval is spent.',
        f'RUNG 9 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with {r5.WINDOW} again: the acknowledge did not take. Either the access is one that the monitor does not take by design (a read, another '
        'width, a syndrome that does not describe it: section 3c) or it is a fault of the monitor: MON ESR and MON WVAL on the page and B= are the syndrome, and the coordinator decodes it before saying which. A '
        'read there would be new and unexpected: nothing after the write reads (section 3c).',
        'RUNG 9 WD1: no panic text and MON STOP DEVICE_RESET: a device model of the monitor asked for a reset of the guest, and the guest\'s virtual watchdog, WD1, is INFERRED to be the one (A= and B= are the two '
        'words of the bitmap of the models that asked: probes/xnuboot/clock_timer.c:245-253 and report_layout.c:170, read on the host). WD1 stops the run only when the guest armed it (control bit 2) and its u32 '
        'counter, which counts the guest\'s clock of 24 MHz, has reached the threshold that the guest wrote (compat/watchdog.c:12-23, probes/guestprofile.py:323-325, read on the host): its time is the guest\'s and no '
        f'part of the class (a wrap alone is {WRAP_S:.2f} s, about {WRAP_TICKS:,} ticks of the monitor\'s 19.2 MHz clock, and stops nothing: the model compares before it reduces the counter to 32 bits). '
        'wdt=-1 is INFERRED to leave WD1 unarmed (the A16 ran 600 to 1,200 s on this code: a document, not a run); only 120.2 s has been run, so a reset is UNTESTED here, and this box of 300 s is the first to span the '
        f'wrap: if the guest did not arm WD1 the page shows nothing at {WRAP_S:.2f} s and the run goes on to MON STOP WATCHDOG at the end of the box. THE RESULT IS THAT THE RUN WAS CUT BY A DEVICE MODEL\'S RESET: '
        'the last line of the log and its time say how far the kernel had got, and the cure is a change of the guest\'s watchdog and not of the mount (a finding to hand back, with the page).',
        'RUNG 9 MOUNTED: the line "BSD root:" is on the film (the kernel announced its root) and there is no panic text. THE MOUNT WORKED (INFERRED to be what the line means; the film is the witness). THE RESULTS ARE, '
        'each with its time: (1) the time of "BSD root:"; (2) what follows it: launchd\'s own lines or the kernel\'s lines about the first user process, or nothing; (3) the stop, or, after a restart with no report, nothing: say so; '
        'M1\'s own verdict stays 5a\'s. A MON STOP of any kind but WATCHDOG '
        'after it, S2_FAULT, UNNAMED, GUEST_EXC, UNDEF or an SPTM refusal, is THE FIRST EL0 (the first user-mode code of the guest): its kind, PC=, A= and the page\'s MON ESR lines are the result, and the monitor\'s '
        'user-address-space path, which the six `control` machinery serves and which has never run on the unit, is the next work (INFERRED). MON STOP WATCHDOG at the end of the box with launchd\'s lines '
        'is a kernel that mounted, started its first process and waits: the best end that this record can have. A film that shows "BSD root:" and a later panic text is PANIC TEXT, above.',
        'RUNG 9 NEW STOP: no panic text, no "BSD root:" and a MON STOP of any kind but WATCHDOG (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, GUEST_EXC or another name) that is not the S2_FAULT of NOT PASSED '
        '(a) and not the DEVICE_RESET of WD1: its kind, PC=, A=, the page\'s MON ESR, MON WVAL and MON GX lines, its MON RET TICKS= and the last line of the log with its time are the result, and the coordinator reads them first, '
        'with the MON TIM line. Two readings of an S2_FAULT and one of an UNNAMED stop are named. THE CLOCK MODEL REFUSED AN ACCESS: MON STOP UNNAMED with B= in '
        f'{clock_window_text()}, the window of the clock model, and A= 0X30 and eight more hex digits, the last of which is the device result (INFERRED, read on the host in probes/xnuboot/mmio_dispatch.c: the reporter does '
        'not name a device model\'s refusal): the guest touched a register or a width that the model does not take, or the model was not started (UNKNOWN which): a finding about the model, read with the clock\'s lines on the film. '
        f'THE PROFILE DID NOT TAKE: A= lies in {win.for_new_stop()}: the kernel attached to a node that '
        'this tree no longer lists; boot 27 ran this tree for 120 s without such a stop (OBSERVED once), so it would be new. THE STOP MOVED: an S2_FAULT with A= anywhere else, in the window of a device that was not '
        'withdrawn or that the mount now reaches (the coordinator names it from A= and PC= and the guest\'s tree; the likeliest are the AES engine, the storage-related nodes, then JPEG, AVE and the display chain: '
        'INFERRED): progress, and a new question, which model or withdrawal that device needs (UNKNOWN). An S2_FAULT elsewhere in the CPU-start block, outside 0X300734004..0X30073400C, is the monitor failing closed '
        'on an access it does not take; inside the panel range (section 2) it is not M1\'s expected end (section 5a). A stop of another kind is a finding to read, with the last line of the log.',
        f'RUNG 9 WAITING FOR THE MEDIA: MON STOP WATCHDOG at the end of the {box_s} s box, no panic text, no "BSD root:", and the last line of the log is a "Waiting on <dict>" for a root device that stands to the end. '
        'THE RESULT IS THE DICT, transcribed whole, with the time it appeared. Three readings: (1) the dict of boot 27 (an IOMedia of content Apple_HFS): the RAM-disk data of /chosen did not take, the kernel did not '
        'see the UUID or the extents (INFERRED), and nothing of the root was tried: a finding about the data; (2) a dict that names IOResources and boot-uuid-media: the kernel took the UUID path and waits for the '
        'volume\'s media, which the RAM disk\'s APFS probe did not publish (INFERRED; the A16\'s class 1: the media or the volume UUID is not published): the stage is THE MEDIA/THE IMPORT, and the cause (no partition '
        'map, a driver that does not match the raw container, the UUID) is UNKNOWN; (3) another dict: transcribed, and read from its words. MON STOP WATCHDOG, no new text, and no such line is SILENT WAIT.',
        f'RUNG 9 SILENT WAIT: MON STOP WATCHDOG at the end of the {box_s} s box, no panic text, no "BSD root:" and no "Waiting on <dict>" line standing at the end: the log was silent for the last stretch (say how long, '
        'from the time of its last change). THE RESULT IS THE LAST LINE AND ITS TIME, with the guess that the film allows (INFERRED, in the order of the A16 route\'s failures: an AES wait, a credential or SEP wait that '
        'acm_fastsim=1 did not remove, the root\'s volume import, or a loop of the kernel\'s own that is silent). MON TIM says whether the timer is delivered (VECTORS above 0 is the normal page of a waiting '
        'kernel, as in boots 26 and 27; CVAL less 1.25 times LAST of the MON CLK line, in ticks of 24 MHz, places the kernel\'s armed deadline: INFERRED). A page with VECTORS 0 would contradict boots 26 and 27 and is '
        'itself a finding. The MON ACK, MON CNT and MON CON lines are findings to read, not classes. The next record adds the layer that the last line points at (AES, SEP or the others that the note lists).',
        f'RUNG 9 NOT PASSED, (b): a restart of the Surface before the report and after the carry, no report (a page that stops at MON CALL, a REFUSED line after it, MON PREPARE FAILED, a fault page of q1n1), the panel '
        f'still dark {wait_s} seconds after the MON CALL line, or any other end that none of the results above names, and no panic text: a restart is NOT A RESULT for M1 (section 5a) and NOT PASSED for rung 9, which '
        'has not reached its aim.')


def section_5d(panel, wait_s):
    """Section 5d of the record of rung 9, after 5c, in the operator's words. `wait_s` is the silence rule's wait for the box (the guide's silence_s)."""
    box_s = panel['run']['box_s']
    texts = (new_text(panel), fixed_text(panel), aim_text(panel), carry_text(panel), expect_text(panel, wait_s), predicts_text(panel), filming_text(panel, wait_s), d7.page_text(), decides_text(), panic_text(),
             *results_paragraphs(panel, panel['run'], wait_s))
    return (heading(box_s), *(line for text in texts for line in m1t.wrap(text, 3, 6)))
