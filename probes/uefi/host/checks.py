# SPDX-License-Identifier: MIT
"""Independent re-derivations for checking what the probe printed and dumped.

The probe's C code is the implementation under test. The tables and rules
here are written again from the Arm ID register field layout and from the
dump format in probes/uefi/README.md, and nothing here imports the probe's
sources, so a mistake in one copy shows up as a disagreement instead of being
repeated in both. The independence is of code, not of authorship: a field
meaning misread from the architecture manual would be misread in both copies.

Every checker returns a list of problem strings (empty means consistent) so a
caller can report all disagreements at once. Functions that take `files` expect
the mapping of path -> bytes that fatimg.read_fat16() returns.
"""
from __future__ import annotations

import re
import struct

# ---- On-screen register decode -------------------------------------------


def bits(value: int, hi: int, lo: int) -> int:
    return (value >> lo) & ((1 << (hi - lo + 1)) - 1)


# (label, register, hi, lo, meaning of the listed values). Values that are not
# listed are printed raw by the probe, with no meaning attached.
DECODED_FIELDS = (
    ("PARANGE", "ID_AA64MMFR0_EL1", 3, 0,
     {0: "32 BIT", 1: "36 BIT", 2: "40 BIT", 3: "42 BIT", 4: "44 BIT", 5: "48 BIT", 6: "52 BIT"}),
    ("TGRAN4", "ID_AA64MMFR0_EL1", 31, 28, {0: "YES", 1: "LPA2", 0xF: "NO"}),
    ("TGRAN16", "ID_AA64MMFR0_EL1", 23, 20, {0: "NO", 1: "YES", 2: "LPA2"}),
    ("TGRAN64", "ID_AA64MMFR0_EL1", 27, 24, {0: "YES", 0xF: "NO"}),
    ("VMIDBITS", "ID_AA64MMFR1_EL1", 7, 4, {0: "8 BIT", 2: "16 BIT"}),
    ("VH", "ID_AA64MMFR1_EL1", 11, 8, {0: "NO", 1: "YES"}),
    ("NV", "ID_AA64MMFR2_EL1", 27, 24, {0: "NO", 1: "NV", 2: "NV2"}),
    ("ECV", "ID_AA64MMFR0_EL1", 63, 60, {0: "NO", 1: "YES", 2: "CNTPOFF"}),
    ("FGT", "ID_AA64MMFR0_EL1", 59, 56, {0: "NO", 1: "YES"}),
    ("AMU", "ID_AA64PFR0_EL1", 47, 44, {0: "NO", 1: "V1", 2: "V1P1"}),
    ("GIC", "ID_AA64PFR0_EL1", 27, 24, {0: "NO", 1: "SYSREG V3/V4", 3: "SYSREG V4.1"}),
    ("SVE", "ID_AA64PFR0_EL1", 35, 32, {0: "NO", 1: "YES"}),
    ("SME", "ID_AA64PFR1_EL1", 27, 24, {0: "NO", 1: "YES", 2: "SME2"}),
)

_REG_LINE = re.compile(r"^([A-Z0-9_]+(?: RAW)?)=([0-9A-F]{16})$")


def parse_registers(payloads) -> dict:
    """Register label -> value for every 'LABEL=<16 hex digits>' payload.

    Labels that were skipped or not read ('ICC_SRE_EL2=SKIPPED GIC=0') are
    absent from the result, which is what lets expected_lines() omit the
    lines that depend on them.
    """
    regs = {}
    for text in payloads:
        m = _REG_LINE.match(text)
        if m:
            regs[m.group(1)] = int(m.group(2), 16)
    return regs


def expected_lines(regs: dict) -> list:
    """Every derived line the probe should print, given the raw registers."""
    out = []
    if "SCTLR_EL2" in regs:
        s = regs["SCTLR_EL2"]
        out.append("MMU M=%d C=%d I=%d WXN=%d" % (bits(s, 0, 0), bits(s, 2, 2), bits(s, 12, 12),
                                                   bits(s, 19, 19)))
    if "HCR_EL2" in regs:
        h = regs["HCR_EL2"]
        out.append("HCR VM=%d TGE=%d RW=%d E2H=%d" % (bits(h, 0, 0), bits(h, 27, 27), bits(h, 31, 31),
                                                       bits(h, 34, 34)))
    for label, reg, hi, lo, meanings in DECODED_FIELDS:
        if reg in regs:
            value = bits(regs[reg], hi, lo)
            meaning = meanings.get(value)
            out.append(f"{label}={value:X}" + (f" {meaning}" if meaning else ""))
    isar1 = regs.get("ID_AA64ISAR1_EL1")
    if isar1 is not None:
        out.append("PAUTH APA=%X API=%X" % (bits(isar1, 7, 4), bits(isar1, 11, 8)))
        out.append("PAUTH GPA=%X GPI=%X" % (bits(isar1, 27, 24), bits(isar1, 31, 28)))
    return out


