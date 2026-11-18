# SPDX-License-Identifier: MIT
"""Rung 2's own words, for the M1 record of the guest that is handed the panel as the kernel's console (D9's third choice, `--panel console`; docs/SP11-STICK-MONITOR.md
section 20): why this rung follows boot 20, what the run is likely to end as, the screen and what the public XNU source says of it, section 5d (what to film, what is new,
what counts as a result, in the operator's words) and what it shares with the first run. SHARED_WORDS are the words that both panel choices give the hooks of the guide
(tools/sp11_stick_bundle_record_m1_guide.py, `said`): a guest that paints is not silent, so the run of a panel choice is ended by the SNAPREP page not being on the screen
when the wait is up, and not by a page that stands still. The record of the first run (the panel withheld) has none of these words.

What boot 20 left is the stop-site analysis of that run, which is INFERRED and was not run again: the stop PC lies in a loop of five instructions that use registers only
(read on the host, a file and not a run: the kernelcache), which matches the progress-bar loop of PE_init_iokit in the public XNU source (pe_init.c, tag xnu-12377.121.6; the
source of this kernel is not public, so the match is of structure only). Names and a description only: no disassembly and no vendor byte is held here or printed in the record.

`facts` builds the entry that the builder adds to the panel's dict (`run`): this record's time box and programs, held against the first run's. Every sentence below that
depends on them is derived from it, so that a record with another box or another wrapper says so instead of repeating the first run's words.

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402

# PC= of boot 20's MON STOP WATCHDOG line (OBSERVED, one unit, one boot). It is the first of the five instructions of the loop, so the loop's own addresses are the
# next four words (INFERRED from the size of the loop; every instruction is four bytes).
STOP_PC = 0xFFFFFE000C1EEF00
LOOP_PCS = tuple(STOP_PC + 4 * i for i in range(5))
# What the first run was made with: the box D5 decided and the digests of the two programs that its approved record named (section 1 of every record has the full ones).
RUNG1_BOX_S = 5
RUNG1_MONITOR = '1d32f5071506a00c91be8c7e218a6db9ba6d280c1cf25be31aac41a3c599171c'
RUNG1_REPORTER = 'ee86676001d89cf653af7bdddd797fef3c54d31b28ee42eb4a2d7aa692ee4b3b'
CALL_S = f'{RUNG1_BOX_S + m1t.PREPARE_AND_CLEAN_S:.2f}'      # boot 20: the call returned this long after the MON CALL line (OBSERVED once: its TICKS, 96,720,173 at 19.2 MHz)
CONSOLE_FINDING = ('a panel that stays blank (neither the runner\'s page nor kernel text), frozen (the UPTIME line stops counting) or noisy (pixels that are neither '
                   'the runner\'s glyphs nor the kernel\'s) after the report is a finding')


def pc_text(pc):
    """A 64-bit number as the page prints it (0X and capitals: probes/sp11-stick-common.h sc_key_hex)."""
    return f'0X{pc:016X}'


def facts(box_s, monitor_sha, reporter_sha):
    """The `run` entry of the panel's dict: this record's box and programs, and whether each is the first run's."""
    return dict(box_s=box_s, monitor=monitor_sha, reporter=reporter_sha, same_box=box_s == RUNG1_BOX_S, same_monitor=monitor_sha == RUNG1_MONITOR,
                same_reporter=reporter_sha == RUNG1_REPORTER)


def differs(run):
    """What of this record's box and programs is not the first run's, as phrases; empty when the pair and the box are boot 20's."""
    out = []
    if not run['same_box']:
        out.append(f'its time box is {run["box_s"]} s where the first run\'s was {RUNG1_BOX_S} s')
    if not run['same_monitor']:
        out.append(f'SNAPMON-M1.BIN has the SHA-256 {run["monitor"][:16]}... where boot 20 ran {RUNG1_MONITOR[:16]}...')
    if not run['same_reporter']:
        out.append(f'SNAPREP-M1.BIN has the SHA-256 {run["reporter"][:16]}... where boot 20 ran {RUNG1_REPORTER[:16]}...')
    return out


