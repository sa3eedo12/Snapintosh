#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""End-to-end test of the SP11 stick wrapper (SNAPMON.BIN) and reporter (SNAPREP.BIN) under QEMU's EDK2 shell, at a real EL2
(docs/SP11-STICK-WRAPPER.md section 10).

    python3 tools/test-sp11-stick-wrapper-qemu.py

The REAL runner (q1n1's Q1N1-PAYLOAD.EFI, built with the PAYLOAD=1 recipe of its Makefile), the REAL wrapper and reporter (built by
tools/sp11_stick_wrapper_build.py) and a small synthetic bundle (tools/sp11_stick_bundle_build.py synthetic) are laid out as the stick
is laid out and booted with q1n1's own harness (tools/test-q1n1-sp11-payload-qemu.py, imported, not copied) under the pinned UEFI Shell
(SP11_UEFI_SHELL). One extra boot runs a small diagnostic payload instead, to read what the bundle must be pinned to: the CPU's MIDR and
MPIDR, CNTFRQ_EL0, the framebuffer geometry the runner passes, and the conventional descriptors of the final memory map; the PIN.DAT
facts and the placement of every bundle are those, and every result is read from the panel text the harness captures.

Cases: (1) the happy path to the stage DONE and the reporter's page; (2) one damaged byte in a GUEST.DAT chunk, with the runner's manifest
rebuilt over the damaged file so that the runner's own hash passes: the wrapper refuses CHUNK_DIGEST at EL2; (3) a wrong MIDR fact: the
OBSERVED check refuses MIDR_MISMATCH and names what the CPU said; (4) a placement over memory the runner allocated for its declared ranges:
PLACE_NOT_CONV, not PLACE_DECLARED. The interface checks the map first, and a declared range is memory the runner allocated, so the final
map never calls it conventional: PLACE_DECLARED is the second line of defence and only the host harness reaches it.

