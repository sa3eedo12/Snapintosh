# SPDX-License-Identifier: MIT
"""Rung 9 of the monitor entry run (docs/SP11-STICK-ROOT.md; docs/SP11-STICK-MONITOR.md section 20): the first ROOT-MOUNT record. Boot 27 (rung 7) ran the stock kernel through bsd_init to a wait for a
root device (OBSERVED once), and rung 8 (record 54c1dff7...c717b, boot 28) binds the PMU's clock; this record carries Apple's BaseSystem root, 1,862,270,976 bytes, from the stick into the guest's RAM as
its RAM disk, in ONE boot: the runner of the root carry reads and hashes the 1.9 GB GUEST.DAT, the wrapper of the root-carry pair digests, copies and verifies it, and the monitor runs the stock kernel for
300 s with the RAM-disk root named in its /chosen. The one question is how far the mount goes.

`--cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=360 --panel console --time-box-s 300 --guest-profile j700ap-26A428-m1-quiet-rtc-acm --bigfile --ramdisk ... --root-hash ...
--trust-cache ... --sptm-hvc-calls 0` is the one combination that builds it (tools/sp11_stick_bundle_m1.py and ..._chunks.py: ACK_FORMS, FORM_PROFILE, RUNG9_FORM), with the pair of ACK_PAIRS for this
profile (the root-carry pair, SNAPMON-M1-ACK.BIN 6af26a84... and SNAPREP-M1-ACK.BIN 562b9b6e...); the guide's table PANEL takes its words by the key
`console+cpu_boot_timeout_secs=360+let_panic+j700ap-26A428-m1-quiet-rtc-acm` (tools/sp11_stick_bundle_panel_rungs.py). The words that it shares with rung 8 are tools/sp11_stick_bundle_panel_rung8.py's, by
attribute (a hook that this module does not define is rung 8's), and where a sentence of rung 8's is kept with a change, `patched` asserts that it is in rung 8's words exactly once, so that a change of
those words cannot go unnoticed here. Section 5d is tools/sp11_stick_bundle_panel_rung9d.py's. Every sentence that says what this guest is against rung 8's is made from the builder's own chunk comparison
(tools/sp11_stick_bundle_rung9.py, `compare`). Rung 8's result is not known when the operator approves this record, so nothing of the record depends on it: boot 28's block, RUNG8 below, is a placeholder
that says so in words, states both outcomes of the clock and names the one that the times of the record assume. A coordinator who has the film replaces the block (one of RUNG8_BLOCK_VERDICTS) and the
record's SHA-256 is then that of the new text.

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung6 as r6  # noqa: E402
import sp11_stick_bundle_panel_rung7 as r7  # noqa: E402
import sp11_stick_bundle_panel_rung8 as r8  # noqa: E402
import sp11_stick_bundle_panel_profile as pp  # noqa: E402
import sp11_stick_bundle_rung9 as r9b  # noqa: E402

NUMBER = 9                                  # the rung's number, for the pointer to section 5d in the reading lines (tools/sp11_stick_bundle_panel.py)
PROFILE = ch.RUNG9_PROFILE                  # j700ap-26A428-m1-quiet-rtc-acm
CPU_WAIT_S, BOX_S = int(ch.RUNG9_FORM[1].rpartition('=')[2]), ch.RUNG9_FORM[2]
CPU_PANIC_AT_S = 362                        # about 2.3 s (the start of the wait: boot 24's PMGR panic came at 2.25 s) plus CPU_WAIT_S (INFERRED): outside the box of 300 s by about 62 s
WD1_WRAP_S = 179                            # 2^32 ticks of the guest's 24 MHz counter are 178.96 s: where the u32 counter of WD1 wraps, which stops nothing (compat/watchdog.c:12-17, read on the host)
BOOT28_RECORD = '54c1dff7...c717b'           # the record of rung 8 (v2, commit 4895ea2): SHA-256 54c1dff710c7faebd962b8a8a8dc1f097d34ec14cfb1aad200e9629e036c717b (r9b.RUNG8_RECORD)
BOOT28_GUEST = r9b.RUNG8_GUEST[:16].upper()
RUNG8_BLOCK_VERDICTS = ('RESULT PENDING', 'PANIC TEXT', 'NOT A RESULT', 'NOT PASSED', 'NEW STOP', 'THE CLOCK DID NOT TAKE', 'KERNEL WAITING')
STAND_HEAD = '0a. Where things stand (boots 20, 21, 23 to 27; one unit, one boot each; OBSERVED unless INFERRED; boot 28 pending)'
HEADER = ('3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic, a wait of 360 s, the quiet guest with the PMU\'s clock bound and a RAM-disk root of 1.86 GB '
          '(rung 9: D9 panel handed, serial=2, cpu_boot_timeout_secs=360, let_panic set, guest profile quiet-rtc-acm, the root-carry pair, stock kernel; docs/SP11-STICK-MONITOR.md section 20)')
VERDICTS = RUNG8_BLOCK_VERDICTS
patched = r7.patched
CARRY_READ_S, CARRY_HASH_S, CARRY_WRAPPER_S = 75, 10, 39       # INFERRED, not measured: the runner's read at an ASSUMED 24 MiB/s (boots 8 and 9 measured the hash alone), its hash at 194 MiB/s,
                                                                # the wrapper's checks, copy and verify scaled by size from the dry run (section 4: 2.9 s for 144.7 MB)
CARRY_WAIT_MIN = 4                                              # the operator's wait before FORCE OFF between two changes of the page before PAYLOAD CALL 0: twice the estimate above, and not a measurement
CALL_WAIT_S = 120                                               # the same, after PAYLOAD CALL 0 and before the MON CALL line (the wrapper's digests, copy and verify, then the GIC arm's 8 s)
LINE_WAIT_S = 120                                               # the operator's wait for a new PAYLOAD READ or PAYLOAD HASH line while the runner loads GUEST.DAT: about 40 times the gap of 2.7 s that 24 MiB/s gives (INFERRED)
READ_GAP_S = 3                                                  # about how far apart the READ lines are at 24 MiB/s or more: 64 MiB in 2.7 s (INFERRED, not measured at this size)
PROGRESS_MIB = 64                                               # the runner prints a line for each 64 MiB read and for each 64 MiB hashed (q1n1 9ea1bdc, Q1N1_PAYLOAD_PROGRESS_BYTES)
FILM_MIN = (CARRY_READ_S + CARRY_HASH_S + CARRY_WRAPPER_S + 8 + 90 + BOX_S + 120) // 60       # the film's length in whole minutes: the carry, the Shell's countdown and the runner's first lines (about 90 s), the box and 120 s after the page


def keyed_lines(block, what):
    """The paragraphs of section 0a that are one run's result: a keyed block (rung 7's rules: ASCII, no claim without a label, never the word that the project keeps for what an independent run
    showed), with the verdicts of this record (RUNG8_BLOCK_VERDICTS). AssertionError for a verdict that this record is not for."""
    assert block['verdict'] in VERDICTS, f'the verdict of {what} is not one that rung 9 is the record for'
    for paragraph in block['words']:
        assert paragraph.isascii() and pp.NEVER not in paragraph, 'a paragraph of a keyed block is not ASCII or uses the word that M1 records never use'
        assert any(label in paragraph for label in ('OBSERVED', 'INFERRED', 'UNKNOWN', 'NOT RUN')), 'a paragraph of a keyed block has no label'
    if block['verdict'] == 'RESULT PENDING':
        head = f'{what} (record {block["record"]}; its boot is called {block["boot"]} in this text; no film of it is read here): {what.upper()} {block["verdict"]}.'
    else:
        head = f'{what} (record {block["record"]}, {block["boot"]}; one unit, one boot; the operator\'s film, read by the coordinator): {what.upper()} {block["verdict"]}.'
    return (*m1t.wrap(head), *(line for paragraph in block['words'] for line in m1t.wrap(paragraph)))


# KEYED BLOCK of boot 28 (rung 8: the clock). Its result is not known when the operator approves this record: `RESULT PENDING` is the placeholder and it is part of the approved text. It states both
# outcomes of the clock in words and names the one that the record's times assume (CASE A), so that the record is right whichever way rung 8 went. A coordinator who has the film replaces `verdict` and
# `words` (one of RUNG8_BLOCK_VERDICTS, each paragraph with its label) and the record's SHA-256 is that of the text with the block as it stands.
RUNG8 = dict(record=BOOT28_RECORD, boot='boot 28', verdict='RESULT PENDING', words=(
    'THIS PARAGRAPH IS A PLACEHOLDER, AND NOTHING IN THIS RECORD DEPENDS ON RUNG 8\'S RESULT. Rung 8 is a separate record (the quiet guest with the PMU\'s clock bound, box 120 s) with its own approval; its '
    'boot is called boot 28 here. Whether boot 28 has run, and what its film showed, are not in this text and not known to it (NOT RUN as far as the text goes; no film of it was read when the text was '
    'written). A record that carries this paragraph was approved without rung 8\'s result, and the two cases below say what holds either way.',
    'CASE A, THE CLOCK TOOK (rung 8\'s prediction: INFERRED, and never seen): `AppleARMRTC registering service!` about 7 s after the MON CALL line, no parity error at f704, f715 or f802 after the clock '
    'driver\'s start lines, and the bsd_init block within about 10 s of the MON CALL line. THIS RECORD ASSUMES CASE A for every time that it gives to the kernel\'s log: the mount\'s stages (section 5d, THE '
    'AIM) then start about 10 s in and the box of 300 s leaves about 290 s for them.',
    'CASE B, THE CLOCK DID NOT TAKE (rung 8\'s class THE CLOCK DID NOT TAKE, which is boot 27\'s course again: the parity-error lines, two 30 s waits for a clock resource that is never published and the '
    'bsd_init block at about 60 s, OBSERVED once for the guest without the clock, INFERRED for this guest). Then every time that this record gives to the kernel\'s log is about 50 s later (INFERRED), the '
    'mount\'s stages get about 240 s of the box and not 290 s, and the clock\'s lines of the film are read as rung 8\'s record reads them. THE CLASSES OF SECTION 5d ARE THE SAME IN BOTH CASES: each reads '
    'the last line of the log and the time of its last change, and no class names a time that only one case has.',
    'CASE C, RUNG 8 ENDED ANOTHER WAY OR HAS NOT RUN (a panic, a new stop, a film that is no result, no film: UNKNOWN): this text claims nothing for it, and approving the text does not decide it, nor '
    'does it approve rung 8\'s boot. Whether this record is still the next to run is the coordinator\'s call, made from rung 8\'s film.',))
RUNG7 = r8.RUNG7
BLOCKS = {'Rung 4': r8.RUNG4, 'Rung 5': r8.RUNG5, 'Rung 6': r8.RUNG6, 'Rung 7': r8.RUNG7, 'Rung 8': RUNG8}


# ---- section 0 -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
DECIDES = ('APPROVING THIS TEXT ALSO DECIDES FOUR THINGS. (1) D5\'\'\': A TIME BOX OF 300 s, which is new. D5 and D8 decided 5 s, D5\' 60 s, and for rung 6 the operator chose 120 s in the app on 2026-11-19 '
           '(D5\'\', "let\'s go with 120 seconds"), which rungs 6 to 8 ask; this record asks 300 s, so that a mount that is slow can show how far it goes. Rungs 6 and 7 ran inside the box of 120 s (61.6 s and '
           '120.2 s, OBSERVED once each) and no run has lasted longer: AC power REQUIRED, one CPU loaded for up to 300 s after a carry of about two minutes (INFERRED: section 5d), no temperature readout, and the exposure '
           'sums of section 0b and section 4. Whether the Surface restarts inside a stock kernel\'s run of 300 s is UNKNOWN (section 4). (2) A GUEST WITH A ROOT. The guest is rung 8\'s (the quiet profile\'s device '
           'tree, 43 nodes withdrawn by data and never faked; the PMU\'s clock bound; let_panic; six CPUs, none started) with four changes, all in GUEST.DAT: its RAM is 6,144 MiB where rung 8\'s is 384; Apple\'s BaseSystem '
           'root, 1,862,270,976 bytes, is in that RAM as a RAM disk, named in the device tree\'s /chosen (its size and extents, the volume\'s UUID, the root hash and two policy words, protected-data-access and '
           'sepfw-load-at-boot, both 0); the command line ends with cpu_boot_timeout_secs=360 and acm_fastsim=1 (the credential manager\'s software mode, the guest profile quiet-rtc-acm: EXPERIMENTAL, whether the mount '
           'needs it is UNKNOWN); and PARAMS names the root\'s static trust cache (26,496 bytes; three words that rung 8\'s guest does not have, INFERRED to be its address, its virtual address and its size). GUEST.DAT '
           'is therefore 1.93 GB, and no earlier run has carried or run a file of that size. (3) A PAIR OF THE ROOT '
           'CARRY: the wrapper and the reporter are built with the file limit of 2 GiB in place of 256 MiB and the box cap of 1200 s in place of 120 s (SNAPMON-M1-ACK.BIN 6af26a84... and SNAPREP-M1-ACK.BIN 562b9b6e...; the '
           'monitor inside, 053a239f..., is the one that ran in boots 25 to 27), and the runner is the one built with the opt-in big-file mode (Q1N1-PAYLOAD.EFI af76e0bf..., 779,264 bytes: it reads and hashes files of up to '
           '2 GiB, and allocates them at the top of RAM). Neither has run on the unit; the wrapper and the runner are EXPERIMENTAL, and the places where they may fail are listed in section 5d (THE CARRY). (4) WHAT THE '
           'GUEST CAN REACH is rung 8\'s, plus a guest RAM of 6,144 MiB at 0X880000000 with the RAM disk inside it, with no new register, no new system-register class and no new store class (read on the host in '
           'compat/cpu-start-ack.h and the placement arithmetic of the builder, a file and not a run); nothing of this record writes to the stick but the Shell\'s marker file (section 0b): the runner reads it, and there '
           'is no host link; this approval is for that reach for up to the '
           'carry and the box. The monitor, the panel as the console, the monitor\'s let_panic word, the timer reporter and the clock\'s model are rung 8\'s or earlier; the clock\'s model has not run in any boot '
           'whose film this text reads (rung 8\'s boot, boot 28, is the first to bind it, and its result is not in this text: section 0a).')
RUNG = ('each of rungs 1 to 8, the first run with the panel withheld, the panel as the console (rung 2, boot 21), the same with cpus=1 (rung 3, boot 23), with cpus=1 and let_panic (rung 4, boot 24), '
        'with the CPU-start stores acknowledged, six CPUs and a 60 s box (rung 5, boot 25), with a wait of 90 s, a box of 120 s and another reporter (rung 6, boot 26), with a wait of 120 s and the '
        'guest profile quiet (rung 7, boot 27) and with the guest profile quiet-rtc (rung 8, boot 28), whose records have their own approvals (those of rungs 1 to 7 spent, rung 8\'s its own: this '
        'text does not know it): this record is rung 9, the root mount, and no approval '
        'covers another\'s boot')
BOX_DECISION = ('the operator has not chosen this box: D5\'\'\' is new, this record asks 300 s where the box of D5\'\' was 120 s, and approving this text decides it and the exposure that section 4 gives, which '
                'D8 accepted for a box of 5 s only')
IDLE = ('the extra seconds are there for the root mount, which no run has reached: the root device\'s wait and its UUID, the APFS import of the root hash, the AES, credential and SEP waits that may follow, '
        'and the first user process (section 5d); a kernel that waits is idle, and the box ends it')
GIC_IDLE = ('The quarantine switches off the physical timer interrupts and not the timer that XNU uses, which is the monitor\'s emulation of it and delivers its interrupts (OBSERVED once: boots 26 and 27, '
            'section 0a), so a kernel that mounts a root and waits has its own timed waits for the whole box (section 5d).')
RESTART = ('Inside a box of 300 s with the stock kernel running, whether the Surface restarts is UNKNOWN: the longest run so far was boot 27\'s 120.2 s, whose film showed none to its end, 152 s (OBSERVED once), '
           'and the carry before this run (about two minutes of the runner reading the stick and hashing at EL2, then the wrapper) has never run (UNKNOWN: section 4).')
RESTART_CONTROL = ('That control ran no guest; the stock kernel ran inside a box of 120 s once, in boot 27, without a restart to the end of its film (OBSERVED once). A run of 300 s, preceded by about two '
                   'minutes of a 1.9 GB read at EL2, is UNTESTED, and the argument above does not reach it.')
WHOLE = ('Only the counts (MON COUNTS), the monitor\'s window words that the reporter of this record prints (MON TIM, MON CNT, MON EP) and the end of the run (MON STOP, MON EXIT) reflect the whole box.')


def d8_decision(real, box_s, wait_s):
    """0b and section 4, beside the exposure: D8 accepted it for 5 s, and nobody has chosen this record's box yet."""
    return (f'D8 accepted the exposure of one CPU for a box of 5 s only: for this box of {box_s} s, which is new (D5\'\'\'), approving this text decides it (section 0), and the carry before the call '
            'loads the CPU too (about two minutes at EL2, INFERRED: section 5d).')


