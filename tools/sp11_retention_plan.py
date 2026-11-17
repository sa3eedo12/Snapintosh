# SPDX-License-Identifier: MIT
"""The plan builder of the retention probe M10 (docs/SP11-RETENTION-PROBE.md section 7): the step texts of the autorun plan `retention-m10` (or
`retention-m10-auto`, which has no manual restart and is refused unless the operator authorised the stick first in the boot order; the `unattended:`
line that plan records in PLAN.TXT is a record of the operator's decision and not an interlock), the configuration file the payload reads, the
payload manifest that lists RETPROBE.BIN and RETPATH.DAT and nothing else, the exact operator actions, and the authorisation record text for the
run. It writes three files under the directory it is given (RETPATH.DAT, PAYLOAD.MAN and RECORD.TXT) and prints the rest; it never touches a stick,
a disk, a device or a link, and it authorises nothing: the record is text the operator reads, and its SHA-256 is what they write down. Evidence:
EXPERIMENTAL; hardware NOT RUN.

    python3 tools/sp11_retention_plan.py --payload build/uefi/payloads/retprobe.bin --path psci-reset --out build/retention
    python3 tools/sp11_retention_plan.py ... --unattended --stick-first-authorised       # the plan retention-m10-auto

The record is wrapped by hand and built from fixed lines, never by a library: its digest is the operator's approval, so a changed byte is another
record and the text must not depend on a library's line breaking."""
import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_autorun as autorun  # noqa: E402
import sp11_retention as retention  # noqa: E402

DEFAULT_SLBOUNCE = 'SLBOUNCE-DEVHE-MARK.EFI'
RESET_IDS = {'psci-reset': ('SYSTEM_RESET', 0x84000009), 'psci-reset2': ('SYSTEM_RESET2', 0xC4000012)}
HOLD_SECONDS = 60           # how long the operator waits for the Shell's countdown after the restart before they stop


