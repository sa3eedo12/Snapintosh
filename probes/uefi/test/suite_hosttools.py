# SPDX-License-Identifier: MIT
"""Host helpers around the dump checker, and the rules every authored file keeps.

* FatImageTests: the FAT16 writer behind the emulator's boot stick and the
  reader that recovers what the firmware wrote to it. The reader is how every
  dump assertion sees the stick, so long names, NT case flags, deleted entries
  and damaged cluster chains are exercised on hand-patched images, and the
  writer's output is judged by the host's own fsck.
* LineFormatTests, VerifyLinesTests: the CRC-8 that ends every screen row and
  the operator's transcription checker built on it.
* HostContractTests: constants the Python decoders share with the C probe.
* HygieneTests: licence tags, size limits, machine-specific text, binaries,
  syntax, unused imports, and that nothing links the probe into the monitor.
* DocsTests: the README against the tree it describes (every suite registered
  in the entry point and named there, every variant, helper and environment
  variable mentioned, example rows with valid CRCs, working links).
"""
from __future__ import annotations

import ast
import os
import random
import re
import shutil
import struct
import sys
import unittest
from pathlib import Path

import uefi_testkit as kit
import fatimg
import lines
import screen

ROOT = kit.ROOT
SECTOR = 512
ROOT_DIR_BYTES = 512 * 32


# ---- FAT image helpers -------------------------------------------------------


def layout(image):
    """Byte offsets of the first FAT and of the root directory."""
    reserved = struct.unpack_from("<H", image, 14)[0]
    nfats = image[16]
    fat_sectors = struct.unpack_from("<H", image, 22)[0]
    return reserved * SECTOR, (reserved + nfats * fat_sectors) * SECTOR


def root_entry(image, index):
    """Directory entry `index` of the root; entry 0 is the volume label."""
    offset = layout(image)[1] + 32 * index
    return image[offset:offset + 32]


def with_root_entries(image, entries):
    """The image with everything after the volume label in the root replaced by `entries`."""
    start = layout(image)[1] + 32
    data = bytearray(image)
    data[start:start - 32 + ROOT_DIR_BYTES] = bytes(ROOT_DIR_BYTES - 32)
    data[start:start + 32 * len(entries)] = b"".join(entries)
    return bytes(data)


def first_cluster(entry):
    return struct.unpack_from("<H", entry, 26)[0]


def set_fat(image, cluster, value):
    data = bytearray(image)
    struct.pack_into("<H", data, layout(image)[0] + 2 * cluster, value)
    return bytes(data)


def patched(image, offset, fmt, *values):
    data = bytearray(image)
    struct.pack_into(fmt, data, offset, *values)
    return bytes(data)


def lfn_checksum(short11):
    """The rotate-and-add checksum that ties long-name entries to their short entry."""
    total = 0
    for byte in short11:
        total = ((((total & 1) << 7) | (total >> 1)) + byte) & 0xFF
    return total


def long_name_entries(name, short11, checksum=None):
    """Long-name directory entries in the order a FAT driver writes them (last fragment first)."""
    if checksum is None:
        checksum = lfn_checksum(short11)
    units = [ord(ch) for ch in name]
    if len(units) % 13:
        units += [0x0000] + [0xFFFF] * (12 - len(units) % 13)
    chunks = [units[i:i + 13] for i in range(0, len(units), 13)]
    entries = []
    for number, chunk in enumerate(chunks, 1):
        entry = bytearray(32)
        entry[0] = number | (0x40 if number == len(chunks) else 0)
        struct.pack_into("<5H", entry, 1, *chunk[0:5])
        entry[11] = 0x0F
        entry[13] = checksum
        struct.pack_into("<6H", entry, 14, *chunk[5:11])
        struct.pack_into("<2H", entry, 28, *chunk[11:13])
        entries.append(bytes(entry))
    return entries[::-1]


def refusals(cases, error=fatimg.FatError):
    """Runs (label, call, message fragment) cases that must each raise `error`."""
    problems = []
    for label, call, fragment in cases:
        try:
            call()
        except error as exc:
            if fragment not in str(exc):
                problems.append(f"{label}: message {str(exc)!r} lacks {fragment!r}")
        except Exception as exc:  # noqa: BLE001 - a wrong exception type is the finding
            problems.append(f"{label}: raised {exc!r} instead of {error.__name__}")
        else:
            problems.append(f"{label}: accepted")
    return problems


