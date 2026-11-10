#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cross-agent reconciliation tests for the Surface Pro 11 host groundwork.

Ten independently written patches were merged into one tree. Each class below proves one decision made at
that merge, against the merged files, so a later edit that breaks the agreement fails here and not on the
unit. Nothing here touches hardware: it parses sources, builds host binaries under build/sp11-integration/,
runs the probe under QEMU, and refuses everything that would need a machine.

  StatusBands       one allocation of the monitor's prepare statuses; every unit, header and host name agrees
  IpaBoundary       a window may end exactly at 1 << 36; five independent validators give the same verdict
  CitationPins      every cite of config/boards/a16.json is inside a line range some test pins
  AcpiDumpContract  the UEFI probe's dump files are the ones tools/check-hamoa-acpi.py reads, and the probe's
                    memmap.txt is refused (not misread) by the stage-2 map parser: no converter exists yet
  CycleThermal      cycle.py --target sp11 runs against the real tools/hamoa_thermal.py, not only its stub
  FairLock          the SP11 default of six vCPUs is refused by the placement model without a fair lock
  Wiring            the opt-in link tuples, the include guards and the SP11 builds of the merged monitor

    python3 tools/test-sp11-integration.py [-v] [-k NAME]

Without clang, QEMU or its EDK2 firmware the classes that need them report SKIP, never PASS.
Evidence: VERIFIED on the host only; every SP11 value behind the units under test is INFERRED or unknown.
"""
import ast
import ctypes
import gc
import importlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
TOOLS, PROBES, COMPAT = ROOT / 'tools', ROOT / 'probes', ROOT / 'compat'
BUILD = ROOT / 'build' / 'sp11-integration'
LIMIT = 1 << 36
PAGE = 0x1000
CLANG_DIRS = ('/opt/homebrew/opt/llvm/bin', '/opt/homebrew/bin')


def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / name) for d in CLANG_DIRS if (Path(d) / name).exists()), None)


def on_path():
    for directory in (TOOLS, PROBES):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))


def load(relpath, name):
    """A module from a file whose name is not an identifier (tools/test-*.py)."""
    on_path()
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def import_tool(name):
    on_path()
    return importlib.import_module(name)


def read(relpath):
    return (ROOT / relpath).read_text(encoding='utf-8')


def top_level_literal(relpath, name):
    """The value of a module-level `name = <literal>` without importing the module."""
    for node in ast.parse(read(relpath)).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f'{relpath} has no literal {name}')


def strip_c_comments(text):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', text, flags=re.S))


def c_enum(relpath, name):
    """{enumerator: value} of `enum <name>` with C numbering (an unset value follows its predecessor)."""
    body = re.search(r'enum\s+%s\s*\{([^}]*)\}' % re.escape(name), strip_c_comments(read(relpath))).group(1)
    values, current = {}, -1
    for item in filter(None, (part.strip() for part in body.split(','))):
        label, _, explicit = (part.strip() for part in item.partition('='))
        current = int(explicit, 0) if explicit else current + 1
        values[label] = current
    return values


def build_dir(*parts):
    path = BUILD.joinpath(*parts)
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


# --------------------------------------------------------------------------------------------------------------
# 1. Status codes

# The A16 monitor's own named prepare statuses, written out so that taking one of them for an SP11 refusal fails.
# -83 is NVMEH_E_QUEUES: nvmeh_adopt returns it through ans_window_prepare, so it is the A16 monitor's, not a free number.
A16_STATUSES = (-1, -2, -3, -4, -5, -6, -7, -8, -9, -10, -11, -12, -13, -14, -15, -16, -19, -21, -27, -28, -60, -61,
                -62, -63, -64, -65, -66, -67, -68, -71, -72, -73, -80, -81, -82, -83)
A16_BAND = range(-83, 0)
LAUNCH_GATE_BAND = range(-95, -89)          # -95 .. -90: NV2 and PAuth (compat/nv2-gate.h, compat/oryon-idregs.h)
NVME_WINDOW_BAND = range(-145, -95)         # -145 .. -96: NVMEH_E_SP11_* and NVMEH_SP11_STATUS_UNWIRED (compat/nvme-host.h)
VCPU_COUNT_STATUS = -146                    # GUEST_CPU_STATUS_FAIR_LOCK (compat/cpu.h)
STAGE2_BAND = range(-274, -256)             # -274 .. -257: -(256 + enum ipag_reason)
# The reasons probes/xnuboot/stage2.c can return; the others exist only in the host-only planner.
STAGE2_HOOK_REASONS = ('IPAG_BAD_IPA_BITS', 'IPAG_BAD_PARANGE', 'IPAG_NO_WINDOWS', 'IPAG_TOO_MANY_WINDOWS',
                       'IPAG_ZERO_SIZE', 'IPAG_INVERTED', 'IPAG_UNALIGNED', 'IPAG_OVERLAP', 'IPAG_ABOVE_PARANGE',
                       'IPAG_WINDOW_ABOVE_LIMIT', 'IPAG_WINDOW_CROSSES_LIMIT')
MONITOR_SOURCES = ('probes/xnuboot.c',)


class StatusBands(unittest.TestCase):
    """Reconciliation 1: nv2-pauth (-90..-95), the NVMe window (-96..-145), the vCPU count (-146), monitor-stage2
    (-257..-274) and the A16 band never overlap, and every status that can reach the host has a name."""

    @classmethod
    def setUpClass(cls):
        cls.failures = top_level_literal('probes/run.py', 'PREPARE_FAILURES')
        cls.sources = sorted(MONITOR_SOURCES + tuple(
            p.relative_to(ROOT).as_posix() for p in (PROBES / 'xnuboot').glob('*.c')))

    def test_the_a16_names_are_untouched_and_the_new_ones_are_in_a_band(self):
        keys = set(self.failures)
        self.assertTrue(set(A16_STATUSES) <= keys, 'an A16 status lost its name')
        new = keys - set(A16_STATUSES)
        sp11 = set(LAUNCH_GATE_BAND) | set(STAGE2_BAND) | set(NVME_WINDOW_BAND) | {VCPU_COUNT_STATUS}
        for key in sorted(new):
            with self.subTest(status=key):
                self.assertIn(key, sp11)
        self.assertEqual({k for k in new if k in LAUNCH_GATE_BAND}, set(LAUNCH_GATE_BAND))
        self.assertEqual({k for k in new if k in NVME_WINDOW_BAND}, set(NVME_WINDOW_BAND))
        self.assertIn(VCPU_COUNT_STATUS, new)

    def test_the_bands_are_disjoint_and_the_gap_numbers_are_unused(self):
        bands = (set(A16_BAND), set(LAUNCH_GATE_BAND), set(NVME_WINDOW_BAND), {VCPU_COUNT_STATUS}, set(STAGE2_BAND))
        for i, one in enumerate(bands):
            for two in bands[i + 1:]:
                self.assertFalse(one & two)
        used = set(self.failures)
        for gap in list(range(-89, -83)) + list(range(-255, -146)) + [-256] + list(range(-300, -274)):
            self.assertNotIn(gap, used, f'{gap} is in an unallocated band')

    def test_the_headers_define_exactly_the_launch_gate_band(self):
        defined = {}
        for header in ('compat/nv2-gate.h', 'compat/oryon-idregs.h'):
            for name, value in re.findall(r'#define\s+(SNAP_(?:NV2|ORYON)_STATUS_\w+)\s+\((-\d+)\)', read(header)):
                defined[name] = int(value)
        self.assertEqual(len(defined), 6, defined)
        self.assertEqual(set(defined.values()), set(LAUNCH_GATE_BAND))
        for name, value in defined.items():
            self.assertIn(value, self.failures, f'{name} = {value} has no PREPARE_FAILURES text')
            self.assertIn('SP11', self.failures[value])

    def test_the_stage2_hook_codes_are_the_named_ones(self):
        base = int(re.search(r'#define\s+IPAG_HOOK_RETURN_BASE\s+(\d+)', read('compat/ipa-geometry.h')).group(1))
        self.assertEqual(base, 256)
        reasons = c_enum('compat/ipa-geometry.h', 'ipag_reason')
        want = {-(base + reasons[name]) for name in STAGE2_HOOK_REASONS}
        self.assertEqual({k for k in self.failures if k in STAGE2_BAND}, want)
        self.assertTrue(all(code in STAGE2_BAND for code in want))
        for code in want:
            self.assertIn('stage-2', self.failures[code])
        self.assertEqual(len(set(self.failures.values())), len(self.failures), 'duplicate text')

    def test_no_literal_status_outside_the_a16_band_is_returned_by_the_monitor(self):
        for relpath in self.sources:
            for number in re.findall(r'return\s*-\s*(\d+)\s*;', strip_c_comments(read(relpath))):
                with self.subTest(source=relpath, status=-int(number)):
                    self.assertIn(-int(number), A16_BAND)

    def test_the_band_table_beside_the_names_matches(self):
        text = read('probes/run.py')
        block = text[text.index('# Status bands'):text.index('PREPARE_FAILURES = {')]
        for needle in ('-1 .. -83', '-84 .. -89', '-90 .. -95', '-96 .. -145', '-146 ', '-147 .. -255',
                       '-257 .. -274', 'IPAG_HOOK_RETURN_BASE', 'NVMEH_E_SP11_', 'NVMEH_SP11_STATUS_UNWIRED',
                       'GUEST_CPU_STATUS_FAIR_LOCK'):
            self.assertIn(needle, block)

    def test_every_nvme_window_status_has_a_decodable_text_that_is_the_contracts_own(self):
        """nvmeh_adopt returns -80 .. -83 and nvmeh_sp11_adopt -96 .. -144 as prepare statuses; the host must read each."""
        header, source = strip_c_comments(read('compat/nvme-host.h')), read('compat/nvme-host.c')
        codes = {name: int(value) for name, value in re.findall(r'\b(NVMEH_E_SP11_[A-Z0-9_]+)\s*=\s*(-\d+)', header)}
        unwired = re.search(r'#define\s+NVMEH_SP11_STATUS_UNWIRED\s+\((-\d+)\)', header)
        self.assertIsNotNone(unwired, 'compat/nvme-host.h defines no NVMEH_SP11_STATUS_UNWIRED')
        codes['NVMEH_SP11_STATUS_UNWIRED'] = int(unwired.group(1))
        self.assertEqual(sorted(codes.values()), sorted(set(NVME_WINDOW_BAND)), 'the band is exactly the defined codes')
        texts = dict(re.findall(r'case (NVMEH_[A-Z0-9_]+):\s*return "([^"]+)";', source))
        for name, value in sorted(codes.items(), key=lambda item: -item[1]):
            with self.subTest(code=name, status=value):
                self.assertIn(value, self.failures, f'{name} = {value} has no PREPARE_FAILURES text')
                self.assertIn(name, texts, f'nvmeh_sp11_strerror has no text for {name}')
                self.assertEqual(self.failures[value], 'SP11 NVMe window: ' + texts[name])

    def test_the_a16_nvme_adoption_statuses_the_monitor_returns_are_all_named(self):
        header = strip_c_comments(read('compat/nvme-host.h'))
        adopt = read('compat/nvme-host.c')
        adopt = adopt[adopt.index('int nvmeh_adopt('):adopt.index('static uint64_t doorbell(')]
        returned = {int(value) for name, value in re.findall(r'\b(NVMEH_E_[A-Z]+)\s*=\s*(-\d+)', header)
                    if re.search(r'e = %s;' % name, adopt)}
        self.assertEqual(returned, {-80, -81, -82, -83}, 'nvmeh_adopt returns a status this test does not know')
        for status in returned:
            with self.subTest(status=status):
                self.assertIn(status, self.failures)
        self.assertIn('admin queue', self.failures.get(-83, ''))

    def test_the_vcpu_count_status_is_the_one_cpu_h_defines_and_the_one_the_monitor_returns(self):
        defined = re.search(r'#define\s+GUEST_CPU_STATUS_FAIR_LOCK\s+\((-\d+)\)', read('compat/cpu.h'))
        self.assertIsNotNone(defined, 'compat/cpu.h defines no GUEST_CPU_STATUS_FAIR_LOCK')
        self.assertEqual(int(defined.group(1)), VCPU_COUNT_STATUS)
        self.assertIn('fair', self.failures.get(VCPU_COUNT_STATUS, '').lower())
        self.assertIn('SNAP_FAIR_TOKEN', self.failures.get(VCPU_COUNT_STATUS, ''))
        self.assertIn('return GUEST_CPU_STATUS_FAIR_LOCK;', read('probes/xnuboot/prepare.c'))


# --------------------------------------------------------------------------------------------------------------
# 2. The 1 << 36 boundary

def boundary_cases():
    """(label, base, size, in range): half-open windows around 1 << 36, the way every validator sees them."""
    return (
        ('last page, ending exactly at the limit', LIMIT - PAGE, PAGE, True),
        ('last two pages', LIMIT - 2 * PAGE, 2 * PAGE, True),
        ('a 30 GiB high bank ending exactly at the limit', LIMIT - (30 << 30), 30 << 30, True),
        ('ends one page below the limit', LIMIT - 2 * PAGE, PAGE, True),
        ('first page', PAGE, PAGE, True),
        ('ends one page beyond the limit', LIMIT - PAGE, 2 * PAGE, False),
        ('ends one page beyond, long window', LIMIT - (30 << 30), (30 << 30) + PAGE, False),
        ('starts exactly at the limit', LIMIT, PAGE, False),
        ('starts one page above the limit', LIMIT + PAGE, PAGE, False),
        ('end wraps 2^64', 0xfffffffffffff000, 0x2000, False),
        ('size alone wraps 2^64', PAGE, 0xfffffffffffff000, False),
    )


class IpaBoundary(unittest.TestCase):
    """Reconciliation 2: platform-gate refused a window ending exactly at 1 << 36, stage 2 accepted it.

    The existing code decides: shadow/stage2.c refuses only hi > limit, probes/bootcontract.py only
    end > 1 << address_bits, so the end is exclusive and may equal the limit. All five validators below now
    agree on that, on every case, and on the 32 GiB model whose high bank ends exactly on the limit."""

    @classmethod
    def setUpClass(cls):
        clang = find_tool('clang')
        if not clang:
            raise unittest.SkipTest('clang not found')
        cls.tbi = load('tools/test-board-info.py', 'sp11_test_board_info')
        cls.tbi.BUILD = BUILD / 'board-info'        # not the path tools/test-board-info.py builds: the suite runs both
        cls.tbd = cls.tbi.tbd
        cls.bootcontract = load('probes/bootcontract.py', 'sp11_bootcontract')
        out = build_dir('ipag') / 'libipag.dylib'
        built = subprocess.run([clang, '-std=c11', '-O1', '-fPIC', '-shared', '-Wall', '-Wextra', '-Werror',
                                f'-I{COMPAT}', str(COMPAT / 'ipa-geometry.c'), '-o', str(out)],
                               capture_output=True, text=True, timeout=300)
        if built.returncode:
            raise AssertionError(built.stderr[-3000:])
        cls.lib = ctypes.CDLL(str(out))
        cls.lib.ipag_check_spans.restype = ctypes.c_int
        cls.lib.ipag_check_spans.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                             ctypes.POINTER(ctypes.c_uint)]

    def spans(self, windows, ipa_bits=36, parange=5):
        """ipag_check_spans over [(lo, hi)]; hi is formed modulo 2^64 exactly as the monitor's range list is."""
        array = (ctypes.c_uint64 * (2 * len(windows)))(*[v & (2 ** 64 - 1) for w in windows for v in w])
        return self.lib.ipag_check_spans(ctypes.addressof(array), len(windows), ipa_bits, parange, None)

    def verdicts(self, base, size):
        tbd, tbi = self.tbd, self.tbi
        descriptor = tbd.synthetic()
        tbd.add_range('edge', 'mmio', f'{base:#x}', f'{size:#x}')(descriptor)
        try:
            tbd.bd.validate(descriptor)
            accepted_descriptor = True
        except tbd.bd.BoardDescriptorError:
            accepted_descriptor = False
        blob = tbi.forge(tbi.item('range', 0, base=base, size=size))
        accepted_python = tbi.bi.check(blob) == tbi.bi.OK
        accepted_c = tbi.native_run([(blob, 0)])[0][0] == tbi.bi.OK
        try:
            self.bootcontract.PhysicalRange(base, size, 'edge').validate(page_shift=12)
            accepted_contract = True
        except ValueError:
            accepted_contract = False
        accepted_spans = self.spans([(base, base + size)]) == 0
        return {'board_descriptor.validate': accepted_descriptor, 'boardinfo.check': accepted_python,
                'board_info_validate': accepted_c, 'PhysicalRange.validate': accepted_contract,
                'ipag_check_spans': accepted_spans}

    def test_every_validator_gives_the_same_verdict_on_every_edge(self):
        for label, base, size, in_range in boundary_cases():
            with self.subTest(label):
                self.assertEqual(self.verdicts(base, size), dict.fromkeys(
                    ('board_descriptor.validate', 'boardinfo.check', 'board_info_validate',
                     'PhysicalRange.validate', 'ipag_check_spans'), in_range))

    def test_a_32_gib_map_that_ends_on_the_limit_is_acceptable_to_the_descriptor_and_to_stage_2(self):
        low, high = (0x80000000, 0x100000000), (0x880000000, LIMIT)          # monitor-stage2's 32 GiB model
        self.assertEqual(self.spans([low, high]), 0)
        crossing = self.spans([low, (0x880000000, LIMIT + (2 << 30))])         # the 64 GiB model's high bank
        self.assertEqual(crossing, c_enum('compat/ipa-geometry.h', 'ipag_reason')['IPAG_WINDOW_CROSSES_LIMIT'])
        board = self.tbd.synthetic()
        self.tbd.chain(self.tbd.S('ranges.0.base', f'{high[0]:#x}'), self.tbd.S('ranges.0.size', f'{high[1] - high[0]:#x}'))(board)
        self.tbd.bd.validate(board)

    def test_the_stage_2_builder_and_the_span_check_stay_one_rule(self):
        stage2 = read('shadow/stage2.c')
        self.assertIn('r->hi > limit', stage2)
        self.assertNotIn('r->hi >= limit', stage2)
        self.assertIn('hi > ipa_limit', read('compat/ipa-geometry.c'))
        for relpath, text in (('tools/board_descriptor.py', 'size > ipa_limit - base'),
                              ('probes/boardinfo.py', 'size > IPA_LIMIT - base'),
                              ('compat/board-info.c', 'size > IPA_LIMIT - base')):
            self.assertIn(text, read(relpath), relpath)
            self.assertNotIn(text.replace('>', '>='), read(relpath), relpath)


