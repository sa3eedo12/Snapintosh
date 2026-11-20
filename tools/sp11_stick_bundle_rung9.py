# SPDX-License-Identifier: MIT
"""The build side of the record of rung 9 (docs/SP11-STICK-ROOT.md; EXPERIMENTAL, host only, hardware NOT RUN): the first ROOT-MOUNT form. Rung 8's form (the acknowledging monitor, the reporter,
let_panic, the panel as the console, the profile quiet-rtc's clock) with the guest of tools/sp11_stick_bundle_root.py: 6,144 MiB of RAM and Apple's 1.86 GB BaseSystem root as the RAM disk of the
guest (the profile quiet-rtc-acm, mark M-), a box of 300 s with cpu_boot_timeout_secs=360, and the root-carry pair of the wrapper (docs/SP11-STICK-ROOT.md). What is here is the proof of what the guest is
against rung 8's own, and the numbers that the record's words are made from:

 - `compare` reads the finished chunk table against the table of rung 8's own bundle (RUNG8_CHUNKS: the kind, destination, size and SHA-256 of each of its 46 chunks, a host build of the rung 8 record
   whose GUEST.DAT is the one that rung 8's record builds, reproduced by the regeneration of the session) and refuses (PANEL_MISMATCH) unless the guest is rung 8's in every chunk but these: the two HANDOFF chunks,
   PARAMS and the device tree (each is rung 8's again, to the SHA-256, once the bytes that follow the RAM size, the box and the command line are put back: HANDOFF_BYTES, PARAMS_WORDS, and the device tree
   is held to the profile and the root's own /chosen properties by the builder's read-back, tools/sp11_stick_bundle_profile.py read_back), four chunks of the monitor's data and the page tables that
   are sized for the larger RAM (RESIZED), one that is not there any more (REMOVED) and three that are new (NEW); and every other byte of the guest is the root, as kind-OTHER chunks of the
   RAM-disk range, which is the profile's pinned root image in full (ROOT_CHUNKS, ROOT_ZERO_CHUNKS: the host census of the 143 stretches of 64 KiB or more of zeros, tools/sp11_stick_bundle_source.py).
   Every "differs from rung 8" sentence of the record is made from what `compare` returns.
 - `with_guest` puts the profile and that comparison into the `panel` dict that the words of rung 9 read (tools/sp11_stick_bundle_panel_rung9.py, ..._rung9d.py), with what the runner's page prints of
   this guest (the 16 capitals of its SHA-256, its size) and its label.

Evidence: EXPERIMENTAL. RUNG8_CHUNKS and the other pins are what the host build of rung 8's record and bundle gave (a file comparison, not a run on the unit)."""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_panel_profile as pt  # noqa: E402