def fsck_msdos():
    found = shutil.which("fsck_msdos")
    if found:
        return found
    return "/sbin/fsck_msdos" if os.access("/sbin/fsck_msdos", os.X_OK) else None


class FatImageTests(kit.Base):
    FILES = {
        "EFI/BOOT/BOOTAA64.EFI": bytes(range(256)) * 300,
        "SP11DUMP/RUN01/EMPTY.BIN": b"",
        "SP11DUMP/RUN01/BIG.BIN": random.Random(7).randbytes(70000),
        "SP11DUMP/RUN01/TABLES.TXT": b"text\n",
        "TOP.TXT": b"x",
    }

    def sample(self, order=None):
        image = fatimg.Fat16Image()
        for path in order or self.FILES:
            image.add_file(path, self.FILES[path])
        return image

    def test_round_trip_with_nested_directories_and_multi_cluster_files(self):
        tree = fatimg.read_fat16(self.sample().to_bytes())
        problems = []
        missing = sorted(set(self.FILES) - set(tree.files))
        extra = sorted(set(tree.files) - set(self.FILES))
        different = sorted(p for p in self.FILES if p in tree.files and tree.files[p] != self.FILES[p])
        if missing or extra or different:
            problems.append(f"files: missing {missing}, unexpected {extra}, wrong content {different}")
        if tree.dirs != {"EFI", "EFI/BOOT", "SP11DUMP", "SP11DUMP/RUN01"}:
            problems.append(f"directories: {sorted(tree.dirs)}")
        if tree.volume_label != "PROBESTICK":
            problems.append(f"volume label {tree.volume_label!r}")
        self.check(problems)

    def test_images_are_deterministic_and_independent_of_insertion_order(self):
        forward = self.sample().to_bytes()
        backward = self.sample(order=list(reversed(list(self.FILES)))).to_bytes()
        self.assertEqual(forward, self.sample().to_bytes(), "the same files gave two different images")
        self.assertEqual(forward, backward, "insertion order changed the image")

    def test_host_fsck_finds_the_volume_consistent(self):
        fsck = fsck_msdos()
        if not fsck:
            raise unittest.SkipTest("fsck_msdos not found")
        path = kit.work_dir("fat-fsck") / "stick.img"
        path.write_bytes(self.sample().to_bytes())
        rc, out, err = kit.run_cmd([fsck, "-n", path], timeout=60)
        self.check([] if rc == 0 else [f"fsck_msdos -n exited {rc}", kit.tail(kit.text_of(out + err))])

    def test_writer_refuses_what_the_firmware_could_not_boot(self):
        def add(path):
            fatimg.Fat16Image().add_file(path, b"")

        def file_as_directory():
            image = fatimg.Fat16Image()
            image.add_file("A", b"x")
            image.add_file("A/B", b"y")

        def overfull():
            image = fatimg.Fat16Image(9 << 20)
            image.add_file("BIG.BIN", bytes(10 << 20))
            image.to_bytes()

        def too_many_files():
            image = fatimg.Fat16Image()
            for number in range(520):
                image.add_file(f"F{number:04d}.BIN", b"")
            image.to_bytes()

        self.check(refusals((
            ("lower-case name", lambda: add("boot.efi"), "upper-case 8.3"),
            ("base longer than 8", lambda: add("TOOLONGNAME.EFI"), "upper-case 8.3"),
            ("extension longer than 3", lambda: add("A.LONG"), "upper-case 8.3"),
            ("empty base", lambda: add(".EFI"), "upper-case 8.3"),
            ("two dots", lambda: add("A.B.C"), "upper-case 8.3"),
            ("file used as a directory", file_as_directory, "is a file"),
            ("size not a multiple of 512", lambda: fatimg.Fat16Image((16 << 20) + 1), "multiple of 512"),
            ("too few clusters for FAT16", lambda: fatimg.Fat16Image(2 << 20), "not a FAT16 volume"),
            ("too many clusters for FAT16", lambda: fatimg.Fat16Image(300 << 20), "not a FAT16 volume"),
            ("full volume", overfull, "image is full"),
            ("full root directory", too_many_files, "root directory is full"),
        )))

    def test_reader_rejects_damaged_images(self):
        image = fatimg.Fat16Image()
        image.add_file("BIG.BIN", bytes(70000))
        image.add_file("D/F.TXT", b"hi")
        good = image.to_bytes()
        big_first = first_cluster(root_entry(good, 1))
        dir_first = first_cluster(root_entry(good, 2))
        too_many = patched(patched(good, 19, "<H", 0), 32, "<I", 300000)
        damaged = (
            ("no boot signature", patched(good, 510, "<H", 0), "no boot sector signature"),
            ("shorter than a sector", good[:100], "no boot sector signature"),
            ("1024-byte sectors", patched(good, 11, "<H", 1024), "unsupported BPB"),
            ("zero sectors per cluster", patched(good, 13, "<B", 0), "unsupported BPB"),
            ("no FATs", patched(good, 16, "<B", 0), "unsupported BPB"),
            ("too few clusters for FAT16", patched(good, 19, "<H", 8192), "not a FAT16 volume"),
            ("too many clusters for FAT16", too_many, "not a FAT16 volume"),
            ("a cluster chain that loops", set_fat(good, big_first + 1, big_first), "loops or leaves"),
            ("a chain that leaves the volume", set_fat(good, big_first, 0xF000), "loops or leaves"),
            ("a directory chain ending in a reserved value", set_fat(good, dir_first, 0x0001),
             "cluster chain ends with 0x1"),
        )
        self.check(refusals([(label, lambda data=data: fatimg.read_fat16(data), fragment)
                             for label, data, fragment in damaged]))

    def test_reader_understands_long_names_case_flags_and_deleted_entries(self):
        base = fatimg.Fat16Image()
        base.add_file("OTHER.TXT", b"other")
        base.add_file("README.TXT", b"readme")
        good = base.to_bytes()
        other, readme = root_entry(good, 1), root_entry(good, 2)
        short11 = readme[:11]

        def flagged(entry, flags):
            return entry[:12] + bytes([flags]) + entry[13:]

        def deleted(entry):
            return b"\xE5" + entry[1:]

        wrong_sum = lfn_checksum(short11) ^ 1
        cases = (
            ("plain short entries", [other, readme], {"OTHER.TXT": b"other", "README.TXT": b"readme"}),
            ("NT flag 0x08 lower-cases the base", [flagged(readme, 0x08)], {"readme.TXT": b"readme"}),
            ("NT flag 0x10 lower-cases the extension", [flagged(readme, 0x10)], {"README.txt": b"readme"}),
            ("both NT flags", [flagged(readme, 0x18)], {"readme.txt": b"readme"}),
            ("a long name replaces the short one",
             [*long_name_entries("lower case.txt", short11), readme], {"lower case.txt": b"readme"}),
            ("a 13-character long name needs no terminator",
             [*long_name_entries("exactly13.txt", short11), readme], {"exactly13.txt": b"readme"}),
            ("a non-ASCII long name",
             [*long_name_entries("\u00dcn\u00ef.txt", short11), readme], {"\u00dcn\u00ef.txt": b"readme"}),
            ("a long name with the wrong checksum is ignored",
             [*long_name_entries("lower case.txt", short11, wrong_sum), readme], {"README.TXT": b"readme"}),
            ("a deleted entry between the long name and its short entry cancels the name",
             [*long_name_entries("lower case.txt", short11), deleted(other), readme],
             {"README.TXT": b"readme"}),
            ("a deleted file is not listed", [deleted(readme)], {}),
            ("entries after a deleted one are still read", [deleted(other), readme],
             {"README.TXT": b"readme"}),
            ("an unused entry ends the directory", [other, bytes(32), readme], {"OTHER.TXT": b"other"}),
            ("orphaned long-name entries list nothing", long_name_entries("lower case.txt", short11), {}),
        )
        problems = []
        for label, entries, want in cases:
            got = fatimg.read_fat16(with_root_entries(good, entries)).files
            if got != want:
                problems.append(f"{label}: expected {sorted(want)}, got {sorted(got)}")
        self.check(problems)


