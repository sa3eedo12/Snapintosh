# SPDX-License-Identifier: MIT
"""Synthetic SP11DUMP/RUNnn directories for testing host/checks.py.

Every byte here is invented: the tables carry a made-up OEM id and filler
bytes, so no firmware content is involved. build() returns a dump that
satisfies every cross-check in checks.dump_problems(). The fields of Spec
either select a legitimate variation (FACS present or absent, 32- or 64-bit
FADT pointers, XSDT or RSDT root, SMBIOS 2 or 3) or break exactly one relation,
so a test can ask whether the checker notices that one inconsistency.

The file set and the text formats follow what probes/uefi/dump.c writes
(OBSERVED in a QEMU run), reduced to a root table, two entries, the DSDT and
the FACS. tables.txt always carries the true addresses; the Spec overrides
change only what the tables themselves, or summary.txt, say.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

RUN_DIR = "SP11DUMP/RUN01"
BOOT_FILE = "EFI/BOOT/BOOTAA64.EFI"
BASE = 0x70000000
ADDR = {"RSDP": BASE, "ROOT": BASE + 0x1000, "FACP": BASE + 0x2000, "APIC": BASE + 0x3000,
        "DSDT": BASE + 0x4000, "FACS": BASE + 0x5000, "SMBIOS": BASE + 0x6000}
ACPI20_GUID = "8868E871-E4F1-11D3-BC22-0080C73C8881"
ACPI10_GUID = "EB9D2D30-2D88-11D3-9A16-0090273FC14D"
SMBIOS3_GUID = "F2FD1544-9794-4A2C-992E-E5BBCF20E394"
# The 32-bit FADT pointers of a "wide" FADT. A correct reader prefers the
# 64-bit X_ fields, so these must never show up in a verdict.
FADT_DECOY_FACS = 0x12340000
FADT_DECOY_DSDT = 0x12350000
MEMMAP_ROWS = ((7, 0x40000000, 0x4000, 0x8, "CONV"),
               (4, 0x50000000, 0x100, 0xF, "BS DATA"),
               (11, 0x7C000000, 0x10, 0x8000000000000001, "MMIO"))


@dataclass(frozen=True)
class Spec:
    root: str = "XSDT"                  # "XSDT" (RSDP revision 2) or "RSDT" (revision 0)
    facs: bool = True                   # False: the FADT has no FACS and tables.txt says ABSENT
    wide: bool = True                   # True: the X_ fields hold the pointers
    smbios: Optional[str] = "3"         # "3", "2" or None
    fadt_dsdt: Optional[int] = None     # what the FADT says the DSDT address is
    fadt_facs: Optional[int] = None     # what the FADT says the FACS address is
    rsdp_root: Optional[int] = None     # what the RSDP says the root table address is
    root_entry: Optional[int] = None    # what the root table says the APIC address is
    summary_rsdp: Optional[int] = None  # the ACPI cfg= address in summary.txt
    tables_rsdp: Optional[int] = None   # the rsdp_addr header of tables.txt


def fix_checksum(data, at):
    """Returns data with the byte at `at` set so that every byte sums to 0."""
    buf = bytearray(data)
    buf[at] = 0
    buf[at] = -sum(buf) & 0xFF
    return bytes(buf)


def table(sig, size, revision=2):
    """A header-only ACPI table of `size` bytes with a valid checksum."""
    header = struct.pack("<4sIBB6s8sIII", sig, size, revision, 0, b"SYNTH ", b"SYNTHTBL",
                         1, 0x544E5953, 1)
    return fix_checksum(header + bytes([0x5A]) * (size - len(header)), 9)


def fadt(wide, dsdt, facs):
    """A revision 6 FADT whose DSDT/FACS pointers are `dsdt` and `facs` (0 = none)."""
    data = bytearray(table(b"FACP", 276, 6))
    if wide:
        legacy_facs, legacy_dsdt = (FADT_DECOY_FACS if facs else 0), FADT_DECOY_DSDT
        wide_facs, wide_dsdt = facs, dsdt
    else:
        legacy_facs, legacy_dsdt = facs, dsdt
        wide_facs = wide_dsdt = 0
    struct.pack_into("<II", data, 36, legacy_facs, legacy_dsdt)
    struct.pack_into("<H", data, 129, 0x0001)   # ARM_BOOT_ARCH: PSCI compliant, SMC conduit
    data[131] = 3                               # FADT minor version
    struct.pack_into("<QQ", data, 132, wide_facs, wide_dsdt)
    return fix_checksum(data, 9)


def root_table(kind, entries):
    width = 8 if kind == "XSDT" else 4
    data = bytearray(table(kind.encode(), 36 + width * len(entries), 1))
    for i, address in enumerate(entries):
        data[36 + i * width:36 + (i + 1) * width] = address.to_bytes(width, "little")
    return fix_checksum(data, 9)


def rsdp(kind, root_address):
    """A 36-byte revision 2 RSDP (XSDT) or a 20-byte revision 0 one (RSDT)."""
    if kind == "XSDT":
        buf = bytearray(struct.pack("<8sB6sBIIQB3s", b"RSD PTR ", 0, b"SYNTH ", 2, 0, 36,
                                    root_address, 0, bytes(3)))
        buf[8] = -sum(buf[:20]) & 0xFF
        buf[32] = -sum(buf) & 0xFF
    else:
        buf = bytearray(struct.pack("<8sB6sBI", b"RSD PTR ", 0, b"SYNTH ", 0, root_address))
        buf[8] = -sum(buf) & 0xFF
    return bytes(buf)


def smbios_table():
    """One type 0 structure with two strings, then the end-of-table structure."""
    type0 = bytes([0, 18]) + struct.pack("<H", 0) + bytes(14) + b"SYNTH\0" + b"0.0\0" + b"\0"
    return type0 + bytes([127, 4, 0x34, 0x12]) + b"\0\0"


def smbios_entry(version, table_length):
    if version == "3":
        entry = bytearray(24)
        entry[0:5] = b"_SM3_"
        entry[6] = 24
        entry[7:10] = bytes([3, 0, 0])
        entry[10] = 1
        struct.pack_into("<I", entry, 12, table_length)
        struct.pack_into("<Q", entry, 16, ADDR["SMBIOS"])
        entry[5] = -sum(entry) & 0xFF
        return bytes(entry)
    entry = bytearray(struct.pack("<4sBBBBHB5s5sBHIHB", b"_SM_", 0, 31, 2, 8, 0x30, 0, bytes(5),
                                  b"_DMI_", 0, table_length, ADDR["SMBIOS"], 2, 0x28))
    entry[21] = -sum(entry[16:31]) & 0xFF
    entry[4] = -sum(entry) & 0xFF
    return bytes(entry)


def memmap_files():
    raw = b"".join(struct.pack("<IIQQQQ", kind, 0, phys, 0, pages, attr) + bytes(8)
                   for kind, phys, pages, attr, _name in MEMMAP_ROWS)
    lines = ["# SP11PROBE memmap v1",
             f"# descriptor_size=48 descriptor_version=1 count={len(MEMMAP_ROWS)} "
             f"map_size={len(raw)}",
             "# idx type phys virt pages attr name"]
    for i, (kind, phys, pages, attr, name) in enumerate(MEMMAP_ROWS):
        lines.append(f"{i:04X} {kind:02X} {phys:016X} {0:016X} {pages:016X} {attr:016X} {name}")
    return raw, "\n".join(lines) + "\n"


def tables_text(rows, rsdp_address):
    lines = ["# SP11PROBE tables v1", "# rsdp_status=OK", f"# rsdp_addr=0x{rsdp_address:016X}",
             "# idx origin sig rev length addr file status"]
    for i, (origin, sig, rev, length, address, name, status) in enumerate(rows):
        lines.append(f"{i:02X} {origin} {sig} {rev} {length:08X} {address:016X} {name} {status}")
    return "\n".join(lines) + "\n"


def summary_text(files_before, bytes_before, rsdp_address):
    lines = ["# SP11PROBE summary v1", "build=000000000000", "variant=synthetic",
             "config_tables=3",
             f"cfg={ACPI20_GUID} 0x{rsdp_address:016X}",
             f"cfg={SMBIOS3_GUID} 0x{ADDR['SMBIOS']:016X}",
             "dump_run=1", f"dump_files_before_summary={files_before}",
             f"dump_bytes_before_summary={bytes_before}", "dump_complete_so_far=1"]
    return "\n".join(lines) + "\n"


def build(spec=Spec()):
    """Returns (files, marker): stick path -> bytes, and the DUMP marker's fields."""
    dsdt, apic = table(b"DSDT", 0x80), table(b"APIC", 0x60)
    facs = b"FACS" + struct.pack("<I", 64) + bytes(56)
    facs_address = ADDR["FACS"] if spec.facs else 0
    facp = fadt(spec.wide,
                ADDR["DSDT"] if spec.fadt_dsdt is None else spec.fadt_dsdt,
                facs_address if spec.fadt_facs is None else spec.fadt_facs)
    root = root_table(spec.root, [ADDR["FACP"],
                                  ADDR["APIC"] if spec.root_entry is None else spec.root_entry])
    run = {"RSDP.bin": rsdp(spec.root, ADDR["ROOT"] if spec.rsdp_root is None else spec.rsdp_root),
           f"{spec.root}.bin": root, "FACP.bin": facp, "APIC.bin": apic, "DSDT.bin": dsdt}
    if spec.facs:
        run["FACS.bin"] = facs
    if spec.smbios:
        smbios = smbios_table()
        run["smbios-ep.raw"] = smbios_entry(spec.smbios, len(smbios))
        run["smbios-table.raw"] = smbios
    run["memmap.raw"], memmap_text = memmap_files()
    run["memmap.txt"] = memmap_text.encode()
    rows = [("ROOT", spec.root, 1, len(root), ADDR["ROOT"], f"{spec.root}.bin", "OK"),
            ("ENTRY", "FACP", 6, len(facp), ADDR["FACP"], "FACP.bin", "OK"),
            ("ENTRY", "APIC", 2, len(apic), ADDR["APIC"], "APIC.bin", "OK"),
            ("DSDT", "DSDT", 2, len(dsdt), ADDR["DSDT"], "DSDT.bin", "OK")]
    rows.append(("FACS", "FACS", 0, len(facs), facs_address, "FACS.bin", "OK") if spec.facs
                else ("FACS", "FACS", 0, 0, 0, "-", "ABSENT"))
    run["tables.txt"] = tables_text(
        rows, ADDR["RSDP"] if spec.tables_rsdp is None else spec.tables_rsdp).encode()
    run["summary.txt"] = summary_text(
        len(run), sum(len(d) for d in run.values()),
        ADDR["RSDP"] if spec.summary_rsdp is None else spec.summary_rsdp).encode()
    marker = {"STATE": "OK", "RUN": "1", "FILES": str(len(run)),
              "BYTES": str(sum(len(d) for d in run.values()))}
    files = {f"{RUN_DIR}/{name}": data for name, data in run.items()}
    files[BOOT_FILE] = b"MZ decoy outside the run directory"
    return files, marker


def path(name):
    return f"{RUN_DIR}/{name}"


def edit_text(files, name, old, new):
    """Copy of `files` with `old` replaced by `new` in one text file.

    A missing `old` raises, so a change to the dump format cannot turn the
    test that relies on this edit into a vacuous one.
    """
    text = files[path(name)].decode("ascii")
    if old not in text:
        raise AssertionError(f"{name} has no {old!r} to replace")
    return {**files, path(name): text.replace(old, new, 1).encode("ascii")}


def patch_byte(files, name, offset, value):
    """Copy of `files` with one byte of one file replaced."""
    data = bytearray(files[path(name)])
    data[offset] = value
    return {**files, path(name): bytes(data)}


def replace_file(files, name, data):
    return {**files, path(name): data}


def drop_file(files, name):
    return {key: value for key, value in files.items() if key != path(name)}
