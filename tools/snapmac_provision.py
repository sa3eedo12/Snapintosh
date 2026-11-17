#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP11 offline storage provisioning (docs/SP11-STORAGE-PROVISIONING.md): IMAGE FILES ONLY. EXPERIMENTAL; hardware NOT RUN.

The SP11's SSD is user-removable. The plan is: the operator shrinks the Windows volume and, after the SSD is on a Mac, creates exactly one GPT
entry (Windows' own partition manager), and these tools check, and then write, the macOS window into that partition. They take the planner's
plan (tools/snapmac-partition-plan.py, reused, not copied) and add what it does not do: the window layout, the digests of every byte that must
not change, a bounded writer and the checks after it.

    plan         survey.json -> window plan: the planner's plan, its digest, the window layout and every protected extent
    pre-image    image + plan + the four readers -> the gate, the image's own GPT against the plan, a digest of every protected extent
    write        image + plan + pre-image + the survey and readers it was made from + container + ISC -> the plan's rules and the gate are checked
                 again, then writes ONLY inside the new partition, the header last; a journal
    post-check   image + plan + pre-image + journal -> every protected extent unchanged, every write inside the partition, the window reads back
    verify-undo  survey + readers + plan + journal -> the planner's verify-undo, and every journaled write lies in the freed extent
    auth-check   the per-run authorisation record of the DEVICE mode, validated; the mode itself is designed here and NOT enabled

No command opens a block device or a volume: every path goes through tools/snapmac_image.py, which refuses anything at or below /dev or
/Volumes and anything that is not a regular file. The device mode (a raw disk, one run, one signed record) is `open_device_for_write`: it
validates the record and then REFUSES, always, because DEVICE_MODE_ENABLED is False and nothing here opens a device node. Nothing in this
file authorises a run. Exit status: 0 ok, 1 a check failed, 2 refused.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import snapmac_image as si  # noqa: E402
import snapmac_window as sw  # noqa: E402

PLAN_FORMAT = 'snapmac-window-plan/1'
PREIMAGE_FORMAT = 'snapmac-preimage/1'
JOURNAL_FORMAT = 'snapmac-write-journal/1'
AUTH_FORMAT = 'snapmac-write-auth/1'
WINDOW_MANIFEST_FORMAT = 'snapmac-window-manifest/1'
MIN_READERS = 4                         # the X6 gate (docs/SURFACE-PRO-11.md section 6.4)
DEVICE_MODE_ENABLED = False             # the authorised device mode is designed below and is NOT enabled
AUTH_MAX_SECONDS = 30 * 60
ACKNOWLEDGEMENTS = ('backup_verified', 'recovery_key_off_device', 'bitlocker_suspended_or_key_off_device', 'windows_shut_down_cleanly',
                    'recovery_media_ready', 'one_hardware_client_only')
AUTH_KEYS = ('format', 'mode', 'run_id', 'issued_utc', 'expires_utc', 'disk', 'plan_sha256', 'preimage_sha256', 'partition',
             'acknowledged', 'tool_source_sha256')
STAMP = '%Y-%m-%dT%H:%M:%SZ'
MODULES = ('snapmac_image.py', 'snapmac_window.py', 'snapmac_provision.py')


class ProvisionError(ValueError):
    """A refusal."""


class StalePreimage(ProvisionError):
    """The image is not the one the pre-image digested."""


class DeviceModeRefused(ProvisionError):
    """The authorised device mode is designed and not enabled."""


_planner = None


def planner():
    """tools/snapmac-partition-plan.py, loaded by path (its name has hyphens); reused, never copied."""
    global _planner
    if _planner is None:
        spec = importlib.util.spec_from_file_location('snapmac_partition_plan', HERE / 'snapmac-partition-plan.py')
        _planner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_planner)
    return _planner


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(',', ':'))


def digest_doc(doc):
    return hashlib.sha256(canonical(doc).encode('utf-8')).hexdigest()


def tool_source_sha256():
    h = hashlib.sha256()
    for name in MODULES:
        h.update(name.encode() + b'\0' + (HERE / name).read_bytes() + b'\0')
    return h.hexdigest()


# ---- The plan -------------------------------------------------------------------------------------------------------------------------------

def _extent(name, offset, length):
    return dict(name=name, offset=offset, bytes=length)


