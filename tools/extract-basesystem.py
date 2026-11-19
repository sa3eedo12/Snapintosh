#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch Apple's BaseSystem root from the restore IPSW, unwrap it and check it against the guest profile's pins.

The RAM-root route boots Apple's BaseSystem (recoveryOS) from a raw APFS image. The guest profile pins that image's SHA-256,
its 208-byte root-hash payload and its trust cache (probes/guestprofile.py). Nothing of Apple's is in the repository, and the
26.6 GB restore image need not be stored: this reads four members of it over HTTPS Range requests (tools/ipsw_remote.py).

    tools/extract-basesystem.py <https url of the IPSW>                       plan only: the default
    tools/extract-basesystem.py <https url of the IPSW> --fetch               plan, then the root

The plan reads about 6 MB of public metadata (the zip's central directory, BuildManifest.plist, the root-hash and trust-cache
members and the first bytes of the BaseSystem member), checks every pin, prints what a fetch would cost and stops. It makes no
key request and fetches no root bytes. --fetch then runs these steps in this order and stops at the first failure:

  1. the disk floor (--min-free-gib, default 25) against the member's size;
  2. ONE key request, to the key URL named inside the member (see KeyService), no retry, no other source of a key;
  3. HPKE opens the member's wrapped key (DHKEM P-256, HKDF-SHA256, AES-256-GCM); the key goes to a 0600 file for aea(1);
  4. the member is streamed to disk, its SHA-384 checked against the manifest as it arrives;
  5. aea decrypt (macOS /usr/bin/aea), then the key file and the .aea are deleted;
  6. hdiutil convert -format UDTO turns the UDIF image into the raw APFS container, which must match the profile's pin
     (size a multiple of 16 KiB, NXSB at byte 32, 4096-byte blocks x count = size, SHA-256); then the .dmg is deleted;
  7. the raw image, the root-hash payload and the trust cache are written under --out.

Every intermediate is deleted the moment the next stage has been verified, and no stage runs that would leave less than the
floor free. The key is never printed. The outputs are Apple's and stay under git-ignored downloads/.

Resuming, so that the one key request is never lost to a later failure: the 0600 key file stays until the decrypt has succeeded,
and a stop after step 5 leaves the unwrapped image. A re-run continues from what is on disk: a key file is validated and reused
without a request, an archive is re-checked against the manifest, an unwrapped image goes straight to step 6. A combination it
cannot continue from (an archive without its key file, an archive beside an unwrapped image) is refused, never deleted.

Evidence. VERIFIED (read on the host): the member names and the pins match the 26A428 restore image (one read of its public
metadata); a UDIF image made from a raw file converts back to the identical bytes with hdiutil (UDZO, ULFO, UDRO); the tests'
synthetic round trip (tools/test-extract-basesystem.py). OBSERVED (one run, 2026-11-19): the key URL answered HTTP 301 to a
request that did not follow redirects, so no key was obtained; the redirect's Location was not captured. UNKNOWN: whether the
key service answers 200 after a redirect, the HPKE parameters for the real archive (empty info is what the public client uses),
the inner image's format and whether its raw form equals the pin. UNIMPLEMENTED: a partition-mapped image is refused, not sliced.

Provenance: the archive metadata layout and the HPKE parameters are those of the public, MIT-licensed blacktop/ipsw client
(pkg/aea); this file is an independent reimplementation on the Python 'cryptography' package, no code copied.
"""
import argparse
import base64
import dataclasses
import hashlib
import json
import os
import plistlib
import runpy
import shutil
import stat
import struct
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'probes'))

MIB, GIB = 1 << 20, 1 << 30
DEFAULT_PROFILE = 'j700ap-26A428'
VARIANT = 'macOS Customer'
KEY_HOST = 'wkms-public.apple.com'
KEY_PREFIX = '/fcs-keys/'
KEY_BODY_MAX = 8192
USER_AGENT = 'snapintosh-extract-basesystem'
DEFAULT_CAP_MIB = 1300
DEFAULT_FLOOR_GIB = 25.0
CHUNK = 8 * MIB
PROLOGUE_MAX = 16384
KEY_FILE = 'basesystem.key'


class Refusal(Exception):
    """A rule of this tool was not met; the message says which. Exit status 1."""
    code = 1


class KeyServiceRefusal(Refusal):
    code = 3


class CapRefusal(Refusal):
    """The plan or the fetch would read more than --max-fetch-mib. Exit status 2, as in tools/extract-firmware.py."""
    code = 2


_FIRMWARE = []


def firmware_tool():
    """tools/extract-firmware.py's namespace: its IM4P reader, is_url and the shared ipsw_remote loader."""
    if not _FIRMWARE:
        _FIRMWARE.append(runpy.run_path(str(HERE / 'extract-firmware.py')))
    return _FIRMWARE[0]


