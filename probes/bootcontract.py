# SPDX-License-Identifier: MIT
"""Inputs shared by guest loaders and host adapters; no board addresses here.

An adapter describes resources it actually owns. A guest loader decides how
its boot ABI represents them. Neither may turn holes into usable memory.
"""
from collections import namedtuple
from dataclasses import dataclass, replace
from pathlib import Path
import struct
import sys
from monitor_work import locator_address


@dataclass(frozen=True)
class PhysicalRange:
    base: int
    size: int
    name: str

    @property
    def end(self):
        return self.base + self.size

    def validate(self, page_shift=14, address_bits=36):
        if self.base < 0 or self.size <= 0 or self.end > 1 << address_bits:
            raise ValueError(f'{self.name}: invalid {address_bits}-bit physical range')
        if (self.base | self.size) & ((1 << page_shift) - 1):
            raise ValueError(f'{self.name}: range is not {1 << page_shift}-byte aligned')
        return self


def checked_ranges(ranges, page_shift=14, address_bits=36):
    ranges = tuple(sorted(ranges, key=lambda r: r.base))
    if not ranges:
        raise ValueError('no physical ranges supplied')
    for i, region in enumerate(ranges):
        region.validate(page_shift, address_bits)
        if i and region.base < ranges[i - 1].end:
            raise ValueError(f'{region.name} overlaps {ranges[i - 1].name}')
    return ranges


@dataclass(frozen=True)
class Framebuffer:
    base: int
    width: int
    height: int
    stride: int                 # bytes, never pixels
    depth: int

    def physical_range(self, page_shift=14):
        if self.base <= 0 or self.width <= 0 or self.height <= 0:
            raise ValueError('framebuffer: base and geometry must be positive')
        if self.depth != 32 or self.stride < self.width * 4 or self.stride % 4:
            raise ValueError('framebuffer: expected 32-bit pixels and a valid byte stride')
        page = 1 << page_shift
        lo = self.base & -page
        hi = (self.base + self.stride * self.height + page - 1) & -page
        return PhysicalRange(lo, hi - lo, 'panel')

    def boot_video(self):
        return self.base, self.width, self.height, self.stride, self.depth


@dataclass(frozen=True)
class HostRuntime:
    """Transport-independent facts; additional adapters return this contract.

    CPU feature checks still run at EL2 before guest entry. A valid descriptor
    is not a claim that a new CPU implements the required NV2/debug features.
    """
    adapter: str
    timer_hz: int
    heap: PhysicalRange
    framebuffer: Framebuffer | None

    @classmethod
    def from_q1n1(cls, info, *, with_framebuffer=False):
        if info.get('current_el') != 2:
            raise ValueError('q1n1 host adapter requires EL2')
        hz = info.get('timer_hz', 0)
        if not isinstance(hz, int) or not 0 < hz < 1 << 32:
            raise ValueError('q1n1 must report a nonzero 32-bit timer_hz')
        heap = PhysicalRange(info.get('heap_base', 0), info.get('heap_size', 0),
                             'q1n1 heap').validate(12)
        # Code/output/config remain below +5 MiB. The work arena is separately
        # reserved firmware RAM; only its checked locator lives in this heap.
        locator_address(info)
        fb = None
        if with_framebuffer:
            fmt = info.get('fb_format')
            if fmt not in (0, 1):
                raise ValueError(f'GOP pixel format {fmt} cannot be described by Boot_Video')
            fb = Framebuffer(info.get('fb_base', 0), info.get('fb_width', 0),
                             info.get('fb_height', 0), info.get('fb_stride', 0) * 4, 32)
            region = fb.physical_range().validate()
            if fb.stride * fb.height > info.get('fb_size', 0):
                raise ValueError('framebuffer geometry exceeds the host allocation')
            if region.base < heap.end and heap.base < region.end:
                raise ValueError('framebuffer overlaps the q1n1 heap')
        return cls('q1n1', hz, heap, fb)

    @classmethod
    def from_hamoa(cls, info, board, tables, *, with_framebuffer=False):
        """Hamoa (Snapdragon X Elite) host adapter: q1n1's live facts plus a descriptor the ACPI tables confirm.

        Refuses, with a BootContractError, a descriptor that is not boot-ready, ACPI tables that disagree with it,
        and a live timer rate that differs from the descriptor's. The A16 path (from_q1n1) is unchanged.
        """
        check_hamoa_platform(board, tables)
        _require_boot_ready(board)
        try:
            host = cls.from_q1n1(info, with_framebuffer=with_framebuffer)
        except BootContractError:
            raise
        except ValueError as exc:
            raise BootContractError('host-info', str(exc)) from exc
        if board.timer_hz != host.timer_hz:
            raise BootContractError('timer-mismatch',
                                    f'q1n1 reports {host.timer_hz} Hz, the descriptor says {board.timer_hz} Hz')
        return replace(host, adapter='hamoa')


