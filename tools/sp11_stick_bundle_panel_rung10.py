# SPDX-License-Identifier: MIT
"""Rung 10 of the monitor entry run (docs/SP11-STICK-ROOT.md section 11): rung 9's form with the ISC. Boot 28 ran rung 9's record (dd2f39f4...b6b, commit b455ce2): the carry of the 1.93 GB
GUEST.DAT worked, the stock kernel mounted the 1.86 GB RAM-disk root and ran /sbin/launchd, the first EL0 code of the guest, and launchd's first boot task, mount-phase-1, failed (files/result-boot28.md of the
session; the keyed block RUNG9 of section 0a). The host reading of the BaseSystem image (files/boottasks-note.md: a file read on the host, INFERRED) says that the task's mandatory step is the xART volume of a second
APFS container, the ISC, which it finds by the GPT partition type. This record changes ONE thing, in GUEST.DAT and as data: the RAM disk is a GPT medium of the root and the ISC (tools/sp11_stick_bundle_rung10.py).

`--cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=360 --panel console --time-box-s 300 --guest-profile j700ap-26A428-m1-quiet-rtc-acm --bigfile --ramdisk ... --root-hash ... --trust-cache ...
--isc-image ... --sptm-hvc-calls 0` is the one combination that builds it: rung 9's form (tools/sp11_stick_bundle_chunks.py RUNG9_FORM, the pair of ACK_PAIRS for this profile) with the ISC image, the facts'
build_label RUNG10 and the label M1RUN-ACKPANIC-M-RUNG10. The guide's table PANEL takes its words by the key `console+cpu_boot_timeout_secs=360+let_panic+j700ap-26A428-m1-quiet-rtc-acm+isc`
(tools/sp11_stick_bundle_panel_rungs.py, kind_key). The words that it shares with rung 9 are tools/sp11_stick_bundle_panel_rung9.py's, by attribute (a hook that this module does not define is rung 9's, and then
rung 8's), and where a sentence of rung 9's is kept with a change, `patched` asserts that it is in rung 9's words exactly once, so that a change of those words cannot go unnoticed here. Section 5d is
tools/sp11_stick_bundle_panel_rung10d.py's. Every sentence that says what this guest is against rung 9's is made from the builder's own comparison (tools/sp11_stick_bundle_rung10.py, `compare`).

Evidence: EXPERIMENTAL, hardware NOT RUN. The keyed block of boot 28 is the coordinator's reading of two films and of the stick, OBSERVED once; the host reading of the image is INFERRED."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung8 as r8  # noqa: E402
import sp11_stick_bundle_panel_rung9 as r9  # noqa: E402
import sp11_stick_bundle_rung10 as r10b  # noqa: E402

NUMBER = 10                                 # the rung's number, for the pointer to section 5d in the reading lines (tools/sp11_stick_bundle_panel.py)
PROFILE = ch.RUNG9_PROFILE                  # j700ap-26A428-m1-quiet-rtc-acm
CPU_WAIT_S, BOX_S = r9.CPU_WAIT_S, r9.BOX_S
patched = r9.patched
BOOT28_RECORD = f'{r10b.RUNG9_RECORD[:8]}...{r10b.RUNG9_RECORD[-3:]}'
STAND_HEAD = '0a. Where things stand (boots 20, 21, 23 to 28; one unit, one boot each; OBSERVED unless INFERRED)'
HEADER = ('3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic, a wait of 360 s, the quiet guest with the PMU\'s clock bound and a RAM disk that holds the root and an ISC '
          '(rung 10: D9 panel handed, serial=2, cpu_boot_timeout_secs=360, let_panic set, guest profile quiet-rtc-acm, the root-carry pair, stock kernel; docs/SP11-STICK-MONITOR.md section 20)')
VERDICTS = r9.VERDICTS
CARRY_WAIT_MIN = 4

# KEYED BLOCK of boot 28 (rung 9: the first root mount). The coordinator's reading of the operator's two films and of the stick read back (files/result-boot28.md of the session); every line quoted is
# transcribed from a frame of the film, and what the films did not show is said to be UNKNOWN.
RUNG9 = dict(record=BOOT28_RECORD, boot='boot 28', verdict='PANIC TEXT', words=(
    'THE STOCK KERNEL MOUNTED THE 1.86 GB RAM-DISK ROOT, RAN /sbin/launchd, AND LAUNCHD\'S FIRST BOOT TASK, mount-phase-1, FAILED: THIS IS THE FIRST USERSPACE (EL0) CODE EVER RUN ON THE SURFACE PRO 11 UNDER THE MONITOR '
    '(OBSERVED once). The films are the operator\'s two films of the panel, the first of the runner\'s whole read and hash and the first 40 s of the wrapper\'s silent phase, the second from about 1.5 s after launchd\'s start to 5 s after the report page: THE MIDDLE, the end of the wrapper and the kernel\'s whole log up to launchd, IS ON NEITHER FILM (UNKNOWN). The stick as it was read back after '
    'the boot: GUEST.DAT b7aeddff..., PIN.DAT 68204251..., PAYLOAD.MAN 2868960b..., REPORT.DAT 2af25e8a... (all-zero, as staged), SNAPMON 6af26a84..., SNAPREP 562b9b6e..., the runner af76e0bf..., and the Shell\'s marker '
    'SP11\\DONE1.TXT (47 bytes) as the only new file. M1 PASSED by section 5a. The carry is in section 5d (THE CARRY): the wrapper\'s placement, digests and copy took 26.1 s from its entry (OBSERVED once).',
    'THE GUEST (OBSERVED once; lines transcribed from the film). `load_init_program: attempting to load /sbin/launchd`, among the noise of the missing SEP and Image4 services; libignition\'s `Darwin Ignition Sequence Version 1.0.0` (`root ramdisk: 0x0`, `rootfs mount flags: 0x480d201`, `volume group uuid: A9037730-2821-4F3A-98DC-91BA521BB000`, `hardware target: j700ap`, `stage count: 0x3`, `goodbye: ignition sequence complete`); dyld\'s `dyld cache mapped system-wide: development, auth GOTs: mapped`; launchd\'s `hello, launchd`, `Darwin Bootstrapper Version 7.0.0`, `boot-args = -v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpu_boot_timeout_secs=360 acm_fastsim=1`, `BaseSystem environment starting.`, `System Integrity Protection is engaged.`, `(exclaves-boot) Doing boot task`, `(fsck) Skipping boot-task` and `(mount-phase-1) Doing boot task`. launchd\'s date was 2026-11-20 00:00:04, the clock model\'s placeholder seed plus 4 s: THE CLOCK MODEL TOOK (OBSERVED once: launchd\'s date), and IORTC was published (INFERRED from it; the clock was the question of '
    'rung 8\'s record, whose own boot is not in this text). The monitor\'s user path (the monitor of rungs 5 to 8, unchanged) ran launchd, dyld and the shared cache: the six `control` defines of the A16 monitor were not '
    'needed to get here (OBSERVED once).',
    'THE FAILURE (OBSERVED once; the right edge of the panel is cut off in the second film): `mount_by_role:451: failed to find volume with role = 10 in APFS container with volcnt = 0 for root device /dev/disk1s1 with status 49245`, `mount_by_role_if_present:556: No volume with role 16 present on /dev/disk1s1.`, `mount_by_role:451: failed to find volume with role = c0 ... status 49245`, `mount_phase_one:1914: Update volume mount failed - error c05d (errno 93). Ignoring...`, then ten rounds, about 3.2 s apart and about 30 s in all, of `wait_for_node:266: APFS IOKit node lookup timed out...` and `wait_for_node:272: Retrying (9 retries left)...` down to 1, interleaved with the USB `failed to find` lines, then `Boot task failed: mount-phase-1`, `Panicking in 3 seconds.` and `panic(cpu 0 caller 0xfffffe000c246d2c): userspace panic: boot task failure: mount-phase-1 - exited due to SIGABRT`, then `Nested panic detected - entry count: 4 panic_caller: 0xfffffe000c245398` (in the RTBuddy path), `Nested panic count exceeds limit 5, machine will reset or spin` and `wdog: panic chipreset`. The roles are APFS role words (INFERRED from the numbers): 0x10 is '
    'Preboot and 0xC0 is Update.',
    'THE PAGE (OBSERVED once): `MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=769573015` (40.082 s at 19.2 MHz), `GIC RESTORE CLEAN=YES`, `STAGE=DONE`, `VERDICTS PIN=OK GUEST=OK POLICY=0X3 LINES=29`, `MON COUNTS EVENTS=512 TRAPS=648755 ABORTS=0`, `MON STOP S2_FAULT PC=0XFFFFFE0008BA7358 A=0X3082BC...` (the digits after BC are cut off by the camera; boot 24\'s was A=0X3082BC224, INFERRED to be the same read), `MON ESR EC=0X24 ISV=1 SIZE=4 SRT=10 WNR=0` (a 32-bit read), SPSR=0X604003C5, `MON TIM IRQS=6525 ASSERTS=12376 CLEARS=12375`, `MON CNT MAPS=79342 MAPFAIL=0 DISP=0 PANIC=13` and `MON ACK COUNT=15`. The S2_FAULT is the kernel\'s chip-reset path after a panic, as in boot 24 (INFERRED): a later stop, and not a result '
    'of its own.',
    'WHAT IT SETTLES (OBSERVED once, one unit, one boot): the stock kernel mounts a 1.86 GB APFS root as a RAM disk under the monitor, with acm_fastsim=1, no SEP and no ISC, and runs launchd; the carry works; the clock '
    'model works. The host reading of why the task failed is in section 5d (THE AIM, INFERRED). UNKNOWN: what the kernel printed from the MON CALL line to launchd; the digits cut '
    'off the page; whether EMPTY volumes and a byte-stamped xART role satisfy the task; what the boot tasks after it do on this unit.',))
RUNG8_NOTE = ('Rung 8 (record 54c1dff7...c717b): its own boot is not in this text. The quiet guest with the PMU\'s clock bound ran inside rung 9\'s boot (boot 28), where the clock took (above, OBSERVED once); '
              'nothing here approves or assumes rung 8\'s approval.')
STICK_NOTE = ('The stick after boots 21, 23, 24, 25, 26, 27 and 28 was unchanged but for the Shell\'s marker (and, after boots 21, 23 and 24, the Mac\'s own .fseventsd entry), and REPORT.DAT kept its all-zero staged '
              'digest: a run does not write the report back, so the page on the panel is its only record (OBSERVED once each; boots 26\'s, 27\'s and 28\'s by a read on the host of the stick as returned).')
BLOCKS = {**r9.BLOCKS, 'Rung 9': RUNG9}
BLOCKS.pop('Rung 8')


# ---- section 0 ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
DECIDES = ('APPROVING THIS TEXT ALSO DECIDES FOUR THINGS. (1) D5\'\'\': A TIME BOX OF 300 s, as the record of rung 9 asked. D5 and D8 decided 5 s, D5\' 60 s, for rung 6 the operator chose 120 s in the app on 2026-11-19 (D5\'\', '
           '"let\'s go with 120 seconds"), and rung 9\'s approval took 300 s for boot 28, whose guest ran 40.08 s of it and ended at a panic (OBSERVED once): a run of 300 s has never been spanned. This record asks 300 s '
           'again, so that what comes after launchd\'s first boot task can show how far it goes: AC power REQUIRED, one CPU loaded for up to 300 s after a carry of up to about three minutes (boot 28: OBSERVED once; section 5d), no '
           'temperature readout, and the exposure sums of section 0b and section 4. Whether the Surface restarts inside a stock kernel\'s run of 300 s is UNKNOWN (section 4). (2) A GUEST WITH A ROOT AND AN ISC. The guest is '
           'rung 9\'s (its device tree, 43 nodes withdrawn by data and never faked, its clock, let_panic, six CPUs, none started, 6,144 MiB of RAM, its command line and its trust cache) with ONE change, in GUEST.DAT and as data: its RAM disk is a GPT medium of two partitions, 2,939,158,528 bytes, '
           'where rung 9\'s was the root alone: Apple\'s BaseSystem root (1,862,270,976 bytes, byte for byte rung 9\'s RAM disk) at 1 MiB and the ISC (1,074,790,400 bytes, a three-role APFS container of three EMPTY '
           'volumes, the roles xART, Preboot and Hardware) after it, named in the device tree\'s /chosen by one size and one extent that cover the medium. GUEST.DAT is about 1.93 GB again: the ISC is almost all zeros. '
           '(3) THE ROOT-CARRY PAIR AND THE BIG-FILE RUNNER, which ran in boot 28 and carried the 1.93 GB file (OBSERVED once; section 5d): SNAPMON-M1-ACK.BIN 6af26a84... and SNAPREP-M1-ACK.BIN 562b9b6e... (the monitor inside, '
           '053a239f..., is the one that ran in boots 25 to 28) and Q1N1-PAYLOAD.EFI af76e0bf..., named again and unchanged. (4) WHAT THE GUEST CAN REACH is rung 9\'s: a guest RAM of 6,144 MiB at 0X880000000 with the RAM disk '
           'inside it, with no new register, no new system-register class and no new store class (read on the host in compat/cpu-start-ack.h and the placement arithmetic of the builder, a file and not a run); nothing of this record '
           'writes to the stick but the Shell\'s marker file (section 0b): the runner reads it, and there is no host link; this approval is for that reach for up to the carry and the box.')
RUNG = patched(r9.RUNG, ('each of rungs 1 to 8,', 'each of rungs 1 to 9,'),
               (' and with the guest profile quiet-rtc (rung 8, boot 28), whose records have their own approvals (those of rungs 1 to 7 spent, rung 8\'s its own: this text does not know it): this record is rung 9, the root mount, and no approval covers another\'s boot',
                ', with the guest profile quiet-rtc (rung 8, whose own boot is not in this text) and with the RAM-disk root (rung 9, boot 28: section 0a), whose records have their own approvals (those of rungs 1 to 7 and 9 spent, '
                'rung 8\'s its own: this text does not know it): this record is rung 10, the ISC, and no approval covers another\'s boot'))
BOX_DECISION = ('this record asks 300 s again, as rung 9\'s did, and approving this text decides it again for this boot, with the exposure that section 4 gives, which D8 accepted for a box of 5 s only')
IDLE = ('the extra seconds are there for what comes after launchd\'s first boot task, which no run has reached: the ISC\'s volumes, the boot tasks that follow it (data-protection, restore-datapartition, fips: section 5d) and the '
        'daemons; a guest that waits is idle, and the box ends it')
RESTART = ('Inside a box of 300 s with the stock kernel running, whether the Surface restarts is UNKNOWN: the longest run of the guest so far was boot 27\'s 120.2 s, whose film showed none to its end, 152 s (OBSERVED once); '
           'boot 28 ran a carry of about two minutes (the runner reading the stick and hashing at EL2, then the wrapper) and then 40.08 s of guest without a restart (OBSERVED once), and a run of 300 s is UNTESTED (UNKNOWN: section 4).')
RESTART_CONTROL = ('That control ran no guest; the stock kernel ran inside a box of 120 s once, in boot 27, without a restart to the end of its film, and for 40.08 s after a carry of about two minutes in boot 28 '
                   '(OBSERVED once each). A run of 300 s is UNTESTED, and the argument above does not reach it.')
RAN = ('ran in its acknowledging build four times, for 60 s (boot 25), for 61.6 s (boot 26), for 120.2 s (boot 27: section 0a) and for 40.08 s (boot 28, as the root-carry pair), and in its plain build in boots 20, 21, '
       '23 and 24. The wrapper and the reporter that this record names are the root-carry pair, which ran in boot 28 (OBSERVED once).')
CONSOLE = patched(r9.CONSOLE, ('its device tree is the profile quiet\'s plus the RAM-disk root\'s /chosen properties,', 'its device tree is the profile quiet\'s plus the /chosen properties of the RAM disk (a GPT medium of the root and the ISC),'),
                  ('(boots 21, 24, 25, 26 and 27, without a root: the kernel\'s log and panic text were on it, OBSERVED once each)',
                   '(boots 21, 24, 25, 26 and 27, without a root, and boot 28, with one: the kernel\'s log, launchd\'s text and panic text were on it, OBSERVED once each)'),
                  ('what the acknowledge, let_panic and the withdrawn nodes do with the clock bound and a RAM-disk root to mount is INFERRED and has not been seen (boot 27 saw them for 120.2 s without either).',
                   'what the acknowledge, let_panic and the withdrawn nodes do with the ISC in the RAM disk is INFERRED and has not been seen (boot 28 saw them with the clock bound and the root, for 40.08 s, to launchd\'s panic).'))


def why_lines(real):
    """Section 0's paragraph for this record: why rung 10, the change, the prediction in a line and where the rest is."""
    d = real['panel_facts']['rung10']['diff']
    r = real['panel_facts']['rung10']
    m = d['medium']
    return m1t.wrap(f'WHY RUNG 10. Boot 28 (rung 9, record {BOOT28_RECORD}) carried the root, mounted it and ran launchd, whose first boot task, mount-phase-1, failed (section 0a, OBSERVED once). The host reading of the BaseSystem '
                    'image says that the task\'s mandatory step is the xART volume of a second APFS container, the ISC, which it finds by its GPT partition type (INFERRED: section 5d, THE AIM). THIS RECORD GIVES THE RAM DISK AN ISC AND '
                    f'CHANGES NOTHING ELSE: GUEST.DAT is {r["guest_bytes"]:,} bytes (rung 9\'s: {d["guest_bytes"][0]:,}) in {d["count"]} chunks (rung 9\'s: {r10b.RUNG9_CHUNK_COUNT}), of which {m["chunks"]} ({m["zero"]} of them ZERO) are the RAM disk, '
                    f'a GPT medium of {m["bytes"]:,} bytes at 0X{m["dest"]:X} that holds Apple\'s root, byte for byte rung 9\'s RAM disk, at 1 MiB, and the ISC ({d["isc"]["bytes"]:,} bytes, three empty volumes) after it; the other {d["outside"]} '
                    f'chunks are rung 9\'s, {d["outside"] - d["put_back"]} of them byte for byte and {d["put_back"]} (HANDOFF chunk 0, PARAMS and the device tree) with the word or the two properties that name the end of the RAM disk. '
                    'THE MONITOR, THE REPORTER, THE RUNNER, THE CLOCK, THE PROFILE, THE COMMAND LINE, THE PAIR AND THE BOX ARE RUNG 9\'S. THE AIM, THE ORDER OF THE CLASSES, THE PREDICTION, ITS FALSIFIERS AND WHAT TO WAIT FOR AND '
                    'FILM are in section 5d, in the operator\'s words (INFERRED; nothing of this has run with an ISC).')


