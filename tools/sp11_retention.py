# SPDX-License-Identifier: MIT
"""The format of the retention probe M10 on the host (docs/SP11-RETENTION-PROBE.md; the payload is payloads/retprobe.c in the q1n1 fork,
docs/SP11-PAYLOAD-RETPROBE.md there): the 4 KiB canary page, the 64-byte configuration file the payload reads, the rule that decides whether
a page read back is WHOLE, PARTIAL or NONE, and the checks of candidate pages against a memory map. No I/O but the file readers named
below, no hardware. Evidence: EXPERIMENTAL; hardware NOT RUN.

The page and configuration layouts and the classification rule are written out here and not imported from the q1n1 fork: the payload's own
reader (C, at EL2), the Shell's dump read by tools/sp11_retention_check.py (this module) and the independent oracle of the payload's tests
are three readers of one format, and tools/test-sp11-retention.py checks this one against the payload's reader on a corpus of pages (SKIP
without a q1n1 tree: Q1N1_SP11_TREE). The words of a page, little endian:

  w0 w1 magic "RETCAN01"; w2 layout version 1; w3 page index 0 to 2; w4 w5 physical address; w6 boot counter 1 to 0xFFFFF; w7 the previous
  counter; w8 w9 CNTPCT_EL0; w10 CNTFRQ_EL0; w11 w12 raw PSCI_FEATURES answers (SYSTEM_RESET, SYSTEM_RESET2); w13 the bytes path requested,
  path used, previous state of pages 0 and 1; w14 the previous state of page 2, the flush method, two zero bytes; w15 the type of the
  memory-map descriptor that holds the page, w16 w17 its start, w18 w19 its size; w20 to w30 zero; w31 CRC-32 of the page with w31 as zero.
  Body word i (0 to 991, words 32 to 1023) is counter << 12 | page index << 10 | i."""
from collections import Counter
from dataclasses import dataclass
import re
import struct
import zlib

PAGE_BYTES = 4096
PAGE_WORDS = PAGE_BYTES // 4
HEADER_WORDS = 32
BODY_WORDS = PAGE_WORDS - HEADER_WORDS
CRC_WORD = 31
PAGE_COUNT = 3
PAGE_MAGIC = b'RETCAN01'
CONFIG_MAGIC = b'RETCFG01'
LAYOUT_VERSION = 1
COUNTER_MAX = 0xFFFFF
MIN_BODY_MATCHES = 16           # words of one epoch that make a page with a lost header PARTIAL
CONFIG_WORDS = 16
CONFIG_BYTES = CONFIG_WORDS * 4
COUNTDOWN_MIN, COUNTDOWN_MAX = 1, 60
ADDRESS_LIMIT = 1 << 48
FLUSH_DC_CVAC = 1
CONVENTIONAL_TYPE = 7
PATHS = {'none': 0, 'psci-reset': 1, 'psci-reset2': 2}
PATH_LABELS = ('NONE', 'PSCI_RESET', 'PSCI_RESET2')              # the payload's names for the three paths
STATES = ('NONE', 'PARTIAL', 'WHOLE', 'UNREADABLE')              # the payload's numbering, as header words 13 and 14 carry it
# Three pages the design chooses for the unit from the memory maps of its six runs (docs/SP11-RETENTION-PROBE.md section 3): the middle of the
# big low extent, the top of the highest extent below 4 GiB (the rule of docs/SP11-HOST-LINK-STUDY.md section 8.2) and the top of RAM, above
# where the wrapper places the guest. Each must still be conventional in the map of the boot it is used in: the payload checks that itself.
UNIT_PAGES = (0x9C000000, 0xF8400000, 0xFFFF00000)
_MAGIC_LO, _MAGIC_HI = struct.unpack('<2I', PAGE_MAGIC)
_CONFIG_MAGIC_LO, _CONFIG_MAGIC_HI = struct.unpack('<2I', CONFIG_MAGIC)