# ---------------------------------------------------------------------------------------------------------------
# Hamoa ACPI cross-check. Evidence: EXPERIMENTAL (synthetic tables only; no firmware dump has been checked and
# nothing here has run on hardware). Layouts are the public ACPI 6.5 ones (RSDP, XSDT, MADT: section 5.2) and the
# PCI Firmware Specification's MCFG; no vendor table content is embedded.
#
# A table is trusted only after its length field equals the bytes supplied, its signature and checksum agree, and
# every walk is bounded by the table length. Each fact the descriptor also states must then match exactly; nothing
# is filled in from a default, and every refusal carries a stable code so callers and tests can tell why.
# ---------------------------------------------------------------------------------------------------------------

BOOT_CONTRACT_CODES = (
    'descriptor-unimplemented', 'descriptor-unknown', 'descriptor-gic-version', 'descriptor-inconsistent',
    'descriptor-not-boot-ready',
    'table-missing', 'table-signature', 'table-length', 'table-checksum', 'table-structure',
    'gicd-mismatch', 'version-mismatch', 'gicr-mismatch', 'gic-overlap', 'its-mismatch', 'cpu-mismatch',
    'ecam-mismatch', 'timer-mismatch', 'host-info',
)

ACPI_HEADER_BYTES = 36
# Bounds every walk. A Hamoa MADT or MCFG is a few KiB; this only stops a corrupt length from costing memory or time.
ACPI_MAX_TABLE_BYTES = 1 << 20
RSDP_V1_BYTES, RSDP_V2_BYTES = 20, 36
MADT_FIXED_BYTES = 44            # header, local interrupt controller address, flags
MADT_MAX_ENTRIES = 4096
MADT_GICC, MADT_GICD, MADT_GICR, MADT_ITS = 0xB, 0xC, 0xE, 0xF
GICC_LENGTHS = (76, 80, 82)      # the ACPI 5.1/6.0, 6.3 and 6.5 revisions of the structure
GICD_LENGTH, GICR_LENGTH, ITS_LENGTH = 24, 16, 20
MCFG_FIXED_BYTES = 44            # header and 8 reserved bytes
MCFG_ALLOCATION_BYTES = 16
MCFG_MAX_ALLOCATIONS = 16        # bounds the overlap search; a descriptor holds at most 4
ECAM_BUS_SHIFT = 20              # 1 MiB of configuration space per bus
# One redistributor is two 64 KiB frames on GICv3 and four on GICv4. ACPI describes redistributors by discovery
# and carries no stride, so the stride comes from the GIC version, never from a default.
GICR_FRAME_STRIDE = {3: 0x20000, 4: 0x40000}
MPIDR_AFFINITY_BITS = 0xff00ffffff   # Aff3:Aff2:Aff1:Aff0 of MPIDR_EL1 (Arm ARM); bits [63:40] are RES0

Gicc = namedtuple('Gicc', 'uid cpu_interface enabled gicr_base mpidr')
Gicd = namedtuple('Gicd', 'base version')
Gicr = namedtuple('Gicr', 'base length')
Its = namedtuple('Its', 'its_id base')
EcamAllocation = namedtuple('EcamAllocation', 'segment base start_bus end_bus')


class BootContractError(ValueError):
    """A boot-contract input was refused. `code` is stable (tests and tools match on it); `reason` is for people."""

    def __init__(self, code, reason):
        if code not in BOOT_CONTRACT_CODES:
            raise AssertionError(f'unregistered boot-contract code {code!r}')
        super().__init__(f'{code}: {reason}')
        self.code = code
        self.reason = reason


def _refuse(code, reason):
    raise BootContractError(code, reason)


