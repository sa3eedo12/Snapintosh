# SPDX-License-Identifier: MIT
"""Dump run rotation of the SP11 EL2 probe (QEMU virt with EDK2).

The operator is expected to run the probe more than once on the same stick (the
default image, then the PSCI image), so a later run must land in a new
SP11DUMP/RUNnn and leave every earlier run alone.  These scenarios boot the real
image on a stick that already holds earlier runs.  Each earlier run holds one file
named SUMMARY.TXT, which is also the name the probe gives its own summary (FAT
names are case-insensitive), so a probe that reused an old directory would collide
with it as well as write into it.

  rerun       RUN01, RUN02 and RUN04 exist: the first free slot, RUN03, is used
  last-slot   RUN01..RUN98 exist: RUN99, the last slot, is used
  full        RUN01..RUN99 exist: nothing is written, the probe says so and carries on

The scenarios register themselves in suite_qemu.SCENARIOS and ORDER on import.  The
dump caps (files, bytes per file, total bytes) are constants of dump.c that no
firmware in the emulator can reach; nothing here exercises them (see the README).
"""

from __future__ import annotations

import checks
import fatimg
import suite_qemu as qemu
import uefi_testkit as kit
from synth_dump import BOOT_FILE

LAST_SLOT = 99  # PROBE_DUMP_MAX_RUN
BASE_DIRS = frozenset({"EFI", "EFI/BOOT", "SP11DUMP"})


def run_dir(slot):
    return f"SP11DUMP/RUN{slot:02d}"


def earlier_runs(slots):
    """Seed pairs (stick path, bytes) for a stick that holds these runs, one file each."""
    return tuple((f"{run_dir(n)}/SUMMARY.TXT", f"earlier run {n}; must stay as it is\n".encode())
                 for n in slots)


SCENARIOS = {s.name: s for s in (
    qemu.Scenario("rerun", "fast", ends_itself=True, seed=earlier_runs((1, 2, 4))),
    qemu.Scenario("last-slot", "fast", ends_itself=True, seed=earlier_runs(range(1, LAST_SLOT))),
    qemu.Scenario("full", "fast", ends_itself=True, seed=earlier_runs(range(1, LAST_SLOT + 1))),
)}
qemu.SCENARIOS.update(SCENARIOS)
qemu.ORDER = tuple(dict.fromkeys(qemu.ORDER + tuple(SCENARIOS)))


def seeded_stick_problems(files, dirs, seed, new_run):
    """What differs from the seeded stick, apart from the boot image.

    `new_run` is the directory the probe must have created, or None when it must
    write nothing.  Every seeded file has to be byte-identical; nothing else may
    appear except below `new_run`.
    """
    problems = []
    for path, data in sorted(seed.items()):
        if path not in files:
            problems.append(f"{path} was deleted")
        elif files[path] != data:
            problems.append(f"{path} was rewritten ({len(files[path])} bytes, seeded {len(data)})")
    allowed = (new_run + "/",) if new_run else ()
    for path in sorted(set(files) - set(seed) - {BOOT_FILE}):
        if not path.startswith(allowed):
            problems.append(f"unexpected file {path}")
    want = BASE_DIRS | {p.rpartition("/")[0] for p in seed} | ({new_run} if new_run else set())
    if set(dirs) != want:
        problems.append(f"directories: unexpected {sorted(set(dirs) - want)[:5]}, "
                        f"missing {sorted(want - set(dirs))[:5]}")
    return problems


class _Rotation:
    """Checks shared by the rotation scenarios; `chosen` is the slot the probe must use, 0 for none."""

    chosen = 0

    def recorded(self):
        view = self.view()
        stick = view.result.stick
        if stick is None:
            self.fail(f"the stick could not be read back: {view.result.stick_error}")
        return view, stick

    def test_earlier_runs_and_the_boot_image_are_untouched(self):
        view, stick = self.recorded()
        seed = dict(qemu.SCENARIOS[self.scenario_name].seed)
        problems = seeded_stick_problems(stick.files, stick.dirs, seed,
                                         run_dir(self.chosen) if self.chosen else None)
        if stick.files.get(BOOT_FILE) != view.outcome.efi:
            problems.append(f"{BOOT_FILE} is not the image that was booted")
        self.check(problems, "last serial lines:\n" + kit.tail(view.result.serial, 8))

    def test_marker_and_screen_name_the_chosen_slot(self):
        view, _ = self.recorded()
        dump, problems = view.marker("DUMP"), []
        if self.chosen:
            qemu.expect_fields(problems, "DUMP", dump, {"STATE": "OK", "RUN": str(self.chosen)})
            want = {"DUMP STATE=OK",
                    f"DUMP RUN={self.chosen:02d} FILES={dump.get('FILES')} BYTES={dump.get('BYTES')}"}
        else:
            qemu.expect_fields(problems, "DUMP", dump, {"STATE": "NO FREE RUN SLOT", "RUN": "0",
                                                        "FILES": "0", "BYTES": "0"})
            want = {"DUMP STATE=NO FREE RUN SLOT", "DUMP RUN=NONE"}
        problems += [f"the screen never showed {line!r}" for line in sorted(want - set(view.shown))]
        self.check(problems, "last serial lines:\n" + kit.tail(view.result.serial, 8))

    def test_boot_services_are_still_exited_and_the_run_ends(self):
        view, _ = self.recorded()
        problems = []
        qemu.expect(problems, "marker tags", [m.tag for m in view.result.markers],
                    list(qemu.MARKER_TAGS))
        ebs = [m.fields for m in view.result.markers if m.tag == "EBS"]
        qemu.expect_fields(problems, "EBS", ebs[-1] if ebs else {}, {"STATE": "CALLING"})
        problems += qemu.ending_problems(view)
        self.check(problems, "last serial lines:\n" + kit.tail(view.result.serial, 8))


