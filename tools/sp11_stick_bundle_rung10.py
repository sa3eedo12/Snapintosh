# SPDX-License-Identifier: MIT
"""The build side of the record of rung 10 (docs/SP11-STICK-ROOT.md; EXPERIMENTAL, host only, hardware NOT RUN): rung 9's form with the ISC. Rung 9 (record dd2f39f4...b6b, commit b455ce2,
boot 28) carried Apple's BaseSystem root as the guest's RAM disk and ran the stock kernel to launchd's first boot task, which failed asking for the Preboot and Update volumes
(files/result-boot28.md of the session). Rung 10 is the SAME form (profile quiet-rtc-acm, the root-carry pair, the gate-on runner, a box of 300 s, cpu_boot_timeout_secs=360, let_panic, 6,144 MiB of
RAM) with ONE change to the guest's data: the RAM disk is a GPT medium of two partitions, the root (at 1 MiB, byte for byte rung 9's RAM disk) and the ISC (the three-role image of
tools/make-isc-fixture.py: three EMPTY volumes of the roles xART 256, Preboot 16 and Hardware 320), as probes/bootmedia.py builds it and as the A16 route did. What is here is the proof of
what the guest is against rung 9's own, and the numbers that the words of rung 10 are made from:

 - `compare` reads the finished chunk table against the table of rung 9's own bundle (RUNG9_OUTSIDE: the kind, destination, size and SHA-256 of each of the 48 chunks that are not the RAM disk;
   RUNG9_ROOT_ROWS_SHA256: the digest of the 291 rows of the RAM disk, whose text is the rows of files/r14-rung9.chunks of the session that lie in 0X88ED00000..0X8FDD00000, one `kind destination size
   sha256` line each) and refuses (PANEL_MISMATCH) unless the guest is rung 9's in every chunk but these: the HANDOFF chunk 0, PARAMS and the device tree, each rung 9's again to the SHA-256 once
   the words that follow the RAM disk's end are put back (the free-memory start, twice in chunk 0 and once in PARAMS, and the two /chosen properties that name the RAM disk, which the builder reads back
   by parsing the tree: PUT_BACK), and the chunks of the RAM disk, which are rung 9's root rows displaced by exactly 1 MiB (the GPT prefix is 1 MiB: bootmedia.ALIGN), but for the last row, which a data
   chunk of the ISC extends, between a prefix and a tail of the GPT. The medium is then read through its chunks and must be a valid GPT (probes/bootmedia.py inspect), its root partition must have the
   SHA-256 that the profile pins and its ISC partition the SHA-256 of the image that the builder read, so that nothing between the loader and the table has changed a byte.
 - `with_guest` puts the profile and that comparison into the `panel` dict that the words of rung 10 read (tools/sp11_stick_bundle_panel_rung10.py, ..._rung10d.py), with what the runner's page prints
   of this guest (the 16 capitals of its SHA-256, its size) and its label, which must be M1RUN-ACKPANIC-M-RUNG10.

Evidence: EXPERIMENTAL. RUNG9_OUTSIDE and the other pins are what the host build of rung 9's record and bundle gave: GUEST.DAT b7aeddff... is the file that the stick held after boot 28 (a file comparison,
not a run on the unit)."""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'probes'))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_panel_profile as pt  # noqa: E402
import sp11_stick_bundle_rung9 as r9b  # noqa: E402