def stand_extra(real):
    """The paragraphs that section 0a gets after boot 20's: rung 8's (boots 21 and 23 condensed, the keyed blocks of rungs 4 to 7), boot 28's keyed block (RUNG9, the film), a line on rung 8 and the stick."""
    lines = r8.stand_extra(real)
    at = next(i for i, line in enumerate(lines) if line.startswith('   The stick after boots'))
    return (*lines[:at], *r9.keyed_lines(RUNG9, 'Rung 9'), *m1t.wrap(RUNG8_NOTE), *m1t.wrap(STICK_NOTE))


def hazard(real, box_s, wait_s):
    """The panel's bullet of 0b: rung 9's, with the guest's course after the root (boot 28) and what is UNKNOWN beyond launchd's first boot task."""
    return patched(r9.hazard(real, box_s, wait_s),
                   ('(OBSERVED once: boots 21, 24, 25, 26 and 27): from the MON CALL line', '(OBSERVED once: boots 21, 24, 25, 26, 27 and 28): from the MON CALL line'),
                   ('in boot 27 the kernel went on past them for the whole box, through a silence of about 29 s (INFERRED: the second of two 30 s waits for an RTC resource that is never published) into bsd_init and to a wait '
                    'for a root device (OBSERVED once); what it does with the clock bound and a root to mount is UNKNOWN (it may wait for the root, panic in the root-hash import or the mount, wait for a driver that needs an AES, '
                    'credential or SEP service, stop at a device that has no model, or run the first user process and stop there; section 5d).',
                    'in boot 27 the kernel went on past them for the whole box into bsd_init and to a wait for a root device (OBSERVED once), and in boot 28, with the clock bound and a root, to the root, launchd and launchd\'s first '
                    'boot task (OBSERVED once); what it does after that task, with an ISC in the RAM disk, is UNKNOWN (it may pass it and run the tasks that follow, fail the mount of the xART volume, stop at a task that needs a service '
                    'this guest lacks, panic, wait, or run a daemon that reaches a device that has no model and stop there; section 5d).'),
                   ('which boot 24 showed printing its text (OBSERVED once) before the monitor stopped it at a device that has no model.',
                    'which boots 24 and 28 showed printing its text (OBSERVED once each), nested panics included, before the monitor stopped it at the read of a register in the chip-reset path that has no model.'),
                   ('(INFERRED from boots 25 to 27), is a new decision', '(INFERRED from boots 25 to 28), is a decision that no run has tested'),
                   ('the longest run so far was boot 27\'s', 'the longest run of the guest so far was boot 27\'s'),
                   ('a run of 300 s is UNTESTED.', 'a run of 300 s is UNTESTED (boot 28\'s guest ended at 40.08 s).'))


