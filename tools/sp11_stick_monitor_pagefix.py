# SPDX-License-Identifier: MIT
"""Windows of an acknowledging monitor, for the page tests (tools/test-sp11-stick-monitor-page.py and tools/test-sp11-stick-monitor-page-ack.py): the stop of the M1 rung 5 run, after
a time box of 60 s with the CPU-start stores acknowledged (OBSERVED once on one unit: the stop kind, its counts, and that the run passed the CPU-start stores; the registers, the stores'
values and their count, the timer words and the clock are MADE UP, a model), in the four states of the guest timer that its words can tell apart: a timer that works, one that was armed and never
disabled but whose deadline has passed with no tick, one that was never armed, and one whose virtual FIQ was raised and never taken. Built on tools/sp11_stick_monitor_pagelib.py
only; `call` is the wrapper's `ticks_call` of the fixture's record. Nothing here touches hardware. EXPERIMENTAL."""
import dataclasses

import sp11_stick_monitor_pagelib as P

STORES = ((0xFFFFFE000C2448CC, 4 << 32 | 0x2), (0xFFFFFE000C2448CC, 8 << 32 | 0x2), (0xFFFFFE000C2448D4, 12 << 32 | 0x0))       # offset << 32 | the 32 bits written: made up


def stores(total, **over):
    """An acknowledge block with `total` stores (at most a thousand), the first eight kept as the monitor keeps them (those of STORES, repeated)."""
    first = tuple(STORES[i % 3] for i in range(min(total, 8)))
    at = [sum(1 for i in range(total) if i % 3 == k) for k in range(3)]
    return P.Ack(total=total, at=tuple(at), last=STORES[(total - 1) % 3] if total else (0, 0), first=first, **over)


def boot25(call, timer, ack=None, **over):
    """The stop of boot 25 as far as it is known: a watchdog stop in the kernel's idle WFI (PC after it, SPSR 0x604003C4), 512 events, a console that is empty (the panel is the
    console), the counts of that page; `timer` are the nine words of the guest timer (reads, writes, irqs, asserts, clears, vectors, ctl, cval, irq_pc)."""
    endpoints = [0] * P.ENDPOINT_BUCKETS
    endpoints[2], endpoints[45] = 30000, 36179
    x = [0xFFFFFE0009000000 + 0x3131 * i for i in range(31)]
    spec = P.Spec(words=(0, 0, 168214, 512, 1441 << 32, 0, 0, 66179), events=tuple((1, 0xFFFFFE000C230000 + 8 * i, 0x2D, 0, 0, 1) for i in range(5)), capture=1,
                  counters=(66000, 0, 0, 0, 0, 0, 54212, 0, 0, 12, 123456, 234567, 345678),
                  terminal=(6, 0xFFFFFE000B8716A8, 0x9600004F, 0xFFFFFECFFBFDC000, 0xFFFFFE000BA87E40, 0), endpoints=tuple(endpoints),
                  regs=(*x, 0xFFFFFE0007FE8000, 0x604003C4, 0xFFFFFE000B8716A8, 0, 0), clock=(2210, call + 3000, call + 96_700_000), timer=timer, ack=ack)
    return dataclasses.replace(spec, **over)


# The four states: (reads, writes, irqs, asserts, clears, vectors, ctl, cval, irq_pc)
OK = (2210, 410, 3120, 410, 410, 410, 0x1, 0x6E3A5F2C0, 0xFFFFFE000B8716A8)             # ticks were taken, virtual FIQs raised and taken, a deadline far ahead
NO_DEADLINE = (2210, 57, 2, 0, 0, 0, 0x0, 0x6E3A5F2C0, 0xFFFFFE000B8716A8)              # armed 57 times and left disabled: nothing is due
LOST = (2230, 63, 0, 0, 0, 0, 0x5, 0x5A0B3C4D5, 0x0)                                     # a deadline that has passed, not masked, and the monitor has not had one tick
STUCK = (2230, 77, 9, 3, 2, 0, 0x5, 0x5A0B3C4D5, 0xFFFFFE000B8716A8)                     # the virtual FIQ was raised more often than it was cleared and never taken


def timer_ok(call):
    return boot25(call, OK, stores(3))


def timer_no_deadline(call):
    return boot25(call, NO_DEADLINE, stores(3))


def timer_lost(call):
    return boot25(call, LOST, stores(3))


def timer_stuck(call):
    return boot25(call, STUCK, stores(3))


def timer_quiet(call):
    """The same states on a monitor without the acknowledge: no block, no ACK line, the page of the older kinds."""
    return boot25(call, OK)


