#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the profile paragraph of a record (tools/sp11_stick_bundle_panel_profile.py): what a record of any kind says of a guest whose device tree is one of the eight
profiles of the builder's menu. A phrase pin and a deletion mutant for each sentence, for each of the eight profiles with
and without the facts of the kind; a generator without one of its blocks makes a text that the pins reject; the base board has no paragraph; the refusals by name; the guard that no
record built with a profile says that the guest differs from its kind's in one thing, in one PARAMS word or with the same size and chunks (`record_problems`); the sizes.

    python3 tools/test-sp11-stick-monitor-profile-text.py

Standalone: it imports the module and the record's own filler (tools/sp11_stick_bundle_record_m1.py wrap), no builder and no kernelcache. The two classes `Menu` compare the module
with the menu of the builder (probes/guestprofile_m1.py, tools/sp11_stick_bundle_profile.py, docs/SP11-M1-GUEST-PROFILES.md of the profile-menu change) and say SKIP when it is not in the
tree. The numbers are those of the report of that change, typed again here. Nothing here runs the unit: Hardware NOT RUN."""
import ast
import itertools
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_stick_bundle_panel_profile as pp  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402

flat = lambda text: ' '.join(text.split())    # noqa: E731
BASE = 'j700ap-26A428'
P = BASE + '-m1-'
BANNED = 'VERI' + 'FIED'        # the word that no record says (it is not spelled out in this file either)
# name -> (mark, sets, nodes of the tree, bytes of its DEVICE_TREE chunk, GUEST.DAT bytes and SHA prefix on rung 4's kind), typed from the report of the menu's change
TABLE = {
    P + 'quiet': ('Q-', 'accel audio', 352, 327664, 118885568, '06056EAE638DF055'),
    P + 'quiet-aop': ('A-', 'accel audio aop', 337, 321344, 118879232, '813365207B3D7E6B'),
    P + 'quiet-dcp': ('D-', 'accel audio dcpext dcp', 341, 315952, 118873856, '3F928286CA0966F4'),
    P + 'quiet-ave': ('V-', 'accel audio ave', 350, 326000, 118883904, '85FFB7503CD79195'),
    P + 'quiet-clpc': ('C-', 'accel audio clpc', 351, 321424, 118879360, '94232D67494F7D29'),
    P + 'quiet-usb': ('U-', 'accel audio usb', 345, 324224, 118882112, '9CDBA4F15D1EDFDD'),
    P + 'quiet-smc': ('S-', 'accel audio aop dcpext dcp smcbtm', 313, 302608, 118860544, 'F577E8D1D0F2CBAD'),
    P + 'all-quiet': ('X-', 'accel audio aop dcpext dcp ave smcbtm', 311, 300944, 118858880, 'A3799F9BD1E68CE8'),
}
NODES = dict(accel=3, audio=40, aop=15, dcpext=4, dcp=7, ave=2, smcbtm=13, clpc=1, usb=7)
ROOTS = {
    'accel': 'avd, scaler0, apr',
    'audio': 'iop-audio-controller, iop-voicetrigger-controller, admac-base-ns, admac-leap-ns, admac-leap-s, admac-leap-s-rx-0-proxy, audio-shared-dart-proxy, audio-hp-mic-proxy, '
             'i2c0/audio-codec-output',
    'aop': 'aop, aop2, aop-spmi0', 'dcpext': 'dispext0, dcpext, dcpext-expert', 'dcp': 'dcp, dcp0-expert, dp-audio0, dp-audio1, disp0, /device-tree/backlight',
    'ave': 'ave, ave1', 'smcbtm': 'smc, smctempsensor0, /device-tree/buttons, :j700_has_mesa_0, nub-spmi0/btm', 'clpc': 'pmgr/clpc', 'usb': 'usb-drd, atc0-dpphy, i2c2/usb-hub-i2c',
}
EXTRAS = {'smcbtm': ' and 7 declarations withdrawn with them (pmu-main, multi-touch, ppm)', 'clpc': ' and the base board\'s function-ane_perf_ctr on it'}
REASONS = {
    'accel': 'avd\'s version read reached an unbacked aperture on the A16; scaler0 and apr are absent there', 'audio': 'no audio hardware; the first playback ended an A16 run',
    'aop': 'starts waited at RTBuddy routes 0x65 and 0x69 and in Stockholm', 'dcpext': 'the external display pipe',
    'dcp': 'waited for the DCP expert (route 0xcf), sio-dma and IONVRAM', 'ave': 'waited on a PMGR state change and a mutex',
    'smcbtm': 'smc never publishes smc-pmu; withdrawing it without btm lets pmu-main return and btm panic the A16 (unable to config PMU)',
    'clpc': 'the A16 board has no cluster governor', 'usb': 'clients waited for the missing PHY',
}
RAN = {'aop': 'aop/aop2', 'clpc': 'clpc'}
KIND = 'rung 5, the CPU-start ACK with six CPUs, let_panic set'
SHA16 = '0123456789ABCDEF'
# What the sizes of the calls are held to (bytes of the paragraphs with the facts of a kind, and the same filled into lines), with a little room: the report names the measured ones.
# R11-v2 raised the caps by 50 B (25 B for clpc and dcp, which the 2,500 B target below holds) on purpose: the RESULT sentence of every profile now says that the video decoder's window has its DART's window as a hole (45 B, review M1); nothing cut.
CAPS = {P + 'quiet': 2300, P + 'quiet-ave': 2375, P + 'quiet-usb': 2400, P + 'quiet-aop': 2500, P + 'quiet-clpc': 2500, P + 'quiet-dcp': 2500, P + 'quiet-smc': 3000, P + 'all-quiet': 3075}
SAMPLES = {     # the sentences as the words of rung 3 and rung 4 at the tip say them, which a profile makes false
    'one thing in the guest': 'THIS RECORD CHANGES ONE THING IN THE GUEST\'S INPUTS: the monitor\'s PARAMS word let_panic (word 23)',
    'one word of the PARAMS chunk only': 'From rung 3\'s guest this one differs in one word of the PARAMS chunk only (let_panic, eight bytes at offset 0xB8)',
    'one PARAMS word and nothing else': 'rung 3 is the run before, this record changes one PARAMS word and nothing else',
    'differs in one thing': 'At run time the guest differs from rung 3\'s in one thing: its own exception handler runs',
    'the same size and chunks': 'the same size and the same chunks, and its own SHA-256 (docs/SP11-STICK-REAL.md section 2)',
    'only the PARAMS chunk': 'Only GUEST.DAT (eight bytes of its PARAMS chunk) and PIN.DAT (the label and the digests) differ from rung 3\'s',
}


def facts_of(name, **over):
    """The facts of rung 5 with the profile `name`: the standard variant of the tests, with `over` put over them."""
    facts = dict(kind=KIND, label=f'M1RUN-LETPANIC-{TABLE[name][0]}RUNG5', let_panic=True, guest_sha16=SHA16, guest_bytes=TABLE[name][4])
    facts.update(over)
    return facts


def variants(name):
    """The facts that the pins are held for: none at all, the standard ones, and the standard ones with let_panic False, with the key left out and with another model count."""
    standard = facts_of(name)
    return {'none': None, 'standard': standard, 'no let_panic': facts_of(name, let_panic=False), 'unknown let_panic': {k: v for k, v in standard.items() if k != 'let_panic'},
            'other models': facts_of(name, models=121)}


def text_of(name, facts=None):
    return '\n'.join(pp.profile_text(name, facts))


def pins(name, facts=None):
    """{pin: the phrase} of each sentence of the text of `name` with `facts`, in the words of the text, written again here from the design and not read from the module."""
    mark, sets, nodes, dt, size, sha = TABLE[name]
    sets, facts = sets.split(), facts or {}
    shows = (f'shows BYTES={facts["guest_bytes"]} SHA={facts["guest_sha16"]}' if facts.get('guest_sha16')
             else f'shows its own BYTES and SHA (rung 4\'s kind: BYTES={size} SHA={sha}, for it only)')
    label = facts.get('label') or f'M1RUN-<the kind\'s marker>{mark}<the facts label>'
    out = {
        'lead': f'THE DEVICE TREE OF THIS GUEST IS A PROFILE\'S, NOT THE BASE BOARD\'S: {name}, mark {mark}, composed with {facts.get("kind") or "the kind of this record"}.',
        'chunk': f'Of that kind\'s own guest only the DEVICE_TREE chunk ({dt:,} bytes; the base board\'s 350,576) and the HANDOFF word with the tree\'s length differ',
        'chunks compared': '(2 of 46 chunks, host comparison on rung 4\'s guest: INFERRED for any kind);',
        'nothing else': f'PARAMS, the {facts.get("models") or 120} models, the programs and the boot arguments are the kind\'s.',
        'count': f'WITHDRAWN: {395 - nodes} nodes (395 -> {nodes}, counted on the host).',
        'basis': 'Why, by set (INFERRED for this unit: the A16 chain\'s reasons; the device is absent or unmodelled on the SP11, withdrawn from the guest tree, never faked):',
        'label': f'TELLS IT APART (INFERRED; no profile bundle has been booted): PIN label {label};',
        'page': f'INDEX=2 on the PAYLOAD LOADED line {shows};',
        'index 3': 'INDEX=3\'s SHA is PIN.DAT\'s, which holds this record\'s own SHA-256: no record names it.',
        'not claimed': 'NOT CLAIMED: that anything runs with these nodes withdrawn (EXPERIMENTAL, hardware NOT RUN); that it changes what the kernel waits for or stops at (UNKNOWN); '
                       'a pass criterion.',
        'smc-gpio': 'smc-gpio stays published;', 'jpeg': 'the jpeg roots (jpeg0/1-h17a, jpeg0/1-h17p) have no A16 pin and stay in the tree.',
        'hazard': 'HAZARD (INFERRED): a driver that waited for a withdrawn device may meet its missing node and panic, or a later driver of the burst may stop first.',
        'let_panic': {True: 'let_panic is set: the panic text prints on the panel.', False: 'let_panic is not set: a panic ends the run with no text (boot 23, OBSERVED once).',
                      None: 'With let_panic the panic text prints on the panel; without it a panic ends the run with no text (boot 23, OBSERVED once).'}[facts.get('let_panic')],
        'result': 'RESULT (INFERRED from files/stopsite-r6.md of the session, read on the host: a file, not a run): on the base board the scaler stopped the run first (boot 26: OBSERVED once), AVD '
                  '(MON STOP S2_FAULT with an IPA in 0x380000000..0x381404000 but for its DART\'s 0x381020000..0x381040000)',
        'scaler': 'is next;',
        'faults': 'a stop in them here means the withdrawal did not take, one elsewhere a later device.',
        'watchdog': 'MON STOP WATCHDOG at the box end is a kernel waiting: its last log line says for what.',
    }
    for each in sets:
        out[f'set {each}'] = f'{each} ({NODES[each]}): {ROOTS[each]}{EXTRAS.get(each, "")} [{REASONS[each]}]'
    if ran := [RAN[each] for each in sets if each in RAN]:
        out['ran'] = f'Boot 21\'s film showed start lines of {", ".join(ran)} (OBSERVED once): withdrawing {"it" if len(ran) == 1 else "them"} changes a path that ran.'
    return out


BLOCKS = {      # the block of the generator -> the pins of its sentences
    '_changes': ('lead', 'chunk', 'chunks compared', 'nothing else'), '_withdrawn': ('count', 'basis', 'ran'), '_tell_apart': ('label', 'page', 'index 3'),
    '_not_claimed': ('not claimed', 'smc-gpio', 'jpeg'), '_hazard': ('hazard', 'let_panic'), '_result': ('result', 'scaler', 'faults', 'watchdog'),
}


def pin_problems(text, phrases):
    """The names of the pins whose phrase is not in `text` (line breaks gone)."""
    f = flat(text)
    return [name for name, phrase in phrases.items() if flat(phrase) not in f]


def mutants_rejected(test, text, phrases):
    """`text` holds each phrase of `phrases` once, and without one phrase it is rejected by the pin of that phrase alone."""
    f = flat(text)
    test.assertEqual(pin_problems(f, phrases), [])
    for name, phrase in phrases.items():
        with test.subTest(pin=name):
            test.assertEqual(f.count(flat(phrase)), 1, 'each sentence is in the text once')
            test.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), phrases), [name])


class Table(unittest.TestCase):
    def test_the_menu_is_closed_and_the_table_is_the_reports_twice_over(self):
        self.assertEqual({P + short: row for short, row in pp.PROFILES.items()}, {name: (m, tuple(s.split()), n, d, b, h) for name, (m, s, n, d, b, h) in TABLE.items()})
        self.assertEqual(list(pp.SETS), list(NODES))
        for name, (mark, sets, nodes, dt, size, sha) in TABLE.items():
            with self.subTest(name):
                self.assertEqual(395 - nodes, sum(NODES[s] for s in sets.split()), 'the nodes that go are those of the sets: two runs of the host agree')
                self.assertEqual(size % 64, 0)
                self.assertLessEqual(abs(size - (118908480 - (350576 - dt))), 32, 'the guest is the chunk\'s difference smaller than rung 4\'s, to the 64 bytes that the file is rounded to')
                self.assertRegex(mark, r'^[A-Z]-$')
                self.assertRegex(sha, r'^[0-9A-F]{16}$')
        self.assertEqual(len({row[0] for row in TABLE.values()}), 8, 'a mark each')
        self.assertEqual((pp.BASE, pp.PREFIX, pp.BASE_NODES, pp.BASE_DT_BYTES), (BASE, P, 395, 350576))

    def test_each_set_names_its_roots_its_nodes_its_declarations_and_its_reason(self):
        for name, (roots, nodes, why) in pp.SETS.items():
            with self.subTest(name):
                self.assertEqual((', '.join(roots), nodes, why, pp.EXTRA.get(name, '')), (ROOTS[name], NODES[name], REASONS[name], EXTRAS.get(name, '')))
        self.assertEqual(pp.RAN, RAN)
        self.assertEqual(sum(len(roots) for roots, *_ in pp.SETS.values()), 35)

    def test_the_needs_of_the_sets_hold_in_every_profile_and_the_smc_unit_is_whole(self):
        for name, (_mark, sets, *_rest) in TABLE.items():
            have = set(sets.split())
            with self.subTest(name):
                self.assertLessEqual({'accel', 'audio'}, have, 'every profile is the quiet board and what it adds')
                self.assertTrue('dcp' not in have or 'dcpext' in have, 'dcp needs dcpext (dispext0 names disp0)')
                self.assertTrue('smcbtm' not in have or {'aop', 'dcp', 'audio'} <= have, 'smcbtm needs aop, dcp and audio')
        smc = pp.SETS['smcbtm']
        self.assertTrue({'smc', 'nub-spmi0/btm'} <= set(smc[0]) and smc[1] == 13, 'smc and btm are one set: no profile splits them')
        self.assertIn('7 declarations', pp.EXTRA['smcbtm'])
        self.assertEqual([r for roots, *_ in pp.SETS.values() for r in roots if 'jpeg' in r or r.endswith('smc-gpio')], [], 'smc-gpio stays published and the jpeg roots are not offered')


class Words(unittest.TestCase):
    def test_each_sentence_of_each_profile_is_pinned_and_a_text_without_it_is_rejected(self):
        for name in TABLE:
            for what, facts in variants(name).items():
                with self.subTest(profile=name, facts=what):
                    mutants_rejected(self, text_of(name, facts), pins(name, facts))

    def test_a_generator_without_one_of_its_blocks_makes_a_text_that_the_pins_reject(self):
        name = P + 'quiet-aop'
        for facts in (None, facts_of(name)):
            wanted = pins(name, facts)
            for block, owned in BLOCKS.items():
                with self.subTest(block=block, facts=bool(facts)):
                    with mock.patch.object(pp, block, lambda *args: ''):
                        text = text_of(name, facts)
                    mine = [pin for pin in wanted if pin in owned or (block == '_withdrawn' and pin.startswith('set '))]
                    self.assertEqual(pin_problems(text, wanted), mine, 'the pins of that block and no other')
                    self.assertEqual(pp.record_problems(text, name, facts), ['MISSING'], 'a record that lacks a block of the paragraph is named')

    def test_a_paragraph_of_another_profile_is_not_the_paragraph_of_this_one(self):
        for a, b in itertools.permutations(TABLE, 2):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(text_of(a), text_of(b))
                self.assertEqual(pp.record_problems(text_of(a), b), ['MISSING'])

    def test_the_text_says_what_it_changes_and_what_it_leaves_alone(self):
        for name in TABLE:
            f = flat(text_of(name, facts_of(name)))
            with self.subTest(name):
                self.assertEqual(f.count('only the DEVICE_TREE chunk'), 1)
                self.assertEqual(f.count('and the HANDOFF word with the tree\'s length differ'), 1)
                self.assertIn('PARAMS, the 120 models, the programs and the boot arguments are the kind\'s', f)
                for named in ('never faked', 'smc-gpio stays published', 'stay in the tree', 'a pass criterion', 'EXPERIMENTAL, hardware NOT RUN', 'UNKNOWN'):
                    self.assertIn(named, f)

    def test_the_base_board_and_no_profile_have_no_paragraph(self):
        for profile in (None, BASE):
            self.assertEqual((pp.profile_text(profile), pp.profile_text(profile, facts_of(P + 'quiet')), pp.lines(profile), pp.record_problems('a record', profile)), ((), (), (), []))
        self.assertEqual(pp.record_problems('x ' + pp.LEAD + ' y', None), ['UNEXPECTED'], 'the paragraph in a record without a profile is named')
        self.assertEqual(pp.record_problems('a record that says ' + SAMPLES['the same size and chunks'], None), [], 'a record without a profile may say what it says')

    def test_the_label_of_another_profile_or_a_bad_fact_is_refused_by_name(self):
        bad = {'a label of another profile': ({'label': 'M1RUN-LETPANIC-A-RUNG5'}, 'PROFILE_TEXT_LABEL'), 'a label without a mark': ({'label': 'M1RUN-LETPANIC-RUNG5'}, 'PROFILE_TEXT_LABEL'),
               'a label with the mark in the facts': ({'label': 'M1RUN-LETPANIC-QRUNG5'}, 'PROFILE_TEXT_LABEL'), 'a label that is no label': ({'label': 'Q-'}, 'PROFILE_TEXT_LABEL'),
               'a label longer than PIN.DAT holds': ({'label': 'M1RUN-LETPANIC-Q-RUNG123456'}, 'PROFILE_TEXT_LABEL'),
               'a key that is not a fact': ({'rung': 5}, 'PROFILE_TEXT_FACTS'), 'a SHA in small letters': ({'guest_sha16': SHA16.lower()}, 'PROFILE_TEXT_FACTS'),
               'a SHA too short': ({'guest_sha16': SHA16[:8]}, 'PROFILE_TEXT_FACTS'), 'a size that is a string': ({'guest_bytes': '118885568'}, 'PROFILE_TEXT_FACTS'),
               'a size of True': ({'guest_bytes': True}, 'PROFILE_TEXT_FACTS'), 'a let_panic that is 1': ({'let_panic': 1}, 'PROFILE_TEXT_FACTS'),
               'a kind that is not ASCII': ({'kind': 'rung 5 \u00e9'}, 'PROFILE_TEXT_FACTS'), 'a kind of two lines': ({'kind': 'rung 5\nrung 6'}, 'PROFILE_TEXT_FACTS'),
               'a kind that is empty': ({'kind': ''}, 'PROFILE_TEXT_FACTS'), 'a model count that is a string': ({'models': '120'}, 'PROFILE_TEXT_FACTS'),
               'a model count of 0': ({'models': 0}, 'PROFILE_TEXT_FACTS'), 'a model count of True': ({'models': True}, 'PROFILE_TEXT_FACTS'),
               'a kind of 121 characters': ({'kind': 'x' * 121}, 'PROFILE_TEXT_FACTS')}
        for what, (facts, name) in bad.items():
            with self.subTest(what), self.assertRaises(pp.ProfileTextError) as caught:
                pp.profile_text(P + 'quiet', facts)
            self.assertEqual(caught.exception.name, name)
        for marker in ('', 'CONSOLE-', 'CPUS1-', 'PANEL-', 'LETPANIC-', 'SIXCPU-'):
            with self.subTest(marker):
                self.assertEqual(pp.profile_text(P + 'quiet', {'label': f'M1RUN-{marker}Q-RUNG5'})[2].count('M1RUN-' + marker + 'Q-RUNG5'), 1, 'every marker of the builder\'s labels, and one that '
                                 'a later kind may add')
        for label in ('M1RUN-LETPANIC-Q-RUNG10', 'M1RUN-CONSOLE-Q-r4-t', 'M1RUN-Q-r5_a.b'):
            with self.subTest(label):
                self.assertIn(label, pp.profile_text(P + 'quiet', {'label': label})[2])

    def test_a_profile_outside_the_menu_is_refused_by_name_and_nothing_is_made(self):
        for profile in (P + 'nope', BASE + '-no-clpc', 'quiet', P, P + 'Quiet', ' ' + P + 'quiet', 7, '', b'x'):
            with self.subTest(profile=profile), self.assertRaises(pp.ProfileTextError) as caught:
                pp.profile_text(profile)
            self.assertEqual(caught.exception.name, 'PROFILE_TEXT_UNKNOWN')
            with self.assertRaises(pp.ProfileTextError):
                pp.lines(profile)

    def test_the_facts_are_not_changed_and_the_text_is_the_same_twice(self):
        name, facts = P + 'quiet-ave', facts_of(P + 'quiet-ave')
        before = dict(facts)
        self.assertEqual(pp.profile_text(name, facts), pp.profile_text(name, facts))
        self.assertEqual(facts, before)
        self.assertEqual(pp.NAMES, tuple(TABLE), 'the menu\'s eight names in its order')
        for not_a_mapping in (5, 'kind', ['kind']):
            with self.subTest(not_a_mapping), self.assertRaises(pp.ProfileTextError) as caught:
                pp.profile_text(name, not_a_mapping)
            self.assertEqual(caught.exception.name, 'PROFILE_TEXT_FACTS')

    def test_the_lines_are_the_records_own_filler_and_fit_its_width(self):
        for name in TABLE:
            facts = facts_of(name)
            lines = pp.lines(name, facts)
            with self.subTest(name):
                self.assertEqual(lines, tuple(line for text in pp.profile_text(name, facts) for line in m1t.wrap(text, 3, 6)))
                self.assertTrue(all(line.isascii() and len(line) <= m1t.WIDTH for line in lines))
                self.assertEqual(flat(' '.join(lines)), flat(text_of(name, facts)))
                self.assertEqual(sum(len(line) + 1 for line in pp.lines(name, facts, 0, 0)) - 1, len(flat(text_of(name, facts))), 'wrapping loses no byte')


class Guard(unittest.TestCase):
    def test_a_record_with_a_profile_that_says_the_guest_differs_in_one_thing_is_named(self):
        name, paragraphs = P + 'quiet', text_of(P + 'quiet')
        self.assertEqual(pp.record_problems(paragraphs, name), [])
        for what, sentence in SAMPLES.items():
            with self.subTest(what):
                self.assertEqual(pp.record_problems(f'a record {sentence} {paragraphs}', name), [f'FALSE: {what}'])
                self.assertEqual(pp.record_problems(f'a record {sentence}', name), [f'FALSE: {what}', 'MISSING'], 'the sentence is false and the paragraph is not there')
                self.assertEqual(pp.record_problems(f'a record {sentence}', None), [], 'a record without a profile may say it')
        everything = f'a record {" ".join(SAMPLES.values())} {paragraphs}'
        self.assertEqual(pp.record_problems(everything, name), [f'FALSE: {what}' for what in SAMPLES])
        self.assertEqual(pp.record_problems(everything.replace('\n', '\n   '), name), pp.record_problems(everything, name), 'the line breaks of the filler do not matter')

    def test_the_guarded_sentences_are_the_ones_the_tips_words_say_and_the_paragraphs_say_none(self):
        self.assertEqual([what for what, _ in pp.FALSE_FOR_A_PROFILE], list(SAMPLES))
        for what, pattern in pp.FALSE_FOR_A_PROFILE:
            with self.subTest(what):
                self.assertRegex(SAMPLES[what], pattern)
        strings = []
        for rel in ('sp11_stick_bundle_panel_rung3.py', 'sp11_stick_bundle_panel_panic.py', 'sp11_stick_bundle_panel.py'):
            path = TOOLS / rel
            if path.exists():
                strings += [node.value for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        if strings:
            words = flat(' '.join(strings))
            for what, pattern in pp.FALSE_FOR_A_PROFILE:
                with self.subTest(what):
                    self.assertRegex(words, pattern, 'a guard that matches nothing of the words of the kinds guards nothing: update it')
        for name in TABLE:
            for what, facts in variants(name).items():
                with self.subTest(name, facts=what):
                    self.assertEqual([w for w, pattern in pp.FALSE_FOR_A_PROFILE if re.search(pattern, flat(text_of(name, facts)))], [])


class Hygiene(unittest.TestCase):
    def texts(self):
        out = {f'{name} {what}': text_of(name, facts) for name in TABLE for what, facts in variants(name).items()}
        return out

    def test_every_paragraph_is_ascii_labelled_and_says_what_it_is_once_where_it_observes(self):
        for name in TABLE:
            for what, facts in variants(name).items():
                for paragraph in pp.profile_text(name, facts):
                    with self.subTest(name, facts=what, paragraph=paragraph[:30]):
                        self.assertTrue(paragraph.isascii() and paragraph == ' '.join(paragraph.split()), 'ASCII, one line, single spaces')
                        self.assertRegex(paragraph, r'EXPERIMENTAL|INFERRED|UNKNOWN|OBSERVED', 'each paragraph is labelled')
        for what, text in self.texts().items():
            with self.subTest(what):
                for found in re.finditer('OBSERVED', text):
                    self.assertTrue(text[found.end():].startswith((' once', ', one unit, one boot')), text[found.start():found.start() + 40])
                self.assertNotRegex(text, BANNED + '|' + BANNED.lower())

    def test_nothing_private_is_in_the_words_the_module_or_this_suite(self):
        private = ('/Us' + 'ers/', '/Vol' + 'umes', '/t' + 'mp', '/v' + 'ar/', '/ho' + 'me/', 'sae' + 'ed', 'ht' + 'tp://', 'ht' + 'tps://', '~' + '/')
        for what, text in self.texts().items():
            with self.subTest(what):
                self.assertEqual([p for p in private if p in text], [])
        for rel in ('tools/sp11_stick_bundle_panel_profile.py', 'tools/test-sp11-stick-monitor-profile-text.py'):
            source = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertTrue(source.isascii())
                self.assertNotIn(BANNED, source)
                self.assertEqual([p for p in private if p in source], [])
                self.assertEqual(source.splitlines()[1 if source.startswith('#!') else 0].count('SPDX-License-Identifier: MIT'), 1)
                self.assertLess(len(source.encode()), 51_200)
                self.assertLess(len(source.splitlines()), 1500)

    def test_the_size_of_each_call_is_the_reports_and_the_record_has_to_find_it_room(self):
        for name, cap in CAPS.items():
            facts = facts_of(name)
            raw = len(text_of(name, facts).encode())
            filled = sum(len(line) + 1 for line in pp.lines(name, facts))
            with self.subTest(name):
                self.assertLessEqual(raw, cap, f'{raw} B of paragraphs')
                self.assertLessEqual(filled, cap + 160, f'{filled} B filled')
                self.assertLess(len(text_of(name).encode()), cap + 130, 'without the facts of the kind it is a little longer')
        self.assertEqual([name for name, cap in CAPS.items() if cap > 2500], [P + 'quiet-smc', P + 'all-quiet'], 'the target of 2,500 B holds for six profiles; the two that name six and seven sets do not')

    def test_the_paragraphs_name_no_file_that_is_not_in_the_repo_or_the_session(self):
        cited = set(re.findall(r'(?:probes|tools|docs|files)/[A-Za-z0-9_./-]+', ' '.join(self.texts().values())))
        self.assertEqual(cited, {'files/stopsite-r6.md'}, 'the one analysis of the session; every other source is named in the module\'s own words')


class Menu(unittest.TestCase):
    """The module against the menu of the builder, when the menu is in the tree."""

    @classmethod
    def setUpClass(cls):
        if not (ROOT / 'probes/guestprofile_m1.py').exists():
            raise unittest.SkipTest('SKIP: the profile menu (probes/guestprofile_m1.py) is not in this tree')
        sys.path.insert(0, str(ROOT / 'probes'))
        import guestprofile_m1 as menu
        cls.menu = menu

    def test_the_sets_their_roots_the_profiles_and_the_declarations_are_the_menus(self):
        menu = self.menu
        self.assertEqual(menu.PREFIX, P)
        self.assertEqual(list(menu.ROOT_SETS), list(pp.SETS))
        for name, (roots, _nodes, _why) in pp.SETS.items():
            with self.subTest(name):
                self.assertEqual(tuple(r.node.removeprefix('/device-tree/arm-io/') for r in menu.ROOT_SETS[name].roots), roots)
        self.assertEqual((len(menu.ROOT_SETS['smcbtm'].functions), len(menu.ROOT_SETS['clpc'].drops)), (7, 1))
        self.assertEqual({name: sets for name, sets in menu.MENU.items()}, {P + short: row[1] for short, row in pp.PROFILES.items()})
        self.assertEqual(menu.BASE.name, BASE)
        self.assertEqual({n.removeprefix('/device-tree/arm-io/') for n in menu.NOT_PINNED}, {'jpeg0-h17a', 'jpeg0-h17p', 'jpeg1-h17a', 'jpeg1-h17p'})

    def test_the_marks_and_the_counts_are_those_of_the_menus_document(self):
        doc = ROOT / 'docs/SP11-M1-GUEST-PROFILES.md'
        if not doc.exists():
            self.skipTest('SKIP: docs/SP11-M1-GUEST-PROFILES.md is not in this tree')
        rows = [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in doc.read_text().splitlines() if line.startswith('|')]
        sets = {row[0]: int(row[2]) for row in rows if len(row) == 5 and row[0] in pp.SETS}
        profiles = {row[0]: (row[1], int(row[3])) for row in rows if len(row) == 4 and row[0] in {s for s in pp.PROFILES}}
        self.assertEqual(sets, {name: nodes for name, (_roots, nodes, _why) in pp.SETS.items()})
        self.assertEqual(profiles, {short: (mark, nodes) for short, (mark, _sets, nodes, *_rest) in pp.PROFILES.items()})


if __name__ == '__main__':
    unittest.main()
