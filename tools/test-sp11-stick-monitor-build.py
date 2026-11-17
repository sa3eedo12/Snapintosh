#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the binaries, the build tool and the gate of the monitor entry (docs/SP11-STICK-MONITOR.md sections 6 to 8), monitor port work item 4b,
Phase A: H3 the build (SNAPMON-M1.BIN is the wrapper, zero padding and the profile build's monitor byte for byte, called by one direct `bl`, with the
position-independence proof and every refusal of the tool, each shown to fail on a seeded input), H4 the gate off (the binaries that ran on the unit
rebuild byte for byte, and the GIC variant is not changed by the edits of this work item), the census of the wrapper binary (the system registers it
writes and reads, the system instructions, no SMC, HVC, TLBI or WFI, no GIC store of its own), H7 the call-graph census of the monitor's stack and, from
Phase C, the execution of the trampoline's real bytes at EL2 under QEMU (class Trampoline; SKIP without qemu-system-aarch64).

    python3 tools/test-sp11-stick-monitor-build.py

These tests need clang, ld.lld, the llvm tools and a q1n1 checkout (Q1N1_SP11_TREE); without them the tests that build report SKIP, which is not a
pass. The monitor used is built here from a LOCAL TEST RECORD of the NV2 gate (synthetic: it names no unit and is never offered as one) into the
scratch directory. No hardware, nothing under /Volumes; everything is written below build/sp11-monitor-build-test-scratch/ and deleted at the end.
EXPERIMENTAL; hardware NOT RUN."""
import collections
import concurrent.futures
import contextlib
import hashlib
import importlib.util
import io
import itertools
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_build as wb  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

ROOT, TOOLS = T.ROOT, T.TOOLS
SCRATCH = ROOT / 'build' / 'sp11-monitor-build-test-scratch'
LLVM = ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-readelf', 'llvm-nm', 'llvm-objdump')
STATE = {}
# The dry-run wrapper and reporter that ran on the unit (docs/SP11-MONITOR-PORT.md D7); the gate off must rebuild them byte for byte.
UNIT_RUN = {'SNAPMON.BIN': (35164, '6137984fbc61a48845e1ca7283911c6a87a4955802eebdb58ef941660bed2d15'),
            'SNAPREP.BIN': (21076, '940c774f1377980f378929400571ed223f00c5fb87a054fbcd2a092f3c0b9aab')}
OWN = ('probes/sp11-stick-monitor.c', 'probes/sp11-stick-monitor.h', 'probes/sp11-stick-monitor-record.h', 'tools/test-sp11-stick-monitor.c',
       'tools/test-sp11-stick-monitor.py', 'tools/test-sp11-stick-monitor-build.py', 'tools/test-sp11-stick-monitor-record.py',
       'tools/test-sp11-stick-monitor-call.S', 'tools/sp11_stick_monitor_testlib.py', 'docs/SP11-STICK-MONITOR.md')
SHARED = ('probes/sp11-stick-wrapper.c', 'probes/sp11-stick-wrapper.h', 'probes/sp11-stick-hw.h', 'probes/sp11-stick-reporter.c')   # earlier files that carry this item's gated hooks
# WI 8 pass 5 moved the GIC dry run's reporter line list into sp11-stick-gic-record.h (SG_REPORTER_ORDER) with one statement of the reporter: a GIC change,
# not a hook of this item, so the committed reporter is compared with that statement as the working tree has it.
REPORTER_ORDER = re.compile(r'static const uint8_t order\[\] = [^;]*;')
# The census of the wrapper binary: what it writes (the GIC arm's class C: nothing else), what it reads, and the system instructions it has.
WRITES = {'ICC_PMR_EL1': 1, 'ICC_BPR1_EL1': 1, 'ICC_IGRPEN1_EL1': 1}
READS_BEFORE = {'CurrentEL', 'MIDR_EL1', 'MPIDR_EL1', 'CNTFRQ_EL0', 'CNTPCT_EL0', 'CTR_EL0', 'HCR_EL2', 'SCTLR_EL2', 'TCR_EL2', 'PAR_EL1', 'SPSel', 'DAIF',
                'ICC_SRE_EL2', 'ICC_CTLR_EL1', 'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1', 'ICC_RPR_EL1'}    # the dry-run and GIC wrapper's
READS_NEW = {'VBAR_EL2', 'TPIDR_EL2', 'CNTHCTL_EL2', 'CNTHP_CTL_EL2', 'MDCR_EL2', 'VTTBR_EL2', 'VTCR_EL2', 'VMPIDR_EL2', 'ID_AA64MMFR2_EL1',
             'ID_AA64MMFR4_EL1', 'REVIDR_EL1', 'ID_AA64ISAR1_EL1', 'ID_AA64ISAR2_EL1'}                              # this stage's
READS_ARM = {'ICC_HPPIR1_EL1'}      # WI 8 pass 5: the GIC arm's one read after its stores, with IRQ masked (refuses FOREIGN_PENDING); no side effect, unlike IAR1
SYSTEM = {('dc', 'cvac'), ('dc', 'civac'), ('at', 's1e2r'), ('at', 's1e2w'), ('ic', 'ialluis'), ('dsb', 'sy'), ('dsb', 'ish'), ('isb', '')}
FORBIDDEN = {'eret', 'hvc', 'smc', 'svc', 'tlbi', 'wfe', 'wfi', 'sev', 'sevl', 'sys', 'sysl', 'br', 'braa', 'brab', 'brk', 'hlt', 'dcps1', 'dcps2', 'dcps3'}


def tools_ready():
    return bool(STATE.get('tree') and all(T.find_tool(t) for t in LLVM))


def quietly(function, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = function(*args, **kwargs)
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def refused(*argv):
    """(the REFUSED= name or None, standard error) of the build tool's main with these arguments."""
    code, out, err = quietly(wb.main, list(argv))
    found = re.search(r'^REFUSED=(\w+)$', out, re.M)
    return (found.group(1) if found else None), err, code


