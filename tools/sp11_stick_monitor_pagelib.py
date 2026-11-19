# SPDX-License-Identifier: MIT
"""The monitor's report window as the monitor lays it out, and the lines of the page that SNAPREP-M1.BIN prints from it, written from
docs/SP11-STICK-REPORT-LINES.md and docs/SP11-STICK-MONITOR.md section 4 and not from the C. A `Spec` says what the window holds; `build` writes
it into the 64 KiB of the window; `window_lines` says what the page shows for it, line by line and without the SNAPREP prefix, including the
budget: the page has 40 lines, the five GIC lines that follow the window are kept, and the new lines, the events and the console share the rest.
Used by tools/test-sp11-stick-monitor-record.py and tools/test-sp11-stick-monitor-page.py. Nothing here touches hardware. EXPERIMENTAL."""
import dataclasses
import struct

MASK = (1 << 64) - 1
WINDOW = 0x10000
LIMIT, GIC_LINES, WIDE, MORE_WIDTH, LINE_MAX = 40, 5, 95, 9, 100       # the runner's lines per payload; the lines kept for the GIC half; the width a line built from values keeps to; the runner's cut
EVENTS_MAX, CONSOLE_LINES, CONSOLE_WIDTH, CONSOLE_BYTES = 5, 6, 64, 1024
REG_LINES, CNT_LINES, CNT_LINES_MAX, CNT_SPLIT, EP_LINES = 8, 2, 3, 7, 3
CAPTURE_MAX, CAPTURE_S2, EV_S2_FAULT, EC_ABORT_INSN, EC_ABORT_DATA = 3, 3, 2, 0x20, 0x24
# Where the monitor puts what, in bytes from the window's start: 16 + 8 * the word offsets of probes/xnuboot/report_layout.c (the drift test of
# tools/test-sp11-stick-monitor-record.py compiles every one of them against the monitor's own translation unit).
STATUS, ENTERED, WORDS, LOG, CONSOLE, COUNTERS, BAD, TERMINAL = 0x0, 0x8, 0x10, 0x50, 0x6050, 0x67C0, 0x6830, 0x6840
ENDPOINTS, ASYNC, CLOCK_READS, TIMER, CLOCK_RAW = 0x6870, 0x7AC8, 0x7C40, 0x7C80, 0x8520
ACK = 0xFA10                    # an ack monitor's CPU-start block, OUT_CPU_START_ACK (compat/cpu-start-ack.h): 24 words
ACK_MAGIC, ACK_LAYOUT, ACK_FIRST, ACK_WORDS = 0x31304B4341555043, (176 << 32) | 1, 8, 24      # "CPUACK01"; sizeof(struct cpu_start_ack) << 32 | version; stores kept whole; words
ENDPOINT_BUCKETS = 64
# The guest timer's words in the order the page prints them: (name, place among the nine words at TIMER, hex), the names the document gives and the monitor's fields
# in report.c; the words themselves are reads, writes, irqs, asserts, clears, vectors, ctl, cval and irq_pc, in that order, at OUT_TIMER_FLOW.
TIMER_FIELDS = (('IRQS', 2, False), ('ASSERTS', 3, False), ('CLEARS', 4, False), ('VECTORS', 5, False), ('CTL', 6, True), ('CVAL', 7, True), ('WRITES', 1, False),
                ('IRQPC', 8, True), ('READS', 0, False))
TIMER_STORED = ('timer.reads', 'timer.writes', 'timer_irqs', 'timer_asserts', 'timer_clears', 'timer_vectors', 'timer_ctl', 'timer.cval', 'timer_irq_pc')
TIM_LINES_MAX = 2
COUNTER_NAMES = ('MAPS', 'MAPFAIL', 'DISP', 'PANIC', 'UNDEF', 'MAPOUT', 'LOGDROP', 'CONDROP', 'RNGDROP', 'MAPDEV', 'MMIORD', 'MMIOWR', 'UARTTX')
COUNTER_FIELDS = ('map_pages', 'map_failures', 'dispatch_cmds', 'panic_calls', 'undef_skipped', 'map_outside', 'log_dropped', 'console_dropped',
                  'ranges_dropped', 'map_device', 'mmio_reads', 'mmio_writes', 'uart_tx')
