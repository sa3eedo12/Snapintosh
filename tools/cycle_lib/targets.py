# SPDX-License-Identifier: MIT
"""Per-target policy for tools/cycle.py: `--target {a16,sp11}`.

cycle.py was written for one machine, the ASUS Zenbook A16. This module adds the explicit target layer a second
machine needs without touching the first:

* a16 (the default, also when --target is absent) is not enforced here. Every existing per-command check stays the A16
  policy, `enforce` returns before it reads anything, and the tool behaves byte for byte as before. The a16 row below
  only records those policies as data so the two targets can be compared.
* sp11 (Surface Pro 11, Snapdragon X Elite X1E80100) is UNIMPLEMENTED and fails closed. Only the passive modes run
  (`next` and every `--check` / `--check-inputs`). Every other mode refuses with a specific code unless ALL of these
  hold, and even then ends at "no SP11 <flow> flow implemented":
    (a) the local, git-ignored tools/cycle_lib/pins.json has a complete `sp11` section (never the example fallback);
    (b) the operator power attestation in that section is fresh, and bound to this run prefix and to this tool's
        source digest, which covers tools/hamoa_thermal.py (it is not satisfiable from the command line);
    (c) the thermal admission of tools/hamoa_thermal.py (imported lazily; missing, without its attestation
        cross-check, or failing means refuse) accepts;
    (d) the section-8 authorizations (docs/SURFACE-PRO-11.md) the mode needs are recorded as true, and the ones the
        plan says are "per run" (NVMe writes) are also recorded in sp11.run_authorization, bound to this run prefix
        and source digest and as fresh as the power attestation, so a standing boolean alone never enables a write.
  Options that raise clocks or voltage are refused for sp11 in every mode.

`check_cp_memcpy` is the size predicate for any host flow that is about to submit a CP_MEMCPY (AGENTS.md section 5: a
4 MiB copy once hung the GPU). No cycle.py flow calls it: none chooses a size, because the copy size is fixed by the
monitor (compat/adreno-coherent.h ADRENO_COHERENT_COPY_MAX) and by the kext and plug-in, which enforce and test their
own 1 MiB bound (tools/test-adreno-coherent.c). A test (tools/test-cycle-targets-hardening.py) ties the cap here to
that constant; wiring the predicate into a flow that picks a size is the precondition for any such flow.

The mode classification in MODES is VERIFIED against the call sites in cycle_lib (which function takes the hardware
lock, loads the proxy, chainloads, boots a guest with writable storage or programs clocks). The authorization names,
their per-class mapping and the required pins are INFERRED from docs/SURFACE-PRO-11.md section 8 and need the
coordinator's confirmation. `--target` is global: it must precede the subcommand, and a trailing one is an argparse
error, which also fails closed.
"""
from datetime import datetime, timezone
from pathlib import Path
import copy
import errno
import importlib
import json
import os
import re
import stat

from . import common
from .common import Refused

# What a mode can do to the machine. A mode is the union of its classes.
PASSIVE = 'passive'              # reads local files only: no proxy, no machine, nothing written
HOST_BUILD = 'host-build'        # host-only, but writes build/ records that a later boot trusts
TARGET_IO = 'target-io'          # connects to the machine (proxy, adapter or launcher)
CODE_LOAD = 'code-load'          # runs code on the machine (chainload, monitor, guest)
STORAGE_WRITE = 'storage-write'  # a guest runs with the machine's storage writable
CLOCK_CHANGE = 'clock-change'    # the flow can program clocks, voltage or bandwidth votes

DEFAULT_TARGET = 'a16'
PINS_FILES = ('pins.json', 'pins.example.json')
PINS_MAX_BYTES = 256 * 1024
SHA256 = re.compile(r'[0-9a-f]{64}')
PLACEHOLDER = re.compile(r'<.*>')
RFC3339_UTC = re.compile(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{1,6})?(?:Z|\+00:00)', re.ASCII)