def protected_extents(p):
    """Every byte outside the new partition, as named extents that tile the disk: both GPT zones, each partition, both guards, and the
    unallocated gaps between them. Overlaps and holes are errors: the tiling is what the digests and the writer rely on."""
    d, n, bs = p['disk'], p['new'], p['disk']['block']
    parts = [_extent('gpt-primary', 0, d['first_usable'] * bs), _extent('gpt-backup', (d['last_usable'] + 1) * bs, d['bytes'] - (d['last_usable'] + 1) * bs),
             _extent('guard-before', n['offset'] - n['guard_bytes'], n['guard_bytes']), _extent('guard-after', n['end'], n['guard_bytes'])]
    parts += [_extent(f"partition-{q['index']}", q['first_lba'] * bs, (q['last_lba'] - q['first_lba'] + 1) * bs) for q in p['protected']]
    parts.sort(key=lambda e: e['offset'])
    tiling, cursor = [], 0
    for e in parts:
        if e['offset'] < cursor:
            raise ProvisionError(f"the protected extents overlap at {e['offset']} ({e['name']})")
        if e['offset'] > cursor:
            tiling.append(_extent(f'unallocated-{cursor}', cursor, e['offset'] - cursor))
        tiling.append(e)
        cursor = e['offset'] + e['bytes']
    if cursor > d['bytes']:
        raise ProvisionError('a protected extent leaves the disk')
    if cursor < d['bytes']:
        tiling.append(_extent(f'unallocated-{cursor}', cursor, d['bytes'] - cursor))
    # The one gap between the two guards is the new partition itself: it is the only extent that is not protected.
    window = [e for e in tiling if (e['offset'], e['bytes']) == (n['offset'], n['bytes'])]
    if len(window) != 1 or not window[0]['name'].startswith('unallocated-'):
        raise ProvisionError('the new partition is not the one gap between its guards')
    return [e for e in tiling if e is not window[0]]


def window_plan(survey, isc_region=sw.DEFAULT_ISC_REGION, **plan_args):
    """The planner's plan for `survey`, with its digest, the window layout and the protected extents."""
    pm = planner()
    try:
        p = pm.plan_sp11(survey, **plan_args)
        lay = sw.layout(p['new']['offset'], p['new']['bytes'], isc_region)
    except (pm.PlanError, sw.WindowError) as e:
        raise ProvisionError(f'refused: {e}') from None
    return check_plan_document(dict(format=PLAN_FORMAT, plan=p, plan_sha256=digest_doc(p), layout=lay, protected_extents=protected_extents(p)))


def check_plan_rules(p):
    """The planner's rules for the one new entry, checked again on a plan read from a file. A plan is data: what it says about the disk is
    trusted no further than these rules and the image's own GPT (image_state), so a forged plan cannot name the ESP, a Windows partition or
    a window without its guards as the place to write."""
    pm = planner()
    d, n = p['disk'], p['new']
    bs = d['block']
    if type(bs) is not int or bs not in (512, 4096):
        raise ProvisionError("the plan's block size is not 512 or 4096")
    for key in ('first_lba', 'last_lba', 'offset', 'bytes', 'end', 'guard_bytes'):
        if type(n[key]) is not int or n[key] < 0:
            raise ProvisionError(f'the new entry has no whole number for {key}')
    kind = n['type'].lower() if isinstance(n['type'], str) else None
    if kind in pm.WINDOWS_TYPES:
        raise ProvisionError('the new entry has a Windows type (EFI system, reserved, basic data or recovery): the window is never written there')
    if kind != pm.TYPE:
        raise ProvisionError('the new entry is not of the private Snapintosh type')
    if n['guard_bytes'] < pm.GUARD_BYTES:
        raise ProvisionError(f"the guard is {n['guard_bytes']} bytes; the planner's is {pm.GUARD_BYTES}")
    if not n['bytes'] or n['offset'] % pm.ALIGN_BYTES or n['bytes'] % pm.ALIGN_BYTES:
        raise ProvisionError(f'the new entry is empty or not whole {pm.ALIGN_BYTES}-byte units at a multiple of {pm.ALIGN_BYTES}')
    if (n['offset'], n['end'], n['bytes']) != (n['first_lba'] * bs, (n['last_lba'] + 1) * bs, n['end'] - n['offset']):
        raise ProvisionError("the new entry's bytes and LBAs disagree")
    if not d['first_usable'] <= n['first_lba'] <= n['last_lba'] <= d['last_usable']:
        raise ProvisionError('the new entry is not inside the usable range of the GPT')
    kinds = [q['type'].lower() if isinstance(q['type'], str) else None for q in p['protected']]
    if kind in kinds:
        raise ProvisionError('a protected entry already has the new type')
    for needed, what in ((pm.T_ESP, 'an EFI system partition'), (pm.T_BASIC, 'a Windows data partition')):
        if needed not in kinds:
            raise ProvisionError(f'the plan does not protect {what}, and a Windows disk always has one')