def decided(real, box_s, wait_s):
    """Section 0, after the decisions of 2026-11-17: the answers of 2026-11-19 (the box of 120 s, which rungs 6 to 8 ask) and what this record asks in its place."""
    first, *rest = m1t.wrap(f'the time box of rungs 6 to 8 was 120 s, with AC power REQUIRED, one CPU loaded for up to 120 s and no temperature readout (D8\'). This record asks {box_s} s (D5\'\'\', above), '
                            'which no answer of the operator covers yet: approving this text is that answer. Sections 0b and 4 give the exposure.', first=8, rest=8)
    return (*m1t.wrap('Decided by the operator on 2026-11-19 (the answer "let\'s go with 120 seconds", quoted from the app, to the question whether the box of rung 6\'s record should be 120 s or 60 s):'),
            '   ' + 'D5\'\''.ljust(5) + first[8:], *rest)


def why_lines(real):
    """Section 0's paragraph for this record: why rung 9, the changes, the prediction in a line and where the rest is. Boot 27's facts are section 0a's."""
    d = real['panel_facts']['rung9']['diff']
    r = real['panel_facts']['rung9']
    return m1t.wrap(f'WHY RUNG 9. Rung 7 (boot 27, record e42f451d...3b01) ran its whole box of 120 s through bsd_init to a wait for a root device and was ended by the monitor\'s watchdog: the kernel had no root '
                    'to find, which is what a guest without storage gives (INFERRED). The operator\'s aim is to see macOS boot, even slowly, and the one run that can show how far the stock kernel gets '
                    f'with a root is the run that has one. THIS RECORD CARRIES THE ROOT AND RUNS THE KERNEL ON IT IN ONE BOOT: GUEST.DAT is {r["guest_bytes"]:,} bytes (rung 8\'s: {d["guest_bytes"][0]:,}) in '
                    f'{d["count"]} chunks (rung 8\'s: 46), of which {d["root"]["chunks"]} ({d["root"]["zero"]} of them ZERO) are Apple\'s root, {d["root"]["size"]:,} bytes at 0X{d["root"]["dest"]:X}, and '
                    f'the other {d["outside"]} are rung 8\'s guest sized for 6,144 MiB of RAM: {len(d["same"])} of rung 8\'s 46 chunks are byte for byte rung 8\'s (the kernel image and the monitors among them) and '
                    'the rest are named in section 3c. THE MONITOR, THE REPORTER AND THE CLOCK ARE THE ONES OF THE EARLIER RECORDS; THE PAIR IS THE ROOT-CARRY PAIR; THE BOX IS 300 s AND THE WAIT FOR THE '
                    'CPU THAT NEVER COMES IS 360 s, OUTSIDE IT. THE AIM, THE ORDER OF THE CLASSES, THE PREDICTION, ITS FALSIFIERS AND WHAT TO WAIT FOR AND FILM are in section 5d, in the operator\'s words '
                    '(INFERRED; nothing of this has run).')


