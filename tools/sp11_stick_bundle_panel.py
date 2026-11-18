# SPDX-License-Identifier: MIT
"""The choices of D9 that hand the unit's framebuffer to the guest, for the monitor entry run: `real --m1-run` with `--panel handed` (the second choice) and with
`--panel console` (the panel as the kernel's console: the command line has serial=2 where the loader's has serial=3), each with `--sptm-hvc-calls 0`, a stock kernel
(docs/SP11-STICK-MONITOR.md section 20). Everything the record of those choices says about the panel is here, so that the text of the choice that withholds it
(tools/sp11_stick_bundle_record.py and tools/sp11_stick_bundle_record_m1.py) is what it was, byte for byte.

 - read_panel is what the builder reads of the built guest: PARAMS fb_pa and fb_span, boot_args.Video and the command line, from the chunks of GUEST.DAT and not
   from the arguments, held to the GOP facts that PIN.DAT pins and to the command line of the choice (CMDLINES). A guest that is not what its record says is
   PANEL_MISMATCH, so the text below is about the bytes the operator stages.
 - The text: the lines of sections 1a, 2, 3 (the ram class and group E of the el2_sysreg class), 4 and 5 that differ from the first run's, section 3c (what the guest
   receives, how stage 2 maps it, the console, what stays withheld, what the monitor does with the range, the screen, the film, the hazards, the store class and the
   results), and the words that the guide of the record keeps by panel (GUIDE_WORDS). Every `path:line` that section 3c cites is in CITES with a fragment that must be
   on that line (tools/test-sp11-stick-monitor-panel.py).
 - The record carries no VERIFIED label, as no record of this tool does: what is read in a source is said with its file and line, what is concluded from it is
   INFERRED, and nothing here was run on the unit.

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_panic as rp  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
REFUSALS = ('PANEL_MISMATCH',)      # beside m1.REFUSALS: docs/SP11-STICK-MONITOR.md section 20 lists it
ch.KNOWN_REFUSALS.update(REFUSALS)
Refused = ch.Refused
MIB = sb.MIB
PAGE_SHIFT = 14                     # GRANULE of probes/xnuboot.py: panel_range() holds the panel to 16 KiB pages
# struct params (probes/xnuboot/report_layout.c): mem_low and mem_high, which check_params reads, are followed by fb_pa and fb_span.
FB_PA_WORD, FB_SPAN_WORD = ch.PARAMS_MEM_HIGH + 1, ch.PARAMS_MEM_HIGH + 2
# boot_args (probes/xnuboot.py boot_args): the page after the SPTM handoff page, 1152 bytes, Video at +0x28 as six u64 and the command line at +0x6C.
BOOT_ARGS_AT, BOOT_ARGS_BYTES, VIDEO_AT, CMDLINE_AT, CMDLINE_BYTES = 0x1000, 0x480, 0x28, 0x6C, 1024
TEXT_MODE, DEPTH = 0, 32            # v_display 0 is XNU's text mode (VIDEO_DISPLAY_MODE in probes/xnuboot.py); the loader hands 32-bit pixels only
LOADER_CMDLINE = '-v debug=0x8 serial=3 cluster_power=0 wdt=-1'        # probes/xnuboot.py BOOT_CMDLINE: the line that the console paragraphs of section 3c are written for
# The command line of each choice, read back from boot_args. The panel as the console is the loader's line with serial bit 0 clear (serial=2), which is what
# probes/run.py makes of it for the A16's native panel console and what tools/sp11_stick_bundle_chunks.py real_built makes of it for `console`; the other two keep the
# loader's own line, so that a console route that is not the record's is refused for every choice.
CMDLINES = {'withheld': LOADER_CMDLINE, 'handed': LOADER_CMDLINE, 'console': LOADER_CMDLINE.replace('serial=3', 'serial=2')}
SAYS = {'withheld': 'a guest that is given none', 'handed': 'a guest that is handed the panel', 'console': 'a guest whose console is the panel'}
GIVEN_THE_PANEL = ('handed', 'console')
FORMATS = {0: 'RGBX', 1: 'BGRX'}    # PIN.DAT fb_format (docs/SP11-STICK-BUNDLE.md section 3)


def command_line(choice, boot_arg=None):
    """The command line that the guest of `choice` has (CMDLINES) and, after it, the one word `boot_arg` of m1.BOOT_ARGS that the panel as the console may add (K10);
    BOOT_ARG_REFUSED for any other word or choice, so that the read-back of a guest never accepts a line that the whitelist does not name."""
    if boot_arg is None:
        return CMDLINES[choice]
    if boot_arg not in m1.BOOT_ARGS or choice != m1.BOOT_ARG_PANEL:
        raise Refused('BOOT_ARG_REFUSED', f'{boot_arg!r} is not a word that --panel {choice} may add to the command line ({" and ".join(m1.BOOT_ARGS)}, for --panel {m1.BOOT_ARG_PANEL})')
    return f'{CMDLINES[choice]} {boot_arg}'


def kind_key(real):
    """The key of the guide's PANEL table for the facts of a real build: its choice, `+word` when its command line has a word of its own, which is a record of its own, and
    `+let_panic` when the monitor's let_panic PARAMS word is set (K12, K13)."""
    return real['panel'] + (f'+{real["boot_arg"]}' if real.get('boot_arg') else '') + ('+let_panic' if real.get('let_panic') else '')