def check_plan_document(w):
    """A plan document read from a file, checked again: its digest, the planner's rules for the new entry, its layout and its extents are
    recomputed, never trusted."""
    if not isinstance(w, dict) or w.get('format') != PLAN_FORMAT or not isinstance(w.get('plan'), dict):
        raise ProvisionError('not a window plan')
    if w.get('plan_sha256') != digest_doc(w['plan']):
        raise ProvisionError('the plan does not match its own digest')
    try:
        check_plan_rules(w['plan'])
        expected = window_layout_of(w)
        extents = protected_extents(w['plan'])
    except (KeyError, TypeError, AttributeError, sw.WindowError) as e:
        raise ProvisionError(f'the plan is malformed: {e!r}') from None
    if w.get('layout') != expected or w.get('protected_extents') != extents:
        raise ProvisionError('the layout or the protected extents of the plan are not the ones its plan implies')
    return w


def window_layout_of(w):
    return sw.layout(w['plan']['new']['offset'], w['plan']['new']['bytes'], w['layout']['isc_region'])


# ---- The image against the plan -------------------------------------------------------------------------------------------------------------

def _row(q):
    return (q['type'], q['guid'], q['first_lba'], q['last_lba'], q['attributes'])


def image_state(image, w):
    """What the image holds, judged against the plan: its GPT is the plan's protected entries plus exactly the one the plan asks for. Returns
    the new entry. A refusal (ProvisionError) names the first difference."""
    p, bs = w['plan'], w['plan']['disk']['block']
    d, n = p['disk'], p['new']
    if image.size != d['bytes']:
        raise ProvisionError(f"the image is {image.size} bytes, the plan's disk {d['bytes']}")
    try:
        gpt = si.parse_gpt(image)
    except si.ImageError as e:
        raise ProvisionError(f'the image GPT: {e}') from None
    if (gpt['block'], gpt['disk_guid'], gpt['first_usable'], gpt['last_usable']) != (bs, d['gpt_guid'], d['first_usable'], d['last_usable']):
        raise ProvisionError('the image GPT is not the disk the plan was made for (block size, GUID or usable range differ)')
    have = sorted(_row(q) for q in gpt['entries'])
    want = sorted(_row(q) for q in p['protected'])
    new = [q for q in gpt['entries'] if q['type'] == n['type']]
    if len(new) != 1 or (new[0]['first_lba'], new[0]['last_lba']) != (n['first_lba'], n['last_lba']):
        raise ProvisionError('the image has not exactly one entry of the new type at the planned LBAs: the operator creates exactly one')
    if new[0]['type'] != planner().TYPE:
        raise ProvisionError("the image's new entry is not of the private Snapintosh type: the window is written only into an entry of that type")
    if new[0]['attributes'] not in planner().ALLOWED_ATTRS:
        raise ProvisionError('the new entry carries attributes the plan does not allow')
    if [r for r in have if r[0] != n['type']] != want:
        raise ProvisionError('a protected entry of the image differs from the plan: something else was changed')
    return new[0]


def extent_digests(image, w):
    return [dict(e, sha256=image.digest(e['offset'], e['bytes'])) for e in w['protected_extents']]


def _is_digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _gate_inputs(before, readers):
    """The digests of what the gate read: the BEFORE survey and every reader document. They are bound into the pre-image so that `write`
    can run the gate again on the same documents instead of believing the pre-image's own claim that it passed."""
    return dict(before_sha256=digest_doc(before), readers=[dict(label=label, sha256=digest_doc(doc)) for label, doc in readers])