def d8_decision(real, box_s, wait_s):
    """0b and section 4, beside the exposure: D8 accepted it for 5 s, rung 9's approval took 300 s for boot 28, and the wrapper's step of section 4 is scaled from the dry run, not from boot 28."""
    import sp11_stick_bundle_panel_rung10d as d
    dc = d.dc_s(real['panel_facts']['rung10']['diff'])
    return (f'D8 accepted the exposure of one CPU for a box of 5 s only: for this box of {box_s} s, which rung 9\'s approval took for boot 28 and which no run has spanned, approving this text decides it again (section 0), and the '
            'carry before the call loads the CPU too (boot 28: the runner\'s read and hash took about 54 s, OBSERVED once, and the wrapper\'s steps after them: section 5d). The wrapper\'s step in the table above is scaled from the dry '
            'run (38.9 s); boot 28 measured the wrapper\'s placement, digests and copy together at 26.1 s (the TICKS line\'s COPIED, counted from the wrapper\'s entry, so it holds the digests of the file: OBSERVED once) for the 2.04 GB '
            f'that rung 9\'s chunks place, and this guest\'s chunks place 3.11 GB, so its digests and copy take at most about {dc} s (INFERRED: 26.1 s plus the ISC\'s zeros at no less than the {d.RATE9_MIB:.0f} MiB/s that 26.1 s allows) '
            'and the verify\'s share is UNKNOWN: the high sum above is an estimate and not a bound for this record (section 5d, THE CARRY).')


