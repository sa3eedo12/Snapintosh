# SPDX-License-Identifier: MIT
"""The sections of the record of a monitor entry run (--m1-run) that tell the operator what he decides, what can go wrong, what a pass looks like, what each
line of the pages means and what he does, in the order he does it. They are in a module of their own so that tools/sp11_stick_bundle_record.py and
tools/sp11_stick_bundle_record_m1.py (the touches, the device families, the gic class) stay under their size limit, and so that the words that depend on the
panel (D9) are in one table, PANEL, which the panel variant of the run extends: a panel that the table does not know is a KeyError, never a text that says
something false for it.

 - reading_lines is the paragraph before section 0: which sections the operator reads, and who the coordinator is (defined at its first use).
 - stand_lines is section 0a: where things stand, with its labels and nothing beyond them.
 - decision_lines is section 0: that approving the SHA-256 of the text IS decision D4, and the decisions already taken, with their dates.
 - hazard_lines is section 0b, in plain words.
 - outcome_lines is section 5a: M1 as docs/SP11-MONITOR-PORT.md sections 2 and 14 define it, the expected ends, and the pages that are a pass, a finding, a failure
   and no result.
 - page_lines is section 5b: the lines of the pages. tools/test-sp11-stick-monitor-text.py reads the C sources of the wrapper and the reporter and fails when a
   line prefix (PAGE_LINES), a kind of stop (STOP_KINDS) or a status name (GATE_STATUSES, SP11_STATUSES) that the text gives is not in them.
 - action_lines is section 5c: what the coordinator stages and what the operator does, in order. The digits it asks him to compare are the first eight bytes of the payload digests.
 - PRE_CALL_SILENCE, RESTART_AND_STAGE2_RULES and HARDWARE_RULES are the bullets of section 5 that only this kind has or words differently.

Every number that the text takes from a build (digests, the ID registers of the NV2 record, the time box, the budget) comes in through the arguments; nothing
here reads a file. The text names no person, and uses no label of a host-side result that tools/test-sp11-stick-monitor-auth.py forbids.

Evidence: EXPERIMENTAL, hardware NOT RUN. Nothing here approves anything."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel as pn  # noqa: E402

wrap, WIDTH = m1t.wrap, m1t.WIDTH
TICKS_PER_S = 19_200_000        # CNTFRQ_EL0 of the unit (OBSERVED, boots 3 to 14): the unit of MON RET's TICKS=
MARKER = 'SP11\\DONE1.TXT'      # the file the Shell writes before the step starts (docs/RUNNING.md, autorun)
SILENCE_BEFORE_CALL_S = 15      # the silence rule of the GIC dry run (docs/SP11-STICK-GIC.md section 11), kept for the stages that come before the call
SILENCE_BEFORE_PAYLOAD_MIN = 2  # the operator's wait while the runner loads and hashes the files (its length is UNKNOWN: nothing of M1 has run yet)
SILENCE_AFTER_PAGE_S = 60       # the operator's wait after the STAGE=DONE line (the report page and the heartbeat come within seconds); the box does not enter
D5_BOX_S = 5                    # the time box that D5 (2026-11-16) and D8 (2026-11-17) decided: not the builder's cap (sp11_stick_bundle_m1.BOX_MAX_S)
SILENCE_MARGIN_S = 60           # what the operator waits beyond the end of the box before FORCE OFF after the MON CALL line (not a measurement)
HOLD_S = 20                     # the hold of Power that FORCE OFF asks for


def silence_s(box_s):
    """The operator's wait, in s, after the MON CALL line before FORCE OFF, and the film's end after the last page change: the 60 s of the text approved for the decided
    box (a box of 5 s or less), the box plus 60 s for a longer one, because the page does not change while the guest runs."""
    return SILENCE_MARGIN_S if box_s <= D5_BOX_S else box_s + SILENCE_MARGIN_S


def runaway_words(box_s, real=None):
    """The runaway case as 0b and section 4 say it, after `one CPU stays loaded`: the least time that the CPU stays loaded when the stop timer never fires and the
    operator acts by the rules, the silence plus the hold. A panel choice says the wait in its own words (its `runaway`, a function of the wait and the hold): a guest that
    keeps painting is not silent."""
    wait = silence_s(box_s)
    if words(real).get('runaway'):
        return words(real)['runaway'](wait, HOLD_S)
    return (f'one CPU stays loaded, at worst, until the operator acts: {wait} s of silence (section 5) plus about {HOLD_S} s holding Power, {wait + HOLD_S} s or more, with '
            'no temperature read; approving this text accepts that too.')


def d8_box(box_s):
    """The box that D8 accepted (one CPU, 5 s, AC power), and this record's beside it when it is another."""
    if box_s == D5_BOX_S:
        return f'a time box of {box_s} s'
    return f'a time box of {D5_BOX_S} s' + (f' at most (this record: {box_s} s)' if box_s < D5_BOX_S else f' (this record: {box_s} s, longer: see above)')


def d5_words(box_s):
    """D5 as decided (5 s at most) and this record's box beside it."""
    return f'D5 the time box is {D5_BOX_S} s at most (this record: {box_s} s{", longer: see above" if box_s > D5_BOX_S else ""})'


def longer_box_lines(box_s, real=None):
    """A box longer than the decided one is decided by approving this text, and the paragraph says so in capitals, and what the extra seconds buy (the three
    omissions that the review of the box cap found: the log, the kernel without its timers, the restart risk); for a box up to it there is nothing to say. A panel choice
    may word the decision and the middle one (`box_decision`, `idle`)."""
    if box_s <= D5_BOX_S:
        return ()
    return wrap(f'THIS RECORD\'S TIME BOX IS {box_s} s, LONGER THAN THE {D5_BOX_S} s THAT D5 AND D8 DECIDED: {said(real, "box_decision", box_s) or f"approving this text decides this box and the exposure that section 4 gives, which D8 accepted for a box of {D5_BOX_S} s only"}. The rest of D5 and D8 holds: one CPU, AC power REQUIRED. What the extra seconds buy is '
                f'limited: the log keeps the first 512 events whatever the box (section 5b), {said(real, "idle") or "the kernel may only idle without its timer interrupts (section 0b)"}, '
                f'and whether the Surface restarts inside a box of {box_s} s is UNKNOWN (section 4).')


def d8_exposure_words(box_s, real=None):
    """Beside the exposure numbers of a box longer than the decided one (0b and section 4): D8 accepted that exposure for the decided box only, so approving this text
    decides this one, unless the choice words it (`d8_decision`). Nothing for a box up to it."""
    if box_s <= D5_BOX_S:
        return ''
    return said(real, 'd8_decision', box_s) or (
        f'D8 accepted the exposure of one CPU for a box of {D5_BOX_S} s only: for this box of {box_s} s it is the operator\'s decision, and approving this text '
        'makes it (section 0).')


