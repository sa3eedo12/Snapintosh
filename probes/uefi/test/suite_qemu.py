# SPDX-License-Identifier: MIT
"""Emulator scenarios for the SP11 EL2 probe (QEMU virt with EDK2).

QEMU models a generic ARM machine.  A pass here shows that the probe's
control flow, dump format and on-screen layout work end to end; it says
nothing about the Surface Pro 11's firmware, GOP, PSCI conduit or registers.
Each scenario boots one image once and records the serial markers, the FAT
stick the probe wrote and decoded screenshots; the cases only inspect that
recording, so one more assertion costs no extra boot.

  default    fast timing, EL2, USB stick: the reference run, resets itself
  no-usb     boot disk behind virtio: the dump must be refused
  el1        guest at EL1: the "not at EL2" halt path
  psci       the opt-in PSCI image with two CPUs
  no-gop     no framebuffer: ExitBootServices skipped, dump still written
  no-reset   RESET=OFF image: holds on its last page
  shipped    the default image at its real 60 s timing, first frames only

suite_rotation registers three more, which start from a stick that already holds
earlier dump runs (Scenario.seed); suite_shell registers via-shell, the default
image started from the pinned UEFI Shell the way the X1 stick starts it.

Adding a scenario: append it to SCENARIOS and ORDER, then add a QemuCase
subclass that mixes in the checks which apply to it.  The pure functions
(footer_problems, register_problems, the table readers) are exercised with
faulty input in AssertionHelperTests so a weakened check is noticed.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import functools
import re
import struct
import types
import unittest

import uefi_testkit as kit
import checks
import qemu_probe
import screen as screenlib
from synth_dump import ACPI10_GUID, ACPI20_GUID, BOOT_FILE, RUN_DIR, SMBIOS3_GUID

SMBIOS_GUID = "EB9D2D31-2D88-11D3-9A16-0090273FC14D"  # legacy 32-bit entry point

MAX_PAGES = 8                  # PROBE_LAYOUT_MAX_PAGES
FAST_COUNTDOWN_START = 16      # two seconds per page at the fast timing
SHIPPED_COUNTDOWN_MIN = 45     # the shipped countdown is 60 s; the first frame may arrive late

MARKER_TAGS = ("BEGIN", "PREEBS", "FW", "GOP", "LAYOUT", "CFG", "ACPI", "SMBIOS", "MEMMAP",
               "DUMP", "EBS")
# Loader, boot-services and runtime code/data, conventional, ACPI reclaim and NVS memory.
RAM_TYPES = frozenset({1, 2, 3, 4, 5, 6, 7, 9, 10})
STAGED_FOOTERS = frozenset({
    "STAGE 0 DONE - READING 1 NOW", "STAGE 1 DONE - READING 2A NOW",
    "ALL STAGES DONE", "PSCI CALL IN PROGRESS"})
COUNTDOWN = re.compile(r"RESET IN (\d+) S")
RESETTING = "RESETTING NOW"
HOLD_FOOTERS = frozenset({"HALTED - NO RESET", "RESET DISABLED - HOLDING"})

RUN_PLAIN = frozenset({"memmap.raw", "memmap.txt", "smbios-ep.raw", "smbios-table.raw",
                       "summary.txt", "tables.txt"})
TABLE_NAME = re.compile(r"[A-Z0-9_]{4}(_\d+)?\.bin")
REQUIRED_TABLES = ("RSDP.bin", "XSDT.bin", "FACP.bin", "DSDT.bin", "APIC.bin")
STICK_DIRS = frozenset({"EFI", "EFI/BOOT", "SP11DUMP", RUN_DIR})

ALWAYS_SHOWN = ("HCR_EL2", "SCTLR_EL2", "TCR_EL2", "TTBR0_EL2", "MAIR_EL2", "VBAR_EL2",
                "CPTR_EL2", "MDCR_EL2", "CNTHCTL_EL2", "CNTVOFF_EL2", "SPSR_EL2 RAW",
                "ELR_EL2 RAW", "MPIDR_EL1", "CTR_EL0", "CNTFRQ_EL0", "DCZID_EL0", "MIDR_EL1",
                "REVIDR_EL1", "ID_AA64PFR0_EL1", "ID_AA64PFR1_EL1", "ID_AA64DFR0_EL1",
                "ID_AA64ISAR0_EL1", "ID_AA64ISAR1_EL1",
                *(f"ID_AA64MMFR{n}_EL1" for n in range(3)))
# Registers the passive probe never reads (see sysreg.h): no raw line may show one.
NEVER_READ = ("ID_AA64MMFR3_EL1", "ID_AA64MMFR4_EL1", "ID_AA64ISAR2_EL1", "ID_AA64DFR1_EL1")
# (label, gating register, hi, lo, reason): the probe reads these only when the feature
# field is nonzero, because reading an unimplemented register can trap.
GATED = (("TTBR1_EL2", "ID_AA64MMFR1_EL1", 11, 8, "VH=0"),
         ("ICC_SRE_EL2", "ID_AA64PFR0_EL1", 27, 24, "GIC=0"),
         ("ID_AA64ZFR0_EL1", "ID_AA64PFR0_EL1", 35, 32, "SVE=0"),
         ("ID_AA64SMFR0_EL1", "ID_AA64PFR1_EL1", 27, 24, "SME=0"))
DERIVED = re.compile(r"(MMU |HCR VM=|PAUTH |(?:%s)=)" % "|".join(f[0] for f in checks.DECODED_FIELDS))

FACT_FAMILIES = tuple((name, re.compile(prefix), re.compile(shape)) for name, prefix, shape in (
    ("FW REV", r"FW REV=", r"FW REV=[0-9A-F]{8}"),
    ("FW VENDOR", r"FW VENDOR=", r"FW VENDOR=\S.*"),
    ("GOP SIZE-PX", r"GOP \d", r"GOP \d+X\d+ SEL=(CONSOLE|SAME)"),
    ("GOP STRIDE", r"GOP STRIDE=", r"GOP STRIDE=\d+ FMT=(BGRX|RGBX|\?)"),
    ("GOP BASE", r"GOP BASE=", r"GOP BASE=[0-9A-F]{16}"),
    ("GOP SIZE", r"GOP SIZE=", r"GOP SIZE=[0-9A-F]{16}"),
    ("LAYOUT", r"LAYOUT ", r"LAYOUT S=\d+ C=\d+ R=\d+ P=\d+"),
    ("CFG N", r"CFG N=", r"CFG N=\d+ ACPI20=[YN] ACPI10=[YN]"),
    ("CFG SMBIOS", r"CFG SMBIOS", r"CFG SMBIOS=[YN] SMBIOS3=[YN]"),
    ("ACPI RSDP", r"ACPI RSDP", r"ACPI RSDP REV=\d+ ROOT=(XSDT|RSDT)"),
    ("ACPI OK", r"ACPI OK=", r"ACPI OK=\d+ REJ=\d+ DUP=\d+"),
    ("FADT", r"FADT ", r"FADT REV=\d+ MINOR=\d+ ARMBOOT=[0-9A-F]{4}"),
    ("PSCI CONDUIT", r"PSCI CONDUIT=", r"PSCI CONDUIT=(SMC|HVC|NONE)"),
    ("SMBIOS", r"SMBIOS V", r"SMBIOS V\d+\.\d+ LEN=\d+"),
    ("MEMMAP", r"MEMMAP ", r"MEMMAP N=\d+ DESCSZ=\d+"),
    ("RAM", r"RAM ", r"RAM [0-9A-F]{16}-[0-9A-F]{16}"),
    ("DUMP STATE", r"DUMP STATE=", r"DUMP STATE=[A-Z ]+"),
    ("DUMP RUN", r"DUMP RUN=", r"DUMP RUN=(\d\d FILES=\d+ BYTES=\d+|NONE)"),
    ("VARIANT", r"VARIANT=", r"VARIANT=(DEFAULT|PSCI) RESET=(ON|OFF) PSCI=(ON|OFF)"),
    ("EBS ATTEMPTS", r"EBS ATTEMPTS=", r"EBS ATTEMPTS=\d+"),
))
PSCI_PAGE_PREFIXES = ("PSCI RUN=", "PSCI_VERSION=", "CPU_ON ", "SECONDARY ", "== PSCI")

# (summary key, marker tag, marker field, compare as numbers)
SUMMARY_LINKS = (
    ("build", "BEGIN", "BUILD", False),
    ("preebs_currentel", "PREEBS", "CURRENTEL", True),
    ("fw_vendor", "FW", "VENDOR", False),
    ("fw_revision", "FW", "REV", True),
    ("gop_width", "GOP", "W", True), ("gop_height", "GOP", "H", True),
    ("gop_stride_pixels", "GOP", "STRIDE", True),
    ("gop_base", "GOP", "BASE", True), ("gop_size", "GOP", "SIZE", True),
    ("gop_select", "GOP", "SEL", False),
    ("layout_scale", "LAYOUT", "SCALE", True), ("layout_cols", "LAYOUT", "COLS", True),
    ("layout_rows", "LAYOUT", "ROWS", True), ("layout_pages", "LAYOUT", "PAGES", True),
    ("config_tables", "CFG", "N", True),
    ("acpi_ok", "ACPI", "OK", True), ("acpi_rejected", "ACPI", "REJ", True),
    ("acpi_duplicate", "ACPI", "DUP", True),
    ("memmap_count", "MEMMAP", "COUNT", True), ("memmap_descriptor_size", "MEMMAP", "DESC", True),
    ("dump_run", "DUMP", "RUN", True),
)


@dataclasses.dataclass(frozen=True)
class Scenario:
    name: str
    variant: str                 # key of bup.VARIANTS / kit.images()
    smp: int = 1
    el2: bool = True
    disk: str = "usb"
    gop: bool = True
    ends_itself: bool = False    # the probe's ResetSystem ends QEMU (it runs with -no-reboot)
    stop: str = ""               # "pages": every page seen; "countdown": first countdown footer
    hold: str = ""               # footer a run that never ends must keep showing
    full_pages: bool = True      # every page must be observed
    seed: tuple = ()             # (stick path, bytes) pairs already on the stick at boot
    sel: str = "CONSOLE"         # rule the GOP marker must name in SEL=
    shell: bool = False          # booted as the pinned UEFI Shell; a test-only STARTUP.NSH starts the probe


SCENARIOS = {s.name: s for s in (
    Scenario("default", "fast", ends_itself=True),
    Scenario("no-usb", "fast", disk="virtio", ends_itself=True),
    Scenario("el1", "fast", el2=False, hold="HALTED - NO RESET", stop="pages"),
    Scenario("psci", "fast-psci", smp=2, ends_itself=True),
    Scenario("no-gop", "fast", gop=False),
    Scenario("no-reset", "fast-noreset", hold="RESET DISABLED - HOLDING", stop="pages"),
    Scenario("shipped", "default", stop="countdown", full_pages=False),
)}
ORDER = ("el1", "no-reset", "default", "no-usb", "psci", "shipped", "no-gop")  # slowest first


@dataclasses.dataclass
class Outcome:
    scenario: Scenario
    result: qemu_probe.RunResult
    attempts: int
    build_id: str
    efi: bytes


_RETRIED = []


# ---------------------------------------------------------------- decoding helpers

def num(text):
    text = text.strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


def field_num(fields, key):
    try:
        return num(fields[key])
    except (KeyError, ValueError):
        return None


def page_of(shot):
    for row in shot.headers:
        found = re.search(r"\bPAGE (\d+)/(\d+)$", row.check.payload.strip())
        if found:
            return int(found.group(1)), int(found.group(2))
    return None


def is_clean(shot):
    return bool(shot is not None and shot.ok and not shot.bad_rows()
                and shot.footer is not None and page_of(shot))


def footer_of(shot):
    return shot.footer.check.payload.strip()


def payloads_of(shot):
    return [row.check.payload.strip() for row in shot.body if row.check.payload.strip()]


def why_unclean(shot):
    if not shot.ok:
        return shot.reason
    if shot.bad_rows():
        return f"{len(shot.bad_rows())} row(s) failed the CRC"
    return "no page header or footer"


def expect(problems, what, got, want):
    if got != want:
        problems.append(f"{what}: {got!r}, expected {want!r}")


def expect_fields(problems, what, fields, want):
    for key, value in want.items():
        if fields.get(key) != value:
            problems.append(f"{what} {key}={fields.get(key)!r}, expected {value!r}")


# ---------------------------------------------------------------- stop rules

def _all_pages_seen(result):
    seen, total = set(), 0
    for sample in result.samples:
        if is_clean(sample.screen):
            page, total = page_of(sample.screen)
            seen.add(page)
    return total > 0 and seen >= set(range(1, total + 1))


def _countdown_seen(result):
    return any(is_clean(s.screen) and COUNTDOWN.fullmatch(footer_of(s.screen))
               for s in result.samples)


_STOPS = {"": None, "pages": _all_pages_seen, "countdown": _countdown_seen}


# ---------------------------------------------------------------- running scenarios

def _incomplete(scn, result, stop):
    """True when the harness, not the probe, may be why the recording is unusable."""
    return bool(result.failure or (scn.ends_itself and not result.ended_by_guest)
                or (stop and not stop(result)))


def _execute(scn):
    shell = kit.pinned_shell() if scn.shell else None  # skips before anything is built or booted
    build = kit.images()[scn.variant]
    assets, font, stop = kit.qemu_assets(), kit.font_table(), _STOPS[scn.stop]
    efi, seed = build.image, dict(scn.seed)
    if shell:
        efi, seed = shell, {**seed, **kit.shell_launch_files(build.image)}
    attempts = 0
    while True:
        attempts += 1
        cfg = qemu_probe.RunConfig(
            name=scn.name, efi=efi, workdir=kit.WORK / "qemu" / scn.name,
            el2=scn.el2, smp=scn.smp, disk=scn.disk, gop=scn.gop, extra_files=seed,
            boot_timeout=150.0 if scn.shell else 90.0, pre_ebs_timeout=60.0,
            capture_seconds=60.0 if scn.ends_itself else 45.0, capture_interval=0.5,
            stop_when=stop)
        try:
            result = qemu_probe.run(cfg, assets, font)
        except Exception as exc:  # noqa: BLE001 - the harness failing fails the scenario
            raise AssertionError(f"scenario {scn.name}: the emulator harness raised {exc!r}") from exc
        if attempts == 2 or not _incomplete(scn, result, stop):
            break
        # Only an incomplete recording is retried (load from other jobs can starve a guest);
        # a recording whose content is wrong fails the cases on the first attempt.
        _RETRIED.append(scn.name)
    return Outcome(scn, result, attempts, build.build_id, efi.read_bytes())


def scenario(name):
    """The recording of one scenario; boots it on first use and shares it afterwards."""
    if kit.SKIP_QEMU:
        raise unittest.SkipTest("UEFI_PROBE_SKIP_QEMU=1")
    kit.qemu_assets()
    return kit.once(("qemu", name), lambda: _execute(SCENARIOS[name]))


def _quiet(name):
    try:
        scenario(name)
    except Exception:  # noqa: BLE001 - once() cached it; every case re-raises it
        pass


def start_background(tests):
    """Entry hook: boot the needed scenarios in parallel while the other suites run."""
    names = {t.scenario_name for t in tests if isinstance(t, QemuCase) and t.scenario_name}
    if not names or kit.SKIP_QEMU:
        return None
    try:
        kit.qemu_assets()
    except unittest.SkipTest:
        return None
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=kit.QEMU_WORKERS)
    for name in (n for n in ORDER if n in names):
        pool.submit(_quiet, name)

    def finish():
        pool.shutdown(wait=True)
        if _RETRIED:
            print("NOTE: QEMU scenarios needing a second attempt: " + ", ".join(sorted(_RETRIED)),
                  flush=True)

    return finish


# ---------------------------------------------------------------- one scenario's data

class View:
    """A recording plus the derived data its checks share."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.scn = outcome.scenario
        self.result = outcome.result
        self.variant = kit.bup.VARIANTS[self.scn.variant]
        self.fast = "PROBE_FAST_TEST=1" in self.variant.defines

    def marker(self, tag, **where):
        found = self.result.marker(tag, **where)
        return dict(found.fields) if found else {}

    @functools.cached_property
    def files(self):
        stick = self.result.stick
        prefix = RUN_DIR + "/"
        return {} if stick is None else {p[len(prefix):]: d for p, d in stick.files.items()
                                         if p.startswith(prefix)}

    @functools.cached_property
    def summary(self):
        text = self.files.get("summary.txt")
        try:
            return {} if text is None else checks.parse_summary(kit.text_of(text))
        except ValueError:
            return {}

    @functools.cached_property
    def clean_shots(self):
        return [s.screen for s in self.result.samples if is_clean(s.screen)]

    @functools.cached_property
    def shown(self):
        return [line for shot in self.clean_shots for line in payloads_of(shot)]


