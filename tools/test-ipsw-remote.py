#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline tests for tools/ipsw_remote.py and the URL path of tools/extract-firmware.py.

Nothing here reaches a network: HTTP is an in-process fake transport or a server this
file starts on 127.0.0.1, and a module-wide guard fails any socket connect or name
lookup that is not loopback. The IPSW is synthetic (BuildManifest.plist, IM4P-wrapped
stand-ins named like the real components, decoys, tens of MiB of filler); no Apple data
is involved. A case that cannot run (binding 127.0.0.1 not permitted) reports SKIP.

    python3 tools/test-ipsw-remote.py [-v]

(a) the IPSW over a fake transport and a localhost server; (b) URL extraction equals
local extraction; (c) fetched bytes are a small fraction and match the printed plan;
(d) each failure is refused with its own message; (e) the cap refuses; (f) --dry-run
fetches no component bytes; (g) zip64; (h) the local path is unchanged, pinned to values
produced by the pre-change tools/extract-firmware.py.
"""
import contextlib
import hashlib
import http.server
import importlib.util
import io
import os
import plistlib
import random
import re
import shutil
import socket
import socketserver
import ssl
import struct
import sys
import threading
import time
import types
import unittest
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import ipsw_remote as ir

_spec = importlib.util.spec_from_file_location('extract_firmware', ROOT / 'tools' / 'extract-firmware.py')
fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fw)

BUILD = ROOT / 'build' / 'test-ipsw-remote'
BOARD, CHIP = 'j700ap', '0x8140'
MIB = 1 << 20
SMALL_BLOCK = 64 << 10
URL = 'https://cdn.example/UniversalMac_Restore.ipsw'   # never resolved: a fake transport answers
LOOPBACK_HOSTS = ('127.0.0.1', '::1', 'localhost')
NO_SLEEP = lambda seconds: None  # noqa: E731


# --- the synthetic IPSW

def stream(label, size):
    return hashlib.shake_256(label.encode()).digest(size)


def macho(label, size):
    return struct.pack('<IIII', 0xFEEDFACF, 0x0100000C, 0, 2) + stream(label, size - 16)


def devicetree(label, size):
    return struct.pack('<II', 12, 3) + stream(label, size - 8)


def der(tag, body):
    if len(body) < 0x80:
        return bytes([tag, len(body)]) + body
    length = len(body).to_bytes((len(body).bit_length() + 7) // 8, 'big')
    return bytes([tag, 0x80 | len(length)]) + length + body


def im4p(kind, payload):
    return der(0x30, der(0x16, b'IM4P') + der(0x16, kind.encode()) + der(0x16, b'synthetic') +
               der(0x04, payload))


def complzss(plain):
    """A literal-only Apple LZSS stream: eight literals behind each all-ones flag byte."""
    body = bytearray()
    for i in range(0, len(plain), 8):
        chunk = plain[i:i + 8]
        body += bytes([(1 << len(chunk)) - 1]) + chunk
    return (b'complzss' + struct.pack('>III', len(plain), len(plain), len(body))).ljust(0x180, b'\0') + bytes(body)


# manifest key, archive path, raw image. SPTM and ExclaveCore are not in the manifest: the tool finds them by name.
COMPONENTS = [
    ('KernelCache', 'kernelcache.release.mac17p', macho('kc', 600_000)),
    ('Ap,TrustedExecutionMonitor', 'Firmware/txm.macosx.release.im4p', macho('txm', 120_000)),
    ('DeviceTree', 'Firmware/all_flash/DeviceTree.j700ap.im4p', devicetree('dt', 50_000)),
    ('iBoot', 'Firmware/all_flash/iBoot.j700.RELEASE.im4p', stream('iboot', 150_000)),
    ('LLB', 'Firmware/all_flash/LLB.j700.RELEASE.im4p', stream('llb', 100_000)),
    ('SEP', 'Firmware/all_flash/sep-firmware.j700.RELEASE.im4p', stream('sep', 200_000)),
    ('SepStage1', 'Firmware/all_flash/sepstage1.j700.RELEASE.im4p', stream('sep1', 30_000)),
    ('iBootData', 'Firmware/all_flash/iBootData.j700.RELEASE.im4p', stream('ibd', 20_000)),
    ('SPTM', 'Firmware/sptm.t8140.release.im4p', macho('sptm', 150_000)),
    ('ExclaveCore', 'Firmware/exclavecore_bundle.t8140.RELEASE.im4p', macho('exc', 130_000)),
]
WRAP = {'SEP': complzss}   # one component travels compressed, to exercise decompress() on both paths
IN_MANIFEST = [key for key, _, _ in COMPONENTS[:8]]
# Interleaving: ints are filler slots. Manifest and end record then sit in filler-only blocks.
ORDER = [0, 'KernelCache', 1, 'Ap,TrustedExecutionMonitor', 'DeviceTree', 2, 'iBoot', 'LLB', 3, 'SEP', 'SepStage1',
         4, 'iBootData', 'SPTM', 5, 'ExclaveCore', 6, 7, 8, 9]


def output_name(path):
    name = Path(path).name
    return name[:-len('.im4p')] if name.endswith('.im4p') else name


EXPECTED = {output_name(path): plain for _, path, plain in COMPONENTS}


def manifest_bytes():
    def identity(board, behavior, paths):
        return {'ApChipID': CHIP, 'ApBoardID': '0x26',
                'Info': {'DeviceClass': board, 'RestoreBehavior': behavior, 'Variant': 'macOS Customer'},
                'Manifest': {key: {'Info': {'Path': path}} for key, path in paths.items()}}
    real = {key: path for key, path, _ in COMPONENTS[:8]}
    decoy = {key: f'Firmware/decoy/{key}.im4p' for key in real}
    return plistlib.dumps({'ProductBuildVersion': '26A428', 'BuildIdentities': [
        identity(BOARD, 'Update', decoy), identity('j999ap', 'Erase', decoy), identity(BOARD, 'Erase', real)]})


def build_zip(fillers=0, filler_size=4 * MIB, force_zip64=False, zip64_limit=None, drop=()):
    """The synthetic IPSW as bytes. zip64_limit lowers zipfile's limits while writing so that a small
    archive gets 64-bit offsets and sizes in its central directory and zip64 end records."""
    buf = io.BytesIO()
    with contextlib.ExitStack() as stack:
        if zip64_limit:
            stack.enter_context(mock.patch.object(zipfile, 'ZIP64_LIMIT', zip64_limit))
            stack.enter_context(mock.patch.object(zipfile, 'ZIP_FILECOUNT_LIMIT', 8))
        archive = stack.enter_context(zipfile.ZipFile(buf, 'w'))

        def add(name, data, method=zipfile.ZIP_STORED):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type, info.create_system = method, 3
            with archive.open(info, 'w', force_zip64=force_zip64) as dest:
                dest.write(data)

        add('BuildManifest.plist', manifest_bytes(), zipfile.ZIP_DEFLATED)
        for key in IN_MANIFEST:
            add(f'Firmware/decoy/{key}.im4p', im4p('decoy', stream('decoy' + key, 1000)))
        by_key = {key: (path, plain) for key, path, plain in COMPONENTS}
        for slot in ORDER:
            if isinstance(slot, int):
                if slot < fillers:
                    add(f'Firmware/other/filler{slot}.bin', stream(f'filler{slot}', filler_size))
            elif slot not in drop:
                path, plain = by_key[slot]
                add(path, im4p('krnl', WRAP.get(slot, bytes)(plain)))
    return buf.getvalue()


def data_spans(data):
    """[start, stop) of the header and data of every component member."""
    wanted = {path for _, path, _ in COMPONENTS}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [ir.member_span(i) for i in archive.infolist() if i.filename in wanted]


def component_reads(log, data):
    """The requested (first, last) ranges that touch any component's bytes."""
    spans = data_spans(data)
    return [r for r in log if any(r[0] < stop and start <= r[1] for start, stop in spans)]


