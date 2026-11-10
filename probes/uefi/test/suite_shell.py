# SPDX-License-Identifier: MIT
"""The probe started from the pinned UEFI Shell, as the X1 flow starts it (QEMU virt with EDK2).

The X1 stick boots the pinned UEFI Shell as BOOTAA64.EFI and the operator types the probe's
path at the Shell prompt. The Shell gives every application it starts its own console output
handle, so the console handle the probe receives is no longer the one the firmware console
draws through. Every other emulator scenario boots the probe directly and never meets that
case; the first rehearsal of the stick found the probe refusing every framebuffer there.

  via-shell   the fast default image, started by a STARTUP.NSH that types the operator's
              command (SP11\\PROBE.EFI); only the test stick has that file, the real one has none

The scenario registers itself in suite_qemu.SCENARIOS and ORDER on import and reuses the checks
of the direct boots; the one difference it expects is the framebuffer rule, SEL=SAME instead
of SEL=CONSOLE. A pass shows the probe's control flow under the emulator's firmware and the
Shell build pinned in tools/sp11-boot-media-pins.json (OBSERVED in QEMU); the console stack of
the Surface Pro 11's firmware is UNKNOWN until X1.

The Shell is a downloaded third-party binary that is not in the repository. SP11_UEFI_SHELL
names it; the file is checked against the pinned size and sha256 before it is booted. When the
variable is unset or names no file, every case of the scenario is SKIPPED, never passed. A
file that is there but is not the pinned one fails, so nothing unpinned ever runs.
"""

from __future__ import annotations

import hashlib
import json
import types
import unittest
from unittest import mock

import fatimg
import suite_qemu as qemu
import synth_dump as sd
import uefi_testkit as kit

FRAMEBUFFER_FIELDS = ("W", "H", "STRIDE", "FMT", "BASE", "SIZE")

SCENARIOS = {s.name: s for s in (
    qemu.Scenario("via-shell", "fast", ends_itself=True, shell=True, sel="SAME"),
)}
qemu.SCENARIOS.update(SCENARIOS)
qemu.ORDER = tuple(dict.fromkeys(qemu.ORDER + tuple(SCENARIOS)))


# ---------------------------------------------------------------- checks (lists of problems)

def launcher_problems(files, staged):
    """The launch files are on the stick byte for byte as the harness staged them."""
    return [f"{path} {'is missing' if path not in files else 'differs from what was staged'}"
            for path, data in staged.items() if files.get(path) != data]


def framebuffer_problems(shell_gop, direct_gop):
    """After a Shell launch the probe must draw into the framebuffer a direct boot selects.

    A direct boot selects by the console handle, so that framebuffer is the one the firmware
    console draws into; the same-framebuffer rule has to land on it.
    """
    problems = [f"GOP {key} is {shell_gop.get(key)!r} after the Shell launch but "
                f"{direct_gop.get(key)!r} after a direct boot"
                for key in FRAMEBUFFER_FIELDS
                if shell_gop.get(key) is None or shell_gop.get(key) != direct_gop.get(key)]
    if direct_gop.get("SEL") != "CONSOLE":
        problems.append(f"the direct boot reports SEL={direct_gop.get('SEL')!r}, not CONSOLE")
    return problems


def shell_stick_problems(view, staged=None):
    """The stick holds the pinned Shell, the launch files and exactly one complete dump run."""
    if staged is None:
        staged = kit.shell_launch_files(kit.images()[view.scn.variant].image)
    problems = qemu.stick_problems(view, tuple(staged))
    if view.result.stick is not None:
        problems += launcher_problems(view.result.stick.files, staged)
    pin = kit.shell_pin_problem(view.outcome.efi)
    return problems + ([f"{sd.BOOT_FILE} is {pin}"] if pin else [])


# ---------------------------------------------------------------- the QEMU scenario