Refused = ch.Refused
RUNG8_RECORD = '54c1dff710c7faebd962b8a8a8dc1f097d34ec14cfb1aad200e9629e036c717b'      # the SHA-256 of the record of rung 8 (v2, commit 4895ea2; v1 was ec9a9e84...c9724)
RUNG8_GUEST = 'e2df284b53dff262bed2f85afb809120c4460f82cf96e61a3fcd7cef75bd3f97'       # GUEST.DAT of rung 8's bundle (118,885,568 bytes, 46 chunks); its first 16 digits are what rung 8's INDEX=2 line shows
RUNG8_GUEST_BYTES = 118_885_568
RUNG8_LABEL = 'M1RUN-ACKPANIC-R-RUNG8'
RAMDISK_AT, ROOT_BYTES = 0x88ED00000, 0x6F000000        # the RAM disk of the guest: where the loader puts it (probes/xnuboot.py, after the payload area of the placement) and the root image's size
ROOT_CHUNKS, ROOT_ZERO_CHUNKS = 291, 143                # the root image cut as the builder cuts it: 291 chunks, 143 of them ZERO (stretches of 64 KiB or more); the image's own digest is pinned by the profile
# Rung 8's 46 chunks, `kind destination size sha256` (zero-fill chunks have an all-zero digest): HANDOFF kind 3, PARAMS 5, DEVICE_TREE 4.
RUNG8_CHUNKS = """\
3 0x880000000 8192 b8da18532a6fb15e6be44bc67cc466b69ba6be2355fee85e49bb4148db7026ad
3 0x880003000 36864 ff40ff9680ca8a4536ebcb889942d1a16429f645cac274bc65fb61c8115a328f
5 0x88000c000 9232 109f9f43c9ea4951dc389c8fc5c52c8f866f49edaefd7c657b2f9d93b585f056
4 0x880010000 327664 bc7fed4c54cd0f21bdcec41e4f234392fe293f7447fbe30b5f6c0c7b7d16b4da
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
7 0x887800000 25200 c7a2391ef306ed5f571b86b2d1d2dd59c5fd702dcc9c66d000011cbd43bc77ef
7 0x887806270 236944 0000000000000000000000000000000000000000000000000000000000000000
7 0x889800000 21600 42865e8b54ba6af4a8a33ef23b2202d5c9732f6b74ac9a09d755f75862fdf8d6
7 0x889805460 31886240 0000000000000000000000000000000000000000000000000000000000000000
7 0x88b66e000 442368 d8559c2c6fbde3f09df5be9a9a7b2703ffa617bcab171bb9b44c191c7bf53d88
2 0x88d800000 507904 801f8d50d53e911a0679b16fe5a7407fd4b9a1e9f337e07a5575d80a3dab03d6
7 0x88e800000 4342096 0000000000000000000000000000000000000000000000000000000000000000
7 0x88ec24150 16048 6799b1b7f84bca25a584daf756857009cc7be4194467905d96411b8ddf547e40
7 0x88ec28000 131072 0000000000000000000000000000000000000000000000000000000000000000
7 0x88ec48000 49152 5a0eeae59f2ec903c44ccb3a1e9b91ae94d2fe0d53da51aed2a00535409fc5f0
7 0x88ec7c000 212992 0000000000000000000000000000000000000000000000000000000000000000
"""
CHUNKS8 = tuple((int(k), int(d, 16), int(s), h) for k, d, s, h in (line.split() for line in RUNG8_CHUNKS.splitlines()))
HANDOFF_AT = (0, 1)                         # the two HANDOFF chunks of the table: the SPTM handoff page with boot_args (8,192 bytes) and the rest of the handoff area (36,864)
PARAMS_AT, DT_AT = 2, 3
PATCHED = (0, 1, 2, 3)                      # rung 8's chunks that rung 9's guest may differ in, whole: HANDOFF, PARAMS and the device tree
RESIZED = (35, 39, 40, 44)                  # rung 8's chunks that are in rung 9's table at the same kind and destination, at another size: the monitor's data and the page tables, sized for the larger RAM
REMOVED = (36,)                             # rung 8's zero chunk after chunk 35, which rung 9's table cuts at another place
NEW = ((7, 0x887806870, 3364752), (2, 0x88DB5C000, 212992), (2, 0x88DB90000, 98304))     # (kind, destination, size) of the chunks that rung 8's table has no row for, in table order
SAME = tuple(i for i in range(len(CHUNKS8)) if i not in PATCHED + RESIZED + REMOVED)
# The bytes of rung 8's HANDOFF chunks that rung 9's guest has otherwise: (offset into the chunk, rung 8's bytes), 38 bytes in 14 runs. The runs below offset 0x1000 are in the SPTM handoff page and
# the others in boot_args; that the ones at 0x1068, 0x10AF and 0x10B2 are the device tree's length and the command line's wait and tail (' acm_fastsim=1') is INFERRED from the values, and the
# fields of the rest are not named here (they follow the RAM size, the box and the memory ranges that the guest has): `compare` takes them as exactly the bytes that differ and nothing else.
HANDOFF_BYTES = (
    ((0x13, 'ff01'), (0x1B, '8e'), (0x1C3, '9b08'), (0x101B, '1700'), (0x1023, '8e'), (0x1068, 'f0ff04'), (0x10AF, '3132'), (0x10B2, '00' * 14), (0x147B, '1700')),
    ((0x1B, '1801'), (0x39, '6000'), (0x43, '9808'), (0x4B, '9808'), (0x51, '0c')),
)
# The PARAMS words (word index of struct params, rung 8's value): the box (word 24), the guest's end and the reserve above it (25, 26), the monitor's memory end (31), three of the frame table's ranges (45
# to 47), two virtual addresses (63, 64) that follow the RAM size, and the static trust cache's three words (1119 to 1121), which rung 8's guest does not have (zero) because it has no root.
PARAMS_WORDS = ((24, 0x78), (25, 0x898000000), (26, 0x3000000), (31, 0x89B000000), (45, 0x89B000000), (46, 0x88ED00000), (47, 0x898000000), (63, 0xFFFFFE01FC000000), (64, 0xFFFFFE0200000000),
                (1119, 0), (1120, 0), (1121, 0))
