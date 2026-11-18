#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the opt-in CPU-start acknowledge of the SP11 M1 monitor (SNAP_SP11_CPU_START_ACK; docs/SP11-MONITOR-PROFILE.md section 10), monitor side:

  python3 tools/test-cpu-start-ack.py

HeaderTests: tools/test-cpu-start-ack.c against compat/cpu-start-ack.h (every syndrome field, the three registers, the neighbours, the accounting; an independent
oracle) and against 18 seeded faults of that header, each of which must make the program fail (a wider store, a read, an undescribed access, another exception class,
a stage-1 walk fault, any fault status, another block, a missing or extra register, an HPFAR that disagrees, a wrong offset, a wrong counter, an overrun ring, an unguarded
offset, an unmasked value). SourceTests: the branch in probes/xnuboot/trap_dispatch.c is behind the gate, after the A16 release, only without host_cpus, calls nothing
that starts a CPU, and every other piece of the monitor that names the gate is inside it; the state is the first byte after the context (the table arena starts at the next page
boundary and the wrapper holds the work base to a page) and xnuboot_setup zeroes all of it (a mutant of each pin is rejected). MonitorBuild (clang and a q1n1 checkout, else
SKIP, which is never a pass): the monitor's translation unit preprocesses to the same text as the commit's when the gate is off (and a seeded ungated hunk is told apart),
the gate off has none of the symbols, the gate on has the window block, the event number and a context of the default's size (the state sits in the slack of its last page), and a build the gate does not
fit (no SNAP_PLATFORM_SP11, SNAP_BOOT_RELOCATE) is an #error. Nothing here runs on hardware; the acknowledge is EXPERIMENTAL and NOT a model of the hardware."""
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_testlib as T  # noqa: E402

ROOT, TOOLS = T.ROOT, T.TOOLS
SCRATCH = ROOT / 'build' / 'cpu-start-ack-test-scratch'
HEADER = ROOT / 'compat/cpu-start-ack.h'
GATE = '#if SNAP_SP11_CPU_START_ACK'
# The identifiers of this feature in the monitor's sources: every line that holds one must sit inside an `#if SNAP_SP11_CPU_START_ACK` region.
FEATURE = re.compile(r'\b(cpu_start_ack\w*|CPU_START_ACK_\w+|EV_CPU_START_ACK|OUT_CPU_START_ACK|register_at)\b')
MONITOR_FILES = ('probes/xnuboot.c', 'probes/xnuboot/trap_dispatch.c', 'probes/xnuboot/report.c', 'probes/xnuboot/report_layout.c', 'probes/xnuboot/context.c',
                 'probes/xnuboot/setup.c')
# (what is wrong, the text of the header that goes, the text that replaces it): each text occurs once, and each fault must fail tools/test-cpu-start-ack.c.
MUTANTS = (
    ('a wider store', 'access->size != 4', 'access->size < 4'),
    ('a read', '!access->write || ', ''),
    ('an undescribed access', '!access->valid || ', ''),
    ('another exception class', '(esr >> 26) != CPU_START_ACK_EC_DABT || ', ''),
    ('a fault of the stage-1 walk', '(iss & CPU_START_ACK_ISS_S1PTW) ||', '0 ||'),
    ('any fault status', '(iss & CPU_START_ACK_DFSC_MASK) != CPU_START_ACK_DFSC_TRANSLATION', '0'),
    ('address-size faults too', '#define CPU_START_ACK_DFSC_MASK 0x3cu', '#define CPU_START_ACK_DFSC_MASK 0x38u'),
    ('another block', 'UINT64_C(0x300734000)', 'UINT64_C(0x300730000)'),
    ('no cluster register', 'at != CPU_START_ACK_CLUSTER_E && ', ''),
    ('the block\'s first word too', 'if (at != CPU_START_ACK_GLOBAL && at != CPU_START_ACK_CLUSTER_E && at != CPU_START_ACK_CLUSTER_P)', 'if (at & 3)'),
    ('an HPFAR that disagrees', '(hpfar & CPU_START_ACK_HPFAR_FIPA) && ', '0 && '),
    ('a wrong offset', '*offset = (unsigned)at;', '*offset = CPU_START_ACK_GLOBAL;'),
    ('a wrong counter', 's->at[offset / 4 - 1]++;', 's->at[0]++;'),
    ('an overrun ring', 'if (s->total < CPU_START_ACK_FIRST) {', 'if (s->total <= CPU_START_ACK_FIRST) {'),
    ('an unguarded offset', 'if (offset != CPU_START_ACK_GLOBAL && offset != CPU_START_ACK_CLUSTER_E && offset != CPU_START_ACK_CLUSTER_P)\n        return;\n', ''),
    ('an unmasked value', '(value & UINT64_C(0xffffffff))', 'value'),
    ('no last pc', '    s->last_pc = pc;\n', ''),
    ('no total', '    s->total++;\n', ''),
)


