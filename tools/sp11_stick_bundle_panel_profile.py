# SPDX-License-Identifier: MIT
"""The words that a record of any kind of monitor entry run (--m1-run) says of a guest whose device tree is a PROFILE's (`--guest-profile NAME` of
tools/sp11_stick_bundle_build.py: the base board j700ap-26A428 with whole sets of device-tree roots withdrawn). Without them such a record carries the words of the kind it is
composed with, and those say that the guest differs from the kind's base guest in one PARAMS word or one boot-argument word, with the same size and the same chunks: false for a
profile guest, whose DEVICE_TREE chunk and the HANDOFF word that holds the tree's length differ as well. Nothing here depends on the builder or on the words of one kind: a
record module calls `profile_text(profile, kind_facts)` for the paragraphs (`lines()` fills them with the record's own filler, tools/sp11_stick_bundle_record_m1.py wrap) and
`record_problems(text, profile, kind_facts)` on the finished record, which names a sentence that is false for a profile guest and a paragraph that is missing.

 - The nine sets, their roots, their node counts and the node count of each of the eight profiles are those of the menu of the builder (probes/guestprofile_m1.py and
   docs/SP11-M1-GUEST-PROFILES.md of the profile-menu change), counted on the host from the published device tree. The menu is not imported, so that this module stands alone;
   tools/test-sp11-stick-monitor-profile-text.py compares the two when the menu is in the tree and says SKIP when it is not.
 - The reason for each set is the A16 chain's own (comments of probes/guestprofile.py and the waiters table of tools/test-quiet-profile.py), which is INFERRED for the SP11.
 - The analysis of the stop sites is files/stopsite-r6.md of the session, read on the host from the kernelcache file and the public XNU source, without a run: INFERRED. Boot 26's
   film (files/result-boot26.md) is the one run that has reached past them (OBSERVED once, one unit, one boot) and ended at the scaler on the base board's tree.

Evidence: EXPERIMENTAL, hardware NOT RUN: no profile of the menu has been booted. What was observed is the films of boots 21, 23, 24 and 26 (one unit, one boot each) that the
sentences name."""
import re
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_windows as win  # noqa: E402

BASE = 'j700ap-26A428'
PREFIX = BASE + '-m1-'
BASE_NODES = 395                    # nodes of the published device tree of the base board
BASE_DT_BYTES = 350_576             # bytes of its DEVICE_TREE chunk (chunk 3 of 46)
LEAD = 'THE DEVICE TREE OF THIS GUEST IS A PROFILE\'S, NOT THE BASE BOARD\'S'
KIND_KEYS = ('kind', 'label', 'let_panic', 'models', 'guest_sha16', 'guest_bytes', 'changes')
NEVER = 'VERI' + 'FIED'             # the word that no M1 record uses (and no module of its words: it is built here so that the hygiene tests find it nowhere)
MODELS = 120                        # the device models of every kind so far: the profile changes the tree, never the models
PIN_LABEL_MAX = 23                  # characters of the label in PIN.DAT (tools/sp11_stick_bundle_m1.py PIN_LABEL_MAX)
JPEG = 'jpeg0/1-h17a, jpeg0/1-h17p'