@dataclass(frozen=True)
class AcpiFacts:
    """What the dumped tables state, before any comparison with a descriptor."""
    xsdt_entries: int
    gicc: tuple        # Gicc in table order
    gicd: tuple        # Gicd in table order
    gicr: tuple        # Gicr in table order
    its: tuple         # Its in table order
    ecam: tuple        # EcamAllocation in table order
    notes: tuple


@dataclass(frozen=True)
class HamoaPlatform:
    """Facts the ACPI tables and the descriptor agree on."""
    gic_version: int
    gicd_base: int
    gicr_base: int
    gicr_stride: int
    gicr_size: int
    its_base: object           # int, or None when the board has no ITS
    mpidrs: tuple              # affinity bits only, sorted
    ecam: tuple                # (segment, base, start_bus, end_bus), sorted
    notes: tuple


def _table_bytes(tables, key):
    try:
        data = tables[key]
    except (KeyError, TypeError, IndexError):
        data = None
    if data is None:
        _refuse('table-missing', f'{key} table was not supplied')
    if not isinstance(data, (bytes, bytearray)):
        _refuse('table-structure', f'{key} must be bytes, not {type(data).__name__}')
    return bytes(data)


def _sdt(tables, key, signature):
    """A system description table after the checks every ACPI table shares.

    The length field must equal the bytes supplied: trailing bytes are refused rather than ignored, because a
    dump that carries more than the table says it holds was not produced by the tool this contract assumes.
    """
    data = _table_bytes(tables, key)
    if len(data) < ACPI_HEADER_BYTES:
        _refuse('table-length', f'{key} is {len(data)} bytes, shorter than the {ACPI_HEADER_BYTES}-byte ACPI header')
    if data[:4] != signature:
        _refuse('table-signature', f'{key} signature is {data[:4]!r}, expected {signature!r}')
    length = struct.unpack_from('<I', data, 4)[0]
    if not ACPI_HEADER_BYTES <= length <= ACPI_MAX_TABLE_BYTES:
        _refuse('table-length', f'{key} length field {length} is outside {ACPI_HEADER_BYTES}..{ACPI_MAX_TABLE_BYTES}')
    if length != len(data):
        _refuse('table-length', f'{key} length field says {length} bytes but {len(data)} were supplied')
    if sum(data) & 0xff:
        _refuse('table-checksum', f'{key} bytes do not sum to zero modulo 256')
    return data


def _rsdp_xsdt_address(tables):
    data = _table_bytes(tables, 'RSDP')
    if len(data) < RSDP_V1_BYTES:
        _refuse('table-length', f'RSDP is {len(data)} bytes, shorter than the {RSDP_V1_BYTES}-byte ACPI 1.0 structure')
    if data[:8] != b'RSD PTR ':
        _refuse('table-signature', f'RSDP signature is {data[:8]!r}')
    if sum(data[:RSDP_V1_BYTES]) & 0xff:
        _refuse('table-checksum', 'RSDP checksum over its first 20 bytes is not zero')
    if data[15] < 2:
        _refuse('table-structure', f'RSDP revision {data[15]} carries no XSDT address (ACPI 2.0 or later is required)')
    if len(data) < RSDP_V2_BYTES:
        _refuse('table-length', f'RSDP is {len(data)} bytes, shorter than the {RSDP_V2_BYTES}-byte ACPI 2.0 structure')
    length = struct.unpack_from('<I', data, 20)[0]
    if length != len(data) or not RSDP_V2_BYTES <= length <= ACPI_MAX_TABLE_BYTES:
        _refuse('table-length', f'RSDP length field {length} does not match the {len(data)} bytes supplied')
    if sum(data) & 0xff:
        _refuse('table-checksum', 'RSDP extended checksum over its length field is not zero')
    xsdt = struct.unpack_from('<Q', data, 24)[0]
    if not xsdt:
        _refuse('table-structure', 'RSDP carries a null XSDT address')
    return xsdt


def _xsdt_entry_count(tables):
    data = _sdt(tables, 'XSDT', b'XSDT')
    body = len(data) - ACPI_HEADER_BYTES
    if body % 8:
        _refuse('table-structure', f'XSDT entry area of {body} bytes is not a multiple of 8')
    entries = struct.unpack_from(f'<{body // 8}Q', data, ACPI_HEADER_BYTES)
    if len(entries) < 2:
        _refuse('table-structure', f'XSDT lists {len(entries)} tables; at least the MADT and MCFG must be reachable')
    if 0 in entries:
        _refuse('table-structure', 'XSDT has a null table address')
    if len(set(entries)) != len(entries):
        _refuse('table-structure', 'XSDT lists the same table address twice')
    return len(entries)