What QEMU cannot show: Oryon facts (MIDR, MPIDR, DminLine, caches), the unit's memory attributes and the real EL2 translation regime,
EL3 and the firmware's behaviour after the Secure-Launch bounce, and real throughput (TCG speed says nothing). It does show the real runner,
a real ExitBootServices, the wrapper's arguments and ops, a real EL2 with AT, DC CVAC/CIVAC and the register reads, and the panel text.
The command line is the harness's with two options changed (-cpu, -m): see CPU and MEMORY below. Evidence: EXPERIMENTAL (QEMU only);
hardware NOT RUN. SKIPs, never passes, without clang, lld-link, ld.lld, make, QEMU, an EDK2 image, the pinned shell or a q1n1 checkout.
Writes only below build/sp11-wrapper-qemu/, deleted at the end (SP11_WRAPPER_QEMU_KEEP=1 keeps it)."""
import concurrent.futures
import importlib.util
import json
import os
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

sb, E, MIB = T.sb, T.E, T.MIB
ROOT = T.ROOT
BUILD = ROOT / 'build' / 'sp11-wrapper-qemu'
# The harness runs QEMU with -cpu max and 512 MiB. The wrapper's EL2 rule (TCR_EL2.T0SZ 17 to 24, the interface's) refuses the T0SZ 16
# that EDK2 sets for -cpu max (OBSERVED in the q1n1 logs), and the smallest placement the interface allows is 480 MiB.
CPU = os.environ.get('SP11_WRAPPER_QEMU_CPU', 'cortex-a76')
MEMORY = os.environ.get('SP11_WRAPPER_QEMU_MEMORY', '2G')
RAM_BASE, RAM_BYTES = 0x40000000, 2048 * MIB
GUEST_MIB, PLACE_BYTES = 192, (192 + 48 + 240) * MIB
FILES = ('SNAPMON.BIN', 'SNAPREP.BIN', 'GUEST.DAT', 'PIN.DAT', 'REPORT.DAT')
TOOLS = ('clang', 'lld-link', 'ld.lld', 'make', 'qemu-system-aarch64')
DONE = 'PAYLOAD DONE INDEX=1 '

MAPDUMP = r'''/* SPDX-License-Identifier: MIT: a diagnostic payload of the QEMU test of the SP11 stick wrapper. Read only. */
#include "payload-util.h"

#define SYSREG(name) ({ uint64_t value_; __asm__ volatile("mrs %0, " #name : "=r"(value_)); value_; })

static void put3(const struct q1n1_payload_args *a, const char *head, const char *k1, uint64_t v1, const char *k2, uint64_t v2,
                 const char *k3, uint64_t v3)
{
    struct pl_line l;
    pl_begin(&l);
    pl_text(&l, head);
    pl_text(&l, k1);
    pl_hex(a, &l, v1);
    pl_text(&l, k2);
    pl_hex(a, &l, v2);
    pl_text(&l, k3);
    pl_hex(a, &l, v3);
    pl_emit(a, &l);
}

uint64_t payload_entry(const struct q1n1_payload_args *a);
uint64_t payload_entry(const struct q1n1_payload_args *a)
{
    const uint8_t *map;
    uint64_t i, n, shown = 0;
    if (!pl_args_ok(a) || !a->map_base || a->map_stride < 40 || !a->map_size) return Q1N1_PAYLOAD_RET_BAD_ARGS;
    put3(a, "MAPDUMP REGS", " MIDR=", SYSREG(midr_el1), " MPIDR=", SYSREG(mpidr_el1), " CNTFRQ=", SYSREG(cntfrq_el0));
    put3(a, "MAPDUMP EL2", " HCR=", SYSREG(hcr_el2), " SCTLR=", SYSREG(sctlr_el2), " TCR=", SYSREG(tcr_el2));
    put3(a, "MAPDUMP FB", " BASE=", a->fb_base, " BYTES=", a->fb_bytes, " WIDTH=", a->fb_width);
    put3(a, "MAPDUMP FBMORE", " HEIGHT=", a->fb_height, " STRIDE=", a->fb_stride, " FORMAT=", a->fb_format);
    map = (const uint8_t *)(uintptr_t)a->map_base;
    n = a->map_size / a->map_stride;
    put3(a, "MAPDUMP MAP", " COUNT=", n, " STRIDE=", a->map_stride, " BASE=", a->map_base);
    for (i = 0; i < n && shown < 30; i++) {
        const uint8_t *d = map + i * a->map_stride;
        uint32_t type;
        uint64_t start, pages, attribute;
        __builtin_memcpy(&type, d, 4);
        __builtin_memcpy(&start, d + 8, 8);
        __builtin_memcpy(&pages, d + 24, 8);
        __builtin_memcpy(&attribute, d + 32, 8);
        if (type == 7) {
            put3(a, "MAPDUMP CONV", " START=", start, " PAGES=", pages, " ATTR=", attribute);
            shown++;
        }
    }
    put3(a, "MAPDUMP END", " SHOWN=", shown, " TYPE=", 7, " DONE=", 1);
    return 0;
}
'''


def load_q1n1():
    """(tree, q1n1's payload test library, q1n1's QEMU payload test module), or a SKIP."""
    tree = T.q1n1_tree()
    if not tree:
        raise unittest.SkipTest('no q1n1 checkout (set Q1N1_SP11_TREE)')
    base = tree / 'tools' / 'test-q1n1-sp11-payload-qemu.py'
    if not base.is_file() or not (tree / 'tools' / 'sp11_payload_testlib.py').is_file():
        raise unittest.SkipTest('the q1n1 checkout has no QEMU payload harness (tools/test-q1n1-sp11-payload-qemu.py)')
    sys.path.insert(0, str(tree / 'tools'))
    lib = importlib.import_module('sp11_payload_testlib')
    spec = importlib.util.spec_from_file_location('sp11_payload_qemu_base', base)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tree, lib, module


class QemuCommand:
    """What the harness's run_boot sees as `subprocess`: the module itself, except that Popen changes the -cpu and -m options of the
    harness's command line (the only two this test needs different)."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    @staticmethod
    def Popen(cmd, *args, **kwargs):
        cmd = list(cmd)
        for option, value in (('-cpu', CPU), ('-m', MEMORY)):
            cmd[cmd.index(option) + 1] = value
        return subprocess.Popen(cmd, *args, **kwargs)


