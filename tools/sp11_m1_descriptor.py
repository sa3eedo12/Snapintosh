#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The guest-view board descriptor of the first monitor run: derived from the committed template and the unit's facts, and checked
(docs/SP11-STICK-REAL.md section 3). Evidence: EXPERIMENTAL, host only. Hardware: NOT RUN.

    sp11_m1_descriptor.py derive --facts F --out build/FILE [--dump DIR ...] [--template T] [--allow-under DIR ...]
    sp11_m1_descriptor.py check  --descriptor FILE --facts F [--dump DIR ...] [--template T]

What it makes. config/boards/sp11.json is a template: it leaves four fields to the unit (soc.midr, gic.version, timer.frequency_hz, ranges.dram0)
and, for the first monitor run, three things to the operator. `derive` fills the four from the local facts file and the firmware dumps of the unit
(the dumps `sp11_stick_bundle_build.py facts-check` holds the facts to), and applies the operator's decision of 2026-11-16: five devices with no
established way to reach them (uart-debug, keyboard, touchpad, touchscreen, nvme) are `absent`, meaning withheld from the guest (nothing is removed
from the Surface); the list of PCIe config windows, pcie.ecam, is empty, because it holds only what this build gives the guest; board.status is
EXPERIMENTAL, so that the builder accepts the file. Every other claim keeps the template's value and provenance.

A GUEST VIEW, not the Surface's description. The unit's MCFG lists eight allocations that this build does not hand over, so the board adapter's
strict equality rule (probes/bootcontract.py _cross_check, `if got != want.ecam`; decision D11 proper) is NOT satisfied. `from_hamoa` and
`check_hamoa_platform`, and so tools/check-hamoa-acpi.py, refuse this descriptor with `ecam-mismatch` on the unit's tables, after every GIC and CPU
check has passed; and no MCFG can satisfy an empty list (a table without an allocation is `table-structure`). That is expected, and left for later
(D11 proper, any use of `from_hamoa`, X6). Nothing here relaxes or bypasses a rule of board_descriptor.py, bootcontract.py or check-hamoa-acpi.py,
and none is edited: the result passes `require_boot_ready` as it is, and so does the builder's own gate (`descriptor_digest`).

