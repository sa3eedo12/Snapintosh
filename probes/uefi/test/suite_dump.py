# SPDX-License-Identifier: MIT
"""The independent dump verifier (host/checks.py), tested on synthetic dumps.

checks.py is what the QEMU scenarios use to judge the probe's output, so it
gets its own tests here: coherent dumps in every variation must pass, and
every relation it claims to check is broken once, on its own, to show that the
checker notices. The register decoder is checked against values computed by
hand from the architecture's field positions, not against the C code.
"""
from __future__ import annotations

import itertools
import re
import struct

import uefi_testkit as kit
import checks
import synth_dump as sd
from synth_dump import ADDR, RUN_DIR, Spec


def verdict(files, marker=None):
    return checks.dump_problems(files, RUN_DIR, marker)


# ---- Edit factories: each returns a function files -> files ----------------


def flip(name, offset):
    return lambda f: sd.patch_byte(f, name, offset, f[sd.path(name)][offset] ^ 1)


def put(name, offset, fmt, value):
    def edit(files):
        data = bytearray(files[sd.path(name)])
        struct.pack_into(fmt, data, offset, value)
        return sd.replace_file(files, name, bytes(data))
    return edit


def cut(name, length):
    return lambda f: sd.replace_file(f, name, f[sd.path(name)][:length])


def append(name, data):
    return lambda f: sd.replace_file(f, name, f[sd.path(name)] + data)


def text(name, old, new):
    return lambda f: sd.edit_text(f, name, old, new)


def set_file(name, data):
    return lambda f: sd.replace_file(f, name, data)


def remove(name):
    return lambda f: sd.drop_file(f, name)


def bump(name, key):
    """Adds one to the decimal value of `key=` in a text file."""
    def edit(files):
        found = re.search(rf"{key}=(\d+)", files[sd.path(name)].decode())
        return sd.edit_text(files, name, found.group(0), f"{key}={int(found.group(1)) + 1}")
    return edit


def smbios_with(table, version="3"):
    """Replaces the SMBIOS table and rebuilds a matching entry point."""
    def edit(files):
        files = sd.replace_file(files, "smbios-table.raw", table)
        return sd.replace_file(files, "smbios-ep.raw", sd.smbios_entry(version, len(table)))
    return edit