# set -> (its roots, under /device-tree/arm-io/ unless a path is given; the nodes under the roots; why the A16 chain withdrew it)
SETS = {
    'accel': (('avd', 'scaler0', 'apr'), 3, 'avd\'s version read reached an unbacked aperture on the A16; scaler0 and apr are absent there'),
    'audio': (('iop-audio-controller', 'iop-voicetrigger-controller', 'admac-base-ns', 'admac-leap-ns', 'admac-leap-s', 'admac-leap-s-rx-0-proxy', 'audio-shared-dart-proxy',
               'audio-hp-mic-proxy', 'i2c0/audio-codec-output'), 40, 'no audio hardware; the first playback ended an A16 run'),
    'aop': (('aop', 'aop2', 'aop-spmi0'), 15, 'starts waited at RTBuddy routes 0x65 and 0x69 and in Stockholm'),
    'dcpext': (('dispext0', 'dcpext', 'dcpext-expert'), 4, 'the external display pipe'),
    'dcp': (('dcp', 'dcp0-expert', 'dp-audio0', 'dp-audio1', 'disp0', '/device-tree/backlight'), 7, 'waited for the DCP expert (route 0xcf), sio-dma and IONVRAM'),
    'ave': (('ave', 'ave1'), 2, 'waited on a PMGR state change and a mutex'),
    'smcbtm': (('smc', 'smctempsensor0', '/device-tree/buttons', ':j700_has_mesa_0', 'nub-spmi0/btm'), 13,
               'smc never publishes smc-pmu; withdrawing it without btm lets pmu-main return and btm panic the A16 (unable to config PMU)'),
    'clpc': (('pmgr/clpc',), 1, 'the A16 board has no cluster governor'),
    'usb': (('usb-drd', 'atc0-dpphy', 'i2c2/usb-hub-i2c'), 7, 'clients waited for the missing PHY'),
}
# What goes with a set besides its roots: declarations that the loader withdraws from retained nodes (not counted as nodes).
EXTRA = {'smcbtm': ' and 7 declarations withdrawn with them (pmu-main, multi-touch, ppm)', 'clpc': ' and the base board\'s function-ane_perf_ctr on it'}
# The sets whose start lines boot 21's film showed on the base tree (docs/SP11-STICK-LOG.md section 19.4, row 8): withdrawing them changes a path that ran. The DCPEXT line of that
# row is not named: dcpext goes only as the dependency of dcp, and its sentence would take the dcp profile past 2,500 bytes.
RAN = {'aop': 'aop/aop2', 'clpc': 'clpc'}
# short name -> (mark, sets, nodes of its tree, bytes of its DEVICE_TREE chunk, GUEST.DAT bytes and the first 16 digits of its SHA-256 as the page prints them, the last two built
# on rung 4's kind: let_panic, facts label RUNG4; a host build, not a boot)
PROFILES = {
    'quiet': ('Q-', ('accel', 'audio'), 352, 327_664, 118_885_568, '06056EAE638DF055'),
    'quiet-aop': ('A-', ('accel', 'audio', 'aop'), 337, 321_344, 118_879_232, '813365207B3D7E6B'),
    'quiet-dcp': ('D-', ('accel', 'audio', 'dcpext', 'dcp'), 341, 315_952, 118_873_856, '3F928286CA0966F4'),
    'quiet-ave': ('V-', ('accel', 'audio', 'ave'), 350, 326_000, 118_883_904, '85FFB7503CD79195'),
    'quiet-clpc': ('C-', ('accel', 'audio', 'clpc'), 351, 321_424, 118_879_360, '94232D67494F7D29'),
    'quiet-usb': ('U-', ('accel', 'audio', 'usb'), 345, 324_224, 118_882_112, '9CDBA4F15D1EDFDD'),
    'quiet-smc': ('S-', ('accel', 'audio', 'aop', 'dcpext', 'dcp', 'smcbtm'), 313, 302_608, 118_860_544, 'F577E8D1D0F2CBAD'),
    'all-quiet': ('X-', ('accel', 'audio', 'aop', 'dcpext', 'dcp', 'ave', 'smcbtm'), 311, 300_944, 118_858_880, 'A3799F9BD1E68CE8'),
}
NAMES = tuple(PREFIX + short for short in PROFILES)         # the eight names of the menu, in its order; the base board has no paragraph

# Sentences that the kinds' own words say of their guest and that a profile makes false: a record built with a profile must not carry them (`record_problems`).
FALSE_FOR_A_PROFILE = (
    ('one thing in the guest', r'CHANGES ONE THING IN THE GUEST'),
    ('one word of the PARAMS chunk only', r'differs? in one word of the PARAMS chunk only'),
    ('one PARAMS word and nothing else', r'changes one PARAMS word and nothing else'),
    ('differs in one thing', r'differs? from rung \d+\'s in one thing'),
    ('the same size and chunks', r'the same size and the same chunks?'),
    ('only the PARAMS chunk', r'Only GUEST\.DAT \(eight bytes of its PARAMS chunk\)'),
)


class ProfileTextError(ValueError):
    """A refusal by name: PROFILE_TEXT_UNKNOWN (a profile that is not in the menu), PROFILE_TEXT_FACTS (a fact of the kind that is not one of KIND_KEYS or is malformed) or
    PROFILE_TEXT_LABEL (a label without the profile's mark: the guest of another profile)."""

    def __init__(self, name, message):
        super().__init__(f'{name}: {message}')
        self.name = name


def short_name(profile):
    """The menu's short name of `profile`, or None for the base board (no paragraph): ProfileTextError PROFILE_TEXT_UNKNOWN for any other name."""
    if profile in (None, BASE):
        return None
    if isinstance(profile, str) and profile.startswith(PREFIX) and profile[len(PREFIX):] in PROFILES:
        return profile[len(PREFIX):]
    raise ProfileTextError('PROFILE_TEXT_UNKNOWN', f'{profile!r} is not the base board or one of the eight profiles of the menu')


