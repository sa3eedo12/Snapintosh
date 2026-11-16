#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""End-to-end test of the SP11 GIC and stop stage (SNAPMON-GIC.BIN and SNAPREP-GIC.BIN) under QEMU's EDK2 shell, at a real EL2
(docs/SP11-STICK-GIC.md section 9).

    python3 tools/test-sp11-stick-gic-qemu.py

The REAL runner (q1n1's Q1N1-PAYLOAD.EFI), the REAL GIC and stop wrapper and reporter (tools/sp11_stick_wrapper_build.py --gic, with QEMU's
GIC bases as the five macros that PIN.DAT is compared with) and a small synthetic bundle (tools/sp11_stick_bundle_build.py synthetic --gic-stop,
whose facts are those of this QEMU: MIDR, MPIDR, CNTFRQ, the framebuffer, a GICv3 with one redistributor frame at 0x080A0000) are laid out as the
stick is laid out and booted with the machinery of tools/test-sp11-stick-wrapper-qemu.py (imported, not copied), which in turn imports q1n1's
own harness. Cases: (1) the happy path: the wrapper reads the GIC, announces its first store and waits eight seconds, stores, arms the EL2
physical timer, unmasks IRQ in window 1 and takes PPI 26 four times at EL2 through the wrapper's own vector table (three acknowledged cycles,
each re-armed 10 ms ahead, then a deadline taken with no acknowledge), announces the excursion and waits eight more seconds, writes VBAR_EL1,
SCTLR_EL1 and HCR_EL2, ERETs to the stub at EL1 and takes PPI 26 from EL1 at the lower-EL vector (+0x480), puts everything back and reports
IRQ_OK; the reporter prints the record, with the survey lines and the quarantine's (pass 5: QEMU's firmware leaves ISENABLER0 clear, so the quarantine
has nothing to disable here and only the host model exercises its writes; the check of ICC_HPPIR1_EL1 before the window is read as 1023 for real); (2) a
redistributor the firmware left asleep is refused GICR_ASLEEP; (3) a bundle that does not
authorise the gic class is refused STORE_NOT_AUTH by the same wrapper.

What this proves: the SEQUENCE and the assembly at a real EL2 under QEMU's GICv3 model: the exception entry, the acknowledge, the end of
interrupt, the ERET, the entry to EL1 and the exception back from it, and the restore of VBAR_EL2, HCR_EL2, VBAR_EL1 and SCTLR_EL1. What it does
NOT prove: anything about the unit. QEMU's GIC has one security state, no EL3 and no firmware between the timer and the CPU, and QEMU does not
model the caches the unit's EL1 fetch depends on; the unit's GICD_CTLR reads 0x10 (two security states), and whether PPI 26 arrives at EL2
there, from EL1 or not, is U3, UNKNOWN until the unit runs. Each window is up to four seconds and the two waits eight, in real time.
Evidence: EXPERIMENTAL (QEMU only); hardware NOT RUN. SKIPs, never passes, without clang, lld-link, ld.lld, make, QEMU, an EDK2 image, the pinned
shell or a q1n1 checkout. Writes only below build/sp11-gic-qemu/, deleted at the end (SP11_WRAPPER_QEMU_KEEP=1 keeps it)."""
import concurrent.futures
import json
import os
import re
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_build as wb  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

base = T.load('sp11_wrapper_qemu_base', T.TOOLS / 'test-sp11-stick-wrapper-qemu.py')
BUILD = T.ROOT / 'build' / 'sp11-gic-qemu'
base.BUILD = BUILD
sb, E, MIB = T.sb, T.E, T.MIB
# QEMU's virt machine with gic-version=3 and one CPU: the distributor, then one redistributor frame (two 64 KiB pages), the ITS in between.
QEMU_GIC = dict(SNAP_GICD_BASE='0x08000000', SNAP_GICR_FIRST='0x080A0000', SNAP_GICR_LIMIT='0x080C0000', SNAP_GICR_STRIDE='0x20000', SNAP_TIMER_INTID='26')
ITS_BASE = 0x08080000


def qemu_tables(HB, f):
    """The ACPI tables of a QEMU dump: a GICv3 with one CPU and one redistributor frame, an ITS, and the timer GTDT of the unit's builder."""
    gtdt = struct.pack('<QIIIIIIIIIQII', (1 << 64) - 1, 0, 29, 4, 30, 4, 27, 4, f['timer_intid'], 4, (1 << 64) - 1, 0, 0)
    entries = [HB.gicd(f['gicd_base'], 3), HB.gicc(0, f['boot_mpidr'] & 0xFF00FFFFFF, gicr=0), HB.gicr(f['gicr_first'], f['gicr_limit'] - f['gicr_first']),
               HB.its(f['its_base'])]
    return {'APIC': HB.madt(entries), 'GTDT': HB.sdt(b'GTDT', gtdt), 'MCFG': HB.mcfg([(0, 0x40000000, 0, 255)]), 'FACP': HB.sdt(b'FACP', bytes(240))}