# ---------------------------------------------------------------- independent table readers

def fadt_facts(facp):
    """(revision, minor, ARM_BOOT_ARCH, PSCI conduit) read straight from FACP.bin."""
    if len(facp) < 132:
        return None
    length = struct.unpack_from("<I", facp, 4)[0]
    arm = struct.unpack_from("<H", facp, 129)[0]
    if facp[8] < 5 or length < 131 or not arm & 1 or arm & 0xFFFC:
        conduit = "NONE"
    else:
        conduit = "HVC" if arm & 2 else "SMC"
    return facp[8], facp[131], arm, conduit


def rsdp_facts(rsdp):
    if len(rsdp) < 32:
        return None
    xsdt = rsdp[15] >= 2 and struct.unpack_from("<Q", rsdp, 24)[0] != 0
    return rsdp[15], "XSDT" if xsdt else "RSDT"


def smbios_facts(ep, table):
    if ep[:5] == b"_SM3_" and len(ep) >= 9:
        return ep[7], ep[8], len(table)
    if ep[:4] == b"_SM_" and len(ep) >= 8:
        return ep[6], ep[7], len(table)
    return None


def ram_bounds(memmap_text):
    try:
        _, rows = checks.parse_memmap_txt(memmap_text)
    except ValueError:
        return None
    spans = [(r["phys"], r["phys"] + r["pages"] * 4096) for r in rows if r["type"] in RAM_TYPES]
    return (min(a for a, _ in spans), max(b for _, b in spans)) if spans else None