def hexes(text, head):
    """{KEY: int} of the line `head KEY=0X.. KEY=0X..` the diagnostic payload printed."""
    found = re.search(rf'^{head}((?: \w+=0X[0-9A-F]+)+)$', text, re.M)
    assert found, f'no {head} line in the first boot'
    return {key: int(value, 16) for key, value in re.findall(r' (\w+)=0X([0-9A-F]+)', found.group(1))}


def align_up(value, grid):
    return (value + grid - 1) // grid * grid


class Facts:
    """What the first boot read, as the facts and the dump that a bundle for this QEMU is built from."""

    def __init__(self, text):
        regs, el2, fb, more = (hexes(text, f'MAPDUMP {head}') for head in ('REGS', 'EL2', 'FB', 'FBMORE'))
        self.midr, self.mpidr, self.cntfrq = regs['MIDR'], regs['MPIDR'], regs['CNTFRQ']
        self.hcr, self.sctlr, self.tcr = el2['HCR'], el2['SCTLR'], el2['TCR']
        self.fb = dict(base=fb['BASE'], bytes=fb['BYTES'], width=fb['WIDTH'], height=more['HEIGHT'], stride=more['STRIDE'], format=more['FORMAT'])
        found = (re.fullmatch(r'MAPDUMP CONV START=0X([0-9A-F]+) PAGES=0X([0-9A-F]+) ATTR=0X([0-9A-F]+)', line) for line in text.split('\n'))
        self.conventional = [tuple(int(group, 16) for group in m.groups()) for m in found if m]


