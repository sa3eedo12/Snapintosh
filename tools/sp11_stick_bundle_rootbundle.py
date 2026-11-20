# SPDX-License-Identifier: MIT
"""The bundle of the root form (rung 9; docs/SP11-STICK-ROOT.md): what tools/sp11_stick_bundle_build.py needs besides the root-guest command to build a whole bundle, the record template
and the stage tree for a guest of 1.9 GB. Evidence: EXPERIMENTAL, hardware NOT RUN. Nothing here approves, authorises or stages anything.

The builder's pipeline holds GUEST.DAT as bytes; the guest of this form is never held. `guest_of` returns a tools/sp11_stick_bundle_root.py VirtualGuest instead, which the interface's own
checks, the record's digests and the writer read by slices, so that the record states the file's size and SHA-256 before one byte is written and nothing is written until the approved
SHA-256 matches (a cross use writes nothing). The file is then written in windows and read back by pread (`write_checked`): its SHA-256 must be the one the record states.

Every option of the form is checked here, by name, before a guest is built: the gate (SP11_ROOT_BUILD=1, as for root-guest), the three inputs of the root, --bigfile (the limits of the
pair of this form) and the guest's RAM; every other profile of the menu refuses a root and rung 9's profile refuses to be built without one (tools/sp11_stick_bundle_profile.py check_root)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_profile as gpb  # noqa: E402
import sp11_stick_bundle_root as root  # noqa: E402

Refused = ch.Refused
ROOT_INPUTS = ('ramdisk', 'root_hash', 'trust_cache')
CMDLINE_EXTRA = ' acm_fastsim=1'        # what probes/credential.py software_cmdline appends to the loader's command line, last, in a build of a profile with the credential manager's software mode


def is_form(a):
    """True when the build `a` is rung 9's (its guest profile); the other profiles of the menu are every build before it."""
    return root.is_form(a)


def cmd_extra(profile):
    """What follows the form's word in the guest's command line: the credential manager's argument for rung 9's profile, nothing for any other."""
    return CMDLINE_EXTRA if profile == ch.RUNG9_PROFILE else ''


def check_args(a, profile, kind):
    """The options of rung 9's build, before any guest is built: OPTIONS_BAD, ROOT_GATE_OFF, ROOT_INPUT_BAD or GUEST_PROFILE_REFUSED, naming what is missing. A root with another profile and
    the profile without a root are both refused. Nothing for a build that has neither."""
    has = [bool(getattr(a, name, None)) for name in ROOT_INPUTS]
    gpb.check_root(profile, any(has))
    if profile != ch.RUNG9_PROFILE:
        return
    if kind != 'real':
        raise Refused('OPTIONS_BAD', f'--guest-profile {profile} is a profile of the real guest: a synthetic build has no kernel to mount a root')
    root.gate()
    if not all(has):
        raise Refused('ROOT_INPUT_BAD', 'rung 9 takes --ramdisk, --root-hash and --trust-cache together')
    if not getattr(a, 'bigfile', False):
        raise Refused('OPTIONS_BAD', 'rung 9\'s guest is over the 256 MiB of the older pairs: give --bigfile (the limits of the pair of this form, 2 GiB, and the manifest tool\'s 8 MiB chunk cap)')
    if a.guest_mib != ch.RUNG9_RAM_MIB:
        raise Refused('OPTIONS_BAD', f'rung 9\'s guest has {ch.RUNG9_RAM_MIB} MiB of RAM (the form is the root with room to run in: docs/SP11-STICK-ROOT.md); got --guest-mib {a.guest_mib}')


def guest(a, xb, place, facts, profile):
    """(built, number of published nodes) of rung 9's guest: the loader with the root, read back against the profile and the root's own /chosen properties
    (tools/sp11_stick_bundle_root.py real_guest), and the placement kept off the runner's data files (the root's own rule)."""
    root.check_data_top(place)
    built, _, nodes, _ = root.real_guest(a, xb, place, facts, profile)
    return built, nodes


def guest_of(place, chunks):
    """GUEST.DAT for `chunks`: the bytes (tools/sp11_stick_bundle_chunks.py guest_from_chunks) unless a chunk is held as a source, which only the root is, and then a VirtualGuest that has
    passed the interface's own structure check against the table it carries."""
    if not any('source' in c for c in chunks):
        return ch.guest_from_chunks(place, chunks)
    virtual = root.VirtualGuest(place['guest_base'], place['guest_size'], chunks)
    with sb.bigfile():
        if code := sb._guest_check(virtual, len(virtual), (virtual.load_base, virtual.load_size, len(virtual), virtual.count, virtual.index_sha256)):
            raise ch.from_checker(ValueError(f'GUEST.DAT refused by its own checker: {sb.error_name(code)}'))
    return virtual


def is_virtual(data):
    return isinstance(data, root.VirtualGuest)


def write_checked(path, virtual):
    """Write `virtual` to the new file `path` and read the file back by pread: its structure, table, every chunk digest and the digest of the whole file (tools/sp11_stick_bundle_root.py
    verify_guest), and the whole-file digest must be the one the record states. FILE_DIGEST otherwise."""
    virtual.write_to(path)
    checked = root.verify_guest(path, virtual.load_base, virtual.load_size, virtual.written())
    if checked['sha256'] != virtual.sha256():
        raise Refused('FILE_DIGEST', 'GUEST.DAT as it was written is not the file that the record states')
    return checked
