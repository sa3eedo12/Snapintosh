#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assemble and verify the SP11 X0/X1 boot-media directory tree (host only).

This tool never writes a disk: it stages a plain directory that the operator
later copies onto a FAT32 stick themselves (see the operator procedure text
in REPORT.md / the proposed docs text). `stage` and `verify` both refuse any
destination that looks like a real mounted volume (a `..` component, a mount
point or a directory below one, under /Volumes or /dev in any letter case, or on
a different filesystem than its parent) so that an accidental `--dest` pointing
at a real disk is rejected before anything is written, not after.

Inputs are validated structurally (PE/COFF headers only) and never executed.
Each input is read once and the validated bytes are the bytes staged, hashed
and listed in the manifest. The aarch64 UEFI shell is the one hash-pinned input:
`stage` and `verify` refuse a missing or unreadable pins file, and only an
explicit `--no-pins` skips the check (the result then says `shell_pin:
unchecked`). `tcblaunch.exe` is the sole exception to hash pinning: which build
actually works on SP11 is UNKNOWN until hardware bring-up (X1), so this tool only
records its hash, never checks it against an expectation.

`autorun` is an opt-in addition and changes nothing for a tree that never uses it.
It works on a directory `stage` made and `verify` accepts, under the same destination
refusals, and never on a mounted stick: it copies the named experiment files into
SP11/, writes EFI/BOOT/startup.nsh (one plan step per boot, markers written first)
and SP11/PLAN.TXT, and lists them in the manifest. `verify` then accepts that one
startup.nsh, and only if it is exactly the script the plan in PLAN.TXT generates;
any other startup.nsh is still a finding. `autorun --clear` and `--remove` undo it in
a staged directory (on a stick the markers and the script are deleted by hand).
Plan rules and the script generator are in sp11_autorun.py.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import stat
import struct
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import sp11_autorun as autorun  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PINS = ROOT / 'tools' / 'sp11-boot-media-pins.json'
PINS_MAX_BYTES = 1 << 20
SHA256_RE = re.compile(r'[0-9a-f]{64}\Z')

# Directories below which a path can be a real mounted volume or a device node. They are matched by spelling in
# any letter case and by identity (os.path.samefile): macOS volumes are case-insensitive, and
# /System/Volumes/Data/Volumes is the very directory /Volumes through a firmlink.
PROTECTED_ROOTS = (('/Volumes', 'dest_under_volumes'), ('/dev', 'dest_under_dev'))

# Fixed destination layout. Values are POSIX-style relative paths; on the
# POSIX host this tool requires (see BUILDING.md toolchain list) `Path`
# joins these directly without a PurePosixPath detour.
LAYOUT = {
    'shell': 'EFI/BOOT/BOOTAA64.EFI',
    'slbounce': 'SP11/SLBOUNCE.EFI',
    'sltest': 'SP11/SLTEST.EFI',
    'probe': 'SP11/PROBE.EFI',
    'tcblaunch': 'SP11/TCBLAUNCH.EXE',
}

# slbounce.efi's real driver path (src/bounce_main.c:213,219, as opposed to
# sltest.efi, which takes an explicit argv path) hard-codes
# FileOpen(GetVolume(ImageHandle), L"tcblaunch.exe"). GetVolume()
# (src/util.c) resolves the EFI_LOADED_IMAGE's DeviceHandle to its
# EFI_SIMPLE_FILE_SYSTEM_PROTOCOL and calls OpenVolume(), which the UEFI
# spec defines as returning the filesystem's *root* directory regardless of
# the directory slbounce.efi itself was loaded from. So `load
# SP11\SLBOUNCE.EFI` only finds tcblaunch.exe if a copy also sits at this
# media's root, not only under SP11/ (BRIEF.md's layout only lists the
# latter). `stage` writes both copies from the same input bytes so the
# operator does not have to remember a manual copy step; see REPORT.md
# "Open questions for the coordinator" for this deviation from the literal
# brief layout.
TCBLAUNCH_ROOT_REL = 'TCBLAUNCH.EXE'
README_REL = 'SP11/README.TXT'
MANIFEST_REL = 'SP11/MANIFEST.SHA256'

# Items required for the staged tree to read as COMPLETE. PROBE is a
# cross-agent optional bonus (the uefi-probe agent's artifact may not exist
# yet per BRIEF.md) so its absence is reported but never blocks COMPLETE.
REQUIRED_FOR_COMPLETE = ('shell', 'slbounce', 'sltest', 'tcblaunch')
OPTIONAL_ITEMS = ('probe',)

PE_MACHINE_ARM64 = 0xAA64
PE32PLUS_MAGIC = 0x20B

# EFI subsystem values actually OBSERVED for the pinned slbounce commit
# c090a8cdafa25e4c99df90f8d6f73f3805d9b397 (Makefile: SUBSYSTEM_APP=10,
# SUBSYSTEM_RT=12 are hard-coded, not flag-dependent) and for the pinned
# pbatard/UEFI-Shell release (llvm-readobj: IMAGE_SUBSYSTEM_EFI_APPLICATION).
# BRIEF.md's acceptance text says "subsystem 10/11" generically for all
# .efi inputs; slbounce.efi is actually subsystem 12 (EFI_RUNTIME_DRIVER),
# not 10 or 11. See REPORT.md "Open questions for the coordinator" for this
# deviation. `probe` is the uefi-probe agent's artifact, built outside this
# tree; its subsystem is not independently confirmed here, so it is left
# permissive (both known EFI driver flavors plus EFI_APPLICATION).
ROLE_SUBSYSTEMS = {
    'shell': frozenset({10}),
    'sltest': frozenset({10}),
    'slbounce': frozenset({12}),
    'probe': frozenset({10, 11, 12}),
    # An application an autorun plan runs. OBSERVED: the q1n1, X2 tests and probe images built today are subsystem 10.
    'app': frozenset({10}),
}