# (what is broken, dump to start from, the edit, a fragment of the expected problem)
FILE_CASES = (
    ("FACP checksum", None, flip("FACP.bin", 100), "FACP.bin: checksum byte sum"),
    ("APIC signature", None, flip("APIC.bin", 0), "APIC.bin: signature field is"),
    ("DSDT header length", None, put("DSDT.bin", 4, "<I", 0x81),
     "DSDT.bin: header length 129 but file is 128 bytes"),
    ("table shorter than a header", None, cut("APIC.bin", 35), "shorter than an ACPI header"),
    ("FACS signature", None, flip("FACS.bin", 0), "FACS.bin: signature field is"),
    ("FACS length", None, put("FACS.bin", 4, "<I", 65),
     "FACS.bin: header length 65 but file is 64 bytes"),
    ("FACS shorter than 24 bytes", None, cut("FACS.bin", 23), "shorter than a FACS"),
    ("RSDP v1 checksum", None, flip("RSDP.bin", 9), "first 20 bytes do not sum to 0"),
    ("RSDP extended checksum", None, flip("RSDP.bin", 33), "extended checksum does not sum to 0"),
    ("RSDP signature", None, flip("RSDP.bin", 0), "RSDP.bin: signature"),
    ("RSDP length field", None, put("RSDP.bin", 20, "<I", 40), "length field 40 but file is 36"),
    ("RSDP under 20 bytes", None, cut("RSDP.bin", 19), "shorter than a v1 RSDP"),
    ("RSDP revision 2 under 36 bytes", None, cut("RSDP.bin", 30), "shorter than 36 bytes"),
    ("memmap.raw truncated", None, lambda f: sd.replace_file(f, "memmap.raw", f[sd.path("memmap.raw")][:-48]),
     "memmap.raw:"),
    ("memmap row differs from raw", None, text("memmap.txt", "0000000040000000", "0000000040001000"),
     "disagrees with raw"),
    ("memmap header count", None, text("memmap.txt", "count=3", "count=4"), "header count 4 but 3 rows"),
    ("memmap descriptor size", None, text("memmap.txt", "descriptor_size=48", "descriptor_size=44"),
     "not a plausible"),
    ("memmap row order", None, text("memmap.txt", "\n0001 ", "\n0002 "), "out of order"),
    ("memmap first line", None, text("memmap.txt", "memmap v1", "memmap v2"),
     "missing '# SP11PROBE memmap v1'"),
    ("tables.txt row length", None, text("tables.txt", " 00000114 ", " 00000113 "),
     "FACP.bin has 276 bytes, row says 275"),
    ("listed table missing", None, remove("APIC.bin"), "names APIC.bin, which is not in the dump"),
    ("unlisted table present", None, set_file("ZZZZ.bin", sd.table(b"ZZZZ", 64)),
     "does not list"),
    ("row status not OK", None, text("tables.txt", "APIC.bin OK", "APIC.bin BADSUM"),
     "APIC.bin is listed with status BADSUM"),
    ("OK row without a file", None, text("tables.txt", "FACS.bin OK", "- OK"),
     "is OK but names no file"),
    ("two ROOT rows", None, text("tables.txt", "01 ENTRY FACP", "01 ROOT FACP"),
     "2 ROOT rows, expected 1"),
    ("no ROOT row", None, text("tables.txt", "00 ROOT XSDT", "00 ENTRY XSDT"),
     "0 ROOT rows, expected 1"),
    ("ENTRY address", None, text("tables.txt", "0000000070003000 APIC.bin", "0000000070003001 APIC.bin"),
     "ENTRY rows differ"),
    ("ROOT address", None, text("tables.txt", "0000000070001000 XSDT.bin", "0000000070001008 XSDT.bin"),
     "ROOT row is XSDT at 0x70001008, RSDP.bin points at XSDT 0x70001000"),
    ("ROOT signature", None, text("tables.txt", "00 ROOT XSDT 1", "00 ROOT RSDT 1"), "ROOT row is RSDT"),
    ("DSDT row at the 32-bit decoy", None,
     text("tables.txt", "0000000070004000 DSDT.bin", f"{sd.FADT_DECOY_DSDT:016X} DSDT.bin"),
     "but the FADT points at 0x70004000"),
    ("unparseable row", None, text("tables.txt", "02 ENTRY APIC 2 00000060", "02 ENTRY APIC 2 0000060"),
     "unparseable row"),
    ("tables.txt first line", None, text("tables.txt", "tables v1", "tables v2"),
     "missing '# SP11PROBE tables v1'"),
    ("no rsdp_addr header", None, text("tables.txt", "# rsdp_addr=", "# rsdp_addx="),
     "no rsdp_addr header"),
    ("tables.txt not ASCII", None, append("tables.txt", b"\xff"), "ascii"),
    ("summary file counter", None, bump("summary.txt", "dump_files_before_summary"),
     "files before the summary"),
    ("summary byte counter", None, bump("summary.txt", "dump_bytes_before_summary"),
     "bytes before the summary plus its own"),
    ("summary counters missing", None,
     text("summary.txt", "dump_files_before_summary=", "dump_files_before_summarx="), "missing"),
    ("summary first line", None, text("summary.txt", "summary v1", "summary v9"),
     "missing '# SP11PROBE summary v1'"),
    ("summary repeated key", None, append("summary.txt", b"variant=again\n"), "repeated key 'variant'"),
    ("summary line without =", None, append("summary.txt", b"junk\n"), "line without '='"),
    ("SMBIOS 3 checksum", None, flip("smbios-ep.raw", 5), "checksum does not sum to 0"),
    ("SMBIOS 3 declared length", None, set_file("smbios-ep.raw", sd.smbios_entry("3", 400)),
     "entry point declares 400 bytes"),
    ("SMBIOS anchor", None, flip("smbios-ep.raw", 0), "unknown anchor"),
    ("SMBIOS 3 entry truncated", None, cut("smbios-ep.raw", 23), "expected a 24-byte 64-bit entry point"),
    ("SMBIOS table without end marker", None, smbios_with(sd.smbios_table()[:-6]),
     "no end-of-table structure"),
    ("SMBIOS data after the end marker", None, smbios_with(sd.smbios_table() + b"\0\0\0"),
     "end-of-table structure ends at"),
    ("SMBIOS structure length under 4", None, smbios_with(bytes([1, 2, 0, 0, 0, 0])), "has length 2"),
    ("SMBIOS unterminated strings", None, smbios_with(bytes([0, 4, 0, 0]) + b"A"),
     "unterminated string set"),
    ("SMBIOS table without entry point", None, remove("smbios-ep.raw"),
     "only one of smbios-ep.raw and smbios-table.raw"),
    ("SMBIOS 2 entry truncated", Spec(smbios="2"), cut("smbios-ep.raw", 30),
     "expected a 31-byte 32-bit entry point"),
    ("SMBIOS 2 intermediate checksum", Spec(smbios="2"), flip("smbios-ep.raw", 21),
     "entry point or intermediate checksum is wrong"),
    ("SMBIOS 2 declared length", Spec(smbios="2"), set_file("smbios-ep.raw", sd.smbios_entry("2", 999)),
     "entry point declares 999 bytes"),
    ("RSDT root table address", Spec(root="RSDT", wide=False, facs=False),
     text("tables.txt", "0000000070001000 RSDT.bin", "0000000070001004 RSDT.bin"), "ROOT row is RSDT"),
)

