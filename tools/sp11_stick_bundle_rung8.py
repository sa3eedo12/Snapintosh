# SPDX-License-Identifier: MIT
"""The build side of the record of rung 8 (docs/SP11-M1-GUEST-PROFILES.md sections 5 and 7, K16; EXPERIMENTAL, host only, hardware NOT RUN): rung 7's form (the acknowledging pair with reporter v3,
let_panic, a box of 120 s, cpu_boot_timeout_secs=120) with the guest profile `quiet-rtc` in place of `quiet`. The profile has the quiet profile's device tree and boot_args page byte for byte and binds
the PMU's real-time clock at nub-spmi0: one of the 120 device models of PARAMS changes three words. What is here is the proof of that, and the numbers that the record's words are made from:

 - `compare` reads the finished chunk table against the table of rung 7's own bundle (RUNG7_CHUNKS: the kind, destination, size and SHA-256 of each of its 46 chunks, a host build of the committed
   rung 7 tree whose GUEST.DAT is the one that boot 27 ran, reproduced by the regeneration of the session) and refuses (PANEL_MISMATCH) unless exactly chunk 2 (PARAMS) differs. For PARAMS it
   locates the one clock model (kind SPMI_PMU_RTC_V1, base 0X308700000), puts its three words back to rung 7's (the empty bus: kind SPMI_GEN3_EMPTY_V1, config[3] and config[4] the queue depth,
   256) and requires rung 7's SHA-256 for the chunk: so those three words are the whole difference. Every "differs from rung 7" sentence of the record is made from what `compare` returns.
 - `with_guest` puts the profile and that comparison into the `panel` dict that the words of rung 8 read (tools/sp11_stick_bundle_panel_rung8.py, ..._rung8d.py), with what the runner's page prints
   of this guest (the 16 capitals of its SHA-256, its size) and its label.

Evidence: EXPERIMENTAL. RUNG7_CHUNKS and the other pins are what the host build of rung 7's record and bundle gave (a file comparison, not a run on the unit)."""
import datetime
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'probes'))
import devicemodel  # noqa: E402
import guestprofile_m1 as menu  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_panel_profile as pt  # noqa: E402