Refused = ch.Refused
RUNG9_RECORD = 'dd2f39f4f327b22e803f00ae9bfac6e47fca1f592b178bb0259cd161c2007b6b'      # the SHA-256 of the record of rung 9 (v2, commit b455ce2), the one that boot 28 ran
RUNG9_GUEST = 'b7aeddff38fd6ad8f2d25173238530919fa2ff51ac04c64afb3792d661e23541'       # GUEST.DAT of rung 9's bundle, read back from the stick after boot 28 (1,926,621,440 bytes, 339 chunks)
RUNG9_GUEST_BYTES, RUNG9_CHUNK_COUNT = 1_926_621_440, 339
RUNG9_LABEL, RUNG10_LABEL = 'M1RUN-ACKPANIC-M-RUNG9', 'M1RUN-ACKPANIC-M-RUNG10'
ROOT_SHA256 = '7cab6d690cf173c194e846079484b524c09da8909607ac808a4f2b936d272416'       # the root image, as the profile pins it (probes/guestprofile.py)
ISC_BYTES, MEDIUM_BYTES = 1025 << 20, 2_939_158_528     # the form's ISC: the smallest three-role container (1 GiB and a MiB: tools/make-isc-fixture.py --size-mib 1025), and the GPT medium of probes/bootmedia.py that holds it after the root
RAMDISK_AT, ROOT_BYTES = r9b.RAMDISK_AT, r9b.ROOT_BYTES
GPT_PREFIX = 1 << 20                                  # the root partition starts here (probes/bootmedia.py ALIGN): rung 9's root rows are displaced by this
RUNG9_END = RAMDISK_AT + ROOT_BYTES                    # 0X8FDD00000: where rung 9's RAM disk ends, which the loader's free-memory words name
RUNG9_ROOT_ROWS, RUNG9_ROOT_ZERO_ROWS = 291, 143
RUNG9_ROOT_ROWS_SHA256 = '1d489e335de8f1019a131926adc7ff25f695a6fac5b1eacf9ba29b5ff5ee5f4c'    # of rows 48 to 337 (the first 290 of the 291, `kind dest size sha256` lines, LF-ended), dest as rung 9 has it
RUNG9_LAST_ROW = (8, 0x8FDCF0000, 65536, '8cd058a8ce848045a8004f00919ca0b06ac1b54c4f74a89857afd74f3a97401d')    # the 291st: data, which a data chunk of the ISC extends in rung 10's table
# Rung 9's 48 chunks that are not the RAM disk, `kind destination size sha256` (zero-fill chunks have an all-zero digest): HANDOFF kind 3, PARAMS 5, DEVICE_TREE 4.
RUNG9_OUTSIDE = """\
3 0x880000000 8192 41866b188609d6a1c4659fe54012b37db6241eb18bf81dfd6f68e7e2d48e099f
3 0x880003000 36864 376042f201008507c77a2c8128bacd0e6f0fe3ec664a42d1ec7de31a8d0dc69b
5 0x88000c000 9232 cf864325c55177bac1ec728c7ddddd9184b8156e00d9bf5f50103243dd9d7598
4 0x880010000 328080 3c817374700f953765b18264b623315b4813f3c5cad59c81f8bf8313a71f1665
1 0x880080000 14272592 1efb4c415ea0da87d015e7339a5aefbdf9824c2769eb3b94f802013de201d65b
1 0x880e1c850 571360 0000000000000000000000000000000000000000000000000000000000000000
1 0x880ea8030 385808 8798c3fc0720ea7ee2821de3ab690c4fc713f6b31cc997d8ec7c09315789ccdb
1 0x880f06340 163856 0000000000000000000000000000000000000000000000000000000000000000
1 0x880f2e350 7371072 3d13bf66bbdbee8da5ff4dcb544c89bcd835bbd3ea1a8e250c9cecc642af7241
1 0x881635c90 66432 0000000000000000000000000000000000000000000000000000000000000000
1 0x881646010 287792 43fa8f7f49465021550c58fba2d5005ce6dd34f3a0112e8da62e136e30262975
1 0x88168c440 535520 0000000000000000000000000000000000000000000000000000000000000000
1 0x88170f020 3140368 aa5e5d6a57d99d7096c9ea3b02c88c39f2d25eb7fe2049d2f9449e2fdb5ad4a4
1 0x881a0db30 484560 0000000000000000000000000000000000000000000000000000000000000000
1 0x881a84000 63097072 9a19927d977b7873a09f4f4d904c762ed486754b985240d54bf081c7d5959720
1 0x8856b08f0 489232 0000000000000000000000000000000000000000000000000000000000000000
1 0x885728000 183200 924dc083459d8bb1dbb760524000652d7b208464ed941c997fbe2450863b9e11
1 0x885754ba0 1438816 0000000000000000000000000000000000000000000000000000000000000000
1 0x8858b4000 16 2d3c875b83c3f44ce9e1820e9d9bfafe110aa9e582f48359d13b399f74732371
1 0x8858b4010 114672 0000000000000000000000000000000000000000000000000000000000000000
1 0x8858d0000 859648 c371a0b98149a3d4b2de8629afb7cc6aff29d09a7812f421c8c644badf97d5a0
1 0x8859a1e00 75712 0000000000000000000000000000000000000000000000000000000000000000
1 0x8859b45c0 197376 27ba38d7337bf2ac6d274dc4ec42a366aa746f6ecba8589a50bdb81dc621b00d
1 0x8859e48c0 117072 0000000000000000000000000000000000000000000000000000000000000000
1 0x885a01210 210800 94900bd476a882e35a4cd6cec9706eb3a5e8732e677c84b5dd049077e351dce6
1 0x885a34980 520592 0000000000000000000000000000000000000000000000000000000000000000
1 0x885ab3b10 79136 b0a978d7ccca2e5a1c82ef71e18b0bf708e13c5da101f5edcdce2dc327a482fe
1 0x885ac7030 92704 0000000000000000000000000000000000000000000000000000000000000000
1 0x885adda50 26174896 81c669b84fb1fd16796dfed3db5621ee17119a4f2736726a7a5ae15e3196f037
6 0x887400000 130032 4117c764ca46f763391161d1c063fed00cc6844884131ef5c2952ca4d83447e3
6 0x88741fbf0 493344 0000000000000000000000000000000000000000000000000000000000000000
6 0x887498310 458016 44046b32567bc5e6d49288d4980afe3f7096485d8e10340f59e64c13c23e0aaa
6 0x887508030 163792 0000000000000000000000000000000000000000000000000000000000000000
6 0x887530000 32 35685210db047d4a1b4f54df2156a8adadb54a7360b52715fac1f086f6b6c97a
6 0x887600000 589856 b746aaba8d3c0874911a53bf51f9591854783aa6e7b02f25494c2d782eecd246
7 0x887800000 26736 b728069a3e8f816a15c43ed1b7edbbc34f22905cc31d02cf454be70a4557bc98
7 0x887806870 3364752 0000000000000000000000000000000000000000000000000000000000000000
7 0x889800000 21600 42865e8b54ba6af4a8a33ef23b2202d5c9732f6b74ac9a09d755f75862fdf8d6
7 0x889805460 31886240 0000000000000000000000000000000000000000000000000000000000000000
7 0x88b66e000 6701056 7ff4482bc658f22b7b699780904325518bac75af599cb720979b6402582c4490
2 0x88d800000 3522560 c75c4154fe7c046c8b7ffdef3310197b30061982d5246a232abf114782b60eee
2 0x88db5c000 212992 0000000000000000000000000000000000000000000000000000000000000000
2 0x88db90000 98304 82a6e689384c08b1912faf9d994a3b0791b1f95185bbfed7d2bae0765dbd559b
7 0x88e800000 4342096 0000000000000000000000000000000000000000000000000000000000000000
7 0x88ec24150 16048 6799b1b7f84bca25a584daf756857009cc7be4194467905d96411b8ddf547e40
7 0x88ec28000 131072 0000000000000000000000000000000000000000000000000000000000000000
7 0x88ec48000 98304 5984bd5c94e7222843e972900f67f69a448b2dd4657d485c620b1466cc5933e1
7 0x88ec7c000 212992 0000000000000000000000000000000000000000000000000000000000000000
"""
CHUNKS9 = tuple((int(k), int(d, 16), int(s), h) for k, d, s, h in (line.split() for line in RUNG9_OUTSIDE.splitlines()))
HANDOFF_AT, PARAMS_AT, DT_AT = 0, 2, 3                # rows of RUNG9_OUTSIDE that rung 10's guest differs in, each rung 9's again once PUT_BACK is applied
PATCHED = (HANDOFF_AT, PARAMS_AT, DT_AT)
AVAIL_WORDS = (0x18, 0x1020)                          # offsets into HANDOFF chunk 0 of the two copies of the free-memory start (the SPTM handoff page and boot_args), a u64 each: the end of the RAM disk
PARAMS_END_WORD = 46                                  # the word of struct params that holds the same address (rung 8's guest has the end of its payload area there)
CHOSEN = '/device-tree/chosen'
BOOT_ARGS_AT = r9b.BOOT_ARGS_AT


