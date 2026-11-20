# SPDX-License-Identifier: MIT
"""Byte sources for the chunks of a GUEST.DAT that must not be held in memory: the 1.8 GiB root image of the root carry (docs/SP11-STICK-ROOT.md).

A chunk of the packer is `dict(kind, dest, data=bytes)` or, for a ZERO chunk, `dict(kind, dest, size)`. A chunk that is a window of a Source is
`dict(kind, dest, size, source=<Source>, offset=<bytes into it>)`: tools/sp11_stick_bundle.py pack_guest refuses it (it would otherwise read it as a ZERO chunk), and
tools/sp11_stick_bundle_root.py write_guest streams it. Nothing here copies more than the window it was asked for. The cut of a long run into chunks (split_run) is that of
tools/sp11_stick_bundle_chunks.py split_zero for the stretches of zero bytes it keeps, found without a regular expression (the regex is quadratic on a stretch of zeros
shorter than its minimum, which a root image has thousands of) and under the budget of the 1024-entry table.

Evidence: EXPERIMENTAL, hardware NOT RUN; the equivalence with split_zero is shown by tools/test-sp11-stick-bundle-big.py."""
import bisect
import hashlib
import mmap

MIB = 1 << 20
SCAN_WINDOW = 4 * MIB           # what zero_stretches holds at once
STREAM_RUN_MIN = 256 * MIB      # a run of this size or more stays a Source in chunks.flatten(); the kernel image (about 115 MiB) is joined as it always was
PIECE_MAX = 128 * MIB           # a data chunk of a Source is at most this long, so that the wrapper's per-chunk progress word moves about every 128 MiB of a 1.8 GiB copy
DEST_ALIGN = 16
REFUSALS = ('ROOT_TABLE_FULL',)


class TableFull(ValueError):
    """The chunks of the guest do not fit the 1024 entries of the table, even with no ZERO chunk of the root."""


class Source:
    """A read-only run of bytes of known length that is read in windows: len(), a slice (a lazy Slice) and read_at() are all a caller needs."""

    def __len__(self):
        raise NotImplementedError

    def read_at(self, offset, count):
        """Exactly `count` bytes from `offset`; the range lies inside the source."""
        raise NotImplementedError

    def __getitem__(self, key):
        if not isinstance(key, slice):
            raise TypeError('a Source is sliced, not indexed')
        start, stop, step = key.indices(len(self))
        if step != 1:
            raise ValueError('a Source slice has no step')
        return Slice(self, start, max(start, stop))

    def padded(self, pad):
        return Pieces([self, bytes(pad)]) if pad else self

    def windows(self, size=4 * MIB):
        for at in range(0, len(self), size):
            yield at, self.read_at(at, min(size, len(self) - at))