def decided(real, box_s, wait_s):
    """Section 0, after the decisions of 2026-11-17: the answer of 2026-11-19 (the box of 120 s) and what rung 9's approval and this record add."""
    first, *rest = m1t.wrap(f'the time box of rungs 6 to 8 was 120 s, with AC power REQUIRED, one CPU loaded for up to 120 s and no temperature readout (D8\'). Rung 9\'s approval took {box_s} s for boot 28 (D5\'\'\'), whose guest ran '
                            f'40.08 s; this record asks {box_s} s again, which approving this text decides for this boot. Sections 0b and 4 give the exposure.', first=8, rest=8)
    return (*m1t.wrap('Decided by the operator on 2026-11-19 (the answer "let\'s go with 120 seconds", quoted from the app, to the question whether the box of rung 6\'s record should be 120 s or 60 s):'),
            '   ' + 'D5\'\''.ljust(5) + first[8:], *rest)


def guest_gets():
    return ('Apple\'s BaseSystem root and an ISC as a RAM disk inside its own RAM, no other storage, no USB and the unit\'s framebuffer (the panel range of section 3c, which it can read and write): its build has the root and the ISC '
            'and no storage or USB model, and stage 2 maps it its RAM (6,144 MiB, the RAM disk part of it) and the panel range only (INFERRED from the sources; a map of 6,144 MiB ran once, in boot 28, for 40.08 s, OBSERVED once; a '
            'map of 384 MiB ran in boots 20 to 27)')


