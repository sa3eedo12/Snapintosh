# SPDX-License-Identifier: MIT
"""The root carry, host side: a GUEST.DAT that holds Apple's BaseSystem root as the RAM disk of the guest, written as a stream (docs/SP11-STICK-ROOT.md). Evidence: EXPERIMENTAL,
hardware NOT RUN. Nothing here approves, authorises or stages anything.

    SP11_ROOT_BUILD=1 python3 tools/sp11_stick_bundle_build.py root-guest synthetic|real --facts F --base A --guest-mib N --time-box-s S --out DIR ...

The command writes DIR/GUEST.DAT and DIR/root-guest-report.json and nothing else: no PIN.DAT, no PAYLOAD.MAN, no record, no authorisation, so what it leaves is not a bundle and
cannot be staged. It exists so that the 1.9 GB guest can be built, measured and digested on the host before a record for it is commissioned. It refuses without SP11_ROOT_BUILD=1
(ROOT_GATE_OFF): the default is that the builder does what it always did. A multi-GiB file is only ever streamed here: the root is mapped read-only by probes/ramdisk.py and is read
in windows, GUEST.DAT is written and read back in windows (a pread view, not a mapping, so that the resident set is a window and not the file), and the cut of the root into
chunks is tools/sp11_stick_bundle_source.py's.

The guest is built by the loader that built the guests of rungs 1 to 7 (probes/xnuboot.py build(), through tools/sp11_stick_bundle_chunks.py real_built): the root, its root hash
and the IOKit RAM-disk mode are passed to it as they are passed on the A16 (probes/run.py), and it adds the /chosen properties of the RAM root (boot-ramdmg-size and -extents,
boot-uuid, apfs-preboot-uuid, the root-hash payload, protected-data-access 0 and sepfw-load-at-boot 0). What this module adds is the command-line words for a root that takes
minutes to mount, `cpu_boot_timeout_secs=<over the box>` and `wdt=-1`, and the RTC seed that the A16 route took at upload, baked at build from an explicit epoch."""
import bisect
import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_source as src  # noqa: E402

GATE_ENV = 'SP11_ROOT_BUILD'
REPORT = 'root-guest-report.json'
BOX_MAX_S = 1200                    # SP11_STICK_MONITOR_BOX_MAX_S of the pair built with --root-carry (tools/sp11_stick_wrapper_build.py ROOT_CARRY_BOX_MAX_S): the longest box this command builds a guest for
DATA_TOP_BYTES = 4 << 30            # the runner puts its data files at the top of RAM, at most 2^36 - 1 (q1n1 Q1N1_SP11_BIGFILE_TOP), and the sum of the manifest's sizes is at most 4 GiB (q1n1 preload.c)
CHUNK_CAP_KIB = 8192                # the runner's flush unit with Q1N1_SP11_BIGFILE (q1n1 docs/SP11-PAYLOAD-BIGFILE.md): 256 operations of 8 MiB are 2 GiB
FREE_AFTER_BYTES = 20 << 30         # the disk that must stay free after the file is written (the coordinator's stop line)
ROOT_WORDS = ('wdt=-1',)            # `wdt=-1` keeps XNU's own watchdog off while the root mounts; whether the unit then leaves its WD1 channel unarmed is UNKNOWN (docs/SP11-STICK-ROOT.md)
DEFAULT_PROFILE = 'j700ap-26A428-m1-quiet'
REFUSALS = ('ROOT_GATE_OFF', 'ROOT_INPUT_BAD', 'ROOT_BOOT_ARG', 'ROOT_RTC', 'ROOT_PLACEMENT', 'ROOT_NO_ROOM')
ch.KNOWN_REFUSALS.update(REFUSALS)
Refused = ch.Refused


