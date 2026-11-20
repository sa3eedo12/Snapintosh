# SPDX-License-Identifier: MIT
"""The build side of the record of rung 7 (docs/SP11-M1-GUEST-PROFILES.md section 5, K15; EXPERIMENTAL, host only, hardware NOT RUN): rung 6's form (the acknowledging monitor with reporter
v3, let_panic, a box of 120 s) with the command line's cpu_boot_timeout_secs=120 in place of rung 6's 90 and the guest profile `quiet`. What is here is the proof that the guest is rung 6's but
for the device tree and the two fields of boot_args that follow from it and from the word, and the numbers that the record's words are made from:

 - `compare` reads the finished chunk table against the table of rung 6's own bundle (RUNG6_CHUNKS: the kind, destination, size and SHA-256 of each of its 46 chunks, a host build of the
   committed rung 6 tree, reproduced by the regeneration of the session) and refuses (PANEL_MISMATCH) unless exactly chunk 0 (HANDOFF: the page of boot_args) and chunk 3 (DEVICE_TREE)
   differ; PARAMS, which holds the box, let_panic and the 120 models, is then rung 6's byte for byte. For chunk 0 it puts the two fields that may differ back to rung 6's values (the length
   of the device tree and the command line) and requires rung 6's SHA-256: so those two fields are the whole difference. Every "differs from rung 6" sentence of the record is made from
   what `compare` returns, and the count of the published nodes is the builder's own, read back from the chunk (tools/sp11_stick_bundle_profile.py read_back).
 - `with_guest` puts the profile and that comparison into the `panel` dict that the words of rung 7 read (tools/sp11_stick_bundle_panel_rung7.py, ..._rung7d.py), with what the runner's page
   prints of this guest (the 16 capitals of its SHA-256, its size) and its label.

Evidence: EXPERIMENTAL. RUNG6_CHUNKS and the other pins are what the host build of rung 6's record and bundle gave (a file comparison, not a run on the unit)."""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_panel_profile as pt  # noqa: E402

Refused = ch.Refused
RUNG6_RECORD = 'd9d79c87cccc8c39f415fcee8a9cc900b8bb1bfe668fef9101c9fec9555a5cda'      # the SHA-256 of the record of rung 6 (commit 0f6174d), the run of boot 26
RUNG6_GUEST = '1dd2e9ac94fdb7413f1e5d222c54b2c20cd635ee34f3211a2199a4db97ea57a2'       # GUEST.DAT of rung 6's bundle (118,908,480 bytes, 46 chunks); its first 16 digits are boot 26's INDEX=2 line
RUNG6_GUEST_BYTES = 118_908_480
RUNG6_LINE = '-v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpu_boot_timeout_secs=90'    # rung 6's command line, as boot_args holds it
RUNG6_DT_LENGTH = 350_572                   # the 32-bit length word of rung 6's boot_args (deviceTreeLength, +0x68): the length of its device tree, 4 bytes under its chunk's 350,576
BOOT_ARGS_AT, DT_LENGTH_AT, CMDLINE_AT, CMDLINE_BYTES = 0x1000, 0x68, 0x6C, 1024        # in chunk 0: boot_args is the page after the SPTM handoff page (tools/sp11_stick_bundle_panel.py)
# Rung 6's 46 chunks, `kind destination size sha256` (zero-fill chunks have an all-zero digest): HANDOFF kind 3, PARAMS 5, DEVICE_TREE 4.
RUNG6_CHUNKS = """\
3 0x880000000 8192 db1f5262b977a6a9a0289424ff0637cc384df45897cd8b9e46d07080b1a1814a
3 0x880003000 36864 ff40ff9680ca8a4536ebcb889942d1a16429f645cac274bc65fb61c8115a328f
5 0x88000c000 9232 b40f643e01b5084ec8e729bcb826b095ca2a803405915c043acb9cabb2d00e2d
4 0x880010000 350576 32c4e3f04e7038fef7c7cbe038f48ac94619d82d40cf777e4152d525f85d610a
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
CHUNKS6 = tuple((int(k), int(d, 16), int(s), h) for k, d, s, h in (line.split() for line in RUNG6_CHUNKS.splitlines()))
DIFFERING = (0, 3)                          # the chunks that rung 7's guest may differ in: HANDOFF (chunk 0) and DEVICE_TREE (chunk 3)
PARAMS_AT = 2                               # the PARAMS chunk of the table


def _patched_handoff(data):
    """The HANDOFF chunk with the two fields that may differ put back to rung 6's values: the length of the device tree and the command line (NUL-filled as the loader fills it)."""
    page = bytearray(data)
    page[BOOT_ARGS_AT + DT_LENGTH_AT:BOOT_ARGS_AT + DT_LENGTH_AT + 4] = struct.pack('<I', RUNG6_DT_LENGTH)
    page[BOOT_ARGS_AT + CMDLINE_AT:BOOT_ARGS_AT + CMDLINE_AT + CMDLINE_BYTES] = RUNG6_LINE.encode().ljust(CMDLINE_BYTES, b'\0')
    return bytes(page)


