# SPDX-License-Identifier: MIT
"""What the authorisation record of a monitor entry run (--m1-run of tools/sp11_stick_bundle_build.py; docs/SP11-STICK-MONITOR.md section 10) is
made from besides the bundle: the build report of tools/sp11_stick_wrapper_build.py --monitor, read strictly and checked against the bytes the
builder was given as --monitor and --reporter, the words of the PARAMS chunk that must be zero for this run, and the device models it must not list (any of
the ANS family: an ANS window makes the monitor call nvmeh_sp11_adopt with two blocks that nothing of the stick bundle places; docs/SP11-STICK-MONITOR.md
section 18).

Nothing in the report is taken on trust: every size and digest the record prints is recomputed from the files, the layout of SNAPMON-M1.BIN
(wrapper, zero padding, monitor) is checked byte for byte, and the NV2 record the monitor was built with is found in the monitor's own bytes,
parsed by tools/nv2_record.py and compared with the digest the report names. The text of the record is in tools/sp11_stick_bundle_record.py.

The interlock between the two kinds of record. The record of the GIC and stop dry run and the older ones say that no monitor is called, so
a wrapper that calls one must never be authorised under them (WRAPPER_CALLS_MONITOR, every kind but this one); and this kind must not
authorise a wrapper that calls none, or a monitor that the build report does not name (M1_NO_MONITOR_CALL, M1_REPORT_MISMATCH). The call is
recognised from the file, not from a report: by the direct `bl` into a zero-padded page that tools/sp11_stick_wrapper_build.py --monitor lays out,
and by the text of the wrapper's monitor stage. What that cannot show is in `monitor_call_evidence`. The reporter slot is executed in the same boot,
so --reporter is refused for the same name when it holds that `bl`, under every kind: the `bl` alone (`refuse_monitor_reporter`), because the
reporter of this kind has the stage's text by design.

Evidence: EXPERIMENTAL, hardware NOT RUN. Nothing here approves anything."""
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nv2_record  # noqa: E402
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_profile as gpb  # noqa: E402

