# SPDX-License-Identifier: MIT
"""--guest-profile: the closed menu of guest device trees of the SP11 M1 bundle (docs/SP11-M1-GUEST-PROFILES.md; EXPERIMENTAL, host only, hardware NOT RUN).

The base board j700ap-26A428 is the board of every bundle so far and stays the default: a build without the option makes the call it always made and the same bytes. The
other eight are virtual board descriptions of probes/guestprofile_m1.py (the base board and a fixed union of whole root sets): they change the device tree and the one
boot_args word that holds its length, and nothing else. A ninth, quiet-rtc, is the quiet board with the PMU's real-time clock bound (RTC_MENU): the same device tree, and three
words of one device model of the PARAMS chunk (the model is seeded at RTC_EPOCH_US, the constant of the menu). What this module adds to a build is a closed list (a name outside it is
GUEST_PROFILE_REFUSED), a mark per profile for the pinned label (a record names its profile, and the PIN.DAT of one profile is not that of another), a check that a registered
profile is whole root sets (GUEST_PROFILE_SPLIT) and a read-back of the device tree and of the device models that the loader built (GUEST_PROFILE_READBACK)."""
import contextlib
import hashlib
import io
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'probes'))
import sp11_stick_bundle_chunks as ch  # noqa: E402
import devicemodel  # noqa: E402
import guestprofile as gp  # noqa: E402
import guestprofile_m1 as menu  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

REFUSALS = ('GUEST_PROFILE_REFUSED', 'GUEST_PROFILE_SPLIT', 'GUEST_PROFILE_READBACK')
ch.KNOWN_REFUSALS.update(REFUSALS)
menu.register()         # the loader's configure_guest looks a profile up in guestprofile.PROFILES
DEFAULT = ch.GUEST_PROFILE_DEFAULT
assert DEFAULT == menu.BASE.name        # the literal of sp11_stick_bundle_chunks.py is the loader's own default, which this file does not import
NAMES = (DEFAULT, *menu.MENU, *menu.RTC_MENU, *menu.ROOT_MENU)    # the whole list: nothing that probes/guestprofile.py registers for the A16 route is admitted
# What a pinned label carries for each profile, after the marker of the guest's other choices (m1.pinned_label): two characters, so that M1RUN-LETPANIC- (15) and the mark
# leave a facts label of six (RUNG10). Every non-default profile is the quiet board and what it adds: Q quiet, A +aop, D +dcp (with dcpext), V +ave, C +clpc, U +usb,
# S +smc (the smc/btm unit with the three sets it needs), X all of them but clpc and usb, R quiet and the PMU's real-time clock (RTC_MENU).
MARKS = dict(zip(NAMES, ('', 'Q-', 'A-', 'D-', 'V-', 'C-', 'U-', 'S-', 'X-', 'R-', 'M-')))
assert len(set(MARKS.values())) == len(MARKS) == len(NAMES)
MARK_CHARS = 2
QUIET = NAMES[1]                        # j700ap-26A428-m1-quiet: the profile of rung 7's record (RUNG7_PROFILES)
RTC = NAMES[9]                          # j700ap-26A428-m1-quiet-rtc: the profile of rung 8's record, the quiet board with the PMU's clock bound (RUNG8_PROFILES)
ROOT = NAMES[10]                        # j700ap-26A428-m1-quiet-rtc-acm: the profile of rung 9's record, quiet-rtc with the credential manager's software mode (ROOT_MENU), which only a build with a RAM-disk root takes
RUNG7_PROFILES = (QUIET,)               # the profiles that the form of rung 7 admits: a profile record carries a prediction and its falsifiers for the roots that the profile withdraws
                                        # (docs/SP11-M1-GUEST-PROFILES.md section 5), and that text exists for this one; the others stay in the menu until theirs does
