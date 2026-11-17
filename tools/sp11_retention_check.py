# SPDX-License-Identifier: MIT
"""The host verdict tool of the retention probe M10 (docs/SP11-RETENTION-PROBE.md): reads the result file SP11\\RET<n>.TXT that the
extract step of the autorun plan `retention-m10` writes on the stick, decodes the three pages, and says, per page, WHOLE (magic, CRC and
body intact), PARTIAL (which bytes) or NONE, the reset path and the boot counter step, and what the memory map of the extraction boot says
about each page. It refuses a damaged or truncated file with a named reason and judges nothing from it. It only reads.

    python3 tools/sp11_retention_check.py check FILE [--config RETPATH.DAT] [--pages A B C] [--json]
    python3 tools/sp11_retention_check.py series FILE... [--config RETPATH.DAT]
    python3 tools/sp11_retention_check.py stick DIR          # SP11/RET*.TXT and SP11/RETPATH.DAT of a staged tree or a stick, read only

The file is the Shell's own text (`dmem ... >a`, `memmap >>a`, `date >>a`, `time >>a`, then one `echo` line, docs/SP11-RETENTION-PROBE.md
section 5):

  Memory Address 0000000<16 hex digits of the page> 1000 Bytes, then 256 lines  "  <address>: XX XX XX XX XX XX XX XX-XX ... XX  *<16 characters>*"
  (the address on a line is the low 32 bits of the line's address: the pinned Shell prints no more, OBSERVED under QEMU for a page above
  4 GiB, while the heading carries all 64 bits), three such blocks in the order of the configuration's pages, the output of `memmap` (a table of
  "Type Start-End Pages Attributes" and its totals), the Shell's date and time ("11/17/2026", "01:20:58 (LOCAL)": the unit's clock at the
  extraction), and the last line  RETENTION-EXTRACT-END plan=<name> step=<n> pages=3.  A file without that last line is a truncated one.

`series` counts trials, not files: a file that is byte for byte one it has already read, or that carries the boot counter and the timestamp of
one it has read, is refused (one dump counted ten times is not ten trials), and so is a file with no memory map, because a series cannot tell
without it whether firmware owned a page. `stick` also refuses a result or a step-1 marker that is not later than the staging time that
SP11/PLAN.TXT records (it was left by an earlier run), and prints the times it compared; a result with no date and time is judged by `check`, which
says that nothing tells when it was extracted, and refused by `stick` (NO_CLOCK), which cannot tell it from a stale one.

A verdict is a statement about the bytes in the file and not about the cause: NONE on a page that the memory map of the extraction boot shows
as owned by firmware is not evidence against retention, and NONE on every page does not say that the probe wrote anything (a probe that refused
leaves no trace). Evidence: EXPERIMENTAL; hardware NOT RUN: no real result file exists."""
import argparse
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_retention as retention  # noqa: E402

END_PREFIX = 'RETENTION-EXTRACT-END'
BLOCK_LINES = retention.PAGE_BYTES // 16
SERIES_PASS_TRIALS = 10         # docs/SP11-HOST-LINK-STUDY.md 8.3: a path passes with a page whole in ten consecutive trials, the map unchanged
FILE_TIME_SLACK_SECONDS = 10    # the Shell writes the clock lines a moment before the end line; FAT times have 2 s resolution
_BLOCK_HEAD = re.compile(r'Memory Address ([0-9A-F]{16}) ([0-9A-F]+) Bytes')
_DATA_LINE = re.compile(r'  ([0-9A-F]{1,16}): ((?:[0-9A-F]{2} ){7}[0-9A-F]{2}-(?:[0-9A-F]{2} ){7}[0-9A-F]{2})  \*(.{16})\*')
_END_LINE = re.compile(rf'{END_PREFIX} plan=([A-Za-z0-9][A-Za-z0-9._-]{{0,23}}) step=([1-9]) pages=([0-9]+)')
_MAP_HEAD = re.compile(r'Type +Start +End +# Pages +Attributes')
_MAP_ROW = re.compile(r'(\S+) +([0-9A-F]{16})-([0-9A-F]{16}) +([0-9A-F]{16}) +([0-9A-F]{16})')
_MAP_TOTAL = re.compile(r'(\s*|\s*-+\s*|\s*[A-Za-z_ ]+: +[0-9,]+ (?:Pages|MB) \([0-9,]+ Bytes\))')
_DATE_LINE = re.compile(r'([0-9]{2})/([0-9]{2})/([0-9]{4})')                          # the Shell's `date`: MM/DD/YYYY (OBSERVED under QEMU, pinned Shell)
_TIME_LINE = re.compile(r'([0-9]{2}):([0-9]{2}):([0-9]{2}) \(([ -~]{1,40})\)')       # its `time`: 01:20:58 (LOCAL), the zone as the firmware gives it
_STAGED_LINE = re.compile(r'^staged: ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})\r?$', re.MULTILINE)
_SHOWN = '%Y-%m-%d %H:%M:%S'
FREE_NAMES = ('Available',)         # the Shell's name for conventional memory
PSCI_ERRORS = ('NOT_SUPPORTED', 'INVALID_PARAMETERS', 'DENIED', 'ALREADY_ON', 'ON_PENDING', 'INTERNAL_FAILURE', 'NOT_PRESENT', 'DISABLED', 'INVALID_ADDRESS')