BOOT_ARGS_AT = 0x1000                       # in chunk 0: boot_args is the page after the SPTM handoff page (tools/sp11_stick_bundle_panel.py)


def _patched(data, runs):
    """`data` with each (offset, hex) run put back to rung 8's bytes."""
    out = bytearray(data)
    for offset, text in runs:
        value = bytes.fromhex(text)
        out[offset:offset + len(value)] = value
    return bytes(out)


def _patched_params(data):
    out = bytearray(data)
    for word, value in PARAMS_WORDS:
        struct.pack_into('<Q', out, 8 * word, value)
    return bytes(out)


def _data(chunks, dest):
    return next((c['data'] for c in chunks if c['dest'] == dest and c.get('data') is not None), None)


def compare(chunks, table, guest_bytes, nodes):
    """What this guest is against rung 8's own bundle, from the finished chunks and the packed table: PANEL_MISMATCH unless every chunk of rung 8's table is in this one at the same kind and destination
    and the same size and SHA-256, but the four of PATCHED (put back, they are rung 8's to the SHA-256), the four of RESIZED (another size), and REMOVED (not there), the three of NEW are the only others
    outside the root, and the root is the kind-OTHER chunks that cover the RAM-disk range exactly (ROOT_CHUNKS of them, ROOT_ZERO_CHUNKS of them ZERO). Returns the numbers the text says."""
    got = {(c['kind'], c['dest']): (c['size'], bytes(c['sha256']).hex(), bool(c['flags'] & sb.CHUNK_ZERO)) for c in table}
    root = [c for c in table if RAMDISK_AT <= c['dest'] < RAMDISK_AT + ROOT_BYTES]
    rest = [c for c in table if c not in root]
    if len(rest) != len(CHUNKS8) - len(REMOVED) + len(NEW):
        raise Refused('PANEL_MISMATCH', f'the guest has {len(rest)} chunks outside the root, the record says {len(CHUNKS8) - len(REMOVED) + len(NEW)}')
    for i, (kind, dest, size, digest) in enumerate(CHUNKS8):
        held = got.get((kind, dest))
        if i in REMOVED:
            if held is not None:
                raise Refused('PANEL_MISMATCH', f'rung 8\'s chunk {i} is still in the guest\'s table')
        elif held is None:
            raise Refused('PANEL_MISMATCH', f'rung 8\'s chunk {i} (kind {kind}, {dest:#x}) is not in the guest\'s table')
        elif i in SAME and held[:2] != (size, digest):
            raise Refused('PANEL_MISMATCH', f'chunk {i} (kind {kind}, {dest:#x}) is not rung 8\'s')
        elif i in RESIZED and held[0] == size:
            raise Refused('PANEL_MISMATCH', f'chunk {i} (kind {kind}, {dest:#x}) has rung 8\'s size, and the record says it is another')
    if [(c['kind'], c['dest'], c['size']) for c in rest if (c['kind'], c['dest']) not in {(k, d) for k, d, _, _ in CHUNKS8}] != list(NEW):
        raise Refused('PANEL_MISMATCH', 'the guest has chunks outside the root that are neither rung 8\'s nor the three that the record names')
    if (len(root), sum(1 for c in root if c['flags'] & sb.CHUNK_ZERO)) != (ROOT_CHUNKS, ROOT_ZERO_CHUNKS):
        raise Refused('PANEL_MISMATCH', f'the root is {len(root)} chunks ({sum(1 for c in root if c["flags"] & sb.CHUNK_ZERO)} ZERO), the record says {ROOT_CHUNKS} ({ROOT_ZERO_CHUNKS})')
    cursor = RAMDISK_AT
    for c in root:
        if c['kind'] != sb.KIND_OTHER or c['dest'] != cursor:
            raise Refused('PANEL_MISMATCH', 'the root chunks are not the kind-OTHER chunks that cover the RAM disk without a gap')
        cursor += c['size']
    if cursor != RAMDISK_AT + ROOT_BYTES:
        raise Refused('PANEL_MISMATCH', 'the root chunks do not cover the whole RAM disk')
    for at, runs in zip(HANDOFF_AT, HANDOFF_BYTES):
        data = _data(chunks, CHUNKS8[at][1])
        if data is None or len(data) != CHUNKS8[at][2] or hashlib.sha256(_patched(data, runs)).hexdigest() != CHUNKS8[at][3]:
            raise Refused('PANEL_MISMATCH', f'HANDOFF chunk {at} differs from rung 8\'s in more than the bytes that follow the RAM size, the device tree\'s length and the command line')
    params = _data(chunks, CHUNKS8[PARAMS_AT][1])
    if params is None or len(params) != CHUNKS8[PARAMS_AT][2] or hashlib.sha256(_patched_params(params)).hexdigest() != CHUNKS8[PARAMS_AT][3]:
        raise Refused('PANEL_MISMATCH', 'the PARAMS chunk differs from rung 8\'s in more than the box, the memory ranges that follow the RAM size and the trust cache\'s words')
    dt = got[(CHUNKS8[DT_AT][0], CHUNKS8[DT_AT][1])][0]
    sizes = {i: (CHUNKS8[i][2], got[(CHUNKS8[i][0], CHUNKS8[i][1])][0]) for i in RESIZED}
    root_data = sum(c['size'] for c in root if not c['flags'] & sb.CHUNK_ZERO)
    return dict(count=len(table), same=SAME, patched=PATCHED, resized=sizes, removed=REMOVED, new=NEW, outside=len(rest), handoff_runs=sum(map(len, HANDOFF_BYTES)),
                handoff_bytes=sum(len(bytes.fromhex(r[1])) for runs in HANDOFF_BYTES for r in runs), params_words=len(PARAMS_WORDS) - 3, trust_words=3,
                dt_bytes=(CHUNKS8[DT_AT][2], dt), nodes=(pt.BASE_NODES, nodes), guest_bytes=(RUNG8_GUEST_BYTES, guest_bytes),
                root=dict(chunks=len(root), zero=ROOT_ZERO_CHUNKS, data_bytes=root_data, zero_bytes=ROOT_BYTES - root_data, dest=RAMDISK_AT, size=ROOT_BYTES,
                          end=RAMDISK_AT + ROOT_BYTES), table_room=sb.CHUNKS_MAX - len(table))


def with_guest(panel, profile, facts, chunks, table, guest, nodes):
    """`panel` (the dict of read_panel) with what the words of rung 9 read: the profile, the comparison with rung 8's guest and what the page prints of this guest (the 16 capitals of its SHA-256, its
    size and the label)."""
    return dict(panel, profile=profile, rung9=dict(diff=compare(chunks, table, len(guest), nodes), label=facts['build_label'], guest_sha16=(guest.sha256() if hasattr(guest, 'sha256') else hashlib.sha256(guest).hexdigest())[:16].upper(),
                                                   guest_bytes=len(guest)))
