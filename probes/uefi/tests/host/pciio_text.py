#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""pciio.txt, the firmware's PCI I/O view that the X2 tests image writes before ExitBootServices.

    python3 probes/uefi/tests/host/pciio_text.py pciio.txt        print what the file says

The image (probes/uefi/tests/pciio.c) asks the firmware's own EFI_PCI_IO_PROTOCOL where each PCI
function is and what it holds, read-only, and T06 reads an ECAM window at EL2 only for a segment where
that view shows an NVMe function. This module is the independent reader of the file: it decodes the
raw dwords a fake firmware held (the C test prints them with --emit-pciio) with its own code, so that
the C renderer and this reader are two implementations of one format kept in agreement by tests, and
it parses a file written by a real run into the same structure for the cross-checks against the page.

The format is line oriented:

    # SP11X2 pciio v1: ...                      the title line
    build=<id>  kind=<HARDWARE|MODEL>  state=<name> status=0x<16 hex>
    located=<n> kept=<n> nvme=<n> skipped=<n> truncated=<0|1>
    pci idx=<n> seg=<n> bus=<hh> dev=<hh> func=<n> root=<name> cfg=<ok|PARTIAL> id=<vvvv>:<dddd> ...
    nvme idx=<n> bar0=<16 hex> regs=<name> [cap= vs= cc= csts= aqa= asq= acq=]
    end

A file whose last line is not `end` was cut short by the buffer; what precedes it is still valid.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HID_PCI_ROOT = 0x0A0341D0   # PNP0A03 as an EISA id
HID_PCIE_ROOT = 0x0A0841D0  # PNP0A08
CLASS_NVME = 0x010802
CMD_MEMORY = 0x2
NVME_DWORDS = {"cap_lo": 0x00, "cap_hi": 0x04, "vs": 0x08, "cc": 0x14, "csts": 0x1C, "aqa": 0x24,
               "asq_lo": 0x28, "asq_hi": 0x2C, "acq_lo": 0x30, "acq_hi": 0x34}
STATE_NAMES = ("NOT COLLECTED", "OK", "NO BOOT SERVICES", "NO PROTOCOL", "LOCATE FAILED")


class PciioError(Exception):
    pass


def bar_base(lo: int, hi: int) -> int:
    """The base a memory BAR decodes to, or 0 for an I/O BAR, a reserved type or an unassigned BAR."""
    if lo & 1:
        return 0
    kind = (lo >> 1) & 3
    if kind == 0:
        return lo & ~0xF
    if kind == 2:
        return (hi << 32) | (lo & ~0xF)
    return 0


def root_name(hid: int, uid: int) -> str:
    if hid == HID_PCIE_ROOT:
        return f"PcieRoot(0x{uid:X})"
    if hid == HID_PCI_ROOT:
        return f"PciRoot(0x{uid:X})"
    return f"Acpi(0x{hid:08X},0x{uid:X})"


def function_line(index: int, raw: dict) -> str:
    """The `pci` line of one function from the dwords the fake firmware held."""
    cfg = raw["cfg"]  # dword index -> value; index 4 is offset 0x10
    hdr = (cfg[3] >> 16) & 0xFF
    kind = hdr & 0x7F
    bars = 6 if kind == 0 else 2 if kind == 1 else 0
    parts = [f"pci idx={index} seg={raw['seg']} bus={raw['bus']:02X} dev={raw['dev']:02X} func={raw['func']}",
             f"root={root_name(raw['hid'], raw['uid'])} cfg=ok",
             f"id={cfg[0] & 0xFFFF:04X}:{cfg[0] >> 16:04X} class={cfg[2] >> 8:06X} rev={cfg[2] & 0xFF:02X} "
             f"hdr={hdr:02X} cmd={cfg[1] & 0xFFFF:04X} sts={cfg[1] >> 16:04X}"]
    line = " ".join(parts)
    if bars:
        line += " bar=" + ",".join(f"{cfg[4 + i]:08X}" for i in range(bars))
    if kind == 1:
        buses = cfg[6]
        line += f" buses={buses & 0xFF:02X}:{(buses >> 8) & 0xFF:02X}:{(buses >> 16) & 0xFF:02X}"
    return line


def is_nvme(raw: dict) -> bool:
    cfg = raw["cfg"]
    return ((cfg[3] >> 16) & 0x7F) == 0 and (cfg[2] >> 8) == CLASS_NVME