# What section 3c cites and what each citation rests on: (file, first line, last line, fragments that must all be somewhere on those lines). The text below
# is held to this table by tools/test-sp11-stick-monitor-panel.py in both directions: a path:line in the text that is not here, and an entry whose fragment is
# not on its lines, both fail. The q1n1 files are cited by function and have no line here.
CITES = (
    ('probes/xnuboot/stage2.c', 193, 196, ('if (p->fb_span)', '.lo = p->fb_pa', 'S2_AP_RW | S2_ATTRS, .upper = S2_XN')),
    ('probes/xnuboot/stage2.c', 186, 199, ('.lo = p->mem_low', '.lo = p->fb_pa', '.lo = c->work_base')),
    ('shadow/stage2.h', 33, 39, ('S2_MEMATTR_NORMAL (0xfUL << 2)', 'stage 1 can only tighten it', 'S2_AP_RW', 'S2_XN', 'S2_ATTRS')),
    ('probes/xnuboot/sptm_dispatch.c', 983, 988, ('c->params->fb_span', 'pte_pa >= c->params->fb_pa')),
    ('probes/xnuboot/prepare.c', 578, 581, ('!v->slot && p->fb_span', 'c->fb_before = framebuffer_hash(p)')),
    ('probes/xnuboot/prepare.c', 149, 149, ('{p->fb_pa, p->fb_span}',)),
    ('probes/xnuboot/context.c', 799, 806, ('framebuffer_hash', 'i < p->fb_span; i += 8', '*(volatile uint64_t *)(p->fb_pa + i)')),
    ('probes/xnuboot/report.c', 243, 250, ('vcpu_of(c)->guest_sctlr', 'publish(c->params->fb_pa', 'OUT_FRAMEBUFFER + 2', 'framebuffer_hash(c->params)')),
    ('probes/xnuboot/mmu_access.c', 193, 198, ('dc civac', 'line += 64')),
    ('probes/xnuboot.py', 167, 187, ('v_baseAddr', 'serial=3', 'Text mode uses v_display = 0')),
    ('probes/xnuboot.py', 188, 192, ('FRAMEBUFFER = None', 'Text mode is the one where XNU', 'VIDEO_DISPLAY_MODE = 0')),
    ('probes/xnuboot.py', 1961, 1969, ('serial=3', 'BOOT_CMDLINE =', 'This does not override the separate kprintf gates')),
    ('probes/xnuboot.py', 2300, 2323, ('dockchannel-uart',)),
    ('probes/xnuboot/mmio_dispatch.c', 70, 74, ('dc_write(&c->uart',)),
    ('probes/run.py', 1315, 1316, ("console_mode == 'auto'", "'panel' if host.framebuffer else 'serial'")),
    ('probes/run.py', 1591, 1594, ("replace('serial=3', 'serial=2')", "console_mode == 'panel'")),
    ('probes/sp11-stick-reporter.c', 244, 245, ('RP_CONSOLE_LINES 6u', 'RP_CONSOLE_WIDTH 64u')),
    ('probes/sp11-stick-reporter.c', 338, 360, ('shown < RP_CONSOLE_LINES', 'pl_text(&l, "MON CON ")', 'if (width)')),
    ('probes/sp11-stick-wrapper.c', 199, 202, ('SP11SB_STAGE_PIN_OK', '" LABEL="', 'build_label')),
    ('probes/xnuboot/fine_locks_calls.c', 272, 272, ('!(p->fb_span && pte_pa>=p->fb_pa && pte_pa-p->fb_pa<p->fb_span)',)),
    ('probes/xnuboot/kernel_views.c', 709, 712, ('.user_io_pa=p->fb_span', '.user_io_size=p->fb_span')),
    ('compat/boot-relocate.h', 147, 153, ('XNU\'s first CPU-start write', 'PMGR reg 24 +0x4004', 'stopped the run (exit 6)')),
)
# The documents that the text cites, by the section that holds the statement and not by line, because the documents move (docs batch 6 moved one of them under a text that
# cited its lines): (the words of the cite as the record has them, the file, the heading that opens the section, the fragments that must all be in that section). Held to
# the text in both directions by tools/test-sp11-stick-monitor-console.py; no line of a document is cited.
DOC_CITES = (
    ('docs/BOOT-CHAIN.md section 6', 'docs/BOOT-CHAIN.md', '## 6. ', ('serial=3', 'kprintf')),
    ('docs/GUI-BOOT-ROADMAP.md, the table of gates', 'docs/GUI-BOOT-ROADMAP.md', '## Gates at a glance', ('| 3 | Kernel console on the panel |',)),
    ('docs/GUI-BOOT-ROADMAP.md section 3', 'docs/GUI-BOOT-ROADMAP.md', '## 3. ', ('draws its own boot console on the A16 panel', 'output bit of `serial=` set', 'leaves drawing to the kernel',
                                                                                  'Framebuffer hashing before entry')),
    ('docs/COMPATIBILITY.md, section "Running the bundle checks"', 'docs/COMPATIBILITY.md', '## Running the bundle checks',
     ('--console serial', 'Panel handed over, printf still to the UART', 'serial output bit clear so printf draws on the panel', '--console panel')),
    ('docs/SP11-STICK-BUILD.md section 10', 'docs/SP11-STICK-BUILD.md', '## 10. ', ('The console.', '--panel console')),
    ('docs/SP11-STICK-BUNDLE.md section 3', 'docs/SP11-STICK-BUNDLE.md', '## 3. ', ('fb_base', 'fb_bytes', 'fb_format', 'OBSERVED (B1 attempts 1 to 5, operator report)')),
    ('docs/SURFACE-PRO-11.md, section WS-B', 'docs/SURFACE-PRO-11.md', '### WS-B: ', ('0xE4800000', '0x21C0')),
    ('docs/SURFACE-PRO-11.md section 4.1', 'docs/SURFACE-PRO-11.md', '### 4.1 ', ('Samsung ATNA30DW01 13-inch OLED, 2880x1920 (3:2)',)),
    ('docs/SP11-MONITOR-PORT.md section 7', 'docs/SP11-MONITOR-PORT.md', '## 7. ', ('x4 = 0; XNU\'s first CPU-start write falls to stage 2', 'whether XNU gets there first is UNKNOWN')),
)
PLACEMENT_HEADER = '2. Placement (the one range the run may store to, and the panel range of section 3c, which the guest may write)'
KIND_LINES = {'handed': m1t.wrap('This record is of the choice that hands the guest the panel: the ram class names its range (section 3c) and no class is added.'),
              'console': m1t.wrap('This record is of the choice that hands the guest the panel as the kernel\'s console (serial=2): the ram class names its range '
                                  '(section 3c) and no class is added.')}
KIND_LINES_ARG = m1t.wrap('This record is of the choice that hands the guest the panel as the kernel\'s console (serial=2) with cpus=1 in its command line: the ram class names '
                          'its range (section 3c) and no class is added.')


def kind_lines(panel):
    """The lines that section 3 adds after the kind's own line, for the choice that read_panel found (and its word, if the guest has one)."""
    if _rung(panel) is r5:
        return r5.kind_lines(panel)
    if panel.get('boot_arg'):
        return KIND_LINES_ARG
    return KIND_LINES[panel['choice']]

# The lines of the first run's text that change under this choice. replaced() finds each exactly once, so a change of that text cannot go unnoticed here.
RAM_OLD = ('and the reserve above it, guest_base to work_base; its other accesses are meant to fault at stage 2 and stop the',
           'run (docs/SP11-MONITOR-PORT.md section 12; INFERRED from the sources; no stage-2 abort in boot 20\'s 5 s,',
           'OBSERVED once). No disk and no RAM',
           'outside the placement and REPORT.DAT. The monitor binary also holds code that stores to devices and calls')
