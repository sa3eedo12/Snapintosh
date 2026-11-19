# SPDX-License-Identifier: MIT
"""The SP11 M1 guest menu: named root sets of the J700 device tree, and the profiles that are unions of them (EXPERIMENTAL; hardware NOT RUN).

The M1 monitor backs 120 device models and no more of the J700 tree. A kernel that meets a device that nothing backs waits or panics, so the A16 route never
used a catch-all: it WITHDREW the nodes that nothing backs (probes/deferred.py) and the kernel never attaches to them. Each ROOT SET below is one tuple of such
roots, with the function declarations that go with them; a profile of the MENU is the base board j700ap-26A428 plus a fixed union of whole sets. A profile is a virtual
board description: it changes the device tree and, with it, the one boot_args word that holds the tree's length. No device model, PARAMS word or monitor byte
changes (tools/test-sp11-m1-guest-profile.py, with the kernelcache), except in a profile of RTC_MENU, which binds the PMU's real-time clock. Nothing here says that a
withdrawn device is unneeded on the SP11: the A16 evidence for a root is a comment of the A16 chain, which is INFERRED for this unit.

The clock binding (RTC_MENU) is the A16 chain's own (-bootfb-rtc): the SPMI controller model of nub-spmi0 serves the PMU's counter and its two offset fields instead of an
empty bus, so that the stock real-time clock driver's read of its offset, which fails in the film of boot 27 (parity errors), can succeed and the driver can publish IORTC
(INFERRED). The monitor binary of the M1 pair already holds that code (read from its ELF, docs/SP11-M1-GUEST-PROFILES.md section 7); the binding changes three words of one device
model of the PARAMS chunk and the device tree is the quiet profile's byte for byte. The model needs a UTC seed, which the A16 route takes from the host at upload and a stick,
which has no host, cannot: the seed is the constant RTC_EPOCH_US, so that a bundle is reproducible.

Every pin is the A16 chain's own. The DeferredSubtree and DeferredFunction objects are picked from that chain by node path and never retyped, so the chain stays the one
place where a digest is written; probes/deferred.py withdraws a root only when the SHA-256 of its generated subtree is its pin. The sets are not independent: a
retained node that points at a withdrawn provider makes the loader refuse, so a set NEEDS the sets whose roots such nodes sit in. Each edge of `needs` is a refusal that
the loader's own guard gives on the base tree (VERIFIED by tools/test-sp11-m1-guest-profile.py).
"""
from dataclasses import dataclass, replace

import devicemodel
import guestprofile as gp

IO = '/device-tree/arm-io/'
PREFIX = 'j700ap-26A428-m1-'
# The one SPMI controller whose PMU holds the real-time clock (the A16 chain's -bootfb-rtc binds this node and no other: probes/devicemodel.py spmi_models pins its address,
# its interrupt and the digest of its pmu-main subtree).
RTC_NODE = IO + 'nub-spmi0'
# The instant at which the bound clock starts counting: 2026-11-20T00:00:00Z in microseconds since the Unix epoch. A placeholder, NOT the unit's time: the stick has no host clock and
# a build that read one would not regenerate byte for byte. The model starts at monitor start (the device registry's start), so the guest's calendar is this instant plus the guest's run time.
RTC_EPOCH_US = 1_795_132_800_000_000
# The profiles of the A16 chain that pin the roots below. A node must be pinned by exactly one digest in all of them, or the import fails.
_CHAIN = (gp.J700_26A428, gp.J700_26A428_NO_CLPC, gp.J700_26A428_BOOTFB_NO_USB, gp.J700_26A428_BOOTFB_XARS_UNIFORM_NO_AUDIO,
          gp.J700_26A428_XARS_UNIFORM_AUXKC_INSTALLED_ADRENO_METAL_DISPLAY,
          gp.J700_26A428_XARS_UNIFORM_AUXKC_INSTALLED_ADRENO_METAL_DISPLAY_SKS_QUIET)