REFUSALS = ('M1_REPORT_BAD', 'M1_REPORT_MISMATCH', 'PARAMS_LAUNCH', 'PARAMS_ANS', 'WRAPPER_CALLS_MONITOR', 'M1_NO_MONITOR_CALL')     # beside BUILDER_REFUSALS: docs/SP11-STICK-BUILD.md lists those name for name
ch.KNOWN_REFUSALS.update(REFUSALS)
ch.KNOWN_REFUSALS.add('OPTIONS_BAD')
Refused = ch.Refused
IMAGE, REPORTER = 'SNAPMON-M1.BIN', 'SNAPREP-M1.BIN'
# The opt-in CPU-start choice of the monitor build (docs/SP11-MONITOR-PROFILE.md section 10): `stop` is every record that exists; `ack` is the EXPERIMENTAL acknowledging
# monitor, whose pair has files of its own. A report is of exactly one choice and a record is for exactly one: the other is M1_REPORT_MISMATCH, never a record.
CPU_STARTS = ('stop', 'ack')
CPU_START_FILES = {'stop': (IMAGE, REPORTER), 'ack': ('SNAPMON-M1-ACK.BIN', 'SNAPREP-M1-ACK.BIN')}
ACK_DEFINE = '-DSNAP_SP11_CPU_START_ACK=1'     # tools/sp11_m1_profile.py CPU_START_DEFINES['ack']
ACK_SPELLING = re.compile(r'-DSNAP_SP11_CPU_START_ACK(=.*)?')
PROFILE = 'sp11-m1'
LABEL_PREFIX = 'M1RUN-'         # SP11_STICK_MONITOR_LABEL_PREFIX in probes/sp11-stick-monitor.h: what the wrapper of this kind requires at the start of PIN.DAT's label
PANEL_MARKS = {'withheld': '', 'handed': 'PANEL-', 'console': 'CONSOLE-'}      # after LABEL_PREFIX in the label of each choice of D9 (docs/SP11-STICK-MONITOR.md section 20): the wrapper checks LABEL_PREFIX only, so these are the builder's rule
D9_PANELS = tuple(PANEL_MARKS)  # the --panel choices this record is written for, each with a stock kernel (--sptm-hvc-calls 0)
# The words that the monitor entry run may add to the guest's command line after its choice's own, each the one change of a record of its own (docs/SP11-STICK-MONITOR.md
# section 20, K10, K13 and K14), with the marker that the label carries after LABEL_PREFIX in place of the panel's: PIN.DAT's label holds 23 characters, so CONSOLE-CPUS1- would leave three for
# the facts' label. Only the panel as the console takes one (BOOT_ARG_PANEL); any other word is BOOT_ARG_REFUSED, never passed on to the guest. Each word goes with one choice of the
# monitor's CPU-start (BOOT_ARG_CPU_START): cpus=1 asks XNU for one CPU and goes with the plain monitor (`stop`); cpu_boot_timeout_secs=120 and =90 set XNU's wait for a CPU that was
# started and go with the acknowledging monitor (`ack`), which starts none: =120 is the word of rung 5 (K13: a wait longer than its 60 s box) and =90 that of rung 6 (K14: a wait that
# ends inside its 120 s box). Both put ACKPANIC- in the label, and the facts' label tells the two records apart (RUNG5, RUNG6).
BOOT_ARGS = {'cpus=1': 'CPUS1-', 'cpu_boot_timeout_secs=120': 'ACKPANIC-', 'cpu_boot_timeout_secs=90': 'ACKPANIC-'}
BOOT_ARG_CPU_START = {'cpus=1': 'stop', 'cpu_boot_timeout_secs=120': 'ack', 'cpu_boot_timeout_secs=90': 'ack'}
BOOT_ARG_PANEL = 'console'
# D5 (2026-11-16) and D8 (2026-11-17) decided a time box of 5 s, DECIDED_BOX_S (tools/test-sp11-stick-monitor-rung5.py holds it to the guide's D5_BOX_S), which every record up to rung 4
# carries; D5' (2026-11-18, the operator's answer "Yes, up to 60 s") lets rung 5's record, the acknowledging monitor's, carry RUNG5_BOX_MAX_S with AC power; D5'' (2026-11-19, the operator's
# answer "let's go with 120 seconds" to whether rung 6's box should be 120 s or 60 s) is RUNG6_BOX_S, which approving its record confirms (K14).
DECIDED_BOX_S = 5
RUNG5_BOX_MAX_S = ch.RUNG5_FORM[2]
RUNG6_BOX_S = ch.RUNG6_FORM[2]      # rung 7's and rung 8's forms ask the same box (ch.RUNG7_FORM, ch.RUNG8_FORM): their approvals confirm D5'' for them as well (K15, K16)
ch.KNOWN_REFUSALS.add('BOOT_ARG_REFUSED')
# The one PARAMS word of the monitor's own that a record of this tool sets: let_panic, word 23 of struct params at offset 0xB8 (probes/xnuboot/report_layout.c), which the monitor reads at
# one place only (probes/xnuboot/trap_dispatch.c:370-387) to run XNU's own handler for a guest EL1 exception that it would otherwise stop at (docs/SP11-STICK-MONITOR.md section 20, K12).
# --let-panic is for three records (--panel, --boot-arg, --time-box-s; chunks.py holds the list): rung 4's, LET_PANIC_FORM, the panel as the console with cpus=1 and the 5 s box and the
# plain monitor, whose label carries LET_PANIC_MARK in place of CPUS1-; and the two of the acknowledging monitor, ACK_FORMS (K13, K14): rung 5's, the panel as the console with
# cpu_boot_timeout_secs=120 and a box of RUNG5_BOX_MAX_S s, rung 6's, with cpu_boot_timeout_secs=90 and a box of RUNG6_BOX_S s, and rung 7's, with cpu_boot_timeout_secs=120, the box of
# RUNG6_BOX_S s and the guest profile quiet (K15), and rung 8's, which is rung 7's with the profile quiet-rtc (K16). No other combination. permissive and skip_undef are the monitor's other diagnostic words of this kind: no record sets them.
LET_PANIC_FORM = ch.LET_PANIC_FORM
RUNG5_FORM, RUNG6_FORM, RUNG7_FORM, RUNG8_FORM, ACK_FORMS = ch.RUNG5_FORM, ch.RUNG6_FORM, ch.RUNG7_FORM, ch.RUNG8_FORM, ch.ACK_FORMS
LET_PANIC_MARK = 'LETPANIC-'
DIAG_WORDS = (('permissive', 21), ('let_panic', 23), ('skip_undef', 28))
LET_PANIC_AT = 8 * 23
ch.KNOWN_REFUSALS.add('LET_PANIC_REFUSED')
PIN_LABEL_MAX = 23              # sp11sb_string_ok: build_label is 24 bytes and a string of 1 to 23 characters
EFI_NAMES = ('SLBOUNCE-DEVHE-MARK.EFI', 'Q1N1-PAYLOAD.EFI')       # the two files that run at EL2 before the wrapper: the builder's SLBOUNCE and RUNNER
REPORT_CAP = 1 << 20
BOX_MAX_S = 120         # the longest time box that a wrapper built from this tree accepts: SP11_STICK_MONITOR_BOX_MAX_S (probes/sp11-stick-monitor.h), a compile-time constant that tests compare with this one
# The cap is a constant of the wrapper that the report of its build does not carry, so the builder knows the pairs that were built with it at 5 s by their SNAPMON digest: the first run's
# (SNAPMON-M1.BIN, the pair of every record up to rung 4, K12) and the acknowledging pair that was made at the cap of 5 (the ack-only tree's). Their wrappers refuse a box over 5 s before they
# call the monitor, so a record with a longer box for one of them is refused here and not on the unit (OPTIONS_BAD), and the record's text names 5 s for them; any other pair has BOX_MAX_S.
LOW_CAP_MONITORS = {'1d32f5071506a00c91be8c7e218a6db9ba6d280c1cf25be31aac41a3c599171c': 5, '54305009c913e74af955d76b83ff99b338541498cf0866d67a33a4c53e0b017a': 5}
# The pair built with `tools/sp11_stick_wrapper_build.py --root-carry` (docs/SP11-STICK-ROOT.md): its wrapper takes files of up to 2 GiB and a box of up to ROOT_CARRY_BOX_MAX_S s, and it is
# a different pair from every pair of ACK_PAIRS: no form of this module names it, so every record that exists refuses it (M1_REPORT_MISMATCH) and every box over BOX_MAX_S stays refused
# (check_options). The digests are those of the build with the monitor of rungs 5 to 7 (053a239f...); a monitor that changes makes a new pair.
ROOT_CARRY_BOX_MAX_S = 1200
ROOT_CARRY_PAIRS = {'6af26a840736a7148e8b213ffc5c2e4e57fc4f4e3fbfb271380f382205e0e456': '562b9b6eeaf97b87a4fb844d4368543a2f6ba8c28f5363451ba244a97d50bb80'}


def box_cap(monitor_sha256):
    """The longest time box that the wrapper of the pair with this SNAPMON-M1(-ACK).BIN digest accepts: 5 s for LOW_CAP_MONITORS, ROOT_CARRY_BOX_MAX_S for ROOT_CARRY_PAIRS, BOX_MAX_S for any other."""
    return ROOT_CARRY_BOX_MAX_S if monitor_sha256 in ROOT_CARRY_PAIRS else LOW_CAP_MONITORS.get(monitor_sha256, BOX_MAX_S)


def check_pair_box(time_box_s, monitor_sha256):
    """OPTIONS_BAD for a time box that the wrapper of this pair would refuse before it calls the monitor (box_cap)."""
    if time_box_s > box_cap(monitor_sha256):
        raise ch.Refused('OPTIONS_BAD', f'--time-box-s {time_box_s}: the wrapper of this pair (SNAPMON {monitor_sha256[:8]}...) was built with the box cap at {box_cap(monitor_sha256)} s and '
                                        'refuses a longer box before it calls the monitor; give the pair of the record (K13)')
