#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The retention probe end to end under QEMU: the plan `retention-m10` on a stick, the real payload runner and the real retprobe payload at EL2,
a PSCI reset, the pinned UEFI Shell's extract step, and the verdict tool on the file it wrote.

    python3 tools/test-sp11-retention-qemu.py

Every stick here is staged by the tool itself (`stage`, then `autorun` with the plan's two steps), with a stand-in for slbounce (the unit's
Secure Launch bounce cannot run here: QEMU starts the guest at EL2 already, which is what the runner needs), the real Q1N1-PAYLOAD.EFI and
RETPROBE.BIN built from the q1n1 tree, and a configuration naming QEMU pages. Boot 1 of the stick runs step 1: the runner calls the probe, the
probe writes three canary pages, cleans them, counts down two seconds and calls SYSTEM_RESET; QEMU's PSCI resets the guest, and QEMU keeps the
guest's RAM across a reset (the emulator runs on, `reboot`). Boot 2, by itself, runs step 2: the Shell dumps the three pages with `dmem`, then
`memmap`, then its `date` and `time`, then the end line, and writes the marker. The file SP11/RET2.TXT is read back from the stick image and judged by
tools/sp11_retention_check.py.

  low        RAM at 0x48000000, 0x50000000, 0x58000000 (1 GiB): three WHOLE pages, path PSCI_RESET, boot counter 1, free in the extraction boot
  high       RAM above 4 GiB (6 GiB, pages at 0x140000000 and above): the Shell's `dmem` of an address that needs nine digits, and the verdict
             tool's reading of it, which is the unit's case (its top page is 0xFFFF00000)
  no-probe   a stick whose step 1 is already marked done, so the dump runs on RAM nothing wrote: NONE on every page, which says the pages carry
             no canary and nothing about whether a probe ran (the design document's outcome table)
  again      the stick of `low` booted once more: every step is done, nothing runs, the result is untouched
  guard      the same stick with the marker of step 2 deleted but the result kept: the script refuses to overwrite the result and says so

What it proves: the plan's script, the real runner and probe, the PSCI call and the Shell's dump work together under an emulator that keeps RAM
across a reset, and the verdict tool reads the Shell's real text. It proves nothing about the unit's DRAM, its firmware's reset or the Shell on
the unit: those are the measurement. Hardware NOT RUN. Evidence: EXPERIMENTAL (QEMU only).

SKIPs, never passes, without Q1N1_SP11_TREE (a q1n1 checkout), make, clang, lld-link, qemu-system-aarch64, an EDK2 AArch64 firmware pair or the
pinned Shell (SP11_UEFI_SHELL names it). Writes only under build/test-sp11-retention-qemu/ (a short path: a socket path may not exceed 104)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
BUILD = ROOT / 'build' / 'test-sp11-retention-qemu'
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


Q = load('autorun_qemu_tests_for_retention', TOOLS / 'test-sp11-autorun-qemu.py')
autorun, stage, fx, q = Q.autorun, Q.stage, Q.fx, Q.q
import sp11_retention as R  # noqa: E402
import sp11_retention_check as C  # noqa: E402
import sp11_retention_plan as P  # noqa: E402

LOW = (0x48000000, 0x50000000, 0x58000000)
HIGH = (0x180000000, 0x190000000, 0x1A0000000)      # RAM is 0x40000000 to 0x1C0000000 with 6 GiB; the runner's image prefers 0x140000000, which is why not there
SP11_VARS = ['SP11=1', 'SP11_USB_TABLE_C=tools/testdata/sp11-usb-table-fixture.c', 'SP11_STAGE_TABLE_C=tools/testdata/sp11-stage-table-fixture.c']
DONE_LINE = 'is written, power off and take the stick to the Mac'
BANNER = 'UEFI firmware (version'                   # EDK2 prints it once at every start of the guest
sha = stage.sha256_bytes


def build_runner(tree: Path, out: Path):
    """The real runner and payloads, built the way the q1n1 fork's QEMU suites build them; (runner, retprobe.bin)."""
    done = subprocess.run(['make', '-f', 'platform/uefi/Makefile', f'OUT={out}', '-j4', 'PAYLOAD=1', *SP11_VARS, 'payload-runner', 'payloads'],
                          cwd=tree, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError('the runner or the payloads do not build:\n' + done.stdout[-1500:] + done.stderr[-1500:])
    return out / 'q1n1-payload.efi', out / 'payloads' / 'retprobe.bin'


class RetentionEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = os.environ.get('Q1N1_SP11_TREE')
        try:
            if not tree or not (Path(tree) / 'payloads' / 'retprobe.c').is_file():
                raise unittest.SkipTest('Q1N1_SP11_TREE does not name a q1n1 checkout with payloads/retprobe.c')
            for tool in ('make', 'clang', 'ld.lld'):
                if shutil.which(tool) is None:
                    raise unittest.SkipTest(f'{tool} is not installed')
            cls.shell = Q.pinned_shell()
            for tool in ('clang', 'lld-link'):
                q.find_tool(tool)
            q.qemu_probe.find_assets()
        except (q.Unavailable, q.qemu_probe.Unavailable, unittest.SkipTest) as reason:
            print(f'SKIP: {reason}')
            raise unittest.SkipTest(str(reason))
        shutil.rmtree(BUILD, ignore_errors=True)
        BUILD.mkdir(parents=True)
        cls.runner, cls.probe = build_runner(Path(tree), BUILD / 'q1n1')
        cls.lab = Q.Lab(cls.shell)
        cls.images = {name: cls.stick(name, pages, done_marker) for name, pages, done_marker in
                      (('low', LOW, False), ('high', HIGH, False), ('no-probe', LOW, True))}
        cls.configs = {'low': R.config_bytes(1, 2, LOW), 'high': R.config_bytes(1, 2, HIGH), 'no-probe': R.config_bytes(1, 2, LOW)}
        jobs = {
            'low': lambda: cls.boot('low', 'low', timeout=300),
            'high': lambda: cls.boot('high', 'high', timeout=300, memory='6G'),
            'no-probe': lambda: cls.boot('no-probe', 'no-probe', timeout=200),
        }
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {name: pool.submit(job) for name, job in jobs.items()}
        cls.boots, cls.errors = {}, {}
        for name, future in futures.items():
            try:
                cls.boots[name] = future.result()
            except Exception as error:     # reported by the cases that need this scenario
                cls.errors[name] = error
        cls.followups = {}
        if 'low' in cls.boots:
            files = dict(cls.boots['low'].stick.files)
            cls.followups['again'] = cls.boot('again', 'low', timeout=200, image=cls.image_of(files, 'again'))
            guarded = {path: data for path, data in files.items() if path != 'SP11/DONE2.TXT'}
            cls.followups['guard'] = cls.boot('guard', 'low', timeout=200, image=cls.image_of(guarded, 'guard'))

    @classmethod
    def tearDownClass(cls):
        if not os.environ.get('SP11_RETENTION_KEEP'):
            shutil.rmtree(BUILD, ignore_errors=True)

    @classmethod
    def stick(cls, tag, pages, done_marker):
        """The image of a stick staged by the tool for the plan retention-m10 with `pages` (and step 1 pre-marked when `done_marker`)."""
        tree = BUILD / tag / 'tree'
        tree.parent.mkdir(parents=True)
        lab = cls.lab
        code, result = Q.run_tool('stage', '--shell', cls.shell, '--slbounce', lab.stubs['slbounce.efi'], '--sltest', lab.stubs['sltest.efi'],
                                  '--tcblaunch', lab.stubs['tcblaunch.exe'], '--dest', tree, '--pins', stage.DEFAULT_PINS)
        assert (code, result['status']) == (0, 'STAGED_COMPLETE'), result
        probe = cls.probe.read_bytes()
        config = R.config_bytes(1, 2, pages)
        files = {'RETPROBE.BIN': probe, 'RETPATH.DAT': config}
        driver, driver_hash = lab.dummy('SLBOUNCE-DEVHE-MARK.EFI', 'DRIVER')
        args = ['autorun', '--dest', tree, '--pins', stage.DEFAULT_PINS, '--name', 'retention-m10',
                '--file', 'SLBOUNCE-DEVHE-MARK.EFI', driver, driver_hash, '--file', 'Q1N1-PAYLOAD.EFI', cls.runner, sha(cls.runner.read_bytes())]
        for name, blob in {'PAYLOAD.MAN': fx.listing(files), **files}.items():
            source = BUILD / tag / 'data' / name
            source.parent.mkdir(exist_ok=True)
            source.write_bytes(blob)
            args += ['--data', name, source, sha(blob)]
        for step in P.step_texts(pages):
            args += ['--step', step]
        code, result = Q.run_tool(*args)
        assert (code, result['status']) == (0, 'AUTORUN_STAGED'), result
        if done_marker:
            (tree / 'SP11' / 'DONE1.TXT').write_bytes(b'AUTORUN retention-m10 STEP 1 STARTED\r\n')
        return lab.stick(tree)

    @classmethod
    def image_of(cls, files, tag):
        image = q.LongNameFat16Image()
        for path, data in sorted(files.items()):
            image.add_file(path, data)
        target = BUILD / tag / 'stick.img'
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(image.to_bytes())
        return target

    @classmethod
    def boot(cls, name, scenario, timeout, memory='', image=None):
        done = (lambda s: 'ALL STEPS DONE' in s) if name == 'again' else (lambda s: DONE_LINE in s or 'AUTORUN REFUSED: SP11\\RET2.TXT exists' in s)
        return q.boot_stick(image or cls.images[scenario], cls.shell, BUILD / name / 'boot', until=done, timeout=timeout, settle=8.0, el2=True, reboot=True,
                            memory=memory)

    def run_of(self, name):
        if name in self.errors:
            self.fail(f'the {name} scenario did not run: {self.errors[name]!r}')
        return self.boots[name] if name in self.boots else self.followups[name]

    def judged(self, name):
        boot = self.run_of(name)
        pages = {'low': LOW, 'high': HIGH, 'no-probe': LOW}[name]
        self.assertIn('SP11/RET2.TXT', boot.stick.files, f'no result file; the stick holds {sorted(boot.stick.files)}')
        dump = C.parse_dump(boot.stick.files['SP11/RET2.TXT'], expected_pages=pages, step=2)
        return boot, dump, C.judge(dump, self.configs[name])

    def test_the_probe_resets_and_the_next_boot_extracts_three_whole_pages(self):
        boot, dump, report = self.judged('low')
        serial = boot.serial
        banners = [n for n in range(len(serial)) if serial.startswith(BANNER, n)]
        self.assertEqual(len(banners), 2, 'the firmware started twice: the probe reset the guest')
        self.assertTrue(banners[0] < serial.index('Leaving firmware now') < banners[1], 'the reset came after the runner left the firmware')
        self.assertLess(banners[1], serial.index('STEP 2 OF 2'))
        self.assertEqual(serial.count('AUTORUN retention-m10 STEP 1 OF 2'), 1, 'step 1 ran once, in boot 1')
        self.assertEqual([r.verdict.state for r in report.pages], ['WHOLE'] * 3)
        self.assertEqual((report.verdict, report.path, report.counter, report.previous_counter), ('WHOLE', 'PSCI_RESET', 1, 0))
        self.assertEqual([r.map_free for r in report.pages], [True] * 3, 'the pages are free memory in the extraction boot too')
        header = report.pages[0].verdict.header
        self.assertEqual((header['path_requested'], header['path_used'], header['feature_reset'], header['feature_reset2'], header['previous_states']),
                         (1, 1, 0, 0xFFFFFFFF, (0, 0, 0)))
        self.assertIn(header['frequency'], (62500000, 1000000000), 'the counter frequency QEMU gives the guest, as the probe read it')
        shown = C.format_report(report, 'RET2.TXT')
        self.assertIn('verdict for path PSCI_RESET: WHOLE (WHOLE x3, PARTIAL x0, NONE x0)', shown)

    def test_the_result_is_the_dumps_first_then_the_memory_map_then_the_clock_then_the_end_line_and_the_markers_follow(self):
        boot = self.run_of('low')
        data = boot.stick.files['SP11/RET2.TXT'].decode('ascii')
        self.assertTrue(data.startswith(f'Memory Address {LOW[0]:016X} 1000 Bytes\r\n'), 'the first thing in the file is the first dump')
        self.assertLess(data.rindex('Memory Address'), data.index('Type '), 'the memory map follows the dumps')
        self.assertRegex(data, r'\r\n[0-9]{2}/[0-9]{2}/[0-9]{4}\r\n[0-9]{2}:[0-9]{2}:[0-9]{2} \([^)\r\n]*\)\r\nRETENTION-EXTRACT-END plan=retention-m10 step=2 pages=3\r\n$',
                         'the Shell\'s date and time, then the end line')
        self.assertLess(data.index('Type '), re.search(r'\r\n[0-9]{2}/[0-9]{2}/[0-9]{4}\r\n', data).start(), 'the clock follows the memory map')
        self.assertEqual(boot.stick.files['SP11/DONE1.TXT'].decode().strip(), 'AUTORUN retention-m10 STEP 1 STARTED')
        self.assertEqual(boot.stick.files['SP11/DONE2.TXT'].decode().strip(), 'AUTORUN retention-m10 STEP 2 EXTRACTED')

    def test_the_clock_the_shell_wrote_is_the_real_time_and_later_than_the_staging_the_stager_recorded(self):
        """QEMU's real-time clock reads UTC (OBSERVED: the Shell's `time` printed 01:20 when the Mac's clock said 05:20, UTC+4), the stager records the
        staging computer's local time, so the comparison names the zone; on the unit both are local time (docs/SP11-STICK-LOG.md section 14.1)."""
        boot, dump, report = self.judged('low')
        plan = BUILD / 'clock-plan' / 'PLAN.TXT'
        plan.parent.mkdir(exist_ok=True)
        plan.write_bytes(boot.stick.files['SP11/PLAN.TXT'])
        staged_local = C._staging_time(plan)
        self.assertEqual(autorun.parse_plan_text(plan.read_bytes()).staged, staged_local.strftime(autorun.STAGED_FORMAT))
        staged_utc = staged_local.astimezone(timezone.utc).replace(tzinfo=None)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertLess(staged_utc, dump.clock, 'the extraction happened after the staging')
        self.assertLess(dump.clock, now_utc + timedelta(minutes=1), 'and not in the future')
        self.assertLess(now_utc - dump.clock, timedelta(hours=1), 'the clock is the real time: the Shell printed what QEMU\'s RTC says')
        self.assertEqual(report.clock, dump.clock)

    def test_pages_above_four_gib_are_dumped_and_read(self):
        boot, dump, report = self.judged('high')
        self.assertEqual([a for a, _ in dump.pages], list(HIGH))
        self.assertIn(f'  {HIGH[0] & 0xFFFFFFFF:X}: ', boot.stick.files['SP11/RET2.TXT'].decode('ascii'), 'the Shell prints the low 32 bits of a line address')
        self.assertEqual((report.verdict, [r.verdict.state for r in report.pages]), ('WHOLE', ['WHOLE'] * 3))

    def test_a_stick_whose_probe_never_ran_extracts_none_and_the_tool_says_what_that_does_not_mean(self):
        boot, dump, report = self.judged('no-probe')
        self.assertEqual(boot.serial.count(BANNER), 1, 'the firmware started once: nothing reset the guest')
        self.assertNotIn('Leaving firmware now', boot.serial)
        self.assertEqual((report.verdict, [r.verdict.state for r in report.pages]), ('NONE', ['NONE'] * 3))
        self.assertIn('It does not say the probe wrote one', C.format_report(report))

    def test_a_finished_stick_runs_nothing_and_a_result_is_never_overwritten(self):
        original = self.run_of('low').stick.files['SP11/RET2.TXT']
        again = self.run_of('again')
        self.assertIn('ALL STEPS DONE', again.serial)
        self.assertEqual(again.stick.files['SP11/RET2.TXT'], original)
        guard = self.run_of('guard')
        self.assertIn('AUTORUN REFUSED: SP11\\RET2.TXT exists and is never overwritten', guard.serial)
        self.assertEqual(guard.stick.files['SP11/RET2.TXT'], original)
        self.assertNotIn('SP11/DONE2.TXT', guard.stick.files, 'the refused extraction writes no marker')

    def test_the_verdict_tool_refuses_the_real_file_cut_short(self):
        data = self.run_of('low').stick.files['SP11/RET2.TXT']
        for keep in (len(data) // 3, len(data) - 120):
            with self.subTest(keep=keep), self.assertRaises(C.DumpError):
                C.parse_dump(data[:keep])
        damaged = bytearray(data)
        damaged[data.index(b'RETCAN01') + 4] ^= 0x01                 # the text column no longer matches the byte
        with self.assertRaises(C.DumpError) as caught:
            C.parse_dump(bytes(damaged))
        self.assertEqual(caught.exception.reason, 'ASCII_COLUMN')


if __name__ == '__main__':
    unittest.main()