def restart_risk_words(box_s, real=None):
    """0b: the restart risk past the decided box (the panel's `restart` words say what a run showed)."""
    if box_s <= D5_BOX_S:
        return ''
    return said(real, 'restart') or f'Inside a box of {box_s} s with the stock kernel running, whether the Surface restarts is UNTESTED: UNKNOWN (section 4).'


def restart_control_words(box_s, real=None):
    """Section 4, after the PSCI-only control: the panel's `restart_c` words say what a longer run showed."""
    if box_s <= D5_BOX_S:
        return ''
    return said(real, 'restart_c') or (
        'That control ran no guest; the one run with the stock kernel (boot 20) went through its 5 s and to the report page without a restart (OBSERVED once). '
        f'A stock kernel running inside a box of {box_s} s is UNTESTED: whether the Surface restarts there is UNKNOWN, and the argument above does not reach it.')


def long_gic_words(box_s, real=None):
    """0b, after the GIC bullet: the quarantine keeps the guest's timer interrupts off for the whole call, whatever the box, so a kernel that needs them may only idle. A panel
    choice may say it in its own words (its `gic_idle`)."""
    if box_s <= D5_BOX_S:
        return ''
    return said(real, 'gic_idle') or f'A kernel without those timer interrupts may only idle (UNKNOWN), so the seconds of a box beyond the first {D5_BOX_S} may add nothing.'


def long_log_lines(box_s, real=None):
    """5b, after MON CON: the log does not grow with the box. The limits are read from the sources (probes/xnuboot/report_layout.c EVENT_MAX and CONSOLE_MAX,
    telemetry.c, and the reporter's RP_EVENTS and RP_CONSOLE_LINES); boot 20's page shows the event log full inside the decided box. A panel choice whose reporter prints
    more says the last sentence in its own words (its `whole`)."""
    if box_s <= D5_BOX_S:
        return ()
    return wrap(f'- A box longer than {D5_BOX_S} s is not logged longer: the monitor keeps at most the first 512 events (EVENT_MAX) and the first 1 KiB of the guest\'s console '
                f'buffer (CONSOLE_MAX) whatever the box, and the page prints five events and six console lines of them, so most of a box of {box_s} s is not logged (read from '
                'probes/xnuboot/report_layout.c, telemetry.c and probes/sp11-stick-reporter.c, INFERRED). Boot 20\'s page already read EVENTS=512 DROPPED=834 inside its 5 s (OBSERVED '
                'once): the log was full, so a longer box adds no events to the page. '
                + (said(real, 'whole') or 'Only the counts (MON COUNTS) and the end of the run (MON STOP, MON EXIT) reflect the whole box.'), first=3, rest=5)

# What depends on the panel and the kernel (D9). 'withheld' with a stock kernel is the run of boot 20; the key of another panel is added with its own words, and until
# then asking for it fails.
PANEL = {'withheld': dict(
    d9='D9 the panel is withheld from the guest and the kernel is stock (SPTM HVC call patch 0).',
    guest_gets=('no disk, no USB and no panel: its build has no root device, no storage or USB model and no framebuffer, and stage 2 maps it RAM only '
                '(INFERRED from the sources; no stage-2 abort in boot 20\'s 5 s, OBSERVED once)'),
    console='The guest has no panel to write to: its console output goes to a buffer in RAM that SNAPREP prints as MON CON lines.',
    rung='rung 2, the run with the panel handed to the guest')}
# The keys that hand the guest the panel are in tools/sp11_stick_bundle_panel.py (GUIDE_WORDS): d9, guest_gets, console and rung replace the first run's, and outside,
# ram_also, ram_note, fault_note, decides, why, stand, hazard, film and step7 are words of their own that the hooks below read with `said`, and a guest that boot 21 ran too
# adds stand_head, stand_loop and stand_stick (stand_lines). The first run's words have none of those, so
# its text is what it was.
PANEL.update(pn.GUIDE_WORDS)
RAM_NOTE = '(INFERRED; no stage-2 abort in boot 20\'s 5 s, OBSERVED once)'      # of the map that boot 20 ran, RAM only: a panel choice says its own (ram_note)
KERNEL = {0: 'the stock macOS kernel in GUEST.DAT (the J700 26A428 kernelcache with only the loader\'s pointer rebase applied, SPTM HVC call patch 0: '
             'docs/SP11-STICK-REAL.md section 5)'}
SYNTHETIC = 'the guest image in GUEST.DAT (a synthetic build: this text is a test artefact, not a run)'

# The lines of the pages that section 5b describes, by the text they begin with: each is a string of probes/sp11-stick-monitor-record.h, probes/sp11-stick-wrapper.c,
# probes/sp11-stick-gic-record.h or probes/sp11-stick-reporter.c, which tools/test-sp11-stick-monitor-text.py looks for there.
PAGE_LINES = ('PAYLOAD LOADED', 'PAYLOAD VERIFY=OK', 'PAYLOAD CALL', 'PAYLOAD DONE', 'PAYLOAD CHAIN STOPPED', 'SNAPMON', 'SNAPREP', 'STAGE=', 'REFUSED=', 'MON IDS',
              'MON IMAGE', 'NEXT GIC STORES AFTER', 'MON CALL', 'MON RET=', 'MON REGS RESTORED=', 'GIC RESTORE', 'MON VERDICT=', 'MON QUARANTINE', 'TICKS',
              'MONITOR RETURNED STAGE=DONE', 'WRAPPER DID NOT FINISH STAGE=', 'VERDICTS PIN=', 'REGS', 'RAM UNTOUCHED BEYOND REPORT.DAT', 'PLACEMENT WRITTEN',
              'CHUNKS', 'PLACE', 'GUEST.DAT', 'REPORT UNREADABLE', 'REPORT EMPTY', 'MON NO RECORD', 'MON RECORD DAMAGED', 'MON STATUS=OK ENTERED=',
              'MON PREPARE FAILED CODE=-', 'MON EL2 FAULT ESR=', 'MON FAULT ELR=', 'MON COUNTS EVENTS=', 'MON STOP ', 'MON EXIT PC=', 'MON EV ', 'MON CON ')