# ---- Dump files ----------------------------------------------------------


def _sum8(data: bytes) -> int:
    return sum(data) & 0xFF


def acpi_table_problems(name: str, data: bytes) -> list:
    """A dumped '<SIG>.bin' or '<SIG>_<k>.bin' must be a self-consistent ACPI table.

    The FACS is the exception: it starts with a signature and a length like the
    others but has no checksum, and the probe accepts it from 24 bytes.
    """
    bad = []
    sig = name.split(".")[0].split("_")[0]
    is_facs = sig == "FACS"
    if len(data) < (24 if is_facs else 36):
        return [f"{name}: {len(data)} bytes is shorter than "
                f"{'a FACS' if is_facs else 'an ACPI header'}"]
    if data[:4] != sig.encode("ascii", "replace"):
        bad.append(f"{name}: signature field is {bytes(data[:4])!r}")
    length = struct.unpack_from("<I", data, 4)[0]
    if length != len(data):
        bad.append(f"{name}: header length {length} but file is {len(data)} bytes")
    if not is_facs and _sum8(data) != 0:
        bad.append(f"{name}: checksum byte sum is {_sum8(data):#x}, not 0")
    return bad


def rsdp_problems(data: bytes) -> list:
    if len(data) < 20:
        return [f"RSDP.bin: {len(data)} bytes is shorter than a v1 RSDP"]
    bad = []
    if data[:8] != b"RSD PTR ":
        bad.append(f"RSDP.bin: signature {bytes(data[:8])!r}")
    if _sum8(data[:20]) != 0:
        bad.append("RSDP.bin: first 20 bytes do not sum to 0")
    revision = data[15]
    if revision >= 2:
        if len(data) < 36:
            return bad + ["RSDP.bin: revision >= 2 but shorter than 36 bytes"]
        length = struct.unpack_from("<I", data, 20)[0]
        if length != len(data):
            bad.append(f"RSDP.bin: length field {length} but file is {len(data)} bytes")
        if _sum8(data) != 0:
            bad.append("RSDP.bin: extended checksum does not sum to 0")
    return bad


def smbios_problems(entry: bytes, table: bytes) -> list:
    """SMBIOS 3.x (64-bit) entry point and a structure table that ends at its marker."""
    bad = []
    if entry[:5] == b"_SM3_":
        if len(entry) != 24 or entry[6] != 24:
            bad.append(f"smbios-ep.raw: expected a 24-byte 64-bit entry point, got {len(entry)} bytes")
        elif _sum8(entry) != 0:
            bad.append("smbios-ep.raw: checksum does not sum to 0")
        else:
            declared = struct.unpack_from("<I", entry, 12)[0]
            if declared != len(table):
                bad.append(f"smbios-table.raw: entry point declares {declared} bytes, file has {len(table)}")
    elif entry[:4] == b"_SM_":
        if len(entry) != 31 or entry[5] != 31:
            bad.append(f"smbios-ep.raw: expected a 31-byte 32-bit entry point, got {len(entry)} bytes")
        elif _sum8(entry) != 0 or entry[16:21] != b"_DMI_" or _sum8(entry[16:31]) != 0:
            bad.append("smbios-ep.raw: entry point or intermediate checksum is wrong")
        else:
            declared = struct.unpack_from("<H", entry, 22)[0]
            if declared != len(table):
                bad.append(f"smbios-table.raw: entry point declares {declared} bytes, file has {len(table)}")
    else:
        return [f"smbios-ep.raw: unknown anchor {bytes(entry[:5])!r}"]

    offset = 0
    while offset + 4 <= len(table):
        kind, length = table[offset], table[offset + 1]
        if length < 4:
            return bad + [f"smbios-table.raw: structure at {offset} has length {length}"]
        end = table.find(b"\0\0", offset + length)
        if end < 0:
            return bad + [f"smbios-table.raw: structure at {offset} has an unterminated string set"]
        offset = end + 2
        if kind == 127:
            break
    else:
        return bad + ["smbios-table.raw: no end-of-table structure"]
    if offset != len(table):
        bad.append(f"smbios-table.raw: end-of-table structure ends at {offset}, file has {len(table)} bytes")
    return bad


