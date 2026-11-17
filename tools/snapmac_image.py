#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Image-file access for the SP11 offline storage provisioning tools (docs/SP11-STORAGE-PROVISIONING.md). EXPERIMENTAL; hardware NOT RUN.

Everything the provisioning tools read or write goes through this module, and this module opens REGULAR FILES and nothing else:

  * `refuse_path` resolves a path (symbolic links, `..`, case) and refuses it when it is at or below `/dev` or `/Volumes`, when it is not a
    regular file, or when it is empty or holds a NUL. A path that names a device node is refused whatever it is called and however it is
    reached; the only way past it is the per-run authorised mode of snapmac_provision.py, which is designed and NOT enabled.
  * `Image` is the one place a file is opened (`os.open`), read-only unless the caller asks for writing, and it checks the open descriptor
    again (`fstat`: still a regular file, still the file that was checked).
  * `read_json` and `write_text_file` are the only other file accesses, through the same refusal; `check_output_path` is what the command
    line calls before any work, so a bad `--out` cannot refuse after an image was written (a journal is the only record of a write).
  * `Writer` writes into ONE extent of an image (a partition the operator created) and refuses a write outside it, a write that touches a
    protected extent, an unaligned or oversized write, and journals every write it makes.
  * `parse_gpt` reads and checks the primary and backup GPT of an image: what the provisioning tools compare with the plan.

