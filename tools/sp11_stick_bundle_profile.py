# SPDX-License-Identifier: MIT
"""--guest-profile: the closed menu of guest device trees of the SP11 M1 bundle (docs/SP11-M1-GUEST-PROFILES.md; EXPERIMENTAL, host only, hardware NOT RUN).

The base board j700ap-26A428 is the board of every bundle so far and stays the default: a build without the option makes the call it always made and the same bytes. The
other eight are virtual board descriptions of probes/guestprofile_m1.py (the base board and a fixed union of whole root sets): they change the device tree and the one
boot_args word that holds its length, and nothing else. What this module adds to a build is a closed list (a name outside it is GUEST_PROFILE_REFUSED), a mark per profile
for the pinned label (a record names its profile, and the PIN.DAT of one profile is not that of another), a check that a registered profile is whole root sets
(GUEST_PROFILE_SPLIT) and a read-back of the device tree that the loader built (GUEST_PROFILE_READBACK)."""
import contextlib
import hashlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'probes'))
import sp11_stick_bundle_chunks as ch  # noqa: E402
import guestprofile as gp  # noqa: E402
import guestprofile_m1 as menu  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

REFUSALS = ('GUEST_PROFILE_REFUSED', 'GUEST_PROFILE_SPLIT', 'GUEST_PROFILE_READBACK')
ch.KNOWN_REFUSALS.update(REFUSALS)
menu.register()         # the loader's configure_guest looks a profile up in guestprofile.PROFILES
DEFAULT = ch.GUEST_PROFILE_DEFAULT
assert DEFAULT == menu.BASE.name        # the literal of sp11_stick_bundle_chunks.py is the loader's own default, which this file does not import
NAMES = (DEFAULT, *menu.MENU)           # the whole list: nothing that probes/guestprofile.py registers for the A16 route is admitted
# What a pinned label carries for each profile, after the marker of the guest's other choices (m1.pinned_label): two characters, so that M1RUN-LETPANIC- (15) and the mark
# leave a facts label of six (RUNG10). Every non-default profile is the quiet board and what it adds: Q quiet, A +aop, D +dcp (with dcpext), V +ave, C +clpc, U +usb,
# S +smc (the smc/btm unit with the three sets it needs), X all of them but clpc and usb.
MARKS = dict(zip(NAMES, ('', 'Q-', 'A-', 'D-', 'V-', 'C-', 'U-', 'S-', 'X-')))
assert len(set(MARKS.values())) == len(MARKS) == len(NAMES)
MARK_CHARS = 2
QUIET = NAMES[1]                        # j700ap-26A428-m1-quiet: the one profile that a record is written for (RUNG7_PROFILES)
RUNG7_PROFILES = (QUIET,)               # the profiles that the form of rung 7 admits: a profile record carries a prediction and its falsifiers for the roots that the profile withdraws
                                        # (docs/SP11-M1-GUEST-PROFILES.md section 5), and that text exists for this one; the others stay in the menu until theirs does
assert QUIET == ch.RUNG7_PROFILE and ch.FORM_PROFILE == {ch.RUNG7_FORM: QUIET}      # the form's own constant is the menu's name


def mark_of(name):
    """The pinned label's mark of profile `name` ('' for the default and for None)."""
    if name is None:
        return ''
    if name not in MARKS:
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {name!r} is not in the menu: {", ".join(NAMES)}')
    return MARKS[name]


def check_form(profile, boot_arg, let_panic, cpu_start, panel, is_m1, box_s=None):
    """The form of a guest profile, checked before any guest is built: a profile other than the default belongs to the record of rung 7 and to no other (the monitor entry run, the
    acknowledging monitor, --let-panic, the panel as the console, the word cpu_boot_timeout_secs=120 and the box of 120 s: ch.RUNG7_FORM), and to a profile of RUNG7_PROFILES only.
    `box_s` None leaves the box out (the label's check). GUEST_PROFILE_REFUSED, naming what is missing. The other direction, rung 7's form without a profile, is m1.check_let_panic's."""
    if profile in (None, DEFAULT):
        return
    if profile not in RUNG7_PROFILES:
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {profile}: the record of rung 7 is written for {QUIET} only, because its prediction and its falsifiers are for the roots that '
                                                  'quiet withdraws and nothing is predicted for any other set (UNKNOWN); the other names stay in the menu until a record text exists for them')
    if not (is_m1 and let_panic and cpu_start == 'ack' and ch.let_panic_form_ok(panel, boot_arg, box_s, cpu_start, profile)):
        raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {profile} is the record of rung 7: --m1-run --panel {ch.RUNG7_FORM[0]} --cpu-start ack --let-panic --boot-arg {ch.RUNG7_FORM[1]} '
                                                  f'--time-box-s {ch.RUNG7_FORM[2]}, and no other form carries a record text that is true of a profile guest (docs/SP11-M1-GUEST-PROFILES.md section 5)')


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


def read_back(xb, built, name):
    """The device tree in built['chunks'] against the tree before deferral, which `xb` builds again from the same state: the loader withdrew the roots of profile `name`
    and the declarations of its deferred functions, and nothing else. For a profile other than the default (the default is the bundle of every earlier record and is
    not touched; it returns None), GUEST_PROFILE_READBACK when: a pin is not the digest of its subtree before deferral; a withdrawn root or a node under one is still published; a node
    that is not under a withdrawn root is not; a property or a child list of a retained node differs from the tree before deferral, but for the declarations withdrawn; or the
    chunk is not the serialisation of the tree it parses to; for the profile quiet, the windows of the withdrawn nodes are not the table of tools/sp11_stick_bundle_windows.py, which the
    record of rung 7 names in four places. Returns the number of nodes of the published tree."""
    if name == DEFAULT:
        return
    from appledt import DeviceTree
    profile = gp.get_profile(name)

    def fail(detail):
        raise ch.Refused('GUEST_PROFILE_READBACK', f'{name}: {detail}')
    blob = built.get('chunks', {}).get(xb.DEVICETREE_PA)
    try:
        published = DeviceTree(bytes(blob))
        with contextlib.redirect_stdout(io.StringIO()):
            before = xb.build_devicetree(include_deferred=True)
    except (Exception, SystemExit) as error:
        fail(f'the published tree or the tree before deferral cannot be read ({type(error).__name__})')
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
        if {k: bytes(v.value) for k, v in props.items()} != was:
            fail(f'the properties of {node} are not those of the generated tree')
        if list(published.children[node]) != [c for c in before.children[node] if c not in gone]:
            fail(f'the children of {node} are not those of the generated tree without the withdrawn roots')
    if published.serialize() != bytes(blob):
        fail('the chunk is not the serialisation of the tree it parses to')
    if name == QUIET:
        base_roots = [spec.node for spec in gp.get_profile(DEFAULT).deferred_subtrees]
        base_gone = {n for n in before.nodes if any(n == r or n.startswith(r + '/') for r in base_roots)}
        try:
            win.check(before, published, sorted(gone - base_gone))
        except ValueError as error:
            fail(str(error))
    return len(published.nodes)