def compare(chunks, table, guest_bytes, nodes):
    """What this guest is against rung 6's own bundle, from the finished chunks and the packed table: PANEL_MISMATCH unless the table has rung 6's 46 chunks and exactly chunks 0 and 3
    differ, and chunk 0 is rung 6's but for the device tree's length word and the command line (rung 6's SHA-256 after those two are put back). Returns the numbers the text says: the
    count, the differing indices, whether PARAMS is the same, the device tree's bytes and nodes, the length words, the command lines' last words and the size of GUEST.DAT, each from
    rung 6's pins and this build."""
    got = [(c['kind'], c['dest'], c['size'], bytes(c['sha256']).hex()) for c in table]
    if len(got) != len(CHUNKS6):
        raise Refused('PANEL_MISMATCH', f'the guest has {len(got)} chunks, rung 6\'s has {len(CHUNKS6)}')
    differing = tuple(i for i, (g, w) in enumerate(zip(got, CHUNKS6)) if g != w)
    if differing != DIFFERING:
        raise Refused('PANEL_MISMATCH', f'the guest differs from rung 6\'s in chunks {list(differing)}; the record says chunks {list(DIFFERING)} (HANDOFF and DEVICE_TREE) and no other')
    page = next((c['data'] for c in chunks if c['dest'] == got[0][1] and c.get('data') is not None), None)
    if page is None or len(page) != got[0][2] or hashlib.sha256(_patched_handoff(page)).hexdigest() != CHUNKS6[0][3]:
        raise Refused('PANEL_MISMATCH', 'the HANDOFF chunk differs from rung 6\'s in more than the device tree\'s length word and the command line')
    return dict(count=len(got), differing=differing, params_same=got[PARAMS_AT] == CHUNKS6[PARAMS_AT], dt_bytes=(CHUNKS6[3][2], got[3][2]), nodes=(pt.BASE_NODES, nodes),
                dt_length=(RUNG6_DT_LENGTH, int.from_bytes(page[BOOT_ARGS_AT + DT_LENGTH_AT:BOOT_ARGS_AT + DT_LENGTH_AT + 4], 'little')), guest_bytes=(RUNG6_GUEST_BYTES, guest_bytes))


def with_guest(panel, profile, facts, chunks, table, guest, nodes):
    """`panel` (the dict of read_panel) with what the words of rung 7 read: the profile, the comparison with rung 6's guest and what the page prints of this guest (the 16 capitals of its
    SHA-256, its size and the label).     The profile quiet-rtc is rung 8's: its guest is compared with rung 7's own (tools/sp11_stick_bundle_rung8.py); quiet-rtc-acm is rung 9's, compared with rung 8's (tools/sp11_stick_bundle_rung9.py)."""
    if profile == ch.RUNG8_PROFILE:
        import sp11_stick_bundle_rung8 as r8b
        return r8b.with_guest(panel, profile, facts, chunks, table, guest, nodes)
    if profile == ch.RUNG9_PROFILE:
        import sp11_stick_bundle_rung9 as r9b
        return r9b.with_guest(panel, profile, facts, chunks, table, guest, nodes)
    digest = hashlib.sha256(guest).hexdigest()
    return dict(panel, profile=profile, rung7=dict(diff=compare(chunks, table, len(guest), nodes), label=facts['build_label'], guest_sha16=digest[:16].upper(), guest_bytes=len(guest)))
