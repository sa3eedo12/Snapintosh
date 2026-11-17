# SPDX-License-Identifier: MIT
"""Plan model and startup.nsh generator for the opt-in autorun of the SP11 boot stick (host only, no I/O).

The stick's UEFI Shell runs EFI\\BOOT\\startup.nsh by itself after its own countdown, so one experiment can start
with nobody typing. An experiment is a "step": `load` one slbounce build, once, then run one application. Nothing
can run after the application calls ExitBootServices, and a hung step is recovered by power-cycling, so the script
runs ONE step per boot and writes that step's marker file (SP11\\DONE<k>.TXT) BEFORE the step starts.

What can be said is decided by tables, not by free text. A step names an slbounce build from SLBOUNCE_BUILDS and an
application from APP_ARGUMENTS with one of its listed argument vectors; anything else is refused with a reason, and
the names in BANNED_NAMES and the arguments in BANNED_ARGUMENTS are refused first so the reason says what they are.
The script is built from fixed lines with only those validated names, the step numbers and the plan name filled in,
and script_problems() then checks the result against a whitelist of line forms, independently of the template: a
template slip cannot put `del`, a banned argument or a second `load` into a script that gets as far as the tool's
output. verify recomputes the script from SP11\\PLAN.TXT and refuses any difference.

Data files are what an application reads at run time and the script never names. Only the payload runner reads any:
Q1N1-PAYLOAD.EFI loads \\SP11\\PAYLOAD.MAN, checks the size and SHA-256 of each file the manifest lists, and calls
file 0 at EL2 (docs/SP11-PAYLOAD.md in the q1n1 fork). A boot is expensive, so the manifest is parsed here, when the
tree is staged: a plan is refused unless the manifest is well formed and lists exactly the staged data files with
their sizes and hashes. The data files are recorded in SP11\\PLAN.TXT and re-checked by verify; the script does not
change for them.

The retention probe's plan `retention-m10` adds a second kind of step, `RETENTION-EXTRACT <three page addresses>` (docs/SP11-RETENTION-PROBE.md). It
loads and runs nothing: its block of the script dumps the three RAM pages to SP11\\RET<k>.TXT with the Shell's `dmem` as the first commands of the boot
that change anything, then appends the memory map, the Shell's date and time and an end line, and only then writes its marker; it never overwrites a
result. Such a step belongs only to the plans `retention-m10` and `retention-m10-auto` (the payload runner's step, then the extract), and such a plan
stages exactly PAYLOAD.MAN, RETPROBE.BIN and RETPATH.DAT as data: the runner calls every .BIN file its manifest lists. The stager records the time of the
staging in PLAN.TXT (`staged:`), which the verdict tool compares with the unit's clock to tell a result left by an earlier run. `retention-m10-auto`, with
no manual restart, is refused unless the operator authorised the stick first in the boot order (build_plan's stick_first_authorised,
`--stick-first-authorised`). That refusal is real in the builder and the stager, but the `unattended:` line of PLAN.TXT is a record of the operator's
decision and not an interlock: nothing here touches the boot order, and any PLAN.TXT that carries the line and parses is the same plan.

Evidence (see docs/RUNNING.md): the countdown, the search for startup.nsh in the directory the Shell was loaded from,
the `homefilesystem` variable, `if exist`, `goto`, `stall`, `@echo` and the `echo ... >a` redirect were OBSERVED with
the pinned Shell under QEMU (tools/test-sp11-autorun-qemu.py). On the unit's own Shell the countdown, the search, the
`homefilesystem` variable, `echo`, `stall`, the redirect with its `if not exist` check, and the `load` and the run were
OBSERVED once (2026-11-14, an operator's photograph); ESC at the countdown and a second boot are not yet.
"""
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import re
import struct

SCRIPT_REL = 'EFI/BOOT/startup.nsh'
PLAN_REL = 'SP11/PLAN.TXT'
MARKER_FORMAT = 'DONE{}.TXT'
MARKER_RE = re.compile(r'DONE[0-9]{1,2}\.TXT', re.IGNORECASE)
MAX_STEPS = 8
PAUSE_MICROSECONDS = 5000000

# The retention probe (docs/SP11-RETENTION-PROBE.md): a plan of two steps, the payload runner with the probe and then an
# extract step that dumps three RAM pages to a result file with the Shell's own `dmem`, before any other command of that
# boot. The extract step is a different kind of step: it loads and runs nothing.
EXTRACT_KEYWORD = 'RETENTION-EXTRACT'
EXTRACT_END = 'RETENTION-EXTRACT-END'
EXTRACT_PAGES = 3
EXTRACT_PAGE_BYTES = 0x1000
EXTRACT_ADDRESS_RE = re.compile(r'0x[1-9A-F][0-9A-F]{0,11}')     # canonical, so that PLAN.TXT has one spelling; 48 bits at most
RESULT_FORMAT = 'RET{}.TXT'
RESULT_RE = re.compile(r'RET[0-9]{1,2}\.TXT', re.IGNORECASE)
RETENTION_PLAN = 'retention-m10'
RETENTION_UNATTENDED_PLAN = 'retention-m10-auto'
RETENTION_PLANS = (RETENTION_PLAN, RETENTION_UNATTENDED_PLAN)
# The data files of a retention plan and no others: the runner calls every file of the manifest whose name ends in .BIN, so a staged
# OTHER.BIN would run a second payload at EL2 in the same boot (RETENTION_DATA_NAMES, after MANIFEST_NAME below, adds the manifest).
RETENTION_PAYLOAD, RETENTION_CONFIG = 'RETPROBE.BIN', 'RETPATH.DAT'
STAGED_FORMAT = '%Y-%m-%d %H:%M:%S'
UNATTENDED_LINE = 'unattended: stick first in the boot order, authorised by the operator (this tool changes no firmware setting)'