def clang():
    return T.find_tool('clang')


# The text this item must leave alone (see SourceTests.test_the_a16_release_path_is_not_edited_by_this_work_item), as of the fork tip eb6ba3b it is built on.
A16_BRANCH_SHA256 = 'd8124d2342cdea855344e41317e92932af55e20ca1fff0df6ecb6c5eeefaec21'
RELEASE_PINS = {'compat/cpu-release.c': '32bb11f8e509bd3cb70564c9890821b2c16c75ad01a089f4b67d32e03a082ae9',
                'compat/cpu-release.h': '0d43c582d2b38e6bbe7f1b6089f27e9f1cdc734ae79844e2500c257f35c683eb'}


def a16_branch(text):
    """The A16 CPU-start branch of the trap dispatcher: from its comment to the brace that closes it."""
    start = text.index('/* Exact binary:')
    return text[start:text.index('\n            }\n', text.index('secondary_release,&request')) + len('\n            }\n')]


def host_test(header_dir=None, name='t'):
    """(exit status, output) of tools/test-cpu-start-ack.c built against the real header, or against the one in `header_dir` when that comes first on the include path."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    exe = SCRATCH / name
    include = [f'-I{header_dir}'] if header_dir else []
    built = subprocess.run([clang(), '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', *include,
                            f'-I{ROOT / "compat"}', f'-I{ROOT / "shadow"}', str(TOOLS / 'test-cpu-start-ack.c'), str(ROOT / 'shadow/dockchannel.c'), '-o', str(exe)],
                           capture_output=True, text=True)
    if built.returncode:
        return None, built.stderr[-1500:]
    done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout + done.stderr


def inside_gate(text):
    """[(line number, line)] of the lines of `text` that name the feature outside every `#if SNAP_SP11_CPU_START_ACK` region, found with a stack of the conditionals."""
    stack, outside = [], []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if re.match(r'#\s*if', stripped):
            stack.append(stripped == GATE)
        elif re.match(r'#\s*endif', stripped):
            stack.pop()
        elif FEATURE.search(line) and not any(stack):
            outside.append((number, line.strip()))
    return outside


def placement_problems(context_text, setup_text, bundle_text):
    """What is wrong with where the acknowledge's state lives: [] when it is the first byte after the context, the table arena starts at the next page boundary (so the
    rest of the context's last page is no one's) and the wrapper's PIN geometry check holds the work base to a multiple of a page (so the context's end is where
    sizeof(struct context) % 4096 says)."""
    problems = []
    if 'return (struct cpu_start_ack *)((uintptr_t)c + sizeof(struct context));' not in context_text:
        problems.append('the accessor is not the first byte after the context')
    if '    c->next_table = (work + sizeof(*c) + 0xfff) & ~0xfffUL;\n' not in setup_text:
        problems.append('the table arena does not start at the page boundary after the context')
    align = re.search(r'#define SP11SB_WORK_ALIGN\s+UINT64_C\((0x[0-9a-fA-F]+)\)', bundle_text)
    if not align or int(align.group(1), 16) % 4096:
        problems.append('the wrapper does not hold the work base to a page')
    if 'wb & (SP11SB_WORK_ALIGN - 1)' not in bundle_text or 'return SP11SB_E_PLACE_ALIGN' not in bundle_text:
        problems.append('the wrapper does not refuse a work base that is not aligned')
    return problems


def zeroing_problems(setup_text):
    """[] when xnuboot_setup zeroes every word of the acknowledge's state in its own gated block, the last thing it does before it returns success: the host does not clear
    the work area, so a stale count of an earlier run would otherwise be this run's."""
    start = setup_text.find(GATE)
    if start < 0:
        return ['xnuboot_setup has no gated block that zeroes the state']
    end = setup_text.index('#endif', start)
    problems = []
    if 'for (unsigned n = 0; n < sizeof(struct cpu_start_ack) / 8; n++) ((volatile uint64_t *)cpu_start_ack_of(c))[n] = 0;' not in setup_text[start:end]:
        problems.append('the loop does not zero every word of the state')
    if setup_text[end:].split('\n')[1:3] != ['    return 0;', '}']:
        problems.append('the zeroing is not the last thing before success')
    return problems