_TABLE_ROW = re.compile(r"^([0-9A-F]{2}) (ROOT|ENTRY|DSDT|FACS) (.{4}) (\d+) ([0-9A-F]{8}) "
                        r"([0-9A-F]{16}) (\S+) (.+)$")


def parse_tables_txt(text: str):
    """Returns (header, rows). Rows are dicts; header maps 'key' -> 'value'."""
    lines = text.splitlines()
    if not lines or lines[0] != "# SP11PROBE tables v1":
        raise ValueError("tables.txt: missing '# SP11PROBE tables v1' first line")
    header, rows = {}, []
    for line in lines[1:]:
        if line.startswith("# "):
            key, sep, value = line[2:].partition("=")
            if sep:
                header[key] = value
            continue
        m = _TABLE_ROW.match(line)
        if not m:
            raise ValueError(f"tables.txt: unparseable row {line!r}")
        rows.append({"idx": int(m.group(1), 16), "origin": m.group(2), "sig": m.group(3),
                     "rev": int(m.group(4)), "length": int(m.group(5), 16),
                     "addr": int(m.group(6), 16), "file": m.group(7), "status": m.group(8)})
    return header, rows


_MEMMAP_ROW = re.compile(r"^([0-9A-F]{4}) ([0-9A-F]{2}) ([0-9A-F]{16}) ([0-9A-F]{16}) ([0-9A-F]{16}) "
                         r"([0-9A-F]{16})(?: (.*))?$")


def parse_memmap_txt(text: str):
    """Returns (header dict, rows). Each row: type, phys, virt, pages, attr, name."""
    lines = text.splitlines()
    if not lines or lines[0] != "# SP11PROBE memmap v1":
        raise ValueError("memmap.txt: missing '# SP11PROBE memmap v1' first line")
    header, rows = {}, []
    for line in lines[1:]:
        if line.startswith("# "):
            for key, value in re.findall(r"(\w+)=(\w+)", line):
                header[key] = int(value)
            continue
        m = _MEMMAP_ROW.match(line)
        if not m:
            raise ValueError(f"memmap.txt: unparseable row {line!r}")
        if int(m.group(1), 16) != len(rows):
            raise ValueError(f"memmap.txt: row index {m.group(1)} out of order")
        rows.append({"type": int(m.group(2), 16), "phys": int(m.group(3), 16),
                     "virt": int(m.group(4), 16), "pages": int(m.group(5), 16),
                     "attr": int(m.group(6), 16), "name": m.group(7) or ""})
    return header, rows


def memmap_problems(raw: bytes, text: str) -> list:
    """memmap.raw (descriptors as the firmware returned them) against memmap.txt."""
    try:
        header, rows = parse_memmap_txt(text)
    except ValueError as exc:
        return [str(exc)]
    bad = []
    size = header.get("descriptor_size", 0)
    if size < 40 or size % 8:
        return [f"memmap.txt: descriptor_size {size} is not a plausible EFI_MEMORY_DESCRIPTOR size"]
    if header.get("count") != len(rows):
        bad.append(f"memmap.txt: header count {header.get('count')} but {len(rows)} rows")
    if header.get("map_size") != len(raw) or len(raw) != size * len(rows):
        bad.append(f"memmap.raw: {len(raw)} bytes, expected {size} x {len(rows)} "
                   f"(header map_size {header.get('map_size')})")
        return bad
    for i, row in enumerate(rows):
        kind, _pad, phys, virt, pages, attr = struct.unpack_from("<IIQQQQ", raw, i * size)
        want = (row["type"], row["phys"], row["virt"], row["pages"], row["attr"])
        if (kind, phys, virt, pages, attr) != want:
            bad.append(f"memmap row {i}: text {want} disagrees with raw {(kind, phys, virt, pages, attr)}")
            break
    return bad