# The reviewed slbounce builds, all from the slbounce fork. `load` of one of these is the first half of a step.
SLBOUNCE_BUILDS = ('SLBOUNCE.EFI', 'SLBOUNCE-DEVHE.EFI', 'SLBOUNCE-DEVHE-MARK.EFI', 'SLBOUNCE-DRYRUN.EFI')
# Applications and the only argument vectors each may be given. q1n1 takes exactly `--el2` or nothing (the no-argument
# run is its preflight, which never leaves boot services). Q1N1-STK.EFI is the second q1n1 image, the read-only USB
# survey that the q1n1 fork builds from the same tree behind a compile-time gate (docs/SP11-STK-SURVEY.md there). It
# runs only in the `--el2` flow and has no preflight-only use, so it takes exactly `--el2` and the no-argument form is
# refused. Q1N1-PAYLOAD.EFI is the third q1n1 image, the stick payload runner (docs/SP11-PAYLOAD.md in the q1n1 fork):
# the same `--el2` flow after one `load`, no preflight-only use, so exactly `--el2`; it also reads data files (below).
# SLTEST.EFI is not listed: it needs an explicit path argument and the operator procedure runs it without a
# preceding `load`.
APP_ARGUMENTS = {
    'Q1N1.EFI': ((), ('--el2',)),
    'Q1N1-STK.EFI': (('--el2',),),
    'Q1N1-PAYLOAD.EFI': (('--el2',),),
    'TESTS.EFI': ((),),
    'TESTS-DECL.EFI': ((),),
    'PROBE.EFI': ((),),
}
# A scratch test-only build outside the reviewed set, and q1n1 modes that drive hardware beyond the EL2 entry run.
# They are not in the tables above, so they are refused anyway; listing them gives the refusal a reason.
BANNED_NAMES = ('SLBOUNCE-OLD-NOSWEEP.EFI',)
BANNED_ARGUMENTS = ('--boot', '--uart-proxy', '--uart', '--fault-test')
_BANNED_TOKENS = BANNED_ARGUMENTS + tuple(name.lower() for name in BANNED_NAMES)
NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,23}')
SHA256_RE = re.compile(r'[0-9a-f]{64}')

# Data files, which an application reads from the stick and the script never names. RUNNER is the one application
# that reads any: it opens \SP11\PAYLOAD.MAN on the volume it started from and every file the manifest lists.
RUNNER = 'Q1N1-PAYLOAD.EFI'
MANIFEST_NAME = 'PAYLOAD.MAN'
RETENTION_DATA_NAMES = (MANIFEST_NAME, RETENTION_PAYLOAD, RETENTION_CONFIG)
MAX_PAYLOAD_FILES = 5           # slbounce takes eight declared ranges and the runner's own allocations use three
DATA_MAX_BYTES = 256 << 20      # slbounce splits a declared range into at most 256 chunks of 1 MiB
DATA_NAME_RE = re.compile(r'[A-Z0-9_-]{1,8}\.[A-Z0-9_-]{1,3}')    # capitals and 8.3, so no long-name alias is in question
# Names under SP11\ that the tool or the base tree owns and that are not application names: a data file may not take them.
RESERVED_DATA_NAMES = ('PLAN.TXT', 'README.TXT', 'STARTUP.NSH', 'SLTEST.EFI')
# The Q1N1PLD1 manifest, from docs/SP11-PAYLOAD.md and tools/preload.py in the q1n1 fork, written out here and not
# imported: a header of four little-endian u64 (magic, version, count, entry size), then per file a u64 size, the
# 32-byte SHA-256 and a 256-byte NUL-padded path.
MANIFEST_MAGIC = 0x31444C50314E3151     # "Q1N1PLD1"
_HEADER = struct.Struct('<4Q')
_ENTRY = struct.Struct('<Q32s256s')
_NAME83_RE = re.compile(r'[A-Za-z0-9_-]{1,8}\.[A-Za-z0-9_-]{1,3}')


class PlanError(Exception):
    def __init__(self, reason: str, detail: str = ''):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Step:
    slbounce: str
    app: str
    args: tuple
    slbounce_sha256: str
    app_sha256: str

    @property
    def run_line(self) -> str:
        return ' '.join((self.app,) + self.args)


@dataclass(frozen=True)
class ExtractStep:
    """The step that dumps three RAM pages to SP11\\RET<k>.TXT. It has no slbounce build and no application, so the two
    hashes are empty and Plan.files() leaves it out; run_line is its spelling in the plan and in PLAN.TXT."""
    pages: tuple            # three distinct 4 KiB aligned physical addresses
    slbounce: str = ''
    app: str = ''
    args: tuple = ()
    slbounce_sha256: str = ''
    app_sha256: str = ''

    @property
    def run_line(self) -> str:
        return ' '.join((EXTRACT_KEYWORD, *(f'0x{page:X}' for page in self.pages)))


@dataclass(frozen=True)
class DataFile:
    name: str       # under SP11\, capitals and 8.3
    size: int       # of the staged bytes
    sha256: str


@dataclass(frozen=True)
class ManifestEntry:
    size: int
    sha256: str
    path: str       # as the manifest writes it, \SP11\NAME


@dataclass(frozen=True)
class Plan:
    name: str
    steps: tuple
    data: tuple = ()        # DataFile entries, the manifest first; empty unless a step runs RUNNER
    stick_first_authorised: bool = False       # only the unattended retention plan; recorded in PLAN.TXT
    staged: str = ''        # only a retention plan: the staging computer's local time (STAGED_FORMAT), recorded in PLAN.TXT by stamp_staging

    def files(self) -> dict:
        """File name under SP11\\ -> SHA-256, one entry per distinct name (a name always has one hash)."""
        found = {}
        for step in self.steps:
            if isinstance(step, ExtractStep):
                continue
            for name, digest in ((step.slbounce, step.slbounce_sha256), (step.app, step.app_sha256)):
                if found.setdefault(name, digest) != digest:
                    raise PlanError('plan_inconsistent_hash', name)
        return found