# The kinds of stop that SNAPREP names after `MON STOP`, with what each means (read from probes/xnuboot/report_layout.c and async.c: INFERRED, and the page says per kind
# whether a run has shown it, STOP_SEEN). The first three are the ends that the plan expects; the others are controlled stops too, but not on its list.
STOP_KINDS = {
    'WATCHDOG': ('the time box ended the run: the stop timer fired at the deadline and the monitor took the guest back. PC= is where the guest was; A=, B= and C= are '
                 'its ESR_EL1, FAR_EL1 and ELR_EL1 as they stood then, which belong to its last exception and not to this stop (an interrupt carries no syndrome)'),
    'S2_FAULT': ('a stage-2 fault, and the run stops on it: PC= the guest PC, A= the IPA (0 when neither the stage-1 walk nor HPFAR could name it; the expected end '
                 'has A= not 0), B= ESR_EL2, C= the guest VA'),
    'GUEST_EXC': ('the kernel took an exception of its own: PC= is the guest\'s x30, not its PC; A=, B= and C= are its ESR, FAR and ELR, and C= is the address that '
                  'row c checks'),
    'PANIC_ENTRY': ('the kernel announced a fatal exception, a panic: A= the caller (x30), B= ELR_EL1, C= ESR_EL1; the panic text, if it is in the first 1 KiB of the '
                    'console buffer, is in the MON CON lines (film them)'),
    'ASYNC': 'an interrupt that was not the stop timer\'s ended the run: A= says which vector, B= and C= are ESR_EL1 and ELR_EL1',
    'SERROR': 'the guest took an SError, an access that the interconnect refused: A= is ESR_EL2, B= ESR_EL1, C= ELR_EL1'}
# Which of those kinds a run has shown: boot 20 (the first run of the monitor entry, 2026-11-18) ended as WATCHDOG, one unit, one boot; no run has ended as another kind.
STOP_SEEN = {'WATCHDOG': 'OBSERVED once (boot 20, one unit, one boot).'}
STOP_NEVER = 'Never seen (UNKNOWN).'
EXPECTED_ENDS = ('WATCHDOG', 'S2_FAULT', 'GUEST_EXC')
# The prepare statuses of the gates, as SNAPREP names them in `MON PREPARE FAILED CODE=-n NAME` (rp_status_name of probes/sp11-stick-reporter.c).
GATE_STATUSES = ((-90, 'NV2_NO_RECORD'), (-91, 'NV2_OTHER_CORE'), (-92, 'NV2_FEAT'), (-93, 'NV2_NOT_PASSING'), (-94, 'PAUTH_MIDR'), (-95, 'PAUTH_IDS'))
SP11_STATUSES = ((-146, 'VCPU_CLUSTERS'), (-147, 'TIMER_GUARD_CNTHCTL'), (-148, 'TIMER_GUARD_CNTVOFF'))


def words(real):
    """The panel words of a record: `real` is the facts of a real build (None for a synthetic one, which has no panel and is written with the withheld words). A guest with a
    boot-arg word of its own has the words of that record (pn.kind_key)."""
    return PANEL[pn.kind_key(real)] if real else PANEL['withheld']


def said(real, key, box_s=None):
    """The words under `key` that a hook of this module adds or puts in place of the first run's: a string, or a function of the facts of the record, the time box and the wait
    that the silence rule gives for it, for the words that name a wait or a size; '' when the panel's words have none, so that the first run's text is what it was."""
    value = words(real).get(key, '')
    return value(real, box_s, silence_s(box_s)) if callable(value) else value


def kernel_words(real):
    """What the guest is, as the text says it: a stock kernel for a real build (D9 decides the patch), a filler for a synthetic one. KeyError for another patch."""
    return KERNEL[real['sptm_hvc_calls']] if real else SYNTHETIC


def page_hex(value):
    """A value as the runner's hex op prints it: 0X and the fewest digits, capitals."""
    return f'0X{value:X}'


def digits16(digest):
    """The first eight bytes of a SHA-256 as 16 capitals: what the runner's PAYLOAD LOADED and PAYLOAD CALL lines print."""
    return digest[:16].upper()


def item(label, text, indent=3, hang=8):
    """`label` at `indent`, then `text` filled beside it, the lines after the first hung at `hang` (the label must be shorter than hang - indent)."""
    first, *rest = wrap(text, first=hang, rest=hang)
    return (' ' * indent + label.ljust(hang - indent) + first[hang:], *rest)


def reading_lines(real=None):
    """The paragraph before section 0: which sections the operator reads and acts on (the NV2 paragraph of 1b, the basis of D6, among them), who the coordinator is (the
    word is defined at its first use, here) and that approving covers one boot. A panel choice may name one section more that he acts on (its `also_reads`) and where
    the evidence ends (its `evidence_to`)."""
    acts = ['0', '0a', '0b', '4', '5', '5a', '5c', *words(real).get('also_reads', ())]
    return wrap(f'The operator reads and acts on sections {", ".join(acts[:-1])} and {acts[-1]}, and reads in section 1b the paragraph on the NV2 record (D6); section 5b is the '
                f'reference for the lines of the pages. The rest of sections 1 to {words(real).get("evidence_to", "3b")} is evidence that the coordinator (the assistant that builds '
                'the files and the stick and reads the film with the operator) checks against the files. Approving this text covers ONE boot of this stick (section 5c, THIS '
                'APPROVAL COVERS ONE BOOT).', first=0, rest=0)


def stand_lines(real=None):
    """Section 0a, in every record of this kind: what the one run of the monitor entry showed, with its labels,
    and nothing beyond it. The numbers are those of the operator's film of boot 20 (record 31e5d4c2...a802). A panel choice may add
    a paragraph of its own after the second one (its `stand` words, given `real`); the first run's has none. A record of a guest that boot 21 ran as well has its own heading
    (`stand_head`), its own last sentence of the second paragraph (`stand_loop`) and no line for the stick (`stand_stick` False: its paragraph says it)."""
    w = words(real)
    return (
        w.get('stand_head', '0a. Where things stand (boot 20, 2026-11-18, one unit, one boot; OBSERVED unless a sentence says INFERRED)'),
        *wrap('Boot 20 (record 31e5d4c2...a802) ran the monitor entry: the guest executed for its 5 s box, with 3,127 traps, 1,443 monitor calls and no stage-2 abort, and '
              'the run ended as MON STOP WATCHDOG at the kernel address 0xFFFFFE000C1EEF00.'),
        *wrap('Why the guest was still at that address when the time box ended the run is INFERRED: an early loop in the guest. '
              + w.get('stand_loop', 'The same stop address again, after a change meant to end the loop, would falsify that.')),
        *(w['stand'](real) if w.get('stand') else ()),
        *(wrap('The stick was unchanged but for the Shell\'s marker and the Mac\'s own .fseventsd entry.') if w.get('stand_stick', True) else ()),
        *wrap('The hardware status of everything else is NOT RUN. This text claims no more: not that macOS boots, and not that anything is stable.'))