def parse_summary(text: str) -> dict:
    """key -> value; a key that repeats (cfg=) maps to a list of its values."""
    lines = text.splitlines()
    if not lines or lines[0] != "# SP11PROBE summary v1":
        raise ValueError("summary.txt: missing '# SP11PROBE summary v1' first line")
    out = {}
    for line in lines[1:]:
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"summary.txt: line without '=': {line!r}")
        if key == "cfg":
            out.setdefault("cfg", []).append(value)
        elif key in out:
            raise ValueError(f"summary.txt: repeated key {key!r}")
        else:
            out[key] = value
    return out


def _first_fadt(mine: dict, rows: list):
    """The FACP the probe parses for DSDT/FACS: the first listed FACP it could dump."""
    for row in rows:
        if row["origin"] == "ENTRY" and row["sig"] == "FACP" and row["file"] in mine:
            return mine[row["file"]]
    return None


def acpi_pointer_problems(mine: dict, rows: list) -> list:
    """The addresses in tables.txt against the pointers inside the dumped tables.

    The probe walks RSDP -> XSDT/RSDT -> FADT -> DSDT/FACS itself. Reading the
    same pointers back out of the dumped bytes is a second, independent walk,
    so a wrong address in tables.txt (which the platform checkers key on)
    cannot pass just because both came from the same code.
    """
    rsdp = mine.get("RSDP.bin", b"")
    if len(rsdp) < 20:
        return []
    roots = [r for r in rows if r["origin"] == "ROOT"]
    if len(roots) != 1:
        return [f"tables.txt: {len(roots)} ROOT rows, expected 1"]
    root = roots[0]
    bad = []
    xsdt = struct.unpack_from("<Q", rsdp, 24)[0] if rsdp[15] >= 2 and len(rsdp) >= 36 else 0
    want_sig = "XSDT" if xsdt else "RSDT"
    want_addr = xsdt or struct.unpack_from("<I", rsdp, 16)[0]
    if (root["sig"], root["addr"]) != (want_sig, want_addr):
        bad.append(f"tables.txt: ROOT row is {root['sig']} at {root['addr']:#x}, "
                   f"RSDP.bin points at {want_sig} {want_addr:#x}")

    root_data = mine.get(root["file"], b"")
    if len(root_data) >= 36:
        size = 8 if xsdt else 4
        pointers = [int.from_bytes(root_data[o:o + size], "little")
                    for o in range(36, len(root_data) - size + 1, size)]
        listed = [r["addr"] for r in rows if r["origin"] == "ENTRY"]
        if pointers != listed:
            index = next((i for i, (a, b) in enumerate(zip(pointers, listed)) if a != b),
                         min(len(pointers), len(listed)))
            bad.append(f"tables.txt: {len(listed)} ENTRY rows differ from the {len(pointers)} "
                       f"entries of {root['file']} (first difference at index {index})")

    fadt = _first_fadt(mine, rows)
    if fadt is not None and len(fadt) >= 44:
        want_dsdt = (struct.unpack_from("<Q", fadt, 140)[0] if len(fadt) >= 148 else 0) \
            or struct.unpack_from("<I", fadt, 40)[0]
        want_facs = (struct.unpack_from("<Q", fadt, 132)[0] if len(fadt) >= 140 else 0) \
            or struct.unpack_from("<I", fadt, 36)[0]
        for origin, want in (("DSDT", want_dsdt), ("FACS", want_facs)):
            found = [r for r in rows if r["origin"] == origin]
            if len(found) != 1 or found[0]["addr"] != want:
                bad.append(f"tables.txt: {origin} row(s) {[hex(r['addr']) for r in found]} "
                           f"but the FADT points at {want:#x}")
    return bad


ACPI_CONFIG_GUIDS = ("8868E871-E4F1-11D3-BC22-0080C73C8881",  # ACPI 2.0 RSDP
                     "EB9D2D30-2D88-11D3-9A16-0090273FC14D")  # ACPI 1.0 RSDP