def _madt_entries(tables):
    data = _sdt(tables, 'APIC', b'APIC')
    if len(data) < MADT_FIXED_BYTES:
        _refuse('table-length', f'APIC (MADT) is {len(data)} bytes, shorter than its {MADT_FIXED_BYTES}-byte fixed part')
    gicc, gicd, gicr, its, skipped = [], [], [], [], {}
    offset = MADT_FIXED_BYTES
    walked = 0
    while offset < len(data):
        walked += 1
        if walked > MADT_MAX_ENTRIES:
            _refuse('table-structure', f'MADT has more than {MADT_MAX_ENTRIES} entries')
        if len(data) - offset < 2:
            _refuse('table-structure', f'MADT entry at offset {offset} has no room for its type and length')
        kind, size = data[offset], data[offset + 1]
        # A length below 2 never advances the walk; a length past the end is not a structure. Refuse both for
        # every type, known or not, so an entry this code does not interpret can still not stall it.
        if size < 2 or offset + size > len(data):
            _refuse('table-structure', f'MADT entry type {kind:#x} at offset {offset} has length {size}, which '
                    f'does not fit the {len(data) - offset} bytes left')
        entry = data[offset:offset + size]
        if kind == MADT_GICC:
            if size not in GICC_LENGTHS:
                _refuse('table-structure', f'MADT GICC entry has length {size}, expected one of {GICC_LENGTHS}')
            cpu_interface, uid, flags = struct.unpack_from('<III', entry, 4)
            gicr_base, mpidr = struct.unpack_from('<QQ', entry, 60)
            gicc.append(Gicc(uid, cpu_interface, bool(flags & 1), gicr_base, mpidr))
        elif kind == MADT_GICD:
            if size != GICD_LENGTH:
                _refuse('table-structure', f'MADT GICD entry has length {size}, expected {GICD_LENGTH}')
            gicd.append(Gicd(struct.unpack_from('<Q', entry, 8)[0], entry[20]))
        elif kind == MADT_GICR:
            if size != GICR_LENGTH:
                _refuse('table-structure', f'MADT GICR entry has length {size}, expected {GICR_LENGTH}')
            base, length = struct.unpack_from('<QI', entry, 4)
            if base + length > 1 << 64:
                _refuse('table-structure', f'MADT GICR range {base:#x}+{length:#x} wraps the 64-bit address space')
            gicr.append(Gicr(base, length))
        elif kind == MADT_ITS:
            if size != ITS_LENGTH:
                _refuse('table-structure', f'MADT ITS entry has length {size}, expected {ITS_LENGTH}')
            its.append(Its(struct.unpack_from('<I', entry, 4)[0], struct.unpack_from('<Q', entry, 8)[0]))
        else:
            skipped[kind] = skipped.get(kind, 0) + 1
        offset += size
    return gicc, gicd, gicr, its, skipped