def decision_lines(real, box_s):
    """Section 0. D4 is decided by approving this text, and is said so in capitals; then the four decisions of 2026-11-17, quoted from the app, and the earlier ones
    with their date (docs/SP11-MONITOR-PORT.md section 15 has the question behind each)."""
    return (
        '0. What approving this text decides (read this first)',
        *wrap('THE SHA-256 OF EXACTLY THIS TEXT, WHEN THE OPERATOR APPROVES IT, ALSO DECIDES D4: this run may store to RAM, to EL2 system registers and to the GIC, in '
              'the three classes ram, el2_sysreg and gic exactly as section 3 lists them, and in no other class. Nothing else decides D4, and a changed byte of this '
              'text is another record.'),
        *(wrap(words(real)['decides']) if words(real).get('decides') else ()),
        *(words(real)['why'](real) if words(real).get('why') else ()),
        *wrap('Approving this text covers ONE boot of this stick. A boot that printed PAYLOAD CALL 0 has spent the approval, and so has a boot whose film does not show '
              'whether it did (section 5c, THIS APPROVAL COVERS ONE BOOT).'),
        *longer_box_lines(box_s, real),
        *wrap('Decided by the operator on 2026-11-17 (the answer "Accept all four (Recommended)", quoted from the app):'),
        *item('D6', 'the NV2 record: embed the first-image record, the one that X2 run 4 measured. Section 1b says what it is and what is not known of it.'),
        *item('D8', f'the thermal exception: accepted for ONE CPU, {d8_box(box_s)} and AC power REQUIRED. Section 4 gives the exposure.'),
        *item('D10', 'SNAP_FAIR_TOKEN=1 stays in the monitor\'s profile (SP11 only): without the queued service lock six vCPUs refuse at -146. Its qualification on '
                     'the A16 is not part of this tree.'),
        *item('N1', 'the boot seed: the default label stays (section 1a); boot entropy at run time stays UNIMPLEMENTED.'),
        *said(real, 'decided', box_s),
        *wrap(f'Decided on 2026-11-16: D2 the stop is the monitor\'s own interrupt of the EL2 timer; {d5_words(box_s)}, and approving this text relaxes the 20 s UPTIME '
              'precaution that D5 left open (section 4); D7 the payloads and data files that run are '
              'named by their SHA-256 (section 1), and the stick\'s other files are checked by the coordinator (section 5c); '
              f'{words(real)["d9"]} N2 the board file is a guest view (section 1a).'),
        *wrap('The questions behind these decisions are in docs/SP11-MONITOR-PORT.md section 15. Until the operator approves it, this text is a template: that is the '
              'status line above, and approval is what changes it.'))


def hazard_lines(real, box_s, low, high):
    """Section 0b: what can go wrong, in plain words, most likely first."""
    w = words(real)
    guest = kernel_words(real)
    return (
        '0b. The hazards, in plain words',
        *wrap(f'- {guest[0].upper()}{guest[1:]} runs at EL1 on the boot CPU, one CPU only, for up to {box_s} s, under a monitor that {w.get("ran", "ran once on this machine (boot 20: section 0a).")} '
              'What the kernel does there beyond what section 0a says is UNKNOWN. The run ends by the monitor\'s own timer interrupt, by a stage-2 fault or by '
              'an exception that the kernel takes (section 5a).', first=3, rest=5),
        *wrap(f'- The guest is given {w["guest_gets"]}. An access outside its RAM{w.get("outside", "")} is meant to fault at stage 2 and stop the run.', first=3, rest=5),
        *(wrap('- ' + said(real, 'hazard', box_s), first=3, rest=5) if w.get('hazard') else ()),
        *(wrap('- ' + w['carry'], first=3, rest=5) if w.get('carry') else ()),
        *wrap('- No disk write is meant to be possible: the code of the wrapper, the monitor and the reporter stores to RAM, EL2 system registers and GIC registers only '
              f'(section 3), and the guest is given RAM{w.get("ram_also", "")} only {w.get("ram_note", RAM_NOTE)}. Stage 2 is all that stops '
              'the guest: an SMC would not be trapped and would reach '
              'firmware (section 3; the kernelcache holds none: read on the host (a file, not a run)). The one certain write to the stick is the Shell\'s marker file '
              f'{MARKER}, made before the step starts (section 5c).', first=3, rest=5),
        *wrap('- The Surface may restart by itself, during the run or after it (cause UNKNOWN; the earliest datum is about UPTIME 17 s, and boot 20 showed none to the end of '
              'its film at 45 s: section 4). A restart before '
              'the report is NOT a result (section 5a).' + (f' {restart_risk_words(box_s, real)}' if box_s > D5_BOX_S else ''), first=3, rest=5),
        *wrap(f'- No thermal guard exists on this path: one CPU is loaded for about {low} to {high} s plus the monitor\'s prepare, {box_s} s of it by the guest (D8, '
              'section 4).' + (f' {d8_exposure_words(box_s, real)}' if box_s > D5_BOX_S else '') + ' AC power is REQUIRED. If the stop timer never fires (the plan\'s second '
              'most likely failure) ' + runaway_words(box_s, real),              first=3, rest=5),
        *wrap('- The GIC: from the first GIC store until after the monitor returns, this CPU\'s SGI 0 and PPIs 23, 25, 27 and 30, the guest\'s own timers (INTID 27, 30) '
              'among them, are switched off and put back afterwards; if the call never returns they stay off until the restart (INFERRED: the firmware sets the GIC up '
              'again). If both group 1 put-backs fail after the return they stay off as well, and the page says GIC RESTORE CLEAN=NO (host model only; never run). '
              f'What the stock kernel does without them for up to {box_s} s is UNKNOWN (section 3, class gic).' + (f' {long_gic_words(box_s, real)}' if box_s > D5_BOX_S else ''),
              first=3, rest=5),
        *wrap('- The two EFI files that run at EL2 before the wrapper (section 1b) are not checked at boot against this record, and the operator cannot check them on the '
              'unit: the control is the coordinator\'s, before the boot (section 5c).', first=3, rest=5),
        *wrap('- The monitor reads a few words of PARAMS again while the guest runs, in RAM that the guest can write (section 3b, the LIVE gates; INFERRED): a guest '
              'that sets one could open a path to a device or to firmware. The builder made them zero.', first=3, rest=5))