def stand_extra(real):
    """The paragraphs that section 0a gets after boot 20's: rung 8's (boots 21 and 23 condensed, the keyed blocks of rungs 4 to 7), boot 28's keyed block (RUNG8, a placeholder or the film) and the stick."""
    lines = r8.stand_extra(real)
    at = next(i for i, line in enumerate(lines) if line.startswith('   The stick after boots'))
    return (*lines[:at], *keyed_lines(RUNG8, 'Rung 8'), *lines[at:])


def hazard(real, box_s, wait_s):
    """The panel's bullet of 0b: the console on the panel is boots 21's, 24's, 25's, 26's and 27's; the path of the guest past the acknowledged stores is boot 27's for its 120.2 s, and what comes after
    that is the mount."""
    return patched(r8.hazard(real, box_s, wait_s),
                   ('what it does with the clock bound, which should publish that resource, is UNKNOWN (it may wait, stop at a device that has no model, panic or spin).',
                    'what it does with the clock bound and a root to mount is UNKNOWN (it may wait for the root, panic in the root-hash import or the mount, wait for a driver that needs an AES, '
                    'credential or SEP service, stop at a device that has no model, or run the first user process and stop there; section 5d).'),
                   (f'A box of {box_s} s with a CPU that is mostly idle after the first 35 s or so (INFERRED from boots 25 and 26) is the operator\'s decision D5\'\', made in the app on 2026-11-19 and '
                    'confirmed by approving this text (section 0):', f'A box of {box_s} s, with a CPU that may be mostly idle after the first minute or so (INFERRED from boots 25 to 27), is a new '
                    'decision, D5\'\'\', which approving this text makes (section 0):'),
                   (f'the longest run so far was boot 27\'s {r8.BOOT27_S} s, whose film showed no restart of the Surface to its end, 152 s (OBSERVED once): a run of {box_s} s has been seen once, and not with '
                    'the clock bound.', f'the longest run so far was boot 27\'s {r8.BOOT27_S} s, whose film showed no restart of the Surface to its end, 152 s (OBSERVED once): a run of {box_s} s is UNTESTED.'))


