#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the Surface Aggregator Module frame codec (compat/sam-frame.{h,c}, tools/sam_frames.py).

    python3 tools/test-sam-frames.py                  # everything, about a minute
    python3 tools/test-sam-frames.py --skip-mutants   # everything except the seeded-bug check (seconds)

What a pass proves: the C codec agrees with an independent Python implementation and with a third, model
implementation inside the native driver, on hand-written vectors, on negative cases (one per rejection reason),
on every chunking of the same stream, and on 200,000 deterministic mutated frames run under AddressSanitizer
and UndefinedBehaviorSanitizer; and the tests really can fail, because each of a list of seeded bugs is caught.
What it does not prove: anything about a real Surface Aggregator Module. The vectors are computed, not captured
(UNKNOWN until the G1 spike reads a real link at gate X9), and the policy for rejecting frames is this
project's own (docs/SP11-INPUT-STUDY.md section 4). No hardware, no network, nothing outside build/.
"""
import concurrent.futures
import os
import random
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import sam_frames as ref  # noqa: E402

OUT = ROOT / 'build' / 'sam-frames'
FILES = ('compat/sam-frame.h', 'compat/sam-frame.c', 'tools/sam_frames.py', 'tools/test-sam-frames.c',
         'tools/test-sam-frames.py', 'docs/SP11-INPUT-STUDY.md')
CFLAGS = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
          '-fno-sanitize-recover=all']
# The flags the monitor's units are built with (probes/run.py FLAGS), minus include paths.
MONITOR_FLAGS = ['--target=aarch64-unknown-none-elf', '-ffreestanding', '-fno-pic', '-mgeneral-regs-only', '-O1',
                 '-Wall', '-Wextra', '-Werror', '-fno-stack-protector', '-mcmodel=tiny']
LLVM_BIN = Path('/opt/homebrew/opt/llvm/bin')


def tool(name):
    return shutil.which(name) or (str(LLVM_BIN / name) if (LLVM_BIN / name).exists() else None)


CC = os.environ.get('CC') or tool('clang') or shutil.which('cc')
FUZZ_CASES = 150000
SKIP_MUTANTS = '--skip-mutants' in sys.argv
if SKIP_MUTANTS:
    sys.argv.remove('--skip-mutants')


def build_native(directory, source_dir):
    """Compiles the native driver against the codec found in source_dir; returns the binary path."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / 'sam-frames-test'
    command = [CC, *CFLAGS, f'-I{source_dir}', str(ROOT / 'tools/test-sam-frames.c'),
               str(source_dir / 'sam-frame.c'), '-o', str(binary)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('build failed:\n' + result.stderr)
    return binary


def run(binary, *args, stdin=None, timeout=300):
    return subprocess.run([str(binary), *args], input=stdin, capture_output=True, text=True, timeout=timeout)


# ---- deterministic corpora for the differential test -------------------------------------------------------

def garbage(rng, count):
    return bytes(rng.choice((0xAA, 0x55, rng.randrange(256))) for _ in range(count))


def random_frame(rng, max_payload):
    ftype = rng.choice(ref.KNOWN_TYPES)
    payload = b''
    if ftype in ref.DATA_TYPES:
        size = min(max_payload, rng.choice((1, 1, 2, 3, 8, 16, 24, rng.randint(1, 300))))
        payload = bytes(rng.choice((0xAA, 0x55, rng.randrange(256))) if rng.random() < 0.15 else rng.randrange(256)
                        for _ in range(size))
    return ref.build_frame(ftype, rng.randrange(256), payload)


def fix_header_crc(data, off):
    if off + 8 <= len(data):
        data[off + 6:off + 8] = ref.le16(ref.crc16(bytes(data[off + 2:off + 6])))


def mutate(rng, data, off):
    data = bytearray(data)
    op = rng.randrange(13)
    if op == 0 and data:
        data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
    elif op == 1 and data:
        data[rng.randrange(len(data))] = rng.randrange(256)
    elif op == 2 and data:
        del data[rng.randrange(len(data)):]
    elif op == 3 and data:
        del data[rng.randrange(len(data))]
    elif op == 4:
        data.insert(rng.randrange(len(data) + 1), rng.randrange(256))
    elif op == 5:
        data += garbage(rng, rng.randint(1, 12))
    elif op == 6:
        data[:0] = garbage(rng, rng.randint(1, 8))
    elif op == 7:
        data += bytes(data)
    elif op == 8 and off + 8 <= len(data):
        data[off + 3:off + 5] = ref.le16(rng.randrange(310))
        fix_header_crc(data, off)
    elif op == 9 and off + 8 <= len(data):
        data[off + 2] = rng.randrange(256)
        fix_header_crc(data, off)
    elif op == 10 and off + 10 <= len(data):
        size = data[off + 3] | (data[off + 4] << 8)
        if size and off + 10 + size <= len(data):
            data[off + 8 + rng.randrange(size)] ^= rng.randint(1, 255)
            data[off + 8 + size:off + 10 + size] = ref.le16(ref.crc16(bytes(data[off + 8:off + 8 + size])))
    elif op == 11 and off + 8 <= len(data):
        data[off + 6:off + 8] = ref.le16(rng.randrange(65536))
    elif len(data) > 1:
        i = rng.randrange(len(data) - 1)
        data[i], data[i + 1] = data[i + 1], data[i]
    return bytes(data)


def make_streams(rng, count):
    streams = []
    for _ in range(count):
        max_payload = rng.choice((1, 2, 8, 64, 300, 4086))
        data = b''
        offsets = []
        for index in range(rng.randint(1, 4)):
            if index and rng.random() < 0.35:
                data += garbage(rng, rng.randint(1, 6))
            offsets.append(len(data))
            data += random_frame(rng, max_payload)
        for _ in range(rng.choice((0, 1, 1, 2, 3))):
            data = mutate(rng, data, rng.choice(offsets))
        streams.append((max_payload, data))
    return streams


def hexarg(data):
    return bytes(data).hex() or '-'


def build_differential(seed=0x5A4D):
    """Returns (stdin text, list of expected output blocks); every block ends where the driver prints END."""
    rng = random.Random(seed)
    lines, expected = [], []

    def add(line, block):
        lines.append(line)
        expected.append(block)

    for max_payload, data in make_streams(rng, 2500):
        block = ref.canonical(*ref.parse_stream(data, max_payload))
        for chunk in (0, 1, 3, rng.choice((2, 5, 11, 64))):
            add(f'parse {max_payload} {chunk} {rng.choice((0, 0, 5, 64))} {hexarg(data)}', block)
        for part in (data[:rng.randrange(len(data) + 1)], data):
            max_exact = rng.choice((1, 8, 300, 4086))
            try:
                ftype, seq, payload = ref.decode_exact(part, max_exact)
                result = 'OK %02x %02x %s' % (ftype, seq, payload.hex() or '-')
            except ref.FrameError as error:
                result = f'ERR {error}'
            add(f'decode {max_exact} {hexarg(part)}', result)
    for _ in range(1500):
        ftype = rng.choice((0x00, 0x04, 0x40, 0x80, rng.randrange(256)))
        seq = rng.randrange(256)
        payload = bytes(rng.randrange(256) for _ in range(rng.choice((0, 0, 1, 5, 40))))
        try:
            result = 'OK ' + ref.build_frame(ftype, seq, payload).hex()
        except ref.FrameError as error:
            result = f'ERR {error}'
        add(f'build {ftype:x} {seq:x} {hexarg(payload)}', result)
    for _ in range(1500):
        ftype = rng.choice((0x00, 0x80, 0x40, 0x04, 0x13))
        seq = rng.randrange(256)
        tc, tid, sid, iid, cid = (rng.randrange(256) for _ in range(5))
        rqid = rng.randrange(65536)
        data = bytes(rng.randrange(256) for _ in range(rng.choice((0, 0, 1, 8, 33))))
        try:
            result = 'OK ' + ref.build_command(ftype, seq, tc, tid, sid, iid, rqid, cid, data).hex()
        except ref.FrameError as error:
            result = f'ERR {error}'
        add('cmdbuild %x %x %d %d %d %d %x %d %s' % (ftype, seq, tc, tid, sid, iid, rqid, cid, hexarg(data)), result)
    for _ in range(1500):
        payload = bytes(rng.randrange(256) for _ in range(rng.choice((0, 3, 7, 8, 9, 20))))
        if payload and rng.random() < 0.7:
            payload = b'\x80' + payload[1:]
        try:
            fields, data = ref.decode_command(payload)
            result = 'OK %d %d %d %d %d %d %s' % (*fields, data.hex() or '-')
        except ref.FrameError as error:
            result = f'ERR {error}'
        add(f'cmddecode {hexarg(payload)}', result)
    for _ in range(1200):
        buf = bytes(rng.randrange(256) for _ in range(rng.choice((0, 1, 4, 9, 64, 300))))
        assert ref.crc16(buf) == ref.crc16_hqx(buf)
        add(f'crc {hexarg(buf)}', 'crc=%04x' % ref.crc16(buf))
    for rqid in [0, 1, 37, 38, 39, 40, 0x7FFF, 0x8000, 0xFFFE, 0xFFFF] + [rng.randrange(65536) for _ in range(300)]:
        add('rqid %x' % rqid, 'event=%d next=%04x' % (ref.rqid_is_event(rqid), ref.rqid_next_request(rqid)))
    return '\n'.join(lines) + '\n', expected


def differential_mismatches(binary, corpus):
    stdin, expected = corpus
    result = run(binary, 'batch', stdin=stdin)
    if result.returncode:
        return [f'driver exited {result.returncode}: {result.stderr[-400:]}']
    blocks = result.stdout.split('END\n')
    if blocks and blocks[-1] == '':
        blocks.pop()
    if len(blocks) != len(expected):
        return [f'{len(blocks)} output blocks for {len(expected)} commands']
    lines = stdin.split('\n')
    return [f'{lines[i][:120]!r}\n  driver:    {got.rstrip()!r}\n  reference: {want!r}'
            for i, (got, want) in enumerate(zip(blocks, expected)) if got.rstrip('\n') != want][:5]


# ---- mutants -----------------------------------------------------------------------------------------------

C = 'sam-frame.c'
H = 'sam-frame.h'
# (name, file, text to replace, replacement). Every `old` must occur exactly once, so a mutant that silently
# stops applying is itself a failure.
MUTANTS = (
    ('skip the payload CRC', C, 'if (sam_crc16_update(SAM_CRC_INIT, buf + header_end, len) != get_le16(buf + header_end + len))',
     'if (0)'),
    ('accept a truncated message in decode_exact', C, 'if (rc == SAM_NEED_MORE)\n        return SAM_ERR_TRUNCATED;',
     'if (rc == SAM_NEED_MORE)\n        return SAM_OK;'),
    ('off by one in the length bound', C, 'if (len > max_payload)', 'if (len >= max_payload)'),
    ('wrong CRC initial value', H, '#define SAM_CRC_INIT 0xFFFFu', '#define SAM_CRC_INIT 0x0000u'),
    ('never resynchronise after a rejection', C, 'skip_bytes(p, SAM_SYN_LEN);\n    }\n}',
     'skip_bytes(p, p->fill - p->head);\n    }\n}'),
    ('count the wrong rejection reason', C, 'case SAM_ERR_HEADER_CRC: p->stats.rej_header_crc++; break;',
     'case SAM_ERR_HEADER_CRC: p->stats.rej_payload_crc++; break;'),
    ('accept an unknown frame type', C, 'if (!known_type(buf[2]))', 'if (0)'),
    ('accept a control frame with a payload', C, 'if (carries_payload(buf[2]) != (len != 0))\n        return SAM_ERR_BAD_LENGTH;\n    total',
     'if (carries_payload(buf[2]) && len == 0)\n        return SAM_ERR_BAD_LENGTH;\n    total'),
    ('header CRC taken over the SYN as well', C, 'sam_crc16_update(SAM_CRC_INIT, buf + SAM_SYN_LEN, SAM_HEADER_LEN)',
     'sam_crc16_update(SAM_CRC_INIT, buf, SAM_HEADER_LEN)'),
    ('payload CRC written big-endian by the builder', C, '    put_le16(out + pos, crc);',
     '    out[pos] = (uint8_t)(crc >> 8);\n    out[pos + 1] = (uint8_t)crc;'),
    ('NAK built with a caller-chosen sequence', C, 'SAM_FRAME_NAK, 0, NULL, 0, written);', 'SAM_FRAME_NAK, 1, NULL, 0, written);'),
    ('event id range one too wide', H, 'return (uint16_t)(rqid - 1u) < SAM_EVENT_ID_COUNT;',
     'return (uint16_t)(rqid - 1u) <= SAM_EVENT_ID_COUNT;'),
    ('request id may land in the event range', H, 'if (rqid == 0 || sam_rqid_is_event(rqid))', 'if (rqid == 0)'),
    ('flush forgets a lone possible SYN byte', C, 'else if (avail == 1)\n        p->stats.rej_trailing++;', 'else if (avail == 1)\n        (void)0;'),
    ('init accepts storage smaller than one frame', C, 'storage_len < SAM_MESSAGE_OVERHEAD + (size_t)max_payload',
     'storage_len < SAM_MESSAGE_OVERHEAD'),
    ('compaction drops a byte', C, 'copy_bytes(p->buf, p->buf + p->head, p->fill - p->head);',
     'copy_bytes(p->buf, p->buf + p->head, p->fill - p->head - 1);'),
    ('rejected bytes not counted as skipped', C, '        p->in_garbage = true;\n        skip_bytes(p, SAM_SYN_LEN);',
     '        p->in_garbage = true;\n        p->head += SAM_SYN_LEN;'),
    ('every garbage call counts as a new run', C, 'if (!p->in_garbage) {', 'if (1) {'),
    ('flush allowed before the parser is drained', C, 'if (!parser_configured(p) || !p->drained)', 'if (!parser_configured(p))'),
    ('decode_exact ignores trailing bytes', C, 'if (rc == SAM_OK && out->message_len != len)', 'if (0)'),
    ('command decode accepts any type byte', C, 'if (payload[0] != SAM_PAYLOAD_TYPE_CMD)', 'if (0)'),
    ('ACK counted as a NAK', C, 'case SAM_FRAME_ACK: p->stats.frames_ack++; break;', 'case SAM_FRAME_ACK: p->stats.frames_nak++; break;'),
    ('NEED_MORE threshold one byte short', C, 'if (avail < header_end)\n        return SAM_NEED_MORE;', 'if (avail < header_end - 1)\n        return SAM_NEED_MORE;'),
    ('oversize and bad length swapped in precedence', C,
     '    if (len > max_payload)\n        return SAM_ERR_OVERSIZE;\n    if (carries_payload(buf[2]) != (len != 0))\n        return SAM_ERR_BAD_LENGTH;',
     '    if (carries_payload(buf[2]) != (len != 0))\n        return SAM_ERR_BAD_LENGTH;\n    if (len > max_payload)\n        return SAM_ERR_OVERSIZE;'),
    ('oversize only judged once the whole payload is present', C, 'if (len > max_payload)',
     'if (len > max_payload && avail >= SAM_MESSAGE_OVERHEAD + (size_t)len)'),
    ('second SYN byte not checked', C, 'if (avail >= 2 && buf[1] != SAM_SYN_1)', 'if (0)'),
    ('a trailing lone 0xAA is treated as garbage', C, 'return (n && b[n - 1] == SAM_SYN_0) ? n - 1 : n;', 'return n;'),
)


def build_mutant(index, mutant):
    name, filename, old, new = mutant
    directory = OUT / 'mutants' / f'{index:02d}'
    directory.mkdir(parents=True, exist_ok=True)
    for source in (C, H):
        shutil.copyfile(ROOT / 'compat' / source, directory / source)
    path = directory / filename
    text = path.read_text()
    if text.count(old) != 1:
        raise AssertionError(f'mutant {name!r}: pattern occurs {text.count(old)} times in {filename}')
    path.write_text(text.replace(old, new))
    return build_native(directory, directory)


def stage_failures(binary, corpus):
    """Names of the stages of the whole suite that fail for this build of the codec."""
    failed = []
    for stage, args in (('selftest', ('selftest',)), ('fuzz', ('fuzz', str(FUZZ_CASES)))):
        try:
            result = run(binary, *args, timeout=120)
            if result.returncode:
                failed.append(stage)
        except subprocess.TimeoutExpired:
            failed.append(stage + ' (hang)')
    try:
        if differential_mismatches(binary, corpus):
            failed.append('python differential')
    except subprocess.TimeoutExpired:
        failed.append('python differential (hang)')
    return failed


# ---- tests -------------------------------------------------------------------------------------------------

BINARY = None
CORPUS = None


def setUpModule():
    global BINARY, CORPUS
    if not CC:
        raise unittest.SkipTest('no C compiler found (tried clang, cc)')
    if OUT.exists():
        shutil.rmtree(OUT)
    BINARY = build_native(OUT, ROOT / 'compat')
    CORPUS = build_differential()


def vectors_from_c_source():
    """The known-answer vectors, read from the native driver so that they have one home."""
    text = (ROOT / 'tools/test-sam-frames.c').read_text()
    text = text.replace('EVENT_PAYLOAD', '"%s"' % re.search(r'#define EVENT_PAYLOAD "([0-9a-f]+)"', text).group(1))
    table = text[text.index('vectors[] = {'):text.index('#define NVECTORS')]
    found = []
    for name, ftype, seq, payload, message in re.findall(
            r'\{"([^"]+)", (0x[0-9a-f]+), (0x[0-9a-f]+|\d+), ((?:"[0-9a-f]*"\s*)+), ((?:"[0-9a-f]*"\s*)+)\}', table):
        join = lambda parts: ''.join(re.findall(r'"([0-9a-f]*)"', parts))  # noqa: E731
        found.append((name, int(ftype, 16), int(seq, 0), bytes.fromhex(join(payload)), bytes.fromhex(join(message))))
    return found


class Reference(unittest.TestCase):
    """The Python reference against the standard and against known answers; no C involved."""

    def test_crc_check_value_and_three_structures(self):
        self.assertEqual(ref.crc16(b'123456789'), ref.CRC_CHECK_123456789)
        self.assertEqual(ref.crc16_hqx(b'123456789'), ref.CRC_CHECK_123456789)
        self.assertEqual(ref.crc16(b'123456789', 0), 0x31C3)  # CRC-16/XMODEM: the same polynomial, another init
        self.assertEqual(ref.crc16(b''), 0xFFFF)
        rng = random.Random(7)
        for _ in range(500):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(80)))
            self.assertEqual(ref.crc16(data), ref.crc16_hqx(data))

    def test_vectors_in_the_native_driver_match_the_reference_builder(self):
        vectors = vectors_from_c_source()
        self.assertGreaterEqual(len(vectors), 9)
        for name, ftype, seq, payload, message in vectors:
            with self.subTest(name):
                self.assertEqual(ref.build_frame(ftype, seq, payload), message)
                self.assertEqual(ref.decode_exact(message, 4086), (ftype, seq, payload))

    def test_documented_facts(self):
        self.assertEqual(ref.build_ack(1).hex(), 'aa55400000017dfaffff')
        self.assertEqual(ref.crc16(b''), 0xFFFF)  # an empty payload still carries a CRC, 0xffff
        self.assertEqual(ref.build_nak()[4:6], b'\x00\x00')  # sequence zero by convention
        self.assertEqual(ref.encode_command(1, 1, 0, 0, 0xA1B2, 0x13).hex(), '80010100' '00b2a113')

    def test_reference_refusals(self):
        for call, name in ((lambda: ref.build_frame(0x41, 0), 'bad-argument'),
                           (lambda: ref.build_frame(ref.TYPE_ACK, 0, b'x'), 'bad-length'),
                           (lambda: ref.build_frame(ref.TYPE_DATA_SEQ, 0), 'bad-length'),
                           (lambda: ref.build_frame(ref.TYPE_DATA_SEQ, 0, bytes(65536)), 'oversize'),
                           (lambda: ref.decode_command(b'\x80' * 7), 'command-short'),
                           (lambda: ref.decode_command(b'\x81' * 8), 'command-type')):
            with self.assertRaises(ref.FrameError) as caught:
                call()
            self.assertEqual(str(caught.exception), name)

    def test_command_line_tool(self):
        tool_py = str(ROOT / 'tools/sam_frames.py')
        run_tool = lambda *a: subprocess.run([sys.executable, tool_py, *a], capture_output=True, text=True)  # noqa: E731
        self.assertEqual(run_tool('crc', '313233343536373839').stdout.strip(), '0x29b1')
        self.assertEqual(run_tool('build', 'ack', '1').stdout.strip(), 'aa55400000017dfaffff')
        self.assertEqual(run_tool('build', 'ack', '1', '00').returncode, 1)
        parsed = run_tool('parse', 'aa55400000017dfaffff')
        self.assertIn('F 40 01 -', parsed.stdout)
        self.assertIn('ack=1', parsed.stdout)