# --- fakes

class Reply:
    def __init__(self, parts):
        self.status, self.url = parts['status'], parts['url']
        self.headers = Message()
        for name, value in parts['headers'].items():
            self.headers[name] = value
        self._body, self._break = io.BytesIO(parts['body']), parts.get('break_after')
        self.consumed, self.closed = 0, False

    def read(self, size):
        if self._break is not None:      # deliver break_after bytes, then drop the connection
            if self.consumed >= self._break:
                raise ConnectionResetError('connection reset mid-body')
            size = min(size, self._break - self.consumed)
        data = self._body.read(size)
        self.consumed += len(data)
        return data

    def close(self):
        self.closed = True


class FakeTransport:
    """The HTTP layer in process: answers ranges like a well-behaved server unless fault(n, parts) interferes.

    n counts requests from 1. The hook may edit parts, or raise to mimic a failure below HTTP.
    """

    def __init__(self, data, *, url=None, fault=None, claimed_size=None):
        self.data, self.url, self.fault, self.claimed = data, url, fault, claimed_size
        self.calls, self.log, self.urls, self.replies = 0, [], [], []

    def __call__(self, url, first, last, timeout):
        self.calls += 1
        self.urls.append(url)
        total = self.claimed or len(self.data)
        last = min(last, total - 1)
        self.log.append((first, last))
        parts = {'status': 206, 'first': first, 'last': last, 'total': total, 'url': self.url or url,
                 'headers': {'Content-Range': f'bytes {first}-{last}/{total}',
                             'Content-Length': str(last - first + 1), 'ETag': '"v1"',
                             'Last-Modified': 'Tue, 05 May 2026 10:00:00 GMT'},
                 'body': self.data[first:last + 1]}
        if self.fault:
            self.fault(self.calls, parts)
        reply = Reply(parts)
        self.replies.append(reply)
        return reply

    @property
    def served(self):
        return sum(last - first + 1 for first, last in self.log)