def _facts(kind_facts, mark):
    if kind_facts is not None and not isinstance(kind_facts, Mapping):
        raise ProfileTextError('PROFILE_TEXT_FACTS', 'the facts of the kind are a mapping or None')
    facts = dict(kind_facts or {})
    kind = facts.get('kind')
    if kind is not None and not (isinstance(kind, str) and 0 < len(kind) <= 120 and kind.isascii() and kind.isprintable()):
        raise ProfileTextError('PROFILE_TEXT_FACTS', 'kind is one line of printable ASCII of at most 120 characters')
    if unknown := sorted(set(facts) - set(KIND_KEYS)):
        raise ProfileTextError('PROFILE_TEXT_FACTS', f'{unknown[0]!r} is not a fact of the kind (one of {", ".join(KIND_KEYS)})')
    label = facts.get('label')
    if label is not None and not (isinstance(label, str) and len(label) <= PIN_LABEL_MAX and re.fullmatch(r'M1RUN-(?:[A-Z0-9]+-)?' + re.escape(mark) + r'[A-Za-z0-9_.-]+', label)):
        raise ProfileTextError('PROFILE_TEXT_LABEL', f'the label {label!r} does not carry the mark {mark} of this profile between the kind\'s marker and the facts label, or is '
                                                     f'longer than the {PIN_LABEL_MAX} characters of PIN.DAT')
    sha = facts.get('guest_sha16')
    if sha is not None and not (isinstance(sha, str) and re.fullmatch(r'[0-9A-F]{16}', sha)):
        raise ProfileTextError('PROFILE_TEXT_FACTS', 'guest_sha16 is the first 16 digits of GUEST.DAT\'s SHA-256, in capitals, as the page prints them')
    size = facts.get('guest_bytes')
    if size is not None and not (isinstance(size, int) and not isinstance(size, bool) and size > 0):
        raise ProfileTextError('PROFILE_TEXT_FACTS', 'guest_bytes is the size of GUEST.DAT')
    models = facts.get('models')
    if models is not None and not (isinstance(models, int) and not isinstance(models, bool) and models > 0):
        raise ProfileTextError('PROFILE_TEXT_FACTS', 'models is the number of device models of the kind')
    if facts.get('let_panic') is not None and not isinstance(facts['let_panic'], bool):
        raise ProfileTextError('PROFILE_TEXT_FACTS', 'let_panic is True, False or absent')
    changes = facts.get('changes')
    if changes is not None and not (isinstance(changes, str) and 0 < len(changes) <= 700 and changes.isascii() and changes.isprintable() and NEVER not in changes):
        raise ProfileTextError('PROFILE_TEXT_FACTS', 'changes is one line of printable ASCII of at most 700 characters, never the word that M1 records do not use')
    return facts


def _changes(short, facts):
    mark, _sets, _nodes, dt, _bytes, _sha = PROFILES[short]
    kind = facts.get('kind') or 'the kind of this record'
    lead = f'{LEAD}: {PREFIX}{short}, mark {mark}, composed with {kind}. '
    if facts.get('changes'):         # the record's own sentence, made from a comparison that it has done itself (rung 6: the chunk table of rung 5's bundle)
        return lead + facts['changes']
    return (lead + f'Of that kind\'s own guest only the DEVICE_TREE chunk ({dt:,} bytes; the base board\'s {BASE_DT_BYTES:,}) '
            'and the HANDOFF word with the tree\'s length differ (2 of 46 chunks, host comparison on rung 4\'s guest: INFERRED for any kind); PARAMS, the '
            f'{facts.get("models") or MODELS} models, the programs and the boot arguments are the kind\'s.')


def _withdrawn(short, _facts):
    _mark, sets, nodes, *_rest = PROFILES[short]
    by_set = '; '.join(f'{name} ({SETS[name][1]}): {", ".join(SETS[name][0])}{EXTRA.get(name, "")} [{SETS[name][2]}]' for name in sets)
    ran = [RAN[name] for name in sets if name in RAN]
    seen = f' Boot 21\'s film showed start lines of {", ".join(ran)} (OBSERVED once): withdrawing {"it" if len(ran) == 1 else "them"} changes a path that ran.' if ran else ''
    return (f'WITHDRAWN: {BASE_NODES - nodes} nodes ({BASE_NODES} -> {nodes}, counted on the host). Why, by set (INFERRED for this unit: the A16 chain\'s reasons; the device is absent '
            f'or unmodelled on the SP11, withdrawn from the guest tree, never faked): {by_set}.{seen}')


