#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for tools/extract-basesystem.py and the URL path of tools/extract-root-hash.py and tools/prepare-trust-cache.py.

    python3 tools/test-extract-basesystem.py [-v]

Nothing here reaches a network: a module-wide guard fails every socket connect and name lookup, HTTP is the in-process fake of
tools/test-ipsw-remote.py, and the key service is a function. No Apple data is involved: the IPSW, the BaseSystem image, the AEA
archive, the key pair and the pins are synthetic (the pins are built for the synthetic bytes). The real aea and hdiutil of macOS run
on synthetic files, and the HPKE step needs the Python 'cryptography' package: a case that cannot run says SKIP and why.
Scratch files live under build/test-extract-basesystem (git-ignored) and are removed.

(a) the pure rules: manifest, AEA prologue, key URL, the one-request key service, HPKE, raw-image pins, the disk floor, the member
allowlist; (b) the whole flow on a synthetic IPSW with seeded failures; (c) the URL path of the two small extractors.
"""
import base64
import contextlib
import copy
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import plistlib
import shutil
import socket
import struct
import subprocess
import sys
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'probes'))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tir = load('test_ipsw_remote', 'test-ipsw-remote.py')       # the fake HTTP transport and the IM4P/DER helpers
import ipsw_remote as ir  # noqa: E402
import ramdisk  # noqa: E402
import trustcache  # noqa: E402

tool = load('extract_basesystem', 'extract-basesystem.py')
root_hash_tool = load('extract_root_hash', 'extract-root-hash.py')
cache_tool = load('prepare_trust_cache', 'prepare-trust-cache.py')

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite
    HAVE_HPKE = True
except ImportError:
    HAVE_HPKE = False
HAVE_MAC_TOOLS = bool(shutil.which('aea') and shutil.which('hdiutil'))
needs_hpke = unittest.skipUnless(HAVE_HPKE, "the Python 'cryptography' package with hazmat.primitives.hpke is not installed")
needs_mac = unittest.skipUnless(HAVE_HPKE and HAVE_MAC_TOOLS, 'needs cryptography with HPKE and the macOS aea and hdiutil')

BUILD = ROOT / 'build' / 'test-extract-basesystem'
GIB, MIB = 1 << 30, 1 << 20
URL = 'https://cdn.example/UniversalMac_Restore.ipsw'      # never resolved: a fake transport answers
KEY_URL = 'https://wkms-public.apple.com/fcs-keys/' + 'A' * 44
AEA_NAME = '022-99999-999.dmg.aea'
ROOT_HASH_NAME, CACHE_NAME = f'Firmware/{AEA_NAME}.root_hash', f'Firmware/{AEA_NAME}.trustcache'
BLOCK = 4096
sha256, sha384 = (lambda data: hashlib.sha256(data).hexdigest()), (lambda data: hashlib.sha384(data).digest())
F = types.SimpleNamespace()


# --- the synthetic BaseSystem, archive, IPSW and pins

def make_raw(blocks=512):
    raw = bytearray(hashlib.shake_256(b'raw').digest(blocks * 4096))
    raw[32:36] = b'NXSB'
    struct.pack_into('<I', raw, 36, 4096)
    struct.pack_into('<Q', raw, 40, blocks)
    return bytes(raw)


def make_cache(count=5):
    entries = sorted(hashlib.sha256(bytes([i])).digest()[:20] + struct.pack('<BBH', 2, 0, 2) for i in range(count))
    return struct.pack('<I16sI', 2, bytes(16), count) + b''.join(entries)


def run_tool(argv, **kwargs):
    done = subprocess.run(argv, capture_output=True, text=True, **kwargs)
    assert done.returncode == 0, (argv[:3], done.stderr)


def make_aea(directory, tag, dmg, key, wrapped, private):
    """dmg.aea (profile 1, key/value auth data as Apple's) of the file `dmg` under `key`, with `wrapped` sealed to `private`."""
    sealed = Suite(KEM.P256, KDF.HKDF_SHA256, AEAD.AES_256_GCM).encrypt(wrapped, private.public_key())
    response = json.dumps({'enc-request': base64.b64encode(sealed[:65]).decode(),
                           'wrapped-key': base64.b64encode(sealed[65:]).decode()}).encode()
    keyfile = directory / f'{tag}.key'
    descriptor = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write('hex:' + key.hex())
    target = directory / f'{tag}.aea'
    run_tool(['aea', 'encrypt', '-profile', '1', '-i', str(dmg), '-o', str(target), '-key', str(keyfile),
              '-auth-data-key', 'com.apple.wkms.url', '-auth-data-value', 'hex:' + b'https://wkms.sd.apple.com'.hex(),
              '-auth-data-key', 'com.apple.wkms.fcs-response', '-auth-data-value', 'hex:' + response.hex(),
              '-auth-data-key', 'com.apple.wkms.fcs-key-url', '-auth-data-value', 'hex:' + KEY_URL.encode().hex()])
    keyfile.unlink()
    return target.read_bytes()


def fake_aea(size, key_url=KEY_URL):
    """A member that starts like Apple's (AEA1, profile 1, the key URL, a wrapped key) but cannot be decrypted: for plan-level tests."""
    response = json.dumps({'enc-request': base64.b64encode(bytes(65)).decode(), 'wrapped-key': base64.b64encode(bytes(48)).decode()}).encode()
    auth = b''.join(struct.pack('<I', 5 + len(key) + len(value)) + key + b'\0' + value
                    for key, value in ((b'com.apple.wkms.fcs-response', response), (b'com.apple.wkms.fcs-key-url', key_url.encode())))
    return b'AEA1' + struct.pack('<II', 1, len(auth)) + auth + hashlib.shake_256(b'body').digest(size)


def manifest_of(aea, root_im4p, cache_im4p, digest=None):
    def entry(path, blob):
        return {'Info': {'Path': path}, 'Digest': sha384(blob)}
    mine = {'BaseSystem': {'Info': {'Path': AEA_NAME}, 'Digest': digest or sha384(aea)},
            'BaseSystemVolume': entry(ROOT_HASH_NAME, root_im4p), 'Ap,BaseSystemTrustCache': entry(CACHE_NAME, cache_im4p)}
    decoy = {'BaseSystem': entry('decoy.dmg.aea', b'x'), 'Ap,BaseSystemTrustCache': entry(CACHE_NAME, cache_im4p)}
    return {'ProductBuildVersion': '26A428', 'BuildIdentities': [
        {'Info': {'DeviceClass': 'j700ap', 'Variant': 'Customer Erase Install (IPSW)'}, 'Manifest': decoy},
        {'Info': {'DeviceClass': 'j999ap', 'Variant': 'macOS Customer'}, 'Manifest': decoy},
        {'Info': {'DeviceClass': 'j700ap', 'Variant': 'macOS Customer'}, 'Manifest': mine}]}


def build_zip(aea, root_im4p, cache_im4p, digest=None, manifest=None):
    """The synthetic IPSW: decoys before and after, so that a read of a member that is not consented shows in the request log."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        def add(name, data, method=zipfile.ZIP_STORED):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = method
            archive.writestr(info, data)
        add('kernelcache.release.mac17p', tir.stream('kernel', 300_000))
        add('BuildManifest.plist', plistlib.dumps(manifest or manifest_of(aea, root_im4p, cache_im4p, digest)), zipfile.ZIP_DEFLATED)
        add(ROOT_HASH_NAME, root_im4p, zipfile.ZIP_DEFLATED)
        add(CACHE_NAME, cache_im4p, zipfile.ZIP_DEFLATED)
        add(AEA_NAME, aea)
        add('Firmware/decoy.bin', tir.stream('decoy', 300_000))
    return buffer.getvalue()


def setUpModule():
    def refuse(*args, **kwargs):
        raise AssertionError('a test tried to use the network')
    for patch in (mock.patch.object(socket.socket, 'connect', refuse), mock.patch.object(socket, 'getaddrinfo', refuse)):
        patch.start()
        unittest.addModuleCleanup(patch.stop)
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    unittest.addModuleCleanup(shutil.rmtree, BUILD, ignore_errors=True)
    F.raw, F.root_payload, F.cache_payload = make_raw(), hashlib.shake_256(b'root').digest(208), make_cache()
    F.root_im4p, F.cache_im4p = tir.im4p('root', F.root_payload), tir.im4p('bstc', F.cache_payload)
    root_spec = ramdisk.RootHashSpec(image_sha256=sha256(F.raw), payload_sha256=sha256(F.root_payload), payload_bytes=208,
                                     component='BaseSystemVolume', component_sha256=sha256(F.root_im4p),
                                     property_name='system-volume-auth-blob', device_class='j700ap', build_id='26A428')
    cache_spec = trustcache.TrustCacheSpec(filename='basesystem-26A428.trustcache', component='Ap,BaseSystemTrustCache',
                                           member=CACHE_NAME, component_sha256=sha256(F.cache_im4p),
                                           component_sha384=sha384(F.cache_im4p).hex(), payload_sha256=sha256(F.cache_payload), entries=5)
    F.profile = types.SimpleNamespace(root_hash=root_spec, trust_cache=cache_spec, firmware_subdir='j700ap')
    F.plan_aea = fake_aea(2 * MIB)
    F.plan_zip = build_zip(F.plan_aea, F.root_im4p, F.cache_im4p)
    if not (HAVE_HPKE and HAVE_MAC_TOOLS):
        return
    fixtures = BUILD / 'fixture'
    fixtures.mkdir()
    (fixtures / 'raw.bin').write_bytes(F.raw)
    run_tool(['hdiutil', 'convert', str(fixtures / 'raw.bin'), '-srcimagekey', 'diskimage-class=CRawDiskImage', '-format', 'ULFO',
              '-o', str(fixtures / 'image')])
    F.private, F.other_private = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    F.pem = pem_of(F.private)
    F.key, F.other_key = os.urandom(32), os.urandom(32)
    F.aea = make_aea(fixtures, 'good', fixtures / 'image.dmg', F.key, F.key, F.private)
    F.aea_wrongkey = make_aea(fixtures, 'wrong', fixtures / 'image.dmg', F.key, F.other_key, F.private)
    F.zip = build_zip(F.aea, F.root_im4p, F.cache_im4p)


def pem_of(private):
    return private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


class KeyTransport:
    """The key service as a function: replies in order, every call recorded."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, agent, timeout):
        self.calls.append((url, agent))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def ok(pem):
    return 200, {'Content-Type': 'application/x-pem-file'}, pem


MOVED = (301, {'Content-Type': 'text/html', 'Content-Length': '162', 'Server': 'Apple',
               'Location': 'https://wkms-public.apple.com/fcs-keys/elsewhere/'}, b'<html><center><h1>301 Moved Permanently</h1></center></html>')


def run(argv, **hooks):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tool.main(argv, **hooks)
    return code, out.getvalue(), err.getvalue()


class Flow:
    """One scratch run: directories, the fake archive transport, the hooks and the command line."""

    def __init__(self, name, data=None, key=None, profile=None, free=lambda path: 1 << 40, block=BLOCK):
        self.root = BUILD / 'run' / name
        self.work, self.keys, self.out = self.root / 'work', self.root / 'keys', self.root / 'out'
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.fake = tir.FakeTransport(data if data is not None else F.zip)
        self.key = key if key is not None else KeyTransport(ok(getattr(F, 'pem', b'')))
        self.hooks = dict(remote_factory=tir.factory(self.fake, block=block), profile_factory=lambda name: profile or F.profile,
                          key_transport=self.key, disk_free=free, prerequisites=lambda decrypt, convert, first: None)

    def argv(self, *extra, fetch=True):
        return [URL] + (['--fetch'] if fetch else []) + ['--out', str(self.out), '--work', str(self.work), '--key-dir', str(self.keys),
                                                         '--threads', '2'] + list(extra)

    def go(self, *extra, fetch=True, **hooks):
        return run(self.argv(*extra, fetch=fetch), **dict(self.hooks, **hooks))

    def left(self, directory):
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else None


def touched(log, data, names):
    """Bytes of the named members that the request log covers."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        spans = [ir.member_span(archive.getinfo(name)) for name in names]
    return sum(max(0, min(last + 1, stop) - max(first, start)) for first, last in log for start, stop in spans)


# --- (a) the pure rules

class A_Rules(unittest.TestCase):
    def test_locate_accepts_the_manifest_and_refuses_each_disagreement(self):
        good = manifest_of(b'a', F.root_im4p, F.cache_im4p)
        located = tool.locate(good, F.profile)
        self.assertEqual((located.aea, located.root_hash, located.trust_cache), (AEA_NAME, ROOT_HASH_NAME, CACHE_NAME))

        def broken(edit):
            manifest = copy.deepcopy(good)
            edit(manifest)
            return manifest
        mine = lambda manifest: manifest['BuildIdentities'][2]['Manifest']  # noqa: E731
        cases = {'build': lambda m: m.update(ProductBuildVersion='26A999'),
                 'two identities': lambda m: m['BuildIdentities'].append(dict(m['BuildIdentities'][2])),
                 'no BaseSystem': lambda m: mine(m).pop('BaseSystem'),
                 'root-hash path': lambda m: mine(m)['BaseSystemVolume']['Info'].update(Path='Firmware/other.root_hash'),
                 'trust-cache path': lambda m: mine(m)['Ap,BaseSystemTrustCache']['Info'].update(Path='Firmware/other.trustcache'),
                 'digest length': lambda m: mine(m)['BaseSystem'].update(Digest=bytes(20))}
        for label, edit in cases.items():
            with self.subTest(label), self.assertRaises(tool.Refusal):
                tool.locate(broken(edit), F.profile)

    def test_prologue_parse_and_its_refusals(self):
        def entry(key, value):
            body = key + b'\0' + value
            return struct.pack('<I', 4 + len(body)) + body
        auth = entry(b'k1', b'v1') + entry(b'k2', b'')
        head = b'AEA1' + struct.pack('<II', 1, len(auth)) + auth
        parsed = tool.parse_prologue(head)
        self.assertEqual((parsed.profile, parsed.scrypt, parsed.entries), (1, 0, {'k1': b'v1', 'k2': b''}))
        self.assertEqual(tool.parse_prologue(b'AEA1' + struct.pack('<II', 1 | 3 << 24, 0)).scrypt, 3)
        for label, bad in {'magic': b'AEA2' + head[4:], 'short': head[:-1], 'length': b'AEA1' + struct.pack('<II', 1, 1 << 20),
                           'no separator': b'AEA1' + struct.pack('<II', 1, 9) + struct.pack('<I', 9) + b'abcde',
                           'entry too long': b'AEA1' + struct.pack('<II', 1, 8) + struct.pack('<I', 99) + b'abcd'}.items():
            with self.subTest(label), self.assertRaises(tool.Refusal):
                tool.parse_prologue(bad)

    def test_key_url_is_held_to_the_consented_host_and_shape(self):
        tool.check_key_url(KEY_URL)
        for url in ('http://wkms-public.apple.com/fcs-keys/x', 'https://wkms-public.apple.com.evil.example/fcs-keys/x',
                    'https://wkms.sd.apple.com/fcs-keys/x', 'https://wkms-public.apple.com/fcs-keys/', 'https://wkms-public.apple.com/other/x',
                    'https://wkms-public.apple.com/fcs-keys/x?a=b', 'https://wkms-public.apple.com/fcs-keys/x#f',
                    'https://wkms-public.apple.com:8443/fcs-keys/x', 'https://user@wkms-public.apple.com/fcs-keys/x',
                    'https://wkms-public.apple.com/fcs-keys/x/y', 'https://wkms-public.apple.com:bad/fcs-keys/x'):
            with self.subTest(url), self.assertRaises(tool.KeyServiceRefusal):
                tool.check_key_url(url)
        self.assertEqual(tool.shape(KEY_URL), 'https://wkms-public.apple.com/fcs-keys/<44 characters>')

    def test_key_target_needs_the_symmetric_profile_and_sizes(self):
        entries = {'com.apple.wkms.fcs-key-url': KEY_URL.encode(),
                   'com.apple.wkms.fcs-response': json.dumps({'enc-request': base64.b64encode(bytes(65)).decode(),
                                                              'wrapped-key': base64.b64encode(bytes(48)).decode()}).encode()}
        url, enc, wrapped = tool.key_target(tool.Prologue(1, 0, entries))
        self.assertEqual((url, len(enc), len(wrapped)), (KEY_URL, 65, 48))
        for label, prologue in {'profile 3': tool.Prologue(3, 0, entries), 'scrypt': tool.Prologue(1, 2, entries),
                                'no url': tool.Prologue(1, 0, {k: v for k, v in entries.items() if k.endswith('response')}),
                                'bad json': tool.Prologue(1, 0, dict(entries, **{'com.apple.wkms.fcs-response': b'{'})),
                                'short enc': tool.Prologue(1, 0, dict(entries, **{'com.apple.wkms.fcs-response': json.dumps(
                                    {'enc-request': base64.b64encode(bytes(64)).decode(), 'wrapped-key': base64.b64encode(bytes(48)).decode()}).encode()}))}.items():
            with self.subTest(label), self.assertRaises(tool.Refusal):
                tool.key_target(prologue)

    def test_key_service_makes_one_request_and_never_retries(self):
        transport = KeyTransport(ok(b'pem'), ok(b'pem'))
        service = tool.KeyService(transport)
        self.assertEqual(service.fetch(KEY_URL), b'pem')
        with self.assertRaisesRegex(tool.KeyServiceRefusal, 'already made'):
            service.fetch(KEY_URL)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1], 'snapintosh-extract-basesystem')
        for label, reply in {'429': (429, {'Retry-After': '9'}, b'slow down'), '403': (403, {}, b'denied'),
                             'timeout': TimeoutError('timed out'), 'oversize': ok(bytes(9000))}.items():
            transport = KeyTransport(reply, ok(b'second'))
            with self.subTest(label), self.assertRaises(tool.KeyServiceRefusal) as caught:
                tool.KeyService(transport).fetch(KEY_URL)
            self.assertEqual(len(transport.calls), 1, 'a failed request is never retried')
            if label == '429':
                self.assertIn('HTTP 429', str(caught.exception))
                self.assertIn('Retry-After: 9', str(caught.exception))

    def test_a_redirect_is_refused_by_default_and_reported_with_its_location(self):
        transport = KeyTransport(MOVED, ok(b'pem'))
        with self.assertRaises(tool.KeyServiceRefusal) as caught:
            tool.KeyService(transport).fetch(KEY_URL)
        text = str(caught.exception)
        self.assertIn('HTTP 301', text)
        self.assertIn('Location: https://wkms-public.apple.com/fcs-keys/elsewhere/', text)
        self.assertIn('Server: Apple', text)
        self.assertEqual(len(transport.calls), 1)

    def test_allowed_redirect_hops_are_bounded_traced_and_vetted(self):
        transport = KeyTransport(MOVED, ok(b'pem'))
        service = tool.KeyService(transport, redirects=1)
        self.assertEqual(service.fetch(KEY_URL), b'pem')
        self.assertEqual([url for url, _ in transport.calls], [KEY_URL, 'https://wkms-public.apple.com/fcs-keys/elsewhere/'])
        self.assertEqual(len(service.trace), 3)
        self.assertIn('redirect target: https://wkms-public.apple.com/fcs-keys/elsewhere/<0 characters>', service.trace[1])
        for label, shown in {'https://evil.example/x?k=v': 'redirect target: https://evil.example/<1 characters> with a query',
                             'http://wkms-public.apple.com/x': 'redirect target: http://wkms-public.apple.com/<1 characters>',
                             'https://wkms-public.apple.com:443/x': 'redirect target: https://wkms-public.apple.com:443/<1 characters>'}.items():
            service = tool.KeyService(KeyTransport((301, {'Location': label}, b''), ok(b'pem')), redirects=1)
            with self.subTest(label), self.assertRaises(tool.KeyServiceRefusal):
                service.fetch(KEY_URL)
            self.assertEqual(service.trace[1], shown, 'a refused target is shown to the operator, without any query value')
        for label, location in {'other host': 'https://evil.example/x', 'plain http': 'http://wkms-public.apple.com/x',
                                'credentials': 'https://u@wkms-public.apple.com/x', 'port': 'https://wkms-public.apple.com:9/x',
                                'look-alike suffix': 'https://fcs-keys-pub-prod.evilcdn-apple.com/fcs-keys/x',
                                'apple name inside another domain': 'https://fcs-keys-pub-prod.cdn-apple.com.evil.example/fcs-keys/x',
                                'plain http on the cdn': 'http://fcs-keys-pub-prod.cdn-apple.com/fcs-keys/x'}.items():
            transport = KeyTransport((302, {'Location': location}, b''), ok(b'pem'))
            with self.subTest(label), self.assertRaises(tool.KeyServiceRefusal):
                tool.KeyService(transport, redirects=2).fetch(KEY_URL)
            self.assertEqual(len(transport.calls), 1, 'the vetted target is never requested')
        cdn = 'https://fcs-keys-pub-prod.cdn-apple.com/fcs-keys/elsewhere/'      # the redirect that the real key service answered (OBSERVED once)
        transport = KeyTransport((301, {'Location': cdn}, b''), ok(b'pem'))
        self.assertEqual(tool.KeyService(transport, redirects=1).fetch(KEY_URL), b'pem')
        self.assertEqual([url for url, _ in transport.calls], [KEY_URL, cdn])
        transport = KeyTransport(MOVED, MOVED, ok(b'pem'))
        with self.assertRaises(tool.KeyServiceRefusal):
            tool.KeyService(transport, redirects=1).fetch(KEY_URL)
        self.assertEqual(len(transport.calls), 2, 'the second redirect exceeds the allowance')

    @needs_hpke
    def test_hpke_opens_the_wrapped_key_and_refuses_everything_else(self):
        private, other = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        key = os.urandom(32)
        sealed = Suite(KEM.P256, KDF.HKDF_SHA256, AEAD.AES_256_GCM).encrypt(key, private.public_key())
        enc, wrapped = sealed[:65], sealed[65:]
        self.assertEqual(tool.derive_archive_key(pem_of(private), enc, wrapped), key)
        wrong_curve = pem_of(ec.generate_private_key(ec.SECP384R1()))
        tampered = bytes([wrapped[0] ^ 1]) + wrapped[1:]
        for label, args in {'another key': (pem_of(other), enc, wrapped), 'wrong curve': (wrong_curve, enc, wrapped),
                            'not a pem': (b'-----BEGIN NOTHING-----', enc, wrapped), 'tampered': (pem_of(private), enc, tampered),
                            'bad encapsulation': (pem_of(private), bytes(65), wrapped)}.items():
            with self.subTest(label), self.assertRaises(tool.Refusal) as caught:
                tool.derive_archive_key(*args)
            self.assertNotIn(key.hex(), str(caught.exception))

    def test_hpke_without_the_package_is_a_clear_refusal(self):
        with mock.patch.dict(sys.modules, {'cryptography.hazmat.primitives.hpke': None}):
            with self.assertRaisesRegex(tool.Refusal, 'cryptography'):
                tool.derive_archive_key(b'x', bytes(65), bytes(48))

    def test_raw_image_pins_with_seeded_wrong_cases(self):
        directory = BUILD / 'raw'
        directory.mkdir(exist_ok=True)
        spec = F.profile.root_hash

        def check(label, data, expect, pin=spec):
            path = directory / label.replace(' ', '-')
            path.write_bytes(data)
            if expect is None:
                self.assertEqual(tool.check_raw(path, pin), (len(data), sha256(data)))
                ramdisk.load_ramdisk(path, 0, 1 << 36).close()      # the loader accepts what this accepts
            else:
                with self.subTest(label), self.assertRaisesRegex(tool.Refusal, expect):
                    tool.check_raw(path, pin)
                if expect != 'SHA-256':
                    with self.assertRaises(ValueError):
                        ramdisk.load_ramdisk(path, 0, 1 << 36)          # ...and refuses what this refuses on geometry
        good = bytearray(F.raw)
        check('good', bytes(good), None)
        check('wrong digest', bytes(good), 'SHA-256', dataclasses.replace(spec, image_sha256='0' * 64))
        check('one bit flipped', bytes(good[:1000]) + bytes([good[1000] ^ 1]) + bytes(good[1001:]), 'SHA-256')
        check('size not 16 KiB', bytes(good) + bytes(4096), '16 KiB')
        check('truncated', bytes(good[:-0x4000]), 'geometry')
        no_magic = bytearray(good)
        no_magic[32:36] = b'XXXX'
        check('no magic', bytes(no_magic), 'NXSB')
        wrong_block = bytearray(good)
        struct.pack_into('<I', wrong_block, 36, 8192)
        check('block size', bytes(wrong_block), 'geometry')
        wrong_count = bytearray(good)
        struct.pack_into('<Q', wrong_count, 40, 511)
        check('block count', bytes(wrong_count), 'geometry')
        check('tiny', bytes(100), '16 KiB')

    def test_the_disk_floor_and_a_path_that_does_not_exist_yet(self):
        disk = tool.Disk(25 * GIB, lambda path: 30 * GIB)
        self.assertEqual(disk.room(BUILD, 4 * GIB, 'a stage'), 26 * GIB)
        with self.assertRaisesRegex(tool.Refusal, 'would leave 24.0 GiB free, below the floor of 25 GiB'):
            disk.room(BUILD, 6 * GIB, 'a stage')
        self.assertGreater(tool.free_bytes(BUILD / 'does' / 'not' / 'exist'), 0)

    def test_a_watched_command_is_killed_and_its_output_deleted_when_the_floor_is_crossed(self):
        BUILD.mkdir(exist_ok=True)
        output = BUILD / 'watched.out'
        output.write_bytes(b'partial')
        started = os.times().elapsed
        with self.assertRaisesRegex(tool.Refusal, 'free space reached the floor'):
            tool.run_watched(['sleep', '30'], output, tool.Disk(10, lambda path: 0), poll=0.05)
        self.assertFalse(output.exists())
        self.assertLess(os.times().elapsed - started, 10)

    def test_only_the_consented_members_can_be_read(self):
        data = build_zip(bytes(5000), F.root_im4p, F.cache_im4p)
        fake = tir.FakeTransport(data)
        with ir.RangeFile(URL, transport=fake, block_size=BLOCK, sleep=tir.NO_SLEEP) as remote:
            members = tool.Members(ir.open_zip(remote))
            members.read('BuildManifest.plist')
            before = fake.calls
            for name in ('kernelcache.release.mac17p', 'Firmware/decoy.bin', AEA_NAME, ROOT_HASH_NAME):
                for method in (members.read, members.getinfo, members.open):
                    with self.subTest(name, method=method.__name__), self.assertRaisesRegex(tool.Refusal, 'not one of the consented'):
                        method(name)
            self.assertEqual(fake.calls, before, 'a refused member costs no request')
            members.allow(ROOT_HASH_NAME)
            self.assertEqual(members.read(ROOT_HASH_NAME), F.root_im4p)

    def test_write_new_and_the_key_file(self):
        directory = BUILD / 'files'
        directory.mkdir(exist_ok=True)
        tool.write_new(directory / 'a', b'one')
        tool.write_new(directory / 'a', b'one')
        with self.assertRaisesRegex(tool.Refusal, 'refusing to replace'):
            tool.write_new(directory / 'a', b'two')
        key = bytes(range(32))
        path = tool.save_key(directory, key)
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')
        self.assertEqual(path.read_text(), 'hex:' + key.hex())
        self.assertEqual(tool.load_key(path), key)
        with self.assertRaisesRegex(tool.Refusal, 'leftover'):
            tool.save_key(directory, bytes(32))
        self.assertEqual(tool.load_key(path), key, 'a refused second key file leaves the first alone')

    def test_load_key_refuses_what_this_tool_did_not_write_without_echoing_it(self):
        directory = BUILD / 'keyfiles'
        directory.mkdir(exist_ok=True)
        key = bytes(range(32))
        for label, text, mode in (('readable by others', 'hex:' + key.hex(), 0o644), ('garbage', 'left over', 0o600),
                                  ('short', 'hex:' + key.hex()[:-2], 0o600), ('not hex', 'hex:' + 'zz' * 32, 0o600),
                                  ('no prefix', key.hex() + '0000', 0o600), ('spaces', 'hex:' + ' '.join(['00'] * 22), 0o600)):
            path = directory / label.replace(' ', '-')
            path.write_text(text)
            path.chmod(mode)
            with self.subTest(label), self.assertRaisesRegex(tool.Refusal, 'nothing was requested') as caught:
                tool.load_key(path)
            self.assertNotIn(key.hex(), str(caught.exception))
        link = directory / 'link'
        link.symlink_to(directory / 'garbage')
        with self.assertRaisesRegex(tool.Refusal, 'regular file'):
            tool.load_key(link)

    def test_the_new_and_changed_files_stay_inside_the_caps(self):
        for name in ('extract-basesystem.py', 'test-extract-basesystem.py', 'extract-root-hash.py', 'prepare-trust-cache.py', 'ipsw_remote.py'):
            path = ROOT / 'tools' / name
            self.assertLess(len(path.read_text().splitlines()), 1500, name)
            self.assertLess(path.stat().st_size, 51200, name)


# --- (b) the plan and the refusals before any root byte, on a member that only starts like Apple's

class B_Plan(unittest.TestCase):
    def test_plan_only_is_the_default_and_fetches_no_root_bytes(self):
        flow = Flow('plan', data=F.plan_zip)
        code, out, err = flow.go(fetch=False)
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn('pins match the IPSW', out)
        self.assertIn('plan only: no key request, no root bytes', out)
        self.assertIn('https://wkms-public.apple.com/fcs-keys/<44 characters>', out)
        self.assertEqual(flow.key.calls, [])
        self.assertEqual([flow.left(d) for d in (flow.work, flow.keys, flow.out)], [None, None, None], 'plan only writes nothing')
        self.assertLess(touched(flow.fake.log, F.plan_zip, [AEA_NAME]), len(F.plan_aea) // 8, 'only the prologue of the member was read')

    def test_the_floor_stops_the_flow_before_any_key_request(self):
        flow = Flow('floor', data=F.plan_zip, free=lambda path: 10 * GIB)
        code, out, err = flow.go()
        self.assertEqual(code, 1)
        self.assertIn('below the floor of 25 GiB', err)
        self.assertEqual((flow.key.calls, flow.left(flow.work)), ([], None))

    def test_the_cap_stops_the_plan(self):
        flow = Flow('cap', data=F.plan_zip)
        code, out, err = flow.go('--max-fetch-mib', '1')
        self.assertEqual(code, 2, err)
        self.assertIn('over the --max-fetch-mib cap', err)
        self.assertEqual(flow.key.calls, [])

    def test_a_key_service_answer_other_than_200_stops_with_everything_it_said(self):
        flow = Flow('moved', data=F.plan_zip, key=KeyTransport(MOVED, ok(b'never asked for')))
        code, out, err = flow.go()
        self.assertEqual(code, 3)
        for text in ('HTTP 301', 'Location: https://wkms-public.apple.com/fcs-keys/elsewhere/', 'No retry, no other source'):
            self.assertIn(text, err)
        self.assertEqual(len(flow.key.calls), 1)
        self.assertEqual((flow.left(flow.work), flow.left(flow.keys), flow.left(flow.out)), (None, None, None))
        self.assertLess(flow.fake.served, len(F.plan_aea) // 8, 'no root bytes were fetched')

    def test_a_member_whose_key_url_is_elsewhere_is_refused_before_any_request(self):
        for url in ('https://example.org/fcs-keys/' + 'A' * 44, 'http://wkms-public.apple.com/fcs-keys/' + 'A' * 44):
            with self.subTest(url):
                flow = Flow('elsewhere', data=build_zip(fake_aea(4096, url), F.root_im4p, F.cache_im4p))
                code, out, err = flow.go()
                self.assertEqual(code, 3)
                self.assertIn('nothing was requested', err)
                self.assertEqual(flow.key.calls, [])

    def test_a_leftover_state_this_tool_cannot_continue_from_is_refused_before_any_key_request(self):
        cases = (('an archive without its key file', {'work': [AEA_NAME]}, 'exists without its key file'),
                 ('an archive beside an unwrapped image', {'work': [AEA_NAME, AEA_NAME[:-4]]}, 'both '),
                 ('a key file that is not one', {'keys': ['basesystem.key']}, 'not a key file'))
        for label, files, expect in cases:
            with self.subTest(label):
                flow = Flow('leftover', data=F.plan_zip)
                for where, names in files.items():
                    (flow.root / where).mkdir(exist_ok=True)
                    for name in names:
                        (flow.root / where / name).write_bytes(b'left over')
                        (flow.root / where / name).chmod(0o600)
                code, out, err = flow.go()
                self.assertEqual(code, 1)
                self.assertIn(expect, err)
                self.assertIn('nothing was requested', err)
                self.assertEqual(flow.key.calls, [])
                for where, names in files.items():
                    for name in names:
                        self.assertEqual((flow.root / where / name).read_bytes(), b'left over', 'a leftover is never deleted or overwritten')

    def test_an_existing_output_is_trusted_only_if_it_matches_its_pin(self):
        flow = Flow('present', data=F.plan_zip)
        flow.out.mkdir()
        (flow.out / 'basesystem-26A428.apfs').write_bytes(b'x' * 100)
        code, out, err = flow.go()
        self.assertEqual(code, 1)
        self.assertIn('16 KiB', err)
        self.assertEqual((flow.fake.calls, flow.key.calls), (0, []), 'nothing was requested')
        self.assertEqual((flow.out / 'basesystem-26A428.apfs').read_bytes(), b'x' * 100, 'a wrong file is reported, not replaced')

    def test_missing_prerequisites_are_refused_before_the_one_key_request(self):
        flow = Flow('prerequisites', data=F.plan_zip)
        del flow.hooks['prerequisites']                       # the real check, not the test bypass
        with mock.patch.dict(sys.modules, {'cryptography.hazmat.primitives.hpke': None}):
            code, out, err = flow.go()
        self.assertEqual(code, 1)
        self.assertIn('"cryptography"', err)
        self.assertIn('nothing was requested', err)
        self.assertEqual(flow.key.calls, [], 'the single key request is not spent on a run that cannot finish')

    @needs_hpke
    def test_the_macos_tools_are_checked_unless_a_hook_stands_in_for_them(self):
        tool.check_prerequisites()
        with mock.patch.object(tool.shutil, 'which', lambda name: None):
            for missing in ('aea', 'hdiutil'):
                with self.subTest(missing), self.assertRaisesRegex(tool.Refusal, f'{missing} not found'):
                    tool.check_prerequisites(decrypt=None if missing == 'aea' else print, convert=None if missing == 'hdiutil' else print)
            tool.check_prerequisites(decrypt=print, convert=print)

    @needs_hpke
    def test_a_resumed_run_checks_only_what_its_remaining_stages_need(self):
        no_hpke = mock.patch.dict(sys.modules, {'cryptography.hazmat.primitives.hpke': None})
        with no_hpke, mock.patch.object(tool.shutil, 'which', lambda name: name):
            tool.check_prerequisites(first='decrypt')
            tool.check_prerequisites(first='convert')
            with self.assertRaisesRegex(tool.Refusal, '"cryptography"'):
                tool.check_prerequisites(first='key')
        with mock.patch.object(tool.shutil, 'which', lambda name: name if name == 'hdiutil' else None):
            tool.check_prerequisites(first='convert')
            with self.assertRaisesRegex(tool.Refusal, 'aea not found'):
                tool.check_prerequisites(first='decrypt')

    def test_a_non_https_ipsw_is_refused(self):
        code, out, err = run(['/some/local.ipsw'], profile_factory=lambda name: F.profile)
        self.assertEqual(code, 1)
        self.assertIn('https://', err)


# --- the whole flow on the synthetic IPSW, with the real aea and hdiutil

@needs_mac
class B_Flow(unittest.TestCase):
    def test_the_whole_flow_round_trips_and_leaves_only_the_three_outputs(self):
        flow = Flow('round-trip')
        code, out, err = flow.go()
        self.assertEqual((code, err), (0, ''), out)
        self.assertEqual(flow.left(flow.out), ['basesystem-26A428.apfs', 'basesystem-26A428.root-hash', 'basesystem-26A428.trustcache'])
        self.assertEqual((flow.out / 'basesystem-26A428.apfs').read_bytes(), F.raw)
        self.assertEqual((flow.out / 'basesystem-26A428.root-hash').read_bytes(), F.root_payload)
        self.assertEqual((flow.out / 'basesystem-26A428.trustcache').read_bytes(), F.cache_payload)
        self.assertEqual((flow.left(flow.work), flow.left(flow.keys)), ([], []), 'the archive, the unwrapped image and the key file are deleted')
        self.assertEqual(len(flow.key.calls), 1)
        for secret in (F.key.hex(), 'PRIVATE KEY', F.other_key.hex()):
            self.assertNotIn(secret, out + err, 'the key is never printed')
        order = [out.index(text) for text in ('the wrapped key opened', 'SHA-384 matches the manifest', 'decrypted:', 'the pin', 'wrote basesystem')]
        self.assertEqual(order, sorted(order))
        self.assertLessEqual(touched(flow.fake.log, F.zip, ['kernelcache.release.mac17p', 'Firmware/decoy.bin']), 4 * BLOCK,
                             'no member outside the consented four was read')
        before = flow.fake.calls
        code, out, _ = flow.go()
        self.assertEqual(code, 0)
        self.assertIn('already present and matches its pin', out)
        self.assertEqual((flow.fake.calls, len(flow.key.calls)), (before, 1), 'a second run needs no network and no second key request')
        (flow.out / 'basesystem-26A428.root-hash').unlink()
        code, out, _ = flow.go()
        self.assertEqual(code, 0)
        self.assertIn('missing beside it: basesystem-26A428.root-hash', out)

    def test_an_allowed_redirect_hop_succeeds_and_is_traced(self):
        flow = Flow('hop', key=KeyTransport(MOVED, ok(F.pem)))
        code, out, err = flow.go('--key-redirects', '1')
        self.assertEqual((code, err), (0, ''), out)
        self.assertEqual(len(flow.key.calls), 2)
        self.assertIn('-> HTTP 301', out)
        self.assertIn('-> HTTP 200', out)

    def test_a_manifest_digest_that_is_not_the_member_deletes_the_partial_file_and_keeps_the_key_file(self):
        flow = Flow('digest', data=build_zip(F.aea, F.root_im4p, F.cache_im4p, digest=bytes(48)))
        code, out, err = flow.go()
        self.assertEqual(code, 1)
        self.assertIn('not what the manifest lists', err)
        self.assertIn('needs no new key request', err)
        self.assertEqual((flow.left(flow.work), flow.left(flow.keys), flow.left(flow.out)), ([], ['basesystem.key'], []),
                         'the partial archive is deleted, the key file stays')

    def test_a_key_that_opens_but_does_not_decrypt_keeps_the_archive_and_the_key_file(self):
        flow = Flow('wrongkey', data=build_zip(F.aea_wrongkey, F.root_im4p, F.cache_im4p))
        code, out, err = flow.go()
        self.assertEqual(code, 1)
        self.assertIn('aea decrypt failed', err)
        self.assertIn('needs no new key request', err)
        self.assertEqual((flow.left(flow.work), flow.left(flow.keys)), ([AEA_NAME], ['basesystem.key']), 'both stay for a re-run')
        self.assertNotIn(F.other_key.hex(), out + err)

    def test_a_failure_after_the_key_request_is_resumed_without_a_second_request(self):
        flow = Flow('resume-stream', data=build_zip(F.aea, F.root_im4p, F.cache_im4p, digest=bytes(48)))
        code, out, err = flow.go()
        self.assertEqual((code, flow.left(flow.work), flow.left(flow.keys)), (1, [], ['basesystem.key']))
        flow.fake = tir.FakeTransport(F.zip)                     # the same IPSW, now with the right digest
        flow.hooks['remote_factory'] = tir.factory(flow.fake, block=BLOCK)
        code, out, err = flow.go()
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn('reusing the key file of an earlier run: no key request', out)
        self.assertEqual(len(flow.key.calls), 1, 'the one key request was not repeated')
        self.assertEqual(flow.left(flow.out), ['basesystem-26A428.apfs', 'basesystem-26A428.root-hash', 'basesystem-26A428.trustcache'])
        self.assertEqual((flow.left(flow.work), flow.left(flow.keys)), ([], []))
        self.assertEqual((flow.out / 'basesystem-26A428.apfs').read_bytes(), F.raw)

    def test_a_failed_decrypt_is_resumed_from_the_archive_on_disk(self):
        flow = Flow('resume-decrypt')

        def broken(source, destination, key_path, disk, threads):
            raise tool.Refusal('aea decrypt failed (1): simulated')
        code, out, err = flow.go(decrypt=broken)
        self.assertEqual((code, flow.left(flow.work), flow.left(flow.keys)), (1, [AEA_NAME], ['basesystem.key']))
        served = flow.fake.served
        code, out, err = flow.go()
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn(f'{AEA_NAME}: SHA-384 matches the manifest', out)
        self.assertEqual(len(flow.key.calls), 1)
        self.assertLess(flow.fake.served - served, len(F.aea) // 2, 'the archive was verified on disk, not fetched again')
        self.assertEqual((flow.out / 'basesystem-26A428.apfs').read_bytes(), F.raw)
        self.assertEqual((flow.left(flow.work), flow.left(flow.keys)), ([], []))

    def test_a_failed_conversion_is_resumed_from_the_unwrapped_image_and_a_stale_key_is_removed(self):
        flow = Flow('resume-convert')

        def broken(dmg, destination, disk):
            raise tool.Refusal('hdiutil convert failed (1): simulated')
        code, out, err = flow.go(convert=broken)
        self.assertEqual((code, flow.left(flow.work), flow.left(flow.keys)), (1, ['022-99999-999.dmg'], []),
                         'the key file and the archive are gone, the unwrapped image stays')
        tool.save_key(flow.keys, F.key)                          # a stale key beside the image: no longer needed
        served = flow.fake.served
        code, out, err = flow.go()
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn('is left by an earlier run: converting it, with no key request and no member fetch', out)
        self.assertEqual(len(flow.key.calls), 1)
        self.assertLess(flow.fake.served - served, len(F.aea) // 2)
        self.assertEqual((flow.out / 'basesystem-26A428.apfs').read_bytes(), F.raw)
        self.assertEqual((flow.left(flow.work), flow.left(flow.keys)), ([], []))

    def test_a_partial_or_altered_leftover_archive_is_refused_and_kept(self):
        flow = Flow('partial')
        flow.work.mkdir()
        flow.keys.mkdir()
        tool.save_key(flow.keys, F.key)
        for label, data, expect in (('one byte short', F.aea[:-1], 'a partial file'),
                                    ('one byte changed', F.aea[:-1] + bytes([F.aea[-1] ^ 1]), 'is not what the manifest lists')):
            with self.subTest(label):
                (flow.work / AEA_NAME).write_bytes(data)
                code, out, err = flow.go()
                self.assertEqual(code, 1)
                self.assertIn(expect, err)
                self.assertEqual((flow.left(flow.work), flow.left(flow.keys), flow.key.calls), ([AEA_NAME], ['basesystem.key'], []))
                self.assertEqual((flow.work / AEA_NAME).read_bytes(), data, 'never deleted or repaired')

    def test_a_pin_mismatch_keeps_the_unwrapped_image_and_reports_the_digest(self):
        spec = dataclasses.replace(F.profile.root_hash, image_sha256='0' * 64)
        flow = Flow('pin', profile=types.SimpleNamespace(root_hash=spec, trust_cache=F.profile.trust_cache, firmware_subdir='j700ap'))
        code, out, err = flow.go()
        self.assertEqual(code, 1)
        self.assertIn(f'SHA-256 {sha256(F.raw)} is not the profile pin', err)
        self.assertEqual((flow.left(flow.work), flow.left(flow.keys), flow.left(flow.out)), (['022-99999-999.dmg'], [], []))

    def test_a_partitioned_image_is_refused_not_sliced(self):
        flow = Flow('gpt')
        with mock.patch.object(tool, 'image_info', lambda dmg: ('UDZO', 'GPTSPUD', 123)):
            code, out, err = flow.go()
        self.assertEqual(code, 1)
        self.assertIn("partition scheme 'GPTSPUD'", err)
        self.assertEqual(flow.left(flow.out), [])

    def test_the_conversion_is_held_to_the_floor_and_replaces_its_own_stale_scratch_file(self):
        dmg, directory = BUILD / 'fixture' / 'image.dmg', BUILD / 'convert'
        directory.mkdir()
        image = directory / 'basesystem-26A428.apfs'
        with self.assertRaisesRegex(tool.Refusal, 'converting the image would leave'):
            tool.convert_udif(dmg, image, tool.Disk(25 * GIB, lambda path: 25 * GIB + 1))
        self.assertEqual(sorted(p.name for p in directory.iterdir()), [], 'a refused conversion writes nothing')
        (directory / 'basesystem-26A428.apfs.part.cdr').write_bytes(b'killed half way')
        self.assertEqual(tool.convert_udif(dmg, image, tool.Disk(25 * GIB, lambda path: 1 << 40)), 'ULFO')
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ['basesystem-26A428.apfs'])
        self.assertEqual(image.read_bytes(), F.raw)


# --- (c) the URL path of the two small extractors, and the shared helper

class C_Extractors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build_zip(b'not used by these tools', F.root_im4p, F.cache_im4p)
        cls.local = BUILD / 'local.ipsw'
        cls.local.write_bytes(cls.data)
        for module in (root_hash_tool, cache_tool):
            module.get_profile = lambda name: F.profile

    def remote(self):
        self.fake = tir.FakeTransport(self.data)
        return tir.factory(self.fake, block=BLOCK)

    def quiet(self, module, argv, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            module.main(argv, **kwargs)
        return out.getvalue()

    def test_root_hash_from_a_url_equals_the_local_file(self):
        local, remote = BUILD / 'rh-local', BUILD / 'rh-url'
        text = self.quiet(root_hash_tool, [str(self.local), '--output', str(local)])
        self.assertEqual(text, f'{ROOT_HASH_NAME}: 208 raw bytes -> {local}\nSHA256 {sha256(F.root_payload)}\n', 'the local output is as before')
        self.quiet(root_hash_tool, [URL, '--output', str(remote)], remote_factory=self.remote())
        self.assertEqual((local.read_bytes(), remote.read_bytes()), (F.root_payload, F.root_payload))
        self.assertLess(self.fake.served, len(self.data))

    def test_trust_cache_from_a_url_equals_the_local_file(self):
        local, remote = BUILD / 'tc-local', BUILD / 'tc-url'
        text = self.quiet(cache_tool, [str(self.local), '--output', str(local)])
        self.assertEqual(text, f'{local}: 5 entries, {len(F.cache_payload)} bytes, SHA-256 {sha256(F.cache_payload)}\n', 'the local output is as before')
        self.quiet(cache_tool, [URL, '--output', str(remote)], remote_factory=self.remote())
        self.assertEqual((local.read_bytes(), remote.read_bytes()), (F.cache_payload, F.cache_payload))

    def test_the_fetch_cap_option_is_for_urls_only_and_a_wrong_build_is_still_refused(self):
        for module in (root_hash_tool, cache_tool):
            with self.subTest(module.__name__):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    module.main([str(self.local), '--output', str(BUILD / 'x'), '--max-fetch-mib', '4'])
                self.assertEqual(caught.exception.code, 2)
                wrong = build_zip(b'a', F.root_im4p, F.cache_im4p, manifest=dict(manifest_of(b'a', F.root_im4p, F.cache_im4p), ProductBuildVersion='26A999'))
                fake = tir.FakeTransport(wrong)
                with self.assertRaisesRegex(ValueError, 'build does not match'):
                    self.quiet(module, [URL, '--output', str(BUILD / 'y')], remote_factory=tir.factory(fake, block=BLOCK))

    def test_open_ipsw_opens_a_path_or_a_url_and_closes_the_remote_on_failure(self):
        archive, remote = ir.open_ipsw(self.local)
        self.assertIsNone(remote)
        archive.close()
        archive, remote = ir.open_ipsw(URL, factory=self.remote())
        self.assertIn('BuildManifest.plist', archive.namelist())
        archive.close()
        remote.close()
        opened = []
        broken = tir.FakeTransport(b'PK\x03\x04' + bytes(200))

        def factory(url, max_fetch):
            opened.append(tir.factory(broken, block=BLOCK)(url, max_fetch))
            return opened[-1]
        with self.assertRaises(ir.NotAZipError):
            ir.open_ipsw(URL, factory=factory)
        self.assertTrue(opened[0].closed)


if __name__ == '__main__':
    unittest.main()