class FileView:
    """A file as a sliceable, sized object read with pread: a window is read when it is asked for and nothing is mapped, so the resident set of a pass over a 2 GiB file is the
    window. The checks of tools/sp11_stick_bundle.py take anything that has len() and slices to bytes."""

    def __init__(self, path):
        self._fd = os.open(path, os.O_RDONLY)
        self._size = os.fstat(self._fd).st_size

    def __len__(self):
        return self._size

    def __getitem__(self, key):
        if not isinstance(key, slice):
            raise TypeError('a FileView is sliced, not indexed')
        start, stop, step = key.indices(self._size)
        if step != 1:
            raise ValueError('a FileView slice has no step')
        out, at = bytearray(), start
        while at < stop:
            piece = os.pread(self._fd, stop - at, at)
            if not piece:
                break
            out += piece
            at += len(piece)
        return bytes(out)

    def close(self):
        os.close(self._fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _emit(out, piece, block=1 << 16):
    """Write `piece`, stepping over each all-zero block of 64 KiB instead of writing it: the file is then sparse where the guest is zero (the bytes read back are the same, and the
    sparse test containers of the tests cost no disk); the file's length is set once, at the end."""
    at = 0
    while at < len(piece):
        end = min(at + block, len(piece))
        if piece.count(0, at, end) == end - at:
            out.seek(end - at, os.SEEK_CUR)
        else:
            out.write(piece[at:end])
        at = end


class VirtualGuest:
    """GUEST.DAT as a sized object that is sliced and never held: the one canonical layout of sb.pack_guest (the tests compare the bytes), made from chunks that are
    dict(kind, dest, data=bytes), dict(kind, dest, size) (ZERO) or dict(kind, dest, size, source=<Source>, offset=<into it>). Building it reads each data chunk once, in windows, for
    its digest (the table needs them, and the file's head comes first); a slice is then made from the head, the table, the zero padding and the chunks' own bytes, so that every check of
    tools/sp11_stick_bundle.py that takes anything with len() and slices (check_guest, check_digests, guest_pin_fields, sha256_range) runs on it as it runs on the file, and the record
    can state the file's size and SHA-256 before one byte of it is written. Chunks must stay alive and unchanged while the object is used."""

    def __init__(self, load_base, load_size, chunks):
        count = len(chunks)
        if not 1 <= count <= sb.CHUNKS_MAX:
            raise Refused('GUEST_COUNT', f'{count} chunks: the table holds 1 to {sb.CHUNKS_MAX}')
        table_end = sb.GUEST_HEAD_BYTES + sb.CHUNK_BYTES * count
        entries, spans, expect = [], [], table_end
        for c in chunks:
            if c.get('data') is None and 'source' not in c:
                entries.append(struct.pack('<QQQHHI32s', c['dest'], c['size'], 0, c['kind'], sb.CHUNK_ZERO, 0, bytes(32)))
                continue
            size = len(c['data']) if c.get('data') is not None else c['size']
            if 'source' in c and c['offset'] + size > len(c['source']):
                raise Refused('CHUNK_EOF', 'a chunk reaches past the end of its source')
            offset = (expect + sb.DATA_ALIGN - 1) & ~(sb.DATA_ALIGN - 1)
            digest = hashlib.sha256()
            for at in range(0, size, sb.WINDOW):
                digest.update(self._piece(c, at, min(sb.WINDOW, size - at)))
            entries.append(struct.pack('<QQQHHI32s', c['dest'], size, offset, c['kind'], 0, 0, digest.digest()))
            spans.append((offset, offset + size, c))
            expect = offset + size
        self.table = b''.join(entries)
        head = bytearray(sb.GUEST_HEAD_BYTES)
        for name, value in (('magic', b'SNAPBGST'), ('version', sb.VERSION), ('head_size', sb.GUEST_HEAD_BYTES), ('entry_size', sb.CHUNK_BYTES), ('entry_count', count),
                            ('table_offset', sb.GUEST_HEAD_BYTES), ('data_offset', table_end), ('file_size', expect), ('load_base', load_base), ('load_size', load_size),
                            ('index_sha256', sb.sha256(self.table))):
            sb.put(head, sb.GUEST_LAYOUT, name, value)
        self.count, self.size, self.load_base, self.load_size = count, expect, load_base, load_size
        self.index_sha256 = sb.sha256(self.table)
        self._front, self._spans, self._starts, self._sha256 = bytes(head) + self.table, spans, [s[0] for s in spans], None

    @staticmethod
    def _piece(chunk, at, n):
        """`n` bytes of a data chunk from its offset `at`."""
        if chunk.get('data') is not None:
            return bytes(chunk['data'][at:at + n])
        return chunk['source'].read_at(chunk['offset'] + at, n)

    def __len__(self):
        return self.size

    def __getitem__(self, key):
        if not isinstance(key, slice):
            raise TypeError('a VirtualGuest is sliced, not indexed')
        start, stop, step = key.indices(self.size)
        if step != 1:
            raise ValueError('a VirtualGuest slice has no step')
        out, at = bytearray(), start
        while at < stop:
            if at < len(self._front):
                end = min(stop, len(self._front))
                out += self._front[at:end]
            else:
                i = bisect.bisect_right(self._starts, at) - 1
                if i >= 0 and at < self._spans[i][1]:
                    lo, end, chunk = self._spans[i]
                    end = min(stop, end)
                    out += self._piece(chunk, at - lo, end - at)
                else:       # the pad that aligns the next chunk (or the end of the file): zeros
                    end = min(stop, self._starts[i + 1] if i + 1 < len(self._starts) else stop)
                    out += bytes(end - at)
            at = end
        return bytes(out)

    def sha256(self):
        """The hex SHA-256 of the whole file, by one pass over it (cached)."""
        if self._sha256 is None:
            self._sha256 = sb.sha256_range(self).hex()
        return self._sha256

    def written(self):
        """What the writer reports and verify_guest binds to: dict(file_size, count, index_sha256)."""
        return dict(file_size=self.size, count=self.count, index_sha256=self.index_sha256.hex())

    def write_to(self, path):
        """The file at `path` (a new file), window by window, sparse where the guest is zero, length set at the end and the bytes on the disk."""
        with open(path, 'xb') as out:
            for at in range(0, self.size, sb.WINDOW):
                _emit(out, self[at:at + sb.WINDOW])
            out.truncate(self.size)
            out.flush()
            os.fsync(out.fileno())


def write_guest(path, load_base, load_size, chunks):
    """GUEST.DAT written to a new file `path` in the one canonical layout of sb.pack_guest (the tests compare the bytes), chunk by chunk and window by window (VirtualGuest).
    Returns dict(file_size, count, index_sha256)."""
    guest = VirtualGuest(load_base, load_size, chunks)
    guest.write_to(path)
    return guest.written()


def verify_guest(path, load_base, load_size, written):
    """The independent read of the file that was written, through a pread view: the structure by the interface's own check (at the limits of the wrapper of the root carry), the
    table digest, every data chunk's digest and the digest of the whole file. Returns dict(sha256, table) and raises the interface's refusal name."""
    with FileView(path) as view, sb.bigfile():
        head, table = sb.unpack_guest(view)
        bind = (load_base, load_size, written['file_size'], written['count'], bytes.fromhex(written['index_sha256']))
        if code := sb._guest_check(view, len(view), bind):
            raise Refused(sb.error_name(code), 'GUEST.DAT as it was read back')
        if len(view) != written['file_size']:
            raise Refused('GUEST_SIZE', 'the file is not the size that was written')
        if sb.sha256(view[sb.GUEST_HEAD_BYTES:sb.GUEST_HEAD_BYTES + sb.CHUNK_BYTES * len(table)]) != head['index_sha256']:
            raise Refused('INDEX_DIGEST', 'the table of the file read back')
        for i, c in enumerate(table):
            if not c['flags'] & sb.CHUNK_ZERO and sb.sha256_range(view, c['file_offset'], c['size']) != c['sha256']:
                raise Refused('CHUNK_DIGEST', f'chunk {i} of the file read back')
        return dict(sha256=sb.sha256_range(view).hex(), table=table)


def gate():
    if os.environ.get(GATE_ENV) != '1':
        raise Refused('ROOT_GATE_OFF', f'{GATE_ENV}=1 is the explicit gate of the root carry build: it writes a GUEST.DAT of up to 2 GiB; without it the builder does what it always did')


def check_data_top(place):
    """The runner's data files sit at the top of RAM (2^36 - 1 at most) and the guest's placement is declared to the wrapper only at run time: a placement that reaches the top
    DATA_TOP_BYTES of the address space is refused here, so that the files can land there. The wrapper's own PLACE_DECLARED check is the authority; this keeps a build from the
    placement that can only fail it."""
    end = place['place_base'] + place['place_size']
    if end > sb.IPA_LIMIT - DATA_TOP_BYTES:
        raise Refused('ROOT_PLACEMENT', f'the placement ends at {end:#x}; the runner puts its files in the top {DATA_TOP_BYTES >> 30} GiB below 2^36 ({sb.IPA_LIMIT - DATA_TOP_BYTES:#x})')


def boot_arg(box_s, wait_s):
    """The one --boot-arg string of the root's command line: XNU waits for a CPU that is never started for `wait_s` seconds, which must outlast the box, and its watchdog is off."""
    if not 1 <= box_s <= BOX_MAX_S:
        raise Refused('OPTIONS_BAD', f'--time-box-s is 1 to {BOX_MAX_S}: the wrapper built with --root-carry refuses a longer box before it calls the monitor')
    if not box_s < wait_s <= 86400:
        raise Refused('ROOT_BOOT_ARG', f'cpu_boot_timeout_secs {wait_s} must be over the box ({box_s} s) and at most 86400')
    return ' '.join((f'cpu_boot_timeout_secs={wait_s}', *ROOT_WORDS))


def form_boot_arg(box_s, wait_s):
    """The one --boot-arg word of rung 9's form (ch.RUNG9_FORM): the loader's own command line already ends in `wdt=-1`, so unlike boot_arg this adds nothing after the wait. The box and the
    wait are the form's, and no other (the closed list of tools/sp11_stick_bundle_chunks.py)."""
    word = f'cpu_boot_timeout_secs={wait_s}'
    if (box_s, word) != (ch.RUNG9_FORM[2], ch.RUNG9_FORM[1]):
        raise Refused('ROOT_BOOT_ARG', f'rung 9\'s form is a box of {ch.RUNG9_FORM[2]} s and {ch.RUNG9_FORM[1]}; got a box of {box_s} s and {word}')
    return word


def is_form(a):
    """True when the build `a` is the one of rung 9 (its guest profile): then its guest is the form's (let_panic data, the acknowledging monitor, the form's word, the clock seeded by the profile)."""
    return getattr(a, 'guest_profile', None) == ch.RUNG9_PROFILE


def real_guest(a, xb, place, facts, profile):
    """The real guest with its RAM-disk root, as root-guest and the bundle of rung 9 build it: the loader is configured for `profile`, the inputs are checked, the loader is called with the root,
    the clock is seeded by the profile (a second seed would refuse), and the guest is read back against the profile and the root's own /chosen properties. Returns (built, (base, size) of the
    root, number of published nodes, the profile's root-hash spec)."""
    import sp11_stick_bundle_profile as gpb
    xb.configure_guest(profile)
    check_inputs(a, xb)
    a.root_inputs = dict(ramdisk=str(a.ramdisk), root_hash=str(a.root_hash), ramdisk_mode='iokit')
    if getattr(a, 'isc_image', None):
        a.isc_info = check_isc(a.isc_image)
        a.root_inputs['isc_image'] = str(a.isc_image)
    built = ch.real_built(a, xb, place, facts)
    if (why := seed_rtc_failure(built)):
        raise Refused('ROOT_RTC', why)
    if not built.get('ramdisk_root_range'):
        raise Refused('ROOT_INPUT_BAD', 'the loader built no RAM-disk root')
    if bool(getattr(a, 'isc_image', None)) != bool(built.get('ramdisk_isc_range')):
        raise Refused('ROOT_INPUT_BAD', 'the loader built the RAM disk with an ISC partition only when --isc-image was given, and with none otherwise')
    spec = xb.GUEST_PROFILE.root_hash
    root_range = built['ramdisk_range']      # the medium that /chosen names: the root alone, or with --isc-image the GPT medium that holds the root and the ISC
    nodes = gpb.read_back(xb, built, profile, dict(extents=root_range, boot_uuid=spec.boot_uuid, payload=Path(a.root_hash).read_bytes()))
    return built, root_range, nodes, spec


def synthetic_root(size, label='synthetic-root'):
    """A stand-in for a root image: SHAKE-128 blocks with a 96 KiB stretch of zeros every 13 MiB and a 40 KiB one between them, as a real root has some of both. Deterministic."""
    if size < 16 or size % 16:
        raise Refused('ROOT_INPUT_BAD', 'a root is a multiple of 16 bytes')
    mib, zeros = 1 << 20, []
    for k in range(1, size // (13 * mib) + 1):
        base = k * 13 * mib
        zeros += [(base, base + 96 * 1024), (base + 5 * mib, base + 5 * mib + 40 * 1024)]
    return src.Generated(label, size, [(s, min(e, size)) for s, e in zeros if s < size])


def seed_rtc_failure(built):
    """ROOT_RTC text when the guest has a clock that needs a seed and none was given, else None."""
    unseeded = [m for m in built.get('device_models', ()) if m.kind == 48 and m.config[3] == 0]
    return f'the guest has {len(unseeded)} unseeded RTC model(s): give --rtc-epoch-us (the epoch, in microseconds, that the virtual clock starts at)' if unseeded else None


def check_inputs(a, xb):
    """The files of the root, named and checked before the loader reads them: regular files, and the trust cache is the one the loader reads (the profile's file in its firmware
    directory), because probes/xnuboot.py takes it from there."""
    spec = xb.GUEST_PROFILE.trust_cache
    for flag, path in (('--ramdisk', a.ramdisk), ('--root-hash', a.root_hash), ('--trust-cache', a.trust_cache)):
        if not path or not Path(path).is_file():
            raise Refused('ROOT_INPUT_BAD', f'{flag} is not a regular file')
    if spec is None or Path(a.trust_cache).resolve() != (Path(xb.FIRMWARE_DIR) / spec.filename).resolve():
        raise Refused('ROOT_INPUT_BAD', '--trust-cache must be the profile\'s own file in the firmware directory (the loader reads it from there): '
                                        + (f'{spec.filename}' if spec else 'this profile has no trust cache'))
    if xb.GUEST_PROFILE.root_hash is None or xb.GUEST_PROFILE.installed_root is not None:
        raise Refused('ROOT_INPUT_BAD', 'the guest profile is not a RAM-disk root profile (a root hash and no installed root)')


def check_isc(path):
    """ISC_ROLES' image, read before the loader maps it: a regular file, a raw APFS container (probes/ramdisk.py checks the geometry) whose volumes have exactly the roles that
    tools/cycle_lib/speed_test.py isc_roles demands (xART 256, Preboot 16, Hardware 320; checked from the latest checkpoint, not from names). Returns dict(bytes, sha256), the digest
    read in 16 MiB windows: the image is a sparse file of 1,025 MiB of which a few MiB are allocated, and the record states its digest."""
    if not Path(path).is_file():
        raise Refused('ROOT_INPUT_BAD', '--isc-image is not a regular file')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        with contextlib.redirect_stderr(io.StringIO()):     # cycle_lib warns on stderr that the machine's pins.json is absent: not this build's business
            from cycle_lib import speed_test
        roles = speed_test.isc_roles(path)
    except Exception as error:      # the checker's own refusal type is cycle_lib.common.Refused
        raise Refused('ROOT_INPUT_BAD', f'--isc-image is not a raw APFS container whose volumes the speed test reads ({error})') from None
    if sorted(roles) != sorted(speed_test.ISC_ROLES):
        raise Refused('ROOT_INPUT_BAD', f'--isc-image has the volume roles {sorted(roles)}, and the ISC has {sorted(speed_test.ISC_ROLES)} (xART, Preboot, Hardware)')
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(16 << 20), b''):
            digest.update(block)
    return dict(bytes=Path(path).stat().st_size, sha256=digest.hexdigest(), roles=sorted(roles))


def check_out(a, bb):
    """--out is confined as the builder's is (under build/ or an --allow-under directory, no `..`, no symbolic link, no protected place) and must be empty, or hold this
    command's report when --force is given."""
    out, st = Path(os.path.abspath(a.out)), bb.stager()
    roots = [bb.ROOT / 'build', *(Path(os.path.abspath(r)) for r in a.allow_under)]
    if '..' in Path(a.out).parts or not any(r in (out, *out.parents) for r in roots) or st.find_symlink_component(out) is not None:
        raise Refused('OUT_DIR_REFUSED', f'--out must be below {bb.ROOT / "build"} or an --allow-under directory, with no ".." and no symbolic link')
    try:
        st.refuse_protected_location(out)
    except st.Refusal as error:
        raise Refused('OUT_DIR_REFUSED', error.reason) from None
    if out.exists() and (not out.is_dir() or any(out.iterdir())) and not (out.is_dir() and a.force and (out / REPORT).is_file()):
        raise Refused('OUT_NOT_EMPTY', f'{a.out} is not an empty directory; --force replaces only a directory with a {REPORT} from an earlier build')
    return out


def check_room(out, estimate):
    """ROOT_NO_ROOM unless the disk that will hold `out` keeps FREE_AFTER_BYTES free after `estimate` bytes are written."""
    anchor = out
    while not anchor.exists():
        anchor = anchor.parent
    free = shutil.disk_usage(anchor).free
    if free - estimate < FREE_AFTER_BYTES:
        raise Refused('ROOT_NO_ROOM', f'{free >> 20} MiB free, {estimate >> 20} MiB to write and {FREE_AFTER_BYTES >> 30} GiB must stay free')


def size_of(chunk):
    return len(chunk['data']) if chunk.get('data') is not None else chunk['size']


def root_chunks(chunks, base, size):
    """The chunks that carry the root: those that start inside its range [base, base + size) (the loader's `ramdisk_root_range`)."""
    return [c for c in chunks if base <= c['dest'] < base + size]


def run(a, kind):
    """The command. Returns the report dict, which is also written."""
    import sp11_stick_bundle_build as bb      # the builder imports this module: the cycle is broken here
    gate()
    wait = a.cpu_boot_timeout_s if a.cpu_boot_timeout_s is not None else a.time_box_s + 60
    words = form_boot_arg(a.time_box_s, wait) if kind == 'real' and is_form(a) else boot_arg(a.time_box_s, wait)
    out = check_out(a, bb)
    facts, dumps = bb.load_facts(a.facts), bb.find_dumps(a.dump)
    lines = bb.facts_check(facts, dumps)
    place = bb.placement_of(a)
    check_data_top(place)
    declared = bb.declared_ranges(a.declared)
    if bad := [(n, sb.error_name(c)) for n, _, c in bb.check_maps(bb.bare_pin(place, facts['avoid']), dumps, (facts['fb_base'], facts['fb_bytes']), declared) if c]:
        raise Refused(bad[0][1], 'maps: ' + ', '.join(f'{n} {c}' for n, c in bad))
    xb, real = bb.xnuboot(), None
    nodes = profile = None
    if kind == 'synthetic':
        bb.place_layout(xb, place)
        built = ch.synthetic_built(xb, int(a.image_mib * bb.MIB), int(a.tables_mib * bb.MIB), int(a.sptm_data_mib * bb.MIB), a.seed, a.time_box_s)
        lay = ch.layout_of(xb)
        disk_pa = (max(hi for _, hi, _ in lay.classes) + 0x3FFF) & ~0x3FFF
        root = synthetic_root(a.root_mib * bb.MIB, a.seed + '/root')
        built['chunks'][disk_pa] = root
        root_range = (disk_pa, len(root))
        inputs = dict(root_bytes=len(root), root_sha256=None, root_hash_sha256=None, trust_cache_sha256=None)
    else:
        import sp11_stick_bundle_profile as gpb
        profile = gpb.resolve(a)
        if is_form(a):      # rung 9's guest: the form's own words (let_panic is a data word of PARAMS, the acknowledge is the pair's), and the clock is the profile's
            if a.rtc_epoch_us is not None:
                raise Refused('ROOT_RTC', f'--guest-profile {profile} seeds its own clock (the menu\'s RTC_EPOCH_US): a second seed would leave no unseeded clock to seed (--rtc-epoch-us)')
            a.panel, a.sptm_hvc_calls, a.boot_arg, a.let_panic, a.cpu_start = 'console', 0, words, True, 'ack'
        else:
            a.panel, a.sptm_hvc_calls, a.boot_arg, a.let_panic, a.cpu_start = 'console', 0, words, False, 'stop'
        built, root_range, nodes, spec = real_guest(a, xb, place, facts, profile)
        lay = ch.layout_of(xb)
        inputs = dict(root_bytes=Path(a.ramdisk).stat().st_size, root_sha256=spec.image_sha256, root_hash_sha256=spec.payload_sha256,
                      trust_cache_sha256=xb.GUEST_PROFILE.trust_cache.payload_sha256)
        real = dict(guest_profile=profile, kernelcache_sha256=bb.sha(Path(a.kernelcache).read_bytes()), rtc_epoch_us=a.rtc_epoch_us, nodes=nodes)
    chunks = ch.chunks_from_built(built, lay)
    ch.check_params(chunks, place, a.time_box_s)
    if (params := [c for c in chunks if c['kind'] == sb.KIND_PARAMS]) and params[0]['dest'] != lay.params_pa:
        raise Refused('BUILT_SHAPE', 'x3 of the monitor call, the PARAMS chunk destination, is not PARAMS_PA')
    carried = root_chunks(chunks, *root_range)
    if not carried or sum(size_of(c) for c in carried) != root_range[1]:
        raise Refused('ROOT_INPUT_BAD', 'the chunks of the guest do not hold the whole root where the loader put it')
    estimate = sum(size_of(c) for c in chunks if c.get('data') is not None or 'source' in c) + sb.GUEST_HEAD_BYTES + sb.CHUNK_BYTES * len(chunks)
    check_room(out, estimate)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    try:
        written = write_guest(out / 'GUEST.DAT', place['guest_base'], place['guest_size'], chunks)
        os.chmod(out / 'GUEST.DAT', 0o644)
        checked = verify_guest(out / 'GUEST.DAT', place['guest_base'], place['guest_size'], written)
        report = report_of(a, kind, place, lines, written, checked, chunks, carried, inputs, real, words, wait, lay, built)
        (out / REPORT).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return report


def report_of(a, kind, place, lines, written, checked, chunks, carried, inputs, real, words, wait, lay, built):
    by_kind = {}
    for c in checked['table']:
        key = f'{bb_kind(c["kind"])}.{"ZERO" if c["flags"] & sb.CHUNK_ZERO else "DATA"}'
        by_kind[key] = by_kind.get(key, 0) + c['size']
    root_data = sum(size_of(c) for c in carried if c.get('data') is not None or 'source' in c)
    cap = CHUNK_CAP_KIB << 10
    return dict(
        format='sp11-root-guest-report-1', kind=kind, hardware='NOT RUN', evidence='EXPERIMENTAL', not_a_bundle='no PIN.DAT, no PAYLOAD.MAN, no record, no authorisation',
        gate=f'{GATE_ENV}=1', guest=dict(file_size=written['file_size'], sha256=checked['sha256'], index_sha256=written['index_sha256'], chunks=written['count'],
                                         bytes_by_kind=by_kind, file_max_of_the_pair=sb.FILE_MAX_BIG, fits=written['file_size'] <= sb.FILE_MAX_BIG),
        root=dict(**inputs, chunks=len(carried), zero_chunks=sum(1 for c in carried if c.get('data') is None and 'source' not in c), data_bytes_in_file=root_data,
                  dest=f'{min(c["dest"] for c in carried):#x}', span=max(c['dest'] + size_of(c) for c in carried) - min(c['dest'] for c in carried),
                  ramdisk_range=[f'{v:#x}' for v in (built.get('ramdisk_range') or ())]),
        runner=dict(chunk_cap_kib=CHUNK_CAP_KIB, operations=-(-written['file_size'] // cap), operations_max=256, note='INFERRED from W4: unit time of an 8 MiB op is UNKNOWN'),
        placement={k: f'{v:#x}' for k, v in place.items()}, data_top_reserve=DATA_TOP_BYTES, time_box_s=a.time_box_s, cpu_boot_timeout_s=wait, boot_words=words,
        panel='console', facts_checked=lines, **({'real': real} if real else {}), **({'isc': a.isc_info} if getattr(a, 'isc_info', None) else {}),
        not_run=['the wrapper, the monitor and the guest', 'the runner with this file', 'any hardware'])


def bb_kind(number):
    return {v: k[12:] for k, v in sb.CONSTANTS.items() if k.startswith('SP11SB_KIND_') and k != 'SP11SB_KIND_MAX'}[number]


def add_options(p, kind):
    """The options of `root-guest synthetic` and `root-guest real`."""
    for flag, kw in (('--facts', dict(required=True)), ('--dump', dict(action='append', default=[])), ('--base', dict(required=True, help='guest_base: a multiple of 32 MiB')),
                     ('--guest-mib', dict(type=int, required=True, help='the guest\'s RAM; 6144 is the proposal for the root, 384 the default of the other builds')),
                     ('--reserve-mib', dict(type=int)), ('--declared', dict(action='append', default=[])),
                     ('--time-box-s', dict(type=int, required=True, help=f'1 to {BOX_MAX_S}: the cap of the wrapper built with --root-carry')),
                     ('--cpu-boot-timeout-s', dict(type=int, default=None, help='cpu_boot_timeout_secs of the guest: over the box (default: the box plus 60)')),
                     ('--out', dict(required=True)), ('--force', dict(action='store_true')), ('--allow-under', dict(action='append', default=[])),
                     ('--seed', dict(default='synthetic' if kind == 'synthetic' else 'sp11-stick-bundle', help='label of the guest\'s boot random seed (the label of the other builds by default)'))):
        p.add_argument(flag, **kw)
    if kind == 'synthetic':
        p.add_argument('--root-mib', type=int, default=1776, help='the size of the generated root')
        for flag, default in (('--image-mib', 115.5), ('--tables-mib', 16.0), ('--sptm-data-mib', 5.0)):
            p.add_argument(flag, type=float, default=default)
    else:
        p.add_argument('--kernelcache', required=True)
        p.add_argument('--guest-profile', default=DEFAULT_PROFILE, metavar='NAME', help='a profile of the closed menu of docs/SP11-M1-GUEST-PROFILES.md (default: %(default)s)')
        p.add_argument('--ramdisk', required=True, help='the raw APFS root image (the profile pins its SHA-256)')
        p.add_argument('--root-hash', required=True, help='the raw 208-byte root-hash payload (the profile pins its SHA-256)')
        p.add_argument('--trust-cache', required=True, help='the profile\'s static trust cache, in the firmware directory the loader reads')
        p.add_argument('--isc-image', default=None, help='EXPERIMENTAL: the three-role ISC image (tools/make-isc-fixture.py --build-three-role): the RAM disk is then a GPT medium of the root and the ISC')
        p.add_argument('--rtc-epoch-us', type=int, default=None, help='the epoch, in microseconds, that the virtual RTC starts at (the A16 took it at upload); needed when the guest has an RTC')