# Generous headroom over observed sizes (shell ~1.1 MiB, slbounce ~103 KiB,
# sltest ~65 KiB) without accepting an implausibly large "EFI application".
MAX_EFI_BYTES = 16 * 1024 * 1024
# tcblaunch.exe is a Windows PE binary, typically a few MiB; still bounded
# so a wrong file (e.g. an accidental ISO) cannot be staged by mistake.
MAX_TCBLAUNCH_BYTES = 64 * 1024 * 1024

# Deterministic output: every file/directory this tool writes gets this
# fixed mtime and a fixed mode, so two independent `stage` runs for the same
# inputs are byte-for-byte and metadata-for-metadata identical (required by
# BRIEF.md item 5, "Output is deterministic"). Epoch is an arbitrary but
# reproducible choice; it carries no meaning beyond "not real wall-clock time".
DETERMINISTIC_EPOCH = 0
FILE_MODE = 0o644
DIR_MODE = 0o755

SHA256_LINE_RE = re.compile(r'^([0-9a-f]{64})  (.+)$')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_pe_header(data: bytes) -> dict:
    """Parse the minimal MZ/PE/PE32+ fields this tool needs to validate.

    Returns a dict with 'ok' and, on failure, 'reason'. On success also
    'machine' and 'subsystem'. Never raises on malformed input: every bound
    is checked explicitly so a truncated or corrupt file yields a specific
    reason string instead of an exception, which keeps CLI error reporting
    (and the matching unit tests) precise about which check failed.
    """
    if len(data) < 0x40:
        return {'ok': False, 'reason': 'truncated_dos_header'}
    if data[0:2] != b'MZ':
        return {'ok': False, 'reason': 'bad_mz_signature'}
    (e_lfanew,) = struct.unpack_from('<I', data, 0x3C)
    if e_lfanew < 0x40 or e_lfanew + 4 > len(data):
        return {'ok': False, 'reason': 'bad_pe_offset'}
    if data[e_lfanew:e_lfanew + 4] != b'PE\x00\x00':
        return {'ok': False, 'reason': 'bad_pe_signature'}
    file_header_off = e_lfanew + 4
    if file_header_off + 20 > len(data):
        return {'ok': False, 'reason': 'truncated_file_header'}
    (machine,) = struct.unpack_from('<H', data, file_header_off)
    (size_of_optional_header,) = struct.unpack_from('<H', data, file_header_off + 16)
    opt_header_off = file_header_off + 20
    if opt_header_off + 2 > len(data):
        return {'ok': False, 'reason': 'truncated_optional_header'}
    (magic,) = struct.unpack_from('<H', data, opt_header_off)
    if magic != PE32PLUS_MAGIC:
        return {'ok': False, 'reason': 'unsupported_pe_magic'}
    # Subsystem sits at offset 68 in both PE32 and PE32+ optional headers:
    # PE32+ drops the 4-byte BaseOfData field but widens ImageBase from 4 to
    # 8 bytes, so every field from SectionAlignment (offset 32) onward lines
    # up at the same offset regardless of which variant this is. We only
    # accept PE32+ above, so this is exact, not a coincidence to re-check.
    subsystem_off = opt_header_off + 68
    if size_of_optional_header < 70 or subsystem_off + 2 > len(data):
        return {'ok': False, 'reason': 'truncated_optional_header'}
    (subsystem,) = struct.unpack_from('<H', data, subsystem_off)
    return {'ok': True, 'machine': machine, 'subsystem': subsystem, 'magic': magic}


def find_symlink_component(path: Path):
    """Return the first existing symlink among `path` and its ancestors, if any."""
    candidates = [path] + list(path.parents)
    for candidate in candidates:
        try:
            if candidate.is_symlink():
                return candidate
        except OSError:
            continue
    return None


def is_safe_relative_path(name: str) -> bool:
    """Reject manifest entries that could escape `dest` when joined to it."""
    if not name or '\x00' in name or '\\' in name:
        return False
    if name.startswith('/') or name.startswith('~'):
        return False
    parts = name.split('/')
    return all(part not in ('', '.', '..') for part in parts)


class Refusal(Exception):
    def __init__(self, reason: str, detail: str = ''):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def read_regular_bytes(path: Path, expected, cap: int) -> bytes:
    """Read `path` once through a descriptor that is checked after it is opened.

    `expected` is the lstat() taken when the path was validated. The open refuses a symbolic link, and the
    descriptor must still be that regular file at the same size, so a file replaced between the check and the
    read cannot put other bytes behind the PE validation.
    """
    flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
             | getattr(os, 'O_CLOEXEC', 0))
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise Refusal('input_unreadable', f'{path}: {exc.strerror}') from exc
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
                or opened.st_size != expected.st_size):
            raise Refusal('input_changed_while_reading', str(path))
        chunks, total = [], 0
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            total += len(block)
            if total > min(cap, expected.st_size):
                raise Refusal('input_changed_while_reading', str(path))
            chunks.append(block)
    finally:
        os.close(fd)
    if total != expected.st_size:
        raise Refusal('input_changed_while_reading', str(path))
    return b''.join(chunks)