def same_text(run):
    """What the choice shares with the first run, for the introduction of section 3c: the first run's pair and box, or the differences, said."""
    panel_itself = ('The panel needs no change of the monitor, the wrapper or the reporter (it is an input of the guest-image build, SNAP_NO_FB, and no flag of the monitor: '
                    'docs/SP11-MONITOR-PROFILE.md section 3)')
    if not (diff := differs(run)):
        return (f'{panel_itself}, and this record changes none of them: SNAPMON-M1.BIN and SNAPREP-M1.BIN have the SHA-256 that boot 20 ran ({RUNG1_MONITOR[:8]}... and '
                f'{RUNG1_REPORTER[:8]}..., in full in section 1), the time box is {RUNG1_BOX_S} s and the store classes are the first run\'s. Only GUEST.DAT (the panel range in '
                'PARAMS, the video block and the command line of boot_args, and the frame map and tables that follow from the range) and PIN.DAT (the label and the digests) '
                'differ, and PAYLOAD.MAN, which lists them; everything of section 1b is the first run\'s. Section 5 has one rule more.')
    return (f'{panel_itself}, but this record is NOT the first run with the guest changed alone: {"; ".join(diff)}. Section 0 and section 4 say what that adds; everything of '
            'section 1b is the first run\'s. Section 5 has one rule more.')


def hazard_lead(run):
    """The lead of 'what is new' in section 5d: what the panel itself leaves unchanged, and where the other differences are said."""
    return ('It adds no store class, no device and no MMIO (INFERRED from the sources; never run), and the panel itself changes neither the monitor, the wrapper, the reporter '
            'nor the time box' + (' (what else this record changes from the first run is said at the start of section 3c)' if differs(run) else '') + '.')


def why_lines(real):
    """Section 0's paragraph for this rung: why the panel, the one change of this record, the prediction and what falsifies it. The stop-site analysis itself is section
    0a's (stand_extra). The programs are said only when the record's facts carry the `run` entry (the builder always adds it; a hand-made `real` of a test may not)."""
    run = real.get('panel_facts', {}).get('run')
    loop = f'{pc_text(LOOP_PCS[0])}, or one of the next four instructions, up to {pc_text(LOOP_PCS[-1])}'
    programs = '' if run is None else (' The two programs and the time box are boot 20\'s (section 3c).' if not differs(run) else
                                       f' The two programs and the box are NOT all boot 20\'s: {"; ".join(differs(run))}. Section 3c and section 4 say what that adds.')
    return m1t.wrap('WHY THE PANEL IS HANDED NOW. Boot 20\'s guest was stuck, not slow: it stopped in a loop that cannot end while its video block is all zero, which D9\'s '
                    'first choice leaves it (INFERRED; section 0a has the analysis). THIS RECORD CHANGES TWO THINGS IN THE GUEST, WHICH BELONG TOGETHER: it hands the guest the '
                    'panel, so that its video block is not zero, and it sends the kernel\'s console to the panel (serial=2).' + programs + ' It does NOT add -noprogress, a third '
                    'change that could end the spin by itself and so leave the result without a cause. PREDICTION (INFERRED): the PC= of the report is not in that loop, and the '
                    f'kernel prints more than the banner it printed in boot 20. FALSIFIER: a PC= in that loop again ({loop}): the panel did not remove the spin, rung 2 is NOT '
                    'PASSED and the next try is -noprogress, in a record of its own. Section 3c has the other ends and section 5d what passes it.')


def stand_extra(real):
    """The paragraph that section 0a gets for this rung (its `stand` words): the stop-site analysis of boot 20's address, which section 0 points to."""
    return m1t.wrap(f'The stop-site analysis of that address, the coordinator\'s (INFERRED, not run again): {pc_text(STOP_PC)} is the first of five instructions that use '
                    'registers only and loop, read on the host (a file, not a run) in the kernelcache. That this is the progress-bar loop of PE_init_iokit is INFERRED, from a '
                    'match of structure with the public XNU source (read from pe_init.c, tag xnu-12377.121.6; this kernel\'s own source is not public), and that loop has no exit '
                    'while the display size is 0. The guest of boot 20 had an all-zero boot_args.Video, because D9 withheld the panel, and its command line has no -noprogress; '
                    'no register was printed, so a display size of 0 is INFERRED too. So that guest was stuck and not slow (INFERRED), and a longer time box '
                    'on that image would only repeat the spin (INFERRED).')


