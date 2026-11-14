# SPDX-License-Identifier: MIT
"""Suites of the `declared` variant of the SP11 X2 tests image
(probes/uefi/tests/ebs_ranges.[ch], the gated step in probes/uefi/probe_main.c).

    XEbsRangesTests      the table builder against the Python model of the fork's sweep
                         (tools/sp11_ebs_sweep_model.py), against the fork's own validator and
                         sweep when SLBOUNCE_SP11_DIR names the checkout (SKIP otherwise), and
                         deliberately broken copies of the builder that the C tests must reject
    XDeclaredBuildTests  the variants and their flags, what the images contain, and the proof that
                         with the gate off nothing of the image changes
    XDeclaredAuditTests  the source audit's pin on the one place a UEFI variable is written
    XDeclaredQemuTests   the model image of the variant under QEMU + EDK2, and three test-only
                         builds of it that each make one step of the declaration fail on purpose

The image has not run in this variant on the Surface Pro 11: a PASS here says the table, the
check and the gate behave as specified against a host model and a standard firmware, nothing
about the unit's firmware or about slbounce running on it.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import io
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path

import audit
import checks
import peinfo
import sp11_ebs_sweep_model as oracle
import suite_x as sx
import uefi_testkit as kit
from uefi_testkit import Base, ROOT, run_cmd, tail, text_of

SLBOUNCE_ENV = "SLBOUNCE_SP11_DIR"
BASELINE_ENV = "UEFI_TESTS_BASELINE_REV"
GATE = "TESTS_DECLARE_EBS_RANGES"
MAIN_C = "probes/uefi/probe_main.c"
HOOK_C = "probes/uefi/tests/hook.c"
EBS_C = "probes/uefi/tests/ebs_ranges.c"
DECLARED_VARIANTS = ("declared", "model-declared")
UNGATED_VARIANTS = ("hardware", "model", "model-fatal", "model-fatal-pre")
PAGE = 4096
TOP = 1 << 64
SWEPT_TYPES = (1, 2, 3, 4, 5, 6, 9)
TABLE = struct.Struct("<QIIII16QQ")  # magic, version, count, chunk_cap, reserved, 8 ranges, checksum
VERDICTS = ("OK", "SIZE_ZERO", "UNALIGNED", "WRAPS", "NO_MAP", "MAP_TOO_LARGE", "MAP_INVALID",
            "NOT_IN_ONE_DESCRIPTOR", "WRONG_TYPE", "NOT_WRITE_BACK")
# The refusals the fork has a name for, and what it calls each. The builder's other refusals
# (alignment, wrap, the size and shape of the map) are this image's own and have none there.
SLBOUNCE_NAMES = {"SIZE_ZERO": "BAD_RANGE", "NOT_IN_ONE_DESCRIPTOR": "NOT_CONTAINED",
                  "WRONG_TYPE": "WRONG_TYPE", "NOT_WRITE_BACK": "NOT_WRITEBACK"}


def fnv1a(data):
    h = 0xCBF29CE484222325
    for b in data:
        h = ((h ^ b) * 0x100000001B3) & (TOP - 1)
    return h


# ---- Cases for the differential run ----------------------------------------------------------

def expected_verdict(base, size, descs):
    """What the builder must say about [base, base + size) over descs = [(type, phys, pages, attr)].

    The refusals before the model are the image's own (ebs_ranges.h); the rest is judged by
    tools/sp11_ebs_sweep_model.py itself, so the C code and the model share no logic.
    """
    if size == 0:
        return "SIZE_ZERO"
    if base % PAGE:
        return "UNALIGNED"
    if base + size >= TOP:
        return "WRAPS"
    if not descs:
        return "NO_MAP"
    if len(descs) > 512:
        return "MAP_TOO_LARGE"
    if any(pages == 0 or phys + pages * PAGE >= TOP for _, phys, pages, _ in descs):
        return "MAP_INVALID"
    model = [oracle.Descriptor(kind, phys, pages, attr) for kind, phys, pages, attr in descs]
    try:
        oracle.scoped_sweep(model, [(base, size)], chunk_cap=1 << 20)
    except oracle.SweepRefused as exc:
        for needle, verdict in (("covered by", "NOT_IN_ONE_DESCRIPTOR"), ("descriptor type", "WRONG_TYPE"),
                                ("non-write-back", "NOT_WRITE_BACK")):
            if needle in str(exc):
                return verdict
        raise AssertionError(f"the model refused for a reason this test does not know: {exc}")
    return "OK"


def fixed_cases():
    """The situations the design names, written out, as the C tests assert them."""
    image = (1, 0x80000000, 0x190, 0x100F)
    neighbour = (4, 0x80190000, 0x10, 0x100F)
    cases = [
        (0x80000000, 0x190000, [image, neighbour]),                              # the image descriptor
        (0x80001000, 0x2000, [image]),
        (0x8018F000, 0x2000, [image, neighbour]),                                # spans two descriptors
        (0x80000000, 0x190000, [(0, 0x80000000, 0x190, 0x100F)]),                # reserved, as a framebuffer
        (0x80000000, 0x190000, [(7, 0x80000000, 0x190, 0x100F)]),                # conventional memory
        (0x80000000, 0x190000, [(1, 0x80000000, 0x190, 0x7)]),                   # no write-back bit
        (0x60000000, 0x1000, [image]),                                           # in no descriptor
        (0x11000, 0x1000, [(2, 0x10000, 0x10, 0xF), (4, 0x10000, 0x10, 0xF)]),   # two holders
        (0x80000000, 0, [image]),
        (0x80000800, 0x1000, [image]),
        (0xFFFFFFFFFFFFF000, 0x2000, [image]),
        (0x80000000, 0x1000, []),
        (0x80000000, 0x1000, [(1, 0x80000000, 0, 0xF)]),                         # a descriptor with no pages
        (0x80000000, 0x1000, [image, (2, 0xFFFFFFFFFFFFF000, 2, 0xF)]),          # one whose end wraps
    ]
    spread = [(2, 0x100000 + i * 0x10000, 0x10, 0xF) for i in range(600)]
    return cases + [(0x100000, 0x1000, spread[:512]),                       # the fork's limit: still fine
                    (0x100000, 0x1000, spread[:513]),                       # one over it
                    (0x100000, 0x1000, spread),
                    (0x100000 + 599 * 0x10000, 0x1000, spread)]             # the range in the last of many


def random_cases(count, seed=0x5011EB5):
    rng = random.Random(seed)
    attrs = (0, 0x7, 0x8, 0xF, 0x100F, 0x800000000000100F, 0x8000000000000000, 0x1000)
    cases = []
    for _ in range(count):
        descs, cursor = [], rng.randrange(1 << 20) * PAGE
        for i in range(rng.choice((0, 1, 1, 2, 3, 5, 8, 13, 24, 40))):
            pages = rng.randrange(1, 65)
            kind = rng.choice(SWEPT_TYPES) if rng.random() < 0.6 else rng.randrange(16)
            if i and rng.random() < 0.125:
                phys = descs[rng.randrange(i)][1] + rng.randrange(8) * PAGE   # overlaps an earlier one
            else:
                cursor += rng.randrange(3) * PAGE                             # 0: adjacent to the last
                phys, cursor = cursor, cursor + pages * PAGE
            descs.append((kind, phys, pages, rng.choice(attrs)))
        pick = rng.random()
        if descs and pick < 0.5:
            _, phys, pages, _ = rng.choice(descs)
            base = phys + rng.randrange(pages) * PAGE
            size = rng.randrange(1, phys + pages * PAGE - base + 2 * PAGE + 1)
            if rng.random() < 1 / 6:
                base -= PAGE
            if rng.random() < 1 / 8:
                base += rng.randrange(PAGE)
            if rng.random() < 0.1:
                size = 0
        elif pick < 0.8 or not descs:
            base = rng.randrange((1 << 20) + 2048) * PAGE + (rng.randrange(PAGE) if rng.random() < 0.25 else 0)
            size = rng.randrange(1 << 20) * 16
        else:
            base = (TOP - rng.randrange(1, 257) * PAGE + (rng.randrange(PAGE) if rng.random() < 1 / 3 else 0)) % TOP
            size = rng.randrange(1 << 16) * 512
        cases.append((base, size, descs))
    return cases


def encode_cases(cases):
    rows = [str(len(cases))]
    for base, size, descs in cases:
        rows.append(f"{base:x} {size:x} {len(descs)}")
        rows += [f"{kind:x} {phys:x} {pages:x} {attr:x}" for kind, phys, pages, attr in descs]
    return "\n".join(rows) + "\n"


_CASE_LINE = re.compile(r"^CASE (\d+) VERDICT=(\w+) TABLE=([0-9a-f]{320})(?: SLB=(\S+) VALID=(-?\d))?$")


def run_cases(exe, cases):
    """[(verdict, table bytes, the fork's name or None, its validator's answer or None)] per case."""
    rc, out, err = run_cmd([exe, "--ebs-cases"], env=kit.asan_env(), input=encode_cases(cases).encode())
    if rc != 0:
        raise AssertionError(f"--ebs-cases exited {rc}: {tail(text_of(err), 20)}")
    rows = []
    for line in text_of(out).splitlines():
        m = _CASE_LINE.match(line)
        if not m:
            raise AssertionError(f"unparseable --ebs-cases line: {line!r}")
        rows.append((m.group(2), bytes.fromhex(m.group(3)), m.group(4), m.group(5)))
    if len(rows) != len(cases):
        raise AssertionError(f"{len(rows)} answers for {len(cases)} cases")
    return rows


def table_problems(raw, base, size):
    """What is wrong with the bytes of a table that declares [base, base + size); [] when nothing."""
    magic, version, count, cap, reserved, *rest = TABLE.unpack(raw)
    ranges = [(rest[2 * i], rest[2 * i + 1]) for i in range(8)]
    bad = []
    if raw[:8] != b"SP11EBS1" or magic != 0x3153424531315053:
        bad.append(f"magic {raw[:8]!r}")
    for name, got, want in (("version", version, 1), ("count", count, 1), ("chunk_cap", cap, 1 << 20),
                            ("reserved", reserved, 0), ("range 0", ranges[0], (base, size)),
                            ("checksum", rest[16], fnv1a(raw[:152]))):
        if got != want:
            bad.append(f"{name} is {got!r}, expected {want!r}")
    if any(r != (0, 0) for r in ranges[1:]):
        bad.append("a slot from count on is not zero")
    return bad


# ---- The builder ------------------------------------------------------------------------------

def slbounce_dir():
    value = os.environ.get(SLBOUNCE_ENV, "").strip()
    if not value:
        raise unittest.SkipTest(f"{SLBOUNCE_ENV} is not set: the reviewed slbounce fork is not available")
    path = Path(value)
    for name in ("sp11-ebs-ranges.h", "sp11_scoped_sweep.h", "sp11_scoped_sweep.c"):
        if not (path / "src" / name).is_file():
            raise unittest.SkipTest(f"{SLBOUNCE_ENV} names no slbounce checkout with src/{name}")
    return path


def native_build(tag, extra_flags=(), extra_sources=(), replace=None, sanitize=True):
    """The X2 native test (suite_x.native_command) with more flags and sources, or with one source
    replaced by another file; returns the executable."""
    argv = sx.native_command()
    argv[0] = shutil.which("clang") or kit.tools().clang
    exe = kit.WORK / f"xebs-{tag}"
    argv[argv.index("-o") + 1] = str(exe)
    if sanitize:
        argv[1:1] = kit.SANITIZE_FLAGS
    if replace:
        old, new = replace
        argv[argv.index(old)] = str(new)
    argv += [*extra_flags, *map(str, extra_sources)]
    rc, out, err = run_cmd(argv)
    if rc != 0:
        raise AssertionError(f"the native test ({tag}) does not compile:\n{tail(text_of(out + err), 30)}")
    return exe


# Each entry seeds one defect into the builder; the C tests must then fail. A test that cannot fail
# proves nothing (project convention, as in tools/test-sp11-ebs-sweep-model.py).
MUTANTS = {
    "checksum stops one byte early": ("offsetof(tests_ebs_table, checksum));",
                                      "offsetof(tests_ebs_table, checksum) - 1);"),
    "checksum covers its own field": ("offsetof(tests_ebs_table, checksum));", "sizeof *t);"),
    "count off by one": ("t->count = 1;", "t->count = 2;"),
    "reserved word not zero": ("t->reserved = 0;", "t->reserved = 1;"),
    "version bumped": ("t->version = TESTS_EBS_VERSION;", "t->version = TESTS_EBS_VERSION + 1;"),
    "chunk cap doubled": ("t->chunk_cap = TESTS_EBS_CHUNK_CAP;", "t->chunk_cap = TESTS_EBS_CHUNK_CAP * 2;"),
    "size written into the base slot": ("t->range[0].size = size;", "t->range[0].size = base;"),
    "table not cleared first": ("    probe_memset(t, 0, sizeof *t);\n", ""),
    "a refused range still gets a table": ("if (status != TESTS_EBS_OK)\n        return status;",
                                           "if (status != TESTS_EBS_OK)\n        (void)status;"),
    "FNV prime mistyped": ("UINT64_C(0x100000001b3)", "UINT64_C(0x100000001b5)"),
    "FNV offset mistyped": ("UINT64_C(0xcbf29ce484222325)", "UINT64_C(0xcbf29ce484222326)"),
    "write-back not required": ("if ((held_attr & TESTS_EBS_ATTR_WB) == 0)",
                                "if ((held_attr & TESTS_EBS_ATTR_WB) == 0 && 0)"),
    "a second holder accepted": ("if (holders != 1)", "if (holders == 0)"),
    "reserved and conventional types accepted": ("(TESTS_EBS_TYPES_MASK >> held_type)",
                                                 "((TESTS_EBS_TYPES_MASK | 0x81u) >> held_type)"),
    "unaligned base accepted": ("if ((base & (TESTS_EBS_PAGE_BYTES - 1u)) != 0)", "if (0)"),
    "wrapping range accepted": ("if (size > UINT64_MAX - base)", "if (0)"),
    "range end exclusive": ("end <= phys + bytes", "end < phys + bytes"),
    "range start exclusive": ("phys <= base", "phys < base"),
    "map limit off by one": ("map->count > TESTS_EBS_MAX_DESCRIPTORS", "map->count >= TESTS_EBS_MAX_DESCRIPTORS"),
    "empty descriptor accepted": ("if (pages == 0 || pages > UINT64_MAX / TESTS_EBS_PAGE_BYTES)",
                                  "if (pages > UINT64_MAX / TESTS_EBS_PAGE_BYTES)"),
    "stride below a descriptor accepted": ("map->desc_size < PROBE_MEMMAP_MIN_DESC_SIZE ||", ""),
}


class XEbsRangesTests(Base):
    def test_the_c_sections_are_part_of_the_required_output(self):
        for section in ("ebs table layout", "ebs table bytes", "ebs range check", "ebs range model"):
            self.assertIn(section, sx.X_SECTIONS)

    def test_the_builder_agrees_with_the_python_model_on_every_case(self):
        cases = fixed_cases() + random_cases(3000)
        rows = run_cases(sx.native_exe("plain"), cases)
        seen, problems = {}, []
        for i, ((base, size, descs), (verdict, raw, _, _)) in enumerate(zip(cases, rows)):
            want = expected_verdict(base, size, descs)
            seen[want] = seen.get(want, 0) + 1
            if verdict != want:
                problems.append(f"case {i}: the builder says {verdict}, the model says {want} "
                                f"(base {base:#x} size {size:#x}, {len(descs)} descriptors)")
            elif verdict == "OK":
                problems += [f"case {i}: {p}" for p in table_problems(raw, base, size)]
                # Decoded from the bytes alone, the model sweeps exactly the declared range.
                ranges = [struct.unpack_from("<QQ", raw, 24 + 16 * k) for k in range(raw[12])]
                cap = struct.unpack_from("<I", raw, 16)[0]
                ops = oracle.scoped_sweep([oracle.Descriptor(*d) for d in descs], ranges, cap)
                if oracle.merged_intervals(ops) != [(base, base + size)] or any(n > cap for _, n in ops):
                    problems.append(f"case {i}: the model's sweep of the decoded table is not the range")
            elif raw != bytes(160):
                problems.append(f"case {i}: a refused case left a table that is not all zero")
        for verdict in VERDICTS:
            minimum = 2 if verdict in ("MAP_TOO_LARGE", "MAP_INVALID") else 50
            if seen.get(verdict, 0) < minimum:
                problems.append(f"only {seen.get(verdict, 0)} cases have the verdict {verdict}; "
                                f"the comparison would not be testing it")
        self.check(problems[:20], f"{len(cases)} cases, verdicts {seen}")

    def test_the_fork_accepts_what_the_builder_makes(self):
        fork = slbounce_dir()
        exe = native_build("slbounce", ["-DX_SLBOUNCE=1", "-DSLBOUNCE_SCOPED_SWEEP=1", f"-I{fork / 'src'}"],
                           [fork / "src" / "sp11_scoped_sweep.c"])
        rc, out, err = run_cmd([exe], env=kit.asan_env())
        text = text_of(out)
        problems = sx.native_output_problems(rc, text, sx.X_SECTIONS + ("ebs against slbounce",))
        # The same cases as the model test, now through the fork's validator and range rule.
        cases = fixed_cases() + random_cases(3000)
        for i, ((_, _, _), (verdict, _, theirs, valid)) in enumerate(zip(cases, run_cases(exe, cases))):
            if verdict == "OK" and (theirs != "OK" or valid != "1"):
                problems.append(f"case {i}: the builder accepts, the fork says {theirs} and valid={valid}")
            if verdict in SLBOUNCE_NAMES and theirs not in (SLBOUNCE_NAMES[verdict], "-"):
                problems.append(f"case {i}: the builder says {verdict}, the fork says {theirs}")
        self.check(problems[:20], tail(text))

    def test_seeded_builder_bugs_are_rejected(self):
        source = (ROOT / EBS_C).read_text(encoding="utf-8")
        work = kit.work_dir("xebs-mutants")
        problems = [f"{name}: the text to change occurs {source.count(old)} times, not once"
                    for name, (old, _) in MUTANTS.items() if source.count(old) != 1]
        if problems:
            self.check(problems)

        def run_mutant(indexed):
            index, (name, (old, new)) = indexed
            path = work / f"m{index:02d}" / "ebs_ranges.c"
            path.parent.mkdir(parents=True)
            path.write_text(source.replace(old, new, 1), encoding="utf-8")
            try:
                exe = native_build(f"mutant{index:02d}", replace=(EBS_C, path), sanitize=False)
            except AssertionError as exc:
                return name, f"the mutant does not compile: {exc}"
            rc, out, err = run_cmd([exe], env=kit.asan_env())
            return name, None if sx.native_output_problems(rc, text_of(out)) else "the C tests still pass"

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for name, problem in pool.map(run_mutant, enumerate(MUTANTS.items())):
                if problem:
                    problems.append(f"{name}: {problem}")
        self.check(problems)


# ---- Variants, images and the gate ------------------------------------------------------------

def preprocess(root, relative, variant, soc="x1e80100"):
    """The preprocessed text of one source of the tree at `root`, under the variant's flags and a
    fixed build id (the real ones hash the sources and differ)."""
    bt = sx.build_tool()
    flags = bt.compile_flags(variant, soc, "TESTBUILD000")
    if relative == MAIN_C:
        flags += bt.PROBE_MAIN_EXTRA
    rc, out, err = run_cmd([kit.tools().clang, *flags, "-E", "-P", relative], cwd=root)
    if rc != 0:
        raise AssertionError(f"preprocessing {relative} failed:\n{tail(text_of(err), 15)}")
    return text_of(out)


def without_gated_blocks(text):
    """The source with every conditional block whose opening directive names the gate removed,
    nested blocks and the gate's own default definition included."""
    out, dropped = [], []   # one entry per open conditional: whether its block is being removed
    for line in text.splitlines():
        m = re.match(r"\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)", line)
        if m:
            kind, rest = m.groups()
            if kind in ("if", "ifdef", "ifndef"):
                dropped.append(GATE in rest or bool(dropped and dropped[-1]))
                if dropped[-1]:
                    continue
            elif kind in ("elif", "else"):
                if dropped and dropped[-1]:
                    continue
            elif dropped and dropped.pop():
                continue
        if not (dropped and dropped[-1]):
            out.append(line)
    return "\n".join(out) + "\n"