def outcome_lines(real, box_s):
    """Section 5a: M1 as docs/SP11-MONITOR-PORT.md section 2 defines it (rows a to g) and what section 14 expects, then the kinds of page."""
    ends = ', '.join(EXPECTED_ENDS[:-1]) + ' or ' + EXPECTED_ENDS[-1]
    hang = said(real, 'hang', box_s) or f'a page that stops at MON CALL and does not change for {silence_s(box_s)} s (no controlled stop: row d)'
    dark = (said(real, 'no_result', box_s) or 'a black panel, or a restart of the Surface, before STAGE=DONE and the SNAPREP page have been on the screen, or a film that '
            'does not show them')
    return (
        '5a. What M1 is, and when it has passed or failed (docs/SP11-MONITOR-PORT.md sections 2 and 14)',
        *wrap('M1: on the boot CPU, one vCPU, started from the stick with no host link, the monitor runs at EL2, loads the kernel, the kernel\'s first instructions '
              'execute at EL1 under it, the run ends in a controlled stop, and nothing is written to any disk. ' + words(real)['console'] + ' M1 passes only if every '
              'row holds:'),
        *item('a', 'the runner loads the files and both hashes agree before and after the bounce: PAYLOAD READY, then PAYLOAD VERIFY=OK for both payloads;'),
        *item('b', 'the wrapper refuses on any precondition mismatch (MIDR, regime, CNTFRQ, GOP, placement): one REFUSED line and no guest entry. That is the designed '
                   'answer to a mismatch; a run in which nothing mismatches has no such line, and a REFUSED line is no pass;'),
        *item('c', 'the guest ran: MON STATUS=OK ENTERED=0X1, and the guest PC of the stop is inside the kernel image: PC=, except on GUEST_EXC, where PC= is the guest\'s '
                   'x30 and C= is the address to check (the coordinator compares it with the kernelcache);'),
        *item('d', f'the run ended by the monitor\'s own stop, a MON STOP line ({ends}), never a hang or a reset;'),
        *item('e', f'the stop came inside the time box: {box_s} s of wall time on CNTPCT_EL0, {box_s * TICKS_PER_S:,} ticks of 19.2 MHz. MON RET ... TICKS= counts the whole '
                   f'call, the prepare and the stop\'s clean included (about {m1t.PREPARE_AND_CLEAN_S} s together in boot 20, OBSERVED once: section 4){said(real, "row_e", box_s)}, '
                   f'so TICKS <= '
                   f'{box_s * TICKS_PER_S:,} proves row e, and a larger value neither proves nor fails it (row e is then UNKNOWN unless the stop is MON STOP WATCHDOG, '
                   'the monitor\'s own deadline);'),
        *item('f', 'no disk was written: nothing in the run\'s code writes one (section 3), and the stick is byte-identical after the run but for the Shell\'s marker and, once the '
                   'Mac has mounted the stick, its .fseventsd entry, which the coordinator re-reads;'),
        *item('g', 'the A16 monitor is byte-identical with the new gate off (tools/monitor_baseline.py IDENTICAL): a host check of the coordinator, on no page.'),
        *wrap('A STAGE-2 FAULT WITH AN IPA IS SUCCESS' + said(real, 'fault_note', box_s) + ': MON STOP S2_FAULT, with the guest PC, the IPA in A= and ESR_EL2 in B=, is the kernel stopping at its first device '
              'that the monitor does not model, which the plan expects of M1 (docs/SP11-MONITOR-PORT.md section 14: expected failure 5, "that is success for M1"; '
              + (said(real, 'fault_seen', box_s) or 'INFERRED: no run has stopped that way yet, UNKNOWN') + '). Rows c and d hold for it, and the coordinator reads A= and B= to say which address the kernel touched; A=0 means '
              'that no IPA could be named, '
              'and that is a finding, not this end. A touch outside RAM that stage 2 stops IS this end, not a failure and not a retry.'),
        *wrap('A run that uses its whole time box ends as MON STOP WATCHDOG, and MON EXIT PC= beside it (' + (said(real, 'wd_exit') or 'OBSERVED once: boot 20, one unit, one boot') + '). The plan\'s own list '
              'of failures to expect, most likely first: the NV2 gate refusing, MON PREPARE FAILED CODE=-90 to -95 (the gate passed with the embedded record in boot 20: '
              'OBSERVED once; the regime that record was built in is still not recorded, INFERRED E2H = 1: section 1b); the stop timer never firing, or '
              'the wrong interrupt arriving, so that the guest runs unbounded and only the operator\'s FORCE OFF ends it (U3: PPI 26 reached EL2 in the GIC dry run, '
              'OBSERVED, one boot; the monitor\'s own stop path ran at the time box in boot 20, OBSERVED once, so this failure is possible and has not been seen); a '
              'stage-2 execute-never fault on the monitor\'s first native call (U10); a debug or '
              'PAuth refusal at the first signed return (U5, U8); a restart with no report (U4).'),
        *wrap('A page is one of four things:'),
        *wrap(f'- M1 PASSED: the SNAPREP page shows MON STATUS=OK ENTERED=0X1 and a MON STOP line of kind {ends}, with PC= a kernel address (C= on GUEST_EXC) and A= not 0 on '
              f'S2_FAULT{said(real, "fault_note", box_s)}; rows a, c, d and e hold, and the coordinator checks f and g. WATCHDOG is the time box ending a run that used all of it ({said(real, "wd_box") or "OBSERVED once: boot 20"}).',
              first=3, rest=5),
        *wrap('- A FINDING, which the coordinator reads before anything else runs and which neither passes nor fails M1 by itself: MON REGS RESTORED=NO, WINDOW OVERRUN, '
              'STACK FULL or GIC RESTORE CLEAN=NO beside a return (a finding about the monitor, never a refusal of the wrapper); a MON STOP of another kind (PANIC_ENTRY, '
              'ASYNC, SERROR or one that SNAPREP prints as UNNAMED): the monitor stopped the run, so row d holds, but the plan did not list it. A MON STOP S2_FAULT with '
              'A=0 is a finding too: no IPA could be named, so it is not the expected end above, though rows c and d hold.', first=3, rest=5),
        *wrap('- M1 FAILED, a result, and the same bundle is never run again: a wrapper REFUSED line (no guest ran); MON PREPARE FAILED CODE=-n (the monitor refused '
              'to launch the guest); MON EL2 FAULT (the monitor itself took an exception at EL2); MON QUARANTINE (the monitor returned a quarantine code: the report '
              f'is partial); {hang}; a fault page of q1n1.', first=3, rest=5),
        *wrap(f'- NOT A RESULT: {dark}. It says nothing about the guest. Whether the approval is spent depends on whether PAYLOAD CALL 0 printed (section 5c, THIS APPROVAL COVERS ONE BOOT).',
              first=3, rest=5))