def pre_image(image_path, w, before, readers, min_readers=MIN_READERS, require_eui=True):
    """The gate, the image's own GPT against the plan, and a digest of every protected extent. A dict, or ProvisionError."""
    check_plan_document(w)
    pm, verdict = planner(), {}
    try:
        problems = pm.verify_sp11(before, readers, w['plan'], min_readers, require_eui, [], verdict)
    except pm.PlanError as e:
        raise ProvisionError(f'the gate refuses: {e}') from None
    if problems or not verdict.get('readers', {}).get('four_reader_gate_met') or min_readers < MIN_READERS:
        raise ProvisionError('the four-reader gate is not met: ' + ('; '.join(problems) or f'{min_readers} readers is below the {MIN_READERS} the gate wants'))
    with si.Image(image_path) as image:
        new = image_state(image, w)
        extents = extent_digests(image, w)
    return dict(format=PREIMAGE_FORMAT, plan_sha256=w['plan_sha256'], image=dict(bytes=w['plan']['disk']['bytes'], block=w['plan']['disk']['block']),
                new=dict(guid=new['guid'], first_lba=new['first_lba'], last_lba=new['last_lba'], type=new['type'],
                         offset=w['plan']['new']['offset'], bytes=w['plan']['new']['bytes']),
                gate=dict(four_reader_gate_met=True, labels=verdict['readers']['labels'], agreeing=verdict['readers']['agreeing']),
                inputs=_gate_inputs(before, readers), extents=extents, extents_sha256=digest_doc(extents))


def check_preimage_document(pre, w):
    if not isinstance(pre, dict) or pre.get('format') != PREIMAGE_FORMAT:
        raise ProvisionError('not a pre-image')
    if pre.get('plan_sha256') != w['plan_sha256']:
        raise ProvisionError('the pre-image is of another plan')
    inputs = pre.get('inputs')
    if not isinstance(inputs, dict) or set(inputs) != {'before_sha256', 'readers'} or not _is_digest(inputs['before_sha256']) \
            or not isinstance(inputs['readers'], list) or len(inputs['readers']) < MIN_READERS \
            or any(not (isinstance(r, dict) and set(r) == {'label', 'sha256'} and _is_digest(r['sha256'])) for r in inputs['readers']):
        raise ProvisionError('the pre-image does not bind the BEFORE survey and the readers the gate read (digests of at least four readers)')
    if not isinstance(pre.get('gate'), dict) or not pre['gate'].get('four_reader_gate_met') or pre.get('extents_sha256') != digest_doc(pre.get('extents')):
        raise ProvisionError('the pre-image has no met gate, or does not match its own digest')
    try:
        covered = [dict(name=e['name'], offset=e['offset'], bytes=e['bytes']) for e in pre['extents']]
    except (KeyError, TypeError):
        raise ProvisionError('the pre-image holds a malformed extent') from None
    if covered != w['protected_extents']:
        raise ProvisionError('the pre-image does not cover the plan\'s protected extents')


def recheck_gate(w, pre, before, readers, require_eui=True):
    """The four-reader gate again, at write time, on the documents the pre-image says it was made from. The pre-image's `gate` is a claim
    written in a file, not a result: this is the check. The planner's verify also reproduces the plan from the BEFORE survey, so a plan the
    planner would not have made from a survey of this disk is refused here, whatever the pre-image says. `readers` are (label, document)."""
    pm, bound = planner(), pre['inputs']
    try:
        before_digest, reader_digests = digest_doc(before), sorted(digest_doc(doc) for _, doc in readers)
    except (TypeError, ValueError):
        raise ProvisionError('a reader or the survey cannot be digested, so the gate cannot run again') from None
    if before_digest != bound['before_sha256']:
        raise ProvisionError('the BEFORE survey is not the one the pre-image was made from')
    if reader_digests != sorted(r['sha256'] for r in bound['readers']):
        raise ProvisionError('the readers are not the ones the pre-image was made from (a document differs, is missing or is extra)')
    verdict = {}
    try:
        problems = pm.verify_sp11(before, readers, w['plan'], MIN_READERS, require_eui, [], verdict)
    except pm.PlanError as e:
        raise ProvisionError(f'the gate refuses at write time: {e}') from None
    if not verdict.get('readers', {}).get('four_reader_gate_met'):
        raise ProvisionError('the four-reader gate is not met at write time: ' + '; '.join(problems))


