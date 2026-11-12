#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A pure, host-only model of the dcache sweep in slbounce's ExitBootServices hook.

This is NOT q1n1 or slbounce code, and it is not linked into anything. It exists so
docs/SP11-Q1N1-BOOT-STUDY.md section 4 can be checked by a test instead of by prose
alone. `classic_sweep()` reproduces the behaviour at TravMurav/slbounce's pinned
commit c090a8cdafa25e4c99df90f8d6f73f3805d9b397, `src/bounce_main.c` lines 117-134,
exactly: every descriptor whose Type is one of the six EfiLoaderCode/EfiLoaderData/
EfiBootServicesCode/EfiBootServicesData/EfiRuntimeServicesCode/EfiRuntimeServicesData
plus EfiACPIReclaimMemory is flushed in full; the `Attribute` field is never read.
`scoped_sweep()` is this study's proposed replacement for a Snapintosh fork of that
hook: flush only the physical ranges the payload itself declares (q1n1 already
enumerates its own heap/stage/image/DMA footprint for an unrelated purpose, see
platform/uefi/main.c `gpu_setup()` lines 671-684 at the pinned q1n1 commit
7872398694ca8322e9cdebcc4ca2380491295f24), require each declared range to sit
inside one single matching descriptor that also advertises EFI_MEMORY_WB (an
UNCHECKED field in the upstream sweep; cache maintenance on non-write-back memory
is a no-op at best, per the architecture, and a plausible explanation for the one
concrete hang TravMurav/slbounce#13 records: an ordinary-looking EfiBootServicesData
range, 231 pages, that is fatal to touch on a different Surface unit), and never
emit a single operation larger than a caller-chosen cap, splitting a large declared
range into chunks instead.

Evidence labels (as used throughout the Surface Pro 11 studies): every byte offset
and type name here is VERIFIED by reading the pinned slbounce source (cited above)
and the pinned q1n1 source; the "fatal EfiBootServicesData range" explanation is
INFERRED (architecturally plausible, not confirmed on hardware); nothing here has
run on a Surface Pro, and passing these tests proves the model against its own
rules, not the firmware. See docs/SP11-Q1N1-BOOT-STUDY.md section 4 for the full
reasoning and docs/SP11-Q1N1-BOOT-STUDY.md section 10 for what a host model can
and cannot prove here.
"""
import argparse
from dataclasses import dataclass, field

PAGE_BYTES = 4096  # bounce_main.c:121, `MemoryEntry->NumberOfPages * 4096`

# EFI_MEMORY_DESCRIPTOR.Type values the UEFI spec defines, named as bounce_main.c
# names them (bounce_main.c:124-130 switches on exactly the seven marked SWEPT).
EFI_RESERVED_MEMORY_TYPE = 0
EFI_LOADER_CODE = 1                 # SWEPT
EFI_LOADER_DATA = 2                 # SWEPT
EFI_BOOT_SERVICES_CODE = 3          # SWEPT
EFI_BOOT_SERVICES_DATA = 4          # SWEPT
EFI_RUNTIME_SERVICES_CODE = 5       # SWEPT
EFI_RUNTIME_SERVICES_DATA = 6       # SWEPT
EFI_CONVENTIONAL_MEMORY = 7
EFI_UNUSABLE_MEMORY = 8
EFI_ACPI_RECLAIM_MEMORY = 9         # SWEPT
EFI_ACPI_MEMORY_NVS = 10
EFI_MEMORY_MAPPED_IO = 11
EFI_MEMORY_MAPPED_IO_PORT_SPACE = 12
EFI_PAL_CODE = 13

# bounce_main.c:124-130, the case labels of the switch inside the sweep loop.
SWEPT_TYPES = frozenset({
    EFI_LOADER_CODE, EFI_LOADER_DATA, EFI_BOOT_SERVICES_CODE, EFI_BOOT_SERVICES_DATA,
    EFI_RUNTIME_SERVICES_CODE, EFI_RUNTIME_SERVICES_DATA, EFI_ACPI_RECLAIM_MEMORY,
})

# EFI_MEMORY_DESCRIPTOR.Attribute bit the UEFI spec names EFI_MEMORY_WB (bit
# index 3, value 0x8: "the memory region supports being configured as write-back
# cacheable"). The upstream sweep never reads this field.
EFI_MEMORY_WB = 1 << 3

DEFAULT_CHUNK_CAP = 1 << 20  # 1 MiB: the project's own existing GPU-submission cap
                              # (AGENTS.md: "never submit a single CP_MEMCPY larger
                              # than 1 MiB"), reused here as a starting default for
                              # the same reason -- an unmeasured but bounded unit of
                              # work -- not a measurement of slbounce's own hang.


@dataclass(frozen=True)
class Descriptor:
    """One EFI_MEMORY_DESCRIPTOR, in the fields this model needs."""
    type: int
    base: int
    pages: int
    attribute: int = EFI_MEMORY_WB  # most real descriptors are WB; a test states otherwise

    def __post_init__(self):
        if self.base < 0 or self.pages <= 0:
            raise ValueError(f"malformed descriptor base=0x{self.base:x} pages={self.pages}")

    @property
    def size(self):
        return self.pages * PAGE_BYTES

    @property
    def end(self):
        return self.base + self.size

    def contains(self, base, size):
        return self.base <= base and base + size <= self.end


class SweepRefused(Exception):
    """A declared range the scoped sweep cannot justify. Raised, never silently
    dropped or silently widened: AGENTS.md "fail closed"."""


def classic_sweep(descriptors):
    """bounce_main.c:117-134, unmodified: one (base, size) op per matching
    descriptor, full extent, Attribute ignored. This is the UPSTREAM behaviour,
    reproduced as a reference oracle, not a recommendation."""
    return [(d.base, d.size) for d in descriptors if d.type in SWEPT_TYPES]


def scoped_sweep(descriptors, declared_ranges, chunk_cap=DEFAULT_CHUNK_CAP):
    """The proposed replacement. `declared_ranges` is an iterable of (base, size)
    pairs the payload (q1n1) asserts it has itself written since boot -- the
    ranges `gpu_setup()` already lists for a different reason at the pinned q1n1
    commit (heap, DMA arena, stage slots, its own image, the memory-map buffer,
    the GPU arena, bootinfo/preload data; `main.c` lines 675-684), plus the
    tcblaunch.exe PE image and Secure-Launch argument blob slbounce's own
    `sl_create_data()` already flushes individually (`sl.c:313-315`) and so does
    not need to be declared here again.

    Refuses (SweepRefused) rather than guesses when a declared range:
      - is empty or has a negative size or base;
      - is not wholly contained in exactly one descriptor (no splitting a
        declared range across a memory-map boundary: a fragmented allocation
        must be declared as separate, descriptor-sized pieces instead);
      - sits in a descriptor whose Type is not one of SWEPT_TYPES; or
      - sits in a descriptor that does not advertise EFI_MEMORY_WB.
    chunk_cap bounds every emitted operation; a cap of 0 or less is refused.
    """
    if chunk_cap <= 0:
        raise SweepRefused(f"chunk_cap must be positive, got {chunk_cap}")
    ops = []
    for base, size in declared_ranges:
        if size <= 0 or base < 0:
            raise SweepRefused(f"malformed declared range base=0x{base:x} size=0x{size:x}")
        covering = [d for d in descriptors if d.contains(base, size)]
        if len(covering) != 1:
            raise SweepRefused(
                f"declared range 0x{base:x}+0x{size:x} is covered by {len(covering)} "
                "descriptors (want exactly 1); split it at the memory-map boundary"
            )
        d = covering[0]
        if d.type not in SWEPT_TYPES:
            raise SweepRefused(
                f"declared range 0x{base:x}+0x{size:x} lies in descriptor type {d.type}, "
                "which the upstream sweep never touches either"
            )
        if not (d.attribute & EFI_MEMORY_WB):
            raise SweepRefused(
                f"declared range 0x{base:x}+0x{size:x} lies in a non-write-back "
                f"descriptor (attribute=0x{d.attribute:x}); cache maintenance on it "
                "is pointless at best and is the one concrete hang this design avoids"
            )
        at, remaining = base, size
        while remaining > 0:
            take = min(remaining, chunk_cap)
            ops.append((at, take))
            at += take
            remaining -= take
    return ops


def total_bytes(ops):
    return sum(size for _, size in ops)


def merged_intervals(ops):
    """Collapse a list of (base, size) ops into the minimal sorted list of
    disjoint, merged [base, end) intervals they cover. Used to compare scoped
    and classic coverage without caring where scoped's chunk boundaries fall."""
    spans = sorted((base, base + size) for base, size in ops if size > 0)
    merged = []
    for base, end in spans:
        if merged and base <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((base, end))
    return merged