# ---- CRC-8 line format -------------------------------------------------------


def reference_crc8(data):
    """CRC-8 (x^8 + x^2 + x + 1, zero start, no reflection) by long division of the message polynomial."""
    value = int.from_bytes(data, "big") << 8
    for position in range(value.bit_length() - 1, 7, -1):
        if value >> position & 1:
            value ^= 0x107 << (position - 8)
    return value


def good_row(payload):
    payload = lines.normalise(payload)
    return f"{payload:<{lines.PAYLOAD_MAX}} |{reference_crc8(payload.encode()):02X}"


class LineFormatTests(kit.Base):
    def test_crc8_matches_the_catalogue_check_value(self):
        self.assertEqual(lines.crc8(b"123456789"), 0xF4)
        self.assertEqual(lines.crc8(b""), 0)

    def test_crc8_agrees_with_an_independent_long_division(self):
        rng = random.Random(8)
        samples = [bytes([b]) for b in range(256)]
        samples += [rng.randbytes(rng.randrange(0, 64)) for _ in range(400)]
        bad = [s.hex() for s in samples if lines.crc8(s) != reference_crc8(s)]
        self.check([f"the CRC differs for {len(bad)} inputs, first {bad[:5]}"] if bad else [])

    def test_every_single_character_misread_changes_the_crc(self):
        payloads = ("HCR_EL2=0000000488000001", "PAGE 1/5", "ID_AA64MMFR0_EL1=0000000000000000",
                    "MMU M=1 C=1 I=1 WXN=0", "A" * lines.PAYLOAD_MAX)
        glyphs = [chr(code) for code in range(0x20, 0x7F)]
        problems = []
        for payload in payloads:
            crc = lines.crc8(payload.encode())
            for i, original in enumerate(payload):
                for ch in glyphs:
                    changed = payload[:i] + ch + payload[i + 1:]
                    if ch != original and lines.crc8(changed.encode()) == crc:
                        problems.append(f"{payload!r}: {original!r} -> {ch!r} at {i} keeps CRC {crc:02X}")
        self.check(problems[:10])

    def test_normalise_matches_what_the_probe_prints(self):
        problems = []
        for text, want in (("  ab   cD ", "AB CD"), ("a\tb\n", "A B"), ("", ""), ("   ", ""), ("x=1", "X=1")):
            if lines.normalise(text) != want:
                problems.append(f"normalise({text!r}) = {lines.normalise(text)!r}, expected {want!r}")
        self.check(problems)

    def test_check_line_classifies_each_failure(self):
        payload = "HCR_EL2=0000000488000001"
        crc = reference_crc8(payload.encode())
        cases = (
            ("a good row", good_row(payload), True, ""),
            ("a good row with CRLF", good_row(payload) + "\r\n", True, ""),
            ("lower-case payload and CRC", f"{payload.lower():<38} |{crc:02x}", True, ""),
            ("extra spacing", f"  {payload}    |{crc:02X} ", True, ""),
            ("a '|' inside the payload", good_row("A|B"), True, ""),
            ("no separator", payload, False, "no '|' CRC separator"),
            ("CRC not hex", f"{payload} |ZZ", False, "is not hex"),
            ("empty CRC", f"{payload} |", False, "is not hex"),
            ("signed CRC", f"{payload} |+5", False, "is not hex"),
            ("non-ASCII digits", f"{payload} |\u0663\u0663", False, "is not hex"),
            ("one digit", f"{payload} |5", False, "two hex digits"),
            ("three digits", f"{payload} |0{crc:02X}", False, "two hex digits"),
            ("wrong CRC", f"{payload} |{crc ^ 0x10:02X}", False, "CRC mismatch"),
            ("a misread character", f"{payload.replace('4', '9', 1)} |{crc:02X}", False, "CRC mismatch"),
        )
        problems = []
        for label, row, ok, fragment in cases:
            result = lines.check_line(row)
            if result.ok != ok or fragment not in result.reason:
                problems.append(f"{label}: ok={result.ok} reason={result.reason!r}")
        self.check(problems)

    def test_a_probe_row_is_exactly_the_documented_width(self):
        self.assertEqual(lines.LINE_COLS, lines.PAYLOAD_MAX + len(" |XX"))
        self.assertEqual(len(good_row("X")), lines.LINE_COLS)