# ---- the archive: only the consented members

class Members:
    """The only door to the archive: BuildManifest.plist and, once the manifest is verified, the three members this tool may
    read. Any other name is refused before a byte of it is requested."""

    def __init__(self, archive):
        self._zip, self._allowed = archive, {'BuildManifest.plist'}

    def allow(self, *names):
        self._allowed.update(names)

    def _check(self, name):
        if name not in self._allowed:
            raise Refusal(f'member {name!r} is not one of the consented members: refused')

    def getinfo(self, name):
        self._check(name)
        return self._zip.getinfo(name)

    def read(self, name):
        self._check(name)
        return self._zip.read(name)

    def open(self, name):
        self._check(name)
        return self._zip.open(name)


@dataclasses.dataclass(frozen=True)
class Located:
    aea: str
    aea_sha384: bytes
    root_hash: str
    root_hash_sha384: bytes
    trust_cache: str
    trust_cache_sha384: bytes


def locate(manifest, profile):
    """The three members the profile's pins name, from BuildManifest.plist, or a Refusal."""
    spec, cache = profile.root_hash, profile.trust_cache
    if spec is None or cache is None:
        raise Refusal('the guest profile has no root-hash or trust-cache pin')
    if manifest.get('ProductBuildVersion') != spec.build_id:
        raise Refusal(f'the IPSW is build {manifest.get("ProductBuildVersion")}, the profile pins {spec.build_id}')
    identities = [i for i in manifest.get('BuildIdentities', ())
                  if i.get('Info', {}).get('DeviceClass') == spec.device_class and i['Info'].get('Variant') == VARIANT]
    if len(identities) != 1:
        raise Refusal(f'expected one {VARIANT!r} identity for {spec.device_class}, found {len(identities)}')
    entries = identities[0].get('Manifest', {})
    try:
        base, volume, trust = entries['BaseSystem'], entries[spec.component], entries[cache.component]
        paths = [e['Info']['Path'] for e in (base, volume, trust)]
        digests = [e['Digest'] for e in (base, volume, trust)]
    except KeyError as error:
        raise Refusal(f'the manifest lacks {error} for the BaseSystem') from None
    aea, root_hash, trust_cache = paths
    if root_hash != f'Firmware/{aea}.root_hash' or trust_cache != f'Firmware/{aea}.trustcache' or trust_cache != cache.member:
        raise Refusal('the manifest member names disagree with each other or with the profile pin')
    if any(not isinstance(d, bytes) or len(d) != 48 for d in digests):
        raise Refusal('a manifest digest is not a SHA-384')
    return Located(aea, digests[0], root_hash, digests[1], trust_cache, digests[2])


def read_small_members(members, located, profile):
    """The root-hash payload and the trust-cache payload, each checked against its pin and the manifest."""
    spec, cache = profile.root_hash, profile.trust_cache
    blob = members.read(located.root_hash)
    if hashlib.sha256(blob).hexdigest() != spec.component_sha256 or hashlib.sha384(blob).digest() != located.root_hash_sha384:
        raise Refusal('the root-hash member differs from the profile pin or the manifest')
    payload = firmware_tool()['im4p_payload'](blob)
    if len(payload) != spec.payload_bytes or hashlib.sha256(payload).hexdigest() != spec.payload_sha256:
        raise Refusal('the root-hash payload differs from the profile pin')
    blob = members.read(located.trust_cache)
    if hashlib.sha384(blob).digest() != located.trust_cache_sha384:
        raise Refusal('the trust-cache member differs from the manifest')
    try:
        return payload, cache.extract(blob)
    except ValueError as error:
        raise Refusal(f'trust cache: {error}') from None


# ---- the AEA1 prologue and the key

