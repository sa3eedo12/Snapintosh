#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the SP11 X2 test image: functional EL2 tests for gate X2.

    python3 tools/build-uefi-tests.py --soc x1e80100          # build/uefi/sp11-el2-tests.efi

The image is the EL2 state probe (probes/uefi) plus the tests of
probes/uefi/tests. It is built only when this tool is run, with its own
compile-time flag (-DPROBE_TESTS_IMAGE=1); the probe's build never produces it
and nothing ever starts it by itself. The operator copies it to the USB stick
and starts it after the X1 probe has shown that the machine is at EL2; see
docs/SP11-X2-TESTS.md.

Images (the hardware image is the only one that is shipped):
  hardware     build/uefi/sp11-el2-tests.efi                 for the Surface Pro 11
  model        build/uefi/test/sp11-el2-tests-model.efi      emulator runs: labels say MODEL,
                                                             short timers, holds the final screen,
                                                             adds the machinery self-tests
  model-fatal  build/uefi/test/sp11-el2-tests-model-fatal.efi  as model, then makes one unguarded
                                                             bad access to prove the fatal page
  model-fatal-pre  build/uefi/test/sp11-el2-tests-model-fatal-pre.efi
                                                             as model, but the unguarded bad access
                                                             is made before ExitBootServices, to
                                                             prove the fatal page does not depend
                                                             on the console

--soc is required for the hardware image: the NV2 record carries it, and the
monitor's gate refuses a record for another SoC, so it is an explicit choice and
never a default. The descriptor (config/boards/sp11.json by default) supplies
the values the tests cross-check ACPI against; the emulator images use none.