class BufferSource(Source):
    """A buffer, typically an mmap of the root image. For an mmap the pages of a window are given back after it is read (madvise DONTNEED where the host has it): a clean file
    page is cheap for the kernel to drop, and the resident set of a multi-GiB pass stays near the window instead of the file."""
    _PAGE = mmap.PAGESIZE

    def __init__(self, buf):
        self._map = buf if isinstance(buf, mmap.mmap) else None
        self._view = memoryview(buf).cast('B')

    def __len__(self):
        return len(self._view)

    def read_at(self, offset, count):
        if offset < 0 or count < 0 or offset + count > len(self._view):
            raise ValueError('read outside the source')
        data = bytes(self._view[offset:offset + count])
        if self._map is not None and hasattr(self._map, 'madvise') and hasattr(mmap, 'MADV_DONTNEED'):
            first = -(-offset // self._PAGE) * self._PAGE
            last = (offset + count) // self._PAGE * self._PAGE
            if last > first:
                self._map.madvise(mmap.MADV_DONTNEED, first, last - first)
        return data


class Slice(Source):
    def __init__(self, parent, start, stop):
        while isinstance(parent, Slice):
            start, stop, parent = start + parent._start, stop + parent._start, parent._parent
        self._parent, self._start, self._stop = parent, start, stop

    def __len__(self):
        return self._stop - self._start

    def read_at(self, offset, count):
        if offset < 0 or count < 0 or offset + count > len(self):
            raise ValueError('read outside the source')
        return self._parent.read_at(self._start + offset, count)


class Pieces(Source):
    """The concatenation of buffers and Sources, never joined."""

    def __init__(self, parts):
        self._parts = [p if isinstance(p, Source) else BufferSource(p) for p in parts if len(p)]
        self._ends, total = [], 0
        for p in self._parts:
            total += len(p)
            self._ends.append(total)

    def __len__(self):
        return self._ends[-1] if self._ends else 0

    def read_at(self, offset, count):
        if offset < 0 or count < 0 or offset + count > len(self):
            raise ValueError('read outside the source')
        out, at = [], offset
        i = bisect.bisect_right(self._ends, at)
        while at < offset + count:
            start = self._ends[i - 1] if i else 0
            take = min(self._ends[i] - at, offset + count - at)
            out.append(self._parts[i].read_at(at - start, take))
            at += take
            i += 1
        return b''.join(out)


class Generated(Source):
    """A deterministic stand-in for a root image: blocks of SHAKE-128(label, block number), with the ranges of `zeros` (half-open, in bytes) set to zero. Opaque and
    reproducible, and it makes a multi-GiB rehearsal without Apple material."""
    BLOCK = MIB

    def __init__(self, label, size, zeros=()):
        self._prefix, self._size, self._zeros = label.encode() + b':', size, sorted(zeros)

    def __len__(self):
        return self._size

    def _block(self, k):
        data = bytearray(hashlib.shake_128(self._prefix + k.to_bytes(8, 'little')).digest(self.BLOCK))
        lo, hi = k * self.BLOCK, (k + 1) * self.BLOCK
        for s, e in self._zeros:
            if s < hi and lo < e:
                data[max(s, lo) - lo:min(e, hi) - lo] = bytes(min(e, hi) - max(s, lo))
        return data

    def read_at(self, offset, count):
        if offset < 0 or count < 0 or offset + count > self._size:
            raise ValueError('read outside the source')
        out, at = [], offset
        while at < offset + count:
            k, within = divmod(at, self.BLOCK)
            take = min(self.BLOCK - within, offset + count - at)
            out.append(bytes(self._block(k)[within:within + take]))
            at += take
        return b''.join(out)


def joined(parts):
    """What a run of the chunk flattener holds: `parts` joined into bytes when they are all buffers and under STREAM_RUN_MIN, else the same bytes as a Pieces."""
    plain = all(isinstance(p, (bytes, bytearray, memoryview)) for p in parts)
    if plain and sum(len(p) for p in parts) < STREAM_RUN_MIN:
        return b''.join(parts)
    return Pieces(parts)


def zero_stretches(src, minimum, window=SCAN_WINDOW):
    """[(start, end)]: the maximal stretches of zero bytes of `src` that are `minimum` bytes or longer, exactly the matches of rb'\\x00{minimum,}' on its bytes. Any such
    stretch holds a whole aligned page of `page` <= minimum // 2 bytes, so the scan classifies pages and then narrows each run of all-zero pages with the zeros that end the
    page before it and begin the page after it (fewer than a page each, by the run's maximality)."""
    if minimum < 32:
        raise ValueError('the shortest stretch is 32 bytes')
    page = min(4096, minimum // 2)
    window = max(page, window // page * page)
    n, found, cur = len(src), [], None
    for base in range(0, n, window):
        data = src.read_at(base, min(window, n - base))
        for p in range(0, len(data), page):
            q = min(p + page, len(data))
            at = base + p
            if data[p] and data[q - 1]:         # no zero at either end of the page: no stretch crosses into it or starts in it that could reach `minimum`
                if cur is not None and at - cur >= minimum:
                    found.append((cur, at))
                cur = None
                continue
            if data.count(0, p, q) == q - p:
                if cur is None:
                    cur = at
                continue
            body = data[p:q]
            lead = (q - p) - len(body.lstrip(b'\0'))
            if cur is not None and at + lead - cur >= minimum:
                found.append((cur, at + lead))
            trail = (q - p) - len(body.rstrip(b'\0'))
            cur = at + (q - p) - trail if trail else None
    if cur is not None and n - cur >= minimum:
        found.append((cur, n))
    return found


def is_zero(src, window=SCAN_WINDOW):
    return all(data.count(0) == len(data) for _, data in src.windows(window))


def _entries(chosen, size, piece_max):
    """Table entries of a run of `size` bytes cut at the (start, end) stretches `chosen`: one ZERO chunk per stretch and the data between them in pieces of at most `piece_max`."""
    total, pos = len(chosen), 0
    for a, b in chosen:
        total += -(-(a - pos) // piece_max)
        pos = b
    return total + -(-(size - pos) // piece_max)


def split_run(kind, start, src, minimum, budget, piece_max=PIECE_MAX):
    """The chunks of one run (padded to 16 bytes, starting at `start`) that is held as a Source: a stretch of `minimum` zero bytes or more, narrowed inward to 16, is a ZERO chunk as in
    split_zero, but only as many stretches, the longest first and the lower address first among equals, as leave at most `budget` entries for this run (the table holds 1024 for
    the whole guest); the data between is cut into pieces of at most `piece_max` bytes. A run that is all zero is one ZERO chunk. TableFull when even no stretch gives more than
    `budget` entries."""
    size = len(src)
    if piece_max % DEST_ALIGN or piece_max <= 0 or size % DEST_ALIGN:
        raise ValueError('a run and its pieces are multiples of 16 bytes')
    if is_zero(src):
        return [dict(kind=kind, dest=start, size=size)]
    stretches = [(-(-s // DEST_ALIGN) * DEST_ALIGN, e // DEST_ALIGN * DEST_ALIGN) for s, e in zero_stretches(src, minimum)]
    stretches = [(a, b) for a, b in stretches if b - a >= minimum]
    ranked = sorted(stretches, key=lambda s: (s[0] - s[1], s[0]))
    low, high = 0, len(ranked)          # _entries grows with the number of stretches kept: the most that fits
    while low < high:
        mid = (low + high + 1) // 2
        if _entries(sorted(ranked[:mid]), size, piece_max) <= budget:
            low = mid
        else:
            high = mid - 1
    chosen = sorted(ranked[:low])
    if _entries(chosen, size, piece_max) > budget:
        raise TableFull(f'a run of {size} bytes needs {_entries(chosen, size, piece_max)} entries with no ZERO chunk beyond what fits; {budget} are left')
    out, pos = [], 0

    def data(a, b):
        for at in range(a, b, piece_max):
            out.append(dict(kind=kind, dest=start + at, size=min(piece_max, b - at), source=src, offset=at))
    for a, b in chosen:
        if a > pos:
            data(pos, a)
        out.append(dict(kind=kind, dest=start + a, size=b - a))
        pos = b
    if pos < size:
        data(pos, size)
    return out