RUNG8_PROFILES = (RTC,)                 # the profiles that the form of rung 8 admits: the same text with the clock's prediction and falsifiers (section 7 of that document)
RUNG9_PROFILES = (ROOT,)                # the profiles that the form of rung 9 admits: the root mount (section 8 of that document)
FORM_PROFILES = (*RUNG7_PROFILES, *RUNG8_PROFILES, *RUNG9_PROFILES)
RUNG_OF = {QUIET: 7, RTC: 8, ROOT: 9}   # the rung whose record a profile belongs to
assert QUIET == ch.RUNG7_PROFILE and RTC == ch.RUNG8_PROFILE and ROOT == ch.RUNG9_PROFILE and ch.FORM_PROFILE == {ch.RUNG7_FORM: QUIET, ch.RUNG8_FORM: RTC, ch.RUNG9_FORM: ROOT}      # the forms' own constants are the menu's names


def mark_of(name):
    """The pinned label's mark of profile `name` ('' for the default and for None)."""
    if name is None:
        return ''
    if name not in MARKS:
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {name!r} is not in the menu: {", ".join(NAMES)}')
    return MARKS[name]


def check_form(profile, boot_arg, let_panic, cpu_start, panel, is_m1, box_s=None):
    """The form of a guest profile, checked before any guest is built: a profile other than the default belongs to the record of rung 7 (quiet), of rung 8 (quiet-rtc) or of rung 9 (quiet-rtc-acm) and to no
    other (the monitor entry run, the acknowledging monitor, --let-panic, the panel as the console and the word and the box of its own form: ch.RUNG7_FORM, ch.RUNG8_FORM and ch.RUNG9_FORM, each with
    its own profile: let_panic_form_ok), and to a profile of FORM_PROFILES only. `box_s` None leaves the box out (the label's check). GUEST_PROFILE_REFUSED, naming what is missing. The other
    direction, a form of rungs 7 to 9 without a profile, is m1.check_let_panic's."""
    if profile in (None, DEFAULT):
        return
    if profile not in FORM_PROFILES:
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {profile}: the records of rungs 7 to 9 are written for {QUIET}, {RTC} and {ROOT} only, because their predictions and their falsifiers are for '
                                                  'the roots that quiet withdraws, the clock that quiet-rtc binds and the mount that quiet-rtc-acm is for, and nothing is predicted for any other set (UNKNOWN); the other names stay in the '
                                                  'menu until a record text exists for them')
    form = next(f for f, p in ch.FORM_PROFILE.items() if p == profile)
    if not (is_m1 and let_panic and cpu_start == 'ack' and ch.let_panic_form_ok(panel, boot_arg, box_s, cpu_start, profile)):
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {profile} is the record of rung {RUNG_OF[profile]}: --m1-run --panel {form[0]} --cpu-start ack --let-panic --boot-arg {form[1]} '
                                                  f'--time-box-s {form[2]}, and no other form carries a record text that is true of a profile guest (docs/SP11-M1-GUEST-PROFILES.md section 5)')


def check_root(profile, has_root):
    """GUEST_PROFILE_REFUSED unless the profile and the RAM-disk root go together: rung 9's profile takes the root (its credential-manager mode and its /chosen data only exist in a build that
    has one), and no other profile of the menu is built with one (a root changes the device tree's /chosen and PARAMS-independent memory, and no other record says so)."""
    if profile == ROOT and not has_root:
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {ROOT} (rung 9) is built with --ramdisk, --root-hash and --trust-cache: its credential-manager mode and its /chosen data exist only in a build that has a root')
    if profile != ROOT and has_root:
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--ramdisk, --root-hash and --trust-cache belong to --guest-profile {ROOT} (rung 9) and to no other profile: no other record says what a root does to the guest')