def read_validated_input(path: Path, role: str):
    """Validate one staged input; returns (PE/hash facts, the exact bytes that were validated) or raises Refusal.

    The hash, the pin comparison and the staged file all come from these bytes: reading the path a second time
    would let a file replaced after validation be staged unchecked.
    """
    symlinked = find_symlink_component(path)
    if symlinked == path:
        raise Refusal('input_is_symlink', str(path))
    if symlinked is not None:
        raise Refusal('input_path_has_symlink_component', str(symlinked))
    if not path.exists():
        raise Refusal('missing_input', str(path))
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise Refusal('input_not_regular_file', str(path))
    cap = MAX_TCBLAUNCH_BYTES if role == 'tcblaunch' else MAX_EFI_BYTES
    if st.st_size == 0:
        raise Refusal('input_empty_file', str(path))
    if st.st_size > cap:
        raise Refusal('input_too_large', f'{path}: {st.st_size} > {cap}')
    data = read_regular_bytes(path, st, cap)
    pe = read_pe_header(data)
    if not pe['ok']:
        raise Refusal('input_' + pe['reason'], str(path))
    if pe['machine'] != PE_MACHINE_ARM64:
        raise Refusal('input_wrong_machine', f'{path}: machine=0x{pe["machine"]:04x}')
    if role != 'tcblaunch':
        allowed = ROLE_SUBSYSTEMS[role]
        if pe['subsystem'] not in allowed:
            raise Refusal('input_wrong_subsystem',
                           f'{path}: subsystem={pe["subsystem"]} not in {sorted(allowed)}')
    facts = {'path': str(path), 'size': len(data), 'sha256': sha256_bytes(data),
             'machine': pe['machine'], 'subsystem': pe['subsystem']}
    return facts, data


def validate_input_file(path: Path, role: str) -> dict:
    """Validate one input and return its facts; `stage` uses read_validated_input to keep the bytes as well."""
    return read_validated_input(path, role)[0]


def refuse_protected_location(path: Path) -> None:
    """Refuse `path` when it is, or lies below, /Volumes or /dev by any spelling the filesystem resolves there.

    A spelling test alone is not enough: macOS volumes are case-insensitive ('/volumes/x' is '/Volumes/x'), and a
    firmlink makes /System/Volumes/Data/Volumes the same directory as /Volumes.
    """
    lowered = path.as_posix().lower()
    for root, reason in PROTECTED_ROOTS:
        if lowered == root.lower() or lowered.startswith(root.lower() + '/'):
            raise Refusal(reason, str(path))
        for ancestor in (path, *path.parents):
            try:
                same = os.path.samefile(ancestor, root)
            except OSError:
                continue
            if same:
                raise Refusal(reason, str(path))


def find_mount_point_ancestor(path: Path):
    """The first ancestor of `path` (itself included), other than '/', that is a mount point; None if there is none."""
    for candidate in (path, *path.parents):
        if candidate != candidate.parent and os.path.ismount(candidate):
            return candidate
    return None


def check_destination_safety(dest: Path, must_exist: bool) -> Path:
    """Shared stage/verify refusals: never operate on anything disk-shaped.

    `dest` must stay a plain directory under the caller's own scratch tree.
    `.absolute()` (not `.resolve()`) is used throughout so an existing
    symlink component is caught by `find_symlink_component` instead of being
    silently followed before we get a chance to inspect it. A `..` component is
    refused outright: `.absolute()` leaves it in place and the lexical checks
    below would judge a different directory from the one the path names.
    """
    if '..' in dest.parts:
        raise Refusal('dest_has_parent_reference', str(dest))
    symlinked = find_symlink_component(dest)
    if symlinked == dest:
        raise Refusal('dest_is_symlink', str(dest))
    if symlinked is not None:
        raise Refusal('dest_path_has_symlink_component', str(symlinked))
    abs_dest = dest.absolute()
    if must_exist:
        if not abs_dest.exists():
            raise Refusal('dest_does_not_exist', str(abs_dest))
        if not abs_dest.is_dir():
            raise Refusal('dest_is_not_directory', str(abs_dest))
    else:
        if abs_dest.exists():
            if not abs_dest.is_dir():
                raise Refusal('dest_is_not_directory', str(abs_dest))
            if any(abs_dest.iterdir()):
                raise Refusal('dest_not_empty', str(abs_dest))
        else:
            parent = abs_dest.parent
            if not parent.is_dir():
                raise Refusal('dest_parent_missing', str(parent))
    probe_path = abs_dest if abs_dest.exists() else abs_dest.parent
    refuse_protected_location(probe_path)
    if os.path.ismount(probe_path):
        raise Refusal('dest_is_mount_point', str(probe_path))
    if probe_path.exists() and probe_path.parent.exists():
        if probe_path.stat().st_dev != probe_path.parent.stat().st_dev:
            raise Refusal('dest_different_filesystem_from_parent', str(probe_path))
    mounted = find_mount_point_ancestor(probe_path)
    if mounted is not None:
        raise Refusal('dest_ancestor_is_mount_point', str(mounted))
    return abs_dest


def write_file_deterministic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)
    os.utime(path, (DETERMINISTIC_EPOCH, DETERMINISTIC_EPOCH))


def finalize_permissions(dest: Path) -> None:
    """Normalize every path under `dest` to a fixed mode/mtime.

    Run once, after all content is written, so staged-tree metadata (not
    just file bytes) is identical between two independent `stage` runs for
    the same inputs; this is what "two stagings are byte-identical" means
    in practice for a directory, not just for each individual file.
    """
    for dirpath, dirnames, filenames in os.walk(dest):
        for name in filenames:
            p = Path(dirpath) / name
            os.chmod(p, FILE_MODE)
            os.utime(p, (DETERMINISTIC_EPOCH, DETERMINISTIC_EPOCH))
        os.chmod(dirpath, DIR_MODE)
        os.utime(dirpath, (DETERMINISTIC_EPOCH, DETERMINISTIC_EPOCH))