EVENT_NAMES = {1: 'MONITOR', 2: 'S2_FAULT', 3: 'GUEST_EXC', 4: 'SYSREG', 5: 'OTHER', 6: 'WATCHDOG', 7: 'HANDOFF', 8: 'DISPATCH_CMD', 9: 'PANIC_ENTRY',
               10: 'MAP_FAILED', 11: 'MAP_OUTSIDE', 12: 'SERROR', 13: 'ASYNC', 15: 'MAP_DEVICE', 16: 'MMIO_STOP', 24: 'EXCEPTION_RETURN', 33: 'TIMER_FIQ',
               36: 'DEVICE_RESET', 52: 'EARLY_STOP'}
FONT = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 :-./=?()_')
DEFAULT_REGS = (*[0x100 + i for i in range(31)], 0x7000, 0x3C5, 0xFFFF000008001234, 0x96000045, 0xFFFF000008004567)   # x0 to x30, sp, spsr, pc, esr, far


def h(value):
    return '0X%X' % value


@dataclasses.dataclass
class Ack:
    """An ack monitor's CPU-start block as the report window holds it (probes/xnuboot/report_layout.c OUT_CPU_START_ACK, compat/cpu-start-ack.h `struct cpu_start_ack`): the
    magic and the layout word, `total` stores acknowledged, `at` the count at +4, +8 and +12, the last store's PC and `what` (offset << 32 | value), and `first`, the first
    eight stores as (pc, what)."""
    magic: int = ACK_MAGIC
    layout: int = ACK_LAYOUT
    total: int = 0
    at: tuple = (0, 0, 0)
    last: tuple = (0, 0)
    first: tuple = ()


@dataclasses.dataclass
class Spec:
    """What the window holds. `words` are the eight report words (2 traps, 3 events, 4 aborts | steps << 32, 7 calls | console bytes << 32); `regs` are
    the 36 words after the capture word: x0 to x30, the selected sp, spsr, pc, esr, far; `clock` is (trapped reads, first raw sample, last raw sample); `timer` is the
    nine words of the guest timer (reads, writes, irqs, asserts, clears, vectors, ctl, cval, irq_pc); `ack` is an `Ack`, or None for a monitor without the acknowledge
    (its block is zero)."""
    status: int = 0
    entered: int = 1
    words: tuple = (0,) * 8
    events: tuple = ()
    counters: tuple = (0,) * 13
    bad: tuple = (0, 0)
    terminal: tuple = None
    capture: int = 0
    regs: tuple = DEFAULT_REGS
    console: bytes = b''
    endpoints: tuple = (0,) * ENDPOINT_BUCKETS
    other: int = 0
    clock: tuple = (0, 0, 0)
    timer: tuple = (0,) * 9         # reads, writes, irqs, asserts, clears, vectors, ctl, cval, irq_pc: OUT_TIMER_FLOW words 0 to 8
    ack: Ack = None


def build(spec):
    """The 64 KiB window for `spec`."""
    w = bytearray(WINDOW)
    struct.pack_into('<2Q', w, STATUS, spec.status & MASK, spec.entered)
    struct.pack_into('<8Q', w, WORDS, *spec.words)
    for i, event in enumerate(spec.events):
        struct.pack_into('<6Q', w, LOG + 48 * i, *event)
    w[CONSOLE:CONSOLE + len(spec.console)] = spec.console
    struct.pack_into('<13Q', w, COUNTERS, *spec.counters)
    struct.pack_into('<2Q', w, BAD, *spec.bad)
    if spec.terminal:
        struct.pack_into('<6Q', w, TERMINAL, *spec.terminal)
    struct.pack_into(f'<{ENDPOINT_BUCKETS + 1}Q', w, ENDPOINTS, *spec.endpoints, spec.other)
    struct.pack_into('<37Q', w, ASYNC, spec.capture, *spec.regs)
    struct.pack_into('<Q', w, CLOCK_READS, spec.clock[0])
    struct.pack_into('<9Q', w, TIMER, *spec.timer)
    struct.pack_into('<2Q', w, CLOCK_RAW, spec.clock[1], spec.clock[2])
    if spec.ack is not None:
        a = spec.ack
        first = [*a.first, *[(0, 0)] * (ACK_FIRST - len(a.first))]
        struct.pack_into(f'<{ACK_WORDS}Q', w, ACK, a.magic, a.layout, a.total, *a.at, *a.last, *[word for pair in first for word in pair])
    return bytes(w)