@dataclasses.dataclass(frozen=True)
class Prologue:
    profile: int
    scrypt: int
    entries: dict


def parse_prologue(head):
    """The AEA1 header (magic, profile and scrypt strength, auth-data length) and its key/value auth data.

    Layout as the public client reads it: u32 length (including itself), key, NUL, value; repeated to the declared length.
    """
    if len(head) < 12 or head[:4] != b'AEA1':
        raise Refusal('the member does not start with an AEA1 header')
    word, length = struct.unpack_from('<II', head, 4)
    if length > PROLOGUE_MAX or len(head) < 12 + length:
        raise Refusal('the AEA1 auth data is larger than this tool reads or truncated')
    auth, entries, pos = head[12:12 + length], {}, 0
    while pos < len(auth):
        if len(auth) - pos < 4:
            raise Refusal('truncated AEA1 auth-data entry')
        (size,) = struct.unpack_from('<I', auth, pos)
        if size < 5 or pos + size > len(auth):
            raise Refusal('invalid AEA1 auth-data entry length')
        key, sep, value = auth[pos + 4:pos + size].partition(b'\0')
        if not sep:
            raise Refusal('AEA1 auth-data entry without a key separator')
        entries[key.decode('latin-1')] = value
        pos += size
    return Prologue(word & 0xffffff, word >> 24, entries)


def read_prologue(members, name):
    with members.open(name) as member:
        head = member.read(12)
        if len(head) == 12 and head[:4] == b'AEA1':
            head += member.read(min(struct.unpack_from('<I', head, 8)[0], PROLOGUE_MAX))
    return parse_prologue(head)


def key_target(prologue):
    """(key URL, HPKE enc, wrapped key): the symmetric profile, the URL held to the consented host and path shape."""
    if prologue.profile != 1 or prologue.scrypt != 0:
        raise Refusal(f'AEA profile {prologue.profile} (scrypt {prologue.scrypt}) is not the symmetric profile 1')
    try:
        url = prologue.entries['com.apple.wkms.fcs-key-url'].decode('ascii')
        response = json.loads(prologue.entries['com.apple.wkms.fcs-response'])
        enc, wrapped = base64.b64decode(response['enc-request']), base64.b64decode(response['wrapped-key'])
    except (KeyError, ValueError, UnicodeDecodeError, TypeError) as error:
        raise Refusal(f'the AEA1 metadata lacks a usable key URL or wrapped key ({type(error).__name__})') from None
    check_key_url(url)
    if len(enc) != 65 or len(wrapped) != 48:
        raise Refusal('the wrapped key is not a P-256 HPKE encapsulation (65 B) plus a sealed 32-byte key (48 B)')
    return url, enc, wrapped


def check_key_url(url):
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (parts.scheme, parts.hostname, port, parts.query, parts.fragment, parts.username, parts.password) != \
            ('https', KEY_HOST, None, '', '', None, None) or not parts.path.startswith(KEY_PREFIX) or parts.path.count('/') != 2 \
            or len(parts.path) == len(KEY_PREFIX):
        raise KeyServiceRefusal(f'the key URL is not https://{KEY_HOST}{KEY_PREFIX}<id>: refused, nothing was requested')


def check_redirect(url):
    """A redirect hop the operator allowed: https, a host under apple.com, no credentials or port."""
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    host = parts.hostname or ''
    if parts.scheme != 'https' or not (host == 'apple.com' or host.endswith('.apple.com')) or parts.username or port is not None:
        raise KeyServiceRefusal('a redirect target is not https on apple.com without credentials or port: refused, not requested')


def shape(url):
    parts = urllib.parse.urlsplit(url)
    return f'{parts.scheme}://{parts.hostname}{parts.path.rsplit("/", 1)[0]}/<{len(parts.path.rsplit("/", 1)[1])} characters>'


def describe_target(url):
    """A redirect target as the operator may read it: scheme, host, port, the path without its last segment, no query value."""
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = 'invalid'
    head, _, last = parts.path.rpartition('/')
    return (f'{parts.scheme or "no-scheme"}://{parts.hostname or "no-host"}' + (f':{port}' if port is not None else '') + f'{head}/<{len(last)} characters>'
            + (' with a query' if parts.query else '') + (' with credentials' if parts.username else ''))