# Each Spec field breaks one relation between what the tables say and what tables.txt records.
KNOB_CASES = (
    ("FADT DSDT pointer", Spec(fadt_dsdt=ADDR["DSDT"] + 0x10), "DSDT row(s)"),
    ("FADT FACS pointer", Spec(fadt_facs=ADDR["FACS"] + 0x10), "FACS row(s)"),
    ("FADT claims a FACS that tables.txt lists as absent", Spec(facs=False, fadt_facs=ADDR["FACS"]),
     "FACS row(s) ['0x0'] but the FADT points at"),
    ("narrow FADT DSDT pointer", Spec(wide=False, fadt_dsdt=ADDR["DSDT"] + 0x10), "DSDT row(s)"),
    ("RSDP root pointer", Spec(rsdp_root=ADDR["ROOT"] + 8), "ROOT row is"),
    ("RSDT root pointer", Spec(root="RSDT", rsdp_root=ADDR["ROOT"] + 8), "ROOT row is"),
    ("root table entry", Spec(root_entry=ADDR["APIC"] + 8), "ENTRY rows differ"),
    ("narrow root table entry", Spec(root="RSDT", root_entry=ADDR["APIC"] + 8), "ENTRY rows differ"),
    ("summary RSDP address", Spec(summary_rsdp=ADDR["RSDP"] + 8), "not one of the ACPI configuration"),
    ("tables.txt RSDP address", Spec(tables_rsdp=ADDR["RSDP"] + 8), "not one of the ACPI configuration"),
)


def run_cases(cases):
    out = []
    for label, spec, edit, fragment in cases:
        files, marker = sd.build(spec or Spec())
        got = verdict(edit(files) if edit else files, marker)
        if not any(fragment in problem for problem in got):
            out.append(f"{label}: expected a problem containing {fragment!r}, got {got or 'none'}")
    return out