def store_page(call, **over):
    """A stage-2 store (x0, 8 bytes, a translation fault at level 2) of an acknowledging monitor, with small timer words that fit one line down to WRITES (reads 11, writes 7)."""
    esr = 0x24 << 26 | 1 << 25 | 1 << 24 | 3 << 22 | 0 << 16 | 1 << 6 | 0x06
    spec = boot25(call, (11, 7, 3, 2, 2, 2, 0x5, 0x1F4, 0xFFFFFE000C1EEF00), stores(3), console=b'a console line\n', words=(0, 0, 9000, 512, 2 << 32, 0, 0, 2000 | 15 << 32),
                  terminal=(2, 0xFFFFFE000C1F4A10, 0x28E104004, esr, 0xFFFFFE0021EB4004, 0), counters=(1200, 0, 0, 0, 0, 0, 1500, 0, 0, 12, 400, 3500, 300))
    return dataclasses.replace(spec, **over)


def ack_loud(call):
    """The page of the same stop on a page that has the console's lines to keep too (a store, text in the console): the acknowledge gives way after the counters."""
    return store_page(call)


def ack_quiet(call):
    """A stage-2 store with an empty console: one line more is left, and the acknowledge is on the page."""
    return store_page(call, console=b'', words=(0, 0, 9000, 512, 2 << 32, 0, 0, 2000))


def ack_none(call):
    """Block present, nothing acknowledged: `COUNT=0` tells a monitor built with the acknowledge that took nothing from one built without it."""
    return boot25(call, OK, P.Ack())


def ack_eight(call):
    return boot25(call, OK, stores(8))


def ack_many(call):
    """A thousand stores: the first eight are in the block, the others are counted in MORE."""
    return boot25(call, OK, stores(1000))


def ack_wide(call):
    """The widest the line can be made: 2^64 - 1 stores of offset 2^32 - 1 and value 2^32 - 1: no store is cut, the line is whole and MORE is exact."""
    ones = 2 ** 64 - 1
    return boot25(call, OK, P.Ack(total=ones, at=(ones,) * 3, last=(ones, ones), first=((ones, ones),) * 8))


def ack_layout(call):
    """The magic is there and the layout word is not the one the reporter reads: named, not read."""
    return boot25(call, OK, P.Ack(layout=(192 << 32) | 1, total=3, first=STORES))


def ack_badmagic(call):
    """A block whose first word is not the magic is no block: the page is the page of a monitor without the acknowledge."""
    return boot25(call, OK, P.Ack(magic=0x31304B4341555044, total=3, first=STORES))


def ack_partial(call):
    """A failed prepare leaves a partial window: nothing of the new lines is printed from it, the block included."""
    return boot25(call, OK, stores(3), status=-90, entered=0)


def capture_marker_partial(call):
    """A capture word that is no class, in a window that a failed prepare left: no marker (the page of a failed prepare is what it was)."""
    return boot25(call, OK, stores(3), status=-90, entered=0, capture=0x1234)


def ack_nocap(call):
    """The acknowledge of a window with no capture of the guest's registers (word 0): the block is the monitor's own and is read all the same, the registers and the timer are not."""
    return boot25(call, OK, stores(3), capture=0)


def stop_edge(call, digits):
    """A watchdog stop whose C= has `digits` hexadecimal digits: with the 73 characters before it and the 8 of the line's prefix, 14 digits end the line at the runner's 100 columns
    (it is kept whole) and 15 end it at 101 (C= goes on a line of its own)."""
    value = int('FFFFFE000BA87E40'[:digits], 16)
    return boot25(call, OK, stores(3), terminal=(6, 0xFFFFFE000B8716A8, 0x9600004F, 0xFFFFFECFFBFDC000, value, 0))


def stop_edge_100(call):
    return stop_edge(call, 14)


def stop_edge_101(call):
    return stop_edge(call, 15)


BUILDERS = {'timer-ok': timer_ok, 'timer-no-deadline': timer_no_deadline, 'timer-lost': timer_lost, 'timer-stuck': timer_stuck, 'timer-quiet': timer_quiet,
            'ack-loud': ack_loud, 'ack-quiet': ack_quiet, 'ack-none': ack_none, 'ack-eight': ack_eight, 'ack-many': ack_many, 'ack-wide': ack_wide, 'ack-layout': ack_layout,
            'ack-badmagic': ack_badmagic, 'ack-partial': ack_partial, 'capture-marker-partial': capture_marker_partial, 'ack-nocap': ack_nocap,
            'stop-edge-100': stop_edge_100, 'stop-edge-101': stop_edge_101}
