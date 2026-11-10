#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the SP11 EL2 probe UEFI applications.

Shipped images (written to build/uefi/):
  sp11-el2-probe.efi       passive probe; ResetSystem after the on-screen report
  sp11-el2-probe-psci.efi  the same plus one PSCI_VERSION and one CPU_ON (opt-in)

Test-only images go to build/uefi/test/ and are never shipped. The fast
variants shorten the on-screen timers so an emulator run takes seconds.

The build is a clang cross-compile to the COFF target and an lld-link link, the
same pairing the q1n1 UEFI stage uses. It needs clang and lld-link only: no
EDK2 tree and no firmware file. The build id embedded in the image is the first
12 hex digits of a hash over the variant name, its defines, the compiler flags
and the contents of the sources, so it changes exactly when the image should.
Nothing time-, path- or user-dependent reaches the image, and /Brepro replaces
the PE timestamp by a content hash, which makes two builds byte-identical.

Exit status: 0 built, 1 build or reproducibility failure, 3 toolchain missing
(the build refuses to run rather than produce a partial image).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "probes" / "uefi"
DEFAULT_OUT = ROOT / "build" / "uefi"

TARGET = "aarch64-pc-windows-msvc"
BASE_SOURCES = (
    "acpi.c", "cpu_aarch64.c", "dump.c", "fbtext.c", "font6x8.c", "logic.c",
    "mem.c", "probe_main.c", "report.c", "sysreg.c",
)
PSCI_SOURCES = ("psci.c",)

# -mgeneral-regs-only keeps FP/SIMD opcodes out: the image runs with whatever
# CPTR_EL2 the firmware left, and an FP trap would hide the report.
# -fno-builtin and the no_builtin attributes in logic.c stop the compiler from
# turning the byte loops of memcpy/memset into calls to themselves.
# -mno-incremental-linker-compatible zeroes the COFF object timestamp, which
# the msvc triple otherwise fills with the wall clock, so objects (kept with
# --keep-objects) compare byte for byte as well as the linked images.
CFLAGS = [
    f"--target={TARGET}", "-std=c11", "-O2", "-ffreestanding", "-fno-builtin",
    "-fno-stack-protector", "-fshort-wchar", "-mgeneral-regs-only",
    "-mno-outline-atomics", "-mno-incremental-linker-compatible",
    "-Wall", "-Wextra", "-Werror",
]
LDFLAGS = [
    "/subsystem:efi_application", "/entry:efi_main", "/nodefaultlib",
    "/machine:arm64", "/dynamicbase", "/Brepro",
]
TOOL_DIRS = (
    "/opt/homebrew/opt/llvm/bin", "/opt/homebrew/opt/lld/bin", "/opt/homebrew/bin",
    "/usr/local/opt/llvm/bin", "/usr/local/opt/lld/bin", "/usr/local/bin",
)
BUILD_ID_SALT = b"sp11-el2-probe-build-id-v1\0"


class ToolchainMissing(Exception):
    pass


class BuildFailed(Exception):
    pass


@dataclass(frozen=True)
class Variant:
    name: str
    image: str
    defines: tuple
    psci: bool
    shipped: bool

    @property
    def sources(self):
        return BASE_SOURCES + (PSCI_SOURCES if self.psci else ())


VARIANTS = {
    v.name: v
    for v in (
        Variant("default", "sp11-el2-probe.efi", (), False, True),
        Variant("psci", "sp11-el2-probe-psci.efi", ("PROBE_PSCI=1",), True, True),
        Variant("fast", "sp11-el2-probe-fast.efi", ("PROBE_FAST_TEST=1",), False, False),
        Variant("fast-psci", "sp11-el2-probe-fast-psci.efi",
                ("PROBE_FAST_TEST=1", "PROBE_PSCI=1"), True, False),
        Variant("fast-noreset", "sp11-el2-probe-fast-noreset.efi",
                ("PROBE_FAST_TEST=1", "PROBE_NO_RESET=1"), False, False),
    )
}
SHIPPED = tuple(v.name for v in VARIANTS.values() if v.shipped)


@dataclass
class Tools:
    clang: str
    lld_link: str
    versions: dict

    @staticmethod
    def discover() -> "Tools":
        def find(name):
            for d in TOOL_DIRS:
                p = Path(d) / name
                if p.is_file() and os.access(p, os.X_OK):
                    return str(p)
            return shutil.which(name)

        clang, lld = find("clang"), find("lld-link")
        missing = [n for n, p in (("clang", clang), ("lld-link", lld)) if not p]
        if missing:
            raise ToolchainMissing("missing tool(s): " + ", ".join(missing))

        def first_line(cmd):
            out = subprocess.run(cmd, capture_output=True, text=True, check=False)
            text = (out.stdout or out.stderr).strip()
            return text.splitlines()[0] if text else "unknown"

        return Tools(clang, lld, {
            "clang": first_line([clang, "--version"]),
            "lld-link": first_line([lld, "--version"]),
        })