class MenuError(ValueError):
    """A profile that is not the base board and whole root sets. `kind`: 'needs' (a set without a set it needs), 'split' (a set only partly withdrawn), 'shape' (anything
    else that differs from the composition), 'unknown' (a set that does not exist)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class RootSet:
    name: str
    roots: tuple            # DeferredSubtree, the chain's own
    functions: tuple = ()   # DeferredFunction: declarations of retained nodes that name a provider inside these roots
    drops: tuple = ()       # (node, function name) of the BASE board's declarations that sit on a root of this set and so cannot stay
    needs: tuple = ()       # names of the sets that must be withdrawn with this one


def _pin(node):
    found = {spec for profile in _CHAIN for spec in profile.deferred_subtrees if spec.node == node}
    if len(found) != 1:
        raise RuntimeError(f'{node}: the A16 chain pins it with {len(found)} digests (exactly one is needed)')
    return found.pop()


def _function(node, name):
    found = {spec for profile in _CHAIN for spec in profile.deferred_functions if (spec.node, spec.name) == (node, name)}
    if len(found) != 1:
        raise RuntimeError(f'{node}:{name}: the A16 chain declares it {len(found)} times (exactly once is needed)')
    return found.pop()


def _io(*names):
    return tuple(_pin(IO + name) for name in names)


# The order of the dict is the canonical order of a composition.
ROOT_SETS = {s.name: s for s in (
    # avd reached an unbacked accelerator aperture on the A16; scaler0 and apr are absent instances (-no-clpc). The part of -no-clpc that is not a root is not taken.
    RootSet('accel', _io('avd', 'scaler0', 'apr')),
    # The codec and the RX proxy cross-reference each other, so the nine go together (-no-audio).
    RootSet('audio', _io('iop-audio-controller', 'iop-voicetrigger-controller', 'admac-base-ns', 'admac-leap-ns', 'admac-leap-s', 'admac-leap-s-rx-0-proxy',
                         'audio-shared-dart-proxy', 'audio-hp-mic-proxy', 'i2c0/audio-codec-output')),
    # The A16 starts waited at RTBuddy routes 0x65 and 0x69 (aop, aop2) and at the Stockholm start (aop-spmi0). aop-spmi0 also holds the one node that points at smc-pmu.
    RootSet('aop', _io('aop', 'aop2', 'aop-spmi0')),
    # -metal-display. The external display pipe: nothing in it is pointed at by a retained node.
    RootSet('dcpext', _io('dispext0', 'dcpext', 'dcpext-expert')),
    # -sks-quiet. dispext0's function-send_disp_clock points at disp0, so the display graph cannot go without the external one.
    RootSet('dcp', _io('dcp', 'dcp0-expert', 'dp-audio0', 'dp-audio1', 'disp0') + (_pin('/device-tree/backlight'),), needs=('dcpext',)),
    RootSet('ave', _io('ave', 'ave1')),
    # ONE unit. Without smc the PMU functions have no provider; without btm and the four btm functions of ppm, pmu-main returns and btm is published, which was a panic
    # on the A16 (AppleCotaBTM::configSampling, "unable to config PMU", generations 64 and 65). The three retained nodes that point at smc-pmu sit in aop-spmi0 (stockholm),
    # disp0 (function-pmu_ram_access) and iop-audio-controller (audio-hp-mic): the unit needs the three sets that withdraw them. smc-gpio stays: the A16 retains it, and the
    # base board models it.
    RootSet('smcbtm', _io('smc', 'smctempsensor0') + (_pin('/device-tree/buttons'), _pin(IO + ':j700_has_mesa_0')) + _io('nub-spmi0/btm'),
            functions=tuple(_function(IO + node, name) for node, name in (
                ('nub-spmi0/pmu-main', 'function-external_standby'),
                ('dockchannel-mtp/mtp-transport/multi-touch', 'function-afe-reset'),
                ('dockchannel-mtp/mtp-transport/multi-touch', 'function-ipd-pwr-seq-control'),
                ('pmgr/ppm', 'function-btm-config'), ('pmgr/ppm', 'function-btm-start'), ('pmgr/ppm', 'function-btm-stop'), ('pmgr/ppm', 'function-btm-vthr'))),
            needs=('aop', 'dcp', 'audio')),
    # -no-clpc. The base board declares clpc's function-ane_perf_ctr withdrawn (its provider, ane, is absent); that declaration is on a node that goes, and probes/deferred.py
    # refuses one, so the set drops it.
    RootSet('clpc', _io('pmgr/clpc'), drops=((IO + 'pmgr/clpc', 'function-ane_perf_ctr'),)),
    # -bootfb-no-usb.
    RootSet('usb', _io('usb-drd', 'atc0-dpphy', 'i2c2/usb-hub-i2c')),
)}
ORDER = tuple(ROOT_SETS)
BASE = gp.J700_26A428
# Not offered. The four jpeg roots have no pin in the A16 chain; a pin is the digest of a generated subtree and a new one is a decision of its own.
NOT_PINNED = tuple(IO + name for name in ('jpeg0-h17a', 'jpeg0-h17p', 'jpeg1-h17a', 'jpeg1-h17p'))


def bind_rtc(profile):
    """`profile` with the PMU's real-time clock bound at RTC_NODE: the A16 chain's own SPMIBootSpec(rtc=True) of that node (J700_26A428_BOOTFB_RTC) and nothing else. The binding
    is a boot spec, not a device-tree edit: the published tree is the same and the loader builds the clock model in place of the node's empty bus (unseeded until `seed`)."""
    if sum(spec.node == RTC_NODE for spec in profile.spmi_boot) != 1:
        raise RuntimeError(f'{RTC_NODE} is not exactly one SPMI boot spec of {profile.name}')
    return replace(profile, spmi_boot=tuple(replace(spec, rtc=True) if spec.node == RTC_NODE else spec for spec in profile.spmi_boot))