class GicQemu(base.WrapperQemu):
    """The base test's machinery with this stage's units, bundles and boots; its own cases are not run here."""

    @classmethod
    def build_units(cls):
        profile = T.write(BUILD / 'profile.json', json.dumps({'defines': [f'-D{k}={v}' for k, v in QEMU_GIC.items()]}).encode())
        with mock.patch.dict(os.environ, {'Q1N1_SP11_TREE': str(cls.tree)}):
            wb.build(str(BUILD / 'units'), profile, gic=True)
        source = T.write(BUILD / 'mapdump' / 'mapdump.c', base.MAPDUMP.encode())
        done, _, flat = cls.lib.build_payload(source, BUILD / 'mapdump', name='mapdump', root=cls.tree)
        if done.returncode:
            raise RuntimeError('the diagnostic payload did not build:\n' + done.stderr[-1500:])
        return {name: (BUILD / 'units' / name).read_bytes() for name in wb.GIC_NAMES} | {'MAPDUMP.BIN': flat.read_bytes()}

    @classmethod
    def bundle(cls, name, placement, gic):
        """The bundle for this QEMU: the facts the first boot read, the GIC of QEMU's virt machine, the real GIC wrapper and reporter. With `gic`
        it authorises the gic class and carries the record of the GIC and stop run; without it, the dry run's two classes."""
        f, root = cls.facts, BUILD / 'bundles' / name
        facts = json.loads(T.EXAMPLE.read_text()) | dict(
            midr=[hex(f.midr)], boot_mpidr=hex(f.mpidr), cntfrq_hz=f.cntfrq, build_label='qemu-gic', gicd_base=QEMU_GIC['SNAP_GICD_BASE'],
            gicr_first=QEMU_GIC['SNAP_GICR_FIRST'], gicr_limit=QEMU_GIC['SNAP_GICR_LIMIT'], gicr_stride=QEMU_GIC['SNAP_GICR_STRIDE'], gicr_count=1,
            its_base=hex(ITS_BASE), timer_intid=int(QEMU_GIC['SNAP_TIMER_INTID']), gop={k: hex(v) if k in ('base', 'bytes') else v for k, v in f.fb.items()})
        numbers = {k: int(v, 0) if isinstance(v, str) else v for k, v in facts.items() if k in ('gicd_base', 'gicr_first', 'gicr_limit', 'its_base', 'boot_mpidr')}
        tables = qemu_tables(T.load('hamoa_bootcontract_tests', T.TOOLS / 'test-hamoa-bootcontract.py'), {**facts, **numbers})
        descs = [(7, s, p, a) for s, p, a in f.conventional] + [(0, f.fb['base'], T.page_up(f.fb['bytes']) // 4096, T.ATTR)]
        dump = root / 'dump'
        for sig, data in tables.items():
            T.write(dump / f'{sig}.bin', data)
        T.write(dump / 'memmap.raw', T.efi_map(sorted(descs, key=lambda d: d[1])))
        gop = [f'gop_width={f.fb["width"]}', f'gop_height={f.fb["height"]}', f'gop_stride_pixels={f.fb["stride"]}', f'gop_format={f.fb["format"]:#x}',
               f'gop_base={f.fb["base"]:#018x}', f'gop_size={f.fb["bytes"]:#018x}']
        T.write(dump / 'summary.txt', ('# QEMU dump\n' + '\n'.join([f'memmap_count={len(descs)}', 'memmap_descriptor_size=48', *gop]) + '\n').encode())
        facts['acpi'] = [dict(sig=s, length=len(t), sha256=T.sha(t)) for s, t in sorted(tables.items())]
        facts_path = T.write(root / 'facts.json', json.dumps(facts).encode())
        opts = ['--facts', facts_path, '--base', hex(placement), '--guest-mib', str(base.GUEST_MIB), '--monitor', str(BUILD / 'units' / 'SNAPMON-GIC.BIN'),
                '--reporter', str(BUILD / 'units' / 'SNAPREP-GIC.BIN'), '--stores', 'ram,el2_sysreg,gic' if gic else 'ram,el2_sysreg', '--time-box-s', '5',
                '--image-mib', '8', '--tables-mib', '1', '--sptm-data-mib', '4', '--dump', str(dump), *(['--gic-stop'] if gic else [])]
        code, text, err = T.run_builder(['authorisation-template', 'synthetic', *opts])
        assert code == 0, (text, err)
        record = T.write(root / 'authorisation.txt', text.encode())
        code, text, err = T.run_builder(['synthetic', *opts, '--no-manifest', '--out', str(root / 'out'), '--authorisation', record])
        assert code == 0, (text, err)
        return T.Bundle(root / 'out' / 'bundle' / 'SP11')

    @classmethod
    def build_bundles(cls):
        start, pages, _ = max(cls.facts.conventional, key=lambda d: d[1])
        low = base.align_up(start + 16 * MIB, 32 * MIB)
        if low + base.PLACE_BYTES + 64 * MIB > start + pages * 4096:
            raise unittest.SkipTest(f'the largest conventional extent of this QEMU ({pages * 4096 // MIB} MiB) cannot hold the 480 MiB placement')
        cls.low = low
        return dict(gic=cls.bundle('gic', low, True), nogic=cls.bundle('nogic', low, False))

    @classmethod
    def boot_cases(cls):
        """Three boots of the same real wrapper. QEMU's firmware leaves this CPU's redistributor asleep (GICR_WAKER 0x6, OBSERVED here), and the
        stage, which refuses to write what it was not authorised to, refuses: that is the `asleep` boot. The `gic` boot is the same stick with the
        shell waking the redistributor first (`mm`, a firmware command, before the runner starts: the machine's preparation, not the stage's
        store), which is the state the unit was found in by the GIC survey (12 of 12 awake). QEMU's wrapper reads ISENABLER0 clear (OBSERVED here, also when
        the shell was asked to set bits before the runner started: the cause is not established, INFERRED to be EDK2's ExitBootServices handler disabling every
        interrupt), and the bundle has no slot for a payload that would set it first (SP11SB_FILES is 5), so the quarantine's write has nothing to disable
        here: only the host model exercises it, and QEMU shows the survey, the check of ICC_HPPIR1_EL1 and the put-back of an enable the stage set itself."""
        wake = f'mm {int(QEMU_GIC["SNAP_GICR_FIRST"], 16) + 0x14:X} 0 -w 4 -mmio -n\n'
        stick = lambda b: cls.stick([(n, (b.dir / n).read_bytes()) for n in base.FILES])        # noqa: E731
        boots = [cls.qemu.Boot('gic', stick(cls.bundles['gic']), cls.qemu.script(before=wake), marker=base.DONE, pinned=True, timeout=600),
                 cls.qemu.Boot('asleep', stick(cls.bundles['gic']), cls.qemu.script(), marker=base.DONE, pinned=True, timeout=600),
                 cls.qemu.Boot('nogic', stick(cls.bundles['nogic']), cls.qemu.script(), marker=base.DONE, pinned=True, timeout=600)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:     # one at a time: each boot holds the stick and a QEMU of its own, and the host may be loaded
            return {b.name: b for b in pool.map(lambda b: cls.qemu.run_boot(b, cls.runner, cls.firmware), boots)}

    # -- the cases -------------------------------------------------------------------------------------------------------------
    def check_survey_and_quarantine(self, mon, rep, found=None):
        """Pass 5. The survey lines of the reporter, and the quarantine against the enable register as the reads found it (`found`, when the boot set it): every
        bit found enabled but PPI 26's was disabled by one write and read back, the window opened with PPI 26 alone enabled, and the register ends as found.
        What QEMU's firmware leaves enabled is whatever it is; QEMU proves the sequence (the writes, the RWP wait, the read-back, the put-back), never the unit's GIC."""
        for pattern in (r'GIC SPI LINES=\d+ EN=\d+ PEND=\d+ BOTH=\d+ FIRST=\d+ SECOND=\d+', r'GIC PPIP P23=0X[0-9A-F]+ P25=0X[0-9A-F]+ P27=0X[0-9A-F]+ P30=0X[0-9A-F]+',
                        r'GIC TIMERS VCTL=0X[0-7] VCVAL=0X[0-9A-F]+ PCTL=0X[0-7] PCVAL=0X[0-9A-F]+'):
            self.assertTrue(any(re.fullmatch(f'SNAPREP {pattern}', line) for line in rep), (pattern, rep))
        gicr = re.fullmatch(r'SNAPREP GIC GICR IDX=0 TYPER=0X[0-9A-F]+ WAKER=0X0 ISEN=0X([0-9A-F]+) PEND=0 ACT=0', next(line for line in rep if line.startswith('SNAPREP GIC GICR ')))
        quar = re.fullmatch(r'SNAPREP GIC QUAR MASK=(?:NONE|0X([0-9A-F]+) DONE=0X([0-9A-F]+)) HPPIR=1023', next(line for line in rep if line.startswith('SNAPREP GIC QUAR ')))
        self.assertTrue(gicr and quar, 'the GICR line, and the QUAR line with ICC_HPPIR1_EL1 read as 1023 before the window')
        seen = int(gicr.group(1), 16)
        if found is not None:
            self.assertEqual(seen, found, 'the reads found the enables the shell set')
        self.assertEqual(int(quar.group(1) or '0', 16), seen & ~(1 << 26), 'the quarantine disabled every bit found enabled but PPI 26\'s, and nothing else')
        if quar.group(1):
            self.assertEqual(int(quar.group(2), 16), seen & (1 << 26), 'ISENABLER0 read back right after the quarantine is PPI 26 as found')
        self.assertRegex(next(line for line in mon if line.startswith('SNAPMON NEXT GIC WINDOW')), r' ISEN=0X4000000 .* HPPIR=1023$')
        self.assertIn(f' ISEN=0X{seen:X} ', next(line for line in rep if line.startswith('SNAPREP GIC FINAL ')), 'ISENABLER0 is as the firmware left it after the put-back')
        return seen

    def test_the_interrupt_arrives_at_el2_and_from_el1_through_the_wrappers_vector_tables_and_everything_is_put_back(self):
        self.assert_runner_ran_both('gic')
        mon, rep, f = self.lines('gic', 'SNAPMON'), self.lines('gic', 'SNAPREP'), self.facts
        self.assertIn('SNAPMON START VERSION=1 EL=0X8 POLICY=0X3', mon)
        at = mon.index(next(line for line in mon if line.startswith('SNAPMON STAGE=COPIED')))
        names = [re.match(r'SNAPMON (GIC \w+|NEXT GIC \w+)', line).group(1) for line in mon[at + 1:] if re.match(r'SNAPMON (GIC|NEXT GIC) ', line)]
        self.assertEqual(names, ['GIC GICD', 'GIC GICR', 'GIC CPU', 'NEXT GIC STORES', 'NEXT GIC WINDOW', 'GIC IRQ', 'NEXT GIC EL1', 'GIC EL1', 'GIC RESTORE',
                                 'GIC VERDICT'], mon)
        self.assertRegex(mon[at + 4], r'^SNAPMON NEXT GIC STORES AFTER 8 S G=[01] R=1 C=1 T=1 V=1 E=1$')
        irq = re.fullmatch(r'SNAPMON GIC IRQ INTID=26 LAT_US=(-?\d+)/(-?\d+)/(-?\d+)/(-?\d+) OK=1111 FINAL=CLEAR', mon[at + 6])
        self.assertTrue(irq, mon[at + 6])
        for k in range(4):
            self.assertTrue(0 <= int(irq.group(k + 1)) < 2_000_000, f'interrupt {k} arrived after its programmed deadline and inside the time box')
        self.assertRegex(mon[at + 7], r'^SNAPMON NEXT GIC EL1 EXCURSION AFTER 8 S HCR=0X80000010 SCTLR1=0X30500800 VBAR1=0X[0-9A-F]+ PCT=1$')
        el1 = re.fullmatch(r'SNAPMON GIC EL1 INTID=26 LAT_US=(-?\d+) (ON_TIME|LATE) ELR=0X[0-9A-F]+ SPSR=0X([0-9A-F]+) OK=1', mon[at + 8])
        self.assertTrue(el1, mon[at + 8])
        self.assertTrue(0 <= int(el1.group(1)) < 2_000_000)
        self.assertEqual(int(el1.group(3), 16) & 0x3FF, 0x345, 'taken from EL1h with D, A and F masked and I clear: the state the stub was entered in')
        self.assertRegex(mon[at + 9], r'^SNAPMON GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=G?RCTVE VBAR=OK EL1=OK$')
        self.assertEqual(mon[at + 10], 'SNAPMON GIC VERDICT=IRQ_OK OUTCOME=0X20 SUB=DONE')
        self.assertEqual(mon[-2:], ['SNAPMON STAGE=DONE RETURN=0', 'SNAPMON MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD'])
        self.assertEqual(self.done_values('gic'), [0, 0])
        self.assertIn('SNAPREP GIC VERDICT=IRQ_OK OUTCOME=0X20 SUB=DONE', rep)
        self.assertIn(f'SNAPREP GIC FRAMES GICD=0X8000000 GICR=0X80A0000 INTID=26 HZ={f.cntfrq}', rep)
        self.assertTrue(any(re.fullmatch(r'SNAPREP GIC IRQ INTID=26 LAT_US=-?\d+/-?\d+/-?\d+/-?\d+ OK=1111 FINAL=CLEAR', line) for line in rep), rep)
        self.assertTrue(any(re.fullmatch(r'SNAPREP GIC OLD PRIO=0X[0-9A-F]+ ICFGR1=0X[0-9A-F]+ IGROUPR0=0X[0-9A-F]+ ISPEND=0X0 ISACT=0X0', line) for line in rep),
                        'the reporter shows the whole of ISPENDR0 and ISACTIVER0 as found')
        # Pass 5: the survey lines, and the quarantine against the enable register as the reads found it.
        self.check_survey_and_quarantine(mon, rep)
        cycles = [re.fullmatch(r'SNAPREP GIC CYC([0-4]) IAR=(\d+) HPPIR=(\d+) POST=(\d+) F=([01]{3}) TICKS=(-?\d+) US=(-?\d+)', line) for line in rep]
        cycles = {int(m.group(1)): m.groups()[1:] for m in cycles if m}
        self.assertEqual(sorted(cycles), [0, 1, 2, 3, 4], rep)
        for k, (iar, hppir, post, state, _, _) in cycles.items():
            self.assertEqual((iar, hppir, post, state), ('0' if k == 3 else '26', '26', '1023', '100'),
                             f'interrupt {k}: acknowledged (not the final one), signalled as 26, ISTATUS set, and nothing pending or active after it')
        for register in ('HCR', 'VBAR1', 'SCTLR1'):
            self.assertTrue(any(re.fullmatch(rf'SNAPREP GIC {register} OLD=(0X[0-9A-F]+) RB=0X[0-9A-F]+ FIN=\1', line) for line in rep), (register, rep))

    def test_a_redistributor_that_the_firmware_left_asleep_is_refused_by_the_real_wrapper_at_el2_before_any_store(self):
        mon, rep = self.refusal('asleep', 'GIC_NOT_READY', 'COPIED', 9)
        gicr = next(line for line in mon if line.startswith('SNAPMON GIC GICR '))
        self.assertRegex(gicr, r'^SNAPMON GIC GICR IDX=0 TYPER=0X[0-9A-F]+ WAKER=0X6 ISEN=0X0 PEND=0 ACT=0$')
        self.assertIn('SNAPMON GIC VERDICT=GICR_ASLEEP OUTCOME=0X9 SUB=READS', mon)
        self.assertEqual([line for line in mon if line.startswith('SNAPMON NEXT GIC')], [], 'a refusal before the first store announces nothing')
        self.assertIn('SNAPREP GIC VERDICT=GICR_ASLEEP OUTCOME=0X9 SUB=READS', rep)

    def test_a_bundle_that_does_not_authorise_the_gic_class_is_refused_by_the_gic_wrapper_at_el2_before_it_touches_the_gic(self):
        mon, rep = self.refusal('nogic', 'STORE_NOT_AUTH', 'PIN_OK', 7)
        self.assertEqual([line for line in mon + rep if ' GIC ' in line and 'NO RECORD' not in line], [], 'no GIC line: the stage never ran')
        self.assertEqual(rep[-1], 'SNAPREP GIC NO RECORD: THE STAGE DID NOT RUN')


# The base test's own cases are for the dry-run wrapper; they are not inherited here.
for _name in [n for n in dir(base.WrapperQemu) if n.startswith('test_')]:
    setattr(GicQemu, _name, None)
del _name

if __name__ == '__main__':
    unittest.main()