# The pair that the record of each acknowledging form is written for (K13 to K16): the SHA-256 of its SNAPMON-M1-ACK.BIN and of its SNAPREP-M1-ACK.BIN, by the form's key, which is its
# word, or, for a form that carries a guest profile (rungs 7 and 8), the profile's name (the word of rung 7 is rung 5's, so the word alone would name rung 5's pair). The text of a record says what
# the page of its reporter prints, and the builder reads the pair from a report that it checks against the files (read_report), so a pair that is not the form's is refused where the report is
# read, whichever way round: the record of rung 6 does not describe the reporter of rung 5 and the record of rung 5 does not describe the other. Rung 7 uses rung 6's pair.
ACK_MONITOR_SHA256 = 'bb34b9701d89302f425138b6229fd334ba9ead54699db2a131fc9a99cb22999c'
ACK_REPORTER5_SHA256 = 'ee86676001d89cf653af7bdddd797fef3c54d31b28ee42eb4a2d7aa692ee4b3b'
ACK_REPORTER6_SHA256 = '533d004c27a11da964048aba34fa5a4a32309f0761c100b0959400d099c40368'
ACK_PAIRS = {RUNG5_FORM[1]: (ACK_MONITOR_SHA256, ACK_REPORTER5_SHA256), RUNG6_FORM[1]: (ACK_MONITOR_SHA256, ACK_REPORTER6_SHA256),
             ch.RUNG7_PROFILE: (ACK_MONITOR_SHA256, ACK_REPORTER6_SHA256), ch.RUNG8_PROFILE: (ACK_MONITOR_SHA256, ACK_REPORTER6_SHA256)}


def check_pair_form(cpu_start, boot_arg, monitor_sha256, reporter_sha256, profile=None):
    """M1_REPORT_MISMATCH when the acknowledging pair of a record is not the pair that its form is written for (ACK_PAIRS, by the guest profile when there is one, else by the word). The
    plain monitor has no table: its records name their pair in their text and are held to it by their approved SHA-256."""
    key = profile if profile not in (None, ch.GUEST_PROFILE_DEFAULT) else boot_arg
    if cpu_start == 'ack' and (monitor_sha256, reporter_sha256) != ACK_PAIRS.get(key):
        want = ACK_PAIRS.get(key, ('none',) * 2)
        raise ch.Refused('M1_REPORT_MISMATCH', f'the pair (SNAPMON-M1-ACK.BIN {monitor_sha256[:8]}..., SNAPREP-M1-ACK.BIN {reporter_sha256[:8]}...) is not the pair that the record of '
                                               f'{key} is written for ({want[0][:8]}... and {want[1][:8]}...)')
TEST_TOOL = 'wi4b-test-only'        # the `tool` field of the LOCAL TEST RECORD that the host tests build a monitor with: never the unit's
PARAMS_BYTES = 0x2410       # sizeof(struct params) (probes/xnuboot/report_layout.c): the wrapper refuses a shorter chunk before the call
# What the monitor would do with these words (docs/SP11-STICK-MONITOR.md section 10): gpu.flags opens the GPU paths (one SMC among them),
# early_stop.flags bit 1 a call through a PARAMS pointer and bit 3 the loads from the A16's sensor addresses (record section 3b), txm_native (image,
# work, size, tree_size) a native TXM image. The wrapper reads the first two before the call; the third only this builder reads.
LAUNCH_WORDS = (('gpu.flags', 0x2340, 8), ('txm_native', 0x23C0, 32), ('early_stop.flags', 0x23E0, 8))
# The device models of struct params (probes/xnuboot/report_layout.c, compat/device.h): model_count at 0x2D0, then DEVICE_MODEL_MAX slots of struct device_model
# {kind, base, span, config[5]}, 64 bytes each, from 0x2D8. ans_window() (probes/xnuboot/ans_glue.c) is true for a DEVICE_ANS_NVME_V1 model whose config[3] is
# ANS_WINDOW_MODE, and only then does ans_window_prepare call nvmeh_sp11_adopt, whose target and record blocks a host tool places in the window reservation
# (docs/SP11-NVME-WINDOW.md section 2.3). Nothing of the stick bundle places them, so this kind lists no ANS model at all. The wrapper does not read these words:
# only this builder does. tools/test-sp11-stick-monitor-ans.py holds the numbers against the headers and the monitor's own translation unit.
MODEL_COUNT_AT, MODELS_AT, MODEL_BYTES, MODEL_MAX = 0x2D0, 0x2D8, 64, 128
ANS_KINDS = {49: 'DEVICE_ANS_ASC_V1', 50: 'DEVICE_ANS_NVME_V1'}
ANS_NVME_KIND, ANS_WINDOW_AT, ANS_WINDOW_MODE = 50, 24 + 3 * 8, 1       # config[3] of a model: kind, base and span (24 bytes) come first
# What the wrapper tool leaves in front of an appended monitor, and what the wrapper prints when it has one (probes/sp11-stick-monitor.c) or has none
# (probes/sp11-stick-wrapper.c). The first 1 MiB of a file is searched for a call: the wrapper is under the monitor's offset, 128 KiB by default.
SCAN_BYTES, PAGE, PAD_BYTES = 1 << 20, 4096, 16
STAGE_TEXT = (b'MON REGS RESTORED=', b'MON QUARANTINE: THE REPORT IS PARTIAL')
NOT_CALLED_TEXT = b'MONITOR NOT CALLED'
HEX64 = re.compile(r'[0-9a-f]{64}')
NV2_KEYS = ('soc', 'tool', 'build_id', 'evidence', 'vncr_redirect', 'negative_control_trapped', 'midr_el1', 'revidr_el1', 'id_aa64mmfr2_el1',
            'id_aa64mmfr4_el1')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('a key appears twice')
        out[key] = value
    return out