def _same_extents(image, pre):
    """The names of the extents whose digest is not the pre-image's, and whether the pre-image's own digest still holds."""
    return [e['name'] for e in pre['extents'] if image.digest(e['offset'], e['bytes']) != e['sha256']]


# ---- The writer ----------------------------------------------------------------------------------------------------------------------------

def _file_digest(path):
    with si.Image(path) as f:
        return f.size, f.digest(0, f.size)


def _copy_in(writer, source, offset, region_bytes=None):
    """Writes the file `source` at `offset` in chunks; returns its size. With region_bytes, zero-fills the rest of that region."""
    with si.Image(source) as f:
        done = 0
        while done < f.size:
            n = min(si.CHUNK, f.size - done)
            writer.write(offset + done, f.read(done, n))
            done += n
    while region_bytes is not None and done < region_bytes:
        n = min(si.CHUNK, region_bytes - done)
        writer.write(offset + done, bytes(n))
        done += n
    return done


def write_window(image_path, w, pre, container, isc, confirm, stamp=None, *, before, readers, require_eui=True):
    """Writes the window into the new partition of an image FILE and returns the journal. Nothing outside the partition is ever written:
    the plan's rules for the new entry and the four-reader gate are checked again first (`before` and `readers` are the documents the
    pre-image was made from), the pre-image is recomputed and a difference refuses before the first write, the writer is bounded to the
    partition, the ISC region and the container go first and are read back, and the header, whose state says 'provisioned', goes last."""
    check_plan_document(w)
    check_preimage_document(pre, w)
    p, lay = w['plan'], w['layout']
    n = p['new']
    if confirm != os.path.basename(os.fspath(image_path)):
        raise ProvisionError('missing confirmation: --confirm-image must be the image file\'s own name')
    recheck_gate(w, pre, before, readers, require_eui)
    isc_bytes, isc_sha = _file_digest(isc)
    container_bytes, container_sha = _file_digest(container)
    if isc_bytes % 4096 or container_bytes % 4096:
        raise ProvisionError('the ISC image and the container are whole 4 KiB blocks')
    with si.Image(image_path, writable=True) as image:
        new = image_state(image, w)
        if new['guid'] != pre['new']['guid']:
            raise StalePreimage('the new entry is not the one the pre-image saw')
        changed = _same_extents(image, pre)
        if changed:
            raise StalePreimage('stale pre-image: a protected extent differs from it before the first write: ' + ', '.join(changed[:5]))
        manifest = dict(format=WINDOW_MANIFEST_FORMAT, isc=dict(bytes=isc_bytes, sha256=isc_sha), container=dict(bytes=container_bytes, sha256=container_sha))
        header = sw.build_header(lay, new['guid'], isc_bytes, isc_sha, container_bytes, container_sha, digest_doc(manifest),
                                 stamp or datetime.datetime.now(datetime.timezone.utc).strftime(STAMP))
        writer = si.Writer(image, n['offset'], n['end'], [(e['offset'], e['bytes']) for e in w['protected_extents']])
        _copy_in(writer, isc, n['offset'] + lay['isc_offset'], lay['isc_region'])
        _copy_in(writer, container, n['offset'] + lay['container_offset'])
        image.sync()
        if image.digest(n['offset'] + lay['isc_offset'], isc_bytes) != isc_sha or image.digest(n['offset'] + lay['container_offset'], container_bytes) != container_sha:
            raise ProvisionError('the ISC region or the container does not read back: no header is written')
        writer.write(n['offset'] + lay['header_offset'], header)
        image.sync()
        try:
            sw.parse_header(image.read(n['offset'] + lay['header_offset'], sw.HEADER_BYTES))
        except sw.WindowError as e:
            raise ProvisionError(f'the header does not read back: {e}') from None
    return dict(format=JOURNAL_FORMAT, plan_sha256=w['plan_sha256'], preimage_sha256=digest_doc(pre), manifest=manifest,
                header_sha256=hashlib.sha256(header).hexdigest(), writes=writer.journal)