def step4(guest, label):
    """5c, step 4: the words of tools/sp11_stick_bundle_panel_rung9d.py step4_text (the programs' digits that tell this bundle from rung 8's, on the runner's lines)."""
    import sp11_stick_bundle_panel_rung9d as d
    return d.step4_text(guest, label)


def step5_with(base):
    """5c, step 5: the carry's lines and waits first (tools/sp11_stick_bundle_panel_rung9d.py step5_text), then rung 8's sentences (the base words' own `step5`)."""
    import sp11_stick_bundle_panel_rung9d as d

    def step5(real, box_s, wait_s):
        return d.step5_text() + (base(real, box_s, wait_s) if callable(base) else base)
    return step5


def checks(real, box_s, wait_s):
    """5c, step 5, in place of `about 3 s of checks`: the wrapper's checks, copy and verify of a file of this size, and the runner's progress lines before them."""
    return (f'about {CARRY_WRAPPER_S} s of the wrapper\'s checks, copy and verify (INFERRED: section 4; the runner\'s read and hash of the file, about {CARRY_READ_S + CARRY_HASH_S} s of progress lines, '
            'come before PAYLOAD CALL 0)')


def film_with(base):
    """5c, step 2: the base words' sentences with the length of the film for this run, which has the carry in it."""
    def film(real, box_s, wait_s):
        text = base(real, box_s, wait_s)
        old = (f'The film runs about {r5.film_total_s(box_s)} s for this box (the runner\'s loading, the call of {box_s} s, the stop and {r5.FILM_AFTER_S} s after the report page: an estimate, not a '
               'measurement)')
        new = (f'The film runs about {(CARRY_READ_S + CARRY_HASH_S + CARRY_WRAPPER_S + 8 + 90 + box_s + r5.FILM_AFTER_S) // 60} minutes for this box (the Shell\'s countdown and the runner\'s first lines, the carry of about '
               f'two minutes, the call of {box_s} s, the stop and {r5.FILM_AFTER_S} s after the report page: an estimate, not a measurement)')
        return (' ' if text.startswith(' ') else '') + patched(text, (old, new))
    return film


def film_until(real, box_s, wait_s):
    """The span of the film in step 2 of 5c: from the Shell's countdown through the carry and the box to 120 s after the report page (or the FORCE OFF of step 5)."""
    return (f'through the carry (about two minutes: the runner\'s progress lines, then a page that stands still: section 5d) and the box until {r5.FILM_AFTER_S} seconds after the SNAPREP report page has appeared, or, if it never appears, '
            f'until the FORCE OFF of step 5 ({wait_s} s after the MON CALL line) has been filmed')