def psci_answer(raw: int) -> str:
    """What a raw 32-bit PSCI_FEATURES answer says: non-negative as a signed value is implemented, a small negative one is an error code."""
    value = raw & 0xFFFFFFFF
    if value < 0x80000000:
        return 'implemented'
    code = (1 << 32) - value
    return f'{PSCI_ERRORS[code - 1]}(-{code})' if 1 <= code <= len(PSCI_ERRORS) else 'negative'


class DumpError(Exception):
    """A result file this tool will not judge; `reason` is the name, `detail` says where."""

    def __init__(self, reason: str, detail: str = ''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Dump:
    plan: str
    step: int
    pages: tuple            # ((address, 4096 bytes), ...) in the order of the file
    memmap: tuple           # retention.Extent entries of the extraction boot's map; empty when the file has none
    clock: datetime = None  # the unit's date and time at the extraction, as the Shell printed them (no zone: the unit's own clock)
    clock_text: str = ''    # those two lines, as printed
    digest: str = ''        # SHA-256 of the file's bytes


@dataclass(frozen=True)
class PageReport:
    index: int
    address: int
    verdict: retention.PageVerdict
    map_note: str           # what the extraction boot's memory map says about the page
    map_free: object        # True free there, False owned there, None no map in the file or the page is in no extent


@dataclass(frozen=True)
class Report:
    plan: str
    step: int
    path_requested: object  # a retention.PATH_LABELS entry from the configuration, or None
    path_used: object       # the same, from the pages' headers, or None when no page names itself
    pages: tuple
    counter: int            # this boot's counter as the whole pages say it, 0 when none is whole
    previous_counter: int
    verdict: str            # WHOLE, PARTIAL or NONE: the best state of the three pages
    path_conflict: bool = False     # the named pages do not all record the same reset path: some of them are damaged
    counter_conflict: bool = False  # the whole pages carry different boot counters: pages of different writes sit side by side
    has_map: bool = True            # the file has the extraction boot's memory map
    stamps: tuple = ()              # (boot counter, timestamp) of each whole page: what names one write of the probe
    digest: str = ''                # SHA-256 of the result file's bytes
    clock: datetime = None          # when the unit's Shell extracted, by the unit's clock
    clock_text: str = ''
    source: str = ''                # the path the file was read from, for messages

    @property
    def path(self) -> str:
        return self.path_used or self.path_requested or 'UNKNOWN'


def parse_dump(blob: bytes, expected_pages=None, step=None) -> Dump:
    """The Dump of a result file, or DumpError. `expected_pages` (the configuration's addresses, in order) and `step` (the n of RET<n>.TXT)
    are compared with the file when given."""
    if not blob:
        raise DumpError('EMPTY_FILE')
    if blob[:2] in (b'\xff\xfe', b'\xfe\xff'):
        raise DumpError('UCS2_OUTPUT', 'the redirect was `>` and not `>a`: the Shell wrote UCS-2')
    if b'\0' in blob or any(byte > 0x7E or (byte < 0x20 and byte not in b'\r\n') for byte in blob):
        raise DumpError('NOT_ASCII', 'a byte outside printable ASCII, CR and LF')
    lines = blob.decode('ascii').replace('\r\n', '\n').split('\n')
    while lines and not lines[-1].strip():
        lines.pop()
    end = _END_LINE.fullmatch(lines[-1]) if lines else None
    if end is None:
        if lines and lines[-1].startswith(END_PREFIX):
            raise DumpError('END_LINE_MALFORMED', lines[-1][:80])
        raise DumpError('NO_END_LINE', 'the file does not end with the line the extract step writes last: it is truncated or not a result file')
    plan, n, count = end.group(1), int(end.group(2)), int(end.group(3))
    if count != retention.PAGE_COUNT:
        raise DumpError('END_LINE_PAGE_COUNT', f'{count}, not {retention.PAGE_COUNT}')
    if step is not None and n != step:
        raise DumpError('END_LINE_STEP_MISMATCH', f'the file is RET{step}.TXT and its end line says step {n}')
    pages, memmap, clock, i, body = [], [], None, 0, lines[:-1]
    while i < len(body):
        head = _BLOCK_HEAD.fullmatch(body[i])
        if head is not None:
            address, size = int(head.group(1), 16), int(head.group(2), 16)
            if size != retention.PAGE_BYTES:
                raise DumpError('BLOCK_SIZE', f'page at 0x{address:X} is {size:#x} bytes, not {retention.PAGE_BYTES:#x}')
            if address in [a for a, _ in pages]:
                raise DumpError('BLOCK_ADDRESS_DUPLICATE', f'0x{address:X}')
            data, i = bytearray(), i + 1
            for k in range(BLOCK_LINES):
                if i >= len(body) or _BLOCK_HEAD.fullmatch(body[i]) or _MAP_HEAD.fullmatch(body[i]):
                    raise DumpError('BLOCK_SHORT', f'page 0x{address:X}: {k} of {BLOCK_LINES} lines')
                data += _data_line(body[i], address + 16 * k, address)
                i += 1
            if i < len(body) and _DATA_LINE.fullmatch(body[i]):
                raise DumpError('BLOCK_LONG', f'page 0x{address:X} has more than {BLOCK_LINES} lines')
            pages.append((address, bytes(data)))
        elif _MAP_HEAD.fullmatch(body[i]):
            memmap, i = _memory_map(body, i + 1)
        elif clock is None and i + 1 < len(body) and _DATE_LINE.fullmatch(body[i]) and _TIME_LINE.fullmatch(body[i + 1]):
            clock, i = _clock(body[i], body[i + 1]), i + 2
        else:
            raise DumpError('UNEXPECTED_LINE', f'line {i + 1}: {body[i][:60]!r}')
    if len(pages) != retention.PAGE_COUNT:
        raise DumpError('BLOCK_COUNT', f'{len(pages)} dumped pages, not {retention.PAGE_COUNT}')
    if expected_pages is not None and [a for a, _ in pages] != list(expected_pages):
        raise DumpError('PAGES_DIFFER_FROM_CONFIG', f'the file has {[hex(a) for a, _ in pages]}, the configuration {[hex(a) for a in expected_pages]}')
    return Dump(plan, n, tuple(pages), tuple(memmap), clock[0] if clock else None, clock[1] if clock else '', hashlib.sha256(blob).hexdigest())


def _clock(date_line: str, time_line: str):
    """(datetime, the two lines) of the Shell's `date` and `time`; the zone text after the time is kept as printed and not interpreted."""
    month, day, year = map(int, _DATE_LINE.fullmatch(date_line).groups())
    hour, minute, second = map(int, _TIME_LINE.fullmatch(time_line).groups()[:3])
    try:
        return datetime(year, month, day, hour, minute, second), f'{date_line} {time_line}'
    except ValueError as error:
        raise DumpError('NO_CLOCK', f'{date_line} {time_line}: {error}') from error


def _data_line(line: str, want: int, page: int) -> bytes:
    found = _DATA_LINE.fullmatch(line)
    if found is None:
        raise DumpError('LINE_MALFORMED', f'page 0x{page:X} at 0x{want:X}: {line[:70]!r}')
    shown = int(found.group(1), 16)
    if shown not in (want, want & 0xFFFFFFFF):       # the pinned Shell prints the low 32 bits of the line address (OBSERVED under QEMU)
        raise DumpError('LINE_ADDRESS', f'page 0x{page:X}: the line says 0x{shown:X}, 0x{want:X} follows the line before')
    raw = bytes(int(token, 16) for token in re.findall(r'[0-9A-F]{2}', found.group(2)))
    if found.group(3) != ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in raw):
        raise DumpError('ASCII_COLUMN', f'page 0x{page:X} at 0x{want:X}: the text column is not what the bytes print as')
    return raw