# --------------------------------------------------------------------------------------------------------------
# 3. Citations

class CitationPins(unittest.TestCase):
    """Reconciliation 3: the A16 descriptor cites lines of files that other agents edited in the same merge."""

    def cites(self, relpath):
        provenance = json.loads(read(relpath))['provenance']
        for claim, entry in provenance.items():
            if entry['source'] == 'UNKNOWN':
                continue
            for part in entry['source'].split(';'):
                match = re.match(r'\s*([\w./+-]+):(.+?)\s*$', part)
                self.assertIsNotNone(match, f'{claim}: {part!r}')
                for span in match.group(2).split(','):
                    first, _, last = span.partition('-')
                    yield claim, match.group(1), int(first), int(last or first)

    def test_every_cited_line_of_a16_exists_and_is_inside_a_pinned_range(self):
        pins = load('tools/test-board-descriptor.py', 'sp11_test_board_descriptor').A16_CITATIONS
        pinned = {}
        for _, path, first, last, _ in pins:
            pinned.setdefault(path, set()).update(range(first, last + 1))
        for claim, path, first, last in self.cites('config/boards/a16.json'):
            with self.subTest(claim=claim, cited=f'{path}:{first}-{last}'):
                lines = read(path).count('\n') + 1
                self.assertLessEqual(last, lines)
                self.assertTrue(set(range(first, last + 1)) <= pinned.get(path, set()),
                                'no citation test pins these lines')

    def test_the_cites_into_files_edited_by_the_other_patches_follow_the_merged_text(self):
        expected = {('probes/secondary.c', 26): '0x17000000', ('probes/secondary.c', 27): '0x17080000',
                    ('probes/secondary.c', 28): '0x17500000', ('probes/secondary.c', 29): '0x40000',
                    ('probes/secondary.c', 30): 'SNAP_TIMER_INTID 26', ('probes/xnuboot/prepare.c', 93): '19200000'}
        for (path, number), token in expected.items():
            with self.subTest(path=path, line=number):
                self.assertIn(token, read(path).splitlines()[number - 1])