def post_check(image_path, w, pre, journal):
    """Problems found, [] meaning the protected extents are what they were, every write was inside the partition and the window reads back."""
    check_plan_document(w)
    check_preimage_document(pre, w)
    p, lay, n, bad = w['plan'], w['layout'], w['plan']['new'], []
    if not isinstance(journal, dict) or journal.get('format') != JOURNAL_FORMAT:
        return ['not a write journal']
    if journal.get('plan_sha256') != w['plan_sha256'] or journal.get('preimage_sha256') != digest_doc(pre):
        bad.append('the journal is of another plan or another pre-image')
    writes = journal.get('writes') or []
    if not writes:
        bad.append('the journal holds no write')
    for e in writes:
        if not (n['offset'] <= e['offset'] and e['offset'] + e['length'] <= n['end']):
            bad.append(f"a journaled write at {e['offset']} ({e['length']} bytes) is outside the partition")
    if writes and writes[-1]['offset'] != n['offset'] + lay['header_offset']:
        bad.append('the header is not the last write')
    with si.Image(image_path) as image:
        try:
            image_state(image, w)
        except ProvisionError as e:
            bad.append(f'the GPT: {e}')
        bad += [f'the protected extent {name} changed' for name in _same_extents(image, pre)]
        try:
            h = sw.parse_header(image.read(n['offset'] + lay['header_offset'], sw.HEADER_BYTES))
        except sw.WindowError as e:
            return bad + [f'the header: {e}']
        facts = dict(guid=pre['new']['guid'], first_lba=n['offset'] // 512, last_lba=(n['end'] // 512) - 1, state=sw.STATE_PROVISIONED,
                     isc_offset=lay['isc_offset'], isc_region=lay['isc_region'], container_offset=lay['container_offset'],
                     container_region=lay['container_region'])
        bad += [f'the header says {k} {h[k]}, the plan {v}' for k, v in facts.items() if h[k] != v]
        if image.digest(n['offset'] + lay['isc_offset'], h['isc_bytes']) != h['isc_sha256']:
            bad.append('the ISC image does not read back as its header says')
        if image.digest(n['offset'] + lay['container_offset'], h['container_bytes']) != h['container_sha256']:
            bad.append('the container does not read back as its header says')
        if h['manifest_sha256'] != digest_doc(journal.get('manifest')):
            bad.append('the header\'s manifest digest is not the journal\'s')
    return bad


def verify_undo(before, readers, w, journal, undone, min_readers=MIN_READERS, require_eui=True):
    """The planner's verify-undo of the one entry, and: every journaled write lies in the free space of the undone survey."""
    check_plan_document(w)
    pm, verdict, bad = planner(), {}, []
    try:
        bad += pm.verify_undo_sp11(before, readers, w['plan'], min_readers, require_eui, [], verdict)
        f = pm.sp11_disk(pm.select_disk(undone, w['plan']['disk']['gpt_guid'], w['plan']['disk']['eui']))
    except pm.PlanError as e:
        raise ProvisionError(f'refused: {e}') from None
    free = [(lo * f['bs'], (hi + 1) * f['bs']) for lo, hi in f['free']]
    for e in journal.get('writes', []):
        if not any(lo <= e['offset'] and e['offset'] + e['length'] <= hi for lo, hi in free):
            bad.append(f"a journaled write at {e['offset']} is not inside the free space after the undo")
    return bad


# ---- The authorised device mode: designed, not enabled -----------------------------------------------------------------------------------

def auth_template(w, pre, run_id, now, valid_seconds=AUTH_MAX_SECONDS):
    """An authorisation record with every acknowledgement False: the operator fills it in for ONE run; nothing here does."""
    d, n = w['plan']['disk'], w['plan']['new']
    return dict(format=AUTH_FORMAT, mode='device', run_id=run_id, issued_utc=now.strftime(STAMP),
                expires_utc=(now + datetime.timedelta(seconds=valid_seconds)).strftime(STAMP),
                disk=dict(bytes=d['bytes'], gpt_guid=d['gpt_guid'], eui=d['eui']), plan_sha256=w['plan_sha256'], preimage_sha256=digest_doc(pre),
                partition=dict(offset=n['offset'], bytes=n['bytes']), acknowledged={k: False for k in ACKNOWLEDGEMENTS},
                tool_source_sha256=tool_source_sha256())