def expect_text(run):
    """The paragraph of section 3c on what the run is likely to end as if the loop is gone."""
    return (f'What the run may end as, if the loop is gone (INFERRED from the stop-site analysis of boot 20, section 0: none of it has been seen, and which comes first, '
            'and when, is UNKNOWN). MON STOP S2_FAULT with A= an IPA: the kernel\'s first access to a device that the monitor does not model, most likely its first CPU-start '
            'write to the PMGR block (docs/SP11-MONITOR-PORT.md section 7; compat/boot-relocate.h:147-153); section 5a counts that as a SUCCESS for M1, because it shows that the '
            f'kernel got that far. MON STOP WATCHDOG at the end of the {run["box_s"]} s box with the kernel still booting: the film then shows how far it got. MON STOP PANIC_ENTRY: '
            'the kernel\'s own panic text on the panel, which is to be filmed, and a finding of section 5a; a panic that a CPU failed to boot, about 1 s after a CPU-start '
            'write, is plausible only if that write were accepted, and with x4 = 0 it falls to stage 2 and stops the run as above. MON STOP GUEST_EXC: an exception that the '
            'kernel took. In every case the operator does nothing but film (section 5d).')


def force_off(wait_s):
    """The rule that ends the run of a guest that keeps painting, which the silence rule of section 5 does not: it is the SNAPREP page that ends the wait, and not a page that
    stands still. `wait_s` is the silence rule's wait for the box (the guide's silence_s)."""
    return (f'Whatever the guest paints, if the SNAPREP page is not on the screen {wait_s} seconds after the MON CALL line, FORCE OFF (boot 20, with a {RUNG1_BOX_S} s box: the '
            f'call returned {CALL_S} s after the MON CALL line, OBSERVED once)')


# The words that the hooks of the guide read for a panel choice (`said`: a string, or a function of the facts of the record, the time box and the wait). The first run's
# words have none of them.
def hazard_console(real, box_s, wait_s):
    """The panel's bullet of 0b for the panel as the console."""
    return ('The guest can write the unit\'s framebuffer, which the display scans out, and the kernel is expected to draw its console there (INFERRED): from the MON CALL line a '
            'dark panel is EXPECTED, because the kernel\'s console clears the whole panel when it takes it (read in the public source, not this kernel\'s own; when, inside the '
            'time box, is UNKNOWN), and its text can paint over the page, so that the report can be hard to read. The film is the only record of that text and of the lines '
            f'printed before the guest ran (section 5d). {force_off(wait_s)}.')


def hazard_handed(real, box_s, wait_s):
    """The panel's bullet of 0b for the panel handed."""
    return ('The guest can write the unit\'s framebuffer, which the display scans out: what it paints can paint over the page and make the report hard to read, and the film is '
            f'the only record of it (section 3c). {force_off(wait_s)}.')


def runaway(wait_s, hold_s):
    """The runaway case of 0b and section 4: the least time that the CPU stays loaded when the stop timer never fires, for a guest that keeps painting."""
    return (f'one CPU stays loaded, at worst, until the operator acts: {wait_s} s after the MON CALL line, whatever the guest paints (section 5), plus about {hold_s} s holding '
            f'Power, {wait_s + hold_s} s or more, with no temperature read; approving this text accepts that too.')


def hang(real, box_s, wait_s):
    """5a, M1 FAILED: no controlled stop, for a guest that paints. A panel that is still dark at the wait is not this case: it is NOT A RESULT (no_result), and neither is a page
    that shows STAGE=DONE without the SNAPREP page (section 5: no result)."""
    return (f'a lit screen with neither STAGE=DONE nor the SNAPREP page {wait_s} s after the MON CALL line, whatever the guest paints (no controlled stop: row d; a panel still '
            'dark then is NOT A RESULT, below)')


def no_result(real, box_s, wait_s):
    """5a, NOT A RESULT: the dark panel of a panel choice is dark for a wait, not for a moment."""
    return (f'a black panel before the MON CALL line, a panel still dark {wait_s} seconds after it, or a restart of the Surface, before STAGE=DONE and the SNAPREP page have been '
            'on the screen, or a film that does not show them')