def _memory_map(body: list, i: int):
    extents = []
    while i < len(body) and (row := _MAP_ROW.fullmatch(body[i])):
        start, end = int(row.group(2), 16), int(row.group(3), 16) + 1
        if end <= start or int(row.group(4), 16) * retention.PAGE_BYTES != end - start:
            raise DumpError('MEMMAP_MALFORMED', f'{body[i][:70]!r}: the page count disagrees with the addresses')
        extents.append(retention.Extent(row.group(1), start, end, row.group(1) in FREE_NAMES))
        i += 1
    while i < len(body) and _MAP_TOTAL.fullmatch(body[i]):
        i += 1
    if not extents:
        raise DumpError('MEMMAP_MALFORMED', 'a "Type Start End" heading and no rows')
    return extents, i


def judge(dump: Dump, config: bytes = None) -> Report:
    """The Report of a Dump: each page classified by the payload's rule (retention.classify_page), what the extraction boot's memory map says
    of it, the reset path (the configuration's request and what the pages record) and the boot counter."""
    requested = None
    if config is not None:
        try:
            parsed = retention.parse_config(config)
        except retention.ConfigError as error:
            raise DumpError('CONFIG_REFUSED', str(error)) from error
        if list(parsed['pages']) != [a for a, _ in dump.pages]:
            raise DumpError('PAGES_DIFFER_FROM_CONFIG', 'the configuration names other pages than the file dumps')
        requested = retention.PATH_LABELS[parsed['path']]
    reports = []
    for index, (address, page) in enumerate(dump.pages):
        verdict = retention.classify_page(page, index, address)
        hit = [e for e in dump.memmap if e.start <= address < e.end]
        if not dump.memmap:
            note, free = 'no memory map in the file', None
        elif not hit:
            note, free = 'in no extent of the extraction boot\'s map', None
        elif hit[0].conventional:
            note, free = f'free in the extraction boot ({hit[0].kind})', True
        else:
            note, free = f'owned by firmware in the extraction boot ({hit[0].kind}): damage or loss here is no evidence against retention', False
        reports.append(PageReport(index, address, verdict, note, free))
    whole = [r.verdict for r in reports if r.verdict.state == 'WHOLE']
    used = {r.verdict.header['path_used'] for r in reports if r.verdict.named and r.verdict.header['path_used'] < len(retention.PATH_LABELS)}
    path_used = retention.PATH_LABELS[next(iter(used))] if len(used) == 1 else None
    best = 'WHOLE' if whole else 'PARTIAL' if any(r.verdict.state == 'PARTIAL' for r in reports) else 'NONE'
    counter = max((v.counter for v in whole), default=0)
    previous = next((v.header['previous_counter'] for v in whole if v.counter == counter), 0)
    return Report(dump.plan, dump.step, requested, path_used, tuple(reports), counter, previous, best, len(used) > 1,
                  len({v.counter for v in whole}) > 1, bool(dump.memmap), tuple(sorted({(v.counter, v.header['timestamp']) for v in whole})),
                  dump.digest, dump.clock, dump.clock_text)