@unittest.skipUnless(clang(), 'no clang')
class HeaderTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(SCRATCH / 'mutants', ignore_errors=True)

    def test_the_real_header_passes_the_host_test(self):
        code, out = host_test()
        self.assertEqual(code, 0, out)
        self.assertIn('PASS: the CPU-start acknowledge takes exactly', out)

    def test_every_seeded_fault_of_the_header_fails_the_host_test(self):
        text = HEADER.read_text()
        for what, old, new in MUTANTS:
            with self.subTest(what):
                self.assertEqual(text.count(old), 1, f'{old!r} must occur once in the header')
                directory = SCRATCH / 'mutants' / re.sub(r'\W+', '-', what)
                directory.mkdir(parents=True, exist_ok=True)
                (directory / 'cpu-start-ack.h').write_text(text.replace(old, new))
                code, out = host_test(directory, 'mutant')
                self.assertIsNotNone(code, f'the faulty header must still compile: {out}')
                self.assertNotEqual(code, 0, f'the host test accepted a header with {what}')

    def test_the_host_test_itself_can_fail(self):
        directory = SCRATCH / 'mutants' / 'none'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'cpu-start-ack.h').write_text(HEADER.read_text().replace('CPU_START_ACK_FIRST 8u', 'CPU_START_ACK_FIRST 7u'))
        code, out = host_test(directory, 'mutant')
        self.assertTrue(code is None or code != 0, out)