def console_lines(text, bytes_held):
    """The console lines the page can show: the held bytes split at breaks and zero bytes, cut every 64 characters, in the panel's font, at most six."""
    out, held, i = [], text[:min(bytes_held, CONSOLE_BYTES)], 0
    while i < len(held) and len(out) < CONSOLE_LINES:
        while i < len(held) and held[i] in (10, 0):
            i += 1
        j = i
        while j < len(held) and held[j] not in (10, 0) and j - i < CONSOLE_WIDTH:
            j += 1
        if j > i:
            out.append('MON CON ' + ''.join(chr(c - 32) if 97 <= c <= 122 else chr(c) if chr(c) in FONT else '?' for c in held[i:j]))
        i = j
    return out


def endpoint_lines(spec, budget):
    """The calls by endpoint in at most `budget` lines: TOTAL and OTHER first, then ENDPOINT:CALLS for every bucket that is not zero, ascending, a pair
    added only while the line stays within 95 characters with the prefix (9 less on the last line, which keeps room for MORE); the pairs that do not
    fit are counted in MORE on the last line."""
    total = (sum(spec.endpoints) + spec.other) & MASK
    lines, text, hidden = [], f'MON EP TOTAL={total} OTHER={spec.other}', 0
    for n, calls in enumerate(spec.endpoints):
        if not calls:
            continue
        pair = f' {n}:{calls}'
        last = len(lines) + 1 == budget
        if hidden or len('SNAPREP ' + text + pair) + (MORE_WIDTH if last else 0) > WIDE:
            if hidden or last:
                hidden += 1
                continue
            lines.append(text)
            text = 'MON EP'
        text += pair
    if hidden:
        text += f' MORE={hidden}'
    return lines + [text]


def counter_lines(spec, budget):
    """The thirteen counters as NAME=value in at most `budget` lines: seven and six while the values leave them room, a counter that does not fit its line
    goes on the next, and those that do not fit the last line are counted in MORE (9 characters are kept for it on the last line, unless the counter is the
    thirteenth): no value is ever cut."""
    pairs = [f' {name}={value}' for name, value in zip(COUNTER_NAMES, spec.counters)]
    lines, text, k = [], 'MON CNT', 0
    while k < len(pairs):
        if k == CNT_SPLIT and not lines:
            lines.append(text)
            text = 'MON CNT'
        last = len(lines) + 1 == budget
        if len('SNAPREP ' + text + pairs[k]) + (MORE_WIDTH if last and k + 1 < len(pairs) else 0) <= WIDE:
            text += pairs[k]
            k += 1
        elif last:
            break
        else:
            lines.append(text)
            text = 'MON CNT'
    if k < len(pairs):
        text += f' MORE={len(pairs) - k}'
    return lines + [text]


def timer_lines(spec, budget):
    """MON TIM: the nine words of the guest timer as NAME=value in the order of TIMER_FIELDS, counters in decimal and CTL, CVAL and IRQPC in hex, in at most `budget`
    lines: a pair that does not fit its line goes on the next, and those that do not fit the last line are counted in MORE (9 characters are kept for it on the last
    line, unless the pair is the ninth): no value is ever cut."""
    pairs = [f' {name}=' + (h(spec.timer[place]) if hexadecimal else str(spec.timer[place])) for name, place, hexadecimal in TIMER_FIELDS]
    lines, text, k = [], 'MON TIM', 0
    while k < len(pairs):
        last = len(lines) + 1 == budget
        if len('SNAPREP ' + text + pairs[k]) + (MORE_WIDTH if last and k + 1 < len(pairs) else 0) <= WIDE:
            text += pairs[k]
            k += 1
        elif last:
            break
        else:
            lines.append(text)
            text = 'MON TIM'
    if k < len(pairs):
        text += f' MORE={len(pairs) - k}'
    return lines + [text]