def carry_rows():
    """The row of section 4's table for the runner: before the wrapper's first line, so in neither sum (boot 28's films)."""
    return [f'      {"runner: read and hash of GUEST.DAT":<41}about 54 s  OBSERVED once (boot 28), before the wrapper: in neither sum']


def pre_call(panel):
    """The bullets of section 5 on the waits before the call: tools/sp11_stick_bundle_panel_rung10d.py pre_call_lines (with the guest's numbers: panel_rungs.pre_call passes the panel)."""
    import sp11_stick_bundle_panel_rung10d as d
    return d.pre_call_lines(panel)


def guide_words(base):
    """The guide's PANEL entry of this record: rung 9's (`base`, which already holds rung 9's wrappers of step5 and film), with the words that this module says again."""
    import sp11_stick_bundle_panel_rung10d as d

    def step4(guest, label):
        return d.step4_text(guest, label)

    def step5(real, box_s, wait_s):
        text = base['step5'](real, box_s, wait_s)
        import sp11_stick_bundle_panel_rung9d as d9
        assert text.startswith(d9.step5_text()), 'rung 9\'s step 5 does not begin with its carry sentence'
        return d.step5_text(real['panel_facts']) + text[len(d9.step5_text()):]

    def checks(real, box_s, wait_s):
        diff = real['panel_facts']['rung10']['diff']
        return (f'up to about {d.wrapper_s(diff)} s of the wrapper\'s checks, copy and verify (INFERRED from boot 28\'s 26.1 s for digests and copy: section 5d, THE CARRY; the runner\'s read and hash of the file, about {d.READ_S + d.HASH_S} s '
                'of progress lines, OBSERVED once, come before PAYLOAD CALL 0)')

    def film(real, box_s, wait_s):
        text = base['film'](real, box_s, wait_s)
        before = (f'The film runs about {(r9.CARRY_READ_S + r9.CARRY_HASH_S + r9.CARRY_WRAPPER_S + 8 + 90 + box_s + r5.FILM_AFTER_S) // 60} minutes for this box (the Shell\'s countdown and the runner\'s first lines, the carry of about '
                  f'two minutes, the call of {box_s} s, the stop and {r5.FILM_AFTER_S} s after the report page: an estimate, not a measurement)')
        after = (f'The film runs about {d.film_min(real["panel_facts"]["rung10"]["diff"], box_s)} minutes for this box (the Shell\'s countdown and the runner\'s first lines, the carry of up to about three minutes, the call of {box_s} s, '
                 f'the stop and {r5.FILM_AFTER_S} s after the report page: an estimate, not a measurement)')
        return (' ' if text.startswith(' ') else '') + patched(text, (before, after))

    def film_until(real, box_s, wait_s):
        return patched(base['film_until'](real, box_s, wait_s), ('through the carry (about two minutes:', 'through the carry (up to about three minutes:'))
    return {**base, 'decides': DECIDES, 'hazard': hazard, 'rung': RUNG, 'why': why_lines, 'stand': stand_extra, 'stand_head': STAND_HEAD, 'console': CONSOLE, 'ran': RAN, 'step4': step4,
            'idle': IDLE, 'restart': RESTART, 'restart_c': RESTART_CONTROL, 'box_decision': BOX_DECISION, 'd8_decision': d8_decision, 'decided': decided, 'step5': step5, 'film': film, 'film_until': film_until,
            'checks': checks, 'guest_gets': guest_gets(), 'carry': CARRY, 'page': d.page_legend, 'no_result': no_result, 'step7_head': step7_head,
            'ram_note': '(INFERRED; boots 20 to 27 ran a map of RAM, with the panel range from boot 21, for up to 120.2 s, and boot 28 a map of 6,144 MiB for 40.08 s, OBSERVED once each)'}