def build_readme_text() -> str:
    # Plain ASCII, written in our own words (not quoted from upstream slbounce
    # docs, to keep this generated file free of any third-party license text
    # that would need separate attribution). Asserted ASCII-only below.
    text = """SP11 boot media - operator instructions
========================================

This media does nothing automatically. No startup.nsh file is present on
it. You type every command yourself at the UEFI Shell prompt below.

Layout on this media:
  EFI\\BOOT\\BOOTAA64.EFI   aarch64 UEFI Shell. Firmware looks for exactly
                           this path on removable media and boots it
                           straight to a Shell> prompt.
  SP11\\SLBOUNCE.EFI       slbounce EFI runtime driver, built with
                           SLBOUNCE_ALWAYS_SWITCH=1 DEBUG=1.
  SP11\\SLTEST.EFI         slbounce self-test EFI application.
  SP11\\PROBE.EFI          optional EL2 probe. Present only if supplied
                           when this media was staged.
  SP11\\TCBLAUNCH.EXE      Microsoft-signed TCB binary. NOT included by
                           this tool. You must supply your own copy and
                           name it exactly SP11\\TCBLAUNCH.EXE; without it
                           this media is incomplete and nothing below will
                           work.
  TCBLAUNCH.EXE            (media root) an automatic copy of the same file,
                           placed here by the staging tool, not by you.
                           slbounce.efi's real driver only ever looks for
                           tcblaunch.exe at this media's root, never inside
                           SP11\\; see step 4 below.
  SP11\\MANIFEST.SHA256    sha256sum-style list of every file above. After
                           copying this tree to your stick, check it with
                           a plain checksum tool, for example from this
                           media's root:
                             sha256sum -c SP11\\MANIFEST.SHA256
                           (or the equivalent "shasum -c" on macOS).

Before you start:
  - This is experimental and can hang or reboot the machine. Save your
    work elsewhere first.
  - UEFI Secure Boot must be OFF in firmware setup. With Secure Boot on,
    neither the Shell nor SLBOUNCE.EFI nor SLTEST.EFI will run.
  - Nothing on this media ever touches your internal disk. Removing the
    stick and rebooting returns the machine to its normal boot order.

At the UEFI Shell prompt (it appears automatically after boot):
  1. Find which fsN: is this media: type "map -r", then try each fsN:
     and "ls" it; the right one lists EFI and SP11 at its root.
  2. Switch to it, for example:
       fs0:
  3. Run the self-test first. It only tests the EL2 switch; it does not
     change anything on disk:
       SP11\\SLTEST.EFI SP11\\TCBLAUNCH.EXE
     Use exactly that form (no "fs0:\\" prefix on the argument): slbounce
     opens its argument straight against the volume SLTEST.EFI itself was
     loaded from, not through the Shell's drive-letter mapping, so a
     "fs0:\\..." style argument will not resolve and the self-test will
     report "Opening file failed." instead of actually running.
     A green line drawn across the top of the screen means the switch to
     EL2 worked; the machine then intentionally hangs there. Power it off
     to continue.
     A hang or a reboot WITHOUT a green line means this exact
     tcblaunch.exe build, or this firmware, does not work with slbounce.
     Do not go on to step 4 with a tcblaunch.exe build that failed here.
  4. Only once step 3 has shown the green line, load the real driver:
       load SP11\\SLBOUNCE.EFI
     Then continue to whatever OS loader the rest of the plan's boot
     procedure directs you to start, from this same shell session.
     SLBOUNCE.EFI performs its EL2 switch only when that loader calls
     ExitBootServices; a failure there still means a hang or a reboot,
     same as in step 3. Unlike step 3, SLBOUNCE.EFI does not take an
     argument: it always opens plain "tcblaunch.exe" at this media's
     root, which is why the staging tool placed a copy there for you (see
     the TCBLAUNCH.EXE entry above). Do not delete that root copy even
     though SP11\\TCBLAUNCH.EXE looks redundant with it.

If anything hangs or reboots unexpectedly: hold the power button to turn
the machine off, then power it back on. Remove this media if you want the
machine to boot normally instead.
"""
    text.encode('ascii')  # defensive: catches any accidental non-ASCII edit
    return text


def build_manifest(staged: dict) -> str:
    """Manifest text for exactly the bytes `stage` wrote, given as {relative path: bytes}.

    Built from memory rather than by re-reading `dest`, so the manifest cannot describe other bytes than the ones
    that were validated and written. Like `verify`, it covers EFI/, SP11/ and the one root-level tcblaunch.exe
    duplicate (see TCBLAUNCH_ROOT_REL, which lives outside both directories and needs its own explicit entry rather
    than a whole-tree scan that would swallow stray root files) and nothing else.
    """
    entries = sorted((rel, sha256_bytes(data)) for rel, data in staged.items()
                     if rel != MANIFEST_REL and (rel.startswith(('EFI/', 'SP11/')) or rel == TCBLAUNCH_ROOT_REL))
    return ''.join(f'{digest}  {rel}\n' for rel, digest in entries)


