# SPDX-License-Identifier: MIT
"""BuildTests: tools/build-uefi-probe.py.

Covers the build id, reproducibility (same sources, same image, at any checkout
location), the refusal paths and the host-identity scan of every artefact. The
build id is not embedded as a string in the images (the compiler folds it into
instruction immediates), so the QEMU suites check it through the BEGIN marker
instead of scanning the image.
"""
from __future__ import annotations

import contextlib
import dataclasses
import getpass
import hashlib
import io
import json
import re
import shutil
import socket
import sys
from pathlib import Path
from unittest import mock

from uefi_testkit import Base, ROOT, WORK, bup, images, run_cmd, text_of

GENERIC_NAMES = {"root", "user", "admin", "runner", "ubuntu", "localhost", "build", "agent"}


def identity_needles():
    """Strings that must not appear in any artefact: kind -> text (values are never printed)."""
    needles = {"checkout path": str(ROOT), "home directory": str(Path.home()),
               "unix user-directory prefix": "/Us" + "ers/", "unix home prefix": "/ho" + "me/",
               "windows drive prefix": "C:" + "\\"}
    names = {}
    for kind, getter in (("user name", getpass.getuser), ("host name", socket.gethostname)):
        try:
            names[kind] = getter()
        except (OSError, KeyError, ImportError):
            continue
    if "host name" in names:
        names["short host name"] = names["host name"].split(".")[0]
    for kind, value in names.items():
        if len(value) >= 5 and value.lower() not in GENERIC_NAMES:
            needles[kind] = value
    return needles


def contains_text(data, needle):
    return any(needle.encode(enc) in data for enc in ("utf-8", "utf-16-le"))


def tree_copy(tag):
    """Only the build inputs, at a root that differs from the checkout in name and depth."""
    root = WORK / "copies" / tag / "nested" / "deeper"
    shutil.rmtree(root.parent.parent, ignore_errors=True)
    (root / "tools").mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "build-uefi-probe.py", root / "tools")
    source = root / "probes" / "uefi"
    source.mkdir(parents=True)
    for path in (ROOT / "probes" / "uefi").iterdir():
        if path.is_file() and path.suffix in (".c", ".h"):
            shutil.copy2(path, source)
    return root


def build_cli(root, *args):
    rc, out, err = run_cmd([sys.executable, "-B", root / "tools" / "build-uefi-probe.py", *args],
                           cwd=root)
    return rc, text_of(out), text_of(err)


def sha_from(stdout):
    m = re.search(r"sha256=([0-9a-f]{64})", stdout)
    return m.group(1) if m else None


def append_to(root, name, text):
    path = root / "probes" / "uefi" / name
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


