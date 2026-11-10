# SPDX-License-Identifier: MIT
"""Shared machinery for the SP11 probe's test suites.

tools/test-uefi-probe.py is the one command that runs the tests. It imports
every probes/uefi/test/suite_<area>.py module, collects the unittest.TestCase
subclasses defined in it and reports one PASS/SKIP/FAIL line per case. A suite
imports this module first (that puts probes/uefi/host on sys.path) and then
whatever host helpers it needs:

    import uefi_testkit as kit
    import checks, fatimg, peinfo, screen          # the helpers in probes/uefi/host

Adding tests means adding a suite module that derives from kit.Base. Two
optional hooks are read from each suite module:

    start_background(tests) -> finish    called once before the run with the list
                                         of selected test instances; returns a
                                         callable that is called after the run.
                                         suite_qemu uses it to boot the QEMU
                                         guests early so they overlap other tests.

Cases that need a built image call images(); cases that need an emulator guest
use suite_qemu.scenario(). Scratch files go below WORK, which the runner
empties before the run and removes afterwards (UEFI_PROBE_KEEP=1 keeps it).
Nothing in the test tree opens a serial port, a block device or a USB device.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOST_DIR = ROOT / "probes" / "uefi" / "host"
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))

import peinfo  # noqa: E402
import qemu_probe  # noqa: E402
import screen as screenlib  # noqa: E402


def _load_build_script():
    path = ROOT / "tools" / "build-uefi-probe.py"
    spec = importlib.util.spec_from_file_location("build_uefi_probe", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while it executes
    spec.loader.exec_module(module)
    return module


bup = _load_build_script()


def _env_int(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


WORK = ROOT / "build" / "uefi" / "test-work"
KEEP = os.environ.get("UEFI_PROBE_KEEP") == "1"
SKIP_QEMU = os.environ.get("UEFI_PROBE_SKIP_QEMU") == "1"
QEMU_WORKERS = _env_int("UEFI_PROBE_QEMU_WORKERS", 3)

# ---- Small helpers ---------------------------------------------------------


def text_of(data):
    return data.decode("utf-8", "replace")


def tail(text, count=15):
    return "\n".join(text.splitlines()[-count:])


def run_cmd(argv, cwd=ROOT, env=None, timeout=240, input=None):
    """Runs argv without a shell; returns (status, stdout bytes, stderr bytes)."""
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), env=env, input=input,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def work_dir(tag):
    """A fresh scratch directory below WORK for one test."""
    path = WORK / "scratch" / tag
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


_SLOTS = {}
_SLOTS_GUARD = threading.Lock()


def once(key, build):
    """Runs build() once per key across threads; a failure is cached and re-raised."""
    with _SLOTS_GUARD:
        slot = _SLOTS.setdefault(key, {"lock": threading.Lock(), "done": False})
    with slot["lock"]:
        if not slot["done"]:
            try:
                slot["value"] = build()
            except Exception as exc:  # noqa: BLE001 - re-raised below, per caller
                slot["error"] = exc
            slot["done"] = True
    if "error" in slot:
        raise slot["error"]
    return slot["value"]


def tools():
    def discover():
        try:
            return bup.Tools.discover()
        except bup.ToolchainMissing as exc:
            raise unittest.SkipTest(f"toolchain missing ({exc})")
    return once("tools", discover)


def images():
    """All five variants, built once with their objects kept: {name: BuildResult}."""
    def build():
        built = {}
        for name, variant in bup.VARIANTS.items():
            try:
                out_dir = bup.variant_out_dir(variant, WORK / "images")
                built[name] = bup.build_variant(variant, out_dir, tools(), keep_objects=True)
            except bup.BuildFailed as exc:
                raise AssertionError(f"building variant {name} failed:\n{exc}")
        return built
    return once("images", build)


def object_path(name, stem):
    return images()[name].image.parent / "obj" / name / f"{stem}.obj"


def objdump_path():
    def find():
        for directory in (*os.environ.get("PATH", "").split(os.pathsep), *bup.TOOL_DIRS):
            candidate = Path(directory) / "llvm-objdump"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise unittest.SkipTest("llvm-objdump not found")
    return once("objdump", find)


def disassembly(path, relocations=False):
    return once(("disasm", str(path), relocations),
                lambda: peinfo.disassemble(objdump_path(), path, relocations))


# ---- The native logic test (tools/test-uefi-probe-logic.c) -----------------

NATIVE_SOURCE = ROOT / "tools" / "test-uefi-probe-logic.c"
SANITIZE_FLAGS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
                  "-fno-omit-frame-pointer"]
NATIVE_SECTIONS = ("crc8", "string builder", "font", "line format", "layout", "framebuffer",
                   "device path", "memory map", "decision tables", "system registers",
                   "acpi scan", "acpi scan (fuzz)", "smbios", "fadt and madt",
                   "register collection", "report", "dump caps", "dump overwrite",
                   "dump errors", "dump run")
_OK_SECTION = re.compile(r"^ok\s+(.+?)\s+(\d+) checks$")


def native_command():
    """The compile command from the C file's header comment, as suite.py reads it.

    That comment is the single copy of the build line: the suite runner and this
    test both parse it, so a test unit added to the build cannot be forgotten.
    """
    rows = [re.sub(r"^\s*(/\*+|\*+/?|//+)\s?", "", raw).replace("*/", "").strip()
            for raw in NATIVE_SOURCE.read_text(encoding="utf-8").splitlines()[:140]]
    for i, row in enumerate(rows):
        if re.search(r"(?:cc|clang)\s+-", row):
            command = row
            while command.endswith("\\") and i + 1 < len(rows):
                i += 1
                command = command[:-1].rstrip() + " " + rows[i]
            argv = shlex.split(command)
            if "&&" in argv:
                argv = argv[:argv.index("&&")]
            return argv
    raise AssertionError("tools/test-uefi-probe-logic.c has no compile command in its header")


def asan_env():
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=0"
    return env


def sanitizer_runtime_works(clang):
    exe = WORK / "sanitizer-probe"
    rc, _, _ = run_cmd([clang, *SANITIZE_FLAGS, "-x", "c", "-", "-o", exe],
                       input=b"int main(void) { return 0; }\n")
    if rc != 0:
        return False
    return run_cmd([exe], env=asan_env())[0] == 0


def native_exe(tag):
    """Builds the native test: tag 'plain' or 'asan' (ASan + UBSan, no recovery)."""
    def build():
        argv = native_command()
        clang = shutil.which("clang") or tools().clang
        exe = WORK / f"native-{tag}"
        exe.parent.mkdir(parents=True, exist_ok=True)
        argv[0] = clang
        argv[argv.index("-o") + 1] = str(exe)
        if tag == "asan":
            argv[1:1] = SANITIZE_FLAGS
        rc, out, err = run_cmd(argv)
        if rc != 0:
            if tag == "asan" and not sanitizer_runtime_works(clang):
                raise unittest.SkipTest("this clang has no usable sanitizer runtime")
            raise AssertionError(f"the native test does not compile ({tag} build):\n"
                                 + text_of(out + err))
        return exe
    return once(("native", tag), build)


def run_native(exe, *args):
    return run_cmd([exe, *args], env=asan_env())


def native_output_problems(rc, text):
    """What is wrong with the native test's output; empty when it is a clean pass.

    Expected sections must be a subset of those printed, so that a later change
    can add sections without editing this list.
    """
    bad = []
    if rc != 0:
        bad.append(f"exit status {rc}")
    seen = {}
    for row in text.splitlines():
        if row.startswith("FAIL"):
            bad.append(f"failure line: {row.strip()}")
        m = _OK_SECTION.match(row)
        if m:
            seen[m.group(1)] = int(m.group(2))
    for name in NATIVE_SECTIONS:
        if name not in seen:
            bad.append(f"section {name!r} did not report success")
        elif seen[name] < 10:
            bad.append(f"section {name!r} ran only {seen[name]} checks")
    if not re.search(r"^PASS: \d+ checks, 0 failures$", text, re.M):
        bad.append("no 'PASS: N checks, 0 failures' summary line")
    return bad


def font_dump_text():
    def dump():
        rc, out, err = run_native(native_exe("plain"), "--dump-font")
        if rc != 0:
            raise AssertionError(f"--dump-font exited {rc}:\n{text_of(err)}")
        return text_of(out)
    return once("font-dump", dump)


def font_table():
    """The probe's glyph bitmaps as the C code defines them, for the independent decoder."""
    return once("font", lambda: screenlib.load_font(font_dump_text()))