class QemuViaShellTests(qemu._Markers, qemu._Screen, qemu._Facts, qemu._Registers,
                        qemu._Footers, qemu._Ending, qemu.QemuCase):
    scenario_name = "via-shell"

    def test_the_shell_started_the_probe(self):
        self.run_check(lambda view: kit.shell_launch_problems(view.result.serial))

    def test_stick_holds_the_pinned_shell_the_launch_files_and_one_complete_run(self):
        self.run_check(shell_stick_problems)

    def test_summary_agrees_with_the_serial_markers(self):
        self.run_check(qemu.summary_problems)

    def test_probe_conclusions_match_an_independent_reading_of_the_dump(self):
        self.run_check(qemu.reading_problems)

    def test_the_framebuffer_is_the_one_a_direct_boot_selects(self):
        view = self.view()
        direct = qemu.scenario("default")
        if direct.result.failure:
            self.fail(f"the direct boot to compare with did not complete: {direct.result.failure}")
        self.check(framebuffer_problems(view.marker("GOP"), qemu.View(direct).marker("GOP")),
                   "last serial lines:\n" + kit.tail(view.result.serial, 8))


# ---------------------------------------------------------------- helper tests (no emulator)

class ShellPinTests(kit.Base):
    """SP11_UEFI_SHELL: no file means SKIP, a different file means FAIL, only the pinned file runs."""

    DATA = b"stand-in for the pinned shell"

    def staged(self, data=None):
        path = kit.work_dir("shell-pin") / "shell.efi"
        path.write_bytes(self.DATA if data is None else data)
        return path

    def pin(self, data=None):
        data = self.DATA if data is None else data
        return len(data), hashlib.sha256(data).hexdigest()

    def test_the_real_pin_is_the_shell_entry_of_the_boot_media_pin_file(self):
        pins = json.loads(kit.SHELL_PINS.read_text(encoding="utf-8"))
        size, digest = kit.shell_pin()
        self.assertEqual((size, digest), (pins["shell"]["size"], pins["shell"]["sha256"]))
        self.assertRegex(digest, r"[0-9a-f]{64}")
        self.assertNotEqual(digest, pins["slbounce_reference"]["sha256"])

    def test_an_absent_shell_skips_and_never_passes(self):
        work = kit.work_dir("shell-absent")
        for env in ({}, {kit.SHELL_ENV: ""}, {kit.SHELL_ENV: "  "},
                    {kit.SHELL_ENV: str(work / "nothing.efi")}, {kit.SHELL_ENV: str(work)}):
            with self.assertRaises(unittest.SkipTest, msg=repr(env)) as caught:
                kit.pinned_shell(env, self.pin())
            self.assertIn(kit.SHELL_ENV, str(caught.exception))

    def test_the_pinned_file_is_accepted(self):
        path = self.staged()
        self.assertEqual(kit.pinned_shell({kit.SHELL_ENV: str(path)}, self.pin()), path)
        self.assertEqual(kit.shell_pin_problem(self.DATA, self.pin()), "")

    def test_a_file_that_is_not_the_pinned_one_fails_instead_of_skipping_or_running(self):
        size, digest = self.pin()
        flipped = bytes([self.DATA[0] ^ 1]) + self.DATA[1:]
        cases = (("another file of the pinned size", flipped, (size, digest)),
                 ("a longer file", self.DATA + b"x", (size, digest)),
                 ("a shorter file", self.DATA[:-1], (size, digest)),
                 ("the right hash under another size", self.DATA, (size + 1, digest)),
                 ("an empty file", b"", (size, digest)))
        problems = []
        for what, data, pin in cases:
            try:
                kit.pinned_shell({kit.SHELL_ENV: str(self.staged(data))}, pin)
                problems.append(f"{what}: accepted")
            except unittest.SkipTest as skipped:
                problems.append(f"{what}: skipped instead of failing ({skipped})")
            except AssertionError as caught:
                if "not the pinned UEFI Shell" not in str(caught):
                    problems.append(f"{what}: fails without saying it is not the pinned file ({caught})")
            if not kit.shell_pin_problem(data, pin):
                problems.append(f"{what}: shell_pin_problem finds nothing wrong")
        self.check(problems)