def parse_step(text: str):
    """(slbounce, app, args) from "SLBOUNCE-X.EFI APP.EFI [ARG ...]", every word checked against the tables."""
    words = text.split()
    if len(words) < 2:
        raise PlanError('step_malformed', f'{text!r}: a step is an slbounce build, an application and its arguments')
    slbounce, app, args = words[0], words[1], tuple(words[2:])
    for role, name, table in (('slbounce', slbounce, SLBOUNCE_BUILDS), ('app', app, APP_ARGUMENTS)):
        if name.upper() in BANNED_NAMES:
            raise PlanError(f'step_{role}_banned', name)
        if name not in table:
            raise PlanError(f'step_{role}_not_allowed', f'{name}: allowed are {", ".join(table)} (upper case)')
    for arg in args:
        if arg.lower().split('=')[0] in BANNED_ARGUMENTS:
            raise PlanError('step_argument_banned', arg)
    if args not in APP_ARGUMENTS[app]:
        allowed = ' | '.join(' '.join(v) or '(none)' for v in APP_ARGUMENTS[app])
        raise PlanError('step_arguments_not_allowed', f'{app} {" ".join(args)}: allowed are {allowed}')
    return slbounce, app, args


def is_extract_text(text: str) -> bool:
    return text.split()[:1] == [EXTRACT_KEYWORD]


def parse_extract(text: str) -> ExtractStep:
    """The ExtractStep of "RETENTION-EXTRACT 0xA 0xB 0xC": three canonical hexadecimal page addresses, distinct, 4 KiB aligned."""
    words = text.split()
    if words[:1] != [EXTRACT_KEYWORD] or len(words) != EXTRACT_PAGES + 1:
        raise PlanError('extract_malformed', f'{text!r}: {EXTRACT_KEYWORD} and {EXTRACT_PAGES} page addresses')
    pages = []
    for word in words[1:]:
        if not EXTRACT_ADDRESS_RE.fullmatch(word):
            raise PlanError('extract_page_malformed', f'{word!r}: 0x and 1 to 12 upper-case hexadecimal digits, no leading zero')
        pages.append(int(word, 16))
    if any(page % EXTRACT_PAGE_BYTES for page in pages):
        raise PlanError('extract_page_unaligned', ', '.join(f'0x{page:X}' for page in pages))
    if len(set(pages)) != len(pages):
        raise PlanError('extract_pages_not_distinct', ', '.join(f'0x{page:X}' for page in pages))
    return ExtractStep(tuple(pages))


def _retention_shape_problems(name: str, parsed: list, stick_first_authorised: bool) -> None:
    """The rules of the retention plans, raised as PlanError. An extract step belongs only to a plan named for it, and such a
    plan is exactly the payload runner's step and then the extract step: the extract is step 2, so that it writes RET2.TXT."""
    extracts = [item for item in parsed if isinstance(item, ExtractStep)]
    if name not in RETENTION_PLANS:
        if extracts:
            raise PlanError('extract_outside_retention_plan', f'{EXTRACT_KEYWORD} is only a step of the plans {", ".join(RETENTION_PLANS)}')
        if stick_first_authorised:
            raise PlanError('stick_first_flag_not_for_this_plan', f'only the plan {RETENTION_UNATTENDED_PLAN} records that the operator authorised it')
        return
    if len(parsed) != 2 or isinstance(parsed[0], ExtractStep) or parsed[0][1] != RUNNER or not isinstance(parsed[1], ExtractStep):
        raise PlanError('retention_plan_shape', f'{name} is two steps: a step that runs {RUNNER}, then {EXTRACT_KEYWORD} with its three pages')
    if name == RETENTION_UNATTENDED_PLAN and not stick_first_authorised:
        raise PlanError('unattended_not_authorised', f'{name} has no manual restart, so it needs the stick first in the boot order, a persistent '
                                                     'firmware setting that only the operator may decide: pass --stick-first-authorised once they did')
    if name == RETENTION_PLAN and stick_first_authorised:
        raise PlanError('stick_first_flag_not_for_this_plan', f'{name} is the plan with the operator starting the stick twice; the flag belongs to {RETENTION_UNATTENDED_PLAN}')


def build_plan(name: str, step_texts, hashes: dict, stick_first_authorised: bool = False) -> Plan:
    """A validated plan. `hashes` maps every file name the steps use to its expected SHA-256, and nothing else."""
    if not NAME_RE.fullmatch(name or '') or any(token in name.lower() for token in _BANNED_TOKENS):
        raise PlanError('plan_name_invalid', f'{name!r}: 1 to 24 of letters, digits, ".", "_" and "-", starting with a letter or digit, '
                                              'and naming no banned argument or file')
    if not step_texts:
        raise PlanError('plan_has_no_steps')
    if len(step_texts) > MAX_STEPS:
        raise PlanError('plan_has_too_many_steps', f'{len(step_texts)} > {MAX_STEPS}')
    parsed = [parse_extract(text) if is_extract_text(text) else parse_step(text) for text in step_texts]
    _retention_shape_problems(name, parsed, stick_first_authorised)
    used = {file_name for step in parsed if not isinstance(step, ExtractStep) for file_name in step[:2]}
    for given in hashes:
        if given.upper() in BANNED_NAMES:
            raise PlanError('file_banned', given)
        if given not in used:
            raise PlanError('file_unused', f'{given} is named by no step')
    for needed in sorted(used):
        digest = hashes.get(needed)
        if digest is None:
            raise PlanError('file_hash_missing', f'{needed}: give --file {needed} PATH SHA256')
        if not SHA256_RE.fullmatch(digest):
            raise PlanError('file_hash_malformed', f'{needed}: {digest!r} is not 64 lower-case hex digits')
    plan = Plan(name, tuple(step if isinstance(step, ExtractStep) else Step(step[0], step[1], step[2], hashes[step[0]], hashes[step[1]])
                            for step in parsed), stick_first_authorised=stick_first_authorised)
    plan.files()
    return plan


# ------------------------------------------------------------------------------------------------ data files

