#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the SP11 EL2 state probe (probes/uefi/, tools/build-uefi-probe.py).

    python3 -B tools/test-uefi-probe.py                  run every suite
    python3 -B tools/test-uefi-probe.py NativeTests      one class, or Class.test_name

The probe is a UEFI application for the Surface Pro 11, so none of this runs
on that machine. The suites are modules in probes/uefi/test/:

    suite_native     the C logic tests (tools/test-uefi-probe-logic.c) with and
                     without sanitizers, plus the font and page renderers
    suite_build      reproducible builds, build ids, the exact flag set
    suite_image      PE structure and instruction audit of the built images
    suite_hosttools  FAT image writer/reader, line CRC, operator CLI, README and repo hygiene
    suite_dump       the independent dump verifier, against synthetic ACPI/SMBIOS
    suite_qemu       the real images under QEMU + EDK2, seven scenarios; the
                     virtual display and the virtual stick are checked against
                     host code that shares nothing with the probe
    suite_rotation   three more QEMU scenarios that start from a stick already
                     holding earlier dump runs (first free RUNnn, last slot, full)

QEMU is only a model of the device. A PASS there says the probe's logic, its
dump format and its drawing work against a standard firmware; it says nothing
about the Surface Pro 11's firmware, GOP, PSCI conduit or EL2 state.

Environment:
    UEFI_PROBE_SKIP_QEMU=1       skip the QEMU scenarios
    UEFI_PROBE_QEMU_WORKERS=N    guests booted in parallel (default 3)
    UEFI_PROBE_KEEP=1            keep build/uefi/test-work for inspection
    UEFI_PROBE_QEMU, UEFI_PROBE_EDK2_CODE, UEFI_PROBE_EDK2_VARS
                                 override the emulator and firmware lookup

A missing tool (clang, lld-link, QEMU, EDK2, fsck_msdos) is reported as a SKIP
line and does not fail the run. Every failure prints its problems on lines that
start with "    | ". The exit status is 0 only if nothing failed. The tests open
no serial port, block device or USB device and write only below build/.
"""
from __future__ import annotations

import importlib
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "probes" / "uefi" / "test"))

import uefi_testkit as kit  # noqa: E402

SUITES = ("suite_native", "suite_build", "suite_image", "suite_hosttools", "suite_dump",
          "suite_qemu", "suite_rotation")


def iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def load(names, suites):
    """Returns (modules, unittest.TestSuite) for the requested names, or exits with 2."""
    modules = [importlib.import_module(name) for name in suites]
    loader = unittest.defaultTestLoader
    if not names:
        return modules, unittest.TestSuite(loader.loadTestsFromModule(m) for m in modules)
    chosen = []
    for name in names:
        owner = name.split(".")[0]
        home = [m for m in modules if isinstance(getattr(m, owner, None), type)
                and issubclass(getattr(m, owner), kit.Base) and getattr(m, owner).__module__ == m.__name__]
        if not home:
            print(f"ERROR: no test class named {owner!r} in {', '.join(suites)}")
            sys.exit(2)
        try:
            chosen.append(loader.loadTestsFromName(name, home[0]))
        except (AttributeError, ImportError) as exc:
            print(f"ERROR: cannot load {name!r}: {exc}")
            sys.exit(2)
    return modules, unittest.TestSuite(chosen)


def main(argv, suites=SUITES):
    modules, suite = load(argv[1:], suites)
    selected = list(iter_tests(suite))
    if not selected:
        print("ERROR: no tests selected")
        return 2
    shutil.rmtree(kit.WORK, ignore_errors=True)
    kit.WORK.mkdir(parents=True, exist_ok=True)
    reporter = kit.Reporter(sys.stdout)
    started = time.monotonic()
    finishers = []
    try:
        for module in modules:
            hook = getattr(module, "start_background", None)
            if hook:
                finishers.append(hook(selected))
        suite.run(reporter)
    finally:
        for finish in finishers:
            if finish:
                finish()
        if not kit.KEEP:
            shutil.rmtree(kit.WORK, ignore_errors=True)
    failed = len(reporter.failures) + len(reporter.errors)
    print(f"Ran {reporter.testsRun} tests in {time.monotonic() - started:.1f}s: "
          f"{reporter.passed} passed, {len(reporter.skipped)} not run (see SKIP lines), "
          f"{failed} failed")
    return 0 if reporter.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