class ConfigError(Exception):
    """A configuration the payload would refuse; `reason` is the payload's word for it."""

    def __init__(self, reason: str, detail: str = ''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail


def page_crc(page: bytes) -> int:
    """The CRC-32 (IEEE, as zlib) of a page with the CRC word taken as zero."""
    return zlib.crc32(page[:CRC_WORD * 4] + bytes(4) + page[CRC_WORD * 4 + 4:])


def body_word(counter: int, index: int, i: int) -> int:
    return (counter << 12 | index << 10 | i) & 0xFFFFFFFF


def build_page(index: int, address: int, counter: int, previous_counter: int = 0, timestamp: int = 0, frequency: int = 19200000,
               feature_reset: int = 0, feature_reset2: int = 0xFFFFFFFF, path_requested: int = 0, path_used: int = 0,
               previous_states: tuple = (0, 0, 0), extent: tuple = (CONVENTIONAL_TYPE, 0, 0)) -> bytes:
    """The page the payload writes for these values, byte for byte. `extent` is (type, start, size) of the descriptor holding the page."""
    if not (0 <= index < PAGE_COUNT and 1 <= counter <= COUNTER_MAX and 0 <= previous_counter <= COUNTER_MAX):
        raise ValueError('index, counter or previous counter out of range')
    words = [0] * PAGE_WORDS
    words[0:2] = [_MAGIC_LO, _MAGIC_HI]
    words[2:6] = [LAYOUT_VERSION, index, address & 0xFFFFFFFF, address >> 32]
    words[6:12] = [counter, previous_counter, timestamp & 0xFFFFFFFF, timestamp >> 32 & 0xFFFFFFFF, frequency & 0xFFFFFFFF,
                   feature_reset & 0xFFFFFFFF]
    words[12] = feature_reset2 & 0xFFFFFFFF
    words[13] = path_requested | path_used << 8 | previous_states[0] << 16 | previous_states[1] << 24
    words[14] = previous_states[2] | FLUSH_DC_CVAC << 8
    words[15:20] = [extent[0], extent[1] & 0xFFFFFFFF, extent[1] >> 32, extent[2] & 0xFFFFFFFF, extent[2] >> 32]
    words[HEADER_WORDS:] = [body_word(counter, index, i) for i in range(BODY_WORDS)]
    page = bytearray(struct.pack(f'<{PAGE_WORDS}I', *words))
    page[CRC_WORD * 4:CRC_WORD * 4 + 4] = struct.pack('<I', page_crc(bytes(page)))
    return bytes(page)


def decode_header(page: bytes) -> dict:
    """The fields of a page's header as the layout names them. The caller decides whether the header is to be believed."""
    w = struct.unpack_from(f'<{HEADER_WORDS}I', page)
    return {'magic': page[:8], 'version': w[2], 'index': w[3], 'address': w[5] << 32 | w[4], 'counter': w[6], 'previous_counter': w[7],
            'timestamp': w[9] << 32 | w[8], 'frequency': w[10], 'feature_reset': w[11], 'feature_reset2': w[12],
            'path_requested': w[13] & 0xFF, 'path_used': w[13] >> 8 & 0xFF, 'previous_states': (w[13] >> 16 & 0xFF, w[13] >> 24 & 0xFF, w[14] & 0xFF),
            'flush': w[14] >> 8 & 0xFF, 'extent_type': w[15], 'extent_start': w[17] << 32 | w[16], 'extent_bytes': w[19] << 32 | w[18],
            'crc': w[CRC_WORD]}


@dataclass(frozen=True)
class PageVerdict:
    state: str              # WHOLE, PARTIAL or NONE
    named: bool             # the header names this page: magic, version, index, address and a counter in range
    counter: int            # the header's boot counter when the page is named, else 0
    crc_ok: bool
    wrong_words: int        # named: body words that are not the pattern; header lost: body words that are not of the epoch that most agree on
    intact: tuple           # (start, end) byte ranges of the body words that are as written; empty for NONE
    damaged: tuple          # (start, end) byte ranges of the body words that are not; empty for NONE
    epoch: tuple            # header lost and PARTIAL: (counter, index) of the epoch the intact words come from, else ()
    header: dict            # the decoded header of a named page, else {}


def _ranges(flags: list) -> tuple:
    """(start, end) byte ranges of the body words whose flag is true, adjacent words joined."""
    found, start = [], None
    for i, flag in enumerate(flags + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            found.append((HEADER_WORDS * 4 + start * 4, HEADER_WORDS * 4 + i * 4))
            start = None
    return tuple(found)


def classify_page(page: bytes, index: int, address: int) -> PageVerdict:
    """WHOLE, PARTIAL or NONE for the 4096 bytes read back from page `index` at `address`, by the rule the payload's reader uses. WHOLE: the
    header names this page, the CRC holds and no body word is wrong. PARTIAL: the header names the page and something is wrong, or the header
    does not name it and at least MIN_BODY_MATCHES body words are of one epoch (the bits above ten of a word whose low ten bits equal its
    own index; the epoch most words agree on, ties to the smallest). NONE: anything else, an all-zero page, an all-ones page and noise included."""
    if len(page) != PAGE_BYTES:
        raise ValueError(f'{len(page)} bytes, not {PAGE_BYTES}')
    words = struct.unpack(f'<{PAGE_WORDS}I', page)
    body = words[HEADER_WORDS:]
    counter = words[6]
    crc_ok = page_crc(page) == words[CRC_WORD]
    if (words[0], words[1], words[2], words[3]) == (_MAGIC_LO, _MAGIC_HI, LAYOUT_VERSION, index) and words[4] == address & 0xFFFFFFFF \
            and words[5] == address >> 32 and 1 <= counter <= COUNTER_MAX:
        expected = [body_word(counter, index, i) for i in range(BODY_WORDS)]
        wrong = [body[i] != expected[i] for i in range(BODY_WORDS)]
        return PageVerdict('WHOLE' if crc_ok and not any(wrong) else 'PARTIAL', True, counter, crc_ok, sum(wrong),
                           _ranges([not flag for flag in wrong]), _ranges(wrong), (), decode_header(page))
    votes = Counter(w >> 10 for i, w in enumerate(body) if w & 0x3FF == i)
    epoch = min((key for key, count in votes.items() if count == max(votes.values())), default=0)
    matches = [body[i] == (epoch << 10 | i) & 0xFFFFFFFF for i in range(BODY_WORDS)]
    if sum(matches) < MIN_BODY_MATCHES:
        return PageVerdict('NONE', False, 0, crc_ok, BODY_WORDS - sum(matches), (), (), (), {})
    return PageVerdict('PARTIAL', False, 0, crc_ok, BODY_WORDS - sum(matches), _ranges(matches), _ranges([not flag for flag in matches]),
                       (epoch >> 2, epoch & 3), {})


def next_boot_counter(verdicts) -> int:
    """The boot counter the payload takes after reading these three verdicts: the highest WHOLE counter plus one, or 1."""
    return max([v.counter for v in verdicts if v.state == 'WHOLE'], default=0) + 1


# ---- the configuration file ----------------------------------------------------------------------------------------------------

def config_bytes(path: int, countdown: int, pages) -> bytes:
    """The 64-byte file the payload's manifest lists after the payload: magic, version, reset path, countdown, page count, three
    addresses, three zero words and the CRC-32 of the first fifteen words. It is checked here the way the payload checks it."""
    pages = tuple(pages)
    body = CONFIG_MAGIC + struct.pack('<4I', LAYOUT_VERSION, path, countdown, len(pages)) + b''.join(struct.pack('<Q', p) for p in pages) + bytes(12)
    blob = body + struct.pack('<I', zlib.crc32(body))
    parse_config(blob)
    return blob


def parse_config(blob: bytes) -> dict:
    """{'path', 'countdown', 'pages'} of a configuration file, or ConfigError. The reasons after BAD_MAGIC are the payload's own, in its
    order; BAD_SIZE and BAD_MAGIC name files the payload would not take for its configuration at all (it would run PHASE 1 only)."""
    if len(blob) != CONFIG_BYTES:
        raise ConfigError('BAD_SIZE', f'{len(blob)} bytes, not {CONFIG_BYTES}')
    w = struct.unpack(f'<{CONFIG_WORDS}I', blob)
    if (w[0], w[1]) != (_CONFIG_MAGIC_LO, _CONFIG_MAGIC_HI):
        raise ConfigError('BAD_MAGIC')
    if zlib.crc32(blob[:-4]) != w[15]:
        raise ConfigError('BAD_CRC')
    if w[2] != LAYOUT_VERSION:
        raise ConfigError('BAD_VERSION', str(w[2]))
    if w[3] >= len(PATHS):
        raise ConfigError('BAD_PATH', str(w[3]))
    if not COUNTDOWN_MIN <= w[4] <= COUNTDOWN_MAX:
        raise ConfigError('BAD_COUNTDOWN', str(w[4]))
    if w[5] != PAGE_COUNT:
        raise ConfigError('BAD_PAGE_COUNT', str(w[5]))
    if w[12] | w[13] | w[14]:
        raise ConfigError('RESERVED_NOT_ZERO')
    pages = [w[7 + 2 * k] << 32 | w[6 + 2 * k] for k in range(PAGE_COUNT)]
    for page in pages:
        if page % PAGE_BYTES:
            raise ConfigError('PAGE_UNALIGNED', f'0x{page:X}')
        if not page or page > ADDRESS_LIMIT - PAGE_BYTES:
            raise ConfigError('PAGE_RANGE', f'0x{page:X}')
    if len(set(pages)) != PAGE_COUNT:
        raise ConfigError('PAGES_NOT_DISTINCT')
    return {'path': w[3], 'countdown': w[4], 'pages': tuple(pages)}


# ---- pages against a memory map --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Extent:
    kind: str       # the type as the map's source names it: a UEFI type number as two hex digits, or the Shell's name
    start: int
    end: int        # exclusive
    conventional: bool


class MapError(Exception):
    """A memory map this module will not read: a line that is neither a comment nor a descriptor row, or a row count that the header
    contradicts. A map read with rows missing would hide the extents that firmware owns."""


_PROBE_COUNT = re.compile(r'# descriptor_size=[0-9]+ descriptor_version=[0-9]+ count=([0-9]+) map_size=[0-9]+')
_PROBE_FIELDS = (r'[0-9A-Fa-f]{4}', r'[0-9A-Fa-f]{2}', r'[0-9A-Fa-f]{1,16}', r'[0-9A-Fa-f]{1,16}', r'[0-9A-Fa-f]{1,16}', r'[0-9A-Fa-f]{1,16}')


def parse_probe_memmap(text: str) -> list:
    """The extents of a map in the format of the SP11 memory-map probe (`idx type phys virt pages attr name`, hexadecimal; the files
    under downloads/sp11/x*-run*/memmap.txt): type 07 is conventional memory. Every descriptor row is read, also those whose name has a
    space (`BS DATA`, `RT DATA`, `LDR CODE`, `ACPI RECLAIM`): the real maps have about a hundred of them and they are the extents that
    firmware owns. A line that is not a row, a row with a field that is not hexadecimal or with no pages, or a count that differs from the
    header's `count=` is a MapError."""
    extents, declared = [], None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if line.startswith('#'):
            header = _PROBE_COUNT.fullmatch(line.rstrip())
            declared = int(header.group(1)) if header else declared
            continue
        fields = line.split(None, 6)
        if len(fields) < 7 or not all(re.fullmatch(pattern, field) for pattern, field in zip(_PROBE_FIELDS, fields)) or not int(fields[4], 16):
            raise MapError(f'line {number} is not a descriptor row: {line[:60]!r}')
        start, pages = int(fields[2], 16), int(fields[4], 16)
        extents.append(Extent(fields[1], start, start + pages * PAGE_BYTES, int(fields[1], 16) == CONVENTIONAL_TYPE))
    if declared is not None and declared != len(extents):
        raise MapError(f'the header says {declared} descriptors and the file has {len(extents)} rows')
    return extents


def page_problems(pages, extents) -> list:
    """Why a page cannot be used, one sentence per page: it must lie wholly inside one conventional extent and overlap no extent of
    another type. [] when all three are fine. This is the host's check; the payload repeats it against the map of the boot it runs in."""
    problems = []
    for page in pages:
        hits = [e for e in extents if e.start < page + PAGE_BYTES and page < e.end]
        if not hits:
            problems.append(f'0x{page:X} is in no extent of the map')
        elif any(not e.conventional for e in hits):
            problems.append(f'0x{page:X} overlaps an extent of type {next(e.kind for e in hits if not e.conventional)}')
        elif len(hits) != 1 or not (hits[0].start <= page and page + PAGE_BYTES <= hits[0].end):
            problems.append(f'0x{page:X} straddles extents')
    return problems