def check_data_names(plan: Plan, hashes: dict) -> None:
    """The rules for the data files that need no file, so that a plan breaking one is refused before anything is
    read. `hashes` is {NAME: SHA-256} as `--data NAME PATH SHA256` gives them. Only RUNNER reads data files and it
    can do nothing without its manifest, so a plan has data files exactly when a step runs RUNNER."""
    runner = any(step.app == RUNNER for step in plan.steps)
    if hashes and not runner:
        raise PlanError('data_without_runner', f'{", ".join(sorted(hashes))}: no step runs {RUNNER}, the only '
                                               'application that reads data files')
    if runner and MANIFEST_NAME not in hashes:
        raise PlanError('data_manifest_missing', f'a step runs {RUNNER}: give --data {MANIFEST_NAME} PATH SHA256')
    taken = {*plan.files(), *SLBOUNCE_BUILDS, *APP_ARGUMENTS, *RESERVED_DATA_NAMES}
    for name, digest in sorted(hashes.items()):
        if not DATA_NAME_RE.fullmatch(name):
            raise PlanError('data_name_invalid', f'{name!r}: 1 to 8 capitals, digits, "_" or "-", a dot, and 1 to 3 more')
        if RESULT_RE.fullmatch(name):
            raise PlanError('data_name_collides', f'{name} is a result file name of the retention extract step')
        if name in taken or MARKER_RE.fullmatch(name):
            raise PlanError('data_name_collides', f'{name} is already a name of the plan, the tables, the tool or the base tree')
        if not SHA256_RE.fullmatch(digest):
            raise PlanError('data_hash_malformed', f'{name}: {digest!r} is not 64 lower-case hex digits')
    if len(hashes) - (MANIFEST_NAME in hashes) > MAX_PAYLOAD_FILES:
        raise PlanError('data_too_many_files', f'{len(hashes) - 1} files besides {MANIFEST_NAME}, at most {MAX_PAYLOAD_FILES}')
    if plan.name in RETENTION_PLANS and set(hashes) != set(RETENTION_DATA_NAMES):
        raise PlanError('retention_data_names', f'{plan.name} stages exactly {", ".join(RETENTION_DATA_NAMES)} and not {", ".join(sorted(hashes))}: the runner '
                                                'calls every .BIN file its manifest lists, so any other would run in the same boot')


def attach_data(plan: Plan, files, manifest: bytes = None) -> Plan:
    """`plan` with its data files, DataFile entries that hold the size and hash of the bytes that are staged. The rules
    of check_data_names apply; given `manifest` (the bytes of PAYLOAD.MAN) it is also parsed and compared with the
    files. A plan with neither data files nor RUNNER comes back as it was."""
    files = tuple(files)
    names = [item.name for item in files]
    if len(set(names)) != len(names):
        raise PlanError('data_given_twice', ', '.join(sorted({name for name in names if names.count(name) > 1})))
    check_data_names(plan, {item.name: item.sha256 for item in files})
    for item in files:
        if not 1 <= item.size <= DATA_MAX_BYTES:
            raise PlanError('data_size_out_of_range', f'{item.name}: {item.size} bytes, not 1 to {DATA_MAX_BYTES}')
    attached = replace(plan, data=tuple(sorted(files, key=lambda item: (item.name != MANIFEST_NAME, item.name))))
    if manifest is not None:
        check_manifest(attached, manifest)
    return attached


def _manifest_path(raw: bytes, index: int) -> str:
    """The path of one manifest entry: printable ASCII and NUL padded, absolute and canonical (the rules of the A16
    preload, tools/preload.py, which the runner shares), then \\SP11\\NAME and never the manifest itself (the runner's
    rules). The runner accepts any name and depth below \\SP11\\; this tool stages flat 8.3 names, as the q1n1
    manifest tool writes them, and refuses the rest. Directory and names compare without regard to case, as there."""
    text, terminator, padding = raw.partition(b'\0')
    if not terminator:
        raise PlanError('manifest_path_unterminated', f'entry {index}: no NUL in the 256-byte path field')
    if padding.strip(b'\0'):
        raise PlanError('manifest_path_padding', f'entry {index}: bytes after the terminating NUL')
    if not text or any(not 32 <= byte <= 126 for byte in text):
        raise PlanError('manifest_path_not_printable', f'entry {index}: empty, or not printable ASCII')
    path = text.decode('ascii')
    parts = path[1:].split('\\')
    if not path.startswith('\\') or any(char in path for char in '/:*?"<>|') or any(part in ('', '.', '..') for part in parts):
        raise PlanError('manifest_path_not_canonical', f'entry {index}: {path}')
    if len(parts) != 2 or parts[0].upper() != 'SP11':
        raise PlanError('manifest_path_outside_sp11', f'entry {index}: {path} is not \\SP11\\NAME')
    if not _NAME83_RE.fullmatch(parts[1]):
        raise PlanError('manifest_path_not_83', f'entry {index}: {path}')
    if parts[1].upper() == MANIFEST_NAME:
        raise PlanError('manifest_lists_itself', f'entry {index}: {path}')
    return path


def render_payload_manifest(files) -> bytes:
    """The Q1N1PLD1 manifest that lists `files` ((NAME, bytes) pairs, capitals and 8.3) as \\SP11\\NAME in the order given: the bytes that
    tools/sp11_payload_manifest.py of the q1n1 fork writes for the same files, and what parse_payload_manifest reads back. The first file is
    the payload the runner calls (a name that ends in .BIN), every other name that ends in .BIN would be called after it."""
    files = tuple(files)
    blob = _HEADER.pack(MANIFEST_MAGIC, 1, len(files), _ENTRY.size) + b''.join(
        _ENTRY.pack(len(data), hashlib.sha256(data).digest(), f'\\SP11\\{name}'.encode('ascii')) for name, data in files)
    parse_payload_manifest(blob)
    return blob