def _spans(ranges: tuple) -> str:
    shown = ', '.join(f'0x{a:X}-0x{b:X}' for a, b in ranges[:6])
    return shown + (f' and {len(ranges) - 6} more' if len(ranges) > 6 else '')


def format_report(report: Report, name: str = '') -> str:
    lines = [' '.join(filter(None, ['SP11 RETENTION RESULT', name, f'plan={report.plan}', f'step={report.step}']))]
    if report.clock is not None:
        lines.append(f'extracted: {report.clock_text}, the unit\'s clock as its Shell printed it')
    else:
        lines.append('WARNING: the file has no date and time, so nothing says when it was extracted (a stick refuses it: NO_CLOCK)')
    notes = [f'requested by the configuration: {report.path_requested}' if report.path_requested else None,
             f'recorded by the pages: {report.path_used}' if report.path_used else 'no page records the path']
    lines.append(f'reset path: {report.path} ({"; ".join(n for n in notes if n)})')
    if report.path_conflict:
        lines.append('WARNING: the pages that name themselves record different reset paths, so some of them are damaged')
    if report.path_requested and report.path_used and report.path_requested != report.path_used:
        lines.append('WARNING: the configuration and the pages disagree about the reset path')
    if report.counter_conflict:
        counters = ', '.join(str(counter) for counter in sorted({counter for counter, _ in report.stamps}))
        lines.append(f'WARNING: the whole pages carry different boot counters ({counters}): pages of different writes sit side by side, so this is not '
                     'one trial and a series does not keep it')
    if not report.has_map:
        lines.append('WARNING: the file has no memory map: it is judged alone, and a series refuses it (NO_MEMORY_MAP)')
    named = [r.verdict for r in report.pages if r.verdict.named]
    if named:
        header = next((v for v in named if v.crc_ok), named[0]).header
        lines.append('PSCI_FEATURES as the probe read them: ' + ', '.join(
            f'{name}=0x{header[key]:X} ({psci_answer(header[key])})' for name, key in (('SYSTEM_RESET', 'feature_reset'), ('SYSTEM_RESET2', 'feature_reset2')))
                     + ('' if any(v.crc_ok for v in named) else ' (the CRC of every page that names itself fails: unverified)'))
    for r in report.pages:
        v = r.verdict
        head = f'page {r.index}  0x{r.address:X}  {v.state:<7}'
        if v.state == 'WHOLE':
            detail = f'counter {v.counter}, CRC ok, {retention.BODY_WORDS} of {retention.BODY_WORDS} body words'
        elif v.state == 'PARTIAL' and v.named:
            detail = (f'header names the page (counter {v.counter}), CRC {"ok" if v.crc_ok else "BAD"}, {v.wrong_words} of {retention.BODY_WORDS} body words wrong'
                      + (f', damaged bytes {_spans(v.damaged)}' if v.damaged else ', the body is intact and the header or the CRC word is not'))
        elif v.state == 'PARTIAL':
            detail = (f'header lost; {retention.BODY_WORDS - v.wrong_words} body words of boot counter {v.epoch[0]}, page {v.epoch[1]} survive: '
                      f'bytes {_spans(v.intact)}')
        else:
            detail = 'no canary found'
        lines.append(f'{head} {detail}; map: {r.map_note}')
    if report.counter:
        step = report.counter - report.previous_counter
        lines.append(f'boot counter: this boot {report.counter}, previous {report.previous_counter}, step +{step}'
                     + (' (first run: the probe found no whole page when it started)' if report.previous_counter == 0
                        else f' (the probe found whole pages of boot {report.previous_counter} when it started)'))
    else:
        lines.append('boot counter: no whole page, so none to read')
    counts = {s: sum(r.verdict.state == s for r in report.pages) for s in ('WHOLE', 'PARTIAL', 'NONE')}
    lines.append(f'verdict for path {report.path}: {report.verdict} (WHOLE x{counts["WHOLE"]}, PARTIAL x{counts["PARTIAL"]}, NONE x{counts["NONE"]})')
    if report.verdict == 'NONE':
        lines.append('NONE says the pages carry no canary. It does not say the probe wrote one: only the last line of its panel does.')
    return '\n'.join(lines)