class SourceTests(unittest.TestCase):
    def test_the_branch_sits_behind_the_gate_after_the_a16_release_and_starts_nothing(self):
        text = (ROOT / 'probes/xnuboot/trap_dispatch.c').read_text()
        self.assertEqual(text.count(GATE), 1)
        start = text.index(GATE)
        block = text[start:text.index('#endif', start)]
        self.assertLess(text.index('guest_cpu_release_write(&c->cpu_release,'), start)
        self.assertLess(start, text.index('int window = dc_window('))
        self.assertIn('if (!c->host_cpus) {', block)
        self.assertIn('cpu_start_ack_match(esr,ipa,hpfar,&a,&register_at)', block)
        self.assertEqual((block.count('*elr+=4;'), block.count('return 0;')), (1, 1))
        calls = set(re.findall(r'\b([A-Za-z_]\w*)\(', re.sub(r'/\*.*?\*/', '', block, flags=re.S))) - {'if'}
        self.assertEqual(calls, {'dc_decode', 'cpu_start_ack_match', 'cpu_start_ack_of', 'dc_narrow', 'cpu_start_ack_note', 'record'})
        self.assertNotIn('__asm__', block)
        self.assertEqual(text.count('guest_cpu_release_write('), 1, 'the A16 release is untouched and the acknowledge never calls it')

    def test_the_a16_release_and_the_acknowledge_name_the_same_block(self):
        text = (ROOT / 'probes/xnuboot/trap_dispatch.c').read_text()
        release = a16_branch(text)
        literals = sorted(int(n, 16) for n in re.findall(r'UINT64_C\((0x30073400\w)\)', release))
        header = HEADER.read_text()
        block = int(re.search(r'#define CPU_START_ACK_BLOCK\s+UINT64_C\((0x[0-9a-f]+)\)', header).group(1), 16)
        self.assertEqual(literals, [block, block, block + 4, block + 12])         # the range's ends and the base the release subtracts, in both of its spellings
        self.assertEqual(re.findall(r'#define CPU_START_ACK_(GLOBAL|CLUSTER_E|CLUSTER_P)\s+(\d+)u', header), [('GLOBAL', '4'), ('CLUSTER_E', '8'), ('CLUSTER_P', '12')])

    def test_the_a16_release_path_is_not_edited_by_this_work_item(self):
        """Pins of the text this item must leave alone: the A16 branch of the trap dispatcher and compat/cpu-release.c and .h (whose own host test is
        tools/test-cpu-release.py). A legitimate later change moves the pin on purpose."""
        branch = a16_branch((ROOT / 'probes/xnuboot/trap_dispatch.c').read_text())
        self.assertEqual(T.sha(branch.encode()), A16_BRANCH_SHA256)
        for rel, digest in RELEASE_PINS.items():
            self.assertEqual(T.sha((ROOT / rel).read_bytes()), digest, rel)

    def test_every_piece_of_the_feature_in_the_monitor_is_inside_the_gate(self):
        for rel in MONITOR_FILES:
            with self.subTest(rel):
                self.assertEqual(inside_gate((ROOT / rel).read_text()), [])
        pieces = {rel: (ROOT / rel).read_text().count(GATE) for rel in MONITOR_FILES}
        self.assertEqual(pieces, {'probes/xnuboot.c': 1, 'probes/xnuboot/trap_dispatch.c': 1, 'probes/xnuboot/report.c': 1, 'probes/xnuboot/report_layout.c': 1,
                                  'probes/xnuboot/context.c': 1, 'probes/xnuboot/setup.c': 1})

    def test_the_gate_scan_can_fail(self):
        text = (ROOT / 'probes/xnuboot/report.c').read_text()
        ungated = text.replace(GATE + '\n    out[OUT_CPU_START_ACK]', '#if 1\n    out[OUT_CPU_START_ACK]')
        self.assertNotEqual(ungated, text)
        self.assertEqual(len(inside_gate(ungated)), 4)
        stray = (ROOT / 'probes/xnuboot/context.c').read_text() + '\nstruct cpu_start_ack stray;\n'
        self.assertEqual(len(inside_gate(stray)), 1)

    def test_the_state_sits_in_the_slack_after_the_context_which_nothing_else_uses(self):
        context, setup, bundle = ((ROOT / rel).read_text() for rel in ('probes/xnuboot/context.c', 'probes/xnuboot/setup.c', 'probes/sp11-stick-bundle.h'))
        self.assertEqual(placement_problems(context, setup, bundle), [])
        for what, text_index, old, new in (
                ('an accessor that is not right after the context', 0, 'sizeof(struct context));', 'sizeof(struct context) - 8);'),
                ('an arena that starts mid-page', 1, 'c->next_table = (work + sizeof(*c) + 0xfff) & ~0xfffUL;', 'c->next_table = work + sizeof(*c);'),
                ('a work alignment under a page', 2, 'SP11SB_WORK_ALIGN UINT64_C(0x10000)', 'SP11SB_WORK_ALIGN UINT64_C(0x800)'),
                ('no work alignment check', 2, 'wb & (SP11SB_WORK_ALIGN - 1) ||', '')):
            with self.subTest(what):
                texts = [context, setup, bundle]
                self.assertEqual(texts[text_index].count(old), 1, old)
                texts[text_index] = texts[text_index].replace(old, new)
                self.assertTrue(placement_problems(*texts), f'the placement check accepted {what}')

    def test_setup_zeroes_the_whole_state_because_the_host_does_not_clear_the_work_area(self):
        setup = (ROOT / 'probes/xnuboot/setup.c').read_text()
        self.assertEqual(zeroing_problems(setup), [])
        for what, old, new in (('a loop that leaves the last word stale', 'sizeof(struct cpu_start_ack) / 8; n++)', 'sizeof(struct cpu_start_ack) / 8 - 1; n++)'),
                               ('no zeroing at all', GATE + '\n    /* Not part of struct context', '#if 0\n    /* Not part of struct context'),
                               ('a zeroing that is not the last thing before success', '#endif\n    return 0;\n}', '#endif\n    c->params = 0;\n    return 0;\n}')):
            with self.subTest(what):
                self.assertEqual(setup.count(old), 1, old)
                self.assertTrue(zeroing_problems(setup.replace(old, new)), f'the zeroing check accepted {what}')

    def test_the_gate_defaults_off_and_refuses_what_it_does_not_fit(self):
        text = (ROOT / 'probes/xnuboot.c').read_text()
        at = text.index('#ifndef SNAP_SP11_CPU_START_ACK')
        gate = text[at:text.index('#include "../compat/cpu-start-ack.h"', at)]
        self.assertIn('#define SNAP_SP11_CPU_START_ACK 0', gate)
        self.assertRegex(gate, r'#if !SNAP_PLATFORM_SP11\n#error')
        self.assertRegex(gate, r'#if SNAP_BOOT_RELOCATE\n#error')
        layout = (ROOT / 'probes/xnuboot/report_layout.c').read_text()
        self.assertRegex(layout, r'#if SNAP_CPU_DVFS \|\| SNAP_GPU_DVFS\n#error "the CPU-start acknowledge record')


