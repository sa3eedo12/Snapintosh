#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pull one board's firmware set out of a macOS restore IPSW.

A UniversalMac IPSW carries every Mac Apple ships, so "the kernelcache" is
ambiguous until a board is named. BuildManifest.plist is the authority on which
files belong together, and this reads it rather than guessing from filenames:
the board picks an identity, the identity names its kernelcache, SPTM, TXM,
device tree, iBoot and SEP, and those are what come out.

    tools/extract-firmware.py downloads/UniversalMac_27.0_26A428_Restore.ipsw j700ap

Each component is unwrapped from its IM4P container and decompressed if it was
compressed, so what lands on disk is the raw image a loader would place in DRAM.

The IPSW may also be an https:// URL, read in place with HTTP Range requests
(tools/ipsw_remote.py) so the 25 GB image is never stored. Only the zip's central
directory, BuildManifest.plist and the components above are fetched: a few hundred
MB, in whole 1 MiB blocks. Run --dry-run first. It fetches just the central
directory and the manifest, prints every member it would read with its size and the
bytes that will cost, and extracts nothing.

    tools/extract-firmware.py --dry-run <https url of the IPSW> j700ap
    tools/extract-firmware.py <https url of the IPSW> j700ap --max-fetch-mib 512

A URL run prints that plan before it extracts, refuses with exit status 2 when the
plan would fetch more than --max-fetch-mib (default 1024), and ends with the
requests and bytes it used. Without --out it writes downloads/extracted/<board>
under the repository root, not next to a URL; a local file is handled as before,
including its default output directory beside the IPSW. Only https is followed.
Nothing here chooses a URL or contacts Apple by itself: whether to fetch Apple's
public restore image is the operator's decision, and how its CDN answers Range
requests is unverified until a first --dry-run.
"""
import argparse
import importlib.util
import pathlib
import plistlib
import struct
import subprocess
import sys
import zipfile

# The parts a boot chain needs. Everything else in the identity is restore
# plumbing, cryptexes and trust caches.
WANTED = ['KernelCache', 'Ap,TrustedExecutionMonitor', 'DeviceTree', 'iBoot',
          'LLB', 'SEP', 'SepStage1', 'iBootData']

MH_MAGIC_64 = 0xFEEDFACF


def der(data: bytes, pos: int):
    """One DER element: returns (tag, body, next_position)."""
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[pos:pos + count], 'big')
        pos += count
    return tag, data[pos:pos + length], pos + length


def im4p_payload(data: bytes) -> bytes:
    """The payload octet string out of an IM4P, or the input if it is not one.

    IM4P is SEQUENCE { IA5String "IM4P", IA5String type, IA5String description,
    OCTET STRING payload, ... }. Only the fourth element is wanted.
    """
    if len(data) < 2 or data[0] != 0x30:
        return data
    _, body, _ = der(data, 0)
    pos, elements = 0, []
    while pos < len(body) and len(elements) < 4:
        tag, value, pos = der(body, pos)
        elements.append((tag, value))
    if len(elements) < 4 or elements[0][1] != b'IM4P':
        return data
    return elements[3][1]


def decompress(payload: bytes, name: str) -> bytes:
    if payload[:4] == b'bvx2':
        proc = subprocess.run(['lzfse', '-decode'], input=payload, capture_output=True)
        if proc.returncode or not proc.stdout:
            raise SystemExit(f'{name}: lzfse failed: {proc.stderr.decode()[:200]}')
        return proc.stdout
    if payload[:8] == b'complzss':
        # Apple's LZSS header: magic, adler32, decompressed size, compressed size.
        _, _, size, _ = struct.unpack_from('>IIII', payload, 0)
        return lzss(payload[0x180:], size)
    return payload


def lzss(data: bytes, size: int) -> bytes:
    """Apple's variant of LZSS, as used by older kernelcaches."""
    window = bytearray(0x1000)
    out = bytearray()
    pos, cursor, flags = 0, 0xFEE, 0
    while len(out) < size and pos < len(data):
        flags >>= 1
        if not flags & 0x100:
            flags = data[pos] | 0xFF00
            pos += 1
        if flags & 1:
            byte = data[pos]
            pos += 1
            out.append(byte)
            window[cursor] = byte
            cursor = (cursor + 1) & 0xFFF
        else:
            low, high = data[pos], data[pos + 1]
            pos += 2
            offset = low | ((high & 0xF0) << 4)
            for n in range((high & 0x0F) + 3):
                byte = window[(offset + n) & 0xFFF]
                out.append(byte)
                window[cursor] = byte
                cursor = (cursor + 1) & 0xFFF
    return bytes(out)