def parse_payload_manifest(blob: bytes) -> tuple:
    """The ManifestEntry list of a Q1N1PLD1 manifest, or PlanError for anything the payload runner would refuse (its
    header, 1 to 5 entries, the exact length, each path, sizes of 1 byte to 256 MiB, two entries of one path in any
    letter case) and for the paths this tool does not stage (see _manifest_path)."""
    if len(blob) < _HEADER.size:
        raise PlanError('manifest_bad_length', f'{len(blob)} bytes is shorter than the {_HEADER.size}-byte header')
    magic, version, count, entry_size = _HEADER.unpack_from(blob)
    if magic != MANIFEST_MAGIC:
        raise PlanError('manifest_bad_magic', f'{magic:#018x}')
    if version != 1:
        raise PlanError('manifest_bad_version', f'{version}, not 1')
    if entry_size != _ENTRY.size:
        raise PlanError('manifest_bad_entry_size', f'{entry_size}, not {_ENTRY.size}')
    if not 1 <= count <= MAX_PAYLOAD_FILES:
        raise PlanError('manifest_bad_count', f'{count} entries, not 1 to {MAX_PAYLOAD_FILES}')
    if len(blob) != _HEADER.size + count * _ENTRY.size:
        raise PlanError('manifest_bad_length', f'{len(blob)} bytes for {count} entries, not {_HEADER.size + count * _ENTRY.size}')
    entries, seen = [], set()
    for index in range(count):
        size, digest, raw = _ENTRY.unpack_from(blob, _HEADER.size + index * _ENTRY.size)
        path = _manifest_path(raw, index)
        if not 1 <= size <= DATA_MAX_BYTES:
            raise PlanError('manifest_size_out_of_range', f'entry {index}: {size} bytes, not 1 to {DATA_MAX_BYTES}')
        if path.lower() in seen:
            raise PlanError('manifest_duplicate_entry', f'entry {index}: {path}')
        seen.add(path.lower())
        entries.append(ManifestEntry(size, digest.hex(), path))
    return tuple(entries)


def check_manifest(plan: Plan, blob: bytes) -> None:
    """PlanError unless `blob` is a manifest the runner would accept whose entries are exactly the plan's data files
    other than the manifest: each entry is one of them, with its size and SHA-256, and none of them is left out. A
    name is compared as the staged file has it, in capitals."""
    staged = {item.name: item for item in plan.data if item.name != MANIFEST_NAME}
    listed = set()
    for entry in parse_payload_manifest(blob):
        item = staged.get(entry.path.split('\\')[2])
        if item is None:
            raise PlanError('manifest_entry_not_staged', f'{entry.path} is not one of the staged data files')
        if entry.size != item.size:
            raise PlanError('manifest_entry_size_mismatch', f'{entry.path}: the manifest says {entry.size} bytes, '
                                                           f'the staged file has {item.size}')
        if entry.sha256 != item.sha256:
            raise PlanError('manifest_entry_hash_mismatch', f'{entry.path}: the manifest says {entry.sha256}, '
                                                           f'the staged file is {item.sha256}')
        listed.add(item.name)
    unlisted = sorted(set(staged) - listed)
    if unlisted:
        raise PlanError('data_file_not_in_manifest', ', '.join(unlisted))


# ------------------------------------------------------------------------------------------------ the script

def _say(text: str, indent: str = '') -> str:
    # Quoted: the Shell's echo takes an unquoted word that starts with "-" for one of its own flags ("--el2" fails).
    return f'{indent}echo "{text}"'


def _refuse(message: str) -> list:
    return [_say(f'AUTORUN REFUSED: {message}, nothing was run', '  '), '  goto end', 'endif']


def _step_lines(plan: Plan, k: int) -> list:
    step, count = plan.steps[k - 1], len(plan.steps)
    load, run = f'SP11\\{step.slbounce}', f'SP11\\{step.run_line}'
    marker = f'SP11\\{MARKER_FORMAT.format(k)}'
    follow = f'step{k + 1}' if k < count else 'done'
    lines = [f':step{k}', f'if exist {marker} then', f'  goto {follow}', 'endif',
             _say(f'AUTORUN {plan.name} STEP {k} OF {count}'),
             _say(f'CMD1: load {load}'), _say(f'CMD2: {run}')]
    for needed in (f'SP11\\{step.slbounce}', f'SP11\\{step.app}'):
        lines += [f'if not exist {needed} then', *_refuse(f'{needed} is missing')]
    lines += [_say(f'Writing {marker} in 5 seconds, then the two commands run. Power off now to cancel.'),
              f'stall {PAUSE_MICROSECONDS}',
              f'{_say(f"AUTORUN {plan.name} STEP {k} STARTED")} >a {marker}',
              # A write-protected stick is stopped by the Shell at the redirect itself (OBSERVED under QEMU); this
              # check is for a volume that accepts the redirect without creating the file (not observed).
              f'if not exist {marker} then', *_refuse(f'cannot write {marker}'),
              '@echo -on', f'load {load}', run, 'goto end']
    return lines


def _extract_lines(plan: Plan, k: int) -> list:
    """The block of an extract step. It skips ahead when its marker exists, refuses to overwrite a result, and then the three `dmem`
    dumps are the first commands that change anything: no stall, no load and no run, nothing that waits or runs code, comes before
    them, because the dump is the experiment and the pages are only as good as the time they are read at. The memory map of that boot
    follows (it says whether firmware took a page for itself), then the Shell's `date` and `time` (the unit's clock at the extraction: the
    verdict tool compares it with the staging time of PLAN.TXT, to tell a result left by an earlier run), then the line that ends the
    file, and only then the marker: a result that has no end line is a truncated one, and a marker never claims a dump that was not
    finished. The console lines come last."""
    step, count = plan.steps[k - 1], len(plan.steps)
    result, marker = f'SP11\\{RESULT_FORMAT.format(k)}', f'SP11\\{MARKER_FORMAT.format(k)}'
    follow = f'step{k + 1}' if k < count else 'done'
    lines = [':step' + str(k), 'if exist ' + marker + ' then', f'  goto {follow}', 'endif',
             'if exist ' + result + ' then', *_refuse(f'{result} exists and is never overwritten')]
    for index, page in enumerate(step.pages):
        lines.append(f'dmem 0x{page:X} 0x{EXTRACT_PAGE_BYTES:X} {">a" if index == 0 else ">>a"} {result}')
    return lines + [f'memmap >>a {result}',
                    f'date >>a {result}',
                    f'time >>a {result}',
                    f'{_say(f"{EXTRACT_END} plan={plan.name} step={k} pages={EXTRACT_PAGES}")} >>a {result}',
                    f'{_say(f"AUTORUN {plan.name} STEP {k} EXTRACTED")} >a {marker}',
                    'if not exist ' + marker + ' then', *_refuse('cannot write ' + marker),
                    _say(f'AUTORUN {plan.name} STEP {k} OF {count}: {result} is written, power off and take the stick to the Mac'),
                    'goto end']