def rsdp_address_problems(header: dict, summary: dict) -> list:
    """The RSDP address in tables.txt against the firmware configuration table.

    The summary lists every EFI configuration table the firmware published,
    and the RSDP the probe walked has to be one of the ACPI entries there.
    """
    text = header.get("rsdp_addr")
    if text is None:
        return ["tables.txt: no rsdp_addr header"]
    try:
        address = int(text, 16)
        published = {int(entry.split()[1], 16) for entry in summary.get("cfg", [])
                     if entry.split()[0].upper() in ACPI_CONFIG_GUIDS}
    except (ValueError, IndexError):
        return ["summary.txt or tables.txt: unparseable RSDP address or cfg= entry"]
    if address not in published:
        return [f"tables.txt: rsdp_addr {address:#x} is not one of the ACPI configuration "
                f"tables {sorted(hex(a) for a in published)} in summary.txt"]
    return []


def dump_problems(files: dict, run_dir: str, dump_marker: dict | None = None) -> list:
    """Checks one SP11DUMP/RUNnn directory for internal consistency.

    `dump_marker` is the fields of the serial 'DUMP STATE=OK ...' line; when
    given, its FILES and BYTES must equal what is actually on the stick.
    """
    prefix = run_dir.strip("/") + "/"
    mine = {p[len(prefix):]: d for p, d in files.items() if p.startswith(prefix)}
    bad = []
    if not mine:
        return [f"{run_dir}: no files"]
    for required in ("summary.txt", "tables.txt", "memmap.txt", "memmap.raw", "RSDP.bin"):
        if required not in mine:
            bad.append(f"{run_dir}: {required} is missing")
    if bad:
        return bad

    bad += rsdp_problems(mine["RSDP.bin"])
    for name, data in sorted(mine.items()):
        if name.endswith(".bin") and name != "RSDP.bin":
            bad += acpi_table_problems(name, data)

    try:
        header, rows = parse_tables_txt(mine["tables.txt"].decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        return bad + [str(exc)]
    listed = set()
    for row in rows:
        if row["file"] == "-":
            if row["status"] == "OK":
                bad.append(f"tables.txt: row {row['idx']} is OK but names no file")
            continue
        listed.add(row["file"])
        data = mine.get(row["file"])
        if data is None:
            bad.append(f"tables.txt: row {row['idx']} names {row['file']}, which is not in the dump")
        elif len(data) != row["length"]:
            bad.append(f"tables.txt: {row['file']} has {len(data)} bytes, row says {row['length']}")
        if row["status"] != "OK":
            bad.append(f"tables.txt: {row['file']} is listed with status {row['status']}")
    stray = {n for n in mine if n.endswith(".bin") and n != "RSDP.bin"} - listed
    if stray:
        bad.append(f"dump has tables that tables.txt does not list: {sorted(stray)}")
    bad += acpi_pointer_problems(mine, rows)

    bad += memmap_problems(mine["memmap.raw"], mine["memmap.txt"].decode("ascii", "replace"))
    if "smbios-ep.raw" in mine or "smbios-table.raw" in mine:
        if "smbios-ep.raw" in mine and "smbios-table.raw" in mine:
            bad += smbios_problems(mine["smbios-ep.raw"], mine["smbios-table.raw"])
        else:
            bad.append("only one of smbios-ep.raw and smbios-table.raw was dumped")

    try:
        summary = parse_summary(mine["summary.txt"].decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        return bad + [str(exc)]
    bad += rsdp_address_problems(header, summary)
    total = sum(len(d) for d in mine.values())
    try:
        before_files = int(summary["dump_files_before_summary"])
        before_bytes = int(summary["dump_bytes_before_summary"])
    except (KeyError, ValueError):
        return bad + ["summary.txt: dump_files_before_summary / dump_bytes_before_summary missing"]
    if before_files + 1 != len(mine):
        bad.append(f"summary.txt: {before_files} files before the summary, but the dump has {len(mine)}")
    if before_bytes + len(mine["summary.txt"]) != total:
        bad.append(f"summary.txt: {before_bytes} bytes before the summary plus its own "
                   f"{len(mine['summary.txt'])} is not the {total} on the stick")
    if dump_marker is not None:
        if dump_marker.get("FILES") != str(len(mine)):
            bad.append(f"DUMP marker says FILES={dump_marker.get('FILES')}, stick has {len(mine)}")
        if dump_marker.get("BYTES") != str(total):
            bad.append(f"DUMP marker says BYTES={dump_marker.get('BYTES')}, stick has {total}")
    return bad