@dataclass
class BuildResult:
    variant: str
    image: Path
    size: int
    sha256: str
    build_id: str
    manifest: Path


def source_digests(variant: Variant) -> dict:
    """Digest of every file that can change the image, keyed by repo-relative path."""
    names = sorted(set(variant.sources) | {p.name for p in SRC_DIR.glob("*.h")})
    return {
        f"probes/uefi/{n}": hashlib.sha256((SRC_DIR / n).read_bytes()).hexdigest()
        for n in names
    }


def compute_build_id(variant: Variant) -> str:
    h = hashlib.sha256(BUILD_ID_SALT)
    for part in (variant.name, *variant.defines, *CFLAGS, *LDFLAGS):
        h.update(part.encode() + b"\0")
    for rel, digest in source_digests(variant).items():
        h.update(f"{rel}\0{digest}\0".encode())
    return h.hexdigest()[:12].upper()


def compile_flags(variant: Variant, build_id: str) -> list:
    flags = [*CFLAGS, f"-ffile-prefix-map={ROOT}=.", "-Iprobes/uefi",
             f'-DPROBE_BUILD_ID="{build_id}"']
    flags += [f"-D{d}" for d in variant.defines]
    return flags


def run(cmd, what):
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise BuildFailed(f"{what} failed (exit {proc.returncode}):\n{proc.stdout}{proc.stderr}")
    return proc.stdout + proc.stderr


def build_variant(variant: Variant, out_dir: Path, tools: Tools, keep_objects=False) -> BuildResult:
    build_id = compute_build_id(variant)
    flags = compile_flags(variant, build_id)
    obj_dir = out_dir / "obj" / variant.name
    shutil.rmtree(obj_dir, ignore_errors=True)
    obj_dir.mkdir(parents=True)

    def compile_one(name):
        obj = obj_dir / (Path(name).stem + ".obj")
        run([tools.clang, *flags, "-c", f"probes/uefi/{name}", "-o", str(obj)], f"compile {name}")
        return obj

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, os.cpu_count() or 1)) as pool:
        objs = list(pool.map(compile_one, variant.sources))

    image = out_dir / variant.image
    run([tools.lld_link, "/nologo", *LDFLAGS, f"/out:{image}", *map(str, objs)], "link")
    data = image.read_bytes()
    result = BuildResult(variant.name, image, len(data), hashlib.sha256(data).hexdigest(),
                         build_id, image.with_suffix(".manifest.json"))
    manifest = {
        "schema": 1,
        "variant": variant.name,
        "image": variant.image,
        "size": result.size,
        "sha256": result.sha256,
        "build_id": build_id,
        "defines": list(variant.defines),
        "target": TARGET,
        "cflags": CFLAGS,
        "ldflags": LDFLAGS,
        "tools": tools.versions,
        "sources": source_digests(variant),
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
                    help="variant to build (repeatable); default: the shipped variants")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--keep-objects", action="store_true", help="keep the per-variant .obj files")
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

    if args.print_build_id:
        for v in variants:
            print(f"{v.name}: {compute_build_id(v)}")
        return 0

    try:
        tools = Tools.discover()
    except ToolchainMissing as exc:
        print(f"REFUSED: {exc}; install LLVM (clang) and LLD (lld-link)", file=sys.stderr)
        return 3

    results, failed = [], False
    try:
        for v in variants:
            out_dir = variant_out_dir(v, args.out)
            first = build_variant(v, out_dir, tools, args.keep_objects)
            results.append(first)
            if args.verify_reproducible:
                second_dir = out_dir / "repro"
                second = build_variant(v, second_dir, tools)
                same = second.sha256 == first.sha256
                failed |= not same
                print(f"{v.name}: reproducible={'yes' if same else 'NO'}", file=sys.stderr)
                shutil.rmtree(second_dir, ignore_errors=True)
    except BuildFailed as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([{"variant": r.variant, "image": str(r.image.relative_to(ROOT))
                           if r.image.is_relative_to(ROOT) else r.image.name,
                           "size": r.size, "sha256": r.sha256, "build_id": r.build_id}
                          for r in results], indent=2))
    else:
        for r in results:
            print(f"{r.image.name}: {r.size} bytes sha256={r.sha256} build_id={r.build_id}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