def script_lines(plan: Plan) -> list:
    count = len(plan.steps)
    lines = ['# SP11 autorun script, generated by tools/stage-sp11-boot-media.py (autorun).',
             '# Do not edit: the staging tool compares this file byte for byte with the plan in SP11\\PLAN.TXT.',
             '@echo -off',
             _say(f'AUTORUN {plan.name}: {count} step{"s" if count != 1 else ""}, one per boot. '
                  'Press ESC at the next countdown for the Shell prompt.'),
             '%homefilesystem%',
             'if not exist SP11\\PLAN.TXT then',
             *_refuse('SP11\\PLAN.TXT not found on the Shell volume')]
    for k in range(1, count + 1):
        lines += _extract_lines(plan, k) if isinstance(plan.steps[k - 1], ExtractStep) else _step_lines(plan, k)
    return lines + [':done', _say('ALL STEPS DONE, run --clear to repeat'), ':end']


def render_script(plan: Plan) -> bytes:
    text = '\r\n'.join(script_lines(plan)) + '\r\n'
    problems = script_problems(text)
    if problems:
        raise PlanError('script_rejected', '; '.join(problems[:3]))
    return text.encode('ascii')


# Every line form the generator emits, and no other. Indentation is the two spaces of an `if` body, and only echo
# and goto lines are indented. A line that matches none of these is not something this tool wrote. The characters of
# quoted text exclude everything the Shell treats specially in a command line: % ^ # | < > ; and quotes.
_WORDS = r'[A-Za-z0-9][A-Za-z0-9 ._,:=/\\-]*'
_FILE = r'[A-Z0-9][A-Z0-9.-]*'
_FORMS = (
    ('comment', re.compile(r'# [ -~]*')),
    ('echo_mode', re.compile(r'@echo -(on|off)')),
    ('home', re.compile(r'%homefilesystem%')),
    ('marker', re.compile(rf'echo "{_WORDS}" >a SP11\\DONE([1-9])\.TXT')),
    ('extract_end', re.compile(rf'echo "{EXTRACT_END} plan=({NAME_RE.pattern}) step=([1-9]) pages={EXTRACT_PAGES}" >>a SP11\\RET([1-9])\.TXT')),
    ('dmem', re.compile(rf'dmem ({EXTRACT_ADDRESS_RE.pattern}) 0x{EXTRACT_PAGE_BYTES:X} (>>?a) SP11\\RET([1-9])\.TXT')),
    ('memmap', re.compile(r'memmap >>a SP11\\RET([1-9])\.TXT')),
    ('clock', re.compile(r'(date|time) >>a SP11\\RET([1-9])\.TXT')),
    ('echo', re.compile(rf' {{0,2}}echo "{_WORDS}"')),
    ('if', re.compile(rf'if (not )?exist SP11\\({_FILE}) then')),
    ('endif', re.compile(r'endif')),
    ('goto', re.compile(r' {0,2}goto (step[1-8]|done|end)')),
    ('label', re.compile(r':(step[1-8]|done|end)')),
    ('stall', re.compile(rf'stall {PAUSE_MICROSECONDS}')),
    ('load', re.compile(rf'load SP11\\({_FILE})')),
    ('run', re.compile(rf'SP11\\({_FILE})((?: [^ ]+)*)')),
)


def script_problems(text: str) -> list:
    """Why `text` is not a script this generator could have written; [] when every line and the step structure fit."""
    problems = []
    bare = text.replace('\r\n', '')
    if not text.isascii() or '\t' in text or '\n' in bare or '\r' in bare or not text.endswith('\r\n'):
        problems.append('not ASCII text of CRLF-terminated lines')
    lowered = text.lower()
    for token in _BANNED_TOKENS:
        if token in lowered:
            problems.append(f'names {token}')
    parsed = []
    for number, line in enumerate(text.split('\r\n')[:-1], 1):
        for kind, pattern in _FORMS:
            found = pattern.fullmatch(line)
            if found:
                parsed.append((kind, found))
                break
        else:
            problems.append(f'line {number} is not an allowed command: {line[:50]!r}')
    if problems:
        return problems
    labels = [m.group(1) for kind, m in parsed if kind == 'label']
    steps = [label for label in labels if label.startswith('step')]
    if steps != [f'step{i}' for i in range(1, len(steps) + 1)] or labels[len(steps):] != ['done', 'end'] \
            or not 1 <= len(steps) <= MAX_STEPS:
        return ['the labels are not step1..stepN, done, end']
    for kind, found in parsed:
        if kind == 'goto' and found.group(1) not in labels:
            problems.append(f'goto {found.group(1)} has no label')
    segments, current = {None: []}, None
    for kind, found in parsed:
        if kind == 'label':
            current = found.group(1) if found.group(1).startswith('step') else None
            segments.setdefault(current, [])
        segments[current].append((kind, found))
    for kind in ('load', 'run', 'marker', 'stall', 'extract_end', 'dmem', 'memmap', 'clock'):
        if any(k == kind for k, _ in segments[None]):
            problems.append(f'a {kind} line outside any step')
    if any(kind == 'goto' and found.group(1) != 'end' for kind, found in segments[None]):
        problems.append('a goto outside the steps that does not go to end')
    for label in steps:
        check = _extract_problems if any(kind == 'dmem' for kind, _ in segments[label]) else _step_problems
        problems += check(label, len(steps), segments[label])
    return problems


# The kinds of the lines of an extract block, in order: nothing but the dumps stands between the guards and the memory map.
_EXTRACT_KINDS = ['label', 'if', 'goto', 'endif', 'if', 'echo', 'goto', 'endif', *['dmem'] * EXTRACT_PAGES, 'memmap', 'clock', 'clock', 'extract_end',
                  'marker', 'if', 'echo', 'goto', 'endif', 'echo', 'goto']