class WrapperQemu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree, cls.lib, cls.qemu = load_q1n1()
        for tool in TOOLS:
            if not cls.lib.have(tool):
                raise unittest.SkipTest(f'{tool} is not installed')
        cls.firmware = cls.qemu.helpers.find_firmware()
        if not cls.firmware:
            raise unittest.SkipTest('no EDK2 aarch64 firmware (set Q1N1_EDK2_CODE)')
        if not cls.qemu.PINNED_SHELL or not Path(cls.qemu.PINNED_SHELL).is_file():
            raise unittest.SkipTest('the pinned UEFI Shell is absent (set SP11_UEFI_SHELL)')
        shutil.rmtree(BUILD, ignore_errors=True)
        BUILD.mkdir(parents=True)
        cls.qemu.BUILD, cls.qemu.subprocess = BUILD, QemuCommand()
        try:
            cls.runner = cls.build_runner()
            cls.units = cls.build_units()
            cls.facts = cls.first_boot()
            cls.bundles = cls.build_bundles()
            cls.runs = cls.boot_cases()
        except BaseException:
            cls.tearDownClass()     # unittest does not call it when setUpClass raises
            raise

    @classmethod
    def tearDownClass(cls):
        if not os.environ.get('SP11_WRAPPER_QEMU_KEEP'):
            shutil.rmtree(BUILD, ignore_errors=True)

    # -- the inputs ----------------------------------------------------------------------------------------------------------
    @classmethod
    def build_runner(cls):
        out = BUILD / 'runner'
        done = subprocess.run(['make', '-f', 'platform/uefi/Makefile', f'OUT={out}', '-j4', 'PAYLOAD=1', *cls.qemu.SP11_VARS, 'payload-runner'],
                              cwd=cls.tree, env=cls.lib.ENV, capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError('the PAYLOAD=1 runner did not build:\n' + done.stdout[-1500:] + done.stderr[-1500:])
        return out / 'q1n1-payload.efi'

    @classmethod
    def build_units(cls):
        profile = T.write(BUILD / 'profile.json', json.dumps({'defines': [f'-D{k}={v}' for k, v in T.GIC.items()]}).encode())
        with mock.patch.dict(os.environ, {'Q1N1_SP11_TREE': str(cls.tree)}):
            wb.build(str(BUILD / 'units'), profile)
        source = T.write(BUILD / 'mapdump' / 'mapdump.c', MAPDUMP.encode())
        done, _, flat = cls.lib.build_payload(source, BUILD / 'mapdump', name='mapdump', root=cls.tree)
        if done.returncode:
            raise RuntimeError('the diagnostic payload did not build:\n' + done.stderr[-1500:])
        return {name: (BUILD / 'units' / name).read_bytes() for name in wb.NAMES} | {'MAPDUMP.BIN': flat.read_bytes()}

    @classmethod
    def stick(cls, entries, tamper=None):
        """The files of a stick: [(name, bytes)] in manifest order, with the runner's manifest made over exactly these bytes, except that
        a file in `tamper` is laid out with other bytes than the manifest hashed (what the runner refuses before the bounce)."""
        files = {'SP11/PAYLOAD.MAN': cls.qemu.manifest([(f'\\SP11\\{name}', data) for name, data in entries])}
        files.update({f'SP11/{name}': (tamper or {}).get(name, data) for name, data in entries})
        return files

    @classmethod
    def first_boot(cls):
        """The facts of this QEMU: one boot of the real runner with the diagnostic payload."""
        boot = cls.qemu.Boot('first', cls.stick([('MAPDUMP.BIN', cls.units['MAPDUMP.BIN'])]), cls.qemu.script(), marker='MAPDUMP END', pinned=True)
        cls.qemu.run_boot(boot, cls.runner, cls.firmware)
        if 'MAPDUMP END' not in boot.text:
            raise RuntimeError(f'the first boot did not reach the diagnostic payload; see {BUILD / "first.log"}')
        facts = Facts(boot.text)
        if not facts.conventional:
            raise RuntimeError('the first boot printed no conventional descriptor')
        return facts

    # -- the bundles -----------------------------------------------------------------------------------------------------------
    @classmethod
    def bundle(cls, name, base, midr=None):
        """The bundle for this QEMU: the facts it read (or a MIDR that is not the CPU's), the placement at `base`, the real wrapper and reporter."""
        f, root = cls.facts, BUILD / 'bundles' / name
        tables = T.tables_for(T.load('hamoa_bootcontract_tests', T.TOOLS / 'test-hamoa-bootcontract.py'), json.loads(T.EXAMPLE.read_text()) | dict(
            gicd_base=int(T.GIC['SNAP_GICD_BASE'], 16), gicr_first=int(T.GIC['SNAP_GICR_FIRST'], 16), gicr_limit=int(T.GIC['SNAP_GICR_LIMIT'], 16),
            its_base=0x17040000, timer_intid=int(T.GIC['SNAP_TIMER_INTID'])))
        descs = [(7, s, p, a) for s, p, a in f.conventional] + [(0, f.fb['base'], T.page_up(f.fb['bytes']) // 4096, T.ATTR)]
        dump = root / 'dump'
        for sig, data in tables.items():
            T.write(dump / f'{sig}.bin', data)
        T.write(dump / 'memmap.raw', T.efi_map(sorted(descs, key=lambda d: d[1])))
        gop = [f'gop_width={f.fb["width"]}', f'gop_height={f.fb["height"]}', f'gop_stride_pixels={f.fb["stride"]}', f'gop_format={f.fb["format"]:#x}',
               f'gop_base={f.fb["base"]:#018x}', f'gop_size={f.fb["bytes"]:#018x}']
        T.write(dump / 'summary.txt', ('# QEMU dump\n' + '\n'.join([f'memmap_count={len(descs)}', 'memmap_descriptor_size=48', *gop]) + '\n').encode())
        acpi = [dict(sig=s, length=len(t), sha256=T.sha(t)) for s, t in sorted(tables.items())]
        facts = json.loads(T.EXAMPLE.read_text()) | dict(midr=[hex(midr if midr is not None else f.midr)], boot_mpidr=hex(f.mpidr), cntfrq_hz=f.cntfrq,
                                                        gop={k: hex(v) if k in ('base', 'bytes') else v for k, v in f.fb.items()}, acpi=acpi,
                                                        build_label='qemu-synthetic')
        facts_path = T.write(root / 'facts.json', json.dumps(facts).encode())
        opts = ['--facts', facts_path, '--base', hex(base), '--guest-mib', str(GUEST_MIB), '--monitor', str(BUILD / 'units' / 'SNAPMON.BIN'),
                '--reporter', str(BUILD / 'units' / 'SNAPREP.BIN'), '--stores', 'ram,el2_sysreg', '--time-box-s', '5', '--image-mib', '8', '--tables-mib', '1',
                '--sptm-data-mib', '4', '--dump', str(dump)]
        code, text, err = T.run_builder(['authorisation-template', 'synthetic', *opts])
        assert code == 0, (text, err)
        record = T.write(root / 'authorisation.txt', text.encode())
        code, text, err = T.run_builder(['synthetic', *opts, '--no-manifest', '--out', str(root / 'out'), '--authorisation', record])
        assert code == 0, (text, err)
        return T.Bundle(root / 'out' / 'bundle' / 'SP11')

    @classmethod
    def build_bundles(cls):
        """The bundles of the cases, from the facts of the first boot. The placement is at the foot of the largest conventional extent of
        the final map, with a margin. The fourth case has no bundle of its own: its PIN.DAT is the first's with the placement moved to the
        top of RAM and the digest recomputed, because the builder rightly refuses a placement over the framebuffer."""
        start, pages, _ = max(cls.facts.conventional, key=lambda d: d[1])
        low = align_up(start + 16 * MIB, 32 * MIB)
        if low + PLACE_BYTES + 64 * MIB > start + pages * 4096:
            raise unittest.SkipTest(f'the largest conventional extent of this QEMU ({pages * 4096 // MIB} MiB) cannot hold the 480 MiB placement')
        cls.low, cls.high = low, RAM_BASE + RAM_BYTES - PLACE_BYTES
        return dict(happy=cls.bundle('happy', low), badmidr=cls.bundle('badmidr', low, midr=0x410FD0C0))

    @classmethod
    def boot_cases(cls):
        good, k = cls.bundles['happy'], cls.bundles['happy'].data_index()
        damaged = bytearray(good.guest)
        damaged[good.chunks[k]['file_offset'] + 3] ^= 0x10
        top_pin = good.repacked_pin(**sb.placement(cls.high, GUEST_MIB * MIB))

        def entries(bundle, **replace):
            return [(name, replace.get(name, (bundle.dir / name).read_bytes())) for name in FILES]
        cls.damaged_chunk = k
        cases = dict(happy=entries(good), damaged=entries(good, **{'GUEST.DAT': bytes(damaged)}), midr=entries(cls.bundles['badmidr']),
                     top=entries(good, **{'PIN.DAT': top_pin}))
        boots = [cls.qemu.Boot(name, cls.stick(files), cls.qemu.script(), marker=DONE, pinned=True, timeout=400) for name, files in cases.items()]
        # The control: the same damaged byte with the manifest of the undamaged file, which the runner refuses before the bounce.
        boots.append(cls.qemu.Boot('control', cls.stick(entries(good), tamper={'GUEST.DAT': bytes(damaged)}), cls.qemu.script(), pinned=True, timeout=400))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return {b.name: b for b in pool.map(lambda b: cls.qemu.run_boot(b, cls.runner, cls.firmware), boots)}

    # -- what a boot printed ---------------------------------------------------------------------------------------------------
    def lines(self, name, prefix):
        """The panel lines of one payload (SNAPMON or SNAPREP) in the order printed."""
        return [line for line in self.runs[name].text.split('\n') if line.startswith(prefix + ' ')]

    def after_bounce(self, name):
        text = self.runs[name].text
        self.assertIn('EL2 CONFIRMED AFTER EXITBOOTSERVICES', text, f'{name}: the runner never reached EL2; see {BUILD / (name + ".log")}')
        return text.split('EL2 CONFIRMED AFTER EXITBOOTSERVICES', 1)[1]

    def done_values(self, name):
        return [int(v, 16) for v in re.findall(r'PAYLOAD DONE INDEX=\d+ RETURN=0X([0-9A-F]+)', self.after_bounce(name))]

    def assert_runner_ran_both(self, name):
        after = self.after_bounce(name)
        self.assertEqual(len(re.findall(r'PAYLOAD VERIFY=OK', after)), 2, f'{name}: the runner did not verify and call both payloads')
        self.assertNotIn('PAYLOAD REFUSED', self.runs[name].text, name)
        self.assertNotIn('SYNCHRONOUS EXCEPTION', self.runs[name].text, name)
        self.assertFalse(self.runs[name].changed, f'{name}: a file of the stick changed')

    def refusal(self, name, code, stage, detail):
        """The wrapper's refusal line and the reporter's line for it, and the wrapper's return value in the runner's DONE line."""
        self.assert_runner_ran_both(name)
        mon, rep = self.lines(name, 'SNAPMON'), self.lines(name, 'SNAPREP')
        want = f'REFUSED={code} STAGE={stage} DETAIL=0X{detail:X}'
        self.assertIn(f'SNAPMON {want}', mon, mon)
        self.assertEqual(rep[0], f'SNAPREP {want}', rep)
        self.assertEqual(self.done_values(name), [sb.RET_REFUSED + getattr(E, code), 0])
        self.assertTrue(all(line == line.upper() for line in mon + rep))
        return mon, rep

    # -- the cases ---------------------------------------------------------------------------------------------------------------
    def test_the_first_boot_read_a_cpu_the_wrapper_accepts_and_a_map_with_room_for_the_placement(self):
        f = self.facts
        self.assertEqual((f.sctlr & 1, (f.tcr >> 14) & 3, 17 <= (f.tcr & 63) <= 24, (f.hcr >> 34) & 1), (1, 0, True, 0), 'the EL2 regime the wrapper accepts')
        start, pages, attr = max(f.conventional, key=lambda d: d[1])
        self.assertTrue(attr & 8, 'write-back')
        self.assertGreaterEqual(pages * 4096, PLACE_BYTES + 80 * MIB)

    def test_the_happy_path_reaches_done_and_the_reporter_reads_the_page(self):
        self.assert_runner_ran_both('happy')
        mon, rep = self.lines('happy', 'SNAPMON'), self.lines('happy', 'SNAPREP')
        b, f = self.bundles['happy'], self.facts
        self.assertEqual([re.match(r'SNAPMON STAGE=(\w+)', line).group(1) for line in mon if line.startswith('SNAPMON STAGE=')],
                         ['ENTERED', 'ARGS_OK', 'FILES_OK', 'PIN_OK', 'OBSERVED_OK', 'PLACED_OK', 'GUEST_OK', 'COPYING', 'COPIED', 'DONE'], mon)
        self.assertIn('SNAPMON START VERSION=1 EL=0X8 POLICY=0X3', mon)
        self.assertIn(f'SNAPMON REGS MIDR=0X{f.midr:X} MPIDR=0X{f.mpidr:X} CNTFRQ={f.cntfrq}', mon)
        self.assertIn(f'SNAPMON GOP BASE=0X{f.fb["base"]:X} BYTES=0X{f.fb["bytes"]:X} W={f.fb["width"]} H={f.fb["height"]} STRIDE={f.fb["stride"]} FMT={f.fb["format"]}', mon)
        data = [c for c in b.chunks if not c['flags'] & sb.CHUNK_ZERO]
        self.assertIn(f'SNAPMON STAGE=COPIED COPIED={len(b.chunks)} BYTES={sum(c["size"] for c in b.chunks)} HASHED={len(data)} '
                      f'HASHED_BYTES={sum(c["size"] for c in data)}', mon)
        probes = len(T.probe_addresses(self.low, PLACE_BYTES))
        place_line = next(line for line in mon if line.startswith('SNAPMON PLACE '))
        place = re.fullmatch(r'SNAPMON PLACE BASE=0X([0-9A-F]+) SIZE=0X([0-9A-F]+) DESC=(\d+) DBASE=0X([0-9A-F]+) DPAGES=0X([0-9A-F]+)', place_line)
        self.assertTrue(place, place_line)
        base, size, dbase, dpages = (int(place.group(i), 16) for i in (1, 2, 4, 5))
        self.assertEqual((base, size), (self.low, PLACE_BYTES))
        self.assertTrue(dbase <= base and base + size <= dbase + dpages * 4096, 'the descriptor the wrapper names holds the placement')
        self.assertTrue(any(re.fullmatch(rf'SNAPMON AT PLACEMENT PROBES={probes} ATTR=\w+ SH=\w+', line) for line in mon), mon)
        self.assertEqual(mon[-2:], ['SNAPMON STAGE=DONE RETURN=0', 'SNAPMON MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD'])
        self.assertEqual(self.done_values('happy'), [0, 0])
        self.assertEqual(rep[0], 'SNAPREP DRY RUN COMPLETE STAGE=DONE: THE MONITOR WAS NOT CALLED')
        self.assertEqual(rep[1], f'SNAPREP VERDICTS PIN=OK GUEST=OK POLICY=0X3 LINES={len(mon)}')
        self.assertEqual(rep[2], f'SNAPREP REGS EL=0X8 MIDR=0X{f.midr:X} MPIDR=0X{f.mpidr:X} CNTFRQ={f.cntfrq}')
        self.assertIn(f'SNAPREP CHUNKS TOTAL={len(b.chunks)} COPIED={len(b.chunks)} HASHED={len(data)} BYTES_COPIED={sum(c["size"] for c in b.chunks)} '
                      f'BYTES_HASHED={sum(c["size"] for c in data)}', rep)
        self.assertIn(f'SNAPREP GUEST.DAT BASE=', ' '.join(rep))

    def test_one_damaged_guest_chunk_that_the_runner_accepts_is_refused_by_the_wrapper_at_el2(self):
        mon, rep = self.refusal('damaged', 'CHUNK_DIGEST', 'PLACED_OK', self.damaged_chunk)
        self.assertIn('SNAPMON RAM UNTOUCHED BEYOND REPORT.DAT', mon)
        self.assertNotIn('SNAPMON STAGE=COPYING', mon)
        self.assertEqual(rep[-1], 'SNAPREP RAM UNTOUCHED BEYOND REPORT.DAT')

    def test_the_same_damaged_byte_with_the_undamaged_manifest_is_refused_by_the_runner_before_the_bounce_and_nothing_is_called(self):
        text = self.runs['control'].text
        self.assertRegex(text, r'PAYLOAD REFUSED=HASH INDEX=2 ')
        self.assertIn('Q1N1_SHELL_RETURN_OK', text)
        for absent in ('Leaving firmware now', 'EL2 CONFIRMED', 'PAYLOAD CALL', 'SNAPMON ', 'SNAPREP '):
            self.assertNotIn(absent, text)

    def test_a_wrong_midr_fact_is_refused_by_the_observed_check_with_what_the_cpu_said(self):
        mon, rep = self.refusal('midr', 'MIDR_MISMATCH', 'PIN_OK', self.facts.midr)
        self.assertIn(f'SNAPMON REGS MIDR=0X{self.facts.midr:X} MPIDR=0X{self.facts.mpidr:X} CNTFRQ={self.facts.cntfrq}', mon)
        self.assertNotIn('SNAPMON STAGE=OBSERVED_OK', mon)

    def test_a_placement_over_what_the_runner_allocated_is_refused_as_not_conventional_not_as_declared(self):
        mon, rep = self.refusal('top', 'PLACE_NOT_CONV', 'OBSERVED_OK', self.high)
        self.assertNotIn('SNAPMON STAGE=PLACED_OK', mon)
        self.assertNotIn('PLACE_DECLARED', ' '.join(mon + rep))


if __name__ == '__main__':
    unittest.main()