def check_options(stores, gic_stop, time_box_s, panel=None, hvc_calls=None, boot_arg=None, let_panic=False, cpu_start='stop', profile=None):
    """--m1-run runs the wrapper's GIC arm (gic), the monitor (el2_sysreg) and RAM stores, with the time box the wrapper's pre-call check accepts. With `real`
    (panel and hvc_calls given) D9 admits a stock kernel (--sptm-hvc-calls 0) with the panel withheld (the first choice), handed (the second) or handed as the
    kernel's console (`console`: serial=2 in the command line, as probes/run.py makes it for the A16's native panel console)
    (tools/sp11_stick_bundle_panel.py; docs/SP11-STICK-MONITOR.md section 20): the record says which, from the bytes of the guest, and says nothing true of
    a patched kernel, so --sptm-hvc-calls 1 stays refused for all three. `boot_arg` is one word of BOOT_ARGS for the panel as the console, with the CPU-start choice that it goes
    with (check_boot_arg), and `let_panic` the option of check_let_panic, which the acknowledging monitor (`cpu_start` ack) takes with its word and no other record does."""
    need = sb.STORE_RAM | sb.STORE_EL2_SYSREG | sb.STORE_GIC
    if stores & need != need:
        raise ch.Refused('OPTIONS_BAD', '--m1-run stores in all three classes: --stores must include ram, el2_sysreg and gic')
    if gic_stop:
        raise ch.Refused('OPTIONS_BAD', '--m1-run and --gic-stop are two kinds of record: give one')
    if not 1 <= time_box_s <= BOX_MAX_S:
        raise ch.Refused('OPTIONS_BAD', f'--m1-run: the wrapper refuses a time box outside 1 to {BOX_MAX_S} s before it calls the monitor (SP11_STICK_MONITOR_BOX_MAX_S)')
    if panel is not None and (panel not in D9_PANELS or hvc_calls != 0):
        raise ch.Refused('OPTIONS_BAD', f'--m1-run with real: D9 admits --panel withheld (the first choice), --panel handed (the second) or --panel console (the panel '
                                        f'as the kernel\'s console), each with --sptm-hvc-calls 0 (a stock kernel), and this record is written for no other choice; '
                                        f'got --panel {panel} and --sptm-hvc-calls {hvc_calls}')
    check_boot_arg(boot_arg, panel, True, cpu_start)
    check_let_panic(let_panic, panel, boot_arg, time_box_s, True, cpu_start, profile)


def check_let_panic(let_panic, panel, boot_arg, time_box_s, is_m1=True, cpu_start='stop', profile=None):
    """--let-panic is for the four records that set the monitor's let_panic word, in the monitor entry run: rung 4's (LET_PANIC_FORM: the plain monitor, the panel as the console
    with cpus=1 and the 5 s box) and the four of the acknowledging monitor (ACK_FORMS, `cpu_start` ack, the panel as the console): rung 5's, with cpu_boot_timeout_secs=120 and the 60 s of D5',
    rung 6's, with cpu_boot_timeout_secs=90 and the 120 s of D5'', rung 7's, with cpu_boot_timeout_secs=120, the 120 s and the guest profile quiet, and rung 8's, the same with the profile quiet-rtc, each with exactly its box (and
    `profile`, the guest profile's name: rungs 7 and 8 only, ch.FORM_PROFILE). LET_PANIC_REFUSED for any other combination or kind of record, before any guest is built; GUEST_PROFILE_REFUSED when
    the combination is a form's but its profile is not (rung 7's form without its profile, another form with one). The acknowledging monitor has those four records and no other, so it is
    refused without --let-panic as well. False is no option."""
    if not let_panic:
        if cpu_start == 'ack' and is_m1:
            raise ch.Refused('LET_PANIC_REFUSED', '--cpu-start ack is for the records of rungs 5 to 8 and goes with --let-panic and no other option set: the acknowledging monitor has no other '
                                                  'record (docs/SP11-STICK-MONITOR.md section 20, K13 to K16)')
        return
    if not is_m1:
        raise ch.Refused('LET_PANIC_REFUSED', '--let-panic is a choice of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K12): it needs --m1-run')
    if cpu_start == 'stop' and time_box_s is not None and time_box_s > LET_PANIC_FORM[2]:
        raise ch.Refused('OPTIONS_BAD', f'--m1-run: rung 4\'s record is for the first run\'s pair, whose wrapper refuses a time box over {LET_PANIC_FORM[2]} s before it calls the monitor '
                                        f'(docs/SP11-STICK-MONITOR.md section 20, K12); got {time_box_s}')
    if not ch.let_panic_form_ok(panel, boot_arg, time_box_s, cpu_start, profile):
        if ch.let_panic_form_ok(panel, boot_arg, time_box_s, cpu_start, any_profile=True):
            raise ch.Refused('GUEST_PROFILE_REFUSED', f'--guest-profile {profile or "(none)"} is not the guest profile of this form: only --boot-arg {ch.RUNG7_FORM[1]} --time-box-s '
                                                      f'{ch.RUNG7_FORM[2]} has one, {ch.RUNG7_PROFILE} (rung 7) or {ch.RUNG8_PROFILE} (rung 8), and it has no other (docs/SP11-M1-GUEST-PROFILES.md section 5)')
        raise ch.Refused('LET_PANIC_REFUSED', f'--let-panic is for {ch.LET_PANIC_FORMS_TEXT} and no other combination; '
                                              f'got --panel {panel} --boot-arg {boot_arg} --time-box-s {time_box_s} --cpu-start {cpu_start}')


