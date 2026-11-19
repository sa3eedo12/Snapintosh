#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Extract the profile-bound active-root hash payload from its matching IPSW.

The IPSW is a path or an https:// URL; a URL is read in place with Range requests (tools/ipsw_remote.py), a few MB for this member.
"""
import argparse
import contextlib
import hashlib
from pathlib import Path
import plistlib
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'probes'))
from guestprofile import get_profile


def main(argv=None, remote_factory=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('ipsw', help='the restore IPSW: a path, or an https:// URL read in place with Range requests')
    ap.add_argument('--guest-profile', default='j700ap-26A428')
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--max-fetch-mib', type=int, default=None,
                    help='URL only: refuse to fetch more than this many MiB in total (default 16)')
    args = ap.parse_args(argv)
    firmware = runpy.run_path(str(ROOT / 'tools/extract-firmware.py'))
    ipsw_remote = firmware['load_remote']()
    if args.max_fetch_mib is not None and (not firmware['is_url'](args.ipsw) or args.max_fetch_mib < 1):
        ap.error('--max-fetch-mib must be at least 1 and applies only to an http(s):// IPSW')
    profile = get_profile(args.guest_profile)
    spec = profile.root_hash
    if spec is None:
        raise ValueError('profile has no qualified root-hash component')
    archive, remote = ipsw_remote.open_ipsw(args.ipsw, max_fetch=(args.max_fetch_mib or 16) << 20, factory=remote_factory)
    with archive, (remote if remote is not None else contextlib.nullcontext()):
        manifest = plistlib.loads(archive.read('BuildManifest.plist'))
        if manifest['ProductBuildVersion'] != spec.build_id:
            raise ValueError('IPSW build does not match profile')
        identities = [i for i in manifest['BuildIdentities']
                      if i['Info'].get('DeviceClass') == spec.device_class and
                      i['Info'].get('Variant') == 'macOS Customer']
        if len(identities) != 1:
            raise ValueError('expected one native macOS identity for this board')
        component = identities[0]['Manifest'][spec.component]
        path = component['Info']['Path']
        data = archive.read(path)
        if (hashlib.sha256(data).hexdigest() != spec.component_sha256 or
                hashlib.sha384(data).digest() != component['Digest']):
            raise ValueError('root-hash component differs from profile or manifest')
    extract = firmware['im4p_payload']
    payload = extract(data)
    if len(payload) != spec.payload_bytes or hashlib.sha256(payload).hexdigest() != spec.payload_sha256:
        raise ValueError('extracted root-hash payload differs from profile')
    if args.output.exists():
        if args.output.read_bytes() != payload:
            raise ValueError('refusing to replace a different existing output')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
    print(f'{path}: {len(payload)} raw bytes -> {args.output}')
    print(f'SHA256 {spec.payload_sha256}')


if __name__ == '__main__':
    main()