class Native(unittest.TestCase):
    def test_selftest(self):
        result = run(BINARY, 'selftest')
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn(' 0 failures', result.stdout)

    def test_fuzz_100k_mutated_frames_under_sanitizers(self):
        result = run(BINARY, 'fuzz', str(FUZZ_CASES))
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        cases = int(re.search(r'fuzz: cases=(\d+)', result.stdout).group(1))
        self.assertGreaterEqual(cases, 100000)
        self.assertIn(' 0 failures', result.stdout)

    def test_fuzz_is_deterministic_and_seed_dependent(self):
        first, again = run(BINARY, 'fuzz', '3000', '1'), run(BINARY, 'fuzz', '3000', '1')
        other = run(BINARY, 'fuzz', '3000', '2')
        self.assertEqual(first.returncode, 0, first.stderr[-1000:])
        self.assertEqual(first.stdout, again.stdout)
        self.assertNotEqual(first.stdout, other.stdout)

    def test_constants_agree_with_the_reference(self):
        values = dict(line.split() for line in run(BINARY, 'consts').stdout.splitlines())
        self.assertEqual({k: int(v) for k, v in values.items()},
                         dict(syn0=0xAA, syn1=0x55, overhead=ref.MESSAGE_OVERHEAD, payload_max=ref.PAYLOAD_MAX,
                              crc_init=ref.CRC_INIT, crc_check=ref.CRC_CHECK_123456789,
                              type_data_nsq=ref.TYPE_DATA_NSQ, type_nak=ref.TYPE_NAK, type_ack=ref.TYPE_ACK,
                              type_data_seq=ref.TYPE_DATA_SEQ, payload_type_cmd=ref.PAYLOAD_TYPE_CMD,
                              command_header_len=ref.COMMAND_HEADER_LEN, event_id_count=ref.EVENT_ID_COUNT))

    def test_differential_against_python_reference(self):
        mismatches = differential_mismatches(BINARY, CORPUS)
        self.assertEqual(mismatches, [], '\n'.join(mismatches))
        self.assertGreater(len(CORPUS[1]), 15000)

    def test_differential_detects_a_wrong_reference(self):
        # The comparison itself must be able to fail: tamper with one expected block and expect a mismatch.
        stdin, expected = CORPUS
        tampered = list(expected)
        tampered[3] = tampered[3] + 'x'
        self.assertTrue(differential_mismatches(BINARY, (stdin, tampered)))