def urllib_transport(url, agent, timeout):
    """One GET, redirects not followed, any HTTP status returned as (status, headers, body prefix)."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': agent, 'Accept-Encoding': 'identity'})
    try:
        with opener.open(request, timeout=timeout) as reply:
            return reply.status, dict(reply.headers), reply.read(KEY_BODY_MAX + 1)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, dict(error.headers), error.read(KEY_BODY_MAX + 1)


class KeyService:
    """The key request: ONE per instance, to the key URL named inside the member, held to its host and path shape.

    No retry, no other endpoint, no database. A status other than 200 raises with every response header and, for an error
    page, its first bytes, so the refusal says exactly what came back. Redirects are not followed unless the operator allows
    N hops (--key-redirects): each hop must be https on apple.com and is a request of its own, and is traced.
    """

    def __init__(self, transport=None, redirects=0, timeout=60):
        self.transport, self.redirects, self.timeout = transport or urllib_transport, redirects, timeout
        self.used, self.trace = False, []

    def fetch(self, url):
        if self.used:
            raise KeyServiceRefusal('a key request was already made: no second request')
        check_key_url(url)
        self.used = True
        hops = 0
        while True:
            try:
                status, headers, body = self.transport(url, USER_AGENT, self.timeout)
            except Exception as error:      # a socket, TLS or timeout failure: reported, never retried
                raise KeyServiceRefusal(f'the key request failed before any HTTP status: {type(error).__name__}: {error}. '
                                        'No retry, no other source.') from None
            self.trace.append(f'GET {shape(url)} -> HTTP {status}')
            if status == 200:
                if len(body) > KEY_BODY_MAX:
                    raise KeyServiceRefusal('the key response is larger than 8 KiB: refused')
                return body
            location = {k.lower(): v for k, v in headers.items()}.get('location')
            if 300 <= status < 400 and location and hops < self.redirects:
                url, hops = urllib.parse.urljoin(url, location), hops + 1
                self.trace.append(f'redirect target: {describe_target(url)}')
                check_redirect(url)
                continue
            text = ''.join(chr(c) if 32 <= c < 127 or c in (10, 13) else '.' for c in body[:300])
            lines = '; '.join(f'{k}: {v}' for k, v in sorted(headers.items()))
            raise KeyServiceRefusal(f'the key request answered HTTP {status} (redirects allowed: {self.redirects}); headers: {lines}; '
                                    f'body[:300]: {text!r}. No retry, no other source.')


def derive_archive_key(pem, enc, wrapped):
    """The 32-byte archive key: HPKE (DHKEM P-256, HKDF-SHA256, AES-256-GCM, empty info) opens the wrapped key."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite
    except ImportError as error:
        raise Refusal(f'the key step needs the Python package "cryptography" with hazmat.primitives.hpke ({error})') from None
    try:
        private = serialization.load_pem_private_key(pem, password=None)
    except Exception as error:               # not PEM, encrypted, or a key type this build lacks: the type only, never the bytes
        raise Refusal(f'the key service did not return a usable PEM private key ({type(error).__name__})') from None
    if not isinstance(private, ec.EllipticCurvePrivateKey) or private.curve.name != 'secp256r1':
        raise Refusal('the key service returned a private key that is not P-256')
    try:
        key = Suite(KEM.P256, KDF.HKDF_SHA256, AEAD.AES_256_GCM).decrypt(enc + wrapped, private)
    except Exception as error:               # the tag did not verify, or the encapsulation is malformed: never echo material
        raise Refusal(f'HPKE could not open the wrapped key ({type(error).__name__}): wrong key or wrong parameters') from None
    if len(key) != 32:
        raise Refusal(f'the opened key is {len(key)} bytes, not 32')
    return key