def _ram_new(shown):
    """The text of the ram class for a guest with the panel range: `shown` says what the runs showed of its map (boot 20's and, for rung 3, boot 21's)."""
    return m1t.wrap('and the reserve above it, guest_base to work_base, and the panel range of section 3c (outside the placement: the guest writes it, the '
                    'monitor reads it and cleans it); its other accesses are meant to fault at stage 2 and stop the run (probes/xnuboot/stage2.c:186-199; INFERRED from '
                    f'the sources; {shown}). No disk and no '
                    'RAM outside the placement, REPORT.DAT and the panel range. The monitor binary also holds code that stores to devices and calls', 0, 0, 114)      # the record module indents each line of a class by six spaces
RAM_NEW = _ram_new('boot 20\'s map of RAM only showed no stage-2 abort in 5 s, OBSERVED once, and this map with the panel range has not run')
RAM_NEW3 = _ram_new(r3.RAM_SHOWN)      # rung 3: boot 21 ran this map for 2.044 s
GROUP_E_OLD, GROUP_E_NEW = ('   range and the reserve.',), ('   range, the reserve and the panel range of section 3c.',)
RAM_HEAD_OLD = ('RAM inside the placement and the tail of REPORT.DAT, as the GIC and stop dry run, and what the monitor and its',
                'guest add. The wrapper: the copy of the chunks and the zero fills; REPORT.DAT page 0 (the head), its GIC record at')
RAM_HEAD_NEW = m1t.wrap('RAM inside the placement, the tail of REPORT.DAT and the panel range of section 3c, as the GIC and stop dry run (which has no panel), and what '
                        'the monitor and its guest add. The wrapper: the copy of the chunks and the zero fills; REPORT.DAT page 0 (the head), its GIC record at',
                        2, 0, 114)
RAM_HEAD_NEW = (RAM_HEAD_NEW[0][2:], *RAM_HEAD_NEW[1:])      # the first line follows `   ram: `, eight columns, and the others six spaces
CONSOLE_FINDING = r2.CONSOLE_FINDING
HANDED_STOP_RULE = ('- Panel handed (section 3c): {force_off}. What the guest paints does not count as a page change for the silence rule above, and it can paint over the '
                    'wrapper\'s lines: note the last line that can still be read and what the screen shows, and keep the film. While the guest runs the panel may change or go '
                    'dark because of what the guest paints (UNKNOWN): that is not a restart and not the black panel of step 7 of section 5c or of the NOT A RESULT page of '
                    'section 5a; the panel still dark {wait} seconds after the MON CALL line, or a restart (the '
                    'Shell\'s countdown or the runner\'s first lines on the screen again), before the report, is that case. The guest\'s touches of the panel range are expected '
                    'and no finding; a touch outside RAM and outside that range still is. A panel that stays blank, frozen or garbled after the report is a finding: '
                    'film it, say what you saw, and FORCE OFF as step 6 says.')


def stop_rules(panel, wait_s):
    """The panel's bullet of section 5, for the dict that read_panel returned; `wait_s` is the wait of the silence rule for this box (the guide's silence_s), which the
    bullet names, so that a box longer than the decided one moves it with the rule."""
    if panel['choice'] == 'console':
        return r2.stop_rule(wait_s, panel['run']['box_s'])
    return m1t.wrap(HANDED_STOP_RULE.format(wait=wait_s, force_off=r2.force_off(wait_s)), 3, 5)


def with_run(panel, box_s, monitor_sha, reporter_sha):
    """`panel` (read_panel's dict, or None) with this record's box and programs added, held against the first run's (tools/sp11_stick_bundle_panel_rung2.py): the words
    that depend on them are derived from it."""
    return {**panel, 'run': r2.facts(box_s, monitor_sha, reporter_sha)} if panel else None

# What the guide of the M1 record (tools/sp11_stick_bundle_record_m1_guide.py) takes from its table PANEL for a guest that is handed the panel, in the place of the first
# run's words, which say that the guest has none. d9, guest_gets, console and rung are the keys that table has; outside, ram_also, ram_note, fault_note, decides, why, stand, hazard, film,
# also_reads, evidence_to, runaway, hang, no_result, row_e, still, wait, step4, step5, step6 and step7 are read by hooks of the guide with .get (`said`), so that the first run's
# words have none of them and its text is what it was. The choice is said not to be decided: only the first
# run's D9 is, and approving a record is what decides the choice that it makes (section 0 says so).
_GETS = ('no disk and no USB, and the unit\'s framebuffer (the panel range of section 3c, which it can read and write): its build has no root device and no storage or USB '
         'model, and stage 2 maps it RAM and that range only (INFERRED from the sources; never run)')
_D9 = ('D9 for the first run only: the panel withheld and a stock kernel (SPTM HVC call patch 0); the choice of this text is not that one (section 0 above, section 3c).')
_DECIDES = 'APPROVING THIS TEXT ALSO DECIDES A CHOICE OF D9 THAT IS NOT DECIDED YET: {what}, with the kernel stock (section 3c). The first run withheld the panel.'
_RUNG = ('rung 1, the first run with the panel withheld, whose record has its own approval: this record is rung 2, {what}, and neither approval covers the other\'s '
         'boot')