def guest_gets():
    return ('Apple\'s BaseSystem root as a RAM disk inside its own RAM, no other storage, no USB and the unit\'s framebuffer (the panel range of section 3c, which it can read and write): its build has '
            'the root and no storage or USB model, and stage 2 maps it its RAM (6,144 MiB, the RAM disk part of it) and the panel range only (INFERRED from the sources; a map of 384 MiB ran in boots 20 '
            'to 27 and one of 6,144 MiB has never run: UNKNOWN)')


def carry_rows():
    """The row of section 4's table for the runner: before the wrapper's first line, so in neither sum (INFERRED, UNKNOWN until a run)."""
    return [f'      {"runner: read and hash of GUEST.DAT":<41}about {CARRY_READ_S + CARRY_HASH_S} s  INFERRED, before the wrapper: in neither sum']


def pre_call():
    """The bullets of section 5 on the waits before the call, for the carry: tools/sp11_stick_bundle_panel_rung9d.py pre_call_lines."""
    import sp11_stick_bundle_panel_rung9d as d
    return d.pre_call_lines()


SILENCE_TAIL = ('The 360 s leave about 15 s of margin over section 4\'s bound for the clean of this guest (44.6 s: 300 + 44.6 = 344.6), less the prepare (UNKNOWN): a FORCE OFF inside it spends the report and '
                'not the guest. ' + r5.SILENCE_TAIL)


def no_result(real, box_s, wait_s):
    """5a, NOT A RESULT: a dark panel in the carry is a result of nothing until it has shown no text for the wait of 5 (the guide's `no_result`)."""
    return (f'a panel that is black and shows no text for {CARRY_WAIT_MIN} minutes before the MON CALL line (a dark panel at 100 s with nothing else changed is not this case: section 5d, THE CARRY), a panel '
            f'still dark {wait_s} seconds after it, or a restart of the Surface, before STAGE=DONE and the SNAPREP page have been on the screen, or a film that does not show them')


def step7_head(real, box_s, wait_s):
    """5c, step 7, in place of its first clause (the guide's `step7_head`): a dark panel in the carry is not a failure by itself."""
    return (f'If the Surface restarts, or the panel goes black and shows no text for {CARRY_WAIT_MIN} minutes before the MON CALL line (a dark panel at 100 s with nothing else changed is not a failure by '
            f'itself: section 5d, THE CARRY), or is still dark {wait_s} seconds after the MON CALL line:')


def guide_words(base):
    """The guide's PANEL entry of this record: rung 8's (`base`), with the words that this module says again, and the legend of the reporter's page (rung 7's) with the runner's big-file notes."""
    import sp11_stick_bundle_panel_rung9d as d
    return {**base, 'decides': DECIDES, 'hazard': hazard, 'rung': RUNG, 'why': why_lines, 'stand': stand_extra, 'stand_head': STAND_HEAD, 'console': CONSOLE, 'ran': RAN, 'step4': step4,
            'idle': IDLE, 'restart': RESTART, 'restart_c': RESTART_CONTROL, 'box_decision': BOX_DECISION, 'd8_decision': d8_decision, 'decided': decided, 'gic_idle': GIC_IDLE, 'whole': WHOLE,
            'film_until': film_until, 'step5': step5_with(base['step5']), 'film': film_with(base['film']), 'checks': checks, 'guest_gets': guest_gets(), 'carry': d.CARRY, 'step7_head': step7_head, 'no_result': no_result, 'silence_tail': SILENCE_TAIL, 'page': d.page_legend,
            'ram_note': '(INFERRED; boots 20 to 27 ran a map of RAM, with the panel range from boot 21, for up to 120.2 s, OBSERVED once each; a map of 6,144 MiB has never run: UNKNOWN)'}


RAN = ('ran in its acknowledging build three times, for 60 s (boot 25), for 61.6 s (boot 26) and for 120.2 s (boot 27: section 0a), and in its plain build in boots 20, 21, '
       '23 and 24; rung 8\'s boot, which would be the fourth in its acknowledging build, is not counted here (section 0a). The wrapper and the reporter that this record names are the root-carry pair, which has not run.')
CONSOLE = patched(r8.CONSOLE, ('its command line is serial=2 with cpu_boot_timeout_secs=120,', f'its command line is serial=2 with cpu_boot_timeout_secs={CPU_WAIT_S} and acm_fastsim=1,'),
                  ('its device tree is the profile quiet\'s, its PARAMS bind the PMU\'s clock,', 'its device tree is the profile quiet\'s plus the RAM-disk root\'s /chosen properties, its PARAMS bind the PMU\'s clock,'),
                  ('(boots 21, 24, 25, 26 and 27:', '(boots 21, 24, 25, 26 and 27, without a root:'),
                  ('what the acknowledge, let_panic and the withdrawn nodes do with the clock bound is INFERRED and has not been seen (boot 27 saw them for 120.2 s without it)',
                   'what the acknowledge, let_panic and the withdrawn nodes do with the clock bound and a RAM-disk root to mount is INFERRED and has not been seen (boot 27 saw them for 120.2 s without either)'))


# ---- section 3 and 3c ------------------------------------------------------------------------------------------------------------------------------------------------------------------
def console_text(panel):
    """The Console paragraph of section 3c for this record: rung 8's, with the root's course among what the console shows."""
    return r8.console_text(panel)


def label(panel, label_text):
    """The label paragraph of section 3c: rung 8's, with the mark of this record's profile."""
    return patched(r8.label(panel, label_text), (f'the mark R- of the guest profile quiet-rtc (rung 7\'s label, {r8.BOOT27_LABEL}, had the mark {pp.PROFILES["quiet"][0]})',
                                                 f'the mark M- of the guest profile quiet-rtc-acm (rung 8\'s label, {r9b.RUNG8_LABEL}, had the mark R-)'))


def kind_lines(panel):
    """The lines that section 3 adds after the kind's own line: rung 8's, with the root in the guest's description."""
    return m1t.wrap(patched(r8.kind_lines(panel), ('with cpu_boot_timeout_secs=120 in its command line and the device tree of the guest profile quiet and the PMU\'s clock bound in PARAMS (quiet-rtc),',
                                                   f'with cpu_boot_timeout_secs={CPU_WAIT_S} and acm_fastsim=1 in its command line and the device tree of the guest profile quiet, the PMU\'s clock bound in PARAMS and a RAM-disk '
                                                   'root of 1.86 GB in its RAM (quiet-rtc-acm),')))