class DumpCheckerTests(kit.Base):
    def test_every_coherent_variation_passes(self):
        problems = []
        for root, facs, wide, smbios in itertools.product(("XSDT", "RSDT"), (True, False),
                                                          (True, False), ("3", "2", None)):
            spec = Spec(root=root, facs=facs, wide=wide, smbios=smbios)
            files, marker = sd.build(spec)
            problems += [f"{spec}: {p}" for p in verdict(files, marker)]
        self.check(problems)

    def test_builder_makes_the_variations_it_claims(self):
        absent, present = sd.build(Spec(facs=False))[0], sd.build(Spec(facs=True))[0]
        narrow, wide = sd.build(Spec(wide=False))[0], sd.build(Spec(wide=True))[0]
        no_smbios = sd.build(Spec(smbios=None))[0]
        rsdt = sd.build(Spec(root="RSDT"))[0]
        problems = []
        if sd.path("FACS.bin") in absent or sd.path("FACS.bin") not in present:
            problems.append("the facs knob does not add and remove FACS.bin")
        if b"FACS FACS 0 00000000 0000000000000000 - ABSENT" not in absent[sd.path("tables.txt")]:
            problems.append("an absent FACS has no ABSENT row")
        if struct.unpack_from("<I", wide[sd.path("FACP.bin")], 40)[0] != sd.FADT_DECOY_DSDT:
            problems.append("a wide FADT lacks the 32-bit decoy DSDT pointer")
        if struct.unpack_from("<Q", wide[sd.path("FACP.bin")], 140)[0] != ADDR["DSDT"]:
            problems.append("a wide FADT lacks X_DSDT")
        if struct.unpack_from("<Q", narrow[sd.path("FACP.bin")], 140)[0] != 0 \
                or struct.unpack_from("<I", narrow[sd.path("FACP.bin")], 40)[0] != ADDR["DSDT"]:
            problems.append("a narrow FADT is not 32-bit only")
        if sd.path("smbios-ep.raw") in no_smbios:
            problems.append("smbios=None still writes SMBIOS files")
        if len(rsdt[sd.path("RSDP.bin")]) != 20 or sd.path("RSDT.bin") not in rsdt:
            problems.append("an RSDT root does not give a 20-byte RSDP and RSDT.bin")
        self.check(problems)

    def test_file_corruptions_are_each_reported(self):
        self.check(run_cases(FILE_CASES))

    def test_single_inconsistencies_between_tables_are_reported(self):
        self.check(run_cases([(label, spec, None, fragment) for label, spec, fragment in KNOB_CASES]))

    def test_marker_counts_must_match_the_stick(self):
        files, marker = sd.build()
        problems = []
        for key, fragment in (("FILES", "marker says FILES"), ("BYTES", "marker says BYTES")):
            got = verdict(files, {**marker, key: str(int(marker[key]) + 1)})
            if not any(fragment in p for p in got):
                problems.append(f"a wrong {key} was not reported: {got}")
        if verdict(files, None):
            problems.append("without a marker the dump should be judged on its own")
        self.check(problems)

    def test_required_files_and_run_scoping(self):
        files, marker = sd.build()
        problems = []
        for name in ("summary.txt", "tables.txt", "memmap.txt", "memmap.raw", "RSDP.bin"):
            got = verdict(sd.drop_file(files, name), marker)
            if got != [f"{RUN_DIR}: {name} is missing"]:
                problems.append(f"without {name}: {got}")
        if verdict({sd.BOOT_FILE: b"x"}) != [f"{RUN_DIR}: no files"]:
            problems.append("a stick with no run directory is not reported as empty")
        neighbours = {**files, "SP11DUMP/RUN02/junk.bin": b"junk", "SP11DUMP/RUN010/x.bin": b"junk"}
        if verdict(neighbours, marker):
            problems.append(f"files of other runs leaked into RUN01: {verdict(neighbours, marker)}")
        if checks.dump_problems(files, "/" + RUN_DIR + "/", marker):
            problems.append("slashes around the run directory name change the verdict")
        self.check(problems)

    def test_truncated_files_are_flagged_and_never_crash_the_checker(self):
        files, marker = sd.build()
        problems = []
        for name in sorted(files):
            if not name.startswith(RUN_DIR + "/"):
                continue
            size = len(files[name])
            for length in sorted({0, 1, 3, 4, 8, 19, 20, 35, 36, size // 2, size - 1} & set(range(size))):
                broken = {**files, name: files[name][:length]}
                try:
                    got = verdict(broken, marker)
                except Exception as exc:  # noqa: BLE001 - any exception is a verifier bug
                    problems.append(f"{name} cut to {length}: raised {exc!r}")
                    continue
                if not got:
                    problems.append(f"{name} cut to {length} bytes passed")
        self.check(problems)

    def test_bit_flips_never_crash_the_checker(self):
        problems = []
        for spec in (Spec(), Spec(root="RSDT", wide=False, facs=False, smbios="2")):
            files, marker = sd.build(spec)
            for name in sorted(files):
                if not name.startswith(RUN_DIR + "/"):
                    continue
                for offset in range(len(files[name])):
                    for bit in (0x01, 0x80):
                        data = bytearray(files[name])
                        data[offset] ^= bit
                        try:
                            got = verdict({**files, name: bytes(data)}, marker)
                        except Exception as exc:  # noqa: BLE001 - any exception is a verifier bug
                            problems.append(f"{name}[{offset}] ^ {bit:#x}: raised {exc!r}")
                            continue
                        if not all(isinstance(p, str) for p in got):
                            problems.append(f"{name}[{offset}]: non-string problem {got}")
            if len(problems) > 10:
                break
        self.check(problems[:10])


class FacsTests(kit.Base):
    """The FACS has a signature and a length but no checksum field."""

    def test_facs_is_exempt_from_the_checksum_only(self):
        facs = b"FACS" + struct.pack("<I", 64) + bytes(range(56))
        self.assertNotEqual(sum(facs) & 0xFF, 0, "the sample must have a non-zero byte sum")
        problems = []
        if checks.acpi_table_problems("FACS.bin", facs):
            problems.append(f"a valid FACS was rejected: {checks.acpi_table_problems('FACS.bin', facs)}")
        if checks.acpi_table_problems("FACS_2.bin", facs):
            problems.append("a duplicate-named FACS_2.bin is not treated as a FACS")
        small = b"FACS" + struct.pack("<I", 24) + bytes(16)
        if checks.acpi_table_problems("FACS.bin", small):
            problems.append("the 24-byte minimum FACS was rejected")
        expectations = (
            ("a 23-byte FACS", facs[:23], "shorter than a FACS"),
            ("the wrong signature", b"FACT" + facs[4:], "signature field is"),
            ("the wrong length", facs[:4] + struct.pack("<I", 65) + facs[8:], "header length 65"),
        )
        for label, data, fragment in expectations:
            if not any(fragment in p for p in checks.acpi_table_problems("FACS.bin", data)):
                problems.append(f"{label} was not reported")
        apic = sd.table(b"APIC", 64)
        flipped = apic[:50] + bytes([apic[50] ^ 1]) + apic[51:]
        if checks.acpi_table_problems("APIC.bin", apic):
            problems.append("a valid APIC was rejected")
        if not any("checksum" in p for p in checks.acpi_table_problems("APIC.bin", flipped)):
            problems.append("an APIC with one flipped byte passed")
        self.check(problems)


class DecodeCheckerTests(kit.Base):
    def test_register_decode_matches_hand_computed_values(self):
        regs = {
            "SCTLR_EL2": 0x0000000000081005,          # M C I WXN
            "HCR_EL2": 0x0000000488000001,            # VM TGE RW E2H
            "ID_AA64MMFR0_EL1": 0x210000001F200006,   # PARANGE 6, TGRAN16 2, TGRAN64 F, TGRAN4 1, FGT 1, ECV 2
            "ID_AA64MMFR1_EL1": 0x0000000000000110,   # VMIDBITS 1 (unlisted), VH 1
            "ID_AA64MMFR2_EL1": 0x0000000001000000,   # NV 1
            "ID_AA64PFR0_EL1": 0x0000200103000000,    # AMU 2, SVE 1, GIC 3
            "ID_AA64PFR1_EL1": 0x0000000002000000,    # SME 2
            "ID_AA64ISAR1_EL1": 0x0000000001000050,   # APA 5, GPA 1
        }
        want = ["MMU M=1 C=1 I=1 WXN=1",
                "HCR VM=1 TGE=1 RW=1 E2H=1",
                "PARANGE=6 52 BIT", "TGRAN4=1 LPA2", "TGRAN16=2 LPA2", "TGRAN64=F NO",
                "VMIDBITS=1", "VH=1 YES",
                "NV=1 NV",
                "ECV=2 CNTPOFF", "FGT=1 YES",
                "AMU=2 V1P1", "GIC=3 SYSREG V4.1", "SVE=1 YES", "SME=2 SME2",
                "PAUTH APA=5 API=0", "PAUTH GPA=1 GPI=0"]
        got = checks.expected_lines(regs)
        self.check([] if got == want else [f"expected {want}", f"got      {got}"])

    def test_registers_the_passive_probe_does_not_read_derive_no_lines(self):
        """MMFR3/4, ISAR2 and DFR1 are read only by the guarded tests image; even if a photo's
        parser somehow produced them, the passive probe's checker must not expect lines from them."""
        extra = {"ID_AA64MMFR3_EL1": ~0 & (2 ** 64 - 1), "ID_AA64MMFR4_EL1": 0x000000000E100000,
                 "ID_AA64ISAR2_EL1": 0x0000000000003100, "ID_AA64DFR1_EL1": 1}
        base = {"ID_AA64ISAR1_EL1": 0x150}
        got = checks.expected_lines({**base, **extra})
        problems = [] if got == checks.expected_lines(base) else [f"unexpected lines: {got}"]
        banned = ("NV_FRAC", "E2H0", "APA3", "ALGO")
        problems += [f"a {b} line is still derived" for b in banned if any(b in line for line in got)]
        self.assertEqual({d[1] for d in checks.DECODED_FIELDS} & set(extra), set())
        self.check(problems)

    def test_register_decode_edge_cases(self):
        problems = []
        if checks.expected_lines({}) != []:
            problems.append("no registers should give no lines")
        if checks.expected_lines({"ID_AA64ISAR1_EL1": 0})[-1] != "PAUTH GPA=0 GPI=0":
            problems.append("ISAR1 alone should give two PAUTH lines")
        mmu = checks.expected_lines({"SCTLR_EL2": 0xFFFFFFFFFFFFFFFF})
        if mmu != ["MMU M=1 C=1 I=1 WXN=1"]:
            problems.append(f"all-ones SCTLR decoded as {mmu}")
        hcr = checks.expected_lines({"HCR_EL2": 0xFFFFFFFFFFFFFFFF & ~(1 << 34) & ~(1 << 0)})
        if hcr != ["HCR VM=0 TGE=1 RW=1 E2H=0"]:
            problems.append(f"HCR with VM and E2H clear decoded as {hcr}")
        self.assertEqual((checks.bits(0xABCD, 11, 8), checks.bits(0xABCD, 3, 0), checks.bits(1 << 63, 63, 60)),
                         (0xB, 0xD, 8))
        self.check(problems)

    def test_register_lines_are_parsed_strictly(self):
        got = checks.parse_registers([
            "HCR_EL2=0000000488000001", "SPSR_EL2 RAW=0000000000000009",
            "ICC_SRE_EL2=SKIPPED GIC=0", "NOTE SPSR/ELR RAW MAY BE STALE",
            "hcr_el2=0000000000000001", "TCR_EL2=123", "MPIDR_EL1=0000000080000000 ", "MPIDR_EL1=00000000800000GG"])
        self.assertEqual(got, {"HCR_EL2": 0x488000001, "SPSR_EL2 RAW": 9})

    def test_text_parsers_return_the_fields_and_reject_malformed_input(self):
        files, _marker = sd.build()
        header, rows = checks.parse_tables_txt(files[sd.path("tables.txt")].decode())
        problems = []
        if header != {"rsdp_status": "OK", "rsdp_addr": "0x0000000070000000"}:
            problems.append(f"tables header {header}")
        if rows[0] != {"idx": 0, "origin": "ROOT", "sig": "XSDT", "rev": 1, "length": 0x34,
                       "addr": 0x70001000, "file": "XSDT.bin", "status": "OK"}:
            problems.append(f"first tables row {rows[0]}")
        mem_header, mem_rows = checks.parse_memmap_txt(files[sd.path("memmap.txt")].decode())
        if mem_header != {"descriptor_size": 48, "descriptor_version": 1, "count": 3, "map_size": 144}:
            problems.append(f"memmap header {mem_header}")
        if mem_rows[2] != {"type": 11, "phys": 0x7C000000, "virt": 0, "pages": 0x10,
                           "attr": 0x8000000000000001, "name": "MMIO"}:
            problems.append(f"memmap row {mem_rows[2]}")
        nameless = "# SP11PROBE memmap v1\n0000 07 " + " ".join(["0000000000000001"] * 4) + "\n"
        if checks.parse_memmap_txt(nameless)[1][0]["name"] != "":
            problems.append("a memmap row without a name should have an empty name")
        summary = checks.parse_summary(files[sd.path("summary.txt")].decode())
        if summary["cfg"] != [f"{sd.ACPI20_GUID} 0x0000000070000000", f"{sd.SMBIOS3_GUID} 0x0000000070006000"]:
            problems.append(f"summary cfg list {summary.get('cfg')}")
        for label, call, arg in (
                ("tables.txt empty", checks.parse_tables_txt, ""),
                ("tables.txt bad row", checks.parse_tables_txt, "# SP11PROBE tables v1\nnot a row\n"),
                ("memmap bad row", checks.parse_memmap_txt, "# SP11PROBE memmap v1\n0000 xx\n"),
                ("summary empty", checks.parse_summary, ""),
                ("summary repeat", checks.parse_summary, "# SP11PROBE summary v1\na=1\na=2\n")):
            try:
                call(arg)
                problems.append(f"{label}: accepted")
            except ValueError:
                pass
        self.check(problems)

    def test_rsdp_address_must_be_an_acpi_configuration_table(self):
        address = "0x0000000070000000"
        problems = []
        accepted = (
            ({"rsdp_addr": address}, {"cfg": [f"{sd.ACPI20_GUID} {address}"]}),
            ({"rsdp_addr": address}, {"cfg": [f"{sd.ACPI10_GUID} {address}"]}),
            ({"rsdp_addr": address}, {"cfg": [f"{sd.ACPI20_GUID.lower()} {address}"]}),
            ({"rsdp_addr": address}, {"cfg": [f"{sd.SMBIOS3_GUID} 0x1", f"{sd.ACPI20_GUID} {address}"]}),
        )
        for header, summary in accepted:
            if checks.rsdp_address_problems(header, summary):
                problems.append(f"rejected {summary}")
        refused = (
            ({"rsdp_addr": address}, {"cfg": [f"{sd.SMBIOS3_GUID} {address}"]}, "not one of the ACPI"),
            ({"rsdp_addr": address}, {}, "not one of the ACPI"),
            ({"rsdp_addr": address}, {"cfg": [f"{sd.ACPI20_GUID} 0x0000000070000008"]}, "not one of the ACPI"),
            ({}, {"cfg": [f"{sd.ACPI20_GUID} {address}"]}, "no rsdp_addr header"),
            ({"rsdp_addr": "nonsense"}, {"cfg": [f"{sd.ACPI20_GUID} {address}"]}, "unparseable"),
            ({"rsdp_addr": address}, {"cfg": [sd.ACPI20_GUID]}, "unparseable"),
        )
        for header, summary, fragment in refused:
            if not any(fragment in p for p in checks.rsdp_address_problems(header, summary)):
                problems.append(f"accepted {header} / {summary}")
        self.check(problems)