_RAM_NOTE = ('(INFERRED; boot 20\'s 5 s, with the map of RAM only, showed no stage-2 abort, OBSERVED once; this map, with the panel range, has not run)')
_GETS3 = _GETS.replace('(INFERRED from the sources; never run)', r3.GETS_NOTE)      # rung 3: boots 20 and 21 ran the map (RAM only, then with the panel range)
assert _GETS3 != _GETS, 'the guest_gets words have changed under the words of rung 3'
_FAULT = ' (not when A= lies inside the panel range: section {where})'      # 5a: the success of an S2_FAULT with an IPA is for a fault elsewhere; the panel's own results say the rest
GUIDE_WORDS = {
    'handed': dict(
        d9=_D9, guest_gets=_GETS, outside=' and outside the panel range', ram_also=' and the panel range', ram_note=_RAM_NOTE, fault_note=_FAULT.format(where='3c'),
        decides=_DECIDES.format(what='the guest is handed the unit\'s panel'), hazard=r2.hazard_handed, step7=r2.step7_handed,
        rung=_RUNG.format(what='the panel handed to the guest'),
        console=('The guest is given the panel (section 3c) and its command line is serial=3, so its console output is expected to go to a buffer in RAM that SNAPREP '
                 'prints as MON CON lines (INFERRED); what it paints on the panel is UNKNOWN.'), **r2.SHARED_WORDS),
    'console': dict(
        d9=_D9, guest_gets=_GETS, outside=' and outside the panel range', ram_also=' and the panel range', ram_note=_RAM_NOTE, fault_note=_FAULT.format(where='5d'),
        decides=_DECIDES.format(what='the guest is handed the unit\'s panel as the kernel\'s console (serial=2)'), hazard=r2.hazard_console, step7=r2.step7_console,
        rung=_RUNG.format(what='the panel handed to the guest as the kernel\'s console (serial=2)'), why=r2.why_lines, stand=r2.stand_extra, film=r2.FILM_STEP,
        also_reads=('5d',), evidence_to='3c',
        console=('The guest is given the panel (section 3c) and its command line is serial=2, so its console is expected to be the panel; the MON CON lines are not expected '
                 'to carry the console text (INFERRED); the kernel\'s own text on the panel is the aim of this choice and is UNKNOWN.'), **r2.SHARED_WORDS),
    # Rung 3 (K10): the panel as the console with cpus=1. Its own words are tools/sp11_stick_bundle_panel_rung3.py; the rest is the console's.
    'console+cpus=1': dict(
        d9=_D9, guest_gets=_GETS3, outside=' and outside the panel range', ram_also=' and the panel range', ram_note=r3.RAM_NOTE, fault_note=_FAULT.format(where='5d'),
        decides=r3.DECIDES, hazard=r3.hazard, step7=r2.step7_console, rung=r3.RUNG, why=r3.why_lines, stand=r3.stand_extra, film=r2.FILM_STEP, also_reads=('5d',), evidence_to='3c',
        stand_head=r3.STAND_HEAD, stand_loop=r3.STAND_LOOP, stand_stick=False,
        console=r3.CONSOLE, ran=r3.RAN, stop_seen=r3.STOP_SEEN, fault_seen=r3.FAULT_SEEN, film_until=r3.film_until, **{**r2.SHARED_WORDS, 'step4': r3.step4, 'step6': r3.STEP6, 'row_e': r3.row_e})}
GUIDE_WORDS['console+cpus=1+let_panic'] = rp.guide_words(GUIDE_WORDS['console+cpus=1'])      # rung 4 (K12): rung 3's words, except those that tools/sp11_stick_bundle_panel_panic.py says again
GUIDE_WORDS['console+cpu_boot_timeout_secs=120+let_panic'] = r5.guide_words(GUIDE_WORDS['console+cpus=1+let_panic'])      # rung 5 (K13)


def reading_lines(panel):
    """The paragraph that the first run's reading pointer (section 0's lead) gets for a choice that hands the panel: for the panel handed, section 3c is the one section after 3b
    that the operator reads; for the panel as the console it is section 5d, and 3c is the evidence for it."""
    if panel['choice'] == 'console':
        return m1t.wrap('This text also asks the operator to read section 5d: it says what to film, what is new beyond section 0b and what counts as a result of rung '
                        f'{(5 if _rung(panel) is r5 else 4) if panel.get("let_panic") else 3 if panel.get("boot_arg") else 2}. Section 3c is the evidence for it, which the coordinator reads.', 0, 0)
    return m1t.wrap('This text also asks the operator to read section 3c: it says what the panel choice is, what the film must show and what counts as a pass of it.', 0, 0)


def replaced(lines, old, new):
    """`lines` with the run `old` replaced by `new`; it must be in `lines` exactly once."""
    at = [i for i in range(len(lines) - len(old) + 1) if tuple(lines[i:i + len(old)]) == tuple(old)]
    assert len(at) == 1, 'the text of the first run has changed under the lines of the panel'
    return (*lines[:at[0]], *new, *lines[at[0] + len(old):])


def touches(base, panel):
    """M1_TOUCHES of tools/sp11_stick_bundle_record.py for the guest that is handed the panel: the ram class and group E of el2_sysreg name the range."""
    assert panel, 'a record that is not of this choice keeps M1_TOUCHES'
    sysreg = replaced(base['el2_sysreg'], GROUP_E_OLD, GROUP_E_NEW)
    ram = replaced(replaced(base['ram'], RAM_HEAD_OLD, RAM_HEAD_NEW), RAM_OLD, RAM_NEW3 if panel.get('boot_arg') else RAM_NEW)
    return {**base, 'ram': ram, 'el2_sysreg': sysreg}


def gop_range(facts):
    """(base, span) that probes/xnuboot.py panel_range() derives from the GOP facts: the framebuffer, on 16 KiB pages."""
    page, size = 1 << PAGE_SHIFT, facts['fb_stride'] * 4 * facts['fb_height']
    low = facts['fb_base'] & -page
    return low, ((facts['fb_base'] + size + page - 1) & -page) - low


def bytes_at(chunks, pa, size):
    """`size` bytes at physical address `pa` from the data chunk that holds them whole, else None."""
    for c in chunks:
        data = c.get('data')
        if data is not None and c['dest'] <= pa and pa + size <= c['dest'] + len(data):
            return bytes(data[pa - c['dest']:pa - c['dest'] + size])
    return None