# ---- The operator's transcription checker -------------------------------------

VERIFY = ROOT / "probes" / "uefi" / "verify-lines.py"
ASCII_TERMINAL = {"PYTHONUTF8": "0", "PYTHONIOENCODING": "ascii", "LC_ALL": "C"}


def verify(args=(), stdin=b"", env=None):
    """Runs verify-lines.py: (exit status, stdout, stderr)."""
    rc, out, err = kit.run_cmd([sys.executable, "-B", VERIFY, *args], input=stdin,
                               env={**os.environ, **env} if env else None, timeout=60)
    return rc, kit.text_of(out), kit.text_of(err)


def ok_line(number, payload):
    return f"OK   {number:4d}  {lines.normalise(payload)}"


class VerifyLinesTests(kit.Base):
    def test_all_good_rows_exit_zero(self):
        payloads = ("HCR_EL2=0000000488000001", "PAGE 2/5", "RAM 0000000040000000-0000000080000000")
        rc, out, err = verify(stdin="".join(good_row(p) + "\n" for p in payloads).encode())
        want = [ok_line(n, p) for n, p in enumerate(payloads, 1)] + ["3 OK, 0 BAD"]
        self.check([] if (rc, out.splitlines(), err) == (0, want, "") else
                   [f"rc={rc} stderr={err!r}", f"stdout {out.splitlines()} != {want}"])

    def test_a_misread_row_exits_one_and_names_the_expected_crc(self):
        right = reference_crc8(b"PAGE 2/5")
        wrong = right ^ 0x04
        rows = [good_row("PAGE 1/5"), f"{'PAGE 2/5':<38} |{wrong:02X}", good_row("PAGE 3/5")]
        rc, out, err = verify(stdin=("\n".join(rows) + "\n").encode())
        bad = f"BAD     2  PAGE 2/5  [CRC mismatch; shown {wrong:02X}, expected {right:02X}]"
        want = [ok_line(1, "PAGE 1/5"), bad, ok_line(3, "PAGE 3/5"), "2 OK, 1 BAD"]
        self.check([] if (rc, out.splitlines()) == (1, want) else [f"rc={rc}", f"stdout {out.splitlines()} != {want}"])

    def test_malformed_rows_are_reported_with_a_reason(self):
        rc, out, _err = verify(stdin=b"NO SEPARATOR HERE\nSOME TEXT |ZZ\nSOME TEXT |5\n")
        reasons = [line.split("[")[1].split(";")[0] for line in out.splitlines() if line.startswith("BAD")]
        self.check([] if rc == 1 and reasons == [
            "no '|' CRC separator", "CRC field 'ZZ' is not hex", "CRC field must be two hex digits"] else
            [f"rc={rc} reasons={reasons}"])

    def test_blank_lines_are_skipped_but_numbering_is_physical(self):
        rows = good_row("A") + "\n\n   \n" + good_row("B") + "\n"
        rc, out, _err = verify(stdin=rows.encode())
        want = [ok_line(1, "A"), ok_line(4, "B"), "2 OK, 0 BAD"]
        self.check([] if (rc, out.splitlines()) == (0, want) else [f"rc={rc}", f"{out.splitlines()} != {want}"])

    def test_case_and_spacing_do_not_matter(self):
        crc = reference_crc8(b"HELLO WORLD")
        rc, out, _err = verify(stdin=f"  hello    world   |{crc:02x}  \n".encode())
        self.check([] if (rc, out.splitlines()) == (0, [ok_line(1, "HELLO WORLD"), "1 OK, 0 BAD"]) else
                   [f"rc={rc} {out!r}"])

    def test_no_rows_exit_two(self):
        problems = []
        for label, data in (("empty input", b""), ("blank lines only", b"\n  \n\t\n")):
            rc, out, _err = verify(stdin=data)
            if (rc, out.strip()) != (2, "0 OK, 0 BAD"):
                problems.append(f"{label}: rc={rc} stdout={out!r}")
        self.check(problems)

    def test_a_file_argument_gives_the_same_answer_as_standard_input(self):
        data = (good_row("PAGE 1/5") + "\n" + f"{'PAGE 2/5':<38} |00\n").encode()
        path = kit.work_dir("verify-lines") / "transcription.txt"
        path.write_bytes(data)
        self.assertEqual(verify([path]), verify(stdin=data))
        self.assertEqual(verify([path])[0], 1)

    def test_unreadable_input_exits_two_without_a_traceback(self):
        directory = kit.work_dir("verify-lines-bad")
        problems = []
        for label, target in (("missing file", directory / "absent.txt"), ("a directory", directory)):
            rc, out, err = verify([target])
            if rc != 2 or out or not err.startswith("verify-lines: cannot read ") or "Traceback" in err:
                problems.append(f"{label}: rc={rc} stdout={out!r} stderr={err!r}")
        rc, _out, err = verify([directory / "a.txt", directory / "b.txt"])
        if rc != 2 or "at most one file" not in err or "Traceback" in err:
            problems.append(f"two files: rc={rc} stderr={err!r}")
        self.check(problems)

    def test_help_exits_zero_and_describes_the_exit_status(self):
        problems = []
        for flag in ("-h", "--help"):
            rc, out, err = verify([flag])
            if rc != 0 or "Exit status" not in out or "transcription" not in out or err:
                problems.append(f"{flag}: rc={rc} stderr={err!r}")
        self.check(problems)

    def test_arbitrary_bytes_never_end_in_a_traceback(self):
        garbage = bytes(range(256)) + b"\n" + b"\xff\xfe |00\n" + "caf\u00e9 |00\n".encode()
        problems = []
        for label, env in (("default terminal", None), ("ASCII terminal", ASCII_TERMINAL)):
            rc, out, err = verify(stdin=garbage, env=env)
            if rc != 1 or "Traceback" in err or not re.search(r"^\d+ OK, \d+ BAD$", out, re.M):
                problems.append(f"{label}: rc={rc} stderr={err[-200:]!r}")
        self.check(problems)