def tiny_elf(work, tag, before, align):
    """An ELF of two nops linked with the monitor's own linker script: `before` is assembly placed ahead of the entry, the vectors sit on `align` bytes."""
    work.mkdir(exist_ok=True)
    T.write(work / f'{tag}.S', (f'.section .text.entry,"ax"\n{before}.global _start\n_start: nop\n.text\n.balign {align}\n.global xnuboot_vectors\n'
                               f'xnuboot_vectors: nop\n').encode())
    T.clang(['--target=aarch64-unknown-none-elf', '-c', str(work / f'{tag}.S'), '-o', str(work / f'{tag}.o')], f'assembling {tag}')
    subprocess.run([T.find_tool('ld.lld'), '-T', str(ROOT / 'probes/probe.ld'), '-o', str(work / f'{tag}.elf'), str(work / f'{tag}.o')], check=True, capture_output=True)
    return work / f'{tag}.elf'


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    STATE['tree'] = T.q1n1_tree()
    if not tools_ready():
        return
    nv2 = T.load('nv2_record', TOOLS / 'nv2_record.py')
    profile = T.load('sp11_m1_profile', TOOLS / 'sp11_m1_profile.py')
    record = nv2.Record(soc=nv2.SOC['x1e80100'], midr_el1=0xFEED4B01, revidr_el1=0, id_aa64mmfr2_el1=0x02001234, id_aa64mmfr4_el1=0,
                        vncr_redirect=nv2.RESULT['pass'], negative_control_trapped=nv2.RESULT['pass'], evidence=nv2.EVIDENCE['OBSERVED'],
                        tool='wi4b-test-only', build_id=0x4B01)
    header = SCRATCH / 'record' / 'test-record.h'
    text = nv2.emit_header(nv2.pack(record))
    T.write(header, text.encode() if isinstance(text, str) else text)
    code, out, err = quietly(profile.main, ['build', '--nv2-record-header', str(header), str(SCRATCH / 'profile')])
    assert code == 0, (out, err)
    STATE['profile'] = SCRATCH / 'profile'
    STATE['record'] = json.loads((STATE['profile'] / 'profile-build.json').read_text())
    default = str(ROOT / 'config/monitor-profile-sp11-m1.json')
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        futures = {'m1': pool.submit(wb.build_monitor, str(SCRATCH / 'm1'), default, str(STATE['profile'] / 'xnuboot.bin'), str(STATE['profile'] / 'profile-build.json')),
                   'plain': pool.submit(wb.build, str(SCRATCH / 'plain'), default), 'gic': pool.submit(wb.build, str(SCRATCH / 'gic'), default, False, True)}
        STATE.update({name: future.result() for name, future in futures.items()})
    STATE['monitor'] = (STATE['profile'] / 'xnuboot.bin').read_bytes()
    STATE['funcs'] = T.parse_functions(T.disassemble(SCRATCH / 'm1' / 'snapmon-m1.elf'))


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not tools_ready() or 'm1' not in STATE:
            raise unittest.SkipTest('no clang, ld.lld, llvm tools or q1n1 checkout (Q1N1_SP11_TREE): nothing is built')