def check_boot_arg(boot_arg, panel, is_m1=True, cpu_start='stop'):
    """--boot-arg is one word of BOOT_ARGS, for the panel as the console and for the CPU-start choice that the word goes with (BOOT_ARG_CPU_START), in the monitor entry run:
    BOOT_ARG_REFUSED for anything else (another word, another value of a word, cpumask= and every other word, another choice, another kind of record, the acknowledging
    monitor without its word), before any guest is built. None is no word, which only the plain monitor takes."""
    if boot_arg is None:
        if cpu_start == 'ack' and is_m1:
            raise ch.Refused('BOOT_ARG_REFUSED', '--cpu-start ack needs --boot-arg cpu_boot_timeout_secs=120 (rungs 5 and 7) or cpu_boot_timeout_secs=90 (rung 6): the acknowledged guest waits for a CPU '
                                                 'that is never started, and without the word its XNU gives up on that CPU after its own default wait, which is shorter than the box '
                                                 '(docs/SP11-STICK-MONITOR.md section 20, K11)')
        return
    if not is_m1:
        raise ch.Refused('BOOT_ARG_REFUSED', '--boot-arg is a choice of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K10): it needs --m1-run')
    if boot_arg not in BOOT_ARGS:
        words = ' and '.join(f'{word} (with --cpu-start {choice})' for word, choice in BOOT_ARG_CPU_START.items())
        raise ch.Refused('BOOT_ARG_REFUSED', f'--boot-arg admits {words} and no other word or value (cpumask= least of all: XNU takes it with cpus= as an incorrect configuration); '
                                             f'got {boot_arg!r}')
    if panel != BOOT_ARG_PANEL:
        raise ch.Refused('BOOT_ARG_REFUSED', f'--boot-arg {boot_arg} is a word of --panel {BOOT_ARG_PANEL} only; got --panel {panel}')
    if cpu_start != BOOT_ARG_CPU_START[boot_arg]:
        raise ch.Refused('BOOT_ARG_REFUSED', f'--boot-arg {boot_arg} goes with --cpu-start {BOOT_ARG_CPU_START[boot_arg]} only; got --cpu-start {cpu_start} (docs/SP11-STICK-MONITOR.md '
                                             'section 20, K11)')


def check_cpu_start(cpu_start, is_m1):
    """OPTIONS_BAD unless `cpu_start` (--cpu-start) is one the record of this kind can carry. `stop` is every record there is and needs nothing. `ack` is the EXPERIMENTAL
    acknowledging monitor (docs/SP11-MONITOR-PROFILE.md section 10): it needs --m1-run, and it needs a record text that says what the acknowledge is (a CPU-start that is
    taken and not performed, a diagnostic and not a model of the hardware, a stop rule that differs from the plain run's), which the text of a `stop` record does not. That
    text is the record generator's: until sp11_stick_bundle_record_m1.CPU_START_TEXTS lists 'ack' the builder writes no record for the acknowledging monitor, because a
    record that names its digest and says nothing of what it does would be approved on the strength of a text that is not about it."""
    if cpu_start not in CPU_STARTS:
        raise ch.Refused('OPTIONS_BAD', f'--cpu-start is one of {", ".join(CPU_STARTS)}')
    if cpu_start == 'stop':
        return
    if not is_m1:
        raise ch.Refused('OPTIONS_BAD', '--cpu-start ack belongs to the monitor entry run: it needs --m1-run')
    import sp11_stick_bundle_record_m1 as text
    if cpu_start not in getattr(text, 'CPU_START_TEXTS', ('stop',)):
        raise ch.Refused('OPTIONS_BAD', '--cpu-start ack: the text of the M1 record does not yet say what the acknowledging monitor does (sp11_stick_bundle_record_m1.'
                                        'CPU_START_TEXTS lists no `ack`), so no record is written for it; docs/SP11-MONITOR-PROFILE.md section 10 lists what that text must say')


def pinned_label(label, is_m1, choice='withheld', boot_arg=None, let_panic=False, cpu_start='stop', *, profile=None):
    """The build_label PIN.DAT carries. For this kind the marker comes first (the wrapper of this kind refuses a PIN.DAT without it, D2) and, for a guest that is
    handed the panel, the marker of that choice (PANEL_MARKS) after it, so that the three choices of D9 have three labels, or, for a guest with a boot-arg word of its own
    (BOOT_ARGS), that word's marker in its place, or, for the guest of --let-panic with the plain monitor, LET_PANIC_MARK in place of that (the guest of rung 5 has the marker of
    its word, which only the acknowledging monitor with --let-panic reaches), so that the record of that guest has a label of its own, and, for a guest with a profile of the
    menu (`profile` is its name: sp11_stick_bundle_profile.MARKS), the two characters of its mark after that, whatever the marker is;
    for every other kind the label is the facts' and may not start with the marker, or a PIN.DAT of another kind would pass for this one. The facts label of this kind may
    not start with a marker of a choice, of a word, of --let-panic or of a profile either: M1RUN-PANEL- is then the label of the choice that hands the panel, M1RUN-CONSOLE- that of
    the panel as the console, M1RUN-CPUS1- that of the panel as the console with cpus=1, M1RUN-LETPANIC- that of the same with let_panic and M1RUN-ACKPANIC- that of the
    acknowledging monitor with let_panic (rungs 5 and 6 alike: the facts' label, RUNG5 or RUNG6, tells their records apart); M1RUN-ACKPANIC-Q- is that of the same with the quiet profile
    (rung 7; the marks of the profiles are two characters), M1RUN-ACKPANIC-R- that of rung 8 (the profile quiet-rtc), and of no other.
    PIN_STRING: it does not fit."""
    if label.startswith(LABEL_PREFIX):
        raise ch.Refused('PIN_STRING', f'build_label starts with {LABEL_PREFIX}, which only --m1-run adds')
    for owner, mark in PANEL_MARKS.items():
        if is_m1 and mark and label.startswith(mark):
            raise ch.Refused('PIN_STRING', f'build_label starts with {mark}, which only --m1-run with --panel {owner} adds after {LABEL_PREFIX}')
    for word, mark in BOOT_ARGS.items():
        if is_m1 and label.startswith(mark):
            raise ch.Refused('PIN_STRING', f'build_label starts with {mark}, which only --m1-run with --boot-arg {word} adds after {LABEL_PREFIX}')
    if is_m1 and label.startswith(LET_PANIC_MARK):
        raise ch.Refused('PIN_STRING', f'build_label starts with {LET_PANIC_MARK}, which only --m1-run with --let-panic adds after {LABEL_PREFIX}')
    for owner, mark in gpb.MARKS.items():
        if is_m1 and mark and label.startswith(mark):
            raise ch.Refused('PIN_STRING', f'build_label starts with {mark}, which only --m1-run with --guest-profile {owner} adds after the marker of the guest\'s choices')
    if boot_arg is not None:
        check_boot_arg(boot_arg, choice, is_m1, cpu_start)
    check_let_panic(let_panic, choice, boot_arg, None, is_m1, cpu_start, profile)
    gpb.check_form(profile, boot_arg, let_panic, cpu_start, choice, is_m1)
    front = LABEL_PREFIX + (LET_PANIC_MARK if let_panic and cpu_start == 'stop' else BOOT_ARGS[boot_arg] if boot_arg is not None else PANEL_MARKS[choice]) + gpb.mark_of(profile)
    if is_m1 and len(front + label) > PIN_LABEL_MAX:
        raise ch.Refused('PIN_STRING', f'--m1-run puts {front} in front of build_label and the pinned label holds {PIN_LABEL_MAX} characters: the facts label holds '
                                       f'{PIN_LABEL_MAX - len(front)}')
    return front + label if is_m1 else label


