#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of rung 4 of the monitor entry run (tools/sp11_stick_bundle_panel_panic.py; docs/SP11-STICK-MONITOR.md section 20, K12): `real --m1-run --panel console --boot-arg cpus=1
--let-panic --time-box-s 5 --sptm-hvc-calls 0`. The mechanism: the option and its refusals (LET_PANIC_REFUSED for every other combination, no environment variable), PARAMS word 23
(offset 0xB8; the finished chunk equals the same build's without the option, rung 3's, in every other word), the label (M1RUN-LETPANIC-), the five kinds against each other and the
record's size. The words: a phrase pin and a deletion mutant for each sentence of rung 4, the sections that it does not change are rung 3's byte for byte, and a generator without
a block is rejected. The fixtures of tools/test-sp11-stick-monitor-panel.py run here with a scratch directory of their own (build/sp11-monitor-panic-test-scratch/, deleted at the end).

    python3 tools/test-sp11-stick-monitor-panic.py

The regeneration script of the session is the end-to-end test with the real J700 26A428 kernelcache. Boot 23's numbers are the coordinator's reading of the operator's film (OBSERVED,
one unit, one boot); what let_panic does is INFERRED from the monitor's source. Nothing here runs the unit: Hardware NOT RUN."""
import importlib.util
import os
import re
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_panel_tests', TOOLS / 'test-sp11-stick-monitor-panel.py')
PT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PT)
SCRATCH = ROOT / 'build' / 'sp11-monitor-panic-test-scratch'
PT.SCRATCH = PT.BLD.SCRATCH = PT.AUTH.SCRATCH = SCRATCH
pn, rp, r2, r3, m1, rec, BT, ch, sb, bb, BLD = PT.pn, PT.pn.rp, PT.pn.r2, PT.pn.r3, PT.m1, PT.rec, PT.BT, PT.ch, PT.sb, PT.bb, PT.BLD
STATE, spying = PT.STATE, PT.spying
flat = lambda text: ' '.join(text.split())    # noqa: E731
LABEL = 'r4-test'    # 7 characters: with M1RUN-LETPANIC- 22 of 23
WAIT, WORD = 60, 23  # the silence rule's wait for the fixture's box of 5 s; let_panic in struct params
# The five kinds of record: (the D9 choice, the word, --let-panic); each has its own guest, label and text.
KINDS = (('withheld', None, False), ('handed', None, False), ('console', None, False), ('console', 'cpus=1', False), ('console', 'cpus=1', True))
NAMES = dict(zip(KINDS, ('withheld', 'handed', 'console', 'rung3', 'panic')))
PANIC = KINDS[-1]


def stand_in(mutate=None, extra_word=None):
    """The panel suite's stand-in loader, whose params_blob writes word 23 from built['let_panic'] as probes/xnuboot.py does; `extra_word` is a seeded fault: a (word, value)
    that it also writes in a build that sets the option."""
    factory = PT.stand_in(mutate).new

    def wrapped():
        xb = factory()
        stand, seen = xb.params_blob, {}

        def params_blob(built):
            chunks = built['chunks']        # the stand-in reads the build's own PARAMS bytes, as the real one does
            chunks[xb.PARAMS_PA] = seen.setdefault(id(chunks), bytes(chunks[xb.PARAMS_PA]))
            words = list(struct.unpack(f'<{ch.PARAMS_WORDS}Q', stand(built)))
            words[WORD] = 1 if built.get('let_panic') else 0
            if extra_word and built.get('let_panic'):
                words[extra_word[0]] = extra_word[1]
            return struct.pack(f'<{ch.PARAMS_WORDS}Q', *words)
        xb.params_blob = params_blob
        return xb
    return mock.patch.object(bb, 'xnuboot', wrapped)


def args_of(name, kind, **over):
    args = PT.real_args(name, kind[0], **({'facts': BLD.facts_with(LABEL), 'boot_arg': kind[1]} if kind[1] else {}))
    args.update(over)
    return args


def opts_of(args, kind):
    return [*BT.opts('real', **args), *(['--let-panic'] if kind[2] else [])]


def record(name, kind=PANIC, extra_word=None, **over):
    """(code, text, err) of the record of a real build of `kind` from the stand-in loader."""
    with stand_in(extra_word=extra_word):
        return BT.cli(['authorisation-template', 'real', *opts_of(args_of(name, kind, **over), kind)])


def build(name, kind=PANIC, approved=None):
    """(code, text, err, out) of a real build of `kind` with the approved record `approved` (its own by default)."""
    out, args = SCRATCH / 'out' / name, args_of(name, kind)
    with stand_in():
        text = approved if approved is not None else BT.cli(['authorisation-template', 'real', *opts_of(args, kind)])[1]
        argv = ['real', *opts_of(args, kind), '--out', out, '--no-manifest', '--authorisation', BT.write(SCRATCH / 'records' / f'{name}.txt', text.encode())]
        return (*BT.cli(argv), out)


def chunks_of(kind):
    """(chunks, what check_launch was given after them) of the guest the builder makes for `kind`."""
    got, real = [], m1.check_launch

    def spy(chunks, *rest):
        got.append((chunks, rest))
        return real(chunks, *rest)
    with mock.patch.object(m1, 'check_launch', spy):
        code, text, err = record(f'chunks-{NAMES[kind]}', kind)
    assert code == 0, err
    return got[0]


def setUpModule():
    PT.setUpModule()
    for kind in KINDS[3:]:
        made = []
        with spying(made):
            code, text, err = record(f'setup-{NAMES[kind]}', kind)
        assert code == 0, err
        STATE[NAMES[kind]], STATE[NAMES[kind] + '_panel'] = text, made[0]


def tearDownModule():
    PT.tearDownModule()


def panel4():
    """The dict that the text of rung 4 is made from, with the `run` entry that the builder adds (the stand-in pair's programs)."""
    return {**STATE['panic_panel'], 'run': r2.facts(5, r2.RUNG1_MONITOR, r2.RUNG1_REPORTER)}


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


def split_sections(text):
    """{section number: its lines} of a record: a section opens with a line that starts with its number at the margin."""
    out, key = {}, 'head'
    for line in text.splitlines():
        found = re.match(r'^(\d+[a-z]?)\. ', line)
        key = found.group(1) if found else key
        out.setdefault(key, []).append(line)
    return out


# ---- the words: a phrase pin each, in the words of the text (line breaks gone) ----------------------------------------------------
STOP23 = 'MON STOP GUEST_EXC PC=0XFFFFFE000B8C2288 A=0X2000000'
PART = 'the part of the panel left of the report page (about its left 55 percent)'
STILLS = f'take three sharp still photographs of {PART}, close and without glare, one of its top band, one of its middle and one of its bottom,'
PANIC_CPU = '"panic(cpu" (the kernel may put its timestamp in front)'
SECTION0_PINS = {
    'decides': 'APPROVING THIS TEXT ALSO DECIDES A CHOICE OF D9 THAT IS NOT DECIDED YET: the guest is handed the unit\'s panel as the kernel\'s console (serial=2), with the kernel stock, cpus=1 in '
               'its command line as in rung 3, and one word of the monitor\'s own parameters set that no run has set, let_panic (section 3c).',
    'why': 'WHY RUNG 4. Boot 23 (rung 3) honoured cpus=1 and the kernel ran 2.166 s, through the platform drivers into the storage drivers\' probes (OBSERVED once)',
    'analysis': 'so the kernel most likely panicked, the cause is UNKNOWN, and its text is printed only after that trap',
    'one change': 'THIS RECORD CHANGES ONE THING IN THE GUEST\'S INPUTS: the monitor\'s PARAMS word let_panic (word 23, offset 0xB8) is 1 where rung 3\'s is 0.',
    'prediction': 'PREDICTION (INFERRED): XNU\'s own panic text appears on the panel, and the run ends later.',
    'falsifier': f'FALSIFIER: {STOP23} again and no text: the word was not honoured, and rung 4 is NOT PASSED.'}
SECTION0A_PINS = {
    'boot 21': 'the call returned after 2.044 s and the run ended as MON STOP S2_FAULT A=0X300734004, INFERRED to be the kernel\'s first CPU-start write',
    'noise': 'were on the film (OBSERVED once: boot 21); that they come from the monitor\'s empty models of those devices is INFERRED. Rung 2 PASSED.',
    'boot 23': 'Boot 23 (rung 3, record 749361cf...a1a7; one unit, one boot; the operator\'s film, read at full resolution)',
    'honoured': 'cpus=1 was honoured: the replayed log has "processor 0" and no line for the processors 1 to 5, and its log-queue line went from 54 slots to 9',
    'ran': 'The kernel ran 2.166 s (TICKS=41593665) through the platform drivers into the storage drivers\' probes',
    'stop': 'MON STOP GUEST_EXC PC=0XFFFFFE000B8C2288 A=0X2000000 B=0XFFFFFEA937FDC000 C=0XFFFFFE000B8C22 and two digits that the page does not show',
    'clean': 'and MON EXIT PC=0XFFFFFE000B86C000 SPSR=0X604003C5 ESR=0X8200000F FAR=0X0. No panic text was on the film (OBSERVED once).',
    'verdict': 'M1 PASSED by section 5a; rung 3\'s own verdict was NOT PASSED, by the letter of its section 5d',
    'trap word': 'and it is XNU\'s own debugger-trap word, which the kernel executes on purpose in its debugger funnel',
    'panicked': 'So the kernel most likely PANICKED (a stackshot or profiling entry is less likely), the cause is UNKNOWN',
    'stick': 'REPORT.DAT kept its all-zero staged digest: a run does not write the report back'}
SECTION3C_PINS = {
    'header': '3c. The panel as the kernel\'s console with cpus=1 and let_panic (rung 4: D9 panel handed, serial=2, cpus=1, let_panic set, stock kernel; docs/SP11-STICK-MONITOR.md section 20)',
    'in short': 'In short. This record is rung 4: rung 3 (boot 23) again with one word of the monitor\'s PARAMS changed, let_panic, from 0 to 1',
    'rung 3': 'Rung 3 is the run before this one: the panel handed as the kernel\'s console (serial=2) with cpus=1, record 749361cf...a1a7, boot 23 (section 0a); its approval is spent.',
    'not decided': 'It is not decided: approving the SHA-256 of this record is that decision, and it covers ONE boot of this stick',
    'wrapper': 'the wrapper\'s own check reads three other words: probes/sp11-stick-monitor.c:118-139)',
    'differ': 'Only GUEST.DAT (eight bytes of its PARAMS chunk) and PIN.DAT (the label and the digests) differ from rung 3\'s',
    'label': 'Label: PIN.DAT\'s build_label is M1RUN-LETPANIC-r4-test. The builder puts LETPANIC- after M1RUN- for this record only, in place of CPUS1- (rung 3\'s)',
    'params row': 'let_panic 1 (word 23 of struct params, offset 0xB8: probes/xnuboot/report_layout.c:23); every other PARAMS word is rung 3\'s',
    'command line': 'command line -v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpus=1',
    'other differences': 'From rung 3\'s guest this one differs in one word of the PARAMS chunk only (let_panic, eight bytes at offset 0xB8)',
    'words': 'rung 3\'s exactly: one word, from the builder\'s list (--boot-arg: cpus=1 and no other word, BOOT_ARG_REFUSED for any other)',
    'words a16': 'no log names that assertion, the kernel ran 2.166 s first, and the film showed no text.',
    'word': 'The PARAMS word. let_panic is word 23 of struct params, at offset 0xB8 (probes/xnuboot/report_layout.c:23); the loader writes 1 there for a build that sets it (probes/xnuboot.py:1806)',
    'word diag': 'It is an existing diagnostic of the monitor, EXPERIMENTAL, used here for the first time on the Surface Pro 11, and not a model of anything.',
    'word reads': 'The monitor reads it at one place only (read on the host in probes/, compat/ and shadow/, a search of the sources and not a run)',
    'word branch': 'whose own handler runs (probes/xnuboot/trap_dispatch.c:370-387; what XNU then does is INFERRED).',
    'word builder': 'they differ in this word and in no other (PARAMS_LAUNCH).',
    'stops': 'What still stops the run (read on the host, in the sources named; whether the guest reaches each is INFERRED): a monitor, SPTM or TXM call that the monitor does not implement',
    'stops after': 'SNAP_PANIC_SERVE, which would serve them, is off: probes/xnuboot/sptm_dispatch.c:35-37 and the profile of the M1 monitor), which the page prints as MON STOP MONITOR or '
                   'DISPATCH_CMD for a call with no implementation and as MON STOP UNNAMED for a call refused after the panic entry (A= its x16, B= the panicking CPU, C= this CPU: '
                   'probes/xnuboot/report_layout.c:177; probes/sp11-stick-reporter.c:247-271)',
    'stops others': 'others end the run too (ASYNC, MMIO_STOP, DEVICE_RESET, OTHER: the page names each)',
    'stops more': 'a stage-2 fault that no model answers, MON STOP S2_FAULT with the address in A= (probes/xnuboot/trap_dispatch.c:472); an SError at EL2, MON STOP SERROR '
                  '(probes/xnuboot/async.c:144-157); and the monitor\'s own timer at the box end, MON STOP WATCHDOG (OBSERVED once: boot 20).',
    'panic path': 'What the panic path may reach (INFERRED; none of it has run on this unit): XNU draws its panic text with the video console into the panel range, after its panic-entry call '
                  'and one more SPTM call that asks for the panicking CPU (public source, tag xnu-12377.121.6, not this kernel\'s xnu-13432: made before the trap on the panic() funnel and '
                  'again after it)',
    'panic entry': '(probes/xnuboot/sptm_dispatch.c:117-194), whether the second is a monitor call that this monitor refuses is UNKNOWN.',
    'a16': 'The A16\'s desktop configuration sets let_panic with --panic-serve, which serves calls after the panic entry',
    'a16 docs': 'The documents of this tree name no device of the A16\'s panic or halt path (UNKNOWN which it touched).',
    'page shows': 'What the page can and cannot show. It prints the first five events of the monitor\'s log, the counts and the stop line, which the runner clips at 100 columns',
    'ends': 'What the run may end as (INFERRED from the host analysis and boot 23: none of it has been seen with let_panic, and which comes first, and when, is UNKNOWN).',
    'ends not': f'Not honoured: {STOP23} again and no text.',
    'reads seen': 'UNKNOWN; both reads ran inside boot 21\'s call of 2.044 s (OBSERVED once, not told apart). Both reads lie outside the time box.'}
FIVE_D_PINS = {
    'new': 'WHAT IS NEW. One input of the guest: the monitor\'s PARAMS word let_panic is 1 where rung 3\'s was 0 (section 3c); the command line is rung 3\'s',
    'same': 'Everything else is rung 3\'s: the same programs (SNAPMON-M1.BIN and SNAPREP-M1.BIN have the SHA-256 that boots 20, 21 and 23 ran), the 5 s box',
    'film': 'THE FILM is rung 3\'s, from the Shell\'s countdown until 60 seconds after the SNAPREP report page has appeared',
    'film text': 'THE PANIC TEXT, if there is one, is the result and may scroll off the panel: nobody touches the keyboard, the screen or the buttons while it runs (except FORCE OFF as step 5 of '
                 'section 5c says), and the film is its record.',
    'film where': f'the text can be anywhere in {PART} (INFERRED).',
    'stills': 'After those 60 seconds, and BEFORE FORCE OFF (only when the SNAPREP page is on the screen; if it is not, FORCE OFF 60 seconds after the MON CALL line as step 5 says, no stills '
              f'first: D8\'s 80 s bound), {STILLS} with the text readable (asked, not required, and valuable).',
    'only place': 'The panel is the only place that text can be read: the MON CON lines are not expected to carry it (INFERRED: serial=2; whether kprintf text reaches them is UNKNOWN).',
    'lowercase': 'Lowercase letters on the panel are the kernel\'s (boots 21 and 23: OBSERVED once each).',
    'expect': 'WHAT TO EXPECT. As in boots 21 and 23: from the MON CALL line the panel is cleared and fills with the kernel\'s log',
    'expect text': 'how long it takes is UNKNOWN; INFERRED, it is printed inside the run, which the monitor ends at the box at the latest.',
    'count': f'Count those {WAIT} seconds from the moment the MON CALL line is on the screen, on a watch or the camera\'s timer.',
    'decides': 'WHAT THE FILM DECIDES (what let_panic does to this run is INFERRED and has not been seen; boot 23 is the comparison, OBSERVED once). First the panel: is there a panic text, a line '
               f'that contains {PANIC_CPU}',
    'decides report': f'Then the report page: boot 23\'s stop was {STOP23} at 2.166 s, and the same stop again with no text is the falsifier of section 0.',
    'decides noise': 'NOT THE FAILURE: the noise lines of section 0a (the SPMI and RTKit lines OBSERVED once in boots 21 and 23, the AFKFirmwareService line in boot 21; INFERRED to come from the '
                     'monitor\'s empty models)',
    'panic': f'WHAT A PANIC LOOKS LIKE (INFERRED from XNU\'s panic format; none has been seen on this unit): a line that contains {PANIC_CPU} and a caller address',
    'panic example': '(the RTKit lines "General panic behavior" or the early line "debug_log_init: Error!! gPanicBase is still not initialized", OBSERVED twice in boot 23\'s replay, are not a panic)',
    'panic entry': 'The monitor answers the panic entry and lets the guest run on; it refuses every later call (section 3c), and that stop can come (read from probes/xnuboot/sptm_dispatch.c, '
                   'INFERRED) before the text is printed',
    'results head': 'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 4\'s. The panel is read first and then the stop; the classes below are read in this order',
    'panic text': 'RUNG 4 PANIC TEXT: the panel shows the kernel\'s panic text (above), whatever the page says and whatever the stop is. THE TEXT IS THE RESULT',
    'not a result': 'RUNG 4 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP',
    'not passed a': f'RUNG 4 NOT PASSED, (a): no panic text and {STOP23} again, boot 23\'s stop: the word was not honoured',
    'watchdog': 'RUNG 4 WATCHDOG, NO TEXT: MON STOP WATCHDOG at the end of the 5 s box and no panic text on the panel: the kernel was still running (its log may still be growing) or '
                'spinning, with no panic text',
    'new stop': 'RUNG 4 NEW STOP: no panic text and a MON STOP of another kind (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, DEVICE_RESET or another name)',
    'new stop s2': 'An S2_FAULT with A= inside the panel range (section 2) is not M1\'s expected end (section 5a).',
    'not passed b': f'RUNG 4 NOT PASSED, (b): a restart of the Surface before the report, no report (a page that stops at MON CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1), the panel '
                    f'still dark {WAIT} seconds after the MON CALL line',
    'separate': 'A run that ends as boot 23 did is still M1 PASSED (section 5a) and rung 4 not passed: the two verdicts are separate.'}
HOOK_PINS = {
    'kind': 'This record is of the choice that hands the guest the panel as the kernel\'s console (serial=2) with cpus=1 in its command line: the ram class names its range (section 3c)',
    'reading': 'what is new beyond section 0b and what counts as a result of rung 4.',
    '0a head': '0a. Where things stand (boots 20, 21, 23; 2026-11-18; one unit, one boot each; OBSERVED unless a sentence says INFERRED)',
    '0a loop': 'an early loop in the guest. Boot 21 did not stop there again, which fits it (INFERRED).',
    '0b ran': 'under a monitor that ran three times on this machine (boots 20, 21 and 23: section 0a).',
    '0b hazard': 'and the kernel draws its console there (OBSERVED once: boots 21 and 23): from the MON CALL line the panel is cleared and fills with the kernel\'s log',
    '0b hazard path': 'let_panic lets the kernel go down a path that no run on this unit has taken, its own panic handler (INFERRED to be the path that boot 23 stopped at)',
    '0b live': 'The monitor reads the let_panic word while the guest runs, like the PARAMS words of the last bullet below, but it is 1 here',
    '5a console': 'boot 23 honoured cpus=1 (OBSERVED once), and what let_panic does to this run is INFERRED and has not been seen.',
    '5b guest exc': 'An expected end of M1. OBSERVED once (boot 23, one unit, one boot).',
    '5b panic': 'With serial=2 the panic text is expected on the panel, not in the MON CON lines (INFERRED: whether kprintf text reaches them is UNKNOWN).',
    'step 6': f'Before that FORCE OFF, and after those 60 seconds, {STILLS} so that the text on it can be read (asked, not required; section 5d).',
    'rung': 'and rung 3, the same with cpus=1 (boot 23), whose records have their own approvals, spent: this record is rung 4, the panel handed to the guest as the kernel\'s console (serial=2) with '
            'cpus=1 in its command line and the monitor\'s let_panic word set, and no approval covers another\'s boot.'}


def hook_pins():
    p, f = STATE['panic_panel'], flat(STATE['panic'])
    size, sha, chunks = re.search(r'data GUEST\.DAT (\d+) bytes sha256 ([0-9a-f]{64}) chunks (\d+)', f).groups()
    return {**HOOK_PINS, '1a panel': f'panel {p["fb_pa"]:#x} + {p["fb_span"]:#x} bytes, {p["gop"]["width"]} x {p["gop"]["height"]}, text mode, the kernel\'s console (serial=2) and cpus=1 in its '
            'command line, let_panic=1 in PARAMS: section 3c',
            'step 4': f'Then compare, on the PAYLOAD LOADED line, INDEX=2 ... BYTES={size} SHA={sha[:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, LABEL=M1RUN-LETPANIC-{LABEL} CHUNKS={chunks} '
            '(boot 23\'s page had LABEL=M1RUN-CPUS1-RUNG3 CHUNKS=46, INDEX=2 ... SHA=A7E159AD6C18832C and INDEX=3 ... SHA=339D64BE6D857808, the SHA of its PIN.DAT: rung 3\'s bundle staged under '
            'this plan has the same programs, the same size and the same chunk count, and only those two SHAs and the label tell it from this one; this record cannot name its own PIN.DAT SHA, '
            'which depends on this record\'s SHA-256).'}


def pins_3c():
    return {**SECTION3C_PINS, 'unchanged': f'SNAPMON-M1.BIN and SNAPREP-M1.BIN have the SHA-256 that boots 20, 21 and 23 ran ({r2.RUNG1_MONITOR[:8]}... and {r2.RUNG1_REPORTER[:8]}..., in full in '
                                           'section 1), the time box is 5 s and the store classes are rung 3\'s.'}


def all_pins():
    """Every pin of the record, with the section in the name."""
    return {f'{section}/{name}': phrase for section, pins in (('0', SECTION0_PINS), ('0a', SECTION0A_PINS), ('3c', pins_3c()), ('5d', FIVE_D_PINS), ('hooks', hook_pins())) for name, phrase in pins.items()}


class Options(unittest.TestCase):
    def raises(self, name, function, *args):
        with self.assertRaises(ch.Refused) as caught:
            function(*args)
        self.assertEqual(caught.exception.name, name)
        return caught.exception

    def test_the_option_is_admitted_for_its_one_form_and_every_other_combination_is_a_named_refusal(self):
        self.assertEqual((m1.LET_PANIC_FORM, ch.LET_PANIC_FORM), (('console', 'cpus=1', 5),) * 2)
        m1.check_options(7, False, 5, 'console', 0, 'cpus=1', True)
        m1.check_options(7, False, 5, 'console', 0, 'cpus=1', False)
        for form, name in {('withheld', 0, None, 5): 'LET_PANIC_REFUSED', ('handed', 0, None, 5): 'LET_PANIC_REFUSED', ('console', 0, None, 5): 'LET_PANIC_REFUSED',
                           ('console', 0, 'cpus=1', 4): 'LET_PANIC_REFUSED', ('console', 0, 'cpus=1', 1): 'LET_PANIC_REFUSED', (None, None, None, 5): 'LET_PANIC_REFUSED',
                           ('console', 0, 'cpus=2', 5): 'BOOT_ARG_REFUSED', ('console', 1, 'cpus=1', 5): 'OPTIONS_BAD', ('console', 0, 'cpus=1', 6): 'OPTIONS_BAD',
                           ('handed', 0, 'cpus=1', 5): 'BOOT_ARG_REFUSED'}.items():
            with self.subTest(form=form):
                panel, hvc, word, box = form
                caught = self.raises(name, m1.check_options, 7, False, box, panel, hvc, word, True)
                if name == 'LET_PANIC_REFUSED':
                    self.assertIn('--panel console --boot-arg cpus=1 --time-box-s 5', caught.detail, 'the message names the one form')
        self.raises('LET_PANIC_REFUSED', m1.check_let_panic, True, 'console', 'cpus=1', 5, False)
        self.assertIsNone(m1.check_let_panic(False, 'withheld', None, 1, False))
        self.assertIn('LET_PANIC_REFUSED', ch.KNOWN_REFUSALS)

    def test_each_bad_form_is_refused_end_to_end_by_name_and_writes_nothing(self):
        word = dict(boot_arg='cpus=1')
        forms = {'the panel handed': ('handed', {}, 'LET_PANIC_REFUSED'), 'the panel withheld': ('withheld', {}, 'LET_PANIC_REFUSED'), 'the console without the word': ('console', {}, 'LET_PANIC_REFUSED'),
                 'a box of 4 s': ('console', dict(word, time_box_s=4), 'LET_PANIC_REFUSED'), 'a box of 1 s': ('console', dict(word, time_box_s=1), 'LET_PANIC_REFUSED'),
                 'another word': ('console', dict(boot_arg='cpus=2'), 'BOOT_ARG_REFUSED'), 'a patched kernel': ('console', dict(word, sptm_hvc_calls=1), 'OPTIONS_BAD'),
                 'a box over the wrapper\'s': ('console', dict(word, time_box_s=6), 'OPTIONS_BAD'), 'a facts label with the mark': ('console', dict(word, facts=BLD.facts_with('LETPANIC-x')), 'PIN_STRING'),
                 'no monitor entry run': ('handed', dict(m1_run=None, m1_efi=None), 'LET_PANIC_REFUSED'), 'the console, no monitor entry run': ('console', dict(word, m1_run=None, m1_efi=None), 'OPTIONS_BAD')}
        for what, (choice, over, name) in forms.items():
            with self.subTest(what), stand_in():
                args = PT.real_args('bad-form', choice)
                args.update(over)
                code, text, err = BT.cli(['authorisation-template', 'real', *BT.opts('real', **args), '--let-panic'])
                self.assertEqual((code, BT.refusal(text)), (1, f'REFUSED={name}'), err)
                self.assertFalse((SCRATCH / 'out' / 'bad-form').exists())

    def test_no_environment_variable_sets_the_word_and_the_synthetic_kind_has_no_such_option(self):
        for flag in (True, False):
            with self.subTest(flag=flag), mock.patch.dict(os.environ, {'SNAP_LET_PANIC': '1'}), stand_in():
                code, text, err = BT.cli(['authorisation-template', 'real', *BT.opts('real', **args_of('env', PANIC)), *(['--let-panic'] if flag else [])])
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=ENV_SNAP_SET'), err)
        with self.assertRaises(SystemExit):
            BT.cli(['authorisation-template', 'synthetic', *BT.opts(stores='ram,el2_sysreg'), '--let-panic'])
        source = (TOOLS / 'sp11_stick_bundle_build_opts.py').read_text()
        self.assertIn("p.add_argument('--let-panic', action='store_true'", source.split("p.add_argument('--kernelcache', required=True)")[1][:2000])
        self.assertNotIn('--let-panic', source.split("if kind == 'synthetic':\n        for flag, default")[1].split("p.add_argument('--kernelcache'")[0])
        self.assertNotIn('SNAP_LET_PANIC', (TOOLS / 'sp11_stick_bundle_chunks.py').read_text())


class Params(unittest.TestCase):
    def words_of_struct(self):
        """{name: index} of the leading words of struct params, from probes/xnuboot/report_layout.c (up to skip_undef)."""
        index, names = 0, {}
        for line in (ROOT / 'probes/xnuboot/report_layout.c').read_text().split('struct params {')[1].splitlines():
            found = re.match(r'\s*uint64_t ([a-z_0-9, \[\]]+);', line)
            for part in found.group(1).split(',') if found else ():
                name, size = re.match(r'\s*([a-z_0-9]+)(?:\[(\d+)\])?', part).groups()
                names[name], index = index, index + int(size or 1)
            if 'skip_undef' in names:
                break
        return names

    def params(self, **words):
        data = bytearray(m1.PARAMS_BYTES)
        for index, value in words.items():
            data[8 * int(index[1:]):8 * int(index[1:]) + 8] = value.to_bytes(8, 'little')
        return [dict(kind=sb.KIND_PARAMS, dest=0x88000C000, data=bytes(data))]

    def refused(self, chunks, reference):
        with self.assertRaises(ch.Refused) as caught:
            m1.check_launch(chunks, reference)
        self.assertEqual(caught.exception.name, 'PARAMS_LAUNCH')
        return caught.exception

    def test_the_word_indexes_are_the_structs_and_the_offset_the_text_says(self):
        names = self.words_of_struct()
        self.assertEqual((names['let_panic'], names['permissive'], names['skip_undef'], names['watchdog_seconds']), (23, 21, 28, ch.PARAMS_WATCHDOG))
        self.assertEqual(dict(m1.DIAG_WORDS), {'permissive': 21, 'let_panic': 23, 'skip_undef': 28})
        self.assertEqual((m1.LET_PANIC_AT, rp.LET_PANIC_AT), (8 * names['let_panic'], '0xB8'))

    def test_the_guest_of_rung_4_differs_from_the_guest_of_rung_3_in_the_let_panic_word_only(self):
        (three, rest3), (four, rest4) = chunks_of(KINDS[3]), chunks_of(PANIC)
        by = lambda chunks: {c['dest']: (c['kind'], bytes(c['data']) if c.get('data') is not None else c['size']) for c in chunks}    # noqa: E731
        a, b = by(three), by(four)
        self.assertEqual(sorted(a), sorted(b))
        differing = [dest for dest in a if a[dest] != b[dest]]
        self.assertEqual([a[d][0] for d in differing], [sb.KIND_PARAMS], 'one chunk differs: the PARAMS chunk')
        pa, pb = a[differing[0]][1], b[differing[0]][1]
        self.assertEqual([i for i in range(len(pa) // 8) if pa[8 * i:8 * i + 8] != pb[8 * i:8 * i + 8]], [WORD])
        self.assertEqual((int.from_bytes(pa[8 * WORD:8 * WORD + 8], 'little'), int.from_bytes(pb[8 * WORD:8 * WORD + 8], 'little')), (0, 1))
        self.assertEqual((rest3, rest4), ((None,), (pa,)), 'the read-back is made against the PARAMS bytes of rung 3\'s guest')

    def test_check_launch_holds_the_diagnostic_words_and_the_read_back_of_the_option(self):
        plain, ref = self.params(), bytes(m1.PARAMS_BYTES)
        m1.check_launch(plain)
        m1.check_launch(self.params(w23=1), ref)
        for name, index in m1.DIAG_WORDS:
            for value in (1, 2, 1 << 63):
                with self.subTest(name, value=value):
                    self.assertIn(name, self.refused(self.params(**{f'w{index}': value}), None).detail)
        for what, (chunks, reference) in {'the word 0 under the option': (plain, ref), 'the word 2 under the option': (self.params(w23=2), ref),
                                          'another word differs': (self.params(w23=1, w5=7), ref), 'the last word differs': (self.params(w23=1, w1153=7), ref),
                                          'a reference that is not rung 3\'s': (self.params(w23=1), self.params(w9=1)[0]['data']), 'a short reference': (self.params(w23=1), ref[:-8]),
                                          'permissive under the option': (self.params(w23=1, w21=1), ref), 'skip_undef under the option': (self.params(w23=1, w28=1), ref)}.items():
            with self.subTest(what):
                self.refused(chunks, reference)

    def test_a_loader_that_changes_another_word_with_the_option_is_refused_end_to_end(self):
        code, text, err = record('seeded-extra-word', extra_word=(100, 7))
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PARAMS_LAUNCH'), err)
        self.assertIn('[23, 100]', err + text)
        self.assertEqual(record('seeded-same-word', extra_word=(23, 1))[0], 0, 'a loader that writes only the let_panic word is the build that rung 4 is')

    def test_the_loader_writes_the_word_from_built_and_the_builder_sets_it_for_the_option_only(self):
        self.assertIn("words.append(1 if built.get('let_panic') else 0)", (ROOT / 'probes/xnuboot.py').read_text())
        source = (TOOLS / 'sp11_stick_bundle_chunks.py').read_text()
        self.assertEqual(source.count("built['let_panic']"), 1, 'one place sets the word, and nothing else touches it')
        self.assertLess(source.index("built['let_panic'] = True"), source.index("built['chunks'][xb.PARAMS_PA] = xb.params_blob(built)"), 'set before PARAMS is serialised')

    def test_the_finished_bundle_has_the_word_and_rung_3s_has_not(self):
        for kind, want in ((PANIC, 1), (KINDS[3], 0)):
            code, text, err, out = build(f'bundle-{NAMES[kind]}', kind)
            self.assertEqual(code, 0, (text, err))
            guest = (out / 'bundle' / 'SP11' / 'GUEST.DAT').read_bytes()
            params = [c for c in sb.unpack_guest(guest)[1] if c['kind'] == sb.KIND_PARAMS][0]
            data = guest[params['file_offset']:params['file_offset'] + params['size']]
            with self.subTest(NAMES[kind]):
                self.assertEqual([int.from_bytes(data[8 * i:8 * i + 8], 'little') for _, i in m1.DIAG_WORDS], [0, want, 0])


class Label(unittest.TestCase):
    def test_the_label_of_the_record_has_its_own_marker_distinct_from_the_others(self):
        self.assertEqual(m1.pinned_label('RUNG4', True, 'console', 'cpus=1', True), 'M1RUN-LETPANIC-RUNG4')
        self.assertEqual(len('M1RUN-LETPANIC-RUNG4'), 20)
        self.assertEqual(m1.pinned_label('RUNG3', True, 'console', 'cpus=1'), 'M1RUN-CPUS1-RUNG3')
        self.assertEqual(m1.pinned_label('x' * 8, True, 'console', 'cpus=1', True), 'M1RUN-LETPANIC-' + 'x' * 8)
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('x' * 9, True, 'console', 'cpus=1', True)
        self.assertEqual(caught.exception.name, 'PIN_STRING')
        marks = [m for m in m1.PANEL_MARKS.values() if m] + sorted(set(m1.BOOT_ARGS.values())) + [m1.LET_PANIC_MARK]
        self.assertEqual(len(marks), len(set(marks)))
        self.assertFalse([a for a in marks for b in marks if a != b and a.startswith(b)], 'no marker is the start of another: one label, one kind of record')
        self.assertEqual(m1.LET_PANIC_MARK, 'LETPANIC-')

    def test_the_label_is_for_the_option_only_and_a_facts_label_with_its_mark_is_refused(self):
        for choice, word in (('withheld', None), ('handed', None), ('console', None), ('handed', 'cpus=1'), ('console', 'cpus=2')):
            with self.subTest(choice=choice, word=word), self.assertRaises(ch.Refused) as caught:
                m1.pinned_label('x', True, choice, word, True)
            self.assertIn(caught.exception.name, ('LET_PANIC_REFUSED', 'BOOT_ARG_REFUSED'))
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('x', False, 'console', None, True)
        self.assertEqual(caught.exception.name, 'LET_PANIC_REFUSED')
        for choice, word, option in KINDS:
            with self.subTest(choice=choice, word=word, option=option), self.assertRaises(ch.Refused) as caught:
                m1.pinned_label('LETPANIC-x', True, choice, word, option)
            self.assertEqual(caught.exception.name, 'PIN_STRING')
        self.assertEqual(m1.pinned_label('LETPANIC-x', False), 'LETPANIC-x', 'the other kinds keep the facts label')

    def test_the_label_is_in_the_record_the_pin_and_the_page_words(self):
        self.assertIn(f'(format 1, label M1RUN-LETPANIC-{LABEL})', STATE['panic'].splitlines()[0])
        code, text, err, out = build('label-panic', PANIC)
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(BLD.label_of(BLD.pin_of(out)).decode(), f'M1RUN-LETPANIC-{LABEL}')
        self.assertEqual(sb.check_pin(BLD.pin_of(out)), 0)


class Kinds(unittest.TestCase):
    def test_the_guest_the_pin_and_the_approved_record_of_one_kind_belong_to_no_other(self):
        pins = {}
        for kind in KINDS:
            code, text, err, out = build(f'belongs-{NAMES[kind]}', kind)
            self.assertEqual(code, 0, (text, err))
            pins[kind] = (BLD.pin_of(out), (out / 'bundle' / 'SP11' / 'GUEST.DAT').read_bytes())
        self.assertEqual(len({PT.sha(guest) for _, guest in pins.values()}), len(KINDS), 'five guests, five digests')

        def belongs(record_text, pin):
            fields = sb.unpack(pin, sb.PIN_LAYOUT)
            return bytes(fields['authorisation_sha256']).hex() == PT.sha(record_text.encode()) and f'label {BLD.label_of(pin).decode()})' in record_text.splitlines()[0]
        for kind in KINDS:
            for other in KINDS:
                with self.subTest(record=NAMES[kind], pin=NAMES[other]):
                    self.assertEqual(belongs(record(f'again-{NAMES[kind]}', kind)[1], pins[other][0]), kind == other, 'the PIN.DAT of one kind belongs to the record of that kind and no other')
                if kind != other:
                    with self.subTest(guest=NAMES[kind], pin=NAMES[other]):
                        self.assertNotEqual(sb.check_guest(pins[kind][1], pins[other][0]) or sb.check_digests(pins[kind][1], pins[other][0], sb.HASH_CHUNKS | sb.HASH_FILE)[0], 0)

    def test_a_build_with_the_approved_record_of_another_kind_is_refused_whichever_is_the_guest(self):
        for kind in KINDS:
            for other in KINDS:
                if kind != other and PANIC in (kind, other):
                    with self.subTest(guest=NAMES[kind], approved=NAMES[other]):
                        code, text, err, out = build(f'cross-{NAMES[kind]}-{NAMES[other]}', kind, approved=STATE[NAMES[other]])
                        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                        self.assertFalse(out.exists(), 'nothing is written for a record that is not this build\'s')

    def test_the_other_four_records_do_not_depend_on_this_change_and_this_one_is_the_same_twice(self):
        for kind in KINDS[:4]:
            with self.subTest(NAMES[kind]):
                code, text, err = PT.record(f'plain-{NAMES[kind]}', kind[0], **({'facts': BLD.facts_with(LABEL), 'boot_arg': kind[1]} if kind[1] else {}))
                self.assertEqual(code, 0, err)
                self.assertEqual(text, record(f'with-{NAMES[kind]}', kind)[1], 'the stand-in changes nothing without the option')
                for word in ('let_panic', 'LETPANIC', 'WHY RUNG 4', 'rung 4', 'Boot 23', 'boot 23'):
                    self.assertNotIn(word, text)
        self.assertEqual(record('twice')[1], STATE['panic'])


class Words(unittest.TestCase):
    def sections(self):
        f = flat(STATE['panic'])
        head3c = '3c. The panel as the kernel\'s console with cpus=1 and let_panic'
        return dict(section0=f.split('0. What approving this text decides (read this first)')[1].split('0a. Where things stand')[0],
                    section0a=f.split('0a. Where things stand')[1].split('0b. The hazards, in plain words')[0],
                    section3c=head3c + f.split(head3c)[1].split('4. Time box')[0], section5d=f.split('5d. Rung 4: what is new')[1])

    def test_each_sentence_of_sections_0_0a_3c_and_5d_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = self.sections()
        for name, pins in (('section0', SECTION0_PINS), ('section0a', SECTION0A_PINS), ('section3c', pins_3c()), ('section5d', FIVE_D_PINS)):
            mutants_rejected(self, parts[name], pins)

    def test_each_hook_sentence_is_pinned_and_a_record_without_it_is_rejected(self):
        pins, f = hook_pins(), flat(STATE['panic'])
        self.assertEqual(pin_problems(f, pins), [])
        for name, phrase in pins.items():
            with self.subTest(pin=name):
                self.assertEqual(f.count(flat(phrase)), 1, 'each sentence is in the text once')
                self.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), pins), [name])

    def test_the_sections_that_rung_4_does_not_change_are_rung_3s_byte_for_byte(self):
        three, four = split_sections(record('same-rung3', KINDS[3])[1]), split_sections(STATE['panic'])
        self.assertEqual(list(three), list(four))
        for key in ('1', '1b', '2', '3', '3b', '4', '5'):
            with self.subTest(section=key):
                differing = [a for a, b in zip(three[key], four[key]) if a != b]
                self.assertEqual((len(three[key]), len(differing), all('sha256' in a for a in differing)), (len(four[key]), 2 if key == '1' else 0, True), 'section 1 names the digests of GUEST.DAT: its own')
        for key in ('0', '0a', '0b', '1a', '3c', '5a', '5b', '5c', '5d'):
            with self.subTest(section=key):
                self.assertNotEqual(three[key], four[key], 'and the sections that it says again are not copies')

    def test_section_5d_is_at_most_75_lines_and_the_reading_pointer_names_it(self):
        lines = rp.section_5d(panel4(), WAIT)
        self.assertLessEqual(len(lines), 75)
        self.assertTrue(all(len(line) <= 118 and line.isascii() for line in lines))
        self.assertEqual(len(STATE['panic'].split('\n5d. ')[1].splitlines()), len(lines))
        self.assertIn('sections 0, 0a, 0b, 4, 5, 5a, 5c and 5d', flat(STATE['panic']))

    def test_a_generator_without_one_of_the_blocks_makes_a_record_that_the_pins_reject(self):
        pins, keys = all_pins(), rec.m1g.PANEL['console+cpus=1+let_panic']
        dropped = lambda key, value: mock.patch.dict(keys, {key: value})      # noqa: E731
        for what, (patch, pin) in {'no why': (dropped('why', lambda real: ()), '0/why'), 'no boot 23 facts in 0a': (dropped('stand', lambda real: ()), '0a/boot 23'),
                                   'no hazard in 0b': (dropped('hazard', ''), 'hooks/0b hazard'), 'no decision': (dropped('decides', ''), '0/decides'),
                                   'rung 3\'s count of runs': (dropped('ran', 'ran twice on this machine (boots 20 and 21: section 0a).'), 'hooks/0b ran'),
                                   'no stop seen': (dropped('stop_seen', {}), 'hooks/5b guest exc'), 'no 5d': (mock.patch.object(rp, 'section_5d', lambda panel, wait: ()), '5d/new'),
                                   'no word paragraph': (mock.patch.object(rp, 'word_text', lambda: 'x'), '3c/words'),
                                   'no extra paragraphs': (mock.patch.object(rp, 'extra_paragraphs', lambda panel: ()), '3c/word'),
                                   'rung 3\'s ends': (mock.patch.object(rp, 'expect_text', r3.expect_text), '3c/ends'), 'no console note': (dropped('console', ''), 'hooks/5a console'),
                                   'no 0a heading': (dropped('stand_head', 'x'), 'hooks/0a head'), 'no step 6 stills': (dropped('step6', r3.STEP6), 'hooks/step 6'),
                                   'rung 3\'s step 4': (dropped('step4', r3.step4), 'hooks/step 4'), 'rung 3\'s rung': (dropped('rung', r3.RUNG), 'hooks/rung'),
                                   'no params row': (mock.patch.object(rp, 'PARAMS_ROW', 'x'), '3c/params row')}.items():
            with self.subTest(what), patch:
                code, text, err = record('mutant-panic')
                self.assertEqual(code, 0, err)
                self.assertNotEqual(text, STATE['panic'])
                self.assertIn(pin, pin_problems(text, pins), what)

    def test_boot_23s_numbers_are_the_operators_film_and_the_stop_is_the_one_the_text_names(self):
        self.assertEqual((rp.BOOT23_TICKS, rp.BOOT23_S), (41_593_665, '2.166'))
        self.assertEqual(rp.BOOT23_STOP, dict(pc=0xFFFFFE000B8C2288, a=0x2000000, b=0xFFFFFEA937FDC000))
        self.assertEqual((rp.STOP23, rp.BOOT23_C, rp.LINE), (STOP23, '0XFFFFFE000B8C22', r3.LINE))
        self.assertEqual(rp.LINE, pn.command_line('console', 'cpus=1'))
        self.assertEqual({k: v for k, v in STATE['panic_panel'].items() if k != 'let_panic'}, STATE['rung3_panel'], 'the guest is read as rung 3\'s but for the option')
        self.assertTrue(STATE['panic_panel']['let_panic'])
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': 'cpus=1', 'let_panic': True}), 'console+cpus=1+let_panic')
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': 'cpus=1'}), 'console+cpus=1')
        self.assertEqual((0x02000000 >> 26, 0x02000000 & 0x1FFFFFF), (0, 0), 'A= of the stop: EC 0 (unknown reason) with an empty syndrome')

    def test_every_source_line_it_cites_has_its_fragment_and_every_citation_is_in_the_table(self):
        pattern = r'((?:probes|shadow|compat|tools)/[A-Za-z0-9_./+-]+\.(?:c|h|py)):(\d+)(?:-(\d+))?'
        cited = {(p, int(a), int(b or a)) for p, a, b in re.findall(pattern, ' '.join(self.sections().values()))}
        mine = {(p, a, b) for p, a, b, _ in rp.CITES}
        self.assertLessEqual(cited - mine, {(p, a, b) for p, a, b, _ in pn.CITES}, 'cited, and no table holds the fragments of it')
        self.assertEqual(mine - cited, set(), 'every entry of the table is cited')
        for path, first, last, fragments in rp.CITES:
            shift = pn.r5.CITE_DRIFT.get((path, first), 0)
            first, last, fragments = pn.r5.REPORTER_DRIFT.get((path, first, last), (first, last, fragments))
            held = '\n'.join((ROOT / path).read_text().splitlines()[first - 1 + shift:last + shift])
            for fragment in fragments:
                with self.subTest(f'{path}:{first}-{last} {fragment}'):
                    self.assertIn(fragment, held)

    def test_the_documents_it_cites_are_cited_by_section_and_no_line_of_a_document_is_cited(self):
        f = flat(STATE['panic'])
        operator = f.split('3b. Device code in the monitor')[0] + ' ' + f.split('3c. The panel as the kernel\'s console with cpus=1 and let_panic')[1]
        for ref, path, heading, fragments in rp.DOC_CITES:
            lines, fenced, at, end, level = (ROOT / path).read_text().splitlines(), False, None, None, len(heading.split()[0])
            for number, line in enumerate(lines):
                if line.lstrip().startswith('```'):
                    fenced = not fenced
                elif fenced:
                    continue
                elif at is None and line.startswith(heading):
                    at = number
                elif at is not None and re.match(rf'#{{1,{level}}} ', line):
                    end = number
                    break
            with self.subTest(ref):
                self.assertIsNotNone(at, f'{path} has no section that opens with {heading!r}')
                for fragment in fragments:
                    self.assertIn(fragment, '\n'.join(lines[at:end]))
                self.assertIn(ref, operator, 'in the table, and the text does not cite it')
        self.assertEqual(re.findall(r'docs/[A-Za-z0-9_-]+\.md:\d+', ' '.join(self.sections().values())), [], 'the documents move: a section is cited, not a line')

    def test_nothing_is_called_verified_and_each_paragraph_says_what_it_is(self):
        f = STATE['panic']
        self.assertTrue(f.isascii() and 'VERIFIED' not in f)
        for rel in ('tools/sp11_stick_bundle_panel_panic.py', 'tools/test-sp11-stick-monitor-panic.py'):
            self.assertTrue((ROOT / rel).read_text().isascii(), rel)
        self.assertNotIn('VERIFIED', (ROOT / 'tools/sp11_stick_bundle_panel_panic.py').read_text())
        tails = (' on the A16', ': docs/', ' once', ', one unit, one boot', ' (B1 attempts 1 to 5, operator report)', ' unless a sentence says INFERRED', ' twice in boot 23\'s replay')
        for name, text in self.sections().items():
            for m in re.finditer('OBSERVED', text):
                with self.subTest(name, at=text[m.start():m.start() + 40]):
                    self.assertTrue(any(text[m.end():].startswith(tail) for tail in tails))
        for paragraph in (*rp.extra_paragraphs(panel4()), rp.word_text(), rp.expect_text(panel4()['run']), ' '.join(rp.stand_extra({})), ' '.join(rp.why_lines({}))):
            with self.subTest(paragraph[:40]):
                self.assertTrue(re.search(r'OBSERVED|INFERRED|UNKNOWN', paragraph), 'each paragraph is labelled')
        self.assertTrue(all(len(line) <= 120 for line in f.split('0. What approving')[1].split('1. What may run')[0].splitlines() + f.split('5d. Rung 4')[1].splitlines()))

    def test_the_hedge_on_the_console_text_is_the_reviews_and_the_stills_are_asked_not_required(self):
        f = flat(STATE['panic'])
        self.assertNotIn('the panic text is on the panel, not in the MON CON lines.', f)
        self.assertEqual(f.count(f'three sharp still photographs of {PART}'), 2, 'step 6 of section 5c and section 5d')
        self.assertNotIn('left half', f)