class _Dumped(_Rotation):
    def test_the_new_run_is_a_complete_dump(self):
        view, stick = self.recorded()
        slot_dir = run_dir(self.chosen)
        prefix = slot_dir + "/"
        names = {p[len(prefix):] for p in stick.files if p.startswith(prefix)}
        problems = list(checks.dump_problems(stick.files, slot_dir, view.marker("DUMP")))
        problems += [f"{slot_dir}: {n} is missing" for n in (*qemu.REQUIRED_TABLES, *sorted(qemu.RUN_PLAIN))
                     if n not in names]
        problems += [f"{slot_dir}: unexpected name {n}" for n in sorted(names)
                     if n not in qemu.RUN_PLAIN and not qemu.TABLE_NAME.fullmatch(n)]
        summary_bytes = stick.files.get(prefix + "summary.txt")
        try:
            summary = {} if summary_bytes is None else checks.parse_summary(kit.text_of(summary_bytes))
        except ValueError as exc:
            problems.append(f"summary.txt is unreadable: {exc}")
            summary = {}
        qemu.expect(problems, "summary dump_run", qemu.field_num(summary, "dump_run"), self.chosen)
        self.check(problems)


class RerunTests(_Dumped, qemu.QemuCase):
    scenario_name = "rerun"
    chosen = 3


class LastSlotTests(_Dumped, qemu.QemuCase):
    scenario_name = "last-slot"
    chosen = LAST_SLOT


class FullStickTests(_Rotation, qemu.QemuCase):
    scenario_name = "full"


class RotationHelperTests(kit.Base):
    """The checks above fail on damage they are meant to catch, and the seeds are what the names say."""

    @staticmethod
    def stick(slots, new_run=None):
        seed = dict(earlier_runs(slots))
        files = {BOOT_FILE: b"image", **seed}
        dirs = set(BASE_DIRS) | {p.rpartition("/")[0] for p in seed}
        if new_run:
            files[new_run + "/summary.txt"] = b"new"
            dirs.add(new_run)
        return seed, files, dirs

    def test_a_clean_stick_passes_and_each_kind_of_damage_is_named(self):
        seed, files, dirs = self.stick((1, 2, 4), run_dir(3))
        self.assertEqual(seeded_stick_problems(files, dirs, seed, run_dir(3)), [])
        first, second = run_dir(1) + "/SUMMARY.TXT", run_dir(2) + "/SUMMARY.TXT"
        damage = (
            ("was rewritten", lambda f, d: f.update({first: b"changed"})),
            ("was rewritten", lambda f, d: f.update({first: seed[first] + b"x"})),
            ("was deleted", lambda f, d: f.pop(second)),
            ("unexpected file", lambda f, d: f.update({run_dir(4) + "/ACPI.bin": b"x"})),
            ("unexpected file", lambda f, d: f.update({"SP11DUMP/stray.txt": b"x"})),
            ("directories", lambda f, d: d.add(run_dir(5))),
            ("directories", lambda f, d: d.discard(run_dir(3))),
            ("directories", lambda f, d: d.discard(run_dir(2))),
        )
        for needle, harm in damage:
            broken_files, broken_dirs = dict(files), set(dirs)
            harm(broken_files, broken_dirs)
            found = seeded_stick_problems(broken_files, broken_dirs, seed, run_dir(3))
            self.assertTrue(any(needle in p for p in found), f"{needle!r} not reported in {found}")

    def test_a_full_stick_must_stay_exactly_as_seeded(self):
        seed, files, dirs = self.stick(range(1, LAST_SLOT + 1))
        self.assertEqual(seeded_stick_problems(files, dirs, seed, None), [])
        files[run_dir(LAST_SLOT) + "/ACPI.bin"] = b"x"
        found = seeded_stick_problems(files, dirs, seed, None)
        self.assertTrue(any("unexpected file" in p for p in found), found)
        found = seeded_stick_problems(*self.stick(range(1, LAST_SLOT + 1), "SP11DUMP/RUN100")[1:],
                                      seed, None)
        self.assertTrue(any("unexpected file" in p for p in found) and
                        any("directories" in p for p in found), found)

    def test_seeds_hold_exactly_the_runs_each_scenario_name_promises(self):
        def slots(name):
            return sorted(int(path.split("/")[1][3:]) for path, _ in SCENARIOS[name].seed)

        problems = []
        qemu.expect(problems, "rerun slots", slots("rerun"), [1, 2, 4])
        qemu.expect(problems, "last-slot slots", slots("last-slot"), list(range(1, LAST_SLOT)))
        qemu.expect(problems, "full slots", slots("full"), list(range(1, LAST_SLOT + 1)))
        for name, scn in SCENARIOS.items():
            contents = [data for _, data in scn.seed]
            if len(set(contents)) != len(contents):
                problems.append(f"{name}: two earlier runs hold the same bytes, so a mix-up is invisible")
            if qemu.SCENARIOS.get(name) is not scn or name not in qemu.ORDER:
                problems.append(f"{name} is not registered in suite_qemu")
        self.check(problems)

    def test_the_fullest_stick_reads_back_exactly_as_it_was_built(self):
        scn = SCENARIOS["full"]
        image = fatimg.Fat16Image()
        image.add_file(BOOT_FILE, b"image")
        for path, data in scn.seed:
            image.add_file(path, data)
        tree = fatimg.read_fat16(image.to_bytes())
        self.check(seeded_stick_problems(tree.files, tree.dirs, dict(scn.seed), None))