def wb_swept_extents(descriptors):
    """Every byte a correctly-attributed classic sweep would touch: the WB subset
    of what `classic_sweep` touches. Used by the equivalence test: when the
    declared ranges are exactly these extents, `scoped_sweep` must cover exactly
    the same bytes as `classic_sweep` restricted to WB descriptors."""
    return [(d.base, d.size) for d in descriptors if d.type in SWEPT_TYPES and (d.attribute & EFI_MEMORY_WB)]


def _demo():
    """A synthetic, illustrative memory map: a q1n1-sized loader footprint next to
    a large firmware-owned EfiBootServicesData range shaped like the one
    TravMurav/slbounce#13 reports as fatal (231 pages, an "ordinary" descriptor
    the sweep would otherwise walk into blindly)."""
    heap_base = 0x90000000
    descriptors = [
        Descriptor(EFI_LOADER_CODE, 0x80000000, pages=4),               # q1n1.efi's own image
        Descriptor(EFI_LOADER_DATA, heap_base, pages=(64 << 20) // PAGE_BYTES),  # HEAP_BYTES, main.c
        Descriptor(EFI_BOOT_SERVICES_DATA, 0xA7F19000, pages=231),       # shaped like the issue #13 report
        Descriptor(EFI_RUNTIME_SERVICES_DATA, 0xA8000000, pages=16),
        Descriptor(EFI_CONVENTIONAL_MEMORY, 0xB0000000, pages=1 << 20), # free RAM: never swept either way
    ]
    declared = [(0x80000000, 4 * PAGE_BYTES), (heap_base, 64 << 20)]
    classic = classic_sweep(descriptors)
    scoped = scoped_sweep(descriptors, declared)

    def show(label, ops):
        intervals = ["0x%x-0x%x" % (b, e) for b, e in merged_intervals(ops)]
        print(f"{label}: {len(ops)} op(s), {len(intervals)} covered range(s), "
              f"{total_bytes(ops) / (1 << 20):.3f} MiB total")
        for s in intervals:
            print("   ", s)

    show("classic_sweep (today's slbounce)", classic)
    show("scoped_sweep (this study's proposal)", scoped)
    print("the 231-page EfiBootServicesData range at 0xa7f19000 is in classic's output:",
          any(b == 0xA7F19000 for b, _ in classic))
    print("the 231-page EfiBootServicesData range at 0xa7f19000 is in scoped's output:",
          any(b == 0xA7F19000 for b, _ in scoped))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo"], nargs="?", default="demo")
    parser.parse_args()
    _demo()


if __name__ == "__main__":
    main()