def read_panel(chunks, lay, facts, choice, real, board=None, boot_arg=None, let_panic=False):
    """What the guest of the built `chunks` is given of the panel, from its bytes: None for a guest with none, else the dict that the text below is made from.
    PANEL_MISMATCH when it is not what `choice` (withheld, handed or console) says it is: the PARAMS words, boot_args.Video and the command line each must be the
    choice's, and for the console with `boot_arg` (a word of m1.BOOT_ARGS) the line with that word after it and no other. PARAMS is read for every guest of this kind; boot_args only for `real` (the synthetic guest's boot_args is filler). `facts` are the builder's (the
    GOP, PIN.DAT's fb_*), `board` the descriptor that the build used. `let_panic` (--let-panic) is only said in the dict that it returns: the PARAMS word itself is held by m1.check_launch."""
    wanted_line = command_line(choice, boot_arg)
    handed, say = choice in GIVEN_THE_PANEL, SAYS[choice] + (f' and the word {boot_arg} in its command line' if boot_arg else '')
    params = [c for c in chunks if c['kind'] == sb.KIND_PARAMS]
    data = params[0].get('data') if len(params) == 1 else None
    if data is None or len(data) < 8 * (FB_SPAN_WORD + 1):
        raise Refused('BUILT_SHAPE', 'the PARAMS chunk is absent, zero-filled or shorter than its panel words')
    word = lambda i: int.from_bytes(data[8 * i:8 * i + 8], 'little')    # noqa: E731
    given, wanted = (word(FB_PA_WORD), word(FB_SPAN_WORD)), gop_range(facts) if handed else (0, 0)
    if given != wanted:
        raise Refused('PANEL_MISMATCH', f'PARAMS fb_pa and fb_span are {given[0]:#x} and {given[1]:#x}; for {say} they are {wanted[0]:#x} and {wanted[1]:#x}')
    if not real:
        return None
    args = bytes_at(chunks, lay.handoff_pa + BOOT_ARGS_AT, BOOT_ARGS_BYTES)
    if args is None:
        raise Refused('PANEL_MISMATCH', 'the guest has no boot_args page to read its video fields from')
    video = struct.unpack_from('<6Q', args, VIDEO_AT)
    want = (facts['fb_base'], TEXT_MODE, facts['fb_stride'] * 4, facts['fb_width'], facts['fb_height'], DEPTH) if handed else (0,) * 6
    if video != want:
        raise Refused('PANEL_MISMATCH', f'boot_args.Video is {[hex(v) for v in video]}; for {say} it is {[hex(v) for v in want]}')
    line = args[CMDLINE_AT:CMDLINE_AT + CMDLINE_BYTES].split(b'\0', 1)[0]
    if line != wanted_line.encode():
        raise Refused('PANEL_MISMATCH', f'the command line of the guest is {line.decode("ascii", "replace")!r}; for {say} it is {wanted_line!r}')
    if not handed:
        return None
    devices = board.devices if board else {}
    return dict(choice=choice, boot_arg=boot_arg, **({'let_panic': True} if let_panic else {}), gop={key: facts[f'fb_{key}'] for key in ('base', 'bytes', 'width', 'height', 'stride', 'format')}, fb_pa=given[0], fb_span=given[1],
                video=video, cmdline=wanted_line, present=sorted(n for n, (state, _, _) in devices.items() if state == 'present'),
                absent=sorted(n for n, (state, _, _) in devices.items() if state == 'absent'))


def real_lines(panel):
    """The line that section 1a of the record adds for this choice, after the D9 lines."""
    g = panel['gop']
    console = (', the kernel\'s console (serial=2)' + (f' and {panel["boot_arg"]} in its command line' if panel.get('boot_arg') else '') + (', let_panic=1 in PARAMS' if panel.get('let_panic') else '')) if panel['choice'] == 'console' else ''
    return [f'   panel        {panel["fb_pa"]:#x} + {panel["fb_span"]:#x} bytes, {g["width"]} x {g["height"]}, text mode{console}: section 3c']


def placement_line(panel):
    """The line that section 2 adds after the placement's own: where the guest may write outside it."""
    lo, span = panel['fb_pa'], panel['fb_span']
    return f'   panel_base {lo:#x}  panel_size {span:#x}  panel_end {lo + span:#x}  (outside the placement; the guest writes it: section 3c)'


def time_rows(panel):
    """The row that section 4's table adds, after the monitor's prepare: the monitor reads the range twice (section 3c). For rung 3 boot 21 ran it, inside its call of 2.044 s."""
    reads = r3.READS_ROW if panel.get('boot_arg') else 'UNKNOWN  never run here: in the prepare, and at the stop'
    return [f'      {"framebuffer: two reads of the panel":<41}{reads}']


# The sum of section 4 cannot count the two reads, and the exposure of D8 is CPU time too: each says so on a line of its own, where the first run's text has no room.
TIME_SUM_NOTE = (f'{"":<47}and the two reads of the framebuffer, UNKNOWN (section 3c)',)
THERMAL_LINES = m1t.wrap('The two reads of the framebuffer (section 3c) are CPU time at EL2 on top of the figures above; their length is UNKNOWN.')


def _p(text, first=3, rest=6):
    return m1t.wrap(text, first, rest)


def _row(label, text):
    """`text` filled into lines of at most 118 characters: the first after `label` (a column of 14 characters, six spaces in), the others under it."""
    first, *rest = m1t.wrap(text, 0, 0, 118 - 20)
    return (f'      {label:<14}{first}', *(' ' * 20 + line for line in rest))


def _rung(panel):
    """The module whose words a record of the console with a word of its own is written in: rung 3's, rung 4's (K12: let_panic) or rung 5's (K13: let_panic with the acknowledging word)."""
    return (r5 if m1.BOOT_ARG_CPU_START.get(panel.get('boot_arg')) == 'ack' else rp) if panel.get('let_panic') else r3


def _in_short(panel):
    if panel.get('boot_arg'):
        return _rung(panel).in_short(panel)
    lo, span = panel['fb_pa'], panel['fb_span']
    if panel['choice'] == 'console':
        return (f'In short. This record is rung 2: the first run again with the unit\'s framebuffer, {span} bytes at {lo:#x}, handed to the guest to read and write, and with '
                'serial=2 in the kernel\'s command line, so that its own console is the panel. Why: boot 20\'s guest spun in a loop of PE_init_iokit that cannot end while the '
                'panel is withheld (INFERRED; sections 0 and 0a). Expected, INFERRED (the A16 did the console, this unit never): the PC= of the report is not in that loop, and XNU\'s '
                'own boot text is on the panel while the guest runs, and not on the MON CON lines of the report; from the MON CALL line the screen may go black and fill with '
                'that text (below). The report is drawn over what the kernel left, after the stop; the film is the only record of the text. What to film, what is new and the '
                'results of rung 2 are in section 5d, in the operator\'s words; this section is the evidence for them.')
    return (f'In short. This run is the first run with the unit\'s framebuffer, {span} bytes at {lo:#x}, handed to the guest to read and write. The kernel\'s text is '
            'expected on the MON CON lines of the report and not on the panel (INFERRED: the command line has serial=3); what the panel shows is UNKNOWN and is a result, '
            'not a criterion. Film it: the guest can paint over the page, and the report is drawn over what it left. The run succeeds as the first run does, and the panel '
            'must not end it.')


