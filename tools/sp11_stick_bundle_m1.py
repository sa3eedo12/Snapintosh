# SPDX-License-Identifier: MIT
"""What the authorisation record of a monitor entry run (--m1-run of tools/sp11_stick_bundle_build.py; docs/SP11-STICK-MONITOR.md section 10) is
made from besides the bundle: the build report of tools/sp11_stick_wrapper_build.py --monitor, read strictly and checked against the bytes the
builder was given as --monitor and --reporter, and the words of the PARAMS chunk that must be zero for this run.

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

REFUSALS = ('M1_REPORT_BAD', 'M1_REPORT_MISMATCH', 'PARAMS_LAUNCH', 'WRAPPER_CALLS_MONITOR', 'M1_NO_MONITOR_CALL')     # beside BUILDER_REFUSALS: docs/SP11-STICK-BUILD.md lists those name for name
ch.KNOWN_REFUSALS.update(REFUSALS)
ch.KNOWN_REFUSALS.add('OPTIONS_BAD')
Refused = ch.Refused
IMAGE, REPORTER = 'SNAPMON-M1.BIN', 'SNAPREP-M1.BIN'
PROFILE = 'sp11-m1'
LABEL_PREFIX = 'M1RUN-'         # SP11_STICK_MONITOR_LABEL_PREFIX in probes/sp11-stick-monitor.h: what the wrapper of this kind requires at the start of PIN.DAT's label
PIN_LABEL_MAX = 23              # sp11sb_string_ok: build_label is 24 bytes and a string of 1 to 23 characters
EFI_NAMES = ('SLBOUNCE-DEVHE-MARK.EFI', 'Q1N1-PAYLOAD.EFI')       # the two files that run at EL2 before the wrapper: the builder's SLBOUNCE and RUNNER
REPORT_CAP = 1 << 20
BOX_MAX_S = 5           # the longest time box the wrapper's pre-call check accepts (SP11_STICK_MONITOR_BOX_MAX_S, D5)
TEST_TOOL = 'wi4b-test-only'        # the `tool` field of the LOCAL TEST RECORD that the host tests build a monitor with: never the unit's
PARAMS_BYTES = 0x2410       # sizeof(struct params) (probes/xnuboot/report_layout.c): the wrapper refuses a shorter chunk before the call
# What the monitor would do with these words (docs/SP11-STICK-MONITOR.md section 10): gpu.flags opens the GPU paths (one SMC among them),
# early_stop.flags bit 1 a call through a PARAMS pointer and bit 3 the loads from the A16's sensor addresses (record section 3b), txm_native (image,
# work, size, tree_size) a native TXM image. The wrapper reads the first two before the call; the third only this builder reads.
LAUNCH_WORDS = (('gpu.flags', 0x2340, 8), ('txm_native', 0x23C0, 32), ('early_stop.flags', 0x23E0, 8))
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


def check_options(stores, gic_stop, time_box_s, panel=None, hvc_calls=None):
    """--m1-run runs the wrapper's GIC arm (gic), the monitor (el2_sysreg) and RAM stores, with the time box the wrapper's pre-call check accepts. With `real`
    (panel and hvc_calls given) only D9's first choice: the record says the guest gets no framebuffer and a stock kernel, and stage 2 maps the
    framebuffer range from PARAMS when it is handed (probes/xnuboot/stage2.c), so another choice would make the text false."""
    need = sb.STORE_RAM | sb.STORE_EL2_SYSREG | sb.STORE_GIC
    if stores & need != need:
        raise ch.Refused('OPTIONS_BAD', '--m1-run stores in all three classes: --stores must include ram, el2_sysreg and gic')
    if gic_stop:
        raise ch.Refused('OPTIONS_BAD', '--m1-run and --gic-stop are two kinds of record: give one')
    if not 1 <= time_box_s <= BOX_MAX_S:
        raise ch.Refused('OPTIONS_BAD', f'--m1-run: the wrapper refuses a time box outside 1 to {BOX_MAX_S} s before it calls the monitor (D5)')
    if panel is not None and (panel, hvc_calls) != ('withheld', 0):
        raise ch.Refused('OPTIONS_BAD', f'--m1-run with real: D9 is open, and this record is written for --panel withheld and --sptm-hvc-calls 0 only '
                                        f'(the guest gets no framebuffer and a stock kernel); got --panel {panel} and --sptm-hvc-calls {hvc_calls}')


def pinned_label(label, is_m1):
    """The build_label PIN.DAT carries. For this kind the marker comes first (the wrapper of this kind refuses a PIN.DAT without it, D2); for every other
    kind the label is the facts' and may not start with the marker, or a PIN.DAT of another kind would pass for this one. PIN_STRING: it does not fit."""
    if label.startswith(LABEL_PREFIX):
        raise ch.Refused('PIN_STRING', f'build_label starts with {LABEL_PREFIX}, which only --m1-run adds')
    if is_m1 and len(LABEL_PREFIX + label) > PIN_LABEL_MAX:
        raise ch.Refused('PIN_STRING', f'--m1-run puts {LABEL_PREFIX} in front of build_label and the pinned label holds {PIN_LABEL_MAX} characters: the facts label holds '
                                       f'{PIN_LABEL_MAX - len(LABEL_PREFIX)}')
    return LABEL_PREFIX + label if is_m1 else label


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