def load_pins(pins_path):
    """The pins document, read strictly.

    The shell hash is the only hard pin on what boots on the unit, so a pins file that is missing, not a regular
    file, unreadable, malformed or without a valid shell entry stops the run. Reading it as 'no pins' would switch
    the pin off without a word; `--no-pins` is the explicit way to run unpinned.
    """
    if pins_path is None:
        raise Refusal('pins_required', 'no pins file given: pass --pins FILE, or --no-pins to skip the shell hash pin')
    path = Path(pins_path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise Refusal('pins_missing', str(path)) from None
    except OSError as exc:
        raise Refusal('pins_unreadable', f'{path}: {exc.strerror}') from exc
    if not stat.S_ISREG(info.st_mode):
        raise Refusal('pins_not_regular_file', str(path))
    if info.st_size > PINS_MAX_BYTES:
        raise Refusal('pins_too_large', f'{path}: {info.st_size} > {PINS_MAX_BYTES}')
    try:
        data = read_regular_bytes(path, info, PINS_MAX_BYTES)
    except Refusal as refusal:
        raise Refusal('pins_unreadable', f'{path}: {refusal.reason}') from refusal
    try:
        document = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise Refusal('pins_malformed', f'{path}: {exc}') from exc
    shell = document.get('shell') if isinstance(document, dict) else None
    if not (isinstance(shell, dict) and isinstance(shell.get('sha256'), str)
            and SHA256_RE.fullmatch(shell['sha256'])):
        raise Refusal('pins_shell_pin_invalid',
                       f'{path}: needs "shell": {{"sha256": <64 lower-case hex digits>}}')
    return document


def resolve_pins(args):
    """(pins document, 'pinned' | 'unchecked'); only an explicit `no_pins` runs without the shell hash pin."""
    if getattr(args, 'no_pins', False):
        return {}, 'unchecked'
    return load_pins(getattr(args, 'pins', None)), 'pinned'


def cmd_stage(args) -> dict:
    dest = check_destination_safety(args.dest, must_exist=False)
    pins, pin_mode = resolve_pins(args)

    required_paths = {'shell': args.shell, 'slbounce': args.slbounce, 'sltest': args.sltest}
    optional_paths = {}
    if args.probe is not None:
        optional_paths['probe'] = args.probe
    if args.tcblaunch is not None:
        optional_paths['tcblaunch'] = args.tcblaunch

    facts, blobs = {}, {}
    for role, path in {**required_paths, **optional_paths}.items():
        facts[role], blobs[role] = read_validated_input(path, role)

    advisories = []
    shell_pin = 'unchecked'
    if pin_mode == 'pinned':
        pinned = pins['shell']['sha256']
        if facts['shell']['sha256'] != pinned:
            raise Refusal('input_hash_pin_mismatch',
                           f'shell: got {facts["shell"]["sha256"]}, pinned {pinned}')
        shell_pin = 'match'
    else:
        advisories.append(f'shell: sha256 {facts["shell"]["sha256"]} was NOT checked against the pinned shell '
                           'hash (--no-pins); confirm it by hand before using this media')
    for role, pin_key in (('slbounce', 'slbounce_reference'), ('sltest', 'sltest_reference')):
        ref = pins.get(pin_key)
        if ref and facts[role]['sha256'] != ref['sha256']:
            advisories.append(f'{role}: sha256 {facts[role]["sha256"]} does not match the '
                               f'reference build {ref["sha256"]} in {pin_key} (informational '
                               'only: this input is a local, toolchain-dependent build)')

    dest.mkdir(parents=True, exist_ok=True)
    written = []
    staged = {}
    for role in {**required_paths, **optional_paths}:
        rel = LAYOUT[role]
        data = blobs[role]
        write_file_deterministic(dest / rel, data, FILE_MODE)
        staged[rel] = data
        written.append(rel)
        if role == 'tcblaunch':
            # See TCBLAUNCH_ROOT_REL: slbounce.efi's real driver path looks
            # for tcblaunch.exe at the media root, not under SP11/.
            write_file_deterministic(dest / TCBLAUNCH_ROOT_REL, data, FILE_MODE)
            staged[TCBLAUNCH_ROOT_REL] = data
            written.append(TCBLAUNCH_ROOT_REL)

    readme = build_readme_text().encode('ascii')
    write_file_deterministic(dest / README_REL, readme, FILE_MODE)
    staged[README_REL] = readme
    written.append(README_REL)

    manifest_text = build_manifest(staged)
    write_file_deterministic(dest / MANIFEST_REL, manifest_text.encode('ascii'), FILE_MODE)
    written.append(MANIFEST_REL)

    finalize_permissions(dest)

    missing = [r for r in REQUIRED_FOR_COMPLETE if r not in facts]
    status = 'STAGED_INCOMPLETE' if missing else 'STAGED_COMPLETE'
    return {
        'status': status,
        'dest': str(dest),
        'missing': missing,
        'present': {r: {k: v for k, v in f.items() if k != 'path'} for r, f in facts.items()},
        'files_written': sorted(written),
        'shell_pin': shell_pin,
        'advisories': advisories,
    }


def cmd_verify(args) -> dict:
    dest = check_destination_safety(args.dest, must_exist=True)
    pins, pin_mode = resolve_pins(args)

    missing = []
    present = {}
    for role, rel in LAYOUT.items():
        path = dest / rel
        if not path.exists():
            if role in REQUIRED_FOR_COMPLETE:
                missing.append(role)
            continue
        try:
            present[role] = validate_input_file(path, role)
        except Refusal as r:
            raise Refusal(r.reason, f'{role} ({rel}): {r.detail}') from r

    startup_hits = [str(p.relative_to(dest)) for p in dest.rglob('*')
                    if p.is_file() and p.name.lower() == 'startup.nsh']
    # The one startup.nsh an autorun-staged tree may hold is the script its own PLAN.TXT generates; autorun_findings
    # says whether this is it. A tree with no autorun files gets no new key in the result.
    autorun_report = autorun_findings(dest)
    if autorun_report is not None and not autorun_report['problems'] and autorun_report['script'] == 'present':
        startup_hits = [hit for hit in startup_hits if hit != autorun.SCRIPT_REL]

    manifest_path = dest / MANIFEST_REL
    manifest_ok = False
    manifest_mismatches = []
    if not manifest_path.is_file():
        manifest_mismatches.append('manifest_missing')
    else:
        listed = {}
        for lineno, line in enumerate(manifest_path.read_text().splitlines(), start=1):
            m = SHA256_LINE_RE.match(line)
            if not m:
                manifest_mismatches.append(f'manifest_unparseable_line:{lineno}')
                continue
            digest, name = m.group(1), m.group(2)
            if not is_safe_relative_path(name):
                manifest_mismatches.append(f'manifest_entry_path_traversal:{name}')
                continue
            listed[name] = digest
        for name, digest in sorted(listed.items()):
            target = dest / name
            if not target.is_file() or target.is_symlink():
                manifest_mismatches.append(f'manifest_listed_file_missing:{name}')
                continue
            actual = sha256_file(target)
            if actual != digest:
                manifest_mismatches.append(f'manifest_hash_mismatch:{name}')
        actual_files = set()
        for base in (dest / 'EFI', dest / 'SP11'):
            if base.is_dir():
                for p in base.rglob('*'):
                    if p.is_file():
                        rel = p.relative_to(dest).as_posix()
                        if rel != MANIFEST_REL:
                            actual_files.add(rel)
        # Mirrors build_manifest()'s narrow addition of the root-level
        # tcblaunch.exe duplicate (TCBLAUNCH_ROOT_REL): it must count as a
        # real, covered file here too, or a correct `stage` output would
        # always fail verify with a spurious manifest_unlisted_extra_file.
        if (dest / TCBLAUNCH_ROOT_REL).is_file():
            actual_files.add(TCBLAUNCH_ROOT_REL)
        for rel in sorted(actual_files - set(listed)):
            manifest_mismatches.append(f'manifest_unlisted_extra_file:{rel}')
        manifest_ok = not manifest_mismatches

    # Re-checks the pinned shell on the staged copy: the manifest only proves the tree matches itself, and a
    # replaced shell with a regenerated manifest would otherwise read as COMPLETE.
    shell_pin = 'unchecked' if pin_mode == 'unchecked' else 'absent'
    pin_mismatches = []
    if pin_mode == 'pinned' and 'shell' in present:
        pinned = pins['shell']['sha256']
        if present['shell']['sha256'] == pinned:
            shell_pin = 'match'
        else:
            shell_pin = 'mismatch'
            pin_mismatches.append(f'shell_pin_mismatch: got {present["shell"]["sha256"]}, pinned {pinned}')

    autorun_problems = autorun_report['problems'] if autorun_report is not None else []
    status = 'FAIL' if (startup_hits or manifest_mismatches or pin_mismatches or autorun_problems) else (
        'INCOMPLETE' if missing else 'COMPLETE')
    result = {
        'status': status,
        'dest': str(dest),
        'missing': missing,
        'present': {r: {k: v for k, v in f.items() if k != 'path'} for r, f in present.items()},
        'manifest_ok': manifest_ok,
        'manifest_mismatches': manifest_mismatches,
        'startup_nsh_found': startup_hits,
        'shell_pin': shell_pin,
        'pin_mismatches': pin_mismatches,
    }
    if autorun_report is not None:
        result['autorun'] = autorun_report
    return result


# ---------------------------------------------------------------------------------------------------------- autorun

BASE_RELS = frozenset(LAYOUT.values()) | {TCBLAUNCH_ROOT_REL, README_REL, MANIFEST_REL}
AUTORUN_TEXT_MAX = 1 << 16


def read_staged_bytes(path: Path, cap: int):
    """The bytes of a regular file inside a staged tree, read like an input (no symlink, bounded); None when absent."""
    symlinked = find_symlink_component(path)
    if symlinked is not None:
        raise Refusal('staged_path_has_symlink_component', str(symlinked))
    if not path.exists():
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise Refusal('staged_not_regular_file', str(path))
    if info.st_size > cap:
        raise Refusal('staged_file_too_large', f'{path}: {info.st_size} > {cap}')
    return read_regular_bytes(path, info, cap)


def parse_manifest(text: str) -> dict:
    """{relative path: sha256} from a manifest, read as strictly as verify reads it."""
    entries = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        found = SHA256_LINE_RE.match(line)
        if not found or not is_safe_relative_path(found.group(2)) or found.group(2) in entries:
            raise Refusal('manifest_unusable', f'line {lineno}')
        entries[found.group(2)] = found.group(1)
    return entries


def render_manifest(entries: dict) -> str:
    """The text build_manifest writes for the same entries."""
    return ''.join(f'{digest}  {rel}\n' for rel, digest in sorted(entries.items()))


def autorun_findings(dest: Path):
    """What autorun left in the staged tree `dest`: None when it has neither SP11/PLAN.TXT nor EFI/BOOT/startup.nsh (a
    default tree), else {'plan', 'steps', 'script', 'problems'}. Reads only, and returns its problems rather than raising.

    The script is accepted only if it is byte for byte what the plan in PLAN.TXT generates, PLAN.TXT is byte for byte
    what the tool writes, every file the plan names is staged with the planned hash, and README.TXT says autorun is on.
    """
    try:
        plan_bytes = read_staged_bytes(dest / autorun.PLAN_REL, AUTORUN_TEXT_MAX)
        script_bytes = read_staged_bytes(dest / autorun.SCRIPT_REL, AUTORUN_TEXT_MAX)
    except Refusal as refusal:
        return {'plan': None, 'steps': 0, 'script': 'unreadable', 'problems': [f'{refusal.reason}: {refusal.detail}']}
    if plan_bytes is None and script_bytes is None:
        return None
    problems = []
    report = {'plan': None, 'steps': 0, 'script': 'present' if script_bytes is not None else 'absent',
              'problems': problems}
    if plan_bytes is None:
        problems.append('autorun_plan_missing: a startup.nsh without SP11/PLAN.TXT cannot be checked against a plan')
        return report
    try:
        plan = autorun.parse_plan_text(plan_bytes)
        expected_script = autorun.render_script(plan)
        expected_readme = autorun.readme_for_plan(build_readme_text(), plan).encode('ascii')
    except autorun.PlanError as error:
        problems.append(f'{error.reason}: {error.detail}')
        return report
    report['plan'], report['steps'] = plan.name, len(plan.steps)
    for name, digest in sorted(plan.files().items()):
        role = 'slbounce' if name in autorun.SLBOUNCE_BUILDS else 'app'
        try:
            found = validate_input_file(dest / 'SP11' / name, role)
        except Refusal as refusal:
            problems.append(f'SP11/{name}: {refusal.reason}')
            continue
        if found['sha256'] != digest:
            problems.append(f'SP11/{name}: sha256 differs from SP11/PLAN.TXT')
    if script_bytes is not None:
        if script_bytes != expected_script:
            problems.append('EFI/BOOT/startup.nsh differs from the script SP11/PLAN.TXT generates')
        try:
            if read_staged_bytes(dest / README_REL, AUTORUN_TEXT_MAX) != expected_readme:
                problems.append('SP11/README.TXT does not say that autorun is enabled')
        except Refusal as refusal:
            problems.append(f'SP11/README.TXT: {refusal.reason}')
    return report


class Journal:
    """Undo log for the edits autorun makes to a staged tree, so a failure part-way leaves the tree as it was."""

    def __init__(self):
        self.undo = []

    def write(self, path: Path, data: bytes) -> None:
        self.undo.append((path, path.read_bytes() if path.exists() else None))
        write_file_deterministic(path, data, FILE_MODE)
        if sha256_file(path) != sha256_bytes(data):
            raise Refusal('copy_hash_mismatch', f'{path} does not read back as the bytes written')

    def delete(self, path: Path) -> None:
        self.undo.append((path, path.read_bytes()))
        path.unlink()

    def rollback(self) -> None:
        """Puts every touched file back. Modes and times are normalized afterwards by finalize_permissions."""
        for path, previous in reversed(self.undo):
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)