def check_auth(record, w, pre, now):
    """Problems with an authorisation record, [] meaning it authorises ONE run of THIS plan and THIS pre-image on THIS tool source now."""
    if not isinstance(record, dict):
        return ['not a record']
    bad = [f'unknown or missing key {k}' for k in sorted(set(record) ^ set(AUTH_KEYS))]
    if bad:
        return bad
    d, n = w['plan']['disk'], w['plan']['new']
    if record['format'] != AUTH_FORMAT or record['mode'] != 'device':
        bad.append('not a device-mode record of this format')
    if not isinstance(record['run_id'], str) or not re.fullmatch(r'[A-Za-z0-9._-]{8,64}', record['run_id']):
        bad.append('the run id is 8 to 64 characters of [A-Za-z0-9._-]')
    try:
        issued = datetime.datetime.strptime(record['issued_utc'], STAMP).replace(tzinfo=datetime.timezone.utc)
        expires = datetime.datetime.strptime(record['expires_utc'], STAMP).replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return bad + ['the times are not YYYY-MM-DDTHH:MM:SSZ']
    if not issued <= now < expires:
        bad.append('the record is not valid now (not yet issued, or expired)')
    if expires - issued > datetime.timedelta(seconds=AUTH_MAX_SECONDS):
        bad.append(f'the record is valid for more than {AUTH_MAX_SECONDS // 60} minutes')
    if record['plan_sha256'] != w['plan_sha256']:
        bad.append('the record is of another plan')
    if record['preimage_sha256'] != digest_doc(pre):
        bad.append('the record is of another pre-image')
    if record['disk'] != dict(bytes=d['bytes'], gpt_guid=d['gpt_guid'], eui=d['eui']) or record['partition'] != dict(offset=n['offset'], bytes=n['bytes']):
        bad.append('the record names another disk or another partition')
    ack = record['acknowledged']
    if not isinstance(ack, dict) or set(ack) != set(ACKNOWLEDGEMENTS) or any(v is not True for v in ack.values()):
        bad.append('every acknowledgement must be present and true: ' + ', '.join(ACKNOWLEDGEMENTS))
    if record['tool_source_sha256'] != tool_source_sha256():
        bad.append('the record was made for another version of these tools')
    return bad


def open_device_for_write(path, record, w, pre, now):
    """The device mode. It validates the record and then REFUSES, always: it is designed here and not enabled, and no code in these tools
    opens a device node. `path` is never opened, not even stat-ed."""
    problems = check_auth(record, w, pre, now)
    if problems:
        raise DeviceModeRefused('no valid authorisation: ' + '; '.join(problems))
    if not DEVICE_MODE_ENABLED:
        raise DeviceModeRefused('the authorised device mode is designed and not enabled')
    raise DeviceModeRefused('the authorised device mode is not implemented: no code in these tools opens a device node')


# ---- Command line --------------------------------------------------------------------------------------------------------------------------

