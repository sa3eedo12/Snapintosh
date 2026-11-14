# SPDX-License-Identifier: MIT
"""Fixtures for the SP11 autorun payload tests: a Q1N1PLD1 manifest writer made with struct.pack.

The layout is written out here from docs/SP11-PAYLOAD.md of the q1n1 fork (a header of four little-endian u64: magic,
version, count, entry size; then per file a u64 size, the 32-byte SHA-256 and a 256-byte NUL-padded path) and
imports nothing from that repository, so the tests judge the tool's parser against a writer it was not derived from.
Every header field can be overridden and raw bytes can be appended or put in a path field, to write the defects the
parser has to refuse. Host only; nothing here touches a file.
"""
import hashlib
import struct

MAGIC = 0x31444C50314E3151              # "Q1N1PLD1"
HEADER = struct.Struct('<4Q')
ENTRY = struct.Struct('<Q32s256s')

# Sample payload data: the payload (file 0 of a real manifest) and two files it could read.
SAMPLE = {
    'STATE.BIN': bytes(range(256)) * 4,
    'DATA1.BIN': b'first data file\n' * 10,
    'DATA2.BIN': b'second data file\n' * 6,
}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entry(path, size: int, digest_hex: str) -> bytes:
    """One 296-byte record. `path` is a str, or bytes for a path field that is not plain text; the field is padded
    with NUL to 256 bytes (a field of 256 bytes or more has no terminator)."""
    raw = path.encode('ascii') if isinstance(path, str) else path
    return ENTRY.pack(size, bytes.fromhex(digest_hex), raw)


def manifest(records, *, magic: int = MAGIC, version: int = 1, count: int = None, entry_size: int = ENTRY.size,
             trailing: bytes = b'') -> bytes:
    """The header and `records` (as entry() makes them), with any header field overridden and `trailing` appended."""
    return HEADER.pack(magic, version, len(records) if count is None else count, entry_size) + b''.join(records) + trailing


def listing(files: dict, directory: str = '\\SP11\\') -> bytes:
    """The manifest that lists `files` {NAME: bytes}, in order, under `directory`: what the q1n1 manifest tool writes."""
    return manifest([entry(directory + name, len(data), sha256_hex(data)) for name, data in files.items()])