# ---- Constants shared with the C probe ---------------------------------------


def c_defines(header):
    """Integer #defines of probes/uefi/<header> as {name: value}."""
    text = (ROOT / "probes" / "uefi" / header).read_text(encoding="utf-8")
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^#define\s+(\w+)\s+(\d+)u?\b", text, re.M)}


class HostContractTests(kit.Base):
    def test_host_decoders_use_the_probes_screen_constants(self):
        fbtext, font = c_defines("fbtext.h"), c_defines("font6x8.h")
        pairs = (
            ("lines.PAYLOAD_MAX", lines.PAYLOAD_MAX, fbtext.get("PROBE_LINE_PAYLOAD_MAX")),
            ("lines.LINE_COLS", lines.LINE_COLS, fbtext.get("PROBE_LINE_COLS")),
            ("screen.LINE_COLS", screen.LINE_COLS, fbtext.get("PROBE_LINE_COLS")),
            ("screen.HEADER_ROWS", screen.HEADER_ROWS, fbtext.get("PROBE_HEADER_ROWS")),
            ("screen.MIN_SCALE", screen.MIN_SCALE, fbtext.get("PROBE_LAYOUT_MIN_SCALE")),
            ("screen.MAX_SCALE", screen.MAX_SCALE, fbtext.get("PROBE_LAYOUT_MAX_SCALE")),
            ("screen.MAX_COLS", screen.MAX_COLS, fbtext.get("PROBE_LAYOUT_MAX_COLS")),
            ("screen.CELL_W", screen.CELL_W, font.get("PROBE_FONT_CELL_W")),
            ("screen.CELL_H", screen.CELL_H, font.get("PROBE_FONT_CELL_H")),
            # screen.py treats the last row of a page as the single footer row.
            ("one footer row", 1, fbtext.get("PROBE_FOOTER_ROWS")),
        )
        self.check([f"{name} is {mine} but the C header says {theirs}"
                    for name, mine, theirs in pairs if mine != theirs])