def series(reports) -> dict:
    """Per reset path over the trials in order: how many, how many had a WHOLE page, the longest run of consecutive such trials in which every
    whole page was free memory in the extraction boot's map (the study's "memory map unchanged"), and whether a path passed. It counts trials and
    nothing else, so it refuses (DumpError, nothing counted) a file with no memory map, a file that is byte for byte one it has already read, and
    a file whose whole page carries the boot counter and the timestamp of one it has read: that is one write of the probe dumped twice."""
    paths, files, writes = {}, {}, {}
    for number, report in enumerate(reports, 1):
        name = report.source or f'file {number}'
        if not report.has_map:
            raise DumpError('NO_MEMORY_MAP', f'{name} has no memory map of the extraction boot, so a series cannot tell whether firmware owned a page and counts nothing from it')
        if report.digest in files:
            raise DumpError('SERIES_FILE_REPEATED', f'{name} is byte for byte the file {files[report.digest]}: one dump is one trial')
        files[report.digest] = name
        for stamp in report.stamps:
            if stamp in writes:
                raise DumpError('SERIES_TRIAL_REPEATED', f'{name} holds a page of boot counter {stamp[0]} and timestamp {stamp[1]} like {writes[stamp]}: '
                                                         'one write of the probe is one trial, dumped twice')
            writes[stamp] = name
        trial = paths.setdefault(report.path, {'trials': 0, 'whole': 0, 'partial': 0, 'none': 0, 'streak': 0, 'best_streak': 0})
        trial['trials'] += 1
        kept = report.verdict == 'WHOLE' and not report.counter_conflict and all(r.map_free is True for r in report.pages if r.verdict.state == 'WHOLE')
        trial[report.verdict.lower()] += 1
        trial['streak'] = trial['streak'] + 1 if kept else 0
        trial['best_streak'] = max(trial['best_streak'], trial['streak'])
    for trial in paths.values():
        trial['pass'] = trial['best_streak'] >= SERIES_PASS_TRIALS
    return paths