CARRY = ('THE CARRY. Before the guest runs, the runner reads GUEST.DAT, 1.9 GB, from the stick into memory and hashes it, and the wrapper digests it, copies it into the guest\'s placement and verifies the copy. Boot 28 '
         'ran this carry once for rung 9\'s file (OBSERVED once: section 5d, THE CARRY); this record\'s medium adds 1.0 GiB of zeros that the wrapper stores like data, so its digests and copy take at most about 14 s more and its verify up '
         'to as long again (INFERRED). A restart of the Surface during the carry is a result of the carry and not of the guest (the guest has not run); a dark panel by itself is not a result (section 5d).')


def kind_lines(panel):
    """The lines that section 3 adds after the kind's own line: rung 9's, with the RAM disk as a GPT medium."""
    return m1t.wrap(patched(r9.kind_lines(panel), ('a RAM-disk root of 1.86 GB in its RAM (quiet-rtc-acm),', 'a RAM disk of 2.94 GB in its RAM that holds the root and an ISC (quiet-rtc-acm),')))


def in_short(panel):
    """The summary of section 3c."""
    d = panel['rung10']['diff']
    return ('In short. This record is rung 10, the first run with an ISC, after rung 9 (boot 28: section 0a): everything is rung 9\'s but the RAM disk, which is a GPT medium of the root and the ISC '
            f'({d["medium"]["chunks"]} of the guest\'s {d["count"]} chunks), and the three words that name its end (HANDOFF twice, PARAMS word 46) and the two /chosen properties that name its size and extents. Before the guest runs, the carry takes up to about three minutes: the runner\'s progress lines for 1.9 GB, a page that stands still for the sweep and the bounce, and the wrapper\'s digests, copy and verify. What it expects, what falsifies that and what counts as '
            'a result are in section 5d, in the operator\'s words; this section is the evidence for them.')


def intro(panel):
    """The introduction of section 3c: rung 9 is the run before; this record gives its RAM disk an ISC."""
    box_s = panel['run']['box_s']
    return (f'Rung 9 is the run before this one: the panel as the console with the CPU-start stores acknowledged, let_panic, cpu_boot_timeout_secs=360, the guest profile quiet-rtc-acm, the root as the RAM disk, the root-carry '
            f'pair and a box of 300 s, record {BOOT28_RECORD}, boot 28 (section 0a: OBSERVED once); its approval is spent and this text neither gives nor assumes another. This record is rung 10: its guest is rung 9\'s with an '
            f'ISC in the RAM disk and the /chosen properties and free-memory words that follow; its pair and its runner are rung 9\'s, and its box is {box_s} s (section 0). It is not decided: approving the SHA-256 of this record '
            'is that decision, and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT). SNAPMON.BIN and SNAPREP.BIN are rung 9\'s (SNAPMON-M1-ACK.BIN 6af26a84... and SNAPREP-M1-ACK.BIN 562b9b6e..., which ran '
            'in boot 28); GUEST.DAT, PIN.DAT (the label and the digests) and PAYLOAD.MAN, which lists them, are this record\'s own.')


def other_differences(panel):
    """The sentence of section 3c on what else differs: made from the builder's own chunk comparison with rung 9's guest (tools/sp11_stick_bundle_rung10.py compare)."""
    d = panel['rung10']['diff']
    m, root, isc, tail, pre = d['medium'], d['root'], d['isc'], d['tail'], d['prefix']
    return (f'From rung 9\'s guest (GUEST.DAT {r10b.RUNG9_GUEST[:16].upper()}..., {d["guest_bytes"][0]:,} bytes, {r10b.RUNG9_CHUNK_COUNT} chunks, the one that rung 9\'s record builds and that the stick held after boot 28) this one has {d["count"]} chunks, '
            f'which the builder compares by SHA-256 with the table of rung 9\'s own bundle that it pins, and it refuses any other difference (PANEL_MISMATCH): the {d["outside"]} chunks that are not the RAM disk are rung 9\'s, '
            f'{d["outside"] - d["put_back"]} byte for byte and {d["put_back"]} once the end of the RAM disk is put back: HANDOFF chunk 0 differs in the free-memory start, 0X{r10b.RUNG9_END:X} in rung 9\'s and 0X{d["avail"][1]:X} here, held twice '
            f'(offsets 0X18 and 0X1020), PARAMS differs in ONE word, 46, which holds the same address, and DEVICE_TREE ({d["dt_bytes"][0]:,} bytes in both; {d["nodes"][1]} of {d["nodes"][0]} nodes) differs in boot-ramdmg-size and '
            'boot-ramdmg-extents and in no other property, which the builder proves by parsing the tree: with those two put back it is rung 9\'s tree to the SHA-256. The RAM disk is the GPT medium of probes/bootmedia.py: '
            f'{m["chunks"]} kind-OTHER chunks ({m["zero"]} of them ZERO, {m["data_bytes"]:,} bytes of data and {m["zero_bytes"]:,} of zeros) that cover 0X{m["dest"]:X} to 0X{m["end"]:X} without a gap: the first MiB (the protective MBR and the GPT, '
            f'{pre["chunks"]} chunks, {pre["data"]:,} bytes of data), the root partition at {root["at"] >> 20} MiB, which is rung 9\'s {r10b.RUNG9_ROOT_ROWS} root chunks displaced by exactly 1 MiB, {r10b.RUNG9_ROOT_ROWS - 1} of them by SHA-256 and the last, '
            f'{r10b.RUNG9_LAST_ROW[2]:,} bytes in rung 9\'s table, {root["last_extra"]:,} bytes longer here because the first data of the ISC follows it, the ISC partition ({isc["bytes"]:,} bytes at 0X{isc["at"]:X} into the medium, '
            f'{isc["chunks"]} more chunks, {isc["zero"]} of them ZERO and {isc["data"]:,} bytes of data) and the GPT\'s tail ({tail["bytes"]:,} bytes, {tail["chunks"]} chunk of {tail["data"]:,} bytes of data after the zeros that the ISC\'s last chunk '
            f'runs on into); the builder reads the medium back through these chunks, holds it to the GPT (probes/bootmedia.py inspect), and the SHA-256 of the root partition to the root\'s ({r10b.ROOT_SHA256[:8]}...) and that of the ISC partition to '
            f'the image\'s. The table has {d["table_room"]} entries of its 1,024 to spare. GUEST.DAT is {d["guest_bytes"][1]:,} bytes, {d["guest_bytes"][1] - d["guest_bytes"][0]:,} more than rung 9\'s, and has its own SHA-256 '
            '(docs/SP11-STICK-REAL.md section 2).')