class ShellLaunchChecksTests(kit.Base):
    """The scenario's checks fail on the damage they exist to catch."""

    BEGIN = "SP11PROBE BEGIN BUILD=0123456789AB VARIANT=DEFAULT RESET=ON FAST=1"
    SHELL = ("\x1b[2J" + kit.SHELL_BANNER + " v2.2\r\nShell> fs0:\r\nFS0:\\> SP11\\PROBE.EFI\r\n")

    def test_launch_log_must_show_the_shell_before_the_probe(self):
        self.assertEqual(kit.shell_launch_problems(self.SHELL + self.BEGIN + "\r\n"), [])
        spaced = self.SHELL.replace("PROBE", "\x1b[0mPROBE")  # escape sequences inside a word
        self.assertEqual(kit.shell_launch_problems(spaced + self.BEGIN), [])
        for what, log in (
                ("a direct boot", self.BEGIN + "\r\n"),
                ("no banner", self.SHELL.replace(kit.SHELL_BANNER, "Shell") + self.BEGIN),
                ("no echo", self.SHELL.replace(kit.LAUNCH_ECHO, "") + self.BEGIN),
                ("a banner after the probe started", self.BEGIN + "\r\n" + self.SHELL),
                ("no probe at all", self.SHELL)):
            self.assertNotEqual(kit.shell_launch_problems(log), [], what)

    def test_launch_files_must_match_what_was_staged(self):
        staged = kit.shell_launch_files(kit.ROOT / "probes" / "uefi" / "README.md")
        files = dict(staged)
        self.assertEqual(launcher_problems(files, staged), [])
        for path in staged:
            missing = {p: d for p, d in files.items() if p != path}
            changed = {**files, path: files[path] + b"x"}
            self.assertTrue(any("is missing" in p for p in launcher_problems(missing, staged)), path)
            self.assertTrue(any("differs" in p for p in launcher_problems(changed, staged)), path)

    def test_staged_names_are_fat_writable_and_the_script_runs_the_staged_probe(self):
        probe = kit.ROOT / "probes" / "uefi" / "README.md"
        staged = kit.shell_launch_files(probe)
        self.assertEqual(set(staged), {"STARTUP.NSH", "SP11/PROBE.EFI"})
        self.assertEqual(staged["STARTUP.NSH"], kit.STARTUP_NSH)
        self.assertEqual(staged["SP11/PROBE.EFI"], probe.read_bytes())
        stick = fatimg.Fat16Image()
        for path, data in staged.items():
            stick.add_file(path, data)  # raises unless every component is an upper-case 8.3 name
        lines = kit.STARTUP_NSH.decode("ascii").split("\r\n")
        self.assertEqual(lines[0].lower(), "fs0:")
        self.assertEqual(lines[1].replace("\\", "/"), "SP11/PROBE.EFI")
        self.assertIn(lines[1].replace("\\", "/"), staged)
        self.assertEqual(lines[1], kit.LAUNCH_ECHO)
        self.assertEqual(lines[2:], [""])

    def test_a_different_framebuffer_or_rule_is_reported(self):
        direct = {"W": "800", "H": "600", "STRIDE": "800", "FMT": "BGRX", "BASE": "0x7C7A0000",
                  "SIZE": "0x300000", "SEL": "CONSOLE"}
        shell = {**direct, "SEL": "SAME"}
        self.assertEqual(framebuffer_problems(shell, direct), [])
        for key, value in (("W", "640"), ("H", "480"), ("STRIDE", "832"), ("FMT", "RGBX"),
                           ("BASE", "0x7C000000"), ("SIZE", "0x200000")):
            self.assertTrue(any(f"GOP {key} " in p for p in framebuffer_problems(
                {**shell, key: value}, direct)), key)
        for key in FRAMEBUFFER_FIELDS:
            self.assertNotEqual(framebuffer_problems({k: v for k, v in shell.items() if k != key},
                                                     {k: v for k, v in direct.items() if k != key}),
                                [], key)
        self.assertNotEqual(framebuffer_problems(shell, {**direct, "SEL": "SAME"}), [])
        self.assertNotEqual(framebuffer_problems(shell, {}), [])