def layout(isc_bytes):
    """probes/bootmedia.py's layout for the root and an ISC of `isc_bytes`, from the loader's own module (the compare then reads the medium through its chunks against it)."""
    import bootmedia
    return bootmedia.layout(ROOT_BYTES, isc_bytes)


class Medium:
    """The RAM disk of a guest as its chunks hold it, read in windows: a data chunk from its bytes or its source, a ZERO chunk as zeros. `rows` are table entries (kind, dest, size, flags) in
    ascending order, `chunks` the builder's chunks that carry the data, `base` where the medium starts and `size` how long it is."""

    def __init__(self, chunks, rows, base, size):
        by_dest = {c['dest']: c for c in chunks}
        self.base, self.size, self.parts = base, size, []
        cursor = base
        for row in rows:
            if row['dest'] != cursor:
                raise Refused('PANEL_MISMATCH', f'the chunks of the RAM disk leave a gap or overlap at {cursor:#x}')
            self.parts.append((row['dest'] - base, row['size'], None if row['flags'] & sb.CHUNK_ZERO else by_dest[row['dest']]))
            cursor += row['size']
        if cursor != base + size:
            raise Refused('PANEL_MISMATCH', f'the chunks of the RAM disk end at {cursor:#x}, the medium at {base + size:#x}')

    def read(self, offset, count):
        """`count` bytes of the medium from `offset`."""
        out, end = [], offset + count
        for start, size, chunk in self.parts:
            lo, hi = max(offset, start), min(end, start + size)
            if lo >= hi:
                continue
            if chunk is None:
                out.append(bytes(hi - lo))
            elif chunk.get('data') is not None:
                out.append(bytes(chunk['data'][lo - start:hi - start]))
            else:
                out.append(chunk['source'].read_at(chunk['offset'] + lo - start, hi - lo))
        return b''.join(out)

    def digest(self, offset, count, window=4 << 20):
        """The SHA-256 of `count` bytes from `offset`, by windows of 4 MiB: the medium is 3.2 GiB and is never held."""
        out = hashlib.sha256()
        for at in range(offset, offset + count, window):
            out.update(self.read(at, min(window, offset + count - at)))
        return out.hexdigest()