class Hygiene(unittest.TestCase):
    def test_the_record_leaves_one_kib_free_and_the_15_16_rule_is_not_this_ones(self):
        self.assertLessEqual(len(STATE['panic'].encode()), bb.AUTHORISATION_CAP - 1024, 'rung 3\'s 15/16 rule (H16, H17) cannot hold here: 1 KiB is left free (2 KiB before R6.2); the real '
                             'record is 107 B longer than the stand-in and the regeneration checks it')
        self.assertEqual(flat(STATE['panic']).count(f'if the SNAPREP page is not on the screen {WAIT} seconds after the MON CALL line, FORCE OFF'), 4)
        self.assertEqual(flat(STATE['panic']).count('- Panel as the console (sections 3c and 5d):'), 1)
        self.assertFalse([s for s in ('REPORT.DAT is a RAM area', 'Other differences from the withheld guest') if s in flat(STATE['panic'])], 'two cut sentences')

    def test_the_module_and_the_suite_are_under_the_size_of_a_file_of_this_tool(self):
        for rel in ('tools/sp11_stick_bundle_panel_panic.py', 'tools/test-sp11-stick-monitor-panic.py', 'tools/sp11_stick_bundle_panel.py', 'tools/sp11_stick_bundle_build.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertLess(len(text.encode()), 51_200)
                self.assertLess(len(text.splitlines()), 1500)
                self.assertEqual(text.splitlines()[1 if text.startswith('#!') else 0].count('SPDX-License-Identifier: MIT'), 1)

    def test_the_notes_hold_k12_the_row_of_the_suite_the_option_and_the_guest(self):
        texts = {name: (ROOT / f'docs/SP11-STICK-{name}.md').read_text() for name in ('MONITOR', 'REAL', 'BUILD')}
        monitor, real, build_ = (' '.join(texts[name].split()) for name in ('MONITOR', 'REAL', 'BUILD'))
        k12 = ' '.join(texts['MONITOR'].split('**K12, rung 4')[1].split('\n\n')[0].split())
        self.assertLess(monitor.index('**K10, rung 3'), monitor.index('**K12, rung 4'))
        for needle in ('`--let-panic`', '`LET_PANIC_REFUSED`', '`M1RUN-LETPANIC-`', '`PARAMS_LAUNCH`', 'H18', 'word 23', 'Caps raised on purpose, nothing cut', '`AUTH_RECORD_MISMATCH`'):
            self.assertIn(needle, k12)
        self.assertNotIn('VERIFIED', k12)
        self.assertIn('| H18 | test-sp11-stick-monitor-panic.py |', monitor)
        self.assertRegex(real, r'\| GUEST\.DAT, panel console with `cpus=1` and `--let-panic` \(rung 4\) \| \d[\d,]* bytes, \d+ chunks')
        self.assertRegex(real, r'\| GUEST\.DAT SHA-256, console, cpus=1, let_panic, 0 \| [0-9a-f]{64} \|')
        self.assertIn('`--let-panic`', build_)
        self.assertIn('`LET_PANIC_REFUSED`', build_)


if __name__ == '__main__':
    unittest.main()