# ---------------------------------------------------------------- checks (lists of problems)

def marker_problems(view):
    scn, variant = view.scn, view.variant
    want_tags = list(MARKER_TAGS)
    if not view.fast and scn.gop:
        want_tags.insert(-1, "EBS")  # the shipped build announces its pre-EBS stall first
    tags = [m.tag for m in view.result.markers]
    if tags != want_tags:
        return [f"marker tags {tags}, expected {want_tags}"]
    problems = []
    expect_fields(problems, "BEGIN", view.marker("BEGIN"), {
        "BUILD": view.outcome.build_id, "VARIANT": "PSCI" if variant.psci else "DEFAULT",
        "RESET": "OFF" if "PROBE_NO_RESET=1" in variant.defines else "ON",
        "FAST": "1" if view.fast else "0"})
    expect(problems, "PREEBS CURRENTEL", field_num(view.marker("PREEBS"), "CURRENTEL"),
           8 if scn.el2 else 4)
    fw = view.marker("FW")
    if field_num(fw, "REV") is None or not fw.get("VENDOR"):
        problems.append(f"FW marker unreadable: {fw}")
    gop, layout = view.marker("GOP"), view.marker("LAYOUT")
    if scn.gop:
        w, h, stride, size = (field_num(gop, k) for k in ("W", "H", "STRIDE", "SIZE"))
        if None in (w, h, stride, size) or not (0 < w <= stride and h > 0 and size >= stride * h * 4):
            problems.append(f"GOP geometry is inconsistent: {gop}")
        if gop.get("FMT") not in ("BGRX", "RGBX"):
            problems.append(f"GOP FMT {gop.get('FMT')!r} is not BGRX or RGBX")
        expect_fields(problems, "GOP", gop, {"SEL": scn.sel})
        scale, cols, rows, pages = (field_num(layout, k) for k in ("SCALE", "COLS", "ROWS", "PAGES"))
        if None in (scale, cols, rows, pages) or not (
                screenlib.MIN_SCALE <= scale <= screenlib.MAX_SCALE
                and 1 <= cols <= screenlib.MAX_COLS and rows >= 1 and 1 <= pages <= MAX_PAGES):
            problems.append(f"LAYOUT marker is out of range: {layout}")
    else:
        expect_fields(problems, "GOP", gop, {"STATE": "NONE"})
        expect_fields(problems, "LAYOUT", layout, {"STATE": "NONE"})
    cfg = view.marker("CFG")
    if field_num(cfg, "N") is None or any(
            cfg.get(k) not in ("0", "1") for k in ("ACPI20", "ACPI10", "SMBIOS", "SMBIOS3")):
        problems.append(f"CFG marker unreadable: {cfg}")
    acpi = view.marker("ACPI")
    expect_fields(problems, "ACPI", acpi, {"RSDP": "OK", "REJ": "0", "DUP": "0"})
    if acpi.get("PSCI") not in ("SMC", "HVC", "NONE"):
        problems.append(f"ACPI PSCI={acpi.get('PSCI')!r}")
    expect_fields(problems, "SMBIOS", view.marker("SMBIOS"), {"STATE": "OK"})
    memmap = view.marker("MEMMAP")
    expect_fields(problems, "MEMMAP", memmap, {"OK": "1"})
    count, desc = field_num(memmap, "COUNT"), field_num(memmap, "DESC")
    if count is None or desc is None or count < 1 or desc < 40:
        problems.append(f"MEMMAP marker is out of range: {memmap}")
    dump = view.marker("DUMP")
    if scn.disk == "usb":
        expect_fields(problems, "DUMP", dump, {"STATE": "OK", "RUN": "1"})
    else:
        expect_fields(problems, "DUMP", dump, {"STATE": "REFUSED NOT USB", "RUN": "0",
                                               "FILES": "0", "BYTES": "0"})
    ebs = [m.fields for m in view.result.markers if m.tag == "EBS"]
    if not scn.gop:
        expect_fields(problems, "EBS", ebs[-1], {"STATE": "SKIPPED", "REASON": "NO USABLE GOP"})
    else:
        expect_fields(problems, "EBS", ebs[-1], {"STATE": "CALLING"})
        if not view.fast:
            expect_fields(problems, "EBS notice", ebs[0], {"IN_SECONDS": "3"})
    return problems


