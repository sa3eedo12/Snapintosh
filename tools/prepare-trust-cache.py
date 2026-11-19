#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Extract the profile's BaseSystem trust cache after checking its manifest.

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
    ap.add_argument('--guest-profile', default='j700ap-26A428-no-clpc')
    ap.add_argument('--output', type=Path)
    ap.add_argument('--max-fetch-mib', type=int, default=None,
                    help='URL only: refuse to fetch more than this many MiB in total (default 16)')
    args = ap.parse_args(argv)
    firmware = runpy.run_path(str(ROOT / 'tools/extract-firmware.py'))
    ipsw_remote = firmware['load_remote']()
    if args.max_fetch_mib is not None and (not firmware['is_url'](args.ipsw) or args.max_fetch_mib < 1):
        ap.error('--max-fetch-mib must be at least 1 and applies only to an http(s):// IPSW')
    profile = get_profile(args.guest_profile)
    spec = profile.trust_cache
    if spec is None or profile.root_hash is None:
        raise ValueError('selected guest has no audited BaseSystem trust cache')
    archive, remote = ipsw_remote.open_ipsw(args.ipsw, max_fetch=(args.max_fetch_mib or 16) << 20, factory=remote_factory)
    with archive, (remote if remote is not None else contextlib.nullcontext()):
        manifest = plistlib.loads(archive.read('BuildManifest.plist'))
        if manifest['ProductBuildVersion'] != profile.root_hash.build_id:
            raise ValueError('IPSW build does not match guest profile')
        identities = [i for i in manifest['BuildIdentities'] if
                      i['Info'].get('DeviceClass') == profile.root_hash.device_class]
        if not identities:
            raise ValueError('IPSW has no selected board identity')
        for identity in identities:
            component = identity['Manifest'][spec.component]
            if (component['Info']['Path'] != spec.member or
                    component['Digest'].hex() != spec.component_sha384):
                raise ValueError('manifest trust cache differs from pinned profile')
        payload = spec.extract(archive.read(spec.member))
    output = args.output or ROOT / 'downloads/extracted' / profile.firmware_subdir / spec.filename
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() != payload:
        raise ValueError(f'refusing to replace a different cache: {output}')
    output.write_bytes(payload)
    print(f'{output}: {spec.entries} entries, {len(payload)} bytes, '
          f'SHA-256 {hashlib.sha256(payload).hexdigest()}')


if __name__ == '__main__':
    main()