def resolve(a):
    """The name of the guest profile of build `a`: --guest-profile, or the default when the namespace has none (the tests' own). GUEST_PROFILE_REFUSED for a name outside
    NAMES, GUEST_PROFILE_SPLIT for a name of the menu whose registered profile is not the base board and whole root sets, before the loader is called. Every kind of real
    build takes the option; only the monitor entry run's label carries the mark (m1.pinned_label), and the form is held to rung 7's by check_form, which the builder calls next."""
    name = getattr(a, 'guest_profile', DEFAULT)
    if name not in NAMES:
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {name!r} is not in the menu: {", ".join(NAMES)}')
    if name != DEFAULT:
        try:
            menu.sets_of(gp.get_profile(name))
        except menu.MenuError as error:
            raise ch.Refused('GUEST_PROFILE_SPLIT', str(error)) from None
        except ValueError as error:
            raise ch.Refused('GUEST_PROFILE_REFUSED', f'the menu names {name!r} and the loader does not know it: {error}') from None
    return name


def root_additions(root):
    """{node: {property: value}} that the loader adds to the device tree for a RAM-disk root in the IOKit mode (probes/xnuboot.py build(), probes/ramdisk.py
    populate_ramdisk_policy): `root` is dict(extents=(base, size), boot_uuid=str, payload=bytes). boot-uuid and apfs-preboot-uuid are the profile's UUID and a NUL, the two
    policy words are zero (no protected data, no SEP firmware: the profile's recipes), the payload is the raw root-hash blob."""
    uuid = root['boot_uuid'].encode('ascii') + b'\0'
    return {'/device-tree/chosen': {'boot-ramdmg-size': struct.pack('<Q', root['extents'][1]), 'boot-ramdmg-extents': struct.pack('<QQ', *root['extents']), 'boot-uuid': uuid,
                                    'apfs-preboot-uuid': uuid, 'protected-data-access': bytes(4), 'sepfw-load-at-boot': bytes(4)},
            '/device-tree/chosen/secure-boot-hashes': {'system-volume-auth-blob': bytes(root['payload'])}}