def marker_paths(dest: Path) -> list:
    """The run-time markers (SP11/DONE<k>.TXT) in a staged tree. A real stick gets them from the Shell, not from here."""
    directory = dest / 'SP11'
    if find_symlink_component(directory) is not None or not directory.is_dir():
        return []
    return [path for path in sorted(directory.iterdir())
            if autorun.MARKER_RE.fullmatch(path.name) and path.is_file() and not path.is_symlink()]


def autorun_stage(dest: Path, args) -> dict:
    hashes, sources = {}, {}
    for name, path, digest in args.file:
        if name in sources:
            raise autorun.PlanError('file_given_twice', name)
        sources[name], hashes[name] = Path(path), digest
    plan = autorun.build_plan(args.name, args.step, hashes)
    for name, path in sources.items():
        if path.name.upper() in autorun.BANNED_NAMES:
            raise autorun.PlanError('source_banned', f'{name}: {path}')
    if autorun_findings(dest) is not None:
        raise Refusal('autorun_already_present', 'the tree has a PLAN.TXT or a startup.nsh: run autorun --remove first')
    base = cmd_verify(args)
    if base['status'] != 'COMPLETE':
        raise Refusal('autorun_base_not_complete', json.dumps({k: base[k] for k in (
            'status', 'missing', 'manifest_mismatches', 'startup_nsh_found', 'pin_mismatches')}))
    fresh = {}
    for name in sorted(plan.files()):
        facts, data = read_validated_input(sources[name], 'slbounce' if name in autorun.SLBOUNCE_BUILDS else 'app')
        if facts['sha256'] != hashes[name]:
            raise Refusal('file_hash_mismatch', f'{name}: {sources[name]} is {facts["sha256"]}, expected {hashes[name]}')
        rel = f'SP11/{name}'
        if (dest / rel).exists():
            if sha256_file(dest / rel) != facts['sha256']:
                raise Refusal('autorun_would_replace_staged_file', f'{rel} is already staged with other bytes')
        else:
            fresh[rel] = data
    readme = read_staged_bytes(dest / README_REL, AUTORUN_TEXT_MAX)
    if readme != build_readme_text().encode('ascii'):
        raise Refusal('autorun_readme_unexpected', 'SP11/README.TXT is not the text this tool stages')
    script = autorun.render_script(plan)
    writes = dict(fresh)
    writes[autorun.PLAN_REL] = autorun.render_plan_text(plan)
    writes[README_REL] = autorun.readme_for_plan(readme.decode('ascii'), plan).encode('ascii')
    entries = parse_manifest(read_staged_bytes(dest / MANIFEST_REL, AUTORUN_TEXT_MAX).decode('utf-8'))
    entries.update({rel: sha256_bytes(data) for rel, data in writes.items()})
    entries[autorun.SCRIPT_REL] = sha256_bytes(script)
    writes[MANIFEST_REL] = render_manifest(entries).encode('utf-8')
    writes[autorun.SCRIPT_REL] = script          # last: a script never exists without its plan and its files
    journal = Journal()
    try:
        for rel, data in writes.items():
            journal.write(dest / rel, data)
        finalize_permissions(dest)
        after = cmd_verify(args)
        if after['status'] != 'COMPLETE':
            raise Refusal('autorun_result_not_verified', json.dumps(after.get('autorun') or after['manifest_mismatches']))
    except BaseException:
        journal.rollback()
        finalize_permissions(dest)
        raise
    return {
        'status': 'AUTORUN_STAGED',
        'dest': str(dest),
        'plan': plan.name,
        'steps': [{'step': k, 'load': step.slbounce, 'run': step.run_line} for k, step in enumerate(plan.steps, 1)],
        'files': dict(sorted(plan.files().items())),
        'files_written': sorted(writes),
        'files_already_staged': sorted(f'SP11/{name}' for name in plan.files() if f'SP11/{name}' not in fresh),
        'shell_pin': base['shell_pin'],
    }