def _intro(panel):
    if panel.get('boot_arg'):
        return _rung(panel).intro(panel)
    run = panel['run']
    same = r2.same_text(run)
    other = '' if not r2.differs(run) else ', and not only those (below)'
    if panel['choice'] == 'console':
        return ('Rung 1 is the first run: D9\'s first choice, the panel withheld and a stock kernel, decided on 2026-11-16, with its own record and its own approval; boot 20 ran '
                'it. This record is rung 2, the first run with two inputs of the guest changed' + other + ': the guest is given the unit\'s framebuffer, and its command '
                'line has serial=2 where rung 1\'s has serial=3 (the replacement that probes/run.py:1591-1594 makes for the A16\'s native panel console). The panel handed '
                'with serial=3 is the choice between them: its record is another text, and with it the kernel\'s text is expected on the MON CON lines and not on the panel '
                '(INFERRED); this one is for the kernel\'s text on the panel. It is not decided: approving the SHA-256 of this record is that decision, and it covers ONE '
                'boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); rung 1\'s approval covers none of it. ' + same +
                ' At run time the choice differs from rung 1 in five things: stage 2 maps the range, boot_args.Video names it, the command line sends the '
                'kernel\'s console to the panel, the monitor reads and cleans the range, and the screen shows what the guest paints.')
    return ('This record is the first run again (D9\'s first choice, panel withheld and a stock kernel, decided on 2026-11-16) with one input of the guest changed' + other +
            ': the guest is given the unit\'s framebuffer. The second choice is not decided: approving the SHA-256 of this record is that decision. ' + same +
            ' At run time the choice differs in four things: stage 2 maps the range, boot_args.Video names it, the monitor reads and cleans it, and the screen can '
            'show what the guest paints.')


def _label(panel, label):
    if panel.get('boot_arg'):
        return _rung(panel).label(panel, label)
    mark = m1.PANEL_MARKS[panel['choice']]
    others = 'the labels of the other two choices' if panel['choice'] == 'console' else 'the label of the withheld choice'
    refuse = f'it or with {m1.PANEL_MARKS["handed"]}' if panel['choice'] == 'console' else 'it'
    return (f'Label: PIN.DAT\'s build_label is {label}. The builder puts {mark} after M1RUN- for this choice only and refuses a facts label that starts with {refuse}, so '
            f'that {others} cannot read as this one; the page prints it on its SNAPMON STAGE=PIN_OK line (LABEL=; '
            'probes/sp11-stick-wrapper.c:199-202). The wrapper checks M1RUN- and no more (docs/SP11-STICK-MONITOR.md section 14, D2): the rest of the label is the '
            'builder\'s rule and the operator\'s check, and an edited PIN.DAT carries any label.')


def _other_differences(panel):
    if panel.get('boot_arg'):
        return _rung(panel).other_differences(panel)
    text = ('Other differences from the withheld guest follow from the panel\'s address, which lies below the bank: the guest\'s frame map and tables are wider '
            '(docs/SP11-STICK-REAL.md section 2: 46 chunks against 44 in the host run that table records).')
    if panel['choice'] == 'console':
        text += (' From the guest of the panel handed it differs in the command line only, which is one page of boot_args: the same size and the same chunks '
                 '(docs/SP11-STICK-REAL.md section 2).')
    return text


def _console(panel):
    if panel['choice'] == 'console':
        return ('Console. What the operator will see, INFERRED and never run on this unit: XNU\'s own boot text, drawn by the kernel itself into the panel while the guest '
                'runs. The A16 did this: its gate 3, the kernel\'s console on the panel (OBSERVED on the A16, as a history note of this project says; not re-run here: '
                'docs/GUI-BOOT-ROADMAP.md, the table of gates, and docs/GUI-BOOT-ROADMAP.md section 3), and docs/COMPATIBILITY.md, section "Running the bundle checks", names the three console modes of probes/run.py, of '
                'which this is the third: serial output bit clear, so that printf draws on the panel. How: the command line has serial=2 where the loader\'s has '
                'serial=3, the replacement that probes/run.py:1591-1594 makes for --console panel; the builder makes it on the loader\'s line before the guest is built '
                '(tools/sp11_stick_bundle_chunks.py, real_built) and reads the command line back from boot_args (PANEL_MISMATCH if it is anything else, serial=3 '
                'included). With bit 0 of serial set and a working UART the kernel\'s printf goes to the serial line and the panel is never its console '
                '(probes/xnuboot.py:167-187 and probes/xnuboot.py:1961-1969, the loader\'s own comments for this kernelcache), so with it clear the video console is the '
                'console. Text mode stays, v_display 0, the one where the CPU paints the glyphs (probes/xnuboot.py:188-192), and the base is in boot_args.Video (given). '
                'What it will not do: the MON CON lines of the report are not expected to carry the kernel\'s console text, because the serial route is off (INFERRED: the '
                'guest\'s device tree still names the dockchannel UART that the monitor models, probes/xnuboot.py:2300-2323 and probes/xnuboot/mmio_dispatch.c:70-74, '
                'but the console is not expected to be written to it): the first 1,024 bytes of console that SNAPREP-M1.BIN prints as at most 6 MON CON lines of 64 '
                'characters (probes/sp11-stick-reporter.c:244-245) are expected to hold none of that text (at most some early lines, printed before the video console takes over: INFERRED from the stop-site analysis of boot 20, section 0), and an empty buffer prints no MON CON line at all '
                '(probes/sp11-stick-reporter.c:338-360). What the kernel\'s kprintf sends to that UART through its own gate is '
                'another matter: debug=0x8 stays in the command line, and the loader\'s comment says that serial= does not override the kprintf gates (probes/xnuboot.py:1961-1969), '
                'so whether any such text reaches the MON CON lines is UNKNOWN, and it is not the console and does not count as the aim. So the console text is on the panel or '
                'nowhere (INFERRED): the choice between rung 1 and this one would have given up to 1,024 bytes of its text as MON CON lines. UNKNOWN: whether the guest reaches '
                'its console within the time box at all, and what it prints, because this RELEASE kernel\'s separate kprintf gate still prevents relying on its diagnostics '
                '(the comment of probes/xnuboot.py:167-187); a kernel that never takes its console leaves the panel as it was.')
    return ('Console. Expectation, INFERRED and never run: with this command line the kernel\'s verbose text goes to the monitor\'s console window and not to the panel, '
            'so read it afterwards from the MON CON lines (at most 6 lines of 64 characters of the first 1,024 bytes of the guest\'s console output, which are in '
            'REPORT.DAT: probes/sp11-stick-reporter.c:244-245), as in the first run. Why: the loader\'s own comments for this kernelcache (probes/xnuboot.py:167-187 and '
            'probes/xnuboot.py:1961-1969; not derived again for this record) say that XNU\'s video console needs the physical base in boot_args.Video, which XNU maps '
            'itself (given); text mode, v_display 0, so that the CPU paints the glyphs (given); the -v flag (given); and a serial setting without bit 0, because with '
            'bit 0 set and a working UART the kernel\'s printf goes to the serial line and the panel is never its console. The command line above has serial=3: bit 0 '
            'is set, the guest\'s device tree names the dockchannel UART that the monitor models (probes/xnuboot.py:2300-2323, probes/xnuboot/mmio_dispatch.c:70-74), '
            'and serial=3 sends kprintf and the console to the monitor\'s serial endpoint (docs/BOOT-CHAIN.md section 6). probes/run.py makes the panel the console, '
            'with serial=2, whenever it hands the guest a framebuffer (its default, and --console panel: probes/run.py:1315-1316 and probes/run.py:1591-1594); `real` '
            'makes that replacement for --panel console only (docs/SP11-STICK-BUILD.md section 10), which is another GUEST.DAT and another record, so this record is of the '
            'panel handed and not of the panel as the console: for kernel text on the panel, take that one. The plan says as much of the first '
            'choice (docs/SP11-MONITOR-PORT.md section 9, D9: the serial=3 output reaches the console window, and glyphs are X5). What the panel shows with this one is '
            'UNKNOWN: nothing of the kernel (the expectation); a cleared or painted screen (XNU maps the panel and records its geometry: INFERRED from the same '
            'comments); or text.')