def _mcfg_allocations(tables):
    data = _sdt(tables, 'MCFG', b'MCFG')
    if len(data) < MCFG_FIXED_BYTES:
        _refuse('table-length', f'MCFG is {len(data)} bytes, shorter than its {MCFG_FIXED_BYTES}-byte fixed part')
    body = len(data) - MCFG_FIXED_BYTES
    if not body or body % MCFG_ALLOCATION_BYTES:
        _refuse('table-structure', f'MCFG allocation area of {body} bytes is not a nonzero multiple of '
                f'{MCFG_ALLOCATION_BYTES}')
    if body // MCFG_ALLOCATION_BYTES > MCFG_MAX_ALLOCATIONS:
        _refuse('table-structure', f'MCFG has more than {MCFG_MAX_ALLOCATIONS} allocations')
    allocations = []
    for offset in range(MCFG_FIXED_BYTES, len(data), MCFG_ALLOCATION_BYTES):
        base, segment, start, end, _ = struct.unpack_from('<QHBBI', data, offset)
        if start > end:
            _refuse('table-structure', f'MCFG segment {segment} starts at bus {start}, after its end bus {end}')
        if base & 0xfff:
            _refuse('table-structure', f'MCFG base {base:#x} is not 4 KiB aligned')
        allocations.append(EcamAllocation(segment, base, start, end))
    # Whether `base` addresses bus 0 or the start bus differs between consumers, so the window is taken as
    # (end_bus + 1) MiB from base, the same conservative extent tools/board_descriptor.py uses for pcie.ecam.
    by_base = sorted(allocations, key=lambda a: a.base)
    for low, high in zip(by_base, by_base[1:]):
        if high.base < low.base + ((low.end_bus + 1) << ECAM_BUS_SHIFT):
            _refuse('table-structure', f'MCFG allocations at {low.base:#x} and {high.base:#x} overlap in memory')
    by_bus = sorted(allocations, key=lambda a: (a.segment, a.start_bus))
    for low, high in zip(by_bus, by_bus[1:]):
        if low.segment == high.segment and high.start_bus <= low.end_bus:
            _refuse('table-structure', f'MCFG segment {low.segment} lists overlapping bus ranges')
    return allocations


def parse_hamoa_tables(tables):
    """Structure-check the RSDP, XSDT, MADT ('APIC') and MCFG and return what they state, comparing nothing.

    `tables` maps 'RSDP', 'XSDT', 'APIC' and 'MCFG' to the raw bytes of each table; other keys are ignored.
    Raises BootContractError for a missing, truncated, oversized, mis-summed or malformed table.
    """
    _rsdp_xsdt_address(tables)
    xsdt_entries = _xsdt_entry_count(tables)
    gicc, gicd, gicr, its, skipped = _madt_entries(tables)
    ecam = _mcfg_allocations(tables)
    notes = ()
    if skipped:
        notes = ('MADT entry types not interpreted: '
                 + ', '.join(f'{kind:#04x} x{count}' for kind, count in sorted(skipped.items())),)
    return AcpiFacts(xsdt_entries, tuple(gicc), tuple(gicd), tuple(gicr), tuple(its), tuple(ecam), notes)


@dataclass(frozen=True)
class _Expectations:
    gic_version: int
    gicd_base: int
    gicr_base: int
    gicr_stride: int
    gicr_size: int
    its_base: object
    cpu_count: int
    affinity_mask: int
    mpidrs: frozenset
    ecam: frozenset


_BOARD_FIELDS = (('clusters.count', 'cluster_count'), ('clusters.cores_per_cluster', 'cores_per_cluster'),
                 ('clusters.mpidr', 'affinity_mask'), ('clusters.mpidrs', 'mpidrs'), ('gic.version', 'gic_version'),
                 ('gic.gicd', 'gicd'), ('gic.gicr', 'gicr'), ('gic.its', 'its'), ('pcie.ecam', 'ecam'))


def _descriptor_expectations(board):
    """The descriptor values the tables are held to, refusing a template or a field nobody has measured."""
    if not all(hasattr(board, attr) for attr in ('status', *(attr for _, attr in _BOARD_FIELDS))):
        _refuse('descriptor-unknown', 'the descriptor is not a validated Board (load it with tools/board_descriptor.py)')
    if board.status == 'UNIMPLEMENTED':
        _refuse('descriptor-unimplemented', 'board.status is UNIMPLEMENTED: a template, not a measured boot profile')
    unknown = [path for path, attr in _BOARD_FIELDS if getattr(board, attr) is None]
    if unknown:
        _refuse('descriptor-unknown', 'descriptor fields unknown (null), so the tables cannot be checked: '
                + ', '.join(unknown))
    version = board.gic_version
    if version not in GICR_FRAME_STRIDE:
        _refuse('descriptor-gic-version', f'gic.version {version!r} is not GICv3 or GICv4, so no redistributor '
                'stride can be derived')
    try:
        gicd_base, _ = board.gicd
        gicr_base, gicr_stride, gicr_size = board.gicr
        its_present, its_base, _ = board.its
        ecam = frozenset(tuple(entry) for entry in board.ecam)
        mpidrs = frozenset(board.mpidrs)
        cpu_count = board.cluster_count * board.cores_per_cluster
    except (TypeError, ValueError) as exc:
        _refuse('descriptor-inconsistent', f'descriptor fields are not in the validated shape ({exc})')
    stride = GICR_FRAME_STRIDE[version]
    if gicr_stride != stride:
        _refuse('descriptor-inconsistent', f'gic.gicr.stride {gicr_stride:#x} is not the {stride:#x}-byte GICv{version} '
                'redistributor; ACPI cannot express a padded or different stride, so it cannot be confirmed')
    if not gicr_size or gicr_size % stride:
        _refuse('descriptor-inconsistent', f'gic.gicr.size {gicr_size:#x} is not a nonzero multiple of {stride:#x}')
    if len(mpidrs) != cpu_count or gicr_size // stride < cpu_count:
        _refuse('descriptor-inconsistent', f'clusters.mpidrs ({len(mpidrs)} distinct) and gic.gicr '
                f'({gicr_size // stride} frames) do not both cover the {cpu_count} CPUs')
    return _Expectations(version, gicd_base, gicr_base, gicr_stride, gicr_size, its_base if its_present else None,
                         cpu_count, board.affinity_mask, mpidrs, ecam)


