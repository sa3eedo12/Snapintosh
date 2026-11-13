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

Evidence (see docs/RUNNING.md): the countdown, the search for startup.nsh in the directory the Shell was loaded from,
the `homefilesystem` variable, `if exist`, `goto`, `stall`, `@echo` and the `echo ... >a` redirect were OBSERVED with
the pinned Shell under QEMU (tools/test-sp11-autorun-qemu.py); they are INFERRED for the Shell on the unit.
"""
from dataclasses import dataclass
import re

SCRIPT_REL = 'EFI/BOOT/startup.nsh'
PLAN_REL = 'SP11/PLAN.TXT'
MARKER_FORMAT = 'DONE{}.TXT'
MARKER_RE = re.compile(r'DONE[0-9]{1,2}\.TXT', re.IGNORECASE)
MAX_STEPS = 8
PAUSE_MICROSECONDS = 5000000

# The reviewed slbounce builds, all from the slbounce fork. `load` of one of these is the first half of a step.
SLBOUNCE_BUILDS = ('SLBOUNCE.EFI', 'SLBOUNCE-DEVHE.EFI', 'SLBOUNCE-DEVHE-MARK.EFI', 'SLBOUNCE-DRYRUN.EFI')
# Applications and the only argument vectors each may be given. q1n1 takes exactly `--el2` or nothing (the no-argument
# run is its preflight, which never leaves boot services). Q1N1-STK.EFI is the second q1n1 image, the read-only USB
# survey that the q1n1 fork builds from the same tree behind a compile-time gate (docs/SP11-STK-SURVEY.md there). It
# runs only in the `--el2` flow and has no preflight-only use, so it takes exactly `--el2` and the no-argument form is
# refused. SLTEST.EFI is not listed: it needs an explicit path argument and the operator procedure runs it without a
# preceding `load`.
APP_ARGUMENTS = {
    'Q1N1.EFI': ((), ('--el2',)),
    'Q1N1-STK.EFI': (('--el2',),),
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
class Plan:
    name: str
    steps: tuple

    def files(self) -> dict:
        """File name under SP11\\ -> SHA-256, one entry per distinct name (a name always has one hash)."""
        found = {}
        for step in self.steps:
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


def build_plan(name: str, step_texts, hashes: dict) -> Plan:
    """A validated plan. `hashes` maps every file name the steps use to its expected SHA-256, and nothing else."""
    if not NAME_RE.fullmatch(name or '') or any(token in name.lower() for token in _BANNED_TOKENS):
        raise PlanError('plan_name_invalid', f'{name!r}: 1 to 24 of letters, digits, ".", "_" and "-", starting with a letter or digit, '
                                              'and naming no banned argument or file')
    if not step_texts:
        raise PlanError('plan_has_no_steps')
    if len(step_texts) > MAX_STEPS:
        raise PlanError('plan_has_too_many_steps', f'{len(step_texts)} > {MAX_STEPS}')
    parsed = [parse_step(text) for text in step_texts]
    used = {file_name for step in parsed for file_name in step[:2]}
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
    plan = Plan(name, tuple(Step(s, a, g, hashes[s], hashes[a]) for s, a, g in parsed))
    plan.files()
    return plan


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
        lines += _step_lines(plan, k)
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
    for kind in ('load', 'run', 'marker', 'stall'):
        if any(k == kind for k, _ in segments[None]):
            problems.append(f'a {kind} line outside any step')
    if any(kind == 'goto' and found.group(1) != 'end' for kind, found in segments[None]):
        problems.append('a goto outside the steps that does not go to end')
    for label in steps:
        problems += _step_problems(label, len(steps), segments[label])
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


def render_plan_text(plan: Plan) -> bytes:
    lines = [*_PLAN_HEAD, f'plan: {plan.name}', f'steps: {len(plan.steps)}', '']
    for k, step in enumerate(plan.steps, 1):
        lines += [f'step {k}', f'  load  {step.slbounce}', f'        sha256 {step.slbounce_sha256}',
                  f'  run   {step.run_line}', f'        sha256 {step.app_sha256}',
                  f'  marker  SP11\\{MARKER_FORMAT.format(k)}, written before the step runs', '']
    return ('\r\n'.join([*lines, *_PLAN_TAIL]) + '\r\n').encode('ascii')


def parse_plan_text(data: bytes) -> Plan:
    """The plan a PLAN.TXT describes. The text is also compared with render_plan_text, so no byte outside the
    fields the plan model holds can differ from what the tool wrote."""
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as exc:
        raise PlanError('plan_text_unparseable', 'not ASCII') from exc
    head = re.search(r'^plan: (\S+)\r\nsteps: (\d+)\r\n', text, re.MULTILINE)
    blocks = _PLAN_STEP.findall(text)
    if head is None or int(head.group(2)) != len(blocks):
        raise PlanError('plan_text_unparseable', 'no plan line, or a step count that disagrees with the step blocks')
    texts, hashes = [], {}
    for index, (number, slbounce, slbounce_hash, app, args, app_hash, marker) in enumerate(blocks, 1):
        if int(number) != index or int(marker) != index:
            raise PlanError('plan_text_unparseable', f'step {number} is not step {index}')
        for name, digest in ((slbounce, slbounce_hash), (app, app_hash)):
            if hashes.setdefault(name, digest) != digest:
                raise PlanError('plan_inconsistent_hash', name)
        texts.append(f'{slbounce} {app}{args}')
    plan = build_plan(head.group(1), texts, hashes)
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