def _console_for(panel):
    """The Console paragraph of section 3c: for rung 3 its first sentence says that boot 21 showed the console on the panel (r3.CONSOLE_OPEN3); the rest is rung 2's."""
    if _rung(panel) is r5:
        return r5.console_text(panel)
    text = _console(panel)
    if not panel.get('boot_arg'):
        return text
    assert r3.CONSOLE_OPEN in text and r3.CONSOLE_EARLY in text, 'the Console paragraph of rung 2 has changed under the words of rung 3'
    return text.replace(r3.CONSOLE_OPEN, r3.CONSOLE_OPEN3, 1).replace(r3.CONSOLE_EARLY, r3.CONSOLE_EARLY3, 1)


def _screen(panel):
    if panel['choice'] == 'console':
        return (r5 if _rung(panel) is r5 else r2).screen_text(panel['gop']['height'], panel['gop']['width'])
    return ('The screen. The wrapper\'s lines and those of SNAPREP-M1.BIN are drawn by the runner at EL2 into this same memory, as glyph pixels only and with no '
            'background (q1n1 platform/uefi/main.c: screen_char, payload_emit). What the guest paints stays until a glyph of the runner lands on it: the lines of the page '
            'drawn before the call can be painted over, and the report drawn after the stop is drawn over what the guest left, which can make it hard or impossible to '
            'read. The kernel\'s own text, if there is any, is drawn only while the guest runs, which is at most the time box and starts after the monitor\'s prepare, '
            'and the report is drawn over it after the stop.')


def _film(panel):
    """The film of the panel handed: the only record of the pixels, what it must show and what is handed back (the steps are those of section 5c; the panel as the console
    has its own, in section 5d)."""
    return ('The film. It is the only record of what the guest paints: the report is drawn over it, and nothing is written to the stick (row f of section 5a is untouched). '
            'Film as step 2 of section 5c says, with the whole panel in frame. It must show the runner\'s line PAYLOAD CALL 0, which spends the approval and is printed before '
            'the guest runs (the guest can paint over it, so that only the film shows it: if the film does not show it, treat the approval as spent), the panel while the '
            'guest runs, and the SNAPREP report with the first UPTIME heartbeat after it. Then stay on the final screen for ten seconds (a margin, not a measurement) before '
            'step 6, and hand the video back as step 6 says. A photograph taken afterwards is not enough.')


def _hazards(panel):
    """The new hazards of the panel handed, as lines (the panel as the console has its own, in section 5d)."""
    span = panel['fb_span']
    return _p(f'New hazards (the first run\'s are in the sections above). The guest writes up to {span} bytes of firmware-reserved memory, which the display '
              'scans out; stage 2 is the only limit on those stores (the wrapper\'s checks cover the placement, which is kept off the panel: PLACE_FB). The page can be '
              'painted over and unreadable (above). The monitor\'s two reads and its cache clean of the range lie outside the '
              'time box and their cost is UNKNOWN, so that the first change of the page after the MON CALL line can come later than the same box without the panel would give. '
              'Nothing of this run writes the firmware\'s display pipeline (INFERRED); a panel that stays blank, frozen or garbled afterwards is a finding.')


def _results(panel):
    """What passes and what fails for the panel handed: M1 is section 5a's and the panel adds one condition (the panel as the console has its results in section 5d)."""
    base = ('M1 passes as section 5a says, with one more condition for this choice: the panel did not end the run. The range is mapped read-write and never execute, so a '
            'MON STOP S2_FAULT with A= inside it is not the success that section 5a gives for a fault elsewhere: it is a finding about the panel.')
    return _p('Success of this record. ' + base + ' What the panel shows is a result and not a criterion: nothing of the kernel, a cleared or painted screen, and kernel text '
              'are each a finding to report, and the page and the film are kept. A run that ends differently from the first run, on the same stick otherwise, is the finding '
              'that matters for the panel.')


HEADERS = {'handed': '3c. The panel handed to the guest (D9, second choice: panel handed, stock kernel; docs/SP11-STICK-MONITOR.md section 20)',
           'console': '3c. The panel as the kernel\'s console (D9: panel handed, serial=2, stock kernel; docs/SP11-STICK-MONITOR.md section 20)'}