def qemu_assets():
    def find():
        try:
            return qemu_probe.find_assets()
        except qemu_probe.Unavailable as exc:
            raise unittest.SkipTest(f"QEMU or EDK2 firmware unavailable ({exc})")
    return once("assets", find)


@dataclasses.dataclass
class Layout:
    scale: int
    cols: int
    body_rows: int
    pages: int
    total_rows: int


_LAYOUT_LINE = re.compile(r"LAYOUT scale=(\d+) cols=(\d+) body_rows=(\d+) pages=(\d+) total_rows=(\d+)")


def render_page(width, height, nbody, page):
    """Renders one page with the C code (--dump-screen); returns (Image, Layout)."""
    rc, out, err = run_native(native_exe("plain"), "--dump-screen",
                              str(width), str(height), str(nbody), str(page))
    if rc != 0:
        raise AssertionError(f"--dump-screen {width} {height} {nbody} {page} exited {rc}:\n"
                             + text_of(err))
    m = _LAYOUT_LINE.search(text_of(err))
    if not m:
        raise AssertionError("--dump-screen printed no LAYOUT line on stderr:\n" + text_of(err))
    return screenlib.parse_ppm(out), Layout(*map(int, m.groups()))


# ---- Result reporting ------------------------------------------------------


def short_id(test):
    """'NativeTests.test_x' rather than 'suite_native.NativeTests.test_x'."""
    parts = test.id().split(".")
    return ".".join(parts[1:] if parts[0].startswith("suite_") or parts[0] == "__main__" else parts)


class Reporter(unittest.TestResult):
    """One line per test in the form suite.py counts: PASS, SKIP, FAIL or ERROR."""

    def __init__(self, out):
        super().__init__()
        self.out = out
        self.began = {}
        self.passed = 0

    def say(self, text):
        print(text, file=self.out, flush=True)

    def startTest(self, test):
        super().startTest(test)
        self.began[test.id()] = time.monotonic()

    def took(self, test):
        return time.monotonic() - self.began.get(test.id(), time.monotonic())

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed += 1
        self.say(f"PASS: {short_id(test)} ({self.took(test):.1f}s)")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.say(f"SKIP: {short_id(test)}: {' '.join(str(reason).split())}")

    def problem(self, label, test, err):
        self.say(f"{label}: {short_id(test)} ({self.took(test):.1f}s)")
        body = "".join(traceback.format_exception(*err)) if label == "ERROR" else str(err[1])
        for row in body.rstrip().splitlines() or ["(no message)"]:
            self.say(f"    | {row}")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.problem("FAIL", test, err)

    def addError(self, test, err):
        super().addError(test, err)
        self.problem("ERROR", test, err)


class Base(unittest.TestCase):
    def check(self, problems, context=""):
        """Fails with every problem at once, so one run shows all the disagreements."""
        if problems:
            self.fail("\n".join([*problems, *([context] if context else [])]))