def autorun_clear(dest: Path) -> dict:
    removed = []
    for path in marker_paths(dest):
        path.unlink()
        removed.append(f'SP11/{path.name}')
    return {'status': 'AUTORUN_CLEARED', 'dest': str(dest), 'markers_removed': removed}


def autorun_remove(dest: Path) -> dict:
    report = autorun_findings(dest)
    if report is None:
        return {'status': 'AUTORUN_NOT_PRESENT', 'dest': str(dest)}
    if report['problems']:
        raise Refusal('autorun_tree_not_valid', '; '.join(report['problems']))
    plan = autorun.parse_plan_text(read_staged_bytes(dest / autorun.PLAN_REL, AUTORUN_TEXT_MAX))
    owned = ({autorun.PLAN_REL, autorun.SCRIPT_REL} | {f'SP11/{name}' for name in plan.files()}) - BASE_RELS
    entries = parse_manifest(read_staged_bytes(dest / MANIFEST_REL, AUTORUN_TEXT_MAX).decode('utf-8'))
    base_readme = build_readme_text().encode('ascii')
    for rel in owned:
        entries.pop(rel, None)
    entries[README_REL] = sha256_bytes(base_readme)
    doomed = [dest / rel for rel in sorted(owned, key=lambda rel: rel != autorun.SCRIPT_REL) if (dest / rel).exists()]
    doomed += marker_paths(dest)
    journal = Journal()
    try:
        for path in doomed:
            journal.delete(path)
        journal.write(dest / README_REL, base_readme)
        journal.write(dest / MANIFEST_REL, render_manifest(entries).encode('utf-8'))
        finalize_permissions(dest)
        after = cmd_verify(argparse.Namespace(dest=dest, pins=None, no_pins=True))
        if after['status'] == 'FAIL':
            raise Refusal('autorun_remove_not_verified', json.dumps(after['manifest_mismatches']))
    except BaseException:
        journal.rollback()
        finalize_permissions(dest)
        raise
    return {'status': 'AUTORUN_REMOVED', 'dest': str(dest),
            'files_removed': sorted(path.relative_to(dest).as_posix() for path in doomed)}