class ShellStickTests(kit.Base):
    """qemu.stick_problems(view, extra) lets the launch files through and nothing else, and
    shell_stick_problems adds the pin of the Shell and the launch files as staged."""

    SHELL = b"stand-in for the shell"
    STAGED = {"STARTUP.NSH": kit.STARTUP_NSH, "SP11/PROBE.EFI": b"stand-in for the probe"}
    DIRS = {"EFI", "EFI/BOOT", "SP11", "SP11DUMP", sd.RUN_DIR}

    def view(self, files=None, dirs=None, efi=None):
        built, marker = sd.build()
        stick_files = {**built, sd.BOOT_FILE: self.SHELL, **self.STAGED}
        stick_files.update(files or {})
        prefix = sd.RUN_DIR + "/"
        run = {p[len(prefix):]: d for p, d in stick_files.items() if p.startswith(prefix)}
        stick = types.SimpleNamespace(files=stick_files, dirs=set(self.DIRS if dirs is None else dirs))
        return types.SimpleNamespace(
            result=types.SimpleNamespace(stick=stick, stick_error=""), files=run,
            summary={"dump_complete_so_far": "yes"}, marker=lambda tag: marker,
            outcome=types.SimpleNamespace(efi=self.SHELL if efi is None else efi))

    def test_the_launch_files_are_tolerated_only_when_named(self):
        self.assertEqual(qemu.stick_problems(self.view(), tuple(self.STAGED)), [])
        found = qemu.stick_problems(self.view())
        self.assertTrue(any("stray files" in p and "STARTUP.NSH" in p for p in found), found)
        self.assertTrue(any("unexpected directories" in p and "SP11" in p for p in found), found)

    def test_everything_else_beside_the_dump_is_still_stray(self):
        extra = tuple(self.STAGED)
        for what, view in (
                ("a stray file", self.view({"NOTES.TXT": b"x"})),
                ("a stray file in the launch directory", self.view({"SP11/EXTRA.EFI": b"x"})),
                ("a stray directory", self.view(dirs=self.DIRS | {"JUNK"})),
                ("a stray dump directory", self.view(dirs=self.DIRS | {"SP11DUMP/RUN02"}))):
            found = qemu.stick_problems(view, extra)
            self.assertTrue(any(w in p for p in found for w in ("stray files", "unexpected directories")),
                            f"{what}: {found}")

    def test_the_booted_image_must_still_be_on_the_stick(self):
        found = qemu.stick_problems(self.view(efi=b"another image"), tuple(self.STAGED))
        self.assertTrue(any("is not the image that was booted" in p for p in found), found)
        found = qemu.stick_problems(self.view({sd.BOOT_FILE: b"another image"}), tuple(self.STAGED))
        self.assertTrue(any("is not the image that was booted" in p for p in found), found)

    def test_the_stick_must_hold_the_pinned_shell_and_the_launch_files_as_staged(self):
        pin = (len(self.SHELL), hashlib.sha256(self.SHELL).hexdigest())
        other = b"another shell"
        with mock.patch.object(kit, "shell_pin", return_value=pin):
            self.assertEqual(shell_stick_problems(self.view(), self.STAGED), [])
            found = shell_stick_problems(self.view({sd.BOOT_FILE: other}, efi=other), self.STAGED)
            self.assertTrue(any("not the pinned UEFI Shell" in p for p in found), found)
            for path in self.STAGED:
                changed = shell_stick_problems(self.view({path: b"changed"}), self.STAGED)
                self.assertTrue(any(path in p and "differs" in p for p in changed), (path, changed))
                dropped = self.view()
                del dropped.result.stick.files[path]
                found = shell_stick_problems(dropped, self.STAGED)
                self.assertTrue(any(path in p and "is missing" in p for p in found), (path, found))