def compose(name, chosen, base=BASE, rtc=False):
    """`base` plus the whole sets `chosen`, in canonical order whatever the order given, with the clock bound when `rtc`. MenuError 'unknown' for a name that is no set and 'needs' for
    a set whose needs are not all chosen."""
    chosen = set(chosen)
    if missing := sorted(chosen - set(ORDER)):
        raise MenuError('unknown', f'no root set {missing[0]!r}')
    order = [n for n in ORDER if n in chosen]
    for n in order:
        if lacking := [need for need in ROOT_SETS[n].needs if need not in chosen]:
            raise MenuError('needs', f'root set {n} needs {" and ".join(lacking)}: a retained node points at a provider inside {"them" if len(lacking) > 1 else "it"}')
    dropped = {drop for n in order for drop in ROOT_SETS[n].drops}
    kept = tuple(f for f in base.deferred_functions if (f.node, f.name) not in dropped)
    if len(kept) + len(dropped) != len(base.deferred_functions):
        raise MenuError('shape', 'a root set drops a declaration that the base board does not make')
    composed = replace(base, name=name, deferred_subtrees=base.deferred_subtrees + tuple(r for n in order for r in ROOT_SETS[n].roots),
                       deferred_functions=kept + tuple(f for n in order for f in ROOT_SETS[n].functions))
    return bind_rtc(composed) if rtc else composed


def rtc_of(profile, base=BASE):
    """True when `profile` binds the clock, that is, when its boot specs are not the base board's. Whether they are exactly the binding of bind_rtc is sets_of's check."""
    return profile.spmi_boot != base.spmi_boot