def identify(blob: bytes) -> str:
    if len(blob) >= 4 and struct.unpack_from('<I', blob)[0] == MH_MAGIC_64:
        filetype = struct.unpack_from('<I', blob, 12)[0]
        return f'Mach-O (filetype {filetype})'
    if blob[:4] in (b'bvx2', b'bvx-'):
        return 'lzfse (undecompressed)'
    # An Apple device tree starts with a property count and a node count.
    if len(blob) >= 8 and struct.unpack_from('<I', blob)[1 if False else 0] < 64:
        return 'device tree (probably)'
    return 'raw'


def is_url(text):
    return text.lower().startswith(('http://', 'https://'))


def repo_root():
    return pathlib.Path(__file__).resolve().parents[1]


def load_remote():
    """tools/ipsw_remote.py, imported only when an IPSW is a URL.

    Loaded by path, and shared through sys.modules, so that tools which import
    this file by path (extract-root-hash.py, cycle_lib) need nothing else on
    sys.path and see one set of exception classes.
    """
    module = sys.modules.get('ipsw_remote')
    if module is None:
        spec = importlib.util.spec_from_file_location(
            'ipsw_remote', pathlib.Path(__file__).resolve().with_name('ipsw_remote.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules['ipsw_remote'] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules['ipsw_remote']
            raise
    return module


def find_identity(manifest, board):
    identity = next((i for i in manifest['BuildIdentities']
                     if i.get('Info', {}).get('DeviceClass') == board
                     and i.get('Info', {}).get('RestoreBehavior') == 'Erase'), None)
    if identity is None:
        classes = sorted({i.get('Info', {}).get('DeviceClass', '?')
                          for i in manifest['BuildIdentities']})
        raise SystemExit(f'no Erase identity for {board}. Boards: {" ".join(classes)}')
    return identity


def components(identity, names, chip):
    """Component name -> member path for everything this tool extracts."""
    # SPTM is named per SoC and is not always listed in the identity's manifest,
    # so it is resolved from the chip ID against what the IPSW actually carries.
    soc = f't{int(str(chip), 16):x}' if str(chip).startswith('0x') else None
    extra = {}
    if soc:
        for entry in names:
            if entry.endswith(f'sptm.{soc}.release.im4p'):
                extra['SPTM'] = entry
            if entry.endswith(f'exclavecore_bundle.{soc}.RELEASE.im4p'):
                extra['ExclaveCore'] = entry

    todo = {}
    for key in WANTED:
        path = identity['Manifest'].get(key, {}).get('Info', {}).get('Path')
        if path:
            todo[key] = path
    todo.update(extra)
    return todo


def extract_components(zf, todo, names, out):
    """Unwrap, decompress and write each component: the one extraction path for a file and a URL."""
    for key, path in todo.items():
        if path not in names:
            print(f'  {key:<28} MISSING ({path})')
            continue
        raw = im4p_payload(zf.read(path))
        blob = decompress(raw, key)
        name = pathlib.Path(path).name
        if name.endswith('.im4p'):
            name = name[:-len('.im4p')]
        target = out / name
        target.write_bytes(blob)
        print(f'  {key:<28} {target.name:<34} {len(blob):>12,}  {identify(blob)}')


def print_plan(zf, todo, names, remote):
    """List the members that will be read; for a URL also what that costs.

    Returns the bytes a URL run will have fetched once it has read them (the
    bytes already fetched plus the whole blocks still missing), None for a file.
    """
    print('plan: members to read (compressed bytes in the archive)')
    total, spans = 0, []
    for key, path in todo.items():
        if path not in names:
            print(f'  {key:<28} {"MISSING":>14}  {path}')
            continue
        info = zf.getinfo(path)
        total += info.compress_size
        print(f'  {key:<28} {info.compress_size:>14,}  {path}')
        if remote is not None:
            spans.append(load_remote().member_span(info))
    print(f'  {"total":<28} {total:>14,}')
    if remote is None:
        return None
    later = remote.bytes_to_fetch(spans)
    projected = remote.bytes_fetched + later
    print(f'remote archive of {remote.size:,} bytes; so far {remote.bytes_fetched:,} bytes fetched in '
          f'{remote.requests} requests (first block, central directory, BuildManifest.plist)')
    print(f'  the members need {later:,} more bytes, in whole blocks of {remote.block_size:,}')
    # A local header's extra field can be longer than its directory entry's, which moves a member's end by a few bytes.
    print(f'  total fetch {projected:,} bytes = {100 * projected / remote.size:.2f}% of the archive, '
          f'give or take one block per member; cap {remote.max_fetch:,} bytes')
    return projected


def extract(args, remote):
    """Everything after argument parsing; remote is an open ipsw_remote.RangeFile or None for a file."""
    if remote is None:
        ipsw = pathlib.Path(args.ipsw)
        out = pathlib.Path(args.out) if args.out else \
            ipsw.parent / 'extracted' / args.board
        if not args.dry_run:
            out.mkdir(parents=True, exist_ok=True)
        zf = zipfile.ZipFile(ipsw)
    else:
        out = pathlib.Path(args.out) if args.out else \
            repo_root() / 'downloads' / 'extracted' / args.board
        zf = load_remote().open_zip(remote)
    manifest = plistlib.loads(zf.read('BuildManifest.plist'))
    identity = find_identity(manifest, args.board)

    chip = identity.get('ApChipID', '?')
    print(f'{args.board}: ApChipID {chip}, ApBoardID {identity.get("ApBoardID", "?")}, '
          f'build {manifest.get("ProductBuildVersion", "?")}')

    names = zf.namelist()
    todo = components(identity, names, chip)
    if args.dry_run or remote is not None:
        projected = print_plan(zf, todo, names, remote)
        if remote is not None and projected > remote.max_fetch:
            print(f'refused: the plan fetches {projected:,} bytes, over the --max-fetch-mib cap of '
                  f'{remote.max_fetch:,} bytes; nothing was extracted', file=sys.stderr)
            return 2
        if args.dry_run:
            print('dry run: nothing extracted')
            return 0

    out.mkdir(parents=True, exist_ok=True)
    extract_components(zf, todo, names, out)

    print(f'\n{len(todo)} components in {out}')
    if remote is not None:
        print(f'remote: {remote.summary()}')
    return 0


def main(argv=None, remote_factory=None):
    """remote_factory(url, max_fetch=bytes) replaces ipsw_remote.RangeFile; tests use it to supply a transport."""
    ap = argparse.ArgumentParser()
    ap.add_argument('ipsw', help='the restore IPSW: a path, or an https:// URL read in place with Range requests')
    ap.add_argument('board', help='device class, e.g. j700ap')
    ap.add_argument('--out', default=None,
                    help='output directory (default downloads/extracted/<board>)')
    ap.add_argument('--dry-run', action='store_true',
                    help='print the members that would be read (and what a URL would fetch), extract nothing')
    ap.add_argument('--max-fetch-mib', type=int, default=None,
                    help='URL only: refuse to fetch more than this many MiB in total (default 1024)')
    args = ap.parse_args(argv)

    if not is_url(args.ipsw):
        if args.max_fetch_mib is not None:
            ap.error('--max-fetch-mib applies only to an http(s):// IPSW')
        return extract(args, None)

    if args.max_fetch_mib is not None and args.max_fetch_mib < 1:
        ap.error('--max-fetch-mib must be at least 1')
    ipsw_remote = load_remote()
    cap = args.max_fetch_mib << 20 if args.max_fetch_mib else ipsw_remote.DEFAULT_MAX_FETCH
    remote = None
    try:
        remote = (remote_factory or ipsw_remote.RangeFile)(args.ipsw, max_fetch=cap)
        return extract(args, remote)
    except ipsw_remote.FetchCapExceededError as error:
        refusal, code = error, 2
    except (ipsw_remote.RemoteZipError, zipfile.BadZipFile) as error:
        refusal, code = error, 1
    finally:
        if remote is not None:
            remote.close()
    print(f'refused: {refusal}', file=sys.stderr)
    if remote is not None:
        print(f'remote: {remote.summary()}', file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