def page_lines(m1, box_s, real=None):
    """Section 5b: the lines of the pages in the order they appear. What comes from the build is computed here from the same facts as sections 1 and 1b: the first
    eight bytes of the monitor's digest, the ID registers of the NV2 record, the time box. The digits of the PAYLOAD lines are in section 5c."""
    n, mon = m1['nv2'], m1['monitor']
    ids = ' '.join(f'{name}={page_hex(int(n[key], 16))}' for name, key in (('MMFR2', 'id_aa64mmfr2_el1'), ('MMFR4', 'id_aa64mmfr4_el1'), ('REVIDR', 'revidr_el1')))
    image = f'MON IMAGE OFF={page_hex(mon["offset"])} BYTES={mon["bytes"]} SHA={page_hex(int(mon["sha256"][:16], 16))} BOX_S={box_s} ESF=0X0 GPUF=0X0'
    gate = ', '.join(f'-{-code} {name}' for code, name in GATE_STATUSES)
    other = ', '.join(f'-{-code} {name}' for code, name in SP11_STATUSES)
    refused = page_hex(-91 & (1 << 64) - 1)
    return (
        '5b. The page, line by line (in the order it appears; the film is the record of it)',
        *wrap('Every line of payload 0 starts with SNAPMON and every line of payload 1 with SNAPREP; this guide leaves the prefix out. A line that this text does not name '
              'is a finding for the coordinator, not a result.'),
        *wrap('The runner draws its own lines first, for each file: PAYLOAD LOADED INDEX=n BASE= BYTES= SHA=<16 hex> TYPE=, then PAYLOAD READY, and after the bounce, for '
              'each payload, PAYLOAD VERIFY=OK and PAYLOAD CALL <n> <16 hex>, which is printed before the call so that a hang is named by it, and, when the payload '
              'returns, PAYLOAD DONE INDEX=n RETURN=0X.. STACK=OK|OVERFLOW LOADS= FAULTS= REFUSED= LINES= DROPPED= (STACK=OVERFLOW, or a PAYLOAD CHAIN STOPPED line, is a '
              'finding). A PAYLOAD REFUSED line ends the boot before the payload runs. The 16 digits to compare are in section 5c. Payload 0, SNAPMON.BIN, is the wrapper '
              'with the monitor; its live page has at most 30 lines:'),
        *wrap('- STAGE=<name> says how far the wrapper got (ENTERED, ARGS_OK, FILES_OK, PIN_OK, OBSERVED_OK, PLACED_OK, GUEST_OK, COPYING, COPIED, PREPARED, '
              'MONITOR_ENTERED, MONITOR_RETURNED, DONE): the last one on a page that stops is where it stopped. A REFUSED=<name> STAGE=<name> DETAIL= line is the '
              'wrapper refusing: no guest ran, and M1 has failed (section 5a).', first=3, rest=5),
        *wrap(f'- MON IDS {ids}, then MON IDS ISAR1= ISAR2=: this CPU\'s ID registers, in the runner\'s capitals. The first line must equal the NV2 record of section 1b, '
              'or the monitor\'s own gate refuses the launch (-91).', first=3, rest=5),
        *wrap(f'- {image}: the monitor image: its offset, its size, the first eight bytes of its digest (section 1b), the time box; ESF and GPUF are zero, or the wrapper '
              'refuses before the call.', first=3, rest=5),
        *wrap('- NEXT GIC STORES AFTER 8 S G=1 R=1 C=1 T=0 V=0 E=0: the GIC stores come after 8 s, and the 8 s of silence are healthy. The GIC lines before and after '
              'it (GICD, GICR, CPU, NEW) are the registers read and written (section 3, class gic).', first=3, rest=5),
        *wrap(f'- MON CALL X0= X1= X2= X3= X4= SP=: the monitor is called now, and {said(real, "still") or "the page does not change until it returns"}: the monitor\'s prepare, the guest for '
              f'up to {box_s} s, and the stop with its cache clean (the prepare and the clean took about {m1t.PREPARE_AND_CLEAN_S} s together in boot 20: section 4).',
              first=3, rest=5),
        *wrap(f'- MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=<n> STACK=<used>/<bytes>: the call returned. STATUS is the prepare\'s status: 0 passed, and a refusal prints it as a '
              f'64-bit number ({refused} is -91); ENTERED=1 says the guest ran. MON REGS RESTORED=YES: the registers that the monitor must put back are back (NO lists '
              'them, and WINDOW OVERRUN or STACK FULL may follow: findings, not refusals). GIC RESTORE CLEAN=YES: the GIC registers that the arm half changed are put back '
              '(NO: one did not go back, a finding). MON VERDICT=RETURNED FLAGS= SUB= is the wrapper\'s verdict of the call. TICKS= is in ticks of 19.2 MHz.',
              first=3, rest=5),
        *wrap('- MON QUARANTINE: THE REPORT IS PARTIAL AND RAM MUST NOT BE REUSED. POWER CYCLE appears only after MON CALL, when the monitor itself returned RET=0X51554E01 or '
              '0X51554E02: FORCE OFF (section 5c), and read nothing else of that page as a result.', first=3, rest=5),
        *wrap('- TICKS HZ= PLACED= COPIED= HASHED=: the wrapper\'s own clock at its steps, in counter ticks (HZ per second); then STAGE=DONE RETURN=0: the wrapper finished. '
              'SNAPREP follows.', first=3, rest=5),
        *wrap('Payload 1, SNAPREP.BIN, reads REPORT.DAT and prints at most 40 lines: MONITOR RETURNED STAGE=DONE (WRAPPER DID NOT FINISH STAGE=<name> when the wrapper '
              'stopped early: a failure), then its own head lines, VERDICTS PIN= GUEST= POLICY= LINES=, two REGS lines (the wrapper\'s reads of EL, MIDR, MPIDR, CNTFRQ, '
              'HCR, SCTLR and TCR), RAM UNTOUCHED BEYOND REPORT.DAT or PLACEMENT WRITTEN only when the wrapper refused (the first: before the copy, and the page ends '
              'there; the second: at or after it), CHUNKS (the copy and hash counts), TICKS, PLACE and GUEST.DAT (the placement '
              'and the file that the wrapper saw), then the MON lines above again, MON VERDICT first, then what the monitor wrote into its window:'),
        *wrap('- MON STATUS=OK ENTERED=0X1: the monitor launched the guest and the guest ran. This is row c.', first=3, rest=5),
        *wrap(f'- MON PREPARE FAILED CODE=-91 NV2_OTHER_CORE: the monitor refused to launch the guest, and says why. {gate} are the NV2 and PAuth gate; {other} are the '
              'other gates of the SP11 build; a status that the reporter does not name prints UNNAMED, with its number. The guest never ran, and M1 has failed.',
              first=3, rest=5),
        *wrap('- MON EL2 FAULT ESR=0X.. EC=0X.. and MON FAULT ELR= OFF= FAR=: the monitor itself took an exception at EL2; OFF is ELR minus the monitor\'s base, to look up '
              'in its ELF with llvm-nm -n. M1 has failed.', first=3, rest=5),
        *wrap('- MON COUNTS EVENTS=n TRAPS=n ABORTS=n STEPS=n CALLS=n DROPPED=n: what the guest did before it stopped.', first=3, rest=5),
        *wrap('- MON STOP <kind> PC= A= B= C=: how the run ended. The forms of the kinds below are read from the monitor\'s sources (INFERRED), and each says whether a run '
              'has shown it:', first=3, rest=5),
        *(line for kind, text in STOP_KINDS.items() for line in wrap(f'{kind}: {text}. ' + ('An expected end of M1. ' if kind in EXPECTED_ENDS else 'A finding (section 5a). ')
                                                                       + {**STOP_SEEN, **words(real).get('stop_seen', {})}.get(kind, STOP_NEVER), first=7, rest=10)),
        *wrap('Any other name is a kind that this text does not list: the coordinator reads it from the monitor\'s sources and it is never guessed at.', first=7, rest=7),
        *wrap('- MON EXIT PC= SPSR= ESR= FAR=: the guest\'s own state at an interrupt stop, where it was.', first=3, rest=5),
        *(words(real)['page'](m1, box_s, real) if words(real).get('page') else ()),
        *wrap('- MON EV n <kind> PC= A= B= C=: the first five events of the monitor\'s log, what the kernel asked for first.', first=3, rest=5),
        *wrap('- MON CON <text>: up to six lines of the first 1 KiB of the guest\'s console buffer, in the panel\'s font (capitals and digits; ":" prints as "!" (OBSERVED once: '
              'boot 20) and anything else that is not in the font as "?").',
              first=3, rest=5),
        *long_log_lines(box_s, real),
        *wrap('- MON NO RECORD: THE STAGE DID NOT RUN, MON RECORD DAMAGED CODE=n, REPORT UNREADABLE and REPORT EMPTY: THE WRAPPER WROTE NOTHING: SNAPREP found no usable '
              'record of the wrapper. None is a result of the guest; the live page and the film say how far the wrapper got.', first=3, rest=5),
        *wrap('Then the five GIC lines again. A restart takes the whole page with it, which is why the film is the record.'))