def row_e(real, box_s, wait_s):
    """5a, row e: TICKS also holds the monitor's two reads of the panel range (a hand-made `real` of a test that carries no panel facts gets nothing)."""
    span = (real.get('panel_facts') or {}).get('fb_span')
    if not span:
        return ''
    return (f' and, for a guest that is handed the panel, the monitor\'s two reads of the panel range (EL2 loops of {span // 8} loads of 8 bytes, in its prepare before the timer '
            f'is armed and at the stop after it is off, and at the stop, if SCTLR_EL1 is not 0, DC CIVAC over {span // 64} lines: probes/xnuboot/prepare.c:578-581, '
            'probes/xnuboot/report.c:243-250, probes/xnuboot/context.c:799-806, probes/xnuboot/mmu_access.c:193-198; they are not in the time box but are in TICKS and in '
            'the operator\'s silence rules, and their cost is UNKNOWN)')


STILL = 'the runner\'s page does not change until it returns, but the guest may paint over it (the screen may go black and fill with text)'
WAIT = 'the prepare, the box and the stop'
STEP6 = ' For this choice wait ten seconds on the final screen before that FORCE OFF (a margin, not a measurement).'
COUNT = 'Count those {wait} seconds from the moment the MON CALL line is on the screen, on a watch or the camera\'s timer.'


def step4(guest, label):
    """5c, step 4: the programs' digits are the first run's too, so the GUEST.DAT line and the label and chunk count of this bundle are compared as well."""
    return (f' Then compare, on the PAYLOAD LOADED line, INDEX=2 ... BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, '
            f'LABEL={label} CHUNKS={guest["chunks"]} (boot 20\'s page had LABEL=M1RUN-RUNG1 CHUNKS=44: the first run\'s bundle staged under this plan has the same programs and '
            'would pass the comparison above).')


def step5(real, box_s, wait_s):
    """5c, step 5: what the screen does from the MON CALL line, and the wait, which a dark panel cannot show: it is counted from the MON CALL line."""
    return (f' From the MON CALL line the screen may go black and fill with text: that is expected. {force_off(wait_s)}. {COUNT.format(wait=wait_s)}')


def step7_console(real, box_s, wait_s):
    """5c, step 7: the dark panel of the panel as the console is not the black panel of this step until the wait is up."""
    return (' For this choice a dark panel from the MON CALL line is not this case, since the kernel\'s console clears it: it is this case when the panel is still dark '
            f'{wait_s} seconds after the MON CALL line (section 5).')


def step7_handed(real, box_s, wait_s):
    """5c, step 7, for the panel handed."""
    return (' For this choice a dark panel while the guest runs is not this case, since the guest may clear it: it is this case when the panel is still dark '
            f'{wait_s} seconds after the MON CALL line (section 5).')


SHARED_WORDS = dict(runaway=runaway, hang=hang, no_result=no_result, row_e=row_e, still=STILL, wait=WAIT, step4=step4, step5=step5, step6=STEP6)


def stop_rule(wait_s, box_s):
    """The panel's bullet of section 5 for the panel as the console; `wait_s` is the silence rule's wait for this box (the guide's silence_s) and `box_s` the box."""
    return m1t.wrap(f'- Panel as the console (sections 3c and 5d): {force_off(wait_s)}. What the guest paints, the kernel\'s text included, does not count as a page change for '
                    'the silence rule above. From the MON CALL line the screen may go black and fill with the kernel\'s text: that is expected (the kernel\'s console clears the '
                    f'whole panel when it takes it: read in the public source, not this kernel\'s own; when, inside the {box_s} s, is UNKNOWN), and it can paint over the '
                    'wrapper\'s lines: note the last line that can still be read and what the screen shows, and keep the film, which is the only record of the kernel\'s text '
                    '(the MON CON lines are not expected to carry it). A dark panel before the wait is up is not a restart and not the black panel of step 7 of section 5c or of '
                    f'the NOT A RESULT page of section 5a; the panel still dark {wait_s} seconds after the MON CALL line, or a restart (the Shell\'s countdown or the runner\'s '
                    'first lines on the screen again), before the report, is that case. The guest\'s touches of the panel range are expected and no finding; a touch outside RAM '
                    'and outside that range still is. The runner\'s glyphs and the kernel\'s in the same cells are expected and no finding; '
                    + CONSOLE_FINDING + ': film it, say what you saw, and FORCE OFF as step 6 says.', 3, 5)