def in_short(panel):
    """The summary of section 3c."""
    d = panel['rung9']['diff']
    return ('In short. This record is rung 9, the first root-mount run, after rung 8 (boot 28: section 0a): the monitor, the acknowledge, let_panic, the panel, the six CPUs, the clock and the device tree '
            f'({d["nodes"][1]} of {d["nodes"][0]} nodes) are rung 8\'s; the guest has 6,144 MiB of RAM and Apple\'s root as its RAM disk ({d["root"]["chunks"]} of its {d["count"]} chunks), the command line has '
            f'cpu_boot_timeout_secs={CPU_WAIT_S} and acm_fastsim=1, the pair is the root-carry pair and the box is {panel["run"]["box_s"]} s (D5\'\'\'). Before the guest runs, the carry takes about two minutes: the runner\'s progress lines for 1.9 GB, a page that stands still for the sweep and the bounce, and the wrapper\'s digests and copy. What it expects, what falsifies that and what counts as a result are in section 5d, in the operator\'s words; this section is the evidence for them.')


def programs(panel):
    """The sentence of the introduction on the programs: the pair is the root-carry pair, which no earlier record names."""
    run = panel['run']
    return (f'The programs: SNAPMON-M1-ACK.BIN has the SHA-256 {run["monitor"][:16]}...: the wrapper with the acknowledging monitor (053a239f..., the monitor of rungs 5 to 8) built with the root-carry gate '
            f'(a file limit of 2 GiB in place of 256 MiB and a box cap of 1200 s in place of 120 s: SNAP_SP11_STICK_ROOT_CARRY and SP11_STICK_MONITOR_BOX_MAX_S, section 4). SNAPREP-M1-ACK.BIN has the SHA-256 '
            f'{run["reporter"][:16]}..., the reporter v3 of rungs 6 to 8 rebuilt with the same gate (it prints the same lines). The builder holds each acknowledging record to its pair by digest (M1_REPORT_MISMATCH '
            'for another pair): rung 9 is built with this pair and with no other, and the records of rungs 5 to 8 refuse it. The wrapper has not run on the unit.')


def intro(panel):
    """The introduction of section 3c: rung 8 is the run before; this record carries a root."""
    box_s = panel['run']['box_s']
    return (f'Rung 8 is the run before this one: the panel as the console with the CPU-start stores acknowledged, let_panic, cpu_boot_timeout_secs=120, the guest profile quiet-rtc and a box of 120 s, '
            f'record {BOOT28_RECORD}, boot 28 (section 0a: its result is not in this text); its approval is its own and this text neither gives nor assumes it. This record is rung 9: its guest is rung 8\'s with 6,144 MiB of RAM, Apple\'s root as its RAM disk and the root\'s '
            f'/chosen properties, with cpu_boot_timeout_secs=360 and acm_fastsim=1 in its command line; its pair is the root-carry pair, its box is {box_s} s (D5\'\'\', which is new and which approving this '
            f'text decides: section 0) and its runner is the one of the big-file mode. It is not decided: approving the SHA-256 of this record is that decision, and it covers ONE boot of this stick (section '
            f'5c, THIS APPROVAL COVERS ONE BOOT); rung 8\'s approval covers none of it. {programs(panel)} SNAPMON.BIN and SNAPREP.BIN differ from rung 8\'s; GUEST.DAT, PIN.DAT (the label and the digests) '
            'and PAYLOAD.MAN, which lists them, are this record\'s own. Section 1b has the programs\' digests and the NV2 record, which is the first run\'s. Section 5 has the rule that rung 2 had.')


def other_differences(panel):
    """The sentence of section 3c on what else differs: made from the builder's own chunk comparison with rung 8's guest (tools/sp11_stick_bundle_rung9.py compare)."""
    d, root = panel['rung9']['diff'], panel['rung9']['diff']['root']
    size, nodes, dt = d['guest_bytes'], d['nodes'], d['dt_bytes']
    resized = ', '.join(f'chunk {i} ({old:,} to {new:,} bytes)' for i, (old, new) in d['resized'].items())
    new = ', '.join(f'kind {k} at 0X{dest:X} ({n:,} bytes)' for k, dest, n in d['new'])
    return ('Other differences from the withheld guest follow from the panel\'s address, which lies below the bank: the guest\'s frame map and tables are wider (docs/SP11-STICK-REAL.md section 2: 46 chunks '
            f'against 44 in the host run that table records). From rung 8\'s guest (GUEST.DAT {BOOT28_GUEST}..., {size[0]:,} bytes, 46 chunks, the one that rung 8\'s record builds) this one has {d["count"]} '
            f'chunks, which the builder compares by SHA-256 with the table of rung 8\'s own bundle that it pins, and it refuses any other difference (PANEL_MISMATCH): {len(d["same"])} of rung 8\'s chunks are '
            f'rung 8\'s byte for byte (the kernel image, the monitors and the other tables among them); HANDOFF (chunks 0 and 1: the SPTM handoff page and boot_args) differs in {d["handoff_bytes"]} bytes '
            f'in {d["handoff_runs"]} places, which are the memory sizes, the device tree\'s length and the command line (its wait, 360, and its tail, " acm_fastsim=1"), and is rung 8\'s to the SHA-256 once they '
            f'are put back; PARAMS (chunk 2) differs in {d["params_words"]} words (the box, 300 for 120; the guest\'s end, the reserve and the monitor\'s memory ranges and virtual addresses, which follow the RAM '
            f'size) and in the three words of the static trust cache that rung 8 does not have, and is rung 8\'s to the SHA-256 once they are put back; DEVICE_TREE (chunk 3: {dt[0]:,} bytes in rung 8\'s, '
            f'{dt[1]:,} here; {nodes[0]} nodes in the base board, {nodes[1]} in the published tree) differs in the root\'s own /chosen properties and in no node, which the builder reads back property by '
            f'property (GUEST_PROFILE_READBACK); the monitor\'s data and the page tables that are sized for the larger RAM are {resized}; the zero chunk after chunk 35 is not in the table any more and '
            f'there are {len(d["new"])} new chunks: {new}. The other {d["outside"]} chunks that are not the root are these, and the root is {root["chunks"]} kind-OTHER chunks ({root["zero"]} of them ZERO, '
            f'{root["data_bytes"]:,} bytes of data and {root["zero_bytes"]:,} of zeros) that cover the RAM disk, 0X{root["dest"]:X} to 0X{root["end"]:X}, without a gap; the table has {d["table_room"]} entries '
            f'of its 1,024 to spare. GUEST.DAT is {size[1]:,} bytes, {size[1] - size[0]:,} more than rung 8\'s, and has its own SHA-256 (docs/SP11-STICK-REAL.md section 2).')