Output. Deterministic: sorted keys, two-space indent, one final line feed, so the same inputs make the same bytes. It never overwrites, and writes
only below build/ unless --allow-under names another directory (no `..`, no symbolic link, never /Volumes or /dev). `check` re-derives from the
same inputs and refuses a descriptor that differs from the derivation in any byte, naming the fields; the board's own rules apply first.
A refusal prints REFUSED=<NAME> as the last line of standard output, the detail on standard error, and exits 1."""
import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import board_descriptor as bd  # noqa: E402
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_build as bb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402

Refused = ch.Refused
OWN_REFUSALS = ('TEMPLATE_CHANGED', 'GIC_VERSION_UNKNOWN', 'DRAM_BANK_UNCLEAR', 'GUEST_VIEW_MISMATCH')
ch.KNOWN_REFUSALS.update(OWN_REFUSALS)       # the builder's other names (OUT_DIR_REFUSED, OUT_NOT_EMPTY, FACTS_DISAGREE, ...) are registered already
TEMPLATE = ROOT / 'config' / 'boards' / 'sp11.json'
DECISION = 'operator decision 2026-11-16, guest view for the first monitor run'
DOC = 'docs/SP11-STICK-REAL.md section 3'
ABSENT = ('uart-debug', 'keyboard', 'touchpad', 'touchscreen', 'nvme')
UNKNOWN_DEVICE = {'state': 'unknown', 'mmio': None, 'ranges': []}
NAME_SUFFIX = ' - GUEST VIEW, first monitor run'
EXPECTED = ('EXPECTED from_hamoa and check_hamoa_platform (probes/bootcontract.py, tools/check-hamoa-acpi.py) refuse this descriptor with ecam-mismatch: '
            'the unit\'s MCFG lists eight allocations and this guest view lists none; the adapter\'s equality rule (D11 proper) is left for later')


def provenance(version, stride):
    """The provenance of every claim this tool sets: the four the unit settles (OBSERVED) and the operator's decisions (EXPERIMENTAL). Fixed text, so
    that the output does not depend on which dumps were named."""
    why = f'{DECISION}: {DOC}'
    notes = {'uart-debug': 'The unit\'s SPCR names a serial console port; nothing shows the guest may use it.',
             'keyboard': 'docs/SP11-INPUT-STUDY.md is an evidence study and picks no path.',
             'touchpad': 'docs/SP11-INPUT-STUDY.md is an evidence study and picks no path.',
             'touchscreen': 'The pinned Denali tree describes no touch or pen transport.',
             'nvme': 'docs/SP11-NVME-WINDOW.md section 7.1: the controller window is an X6 measurement.'}
    found = {
        'board': dict(source=f'{why}; identity and every claim not named here as config/boards/sp11.json (Linux 7475ea8b58616b33c04afb976cf0fbaad9dd63fb '
                             'arch/arm64/boot/dts/qcom/x1e80100-microsoft-denali-oled.dts:12-14)', evidence='EXPERIMENTAL',
                      note='Derived by tools/sp11_m1_descriptor.py. A GUEST VIEW for the first monitor run, not the Surface\'s description: pcie.ecam is empty while the '
                           'unit\'s MCFG lists eight allocations, so the adapter\'s equality rule (D11 proper) is not satisfied and check_hamoa_platform refuses it at '
                           'ecam-mismatch. Expected, and left for later. board.status is EXPERIMENTAL by the same decision.'),
        'soc.midr': dict(source='docs/SP11-STICK-LOG.md (the MIDR_EL1 table of boot 7 and restart probe step 3, one unit); docs/SP11-STICK-REAL.md section 3.3; '
                                'the local facts file\'s midr', evidence='OBSERVED',
                         note='Exact masks (0xffffffff): no wider set than was read.'),
        'gic.version': dict(source='the GICD structure of the unit\'s MADT in the local dumps (git-ignored, downloads/sp11/): its version field, which every dump '
                                   'gives alike; docs/SP11-MONITOR-PORT.md section 7', evidence='OBSERVED',
                            note=f'The facts\' redistributor stride {stride:#x} is the GICv{version} frame size, which agrees.'),
        'timer.frequency_hz': dict(source='the local facts file\'s cntfrq_hz (CNTFRQ_EL0 read at EL2 by the payload runs, docs/SP11-STICK-LOG.md); '
                                          'docs/SP11-STICK-REAL.md section 3.3', evidence='OBSERVED',
                                   note='One unit. The device tree carries no timer frequency.'),
        'ranges.dram0': dict(source='the final EFI memory maps of the local dumps (git-ignored, downloads/sp11/): one conventional write-back descriptor at or '
                                    'above 4 GiB, the same in every dump; docs/SP11-STICK-BUILD.md section 2; docs/SP11-MONITOR-PORT.md section 5',
                             evidence='OBSERVED', note='The bank only. The low extents below 4 GiB are not claimed: the descriptor count of the map differs between boots.'),
        'pcie.ecam': dict(source=f'{why}: the list is empty because it holds only the PCIe config windows this build gives the guest, which are none',
                          evidence='EXPERIMENTAL',
                          note='The Surface has eight allocations in its MCFG (segments 0 to 7) that are not handed over. The adapter\'s strict equality rule is left for '
                               'later (D11 proper): check_hamoa_platform refuses this list at ecam-mismatch, and no MCFG can satisfy an empty list.')}
    for name in ABSENT:
        found[f'devices.{name}'] = dict(source=f'{why}: no established way to reach this device, so it is marked absent, meaning withheld from the guest; '
                                               'nothing is removed from the Surface', evidence='EXPERIMENTAL', note=notes[name])
    return found


# ---- The derivation --------------------------------------------------------------------------------------------------------------
def load_template(path):
    data = bb.read_regular(path, '--template', cap=1 << 20)
    try:
        bd.loads(data.decode('utf-8'))      # the template must itself be a valid descriptor
    except (bd.BoardDescriptorError, UnicodeDecodeError) as error:
        raise Refused('DESCRIPTOR_NOT_READY', f'the template: {error}') from None
    return json.loads(data)


def require_unknowns(d):
    """The derivation fills what the template leaves unknown. If the template already states one of these fields it was promoted, and this tool
    is out of date: it refuses rather than overwrite a committed value."""
    first = (d['ranges'] or [{}])[0]
    stated = [name for name, value in (('board.status', d['board']['status'] != 'UNIMPLEMENTED'), ('soc.midr', d['soc']['midr'] is not None),
                                       ('gic.version', d['gic']['version'] is not None), ('timer.frequency_hz', d['timer']['frequency_hz'] is not None),
                                       ('ranges.dram0', (first.get('name'), first.get('base'), first.get('size')) != ('dram0', None, None)),
                                       ('pcie.ecam', d['pcie']['ecam'] is not None)) if value]
    stated += [f'devices.{name}' for name in ABSENT if d['devices'].get(name) != UNKNOWN_DEVICE]
    if stated:
        raise Refused('TEMPLATE_CHANGED', f'the template already states {stated}: it was promoted, so this derivation is out of date')


def derive(template, facts, version, bank):
    """The guest-view descriptor as a parsed object: `template` with the four fields of the unit and the three decisions applied."""
    d = copy.deepcopy(template)
    require_unknowns(d)
    d['board'].update(name=d['board']['name'] + NAME_SUFFIX, status='EXPERIMENTAL')
    d['soc']['midr'] = [{'value': f'{value:#010x}', 'mask': '0xffffffff'} for value in facts['midr']]
    d['gic']['version'] = version
    d['timer']['frequency_hz'] = facts['cntfrq_hz']
    d['ranges'][0].update(base=f'{bank[0]:#x}', size=f'{bank[1]:#x}')
    d['pcie']['ecam'] = []
    for name in ABSENT:
        d['devices'][name] = dict(state='absent', mmio=None, ranges=[])
    d['provenance'].update(provenance(version, facts['gicr_stride']))
    return d


def render(descriptor):
    return (json.dumps(descriptor, indent=2, sort_keys=True) + '\n').encode('ascii')


def gic_version(dumps, facts):
    """The GIC version the unit's MADT states, the same in every dump and agreeing with the facts' redistributor stride."""
    seen = {bb.madt(bb.sdt(d, 'APIC'))['gicd'][1] for d in dumps}
    if len(seen) != 1 or not seen <= set(bb.GICR_STRIDE):
        raise Refused('GIC_VERSION_UNKNOWN', f'the MADTs state GIC versions {sorted(seen)}; one of {sorted(bb.GICR_STRIDE)} is needed, the same in every dump')
    version = seen.pop()
    if bb.GICR_STRIDE[version] != facts['gicr_stride']:
        raise Refused('FACTS_DISAGREE', f'the MADTs say GICv{version}, whose redistributor stride is {bb.GICR_STRIDE[version]:#x}; the facts say {facts["gicr_stride"]:#x}')
    return version


def dram_bank(dumps):
    """(base, size) of the one conventional write-back descriptor at or above 4 GiB, identical in every dump."""
    banks = set()
    for d in dumps:
        high = [(start, end) for kind, start, end, attr in bb.descriptors(d.memmap, d.stride) if kind == sb.EFI_CONVENTIONAL and attr & sb.EFI_WB and start >= 1 << 32]
        if len(high) != 1:
            raise Refused('DRAM_BANK_UNCLEAR', f'{d.name} has {len(high)} conventional write-back descriptors at or above 4 GiB; one is needed')
        banks.add(high[0])
    if len(banks) != 1:
        raise Refused('DRAM_BANK_UNCLEAR', 'the dumps disagree on the bank above 4 GiB')
    start, end = banks.pop()
    return start, end - start


def gather(a):
    """(facts, dumps, GIC version, bank): the facts are held to every dump first, as the builder holds them."""
    facts, dumps = bb.load_facts(a.facts), bb.find_dumps(a.dump)
    bb.facts_check(facts, dumps)
    return facts, dumps, gic_version(dumps, facts), dram_bank(dumps)


def gate(data, facts):
    """What `real` demands of a descriptor (bb.descriptor_digest): the board's own gate, then the facts the descriptor must state. Nothing is relaxed."""
    try:
        board = bd.loads(data.decode('utf-8'))
        bd.require_boot_ready(board)
    except (bd.BoardDescriptorError, UnicodeDecodeError) as error:
        raise Refused('DESCRIPTOR_NOT_READY', str(error)) from None
    if bad := ch.descriptor_mismatches(board, facts):
        raise Refused('FACTS_DISAGREE', f'the facts disagree with the descriptor on {bad}')
    return board