def root_paragraphs(panel):
    """The paragraphs of section 3c on the RAM disk: the root and the ISC, what the builder checked, what they cannot show, and how to tell this guest apart."""
    d, r = panel['rung10']['diff'], panel['rung10']
    m, isc = d['medium'], d['isc']
    what = (f'THE RAM DISK (EXPERIMENTAL; the guest profile {PROFILE}, mark M-, composed with rung 9\'s form). One RAM disk of {m["bytes"]:,} bytes at 0X{m["dest"]:X}, as the loader makes it for the A16 route (probes/bootmedia.py: a protective MBR, '
            'a GPT of two partitions and its backup, each partition on a 1 MiB boundary): partition 1 is Apple\'s BaseSystem root, 1,862,270,976 bytes (SHA-256 7cab6d69..., the digest that the profile pins), type '
            '7C3457EF-0000-11AA-AA11-00306543ECAC, at 1 MiB, and partition 2 is the ISC, type 69646961-6700-11AA-AA11-00306543ECAC (iBootSystemContainer), a raw APFS container of 1,074,790,400 bytes with three EMPTY volumes, '
            f'Snapintosh-xART (role 256), Snapintosh-Preboot (16) and Snapintosh-Hardware (320), made by tools/make-isc-fixture.py --build-three-role on the host (SHA-256 {isc["sha256"]}; the macOS formatter made Preboot and Hardware; '
            'it refused the xART role, so that role is a byte stamped into the container with its checksum redone: OBSERVED on the host; the builder reads the three roles back from the image\'s latest checkpoint with the speed '
            'test\'s own reader before the loader maps it). A container of 1,074,790,400 bytes is the smallest that the formatter lets hold three volumes (1 GiB and a MiB: OBSERVED on the host). The device tree\'s /chosen names '
            'the medium as ONE RAM disk: boot-ramdmg-size and ONE boot-ramdmg-extents, base and size, cover the whole of it (not a second RAM disk and not a second extent); boot-uuid and apfs-preboot-uuid (the UUID of the root volume, '
            'A9037730... in the profile) are rung 9\'s, the root hash (208 bytes, SHA-256 e3d407cc...) is over the root partition alone, and protected-data-access and sepfw-load-at-boot stay 0; the static trust cache (26,496 bytes) '
            'travels in PARAMS. The medium leaves 3,104 MiB of the guest\'s 6,144 MiB free after the 128 MiB that the loader keeps for the allocator (rung 9\'s RAM disk left 4,131 MiB). The builder reads the device tree back property by '
            'property (GUEST_PROFILE_READBACK), holds the command line to the form (PANEL_MISMATCH) and the placement to the runner\'s data files (ROOT_PLACEMENT).')
    cannot = ('WHAT THE ISC CANNOT SHOW (UNKNOWN unless a film says): that the kernel\'s APFS accepts EMPTY volumes (no xART records, no Preboot directories) and the xART role that is a byte stamp; that the kernel finds the root as '
              'a partition of a GPT medium as it found the raw image in boot 28 (the A16 route\'s root was this medium: a document, not a run here); that the partition scheme publishes the ISC\'s slice for a RAM disk, so that '
              'apfs_boot_util can match it by its type GUID; that mount-phase-1 asks for nothing else (INFERRED: it reads nothing from the volumes); that the later boot tasks pass (section 5d); that 3,104 MiB of free RAM is enough '
              'for the guest\'s userspace.')
    tell = (f'TELLS IT APART (INFERRED until it runs): PIN label {r["label"]}; INDEX=2 on the PAYLOAD LOADED line shows BYTES={r["guest_bytes"]} SHA={r["guest_sha16"]} (rung 9\'s: {r10b.RUNG9_GUEST_BYTES} bytes, SHA={r10b.RUNG9_GUEST[:16].upper()}); '
            f'the SNAPMON STAGE=PIN_OK line shows CHUNKS={d["count"]} (rung 9\'s: {r10b.RUNG9_CHUNK_COUNT}); INDEX=3\'s SHA is PIN.DAT\'s, which holds this record\'s own SHA-256: no record names it.')
    return (what, cannot, tell)