def _extract_problems(label: str, count: int, segment: list) -> list:
    """The lines of one extract block (see _extract_lines): skip when the marker exists, refuse an existing result, three dumps that
    are the first and only commands before the memory map, then the date and the time, the end line, the marker, its check and the exit.
    Any other command in the block, before the dumps most of all, is a problem, and so is a different order."""
    k = int(label[4:])
    kinds = [kind for kind, _ in segment]
    if kinds != _EXTRACT_KINDS:
        return [f'{label} is not the lines of an extract block (the dumps must come right after the guards, then the memory map, the date and the time, '
                'the end line and the marker)']
    problems = []
    follow = f'step{k + 1}' if k < count else 'done'
    if segment[1][1].group(1) or segment[1][1].group(2) != MARKER_FORMAT.format(k) or segment[2][1].group(1) != follow:
        problems.append(f'{label} does not begin by skipping ahead when its marker exists')
    if segment[4][1].group(1) or segment[4][1].group(2) != RESULT_FORMAT.format(k) or segment[6][1].group(1) != 'end':
        problems.append(f'{label} does not refuse to overwrite an existing result')
    dumps = [found for kind, found in segment if kind == 'dmem']
    if [found.group(2) for found in dumps] != ['>a'] + ['>>a'] * (EXTRACT_PAGES - 1):
        problems.append(f'{label} does not start its result file with the first dump and append the others')
    if len({found.group(1) for found in dumps}) != EXTRACT_PAGES or any(int(found.group(1), 16) % EXTRACT_PAGE_BYTES for found in dumps):
        problems.append(f'{label} does not dump three distinct 4 KiB aligned pages')
    if [segment[12][1].group(1), segment[13][1].group(1)] != ['date', 'time']:
        problems.append(f'{label} does not write the date and then the time')
    ends = {found.group(3) for found in dumps} | {segment[11][1].group(1), segment[12][1].group(2), segment[13][1].group(2), segment[14][1].group(2),
                                                  segment[14][1].group(3)}
    if ends != {str(k)} or segment[15][1].group(1) != str(k):
        problems.append(f'{label} writes the result or the marker of another step')
    if not segment[16][1].group(1) or segment[16][1].group(2) != MARKER_FORMAT.format(k) or segment[18][1].group(1) != 'end' or segment[-1][1].group(1) != 'end':
        problems.append(f'{label} does not check its marker and leave the script')
    return problems


def _step_problems(label: str, count: int, segment: list) -> list:
    """The lines of one `:stepK` block: skip when the marker exists, check both files, pause, write the marker,
    load, run, and leave the script (a block that fell through would run the next step in the same boot)."""
    k = int(label[4:])
    kinds = [kind for kind, _ in segment]
    found = {kind: [m for k2, m in segment if k2 == kind] for kind in ('stall', 'marker', 'load', 'run')}
    if any(len(lines) != 1 for lines in found.values()):
        return [f'{label} does not have exactly one stall, marker, load and run line']
    problems = []
    if kinds[:4] != ['label', 'if', 'goto', 'endif'] or segment[1][1].group(1) \
            or segment[1][1].group(2) != MARKER_FORMAT.format(k) or segment[2][1].group(1) != (f'step{k + 1}' if k < count else 'done'):
        problems.append(f'{label} does not begin by skipping ahead when its marker exists')
    positions = [kinds.index(kind) for kind in ('stall', 'marker', 'load', 'run')]
    if positions != sorted(positions):
        problems.append(f'{label} does not pause, write its marker, load and run in that order')
    if found['marker'][0].group(1) != str(k):
        problems.append(f'{label} writes the marker of another step')
    slbounce, (app, args) = found['load'][0].group(1), found['run'][0].groups()
    if slbounce not in SLBOUNCE_BUILDS or app not in APP_ARGUMENTS or tuple(args.split()) not in APP_ARGUMENTS[app]:
        problems.append(f'{label} loads or runs something outside the tables')
    checked = {m.group(2) for kind, m in segment if kind == 'if' and m.group(1)}
    if not {slbounce, app} <= checked:
        problems.append(f'{label} does not check that both of its files exist')
    shown = {m.group(0) for kind, m in segment if kind == 'echo'}
    if not {f'echo "CMD1: load SP11\\{slbounce}"', f'echo "CMD2: SP11\\{app}{args}"'} <= shown:
        problems.append(f'{label} does not print the two commands it runs')
    if kinds[-1] != 'goto' or {m.group(1) for kind, m in segment[3:] if kind == 'goto'} != {'end'}:
        problems.append(f'{label} can run on into another step')
    return problems


# ------------------------------------------------------------------------------------- PLAN.TXT and README.TXT

_PLAN_HEAD = ('SP11 AUTORUN PLAN',
              '=================',
              'Generated by tools/stage-sp11-boot-media.py autorun. Do not edit: the tool refuses a tree',
              'whose startup.nsh or PLAN.TXT differs from what it generates from this plan.',
              '')
_PLAN_TAIL = ('How it runs',
              '  The Shell runs EFI\\BOOT\\startup.nsh after its own countdown ("Press ESC in N seconds to',
              '  skip startup.nsh or any other key to continue"); ESC skips it and leaves the Shell prompt.',
              '  Each boot runs the first step whose marker file does not exist. The marker is written',
              '  before the step starts, so a step that hangs is not repeated: hold the power button and',
              '  boot the stick again for the next step. When every marker exists the script runs nothing.',
              'To start again',
              '  Delete SP11\\DONE*.TXT on the stick. To switch autorun off, also delete',
              '  EFI\\BOOT\\startup.nsh. The tool\'s --clear and --remove do this in a staged directory.')
_PLAN_STEP = re.compile(r'step (\d+)\r\n  load  (\S+)\r\n        sha256 (\S+)\r\n  run   (\S+)((?: \S+)*)\r\n'
                        r'        sha256 (\S+)\r\n  marker  SP11\\DONE(\d+)\.TXT, written before the step runs\r\n')