def cmd_autorun(args) -> dict:
    dest = check_destination_safety(args.dest, must_exist=True)
    try:
        if args.clear or args.remove:
            if args.name or args.file:
                raise Refusal('autorun_options_conflict', '--name and --file describe a plan, not --clear or --remove')
            return autorun_clear(dest) if args.clear else autorun_remove(dest)
        return autorun_stage(dest, args)
    except autorun.PlanError as error:
        raise Refusal(error.reason, error.detail) from error


def add_pin_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--pins', type=Path, default=DEFAULT_PINS,
                       help='pins JSON (default: tools/sp11-boot-media-pins.json); a missing or unreadable file '
                            'is refused')
    group.add_argument('--no-pins', action='store_true',
                       help='skip the shell hash pin; the result then reports shell_pin: unchecked')


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)

    stage = sub.add_parser('stage', help='Assemble the SP11 boot-media directory tree.')
    stage.add_argument('--shell', type=Path, required=True, help='aarch64 UEFI shell .efi')
    stage.add_argument('--slbounce', type=Path, required=True, help='slbounce-always.efi')
    stage.add_argument('--sltest', type=Path, required=True, help='sltest.efi')
    stage.add_argument('--probe', type=Path, default=None, help='optional sp11-el2-probe.efi')
    stage.add_argument('--tcblaunch', type=Path, default=None,
                        help='optional operator-supplied tcblaunch.exe')
    stage.add_argument('--dest', type=Path, required=True, help='empty scratch directory')
    add_pin_options(stage)

    verify = sub.add_parser('verify', help='Verify a previously staged directory tree.')
    verify.add_argument('--dest', type=Path, required=True, help='directory to verify')
    verify.add_argument('--allow-incomplete', action='store_true',
                         help='exit 0 even if required items (e.g. tcblaunch) are missing')
    add_pin_options(verify)

    auto = sub.add_parser('autorun', help='Opt in: make a staged tree run one plan step per boot from startup.nsh.')
    auto.add_argument('--dest', type=Path, required=True,
                      help='a directory `stage` made and `verify` accepts (never a mounted stick)')
    action = auto.add_mutually_exclusive_group(required=True)
    action.add_argument('--step', action='append', metavar='"SLBOUNCE.EFI APP.EFI [ARG]"',
                        help='one plan step, in run order: an slbounce build to `load` once, then one application '
                             'and its arguments (only the names and arguments sp11_autorun.py lists are accepted); '
                             'repeat for each step')
    action.add_argument('--clear', action='store_true',
                        help='delete the SP11/DONE<k>.TXT markers, so the plan starts again at step 1')
    action.add_argument('--remove', action='store_true',
                        help='delete startup.nsh, PLAN.TXT, the markers and the files the plan added, and restore '
                             'the default README.TXT and manifest')
    auto.add_argument('--name', help='plan name, shown by the script and in PLAN.TXT (with --step)')
    auto.add_argument('--file', nargs=3, action='append', default=[], metavar=('NAME', 'PATH', 'SHA256'),
                      help='the file SP11/NAME comes from PATH, which must hash to SHA256; one per file the steps '
                           'use (with --step)')
    add_pin_options(auto)

    return ap


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    try:
        if args.command == 'stage':
            result = cmd_stage(args)
        elif args.command == 'autorun':
            result = cmd_autorun(args)
        else:
            result = cmd_verify(args)
    except Refusal as r:
        print(json.dumps({'status': 'REFUSED', 'reason': r.reason, 'detail': r.detail}, indent=2))
        return 1

    print(json.dumps(result, indent=2))
    if args.command in ('stage', 'autorun'):
        return 0
    if result['status'] == 'FAIL':
        return 1
    if result['status'] == 'INCOMPLETE' and not args.allow_incomplete:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