def _cross_check(want, facts):
    notes = list(facts.notes)

    if len(facts.gicd) != 1:
        _refuse('gicd-mismatch', f'MADT has {len(facts.gicd)} GIC Distributor structures; exactly one is required')
    gicd = facts.gicd[0]
    if gicd.base != want.gicd_base:
        _refuse('gicd-mismatch', f'MADT GICD base {gicd.base:#x} differs from descriptor gic.gicd.base '
                f'{want.gicd_base:#x}')
    if gicd.version == 0:
        notes.append(f'MADT GICD states no GIC version; the descriptor\'s GICv{want.gic_version} is not confirmed '
                     'by ACPI')
    elif gicd.version != want.gic_version:
        _refuse('version-mismatch', f'MADT GICD reports GICv{gicd.version}, the descriptor says '
                f'GICv{want.gic_version}')

    # ACPI makes a GICC entry with the Enabled flag clear unusable, so its other fields carry no information
    # (firmware often zeroes them). Only enabled entries are interpreted; disabled ones are neither checked
    # nor counted.
    enabled = [cpu for cpu in facts.gicc if cpu.enabled]
    uids = [cpu.uid for cpu in enabled]
    if len(set(uids)) != len(uids):
        _refuse('cpu-mismatch', 'MADT lists the same ACPI processor UID twice among enabled CPU interfaces')
    for cpu in enabled:
        if cpu.mpidr >> 40:
            _refuse('cpu-mismatch', f'MADT MPIDR {cpu.mpidr:#x} (UID {cpu.uid}) sets reserved bits above bit 39')
        if cpu.mpidr & MPIDR_AFFINITY_BITS & ~want.affinity_mask:
            _refuse('cpu-mismatch', f'MADT MPIDR {cpu.mpidr:#x} (UID {cpu.uid}) sets affinity bits outside '
                    f'the descriptor mask {want.affinity_mask:#x}')
    affinities = [cpu.mpidr & want.affinity_mask for cpu in enabled]
    if len(set(affinities)) != len(affinities):
        _refuse('cpu-mismatch', 'two enabled MADT GICC entries share the same MPIDR affinity')
    if len(enabled) != want.cpu_count:
        _refuse('cpu-mismatch', f'MADT enables {len(enabled)} CPU interfaces, the descriptor has {want.cpu_count} '
                '(clusters.count x clusters.cores_per_cluster)')
    seen = frozenset(affinities)
    if seen != want.mpidrs:
        _refuse('cpu-mismatch', f'MADT MPIDRs differ from descriptor clusters.mpidrs: missing '
                f'{sorted(want.mpidrs - seen)}, unexpected {sorted(seen - want.mpidrs)}')

    stride = want.gicr_stride
    ranges = sorted(facts.gicr)
    if not ranges:
        _refuse('gicr-mismatch', 'MADT has no GIC Redistributor structure, so descriptor gic.gicr cannot be confirmed')
    for base, length in ranges:
        if not length or length % stride:
            _refuse('gicr-mismatch', f'MADT GICR range {base:#x}+{length:#x} is not a nonzero multiple of the '
                    f'{stride:#x}-byte GICv{want.gic_version} redistributor stride')
    for (base, length), (next_base, _) in zip(ranges, ranges[1:]):
        if next_base < base + length:
            _refuse('gic-overlap', f'MADT GICR ranges at {base:#x} and {next_base:#x} overlap')
        if next_base != base + length:
            _refuse('gicr-mismatch', f'MADT GICR ranges leave a gap at {base + length:#x}; the descriptor '
                    'describes one contiguous range')
    first = ranges[0].base
    total = sum(length for _, length in ranges)
    if (first, total) != (want.gicr_base, want.gicr_size):
        _refuse('gicr-mismatch', f'MADT GICR range {first:#x}+{total:#x} differs from descriptor gic.gicr '
                f'{want.gicr_base:#x}+{want.gicr_size:#x}')
    frames = set()
    for cpu in enabled:
        if not cpu.gicr_base:
            continue
        offset = cpu.gicr_base - first
        if not 0 <= offset < total or offset % stride:
            _refuse('gicr-mismatch', f'GICC UID {cpu.uid} places its redistributor at {cpu.gicr_base:#x}, not on a '
                    f'{stride:#x}-byte frame inside the GICR range')
        if cpu.gicr_base in frames:
            _refuse('gic-overlap', f'two GICC entries share the redistributor at {cpu.gicr_base:#x}')
        frames.add(cpu.gicr_base)

    if want.its_base is None:
        if facts.its:
            _refuse('its-mismatch', f'MADT lists {len(facts.its)} ITS structures but the descriptor says the board '
                    'has none')
    elif len(facts.its) != 1:
        _refuse('its-mismatch', f'MADT lists {len(facts.its)} ITS structures; the descriptor describes exactly one')
    elif facts.its[0].base != want.its_base:
        _refuse('its-mismatch', f'MADT ITS base {facts.its[0].base:#x} differs from descriptor gic.its.base '
                f'{want.its_base:#x}')

    got = frozenset(tuple(allocation) for allocation in facts.ecam)
    if got != want.ecam:
        _refuse('ecam-mismatch', f'MCFG allocations differ from descriptor pcie.ecam: missing '
                f'{sorted(want.ecam - got)}, unexpected {sorted(got - want.ecam)}')

    return HamoaPlatform(want.gic_version, want.gicd_base, first, stride, total, want.its_base,
                         tuple(sorted(seen)), tuple(sorted(got)), tuple(notes))