class Build(Base):
    """H3: the image, the call and the position-independence proof."""

    def test_the_image_is_the_wrapper_zeros_and_the_monitor_byte_for_byte(self):
        out, report = SCRATCH / 'm1', STATE['m1']
        image, wrapper = (out / 'SNAPMON-M1.BIN').read_bytes(), (out / 'wrapper-m1.bin').read_bytes()
        offset = report['monitor']['offset']
        self.assertEqual(offset % 4096, 0)
        self.assertEqual(image, wrapper + bytes(offset - len(wrapper)) + STATE['monitor'])
        self.assertEqual(image[offset:], STATE['monitor'])
        self.assertEqual(report['files']['SNAPMON-M1.BIN'], {'bytes': len(image), 'sha256': hashlib.sha256(image).hexdigest()})
        self.assertEqual(report['monitor']['sha256'], STATE['record']['monitor']['sha256'])
        self.assertEqual(report['wrapper'], {'bytes': len(wrapper), 'sha256': hashlib.sha256(wrapper).hexdigest(), 'padding': offset - len(wrapper)})
        self.assertEqual(report['hardware'], 'NOT RUN')
        self.assertEqual(report['profile'], STATE['record']['profile'], 'the report names the profile the monitor was built from (the authorisation record reads it)')
        self.assertEqual(report['monitor']['nv2_record'], STATE['record']['nv2_record']['record_sha256'], 'the report names the NV2 record the monitor was built with')

    def test_the_wrapper_pins_the_image_it_calls(self):
        digest = hashlib.sha256(STATE['monitor']).digest()
        words = [f'-DSP11_MONITOR_SHA{i}={int.from_bytes(digest[8 * i:8 * i + 8], "little"):#x}ULL' for i in range(4)]
        added = STATE['m1']['added']
        for item in (f'-DSP11_MONITOR_OFFSET={STATE["m1"]["monitor"]["offset"]:#x}', f'-DSP11_MONITOR_BYTES={len(STATE["monitor"])}', *words):
            self.assertIn(item, added)

    def test_one_direct_call_to_the_absolute_symbol(self):
        offset = STATE['m1']['monitor']['offset']
        calls = [(n, a) for n, ins in STATE['funcs'].items() for a, m, o in ins if m == 'bl' and o.startswith(f'0x{offset:x} ')]
        self.assertEqual([n for n, _ in calls], ['sw_monitor_call'])
        nm = subprocess.run([T.find_tool('llvm-nm'), str(SCRATCH / 'm1' / 'snapmon-m1.elf')], capture_output=True, text=True).stdout
        self.assertRegex(nm, rf'(?m)^0*{offset:x} A sp11_monitor$')
        body = [(m, o) for _, m, o in STATE['funcs']['sw_monitor_call']]
        self.assertEqual(body[:2], [('stp', 'x29, x30, [sp, #-0x20]!'), ('str', 'x19, [sp, #0x10]')])
        self.assertIn(('mov', 'sp, x5'), body)
        self.assertIn(('mov', 'sp, x19'), body)
        self.assertEqual(body[-1], ('ret', ''))

    def test_the_monitor_is_position_independent(self):
        self.assertEqual(set(STATE['m1']['monitor']['relocations']), {'R_AARCH64_CALL26', 'R_AARCH64_ADR_PREL_LO21', 'R_AARCH64_JUMP26', 'R_AARCH64_CONDBR19'})
        relink = (SCRATCH / 'm1' / 'monitor-pic' / 'xnuboot-relocs.bin').read_bytes()
        self.assertEqual(relink, STATE['monitor'], 'relinked with --emit-relocs, the objects flatten to the same bytes')

    def test_the_check_refuses_an_absolute_pointer(self):
        """The seeded input: one more object, with a pointer to itself in its data, in the link."""
        work = SCRATCH / 'evil'
        work.mkdir(exist_ok=True)
        T.write(work / 'evil.c', b'const void *const evil_pointer = &evil_pointer;\n')
        T.clang(['--target=aarch64-unknown-none-elf', '-ffreestanding', '-fno-pic', '-mcmodel=tiny', '-Os', '-c', str(work / 'evil.c'), '-o', str(work / 'evil.o')], 'compiling the seeded object')
        objects = wb.monitor_objects(STATE['profile'], STATE['record']['flags'])
        with self.assertRaises(wb.Refused) as caught:
            wb.relink_check([*objects, work / 'evil.o'], STATE['tree'], work / 'pic', STATE['monitor'])
        self.assertEqual(caught.exception.name, 'MONITOR_NOT_PIC')
        self.assertIn('ABS64', caught.exception.detail)
        counts = wb.relink_check(objects, STATE['tree'], work / 'pic-good', STATE['monitor'])
        self.assertEqual(counts, STATE['m1']['monitor']['relocations'], 'the real objects pass the same check')

    def test_the_check_refuses_objects_that_do_not_make_the_monitor(self):
        objects = wb.monitor_objects(STATE['profile'], STATE['record']['flags'])
        with self.assertRaises(wb.Refused) as caught:
            wb.relink_check(objects, STATE['tree'], SCRATCH / 'pic-other', STATE['monitor'][:-4] + b'\x00\x00\x00\x00')
        self.assertEqual(caught.exception.name, 'MONITOR_MISMATCH')

    def test_the_layout_check_refuses_a_misplaced_entry_and_vectors(self):
        """Tiny objects linked with the monitor's own linker script: the entry stub first and the vectors on a 2 KiB boundary pass; a word before
        the entry, or vectors on 1 KiB, are refused."""
        for tag, before, align in (('good', '', 2048), ('start', '    nop\n', 2048), ('vectors', '', 1024)):
            elf = tiny_elf(SCRATCH / 'layout', tag, before, align)
            if tag == 'good':
                wb.check_layout(elf)
            else:
                with self.assertRaises(wb.Refused) as caught:
                    wb.check_layout(elf)
                self.assertEqual(caught.exception.name, 'MONITOR_LAYOUT', tag)