def _readers(paths):
    pm, out, used = planner(), [], set()
    for path in paths:
        base = re.sub(r'[^A-Za-z0-9._-]', '-', Path(path).stem)[:40] or 'reader'
        label, k = base, 1
        while label in used:
            k += 1
            label = f'{base}-{k}'
        used.add(label)
        try:
            out.append((label, si.read_json(path)))
        except si.ImageError as e:
            out.append((label, pm.PlanError(str(e))))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('plan')
    s.add_argument('--survey', required=True)
    s.add_argument('--out', required=True)
    s.add_argument('--min-bytes', type=int)
    s.add_argument('--isc-region', type=int, default=sw.DEFAULT_ISC_REGION)
    s.add_argument('--extent-start', type=int)
    s.add_argument('--extent-end', type=int)
    s.add_argument('--no-eui', action='store_true')
    s = sub.add_parser('pre-image')
    s.add_argument('--image', required=True)
    s.add_argument('--plan', required=True)
    s.add_argument('--before', required=True)
    s.add_argument('--after', action='append', required=True)
    s.add_argument('--min-readers', type=int, default=MIN_READERS)
    s.add_argument('--no-eui', action='store_true')
    s.add_argument('--out', required=True)
    s = sub.add_parser('write')
    s.add_argument('--image', required=True)
    s.add_argument('--plan', required=True)
    s.add_argument('--pre-image', required=True)
    s.add_argument('--before', required=True)
    s.add_argument('--after', action='append', required=True)
    s.add_argument('--no-eui', action='store_true')
    s.add_argument('--container', required=True)
    s.add_argument('--isc', required=True)
    s.add_argument('--confirm-image', default='')
    s.add_argument('--stamp')
    s.add_argument('--out', required=True)
    s = sub.add_parser('post-check')
    s.add_argument('--image', required=True)
    s.add_argument('--plan', required=True)
    s.add_argument('--pre-image', required=True)
    s.add_argument('--journal', required=True)
    s = sub.add_parser('verify-undo')
    s.add_argument('--before', required=True)
    s.add_argument('--after', action='append', required=True)
    s.add_argument('--plan', required=True)
    s.add_argument('--journal', required=True)
    s.add_argument('--undone-survey', required=True)
    s.add_argument('--min-readers', type=int, default=MIN_READERS)
    s.add_argument('--no-eui', action='store_true')
    s = sub.add_parser('auth-check')
    s.add_argument('--record', required=True)
    s.add_argument('--plan', required=True)
    s.add_argument('--pre-image', required=True)
    s.add_argument('--device')
    args = ap.parse_args(argv)
    try:
        if args.cmd in ('plan', 'pre-image', 'write'):
            si.check_output_path(args.out)                  # before any work: a refusal after a write would leave the write unrecorded
        if args.cmd == 'plan':
            if (args.extent_start is None) != (args.extent_end is None):
                ap.error('--extent-start and --extent-end go together')
            pm = planner()
            extent = None if args.extent_start is None else (args.extent_start, args.extent_end)
            doc = window_plan(si.read_json(args.survey), args.isc_region, extent=extent, min_bytes=pm.SP11_MIN_BYTES if args.min_bytes is None else args.min_bytes,
                              require_eui=not args.no_eui)
            si.write_text_file(args.out, json.dumps(doc, indent=1) + '\n')
            print('\n'.join(pm.plan_text(doc['plan'])))
            return 0
        w = check_plan_document(si.read_json(args.plan)) if hasattr(args, 'plan') else None
        if args.cmd == 'pre-image':
            pre = pre_image(args.image, w, si.read_json(args.before), _readers(args.after), args.min_readers, not args.no_eui)
            si.write_text_file(args.out, json.dumps(pre, indent=1) + '\n')
            print(f"pre-image: {len(pre['extents'])} protected extents digested, gate met by {pre['gate']['labels']}")
            return 0
        if args.cmd == 'write':
            journal = write_window(args.image, w, si.read_json(args.pre_image), args.container, args.isc, args.confirm_image, args.stamp,
                                   before=si.read_json(args.before), readers=_readers(args.after), require_eui=not args.no_eui)
            text = json.dumps(journal, indent=1) + '\n'
            try:
                si.write_text_file(args.out, text)
            except (si.ImageError, OSError) as e:          # the image is already written: the journal must not be lost
                print(f'FAIL: the window was written but the journal could not be saved ({e}); it follows:\n{text}')
                return 1
            print(f"wrote {len(journal['writes'])} extents inside the partition; header last")
            return 0
        if args.cmd == 'post-check':
            problems = post_check(args.image, w, si.read_json(args.pre_image), si.read_json(args.journal))
        elif args.cmd == 'verify-undo':
            problems = verify_undo(si.read_json(args.before), _readers(args.after), w, si.read_json(args.journal), si.read_json(args.undone_survey),
                                   args.min_readers, not args.no_eui)
        else:
            if args.device:
                try:
                    open_device_for_write(args.device, si.read_json(args.record), w, si.read_json(args.pre_image), datetime.datetime.now(datetime.timezone.utc))
                except DeviceModeRefused as e:
                    print(f'REFUSED: {e}', file=sys.stderr)
                    return 2
            problems = check_auth(si.read_json(args.record), w, si.read_json(args.pre_image), datetime.datetime.now(datetime.timezone.utc))
        for q in problems:
            print('FAIL:', q)
        print('PASS' if not problems else f'{len(problems)} problem(s)')
        return 1 if problems else 0
    except (ProvisionError, si.ImageError, sw.WindowError, planner().PlanError) as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    except Exception as e:                  # a crash must not look like a failed check
        print(f'REFUSED: unexpected {type(e).__name__}: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