def stick_problems(view, extra=()):
    """`extra` names stick files the harness put beside the boot image, outside the dump."""
    stick = view.result.stick
    if stick is None:
        return [f"the stick could not be read back: {view.result.stick_error}"]
    files, run = stick.files, view.files
    problems = list(checks.dump_problems(files, RUN_DIR, view.marker("DUMP")))
    if files.get(BOOT_FILE) != view.outcome.efi:
        problems.append(f"{BOOT_FILE} is not the image that was booted")
    stray = [p for p in files if p != BOOT_FILE and p not in extra and not p.startswith(RUN_DIR + "/")]
    badnames = [n for n in run if n not in RUN_PLAIN and not TABLE_NAME.fullmatch(n)]
    missing = [n for n in (*REQUIRED_TABLES, *sorted(RUN_PLAIN)) if n not in run]
    for what, names in (("stray files", stray), ("unexpected names", badnames),
                        ("missing files", missing), ("unexpected directories", sorted(
                            set(stick.dirs) - STICK_DIRS - {p.rpartition("/")[0] for p in extra}))):
        if names:
            problems.append(f"{what}: {names}")
    expect(problems, "RSDP.bin length", len(run.get("RSDP.bin", b"")), 36)
    expect(problems, "summary dump_complete_so_far", view.summary.get("dump_complete_so_far"), "yes")
    return problems


def summary_problems(view):
    summary, scn = view.summary, view.scn
    if not summary:
        return ["summary.txt is missing or unreadable"]
    problems = []
    for key, tag, field, numeric in SUMMARY_LINKS:
        if not scn.gop and key.startswith(("gop_", "layout_")):
            continue
        got, want = summary.get(key), view.marker(tag).get(field)
        same = (field_num(summary, key) == field_num(view.marker(tag), field)
                and got is not None) if numeric else got == want
        if not same:
            problems.append(f"summary {key}={got!r} but marker {tag}.{field}={want!r}")
    begin = view.marker("BEGIN")
    expect(problems, "summary variant", summary.get("variant"), begin.get("VARIANT", "").lower())
    expect(problems, "summary reset", summary.get("reset"), begin.get("RESET", "").lower())
    for key in ("acpi_rsdp", "acpi_root_status", "smbios"):
        expect(problems, f"summary {key}", summary.get(key), "OK")
    if scn.gop:
        fmt = {"RGBX": 0, "BGRX": 1}.get(view.marker("GOP").get("FMT"))
        expect(problems, "summary gop_format", field_num(summary, "gop_format"), fmt)
    else:
        expect(problems, "summary gop", summary.get("gop"), "none")
        leaked = sorted(k for k in summary if k.startswith(("gop_", "layout_")))
        if leaked:
            problems.append(f"summary keeps GOP/layout keys without a GOP: {leaked}")
    return problems