class Refusals(Base):
    """H3: a monitor that is not the output of the named profile build, a wrapper over the offset and the rest, each REFUSED= by name."""

    count = 0

    def args(self, monitor=None, record=None, *more):
        """A fresh output directory every time: a refused build may leave a partial one, which --force replaces only when it holds a report."""
        Refusals.count += 1
        return ['--out', str(SCRATCH / f'refused-{Refusals.count}'), '--monitor', str(monitor or STATE['profile'] / 'xnuboot.bin'),
                '--monitor-build', str(record or STATE['profile'] / 'profile-build.json'), *more]

    def test_a_monitor_that_is_not_that_builds_output(self):
        other = SCRATCH / 'other'
        other.mkdir(exist_ok=True)
        data = bytearray(STATE['monitor'])
        data[100] ^= 1
        T.write(other / 'xnuboot.bin', bytes(data))
        name, why, _ = refused(*self.args(monitor=other / 'xnuboot.bin'))
        self.assertEqual(name, 'MONITOR_MISMATCH', why)

    def test_a_record_that_is_not_an_sp11_m1_profile_build(self):
        broken = SCRATCH / 'broken'
        broken.mkdir(exist_ok=True)
        for tag, change in (('profile', {'profile': 'a16'}), ('format', {'format': 2}), ('hardware', {'hardware': 'RUN'}), ('digest', {'monitor': {'bytes': 1, 'sha256': 'x', 'elf_sha256': 'y'}})):
            T.write(broken / f'{tag}.json', json.dumps({**STATE['record'], **change}).encode())
            name, why, _ = refused(*self.args(record=broken / f'{tag}.json'))
            self.assertEqual(name, 'MONITOR_BUILD_BAD', f'{tag}: {why}')
        name, why, _ = refused(*self.args(record=broken / 'absent.json'))
        self.assertEqual(name, 'MONITOR_BUILD_BAD', why)

    def test_a_record_with_no_elf_beside_it(self):
        bare = SCRATCH / 'bare'
        bare.mkdir(exist_ok=True)
        T.write(bare / 'profile-build.json', (STATE['profile'] / 'profile-build.json').read_bytes())
        name, why, _ = refused(*self.args(record=bare / 'profile-build.json'))
        self.assertEqual(name, 'MONITOR_BUILD_BAD', why)

    def test_a_record_whose_flags_break_the_rules_of_the_profile(self):
        """Each record below keeps the five GIC macros, which were the only flags checked before, so only the profile's own rules (tools/
        sp11_m1_profile.py load_profile) refuse it, and the detail names the rule."""
        flags = STATE['record']['flags']
        self.assertEqual(wb.flag_problems(flags), [], 'the control: the flags of the real profile build break no rule')
        header = next(f for f in flags if f.startswith('-DSNAP_NV2_RECORD_HEADER='))
        cases = {'a required define is absent': ([f for f in flags if f != '-DSNAP_FAIR_TOKEN=1'], '-DSNAP_FAIR_TOKEN=1 is required'),
                 'a required define has another value': (['-DSNAP_PLATFORM_SP11=0' if f == '-DSNAP_PLATFORM_SP11=1' else f for f in flags], '-DSNAP_PLATFORM_SP11=1 is required'),
                 'a forbidden name': ([*flags, '-DSNAP_BOOT_RELOCATE=1'], '-DSNAP_BOOT_RELOCATE is forbidden'),
                 'a forbidden part': ([*flags, '-DSNAP_SOME_THERMAL_GUARD=1'], '-DSNAP_SOME_THERMAL_GUARD is forbidden'),
                 'a base define the profile requires is absent': ([f for f in flags if f != '-DSNAP_NATIVE_CALLS=1'], '-DSNAP_NATIVE_CALLS=1 is required'),
                 'a define twice': ([*flags, '-DSNAP_FAIR_TOKEN=1'], '-DSNAP_FAIR_TOKEN occurs twice'),
                 'no NV2 header': ([f for f in flags if f != header], '-DSNAP_NV2_RECORD_HEADER is missing'),
                 'two NV2 headers': ([*flags, header.replace('test-record.h', 'other.h')], '-DSNAP_NV2_RECORD_HEADER occurs twice')}
        broken = SCRATCH / 'flags'
        broken.mkdir(exist_ok=True)
        for tag, (bad, expected) in cases.items():
            self.assertNotEqual(wb.macros_of(bad), None, f'{tag}: the five GIC macros are intact, so the older check passes this record')
            T.write(broken / 'profile-build.json', json.dumps({**STATE['record'], 'flags': bad}).encode())
            name, why, _ = refused(*self.args(record=broken / 'profile-build.json'))
            self.assertEqual(name, 'MONITOR_BUILD_BAD', f'{tag}: {why}')
            self.assertIn(expected, why, tag)

    def test_the_layout_is_checked_in_the_relinked_elf_and_not_only_in_the_records(self):
        """The record's ELF is right, so only a check of the ELF that the proof relinked can see a relink whose vectors moved: the doctored relink
        below leaves the proof's bytes alone and replaces the ELF that the layout check is then given."""
        bad, real_relink = tiny_elf(SCRATCH / 'layout', 'relinked-vectors', '', 1024), wb.relink_check

        def doctored(objects, tree, out, monitor):
            counts = real_relink(objects, tree, out, monitor)
            shutil.copy(bad, out / 'xnuboot-relocs.elf')
            return counts
        with mock.patch.object(wb, 'relink_check', doctored):
            name, why, _ = refused(*self.args())
        self.assertEqual(name, 'MONITOR_LAYOUT', why)
        self.assertIn("'xnuboot_vectors': 1024", why, 'the ELF the check was given is the doctored relink, not the record\'s')

    def test_a_profile_whose_gic_macros_differ_from_the_monitor_build(self):
        profile = json.loads((ROOT / 'config/monitor-profile-sp11-m1.json').read_text())
        profile['defines'] = [d.replace('SNAP_GICD_BASE=0x17000000', 'SNAP_GICD_BASE=0x17010000') for d in profile['defines']]
        T.write(SCRATCH / 'profile-other.json', json.dumps(profile).encode())
        name, why, _ = refused(*self.args(), '--profile', str(SCRATCH / 'profile-other.json'))
        self.assertEqual(name, 'GIC_MACROS_DIFFER', why)

    def test_a_monitor_that_is_empty_or_over_the_bound(self):
        for tag, size in (('empty', 0), ('big', wb.MONITOR_MAX + 1)):
            fake = SCRATCH / f'size-{tag}'
            fake.mkdir(exist_ok=True)
            monitor, elf = bytes(size), b'not an elf'      # consistent with its record, so only the size can refuse it
            T.write(fake / 'xnuboot.bin', monitor)
            T.write(fake / 'xnuboot.elf', elf)
            built = {'bytes': size, 'sha256': hashlib.sha256(monitor).hexdigest(), 'elf_sha256': hashlib.sha256(elf).hexdigest()}
            T.write(fake / 'profile-build.json', json.dumps({**STATE['record'], 'monitor': built}).encode())
            name, why, _ = refused(*self.args(monitor=fake / 'xnuboot.bin', record=fake / 'profile-build.json'))
            self.assertEqual(name, 'IMAGE_TOO_BIG', f'{tag}: {why}')

    def test_an_offset_that_is_not_a_page(self):
        for bad in ('0x20001', '0x800', '0', '-4096'):
            name, why, _ = refused(*self.args(), f'--monitor-offset={bad}')
            self.assertEqual(name, 'MONITOR_LAYOUT', f'{bad}: {why}')

    def test_a_wrapper_that_is_over_the_offset(self):
        name, why, _ = refused(*self.args(), '--monitor-offset', '0x1000')
        self.assertEqual(name, 'WRAPPER_TOO_BIG', why)

    def test_the_options_go_together(self):
        for argv in (['--out', str(SCRATCH / 'x'), '--monitor', 'a'], ['--out', str(SCRATCH / 'x'), '--monitor-build', 'a'],
                     [*self.args(), '--gic']):
            code, _, _ = quietly(wb.main, argv)
            self.assertEqual(code, 2, argv)