def differences(want, got, path=''):
    """The dotted paths at which two parsed descriptors differ."""
    if isinstance(want, dict) and isinstance(got, dict):
        return [p for key in sorted(set(want) | set(got)) for p in differences(want.get(key), got.get(key), f'{path}.{key}' if path else key)]
    if isinstance(want, list) and isinstance(got, list) and len(want) == len(got):
        return [p for i, (x, y) in enumerate(zip(want, got)) for p in differences(x, y, f'{path}[{i}]')]
    return [] if want == got else [path or '(the whole file)']


# ---- The output --------------------------------------------------------------------------------------------------------------------
def check_out(raw, allow_under):
    out, st = Path(os.path.abspath(raw)), bb.stager()
    try:
        st.refuse_protected_location(out)           # first: a path under /Volumes or /dev is refused by its spelling, before anything looks at the filesystem
    except st.Refusal as error:
        raise Refused('OUT_DIR_REFUSED', error.reason) from None
    roots = [ROOT / 'build', *(Path(os.path.abspath(r)) for r in allow_under)]
    if '..' in Path(raw).parts or not any(r in out.parents for r in roots) or st.find_symlink_component(out) is not None:
        raise Refused('OUT_DIR_REFUSED', '--out must be a file below build/ or an --allow-under directory, with no ".." and no symbolic link')
    if os.path.lexists(out):
        raise Refused('OUT_NOT_EMPTY', f'{raw} exists: this tool never overwrites')
    return out