def factory(transport, block=SMALL_BLOCK, **options):
    return lambda url, max_fetch: ir.RangeFile(url, max_fetch=max_fetch, block_size=block, transport=transport,
                                              sleep=NO_SLEEP, **options)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind calls getfqdn(), a reverse lookup that has no place in an offline test.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]

    def handle_error(self, request, client_address):
        if not issubclass(sys.exc_info()[0], (BrokenPipeError, ConnectionResetError)):   # clients hang up on purpose
            super().handle_error(request, client_address)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        state, path = self.server.state, self.path.split('?')[0]
        state.log.append((path, self.headers.get('Range')))
        if path in state.redirects:
            self.send_response(302)
            self.send_header('Location', state.redirects[path])
            self.send_header('Content-Length', '0')
            self.end_headers()
        elif path == '/signed' and state.signed_hits >= state.signed_budget:
            self.send_error(403)
        elif path in ('/object', '/signed'):
            state.signed_hits += path == '/signed'
            self.ranged(state)
        else:
            self.send_error(404)

    def ranged(self, state):
        if state.delay:
            time.sleep(state.delay)
        data, match = state.data, re.fullmatch(r'bytes=(\d+)-(\d+)', self.headers.get('Range', ''))
        if not match or state.mode == 'ignore':
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)      # the client must hang up after the headers; BrokenPipe is expected
            return
        first, last = int(match[1]), min(int(match[2]), len(data) - 1)
        body = data[first:last + 1]
        self.send_response(206)
        self.send_header('Content-Range', f'bytes {first}-{last}/{len(data)}')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', '"v1"')
        self.send_header('Last-Modified', 'Tue, 05 May 2026 10:00:00 GMT')
        self.end_headers()
        self.wfile.write(body[:len(body) // 2] if state.mode == 'short' else body)


@contextlib.contextmanager
def serve(data):
    """A Range-capable server on 127.0.0.1. Yields (base URL, state); state.mode, .delay, ... steer it."""
    state = types.SimpleNamespace(data=data, log=[], mode='ok', delay=0, signed_hits=0, signed_budget=10 ** 9,
                                  redirects={})
    try:
        server = Server(('127.0.0.1', 0), Handler)
    except OSError as error:
        raise unittest.SkipTest(f'cannot bind 127.0.0.1: {error}')
    server.state = state
    base = f'http://127.0.0.1:{server.server_address[1]}'
    state.redirects = {'/start': '/object', '/to-http': 'http://redirect-target.invalid/object',
                       '/to-ftp': 'ftp://127.0.0.1/object', '/to-file': 'file:///nonexistent',
                       '/loop-a': '/loop-b', '/loop-b': '/loop-a', '/to-signed': '/signed'}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


# --- shared fixtures

BIG = SMALL = Z64_FORCED = Z64_RECORDS = None


def setUpModule():
    global BIG, SMALL, Z64_FORCED, Z64_RECORDS
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def guarded_connect(self, address):
        if not (isinstance(address, tuple) and address[0] in LOOPBACK_HOSTS):
            raise AssertionError(f'a test tried to connect to {address!r}')
        return real_connect(self, address)

    def guarded_lookup(host, *args, **kwargs):
        if host is not None and host not in LOOPBACK_HOSTS:
            raise AssertionError(f'a test tried to resolve {host!r}')
        return real_lookup(host, *args, **kwargs)

    patches = [mock.patch.object(socket.socket, 'connect', guarded_connect),
               mock.patch.object(socket, 'getaddrinfo', guarded_lookup)]
    for patch in patches:
        patch.start()
        unittest.addModuleCleanup(patch.stop)
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    unittest.addModuleCleanup(shutil.rmtree, BUILD, ignore_errors=True)
    BIG = build_zip(fillers=10)
    (BUILD / 'big.ipsw').write_bytes(BIG)
    SMALL = build_zip()
    (BUILD / 'small').mkdir()
    (BUILD / 'small' / 'small.ipsw').write_bytes(SMALL)
    Z64_FORCED = build_zip(fillers=10, filler_size=MIB, force_zip64=True)
    Z64_RECORDS = build_zip(fillers=10, filler_size=MIB, zip64_limit=MIB)


def run(argv, **kwargs):
    """fw.main(argv) -> (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = fw.main(argv, **kwargs)
        except SystemExit as exit_:
            code = exit_.code
            if isinstance(code, str):
                print(code, file=err)
                code = 1
    return code, out.getvalue(), err.getvalue()


def tree(path):
    return {item.name: item.read_bytes() for item in sorted(path.iterdir())}


def fresh(name):
    path = BUILD / 'out' / name
    shutil.rmtree(path, ignore_errors=True)
    return path


def number(pattern, text):
    return int(re.search(pattern, text)[1].replace(',', ''))


def tail_read(f):
    """The last 22 bytes: a read that needs the end block, so one more request after the first."""
    f.seek(-22, 2)
    return f.read(22)


# --- (a) the synthetic IPSW, served two ways

class A_SyntheticIpsw(unittest.TestCase):
    def check_archive(self, archive):
        self.assertEqual(plistlib.loads(archive.read('BuildManifest.plist'))['ProductBuildVersion'], '26A428')
        for key, path, plain in COMPONENTS:
            self.assertEqual(fw.decompress(fw.im4p_payload(archive.read(path)), key), plain, key)

    def test_a_fake_transport_serves_every_component(self):
        fake = FakeTransport(BIG)
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK) as remote_file, ir.open_zip(remote_file) as z:
            self.check_archive(z)
        self.assertEqual(fake.calls, remote_file.requests)
        self.assertEqual(fake.served, remote_file.bytes_fetched)
        self.assertTrue(all(reply.closed for reply in fake.replies))

    def test_a_localhost_server_serves_every_component(self):
        with serve(BIG) as (base, state):
            with ir.RangeFile(f'{base}/object') as remote_file, ir.open_zip(remote_file) as z:
                self.check_archive(z)
        self.assertTrue(all(rng and rng.startswith('bytes=') for _, rng in state.log), state.log)
        spans = [tuple(map(int, re.findall(r'\d+', rng))) for _, rng in state.log]
        self.assertTrue(all(last - first + 1 <= ir.MAX_REQUEST for first, last in spans))

    def test_a_a_reply_without_content_length_or_validators_is_accepted(self):
        def strip(n, parts):      # what a chunked CDN answer without ETag or Last-Modified looks like
            for name in ('Content-Length', 'ETag', 'Last-Modified'):
                del parts['headers'][name]
        with ir.RangeFile(URL, transport=FakeTransport(BIG, fault=strip), block_size=SMALL_BLOCK) as f:
            with ir.open_zip(f) as z:
                self.check_archive(z)

    def test_a_default_transport_ignores_proxy_settings_for_loopback(self):
        with serve(SMALL) as (base, _), mock.patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:1',
                                                                     'HTTP_PROXY': 'http://127.0.0.1:1'}):
            with ir.RangeFile(f'{base}/object') as remote_file:
                self.assertEqual(remote_file.size, len(SMALL))


# --- (b) URL extraction equals local extraction

class B_UrlMatchesLocal(unittest.TestCase):
    def check(self, run_url, name):
        local_out, out = fresh(name + '-local'), fresh(name + '-url')
        self.assertEqual(run([str(BUILD / 'big.ipsw'), BOARD, '--out', str(local_out)])[0], 0)
        code, text, err = run_url(out)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(tree(out), tree(local_out))
        self.assertEqual(tree(out), EXPECTED)
        self.assertEqual([line for line in text.splitlines() if line in COMPONENT_LINES], COMPONENT_LINES)

    def test_b_fake_transport(self):
        self.check(lambda out: run([URL, BOARD, '--out', str(out)], remote_factory=factory(FakeTransport(BIG))), 'fake')

    def test_b_localhost_server_default_blocks(self):
        with serve(BIG) as (base, _):
            self.check(lambda out: run([f'{base}/object', BOARD, '--out', str(out)]), 'http')

    def test_b_a_redirect_is_followed_and_its_target_reused(self):
        with serve(BIG) as (base, state):
            self.check(lambda out: run([f'{base}/start', BOARD, '--out', str(out)]), 'redirect')
        paths = [path for path, _ in state.log]
        self.assertEqual(paths[0], '/start')
        self.assertTrue(all(path == '/object' for path in paths[1:]), paths)

    def test_b_default_output_is_under_the_repository_root_not_next_to_a_url(self):
        fake_root = BUILD / 'fake-root'
        with mock.patch.object(fw, 'repo_root', return_value=fake_root):
            code, _, _ = run([URL, BOARD], remote_factory=factory(FakeTransport(SMALL)))
        self.assertEqual(code, 0)
        self.assertEqual(tree(fake_root / 'downloads' / 'extracted' / BOARD), EXPECTED)


# --- (c) accounting

class C_Accounting(unittest.TestCase):
    def plan_and_run(self, data, name):
        fake = FakeTransport(data)
        code, text, _ = run([URL, BOARD, '--out', str(fresh(name))], remote_factory=factory(fake))
        self.assertEqual(code, 0)
        return fake, text, number(r'total fetch ([\d,]+) bytes', text)

    def test_c_a_small_fraction_is_fetched_and_the_plan_accounts_for_it(self):
        fake, text, planned = self.plan_and_run(BIG, 'c-plain')
        self.assertLess(fake.served * 10, len(BIG))
        self.assertLessEqual(abs(fake.served - planned), SMALL_BLOCK * len(COMPONENTS))
        self.assertEqual(number(r'remote: \d+ requests, ([\d,]+) bytes fetched', text), fake.served)
        self.assertEqual(number(r'archive of ([\d,]+) bytes', text), len(BIG))
        listed = sum(number(r'^  \S+\s+([\d,]+)  \S', line) for line in text.splitlines()
                     if re.match(r'^  (?!total)\S+\s+[\d,]+  \S', line))
        self.assertEqual(number(r'total\s+([\d,]+)', text), listed)

    def test_c_small_reads_cost_one_request_per_block(self):
        fake = FakeTransport(BIG)
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK) as f:
            before = f.requests
            f.seek(5 * SMALL_BLOCK)
            for _ in range(1000):
                f.read(10)
            self.assertEqual(f.requests - before, 1)

    def test_c_long_reads_are_coalesced_but_bounded(self):
        fake = FakeTransport(BIG)
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK) as f:
            self.assertEqual(f.read(20 * MIB), BIG[:20 * MIB])
        sizes = [last - first + 1 for first, last in fake.log[1:]]
        self.assertEqual(max(sizes), ir.MAX_REQUEST)
        self.assertEqual(len(sizes), 3)

    def test_c_lru_is_bounded_and_evicts_the_oldest_block(self):
        data = b'PK\x03\x04' + stream('lru', 20 * SMALL_BLOCK)
        fake = FakeTransport(data)
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, cache_blocks=2) as f:
            for block in (3, 4, 5):
                f.seek(block * SMALL_BLOCK)
                f.read(1)
            count = f.requests
            f.seek(5 * SMALL_BLOCK)
            f.read(1)
            f.seek(4 * SMALL_BLOCK)
            f.read(1)
            self.assertEqual(f.requests, count)
            f.seek(3 * SMALL_BLOCK)
            f.read(1)
            self.assertEqual(f.requests, count + 1)

    def test_c_reads_and_seeks_match_a_bytesio(self):
        data = b'PK\x03\x04' + stream('model', 37 * 1000 + 123)
        model, rng = io.BytesIO(data), random.Random(7)
        with ir.RangeFile(URL, transport=FakeTransport(data), block_size=1000, cache_blocks=3) as f:
            for _ in range(400):
                if rng.random() < 0.5:
                    size = rng.choice([-1, 0, 1, 7, 999, 1000, 1001, 5000, 40000])
                    self.assertEqual(f.read(size), model.read(size))
                else:
                    whence, target = rng.choice([0, 1, 2]), rng.randrange(0, len(data) + 50)
                    offset = target - (0, f.tell(), len(data))[whence]
                    self.assertEqual(f.seek(offset, whence), model.seek(offset, whence))
                self.assertEqual(f.tell(), model.tell())
            with self.assertRaises(OSError):     # like a real file, unlike BytesIO.seek(-1, 1)
                f.seek(-1)

    def test_c_the_plan_counts_each_uncached_block_once_and_clips_at_the_end(self):
        block = SMALL_BLOCK
        with ir.RangeFile(URL, transport=FakeTransport(BIG), block_size=block) as f:
            self.assertEqual(f.bytes_to_fetch([(0, 10)]), 0)       # block 0 came with the first request
            self.assertEqual(f.bytes_to_fetch([(block, block + 1), (block + 5, block + 9),
                                               (2 * block - 1, 2 * block + 1)]), 2 * block)
            self.assertEqual(f.bytes_to_fetch([(len(BIG) - 10, len(BIG) + 99)]), len(BIG) % block or block)

    def test_c_reading_a_closed_file_fails(self):
        f = ir.RangeFile(URL, transport=FakeTransport(SMALL), block_size=SMALL_BLOCK)
        f.close()
        with self.assertRaises(ValueError):
            f.read(1)


# --- (d) every failure is refused, each with its own message

def _ignore_range(p): p['status'] = 200
def _shifted_start(p): p['headers']['Content-Range'] = f"bytes {p['first'] + 1}-{p['last'] + 1}/{p['total']}"
def _bad_content_length(p): p['headers']['Content-Length'] = str(p['last'] - p['first'])
def _no_content_range(p): del p['headers']['Content-Range']
def _gzip(p): p['headers']['Content-Encoding'] = 'gzip'
def _total_changes(p): p['headers']['Content-Range'] = f"bytes {p['first']}-{p['last']}/{p['total'] + 1}"
def _etag_changes(p): p['headers']['ETag'] = '"v2"'
def _modified_changes(p): p['headers']['Last-Modified'] = 'Wed, 06 May 2026 10:00:00 GMT'
def _short_body(p): p['body'] = p['body'][:-1]
def _http_416(p): p['status'] = 416
def _plain_http_final(p): p['url'] = 'http://elsewhere.example/x'


def _shorter_range(p):
    p['headers']['Content-Range'] = f"bytes {p['first']}-{p['last'] - 1}/{p['total']}"
    p['headers']['Content-Length'] = str(p['last'] - p['first'])
    p['body'] = p['body'][:-1]


def _long_body(p):
    del p['headers']['Content-Length']
    p['body'] += b'X'


# label, hook, request numbers from which it applies, exception, phrase only this refusal uses, retried
CASES = [
    ('range ignored', _ignore_range, (1, 2), ir.RangeIgnoredError, 'ignored Range', False),
    ('different range', _shifted_start, (1, 2), ir.RangeMismatchError, 'different range', False),
    ('different length', _shorter_range, (1, 2), ir.RangeMismatchError, 'different length', False),
    ('Content-Length contradicts the range', _bad_content_length, (1, 2), ir.RangeMismatchError, 'contradicts', False),
    ('no Content-Range', _no_content_range, (1, 2), ir.RangeMismatchError, 'no usable Content-Range', False),
    ('Content-Encoding', _gzip, (1, 2), ir.RangeMismatchError, 'Content-Encoding', False),
    ('body longer than its range', _long_body, (1, 2), ir.RangeMismatchError, 'sent more than', False),
    ('length changed', _total_changes, (2,), ir.ObjectChangedError, 'length was', False),
    ('ETag changed', _etag_changes, (2,), ir.ObjectChangedError, 'ETag was', False),
    ('Last-Modified changed', _modified_changes, (2,), ir.ObjectChangedError, 'Last-Modified was', False),
    ('short body', _short_body, (1, 2), ir.ShortBodyError, 'short body', True),
    ('416 inside the recorded length', _http_416, (2,), ir.ObjectChangedError, 'HTTP 416', False),
    ('plain http final URL', _plain_http_final, (1, 2), ir.UrlRefusedError, 'redirect to', False),
]


class D_Refusals(unittest.TestCase):
    def provoke(self, hook, first_bad, **options):
        fake = FakeTransport(BIG, fault=lambda n, parts: hook(parts) if n >= first_bad else None)
        with self.assertRaises(ir.RemoteZipError) as caught:
            tail_read(ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, sleep=NO_SLEEP, **options))
        return caught.exception, fake

    def test_d_each_failure_is_refused_with_its_own_message(self):
        messages = {}
        for label, hook, starts, exception, phrase, retried in CASES:
            for first_bad in starts:
                with self.subTest(label, first_bad=first_bad):
                    error, fake = self.provoke(hook, first_bad, attempts=2)
                    self.assertIs(type(error), exception)
                    self.assertIn(phrase, str(error))
                    # Protocol violations stop at the first bad answer; only transient failures are retried.
                    self.assertEqual(fake.calls, first_bad + (1 if retried else 0), label)
                    messages[label] = str(error)
        phrases = {label: phrase for label, _, _, _, phrase, _ in CASES}
        for label, message in messages.items():
            for other, phrase in phrases.items():
                self.assertEqual(phrase in message, other == label, (label, other, message))
        self.assertEqual(len(set(messages.values())), len(CASES))

    def test_d_a_refused_range_ignoring_server_is_not_read(self):
        error, fake = self.provoke(_ignore_range, 1)
        self.assertEqual([(reply.consumed, reply.closed) for reply in fake.replies], [(0, True)])

    def test_d_a_cdn_that_ignores_range_is_refused_over_http_too(self):
        with serve(BIG) as (base, state):
            state.mode = 'ignore'
            with self.assertRaisesRegex(ir.RangeIgnoredError, 'ignored Range'):
                ir.RangeFile(f'{base}/object')

    def test_d_short_bodies_from_a_server_are_retried_then_refused(self):
        sleeps = []
        with serve(BIG) as (base, state):
            state.mode = 'short'
            with self.assertRaisesRegex(ir.ShortBodyError, 'short body') as caught:
                ir.RangeFile(f'{base}/object', attempts=3, sleep=sleeps.append)
        self.assertEqual(len(state.log), 3)
        self.assertEqual(sleeps, [ir.BACKOFF_STEP, 2 * ir.BACKOFF_STEP])
        self.assertIsInstance(caught.exception.__cause__, Exception)

    def test_d_a_reset_part_way_through_a_body_is_retried_and_its_bytes_are_counted(self):
        sleeps, fake = [], FakeTransport(BIG, fault=lambda n, p: p.update(break_after=10) if n == 2 else None)
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, sleep=sleeps.append) as f:
            before = f.bytes_fetched
            self.assertEqual(tail_read(f), BIG[-22:])
            self.assertEqual((f.retries, f.requests, sleeps), (1, 3, [ir.BACKOFF_STEP]))
            first, last = fake.log[-1]
            self.assertEqual(f.bytes_fetched - before, 10 + last - first + 1)
        always = FakeTransport(BIG, fault=lambda n, p: p.update(break_after=10) if n >= 2 else None)
        with ir.RangeFile(URL, transport=always, block_size=SMALL_BLOCK, attempts=2, sleep=NO_SLEEP) as f:
            with self.assertRaisesRegex(ir.FetchFailedError, 'ConnectionResetError'):
                ir.open_zip(f)      # zipfile must not turn it into "not a zip file"

    def test_d_transient_errors_back_off_linearly_and_retries_are_bounded(self):
        def failing(last):
            def fault(n, parts):
                if 2 <= n <= last:
                    raise ConnectionResetError('reset by peer')
            return fault
        sleeps, fake = [], FakeTransport(BIG, fault=failing(4))
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, sleep=sleeps.append) as f:
            self.assertEqual(tail_read(f), BIG[-22:])
            self.assertEqual((f.retries, f.requests), (3, 5))
        self.assertEqual(sleeps, [ir.BACKOFF_STEP * n for n in (1, 2, 3)])
        sleeps, fake = [], FakeTransport(BIG, fault=failing(99))
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, sleep=sleeps.append, attempts=14) as f:
            with self.assertRaisesRegex(ir.FetchFailedError, 'on all 14 attempts'):
                tail_read(f)
        self.assertEqual((fake.calls, len(sleeps), max(sleeps)), (1 + 14, 13, ir.BACKOFF_MAX))

    def test_d_http_5xx_and_429_are_transient_but_403_is_not(self):
        for status, calls in ((503, 3), (429, 3), (403, 2)):
            with self.subTest(status):
                fake = FakeTransport(BIG, fault=lambda n, p: p.update(status=status) if n >= 2 else None)
                with self.assertRaises(ir.FetchFailedError):
                    tail_read(ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, sleep=NO_SLEEP, attempts=2))
                self.assertEqual(fake.calls, calls)

    def test_d_a_certificate_failure_is_not_retried(self):
        def fault(n, parts):
            raise urllib.error.URLError(ssl.SSLCertVerificationError(1, 'certificate verify failed'))
        fake = FakeTransport(BIG, fault=fault)
        with self.assertRaisesRegex(ir.FetchFailedError, 'certificate'):
            ir.RangeFile(URL, transport=fake, sleep=NO_SLEEP)
        self.assertEqual(fake.calls, 1)

    def test_d_a_slow_server_hits_the_per_request_timeout(self):
        with serve(BIG) as (base, state):
            state.delay = 1.0
            with self.assertRaisesRegex(ir.FetchFailedError, 'timed out'):
                ir.RangeFile(f'{base}/object', timeout=0.2, attempts=2, sleep=NO_SLEEP)
        self.assertEqual(len(state.log), 2)

    def test_d_a_non_zip_answer_is_refused(self):
        for label, data in (('html', b'<html><body>access denied</body></html>' * 100), ('empty-ish', b'PK')):
            with self.subTest(label):
                fake = FakeTransport(data)
                with self.assertRaisesRegex(ir.NotAZipError, 'not a zip archive'):
                    ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK)

    def test_d_a_zip_signature_without_an_end_record_is_refused_by_open_zip(self):
        fake = FakeTransport(b'PK\x03\x04' + stream('truncated', 5000))
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK) as f:
            with self.assertRaisesRegex(ir.NotAZipError, 'not a readable zip archive'):
                ir.open_zip(f)

    def test_d_zipfile_cannot_mask_a_refusal_as_a_bad_zip(self):
        fake = FakeTransport(BIG, fault=lambda n, p: _etag_changes(p) if n >= 2 else None)
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK) as f:
            with self.assertRaisesRegex(ir.ObjectChangedError, 'ETag was'):
                ir.open_zip(f)

    def test_d_the_final_url_is_reused_and_never_printed_with_its_token(self):
        final = 'https://signed.example/object?token=SECRET'
        fake = FakeTransport(BIG, url=final, fault=lambda n, p: p.update(status=403) if n >= 3 else None)
        with self.assertRaises(ir.FetchFailedError) as caught:
            f = ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK)
            tail_read(f)
            f.seek(2 * MIB)
            f.read(22)
        self.assertEqual(fake.urls, [URL, final, final])
        self.assertIn('signed URL', str(caught.exception))
        self.assertNotIn('SECRET', str(caught.exception))

    def test_d_redirect_policy(self):
        for url in ('https://cdn.example/x', 'http://127.0.0.1:8080/x', 'http://localhost/x'):
            ir.check_url(url)
        for url in ('http://cdn.example/x', 'ftp://127.0.0.1/x', 'file:///x', 'https:///x', 'x-no-scheme',
                    'http://127.0.0.1.example/x', 'http://localhost.example/x', 'http://127.0.0.1@cdn.example/x'):
            with self.subTest(url), self.assertRaises(ir.UrlRefusedError):
                ir.check_url(url)
        ir.check_url('http://127.0.0.1/x', origin='http://127.0.0.1:9/y')
        ir.check_url('https://elsewhere.example/x', origin='https://cdn.example/y')
        with self.assertRaises(ir.UrlRefusedError):
            ir.check_url('http://127.0.0.1/x', origin='https://cdn.example/y')

    def test_d_plain_http_is_refused_before_any_request(self):
        fake = FakeTransport(BIG)
        with self.assertRaisesRegex(ir.UrlRefusedError, 'URL http://cdn.example/x is not allowed'):
            ir.RangeFile('http://cdn.example/x', transport=fake)
        self.assertEqual(fake.calls, 0)

    def test_d_redirects_to_forbidden_targets_are_refused_without_contacting_them(self):
        with serve(BIG) as (base, state):
            for path in ('/to-http', '/to-ftp', '/to-file', '/loop-a'):
                with self.subTest(path), self.assertRaises(ir.UrlRefusedError):
                    ir.RangeFile(f'{base}{path}', sleep=NO_SLEEP)
            self.assertEqual([p for p, _ in state.log if p == '/object'], [])

    def test_d_an_expired_signed_redirect_target_fails_with_its_own_hint(self):
        with serve(BIG) as (base, state):
            state.signed_budget = 2
            with self.assertRaisesRegex(ir.FetchFailedError, 'HTTP 403.*signed URL'):
                with ir.RangeFile(f'{base}/to-signed', block_size=SMALL_BLOCK) as f:
                    tail_read(f)
                    f.seek(2 * MIB)
                    f.read(22)
        self.assertEqual([p for p, _ in state.log], ['/to-signed', '/signed', '/signed', '/signed'])


# --- (e) the cap

class E_Cap(unittest.TestCase):
    def test_e_a_read_over_the_cap_is_refused_before_any_request(self):
        fake = FakeTransport(BIG)
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, max_fetch=4 * SMALL_BLOCK) as f:
            f.seek(10 * SMALL_BLOCK)
            with self.assertRaisesRegex(ir.FetchCapExceededError, 'fetch cap exceeded'):
                f.read(4 * SMALL_BLOCK)
            self.assertEqual((f.requests, f.bytes_fetched), (1, SMALL_BLOCK))
            f.seek(10 * SMALL_BLOCK)
            self.assertEqual(len(f.read(3 * SMALL_BLOCK)), 3 * SMALL_BLOCK)
            self.assertEqual(f.bytes_fetched, 4 * SMALL_BLOCK)
            f.seek(20 * SMALL_BLOCK)
            with self.assertRaises(ir.FetchCapExceededError):
                f.read(1)
            self.assertLessEqual(f.bytes_fetched, f.max_fetch)

    def test_e_a_whole_archive_read_cannot_happen(self):
        fake = FakeTransport(BIG, claimed_size=25_000_000_000)
        with ir.RangeFile(URL, transport=fake) as f:
            self.assertEqual(f.size, 25_000_000_000)
            for read in (lambda: f.read(), lambda: f.read(-1), lambda: f.read(f.size)):
                with self.assertRaisesRegex(ir.FetchCapExceededError, 'more needed'):
                    read()
            self.assertEqual(f.requests, 1)

    def test_e_a_cap_smaller_than_a_block_refuses_at_once(self):
        fake = FakeTransport(BIG)
        with self.assertRaises(ir.FetchCapExceededError):
            ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, max_fetch=SMALL_BLOCK - 1)
        self.assertEqual(fake.calls, 0)

    def test_e_retried_bytes_count_toward_the_cap(self):
        fake = FakeTransport(BIG, fault=lambda n, p: p.update(body=p['body'][:-1]) if n >= 2 else None)
        with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK, max_fetch=2 * SMALL_BLOCK,
                          sleep=NO_SLEEP) as f:
            f.seek(10 * SMALL_BLOCK)
            with self.assertRaises(ir.FetchCapExceededError):
                f.read(1)
            self.assertLessEqual(f.bytes_fetched, f.max_fetch)
            self.assertEqual(f.bytes_fetched, SMALL_BLOCK + SMALL_BLOCK - 1)

    def test_e_the_cli_refuses_a_plan_over_the_cap_before_fetching_components(self):
        fake = FakeTransport(BIG)
        out = fresh('e-cli')
        code, text, err = run([URL, BOARD, '--out', str(out), '--max-fetch-mib', '1'], remote_factory=factory(fake))
        self.assertEqual(code, 2)
        self.assertIn('plan: members to read', text)
        self.assertIn('refused: the plan fetches', err)
        self.assertIn('--max-fetch-mib cap of 1,048,576 bytes', err)
        self.assertFalse(out.exists())
        self.assertEqual(component_reads(fake.log, BIG), [])

    def test_e_the_cap_option_is_validated(self):
        self.assertEqual(run([str(BUILD / 'small' / 'small.ipsw'), BOARD, '--max-fetch-mib', '5'])[0], 2)
        self.assertEqual(run([URL, BOARD, '--max-fetch-mib', '0'])[0], 2)

    def test_e_the_listing_cli_honours_the_cap(self):
        with serve(BIG) as (base, _):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(ir.main([f'{base}/object', '--max-fetch-mib', '1']), 2)
                self.assertEqual(ir.main([f'{base}/object', '--max-fetch-mib', '8']), 0)
        self.assertIn('fetch cap exceeded', err.getvalue())
        self.assertIn(' kernelcache.release.mac17p', out.getvalue())
        self.assertRegex(out.getvalue(), r'\d+ members; \d+ requests, [\d,]+ bytes fetched')


# --- (f) --dry-run

class F_DryRun(unittest.TestCase):
    def test_f_dry_run_reads_no_component_bytes_and_writes_nothing(self):
        fake = FakeTransport(BIG)
        out = fresh('f-dry')
        code, text, err = run([URL, BOARD, '--dry-run', '--out', str(out)], remote_factory=factory(fake))
        self.assertEqual((code, err), (0, ''))
        self.assertFalse(out.exists())
        self.assertIn('dry run: nothing extracted', text)
        for key, path, _ in COMPONENTS:
            self.assertIn(path, text)
        self.assertEqual(component_reads(fake.log, BIG), [])
        so_far = number(r'so far ([\d,]+) bytes fetched', text)
        self.assertEqual(so_far, fake.served)
        self.assertLess(so_far, len(BIG) // 100)

    def test_f_the_dry_run_plan_is_what_a_real_run_then_fetches(self):
        dry = FakeTransport(BIG)
        _, dry_text, _ = run([URL, BOARD, '--dry-run'], remote_factory=factory(dry))
        real = FakeTransport(BIG)
        _, real_text, _ = run([URL, BOARD, '--out', str(fresh('f-real'))], remote_factory=factory(real))
        planned = number(r'total fetch ([\d,]+) bytes', dry_text)
        self.assertEqual(planned, number(r'total fetch ([\d,]+) bytes', real_text))
        self.assertLessEqual(abs(real.served - planned), SMALL_BLOCK * len(COMPONENTS))

    def test_f_a_dry_run_that_would_exceed_the_cap_exits_2(self):
        code, text, err = run([URL, BOARD, '--dry-run', '--max-fetch-mib', '1'],
                              remote_factory=factory(FakeTransport(BIG)))
        self.assertEqual(code, 2)
        self.assertIn('plan: members to read', text)
        self.assertNotIn('dry run: nothing extracted', text)

    def test_f_a_file_dry_run_prints_the_plan_and_extracts_nothing(self):
        shutil.rmtree(BUILD / 'small' / 'extracted', ignore_errors=True)
        code, text, _ = run([str(BUILD / 'small' / 'small.ipsw'), BOARD, '--dry-run'])
        self.assertEqual(code, 0)
        self.assertFalse((BUILD / 'small' / 'extracted').exists())
        self.assertIn('plan: members to read', text)
        self.assertNotIn('remote archive', text)


# --- (g) zip64

class G_Zip64(unittest.TestCase):
    def test_g_the_fixtures_really_are_zip64(self):
        with zipfile.ZipFile(io.BytesIO(Z64_FORCED)) as archive:
            offset = archive.infolist()[0].header_offset
        self.assertEqual(struct.unpack_from('<H', Z64_FORCED, offset + 28)[0], 20)    # local extra: a zip64 field
        self.assertEqual(Z64_RECORDS[-22 - 20 - 56:][:4], b'PK\x06\x06')              # zip64 end record,
        self.assertEqual(Z64_RECORDS[-22 - 20:][:4], b'PK\x06\x07')                   # its locator,
        self.assertEqual(Z64_RECORDS[-22:][:4], b'PK\x05\x06')                        # then the classic end record

    def test_g_describe_reads_the_central_directory_of_a_zip64_archive_and_nothing_else(self):
        for name, data in (('forced', Z64_FORCED), ('records', Z64_RECORDS)):
            with self.subTest(name):
                fake = FakeTransport(data)
                with ir.RangeFile(URL, transport=fake, block_size=SMALL_BLOCK) as f:
                    table = ir.describe(f)
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    local = [(i.filename, i.compress_size, i.header_offset) for i in archive.infolist()]
                    directory = (archive.start_dir // SMALL_BLOCK) * SMALL_BLOCK
                self.assertEqual([tuple(member) for member in table], local)
                self.assertTrue(all(isinstance(member, ir.Member) for member in table))
                self.assertEqual(fake.log[0], (0, SMALL_BLOCK - 1))
                self.assertTrue(all(first >= directory for first, _ in fake.log[1:]), fake.log)
                self.assertEqual(component_reads(fake.log, data), [])

    def test_g_a_longer_local_extra_field_costs_one_more_block_and_no_more(self):
        block, buf = 4096, io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            for name, size in (('a', block - 10 - 31), ('b', 3 * block)):    # a's directory entry ends 10 bytes short of block 1
                with archive.open(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), 'w', force_zip64=True) as dest:
                    dest.write(bytes(size))
        with ir.RangeFile(URL, transport=FakeTransport(buf.getvalue()), block_size=block) as f, ir.open_zip(f) as z:
            span = ir.member_span(z.getinfo('a'))
            self.assertEqual((span[1], f.bytes_to_fetch([span])), (block - 10, 0))   # planned: block 0, already cached
            before = f.bytes_fetched
            z.read('a')
            self.assertEqual(f.bytes_fetched - before, block)   # the local header's 20-byte zip64 field reaches block 1

    def test_g_extraction_from_zip64_archives_is_byte_identical(self):
        for name, data in (('forced', Z64_FORCED), ('records', Z64_RECORDS)):
            with self.subTest(name):
                local_zip = BUILD / f'{name}.ipsw'
                local_zip.write_bytes(data)
                local_out, url_out = fresh(f'g-{name}-local'), fresh(f'g-{name}-url')
                self.assertEqual(run([str(local_zip), BOARD, '--out', str(local_out)])[0], 0)
                self.assertEqual(run([URL, BOARD, '--out', str(url_out)],
                                     remote_factory=factory(FakeTransport(data)))[0], 0)
                self.assertEqual(tree(url_out), tree(local_out))
                self.assertEqual(tree(url_out), EXPECTED)


# --- (h) the local path is unchanged

def helper_samples():
    """Inputs for the pure helpers. GOLDEN_HELPERS was produced by the pre-change tools/extract-firmware.py."""
    blob = stream('golden', 4096)
    wrong_tag = der(0x30, der(0x16, b'IM4X') + der(0x16, b'a') + der(0x16, b'b') + der(0x04, b'payload'))
    return {
        'im4p_payload/short length': (fw.im4p_payload, (im4p('krnl', b'abc'),)),
        'im4p_payload/long-form length': (fw.im4p_payload, (im4p('krnl', blob),)),
        'im4p_payload/not a sequence': (fw.im4p_payload, (blob,)),
        'im4p_payload/wrong tag': (fw.im4p_payload, (wrong_tag,)),
        'im4p_payload/too few elements': (fw.im4p_payload, (der(0x30, der(0x16, b'IM4P')),)),
        'decompress/raw': (fw.decompress, (blob, 'x')),
        'decompress/complzss': (fw.decompress, (complzss(blob), 'x')),
        'lzss/literal then reference': (fw.lzss, (bytes([0x01, 0x41, 0xEE, 0xF0]), 10)),
        'identify/mach-o': (fw.identify, (struct.pack('<IIII', 0xFEEDFACF, 0, 0, 2) + bytes(8),)),
        'identify/lzfse': (fw.identify, (b'bvx2' + bytes(8),)),
        'identify/device tree': (fw.identify, (struct.pack('<II', 12, 3),)),
        'identify/raw': (fw.identify, (blob,)),
    }


def pin(value):
    return hashlib.sha256(repr(value).encode()).hexdigest()[:16]


GOLDEN_HELPERS = {
    'im4p_payload/short length': 'a5525591b56dd371', 'im4p_payload/long-form length': 'd99dfa5b5f1805a7',
    'im4p_payload/not a sequence': 'd99dfa5b5f1805a7', 'im4p_payload/wrong tag': 'cf2280306d8f1a00',
    'im4p_payload/too few elements': 'e73881fc9be550f1', 'decompress/raw': 'd99dfa5b5f1805a7',
    'decompress/complzss': 'd99dfa5b5f1805a7', 'lzss/literal then reference': 'ee3b78aff3a17d5f',
    'identify/mach-o': 'f70d8bf6800732ea', 'identify/lzfse': 'fef00e6cd49783d5',
    'identify/device tree': 'e35ef7ca3474f350', 'identify/raw': 'f6eaf1cbd6b81dac'}
GOLDEN_STDOUT = '''j700ap: ApChipID 0x8140, ApBoardID 0x26, build 26A428
  KernelCache                  kernelcache.release.mac17p              600,000  Mach-O (filetype 2)
  Ap,TrustedExecutionMonitor   txm.macosx.release                      120,000  Mach-O (filetype 2)
  DeviceTree                   DeviceTree.j700ap                        50,000  device tree (probably)
  iBoot                        iBoot.j700.RELEASE                      150,000  raw
  LLB                          LLB.j700.RELEASE                        100,000  raw
  SEP                          sep-firmware.j700.RELEASE               200,000  raw
  SepStage1                    sepstage1.j700.RELEASE                   30,000  raw
  iBootData                    iBootData.j700.RELEASE                   20,000  raw
  SPTM                         sptm.t8140.release                      150,000  Mach-O (filetype 2)
  ExclaveCore                  exclavecore_bundle.t8140.RELEASE        130,000  Mach-O (filetype 2)

10 components in {out}
'''
COMPONENT_LINES = GOLDEN_STDOUT.splitlines()[1:1 + len(COMPONENTS)]
GOLDEN_TREE = '0097d6c57cf20ca5'   # pin() of {output name: SHA-256 of its bytes}, sorted by name
GOLDEN_BAD_BOARD = 'no Erase identity for j000ap. Boards: j700ap j999ap'
GOLDEN_MISSING_LINE = '  iBootData                    MISSING (Firmware/all_flash/iBootData.j700.RELEASE.im4p)'


class H_LocalBehaviour(unittest.TestCase):
    archive = BUILD / 'small' / 'small.ipsw'

    def test_h_pure_helpers_match_the_pre_change_results(self):
        for name, (function, args) in helper_samples().items():
            with self.subTest(name):
                self.assertEqual(pin(function(*args)), GOLDEN_HELPERS[name])

    def test_h_a_local_run_matches_the_pre_change_run(self):
        shutil.rmtree(self.archive.parent / 'extracted', ignore_errors=True)
        code, text, err = run([str(self.archive), BOARD])
        out = self.archive.parent / 'extracted' / BOARD
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(text, GOLDEN_STDOUT.replace('{out}', str(out)))
        self.assertEqual(pin({name: hashlib.sha256(data).hexdigest() for name, data in tree(out).items()}),
                         GOLDEN_TREE)

    def test_h_an_unknown_board_fails_as_before_after_creating_its_directory(self):
        code, text, err = run([str(self.archive), 'j000ap'])
        self.assertEqual((code, text, err), (1, '', GOLDEN_BAD_BOARD + '\n'))
        self.assertTrue((self.archive.parent / 'extracted' / 'j000ap').is_dir())

    def test_h_a_member_missing_from_the_archive_is_reported_as_before(self):
        missing = BUILD / 'missing' / 'missing.ipsw'
        missing.parent.mkdir()
        missing.write_bytes(build_zip(drop=('iBootData',)))
        code, text, _ = run([str(missing), BOARD])
        self.assertEqual(code, 0)
        self.assertIn(GOLDEN_MISSING_LINE, text.splitlines())
        self.assertIn('10 components in', text)


if __name__ == '__main__':
    unittest.main()