class Builds(unittest.TestCase):
    def test_strict_host_warnings(self):
        result = subprocess.run([CC, '-std=c11', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-Wconversion', '-Wshadow',
                                 '-Wcast-qual', '-Wstrict-prototypes', '-Werror', '-c', 'compat/sam-frame.c', '-o',
                                 str(OUT / 'strict.o')], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_freestanding_aarch64_build_needs_no_runtime(self):
        nm = tool('llvm-nm')
        if not nm:
            self.skipTest('llvm-nm not found')
        obj = OUT / 'sam-frame-aarch64.o'
        result = subprocess.run([CC, *MONITOR_FLAGS, '-c', 'compat/sam-frame.c', '-o', str(obj)], cwd=ROOT,
                                capture_output=True, text=True)
        if result.returncode and 'unable to create target' in result.stderr:
            self.skipTest('this clang has no aarch64 target')
        self.assertEqual(result.returncode, 0, result.stderr)
        undefined = subprocess.run([nm, '-u', str(obj)], capture_output=True, text=True).stdout.split()
        self.assertEqual(undefined, [], 'the codec must not need memcpy, memmove or any other runtime symbol')

    def test_gcc_freestanding_build(self):
        gcc = shutil.which('aarch64-elf-gcc')
        if not gcc:
            self.skipTest('aarch64-elf-gcc not installed')
        result = subprocess.run([gcc, '-std=c11', '-ffreestanding', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-Werror',
                                 '-c', 'compat/sam-frame.c', '-o', str(OUT / 'sam-frame-gcc.o')], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class Hygiene(unittest.TestCase):
    def test_new_files_follow_the_repository_rules(self):
        marker = '/' + 'Users' + '/'  # spelled in two pieces so that this file does not trip its own check
        for name in FILES:
            path = ROOT / name
            if not path.exists():
                continue
            text = path.read_text()
            with self.subTest(name):
                self.assertNotIn(marker, text)
                if not name.endswith('.md'):
                    self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
                self.assertLess(len(text.splitlines()), 1500)
                self.assertLess(len(text.encode()), 50 * 1024)


class SeededBugs(unittest.TestCase):
    @unittest.skipIf(SKIP_MUTANTS, '--skip-mutants')
    def test_every_seeded_bug_is_caught(self):
        self.assertGreaterEqual(len(MUTANTS), 6)
        results = {}

        def check(item):
            index, mutant = item
            return mutant[0], stage_failures(build_mutant(index, mutant), CORPUS)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for name, failed in pool.map(check, enumerate(MUTANTS)):
                results[name] = failed
        width = max(len(name) for name in results)
        report = '\n'.join(f'  {name:<{width}}  caught by: {", ".join(failed) or "NOT CAUGHT"}'
                           for name, failed in results.items())
        print('\nseeded bugs:\n' + report, file=sys.stderr)
        (OUT / 'mutants.txt').write_text(report + '\n')
        self.assertEqual([name for name, failed in results.items() if not failed], [], 'mutants that survived')


if __name__ == '__main__':
    unittest.main(verbosity=2)