def save_key(directory, key):
    """The archive key as a 0600 file for aea(1), created exclusively.

    It stays until the decrypt has succeeded, so that a failure between the one key request and the decrypt needs no second
    request: a re-run finds the file and reuses it (load_key).
    """
    path = Path(directory) / KEY_FILE
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Refusal(f'{path.name} already exists in {path.parent.name}/: a leftover key file; delete it first') from None
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write('hex:' + key.hex())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def load_key(path):
    """The key of an earlier run from its file; a file that this tool did not write, or that others can read, is refused and left alone."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise Refusal(f'{path.name} is not a regular file with mode 0600: refused, nothing was requested')
    text = path.read_text(errors='replace')
    try:
        key = bytes.fromhex(text[4:]) if text.startswith('hex:') else b''
    except ValueError:
        key = b''
    if len(key) != 32 or len(text) != 4 + 64:
        raise Refusal(f'{path.name} is not a key file of this tool: refused, nothing was requested')
    return key


# ---- disk, fetch, decrypt, convert, pins

def free_bytes(path):
    """Free bytes on the volume that holds `path`, or the nearest existing parent of it."""
    path = Path(path).absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


class Disk:
    """The free-space floor. free(path) -> bytes is replaceable for tests."""

    def __init__(self, floor_bytes, free=None):
        self.floor, self.free = int(floor_bytes), free or free_bytes

    def room(self, path, extra, what):
        left = self.free(path) - extra
        if left < self.floor:
            raise Refusal(f'{what} would leave {left / GIB:.1f} GiB free, below the floor of {self.floor / GIB:g} GiB: stopped')
        return left


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def stream_member(members, name, destination, expect_sha384, disk, progress=None):
    """The member to disk in 8 MiB steps, its SHA-384 and size checked; a failure removes the partial file."""
    info = members.getinfo(name)
    if info.compress_type != zipfile.ZIP_STORED:
        raise Refusal(f'{name} is not stored in the archive: refused')
    disk.room(destination.parent, info.file_size, 'fetching the member')
    try:
        out = open(destination, 'xb')
    except FileExistsError:
        raise Refusal(f'{destination.name} already exists: delete it, or finish the earlier run, first') from None
    digest, done = hashlib.sha384(), 0
    with out:
        try:
            with members.open(name) as source:
                for block in iter(lambda: source.read(CHUNK), b''):
                    digest.update(block)
                    out.write(block)
                    done += len(block)
                    if done % (64 * MIB) < CHUNK:
                        disk.room(destination.parent, 0, 'fetching the member')
                        if progress:
                            progress(done, info.file_size)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    if done != info.file_size or digest.digest() != expect_sha384:
        destination.unlink(missing_ok=True)
        raise Refusal(f'the fetched member is {done:,} B with SHA-384 {digest.hexdigest()[:16]}..., not what the manifest lists: deleted')
    return done


def verify_member(path, size, expect_sha384):
    """An archive left by an earlier run, checked against the manifest the way a fresh one is; never deleted or repaired here."""
    found = path.stat().st_size
    if found != size:
        raise Refusal(f'{path.name} is {found:,} B, the manifest lists {size:,}: a partial file; delete it and re-run')
    digest = hashlib.sha384()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    if digest.digest() != expect_sha384:
        raise Refusal(f'{path.name} is not what the manifest lists (SHA-384 {digest.hexdigest()[:16]}...); delete it and re-run')


def run_watched(argv, output, disk, poll=0.5):
    """Run a command that writes `output` (hdiutil, aea) at low priority, killing it and deleting `output` if the floor is crossed."""
    output = Path(output)
    process = subprocess.Popen(['nice', '-n', '10'] + argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = []
    reader = threading.Thread(target=lambda: result.append(process.communicate()))
    reader.start()
    crossed = False
    while reader.is_alive():
        reader.join(poll)
        if not crossed and disk.free(output.parent) < disk.floor:
            crossed = True
            process.kill()
    reader.join()
    if crossed:
        Path(output).unlink(missing_ok=True)
        raise Refusal(f'{argv[0]} was stopped and its output deleted: free space reached the floor')
    return process.returncode, result[0][1] if result else ''


def aea_decrypt(source, destination, key_path, disk, threads=4):
    disk.room(Path(destination).parent, 0, 'decrypting')
    code, errors = run_watched(['aea', 'decrypt', '-t', str(threads), '-i', str(source), '-o', str(destination), '-key', str(key_path)],
                               destination, disk)
    if code:
        Path(destination).unlink(missing_ok=True)
        raise Refusal(f'aea decrypt failed ({code}): {errors.strip()[:200]}')


def image_info(dmg):
    """hdiutil imageinfo (read only, nothing mounted): (format, partition scheme, bytes of the expanded image)."""
    run = subprocess.run(['hdiutil', 'imageinfo', '-plist', str(dmg)], capture_output=True)
    if run.returncode:
        raise Refusal(f'hdiutil imageinfo failed on the unwrapped image: {run.stderr.decode(errors="replace").strip()[:200]}')
    info = plistlib.loads(run.stdout)
    scheme = (info.get('partitions') or {}).get('partition-scheme')
    return info.get('Format'), scheme, info.get('Size Information', {}).get('Total Bytes')


def convert_udif(dmg, destination, disk):
    """The UDIF image to the raw container at `destination` (hdiutil convert -format UDTO writes <base>.cdr, renamed)."""
    fmt, scheme, total = image_info(dmg)
    if scheme != 'none':
        raise Refusal(f'the unwrapped image has partition scheme {scheme!r}, not a bare APFS container: slicing is not implemented')
    disk.room(destination.parent, total or 0, 'converting the image')
    base = destination.with_name(destination.name + '.part')
    produced = base.with_name(base.name + '.cdr')
    produced.unlink(missing_ok=True)                  # this function's own scratch name, only ever complete after the rename below
    code, errors = run_watched(['hdiutil', 'convert', str(dmg), '-format', 'UDTO', '-o', str(base)], produced, disk)
    if code or not produced.exists():
        produced.unlink(missing_ok=True)
        raise Refusal(f'hdiutil convert failed ({code}): {errors.strip()[:200]}')
    os.replace(produced, destination)
    return fmt


def check_raw(path, spec):
    """Size, geometry and SHA-256 of the raw container against the profile pin: (bytes, sha256) or a Refusal."""
    size = Path(path).stat().st_size
    with open(path, 'rb') as stream:
        head = stream.read(48)
    if size < 0x4000 or size % 0x4000:
        raise Refusal(f'the raw image is {size:,} B, not a multiple of 16 KiB')
    if len(head) != 48 or head[32:36] != b'NXSB':
        raise Refusal('the raw image has no NXSB at byte 32: not a bare APFS container')
    block, count = struct.unpack_from('<IQ', head, 36)
    if block != 4096 or block * count != size:
        raise Refusal(f'APFS geometry {count:,} blocks of {block} B does not match the file size {size:,}')
    digest = sha256_file(path)
    if digest != spec.image_sha256:
        raise Refusal(f'the raw image SHA-256 {digest} is not the profile pin {spec.image_sha256}')
    return size, digest


def write_new(path, data):
    """Write `data` exclusively; an existing identical file is fine, a different one is never replaced."""
    path = Path(path)
    if path.exists():
        if path.read_bytes() != data:
            raise Refusal(f'refusing to replace a different existing file: {path}')
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)


# ---- the flow

def plan_lines(remote, located, info, cap):
    projected = remote.bytes_to_fetch([firmware_tool()['load_remote']().member_span(info)]) + remote.bytes_fetched
    lines = [f'member {located.aea}: {info.file_size:,} B, stored, manifest SHA-384 {located.aea_sha384.hex()[:16]}...',
             f'plan: {remote.bytes_fetched:,} B fetched so far in {remote.requests} requests; the member needs ~{projected - remote.bytes_fetched:,} '
             f'more, total ~{projected:,} B of a cap of {cap:,} B ({100 * projected / remote.size:.2f}% of the archive)']
    return projected, lines


def check_prerequisites(decrypt=None, convert=None, first='key'):
    """What can be known before the one key request, for the stages that will run (first: 'key', 'decrypt' or 'convert'):
    the HPKE module and the two macOS tools, so that a missing one wastes nothing."""
    if first == 'key':
        try:
            from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite  # noqa: F401
        except ImportError as error:
            raise Refusal(f'the key step needs the Python package "cryptography" with hazmat.primitives.hpke in THIS interpreter ({error}); '
                          'nothing was requested') from None
    for name, hook, needed in (('aea', decrypt, first != 'convert'), ('hdiutil', convert, True)):
        if needed and hook is None and shutil.which(name) is None:
            raise Refusal(f'{name} not found: unwrapping needs macOS; nothing was requested')


def request_key(key_url, enc, wrapped, transport, redirects):
    """The ONE key request and the HPKE step: the 32-byte archive key. Every request made is traced on stdout, success or not."""
    service = KeyService(transport, redirects)
    try:
        pem = service.fetch(key_url)
    finally:
        for line in service.trace:
            print(line)
    key = derive_archive_key(pem, enc, wrapped)
    print('the wrapped key opened (HPKE, tag verified); the key is not printed')
    return key


def run(args, remote_factory=None, profile_factory=None, key_transport=None, decrypt=None, convert=None, disk_free=None, prerequisites=None):
    from guestprofile import get_profile
    fw = firmware_tool()
    if not fw['is_url'](args.ipsw) or not args.ipsw.lower().startswith('https://'):
        raise Refusal('the IPSW must be an https:// URL (a local file is not needed: only four members are read)')
    profile = (profile_factory or get_profile)(args.guest_profile)
    spec = profile.root_hash
    out = Path(args.out) if args.out else ROOT / 'downloads' / 'extracted' / profile.firmware_subdir
    work, keys = Path(args.work), Path(args.key_dir)
    disk = Disk(args.min_free_gib * GIB, disk_free)
    image = out / f'basesystem-{spec.build_id}.apfs'
    root_hash_file, cache_file = out / f'basesystem-{spec.build_id}.root-hash', out / profile.trust_cache.filename
    if image.exists():
        size, digest = check_raw(image, spec)
        missing = [path.name for path in (root_hash_file, cache_file) if not path.exists()]
        print(f'{image.name} is already present and matches its pin ({size:,} B, SHA-256 {digest}): nothing to do'
              + (f'; missing beside it: {", ".join(missing)} (tools/extract-root-hash.py and tools/prepare-trust-cache.py write them from the URL)'
                 if missing else ''))
        return 0
    ipsw_remote = fw['load_remote']()
    cap = args.max_fetch_mib << 20
    remote = (remote_factory or ipsw_remote.RangeFile)(args.ipsw, max_fetch=cap)
    try:
        members = Members(ipsw_remote.open_zip(remote))
        located = locate(plistlib.loads(members.read('BuildManifest.plist')), profile)
        members.allow(located.aea, located.root_hash, located.trust_cache)
        root_payload, cache_payload = read_small_members(members, located, profile)
        print(f'pins match the IPSW: root-hash payload {len(root_payload)} B, trust cache {len(cache_payload):,} B ({profile.trust_cache.entries} entries)')
        info = members.getinfo(located.aea)
        if info.compress_type != zipfile.ZIP_STORED or info.compress_size != info.file_size:
            raise Refusal(f'{located.aea} is not stored in the archive: refused')
        key_url, enc, wrapped = key_target(read_prologue(members, located.aea))
        print(f'AEA1 profile 1; key URL {shape(key_url)}; wrapped key {len(wrapped)} B under a {len(enc)} B encapsulation')
        projected, lines = plan_lines(remote, located, info, cap)
        print('\n'.join(lines))
        if projected > cap:
            raise CapRefusal(f'the plan fetches ~{projected:,} B, over the --max-fetch-mib cap of {cap:,} B: nothing was fetched')
        if not args.fetch:
            print(f'plan only: no key request, no root bytes. --fetch needs {info.file_size / GIB:.2f} GiB for the member, then the unwrapped image '
                  f'and the raw image together (history: 1,776 MiB); every stage is held to the {args.min_free_gib:g} GiB floor')
            return 0
        aea_name = Path(located.aea).name
        aea_path, dmg_path, key_path = work / aea_name, work / aea_name[:-len('.aea')], keys / KEY_FILE
        have_aea, have_dmg, have_key = aea_path.exists(), dmg_path.exists(), key_path.exists()
        if have_aea and have_dmg:
            raise Refusal(f'both {aea_path.name} and {dmg_path.name} exist: the unwrapped image may be partial; delete the one that is not '
                          'complete; nothing was requested')
        if have_aea and not have_key:
            raise Refusal(f'{aea_path.name} exists without its key file, and the one key request is spent: delete the archive, '
                          'or restore the key file; nothing was requested')
        archive_key = load_key(key_path) if have_key and not have_dmg else None
        (prerequisites or check_prerequisites)(decrypt, convert, 'convert' if have_dmg else 'decrypt' if have_key else 'key')
        if have_dmg:
            key_path.unlink(missing_ok=True)
            out.mkdir(parents=True, exist_ok=True)
            print(f'{dmg_path.name} is left by an earlier run: converting it, with no key request and no member fetch')
        else:
            if not have_aea:
                disk.room(work, info.file_size, 'the whole flow (the archive member alone)')
            reuse = archive_key is not None
            if not reuse:
                archive_key = request_key(key_url, enc, wrapped, key_transport, args.key_redirects)
            for directory in (work, keys, out):
                directory.mkdir(parents=True, exist_ok=True)
            if reuse:
                print('reusing the key file of an earlier run: no key request')
            else:
                save_key(keys, archive_key)
            try:
                if have_aea:
                    verify_member(aea_path, info.file_size, located.aea_sha384)
                else:
                    stream_member(members, located.aea, aea_path, located.aea_sha384, disk,
                                  lambda done, total: print(f'  fetched {done / MIB:,.0f} of {total / MIB:,.0f} MiB', flush=True))
                print(f'{aea_path.name}: SHA-384 matches the manifest')
                (decrypt or aea_decrypt)(aea_path, dmg_path, key_path, disk, args.threads)
            except BaseException:
                print('the 0600 key file stays, so a re-run needs no new key request (delete it to start over)', file=sys.stderr)
                raise
            key_path.unlink()
            aea_path.unlink()
            print(f'decrypted: {dmg_path.name} {dmg_path.stat().st_size:,} B; the key file and {aea_path.name} are deleted')
        fmt = (convert or convert_udif)(dmg_path, image, disk)
        try:
            size, digest = check_raw(image, spec)
        except Refusal:
            image.unlink(missing_ok=True)
            raise
        dmg_path.unlink()
        write_new(root_hash_file, root_payload)
        write_new(cache_file, cache_payload)
        print(f'image format {fmt}; {image.name}: {size:,} B, SHA-256 {digest} = the pin; {dmg_path.name} deleted')
        print(f'wrote {root_hash_file.name} ({len(root_payload)} B) and {cache_file.name} ({len(cache_payload):,} B) under {out.name}/')
        return 0
    finally:
        print(f'remote: {remote.summary()}')
        remote.close()


def main(argv=None, **hooks):
    """hooks (tests): remote_factory, profile_factory, key_transport, decrypt, convert, disk_free, prerequisites."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('ipsw', help='the restore IPSW as an https:// URL, read in place with Range requests')
    ap.add_argument('--guest-profile', default=DEFAULT_PROFILE, help='profile whose pins name the BaseSystem (default %(default)s)')
    ap.add_argument('--fetch', action='store_true', help='make the key request, fetch the member and unwrap it (default: plan only)')
    ap.add_argument('--max-fetch-mib', type=int, default=DEFAULT_CAP_MIB, help='hard cap on bytes read from the IPSW (default %(default)s)')
    ap.add_argument('--min-free-gib', type=float, default=DEFAULT_FLOOR_GIB, help='refuse a stage that leaves less free disk (default %(default)s)')
    ap.add_argument('--key-redirects', type=int, default=0, help='redirect hops the key request may follow, https on apple.com (default 0: none)')
    ap.add_argument('--threads', type=int, default=4, help='aea worker threads (default %(default)s)')
    ap.add_argument('--out', help='output directory (default downloads/extracted/<profile board>)')
    ap.add_argument('--work', default=str(ROOT / 'downloads'), help='where the member and the unwrapped image live meanwhile')
    ap.add_argument('--key-dir', default=str(ROOT / 'build'), help='where the 0600 key file lives meanwhile (git-ignored)')
    args = ap.parse_args(argv)
    if args.max_fetch_mib < 1 or args.min_free_gib < 0 or not 0 <= args.key_redirects <= 3 or args.threads < 1:
        ap.error('--max-fetch-mib must be at least 1, --min-free-gib not negative, --key-redirects 0 to 3, --threads at least 1')
    ipsw_remote = firmware_tool()['load_remote']()
    try:
        return run(args, **hooks)
    except Refusal as error:
        print(f'refused: {error}', file=sys.stderr)
        return error.code
    except ipsw_remote.FetchCapExceededError as error:
        print(f'refused: {error}', file=sys.stderr)
        return 2
    except (ipsw_remote.RemoteZipError, zipfile.BadZipFile) as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