@unittest.skipUnless(clang() and T.q1n1_tree(), 'no clang or q1n1 checkout')
class MonitorBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(TOOLS))
        import monitor_build_offline as offline
        cls.offline = offline
        cls.uefi, why = offline.locate_q1n1(ROOT)
        assert cls.uefi, why
        nv2 = T.load('nv2_record', TOOLS / 'nv2_record.py')
        profile = T.load('sp11_m1_profile', TOOLS / 'sp11_m1_profile.py')
        cls.scratch = SCRATCH / 'monitor'
        shutil.rmtree(cls.scratch, ignore_errors=True)
        cls.scratch.mkdir(parents=True)
        record = nv2.Record(soc=nv2.SOC['x1e80100'], midr_el1=0xFEED4B01, revidr_el1=0, id_aa64mmfr2_el1=0x02001234, id_aa64mmfr4_el1=0,
                            vncr_redirect=nv2.RESULT['pass'], negative_control_trapped=nv2.RESULT['pass'], evidence=nv2.EVIDENCE['OBSERVED'],
                            tool='wi4b-test-only', build_id=0x4B01)
        text = nv2.emit_header(nv2.pack(record))
        T.write(cls.scratch / 'test-record.h', text.encode() if isinstance(text, str) else text)
        cls.flags = {}
        for choice in ('stop', 'ack'):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = profile.main(['flags', '--nv2-record-header', str(cls.scratch / 'test-record.h'), '--cpu-start', choice])
            assert code == 0, err.getvalue()
            cls.flags[choice] = json.loads(out.getvalue())['flags']

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def run_clang(self, flags, source, mode, root=ROOT, name='probe'):
        path = self.scratch / f'{name}.c'
        path.write_text(source)
        return subprocess.run([self.offline.LLVM / 'clang', *flags, f'-I{root / "shadow"}', f'-I{self.uefi}', *mode, str(path)], capture_output=True, text=True)

    def tu(self, root=ROOT):
        return f'#include "{root / "probes/xnuboot.c"}"\n#include <stddef.h>\n'

    def test_the_two_profiles_differ_by_the_one_define_before_the_record(self):
        stop, ack = self.flags['stop'], self.flags['ack']
        self.assertEqual(ack, [*stop[:-1], '-DSNAP_SP11_CPU_START_ACK=1', stop[-1]])
        self.assertFalse([flag for flag in stop if 'CPU_START' in flag])

    def test_the_gate_off_has_none_of_it(self):
        source = self.tu() + ''.join(f'#ifdef {name}\n#error "{name} is defined with the gate off"\n#endif\n' for name in ('EV_CPU_START_ACK', 'OUT_CPU_START_ACK',
                                                                                                                         'CPU_START_ACK_MAGIC', 'CPU_START_ACK_BLOCK'))
        source += '#if SNAP_SP11_CPU_START_ACK != 0\n#error "the gate is not off by default"\n#endif\n'
        done = self.run_clang(self.flags['stop'], source, ['-fsyntax-only'])
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])

    def value(self, flags, expression, name='value'):
        """The number `expression` has in the monitor's translation unit built with `flags`: clang prints it when an assertion that it is -1 fails."""
        done = self.run_clang(flags, self.tu() + f'_Static_assert(({expression}) == -1, "PROBE");\n', ['-fsyntax-only'], name=name)
        found = re.search(r"expression evaluates to '(-?\d+) == -1'", done.stderr)
        self.assertTrue(found, done.stderr[-800:])
        return int(found.group(1))

    def test_the_gate_on_has_the_window_block_the_event_and_a_context_that_is_the_defaults(self):
        asserts = '\n'.join(['_Static_assert(SNAP_SP11_CPU_START_ACK == 1, "gate");', '_Static_assert(EV_CPU_START_ACK == 61, "event");',
                             '_Static_assert(OUT_CPU_START_ACK == 8000 && 16 + 8 * OUT_CPU_START_ACK == 0xFA10, "window word and byte offset");',
                             '_Static_assert(CPU_START_ACK_REPORT_WORDS == 24 && OUT_CPU_START_ACK + CPU_START_ACK_REPORT_WORDS <= (0x10000 - 16) / 8, "fits");',
                             '_Static_assert(OUT_CPU_START_ACK == OUT_THERMAL + A16_THERMAL_REPORT_WORDS, "after the thermal record");',
                             '_Static_assert(sizeof(struct cpu_start_ack) == 176, "state");'])
        done = self.run_clang(self.flags['ack'], self.tu() + asserts + '\n', ['-fsyntax-only'])
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])
        wrong = self.run_clang(self.flags['ack'], self.tu() + '_Static_assert(OUT_CPU_START_ACK == 8001, "a wrong offset");\n', ['-fsyntax-only'], name='wrong')
        self.assertNotEqual(wrong.returncode, 0, 'the check of the window offset can fail')
        stop, ack = (self.value(self.flags[choice], 'sizeof(struct context)', choice) for choice in ('stop', 'ack'))
        self.assertEqual(stop, ack, 'the state is not a member of the context: no offset and no size of it moves, and the table arena starts where it did')
        self.assertGreaterEqual(4096 - ack % 4096, 176)
        self.assertNotEqual(ack % 4096, 0)

    def test_a_build_the_gate_does_not_fit_is_an_error(self):
        a16 = json.loads((ROOT / 'config/monitor-flags.json').read_text())['flags']
        done = self.run_clang([*a16, '-DSNAP_SP11_CPU_START_ACK=1'], self.tu(), ['-fsyntax-only'], name='a16')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('diagnostic of the Surface Pro 11', done.stderr)
        done = self.run_clang([*self.flags['ack'], '-DSNAP_BOOT_RELOCATE=1'], self.tu(), ['-fsyntax-only'], name='relocate')
        self.assertIn('both want the PMGR CPU-start block', done.stderr)

    @unittest.skipUnless(shutil.which('git') and shutil.which('tar'), 'no git or tar')
    def test_with_the_gate_off_the_translation_unit_preprocesses_to_the_commits(self):
        """The default build is unchanged by construction: the text the compiler sees with the gate off is the same as the commit's (clang -E -P, the profile's flags),
        so no byte of the default monitor can differ. Like the gate-off rebuilds of tools/test-sp11-stick-monitor-build.py this compares the tree with the commit it is
        checked out at; the lasting pins are the byte pins of the record suites and tools/monitor_baseline.py. A hunk that lost its gate is told apart."""
        head = self.scratch / 'head'
        head.mkdir()
        archive = subprocess.run(['git', '-C', str(ROOT), 'archive', 'HEAD', 'probes', 'compat', 'shadow'], capture_output=True)
        if archive.returncode:
            self.skipTest('git cannot archive HEAD')
        subprocess.run(['tar', '-x', '-C', str(head)], input=archive.stdout, check=True)
        mode = ['-E', '-P']
        mine = self.run_clang(self.flags['stop'], self.tu(), mode, name='mine')
        theirs = self.run_clang(self.flags['stop'], self.tu(head), mode, root=head, name='theirs')
        self.assertEqual((mine.returncode, theirs.returncode), (0, 0), mine.stderr[-500:] + theirs.stderr[-500:])
        self.assertGreater(len(mine.stdout), 200_000)         # the whole translation unit, not an error's empty text
        self.assertEqual(mine.stdout, theirs.stdout)
        ungated = self.scratch / 'ungated'
        shutil.copytree(ROOT / 'probes', ungated / 'probes')
        shutil.copytree(ROOT / 'compat', ungated / 'compat')
        shutil.copytree(ROOT / 'shadow', ungated / 'shadow')
        report = ungated / 'probes/xnuboot/report.c'
        text = report.read_text()
        self.assertIn(GATE + '\n    out[OUT_CPU_START_ACK]', text)
        report.write_text(text.replace(GATE + '\n    out[OUT_CPU_START_ACK]', '#if 1\n    out[OUT_CPU_START_ACK]'))
        changed = self.run_clang(self.flags['stop'], self.tu(ungated), mode, root=ungated, name='changed')
        self.assertEqual(changed.returncode, 0, changed.stderr[-500:])
        self.assertNotEqual(changed.stdout, theirs.stdout, 'a hunk without its gate is told apart')


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