def action_lines(r):
    """Section 5c: the coordinator's part (the stage tree, the stick, the marker) and the operator's, in the order he does it. `r` is the record's input: the label
    (which is the plan's name), the two payloads' (size, sha256) and the facts of the monitor entry run."""
    mon, rep = digits16(r['monitor'][1]), digits16(r['reporter'][1])
    efi = ' and '.join(r['m1']['efi'])
    real, box_s = r.get('real'), r['time_box_s']
    step4 = words(real).get('step4')        # a panel choice also names the GUEST.DAT line and the label: the programs' digits alone are rung 1's too
    return (
        '5c. The operator\'s actions, in order (docs/SP11-STICK-BUILD.md section 8 and docs/RUNNING.md, autorun)',
        *wrap('Done by the coordinator before the boot, not by the operator: the stick is staged from the stage tree that tools/sp11_stick_bundle_build.py makes with the '
              f'approved SHA-256 of this text (--base-tree and --file for {efi}), and the stager\'s verify says COMPLETE with shell_pin: match. The stage tree itself writes STICK-FILES.TXT, the '
              'list of every file of the stick with its SHA-256; this text does not list it, and cannot, because it is a product of the stage tree. The control of the '
              'stick is the coordinator\'s, and it is made here, before the boot: each staged file is compared with the entries of STICK-FILES.TXT, and so are the two EFI '
              'digests of section 1b, which nothing checks at boot. '
              f'The plan is named after the label, {r["label"]}, and has one step: load SP11\\SLBOUNCE-DEVHE-MARK.EFI, then run SP11\\Q1N1-PAYLOAD.EFI '
              '--el2. The stick holds EFI\\BOOT\\startup.nsh, SP11\\PLAN.TXT, the two EFI files and the bundle SP11\\SNAPMON.BIN, SNAPREP.BIN, GUEST.DAT, PIN.DAT, '
              'REPORT.DAT and PAYLOAD.MAN, besides the files of the base tree. The coordinator deletes the marker '
              f'{MARKER} before the boot: the Shell writes it before the step, and a stick that holds it skips this single step (the Shell prints ALL STEPS DONE and '
              'nothing runs: docs/RUNNING.md).'),
        *wrap('Then the operator; nothing is typed. FORCE OFF, used below, means: hold Power alone for about 20 s by the clock until the Surface logo or Windows appears, '
              'then release (that restarts the Surface; it is no power-off: INFERRED for the Pro 11); if nothing appears after 30 s, hold Volume-Up and Power together for '
              'about 15 s (Microsoft lists that for earlier models only; its effect on the Pro 11 is UNKNOWN), then release both. Source: Microsoft, "Force a shutdown and '
              'restart your Surface", Surface Pro 5th Gen and later (the Pro 11 is not named there). A restart, FORCE OFF\'s or the Surface\'s own, goes to the boot order '
              'that the firmware has; which device the Pro 11 tries first with the stick in is UNKNOWN. If the stick starts again, the marker (written before the step) makes '
              'the Shell print ALL STEPS DONE and run nothing. Do not let Windows run with Secure Boot off (docs/RUNNING.md, Way A: a Windows boot by accident only asks for '
              'the BitLocker recovery key; if Windows does start, shut it down from Windows); take the stick out as soon as the logo appears, so that nothing mounts it '
              '(row f); at a recovery-key screen type and choose nothing.'),
        *item('1.', 'AC power is REQUIRED (D8): the charger may be in Surface Connect or a USB-C port, and stays in from before the boot until the end. One hardware client. '
                    'NO cable to the Mac, no Mac logger, no other device on the USB ports; the stick goes in either port.', hang=7),
        *item('2.', 'Film 30 frames a second or more (29.97 is fine: boot 20\'s film had it; 60 if the camera can) from the Shell\'s countdown '
                    + (said(real, 'film_until', box_s) or f'until the first UPTIME heartbeat AFTER the line STAGE=DONE, or until {silence_s(r["time_box_s"])} s after the last page change, '
                       'whichever comes first')
                    + '. Say the time of day aloud on the video at the start. Keep the whole panel in frame.'
                    + said(real, 'film', box_s), hang=7),
        *item('3.', f'Boot the stick as for every SP11 boot before this one. Do not press ESC at the countdown. The Shell then prints AUTORUN {r["label"]} STEP 1 OF 1, CMD1: load '
                    'SP11\\SLBOUNCE-DEVHE-MARK.EFI, CMD2: SP11\\Q1N1-PAYLOAD.EFI --el2 and "Writing SP11\\DONE1.TXT in 5 seconds, then the two commands run. Power off now to '
                    'cancel." Compare the plan name and both commands with this text; if either differs, start FORCE OFF at once and say what differs on the video: the 5 s end '
                    'before a 20 s hold does, so the step may still start (THIS APPROVAL COVERS ONE BOOT, below, says whether the approval is then spent).', hang=7),
        *item('4.', f'On the runner\'s lines compare the digits: PAYLOAD LOADED INDEX=0 ... SHA={mon} (SNAPMON.BIN) and INDEX=1 ... SHA={rep} (SNAPREP.BIN), then '
                    f'PAYLOAD VERIFY=OK and PAYLOAD CALL 0 {mon}.{step4(r["guest"], r["label"]) if step4 else ""} A mismatch, no PAYLOAD VERIFY=OK, or a PAYLOAD REFUSED line: start '
                    'FORCE OFF at once and tell the '
                    'coordinator; the call may already have begun. Do not retry.', hang=7),
        *item('5.', f'Touch nothing while the pages run (section 5b: {said(real, "checks", box_s) or "about 3 s of checks"}, the 8 s after NEXT GIC STORES AFTER 8 S, MON CALL, then '
                    f'{said(real, "wait", box_s) or "a page that stands still for the prepare, the box and the stop"}). The stop rules of section 5 say when a silence ends '
                    f'the run.{said(real, "step5", box_s)} If at any time the Surface is too hot to hold comfortably, '
                    'FORCE OFF at once and tell the coordinator: that outranks "touch nothing" and every wait of these steps, and once PAYLOAD CALL 0 has printed, the approval '
                    'is spent.', hang=7),
        *item('6.', 'When STAGE=DONE, the SNAPREP page and the first UPTIME heartbeat after them have been filmed, FORCE OFF: that writes nothing of ours.'
                    f'{said(real, "step6", box_s)} Do not run the '
                    'bundle a second time. Then give the coordinator every video (the countdown to the first UPTIME heartbeat after STAGE=DONE), say the time of day, and hand '
                    'over the stick untouched: open, mount, edit or delete nothing on it before the coordinator has read it (row f).', hang=7),
        *item('7.', f'{said(real, "step7_head", box_s) or "If the panel goes black or the Surface restarts at any point:"} write down the last line you saw, the last UPTIME shown if any, and the time on the video, '
                    'then FORCE OFF, DO NOT RETRY, and tell the coordinator. ONE RESTART BEFORE THE REPORT IS NOT A RESULT (section 5a).' + said(real, 'step7', box_s),
                    hang=7),
        *wrap(f'THIS APPROVAL COVERS ONE BOOT. A boot that printed PAYLOAD CALL 0 {mon} has spent it, whatever follows. A boot whose film does not show whether PAYLOAD CALL 0 '
              'printed counts as printed: the approval is spent. A boot that did not print that line ran nothing of M1 (the marker may exist: the coordinator deletes it) and '
              'is repeated only with the same files, after the coordinator has found the cause. Another file, kernel, patch value, time box or store class needs its own '
              f'record, and so does {words(r.get("real"))["rung"]}.'))