def reading_problems(view):
    """The probe's conclusions against a second, independent reading of the raw files."""
    run, summary, scn = view.files, view.summary, view.scn
    facp = fadt_facts(run.get("FACP.bin", b""))
    rsdp = rsdp_facts(run.get("RSDP.bin", b""))
    smbios = smbios_facts(run.get("smbios-ep.raw", b""), run.get("smbios-table.raw", b""))
    if not (facp and rsdp and smbios):
        return ["FACP.bin, RSDP.bin or the SMBIOS files are too short to read independently"]
    problems = []
    revision, minor, arm, conduit = facp
    acpi = view.marker("ACPI")
    expect(problems, "PSCI conduit in the ACPI marker", acpi.get("PSCI"), conduit)
    expect(problems, "PSCI conduit in the summary", summary.get("psci_conduit"), conduit)
    # OBSERVED on the QEMU virt board: the FADT advertises SMC for an EL2 guest, HVC for EL1.
    expect(problems, "PSCI conduit for this QEMU guest level", conduit, "SMC" if scn.el2 else "HVC")
    expect(problems, "fadt_revision", field_num(summary, "fadt_revision"), revision)
    expect(problems, "fadt_minor", field_num(summary, "fadt_minor"), minor)
    expect(problems, "fadt_arm_boot_arch", field_num(summary, "fadt_arm_boot_arch"), arm)
    expect(problems, "acpi_root", summary.get("acpi_root", "").upper(), rsdp[1])
    expect(problems, "smbios major/minor/table length",
           tuple(field_num(summary, k) for k in ("smbios_major", "smbios_minor", "smbios_table_len")),
           smbios)
    tables = sum(1 for n in run if n.endswith(".bin") and n != "RSDP.bin")
    expect(problems, "ACPI OK against the .bin files", field_num(acpi, "OK"), tables)
    cfg, listed = view.marker("CFG"), summary.get("cfg", [])
    counts = {"CFG marker": field_num(cfg, "N"), "summary config_tables":
              field_num(summary, "config_tables"), "summary cfg lines": len(listed)}
    if len(set(counts.values())) != 1:
        problems.append(f"configuration table counts disagree: {counts}")
    guids = {line.split()[0].upper() for line in listed if line.split()}
    for field, guid in (("ACPI20", ACPI20_GUID), ("ACPI10", ACPI10_GUID),
                        ("SMBIOS", SMBIOS_GUID), ("SMBIOS3", SMBIOS3_GUID)):
        expect(problems, f"CFG {field} flag", cfg.get(field) == "1", guid in guids)
    text = kit.text_of(run.get("memmap.txt", b""))
    try:
        header, rows = checks.parse_memmap_txt(text)
    except ValueError as exc:
        return problems + [f"memmap.txt does not parse: {exc}"]
    raw, mem = run.get("memmap.raw", b""), view.marker("MEMMAP")
    desc = field_num(mem, "DESC") or 0
    sizes = {"marker": field_num(mem, "COUNT"), "memmap.txt header": header.get("count"),
             "memmap.txt rows": len(rows), "memmap.raw": len(raw) // desc if desc else None}
    if len(set(sizes.values())) != 1 or (desc and len(raw) % desc):
        problems.append(f"memory map entry counts disagree: {sizes}")
    expect(problems, "ram_lo/ram_hi",
           (field_num(summary, "ram_lo"), field_num(summary, "ram_hi")), ram_bounds(text))
    return problems


def geometry_problems(view):
    gop, layout = view.marker("GOP"), view.marker("LAYOUT")
    w, h = field_num(gop, "W"), field_num(gop, "H")
    scale, cols, rows, pages = (field_num(layout, k) for k in ("SCALE", "COLS", "ROWS", "PAGES"))
    problems = set()
    for shot in view.clean_shots:
        got = (shot.width, shot.height, shot.scale, shot.total_rows)
        want = (w, h, scale, screenlib.HEADER_ROWS + (rows or 0) + 1)
        if got != want:
            problems.add(f"decoded (width, height, scale, total_rows) {got}, expected {want}")
        if shot.cols < (cols or 0):
            problems.add(f"decoded {shot.cols} column(s), layout marker says {cols}")
        page, total = page_of(shot)
        # The not-EL2 halt path lays the screen out again, so its page count differs.
        total_fits = 1 <= total <= MAX_PAGES if not view.scn.el2 else total == pages
        if not total_fits:
            problems.add(f"page total {total} against LAYOUT PAGES={pages}")
    return sorted(problems)


def frame_problems(view):
    samples, clean = view.result.samples, view.clean_shots
    seen = {page_of(shot)[0] for shot in clean}
    totals = {page_of(shot)[1] for shot in clean}
    problems = []
    # A rotation shows each page once, so a two-page halt screen can supply only two frames.
    need = min(3, max(totals, default=3))
    if len(clean) < need:
        problems.append(f"only {len(clean)} clean frame(s) out of {len(samples)}, need {need}")
    spoiled = [s.screen for s in samples if not is_clean(s.screen)]
    if len(spoiled) > max(1, len(samples) // 10):
        reasons = sorted({why_unclean(s) for s in spoiled})[:4]
        problems.append(f"{len(spoiled)} of {len(samples)} frames are not clean: {reasons}")
    if len(totals) > 1:
        problems.append(f"the page total changes between frames: {sorted(totals)}")
    if view.scn.full_pages and totals:
        missing = sorted(set(range(1, max(totals) + 1)) - seen)
        if missing:
            problems.append(f"pages never observed: {missing} of {max(totals)}")
    return problems


def title_problems(view):
    title = "SP11 EL2 PROBE%s B=%s" % ("+PSCI" if view.variant.psci else "", view.outcome.build_id)
    level = 2 if view.scn.el2 else 1
    status = re.compile(rf"CURRENTEL={level} PAGE \d+/\d+")
    problems = set()
    for shot in view.clean_shots:
        heads = [row.check.payload.strip() for row in shot.headers]
        if title not in heads:
            problems.add(f"header rows {heads} lack the title {title!r}")
        if not any(status.fullmatch(h) for h in heads):
            problems.add(f"header rows {heads} lack 'CURRENTEL={level} PAGE k/N'")
    return sorted(problems)


def expected_facts(view):
    """Exact fact lines derivable from the markers and, when a dump exists, from the dump."""
    mark = view.marker
    fw, gop, layout, cfg, acpi, mem, dump = (
        mark(t) for t in ("FW", "GOP", "LAYOUT", "CFG", "ACPI", "MEMMAP", "DUMP"))
    variant, run = view.variant, field_num(dump, "RUN")

    def yn(flag):
        return "Y" if flag == "1" else "N"

    facts = {
        "FW REV": "FW REV=%08X" % field_num(fw, "REV"),
        "FW VENDOR": "FW VENDOR=" + fw["VENDOR"],
        "GOP SIZE-PX": f"GOP {gop['W']}X{gop['H']} SEL={gop['SEL']}",
        "GOP STRIDE": f"GOP STRIDE={gop['STRIDE']} FMT={gop['FMT']}",
        "GOP BASE": "GOP BASE=%016X" % field_num(gop, "BASE"),
        "GOP SIZE": "GOP SIZE=%016X" % field_num(gop, "SIZE"),
        "LAYOUT": "LAYOUT S={SCALE} C={COLS} R={ROWS} P={PAGES}".format(**layout),
        "CFG N": f"CFG N={cfg['N']} ACPI20={yn(cfg['ACPI20'])} ACPI10={yn(cfg['ACPI10'])}",
        "CFG SMBIOS": f"CFG SMBIOS={yn(cfg['SMBIOS'])} SMBIOS3={yn(cfg['SMBIOS3'])}",
        "ACPI OK": f"ACPI OK={acpi['OK']} REJ={acpi['REJ']} DUP={acpi['DUP']}",
        "MEMMAP": f"MEMMAP N={mem['COUNT']} DESCSZ={mem['DESC']}",
        "DUMP STATE": "DUMP STATE=" + dump["STATE"],
        "DUMP RUN": "DUMP RUN=NONE" if not run else
                    f"DUMP RUN={run:02d} FILES={dump['FILES']} BYTES={dump['BYTES']}",
        "VARIANT": "VARIANT=%s RESET=%s PSCI=%s" % (
            "PSCI" if variant.psci else "DEFAULT",
            "OFF" if "PROBE_NO_RESET=1" in variant.defines else "ON",
            "ON" if variant.psci else "OFF"),
        "EBS ATTEMPTS": "EBS ATTEMPTS=1",
    }
    files = view.files
    facp = fadt_facts(files.get("FACP.bin", b""))
    if facp:
        facts["FADT"] = "FADT REV=%d MINOR=%d ARMBOOT=%04X" % facp[:3]
        facts["PSCI CONDUIT"] = "PSCI CONDUIT=" + facp[3]
    rsdp = rsdp_facts(files.get("RSDP.bin", b""))
    if rsdp:
        facts["ACPI RSDP"] = "ACPI RSDP REV=%d ROOT=%s" % rsdp
    smbios = smbios_facts(files.get("smbios-ep.raw", b""), files.get("smbios-table.raw", b""))
    if smbios:
        facts["SMBIOS"] = "SMBIOS V%d.%d LEN=%d" % smbios
    bounds = ram_bounds(kit.text_of(files.get("memmap.txt", b"")))
    if bounds:
        facts["RAM"] = "RAM %016X-%016X" % bounds
    return facts


def fact_problems(view):
    try:
        expected = expected_facts(view)
    except (KeyError, TypeError, ValueError) as exc:
        return [f"cannot derive the expected facts from the markers: {exc!r}"]
    problems = []
    for name, prefix, shape in FACT_FAMILIES:
        shown = sorted({line for line in view.shown if prefix.match(line)})
        if not shown:
            problems.append(f"{name}: never shown")
        elif any(not shape.fullmatch(line) for line in shown):
            problems.append(f"{name}: unexpected shape {shown}")
        elif len(shown) > 1:
            problems.append(f"{name}: shown differently over time {shown}")
        elif name in expected and shown[0] != expected[name]:
            problems.append(f"{name}: shown {shown[0]!r}, expected {expected[name]!r}")
    return problems


def register_problems(pages):
    """Presence, stability and derivation of the register lines in a set of decoded pages."""
    seen, problems = {}, []
    for lines in pages:
        for label, value in checks.parse_registers(lines).items():
            seen.setdefault(label, set()).add(value)
    shown = {line for lines in pages for line in lines}
    regs = {label: min(values) for label, values in seen.items()}
    for label, values in sorted(seen.items()):
        if len(values) > 1:
            problems.append(f"{label} changes between frames: {sorted(hex(v) for v in values)}")
    problems += [f"{label} is not shown as LABEL=<16 hex digits>"
                 for label in ALWAYS_SHOWN if label not in regs]
    problems += [f"{label} is shown, but the passive probe must never read it"
                 for label in NEVER_READ if label in regs]
    for label, gate, hi, lo, reason in GATED:
        if gate not in regs:
            continue
        implemented = checks.bits(regs[gate], hi, lo) != 0
        skipped = f"{label}=SKIPPED {reason}" in shown
        if implemented != (label in regs) or implemented == skipped:
            problems.append(f"{label}: gated on {gate}[{hi}:{lo}]={'nonzero' if implemented else 0} "
                            f"but shown={label in regs}, skipped={skipped}")
    derived = checks.expected_lines(regs)
    problems += [f"derived line {line!r} is not shown" for line in derived if line not in shown]
    problems += [f"derived-looking line {line!r} is not what the registers imply"
                 for line in sorted(shown) if DERIVED.match(line) and line not in derived]
    if "MPIDR_EL1" in regs and not regs["MPIDR_EL1"] >> 31 & 1:
        problems.append("MPIDR_EL1 bit 31 (RES1) is clear")
    if regs.get("CNTFRQ_EL0", 1) == 0:
        problems.append("CNTFRQ_EL0 reads zero")
    return problems


def footer_problems(scn, footers):
    """Legal text and order of the footers of one scenario's clean frames, oldest first."""
    problems, ranks, counts = [], [], []
    for text in footers:
        count = COUNTDOWN.fullmatch(text)
        if text in STAGED_FOOTERS:
            ranks.append((0, 0))
        elif scn.hold and text == scn.hold:
            ranks.append((1, 0))
        elif count and not scn.hold:
            counts.append(int(count.group(1)))
            ranks.append((1, -counts[-1]))
        elif text == RESETTING and not scn.hold:
            ranks.append((2, 0))
        else:
            problems.append(f"unexpected footer {text!r}")
    if ranks != sorted(ranks):
        steps = [t for i, t in enumerate(footers) if i == 0 or t != footers[i - 1]]
        problems.append("footer order is not staged, then countdown, then reset: " + " | ".join(steps))
    if scn.hold and footers and footers[-1] != scn.hold:
        problems.append(f"the last footer is {footers[-1]!r}, expected {scn.hold!r}")
    if scn.ends_itself and (not counts or counts[0] > FAST_COUNTDOWN_START
                            or counts[0] - counts[-1] < 3):
        problems.append(f"countdown {counts[:1]}..{counts[-1:]}: expected a start of at most "
                        f"{FAST_COUNTDOWN_START} and at least 3 s of progress")
    return problems


def ending_problems(view):
    scn, res = view.scn, view.result
    problems = []
    if scn.ends_itself:
        if not res.ended_by_guest:
            problems.append("the guest did not end the run itself within the capture window")
        elif res.exit_code != 0:
            problems.append(f"QEMU exit status {res.exit_code} after the guest's reset")
        if res.t_ebs is None or res.t_end - res.t_ebs < 10:
            problems.append(f"the run ended {res.t_end - (res.t_ebs or 0):.1f} s after "
                            "ExitBootServices; a countdown needs at least 10 s")
    elif res.ended_by_guest:
        problems.append("the guest ended the run although this scenario must keep holding")
    return problems


# ---------------------------------------------------------------- case classes

class QemuCase(kit.Base):
    scenario_name = ""

    def view(self):
        outcome = scenario(self.scenario_name)
        if outcome.result.failure:
            self.fail(f"scenario {self.scenario_name} did not complete "
                      f"(attempts={outcome.attempts}): {outcome.result.failure}")
        return View(outcome)

    def run_check(self, function):
        view = self.view()
        self.check(function(view), "last serial lines:\n" + kit.tail(view.result.serial, 8))


class _Markers:
    def test_serial_markers_follow_the_documented_order_and_values(self):
        self.run_check(marker_problems)


class _Dump:
    def test_stick_holds_the_boot_image_and_one_complete_run(self):
        self.run_check(stick_problems)

    def test_summary_agrees_with_the_serial_markers(self):
        self.run_check(summary_problems)

    def test_probe_conclusions_match_an_independent_reading_of_the_dump(self):
        self.run_check(reading_problems)


class _Screen:
    def test_frames_decode_cleanly_and_cover_every_page(self):
        self.run_check(frame_problems)

    def test_geometry_and_page_total_match_the_layout_marker(self):
        self.run_check(geometry_problems)

    def test_header_rows_carry_title_exception_level_and_page(self):
        self.run_check(title_problems)


class _Facts:
    def test_fact_lines_are_stable_and_agree_with_markers_and_dump(self):
        self.run_check(fact_problems)

    def test_no_psci_page_without_the_psci_variant(self):
        view = self.view()
        shown = sorted({line for line in view.shown if line.startswith(PSCI_PAGE_PREFIXES)})
        self.check([f"PSCI lines shown: {shown}"] if shown and not view.variant.psci else [])


class _Registers:
    def test_register_lines_are_present_stable_and_consistently_derived(self):
        self.run_check(lambda view: register_problems([payloads_of(s) for s in view.clean_shots]))


class _Footers:
    def test_footer_text_and_order(self):
        self.run_check(lambda view: footer_problems(
            view.scn, [footer_of(shot) for shot in view.clean_shots]))


class _Ending:
    def test_the_run_ends_the_way_the_variant_says(self):
        self.run_check(ending_problems)


class QemuDefaultTests(_Markers, _Dump, _Screen, _Facts, _Registers, _Footers, _Ending, QemuCase):
    scenario_name = "default"


class QemuNoUsbTests(_Markers, _Screen, _Facts, _Registers, _Footers, _Ending, QemuCase):
    scenario_name = "no-usb"

    def test_dump_is_refused_and_nothing_is_written_to_the_disk(self):
        view = self.view()
        stick = view.result.stick
        if stick is None:
            self.fail(f"the disk could not be read back: {view.result.stick_error}")
        problems = []
        expect(problems, "files on a refused disk", sorted(stick.files), [BOOT_FILE])
        if stick.files.get(BOOT_FILE) != view.outcome.efi:
            problems.append("the booted image changed")
        self.check(problems)
        self.assertIn("DUMP STATE=REFUSED NOT USB", view.shown)
        self.assertIn("DUMP RUN=NONE", view.shown)


class QemuEl1Tests(_Markers, _Dump, _Screen, _Facts, _Footers, _Ending, QemuCase):
    scenario_name = "el1"

    def test_halt_page_names_the_exception_level_and_reads_no_other_register(self):
        view = self.view()
        lines = set(view.shown)
        want = {"NOT AT EL2", "NO OTHER REGISTERS READ", "CURRENTEL RAW=0000000000000004 EL=1",
                "PREEBS EL=1 RAW=0000000000000004"}
        problems = [f"line {line!r} is never shown" for line in sorted(want - lines)]
        regs = checks.parse_registers(view.shown)
        problems += [f"register {label} is shown at EL1" for label in sorted(set(regs) - {"CURRENTEL RAW"})]
        problems += [f"derived line {line!r} is shown at EL1" for line in sorted(lines)
                     if DERIVED.match(line)]
        self.check(problems)


class QemuPsciTests(_Markers, _Dump, _Screen, _Facts, _Registers, _Footers, _Ending, QemuCase):
    scenario_name = "psci"

    def test_psci_page_reports_a_successful_cpu_on_to_a_secondary_at_el2(self):
        view = self.view()
        lines = set(view.shown)
        want = {"PSCI RUN=DONE", "CPU_ON RET=00000000 SUCCESS", "SECONDARY ARRIVED=YES",
                "SECONDARY EL=2 RAW=0000000000000008", "== PSCI (OPT-IN VARIANT) ==",
                "VARIANT=PSCI RESET=ON PSCI=ON"}
        problems = [f"line {line!r} is never shown" for line in sorted(want - lines)]

        def hexed(pattern):
            found = [re.fullmatch(pattern, line) for line in lines]
            return [m.group(1) for m in found if m]

        version, target, mpidr = (hexed(rf"{p}=([0-9A-F]{{{n}}})") for p, n in (
            ("PSCI_VERSION", 8), ("CPU_ON TARGET", 16), ("SECONDARY MPIDR", 16)))
        if len(version) != 1 or len(target) != 1 or len(mpidr) != 1:
            problems.append(f"PSCI_VERSION {version}, CPU_ON TARGET {target}, SECONDARY MPIDR {mpidr}")
        # EXPERIMENTAL: under QEMU the target is the affinity value of CPU 1.
        elif int(target[0], 16) & 0xFFFFFF != int(mpidr[0], 16) & 0xFFFFFF:
            problems.append(f"secondary MPIDR {mpidr[0]} does not match target {target[0]}")
        self.check(problems)


class QemuNoGopTests(_Markers, _Dump, QemuCase):
    scenario_name = "no-gop"

    def test_exit_boot_services_is_skipped_and_nothing_is_drawn(self):
        view = self.view()
        res = view.result
        problems = []
        expect(problems, "frames captured", len(res.samples), 0)
        expect(problems, "t_ebs", res.t_ebs, None)
        expect(problems, "ended_by_guest", res.ended_by_guest, False)
        self.check(problems)


class QemuNoResetTests(_Markers, _Dump, _Screen, _Facts, _Registers, _Footers, _Ending, QemuCase):
    scenario_name = "no-reset"


class QemuShippedTests(_Markers, _Dump, _Footers, _Ending, QemuCase):
    scenario_name = "shipped"

    def test_first_frames_decode_cleanly_with_the_real_timing(self):
        view = self.view()
        problems = geometry_problems(view) + title_problems(view)
        if not view.clean_shots:
            problems.append("no clean frame was captured")
        first = next((int(m.group(1)) for shot in view.clean_shots
                      if (m := COUNTDOWN.fullmatch(footer_of(shot)))), None)
        if first is None or not SHIPPED_COUNTDOWN_MIN <= first <= 60:
            problems.append(f"first countdown value {first}: the shipped timing starts at 60 s")
        lines = view.shown
        problems += [f"register line {label} not shown" for label in
                     ("HCR_EL2", "SCTLR_EL2", "MPIDR_EL1")
                     if label not in checks.parse_registers(lines)]
        self.check(problems)


class ScenarioTableTests(kit.Base):
    def test_table_is_consistent_and_every_scenario_has_a_case_class(self):
        classes, pending = set(), [QemuCase]
        while pending:  # case classes may live in other suite modules that registered scenarios
            for sub in pending.pop().__subclasses__():
                classes.add(sub.scenario_name)
                pending.append(sub)
        problems = []
        expect(problems, "ORDER against SCENARIOS", sorted(ORDER), sorted(SCENARIOS))
        expect(problems, "case classes against SCENARIOS", sorted(classes), sorted(SCENARIOS))
        for scn in SCENARIOS.values():
            if scn.variant not in kit.bup.VARIANTS:
                problems.append(f"{scn.name}: unknown variant {scn.variant}")
            if scn.stop not in _STOPS:
                problems.append(f"{scn.name}: unknown stop rule {scn.stop!r}")
            if scn.ends_itself and (scn.stop or scn.hold):
                problems.append(f"{scn.name}: a run that ends itself needs no stop rule or hold footer")
            if scn.gop and not scn.ends_itself and not scn.stop:
                problems.append(f"{scn.name}: would capture until the timeout")
            if scn.hold and scn.hold not in HOLD_FOOTERS:
                problems.append(f"{scn.name}: unknown hold footer {scn.hold!r}")
        self.check(problems)


def _shot(page, total, footer, bad=0, ok=True):
    head = types.SimpleNamespace(check=types.SimpleNamespace(payload=f"CURRENTEL=2 PAGE {page}/{total}"))
    foot = types.SimpleNamespace(check=types.SimpleNamespace(payload=footer))
    return types.SimpleNamespace(ok=ok, headers=[head], footer=foot, body=[],
                                 bad_rows=lambda: [None] * bad)


def _result(*shots):
    return types.SimpleNamespace(samples=[types.SimpleNamespace(screen=s) for s in shots])


def _facp(revision=6, length=276, arm=1):
    table = bytearray(276)
    table[8], table[131] = revision, 3
    struct.pack_into("<I", table, 4, length)
    struct.pack_into("<H", table, 129, arm)
    return bytes(table)


class AssertionHelperTests(kit.Base):
    """The checks above must reject faulty recordings, not only accept good ones."""

    def test_stop_rules_need_every_page_and_a_clean_countdown_frame(self):
        first, second = _shot(1, 2, "ALL STAGES DONE"), _shot(2, 2, "RESET IN 9 S")
        self.assertFalse(_all_pages_seen(_result()))
        self.assertFalse(_all_pages_seen(_result(first, first)))
        self.assertFalse(_all_pages_seen(_result(first, _shot(2, 2, "x", bad=1))))
        self.assertTrue(_all_pages_seen(_result(first, second)))
        self.assertFalse(_countdown_seen(_result(first)))
        self.assertFalse(_countdown_seen(_result(_shot(1, 2, "RESET IN 9 S", bad=1))))
        self.assertTrue(_countdown_seen(_result(first, second)))

    def test_footer_rules_reject_wrong_text_order_and_countdowns(self):
        default, hold = SCENARIOS["default"], SCENARIOS["no-reset"]
        good = ["ALL STAGES DONE", "RESET IN 16 S", "RESET IN 14 S", "RESET IN 12 S", "RESETTING NOW"]
        self.assertEqual(footer_problems(default, good), [])
        self.assertEqual(footer_problems(hold, ["ALL STAGES DONE", hold.hold, hold.hold]), [])
        bad = {
            "rising countdown": (default, ["RESET IN 14 S", "RESET IN 16 S", "RESET IN 10 S"]),
            "failed reset": (default, ["RESET IN 16 S", "RESET IN 8 S", "RESETSYSTEM DID NOT RESET"]),
            "staged after countdown": (default, ["RESET IN 16 S", "ALL STAGES DONE", "RESET IN 8 S"]),
            "countdown after reset": (default, ["RESET IN 16 S", "RESETTING NOW", "RESET IN 8 S"]),
            "countdown too short": (default, ["RESET IN 16 S", "RESET IN 15 S"]),
            "shipped countdown in a fast run": (default, ["RESET IN 60 S", "RESET IN 50 S"]),
            "no countdown at all": (default, ["ALL STAGES DONE"]),
            "countdown while holding": (hold, ["RESET IN 16 S", hold.hold]),
            "wrong last footer": (hold, [hold.hold, "ALL STAGES DONE"]),
        }
        for what, (scn, footers) in bad.items():
            with self.subTest(what):
                self.assertTrue(footer_problems(scn, footers), what)

    def test_register_rules_reject_missing_unstable_and_misderived_lines(self):
        raw = {label: 0x10 for label in ALWAYS_SHOWN}
        raw.update({"SCTLR_EL2": 0x100D, "MPIDR_EL1": 0x80000000, "CNTFRQ_EL0": 1000,
                    "ID_AA64PFR0_EL1": 0, "ID_AA64PFR1_EL1": 0, "ID_AA64MMFR1_EL1": 0})
        lines = [f"{label}={value:016X}" for label, value in raw.items()]
        lines += ["TTBR1_EL2=SKIPPED VH=0", "ICC_SRE_EL2=SKIPPED GIC=0",
                  "ID_AA64ZFR0_EL1=SKIPPED SVE=0", "ID_AA64SMFR0_EL1=SKIPPED SME=0"]
        lines += checks.expected_lines(raw)
        self.assertEqual(register_problems([lines]), [])

        def drop(prefix):
            return [line for line in lines if not line.startswith(prefix)]

        def swap(old, new):
            return [line.replace(old, new) for line in lines]

        bad = {
            "missing register": drop("TCR_EL2="),
            "missing derived line": drop("MMU "),
            "stray derived line": lines + ["HCR VM=1 TGE=1 RW=1 E2H=1"],
            "register shown though gated off": lines + ["TTBR1_EL2=0000000000000000"],
            "a register the passive probe must not read": lines + ["ID_AA64MMFR4_EL1=000000000E100000"],
            "gated register not marked skipped": drop("ICC_SRE_EL2="),
            "clear RES1 bit": swap("MPIDR_EL1=0000000080000000", "MPIDR_EL1=0000000000000000"),
            "zero counter frequency": swap("CNTFRQ_EL0=00000000000003E8",
                                           "CNTFRQ_EL0=0000000000000000"),
        }
        for what, mutated in bad.items():
            with self.subTest(what):
                self.assertTrue(register_problems([mutated]), what)
        with self.subTest("value changes between frames"):
            changed = swap("SCTLR_EL2=000000000000100D", "SCTLR_EL2=000000000000100C")
            self.assertTrue(register_problems([lines, changed]))

    def test_table_readers_cover_every_conduit_branch_and_short_input(self):
        self.assertEqual(fadt_facts(_facp()), (6, 3, 1, "SMC"))
        self.assertEqual(fadt_facts(_facp(arm=3)), (6, 3, 3, "HVC"))
        for what, table in {"revision 4": _facp(revision=4), "length 130": _facp(length=130),
                            "bit 0 clear": _facp(arm=2), "reserved bit": _facp(arm=5)}.items():
            with self.subTest(what):
                self.assertEqual(fadt_facts(table)[3], "NONE")
        self.assertIsNone(fadt_facts(b"short"))
        rsdp = bytearray(36)
        rsdp[15] = 2
        self.assertEqual(rsdp_facts(bytes(rsdp)), (2, "RSDT"))
        struct.pack_into("<Q", rsdp, 24, 0x1000)
        self.assertEqual(rsdp_facts(bytes(rsdp)), (2, "XSDT"))
        rsdp[15] = 0
        self.assertEqual(rsdp_facts(bytes(rsdp)), (0, "RSDT"))
        self.assertIsNone(rsdp_facts(b"x" * 8))
        sm3 = b"_SM3_" + bytes([0, 24, 3, 7, 0, 1])
        sm2 = b"_SM_" + bytes([0, 31, 2, 8])
        self.assertEqual(smbios_facts(sm3, b"t" * 5), (3, 7, 5))
        self.assertEqual(smbios_facts(sm2, b""), (2, 8, 0))
        self.assertIsNone(smbios_facts(b"_SM3_", b""))
        self.assertIsNone(smbios_facts(b"junk", b""))

    def test_frame_and_marker_helpers_reject_spoiled_frames(self):
        good = _shot(1, 5, "RESET IN 3 S")
        self.assertTrue(is_clean(good))
        self.assertEqual(page_of(good), (1, 5))
        self.assertFalse(is_clean(_shot(1, 5, "x", bad=1)))
        self.assertFalse(is_clean(_shot(1, 5, "x", ok=False)))
        self.assertFalse(is_clean(None))
        self.assertEqual([num("0x10"), num("08"), num(" 12 ")], [16, 8, 12])
        self.assertIsNone(field_num({"A": "zz"}, "A"))
        self.assertIsNone(field_num({}, "A"))

    def test_frame_rules_need_a_rotation_and_tolerate_one_torn_frame(self):
        def view(*shots, full=True):
            return types.SimpleNamespace(
                result=_result(*shots), clean_shots=[s for s in shots if is_clean(s)],
                scn=types.SimpleNamespace(full_pages=full))

        five = [_shot(n, 5, "RESET IN 9 S") for n in range(1, 6)]
        halt = [_shot(n, 2, "HALTED - NO RESET") for n in (1, 2)]
        torn = _shot(1, 5, "x", bad=1)
        self.assertEqual(frame_problems(view(*five)), [])
        self.assertEqual(frame_problems(view(*halt)), [])
        self.assertEqual(frame_problems(view(*five, torn)), [])
        cases = {
            "no frame at all": view(),
            "two frames of a five-page rotation": view(*five[:2], full=False),
            "a page never shown": view(*five[:2], *five[3:]),
            "the page total changes": view(five[0], _shot(2, 4, "x"), five[2]),
            "two torn frames in seven": view(*five, torn, torn),
        }
        for what, case in cases.items():
            with self.subTest(what):
                self.assertTrue(frame_problems(case), what)