# Step 2 of section 5c for this choice (the guide's `film` key, after the whole-panel sentence): the kernel's glyphs cannot be enlarged from this side (public_text), so a
# second camera on the top-left quarter is asked for, and one camera keeps the whole panel.
FILM_STEP = (' That is the main camera: the pages, PAYLOAD CALL 0 and the PC= line are on it. For this choice the kernel\'s text is small (section 3c), so if there is a second '
             'camera, film the TOP-LEFT quarter of the panel close up for the same span, held steady, with focus and exposure locked, in a dim room. With one camera only, film '
             'the whole panel at the best resolution it gives. The report page stays the primary record; the kernel\'s text is expected from the top-left cell (INFERRED).')
POINTER_5D = ('What to film, the new hazards of this choice and the results are in section 5d, in the operator\'s words; this section is the evidence for them.')


def screen_text(height, width):
    """The paragraph 'The screen' of section 3c for the panel as the console: what the runner's page does to the same pixels and what that leaves of the kernel's text."""
    return ('The screen. What the runner\'s page does to the same pixels, read in q1n1\'s runner (platform/uefi/main.c: screen_char, payload_emit, sp11_draw_uptime '
            'and the clear at the runner\'s start; platform/uefi/sp11-payload-text.c: q1n1_payload_region_place), INFERRED for this run because nothing of it was run '
            'and this record does not check that source: the runner paints the whole panel dark once, when it starts, and never clears the panel again. It draws its '
            'own page at the left, from (20, 20), in glyphs of 5 x 7 pixels at scale 3 (cells of 18 x 27 pixels), and an emblem 256 pixels square at the top right. '
            'The lines of the wrapper and of SNAPREP-M1.BIN go to a region at the right of that page, below the emblem; the runner\'s formula puts it, for this '
            'panel\'s 2880 x 1920 pixels, at x = 1664, y = 308, at scale 2 (cells of 12 x 18 pixels), 89 rows. A character is drawn as foreground pixels only '
            '(colour 0xe8edf5): no background, nothing erased, and a line past the region\'s last row is mirrored to the UART only. So the report that SNAPREP-M1.BIN '
            'draws after the stop only adds glyph pixels over whatever the kernel left in the region: it clears nothing. After the report the runner\'s heartbeat '
            'redraws, once a second, a blinking block at the bottom left and its UPTIME line at (20, 74), clearing that line\'s 576 x 27 pixels first. The kernel\'s '
            'text starts at the top of the panel, where the runner\'s own page is, and is expected to draw over it: that is no fault, and the kernel\'s console is expected to '
            'CLEAR the whole panel when it takes it (read in the public source, below; INFERRED for this kernel), which wipes the runner\'s page and the wrapper\'s lines '
            'drawn before the call: only the film holds them. Whether the kernel\'s text reaches the runner\'s region is UNKNOWN; where the kernel\'s glyphs lie under the '
            'report\'s the two mix and may not be legible. The kernel draws its text only while the '
            'guest runs, which is at most the time box and starts after the monitor\'s prepare; what it leaves stays in the framebuffer until something paints over it '
            '(the report\'s glyphs and the rectangle of 576 x 27 pixels at (20, 74) that the heartbeat clears each second, above), so that the screen shows it after the '
            'stop too (INFERRED). The console scrolls when its text passes the last row of the panel. Its cell is 8 x 16 pixels, read on the host (a file, not a run) in '
            'the kernelcache: its initialize_screen, at 0xfffffe000ba55740, sets the console\'s rows to the height shifted right by 4 and its columns to the width shifted '
            f'right by 3, so {height // 16} rows of {width // 8} columns on this panel, whatever the scale (the public source, below), about 0.8 x 1.5 mm on this 13-inch '
            'panel (INFERRED from its nominal size, docs/SURFACE-PRO-11.md section 4.1), where the runner\'s own lines are 12 x 18 pixels or more. After a long text the panel '
            'holds only the last rows, and what scrolled off, the wrapper\'s lines of before the call included, is on the film only (INFERRED).')