# --------------------------------------------------------------------------------------------------------------
# 4. The UEFI probe's dump against the ACPI checker, and its memory map against the stage-2 map parser

PROBE_TABLES = ('RSDP.bin', 'XSDT.bin', 'APIC.bin', 'MCFG.bin')


class AcpiDumpContract(unittest.TestCase):
    """Reconciliation 4: file names agree, and the checker ends at the designed refusal on a real probe dump."""

    def test_the_checker_reads_the_names_the_probe_documents(self):
        checker = load('tools/check-hamoa-acpi.py', 'sp11_check_hamoa_acpi')
        self.assertEqual(tuple(name for _, name in checker.TABLE_FILES), PROBE_TABLES)
        readme = read('probes/uefi/README.md')
        for name in ('RSDP.bin', 'APIC.bin', '<SIG>.bin'):
            self.assertIn(name, readme)

    @classmethod
    def probe_dump(cls):
        """Boots the fast probe under QEMU (the probe's own default scenario) and returns {path: bytes}."""
        if not hasattr(cls, '_dump'):
            sys.path[:0] = [str(PROBES / 'uefi' / 'test'), str(PROBES / 'uefi')]
            import suite_qemu                       # noqa: PLC0415 - only needed (and importable) here
            work = BUILD / 'probe-work'             # not build/uefi/test-work: the suite also runs tools/test-uefi-probe.py
            work.mkdir(parents=True, exist_ok=True)
            suite_qemu.kit.WORK = work
            with warnings.catch_warnings():         # the probe's runner leaves pipes to the garbage collector
                warnings.simplefilter('ignore', ResourceWarning)
                outcome = suite_qemu.scenario('default')
                gc.collect()
            if outcome.result.failure or outcome.result.stick_error:
                raise AssertionError(f'the probe scenario failed: {outcome.result.failure} {outcome.result.stick_error}')
            cls._dump = {p: d for p, d in outcome.result.stick.files.items() if p.upper().startswith('SP11DUMP/')}
        return cls._dump

    def test_the_designed_result_on_a_real_probe_dump_is_descriptor_unimplemented(self):
        dump = self.probe_dump()
        names = {Path(p).name for p in dump}
        self.assertTrue(set(PROBE_TABLES) <= names, sorted(names))
        root = build_dir('dump')
        for path, data in dump.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(data)
        done = subprocess.run([sys.executable, '-B', str(TOOLS / 'check-hamoa-acpi.py'), str(root / 'SP11DUMP')],
                              capture_output=True, text=True, timeout=120, cwd=ROOT)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('ACPI tables read from RUN01: RSDP, XSDT, APIC, MCFG', done.stdout)
        self.assertIn('REFUSED: descriptor-unimplemented', done.stdout)
        self.assertNotIn('ACCEPTED', done.stdout)
        # The probe's memory map is the other consumer's input; see the next test.
        self.assertTrue(dump['SP11DUMP/RUN01/memmap.txt'].startswith(b'# SP11PROBE memmap v1'))

    def test_the_probe_memmap_is_refused_by_the_stage_2_parser_until_a_converter_exists(self):
        """UNIMPLEMENTED bridge: tools/test-stage2-geometry.py reads `ipa-geometry-map 1`, the probe writes
        `# SP11PROBE memmap v1`. A misread map must never become a plan, so the parser has to refuse it."""
        stage2 = load('tools/test-stage2-geometry.py', 'sp11_test_stage2_geometry')
        probe_text = ('# SP11PROBE memmap v1\n# descriptor_size=48 descriptor_version=1 count=2 map_size=96\n'
                      '# idx type phys virt pages attr name\n'
                      '0000 07 0000000040000000 0000000000000000 0000000000004000 0000000000000008 CONV\n'
                      '0001 0b 0000000070000000 0000000000000000 0000000000000200 0000000000000001 MMIO\n')
        with self.assertRaises(stage2.MapError):
            stage2.parse_map(probe_text)