class PlanRefusal(Exception):
    def __init__(self, reason: str, detail: str = ''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason


def step_texts(pages, slbounce: str = DEFAULT_SLBOUNCE) -> list:
    """The two --step arguments of the plan: the payload runner after `load`, then the extract step with the three pages."""
    return [f'{slbounce} {autorun.RUNNER} --el2', ' '.join((autorun.EXTRACT_KEYWORD, *(f'0x{page:X}' for page in pages)))]


def check_request(path: str, countdown: int, pages, unattended: bool, stick_first_authorised: bool, expect_refusal: bool = False) -> None:
    """PlanRefusal for a request the builder will not make; the autorun model repeats the plan rules (build_plan) when the tree is staged."""
    if path not in retention.PATHS:
        raise PlanRefusal('path_unknown', f'{path}: one of {", ".join(retention.PATHS)}')
    if path == 'psci-reset2' and not expect_refusal:
        raise PlanRefusal('path_not_implemented_on_the_unit', 'PSCI_FEATURES answered NOT_SUPPORTED(-1) for SYSTEM_RESET2 on the unit (OBSERVED once, boot 5): '
                                                              'the payload would refuse before any store; pass --expect-refusal to build that run anyway')
    if unattended and not stick_first_authorised:
        raise PlanRefusal('unattended_not_authorised', 'the unattended plan needs the stick first in the boot order, a persistent firmware setting '
                                                       'that only the operator may decide: pass --stick-first-authorised once they did')
    if stick_first_authorised and not unattended:
        raise PlanRefusal('stick_first_flag_without_unattended_plan', '--stick-first-authorised belongs to --unattended')
    if unattended and path == 'none':
        raise PlanRefusal('unattended_without_reset', 'the unattended plan waits for a reset by itself: path none makes none')
    try:
        retention.config_bytes(retention.PATHS[path], countdown, pages)
    except retention.ConfigError as error:
        raise PlanRefusal('config_refused', str(error)) from error


def operator_actions(plan: str, path: str, countdown: int, unattended: bool) -> str:
    """The exact actions of the operator for one run, with no photograph: what to press, what to wait for, when to take the stick to the Mac."""
    name = RESET_IDS.get(path, ('NONE', 0))[0]
    out = [f'OPERATOR ACTIONS FOR PLAN {plan}',
           f'Reset path of this run: {retention.PATH_LABELS[retention.PATHS[path]]}'
           + (f' (the probe calls {name} after a countdown of {countdown} s).' if path != 'none' else ' (no reset call: the canaries stay for a reset you make).'),
           '',
           'Before you start',
           '  - The unit is on AC power, or the battery is at 50 percent or more. Nothing but the stick is plugged in.',
           '  - You approved RECORD.TXT and wrote down its SHA-256. The stick was staged from the Mac by the tool and holds no SP11\\RET2.TXT.',
           '  - A used stick needs `autorun --clear` on the staged tree and a fresh copy of it: the markers DONE1.TXT and DONE2.TXT must be gone.',
           '    A marker left by an earlier run does harm: a DONE1.TXT makes step 1 skip, so the probe does not run and step 2 dumps RAM that nothing',
           '    wrote (a NONE that means nothing); a RET2.TXT makes step 2 refuse and leaves the old result to be read as this one (a false WHOLE).',
           '    The Mac tool refuses both by the times it records (STALE_STEP_MARKER, STALE_RESULT), but only after the run: start clean.']
    if unattended:
        out += ['  - The stick is first in the boot order. You set that in the firmware setup yourself: this tool changes no setting, and the plan',
                '    records that you authorised it. After the run, put the boot order back.',
                '',
                'ACTION 1: start the stick and wait',
                '  1. Power the unit on. With the stick first in the boot order it starts the stick by itself and shows the Shell countdown.',
                '  2. Press no key. Step 1 starts by itself; after about 5 s the runner shows the probe\'s page.',
                f'  3. Wait. {countdown} s after the page shows NEXT RESET AFTER the unit resets and starts the stick again by itself.',
                '     Press no key. Step 2 starts by itself, writes SP11\\RET2.TXT and shows',
                f'     AUTORUN {plan} STEP 2 OF 2: SP11\\RET2.TXT is written, power off and take the stick to the Mac.',
                '  If the page shows a line that starts with REFUSED or VERIFY FAILED: nothing was reset. Power off and tell the coordinator the last',
                '  line you saw, in words.',
                f'  If the unit shows nothing within {HOLD_SECONDS} s of the reset: do NOT power-cycle it (that loses what the probe wrote), touch nothing,',
                '  and tell the coordinator what the unit did, in words.']
    else:
        out += ['',
                'ACTION 1: start the stick and wait',
                '  1. Power the unit off (hold the power button until the screen is dark).',
                '  2. Start the stick as you always do: hold Volume-Down, press Power, keep Volume-Down held until the Shell countdown shows.',
                '  3. Press no key. Step 1 starts by itself; after about 5 s the runner shows the probe\'s page.',
                '  4. Wait until the page shows the line NEXT RESET AFTER ' + f'{countdown} S.',
                '     If the page shows a line that starts with REFUSED or VERIFY FAILED instead, or a run of lines that ends before that one: stop. Nothing',
                '     was reset. Power off and tell the coordinator the last line you saw, in words.']
        if path != 'none':
            out += ['',
                    'ACTION 2: hold Volume-Down through the restart',
                    f'  5. From the moment the page shows NEXT RESET AFTER {countdown} S, HOLD Volume-Down and keep holding it. The unit resets by itself',
                    f'     {countdown} s later; the screen goes dark and the unit restarts. UNKNOWN until a run: whether the firmware honours a held key at a',
                    '     restart. Keep holding Volume-Down until the Shell countdown shows, then release it and press nothing.',
                    '  6. Step 2 starts by itself. It writes SP11\\RET2.TXT and shows',
                    f'     AUTORUN {plan} STEP 2 OF 2: SP11\\RET2.TXT is written, power off and take the stick to the Mac.',
                    f'  If the Shell countdown does not show within {HOLD_SECONDS} s of the restart (the unit started something else, or nothing): stop. Do NOT',
                    '  power-cycle to try again in this run: a power cycle loses what the probe wrote. Touch nothing and tell the coordinator what the unit did,',
                    '  in words. The power button is for the end of the run (step 7) and for danger (heat, a fan at full speed, a smell): then hold it and say so.']
        else:
            out += ['',
                    'ACTION 2: reset the unit yourself (the negative control)',
                    '  5. The page ends with RESET NONE. Hold the power button until the screen is dark, then start the stick as in action 1.',
                    '     Expect NONE: a power cycle is expected to lose the pages. A WHOLE or PARTIAL page here is a surprise worth keeping.']
    first = 4 if unattended else 7 if path != 'none' else 6          # the number after the last step of the actions above
    out += ['',
            'AFTER: take the stick to the Mac',
            f'  {first}. Hold the power button until the screen is dark. Unplug the stick and plug it into the Mac.',
            f'  {first + 1}. On the Mac, read the result (it only reads): python3 tools/sp11_retention_check.py stick <the stick\'s mount point>',
            f'  {first + 2}. Copy SP11/RET2.TXT off the stick and keep it, with what the tool printed. The next run starts from a fresh copy of the staged tree,',
            '     never from a used stick.']
    return '\n'.join(out) + '\n'


def authorisation_record(plan: str, payload_sha256: str, payload_size: int, config_sha256: str, manifest_sha256: str, manifest_size: int, path: str,
                         countdown: int, pages, slbounce: str = DEFAULT_SLBOUNCE, unattended: bool = False) -> str:
    """The text the operator approves for one run. ASCII, hand wrapped, deterministic in its arguments."""
    label = retention.PATH_LABELS[retention.PATHS[path]]
    call = {'psci-reset': 'SYSTEM_RESET, the SMC call with function id 0x84000009 and no argument',
            'psci-reset2': 'SYSTEM_RESET2, the SMC call with function id 0xC4000012, reset type 0 (the architectural warm reset), cookie 0',
            'none': 'none: no reset call is made'}[path]
    out = ['SP11 RETENTION PROBE (M10): AUTHORISATION RECORD',
           '================================================',
           'Status: EXPERIMENTAL. Hardware NOT RUN. Nothing is authorised until the operator has read this text and written down the SHA-256 of its',
           'bytes: a changed byte is another record. Approving it is not a decision about anything it does not name (section 7).',
           '',
           '1. What is authorised: one run',
           f'   Plan:           {plan}' + (' (no manual restart: the stick is first in the boot order)' if unattended else ' (two starts of the stick by the operator)'),
           f'   slbounce build: {slbounce}, then Q1N1-PAYLOAD.EFI --el2 (the payload runner), as the earlier plans',
           f'   Payload:        retprobe.bin, {payload_size} bytes, SHA-256 {payload_sha256}',
           f'   Configuration:  RETPATH.DAT, {retention.CONFIG_BYTES} bytes, SHA-256 {config_sha256}',
           f'   Manifest:       PAYLOAD.MAN, {manifest_size} bytes, SHA-256 {manifest_sha256}',
           '                   it lists RETPROBE.BIN and RETPATH.DAT and nothing else: the runner calls every file of its manifest',
           '                   whose name ends in .BIN',
           f'   Reset path:     {label}: {call}',
           f'   Countdown:      {countdown} s between the line NEXT RESET AFTER and the call',
           f'   Pages:          {", ".join(f"0x{page:X}" for page in pages)} (4096 bytes each, conventional memory of the unit\'s map)',
           '',
           '2. What the payload does to the machine, at EL2',
           '   Reads:  the previous contents of the three pages (3072 loads through the runner\'s guarded load, so a bad address is an error and not',
           '           a fault). Plain loads that nothing guards: the runner\'s preload table, the 64-byte configuration file it names, the runner\'s',
           '           memory map and, after the stores, the three pages it wrote. The system registers CurrentEL, CTR_EL0, CNTFRQ_EL0 and PAR_EL1,',
           '           and CNTPCT_EL0 through the runner\'s counter helper.',
           '   MMU:    AT S1E2R and AT S1E2W ask EL2\'s stage 1 about each page and access nothing; they write PAR_EL1, the one system register the',
           '           payload writes. They show that EL2 maps the page, not that TrustZone, an XPU or QHEE leave it alone outside UEFI\'s memory',
           '           map: a store the hardware rejected would be an external abort, which only a power cycle ends.',
           '   Stores: exactly the three pages above, RAM only, 4096 bytes each, then DC CVAC and a barrier on every line, then DC CIVAC and a',
           '           read-back whose CRC must match. It stores nothing else outside its own stack. DC CVAC and DC CIVAC reach the point of',
           '           coherency and no further: whether a system cache (LLCC), a buffer of the memory controller, DRAM\'s self-refresh or a',
           '           scrub of memory at boot keeps or loses a page is not named here; it is part of what the run measures.',
           '   Calls:  PSCI_FEATURES twice (a question with no effect)' + (', then' if path != 'none' else '. It makes no other call.'),
           ]
    if path != 'none':
        out += [f'           {label} once, only when PSCI_FEATURES reported that function implemented, only after the pages verified and the',
                '           countdown ran, and never after a failed check. It restarts the whole machine. It has never been called on this unit:',
                '           whether it restarts the unit, and whether DRAM keeps the pages, are UNKNOWN. This run is the measurement.',
                '           If the call returns, the payload does nothing more: no second call, no other function.']
    if path == 'psci-reset':
        out += ['           SYSTEM_RESET is the architectural cold-reset call; the warm reset is SYSTEM_RESET2, which the unit answered NOT_SUPPORTED',
                '           (OBSERVED once; the PSCI text was not re-read here: INFERRED). So NONE is a likely outcome of this run, and it says',
                '           nothing is wrong with the probe or the unit.']
    out += ['           It calls no other function (not CPU_ON, CPU_OFF, SYSTEM_OFF or SYSTEM_SUSPEND, no vendor call) and writes no other system',
            '           register and no device register (no xHC, GIC or SMMU register).',
            '',
            '3. What writes the stick, and who',
            '   The UEFI Shell only, as it already writes the DONE markers: SP11\\DONE1.TXT before step 1 starts, and in step 2 SP11\\RET2.TXT (the',
            '   three pages, the Shell\'s memory map and its date and time as text, a few tens of KiB), then SP11\\DONE2.TXT. The result file is',
            '   never overwritten: step 2 refuses if it exists. No code at EL2 writes the stick: no USB bring-up and no sector write by any code',
            '   of this plan (authorisation points A1 and A2 of docs/SP11-STICK-LOG.md are neither granted nor used).']
    if path != 'none':
        out += ['   The reset call quiesces no device, so for the stick\'s FAT and the internal NVMe it acts as a power cut would (INFERRED); the one',
                '   write to the stick before it, SP11\\DONE1.TXT, was made at least 5 s earlier (the pause of step 1), and this plan writes nothing to',
                '   the internal disk.']
    out += ['',
            '4. What is not written',
            '   The internal disk and its partition table, NVRAM and UEFI variables, any firmware setting (the boot order, Secure Boot), any device',
            '   register, any clock or voltage.']
    if unattended:
        out += ['   The boot order is a persistent firmware setting. This tool does not change it. The unattended plan exists only because the',
                '   operator set the stick first themselves and said so (--stick-first-authorised, recorded in SP11\\PLAN.TXT); they put it back after.',
                '   That line is a record of their decision and not an interlock: nothing here checks the boot order.']
    out += ['',
            '5. What the operator does',
            '   The actions printed with this record by tools/sp11_retention_plan.py: start the stick and wait;',
            '   ' + ('nothing more until the result is written' if unattended else 'hold Volume-Down through the restart (no key at all for path none)') + ';',
            '   then take the stick to the Mac and read it with tools/sp11_retention_check.py. No photograph is needed.',
            '',
            '6. Stop rules',
            '   - A page line that starts with REFUSED or VERIFY FAILED, before any reset call: nothing was reset. Stop, power off, report the line.',
            '     Any other surprise before the call (a hang with the page stuck): hold the power button, and report what you saw.']
    if path != 'none':
        out += ['   - From the reset call until SP11\\RET2.TXT is written, a power cycle loses the pages, so the power button is not the answer to a dark',
                f'     or unexpected screen: if the unit does not show the Shell countdown within {HOLD_SECONDS} s of the restart, stop, do not power-cycle in this',
                '     run, touch nothing, and report what the unit did, in words.']
    out += ['   - A line RESET RETURNED: the call came back. Stop: no second call and no other path in this run.',
            '   - Danger overrides all of the above: heat, a fan at full speed, a smell or smoke: hold the power button at once and say so.',
            '   - The run is repeated only by a new approval of a new record, never by habit.',
            '',
            '7. What approving this record does not cover',
            '   Another reset path, SYSTEM_RESET2 (answered NOT_SUPPORTED on the unit once), a second reset call, more or other pages,',
            '   any write to the stick by code at EL2, a change of the boot order (unless this plan is the unattended one and you made it),',
            '   any NVRAM or firmware write, and a second payload in the same boot.',
            '',
            '8. Approval',
            f'   Approved by the operator for one run of the plan {plan}, for exactly the files named in section 1.',
            '   Operator: ______________________   Date and time: ______________________   SHA-256 of this record: ______________________',
            '']
    return '\n'.join(out)


def build(payload: bytes, path: str, countdown: int, pages, out_dir: Path, unattended: bool = False, stick_first_authorised: bool = False,
          slbounce: str = DEFAULT_SLBOUNCE, expect_refusal: bool = False) -> dict:
    """Validates the request, writes RETPATH.DAT, PAYLOAD.MAN and RECORD.TXT under `out_dir`, and returns the facts the staging commands and the
    record use. The manifest lists the payload and the configuration and nothing else, and it is checked with the rules the stager applies to
    the data files of a retention plan, so that a record never names a file set the stager would refuse."""
    pages = tuple(pages)
    check_request(path, countdown, pages, unattended, stick_first_authorised, expect_refusal)
    plan = autorun.RETENTION_UNATTENDED_PLAN if unattended else autorun.RETENTION_PLAN
    zero = '0' * 64
    config = retention.config_bytes(retention.PATHS[path], countdown, pages)
    try:
        manifest = autorun.render_payload_manifest([(autorun.RETENTION_PAYLOAD, payload), (autorun.RETENTION_CONFIG, config)])
        staged = [autorun.DataFile(name, len(blob), hashlib.sha256(blob).hexdigest()) for name, blob in
                  ((autorun.MANIFEST_NAME, manifest), (autorun.RETENTION_PAYLOAD, payload), (autorun.RETENTION_CONFIG, config))]
        autorun.attach_data(autorun.build_plan(plan, step_texts(pages, slbounce), {slbounce: zero, autorun.RUNNER: zero}, stick_first_authorised), staged, manifest)
    except autorun.PlanError as error:
        raise PlanRefusal(error.reason, error.detail) from error
    record = authorisation_record(plan, hashlib.sha256(payload).hexdigest(), len(payload), hashlib.sha256(config).hexdigest(), hashlib.sha256(manifest).hexdigest(),
                                  len(manifest), path, countdown, pages, slbounce, unattended).encode('ascii')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / autorun.RETENTION_CONFIG).write_bytes(config)
    (out_dir / autorun.MANIFEST_NAME).write_bytes(manifest)
    (out_dir / 'RECORD.TXT').write_bytes(record)
    return {'plan': plan, 'steps': step_texts(pages, slbounce), 'config': config, 'manifest': manifest, 'record': record,
            'record_sha256': hashlib.sha256(record).hexdigest(), 'config_sha256': hashlib.sha256(config).hexdigest(),
            'manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'payload_sha256': hashlib.sha256(payload).hexdigest()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--payload', type=Path, required=True, help='the built retprobe.bin (its size and SHA-256 go into the record)')
    ap.add_argument('--path', choices=sorted(retention.PATHS), default='none', help='the reset path; the default is none, the negative control')
    ap.add_argument('--countdown', type=int, default=8)
    ap.add_argument('--pages', nargs=3, type=lambda s: int(s, 0), default=list(retention.UNIT_PAGES), metavar='ADDR')
    ap.add_argument('--memmap', nargs='*', type=Path, default=[], help='memory maps (SP11 probe format) the pages must be conventional in')
    ap.add_argument('--slbounce', default=DEFAULT_SLBOUNCE)
    ap.add_argument('--unattended', action='store_true', help='plan retention-m10-auto: no manual restart, the stick first in the boot order')
    ap.add_argument('--stick-first-authorised', action='store_true', help='the operator authorised the stick first in the boot order')
    ap.add_argument('--expect-refusal', action='store_true', help='build a run the unit is expected to refuse (SYSTEM_RESET2)')
    ap.add_argument('--out', type=Path, required=True, help='a directory below build/: RETPATH.DAT, PAYLOAD.MAN and RECORD.TXT are written there')
    args = ap.parse_args(argv)
    try:
        for memmap in args.memmap:
            try:
                problems = retention.page_problems(args.pages, retention.parse_probe_memmap(memmap.read_text(encoding='ascii')))
            except retention.MapError as error:
                raise PlanRefusal('memory_map_unreadable', f'{memmap.name}: {error}') from error
            if problems:
                raise PlanRefusal('pages_not_in_conventional_memory', f'{memmap.name}: ' + '; '.join(problems))
        built = build(args.payload.read_bytes(), args.path, args.countdown, args.pages, args.out, args.unattended, args.stick_first_authorised,
                      args.slbounce, args.expect_refusal)
    except PlanRefusal as error:
        print(f'REFUSED {error.reason}: {error}')
        return 2
    plan = built['plan']
    print(f'plan {plan}: record {len(built["record"])} bytes SHA-256 {built["record_sha256"]}; configuration {built["config_sha256"]}; '
          f'manifest {built["manifest_sha256"]}')
    for step in built['steps']:
        print(f'  --step "{step}"')
    print(f'stage with: python3 tools/stage-sp11-boot-media.py autorun --dest <staged tree> --name {plan} --file {args.slbounce} <PATH> <SHA256> '
          f'--file {autorun.RUNNER} <PATH> <SHA256> --data {autorun.MANIFEST_NAME} {args.out / autorun.MANIFEST_NAME} {built["manifest_sha256"]} '
          f'--data {autorun.RETENTION_PAYLOAD} {args.payload} {built["payload_sha256"]} '
          f'--data {autorun.RETENTION_CONFIG} {args.out / autorun.RETENTION_CONFIG} {built["config_sha256"]}'
          + (' --stick-first-authorised' if args.unattended else '') + ' --step ... --step ...')
    print()
    print(operator_actions(plan, args.path, args.countdown, args.unattended), end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