def public_text():
    """The paragraph of section 3c on what the public XNU source says of the console: the glyphs, where the text starts and the clear of the whole screen, which the film's
    framing and the dark panel of section 5 rest on."""
    return ('What the public source says of the console (read from the public source: video_console.c, boot.h and pe_init.c of tag xnu-12377.121.6, not this '
            'kernel\'s own, whose source is not public; so INFERRED for this kernel). The text is drawn in cells of ISO_CHAR_WIDTH by ISO_CHAR_HEIGHT pixels, 8 x 16 in the '
            'kernelcache (above), and the rows and columns are the height and the width of the panel divided by them (vc_initialize, video_console.c:2785-2786). The scale '
            'that the depth word of boot_args.Video carries, its bits 16 to 23 plus 1 (depth is bits 0 to 7, rotation 8 to 15, boot rotation 24 to 31: boot.h:33-37, read in '
            'pe_init.c:449-460), reaches only the progress-bar images (vc_uiscale, video_console.c:2524, 2664-2666 and 2696-2697): the text is 8 x 16 pixels at any scale and '
            'cannot be enlarged from this side. The loader\'s depth word is 32: depth 32, no rotation, scale 1. For Video.v_display 0 the boot picks the text mode '
            '(pe_init.c:555-558); the screen is then taken (kPEAcquireScreen) and the console enabled (gc_enable), which resets the cursor to column 0, row 0 and clears the '
            'WHOLE screen to the background colour (video_console.c:436-440, 484 and 958), and vcattach then replays the kernel\'s message buffer on it (video_console.c:3082-3098). '
            'So the text is expected from the top-left cell, a dark panel from the MON CALL line is EXPECTED, and the runner\'s page and the wrapper\'s lines of before the call '
            'are wiped from the panel: only the film holds them (all INFERRED for this kernel; when this happens, inside the time box, is UNKNOWN).')


def results_paragraphs(run, wait_s):
    """The results of the record, one paragraph each, in the operator's words: M1's verdict is section 5a's, and the named results of rung 2 are in terms of the stop report, its
    PC= and the film of either camera. `wait_s` is the silence rule's wait for this record's box."""
    loop = f'{pc_text(LOOP_PCS[0])} to {pc_text(LOOP_PCS[-1])}'
    return (
        'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 2\'s, with one more condition: the panel did not end the run. A STOP REPORT is the SNAPREP '
        'page with MON STATUS=OK ENTERED=0X1 and a MON STOP line of any kind; it is CLEAN when none of MON REGS RESTORED=NO, WINDOW OVERRUN, STACK FULL or GIC RESTORE CLEAN=NO '
        f'is on it. The loop of boot 20 is the five instructions from {loop} (section 0); on GUEST_EXC the PC= is the guest\'s x30, so read C= (section 5b). The film, where a '
        'result names it, is the film of either camera: the close-up of a second camera counts as much as the whole panel\'s (step 2 of section 5c).',
        'RUNG 2 PASSED: a stop report with a PC= that is not in that loop, and the film of either camera shows kernel glyphs on the panel, in any frame and not only the last '
        '(scrolled text counts), beyond the lines that the runner drew before the call. The PC= says only that it is not in the loop: the coordinator says from the kernelcache '
        'whether it is past it.',
        'RUNG 2 PARTIAL: a stop report, clean or not (say which), with a PC= that is not in that loop, and no kernel glyph that can be read in the film of either camera: the aim '
        'is not met and why is UNKNOWN (the console did not draw within the box, was cleared again, or is not there); the MON CON lines are not expected to say.',
        'RUNG 2 NOT PASSED: a PC= in that loop again (the falsifier of section 0: the panel did not remove the spin); or a MON STOP S2_FAULT with A= inside the panel range '
        '(section 2: a finding about the panel, not the success that section 5a gives for a fault elsewhere); or the panel still dark '
        f'{wait_s} seconds after the MON CALL line, or the Surface restarts, before the report; or no report (a page that stops at MON CALL, a REFUSED line, MON PREPARE FAILED, '
        'a fault page of q1n1). Glyphs seen before such an end are an observation to hand back, not a pass.',
        'RUNG 2 NOT A RESULT: a film that does not let the coordinator read MON STOP (too soft, too dark, or the report unreadable under the kernel\'s text): nothing is concluded '
        'from it, and the one-boot rule of section 5c says whether the approval is spent.',
        'A run that spins in that loop again is still a MON STOP WATCHDOG with a kernel PC=, which section 5a calls M1 PASSED, and it is rung 2 not passed: the two verdicts are '
        'separate. A run that ends differently from rung 1, on the same stick otherwise, is the finding that matters for the panel.')