def write_new(out, data):
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o644)
    except FileExistsError:
        raise Refused('OUT_NOT_EMPTY', f'{out.name} exists: this tool never overwrites') from None
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)


def run_derive(a):
    out = check_out(a.out, a.allow_under)           # a refused path costs nothing else: no input is read for it
    facts, dumps, version, bank = gather(a)
    data = render(derive(load_template(a.template), facts, version, bank))
    gate(data, facts)
    write_new(out, data)
    try:
        digest, state, _ = bb.descriptor_digest(argparse.Namespace(descriptor=out), facts, 'real')      # the file `real` will read, through the gate `real` applies
    except Refused:
        out.unlink()
        raise
    shown = os.path.relpath(out, ROOT) if ROOT in out.parents else out
    print('GUEST VIEW of the first monitor run (operator decision 2026-11-16, docs/SP11-STICK-REAL.md section 3): not the Surface\'s description')
    print(f'FROM facts-check OK on {len(dumps)} dumps: soc.midr {[f"{v:#010x}" for v in facts["midr"]]} gic.version {version} '
          f'timer.frequency_hz {facts["cntfrq_hz"]} ranges.dram0 {bank[0]:#x}+{bank[1]:#x}')
    print(f'DECIDED board.status EXPERIMENTAL; absent (withheld from the guest, nothing removed from the Surface): {" ".join(ABSENT)}; pcie.ecam []')
    print(EXPECTED)
    print(f'DESCRIPTOR {shown} sha256 {digest.hex()} (what real pins as descriptor_sha256), {state}')


def run_check(a):
    facts, dumps, version, bank = gather(a)
    data = bb.read_regular(a.descriptor, '--descriptor', cap=1 << 20)
    gate(data, facts)                               # the board's rules first: a policy field that breaks one is refused by it, by name
    want = render(derive(load_template(a.template), facts, version, bank))
    if data != want:
        found = differences(json.loads(want), json.loads(data))
        raise Refused('GUEST_VIEW_MISMATCH', 'differs from the derivation at ' + (', '.join(found[:8]) + (' ...' if len(found) > 8 else '') if found else
                      'formatting only: the derivation writes sorted keys, two-space indent and one final line feed'))
    print(f'CHECK identical to the derivation, sha256 {hashlib.sha256(data).hexdigest()}')
    print(EXPECTED)


def build_parser():
    ap = argparse.ArgumentParser(prog='sp11_m1_descriptor.py', description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('derive', 'check'):
        p = sub.add_parser(name)
        p.add_argument('--facts', required=True, help='the local facts JSON (docs/SP11-STICK-BUILD.md section 4.1)')
        p.add_argument('--dump', action='append', default=[], help='a firmware dump directory (default downloads/sp11/x*-run*)')
        p.add_argument('--template', default=str(TEMPLATE), help='default config/boards/sp11.json')
    sub.choices['derive'].add_argument('--out', required=True, help='a new file below build/')
    sub.choices['derive'].add_argument('--allow-under', action='append', default=[], help='another directory --out may be below')
    sub.choices['check'].add_argument('--descriptor', required=True)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        (run_derive if a.cmd == 'derive' else run_check)(a)
    except Refused as refusal:
        print(f'REFUSED={refusal.name}')
        print(f'detail: {refusal.detail}', file=sys.stderr)
        return 1
    print('HARDWARE=NOT RUN')
    return 0


if __name__ == '__main__':
    sys.exit(main())