def nvme_line(index: int, raw: dict) -> str:
    """The `nvme` line: the BAR0 base, and the seven registers if BAR0 is an assigned memory BAR
    and memory decode is on."""
    cfg, reg = raw["cfg"], raw["reg"]
    base = bar_base(cfg[4], cfg[5])
    line = f"nvme idx={index} bar0={base:016X} regs="
    if base == 0 or (cfg[4] & 1):
        return line + "NO BAR0"
    if not cfg[1] & CMD_MEMORY:
        return line + "DECODE OFF"
    cap = (reg[0x04 // 4] << 32) | reg[0x00 // 4]
    asq = (reg[0x2C // 4] << 32) | reg[0x28 // 4]
    acq = (reg[0x34 // 4] << 32) | reg[0x30 // 4]
    return (line + f"OK cap={cap:016X} vs={reg[0x08 // 4]:08X} cc={reg[0x14 // 4]:08X} "
            f"csts={reg[0x1C // 4]:08X} aqa={reg[0x24 // 4]:08X} asq={asq:016X} acq={acq:016X}")


def expected_text(raws: list, build: str, kind: str, state: str = "OK", status: int = 0) -> str:
    """The file the image should write for these functions, every one present and readable."""
    nvme = [i for i, r in enumerate(raws) if is_nvme(r)]
    lines = ["# SP11X2 pciio v1: the firmware's PCI I/O view, read before ExitBootServices (reads only)",
             f"build={build}", f"kind={kind}", f"state={state} status=0x{status:016X}",
             f"located={len(raws)} kept={len(raws)} nvme={len(nvme)} skipped=0 truncated=0"]
    lines += [function_line(i, r) for i, r in enumerate(raws)]
    lines += [nvme_line(i, raws[i]) for i in nvme]
    return "\n".join(lines) + "\nend\n"


# ---- Reading a file ----

_PCI = re.compile(
    r"^pci idx=(\d+) (?:seg=(\d+) bus=([0-9A-F]{2}) dev=([0-9A-F]{2}) func=(\d)|loc=BAD) root=(\S+) "
    r"(?:cfg=(ok|PARTIAL) id=([0-9A-F]{4}):([0-9A-F]{4}) class=([0-9A-F]{6}) rev=([0-9A-F]{2}) "
    r"hdr=([0-9A-F]{2}) cmd=([0-9A-F]{4}) sts=([0-9A-F]{4})(?: bar=([0-9A-F]{8}(?:,[0-9A-F]{8})*))?"
    r"(?: buses=([0-9A-F]{2}):([0-9A-F]{2}):([0-9A-F]{2}))?|cfg=FAILED)$")
_NVME = re.compile(
    r"^nvme idx=(\d+) bar0=([0-9A-F]{16}) regs=(NO BAR0|DECODE OFF|READ FAILED|NONE|OK"
    r"(?: cap=([0-9A-F]{16}) vs=([0-9A-F]{8}) cc=([0-9A-F]{8}) csts=([0-9A-F]{8}) aqa=([0-9A-F]{8}) "
    r"asq=([0-9A-F]{16}) acq=([0-9A-F]{16}))?)$")
_HEAD = re.compile(r"^state=(.+) status=0x([0-9A-F]{16})$")
_COUNTS = re.compile(r"^located=(\d+) kept=(\d+) nvme=(\d+) skipped=(\d+) truncated=([01])$")


def parse(text: str) -> dict:
    """The structure of a pciio.txt, strictly: any line that is not of the format raises PciioError."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) < 5 or not lines[0].startswith("# SP11X2 pciio v1"):
        raise PciioError("no title line")
    out = {"functions": [], "controllers": [], "complete": lines[-1] == "end"}
    for key, line in (("build", lines[1]), ("kind", lines[2])):
        if not line.startswith(key + "="):
            raise PciioError(f"expected {key}=, got {line!r}")
        out[key] = line[len(key) + 1:]
    m = _HEAD.match(lines[3])
    if not m or m.group(1) not in STATE_NAMES:
        raise PciioError(f"bad state line {lines[3]!r}")
    out["state"], out["status"] = m.group(1), int(m.group(2), 16)
    m = _COUNTS.match(lines[4])
    if not m:
        raise PciioError(f"bad counts line {lines[4]!r}")
    out.update(zip(("located", "kept", "nvme", "skipped", "truncated"), map(int, m.groups())))
    for line in lines[5:]:
        if line == "end":
            continue
        m = _PCI.match(line)
        if m and line.startswith("pci "):
            g = m.groups()
            fn = {"idx": int(g[0]), "root": g[5], "cfg": g[6]}
            if g[1] is not None:
                fn.update(seg=int(g[1]), bus=int(g[2], 16), dev=int(g[3], 16), func=int(g[4]))
            if g[7] is not None:
                fn.update(vendor=int(g[7], 16), device=int(g[8], 16), cls=int(g[9], 16),
                          rev=int(g[10], 16), hdr=int(g[11], 16), cmd=int(g[12], 16), sts=int(g[13], 16),
                          bars=[int(b, 16) for b in g[14].split(",")] if g[14] else [])
            if g[15] is not None:
                fn["buses"] = tuple(int(x, 16) for x in g[15:18])
            out["functions"].append(fn)
            continue
        m = _NVME.match(line)
        if m and line.startswith("nvme "):
            g = m.groups()
            ctl = {"idx": int(g[0]), "bar0": int(g[1], 16), "regs": "OK" if g[2].startswith("OK") else g[2]}
            if g[3] is not None:
                ctl.update(cap=int(g[3], 16), vs=int(g[4], 16), cc=int(g[5], 16), csts=int(g[6], 16),
                           aqa=int(g[7], 16), asq=int(g[8], 16), acq=int(g[9], 16))
            out["controllers"].append(ctl)
            continue
        raise PciioError(f"a line of no known kind: {line!r}")
    if out["complete"]:
        if out["kept"] != len(out["functions"]):
            raise PciioError(f"kept={out['kept']} but {len(out['functions'])} function lines")
        if out["nvme"] != len(out["controllers"]):
            raise PciioError(f"nvme={out['nvme']} but {len(out['controllers'])} nvme lines")
        if any(c["idx"] >= out["kept"] for c in out["controllers"]):
            raise PciioError("an nvme line names a function that is not in the file")
    return out


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    data = parse(Path(argv[1]).read_text(encoding="utf-8"))
    print(f"build {data['build']} kind {data['kind']} state {data['state']} "
          f"functions {len(data['functions'])} nvme {len(data['controllers'])} complete {data['complete']}")
    for fn in data["functions"]:
        print(fn)
    for ctl in data["controllers"]:
        print(ctl)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
