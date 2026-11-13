#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The autorun script under the pinned UEFI Shell in QEMU (EDK2 firmware): what a stick staged by the tool does.

    python3 tools/test-sp11-autorun-qemu.py

Every stick here is made by the tool itself: `stage` with the pinned Shell and synthetic stand-ins for slbounce, its
self-test and tcblaunch, then `autorun` with stand-in images (tools/sp11-autorun-dummy.c, built with clang and
lld-link) that print one line and return. No reviewed binary runs. The staged directory is copied into a FAT16 image
(tools/sp11_autorun_qemu.py) and booted; the console log and the stick read back afterwards are what the cases check.

  two-step    one stick, three boots: step 1 only, step 2 only, then "ALL STEPS DONE" and nothing runs; nobody types
              (no key is sent), so the Shell's countdown elapses by itself
  hung        the first step's application never returns, so only the marker written BEFORE it can have stopped the
              next boot from repeating it: the next boot of the same stick runs step 2
  missing     the application, and separately the slbounce build, is absent from the stick: a refusal line, no
              step runs, no marker
  esc         ESC at the countdown (sent through the emulator's monitor) skips startup.nsh
  ctrl-c      Ctrl-C during the pause before the marker cancels the step with no marker
  decoy       another volume comes first, so the stick is not FS0: the script still uses the Shell's own volume and
              writes nothing to the other one
  removed     after `autorun --remove` the stick starts the Shell with no script (the manual state)
  no-volume   the script without its volume selection (a Shell that sets no `homefilesystem`): it finds no
              SP11/PLAN.TXT on the bare prompt, refuses, and nothing runs
  bad-load    the slbounce stand-in fails to start: the Shell says so but the script cannot tell (a documented limit)
  read-only   the stick is write-protected, so the marker cannot be written first: the Shell stops the script at the
              failed redirect and nothing runs
  survey      a plan whose application is named Q1N1-STK.EFI (the read-only USB survey build of q1n1, run with
              --el2): the first command name with a hyphen, and an exact 8.3 name where the others are long ones

Evidence: EXPERIMENTAL (QEMU only), hardware NOT RUN. QEMU's firmware is edk2-stable202408 and the Shell is the pinned
release binary (edk2-stable202608), not necessarily the firmware or even the file of the unit; what the unit's
keyboard, USB stack and FAT driver do is INFERRED. SKIPs, never passes, without clang, lld-link,
qemu-system-aarch64, an EDK2 AArch64 firmware pair or the pinned Shell (SP11_UEFI_SHELL or SP11_SHELL_EFI name it;
build/sp11-inputs/shellaa64.efi and downloads/sp11/boot-media/shellaa64.efi are tried; a file that is not the pinned
one FAILS). Writes only under build/test-sp11-autorun-qemu/.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
BUILD = ROOT / 'build' / 'test-sp11-autorun-qemu'
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


autorun = load('sp11_autorun', TOOLS / 'sp11_autorun.py')   # the stager imports it by name, so it is registered first
stage = load('stage_sp11_boot_media', TOOLS / 'stage-sp11-boot-media.py')
fixtures = load('boot_media_fixtures', TOOLS / 'test-sp11-boot-media.py')
import sp11_autorun_qemu as q  # noqa: E402

STEP_ONE, STEP_TWO = 'SLBOUNCE-DEVHE-MARK.EFI Q1N1.EFI --el2', 'SLBOUNCE-DEVHE.EFI Q1N1.EFI --el2'
APP_LINE = r'AUTORUN-DUMMY APP Q1N1.EFI OPTIONS=[SP11\Q1N1.EFI --el2]'
STEP_SURVEY = 'SLBOUNCE-DEVHE-MARK.EFI Q1N1-STK.EFI --el2'
SURVEY_LINE = r'AUTORUN-DUMMY APP Q1N1-STK.EFI OPTIONS=[SP11\Q1N1-STK.EFI --el2]'
EFI_LOAD_ERROR = 0x8000000000000001


def pinned_shell(candidates=None, pin=None) -> Path:
    """The pinned UEFI Shell: SKIP when no file is found, FAIL when a file that is found is not the pinned one."""
    if pin is None:
        entry = json.loads(stage.DEFAULT_PINS.read_text(encoding='utf-8'))['shell']
        pin = (entry['size'], entry['sha256'])
    if candidates is None:
        candidates = [os.environ.get('SP11_UEFI_SHELL'), os.environ.get('SP11_SHELL_EFI'),
                      ROOT / 'build' / 'sp11-inputs' / 'shellaa64.efi',
                      ROOT / 'downloads' / 'sp11' / 'boot-media' / 'shellaa64.efi']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            data = Path(candidate).read_bytes()
            if (len(data), stage.sha256_bytes(data)) != tuple(pin):
                raise AssertionError(f'{candidate} is not the UEFI Shell that tools/sp11-boot-media-pins.json pins')
            return Path(candidate)
    raise unittest.SkipTest('the pinned UEFI Shell is not available (set SP11_UEFI_SHELL to shellaa64.efi)')


def run_tool(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = stage.main([str(a) for a in argv])
    return code, json.loads(out.getvalue())


class Lab:
    """Builds the sticks one after another (the tool's output goes through a redirected stdout) and boots them."""

    def __init__(self, shell: Path):
        self.shell = shell
        shutil.rmtree(BUILD, ignore_errors=True)
        (BUILD / 'in').mkdir(parents=True)
        self.stubs = {}
        for name, subsystem in (('slbounce.efi', 12), ('sltest.efi', 10), ('tcblaunch.exe', 16)):
            self.stubs[name] = BUILD / 'in' / name
            self.stubs[name].write_bytes(fixtures.make_pe(subsystem=subsystem, body=name.encode() * 4))
        self.dummies = BUILD / 'dummies'
        self.built = {}

    def dummy(self, name, kind, hang=False, status=0):
        key = (name, kind, hang, status)
        if key not in self.built:
            data = q.build_dummy(name, kind, self.dummies, hang=hang, status=status)
            path = self.dummies / f'{name}-{kind}{"-HANG" if hang else ""}{"-FAIL" if status else ""}.bin'
            path.write_bytes(data)
            self.built[key] = (path, stage.sha256_bytes(data))
        return self.built[key]

    def tree(self, tag, steps, hung=(), failing=()):
        """A staged directory made by `stage` then `autorun`. `hung` names the applications that never return and
        `failing` the slbounce stand-ins whose entry point returns an error."""
        tree = BUILD / tag / 'tree'
        shutil.rmtree(tree.parent, ignore_errors=True)
        tree.parent.mkdir(parents=True)
        code, result = run_tool('stage', '--shell', self.shell, '--slbounce', self.stubs['slbounce.efi'],
                                '--sltest', self.stubs['sltest.efi'], '--tcblaunch', self.stubs['tcblaunch.exe'],
                                '--dest', tree, '--pins', stage.DEFAULT_PINS)
        assert (code, result['status'], result['shell_pin']) == (0, 'STAGED_COMPLETE', 'match'), result
        args = ['autorun', '--dest', tree, '--pins', stage.DEFAULT_PINS, '--name', 'devhe-mark']
        names = {}
        for step in steps:
            slbounce, app, *_ = step.split()
            names[slbounce] = 'DRIVER'
            names[app] = 'APP'
        for name, kind in sorted(names.items()):
            path, digest = self.dummy(name, kind, hang=name in hung, status=EFI_LOAD_ERROR if name in failing else 0)
            args += ['--file', name, path, digest]
        for step in steps:
            args += ['--step', step]
        code, result = run_tool(*args)
        assert (code, result['status']) == (0, 'AUTORUN_STAGED'), result
        code, result = run_tool('verify', '--dest', tree, '--pins', stage.DEFAULT_PINS)
        assert (code, result['status'], result['startup_nsh_found']) == (0, 'COMPLETE', []), result
        return tree

    def stick(self, tree, drop=(), replace=None):
        """A FAT16 image of the staged directory, minus the paths in `drop` and with `replace` {path: bytes} swapped in."""
        image = q.LongNameFat16Image()
        for path in sorted(tree.rglob('*')):
            rel = path.relative_to(tree).as_posix()
            if path.is_file() and rel not in drop:
                image.add_file(rel, (replace or {}).get(rel, path.read_bytes()))
        target = tree.parent / 'stick.img'
        target.write_bytes(image.to_bytes())
        return target

    def decoy(self, tag):
        image = q.LongNameFat16Image(label='DECOYDISK')
        image.add_file('DECOY.TXT', b'another volume\r\n')
        target = BUILD / tag / 'decoy.img'
        target.write_bytes(image.to_bytes())
        return target

    def boot(self, image, tag, number=1, **kw):
        return q.boot_stick(image, self.shell, BUILD / tag / f'boot{number}', **kw)


def markers(boot):
    return sorted(path for path in boot.stick.files if path.startswith('SP11/DONE'))


class AutorunUnderTheShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.shell = pinned_shell()
            for tool in ('clang', 'lld-link'):
                q.find_tool(tool)
            q.qemu_probe.find_assets()
        except (q.Unavailable, q.qemu_probe.Unavailable, unittest.SkipTest) as reason:
            print(f'SKIP: {reason}')
            raise unittest.SkipTest(str(reason))
        lab = Lab(cls.shell)
        two = lab.tree('two-step', [STEP_ONE, STEP_TWO])
        hung = lab.tree('hung', ['SLBOUNCE-DEVHE-MARK.EFI TESTS.EFI', 'SLBOUNCE-DEVHE.EFI PROBE.EFI'], hung=('TESTS.EFI',))
        bad = lab.tree('bad-load', [STEP_ONE], failing=('SLBOUNCE-DEVHE-MARK.EFI',))
        cls.images = {
            'two-step': lab.stick(two),
            'hung': lab.stick(hung),
            'bad-load': lab.stick(bad),
            'read-only': lab.stick(lab.tree('read-only', [STEP_ONE, STEP_TWO])),
            'no-app': lab.stick(lab.tree('no-app', [STEP_ONE, STEP_TWO]), drop=('SP11/Q1N1.EFI',)),
            'no-slbounce': lab.stick(lab.tree('no-slbounce', [STEP_ONE, STEP_TWO]), drop=('SP11/SLBOUNCE-DEVHE-MARK.EFI',)),
            'esc': lab.stick(lab.tree('esc', [STEP_ONE, STEP_TWO])),
            'ctrl-c': lab.stick(lab.tree('ctrl-c', [STEP_ONE, STEP_TWO])),
            'decoy': lab.stick(lab.tree('decoy', [STEP_ONE, STEP_TWO])),
            'survey': lab.stick(lab.tree('survey', [STEP_SURVEY])),
        }
        removed = lab.tree('removed', [STEP_ONE, STEP_TWO])
        code, result = run_tool('autorun', '--dest', removed, '--remove')
        assert (code, result['status']) == (0, 'AUTORUN_REMOVED'), result
        cls.images['removed'] = lab.stick(removed)
        # Stands for a Shell that does not set `homefilesystem`: the script is the generated one without that line.
        blind = lab.tree('no-volume', [STEP_ONE, STEP_TWO])
        script = (blind / 'EFI' / 'BOOT' / 'startup.nsh').read_bytes()
        assert b'%homefilesystem%\r\n' in script
        cls.images['no-volume'] = lab.stick(blind, replace={'EFI/BOOT/startup.nsh': script.replace(b'%homefilesystem%\r\n', b'')})
        cls.decoy_image = lab.decoy('decoy')
        jobs = {
            'two-step': lambda: [lab.boot(cls.images['two-step'], 'two-step', n) for n in (1, 2, 3)],
            'hung': lambda: [lab.boot(cls.images['hung'], 'hung', 1, until=lambda s: 'AUTORUN-DUMMY APP TESTS.EFI' in s),
                             lab.boot(cls.images['hung'], 'hung', 2)],
            'bad-load': lambda: [lab.boot(cls.images['bad-load'], 'bad-load')],
            'read-only': lambda: [lab.boot(cls.images['read-only'], 'read-only', read_only=True)],
            'no-app': lambda: [lab.boot(cls.images['no-app'], 'no-app')],
            'no-slbounce': lambda: [lab.boot(cls.images['no-slbounce'], 'no-slbounce')],
            'esc': lambda: [lab.boot(cls.images['esc'], 'esc', keys=(('Press ESC in 5', 'esc'),))],
            'ctrl-c': lambda: [lab.boot(cls.images['ctrl-c'], 'ctrl-c', keys=(('Writing SP11', 'ctrl-c'),))],
            'decoy': lambda: [lab.boot(cls.images['decoy'], 'decoy', decoy=cls.decoy_image)],
            'removed': lambda: [lab.boot(cls.images['removed'], 'removed')],
            'no-volume': lambda: [lab.boot(cls.images['no-volume'], 'no-volume')],
            'survey': lambda: [lab.boot(cls.images['survey'], 'survey')],
        }
        with ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) // 2))) as pool:
            futures = {name: pool.submit(job) for name, job in jobs.items()}
        cls.boots, cls.errors = {}, {}
        for name, future in futures.items():
            try:
                cls.boots[name] = future.result()
            except Exception as error:   # reported by the cases that need this scenario
                cls.errors[name] = error

    @classmethod
    def tearDownClass(cls):
        if not os.environ.get('SP11_AUTORUN_KEEP'):
            shutil.rmtree(BUILD, ignore_errors=True)

    def run_of(self, name):
        if name in self.errors:
            self.fail(f'the {name} scenario did not run: {self.errors[name]!r}')
        return self.boots[name]

    def test_the_shell_under_test_is_the_pinned_release(self):
        boot = self.run_of('two-step')[0]
        self.assertIn('UEFI Interactive Shell v2.2', boot.serial)
        self.assertIn('edk2-stable202608 (https://github.com/pbatard/UEFI-Shell)', boot.serial)
        firmware = [line for line in boot.serial.splitlines() if line.startswith('UEFI firmware')]
        print(f'\nQEMU: {firmware[0] if firmware else "firmware banner not found"}; Shell: UEFI Interactive Shell v2.2, '
              'edk2-stable202608 (pinned release)')

    def test_nobody_types_and_the_countdown_elapses_by_itself(self):
        boot = self.run_of('two-step')[0]
        self.assertEqual(boot.sent, ())
        for seconds in (5, 4, 3, 2, 1):
            self.assertIn(f'Press ESC in {seconds} seconds to skip startup.nsh', boot.serial)
        self.assertLess(boot.serial.index('Press ESC in 1 seconds'), boot.serial.index('AUTORUN devhe-mark: 2 steps'))

    def test_each_boot_of_one_stick_runs_one_step(self):
        first, second, third = self.run_of('two-step')
        for boot in (first, second, third):
            self.assertEqual(boot.ended, 'idle', boot.serial[-400:])
            self.assertIn('AUTORUN devhe-mark: 2 steps, one per boot.', boot.serial)
        self.assertEqual(markers(first), ['SP11/DONE1.TXT'])
        self.assertEqual(markers(second), ['SP11/DONE1.TXT', 'SP11/DONE2.TXT'])
        self.assertEqual(markers(third), ['SP11/DONE1.TXT', 'SP11/DONE2.TXT'])
        self.assertEqual(first.stick.files['SP11/DONE1.TXT'], b'AUTORUN devhe-mark STEP 1 STARTED\r\n')
        self.assertEqual(second.stick.files['SP11/DONE1.TXT'], first.stick.files['SP11/DONE1.TXT'])
        self.assertEqual(second.stick.files['SP11/DONE2.TXT'], b'AUTORUN devhe-mark STEP 2 STARTED\r\n')
        for number, boot, driver, other in ((1, first, 'SLBOUNCE-DEVHE-MARK.EFI', 2), (2, second, 'SLBOUNCE-DEVHE.EFI', 1)):
            text = boot.serial
            self.assertIn(f'AUTORUN devhe-mark STEP {number} OF 2', text)
            self.assertNotIn(f'STEP {other} OF 2', text)
            self.assertEqual(text.count('AUTORUN-DUMMY DRIVER'), 1, 'slbounce is loaded once per boot')
            self.assertEqual(text.count('AUTORUN-DUMMY APP'), 1)
            self.assertIn(f'AUTORUN-DUMMY DRIVER {driver} OPTIONS=[]', text)
            self.assertIn(APP_LINE, text)
            self.assertLess(text.index('AUTORUN-DUMMY DRIVER'), text.index('AUTORUN-DUMMY APP'), 'load, then run')
            self.assertIn(f'CMD1: load SP11\\{driver}', text)
            self.assertIn(f'FS0:\\> load SP11\\{driver}', text, 'the Shell echoes the commands it runs')
            self.assertIn('FS0:\\> SP11\\Q1N1.EFI --el2', text)
            self.assertNotIn('ALL STEPS DONE', text)
        self.assertEqual(second.serial.count("Image 'FS"), 1)

    def test_when_every_step_is_done_nothing_runs(self):
        _, second, third = self.run_of('two-step')
        self.assertIn('ALL STEPS DONE, run --clear to repeat', third.serial)
        self.assertNotIn('AUTORUN-DUMMY', third.serial)
        self.assertNotIn("Image 'FS", third.serial)
        self.assertNotIn('STEP 1 OF', third.serial)
        self.assertNotIn('STEP 2 OF', third.serial)
        self.assertEqual(third.stick.files, second.stick.files, 'the third boot wrote nothing')

    def test_a_hung_step_is_not_repeated(self):
        first, second = self.run_of('hung')
        self.assertIn('AUTORUN-DUMMY APP TESTS.EFI', first.serial)
        self.assertNotIn('FS0:\\> goto end', first.serial, 'the hung application never returned to the script')
        self.assertEqual(markers(first), ['SP11/DONE1.TXT'], 'the marker was written before the step ran')
        self.assertIn('AUTORUN devhe-mark STEP 2 OF 2', second.serial)
        self.assertNotIn('STEP 1 OF 2', second.serial)
        self.assertNotIn('AUTORUN-DUMMY APP TESTS.EFI', second.serial)
        self.assertIn('AUTORUN-DUMMY APP PROBE.EFI', second.serial)
        self.assertEqual(second.serial.count('AUTORUN-DUMMY APP'), 1)
        self.assertEqual(markers(second), ['SP11/DONE1.TXT', 'SP11/DONE2.TXT'])

    def test_a_missing_file_is_refused_and_no_step_runs(self):
        for scenario, missing in (('no-app', 'Q1N1.EFI'), ('no-slbounce', 'SLBOUNCE-DEVHE-MARK.EFI')):
            with self.subTest(scenario):
                (boot,) = self.run_of(scenario)
                self.assertIn(f'AUTORUN REFUSED: SP11\\{missing} is missing, nothing was run', boot.serial)
                self.assertNotIn('AUTORUN-DUMMY', boot.serial)
                self.assertNotIn("Image 'FS", boot.serial)
                self.assertNotIn('Writing SP11', boot.serial)
                self.assertEqual(markers(boot), [], 'a refused step leaves no marker')
                self.assertEqual(boot.ended, 'idle')

    def test_esc_at_the_countdown_skips_the_script(self):
        (boot,) = self.run_of('esc')
        self.assertEqual(boot.sent, (('Press ESC in 5', 'esc'),))
        self.assertIn('Press ESC in 5 seconds', boot.serial)
        self.assertNotIn('Press ESC in 4 seconds', boot.serial, 'the countdown ended at the key')
        self.assertNotIn('AUTORUN', boot.serial)
        self.assertEqual(markers(boot), [])
        self.assertTrue(boot.serial.rstrip(' ').endswith('Shell>'), boot.serial[-200:])

    def test_ctrl_c_during_the_pause_cancels_the_step(self):
        (boot,) = self.run_of('ctrl-c')
        self.assertEqual(boot.sent, (('Writing SP11', 'ctrl-c'),))
        self.assertIn('AUTORUN devhe-mark STEP 1 OF 2', boot.serial)
        self.assertNotIn('AUTORUN-DUMMY', boot.serial)
        self.assertNotIn("Image 'FS", boot.serial)
        self.assertEqual(markers(boot), [], 'cancelled before the marker was written')
        self.assertEqual(boot.ended, 'idle')

    def test_the_stick_need_not_be_fs0_and_the_other_volume_is_left_alone(self):
        (boot,) = self.run_of('decoy')
        self.assertIn('FS1:\\> load SP11\\SLBOUNCE-DEVHE-MARK.EFI', boot.serial)
        self.assertNotIn('FS0:\\> load', boot.serial)
        self.assertIn(APP_LINE, boot.serial)
        self.assertEqual(markers(boot), ['SP11/DONE1.TXT'], 'the stick, not the first volume, got the marker')
        other = q.fatimg.read_fat16(self.decoy_image.read_bytes())
        self.assertEqual(other.files, {'DECOY.TXT': b'another volume\r\n'}, 'the other volume was written')

    def test_a_stick_that_cannot_be_written_stops_the_script_before_the_step(self):
        # The Shell itself stops the script at the failed redirect, so the script's own refusal line is not reached here.
        (boot,) = self.run_of('read-only')
        self.assertIn('AUTORUN devhe-mark STEP 1 OF 2', boot.serial)
        self.assertIn('Unable to redirect file.', boot.serial)
        self.assertIn('Script Error Status: Invalid Parameter', boot.serial)
        self.assertNotIn('AUTORUN-DUMMY', boot.serial)
        self.assertNotIn("Image 'FS", boot.serial)
        self.assertEqual(markers(boot), [])
        self.assertEqual(boot.ended, 'idle')

    def test_a_failed_load_is_not_detected_so_the_application_still_runs(self):
        # The limit docs/RUNNING.md states: under this Shell `load` leaves %lasterror% at 0x0 when the driver fails to
        # start, so the script has nothing to test. This pins that observation; a Shell that reported it would break it.
        (boot,) = self.run_of('bad-load')
        self.assertIn('AUTORUN-DUMMY DRIVER SLBOUNCE-DEVHE-MARK.EFI', boot.serial)
        self.assertIn('error in StartImage: Load Error', boot.serial)
        self.assertNotIn(' loaded at ', boot.serial)
        self.assertIn(APP_LINE, boot.serial)
        self.assertEqual(markers(boot), ['SP11/DONE1.TXT'])

    def test_without_the_volume_selection_the_plan_is_not_found_and_nothing_runs(self):
        (boot,) = self.run_of('no-volume')
        self.assertIn('AUTORUN REFUSED: SP11\\PLAN.TXT not found on the Shell volume, nothing was run', boot.serial)
        self.assertNotIn('STEP 1 OF', boot.serial)
        self.assertNotIn('AUTORUN-DUMMY', boot.serial)
        self.assertEqual(markers(boot), [])
        self.assertEqual(boot.ended, 'idle')

    def test_the_survey_image_is_run_with_el2_after_the_load_like_q1n1(self):
        # Q1N1-STK.EFI is the first command name with a hyphen that the Shell runs from the script, and an exact 8.3
        # name (a short directory entry), where the other names on the stick are long ones.
        (boot,) = self.run_of('survey')
        text = boot.serial
        self.assertIn('AUTORUN devhe-mark: 1 step, one per boot.', text)
        self.assertIn('CMD1: load SP11\\SLBOUNCE-DEVHE-MARK.EFI', text)
        self.assertIn('CMD2: SP11\\Q1N1-STK.EFI --el2', text)
        self.assertIn('FS0:\\> load SP11\\SLBOUNCE-DEVHE-MARK.EFI', text)
        self.assertIn('FS0:\\> SP11\\Q1N1-STK.EFI --el2', text)
        self.assertIn(SURVEY_LINE, text)
        self.assertEqual(text.count('AUTORUN-DUMMY DRIVER'), 1, 'slbounce is loaded once per boot')
        self.assertEqual(text.count('AUTORUN-DUMMY APP'), 1)
        self.assertLess(text.index('AUTORUN-DUMMY DRIVER'), text.index('AUTORUN-DUMMY APP'), 'load, then run')
        self.assertEqual(markers(boot), ['SP11/DONE1.TXT'])
        self.assertEqual(boot.stick.files['SP11/DONE1.TXT'], b'AUTORUN devhe-mark STEP 1 STARTED\r\n')
        self.assertEqual(boot.ended, 'idle')

    def test_after_remove_the_stick_starts_the_shell_with_no_script(self):
        (boot,) = self.run_of('removed')
        self.assertIn('Press ESC in 5 seconds to skip startup.nsh', boot.serial)
        self.assertNotIn('AUTORUN', boot.serial)
        self.assertEqual([p for p in boot.stick.files if p.lower().endswith('startup.nsh')], [])
        self.assertEqual(markers(boot), [])
        self.assertTrue(boot.serial.rstrip(' ').endswith('Shell>'), boot.serial[-200:])


class ShellPinTests(unittest.TestCase):
    """No pinned Shell means SKIP, a different file means FAIL, and only the pinned file runs (nothing unpinned boots)."""

    UNIT = ROOT / 'build' / 'test-sp11-autorun-qemu-unit'

    def setUp(self):
        shutil.rmtree(self.UNIT, ignore_errors=True)
        self.UNIT.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.UNIT, ignore_errors=True)
        self.shell = self.UNIT / 'shell.efi'
        self.shell.write_bytes(b'stand-in for the pinned shell')
        self.pin = (len(b'stand-in for the pinned shell'), stage.sha256_bytes(b'stand-in for the pinned shell'))

    def test_no_file_skips_and_never_passes(self):
        for candidates in ([], [None, '', '  '], [self.UNIT / 'absent.efi'], [self.UNIT]):
            with self.assertRaises(unittest.SkipTest, msg=repr(candidates)):
                pinned_shell(candidates, self.pin)

    def test_the_pinned_file_is_accepted(self):
        self.assertEqual(pinned_shell([None, self.shell], self.pin), self.shell)

    def test_a_file_that_is_not_the_pinned_one_fails_instead_of_skipping_or_running(self):
        for data in (b'stand-in for the pinned shelL', b'stand-in for the pinned shell ', b'', b'x'):
            self.shell.write_bytes(data)
            with self.assertRaises(AssertionError, msg=repr(data)):
                pinned_shell([self.shell], self.pin)

    def test_the_real_pin_is_the_one_of_the_boot_media_pin_file(self):
        entry = json.loads(stage.DEFAULT_PINS.read_text(encoding='utf-8'))['shell']
        self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(entry['size'], 1105920)