def section_5d(panel, wait_s):
    """Section 5d of the record of the panel as the console, after 5c: what to film, what to expect, what is new beyond 0b and what counts as a result of rung 2, in the
    operator's words (at most 60 lines). Section 3c is the evidence for it. `panel` is read_panel's dict (with `run`), `wait_s` the silence rule's wait for the box."""
    run, span = panel['run'], panel['fb_span']
    film = ('THE FILM is the only record of the kernel\'s text: the console draws only while the guest runs, the MON CON lines are not expected to carry it, and nothing is '
            'written to the stick (row f of section 5a). The main camera is on the whole panel, sharp enough to read small text, for the span that step 2 of section 5c gives. '
            'It must show the AUTORUN lines (steps 3 and 4); PAYLOAD CALL 0, which only the film can show once the kernel has cleared the panel (if the film does not show it, '
            'the approval is spent); the panel while the guest runs; and the SNAPREP report, with MON STOP and MON EXIT, whose PC= decides the result, and the first UPTIME '
            'heartbeat after it. A second camera, if there is one, is on the TOP-LEFT quarter of the panel, close up, for the same span, steady, with focus and exposure locked, '
            'in a dim room: the kernel\'s glyphs are 8 x 16 pixels, about 0.8 x 1.5 mm, and cannot be enlarged from our side (INFERRED; section 3c); a 4K frame of the whole panel gives a '
            'glyph of about 9 x 18 pixels, which is marginal, and of the quarter about 18 x 36. With one camera only, film the whole panel at the best resolution it gives; the '
            'report page stays the primary record. Film 30 frames a second or more (29.97 is fine: boot 20\'s film had it; 60 if the camera can): lines that scroll between two '
            'frames are not in it. Stay on the final screen for ten seconds before the FORCE OFF of step 6, and hand every video back as recorded, not trimmed or re-encoded. '
            'Kernel glyphs are text that is not the runner\'s capitals and digits: once the runner\'s page is up, any lowercase letter on the panel is the kernel\'s (INFERRED from '
            'section 5b: the runner\'s font has capitals only). A photograph taken afterwards is not enough: the heartbeat clears part of what the kernel left.')
    expect = ('WHAT TO EXPECT. From the MON CALL line the screen may go black and fill with the kernel\'s text: that is expected (the kernel\'s console clears the whole panel '
              'when it takes it: read in the public source, not this kernel\'s own; section 3c). The text can paint over the page, and the report can then be hard to read: the '
              f'film holds it. The operator does nothing but film. {force_off(wait_s)}. {COUNT.format(wait=wait_s)}')
    new = (f'WHAT IS NEW beyond section 0b. {hazard_lead(run)} The monitor reads the panel range twice and cleans it at the stop ({span // 8} loads of 8 bytes and {span // 64} '
           'lines of DC CIVAC, at EL2, outside the time box): the cost is UNKNOWN, so the report can come later than the same box without the panel would give, and TICKS= can '
           'exceed the box (row e of section 5a). The kernel\'s video-console code runs on this unit for the first time: what it does here is UNKNOWN. '
           + CONSOLE_FINDING[0].upper() + CONSOLE_FINDING[1:] + ': film it, say what you saw, and FORCE OFF as step 6 says. The console text is on the panel or nowhere '
           '(INFERRED): a panel with none of it leaves the run without kernel text.')
    return ('5d. The panel as the kernel\'s console: what to film, what is new, and what counts as a result (rung 2)',
            *(line for text in (film, expect, new, *results_paragraphs(run, wait_s)) for line in m1t.wrap(text, 3, 6)))