def baseline_tree(rev):
    """probes/uefi of revision `rev`, extracted below the work directory, or a SKIP."""
    proc = subprocess.run(["git", "archive", rev, "probes/uefi"], cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise unittest.SkipTest(f"git cannot give probes/uefi of {rev}: {text_of(proc.stderr).strip()}")
    target = kit.work_dir("xbaseline")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        # Our own archive, but still extracted with the data filter where this Python has one.
        archive.extractall(target, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return target


def revision_before_the_gate():
    """A hint for the SKIP below: the parent of the commit that first mentioned the gate, if git knows."""
    proc = subprocess.run(["git", "log", "--format=%H", "-S", GATE, "--", MAIN_C], cwd=ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    commits = proc.stdout.split() if proc.returncode == 0 else []
    return f"{commits[-1]}^" if commits else "a revision from before the declared variant"


def copy_tree(tag, edits=()):
    """A copy of probes/uefi (sources and headers, not the test trees) below the work directory,
    with edits = [(relative path, old text, new text)] applied, each to text that occurs once."""
    work = kit.work_dir(f"xtree-{tag}")
    shutil.copytree(ROOT / "probes" / "uefi", work / "probes" / "uefi",
                    ignore=shutil.ignore_patterns("__pycache__", "test", "host"))
    for relative, old, new in edits:
        text = (work / relative).read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise AssertionError(f"{relative}: the text to edit occurs {text.count(old)} times, not once")
        (work / relative).write_text(text.replace(old, new, 1), encoding="utf-8")
    return work


def preprocess_probe(root, name):
    """probe_main.c as the X1 probe's own build (tools/build-uefi-probe.py) preprocesses it: the
    shipped probe compiles the same file, with the gate off."""
    bup = sx.build_tool().bup
    rc, out, err = run_cmd([kit.tools().clang, *bup.compile_flags(bup.VARIANTS[name], "TESTBUILD000"), "-E", "-P",
                            MAIN_C], cwd=root)
    if rc != 0:
        raise AssertionError(f"preprocessing {MAIN_C} for the probe variant {name} failed:\n{tail(text_of(err), 15)}")
    return text_of(out)


def gate_off_differences(a, b):
    """The (file, variant) pairs whose gate-off preprocessed text differs between two trees: the
    tests image's variants without the gate, and the X1 probe's variants."""
    bt = sx.build_tool()
    found = [f"{relative} under {name}" for relative in (MAIN_C, HOOK_C) for name in UNGATED_VARIANTS
             if preprocess(a, relative, bt.VARIANTS[name]) != preprocess(b, relative, bt.VARIANTS[name])]
    return found + [f"{MAIN_C} under the probe's {name}" for name in bt.bup.VARIANTS
                    if preprocess_probe(a, name) != preprocess_probe(b, name)]


def not_inert_problems(tree, label):
    """Why the gated blocks of the tree's probe_main.c are not the whole of what the gate adds: the
    file without them must preprocess, with the gate off, to the same text as the file itself."""
    text = (tree / MAIN_C).read_text(encoding="utf-8")
    stripped = without_gated_blocks(text)
    problems = []
    if text == stripped:
        problems.append("probe_main.c has no gated block, so this check would prove nothing")
    if GATE in stripped:
        problems.append(f"{GATE} is used outside a conditional directive")
    bare = kit.work_dir(f"xtree-{label}-bare")
    shutil.copytree(tree / "probes" / "uefi", bare / "probes" / "uefi", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "test", "host"))
    (bare / MAIN_C).write_text(stripped, encoding="utf-8")
    bt = sx.build_tool()
    problems += [f"under {name} the gate off is not the file without the gate" for name in UNGATED_VARIANTS
                 if preprocess(tree, MAIN_C, bt.VARIANTS[name]) != preprocess(bare, MAIN_C, bt.VARIANTS[name])]
    problems += [f"under the probe's {name} the gate off is not the file without the gate" for name in bt.bup.VARIANTS
                 if preprocess_probe(tree, name) != preprocess_probe(bare, name)]
    return problems


class XDeclaredBuildTests(Base):
    def test_variants_and_their_flags(self):
        bt, images, problems = sx.build_tool(), sx.x_images(), []
        manifests = {n: json.loads(r.manifest.read_text()) for n, r in images.items()}
        if set(manifests["declared"]["defines"]) != {"PROBE_TESTS_IMAGE=1", "TESTS_SOC=1", f"{GATE}=1"}:
            problems.append(f"declared's defines are {sorted(manifests['declared']['defines'])}")
        if set(manifests["model-declared"]["defines"]) != set(manifests["model"]["defines"]) | {f"{GATE}=1"}:
            problems.append(f"model-declared's defines are {sorted(manifests['model-declared']['defines'])}")
        for name, m in manifests.items():
            gated = f"{GATE}=1" in m["defines"]
            if gated != (name in DECLARED_VARIANTS):
                problems.append(f"{name} {'has' if gated else 'lacks'} {GATE}, which only {DECLARED_VARIANTS} carry")
            if (EBS_C in m["sources"]) != gated:
                problems.append(f"{name}: ebs_ranges.c is {'' if gated else 'not '}expected among its sources")
        if images["declared"].image.parent.name != "ximages" or images["model-declared"].image.parent.name != "test":
            problems.append("declared belongs next to the hardware image, model-declared in test/")
        if bt.DEFAULT != ("hardware",):
            problems.append(f"the default build is {bt.DEFAULT}, not the hardware image alone")
        if not (bt.VARIANTS["declared"].shipped and bt.VARIANTS["declared"].uses_descriptor):
            problems.append("declared is a hardware image: it needs --soc and the board descriptor")
        self.check(problems)

    def test_the_declared_image_needs_an_explicit_soc_and_is_not_built_by_default(self):
        work = kit.work_dir("xdeclared-cli")
        rc, out, err = run_cmd([sys.executable, "-B", "tools/build-uefi-tests.py", "--variant", "declared",
                                "--out", work / "out"])
        problems = []
        if rc != 2 or "REFUSED" not in text_of(err) or "--soc" not in text_of(err):
            problems.append(f"no --soc gave exit {rc}: {text_of(err)!r}")
        if (work / "out").exists() and any((work / "out").iterdir()):
            problems.append("a refused build wrote output")
        rc, out, err = run_cmd([sys.executable, "-B", "tools/build-uefi-tests.py", "--soc", "x1e80100",
                                "--print-build-id"])
        names = [row.split(":")[0] for row in text_of(out).strip().splitlines()]
        if rc != 0 or names != ["hardware"]:
            problems.append(f"the default build names {names}, not the hardware image alone")
        self.check(problems)

    def test_only_the_declared_images_contain_the_declaration(self):
        images, problems = sx.x_images(), []
        name_u16 = "Q1N1Sp11EbsRanges".encode("utf-16-le")
        for name, built in images.items():
            data = built.image.read_bytes()
            declares = name in DECLARED_VARIANTS
            for label, needle in (("the variable name", name_u16),
                                  ("the image tag", b"SP11X2-EBS=DECLARED-IMAGE-RANGE")):
                if (needle in data) != declares:
                    problems.append(f"{name} {'lacks' if declares else 'contains'} {label}")
        hardware = images["hardware"].image.read_bytes()
        for needle in (b"SP11X2-KIND=HARDWARE", b"SP11X2-WINDOWS=NO-CONFLICT"):
            if needle not in hardware:
                problems.append(f"the hardware image no longer contains {needle.decode()}")
        for name, kind in (("declared", b"SP11X2-KIND=HARDWARE"), ("model-declared", b"SP11X2-KIND=MODEL")):
            if kind not in images[name].image.read_bytes():
                problems.append(f"the {name} image does not say {kind.decode()}")
        self.check(problems)

    def test_the_gate_belongs_to_the_tests_image(self):
        bt = sx.build_tool()
        flags = [*[f for f in bt.bup.CFLAGS if not f.startswith("-D")], "-Iprobes/uefi", "-Iprobes/uefi/tests",
                 f'-DPROBE_BUILD_ID="{"A" * 12}"', "-fsyntax-only"]
        problems = []
        for extra in ([f"-D{GATE}=1"], [f"-D{GATE}=1", "-DPROBE_TESTS_IMAGE=0"]):
            rc, out, err = run_cmd([kit.tools().clang, *flags, *extra, MAIN_C])
            if rc == 0 or "belongs to the X2 tests image" not in text_of(err):
                problems.append(f"{' '.join(extra)} without PROBE_TESTS_IMAGE was not refused: {tail(text_of(err), 3)}")
        self.check(problems)

    def test_with_the_gate_off_every_line_the_gate_adds_is_discarded(self):
        """probe_main.c without its gated blocks preprocesses to the same text as the whole file,
        so everything the gate adds is inside a block the preprocessor drops when it is off."""
        problems = not_inert_problems(ROOT, "real")
        if GATE in (ROOT / HOOK_C).read_text(encoding="utf-8"):
            problems.append("hook.c mentions the gate; the gate lives in probe_main.c alone")
        self.check(problems)

    def test_with_the_gate_off_the_text_is_that_of_the_baseline_revision(self):
        """probe_main.c and hook.c, headers included, preprocess to exactly what the baseline
        revision's own tree gives, under every variant but the declared ones. HEAD is the baseline
        unless UEFI_TESTS_BASELINE_REV names another, and a baseline that already has the gate is a
        SKIP: comparing with it would prove nothing."""
        rev = os.environ.get(BASELINE_ENV, "").strip() or "HEAD"
        bt = sx.build_tool()
        shown = subprocess.run(["git", "show", f"{rev}:{MAIN_C}"], cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
        if shown.returncode != 0:
            raise unittest.SkipTest(f"git cannot show {MAIN_C} at {rev}: {text_of(shown.stderr).strip()}")
        if GATE in text_of(shown.stdout):
            raise unittest.SkipTest(f"{rev} already contains {GATE}, so comparing with it proves nothing; the "
                                    f"comparison was meant for the commit that added the gate, and with "
                                    f"{BASELINE_ENV}={revision_before_the_gate()} hook.c now also differs by "
                                    f"the intentional changes of the second image version")
        base = baseline_tree(rev)
        problems = [f"{what} differs from {rev}" for what in gate_off_differences(ROOT, base)]
        # The comparison can fail: with the gate on, the text is not the baseline's.
        on, off = (preprocess(ROOT, MAIN_C, bt.VARIANTS[v]) for v in ("declared", "hardware"))
        if on == preprocess(base, MAIN_C, bt.VARIANTS["declared"]) or "DECLARED-IMAGE-RANGE" in off \
                or "DECLARED-IMAGE-RANGE" not in on:
            problems.append("the gate does not decide whether the declaration is compiled: the comparison is blind")
        self.check(problems, f"baseline {rev}")

    def test_the_gate_off_checks_can_fail(self):
        """Negative controls, on copies of the tree and with no git: an edit outside the gate is a
        difference from the original, an edit inside it is not, and a branch the gate leaves
        behind (an #else, or the gate named outside a directive) is not inert."""
        main_start = f"#if PROBE_TESTS_IMAGE && {GATE}\n#include \"tests/ebs_ranges.h\"\n#endif"
        original = copy_tree("original")
        problems = [f"the unmodified tree differs from itself: {d}" for d in gate_off_differences(ROOT, original)]
        problems += [f"the unmodified tree is not inert: {p}" for p in not_inert_problems(original, "original")]
        outside = copy_tree("outside", [(MAIN_C, "(void)bs->set_watchdog_timer(0, 0, 0, NULL);",
                                         "(void)bs->set_watchdog_timer(0, 0, 1, NULL);")])
        inside = copy_tree("inside", [(MAIN_C, '"SP11X2-EBS=DECLARED-IMAGE-RANGE"', '"SP11X2-EBS=OTHER-TEXT"')])
        elsewhere = copy_tree("else", [(MAIN_C, main_start, main_start[:-6] + "#else\nstatic int g_stray;\n#endif")])
        named = copy_tree("named", [(MAIN_C, "#if PROBE_TESTS_IMAGE\n#include \"tests/tests_hook.h\"",
                                     f"static int g_stray = {GATE};\n#if PROBE_TESTS_IMAGE\n#include \"tests/tests_hook.h\"")])
        if not gate_off_differences(original, outside):
            problems.append("an edit outside the gate was not seen")
        if gate_off_differences(original, inside):
            problems.append("an edit inside the gate was reported as a gate-off difference")
        if not any("is not the file without the gate" in p for p in not_inert_problems(elsewhere, "else")):
            problems.append("an #else branch of the gate was not seen")
        if not any("outside a conditional directive" in p for p in not_inert_problems(named, "named")):
            problems.append("the gate named outside a directive was not seen")
        self.check(problems)

    def test_the_default_probe_build_does_not_mention_the_declaration(self):
        proc = subprocess.run([kit.tools().clang, *[f for f in sx.build_tool().bup.CFLAGS if not f.startswith("-D")],
                               "-E", "-Iprobes/uefi", MAIN_C], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, tail(proc.stderr))
        for needle in ("tests_ebs", "EBSRANGES", "DECLARED", GATE):
            self.assertNotIn(needle, proc.stdout)


class XDeclaredAuditTests(Base):
    def test_the_source_audit_pins_the_one_write(self):
        self.check(audit.audit_variable_writes(ROOT) + audit.audit_sources(ROOT))
        self.assertIn("set_variable", (ROOT / MAIN_C).read_text(encoding="utf-8"))

    def test_the_audit_rejects_any_other_way_to_write_a_variable(self):
        work = kit.work_dir("xvaraudit")
        for sub in ("", "tests"):
            (work / "probes" / "uefi" / sub).mkdir(parents=True, exist_ok=True)
            for path in (ROOT / "probes" / "uefi" / sub).glob("*.[ch]"):
                shutil.copy(path, work / "probes" / "uefi" / sub / path.name)
        problems = [f"the unmodified tree is refused: {p}" for p in audit.audit_sources(work)]
        main, hook = work / MAIN_C, work / HOOK_C
        original = {main: main.read_text(encoding="utf-8"), hook: hook.read_text(encoding="utf-8")}
        call = "static void again(efi_system_table *s) { s->runtime_services->set_variable(0, 0, 0, 0, 0); }\n"
        gate = f"#if PROBE_TESTS_IMAGE && {GATE}\n"
        opening = gate + "/* In .rdata"
        self.assertEqual(original[main].count(opening), 1)
        cases = (
            ("a call in a test source", hook, original[hook] + "\n" + call, "outside probe_main.c"),
            ("a call inside the gate, in another file", hook, original[hook] + f"\n{gate}{call}#endif\n",
             "outside probe_main.c"),
            ("a call in probe_main.c with the gate widened", main,
             original[main].replace(opening, "#if PROBE_TESTS_IMAGE\n/* In .rdata", 1), "outside `#if"),
            ("a call after the gate closed", main, original[main] + "\n" + call, "outside `#if"),
            ("a call in the else branch of the gate", main, original[main] + f"\n{gate}#else\n{call}#endif\n",
             "outside `#if"),
            ("a call nested inside the gate is still inside it", main,
             original[main] + f"\n{gate}#if PROBE_PSCI\n{call}#endif\n#endif\n", None),
            ("a mention in a comment or a string is not a call", hook,
             original[hook] + '\n/* set_variable */ static const char g_note[] = "set_variable";\n', None),
        )
        for label, path, text, fragment in cases:
            path.write_text(text, encoding="utf-8")
            found = audit.audit_sources(work)
            path.write_text(original[path], encoding="utf-8")
            if fragment is None and found:
                problems.append(f"{label}: flagged {found}")
            elif fragment is not None and not any(fragment in p for p in found):
                problems.append(f"{label}: expected {fragment!r}, got {found}")
        self.check(problems)


# ---- QEMU --------------------------------------------------------------------------------------

def declared_run():
    return sx.scenario("declared", "model-declared", sx.FULL, 150, until=sx.whole_report_seen)


@contextlib.contextmanager
def built_from(root):
    """The build tool pointed at another tree for the length of one build, so that an edited copy
    of probes/uefi goes through the real compile and link steps. Tests run one at a time."""
    bt = sx.build_tool()
    saved = [(mod, name, getattr(mod, name)) for mod, names in ((bt, ("ROOT", "SRC_DIR", "TESTS_DIR")),
                                                               (bt.bup, ("ROOT", "SRC_DIR")))
             for name in names]
    bt.ROOT = bt.bup.ROOT = root
    bt.SRC_DIR = bt.bup.SRC_DIR = root / "probes" / "uefi"
    bt.TESTS_DIR = bt.SRC_DIR / "tests"
    try:
        yield
    finally:
        for mod, name, value in saved:
            setattr(mod, name, value)


def patched_model_image(tag, edits):
    """The model-declared image built from a copy of the tree with `edits` applied (test-only: it
    exists to make one step of the declaration fail on purpose)."""
    def build():
        bt = sx.build_tool()
        work = copy_tree(f"img-{tag}", edits)
        with built_from(work):
            return bt.build_variant(bt.VARIANTS["model-declared"], "x1e80100", bt.DEFAULT_DESCRIPTOR,
                                    kit.work_dir(f"ximg-{tag}"), kit.tools()).image
    return kit.once(("ximg", tag), build)


# Each makes one step of the declaration fail, by changing one expression of the gated function.
REFUSALS = (
    ("unaligned", "RANGE BASE UNALIGNED", False,
     [(MAIN_C, "base = (uint64_t)(uintptr_t)li->image_base;", "base = (uint64_t)(uintptr_t)li->image_base + 0x10;")]),
    ("not-in-one", "RANGE NOT IN ONE DESCRIPTOR", False,
     [(MAIN_C, "size = li->image_size;", "size = li->image_size + (1ull << 40);")]),
    ("set-variable", "SET VARIABLE FAILED", True,
     [(MAIN_C, "variable_name, &variable_guid, TESTS_EBS_VARIABLE_ATTRIBUTES,", "variable_name, &variable_guid, 0u,")]),
)


class XDeclaredQemuTests(Base):
    def setUp(self):
        if kit.SKIP_QEMU:
            self.skipTest("UEFI_PROBE_SKIP_QEMU=1")

    def test_the_declared_model_image_declares_its_range_then_exits_boot_services(self):
        out = declared_run()
        # Everything the model image does after the declaration is what the plain model does. The
        # method keeps no state of its class, so this test borrows it.
        problems = sx.XQemuTests.model_run_problems(self, out)
        declared = out.marker("EBSRANGES")
        if declared is None:
            self.check(problems + ["no EBSRANGES line: the image never declared its range"], tail(out.serial))
        fields = declared.fields
        if (fields.get("STATE"), fields.get("RANGES"), fields.get("STATUS")) != ("DECLARED", "1", "0x0000000000000000"):
            problems.append(f"the declaration line is {declared.raw!r}")
        base, size = int(fields.get("BASE", "0"), 16), int(fields.get("SIZE", "0"), 16)
        if not 0 <= out.serial.find("EBSRANGES STATE=DECLARED") < out.serial.find("EBS STATE=CALLING"):
            problems.append("the declaration did not come before the ExitBootServices call")
        if any(m.tag == "EBS" and m.fields.get("STATE") in ("SKIPPED", "FAILED") for m in out.markers):
            problems.append("ExitBootServices was skipped or failed")
        image = peinfo.parse_pe(sx.x_images()["model-declared"].image.read_bytes())
        if size != image.size_of_image:
            problems.append(f"the declared size {size:#x} is not the image's SizeOfImage {image.size_of_image:#x}")
        if base % PAGE:
            problems.append(f"the declared base {base:#x} is not page aligned")
        # The firmware's own memory map, as the probe dumped it before the call, accepts the
        # range under the model of the fork's rule.
        files = {} if out.stick is None else out.stick.files
        try:
            _, rows = checks.parse_memmap_txt(text_of(files["SP11DUMP/RUN01/memmap.txt"]))
            descs = [oracle.Descriptor(r["type"], r["phys"], r["pages"], r["attr"]) for r in rows]
            oracle.scoped_sweep(descs, [(base, size)], chunk_cap=1 << 20)
        except (KeyError, ValueError, oracle.SweepRefused) as exc:
            problems.append(f"the dumped memory map does not accept the declared range: {exc!r}")
        self.check(problems, tail(out.serial, 12))

    def test_a_refused_declaration_names_its_reason_and_never_reaches_exitbootservices(self):
        problems = []
        for tag, reason, status_shown, edits in REFUSALS:
            out = sx.scenario(f"declared-{tag}", patched_model_image(tag, edits), sx.FULL, 5)
            ebs = [m for m in out.markers if m.tag == "EBS"]
            skipped = ebs[0] if ebs else None
            if out.failure:
                problems.append(f"{tag}: harness: {out.failure}")
            if skipped is None or (skipped.fields.get("STATE"), skipped.fields.get("REASON")) != ("SKIPPED", reason):
                problems.append(f"{tag}: the first EBS line is {skipped and skipped.raw!r}, expected SKIPPED {reason!r}")
                continue
            if ("STATUS" in skipped.fields) != status_shown or (
                    status_shown and not int(skipped.fields["STATUS"], 16) >> 63):
                problems.append(f"{tag}: STATUS is {skipped.fields.get('STATUS')!r} on {skipped.raw!r}")
            if out.marker("EBSRANGES") is not None or any(m.fields.get("STATE") == "CALLING" for m in ebs):
                problems.append(f"{tag}: the image declared or called ExitBootServices after refusing")
            if out.samples:
                problems.append(f"{tag}: the screen changed after the refusal, as if ExitBootServices had run")
            if not any(m[0] == "STICK" and m[1].get("STATE") == "OK" for m in sx.x_markers(out)):
                problems.append(f"{tag}: phase A's results were not written before the declaration")
        self.check(problems)