def _load(path: Path, config: bytes, pages, step):
    name = path.name
    found = re.fullmatch(r'RET([0-9]{1,2})\.TXT', name, re.IGNORECASE)
    return replace(judge(parse_dump(path.read_bytes(), expected_pages=pages, step=int(found.group(1)) if found and step else None), config), source=str(path))


def _file_time(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)


def _staging_time(plan: Path) -> datetime:
    """The staging time SP11/PLAN.TXT records, by the clock of the computer that staged the tree, or DumpError."""
    try:
        text = plan.read_text(encoding='ascii')
    except (OSError, UnicodeDecodeError) as error:
        raise DumpError('NO_STAGING_TIME', f'{plan.name} cannot be read ({type(error).__name__}), so a stale result cannot be told from a fresh one') from error
    found = _STAGED_LINE.search(text)
    if found is None:
        raise DumpError('NO_STAGING_TIME', f'{plan.name} records no staging time (a tree staged by an older tool), so a stale result cannot be told '
                                           'from a fresh one: `check` judges the file alone')
    return datetime.strptime(found.group(1), _SHOWN)


def stick_times(sp11: Path, files, reports) -> list:
    """The lines `stick` prints about time. DumpError when a result, or the marker of the step before it, is not later than the staging that
    PLAN.TXT records: it was left by an earlier run, and a verdict on it would be about that run. A stale DONE1.TXT makes step 1 skip, so the
    extraction dumps RAM that nothing wrote (NONE); a stale RET2.TXT makes step 2 refuse and leaves the old result to be judged (WHOLE)."""
    staged = _staging_time(sp11 / 'PLAN.TXT')
    lines = [f'times: SP11/PLAN.TXT records the staging at {staged:{_SHOWN}}, by the clock of the computer that staged it; the other times are the',
             'unit\'s clock, which read local time when it wrote the DONE markers of the restart probe (2026-11-15, OBSERVED once)']
    for path, report in zip(files, reports):
        marker = sp11 / f'DONE{report.step - 1}.TXT'
        if report.step < 2 or not marker.is_file():
            raise DumpError('NO_STEP_MARKER', f'{marker.name} is not on the stick: step {report.step - 1} never started on it, so the probe did not run '
                                              f'and {path.name} is a dump of RAM that nothing wrote')
        started = _file_time(marker)
        if started <= staged:
            raise DumpError('STALE_STEP_MARKER', f'{marker.name} was written at {started:{_SHOWN}}, not after the staging at {staged:{_SHOWN}}: it is left '
                                                 f'from an earlier run, so step {report.step - 1} was skipped in this one and {path.name} is a dump of RAM that nothing wrote')
        if report.clock is None:
            raise DumpError('NO_CLOCK', f'{path.name} has no date and time, so nothing says whether it was written after the staging at {staged:{_SHOWN}}: '
                                        '`check` judges the file alone')
        if report.clock <= staged:
            raise DumpError('STALE_RESULT', f'{path.name} says it was extracted at {report.clock:{_SHOWN}}, not after the staging at {staged:{_SHOWN}}: '
                                            'it is a result left from an earlier run, or the unit\'s clock is wrong; `check` judges the file alone')
        written = _file_time(path)
        lines += [f'  {marker.name}  written {started:{_SHOWN}} (step {report.step - 1} started), {(report.clock - started).total_seconds():.0f} s before the extraction',
                  f'  {path.name}  extracted {report.clock:{_SHOWN}} by the Shell\'s clock, file time {written:{_SHOWN}}']
        if abs((written - report.clock).total_seconds()) > FILE_TIME_SLACK_SECONDS:
            lines.append(f'WARNING: the file time of {path.name} is not the time written in it: it was copied or edited after the unit wrote it')
        finished = sp11 / f'DONE{report.step}.TXT'
        lines.append(f'  {finished.name}  written {_file_time(finished):{_SHOWN}} (the extraction ended)' if finished.is_file()
                     else f'  {finished.name}  is not on the stick: the extraction did not reach its marker')
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    for name in ('check', 'series'):
        p = sub.add_parser(name)
        p.add_argument('files', nargs='+' if name == 'series' else 1, type=Path)
        p.add_argument('--config', type=Path, help='the RETPATH.DAT of the run: the requested path and the pages are then checked against the file')
        p.add_argument('--pages', nargs=3, type=lambda s: int(s, 0), metavar='ADDR')
        p.add_argument('--json', action='store_true')
    stick = sub.add_parser('stick')
    stick.add_argument('directory', type=Path)
    args = ap.parse_args(argv)
    times, summary = [], {}
    try:
        if args.command == 'stick':
            sp11 = args.directory / 'SP11'
            config = sp11 / 'RETPATH.DAT'
            files = sorted(sp11.glob('RET[0-9]*.TXT'))
            if not files:
                raise DumpError('NO_RESULT_FILE', f'{sp11} has no RET<n>.TXT')
            args.config, args.files, args.pages, args.json = (config if config.is_file() else None), files, None, False
            args.command = 'check' if len(files) == 1 else 'series'
            reports = [_load(path, args.config.read_bytes() if args.config else None, None, True) for path in files]
            times = stick_times(sp11, files, reports)
        else:
            config = args.config.read_bytes() if args.config else None
            reports = [_load(path, config, args.pages, True) for path in args.files]
        if args.command == 'series':
            summary = series(reports)
    except DumpError as error:
        print(f'REFUSED {error.reason}: {error.detail}')
        return 2
    if args.json:
        print(json.dumps([{'plan': r.plan, 'step': r.step, 'path': r.path, 'verdict': r.verdict, 'counter': r.counter,
                           'pages': [{'index': p.index, 'address': hex(p.address), 'state': p.verdict.state, 'map': p.map_note} for p in r.pages]}
                          for r in reports], indent=2))
    elif args.command == 'check':
        print(*times, sep='\n', end='\n\n' if times else '')
        print(format_report(reports[0], args.files[0].name))
    else:
        print(*times, sep='\n', end='\n\n' if times else '')
        for path, report in zip(args.files, reports):
            print(format_report(report, path.name), end='\n\n')
        for name, trial in summary.items():
            print(f'path {name}: {trial["trials"]} trials, WHOLE {trial["whole"]}, PARTIAL {trial["partial"]}, NONE {trial["none"]}, '
                  f'best run of consecutive WHOLE trials {trial["best_streak"]} of {SERIES_PASS_TRIALS} needed: {"PASS" if trial["pass"] else "NOT PASSED"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