def read_back(xb, built, name, root=None):
    """The device tree in built['chunks'] against the tree before deferral, which `xb` builds again from the same state: the loader withdrew the roots of profile `name`
    and the declarations of its deferred functions, and nothing else. For a profile other than the default (the default is the bundle of every earlier record and is
    not touched; it returns None), GUEST_PROFILE_READBACK when: a pin is not the digest of its subtree before deferral; a withdrawn root or a node under one is still published; a node
    that is not under a withdrawn root is not; a property or a child list of a retained node differs from the tree before deferral, but for the declarations withdrawn; or the
    chunk is not the serialisation of the tree it parses to; the device models are not the loader's own (with the clock model seeded at RTC_EPOCH_US for a profile of RTC_MENU and
    with no clock model for any other) or the PARAMS chunk is not their serialisation; for the profiles quiet and quiet-rtc, the windows of the withdrawn nodes are not the table of
    tools/sp11_stick_bundle_windows.py, which the record of rung 7 names in four places. Returns the number of nodes of the published tree.
    `root` (the root carry, docs/SP11-STICK-ROOT.md; None for every other build) is dict(extents=(base, size), boot_uuid=str, payload=bytes): the properties that the loader adds for a
    RAM-disk root (probes/xnuboot.py, iokit mode) are then expected with exactly these values, and no other property may differ."""
    if name == DEFAULT:
        return
    from appledt import DeviceTree
    profile = gp.get_profile(name)
    added = root_additions(root) if root is not None else {}

    def fail(detail):
        raise ch.Refused('GUEST_PROFILE_READBACK', f'{name}: {detail}')
    blob = built.get('chunks', {}).get(xb.DEVICETREE_PA)
    configured = xb.GUEST_PROFILE
    try:
        published = DeviceTree(bytes(blob))
        xb.GUEST_PROFILE = profile      # the tree and the models before deferral are those of the profile read back, whatever the loader was last configured for
        with contextlib.redirect_stdout(io.StringIO()):
            before = xb.build_devicetree(include_deferred=True)
    except (Exception, SystemExit) as error:
        fail(f'the published tree or the tree before deferral cannot be read ({type(error).__name__})')
    finally:
        xb.GUEST_PROFILE = configured
    roots = [spec.node for spec in profile.deferred_subtrees]
    for spec in profile.deferred_subtrees:
        if spec.node not in before.nodes:
            fail(f'{spec.node} is not in the generated tree')
        if hashlib.sha256(before.serialize(spec.node)).hexdigest() != spec.sha256:
            fail(f'the digest of the generated subtree {spec.node} is not its pin')
    gone = {n for n in before.nodes if any(n == r or n.startswith(r + '/') for r in roots)}
    if still := sorted(n for n in published.nodes if n in gone):
        fail(f'{len(still)} withdrawn nodes are still published, {still[0]} among them')
    if lost := sorted(set(before.nodes) - gone - set(published.nodes)):
        fail(f'{len(lost)} nodes that are not withdrawn are not published, {lost[0]} among them')
    if extra := sorted(set(published.nodes) - set(before.nodes)):
        fail(f'{len(extra)} published nodes are not in the generated tree, {extra[0]} among them')
    functions = {(f.node, f.name): f.value for f in profile.deferred_functions}
    for (node, prop), value in functions.items():
        had = before.nodes.get(node, {}).get(prop)
        if node in gone or had is None or bytes(had.value) != value or prop in published.nodes.get(node, {}):
            fail(f'the declaration {prop} of {node} is not withdrawn as the profile says')
    for node, props in published.nodes.items():
        was = {k: bytes(v.value) for k, v in before.nodes[node].items() if (node, k) not in functions}
        was.update(added.get(node, {}))       # the root's own properties, expected with exactly their values (no `root`: nothing is added)
        if {k: bytes(v.value) for k, v in props.items()} != was:
            fail(f'the properties of {node} are not those of the generated tree' + (' and the properties of the RAM root' if added.get(node) else ''))
        if list(published.children[node]) != [c for c in before.children[node] if c not in gone]:
            fail(f'the children of {node} are not those of the generated tree without the withdrawn roots')
    if published.serialize() != bytes(blob):
        fail('the chunk is not the serialisation of the tree it parses to')
    # The device models: the loader's own, with the clock model seeded at RTC_EPOCH_US in a profile of RTC_MENU and with none in any other (a clock that is unseeded is refused by the
    # monitor at start; one that is bound where the profile does not say so is a guest that no record describes), and the PARAMS chunk is their serialisation.
    expected = tuple(before.device_models)
    if menu.binds_clock(name):
        try:
            expected = tuple(menu.seed(expected))
        except ValueError as error:
            fail(f'the clock model of the loader cannot be seeded: {error}')
    models = tuple(built.get('device_models', ()))
    if any(m.kind == devicemodel.SPMI_PMU_RTC_V1 for m in models) != menu.binds_clock(name):
        fail('the real-time clock is ' + ('not bound' if menu.binds_clock(name) else 'bound') + ' in the device models, and the profile says the opposite')
    if models != expected:
        fail('the device models are not the loader\'s own' + (' with the clock seeded at RTC_EPOCH_US' if menu.binds_clock(name) else ''))
    try:
        params = xb.params_blob(built)
    except (Exception, SystemExit) as error:
        fail(f'the PARAMS chunk cannot be made again from the device models ({type(error).__name__})')
    if bytes(built.get('chunks', {}).get(xb.PARAMS_PA, b'')) != bytes(params):
        fail('the PARAMS chunk is not the serialisation of the device models')
    if name in FORM_PROFILES:
        base_roots = [spec.node for spec in gp.get_profile(DEFAULT).deferred_subtrees]
        base_gone = {n for n in before.nodes if any(n == r or n.startswith(r + '/') for r in base_roots)}
        try:
            win.check(before, published, sorted(gone - base_gone))
        except ValueError as error:
            fail(str(error))
    return len(published.nodes)