# ---- Repository rules -----------------------------------------------------------

BINARY_SUFFIXES = {".efi", ".bin", ".fd", ".img", ".obj", ".o", ".ppm", ".png", ".exe", ".dll", ".pyc"}
MAC_ADDRESS = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
IPV4_ADDRESS = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
MONITOR_NEEDLES = (b"probes/uefi", b"sp11-el2-probe", b"build-uefi-probe")


def authored_files():
    """Everything this task owns: probes/uefi/** plus the three tools files."""
    tree = [p for p in (ROOT / "probes" / "uefi").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith(".")]
    tools = [ROOT / "tools" / name for name in
             ("build-uefi-probe.py", "test-uefi-probe.py", "test-uefi-probe-logic.c")]
    return sorted(tree + tools)


def rel(path):
    return path.relative_to(ROOT).as_posix()


def unused_imports(path):
    """Names a module imports and never uses; a line that says noqa is excused."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, str(path))
    source_lines = source.splitlines()
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            for alias in node.names:
                imported[alias.asname or alias.name] = node.lineno
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    return sorted(f"{name} (line {line})" for name, line in imported.items()
                  if name not in used and "noqa" not in source_lines[line - 1])


class HygieneTests(kit.Base):
    def test_every_authored_file_carries_the_licence_tag(self):
        missing = [rel(p) for p in authored_files()
                   if b"SPDX-License-Identifier: MIT" not in p.read_bytes()[:1024]]
        self.check([f"no SPDX-License-Identifier: MIT in the first 1 KiB of {name}" for name in missing])

    def test_authored_files_stay_under_the_size_limits(self):
        problems = []
        for path in authored_files():
            data = path.read_bytes()
            count = len(data.splitlines())
            if count >= 1500 or len(data) >= 50 * 1024:
                problems.append(f"{rel(path)}: {count} lines, {len(data)} bytes (limit 1500 lines, 50 KiB)")
        self.check(problems)

    def test_no_machine_specific_text(self):
        needles = ["/Us" + "ers/", "/ho" + "me/", str(Path.home()), str(ROOT)]
        needles = [n for n in needles if len(n) >= 6]
        problems = []
        for path in authored_files():
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    problems.append(f"{rel(path)} contains a machine-specific path")
            for label, pattern in (("a MAC address", MAC_ADDRESS), ("an IPv4 address", IPV4_ADDRESS)):
                found = pattern.search(text)
                if found:
                    problems.append(f"{rel(path)} contains {label}: {found.group(0)}")
        self.check(problems)

    def test_no_binary_artifacts_among_the_sources(self):
        problems = []
        for path in authored_files():
            data = path.read_bytes()
            if path.suffix.lower() in BINARY_SUFFIXES:
                problems.append(f"{rel(path)}: binary file type")
                continue
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"{rel(path)}: not UTF-8 text")
            if b"\0" in data:
                problems.append(f"{rel(path)}: contains NUL bytes")
        self.check(problems)

    def test_python_sources_compile_and_import_only_what_they_use(self):
        problems = []
        for path in (p for p in authored_files() if p.suffix == ".py"):
            try:
                unused = unused_imports(path)
            except SyntaxError as exc:
                problems.append(f"{rel(path)}: {exc}")
                continue
            problems += [f"{rel(path)}: unused import {name}" for name in unused]
        self.check(problems)

    def test_nothing_links_the_probe_into_the_monitor(self):
        monitor = [p for p in (ROOT / "compat").rglob("*") if p.is_file()]
        monitor += sorted((ROOT / "probes").glob("xnuboot.*"))
        self.assertTrue(monitor, "no monitor sources found; the test would pass vacuously")
        problems = []
        for path in monitor:
            data = path.read_bytes()
            problems += [f"{rel(path)} mentions {needle.decode()}" for needle in MONITOR_NEEDLES if needle in data]
        self.check(problems)


# ---- The README and the entry point ----------------------------------------------

README = ROOT / "probes" / "uefi" / "README.md"
ENTRY = ROOT / "tools" / "test-uefi-probe.py"
MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
SCREEN_ROW_END = re.compile(r"\|[0-9A-F]{2}$")
ENVIRONMENT_VARIABLE = re.compile(r"UEFI_PROBE_[A-Z0-9_]+")
EVIDENCE_LABELS = ("VERIFIED", "OBSERVED", "INFERRED", "EXPERIMENTAL", "UNIMPLEMENTED")


def readme_text():
    return README.read_text(encoding="utf-8")


def registered_suites():
    """SUITES from the entry point, read from its source so that it is neither imported nor run."""
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"), str(ENTRY))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "SUITES"):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{rel(ENTRY)} has no module-level SUITES tuple")


def fenced_screen_rows(markdown):
    """Lines inside ``` blocks that end in |XX; the README shows its example screen rows that way."""
    rows, inside = [], False
    for line in markdown.splitlines():
        if line.startswith("```"):
            inside = not inside
        elif inside and SCREEN_ROW_END.search(line):
            rows.append(line)
    return rows