def _tell_apart(short, facts):
    mark, _sets, _nodes, _dt, rung4_bytes, rung4_sha = PROFILES[short]
    pin = facts.get('label') or f'M1RUN-<the kind\'s marker>{mark}<the facts label>'
    if facts.get('guest_sha16'):
        shows = f'shows BYTES={facts["guest_bytes"]} SHA={facts["guest_sha16"]}' if facts.get('guest_bytes') else f'shows SHA={facts["guest_sha16"]}'
    else:
        shows = f'shows its own BYTES and SHA (rung 4\'s kind: BYTES={rung4_bytes} SHA={rung4_sha}, for it only)'
    return (f'TELLS IT APART (INFERRED; no profile bundle has been booted): PIN label {pin}; INDEX=2 on the PAYLOAD LOADED line {shows}; INDEX=3\'s SHA is PIN.DAT\'s, which holds '
            'this record\'s own SHA-256: no record names it.')


def _not_claimed(_short, _facts):
    return ('NOT CLAIMED: that anything runs with these nodes withdrawn (EXPERIMENTAL, hardware NOT RUN); that it changes what the kernel waits for or stops at (UNKNOWN); a pass '
            f'criterion. smc-gpio stays published; the jpeg roots ({JPEG}) have no A16 pin and stay in the tree.')


def _hazard(_short, facts):
    tail = {True: 'let_panic is set: the panic text prints on the panel.',
            False: 'let_panic is not set: a panic ends the run with no text (boot 23, OBSERVED once).',
            None: 'With let_panic the panic text prints on the panel; without it a panic ends the run with no text (boot 23, OBSERVED once).'}[facts.get('let_panic')]
    return f'HAZARD (INFERRED): a driver that waited for a withdrawn device may meet its missing node and panic, or a later driver of the burst may stop first. {tail}'


def _result(_short, _facts):
    return ('RESULT (INFERRED from files/stopsite-r6.md of the session, read on the host: a file, not a run): on the base board the scaler stopped the run first (boot 26: OBSERVED once), AVD '
            '(MON STOP S2_FAULT with an IPA in ' + win.for_result() + ') is next; a stop in them here means the withdrawal did not take, one elsewhere a later device. '
            'MON STOP WATCHDOG at the box end is a kernel waiting: its last log line says for what.')


def profile_text(profile, kind_facts=None):
    """The paragraphs (strings, not filled) that a record of any kind says of a guest built with `profile`, a name of the menu: () for the base board or None.
    `kind_facts`, all optional: 'kind' (what the guest is composed with, in a few words: "rung 5, ..."), 'label' (the PIN label of the bundle, with the profile's mark),
    'let_panic' (True or False: whether the kind sets the monitor's let_panic word; absent says both), 'models' (the number of device models of the kind, 120 in every kind so far),
    'guest_sha16' and 'guest_bytes' (what INDEX=2 prints for this guest: the 16 capitals of its SHA-256 and its size), 'changes' (the record's own sentence on what differs, in place of this
    module's, for a record that compares its guest with another itself). ProfileTextError for a profile that is not of the menu, a
    key that is not one of KIND_KEYS or a label of another profile."""
    short = short_name(profile)
    if short is None:
        return ()
    facts = _facts(kind_facts, PROFILES[short][0])
    return tuple(text for text in (_changes(short, facts), _withdrawn(short, facts), _tell_apart(short, facts), _not_claimed(short, facts), _hazard(short, facts),
                                   _result(short, facts)) if text)


def lines(profile, kind_facts=None, first=3, rest=6):
    """The paragraphs of `profile_text` filled into the lines of the record (the record's own filler, imported here: its bytes are what the operator approves)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import sp11_stick_bundle_record_m1 as m1t
    return tuple(line for text in profile_text(profile, kind_facts) for line in m1t.wrap(text, first, rest))


def record_problems(text, profile, kind_facts=None):
    """What is wrong with `text`, the finished record of a guest built with `profile` (None or the base board: a record without the paragraphs): the names, in order, of each
    sentence of FALSE_FOR_A_PROFILE that it carries ('FALSE: ...'), 'MISSING' if a paragraph of `profile_text` is not in it, and 'UNEXPECTED' for the lead of the paragraphs in a
    record without a profile. An empty list is a record that does not say what a profile makes false and holds the words of its profile."""
    flat = ' '.join(text.split())
    paragraphs = profile_text(profile, kind_facts)
    problems = [f'FALSE: {name}' for name, pattern in FALSE_FOR_A_PROFILE if paragraphs and re.search(pattern, flat)]
    problems += ['MISSING' for paragraph in paragraphs if ' '.join(paragraph.split()) not in flat][:1]
    return problems + (['UNEXPECTED'] if not paragraphs and LEAD in flat else [])