# 'command:mode' -> classes. VERIFIED by reading the call sites named in the comments (function, not line, so the
# table does not rot when a file is edited). A mode that is not listed is refused for sp11 (unclassified-mode).
MODES = {
    'next': (PASSIVE,),                                   # status.next_step: local records only
    'status:check': (PASSIVE,),                           # status.status(check=True) returns before require_exclusive
    'capture:check': (PASSIVE,),
    'preserve:check': (PASSIVE,),
    'retire:check': (PASSIVE,),                           # retire.retire(check=True) returns before the proxy
    'storage:check': (PASSIVE,),                          # storage.main: STORAGE_ADAPTER_VERIFIED, no hardware access
    'launch:check-inputs': (PASSIVE,),                    # runs the launcher's offline --check-inputs
    'bootstrap-smoke:check': (PASSIVE,),
    'speed-test:check': (PASSIVE,),
    'installed-fresh:check': (PASSIVE,),
    'preserve:run': (HOST_BUILD,),                        # copies and hashes into build/; never opens the proxy
    'bootstrap-smoke:prepare': (HOST_BUILD,),
    'speed-test:prepare': (HOST_BUILD,),
    'speed-test:decode': (HOST_BUILD,),                   # writes console files; runs pinned decoders through runpy
    'speed-test:compare': (HOST_BUILD,),
    'installed-fresh:prepare': (HOST_BUILD,),
    'status:run': (TARGET_IO,),                           # require_exclusive, load_proxy, connect (read-only query)
    'capture:run': (TARGET_IO,),                          # require_exclusive; read-only collectors as subprocesses
    'storage:run': (TARGET_IO,),                          # execs the storage adapter, which reads the SSD
    'retire:run': (TARGET_IO, CODE_LOAD),                 # connects, quiesces, chainloads the other stage slot
    'launch:prepare': (TARGET_IO, CODE_LOAD, CLOCK_CHANGE),     # admit() reads the proxy; the launcher prepares the GPU
    'launch:boot': (TARGET_IO, CODE_LOAD, STORAGE_WRITE),       # the guest runs with --ans-writable
    'launch:boot-child': (TARGET_IO, CODE_LOAD, STORAGE_WRITE),
    'bootstrap-smoke:boot': (TARGET_IO, CODE_LOAD),             # RAM-only smoke, still a connect and a monitor load
    'speed-test:boot': (TARGET_IO, CODE_LOAD),                  # RAM-disk comparison, no writable storage
    'installed-fresh:boot': (TARGET_IO, CODE_LOAD, STORAGE_WRITE, CLOCK_CHANGE),  # ans_writable; GPU/DVFS steps
    'installed-fresh:quiesce': (TARGET_IO, CLOCK_CHANGE),       # restores the GPU and bandwidth votes
}

# Which argparse destinations select the mode of the commands that have several.
MODE_FLAGS = {
    'launch': ('check_inputs', 'prepare', 'boot', 'boot_child'),
    'bootstrap-smoke': ('prepare', 'check', 'boot'),
    'speed-test': ('prepare', 'check', 'boot', 'decode', 'compare'),
    'installed-fresh': ('prepare', 'check', 'boot', 'quiesce'),
}
CHECK_SELECTS = ('status', 'capture', 'preserve', 'retire', 'storage')   # --check is the offline half

SP11_AUTHORIZATIONS = (
    'disable_secure_boot', 'suspend_bitlocker', 'use_tcblaunch',                   # X1, X1, X0
    'add_uefi_boot_entry_or_write_esp',                                            # X3, only if USB is not enough
    'create_snapintosh_gpt_entry', 'enable_nvme_window_writes', 'cycle_mode_may_write_nvme',   # X6
    'raise_gpu_cpu_clock_voltage',                                                 # X8, X11
)
# docs/SURFACE-PRO-11.md section 8 lists both NVMe-writing authorizations ("Enable NVMe writes to the window" and the cycle
# mode that can write NVMe) as per run. Both are recorded in sp11.run_authorization (prefix, source digest, age) as well as
# in sp11.authorizations, so that a boolean left over from an earlier run can never enable a write on its own.
SP11_PER_RUN_AUTHORIZATIONS = ('enable_nvme_window_writes', 'cycle_mode_may_write_nvme')