# The bullets of section 5 that only this kind has or words differently, in the form of the other bullets (three spaces, a dash, the text; five spaces under it).
PRE_CALL_SILENCE = (
    *wrap(f'- No page change for {SILENCE_BEFORE_PAYLOAD_MIN} minutes at any point after the Shell\'s AUTORUN line and before the runner\'s PAYLOAD CALL 0 line: nothing of M1 '
          'has run; note the last line and FORCE OFF (section 5c). The 2 minutes is an operator-safe wait and not a measurement: the runner\'s own loading and hashing of the '
          'files is UNKNOWN in length. The approval is not spent (section 5c, THIS APPROVAL COVERS ONE BOOT).', first=3, rest=5),
    *wrap(
        f'- No page change for {SILENCE_BEFORE_CALL_S} seconds at any point after the runner\'s PAYLOAD CALL 0 line and before the MON CALL line: note the last line and FORCE '
        f'OFF (section 5c). The {SILENCE_BEFORE_CALL_S} s is the silence rule of the GIC dry run (docs/SP11-STICK-GIC.md section 11), kept here, and not a measurement. The healthy '
        'silences in that stretch are the wrapper\'s checks, copy and verify, about 3 s (section 4), and the 8 s that the NEXT GIC STORES AFTER 8 S line announces; '
        'the runner\'s own loading before PAYLOAD CALL is UNKNOWN in length (UPTIME 37 to 38 S at the first heartbeat of boot 14 covers all of it).', first=3, rest=5))
RESTART_AND_STAGE2_RULES = (
    *wrap('- One restart before the report is not a result (section 5a). A boot that printed PAYLOAD CALL 0 has spent the approval, and the same bundle is not run again; '
          'a boot that did not is the coordinator\'s to repeat or not (section 5c, THIS APPROVAL COVERS ONE BOOT).', first=3, rest=5),
    *wrap('- A touch outside RAM is a finding to read, not a retry. When stage 2 stops the guest\'s access (MON STOP S2_FAULT, the IPA in A=) it is the expected end of M1 '
          '(section 5a). An access that nothing stopped, a store outside the placement (WINDOW OVERRUN, STACK FULL), or a hang or a restart '
          'that follows a device access, is a finding of the other kind: read the page and the film before anything else is run, and do not retry.', first=3, rest=5))
HARDWARE_RULES = wrap(
    '- One hardware client; AC power REQUIRED (D8: a battery is not enough, whatever its charge); no cable; the operator present and filming (section 5c). A spinning guest '
    'is ended by the operator\'s FORCE OFF (section 5c), which writes nothing of ours; if the Surface is too hot to hold comfortably, FORCE OFF at once.', first=3, rest=5)