def check_hamoa_platform(board, tables):
    """Hold the ACPI tables to a board descriptor; return the agreed facts or raise BootContractError.

    `board` is a validated tools/board_descriptor.py Board. The descriptor is checked first, so a template or an
    unmeasured field is refused before any table is read.
    """
    want = _descriptor_expectations(board)
    return _cross_check(want, parse_hamoa_tables(tables))


def _require_boot_ready(board):
    """The descriptor's whole boot gate (MIDR, SMMUs, devices, ...), beyond the fields ACPI can confirm."""
    tools = str(Path(__file__).resolve().parents[1] / 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import board_descriptor
    try:
        board_descriptor.require_boot_ready(board)
    except board_descriptor.BoardNotBootReady as exc:
        raise BootContractError('descriptor-not-boot-ready', str(exc)) from exc


def describe_acpi_facts(facts):
    """Lines for people. Table OEM fields are not parsed, so nothing identifying can leak into the output."""
    enabled = [cpu for cpu in facts.gicc if cpu.enabled]
    lines = [f'xsdt entries      : {facts.xsdt_entries}']
    for gicd in facts.gicd:
        lines.append(f'gic distributor   : base {gicd.base:#x}, version {gicd.version or "not stated"}')
    for gicr in facts.gicr:
        lines.append(f'gic redistributor : base {gicr.base:#x}, length {gicr.length:#x}')
    for its in facts.its:
        lines.append(f'gic its           : base {its.base:#x}')
    lines.append(f'cpu interfaces    : {len(enabled)} enabled of {len(facts.gicc)} listed')
    lines.append('mpidr affinities  : ' + ', '.join(f'{cpu.mpidr & MPIDR_AFFINITY_BITS:#x}' for cpu in enabled))
    for ecam in facts.ecam:
        lines.append(f'ecam              : segment {ecam.segment}, base {ecam.base:#x}, '
                     f'buses {ecam.start_bus}..{ecam.end_bus}')
    lines.extend(f'note              : {note}' for note in facts.notes)
    return lines