def word_text():
    """The paragraph of section 3c on the command line: the word is 360 and the box 300, and acm_fastsim=1 follows it."""
    return ('The extra words. The command line is the loader\'s with serial=2 and, after it, cpu_boot_timeout_secs=360 and acm_fastsim=1 (-v debug=0x8 serial=2 cluster_power=0 wdt=-1 '
            'cpu_boot_timeout_secs=360 acm_fastsim=1): the first from the builder\'s list (--boot-arg: cpus=1 with --cpu-start stop and cpu_boot_timeout_secs=120, =90 or =360 with --cpu-start ack, '
            'BOOT_ARG_REFUSED for any other word or pair), the second added by the loader for the profile quiet-rtc-acm and for a build with a RAM-disk root only (probes/credential.py software_cmdline), and '
            'both read back from boot_args by the builder (PANEL_MISMATCH for any other line). cpu_boot_timeout_secs is a string in the kernelcache and its variable is a 32-bit tunable with the default 1 '
            'and no clamp (read on the host, a file, not a run). What it does is INFERRED from the public source (tag xnu-12377.121.6, not this kernel\'s own): the kernel\'s CPU-boot thread calls '
            'processor_boot for all five secondary CPUs, and then waits for each in turn, CPU 1 first (processor_wait_for_start: a timed block of the word\'s seconds), and panics "cpu 1 failed to boot for '
            'the first time, waited N seconds" when it times out. The word is 360 so that the panic comes about 362 s after the MON CALL line (the wait starts about 2.3 s in, as boot 24\'s PMGR panic at '
            '2.25 s suggests: INFERRED), 62 s outside the box of 300 s, so that the box ends the run if nothing else does; rungs 5 to 7 had 120 or 90, which would have cut this run. The wait blocks '
            'only the CPU-boot thread and the starts of AppleARMWatchdogTimer, AppleProcessorTrace and AppleT8140CLPC, and nothing on the path to the root device or to launchd (read on the host in the '
            'kernelcache and the public source, a file and not a run); boots 25, 26 and 27 went on on the boot CPU (OBSERVED once each). wdt=-1 is the loader\'s own word and stays (the loader\'s line '
            'already ends with it), and cpus=1 is not in this line (boot 24 asserted in the PMGR driver: section 0a). acm_fastsim=1 makes the kernel\'s credential manager take its software path; the '
            'kernel reads it together with sepfw-load-at-boot, which the root\'s /chosen data sets to 0, and its strings include a timed wait for the SEP manager that the argument is INFERRED to avoid '
            '(read on the host); whether the mount needs it is UNKNOWN. EXPERIMENTAL, and a new operator decision.')


ACK_REACH = patched(r8.ACK_REACH, ('boot 25 ran 60 s past it, boot 26 61.6 s and boot 27 120.2 s (OBSERVED once each), and this record runs up to 120 s.',
                                   'boot 25 ran 60 s past it, boot 26 61.6 s and boot 27 120.2 s (OBSERVED once each), and this record runs up to 300 s.')) + (
    ' The RAM that the guest can reach is 6,144 MiB at 0X880000000 where rungs 1 to 8 had 384 MiB: the placement arithmetic of the builder keeps the placement, the reserve and the monitor\'s work area '
    'off the top 4 GiB of the bank, where the runner puts its data files (read on the host in tools/sp11_stick_bundle_root.py check_data_top, a file and not a run), and the wrapper\'s own check at '
    'run time (PLACE_DECLARED) is the authority; the monitor\'s map of RAM at that size has never run (UNKNOWN).')


def root_paragraphs(panel):
    """The paragraphs of section 3c on the root: what it is and how the guest finds it, what the builder checked, what the root cannot show, and how to tell this guest apart."""
    d, r = panel['rung9']['diff'], panel['rung9']
    root = d['root']
    what = (f'THE ROOT (EXPERIMENTAL; the guest profile {PROFILE}, mark M-, composed with rung 8\'s form). Apple\'s BaseSystem root, {root["size"]:,} bytes (SHA-256 7cab6d69..., the digest that the profile pins), is '
            f'in GUEST.DAT as {root["chunks"]} chunks of kind OTHER and is copied by the wrapper to 0X{root["dest"]:X}..0X{root["end"]:X}, inside the guest\'s RAM. The device tree\'s /chosen names it as the loader names '
            'a RAM-disk root for the A16 (probes/xnuboot.py, the iokit mode): boot-ramdmg-size and boot-ramdmg-extents (its size and its place), boot-uuid and apfs-preboot-uuid (the UUID of the root volume, '
            'A9037730... in the profile), the root hash (208 bytes, SHA-256 e3d407cc... as the profile pins it) in /chosen/secure-boot-hashes, and protected-data-access and sepfw-load-at-boot, both 0. The '
            'static trust cache (26,496 bytes, SHA-256 09c90cd2...) travels in PARAMS (three words). The builder reads the device tree back against the tree before the withdrawals and the root\'s own properties '
            'and refuses any other difference (GUEST_PROFILE_READBACK); it holds the guest\'s command line to the form (PANEL_MISMATCH) and the placement to the runner\'s data files (ROOT_PLACEMENT). '
            'Whether the stock kernel finds the root by these properties is INFERRED from names read on the host in the kernelcache (the loader\'s own contract for the A16, whose kernel is this one): '
            'the A16\'s first raw root stopped at the root-hash import (INFERRED from the A16 notes).')
    cannot = ('WHAT THE ROOT CANNOT SHOW (UNKNOWN unless a film says): that the kernel mounts it (the run may end at the wait for the media, at a panic in the import of the root hash or in the mount, at a '
              'wait for an AES, credential or SEP service, or at the first user process); that userspace finds what it needs on it (the image has no ISC, the volume that the first user process waits for: '
              'INFERRED); that 6,144 MiB of RAM is enough or too much (the A16 used 8,192 MiB; 4,096 MiB is INFERRED to hold the root with 2,083 MiB free); that a build of a profile with the credential '
              'manager\'s software mode changes the course at all (the mode is the A16\'s, added with its first RAM root).')
    tell = (f'TELLS IT APART (INFERRED; no root bundle has been booted): PIN label {r["label"]}; INDEX=2 on the PAYLOAD LOADED line shows BYTES={r["guest_bytes"]} SHA={r["guest_sha16"]} (rung 8\'s: '
            f'{r9b.RUNG8_GUEST_BYTES} bytes, SHA={BOOT28_GUEST}); the SNAPMON STAGE=PIN_OK line shows CHUNKS={d["count"]}; INDEX=3\'s SHA is PIN.DAT\'s, which holds this record\'s own SHA-256: no record names it.')
    return (what, cannot, tell)