--require-mmio-map makes T05 to T07 read a device window only if the UEFI memory
map lists all of it as MMIO. Without it a window the map does not mention is
read as well (and one the map calls RAM, firmware or anything but MMIO is always
refused): the memory map of the QEMU machine lists no GIC, ECAM or SMMU window at
all, and whether the Surface Pro 11 firmware does is for the first hardware run
to show (the probe's memmap.txt). The option changes the build id.

The build needs clang and lld-link only. Like the probe's, it is reproducible:
building twice gives identical bytes, and the 12-hex-digit build id shown on
every page is a hash of the variant, its defines, the compiler flags and every
source.

Exit status: 0 built, 1 build or reproducibility failure, 2 bad usage,
3 toolchain missing (nothing is written).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "probes" / "uefi"
TESTS_DIR = SRC_DIR / "tests"
DEFAULT_OUT = ROOT / "build" / "uefi"
DEFAULT_DESCRIPTOR = ROOT / "config" / "boards" / "sp11.json"


def _load(module_name, filename):
    path = ROOT / "tools" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while it executes
    spec.loader.exec_module(module)
    return module


bup = _load("build_uefi_probe", "build-uefi-probe.py")

TESTS_SOURCES = (
    "acpix.c", "decode.c", "env.c", "guard.c", "hook.c", "nv2.c", "nv2rec.c", "regs.c",
    "report_x.c", "stick.c", "sys_aarch64.c", "sysstate.c", "t_gic.c", "t_misc.c", "t_pcie.c",
    "t_smmu.c", "tests_types.c", "vectors_aarch64.c",
)
SOC_CODES = {"x1e80100": 1, "x2e-96-100": 2}
BUILD_ID_SALT = b"sp11-el2-tests-build-id-v1\0"

# The probe's entry point has statics that only its own post-exit stage uses;
# with that stage replaced they are unused, and -Werror would refuse them.
PROBE_MAIN_EXTRA = ["-Wno-unused-function", "-Wno-unused-variable"]


@dataclass(frozen=True)
class Variant:
    name: str
    image: str
    defines: tuple
    shipped: bool
    uses_descriptor: bool


_BASE = ("PROBE_TESTS_IMAGE=1",)
_MODEL = _BASE + ("TESTS_MODEL_BUILD=1", "PROBE_FAST_TEST=1", "TESTS_NO_RESET=1", "TESTS_SELFTEST=1")
VARIANTS = {
    v.name: v
    for v in (
        Variant("hardware", "sp11-el2-tests.efi", _BASE, True, True),
        Variant("model", "sp11-el2-tests-model.efi", _MODEL, False, False),
        Variant("model-fatal", "sp11-el2-tests-model-fatal.efi", _MODEL + ("TESTS_SELFTEST_FATAL=1",),
                False, False),
        Variant("model-fatal-pre", "sp11-el2-tests-model-fatal-pre.efi",
                _MODEL + ("TESTS_SELFTEST_FATAL_PRE=1",), False, False),
    )
}
SHIPPED = tuple(v.name for v in VARIANTS.values() if v.shipped)


class DescriptorError(Exception):
    pass


def descriptor_c_source(path: Path) -> tuple:
    """(C source text, sha256 of the descriptor file) for a board descriptor.

    The descriptor is validated with tools/board_descriptor.py, so a malformed or
    unknown-schema file is refused. Only the values the tests cross-check are
    emitted; every one is 0 (unknown) when the descriptor has a null there.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import board_descriptor  # noqa: PLC0415 - needs the path set up above

    data = Path(path).read_bytes()
    try:
        board = board_descriptor.loads(data.decode("utf-8"))
    except (board_descriptor.BoardDescriptorError, UnicodeDecodeError) as exc:
        raise DescriptorError(f"{path.name}: {exc}") from exc

    def hexval(v):
        return f"0x{v:x}ull" if v else "0"

    cpus = list(board.mpidrs or ())[:32]
    pcie_smmu = next((base for name, kind, base, size in (board.smmus or ())
                      if kind == "smmu-v3" and "pcie" in name), 0)
    gicd = board.gicd or (0, 0)
    gicr = board.gicr or (0, 0, 0)
    its = board.its[1] if board.its and board.its[0] else 0
    lines = [
        "/* SPDX-License-Identifier: MIT */",
        f"/* Generated by tools/build-uefi-tests.py from the descriptor of board {board.board_id!r}.",
        " * Cross-check values only; every one carries the provenance of that descriptor and a 0",
        " * means unknown. Build output: do not commit. */",
        '#include "desc.h"',
        "",
        "const tests_desc tests_desc_data = {",
        f"    .n_cpus = {len(cpus)},",
        "    .mpidr = {" + ", ".join(hexval(m) for m in cpus) + "},",
        f"    .gicd_base = {hexval(gicd[0])},",
        f"    .gicr_base = {hexval(gicr[0])},",
        f"    .gicr_stride = {hexval(gicr[1])},",
        f"    .gicr_size = {hexval(gicr[2])},",
        f"    .its_base = {hexval(its)},",
        f"    .pcie_smmu_base = {hexval(pcie_smmu)},",
        f"    .timer_hz = {hexval(board.timer_hz or 0)},",
        f"    .ipa_bits = {int(board.ipa_bits or 0)},",
        "};",
        "",
    ]
    return "\n".join(lines), hashlib.sha256(data).hexdigest()


def sources_of(variant: Variant) -> list:
    """(repo-relative path, kind) for every compiled file, descriptor source aside."""
    files = [f"probes/uefi/{n}" for n in bup.BASE_SOURCES]
    files += [f"probes/uefi/tests/{n}" for n in TESTS_SOURCES]
    if not variant.uses_descriptor:
        files.append("probes/uefi/tests/desc_none.c")
    return files


def digests(variant: Variant) -> dict:
    """Digest of every file that can change the image, keyed by repo-relative path."""
    names = set(sources_of(variant))
    names |= {f"probes/uefi/{p.name}" for p in SRC_DIR.glob("*.h")}
    names |= {f"probes/uefi/tests/{p.name}" for p in TESTS_DIR.glob("*.h")}
    return {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in sorted(names)}


def compute_build_id(variant: Variant, soc: str, descriptor_digest: str) -> str:
    h = hashlib.sha256(BUILD_ID_SALT)
    for part in (variant.name, soc, descriptor_digest, *variant.defines, *bup.CFLAGS, *bup.LDFLAGS,
                 *PROBE_MAIN_EXTRA):
        h.update(part.encode() + b"\0")
    for rel, digest in digests(variant).items():
        h.update(f"{rel}\0{digest}\0".encode())
    return h.hexdigest()[:12].upper()


def compile_flags(variant: Variant, soc: str, build_id: str) -> list:
    flags = [*bup.CFLAGS, f"-ffile-prefix-map={ROOT}=.", "-Iprobes/uefi", "-Iprobes/uefi/tests",
             f'-DPROBE_BUILD_ID="{build_id}"', f"-DTESTS_SOC={SOC_CODES[soc]}"]
    flags += [f"-D{d}" for d in variant.defines]
    return flags


@dataclass
class BuildResult:
    variant: str
    image: Path
    size: int
    sha256: str
    build_id: str
    manifest: Path


def build_variant(variant: Variant, soc: str, descriptor: Path, out_dir: Path, tools,
                  keep_objects=False) -> BuildResult:
    desc_text, desc_digest = ("", "none")
    if variant.uses_descriptor:
        desc_text, desc_digest = descriptor_c_source(descriptor)
    build_id = compute_build_id(variant, soc, desc_digest)
    flags = compile_flags(variant, soc, build_id)
    obj_dir = out_dir / "obj" / variant.name
    shutil.rmtree(obj_dir, ignore_errors=True)
    obj_dir.mkdir(parents=True)

    jobs = [(rel, flags + (PROBE_MAIN_EXTRA if rel.endswith("/probe_main.c") else []))
            for rel in sources_of(variant)]
    if variant.uses_descriptor:
        gen = obj_dir / "desc_data.c"
        gen.write_text(desc_text, encoding="utf-8")
        jobs.append((str(gen.relative_to(ROOT)) if gen.is_relative_to(ROOT) else str(gen), flags))

    def compile_one(job):
        rel, extra = job
        stem = Path(rel).stem
        obj = obj_dir / f"{stem}.obj"
        bup.run([tools.clang, *extra, "-c", rel, "-o", str(obj)], f"compile {rel}")
        return obj

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, os.cpu_count() or 1)) as pool:
        objs = list(pool.map(compile_one, jobs))

    image = out_dir / variant.image
    bup.run([tools.lld_link, "/nologo", *bup.LDFLAGS, f"/out:{image}", *map(str, objs)], "link")
    data = image.read_bytes()
    result = BuildResult(variant.name, image, len(data), hashlib.sha256(data).hexdigest(), build_id,
                         image.with_suffix(".manifest.json"))
    manifest = {
        "schema": 1,
        "variant": variant.name,
        "image": variant.image,
        "size": result.size,
        "sha256": result.sha256,
        "build_id": build_id,
        "soc": soc,
        "descriptor_sha256": desc_digest,
        "defines": [*variant.defines, f"TESTS_SOC={SOC_CODES[soc]}"],
        "target": bup.TARGET,
        "cflags": bup.CFLAGS,
        "ldflags": bup.LDFLAGS,
        "tools": tools.versions,
        "sources": digests(variant),
    }
    result.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if not keep_objects:
        shutil.rmtree(obj_dir, ignore_errors=True)
        try:
            obj_dir.parent.rmdir()
        except OSError:
            pass
    return result


def variant_out_dir(variant: Variant, out: Path) -> Path:
    path = out if variant.shipped else out / "test"
    path.mkdir(parents=True, exist_ok=True)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variant", action="append", choices=[*VARIANTS, "all"],
                    help="variant to build (repeatable); default: the hardware image")
    ap.add_argument("--soc", choices=sorted(SOC_CODES),
                    help="SoC recorded in the NV2 record; required for the hardware image")
    ap.add_argument("--descriptor", type=Path, default=DEFAULT_DESCRIPTOR,
                    help="board descriptor for the hardware image (default: %(default)s)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--keep-objects", action="store_true", help="keep the per-variant .obj files")
    ap.add_argument("--require-mmio-map", action="store_true",
                    help="T05 to T07 read a device window only if the UEFI memory map lists all of "
                         "it as MMIO (default: windows the map does not mention are read too)")
    ap.add_argument("--verify-reproducible", action="store_true",
                    help="build each variant twice into separate directories and compare")
    ap.add_argument("--print-build-id", action="store_true",
                    help="print the build id of each variant and exit (needs no toolchain)")
    ap.add_argument("--json", action="store_true", help="print results as JSON")
    args = ap.parse_args(argv)

    names = list(SHIPPED) if not args.variant else args.variant
    if "all" in names:
        names = list(VARIANTS)
    variants = [VARIANTS[n] for n in dict.fromkeys(names)]
    if args.require_mmio_map:
        variants = [replace(v, defines=v.defines + ("TESTS_REQUIRE_MMIO_IN_MAP=1",)) for v in variants]
    if any(v.shipped for v in variants) and args.soc is None:
        print("REFUSED: --soc is required for the hardware image (x1e80100 for the Surface Pro 11); "
              "the NV2 record carries it and the monitor's gate refuses a record for another SoC",
              file=sys.stderr)
        return 2
    soc = args.soc or "x1e80100"

    try:
        desc_digest = {v.name: (descriptor_c_source(args.descriptor)[1] if v.uses_descriptor else "none")
                       for v in variants}
    except (DescriptorError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    if args.print_build_id:
        for v in variants:
            print(f"{v.name}: {compute_build_id(v, soc, desc_digest[v.name])}")
        return 0

    try:
        tools = bup.Tools.discover()
    except bup.ToolchainMissing as exc:
        print(f"REFUSED: {exc}; install LLVM (clang) and LLD (lld-link)", file=sys.stderr)
        return 3

    results, failed = [], False
    try:
        for v in variants:
            out_dir = variant_out_dir(v, args.out)
            first = build_variant(v, soc, args.descriptor, out_dir, tools, args.keep_objects)
            results.append(first)
            if args.verify_reproducible:
                second_dir = out_dir / "repro"
                second = build_variant(v, soc, args.descriptor, second_dir, tools)
                same = second.sha256 == first.sha256
                failed |= not same
                print(f"{v.name}: reproducible={'yes' if same else 'NO'}", file=sys.stderr)
                shutil.rmtree(second_dir, ignore_errors=True)
    except bup.BuildFailed as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([{"variant": r.variant, "image": r.image.name, "size": r.size,
                           "sha256": r.sha256, "build_id": r.build_id} for r in results], indent=2))
    else:
        for r in results:
            print(f"{r.image.name}: {r.size} bytes sha256={r.sha256} build_id={r.build_id}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