class BuildTests(Base):
    def test_variants_build_with_distinct_consistent_ids(self):
        problems, ids = [], {}
        for name, built in images().items():
            variant = bup.VARIANTS[name]
            data = built.image.read_bytes()
            manifest = json.loads(built.manifest.read_text(encoding="utf-8"))
            ids[name] = built.build_id
            checks_ = (
                (re.fullmatch(r"[0-9A-F]{12}", built.build_id), "build id is not 12 upper-case hex digits"),
                (built.build_id == bup.compute_build_id(variant), "build id differs from compute_build_id"),
                (manifest["build_id"] == built.build_id, "manifest build id differs"),
                (manifest["sha256"] == hashlib.sha256(data).hexdigest() == built.sha256,
                 "manifest or result hash differs from the image"),
                (manifest["size"] == len(data) == built.size, "manifest or result size differs"),
                (manifest["target"] == bup.TARGET and manifest["variant"] == name, "manifest identity"),
                (built.image.name == variant.image, "image file name"),
            )
            problems += [f"{name}: {text}" for ok, text in checks_ if not ok]
        if len(set(ids.values())) != len(ids):
            problems.append(f"two variants share a build id: {ids}")
        self.check(problems)

    def test_shipped_images_are_the_two_documented_names(self):
        shipped = {v.image for v in bup.VARIANTS.values() if v.shipped}
        self.assertEqual(shipped, {"sp11-el2-probe.efi", "sp11-el2-probe-psci.efi"})
        # Test-only variants must not land beside the shipped images by default.
        for name, built in images().items():
            expected_dir = WORK / "images" if bup.VARIANTS[name].shipped else WORK / "images" / "test"
            self.assertEqual(built.image.parent, expected_dir, name)

    def test_verify_reproducible_flag(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bup.main(["--variant", "default", "--out", str(WORK / "cli-repro"),
                           "--verify-reproducible"])
        problems = []
        if rc != 0:
            problems.append(f"exit status {rc}: {err.getvalue().strip()}")
        if "default: reproducible=yes" not in err.getvalue():
            problems.append("stderr does not say 'default: reproducible=yes'")
        if sha_from(out.getvalue()) != images()["default"].sha256:
            problems.append("the CLI build differs from the library build")
        if (WORK / "cli-repro" / "repro").exists():
            problems.append("the comparison build directory was left behind")
        self.check(problems)

    def test_nonreproducible_build_is_reported(self):
        first = images()["default"]
        second = dataclasses.replace(first, sha256="0" * 64)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(bup, "build_variant", side_effect=[first, second]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bup.main(["--variant", "default", "--out", str(WORK / "cli-nonrepro"),
                           "--verify-reproducible"])
        self.assertEqual(rc, 1)
        self.assertIn("default: reproducible=NO", err.getvalue())

    def test_build_is_independent_of_the_checkout_location(self):
        root = tree_copy("relocated")
        rc, out, err = build_cli(root, "--variant", "default", "--out", root / "out")
        self.assertEqual(rc, 0, err)
        self.assertEqual(sha_from(out), images()["default"].sha256,
                         "the same sources built under a different root give a different image")
        rc, out, err = build_cli(root, "--print-build-id", "--variant", "default")
        self.assertEqual(out.strip(), f"default: {images()['default'].build_id}", err)

    def test_changed_source_changes_the_id_and_the_image(self):
        root = tree_copy("changed")
        append_to(root, "logic.c", "\n/* edited */\n")
        rc, out, err = build_cli(root, "--variant", "default", "--out", root / "out")
        self.assertEqual(rc, 0, err)
        self.assertNotEqual(re.search(r"build_id=(\w+)", out).group(1), images()["default"].build_id)
        self.assertNotEqual(sha_from(out), images()["default"].sha256)

    def test_every_input_that_changes_the_image_changes_the_id(self):
        # Sources are covered above. The defines, the variant name and the tool flags decide
        # what is compiled too, so an id that ignored one would name two different images alike.
        base = bup.VARIANTS["default"]
        want = bup.compute_build_id(base)
        cases = {
            "an extra define": (dataclasses.replace(base, defines=(*base.defines, "PROBE_EXTRA=1")),
                                bup.CFLAGS, bup.LDFLAGS),
            "the variant name": (dataclasses.replace(base, name=base.name + "-x"),
                                 bup.CFLAGS, bup.LDFLAGS),
            "a compiler flag": (base, [*bup.CFLAGS, "-DPROBE_EXTRA_FLAG"], bup.LDFLAGS),
            "a linker flag": (base, bup.CFLAGS, [*bup.LDFLAGS, "/extra"]),
        }
        problems = []
        for what, (variant, cflags, ldflags) in cases.items():
            with mock.patch.object(bup, "CFLAGS", cflags), mock.patch.object(bup, "LDFLAGS", ldflags):
                if bup.compute_build_id(variant) == want:
                    problems.append(f"the build id ignores {what}")
        self.assertEqual(bup.compute_build_id(base), want, "the id of an unchanged variant moved")
        self.check(problems)

    def test_compile_errors_and_warnings_fail_the_build(self):
        problems = []
        for tag, text, why in (("syntax", "\nint broken( {\n", "a syntax error"),
                               ("warning", "\nstatic void unused_helper(void) {}\n",
                                "an unused function (-Werror)")):
            root = tree_copy(tag)
            append_to(root, "logic.c", text)
            rc, out, err = build_cli(root, "--variant", "default", "--out", root / "out")
            if rc != 1 or "BUILD FAILED" not in err:
                problems.append(f"{why}: exit {rc}, stderr {err.strip()[-120:]!r}")
            if (root / "out" / "sp11-el2-probe.efi").exists():
                problems.append(f"{why}: an image was written anyway")
        self.check(problems)

    def test_no_host_identity_in_artifacts(self):
        needles = identity_needles()
        problems = []
        for name, built in images().items():
            targets = [built.image, built.manifest, *sorted(built.image.parent.glob(f"obj/{name}/*.obj"))]
            for path in targets:
                data = path.read_bytes()
                problems += [f"{name}: {path.suffix or path.name} contains the {kind}"
                             for kind, needle in needles.items() if contains_text(data, needle)]
        self.assertTrue(any(images()["default"].image.parent.glob("obj/default/*.obj")),
                        "no kept objects were scanned")
        self.check(problems)

    def test_missing_toolchain_is_refused_and_nothing_is_written(self):
        out_dir = WORK / "cli-refused"
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(bup.Tools, "discover", side_effect=bup.ToolchainMissing("no clang")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bup.main(["--variant", "default", "--out", str(out_dir)])
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED:", err.getvalue())
        self.assertEqual(list(out_dir.rglob("*.efi")) if out_dir.exists() else [], [])

    def test_build_id_listing_needs_no_toolchain(self):
        out = io.StringIO()
        with mock.patch.object(bup.Tools, "discover", side_effect=bup.ToolchainMissing("no clang")), \
                contextlib.redirect_stdout(out):
            rc = bup.main(["--print-build-id", "--variant", "all"])
        want = [f"{name}: {bup.compute_build_id(v)}" for name, v in bup.VARIANTS.items()]
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().splitlines(), want)

    def test_identity_scan_detects_planted_names(self):
        needles = identity_needles()
        for kind in ("checkout path", "unix user-directory prefix", "unix home prefix"):
            self.assertIn(kind, needles)
        problems = []
        for kind, needle in needles.items():
            for encoding in ("utf-8", "utf-16-le"):
                planted = b"\0\0pad" + needle.encode(encoding) + b"pad\0"
                if not contains_text(planted, needle):
                    problems.append(f"the {kind} is not found when stored as {encoding}")
            if contains_text(b"nothing to see here", needle):
                problems.append(f"the {kind} matches unrelated bytes")
        self.check(problems)