def profile_paragraphs(panel):
    """The paragraphs of section 3c on the guest profile (the profile text of tools/sp11_stick_bundle_panel_profile.py has none for quiet-rtc-acm: the quiet board's are rung 7's, taken from it for the
    profile quiet, and the clock's are rung 8's): what the guest is against rung 8's, the nodes withdrawn, the clock, what is not claimed, the hazard and the result."""
    d = panel['rung9']['diff']
    quiet = pp.profile_text(pp.PREFIX + 'quiet', dict(kind='rung 7\'s form', let_panic=True, models=pp.MODELS, changes='-'))
    assert len(quiet) == 6 and quiet[1].startswith('WITHDRAWN:') and quiet[3].startswith('NOT CLAIMED:') and quiet[4].startswith('HAZARD') and quiet[5].startswith('RESULT'), 'the profile text of quiet has changed'
    changes = (f'{pp.LEAD}: {pp.PREFIX}quiet-rtc-acm, mark M-, composed with rung 9\'s form: the quiet profile\'s device tree ({d["nodes"][1]} of {d["nodes"][0]} nodes), the PMU\'s clock bound at nub-spmi0 as in '
               f'rung 8\'s profile quiet-rtc, and the credential manager\'s software mode, which adds acm_fastsim=1 to the command line when the loader builds a RAM-disk root and changes no node and no device model. '
               f'Against rung 8\'s own guest it has {d["count"]} chunks, {len(d["same"])} of them rung 8\'s byte for byte (Other differences, above), which the builder compares by SHA-256.')
    claimed = patched(quiet[3], ('that anything runs with these nodes withdrawn (EXPERIMENTAL, hardware NOT RUN); that it changes what the kernel waits for or stops at (UNKNOWN)',
                                 'that the withdrawn nodes are enough for a run longer than boot 27\'s 120.2 s (EXPERIMENTAL; the withdrawal ran once); that the clock, the credential mode and the root change what the '
                                 'kernel waits for or stops at (INFERRED: section 5d); that the placeholder calendar is harmless (UNKNOWN)'))
    return (changes, quiet[1], r8.CLOCK_TEXT, claimed, quiet[4], quiet[5])


def record_problems(text, panel):
    """What is wrong with `text`, the finished record of rung 9: the names, in order, of each sentence of pp.FALSE_FOR_A_PROFILE that it carries ('FALSE: ...') and 'MISSING' if a paragraph of
    profile_paragraphs is not in it. An empty list is a record that does not say what a profile guest makes false and holds the words of its profile."""
    flat = ' '.join(text.split())
    problems = [f'FALSE: {name}' for name, pattern in pp.FALSE_FOR_A_PROFILE if re.search(pattern, flat)]
    return problems + ['MISSING' for paragraph in profile_paragraphs(panel) if ' '.join(paragraph.split()) not in flat][:1]


def extra_paragraphs(panel):
    """The paragraphs of section 3c that only these records have, after the command line's: rung 6's (the PARAMS word, the acknowledge, what still stops the run, the panic path and the page), with this
    record's reach of the acknowledge, the profile's paragraphs and the root's at the end."""
    parts = r6.extra_paragraphs(panel)
    assert len(parts) == 6 and parts[2].startswith('What the acknowledge lets the guest reach.'), 'the paragraphs of rung 6 have changed under rung 9'
    params = patched(parts[0], ('for the records of rungs 4, 5 and 6 only', 'for the records of rungs 4 to 9 only'),
                     ('used here for the first time on the Surface Pro 11,', 'used on the Surface Pro 11 in boots 24 to 27 (OBSERVED once each),'))
    stops = patched(parts[3], ('MON STOP WATCHDOG (OBSERVED once: boot 20)', f'MON STOP WATCHDOG ({r8.WD_BOXES})'))
    return (params, *parts[1:2], ACK_REACH, stops, *parts[4:], *profile_paragraphs(panel), *root_paragraphs(panel))


def expect_text(run):
    """The paragraph of section 3c that stands where rung 3's and 4's what-the-run-may-end-as is: the prediction, the order of the classes and the falsifiers are section 5d's, in the operator's words."""
    return patched(r8.expect_text(run), ('NOT PASSED for rung 8 (section 5d)', 'NOT PASSED for rung 9 (section 5d)'),
                   ('from the host analysis of boots 24 to 27 and of the PMU\'s clock: none of it has been seen with the clock bound',
                    'from the host analysis of boots 24 to 27, of the PMU\'s clock and of the root: none of it has been seen with a root'))


def section_5d(panel, wait_s):
    """Section 5d of the record, the operator's words: tools/sp11_stick_bundle_panel_rung9d.py, which takes its constants from this module (so it is imported here, when asked)."""
    import sp11_stick_bundle_panel_rung9d as d
    return d.section_5d(panel, wait_s)


def __getattr__(name):
    """A hook or a constant that this module does not say again is rung 8's (screen_text, stop_seen, film_until and the rest)."""
    if name.startswith('__'):
        raise AttributeError(name)
    return getattr(r8, name)