def section_3c(panel, label):
    """Section 3c of the record, from the dict that read_panel returned and the pinned label: the same paragraphs for the panel handed and for the panel as the
    kernel's console, but the summary, the first paragraph, the label, the console, the screen and the ends. The panel handed also says its film, its new hazards and its
    results here; for the panel as the console they are section 5d (tools/sp11_stick_bundle_panel_rung2.py), in the operator's words, and 3c keeps the evidence."""
    choice = panel['choice']
    boot_arg = panel.get('boot_arg')
    g, (base, mode, row, width, height, depth) = panel['gop'], panel['video']
    lo, span = panel['fb_pa'], panel['fb_span']
    shown = f'{span / MIB:.1f}'
    return (
        _rung(panel).HEADER if boot_arg else HEADERS[choice],
        *_p(_in_short(panel)),
        *_p(_intro(panel)),
        *_p(_label(panel, label)),
        '   What the guest receives, read by the builder from the built GUEST.DAT (PANEL_MISMATCH if it is anything else):',
        *_row('GOP', f'base {g["base"]:#x}, {g["bytes"]:#x} bytes, {g["width"]} x {g["height"]}, stride {g["stride"]} pixels ({g["stride"] * 4} bytes), format '
                     f'{g["format"]} ({FORMATS.get(g["format"], "?")}): the framebuffer that the runner reports (OBSERVED: docs/SP11-STICK-BUNDLE.md section 3, PIN.DAT fb_*); '
                     'the wrapper compares it with the live GOP before the call (GOP_MISMATCH) and the builder held these facts to the firmware dumps it was given'),
        *_row('PARAMS', f'fb_pa {lo:#x}, fb_span {span:#x} ({span} bytes, {shown} MiB): that range on 16 KiB pages (words {FB_PA_WORD} and {FB_SPAN_WORD} of struct params)'),
        *(_row('let_panic', _rung(panel).PARAMS_ROW) if panel.get('let_panic') else ()),
        *_row('boot_args', f'Video: v_baseAddr {base:#x}, v_display {mode} (text mode), v_rowBytes {row}, v_width {width}, v_height {height}, v_depth {depth}'),
        *_row('command line', panel['cmdline']),
        *_p(_other_differences(panel)),
        *_p('How stage 2 maps it (probes/xnuboot/stage2.c:193-196, shadow/stage2.h:33-39): to itself, read-write and never execute, as Normal write-back '
            'inner-shareable memory, the attributes of the guest\'s RAM (the guest\'s own stage 1 can only tighten them). The guest can therefore read and write all of '
            'it, and an instruction fetch from it is a stage-2 fault. The monitor\'s SPTM model does not count a guest page-table entry that points into it as '
            'pointing outside the guest\'s memory (probes/xnuboot/sptm_dispatch.c:983-988). The firmware\'s reserved descriptor around the GOP is 0x21c0000 bytes '
            '(OBSERVED: docs/SURFACE-PRO-11.md, section WS-B); the guest gets the first part of it and nothing else is mapped.'),
        *_p(_console_for(panel)),
        *((*_p(r3.boot21_text()), *_p(_rung(panel).word_text()), *(line for text in (r5 if _rung(panel) is r5 else rp).extra_paragraphs(panel) for line in _p(text))) if boot_arg else ()),
        *_p('What stays withheld. Stage 2 maps no display, GPU or DCP register for the guest: its ranges are its RAM and reserve, this panel range and the monitor\'s '
            'work area read-only (probes/xnuboot/stage2.c:186-199); the Adreno windows exist only with gpu.flags, which the builder requires zero (PARAMS_LAUNCH), and '
            'the windows of the display timing, input and Wi-Fi models are not in this monitor (section 3b). So no guest driver can start the display pipeline: it stays '
            'as the firmware left it, and its scan-out goes on reading this memory (INFERRED from the map; not shown on the unit). The descriptor that section 1a names '
            f'(PIN.DAT descriptor_sha256) states {", ".join(panel["present"]) or "no device"} as present and {", ".join(panel["absent"]) or "no device"} as absent: it '
            'describes the board and does not control what the guest reaches (xnuboot.build() takes no descriptor: docs/SP11-STICK-REAL.md section 3.1); stage 2 does, '
            'and the UART that the guest\'s tree names is the monitor\'s model and not a device of that list. '
            'A guest access to any of those registers is meant to fault at stage 2 and stop the run (INFERRED, never run).'),
        *_p(f'What the monitor does with the range. It reads all of it twice, as {span // 8} loads of 8 bytes, and hashes what it reads: in its prepare, before the '
            'guest\'s first instruction (probes/xnuboot/prepare.c:578-581, probes/xnuboot/context.c:799-806), and at the stop (probes/xnuboot/report.c:243-250). At the '
            f'stop, when the guest\'s SCTLR_EL1 is not zero, it first cleans and invalidates the range by virtual address, DC CIVAC on each of its {span // 64} lines of 64 '
            'bytes (probes/xnuboot/mmu_access.c:193-198), so that what the guest wrote reaches memory. Its other uses of fb_pa and fb_span are the stage-2 range, the '
            'exclusion of its device models from the range (probes/xnuboot/prepare.c:149), the page-table checks of its SPTM model (probes/xnuboot/sptm_dispatch.c:983-988 and '
            'probes/xnuboot/fine_locks_calls.c:272) and the user_io range of its kernel views (probes/xnuboot/kernel_views.c:709-712): checks, and none stores to it (read from '
            'the sources, INFERRED). Both hashes stay in the report words of the monitor, and no line of SNAPREP-M1.BIN reads them: the page does not say whether the guest '
            'wrote the panel. How the runner maps this memory at EL2 is not known here, so the time of those reads and whether any dirty line is written back are '
            'UNKNOWN' + (r3.READS_SEEN if boot_arg else '') + '. Both reads lie outside the time box.'),
        *_p(_screen(panel)),
        *((*_p(r2.public_text()), *_p(_rung(panel).expect_text(panel['run']) if boot_arg else r2.expect_text(panel['run']))) if choice == 'console' else (*_p(_film(panel)), *_hazards(panel))),
        *_p('Store class. The ram class covers the range: the guest\'s stores to it are RAM stores outside the placement, and the monitor\'s accesses are loads and DC '
            'CIVAC. The interface has no class for a framebuffer, so PIN.DAT\'s authorised_stores stays 7 and approving this record approves those stores by name: the '
            'panel range of section 2.'),
        *(_p(r2.POINTER_5D) if choice == 'console' else _results(panel)))


def section_5d(panel, wait_s):
    """Section 5d of the record of the panel as the console, after 5c: what to film, what is new beyond 0b and what counts as a result of rung 2, in the operator's words
    (tools/sp11_stick_bundle_panel_rung2.py), or of rung 3, the console with cpus=1 (tools/sp11_stick_bundle_panel_rung3.py). `wait_s` is the silence rule's wait for this
    record's box (the guide's silence_s)."""
    assert panel['choice'] == 'console', 'the panel handed has these in section 3c'
    return (_rung(panel) if panel.get('boot_arg') else r2).section_5d(panel, wait_s)