def read_report(data, image, reporter, efi=None):
    """The record's M1 facts: the report `data` (the bytes of build-report.json) read strictly, then checked against `image` (SNAPMON-M1.BIN) and
    `reporter` (SNAPREP-M1.BIN), the bytes given as --monitor and --reporter. M1_REPORT_BAD: not a report of this kind. M1_REPORT_MISMATCH: a
    report of this kind that does not describe these files. A detail quotes no text of the file: it is printed on the operator's terminal. `efi`
    (read_efi's result) is carried into the facts for section 1b; the report does not know the stick's EFI files."""
    try:
        raw = json.loads(data.decode('utf-8'), object_pairs_hook=_no_duplicates)
        if raw['hardware'] != 'NOT RUN' or raw['profile'] != PROFILE or not str(raw['variant']).startswith('m1:'):
            raise ValueError('not the report of an sp11-m1 monitor entry build with hardware NOT RUN')
        pins = raw['added']
        if not isinstance(pins, list) or '-DSNAP_SP11_STICK_MONITOR=1' not in pins:
            raise ValueError('the wrapper was not built with SNAP_SP11_STICK_MONITOR')
        files, wrapper, mon = raw['files'], raw['wrapper'], raw['monitor']
        listed, rep, wrap = _entry(files[IMAGE], IMAGE), _entry(files[REPORTER], REPORTER), dict(_entry(wrapper, 'wrapper'), padding=_int(wrapper['padding'], 'padding'))
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
        mismatch.append(f'--monitor is not the {IMAGE} the report lists')
    if (len(reporter), sha(reporter)) != (rep['bytes'], rep['sha256']):
        mismatch.append(f'--reporter is not the {REPORTER} the report lists')
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
    fields = found[0][1]
    return dict(profile=PROFILE, image=listed, wrapper=wrap, monitor=monitor, reporter=rep, nv2=dict(sha256=nv2_digest, **{k: fields[k] for k in NV2_KEYS}),
                test_record=fields['tool'] == TEST_TOOL, efi=efi)


def check_launch(chunks):
    """PARAMS_LAUNCH: the PARAMS chunk of the guest holds zero in every word of LAUNCH_WORDS. A chunk too short to hold them is BUILT_SHAPE, as
    for the words check_params reads."""
    params = [c for c in chunks if c['kind'] == sb.KIND_PARAMS]
    data = params[0].get('data') if len(params) == 1 else None
    if data is None or len(data) < PARAMS_BYTES:
        raise Refused('BUILT_SHAPE', f'the PARAMS chunk is absent, zero-filled or shorter than the {PARAMS_BYTES:#x} bytes of struct params')
    if bad := [f'{name} (offset {at:#x})' for name, at, size in LAUNCH_WORDS if any(data[at:at + size])]:
        raise Refused('PARAMS_LAUNCH', f'PARAMS is not zero in {", ".join(bad)}: this run opens none of the paths those words open')
