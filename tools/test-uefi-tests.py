#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the SP11 X2 EL2 tests image (probes/uefi/tests/, tools/build-uefi-tests.py).

    python3 -B tools/test-uefi-tests.py                  run every suite
    python3 -B tools/test-uefi-tests.py XNativeTests     one class, or Class.test_name

The image is a UEFI application for the Surface Pro 11 that decides kill
criterion K1 (NV2), so none of this runs on that machine and none of it can say
how the machine behaves. What it does say, per suite (probes/uefi/tests/test/suite_x.py):

    XNativeTests   every decision of the image on the host, with all-ones, all-zeros and
                   contradictory register images that must never give GO, plus the record
                   round trip through tools/nv2_record.py
    XBuildTests    --soc refusal, variants and flags, reproducible builds
    XAuditTests    the image's privileged instructions, its vector tables and its sources
    XQemuTests     the model image under QEMU with EDK2

It reuses the probe's test kit (probes/uefi/test/uefi_testkit.py) with its own
work directory, build/uefi/tests-work.

Environment (the probe's switches apply):
    UEFI_PROBE_SKIP_QEMU=1       skip the QEMU scenarios
    UEFI_PROBE_KEEP=1            keep build/uefi/tests-work for inspection
    UEFI_PROBE_QEMU, UEFI_PROBE_EDK2_CODE, UEFI_PROBE_EDK2_VARS
                                 override the emulator and firmware lookup

A missing tool (clang, lld-link, QEMU, EDK2) is reported as a SKIP line, never as a
pass, and does not fail the run. Every failure prints its problems on lines that
start with "    | ". The exit status is 0 only if nothing failed. The tests open no
serial port, block device or USB device and write only below build/.
"""
from __future__ import annotations

import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("probes/uefi/test", "probes/uefi/host", "probes/uefi/tests/test", "probes/uefi/tests/host",
            "tools"):
    sys.path.insert(0, str(ROOT / sub))

import uefi_testkit as kit  # noqa: E402

kit.WORK = ROOT / "build" / "uefi" / "tests-work"

import suite_x  # noqa: E402


def iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def load(names):
    loader = unittest.defaultTestLoader
    if not names:
        return unittest.TestSuite(loader.loadTestsFromModule(suite_x))
    chosen = []
    for name in names:
        try:
            chosen.append(loader.loadTestsFromName(name, suite_x))
        except (AttributeError, ImportError) as exc:
            print(f"ERROR: cannot load {name!r}: {exc}")
            sys.exit(2)
    return unittest.TestSuite(chosen)


def main(argv):
    suite = load(argv[1:])
    if not list(iter_tests(suite)):
        print("ERROR: no tests selected")
        return 2
    shutil.rmtree(kit.WORK, ignore_errors=True)
    kit.WORK.mkdir(parents=True, exist_ok=True)
    reporter = kit.Reporter(sys.stdout)
    started = time.monotonic()
    try:
        suite.run(reporter)
    finally:
        if not kit.KEEP:
            shutil.rmtree(kit.WORK, ignore_errors=True)
    failed = len(reporter.failures) + len(reporter.errors)
    print(f"Ran {reporter.testsRun} tests in {time.monotonic() - started:.1f}s: "
          f"{reporter.passed} passed, {len(reporter.skipped)} not run (see SKIP lines), "
          f"{failed} failed")
    return 0 if reporter.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