TARGETS = {
    'a16': dict(
        label='A16',
        machine='ASUS Zenbook A16 (UX3607OA), Snapdragon X2 Elite X2E-96-100',
        enforced=False,
        pins_section=None,              # the A16 keeps its top-level pins.json keys
        pins_schema=None,
        required_pins=(),
        passive_modes=None,             # every mode is subject only to its own existing checks
        forbidden_options={},
        authorization_keys=(),
        per_run_authorizations=(),
        class_authorizations={},
        power=dict(source='live-proxy', floor_percent=50, max_age_seconds=None, future_skew_seconds=None,
                   attestation_keys=(), contract_keys=()),
        thermal=dict(module='a16_thermal', admit='admit_target', attestation_check=None,
                     guard_option='thermal_guard', mandatory=False),
        storage_write='existing-checks',
        max_cp_memcpy_bytes=1 << 20,
        singleton=True,
    ),
    'sp11': dict(
        label='SP11',
        machine='Microsoft Surface Pro 11, Snapdragon X Elite X1E80100',
        enforced=True,
        pins_section='sp11',
        pins_schema='snapintosh-cycle-pins-sp11-v1',
        # INFERRED: the least the later gates need to name a machine and a boot medium. The example holds
        # placeholders for all of them, so the example alone can never satisfy this list.
        required_pins=(
            (('platform_facts', 'uefi_version'), 'text'),
            (('platform_facts', 'windows_build'), 'text'),
            (('platform_facts', 'secure_boot_state'), 'text'),
            (('tcblaunch_sha256',), 'sha256'),
            (('boot_media_manifest_sha256',), 'sha256'),
            (('bootinfo',), 'object'),
            (('host_link', 'selected'), 'text'),
        ),
        passive_modes=('next', 'status:check', 'capture:check', 'preserve:check', 'retire:check', 'storage:check',
                       'launch:check-inputs', 'bootstrap-smoke:check', 'speed-test:check', 'installed-fresh:check'),
        # The destinations of the installed-fresh clock, voltage and bandwidth options, each with the value the
        # parser gives it when the option is absent. Refused for sp11 whatever the authorizations say.
        forbidden_options=dict(gpu_opp_mhz=None, gpu_dvfs=False, gpu_dvfs_ceiling=None, gpu_dvfs_hold=None,
                               gpu_dvfs_defer=False, cpu_dvfs=False, cpu_dvfs_ceiling=None, ddr_vote=False,
                               llcc=False, llcc_table=None, bw_probe=False),
        authorization_keys=SP11_AUTHORIZATIONS,
        per_run_authorizations=SP11_PER_RUN_AUTHORIZATIONS,
        class_authorizations={
            PASSIVE: (),
            HOST_BUILD: (),
            TARGET_IO: ('disable_secure_boot', 'suspend_bitlocker', 'use_tcblaunch'),
            CODE_LOAD: ('disable_secure_boot', 'suspend_bitlocker', 'use_tcblaunch'),
            STORAGE_WRITE: ('create_snapintosh_gpt_entry', 'enable_nvme_window_writes', 'cycle_mode_may_write_nvme'),
            CLOCK_CHANGE: ('raise_gpu_cpu_clock_voltage',),
        },
        # UNIMPLEMENTED: the SP11's AC and battery state is not readable by the monitor or by a host tool, so the
        # operator records it. The age bound matches the 30-minute staleness bound of hamoa_thermal's own check.
        power=dict(source='operator-attestation', floor_percent=50, max_age_seconds=1800, future_skew_seconds=60,
                   attestation_keys=('ac_connected', 'battery_percent', 'recorded_utc', 'run_prefix', 'source_sha256'),
                   contract_keys=('ac_connected', 'battery_percent', 'recorded_utc', 'run_prefix')),
        thermal=dict(module='hamoa_thermal', admit='admit_target', attestation_check='power_attestation_ok',
                     guard_option='thermal_guard', mandatory=True),
        storage_write='authorization-required',
        max_cp_memcpy_bytes=1 << 20,
        singleton=True,
    ),
}