def read_efi(given, files, is_m1):
    """{name: sha256} of the two EFI files that run at EL2 before the wrapper, for section 1b of the record, from --m1-efi NAME=SHA256 (both, once each) and
    checked against the stage tree's --file NAME PATH SHA256 when those are given: the record must name the files the tree is made of. None for another
    kind, which refuses --m1-efi."""
    if not is_m1:
        if given:
            raise ch.Refused('OPTIONS_BAD', '--m1-efi belongs to the --m1-run record')
        return None
    efi = {}
    for item in given:
        name, _, digest = item.partition('=')
        if name not in EFI_NAMES or name in efi or not HEX64.fullmatch(digest):
            raise ch.Refused('OPTIONS_BAD', f'--m1-efi is NAME=SHA256 (64 lower-case hex digits) once for each of {" and ".join(EFI_NAMES)}')
        efi[name] = digest
    if sorted(efi) != sorted(EFI_NAMES):
        raise ch.Refused('OPTIONS_BAD', f'--m1-run names the two EFI files that run before the wrapper: --m1-efi for {" and ".join(EFI_NAMES)}')
    if bad := [name for name, _, digest in files if name in efi and digest.lower() != efi[name]]:
        raise ch.Refused('OPTIONS_BAD', f'--file {", ".join(bad)} has another SHA-256 than --m1-efi names: the record must name the files the stage tree is made of')
    return {name: efi[name] for name in EFI_NAMES}


def pinned_defines(monitor, offset):
    """What tools/sp11_stick_wrapper_build.py builds the wrapper with to make it refuse any other monitor: its place, its length and its digest."""
    digest = hashlib.sha256(monitor).digest()
    return [f'-DSP11_MONITOR_OFFSET={offset:#x}', f'-DSP11_MONITOR_BYTES={len(monitor)}',
            *[f'-DSP11_MONITOR_SHA{i}={int.from_bytes(digest[8 * i:8 * i + 8], "little"):#x}ULL' for i in range(4)]]


def find_nv2_record(monitor, digest):
    """[(offset, parsed fields)] of the windows of `monitor` that carry the NV2 record's magic, parse as one and have this SHA-256."""
    magic, found, start = nv2_record.MAGIC.to_bytes(4, 'little'), [], 0
    while (at := monitor.find(magic, start)) >= 0:
        window = monitor[at:at + nv2_record.SIZE]
        if len(window) == nv2_record.SIZE and sha(window) == digest:
            verdict, record = nv2_record.parse_binary(window)
            if verdict == nv2_record.PARSED:
                found.append((at, nv2_record.fields_of(record)))
        start = at + 1
    return found


def bl_calls(image, limit=SCAN_BYTES):
    """{file offset called: [pc, ...]} of the A64 BL instructions among the 4-aligned words of the first `limit` bytes of `image`. The image is flat and
    position independent, so what a BL calls is a place in the file. A data word that reads as a BL counts too: this decodes, it does not follow."""
    found, end, step = {}, min(len(image), limit) // 4 * 4, 1 << 20
    for start in range(0, end, step):
        block = image[start:min(end, start + step)]
        for pc, word in zip(range(start, start + len(block), 4), struct.unpack(f'<{len(block) // 4}I', block)):
            if word >> 26 == 0b100101:
                found.setdefault(pc + 4 * ((word & 0x3FFFFFF) - ((word & 0x2000000) << 1)), []).append(pc)
    return found


def appended_image_calls(image):
    """[(pc, target)] of the BL instructions in `image` that call a page of the file where at least PAD_BYTES zero bytes end and non-zero bytes begin:
    the way tools/sp11_stick_wrapper_build.py --monitor lays out the wrapper, its zero padding and the monitor."""
    found = []
    for target, pcs in sorted(bl_calls(image).items()):
        if target % PAGE == 0 and PAD_BYTES <= target <= len(image) - PAD_BYTES and not any(image[target - PAD_BYTES:target]) and any(image[target:target + PAD_BYTES]):
            found += [(pc, target) for pc in pcs]
    return found


def monitor_call_evidence(image):
    """[what] in `image` says that the wrapper calls a monitor: a BL into a zero-padded page (appended_image_calls), or the text of the wrapper's
    monitor stage (STAGE_TEXT). Empty when neither is found, which does not prove that there is no call. What this cannot show: a wrapper that enters
    a monitor by an indirect branch, by a call into a page that no zero bytes precede, or from a build whose stage text differs; a call beyond the
    first SCAN_BYTES of the file (the text is searched in all of it); and what a wrapper does at run time. It has no override: a false positive on a
    wrapper that calls none is a refusal until the signals are corrected. The wrappers that ran on the unit or were staged for it, none with a
    monitor, are tested not to match."""
    found = [f'a BL at {pc:#x} calls the zero-padded page at {target:#x}' for pc, target in appended_image_calls(image)]
    found += [f'the text of the wrapper\'s monitor stage is in the file ({text.decode()}...)' for text in STAGE_TEXT if text in image]
    return found