def clock_line(spec, call_ticks, return_ticks):
    """MON CLK: the reads, then AFTER (the last sample less the wrapper's read before the call, NA with no sample or one outside the call, from its start to its end: the last
    sample is the report writer's own, so AFTER is the time from the call's start to the report and not a guest event), FIRST and LAST; a field that would pass 95 characters
    (9 less when fields follow it) is left out and the line ends with MORE and their number."""
    reads, first, last = spec.clock
    fields = [f' AFTER={last - call_ticks}' if last and call_ticks <= last <= return_ticks else ' AFTER=NA', f' FIRST={h(first)}', f' LAST={h(last)}']
    text = f'MON CLK READS={reads}'
    for i, field in enumerate(fields):
        if len('SNAPREP ' + text + field) + (MORE_WIDTH if i + 1 < len(fields) else 0) > WIDE:
            return text + f' MORE={len(fields) - i}'
        text += field
    return text


def ack_line(spec):
    """MON ACK: an ack monitor's CPU-start acknowledge: COUNT (all the stores taken and ignored), then the first eight as `offset=value` (the 32 bits written; every store is
    a 32-bit one, so no width), a store added only while the line, with the room for ` MORE=n` (n counted exactly) after it, stays within 95 characters; MORE counts the stores not
    listed. A block whose layout word is not the one this reads is named, not read. The line exists only where the first word is the magic."""
    block = spec.ack
    if block.layout != ACK_LAYOUT:
        return f'MON ACK LAYOUT={h(block.layout)} NOT READ'
    text, shown = f'MON ACK COUNT={block.total}', 0
    for k in range(min(block.total, ACK_FIRST)):
        what = block.first[k][1] if k < len(block.first) else 0
        more = block.total - (k + 1)
        token = f' {what >> 32}={h(what & 0xFFFFFFFF)}'
        if len('SNAPREP ' + text + token) + (len(f' MORE={more}') if more else 0) > WIDE:
            break
        text += token
        shown += 1
    return text + (f' MORE={block.total - shown}' if block.total - shown else '')


def esr_line(esr):
    """MON ESR: the fields of the syndrome of a stage-2 fault as probes/run.py s2_fault_words takes them. An abort from the guest's EL1 is EC 0x20 (instruction)
    or 0x24 (data); ISV, SIZE (SAS as the bytes it means: 1, 2, 4, 8), SRT and WnR are a data abort's, SIZE and SRT only with ISV set; the fault status and S1PTW are an
    abort's; the rest is NA."""
    ec = esr >> 26 & 0x3F
    data, abort = ec == EC_ABORT_DATA, ec in (EC_ABORT_INSN, EC_ABORT_DATA)
    isv = data and esr >> 24 & 1

    def field(key, valid, value):
        return f' {key}={value if valid else "NA"}'
    return (f'MON ESR EC={h(ec)}' + field('ISV', data, esr >> 24 & 1) + field('SIZE', isv, 1 << (esr >> 22 & 3)) + field('SRT', isv, esr >> 16 & 31) +
            field('WNR', data, esr >> 6 & 1) + field('S1PTW', abort, esr >> 7 & 1) + ' DFSC=' + (h(esr & 0x3F) if abort else 'NA'))


def store_described(esr):
    """Whether the syndrome describes a store of the guest's own instruction: a data abort with ISV set and WnR set, not on the stage-1 table walk."""
    return esr >> 26 & 0x3F == EC_ABORT_DATA and bool(esr >> 24 & 1) and bool(esr >> 6 & 1) and not esr >> 7 & 1


def wval_line(esr, x):
    """MON WVAL: the guest register that SRT names (31 is the zero register) whole, and its low SAS bytes, which is what the store carried."""
    srt = esr >> 16 & 31
    value = 0 if srt == 31 else x[srt]
    carried = value & ((1 << (8 << (esr >> 22 & 3))) - 1)
    return f'MON WVAL {"XZR" if srt == 31 else f"X{srt}"}={h(value)} DATA={h(carried)}'