class TargetRefused(Refused):
    """The target policy refused this invocation. `code` is stable (tests and callers match on it)."""

    def __init__(self, target, code, detail):
        super().__init__(f'target {target} refused ({code}): {detail}')
        self.target = target
        self.code = code
        self.detail = detail


def mode_of(args):
    """'command:mode' of a parsed namespace, or None when it names no mode or several."""
    command = getattr(args, 'command', None)
    if command == 'next':
        return 'next'
    if command in CHECK_SELECTS:
        return command + (':check' if getattr(args, 'check', False) else ':run')
    chosen = [flag for flag in MODE_FLAGS.get(command, ()) if getattr(args, flag, None)]
    if len(chosen) != 1:
        return None
    return command + ':' + chosen[0].replace('_', '-')


def required_authorizations(row, classes):
    """The authorizations a mode with these classes needs, in a stable order and without repeats."""
    needed = []
    for cls in classes:
        for key in row['class_authorizations'].get(cls, ()):
            if key not in needed:
                needed.append(key)
    return tuple(needed)


def flow_name(mode):
    """The word the 'no SP11 <flow> flow implemented' refusal uses."""
    command, _, kind = mode.partition(':')
    return {'run': command, 'boot-child': 'boot'}.get(kind, kind)


def run_prefix_of(args):
    """What the operator attestation is bound to: the run prefix, else the launch candidate's stem, else the command."""
    prefix = getattr(args, 'prefix', None)
    if prefix:
        return str(prefix)
    candidate = getattr(args, 'candidate', None)
    if getattr(args, 'command', None) == 'launch' and candidate:
        return Path(candidate).stem
    return str(getattr(args, 'command', ''))


def source_digest(ws):
    """SHA-256 over the relative path and hash of every tool source except the pins files, plus the thermal module.

    The attestation lives in pins.json, so a digest that included it could never match the record it is written
    into. The pins files are still covered by the A16 source-hash invariant (Workspace.tool_sources); only this
    binding leaves them out. tools/hamoa_thermal.py is not one of the tool sources (cycle.py imports it lazily) but it
    decides the thermal admission and the attestation cross-check, so a change to it must void an attestation that was
    recorded against the old one; a missing file digests as "missing" and the import then refuses. Paths are relative
    to the workspace so the digest names no machine."""
    root = Path(ws.root)
    lines = []
    for name, digest in ws.tool_sources().items():
        path = Path(name)
        if path.name in PINS_FILES:
            continue
        try:
            shown = path.relative_to(root).as_posix()
        except ValueError:
            shown = path.name
        lines.append(f'{shown} {digest}')
    for module in sorted({row['thermal']['module'] for row in TARGETS.values() if row['enforced']}):
        path = root / 'tools' / f'{module}.py'
        lines.append(f'tools/{module}.py {common.sha(path) if path.is_file() else "missing"}')
    return common.sha_bytes(('\n'.join(sorted(lines)) + '\n').encode())


def check_cp_memcpy(target, nbytes):
    """Refuse a CP_MEMCPY that is negative, not an integer or over the target's cap (1 MiB).

    AGENTS.md section 5: a single 4 MiB CP_MEMCPY hung the GPU. No cycle.py option or flow carries a size, so
    nothing calls this today (see the module docstring); it is the check a flow that chooses one must make first, and
    it applies to every target."""
    if target not in TARGETS:
        raise TargetRefused(target, 'unknown-target', f'no such target (known: {", ".join(sorted(TARGETS))})')
    if isinstance(nbytes, bool) or not isinstance(nbytes, int) or nbytes < 0:
        raise TargetRefused(target, 'cp-memcpy-invalid',
                            f'a CP_MEMCPY size must be a non-negative integer, not {nbytes!r}')
    limit = TARGETS[target]['max_cp_memcpy_bytes']
    if nbytes > limit:
        raise TargetRefused(target, 'cp-memcpy-too-large',
                            f'a CP_MEMCPY of {nbytes} bytes exceeds the {limit}-byte cap')


def _no_duplicate_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f'duplicate key {key!r}')
        document[key] = value
    return document


def _reject_constant(name):
    raise ValueError(f'{name} is not a JSON number')