_PLAN_DATA_HEAD = 'data files (read by Q1N1-PAYLOAD.EFI from this volume at run time; the script never names them)'
_PLAN_DATA = re.compile(r'  file (\S+)\r\n        size (\d+)\r\n        sha256 (\S+)\r\n')
_PLAN_EXTRACT = re.compile(rf'step (\d+)\r\n  extract  ({EXTRACT_KEYWORD}(?: 0x[0-9A-F]+){{{EXTRACT_PAGES}}})\r\n'
                           r'  marker  SP11\\DONE(\d+)\.TXT, written after the dump, once it is complete\r\n'
                           r'  result  SP11\\RET(\d+)\.TXT, never overwritten\r\n')
_PLAN_ANY_STEP = re.compile(f'(?:{_PLAN_STEP.pattern})|(?:{_PLAN_EXTRACT.pattern})')


def stamp_staging(plan: Plan, now: datetime = None) -> Plan:
    """A retention plan with the staging computer's local time to the second, which PLAN.TXT records and the verdict tool compares with the
    unit's clock: a result or a marker that is not later than it was left by an earlier run. Any other plan comes back as it was, so that its
    PLAN.TXT does not change."""
    if plan.name not in RETENTION_PLANS:
        return plan
    return replace(plan, staged=(now or datetime.now()).strftime(STAGED_FORMAT))


def render_plan_text(plan: Plan) -> bytes:
    lines = [*_PLAN_HEAD, f'plan: {plan.name}', f'steps: {len(plan.steps)}', *([UNATTENDED_LINE] if plan.stick_first_authorised else []),
             *([f'staged: {plan.staged}'] if plan.staged else []), '']
    for k, step in enumerate(plan.steps, 1):
        if isinstance(step, ExtractStep):
            lines += [f'step {k}', f'  extract  {step.run_line}',
                      f'  marker  SP11\\{MARKER_FORMAT.format(k)}, written after the dump, once it is complete',
                      f'  result  SP11\\{RESULT_FORMAT.format(k)}, never overwritten', '']
            continue
        lines += [f'step {k}', f'  load  {step.slbounce}', f'        sha256 {step.slbounce_sha256}',
                  f'  run   {step.run_line}', f'        sha256 {step.app_sha256}',
                  f'  marker  SP11\\{MARKER_FORMAT.format(k)}, written before the step runs', '']
    if plan.data:
        lines.append(_PLAN_DATA_HEAD)
        for item in plan.data:
            lines += [f'  file {item.name}', f'        size {item.size}', f'        sha256 {item.sha256}']
        lines.append('')
    return ('\r\n'.join([*lines, *_PLAN_TAIL]) + '\r\n').encode('ascii')


def parse_plan_text(data: bytes) -> Plan:
    """The plan a PLAN.TXT describes. The text is also compared with render_plan_text, so no byte outside the
    fields the plan model holds can differ from what the tool wrote."""
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as exc:
        raise PlanError('plan_text_unparseable', 'not ASCII') from exc
    head = re.search(r'^plan: (\S+)\r\nsteps: (\d+)\r\n', text, re.MULTILINE)
    blocks = list(_PLAN_ANY_STEP.finditer(text))
    if head is None or int(head.group(2)) != len(blocks):
        raise PlanError('plan_text_unparseable', 'no plan line, or a step count that disagrees with the step blocks')
    texts, hashes = [], {}
    for index, block in enumerate(blocks, 1):
        if block.group(1) is None:              # an extract step: the groups after the seven of a load and run step
            number, extract, marker, result = block.groups()[7:]
            if not int(number) == int(marker) == int(result) == index:
                raise PlanError('plan_text_unparseable', f'step {number} is not step {index}')
            texts.append(extract)
            continue
        number, slbounce, slbounce_hash, app, args, app_hash, marker = block.groups()[:7]
        if int(number) != index or int(marker) != index:
            raise PlanError('plan_text_unparseable', f'step {number} is not step {index}')
        for name, digest in ((slbounce, slbounce_hash), (app, app_hash)):
            if hashes.setdefault(name, digest) != digest:
                raise PlanError('plan_inconsistent_hash', name)
        texts.append(f'{slbounce} {app}{args}')
    data_files = [DataFile(name, int(size), digest) for name, size, digest in _PLAN_DATA.findall(text)]
    unattended = re.search(rf'^{re.escape(UNATTENDED_LINE)}\r\n', text, re.MULTILINE) is not None
    plan = attach_data(build_plan(head.group(1), texts, hashes, stick_first_authorised=unattended), data_files)
    staged = re.search(r'^staged: (.*)\r\n', text, re.MULTILINE)
    if staged is not None:
        try:
            plan = stamp_staging(plan, datetime.strptime(staged.group(1), STAGED_FORMAT))
        except ValueError as exc:
            raise PlanError('plan_text_unparseable', f'the staged line is not a time: {staged.group(1)!r}') from exc
    if render_plan_text(plan) != data:
        raise PlanError('plan_text_not_canonical', 'differs from what the tool writes for this plan')
    return plan


# The paragraph of the staged README.TXT (stage-sp11-boot-media.py build_readme_text) that is false once the stick
# runs a script by itself.
README_MANUAL_PARAGRAPH = ('This media does nothing automatically. No startup.nsh file is present on\n'
                           'it. You type every command yourself at the UEFI Shell prompt below.\n')


def readme_for_plan(base_text: str, plan: Plan) -> str:
    """`base_text` with the manual-only paragraph replaced by one that says autorun is enabled."""
    if base_text.count(README_MANUAL_PARAGRAPH) != 1:
        raise PlanError('readme_paragraph_missing', 'README.TXT no longer has the paragraph this tool replaces')
    count = len(plan.steps)
    paragraph = (f'AUTORUN IS ENABLED on this media.\n'
                 f'  Plan {plan.name}, {count} step{"s" if count != 1 else ""}, with SHA-256 hashes in SP11\\PLAN.TXT.\n'
                 'EFI\\BOOT\\startup.nsh runs one step per boot after the Shell\'s countdown. Press\n'
                 'ESC at "Press ESC in N seconds to skip startup.nsh" to get the Shell prompt and\n'
                 'type the commands below yourself. Without startup.nsh this media does nothing\n'
                 'automatically.\n')
    return base_text.replace(README_MANUAL_PARAGRAPH, paragraph)