class GateOff(Base):
    """H4: the gate at 0 is the build that ran on the unit, and this work item's edits of the earlier files change nothing."""

    def test_the_binaries_that_ran_on_the_unit_rebuild_byte_for_byte(self):
        for name, (size, digest) in UNIT_RUN.items():
            with self.subTest(name):
                self.assertEqual(STATE['plain']['files'][name], {'bytes': size, 'sha256': digest})

    def snapshot(self, name, from_head):
        """A scratch copy of what the wrapper build reads (the tool, the profile, the stick sources), taken in one go, with the three earlier files
        that carry this item's gated hooks either as the working tree has them or as committed. The other agents' files are the same copy in both."""
        root = SCRATCH / name
        for rel in ('tools/sp11_stick_wrapper_build.py', 'config/monitor-profile-sp11-m1.json', *SHARED):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            if from_head:
                shown = subprocess.run(['git', '-C', str(ROOT), 'show', f'HEAD:{rel}'], capture_output=True)
                if shown.returncode:
                    self.skipTest(f'git cannot show HEAD:{rel}')
                text = shown.stdout
                if rel == 'probes/sp11-stick-reporter.c':
                    text = REPORTER_ORDER.sub(lambda _: REPORTER_ORDER.search((ROOT / rel).read_text()).group(0), text.decode()).encode()
                (root / rel).write_bytes(text)
            else:
                shutil.copy(ROOT / rel, root / rel)
        return root

    def test_the_gic_variant_is_not_changed_by_this_work_item(self):
        """Both pairs of binaries are built twice from snapshots of one moment: with the three earlier files as committed, and as the working tree
        has them. They must be equal byte for byte: whatever else changed in the GIC sources since (another work item's), it is in both."""
        if shutil.which('git') is None:
            self.skipTest('no git')
        roots = {tag: self.snapshot(tag, tag == 'head') for tag in ('head', 'work')}
        for path in (ROOT / 'probes').glob('sp11-stick-*'):
            if f'probes/{path.name}' not in SHARED and 'monitor' not in path.name:
                for root in roots.values():
                    shutil.copy(path, root / 'probes' / path.name)
        built = {}
        for tag, root in roots.items():
            spec = importlib.util.spec_from_file_location(f'wrapper_build_{tag}', root / 'tools/sp11_stick_wrapper_build.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            default = str(root / 'config/monitor-profile-sp11-m1.json')
            built[tag] = (module.build(str(root / 'build/plain'), default)['files'], module.build(str(root / 'build/gic'), default, False, True)['files'])
        self.assertEqual(built['head'][0], built['work'][0], 'the dry-run pair')
        self.assertEqual(built['head'][1], built['work'][1], 'the GIC pair')
        self.assertEqual(built['work'][0], STATE['plain']['files'])

    def test_the_identity_check_can_fail(self):
        """The same comparison with one un-gated change in the working copy (a letter of a panel line): the digest must differ."""
        root = self.snapshot('doctored', False)
        for path in (ROOT / 'probes').glob('sp11-stick-*'):
            if f'probes/{path.name}' not in SHARED and 'monitor' not in path.name:
                shutil.copy(path, root / 'probes' / path.name)
        wrapper = root / 'probes/sp11-stick-wrapper.c'
        text = wrapper.read_text()
        self.assertIn('pl_text(&l, "START");', text)
        wrapper.write_text(text.replace('pl_text(&l, "START");', 'pl_text(&l, "STARX");'))
        spec = importlib.util.spec_from_file_location('wrapper_build_doctored', root / 'tools/sp11_stick_wrapper_build.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        built = module.build(str(root / 'build/plain'), str(root / 'config/monitor-profile-sp11-m1.json'))['files']
        self.assertNotEqual(built['SNAPMON.BIN']['sha256'], UNIT_RUN['SNAPMON.BIN'][1])
        self.assertEqual(built['SNAPMON.BIN']['bytes'], UNIT_RUN['SNAPMON.BIN'][0], 'the same size: only the digest sees it')


class Census(Base):
    """The wrapper binary of the variant: what it writes, reads and executes among the system instructions."""

    def instructions(self):
        return [(name, addr, m, o) for name, ins in STATE['funcs'].items() for addr, m, o in ins]

    def test_the_only_system_register_it_writes_are_the_cpu_interface_class_c(self):
        writes = collections.Counter(o.split(',')[0].strip() for _, _, m, o in self.instructions() if m == 'msr')
        self.assertEqual(dict(writes), WRITES, 'no write of CNTHP, VBAR_EL2, HCR_EL2, DAIF or anything else: the monitor sets those itself')
        where = {n for n, _, m, o in self.instructions() if m == 'msr'}
        self.assertEqual(where, {'sp11hw_write_icc_pmr_el1', 'sp11hw_write_icc_bpr1_el1', 'sp11hw_write_icc_igrpen1_el1'})

    def test_the_reads(self):
        reads = {o.split(',')[-1].strip() for _, _, m, o in self.instructions() if m == 'mrs'}
        self.assertEqual(reads - READS_BEFORE, READS_NEW | READS_ARM, 'the new reads are the thirteen of the stage and the GIC arm\'s ICC_HPPIR1_EL1')
        self.assertFalse(reads - READS_BEFORE - READS_NEW - READS_ARM)

    def test_the_system_instructions(self):
        found, forbidden = set(), []
        for name, addr, m, o in self.instructions():
            if m in FORBIDDEN:
                forbidden.append(f'{name}+{addr:#x}: {m} {o}')
            if m in ('dc', 'at', 'ic', 'dsb', 'isb', 'dmb'):
                found.add((m, o.split(',')[0].split()[0] if o else ''))
        self.assertEqual(forbidden, [])
        self.assertEqual(found - SYSTEM, set(), 'a system instruction that is not on the list')
        self.assertIn(('ic', 'ialluis'), found)
        self.assertEqual([n for n, _, m, _ in self.instructions() if m == 'ic'], ['sp11hw_ic_ialluis'], 'IC IALLUIS is one instruction in one function')

    def test_the_encodings_by_their_bits_agree_with_the_listing(self):
        flat = (SCRATCH / 'm1' / 'wrapper-m1.bin').read_bytes()
        words = T.code_words(flat, T.sections(SCRATCH / 'm1' / 'snapmon-m1.elf')['.text'])
        msr = [w for w in words if (w & 0xFFF00000) == 0xD5100000]
        listed = [1 for _, _, m, _ in self.instructions() if m == 'msr']
        self.assertEqual(len(msr), len(listed), 'every MSR of the binary is one of the listing')
        self.assertEqual(sorted((w >> 16 & 7, w >> 12 & 15, w >> 8 & 15, w >> 5 & 7) for w in msr), sorted([(0, 4, 6, 0), (0, 12, 12, 3), (0, 12, 12, 7)]),
                         'ICC_PMR_EL1, ICC_BPR1_EL1 and ICC_IGRPEN1_EL1')
        self.assertFalse([w for w in words if w in (0xD69F03E0, 0xD4000001, 0xD4000002, 0xD4000003)], 'no ERET, SVC, HVC or SMC')

    def test_stores_are_the_stack_the_arguments_and_the_two_mmio_functions_which_the_stage_does_not_call(self):
        funcs = STATE['funcs']
        callees = {name.split('#')[0] for name in funcs} - {'payload_entry', '_start'}
        self.assertEqual(T.store_violations(funcs, through_arguments=callees), [])
        self.assertEqual(T.indirect_branch_violations(funcs), [])
        for name in ('sp11hw_mmio_write8', 'sp11hw_mmio_write32'):
            callers = T.callers_of(funcs, name)
            self.assertTrue(callers)
            stage = {c for c in callers if c.startswith('mon_') or c == 'sw_monitor_stage'}
            self.assertEqual(stage, set(), f'the monitor stage does not call {name}: only the GIC arm and put-back write GIC registers')

    def test_the_stage_calls_the_gic_arm_and_put_back_and_no_dry_run_function(self):
        funcs = STATE['funcs']
        self.assertTrue({'sw_gic_arm', 'sw_gic_put_back'} <= {n.split('#')[0] for n in funcs})
        self.assertFalse({'sw_gic_stage', 'sw_gic_window', 'sw_gic_excursion', 'sw_gic_vec_irq'} & {n.split('#')[0] for n in funcs})

    def test_the_reporter_is_read_only_and_under_the_cap(self):
        """No instruction that writes memory but a store to its own stack, no system register access, no cache, translation or barrier instruction."""
        funcs = T.parse_functions(T.disassemble(SCRATCH / 'm1' / 'snaprep-m1.elf'))
        callees = {name.split('#')[0] for name in funcs} - {'payload_entry', '_start'}
        self.assertEqual(T.store_violations(funcs, through_arguments=callees), [], 'the callees store through the line counter their caller passes: its own stack')
        found = [f'{n}+{a:#x}: {m} {o}' for n, ins in funcs.items() for a, m, o in ins if m in ('msr', 'mrs', 'dc', 'ic', 'at', 'tlbi', 'dsb', 'dmb', 'isb', 'sys', 'sysl')
                 or m in FORBIDDEN]
        self.assertEqual(found, [])
        self.assertLess((SCRATCH / 'm1' / 'SNAPREP-M1.BIN').stat().st_size, wb.REPORTER_MAX)


class StackCensus(Base):
    """H7: the monitor's call graph, in the build the wrapper calls (the test record): what a static count can and cannot say."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.funcs = T.parse_functions(T.disassemble(STATE['profile'] / 'xnuboot.elf'))
        cls.roots = ('xnuboot_setup', 'xnuboot_save', 'xnuboot_prepare', 'xnuboot_watchdog', 'xnuboot_report', 'xnuboot_async', 'xnuboot_pauth_resume',
                     'xnuboot_pauth_failed')

    def depths(self):
        found = {}
        for root in self.roots:
            try:
                found[root] = T.stack_depth(self.funcs, root)
            except AssertionError as error:
                found[root] = str(error)
        return found

    def test_the_depths_the_count_can_give_and_the_recursion_it_cannot(self):
        found = self.depths()
        numbers = {r: d for r, d in found.items() if isinstance(d, int)}
        self.assertTrue(numbers, found)
        self.assertLess(max(numbers.values()), 16 * 1024, f'the deepest root without recursion: {numbers}')
        with self.assertRaises(AssertionError) as caught:
            T.stack_depth(self.funcs, 'xnuboot_trap')
        self.assertIn('recursion through', str(caught.exception), 'the trap handler recurses: no static bound exists')

    def test_indirect_calls_make_every_figure_a_lower_bound(self):
        blr = sum(1 for ins in self.funcs.values() for _, m, _ in ins if m == 'blr')
        br = sum(1 for ins in self.funcs.values() for _, m, _ in ins if m == 'br')
        self.assertGreater(blr + br, 100, 'a call graph with this many indirect branches cannot be bounded by counting frames')

    def test_the_decision_is_the_a16_stack_not_the_runners(self):
        header = (ROOT / 'probes/sp11-stick-monitor.h').read_text()
        mine = int(re.search(r'#define SM_STACK_BYTES (0x[0-9A-Fa-f]+)u', header).group(1), 16)
        runner = int(re.search(r'Q1N1_PAYLOAD_STACK_BYTES \((\d+)u \* 1024u\)', (STATE['tree'] / 'platform/uefi/sp11-payload-abi.h').read_text()).group(1)) * 1024
        a16 = int(re.search(r'q1n1_stack:\s*\.space (\d+)', (STATE['tree'] / 'platform/uefi/entry.S').read_text()).group(1))
        self.assertEqual((mine, runner, a16), (256 * 1024, 64 * 1024, 256 * 1024), 'S2 gives the monitor the 256 KiB it has always had; the runner gives 64 KiB')


class Trampoline(Base):
    """Finding 6b of the review: sw_monitor_call is executed, not read. The REAL bytes of the function, taken from the wrapper of the M1 build, are put
    at their own offset in a flat image with a fake monitor at the monitor's offset and a boot stub (tools/test-sp11-stick-monitor-call.S), and run at
    EL2 under QEMU (`-M virt,virtualization=on -cpu max`, no firmware, the PL011 for the verdicts). The stub sets x0 to x4, x19, x29 and x30 to known
    values, calls with a stack in x5 and with x5 = 0, and compares what the fake monitor saw (x0 to x4, SP) and what came back (x0, SP, x19, x29, x30).
    Each of the faulty cases changes one word of the real bytes and must fail the check that matches it. What this does not show: the unit (here the
    MMU and the caches are off), the monitor's own code, or the function in any build but this one; QEMU's CPU model is not an Oryon."""

    BASE = 0x40200000                       # above the device tree QEMU puts at the start of its RAM; the stub's own stacks are above the image
    CHECKS = ('ARGS', 'STACK', 'ALIGN', 'RET', 'X19', 'X29', 'X30', 'SP')
    CASES = ('stack', 'nostack')
    ALL = set(itertools.product(CASES, CHECKS))
    NOP = 0xD503201F
    # name: (mnemonic and operands as the listing shows them, the word, the faulty word, the checks that must fail)
    MUTANTS = {
        'sp-from-the-wrong-register': (('mov', 'sp, x5'), 0x910000BF, 0x9100009F, {('stack', 'STACK'), ('stack', 'ALIGN')}),
        'a-zero-stack-is-not-skipped': (('cbz', 'x5, '), 0xB4000045, NOP, {('nostack', 'STACK')}),
        'x19-restored-from-the-wrong-slot': (('ldr', 'x19, [sp, #0x10]'), 0xF9400BF3, 0xF9400FF3, {('stack', 'X19'), ('nostack', 'X19')}),
        'x19-never-saved': (('str', 'x19, [sp, #0x10]'), 0xF9000BF3, NOP, {('stack', 'X19'), ('nostack', 'X19')}),
        'the-frame-is-popped-by-less': (('ldp', 'x29, x30, [sp], #0x20'), 0xA8C27BFD, 0xA8C17BFD, {('stack', 'SP'), ('nostack', 'SP')}),
        'an-argument-register-is-clobbered': (('mov', 'x29, sp'), 0x910003FD, 0x910003E1, {('stack', 'ARGS'), ('nostack', 'ARGS')}),
    }
    LOSES_X30 = (('ldp', 'x29, x30, [sp], #0x20'), 0xA8C27BFD, 0xA8C243FD)         # x30 is loaded into x16: ret goes back into the trampoline for good
    runs = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.qemu = T.find_tool('qemu-system-aarch64')
        if not cls.qemu:
            raise unittest.SkipTest('no qemu-system-aarch64: the trampoline is read, not executed')
        cls.listing = STATE['funcs']['sw_monitor_call']
        start = cls.listing[0][0]
        wrapper = (SCRATCH / 'm1' / 'wrapper-m1.bin').read_bytes()
        work = SCRATCH / 'call'
        function = T.write(work / 'sw_monitor_call.bin', wrapper[start:cls.listing[-1][0] + 4])
        T.clang(['--target=aarch64-unknown-none-elf', '-c', str(TOOLS / 'test-sp11-stick-monitor-call.S'), '-o', str(work / 'call.o'),
                 f'-DTRAMP_AT={start:#x}', f'-DMON_AT={STATE["m1"]["monitor"]["offset"]:#x}', f'-DTRAMP_FILE="{function}"'], 'assembling the boot stub')
        subprocess.run([T.find_tool('ld.lld'), f'-Ttext={cls.BASE:#x}', '-e', '_start', '--oformat=binary', '-o', str(work / 'call.bin'), str(work / 'call.o')],
                       check=True, capture_output=True)
        cls.image, cls.start = (work / 'call.bin').read_bytes(), start

    def patch(self, what, was, now):
        """The image with one word of sw_monitor_call changed: the word is found by its instruction in the listing and must be the one expected."""
        (mnemonic, operands), image = what, bytearray(self.image)
        at = next(a for a, m, o in self.listing if m == mnemonic and o.startswith(operands))
        self.assertEqual(int.from_bytes(image[at:at + 4], 'little'), was, f'{mnemonic} {operands} at {at:#x} is not the word this test expects: the table is stale')
        image[at:at + 4] = now.to_bytes(4, 'little')
        return bytes(image)

    def boot(self, image, timeout=60):
        """(the checks the stub printed {(case, name): passed}, whether it ran to its end, its output)."""
        Trampoline.runs += 1
        path = T.write(SCRATCH / 'call' / f'run-{Trampoline.runs}.bin', image)
        command = [self.qemu, '-M', 'virt,virtualization=on', '-cpu', 'max', '-m', '64', '-display', 'none', '-serial', 'stdio', '-monitor', 'none',
                   '-device', f'loader,file={path},addr={self.BASE:#x},cpu-num=0']
        try:
            done = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
            out, finished = done.stdout, done.returncode == 0
        except subprocess.TimeoutExpired as late:
            out, finished = (late.stdout or b'').decode('ascii', 'replace') if isinstance(late.stdout, bytes) else (late.stdout or ''), False
        checks = {(m.group(1), m.group(2)): m.group(3) == 'PASS' for m in re.finditer(r'^CALLTEST CHECK (\w+) (\w+) (PASS|FAIL)$', out, re.M)}
        return checks, finished and 'CALLTEST DONE' in out, out

    def failed(self, checks):
        return {k for k, v in checks.items() if not v}

    def test_the_real_bytes_pass_every_check_at_el2_with_a_stack_and_without(self):
        checks, finished, out = self.boot(self.image)
        self.assertIn('CALLTEST EL 0000000000000002', out, 'the stub runs at EL2: that is where the monitor is called')
        self.assertTrue(finished, out)
        self.assertEqual(set(checks), self.ALL, out)
        self.assertEqual(self.failed(checks), set(), out)

    def test_the_image_holds_the_functions_own_bytes_at_its_own_offset(self):
        """The bytes that ran are the wrapper's: the listing's words, at the offset the wrapper has them, ending in the bl to the monitor's offset."""
        wrapper = (SCRATCH / 'm1' / 'wrapper-m1.bin').read_bytes()
        end = self.listing[-1][0] + 4
        self.assertEqual(self.image[self.start:end], wrapper[self.start:end])
        bl = next(a for a, m, o in self.listing if m == 'bl')
        word = int.from_bytes(self.image[bl:bl + 4], 'little')
        self.assertEqual(word >> 26, 0b100101, 'a BL')
        self.assertEqual(bl + 4 * (word & 0x3FFFFFF), STATE['m1']['monitor']['offset'], 'a PC-relative branch to the offset the build puts the monitor at')

    def test_each_changed_word_fails_the_check_that_matches_it_and_no_other(self):
        for name, (what, was, now, failing) in self.MUTANTS.items():
            with self.subTest(name):
                checks, finished, out = self.boot(self.patch(what, was, now))
                self.assertTrue(finished, f'{name} must still run to its end: {out}')
                self.assertEqual(set(checks), self.ALL, out)
                self.assertEqual(self.failed(checks), failing, out)

    def test_a_trampoline_that_loses_x30_never_comes_back(self):
        what, was, now = self.LOSES_X30
        checks, finished, out = self.boot(self.patch(what, was, now), timeout=6)
        self.assertFalse(finished, 'the ret goes back into the trampoline: the stub never prints its end')
        self.assertEqual(checks, {}, out)


class Hygiene(unittest.TestCase):
    def test_the_refusal_names_this_variant_adds_are_the_tool_s_and_the_note_lists_them(self):
        """They live apart from wb.REFUSALS, which docs/SP11-STICK-WRAPPER.md lists name for name and has no room for these."""
        note = (ROOT / 'docs/SP11-STICK-MONITOR.md').read_text()
        self.assertFalse(set(wb.REFUSALS) & set(wb.MONITOR_REFUSALS))
        self.assertEqual(len(wb.MONITOR_REFUSALS), len(set(wb.MONITOR_REFUSALS)))
        for name in wb.MONITOR_REFUSALS:
            self.assertIn(name, note)

    def test_what_the_position_proof_cannot_show_is_written_in_the_tool_and_in_the_note(self):
        note = (ROOT / 'docs/SP11-STICK-MONITOR.md').read_text()
        for where, text in (('the note', note), ('the tool', wb.__doc__)):
            flat = ' '.join(text.split())
            for needle in ('an address dependence that is not a relocation', 'an address built from integers', '.init_array', 'TLS', 'self-attesting'):
                self.assertIn(needle, flat, f'{where}: {needle}')

    def test_the_new_files_are_ascii_with_an_spdx_line_within_the_size_limits_and_hold_no_machine_data(self):
        for rel in OWN:
            path = ROOT / rel
            if not path.exists():
                continue
            data = path.read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                if not rel.endswith('.md'):
                    self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                    self.assertLess(len(data), 50 * 1024)
                    self.assertLess(data.count(b'\n'), 1500)
                else:
                    self.assertLessEqual(len(data), 91000)       # raised from 88,000 on purpose for draft 3 of the record (84,300 before draft 2, 78,100 before E part 4, 75,500 before E part 3, 70,000 before E part 2, 60,000 before E, 46,000 before D, 36,000 before C); nothing was cut
                for needle in (b'/Us' + b'ers/', b'/Vol' + b'umes/', b'/t' + b'mp/', b'/var/fol' + b'ders'):
                    self.assertNotIn(needle, data)


if __name__ == '__main__':
    unittest.main()