def refuse_monitor_wrapper(image):
    """WRAPPER_CALLS_MONITOR: for every kind of record but the one of --m1-run, `image` (--monitor, payload 0) must not be a wrapper that calls a
    monitor. The text of those kinds says that none is called, so what they authorise would not be what they say."""
    if found := monitor_call_evidence(image):
        raise Refused('WRAPPER_CALLS_MONITOR', f'--monitor calls a monitor ({"; ".join(found[:2])}); only the record of --m1-run authorises that, '
                                               'and it names the monitor: give --m1-run, or a wrapper that calls none')


def refuse_monitor_reporter(image):
    """WRAPPER_CALLS_MONITOR, for every kind of record including --m1-run: `image` (--reporter, payload 1) runs in the same boot as payload 0 and is
    authorised as a program that reads REPORT.DAT and prints, so it must not call a monitor. Only the call is looked for (appended_image_calls), never the
    text of the monitor stage: SNAPREP-M1.BIN prints the monitor's record by design and has those words, and it is the one reporter this kind authorises.
    What this cannot show is what monitor_call_evidence cannot, and that a reporter has no text to find."""
    if found := appended_image_calls(image):
        raise Refused('WRAPPER_CALLS_MONITOR', f'--reporter calls a monitor (a BL at {found[0][0]:#x} calls the zero-padded page at {found[0][1]:#x}); '
                                               'the reporter reads REPORT.DAT and prints, and no record authorises it to call one: give the reporter')


def _int(value, what, low=0):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value < 1 << 32:
        raise ValueError(f'{what} is not a number')
    return value


def _digest(value, what):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ValueError(f'{what} is not a SHA-256')
    return value


def _entry(entry, what):
    return dict(bytes=_int(entry['bytes'], f'{what} bytes', 1), sha256=_digest(entry['sha256'], f'{what} sha256'))


def read_report(data, image, reporter, efi=None, cpu_start='stop'):
    """The record's M1 facts: the report `data` (the bytes of build-report.json) read strictly, then checked against `image` (SNAPMON-M1.BIN) and
    `reporter` (SNAPREP-M1.BIN), the bytes given as --monitor and --reporter. M1_REPORT_BAD: not a report of this kind. M1_REPORT_MISMATCH: a
    report of this kind that does not describe these files, or is of another CPU-start choice than `cpu_start` (its "cpu_start" key, absent in a `stop` report, and the
    acknowledge gate among its added defines must both say the choice; with `ack` the files are SNAPMON-M1-ACK.BIN and SNAPREP-M1-ACK.BIN and the facts carry
    cpu_start). A detail quotes no text of the file: it is printed on the operator's terminal. `efi`
    (read_efi's result) is carried into the facts for section 1b; the report does not know the stick's EFI files."""
    if cpu_start not in CPU_STARTS:
        raise Refused('OPTIONS_BAD', f'--cpu-start is one of {", ".join(CPU_STARTS)}')
    image_name, reporter_name = CPU_START_FILES[cpu_start]
    try:
        raw = json.loads(data.decode('utf-8'), object_pairs_hook=_no_duplicates)
        if raw['hardware'] != 'NOT RUN' or raw['profile'] != PROFILE or not str(raw['variant']).startswith('m1:'):
            raise ValueError('not the report of an sp11-m1 monitor entry build with hardware NOT RUN')
        pins = raw['added']
        if not isinstance(pins, list) or '-DSNAP_SP11_STICK_MONITOR=1' not in pins:
            raise ValueError('the wrapper was not built with SNAP_SP11_STICK_MONITOR')
        says = raw.get('cpu_start', 'stop')
        spelled = [pin for pin in pins if isinstance(pin, str) and ACK_SPELLING.fullmatch(pin)]
        if says != cpu_start or spelled != ([ACK_DEFINE] if cpu_start == 'ack' else []):
            raise Refused('M1_REPORT_MISMATCH', f'the report is of a --cpu-start {str(says)[:16]!r} build and its added defines carry {len(spelled)} spelling(s) of the '
                                                f'acknowledge gate; this record is for --cpu-start {cpu_start}, which needs {"exactly " + ACK_DEFINE if cpu_start == "ack" else "none"}')
        files, wrapper, mon = raw['files'], raw['wrapper'], raw['monitor']
        listed, rep, wrap = _entry(files[image_name], image_name), _entry(files[reporter_name], reporter_name), dict(_entry(wrapper, 'wrapper'), padding=_int(wrapper['padding'], 'padding'))
        monitor = dict(offset=_int(mon['offset'], 'monitor offset', 1), bytes=_int(mon['bytes'], 'monitor bytes', 1), sha256=_digest(mon['sha256'], 'monitor sha256'),
                       elf_sha256=_digest(mon['elf_sha256'], 'monitor elf_sha256'), build_record_sha256=_digest(mon['build_record_sha256'], 'build record sha256'),
                       relocations=mon['relocations'])
        nv2_digest = _digest(mon['nv2_record'], 'NV2 record sha256')
        counts = monitor['relocations']
        if not isinstance(counts, dict) or not all(re.fullmatch(r'R_AARCH64_\w+', k) and isinstance(n, int) and not isinstance(n, bool) and n > 0 for k, n in counts.items()):
            raise ValueError('the relocation counts are not a table of counts')
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
        what = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError | UnicodeDecodeError) else type(error).__name__
        raise Refused('M1_REPORT_BAD', f'--m1-run is not the build-report.json of tools/sp11_stick_wrapper_build.py --monitor ({what})') from None
    offset, mismatch = monitor['offset'], []
    if (len(image), sha(image)) != (listed['bytes'], listed['sha256']):
        mismatch.append(f'--monitor is not the {image_name} the report lists')
    if (len(reporter), sha(reporter)) != (rep['bytes'], rep['sha256']):
        mismatch.append(f'--reporter is not the {reporter_name} the report lists')
    if offset % 4096 or wrap['bytes'] + wrap['padding'] != offset or len(image) != offset + monitor['bytes']:
        mismatch.append('the layout (wrapper, padding, monitor) does not add up to the image, or the monitor is not on a page')
    elif (sha(image[:wrap['bytes']]), image[wrap['bytes']:offset], sha(image[offset:])) != (wrap['sha256'], bytes(wrap['padding']), monitor['sha256']):
        mismatch.append('the wrapper, the zero padding or the appended monitor in the image is not what the report says')
    elif not all(item in pins for item in pinned_defines(image[offset:], offset)):
        mismatch.append('the wrapper was not built to refuse any monitor but this one (its pinned offset, length and digest differ from the image)')
    found = find_nv2_record(image[offset:], nv2_digest) if not mismatch else []
    if not mismatch and len(found) != 1:
        mismatch.append(f'the NV2 record the report names is in the monitor {len(found)} times, not once')
    if mismatch:
        raise Refused('M1_REPORT_MISMATCH', '; '.join(mismatch))
    wrapper_bytes = image[:wrap['bytes']]
    if offset not in bl_calls(wrapper_bytes, len(wrapper_bytes)):
        raise Refused('M1_NO_MONITOR_CALL', f'the wrapper in --monitor has no BL to the monitor at {offset:#x}: the report describes the files, but this wrapper does not call this monitor')
    if NOT_CALLED_TEXT in wrapper_bytes:
        raise Refused('M1_NO_MONITOR_CALL', 'the wrapper in --monitor says that it calls no monitor, whatever else it holds')
    at, fields = found[0]
    # The digest of the header that the build read, from the record's own 96 bytes: emit_header is a function of them alone. The path of that header is in the
    # profile's build record and is no part of the facts, so the record's text does not depend on where the monitor was built.
    header = sha(nv2_record.emit_header(image[offset + at:offset + at + nv2_record.SIZE]).encode())
    return dict(profile=PROFILE, image=listed, wrapper=wrap, monitor=monitor, reporter=rep,
                nv2=dict(sha256=nv2_digest, header_sha256=header, **{k: fields[k] for k in NV2_KEYS}), test_record=fields['tool'] == TEST_TOOL, efi=efi,
                **({'cpu_start': cpu_start} if cpu_start != 'stop' else {}))