def _rows(table, lo, hi):
    return [c for c in table if lo <= c['dest'] < hi]


def _row_text(c, shift=0):
    return f'{c["kind"]} {c["dest"] - shift:#x} {c["size"]} {bytes(c["sha256"]).hex()}\n'


def put_back_dt(tree_bytes, medium_bytes):
    """The device tree chunk `tree_bytes` with its two RAM-disk properties put back to rung 9's values (the root alone), after the check that they are this medium's: the bytes then are rung 9's
    tree to the SHA-256, which is the proof that the two properties are the whole difference. Parsed with probes/appledt.py, the loader's own reader."""
    from appledt import DeviceTree
    try:
        tree = DeviceTree(bytes(tree_bytes))
        props = tree.nodes[CHOSEN]
        stated = (bytes(props['boot-ramdmg-size'].value), bytes(props['boot-ramdmg-extents'].value))
    except (KeyError, struct.error, ValueError, IndexError):
        raise Refused('PANEL_MISMATCH', 'the device tree does not parse to a /chosen node with boot-ramdmg-size and boot-ramdmg-extents') from None
    if stated != (struct.pack('<Q', medium_bytes), struct.pack('<QQ', RAMDISK_AT, medium_bytes)):
        raise Refused('PANEL_MISMATCH', 'the device tree\'s boot-ramdmg-size and -extents are not the GPT medium\'s')
    tree.put(CHOSEN, 'boot-ramdmg-size', struct.pack('<Q', ROOT_BYTES))
    tree.put(CHOSEN, 'boot-ramdmg-extents', struct.pack('<QQ', RAMDISK_AT, ROOT_BYTES))
    return tree.serialize()


def put_back(chunks, medium_bytes):
    """The three guest chunks that differ from rung 9's, put back to rung 9's bytes: {row index of RUNG9_OUTSIDE: bytes}. Each of the three words that name the end of the RAM disk must be this
    medium's end, and is put back to rung 9's end (0X8FDD00000)."""
    end = RAMDISK_AT + medium_bytes
    out = {}
    page = r9b._data(chunks, CHUNKS9[HANDOFF_AT][1])
    params = r9b._data(chunks, CHUNKS9[PARAMS_AT][1])
    dt = r9b._data(chunks, CHUNKS9[DT_AT][1])
    if page is None or params is None or dt is None:
        raise Refused('PANEL_MISMATCH', 'the guest has no HANDOFF, PARAMS or DEVICE_TREE chunk with data')
    fixed = bytearray(page)
    for at in AVAIL_WORDS:
        if struct.unpack_from('<Q', fixed, at)[0] != end:
            raise Refused('PANEL_MISMATCH', f'HANDOFF chunk 0 holds {struct.unpack_from("<Q", fixed, at)[0]:#x} at {at:#x}; the medium ends at {end:#x}')
        struct.pack_into('<Q', fixed, at, RUNG9_END)
    out[HANDOFF_AT] = bytes(fixed)
    fixed = bytearray(params)
    if struct.unpack_from('<Q', fixed, 8 * PARAMS_END_WORD)[0] != end:
        raise Refused('PANEL_MISMATCH', f'PARAMS word {PARAMS_END_WORD} is not the end of the medium, {end:#x}')
    struct.pack_into('<Q', fixed, 8 * PARAMS_END_WORD, RUNG9_END)
    out[PARAMS_AT] = bytes(fixed)
    out[DT_AT] = put_back_dt(dt, medium_bytes)
    return out