# --------------------------------------------------------------------------------------------------------------
# 5. cycle.py --target sp11 against the real thermal module

class CycleThermal(unittest.TestCase):
    """Reconciliation 5: cycle-target was tested against a stub; here the real hamoa_thermal answers."""

    @classmethod
    def setUpClass(cls):
        cls.tct = load('tools/test-cycle-targets.py', 'sp11_test_cycle_targets')
        sys.modules.pop('hamoa_thermal', None)
        cls.thermal = import_tool('hamoa_thermal')
        cls.a16_thermal = import_tool('a16_thermal')

    def setUp(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        holder = tempfile.TemporaryDirectory(dir=BUILD, prefix='cycle-')
        self.addCleanup(holder.cleanup)
        self.dir = Path(holder.name).resolve()
        shutil.copyfile(self.tct.EXAMPLE, self.dir / 'pins.example.json')
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SNAP_BATTERY_MINIMUM', None)
        for name in ('subprocess.Popen', 'socket.socket', 'os.system'):
            patcher = mock.patch(name, side_effect=AssertionError(f'{name} is blocked: no machine access'))
            patcher.start()
            self.addCleanup(patcher.stop)

    def enforce(self, mode, change=None, offset=None):
        tct = self.tct
        section = tct.good_section(tct.CASES[mode][1]) if offset is None else tct.good_section(tct.CASES[mode][1], offset)
        if change:
            change(section)
        (self.dir / 'pins.json').write_text(json.dumps({'sp11': section}))
        args = tct.parse(['--target', 'sp11'] + tct.CASES[mode][0])
        with self.assertRaises(tct.targets.TargetRefused) as raised:
            tct.targets.enforce(args, tct.stub_ws(), pins_dir=self.dir, clock=lambda: tct.NOW)
        return raised.exception

    def test_the_real_admission_refuses_every_target_until_the_fingerprint_is_measured(self):
        self.assertIsNone(self.thermal.FINGERPRINT)
        for mode in self.tct.NON_PASSIVE:
            with self.subTest(mode=mode):
                error = self.enforce(mode)
                self.assertEqual(error.code, 'thermal-refused', str(error))
                self.assertIn('fingerprint not yet measured', str(error))

    def test_the_stub_and_the_real_module_expose_the_same_contract(self):
        stub = self.tct.thermal_module()
        for name in ('admit_target', 'power_attestation_ok'):
            self.assertTrue(callable(getattr(self.thermal, name)))
            self.assertTrue(callable(getattr(stub, name)))
            self.assertEqual(list(inspect.signature(getattr(self.thermal, name)).parameters),
                             list(inspect.signature(getattr(stub, name)).parameters))

    def test_the_real_attestation_check_accepts_what_the_cycle_gate_records(self):
        tct = self.tct
        for mode in tct.NON_PASSIVE:
            with self.subTest(mode=mode):
                prefix = tct.CASES[mode][1]
                record = tct.good_section(prefix)['power_attestation']
                four = {key: record[key] for key in ('ac_connected', 'battery_percent', 'recorded_utc', 'run_prefix')}
                self.assertIs(self.thermal.power_attestation_ok(four, tct.NOW, prefix), True)

    def test_the_cross_check_only_narrows_what_the_cycle_gate_accepts(self):
        """Offsets of recorded_utc from now, seconds: (offset, the refusal code that ends the run)."""
        for offset, code in ((-60, 'thermal-refused'), (0, 'thermal-refused'), (-1800, 'thermal-refused'),
                             (-1801, 'attestation-expired'),
                             (30, 'attestation-rejected'),     # inside the cycle gate's 60 s skew, outside thermal's 0 s
                             (90, 'attestation-invalid')):
            with self.subTest(offset=offset):
                error = self.enforce('status:run', offset=offset)
                self.assertEqual(error.code, code, str(error))
        record = {'ac_connected': True, 'battery_percent': None, 'recorded_utc': self.tct.stamp(30),
                  'run_prefix': 'status'}
        self.assertIs(self.thermal.power_attestation_ok(record, self.tct.NOW, 'status'), False)

    def test_the_a16_reason_numbers_are_kept_and_the_new_ones_start_after_them(self):
        a16, hamoa = self.a16_thermal.REASONS, self.thermal.REASONS
        self.assertEqual(sorted(a16), list(range(11)))
        self.assertEqual({k: hamoa[k] for k in range(11)}, a16)
        self.assertEqual(sorted(set(hamoa) - set(a16)), [11, 12, 13])
        old = list(c_enum('compat/a16-thermal.h', 'a16_thermal_reason'))
        new = list(c_enum('compat/hamoa-thermal.h', 'hamoa_thermal_reason'))
        self.assertEqual([n.replace('A16_THERMAL_', '') for n in old],
                         [n.replace('HAMOA_THERMAL_', '') for n in new[:11]])
        self.assertEqual([n.replace('HAMOA_THERMAL_', '') for n in new[11:]], ['NO_SOURCE', 'NOT_READY', 'UNCALIBRATED'])

    def test_neither_decoder_reads_the_other_machines_report(self):
        for decoder, other_size in ((self.a16_thermal.decode, self.thermal.SIZE), (self.thermal.decode, self.a16_thermal.SIZE)):
            with self.subTest(decoder=decoder.__module__):
                with self.assertRaises(ValueError):
                    decoder(bytes([1]) * other_size)
        self.assertNotEqual(self.thermal.SIZE, self.a16_thermal.SIZE)


# --------------------------------------------------------------------------------------------------------------
# 6. The fair lock

class FairLock(unittest.TestCase):
    """Reconciliation 6: six SP11 vCPUs span two clusters, which needs SNAP_FAIR_TOKEN; A16 stays as it was."""

    @classmethod
    def setUpClass(cls):
        if not find_tool('clang'):
            raise unittest.SkipTest('clang not found')
        cls.topology = load('tools/test-guest-topology.py', 'sp11_test_guest_topology')
        cls.topology.BUILD = BUILD / 'topology'     # not the path tools/test-guest-topology.py builds: the suite runs both

    def test_the_model_refuses_six_sp11_vcpus_without_a_fair_lock_and_accepts_them_with_one(self):
        place, status = self.topology.place, self.topology.ST
        self.assertEqual(place('sp11', 'pack', 0, 6, False)[0], status['TOPOLOGY_NEEDS_FAIR_LOCK'])
        self.assertEqual(place('sp11', 'pack', 0, 6, True)[0], status['TOPOLOGY_OK'])
        self.assertEqual(place('sp11', 'pack', 0, 4, False)[0], status['TOPOLOGY_OK'])      # one cluster

    def test_the_a16_placement_is_the_one_it_always_was(self):
        status, affinities = self.topology.place('a16', 'pack', 0, 6, False)
        self.assertEqual(status, self.topology.ST['TOPOLOGY_OK'])
        self.assertEqual(affinities, [0x0, 0x100, 0x200, 0x300, 0x400, 0x500])

    def test_the_token_is_opt_in_for_every_build_that_exists_today(self):
        flags = json.loads(read('config/monitor-flags.json'))['flags']
        self.assertFalse([f for f in flags if 'SNAP_FAIR_TOKEN' in f or 'SNAP_PLATFORM_SP11' in f], flags)
        for path in [COMPAT / 'service-lock.h', COMPAT / 'cpu.h', PROBES / 'xnuboot.c'] + list((PROBES / 'xnuboot').glob('*.c')):
            self.assertNotRegex(path.read_text(encoding='utf-8'), r'#\s*define\s+SNAP_FAIR_TOKEN\s+[1-9]', path.name)


# --------------------------------------------------------------------------------------------------------------
# 7. Wiring

SP11_ONLY_HEADERS = ('board-info.h', 'nv2-gate.h', 'oryon-idregs.h', 'topology.h', 'ipa-geometry.h', 'hamoa-thermal.h')
SP11_UNITS = ('compat/board-info.c', 'compat/nv2-gate.c', 'compat/oryon-idregs.c', 'compat/topology.c',
              'compat/ipa-geometry.c', 'compat/hamoa-thermal.c')
SP11_FLAGS = ('-DSNAP_PLATFORM_SP11=1', '-DSNAP_FAIR_TOKEN=1', '-DSNAP_GICD_BASE=0x17000000',
              '-DSNAP_GICR_FIRST=0x17080000', '-DSNAP_GICR_LIMIT=0x17380000', '-DSNAP_GICR_STRIDE=0x40000',
              '-DSNAP_TIMER_INTID=26')


def gated_includes(text):
    """[(line, header, gated)] for each #include of an SP11-only header; gated means it can only be reached when
    SNAP_PLATFORM_SP11 is nonzero (inside `#if SNAP_PLATFORM_SP11`, or the #else of `#if !SNAP_PLATFORM_SP11`)."""
    stack, found = [], []
    for number, line in enumerate(text.splitlines(), 1):
        directive = re.match(r'\s*#\s*(if|ifdef|ifndef|elif|else|endif|include)\b(.*)', line)
        if not directive:
            continue
        word, rest = directive.groups()
        if word in ('if', 'ifdef', 'ifndef'):
            stack.append([word, rest.strip(), False])
        elif word == 'elif':
            stack[-1] = ['elif', rest.strip(), True]
        elif word == 'else':
            stack[-1][2] = True
        elif word == 'endif':
            stack.pop()
        else:
            header = re.search(r'"(?:[^"]*/)?([\w.-]+\.h)"', rest)
            if header and header.group(1) in SP11_ONLY_HEADERS:
                gated = any(('SNAP_PLATFORM_SP11' in cond and not in_else and not re.search(r'!\s*\(?\s*SNAP_PLATFORM_SP11', cond))
                            or ('SNAP_PLATFORM_SP11' in cond and in_else and re.search(r'!\s*\(?\s*SNAP_PLATFORM_SP11', cond))
                            for _, cond, in_else in stack)
                found.append((number, header.group(1), gated))
    return found


class Wiring(unittest.TestCase):
    """The coordinator-owned wiring of the ten patches: link tuples, include guards and the SP11 builds."""

    def test_every_sp11_unit_is_linked_once_and_only_with_the_sp11_define(self):
        optional = top_level_literal('probes/run.py', 'OPTIONAL')['xnuboot']
        sp11 = [source for define, source in optional if define == '-DSNAP_PLATFORM_SP11=1']
        self.assertEqual(sorted(sp11), sorted(SP11_UNITS))
        self.assertEqual(len(set(optional)), len(optional))
        for define, source in optional:
            self.assertTrue((ROOT / source).is_file(), source)
        a16 = [source for define, source in optional if define in ('-O1', '-Os')]
        self.assertEqual(a16, [])
        self.assertEqual([s for d, s in optional if d != '-DSNAP_PLATFORM_SP11=1'],
                         ['compat/adreno-coherent.c', 'compat/edp-backlight.c'])

    def test_sp11_headers_are_included_only_where_the_sp11_gate_is_on(self):
        own = set(SP11_ONLY_HEADERS) | {Path(unit).name for unit in SP11_UNITS}    # linked only by the OPTIONAL tuples
        sources = [p for p in list(PROBES.glob('*.[ch]')) + list((PROBES / 'xnuboot').glob('*.[ch]'))
                   + list(PROBES.glob('*.S')) + list(COMPAT.glob('*.[ch]')) if p.name not in own]
        seen = 0
        for path in sorted(sources):
            for number, header, gated in gated_includes(path.read_text(encoding='utf-8', errors='replace')):
                seen += 1
                with self.subTest(source=path.relative_to(ROOT).as_posix(), line=number, header=header):
                    self.assertTrue(gated)
        self.assertGreaterEqual(seen, 4, 'the scan found fewer includes than the merge adds')

    def test_the_include_scanner_sees_an_unguarded_include(self):
        text = '#include "a.h"\n#include "../compat/board-info.h"\n#if SNAP_PLATFORM_SP11\n#include "x/nv2-gate.h"\n#endif\n'
        self.assertEqual([(g[1], g[2]) for g in gated_includes(text)], [('board-info.h', False), ('nv2-gate.h', True)])
        inverted = '#if !SNAP_PLATFORM_SP11\n#include "topology.h"\n#else\n#include "topology.h"\n#endif\n'
        self.assertEqual([g[2] for g in gated_includes(inverted)], [False, True])

    def build_monitor(self, name, *flags):
        clang = find_tool('clang')
        if not clang or not find_tool('ld.lld'):
            self.skipTest('clang and ld.lld are needed')
        out = BUILD / name
        shutil.rmtree(out, ignore_errors=True)
        env = dict(os.environ, PATH=os.pathsep.join(list(CLANG_DIRS) + ['/opt/homebrew/opt/lld/bin', os.environ['PATH']]))
        return subprocess.run([sys.executable, '-B', str(TOOLS / 'monitor_build_offline.py'), '.', str(out.relative_to(ROOT)),
                               'config/monitor-flags.json', *flags], cwd=ROOT, env=env, capture_output=True, text=True,
                              timeout=900), out

    def test_the_merged_monitor_builds_and_links_with_the_bare_sp11_gate(self):
        done, out = self.build_monitor('sp11-bare', '-DSNAP_PLATFORM_SP11=1')
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        self.assertGreater(json.loads(done.stdout.strip().splitlines()[-1])['bytes'], 100000)

    def test_the_documented_sp11_flag_set_builds_and_links(self):
        done, out = self.build_monitor('sp11-full', *SP11_FLAGS)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])

    def test_a_vcpu_count_other_than_six_still_stops_the_sp11_build(self):
        for count in ('4', '12'):
            with self.subTest(count=count):
                done, _ = self.build_monitor(f'sp11-n{count}', '-DSNAP_PLATFORM_SP11=1', f'-DGUEST_CPU_COUNT={count}')
                self.assertNotEqual(done.returncode, 0)


if __name__ == '__main__':
    unittest.main()