No tool here shells out, imports a network module, or names a disk. The tests (tools/test-snapmac-image-sp11.py, class Sources) audit the
sources for that, and prove the audit finds each seeded fault. Evidence: EXPERIMENTAL (synthetic images only); hardware NOT RUN."""
import hashlib
import json
import os
import stat
import struct
import uuid
import zlib

BLOCK = 512
MIB = 1 << 20
CHUNK = 8 * MIB                         # the most one read, hash or write handles at a time (the A16 flow's chunk)
SIGNATURE = b'EFI PART'
HEADER = '<8sIIIIQQQQ16sQIII'          # the 92 bytes of a GPT header
HEADER_BYTES = 92
ENTRY_BYTES = 128
NIL = '00000000-0000-0000-0000-000000000000'
# A device node or a volume is reached through these. Compared after resolution and case-insensitively, and by identity (samefile) where
# the directory exists, so a firmlink, a symbolic link or a case variant does not get round it.
FORBIDDEN_ROOTS = ('/dev', '/Volumes')


class ImageError(ValueError):
    """The image or the path is refused."""


class WriteRefused(ImageError):
    """A write the bounded writer will not make."""


def _forbidden(real):
    low = real.lower().rstrip('/')
    for root in FORBIDDEN_ROOTS:
        if low == root.lower() or low.startswith(root.lower() + '/'):
            return root
        if os.path.isdir(root):
            parent = real
            while True:
                try:
                    if os.path.samefile(parent, root):
                        return root
                except OSError:
                    pass
                up = os.path.dirname(parent)
                if up == parent:
                    break
                parent = up
    return None


def _text(path):
    if not isinstance(path, (str, os.PathLike)) or not isinstance(os.fspath(path), str):
        raise ImageError('a path is a string')
    text = os.fspath(path)
    if not text or '\x00' in text:
        raise ImageError('an empty path, or one with a NUL')
    return text


def refuse_path(path):
    """The resolved path of a regular file this module may open, or ImageError."""
    text = _text(path)
    real = os.path.realpath(text)
    for candidate in (os.path.abspath(text), real):         # the path as given counts too: a symbolic link out of a volume is still under it
        root = _forbidden(candidate)
        if root:
            raise ImageError(f'the path {candidate} is at or below {root}: a device node or a volume is never opened by these tools')
    try:
        mode = os.stat(real).st_mode
    except OSError as e:
        raise ImageError(f'cannot stat {real}: {e.strerror}') from None
    if not stat.S_ISREG(mode):
        raise ImageError(f'{real} is not a regular file')
    return real


def read_json(path, limit=64 * MIB):
    real = refuse_path(path)
    if os.path.getsize(real) > limit:
        raise ImageError(f'{real} is larger than {limit} bytes')
    with open(real, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except ValueError as e:
            raise ImageError(f'{real} is not JSON: {e}') from None


def check_output_path(path):
    """The path a NEW output file may take, or ImageError. The command line calls it before any work, so that a bad --out cannot refuse after
    an image was already written: the journal of a write is the only record of it."""
    text_path = _text(path)
    given = os.path.dirname(os.path.abspath(text_path))
    parent = os.path.realpath(given)
    for candidate in (given, parent):
        if _forbidden(candidate):
            raise ImageError(f'{candidate} is at or below a device or volume root')
    if not os.path.isdir(parent):
        raise ImageError(f'{parent} is not a directory')
    if os.path.lexists(text_path):
        raise ImageError(f'{text_path} exists; an output is never overwritten')
    return text_path


def write_text_file(path, text):
    """Writes a new text file (never an existing one: an output is not overwritten) beside the files the tools read. The exclusive create is
    the last guard, and it is not the check above: it also refuses a file or a symbolic link made after that check."""
    text_path = check_output_path(path)
    try:
        f = open(text_path, 'x', encoding='utf-8')
    except FileExistsError:
        raise ImageError(f'{text_path} exists; an output is never overwritten') from None
    with f:
        f.write(text)


class Image:
    """An image FILE, opened once. `writable` asks for O_RDWR; nothing is ever created or truncated."""

    def __init__(self, path, writable=False):
        self.path = refuse_path(path)
        before = os.stat(self.path)
        # O_NOFOLLOW: a symbolic link put in the place of the checked file after refuse_path is not followed to another file.
        flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            self.fd = os.open(self.path, flags)
        except OSError as e:
            raise ImageError(f'cannot open {self.path}: {e.strerror}') from None
        after = os.fstat(self.fd)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            os.close(self.fd)
            raise ImageError('the file changed between the check and the open')
        self.size, self.writable, self.writes = after.st_size, writable, 0

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self.size:
            raise ImageError(f'a read of {length} bytes at {offset} leaves the image ({self.size} bytes)')
        out = bytearray()
        while len(out) < length:
            got = os.pread(self.fd, length - len(out), offset + len(out))
            if not got:
                raise ImageError('the image ended inside a read')
            out += got
        return bytes(out)

    def digest(self, offset, length):
        h, done = hashlib.sha256(), 0
        while done < length:
            n = min(CHUNK, length - done)
            h.update(self.read(offset + done, n))
            done += n
        return h.hexdigest()

    def digest_outside(self, start, end):
        """SHA-256 of everything but [start, end): the bytes before it, then the bytes after it, in order."""
        h = hashlib.sha256()
        for lo, hi in ((0, start), (end, self.size)):
            done = lo
            while done < hi:
                n = min(CHUNK, hi - done)
                h.update(self.read(done, n))
                done += n
        return h.hexdigest()

    def _write(self, offset, data):
        if not self.writable:
            raise WriteRefused('the image was opened read-only')
        done = 0
        while done < len(data):
            done += os.pwrite(self.fd, data[done:], offset + done)
        self.writes += 1

    def sync(self):
        if self.writable:
            os.fsync(self.fd)


def sha256_file(path):
    with Image(path) as image:
        return image.digest(0, image.size)


class Writer:
    """Writes into one extent [start, end) of an image, never anywhere else, and journals what it wrote.

    The extent is the partition the operator created; `protected` are the byte extents (offset, length) of everything that must not change
    (the other partitions, both GPT zones, both guards). The writer refuses an extent that overlaps one of them, so that a wrong plan cannot
    even be opened for writing, and refuses every write that is outside the extent, touches one, is empty, is not whole sectors, or is
    larger than one chunk."""

    def __init__(self, image, start, end, protected):
        if not image.writable:
            raise WriteRefused('the image was opened read-only')
        if not (0 <= start < end <= image.size) or start % BLOCK or end % BLOCK:
            raise WriteRefused(f'the extent {start}..{end} is not whole sectors inside the image ({image.size} bytes)')
        for offset, length in protected:
            if offset < end and start < offset + length:
                raise WriteRefused(f'the extent {start}..{end} overlaps the protected extent {offset}..{offset + length}')
        self.image, self.start, self.end, self.protected, self.journal = image, start, end, list(protected), []

    def write(self, offset, data):
        n = len(data)
        if n == 0 or offset % BLOCK or n % BLOCK:
            raise WriteRefused('a write is whole sectors and not empty')
        if n > CHUNK:
            raise WriteRefused(f'a write is at most {CHUNK} bytes')
        if offset < self.start or offset + n > self.end:
            raise WriteRefused(f'a write of {n} bytes at {offset} leaves the partition {self.start}..{self.end}')
        for p_offset, p_length in self.protected:
            if offset < p_offset + p_length and p_offset < offset + n:
                raise WriteRefused(f'a write at {offset} touches the protected extent {p_offset}..{p_offset + p_length}')
        self.image._write(offset, data)
        self.journal.append(dict(offset=offset, length=n, sha256=hashlib.sha256(data).hexdigest()))


def _guid(raw):
    return str(uuid.UUID(bytes_le=raw))


def _header(image, lba, bs):
    raw = image.read(lba * bs, HEADER_BYTES)
    fields = struct.unpack(HEADER, raw)
    sig, revision, size, crc, _, my, alt, first, last, guid, e_lba, e_count, e_size, e_crc = fields
    if sig != SIGNATURE or revision != 0x10000 or size != HEADER_BYTES:
        raise ImageError(f'no GPT header at LBA {lba}')
    zeroed = bytearray(raw)
    struct.pack_into('<I', zeroed, 16, 0)
    if zlib.crc32(bytes(zeroed)) != crc:
        raise ImageError(f'the GPT header at LBA {lba} fails its CRC')
    if my != lba:
        raise ImageError(f'the GPT header at LBA {lba} says it is at {my}')
    if e_size != ENTRY_BYTES or not 0 < e_count <= 128:
        raise ImageError('the entry array is not 128-byte entries, at most 128 of them')
    array = image.read(e_lba * bs, e_count * e_size)
    if zlib.crc32(array) != e_crc:
        raise ImageError(f'the entry array of the GPT header at LBA {lba} fails its CRC')
    return dict(alt=alt, first_usable=first, last_usable=last, guid=_guid(guid), array=array, entries_lba=e_lba, count=e_count)


def parse_gpt(image):
    """The GPT of an image: both copies read, their CRCs checked, the two compared. A dict, or ImageError.

    {block, disk_guid, first_usable, last_usable, entries: [{index, type, guid, first_lba, last_lba, attributes, name}]}; the entries are in
    slot order and only the used ones are listed."""
    errors = []
    for bs in (512, 4096):
        try:
            primary = _header(image, 1, bs)
        except ImageError as e:
            errors.append(e)
            continue
        if primary['alt'] != image.size // bs - 1:
            raise ImageError('the primary GPT header does not point at the last block of the image')
        backup = _header(image, primary['alt'], bs)
        if backup['alt'] != 1 or backup['array'] != primary['array'] or any(
                backup[k] != primary[k] for k in ('first_usable', 'last_usable', 'guid', 'count')):
            raise ImageError('the primary and backup GPT disagree')
        entries = []
        for slot in range(primary['count']):
            raw = primary['array'][slot * ENTRY_BYTES:(slot + 1) * ENTRY_BYTES]
            kind = _guid(raw[:16])
            if kind == NIL:
                continue
            first, last, attrs = struct.unpack('<QQQ', raw[32:56])
            if not primary['first_usable'] <= first <= last <= primary['last_usable']:
                raise ImageError(f'entry {slot} leaves the usable range')
            try:
                name = raw[56:128].decode('utf-16-le').split('\x00')[0]
            except UnicodeDecodeError:
                raise ImageError(f'entry {slot} has a name that is not UTF-16') from None
            entries.append(dict(index=slot, type=kind, guid=_guid(raw[16:32]), first_lba=first, last_lba=last, attributes=attrs,
                                name=name))
        return dict(block=bs, disk_guid=primary['guid'], first_usable=primary['first_usable'], last_usable=primary['last_usable'],
                    entries=entries)
    # Neither block size gave a GPT. Say why a header that WAS there is bad (its CRC, a pointer), not that the other block size's offset
    # holds no header: a 512-byte image always fails at the 4096-byte offset too.
    raise next((e for e in errors if not str(e).startswith('no GPT header')), errors[0])