def compare(chunks, table, guest_bytes, nodes, isc):
    """What this guest is against rung 9's own bundle, from the finished chunks and the packed table: PANEL_MISMATCH unless the 48 chunks that are not the RAM disk are rung 9's (three of them put
    back), the RAM disk is the GPT medium that probes/bootmedia.py makes of the root and this ISC, and the root rows are rung 9's displaced by 1 MiB. `isc` is dict(bytes, sha256) of the image that
    the builder read. Returns the numbers the text says."""
    desc = layout(isc['bytes'])
    medium_bytes, root_at, (isc_at, isc_bytes) = desc['bytes'], desc['root'][0], desc['isc']
    if (isc['bytes'], medium_bytes) != (ISC_BYTES, MEDIUM_BYTES):
        raise Refused('PANEL_MISMATCH', f'the form\'s ISC is {ISC_BYTES} bytes (1025 MiB) and its medium {MEDIUM_BYTES}; this one is {isc["bytes"]} and {medium_bytes}')
    if root_at != GPT_PREFIX or desc['root'][1] != ROOT_BYTES:
        raise Refused('PANEL_MISMATCH', 'the loader\'s layout does not put the root at 1 MiB')
    inside = _rows(table, RAMDISK_AT, RAMDISK_AT + medium_bytes)
    rest = [c for c in table if c not in inside]
    if len(rest) != len(CHUNKS9) or any((c['kind'], c['dest']) != (k, d) for c, (k, d, _, _) in zip(rest, CHUNKS9)):
        raise Refused('PANEL_MISMATCH', f'the guest has {len(rest)} chunks outside the RAM disk, in the places of rung 9\'s {len(CHUNKS9)}, and the record says no others')
    fixed = put_back(chunks, medium_bytes)
    for i, (c, (kind, dest, size, digest)) in enumerate(zip(rest, CHUNKS9)):
        if i in fixed:
            if len(fixed[i]) != size or hashlib.sha256(fixed[i]).hexdigest() != digest:
                raise Refused('PANEL_MISMATCH', f'chunk {i} (kind {kind}, {dest:#x}) is not rung 9\'s once the end of the RAM disk is put back: it differs in more')
        elif (c['size'], bytes(c['sha256']).hex(), bool(c['flags'] & sb.CHUNK_ZERO)) != (size, digest, set(digest) == {'0'}):
            raise Refused('PANEL_MISMATCH', f'chunk {i} (kind {kind}, {dest:#x}) is not rung 9\'s')
    medium = Medium(chunks, inside, RAMDISK_AT, medium_bytes)
    if any(c['kind'] != sb.KIND_OTHER for c in inside):
        raise Refused('PANEL_MISMATCH', 'a chunk of the RAM disk is not of kind OTHER')
    import bootmedia
    try:
        if bootmedia.inspect(medium.read, medium_bytes) != desc:
            raise ValueError('the partitions are not the layout')
    except ValueError as error:
        raise Refused('PANEL_MISMATCH', f'the RAM disk is not the GPT medium of the root and the ISC: {error}') from None
    root_rows = _rows(table, RAMDISK_AT + root_at, RAMDISK_AT + root_at + ROOT_BYTES)
    if len(root_rows) != RUNG9_ROOT_ROWS or hashlib.sha256(''.join(_row_text(c, root_at) for c in root_rows[:-1]).encode()).hexdigest() != RUNG9_ROOT_ROWS_SHA256:
        raise Refused('PANEL_MISMATCH', f'the root partition is {len(root_rows)} chunks that are not rung 9\'s {RUNG9_ROOT_ROWS} displaced by 1 MiB (the first {RUNG9_ROOT_ROWS - 1} by digest)')
    if sum(1 for c in root_rows[:-1] if c['flags'] & sb.CHUNK_ZERO) != RUNG9_ROOT_ZERO_ROWS:
        raise Refused('PANEL_MISMATCH', f'the root partition has not the {RUNG9_ROOT_ZERO_ROWS} ZERO chunks of rung 9')
    kind, dest, size, digest = RUNG9_LAST_ROW
    last = root_rows[-1]
    if (last['kind'], last['dest'] - root_at, last['flags'] & sb.CHUNK_ZERO) != (kind, dest, 0) or last['size'] < size or medium.digest(last['dest'] - RAMDISK_AT, size) != digest:
        raise Refused('PANEL_MISMATCH', 'the last root chunk does not begin with rung 9\'s last chunk (65,536 bytes of data)')
    if medium.digest(root_at, ROOT_BYTES) != ROOT_SHA256:
        raise Refused('PANEL_MISMATCH', 'the root partition of the RAM disk is not the image that the profile pins')
    if medium.digest(isc_at, isc_bytes) != isc['sha256']:
        raise Refused('PANEL_MISMATCH', 'the ISC partition of the RAM disk is not the image that the builder read')
    prefix = [c for c in inside if c['dest'] < RAMDISK_AT + root_at]
    isc_rows = [c for c in inside if c['dest'] >= RAMDISK_AT + root_at + ROOT_BYTES]
    after = [c for c in isc_rows if c['dest'] >= RAMDISK_AT + isc_at + isc_bytes]
    inside_isc = [c for c in isc_rows if c not in after]
    data = lambda rows: sum(c['size'] for c in rows if not c['flags'] & sb.CHUNK_ZERO)       # noqa: E731
    zero = lambda rows: sum(1 for c in rows if c['flags'] & sb.CHUNK_ZERO)                   # noqa: E731
    dt = next(c['size'] for c in rest if c['kind'] == sb.KIND_DEVICE_TREE)
    return dict(count=len(table), outside=len(rest), put_back=len(fixed), nodes=(pt.BASE_NODES, nodes), guest_bytes=(RUNG9_GUEST_BYTES, guest_bytes), dt_bytes=(CHUNKS9[DT_AT][2], dt),
                avail=(RUNG9_END, RAMDISK_AT + medium_bytes), medium=dict(bytes=medium_bytes, dest=RAMDISK_AT, end=RAMDISK_AT + medium_bytes, chunks=len(inside), zero=zero(inside),
                                                                           data_bytes=data(inside), zero_bytes=medium_bytes - data(inside)),
                prefix=dict(chunks=len(prefix), data=data(prefix), bytes=root_at), root=dict(at=root_at, bytes=ROOT_BYTES, chunks=len(root_rows), zero=RUNG9_ROOT_ZERO_ROWS,
                                                                                            last_dest=last['dest'], last_size=last['size'], last_extra=last['size'] - size),
                isc=dict(at=isc_at, bytes=isc_bytes, chunks=len(inside_isc), zero=zero(inside_isc), data=data(inside_isc), sha256=isc['sha256']),
                tail=dict(at=isc_at + isc_bytes, bytes=medium_bytes - isc_at - isc_bytes, chunks=len(after), data=data(after)),
                table_room=sb.CHUNKS_MAX - len(table))


def with_guest(panel, profile, facts, chunks, table, guest, nodes, isc):
    """`panel` (the dict of read_panel) with what the words of rung 10 read: the profile, the comparison with rung 9's guest and what the page prints of this guest (the 16 capitals of its SHA-256,
    its size and the label). PANEL_MISMATCH unless the label is the one of this record."""
    if facts['build_label'] != RUNG10_LABEL:
        raise Refused('PANEL_MISMATCH', f'the pin label of rung 10 is {RUNG10_LABEL}, not {facts["build_label"]} (the facts\' build_label is RUNG10)')
    return dict(panel, profile=profile, isc=True, rung10=dict(diff=compare(chunks, table, len(guest), nodes, isc), label=facts['build_label'], guest_bytes=len(guest),
                                                               guest_sha16=(guest.sha256() if hasattr(guest, 'sha256') else hashlib.sha256(guest).hexdigest())[:16].upper()))