def _read_bounded(fd):
    chunks, total = [], 0
    while True:
        block = os.read(fd, 1 << 16)
        if not block:
            return b''.join(chunks)
        total += len(block)
        if total > PINS_MAX_BYTES:
            return None
        chunks.append(block)


def _local_pins_section(target, row, directory):
    """The sp11 section of the local pins.json, read strictly.

    The example fallback of common.PINS is never used here: a machine without its own pins must not pass for one
    with them. The file is opened without following a link and without blocking (a FIFO would hang an open for
    reading), and only a bounded regular file is parsed."""
    path = Path(directory if directory is not None else common.LIB) / 'pins.json'
    if '..' in path.parts:
        raise TargetRefused(target, 'unsafe-pins-path', "the pins path contains '..'")
    if os.path.realpath(path) != os.path.abspath(path):
        raise TargetRefused(target, 'unsafe-pins-path', 'the pins path goes through a symbolic link')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0)
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError:
        raise TargetRefused(target, 'no-pins', 'tools/cycle_lib/pins.json does not exist; copy pins.example.json to '
                            'pins.json and fill in its sp11 section') from None
    except OSError as error:
        code = 'unsafe-pins-path' if error.errno == errno.ELOOP else 'bad-pins'
        raise TargetRefused(target, code, f'pins.json cannot be opened: {error.strerror}') from None
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise TargetRefused(target, 'unsafe-pins-path', 'pins.json is not a regular file')
        data = _read_bounded(fd)
    finally:
        os.close(fd)
    if data is None:
        raise TargetRefused(target, 'bad-pins', f'pins.json is larger than {PINS_MAX_BYTES} bytes')
    try:
        document = json.loads(data.decode('utf-8'), object_pairs_hook=_no_duplicate_keys,
                              parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise TargetRefused(target, 'bad-pins', f'pins.json is not strict JSON: {error}') from None
    if not isinstance(document, dict):
        raise TargetRefused(target, 'bad-pins', 'pins.json is not a JSON object')
    section = document.get(row['pins_section'])
    if section is None:
        raise TargetRefused(target, 'no-sp11-pins', f'pins.json has no "{row["pins_section"]}" section')
    if not isinstance(section, dict):
        raise TargetRefused(target, 'bad-pins', f'the "{row["pins_section"]}" section is not an object')
    return section


def _example_section(directory, row):
    path = Path(directory if directory is not None else common.LIB) / 'pins.example.json'
    try:
        return json.loads(path.read_text()).get(row['pins_section'])
    except (OSError, ValueError, AttributeError):
        return None


def _valid_pin(kind, value):
    if kind == 'text':
        return isinstance(value, str) and bool(value.strip()) and not PLACEHOLDER.fullmatch(value.strip())
    if kind == 'sha256':
        return isinstance(value, str) and bool(SHA256.fullmatch(value))
    if kind == 'object':
        return isinstance(value, dict) and bool(value)
    raise ValueError(f'unknown pin kind {kind!r}')


def _check_section(target, row, section, directory):
    if section.get('schema') != row['pins_schema']:
        raise TargetRefused(target, 'bad-pins', f'sp11.schema must be "{row["pins_schema"]}"')
    if section == _example_section(directory, row):
        raise TargetRefused(target, 'example-pins', 'the sp11 section is the unedited example; record this machine\'s '
                            'own facts in tools/cycle_lib/pins.json')
    for path, kind in row['required_pins']:
        node = section
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if not _valid_pin(kind, node):
            raise TargetRefused(target, 'incomplete-pins', f'sp11.{".".join(path)} is missing, null, a placeholder or '
                                f'not a valid {kind}')


def _check_authorizations(target, row, section, classes):
    recorded = section.get('authorizations', {})
    if not isinstance(recorded, dict):
        raise TargetRefused(target, 'bad-pins', 'sp11.authorizations must be an object')
    for key, value in recorded.items():
        if key not in row['authorization_keys']:
            raise TargetRefused(target, 'bad-pins', f'sp11.authorizations.{key} is not a known authorization')
        if not isinstance(value, bool):
            raise TargetRefused(target, 'bad-pins', f'sp11.authorizations.{key} must be true or false')
    for key in required_authorizations(row, classes):
        if recorded.get(key) is not True:
            raise TargetRefused(target, 'missing-authorization', f'{key} is not recorded as authorized '
                                '(docs/SURFACE-PRO-11.md section 8); access to the repository is not authorization')


def _epoch(text):
    """Seconds since the epoch of an RFC 3339 UTC timestamp. Parsed by hand: fromisoformat rejects 'Z' before 3.11."""
    match = RFC3339_UTC.fullmatch(text)
    if not match:
        raise ValueError('not an RFC 3339 UTC timestamp (YYYY-MM-DDTHH:MM:SS[.ffffff]Z)')
    fields = [int(match.group(i)) for i in range(1, 7)]
    return datetime(*fields, tzinfo=timezone.utc).timestamp() + (float(match.group(7)) if match.group(7) else 0.0)


def _check_attestation(target, row, section, args, ws, clock):
    """Validate the operator's power attestation; returns the record the thermal module's own check consumes."""
    power = row['power']
    keys = power['attestation_keys']
    node = section.get('power_attestation')
    prefix = run_prefix_of(args)
    digest = source_digest(ws)
    if node is None or (isinstance(node, dict) and set(node) <= set(keys) and all(node.get(k) is None for k in keys)):
        raise TargetRefused(target, 'no-attestation', 'record sp11.power_attestation in tools/cycle_lib/pins.json: '
                            f'ac_connected, battery_percent, recorded_utc (UTC, RFC 3339), run_prefix "{prefix}" and '
                            f'source_sha256 "{digest}"; it expires after {power["max_age_seconds"] // 60} minutes')

    def invalid(reason):
        return TargetRefused(target, 'attestation-invalid', reason)

    if not isinstance(node, dict) or set(node) != set(keys):
        raise invalid(f'sp11.power_attestation must have exactly the keys {", ".join(keys)}')
    ac, battery, recorded, bound_prefix, bound_digest = (node[k] for k in keys)
    if not isinstance(ac, bool):
        raise invalid('ac_connected must be true or false')
    if battery is not None and (isinstance(battery, bool) or not isinstance(battery, int) or not 0 <= battery <= 100):
        raise invalid('battery_percent must be an integer 0..100 or null')
    if not isinstance(bound_prefix, str) or not bound_prefix:
        raise invalid('run_prefix must be a non-empty string')
    if not isinstance(bound_digest, str) or not SHA256.fullmatch(bound_digest):
        raise invalid('source_sha256 must be 64 lower-case hex digits')
    if not isinstance(recorded, str):
        raise invalid('recorded_utc must be a string')
    try:
        moment = _epoch(recorded)
    except ValueError as error:
        raise invalid(f'recorded_utc: {error}') from None
    age = clock() - moment
    if age < -power['future_skew_seconds']:
        raise invalid('recorded_utc is in the future')
    if age > power['max_age_seconds']:
        raise TargetRefused(target, 'attestation-expired', f'the power attestation is {int(age)} s old '
                            f'(limit {power["max_age_seconds"]} s); record a new one')
    if bound_prefix != prefix:
        raise TargetRefused(target, 'attestation-prefix', f'the power attestation is for run prefix "{bound_prefix}", '
                            f'this invocation is "{prefix}"')
    if bound_digest != digest:
        raise TargetRefused(target, 'attestation-source', 'the tool sources changed since the power attestation was '
                            f'recorded (current source_sha256 {digest})')
    try:
        floor = max(power['floor_percent'], common.battery_minimum(getattr(args, 'battery_minimum', None)))
    except (Refused, ValueError, TypeError) as error:
        raise TargetRefused(target, 'power-insufficient', f'battery minimum: {error}') from None
    if not ac and (battery is None or battery < floor):
        raise TargetRefused(target, 'power-insufficient', f'the attestation records neither AC power nor a battery '
                            f'of at least {floor}%')
    return {key: node[key] for key in power['contract_keys']}


def _check_run_authorization(target, row, section, classes, args, ws, clock):
    """The per-run authorizations a mode needs, from sp11.run_authorization.

    sp11.authorizations holds standing booleans; the plan says NVMe writes are enabled "per run", so a boolean left
    over from an earlier run must never be enough. The per-run ones are recorded again for this run, with the run
    prefix, the tool source digest and a timestamp that expires, exactly like the power attestation."""
    per_run = row['per_run_authorizations']
    needed = [key for key in required_authorizations(row, classes) if key in per_run]
    if not needed:
        return
    power = row['power']
    keys = per_run + ('recorded_utc', 'run_prefix', 'source_sha256')
    node = section.get('run_authorization')
    prefix = run_prefix_of(args)
    digest = source_digest(ws)
    if node is None or (isinstance(node, dict) and set(node) <= set(keys) and all(node.get(k) is None for k in keys)):
        raise TargetRefused(target, 'no-run-authorization', f'record sp11.run_authorization in tools/cycle_lib/pins.json '
                            f'for this run: {", ".join(per_run)} (true or false), recorded_utc (UTC, RFC 3339), run_prefix '
                            f'"{prefix}" and source_sha256 "{digest}"; it expires after {power["max_age_seconds"] // 60} '
                            'minutes (docs/SURFACE-PRO-11.md section 8: NVMe writes are enabled per run)')

    def invalid(reason):
        return TargetRefused(target, 'run-authorization-invalid', reason)

    if not isinstance(node, dict) or set(node) != set(keys):
        raise invalid(f'sp11.run_authorization must have exactly the keys {", ".join(keys)}')
    for key in per_run:
        if not isinstance(node[key], bool):
            raise invalid(f'{key} must be true or false')
    recorded, bound_prefix, bound_digest = node['recorded_utc'], node['run_prefix'], node['source_sha256']
    if not isinstance(bound_prefix, str) or not bound_prefix:
        raise invalid('run_prefix must be a non-empty string')
    if not isinstance(bound_digest, str) or not SHA256.fullmatch(bound_digest):
        raise invalid('source_sha256 must be 64 lower-case hex digits')
    if not isinstance(recorded, str):
        raise invalid('recorded_utc must be a string')
    try:
        moment = _epoch(recorded)
    except ValueError as error:
        raise invalid(f'recorded_utc: {error}') from None
    age = clock() - moment
    if age < -power['future_skew_seconds']:
        raise invalid('recorded_utc is in the future')
    if age > power['max_age_seconds']:
        raise TargetRefused(target, 'run-authorization-expired', f'the run authorization is {int(age)} s old '
                            f'(limit {power["max_age_seconds"]} s); record a new one')
    if bound_prefix != prefix:
        raise TargetRefused(target, 'run-authorization-prefix', f'the run authorization is for run prefix '
                            f'"{bound_prefix}", this invocation is "{prefix}"')
    if bound_digest != digest:
        raise TargetRefused(target, 'run-authorization-source', 'the tool sources changed since the run authorization '
                            f'was recorded (current source_sha256 {digest})')
    for key in needed:
        if node[key] is not True:
            raise TargetRefused(target, 'missing-authorization', f'{key} is not authorized for this run '
                                '(docs/SURFACE-PRO-11.md section 8); access to the repository is not authorization')


def _check_thermal(target, row, section, record, prefix, clock):
    """Thermal admission through the thermal module, imported lazily so that nothing but sp11 needs it."""
    thermal = row['thermal']
    name = thermal['module']
    try:
        module = importlib.import_module(name)
    except ImportError as error:
        raise TargetRefused(target, 'thermal-missing', f'{name} cannot be imported ({error}); there is no SP11 '
                            'thermal admission') from None
    except Exception as error:
        raise TargetRefused(target, 'thermal-error',
                            f'{name} failed to import: {type(error).__name__}: {error}') from None
    check_name = thermal['attestation_check']
    if check_name is not None:
        # A module that lacks the cross-check must refuse, not skip it: the cross-check is what ties the operator's
        # power record to the thermal module's own staleness rules.
        cross_check = getattr(module, check_name, None)
        if cross_check is None:
            raise TargetRefused(target, 'thermal-missing', f'{name} has no {check_name}: the power attestation cannot '
                                'be cross-checked, and a missing check is a refusal, not a pass')
        try:
            accepted = cross_check(dict(record), clock(), prefix)
        except Exception as error:
            raise TargetRefused(target, 'attestation-rejected', f'{name}.{check_name} raised '
                                f'{type(error).__name__}: {error}') from None
        if accepted is not True:
            raise TargetRefused(target, 'attestation-rejected', f'{name}.{check_name} returned '
                                f'{accepted!r}, not True')
    admit = getattr(module, thermal['admit'], None)
    if admit is None:
        raise TargetRefused(target, 'thermal-missing', f'{name} has no {thermal["admit"]}')
    try:
        # The recorded boot info stands in for the live one: INFERRED, and only until an SP11 flow can read it.
        result = admit(copy.deepcopy(section['bootinfo']))
    except ValueError as error:
        raise TargetRefused(target, 'thermal-refused', str(error)) from None
    except Exception as error:
        raise TargetRefused(target, 'thermal-error', f'{type(error).__name__}: {error}') from None
    # Admission signals refusal by raising; the only returns that mean "admitted" are None and True. Anything else,
    # including 0, '' and [], is not a decision this gate understands and must not pass.
    if result is not None and result is not True:
        raise TargetRefused(target, 'thermal-refused', f'{name}.{thermal["admit"]} returned {result!r}, not None or True')


def enforce(args, ws, *, pins_dir=None, clock=None):
    """Apply the selected target's policy to a parsed invocation; raises TargetRefused to stop it.

    Returns None for a16 (before reading anything: the A16 flow is unchanged) and a decision dict for an sp11 mode
    that is passive. Never returns for another sp11 mode: no SP11 flow exists behind the gates, so the last
    statement refuses. `pins_dir` and `clock` exist for the tests; production uses the real pins and time."""
    name = getattr(args, 'target', DEFAULT_TARGET)
    row = TARGETS.get(name) if isinstance(name, str) else None
    if row is None:
        raise TargetRefused(name, 'unknown-target', f'no such target (known: {", ".join(sorted(TARGETS))})')
    if not row['enforced']:
        return None
    clock = clock or common.now
    mode = mode_of(args)
    if mode not in MODES:
        raise TargetRefused(name, 'unclassified-mode', f'"{getattr(args, "command", None)}" with these options is not '
                            f'a classified {row["label"]} mode')
    classes = MODES[mode]
    for dest, off in row['forbidden_options'].items():
        if getattr(args, dest, off) != off:
            raise TargetRefused(name, 'option-forbidden', f'--{dest.replace("_", "-")} raises clocks, voltage or '
                                f'bandwidth votes; it is refused for {row["label"]} (AGENTS.md section 5)')
    if classes == (PASSIVE,) and mode in row['passive_modes']:
        return dict(target=name, mode=mode, classes=classes,
                    notice=f'cycle: --target {name}: {mode} is passive and runs only the existing offline checks, '
                           f'which read the A16 tool inputs; no {row["label"]} flow is implemented (UNIMPLEMENTED)')
    section = _local_pins_section(name, row, pins_dir)
    _check_section(name, row, section, pins_dir)
    _check_authorizations(name, row, section, classes)
    guard = row['thermal']['guard_option']
    if row['thermal']['mandatory'] and hasattr(args, guard) and not getattr(args, guard):
        raise TargetRefused(name, 'thermal-guard-required', f'--{guard.replace("_", "-")} cannot be off for '
                            f'{row["label"]}: the thermal guard is mandatory')
    record = _check_attestation(name, row, section, args, ws, clock)
    _check_run_authorization(name, row, section, classes, args, ws, clock)
    _check_thermal(name, row, section, record, run_prefix_of(args), clock)
    if row['singleton'] and TARGET_IO in classes:
        try:
            common.require_exclusive(ws)
        except Refused as error:
            raise TargetRefused(name, 'hardware-client-running', str(error)) from None
    raise TargetRefused(name, 'unimplemented-flow', f'no {row["label"]} {flow_name(mode)} flow implemented: every gate '
                        'for this mode passed, but nothing behind them can run (UNIMPLEMENTED)')