class DocsTests(kit.Base):
    def test_every_suite_module_is_registered_and_named_in_the_readme(self):
        on_disk = sorted(p.stem for p in (ROOT / "probes" / "uefi" / "test").glob("suite_*.py"))
        registered = registered_suites()
        text = readme_text()
        problems = [f"{name}.py exists but is not in SUITES of {rel(ENTRY)}"
                    for name in on_disk if name not in registered]
        problems += [f"SUITES names {name}, which has no module" for name in registered if name not in on_disk]
        problems += [f"SUITES lists {name} more than once" for name in sorted(set(registered))
                     if registered.count(name) > 1]
        problems += [f"the README does not name `{name}`" for name in on_disk if f"`{name}`" not in text]
        self.check(problems)

    def test_readme_names_every_variant_helper_and_environment_variable(self):
        text = readme_text()
        problems = []
        for variant in kit.bup.VARIANTS.values():
            problems += [f"the README does not mention {needle}"
                         for needle in (f"`{variant.name}`", variant.image) if needle not in text]
        for folder in ("host", "test"):
            for path in sorted((ROOT / "probes" / "uefi" / folder).glob("*.py")):
                if not path.name.startswith(("suite_", "__")) and f"{folder}/{path.name}" not in text:
                    problems.append(f"the README does not mention {folder}/{path.name}")
        if "verify-lines.py" not in text:
            problems.append("the README does not mention the operator CLI verify-lines.py")
        sources = [p for p in (ROOT / "probes" / "uefi").rglob("*.py")] + [ENTRY, ROOT / "tools" / "build-uefi-probe.py"]
        used = {name for path in sources for name in ENVIRONMENT_VARIABLE.findall(path.read_text(encoding="utf-8"))}
        self.assertTrue(used, "no environment variables found; the test would pass vacuously")
        problems += [f"the README does not document {name}" for name in sorted(used) if name not in text]
        self.check(problems)

    def test_readme_example_rows_carry_valid_crcs(self):
        rows = fenced_screen_rows(readme_text())
        self.assertTrue(rows, "no example screen rows in the README; the test would pass vacuously")
        problems = []
        for row in rows:
            verdict = lines.check_line(row)
            if len(row) != lines.LINE_COLS or row[lines.PAYLOAD_MAX:lines.PAYLOAD_MAX + 2] != " |":
                problems.append(f"{row!r} is not {lines.PAYLOAD_MAX} payload columns plus ' |XX'")
            if not verdict.ok:
                problems.append(f"{row!r}: {verdict.reason} (CRC should be {verdict.expected:02X})")
        self.check(problems)

    def test_readme_relative_links_resolve(self):
        targets = [t for t in MARKDOWN_LINK.findall(readme_text()) if not re.match(r"([a-z][a-z0-9+.-]*:|#)", t)]
        self.assertTrue(targets, "no relative links in the README; the test would pass vacuously")
        problems = [f"the README links to {t}, which does not exist"
                    for t in targets if not (README.parent / t.split("#")[0]).resolve().exists()]
        self.check(problems)

    def test_readme_states_hardware_status_and_uses_the_evidence_labels(self):
        text = readme_text()
        problems = [] if "Hardware status:" in text else ["the README has no 'Hardware status:' statement"]
        problems += [f"the README never uses the evidence label {label}"
                     for label in EVIDENCE_LABELS if label not in text]
        self.check(problems)