def profile_paragraphs(panel):
    """The paragraphs of section 3c on the guest profile: rung 9's, with the ISC among what is not claimed."""
    d = panel['rung10']['diff']
    quiet = r9.pp.profile_text(r9.pp.PREFIX + 'quiet', dict(kind='rung 7\'s form', let_panic=True, models=r9.pp.MODELS, changes='-'))
    assert len(quiet) == 6 and quiet[1].startswith('WITHDRAWN:') and quiet[3].startswith('NOT CLAIMED:') and quiet[4].startswith('HAZARD') and quiet[5].startswith('RESULT'), 'the profile text of quiet has changed'
    changes = (f'{r9.pp.LEAD}: {r9.pp.PREFIX}quiet-rtc-acm, mark M-, composed with rung 10\'s form: the quiet profile\'s device tree ({d["nodes"][1]} of {d["nodes"][0]} nodes), the PMU\'s clock bound at nub-spmi0 as in '
               'rung 8\'s profile quiet-rtc, and the credential manager\'s software mode, which adds acm_fastsim=1 to the command line when the loader builds a RAM-disk root and changes no node and no device model. The '
               f'profile is rung 9\'s and is not changed for the ISC: the guest has {d["count"]} chunks, {d["outside"]} of them rung 9\'s (Other differences, above), which the builder compares by SHA-256.')
    claimed = patched(quiet[3], ('that anything runs with these nodes withdrawn (EXPERIMENTAL, hardware NOT RUN); that it changes what the kernel waits for or stops at (UNKNOWN)',
                                 'that the withdrawn nodes are enough for what comes after launchd\'s first boot task (EXPERIMENTAL; they ran to launchd\'s panic in boot 28, OBSERVED once); that the ISC changes what launchd waits '
                                 'for or stops at (INFERRED: section 5d); that the placeholder calendar is harmless (UNKNOWN)'))
    return (changes, quiet[1], r8.CLOCK_TEXT, claimed, quiet[4], quiet[5])


def extra_paragraphs(panel):
    """The paragraphs of section 3c that only these records have, after the command line's: rung 9's (the PARAMS word, the acknowledge, what still stops the run, the panic path and the page), with the profile's paragraphs and the RAM disk's at the end."""
    parts = r9.r6.extra_paragraphs(panel)
    assert len(parts) == 6 and parts[2].startswith('What the acknowledge lets the guest reach.'), 'the paragraphs of rung 6 have changed under rung 10'
    params = patched(parts[0], ('for the records of rungs 4, 5 and 6 only', 'for the records of rungs 4 to 10 only'),
                     ('used here for the first time on the Surface Pro 11,', 'used on the Surface Pro 11 in boots 24 to 28 (OBSERVED once each),'))
    stops = patched(parts[3], ('MON STOP WATCHDOG (OBSERVED once: boot 20)', f'MON STOP WATCHDOG ({r8.WD_BOXES})'))
    return (params, *parts[1:2], r9.ACK_REACH, stops, *parts[4:], *profile_paragraphs(panel), *root_paragraphs(panel))


def record_problems(text, panel):
    """What is wrong with `text`, the finished record of rung 10: the names of each sentence of pp.FALSE_FOR_A_PROFILE that it carries ('FALSE: ...') and 'MISSING' if a paragraph of profile_paragraphs is not in it."""
    flat = ' '.join(text.split())
    problems = [f'FALSE: {name}' for name, pattern in r9.pp.FALSE_FOR_A_PROFILE if r9.re.search(pattern, flat)]
    return problems + ['MISSING' for paragraph in profile_paragraphs(panel) if ' '.join(paragraph.split()) not in flat][:1]


WAIT_OLD = f'for {CARRY_WAIT_MIN} minutes before the MON CALL line (a dark panel at 100 s with nothing else changed is not'


def wait_swap():
    """Rung 9's wait of 4 minutes before the MON CALL line, which section 5 does not give (4 minutes before PAYLOAD CALL 0, then the wait after it: section 5d, CALL_WAIT_S), for the guide's two sentences about a dark panel."""
    import sp11_stick_bundle_panel_rung10d as d
    return (WAIT_OLD, f'for the wait of section 5 that applies ({CARRY_WAIT_MIN} minutes before PAYLOAD CALL 0, {d.CALL_WAIT_S} s between it and the MON CALL line; a dark panel at 100 s with nothing else changed is not')


def no_result(real, box_s, wait_s):
    """5a, NOT A RESULT: a dark panel is a result of nothing until it has shown no text for the wait of section 5 that applies (the guide's `no_result`)."""
    return patched(r9.no_result(real, box_s, wait_s), wait_swap())


def step7_head(real, box_s, wait_s):
    """5c, step 7, in place of its first clause (the guide's `step7_head`): the same wait."""
    return patched(r9.step7_head(real, box_s, wait_s), wait_swap())


def expect_text(run):
    """The paragraph of section 3c that stands where rung 3's and 4's what-the-run-may-end-as is: the prediction, the order of the classes and the falsifiers are section 5d's, in the operator's words."""
    return patched(r9.expect_text(run), ('NOT PASSED for rung 9 (section 5d)', 'NOT PASSED for rung 10 (section 5d)'),
                   ('from the host analysis of boots 24 to 27, of the PMU\'s clock and of the root: none of it has been seen with a root',
                    'from the host analysis of boots 24 to 28, of the BaseSystem image and of the ISC: none of it has been seen with an ISC'))


def label(panel, label_text):
    """The label paragraph of section 3c: rung 9's, with this record's label."""
    return patched(r9.label(panel, label_text), (f'(rung 8\'s label, {r9.r9b.RUNG8_LABEL}, had the mark R-)', f'(rung 9\'s label, {r10b.RUNG9_LABEL}, had the same mark)'))


def section_5d(panel, wait_s):
    """Section 5d of the record, the operator's words: tools/sp11_stick_bundle_panel_rung10d.py."""
    import sp11_stick_bundle_panel_rung10d as d
    return d.section_5d(panel, wait_s)


def __getattr__(name):
    """A hook or a constant that this module does not say again is rung 9's (and then rung 8's: screen_text, stop_seen, word_text and the rest)."""
    if name.startswith('__'):
        raise AttributeError(name)
    return getattr(r9, name)