Refused = ch.Refused
RUNG7_RECORD = 'e42f451de33c2d44ec86f10036627aa129f00e6ae1c3edf526231fd42adb3b01'      # the SHA-256 of the record of rung 7 (commit 5f2f8dd, the text of its second edition), the run of boot 27
RUNG7_GUEST = '25f74cb8cedb5e206fb051e2180c843929dd60feb5f4f50bf82b9a8586b23ae8'       # GUEST.DAT of rung 7's bundle (118,885,568 bytes, 46 chunks); its first 16 digits are boot 27's INDEX=2 line
RUNG7_GUEST_BYTES = 118_885_568
RUNG7_LABEL = 'M1RUN-ACKPANIC-Q-RUNG7'
# Rung 7's 46 chunks, `kind destination size sha256` (zero-fill chunks have an all-zero digest): HANDOFF kind 3, PARAMS 5, DEVICE_TREE 4.
RUNG7_CHUNKS = """\
3 0x880000000 8192 b8da18532a6fb15e6be44bc67cc466b69ba6be2355fee85e49bb4148db7026ad
3 0x880003000 36864 ff40ff9680ca8a4536ebcb889942d1a16429f645cac274bc65fb61c8115a328f
5 0x88000c000 9232 b40f643e01b5084ec8e729bcb826b095ca2a803405915c043acb9cabb2d00e2d
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
CHUNKS7 = tuple((int(k), int(d, 16), int(s), h) for k, d, s, h in (line.split() for line in RUNG7_CHUNKS.splitlines()))
DIFFERING = (2,)                            # the one chunk that rung 8's guest may differ in: PARAMS (chunk 2)
PARAMS_AT = 2
CLOCK_BASE = 0x308700000                    # the SPMI controller of nub-spmi0, the one whose PMU holds the clock (probes/devicemodel.py spmi_models pins it)
EMPTY_BUS_WORDS = (devicemodel.SPMI_GEN3_EMPTY_V1, 256, 256)      # kind, config[3], config[4] of that model in rung 7's PARAMS: the empty bus and its queue depth twice
assert EMPTY_BUS_WORDS[0] == 9, 'the empty bus model has another kind number'
WORD_OFFSETS = (('kind', 0), ('config[3] (the seed)', 48), ('config[4] (the contract)', 56))      # in struct device_model {kind, base, span, config[5]} (compat/device.h)
SEED_TEXT = datetime.datetime.fromtimestamp(menu.RTC_EPOCH_US // 1_000_000, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def clock_model(params):
    """(index, offset in the chunk, span) of the one device model of PARAMS that is the clock (kind SPMI_PMU_RTC_V1) at CLOCK_BASE, or PANEL_MISMATCH: a clock anywhere else, a second clock or none."""
    count = struct.unpack_from('<Q', params, m1.MODEL_COUNT_AT)[0]
    if not 0 < count <= m1.MODEL_MAX or len(params) < m1.MODELS_AT + count * m1.MODEL_BYTES:
        raise Refused('PANEL_MISMATCH', 'the PARAMS chunk has no device model list that this comparison can read')
    found = []
    for i in range(count):
        at = m1.MODELS_AT + i * m1.MODEL_BYTES
        kind, base, span = struct.unpack_from('<3Q', params, at)
        if kind == devicemodel.SPMI_PMU_RTC_V1 or base == CLOCK_BASE:
            found.append((i, at, kind, base, span))
    if len(found) != 1 or found[0][2:4] != (devicemodel.SPMI_PMU_RTC_V1, CLOCK_BASE):
        raise Refused('PANEL_MISMATCH', f'the PARAMS chunk holds {len(found)} clock or controller models at 0X{CLOCK_BASE:X}, and the record says one clock model there')
    index, at, _, _, span = found[0]
    return index, at, span


def _patched_params(data):
    """The PARAMS chunk with the clock model's three words put back to rung 7's: the empty bus. Returns (the bytes, the clock model's index, offset and span, its three words as this guest has them)."""
    params = bytearray(data)
    index, at, span = clock_model(params)
    words = tuple(struct.unpack_from('<Q', params, at + off)[0] for _, off in WORD_OFFSETS)
    for (_, off), word in zip(WORD_OFFSETS, EMPTY_BUS_WORDS):
        struct.pack_into('<Q', params, at + off, word)
    return bytes(params), index, at, span, words


def compare(chunks, table, guest_bytes, nodes):
    """What this guest is against rung 7's own bundle, from the finished chunks and the packed table: PANEL_MISMATCH unless the table has rung 7's 46 chunks and exactly chunk 2 (PARAMS) differs,
    and PARAMS is rung 7's but for the clock model's three words (rung 7's SHA-256 after they are put back), which hold the seed of the menu and the clock model's contract word. Returns the
    numbers the text says: the count, the differing indices, the PARAMS chunk's size, the clock model's index, offset, span and three words (rung 7's and this guest's), the seed as a date, the
    device tree's bytes and nodes (the same as rung 7's) and the size of GUEST.DAT."""
    got = [(c['kind'], c['dest'], c['size'], bytes(c['sha256']).hex()) for c in table]
    if len(got) != len(CHUNKS7):
        raise Refused('PANEL_MISMATCH', f'the guest has {len(got)} chunks, rung 7\'s has {len(CHUNKS7)}')
    differing = tuple(i for i, (g, w) in enumerate(zip(got, CHUNKS7)) if g != w)
    if differing != DIFFERING:
        raise Refused('PANEL_MISMATCH', f'the guest differs from rung 7\'s in chunks {list(differing)}; the record says chunk {DIFFERING[0]} (PARAMS) and no other')
    data = next((c['data'] for c in chunks if c['dest'] == got[PARAMS_AT][1] and c.get('data') is not None), None)
    if data is None or len(data) != got[PARAMS_AT][2]:
        raise Refused('PANEL_MISMATCH', 'the PARAMS chunk is absent, zero-filled or not the size that the table says')
    patched, index, at, span, words = _patched_params(data)
    if hashlib.sha256(patched).hexdigest() != CHUNKS7[PARAMS_AT][3]:
        raise Refused('PANEL_MISMATCH', 'the PARAMS chunk differs from rung 7\'s in more than the three words of the clock model')
    if words != (devicemodel.SPMI_PMU_RTC_V1, menu.RTC_EPOCH_US, devicemodel.PMU_RTC_CONTRACT):
        raise Refused('PANEL_MISMATCH', 'the clock model\'s words are not the clock seeded at the menu\'s instant with the clock model\'s contract word')
    return dict(count=len(got), differing=differing, params_bytes=got[PARAMS_AT][2], model=index, model_at=at, model_span=span, words=(EMPTY_BUS_WORDS, words), seed=SEED_TEXT,
                dt_bytes=(CHUNKS7[3][2], got[3][2]), nodes=(pt.BASE_NODES, nodes), guest_bytes=(RUNG7_GUEST_BYTES, guest_bytes))


def with_guest(panel, profile, facts, chunks, table, guest, nodes):
    """`panel` (the dict of read_panel) with what the words of rung 8 read: the profile, the comparison with rung 7's guest and what the page prints of this guest (the 16 capitals of its SHA-256, its
    size and the label)."""
    digest = hashlib.sha256(guest).hexdigest()
    return dict(panel, profile=profile, rung8=dict(diff=compare(chunks, table, len(guest), nodes), label=facts['build_label'], guest_sha16=digest[:16].upper(), guest_bytes=len(guest)))