def window_lines(spec, call_ticks, return_ticks, monitor_pa, before, status_names=None, gic_lines=GIC_LINES):
    """The lines of the window's part of the page, without the prefix, from the status line to the last console line. `call_ticks` and `return_ticks` are the
    wrapper's `ticks_call` and `ticks_return`, `monitor_pa` the base the EL2 fault's OFF is counted from, `before` the number of lines the page has printed
    already, `status_names` {status: (NAME, ...)} the names of the prepare failures, `gic_lines` the lines kept for what follows the window."""
    out, status = [], spec.status & MASK
    if status == 0:
        out.append(f'MON STATUS=OK ENTERED={h(spec.entered)}')
    elif status >> 63:
        code = (1 << 64) - status
        out.append(f'MON PREPARE FAILED CODE=-{code} {status_names[-code][0] if status_names and -code in status_names else "UNNAMED"}')
    else:
        out += [f'MON EL2 FAULT ESR={h(status)} EC={h(status >> 26 & 0x3F)}',
                f'MON FAULT ELR={h(spec.bad[0])} OFF={h((spec.bad[0] - monitor_pa) & MASK)} FAR={h(spec.bad[1])}']
    w = spec.words
    out.append(f'MON COUNTS EVENTS={w[3]} TRAPS={w[2]} ABORTS={w[4] & 0xFFFFFFFF} STEPS={w[4] >> 32} CALLS={w[7] & 0xFFFFFFFF} DROPPED={spec.counters[6]}')
    kind, esr = 0, 0
    if spec.terminal and spec.terminal[0]:
        t = spec.terminal
        kind, esr = t[0], t[3]
        stop = f'MON STOP {EVENT_NAMES.get(t[0], "UNNAMED")} PC={h(t[1])} A={h(t[2])} B={h(t[3])}'
        # C= goes on a line of its own where the line with it would pass the runner's 100 columns, which would cut a digit of it
        out += [stop, f'MON STOP C={h(t[4])}'] if len('SNAPREP ' + stop + f' C={h(t[4])}') > LINE_MAX else [stop + f' C={h(t[4])}']
    x = spec.regs
    capture = spec.capture if 1 <= spec.capture <= CAPTURE_MAX else 0
    if spec.capture and not capture and status == 0:
        out.append(f'MON CAPTURE WORD={h(spec.capture)} IS NO CLASS')
    if capture:
        out.append(f'MON EXIT PC={h(x[33])} SPSR={h(x[32])} ESR={h(x[34])} FAR={h(x[35])}')
    events = min(w[3], EVENTS_MAX)
    console = console_lines(spec.console, w[7] >> 32)
    keep = (events > 0) + (len(console) > 0)

    def room():
        return max(0, LIMIT - gic_lines - before - len(out))
    if status == 0:
        ack = int(spec.ack is not None and spec.ack.magic == ACK_MAGIC)
        if kind == EV_S2_FAULT and room() >= 1 + keep:
            out.append(esr_line(esr))
        if kind == EV_S2_FAULT and capture == CAPTURE_S2 and store_described(esr) and room() >= 1 + keep:
            out.append(wval_line(esr, x))
        if capture and room() >= REG_LINES + keep:
            for i in range(REG_LINES):
                out.append(f'MON GX{4 * i}-{"SP" if i == REG_LINES - 1 else 4 * i + 3}' + ''.join(' ' + h(v) for v in x[4 * i:4 * i + 4]))
        if room() >= 1 + keep:
            out.append(clock_line(spec, call_ticks, return_ticks))
        if (capture and room() >= 1 + keep):
            out += timer_lines(spec, TIM_LINES_MAX if room() - keep >= TIM_LINES_MAX + CNT_LINES + ack else 1)
        if room() >= CNT_LINES + keep:
            out += counter_lines(spec, CNT_LINES_MAX if room() - keep > CNT_LINES + 1 else CNT_LINES)
        if ack and room() >= 1 + keep:
            out.append(ack_line(spec))
        if room() > keep:
            out += endpoint_lines(spec, min(room() - keep, EP_LINES))
    left = room()
    shown_events = min(events, 1, left)
    shown_console = min(len(console), left - shown_events)
    shown_events += min(events - shown_events, left - shown_events - shown_console)
    for n in range(shown_events):
        e = spec.events[n] if n < len(spec.events) else (0,) * 6
        out.append(f'MON EV {n} {EVENT_NAMES.get(e[0], "UNNAMED")} PC={h(e[1])} A={h(e[2])} B={h(e[3])} C={h(e[4])}')
    return out + console[:shown_console]