class LongNameImageTests(unittest.TestCase):
    """The FAT16 writer of sp11_autorun_qemu, judged by the independent reader of probes/uefi/host/fatimg.py. These
    need no emulator, so they run (and can fail) even where the scenarios above SKIP."""

    FILES = {
        'EFI/BOOT/BOOTAA64.EFI': b'shell' * 1000, 'EFI/BOOT/startup.nsh': b'echo hi\r\n' * 300,
        'SP11/SLBOUNCE-DEVHE-MARK.EFI': b'a' * 4097, 'SP11/SLBOUNCE-DEVHE.EFI': b'b' * 10, 'SP11/SLBOUNCE-DRYRUN.EFI': b'c',
        'SP11/SLBOUNCE.EFI': b'd' * 2048, 'SP11/MANIFEST.SHA256': b'e' * 70, 'SP11/README.TXT': b'f' * 3000,
        'SP11/Mixed Case Name With Spaces.txt': b'g', 'TCBLAUNCH.EXE': b'h' * 5000, 'EMPTY.TXT': b'',
        'SP11/Q1N1-STK.EFI': b'j' * 33,
        'SP11/' + 'long-' * 40 + '.bin': b'i' * 20,
    }

    def test_what_goes_in_comes_out_through_the_independent_reader(self):
        image = q.LongNameFat16Image()
        for path, data in self.FILES.items():
            image.add_file(path, data)
        tree = q.fatimg.read_fat16(image.to_bytes())
        self.assertEqual(tree.files, self.FILES)
        self.assertEqual(tree.dirs, {'EFI', 'EFI/BOOT', 'SP11'})

    def test_names_that_share_a_short_prefix_get_distinct_aliases(self):
        image = q.LongNameFat16Image()
        names = {f'SP11/SLBOUNCE-VARIANT-{n}.EFI': bytes([n]) * (n + 1) for n in range(12)}
        for path, data in names.items():
            image.add_file(path, data)
        self.assertEqual(q.fatimg.read_fat16(image.to_bytes()).files, names)

    def test_the_image_is_deterministic_and_refuses_what_it_cannot_store(self):
        def build():
            image = q.LongNameFat16Image()
            for path, data in self.FILES.items():
                image.add_file(path, data)
            return image.to_bytes()
        self.assertEqual(build(), build())
        for bad in ('', 'dir/', 'caf\u00e9.txt', 'a\\b.txt'):
            with self.assertRaises(q.fatimg.FatError):
                q.LongNameFat16Image().add_file(bad, b'x')


if __name__ == '__main__':
    unittest.main()