def sets_of(profile, base=BASE):
    """The root sets that `profile` withdraws on top of `base`, in canonical order. MenuError 'split' for a set that is only partly withdrawn (its roots or its functions),
    'needs' for a set without those it needs, 'shape' for a profile that is not exactly the composition of the sets found, with the clock bound or not (another root, another field,
    a clock bound at another node or more than the clock)."""
    roots = {(s.node, s.sha256) for s in profile.deferred_subtrees}
    functions = {(f.node, f.name, f.value) for f in profile.deferred_functions}
    chosen = []
    for name in ORDER:
        s = ROOT_SETS[name]
        absent = [r.node for r in s.roots if (r.node, r.sha256) not in roots] + [f'{f.node}:{f.name}' for f in s.functions if (f.node, f.name, f.value) not in functions]
        if not absent:
            chosen.append(name)
        elif len(absent) < len(s.roots) + len(s.functions):
            raise MenuError('split', f'root set {name} is only partly withdrawn: {absent[0]}{" and others" if len(absent) > 1 else ""} stay{"s" if len(absent) == 1 else ""} published')
    if profile != compose(profile.name, chosen, base, rtc=rtc_of(profile, base)):
        raise MenuError('shape', f'{profile.name} is not {base.name} and the whole root sets {", ".join(chosen) or "(none)"}{" with the clock bound at " + RTC_NODE if rtc_of(profile, base) else ""}')
    return tuple(chosen)


MENU = {
    PREFIX + 'quiet': ('accel', 'audio'),
    PREFIX + 'quiet-aop': ('accel', 'audio', 'aop'),
    PREFIX + 'quiet-dcp': ('accel', 'audio', 'dcpext', 'dcp'),
    PREFIX + 'quiet-ave': ('accel', 'audio', 'ave'),
    PREFIX + 'quiet-clpc': ('accel', 'audio', 'clpc'),
    PREFIX + 'quiet-usb': ('accel', 'audio', 'usb'),
    PREFIX + 'quiet-smc': ('accel', 'audio', 'aop', 'dcpext', 'dcp', 'smcbtm'),
    PREFIX + 'all-quiet': ('accel', 'audio', 'aop', 'dcpext', 'dcp', 'ave', 'smcbtm'),
}
# The profiles that bind the clock: the sets they withdraw, in the same terms as MENU. Kept apart from MENU, whose profiles change the device tree and nothing in PARAMS, so that
# the profile text module and its menu comparison (tools/test-sp11-stick-monitor-profile-text.py) keep holding the eight; this one has no record text.
RTC_MENU = {
    PREFIX + 'quiet-rtc': ('accel', 'audio'),
}
MENU_PROFILES = {name: compose(name, sets) for name, sets in MENU.items()}
RTC_PROFILES = {name: compose(name, sets, rtc=True) for name, sets in RTC_MENU.items()}


def seed(models):
    """`models` (a loader's device_models) with the one clock model seeded at RTC_EPOCH_US. ValueError, from probes/devicemodel.py seed_rtc_models, unless exactly one unseeded
    clock model is in them: a profile of RTC_MENU has one and any other has none."""
    return devicemodel.seed_rtc_models(models, RTC_EPOCH_US)


def seeded(models):
    """`models` as they go into PARAMS: seeded at RTC_EPOCH_US when they hold a clock model (a profile of RTC_MENU) and the same object when they do not, so that every other
    guest is byte for byte what it was."""
    return seed(models) if any(m.kind == devicemodel.SPMI_PMU_RTC_V1 for m in models) else models


def register():
    """Add the profiles of the menu and of RTC_MENU to guestprofile.PROFILES, where the loader's configure_guest finds them; returns them. Idempotent. Only the M1 stick builder calls it
    (tools/sp11_stick_bundle_profile.py): probes/guestprofile.py does not import this file, so the A16 route, whose tools copy and hash that file alone, never sees the menu."""
    registered = {**MENU_PROFILES, **RTC_PROFILES}
    for name, profile in registered.items():
        if gp.PROFILES.setdefault(name, profile) is not profile:
            raise RuntimeError(f'{name} is registered with another profile')
    return registered