def check_launch(chunks, let_panic_reference=None):
    """PARAMS_LAUNCH: the PARAMS chunk of the guest holds zero in every word of LAUNCH_WORDS and in the monitor's diagnostic words of DIAG_WORDS, except let_panic for the
    guest of --let-panic: that guest is given as `let_panic_reference` the PARAMS bytes that the same build has with let_panic at 0 (rung 3's PARAMS chunk), and its chunk equals
    them in every word but let_panic, which is 1 (what was built is what the record says: K12). PARAMS_ANS: none of the MODEL_MAX slots of its device models holds
    a DEVICE_ANS_* kind, the slots past model_count included (the monitor does not read those, but a PARAMS that has one is not a PARAMS this builder wrote; the
    whole 64-bit word is the kind, as the monitor compares it). A chunk too short to hold them, or a model_count that struct params has no slots for, is
    BUILT_SHAPE, as for the words check_params reads."""
    params = [c for c in chunks if c['kind'] == sb.KIND_PARAMS]
    data = params[0].get('data') if len(params) == 1 else None
    if data is None or len(data) < PARAMS_BYTES:
        raise Refused('BUILT_SHAPE', f'the PARAMS chunk is absent, zero-filled or shorter than the {PARAMS_BYTES:#x} bytes of struct params')
    if bad := [f'{name} (offset {at:#x})' for name, at, size in LAUNCH_WORDS if any(data[at:at + size])]:
        raise Refused('PARAMS_LAUNCH', f'PARAMS is not zero in {", ".join(bad)}: this run opens none of the paths those words open')
    word = lambda at: int.from_bytes(data[at:at + 8], 'little')     # noqa: E731
    wanted = {'let_panic': int(let_panic_reference is not None)}
    if bad := [f'{name} (offset {8 * i:#x}) is {word(8 * i)}, not {wanted.get(name, 0)}' for name, i in DIAG_WORDS if word(8 * i) != wanted.get(name, 0)]:
        raise Refused('PARAMS_LAUNCH', f'PARAMS {"; ".join(bad)}: the monitor\'s diagnostic words are zero in every record of this kind but --let-panic\'s, which sets let_panic to 1 and no other')
    if let_panic_reference is not None:
        size = len(let_panic_reference)
        different = [i for i in range(size // 8) if data[8 * i:8 * i + 8] != let_panic_reference[8 * i:8 * i + 8]]
        if size < PARAMS_BYTES or len(data) < size or different != [LET_PANIC_AT // 8] or any(data[size:]):
            raise Refused('PARAMS_LAUNCH', f'PARAMS of --let-panic differs from the one that the same build has without it in the words {different} (after its {size} bytes '
                                           f'only zero padding may follow): the guest differs in the let_panic word (index {LET_PANIC_AT // 8}) and in no other')
    if (count := word(MODEL_COUNT_AT)) > MODEL_MAX:
        raise Refused('BUILT_SHAPE', f'PARAMS lists {count} device models and struct params has {MODEL_MAX} slots')
    for slot in range(MODEL_MAX):
        at = MODELS_AT + slot * MODEL_BYTES
        if (kind := word(at)) in ANS_KINDS:
            window = ', configured as an ANS window (config[3] is 1)' if kind == ANS_NVME_KIND and word(at + ANS_WINDOW_AT) == ANS_WINDOW_MODE else ''
            raise Refused('PARAMS_ANS', f'PARAMS lists {ANS_KINDS[kind]} in device model slot {slot} (model_count {count}){window}: this kind has no ANS window, so '
                                        'that the monitor never calls nvmeh_sp11_adopt, whose target and record blocks nothing of the stick bundle places')
