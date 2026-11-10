# SPDX-License-Identifier: MIT
"""Host-side companion of compat/hamoa-thermal.h (Surface Pro 11 read-only thermal guard).

Decodes the guard's report, refuses to admit a target until the SP11 platform fingerprint is measured, and
checks the operator's power attestation. Everything here is pure: no target, serial, USB or block I/O.
Evidence status: EXPERIMENTAL; the report layout is verified against the C structs by
tools/test-hamoa-thermal-guard.py, but nothing has run on an SP11 and every register fact behind it is
INFERRED from public Linux sources until gate X2.
"""
import datetime
import struct

MAGIC = 0x314d524548544d48  # "HMTHERM1" in memory order
ABI = 1
WORDS = 298
SIZE = WORDS * 8
# UNIMPLEMENTED: the monitor has not reserved a report page for the SP11 yet. The A16 report offset
# belongs to a different image layout and must not be reused.
REPORT_OFFSET = None
INT64_MIN = -(1 << 63)
FIELDS = ('magic abi bytes polls reads started last_poll next_poll last_change trip_ticks reason location '
          'current_mc peak_mc valid_count missing_count max_gap_ticks invalid_samples read_failures '
          'poll_ticks max_poll_ticks ready unchanged_ticks read_inflight limit_mc hw_version slot_count '
          'valid_slots seen_slots detail').split()
ARRAYS = (('version', 4), ('control', 4), ('trdy', 4), ('slot', 64), ('raw', 64), ('conversion', 64),
          ('last_valid', 64))
SIGNED = ('current_mc', 'peak_mc', 'limit_mc')
REASONS = {0: 'okay', 1: 'temperature ceiling', 2: 'sensor configuration', 3: 'sensor invalid for one second',
           4: 'sample gap of one second', 5: 'sensor read failure', 6: 'temperature outside plausible range',
           7: 'hardware critical status', 8: 'all temperatures unchanged for ten seconds',
           9: 'sample consumed a polling interval', 10: 'invalid counter frequency',
           11: 'no temperature source configured', 12: 'sensor not valid while controller not ready',
           13: 'sensor conversion not programmed'}
DETAILS = {0: 'none', 1: 'no configuration', 2: 'state never initialized', 3: 'report header changed',
           4: 'read or counter callback missing', 5: 'no early-stop state', 6: 'ceiling outside accepted window',
           7: 'empty sensor list', 8: 'too many sensors', 9: 'sensor instance or id out of range',
           10: 'duplicate sensor', 11: 'hardware version not measured', 12: 'hardware version mismatch',
           13: 'controller not enabled in temperature mode'}
assert len(FIELDS) + sum(count for _, count in ARRAYS) == WORDS

# Filled from a measured SP11 run at gate X2 (EL, counter frequency and interrupt-controller bases);
# nothing in the public sources discriminates the SP11 from the A16 on these, so none is guessed here.
FINGERPRINT = None

MAX_ATTESTATION_AGE_S = 30 * 60
MIN_BATTERY_PERCENT = 50


def admit_target(info):
    """Raises ValueError unless info matches the measured SP11 fingerprint. While no fingerprint is
    measured it refuses every target, whatever info is."""
    if not FINGERPRINT:
        raise ValueError('SP11 fingerprint not yet measured (X2): the thermal guard refuses every target')
    if not isinstance(info, dict):
        raise ValueError('target information must be a mapping')
    if info.get('current_el') != 2 or any(info.get(key) != value for key, value in FINGERPRINT.items()):
        raise ValueError('thermal guard requires the measured SP11 EL2 platform')


def decode(raw):
    """Report fields as a dict, None for an all-zero (guard never armed) report; ValueError otherwise."""
    if len(raw) != SIZE:
        raise ValueError(f'thermal report requires {SIZE} bytes')
    words = struct.unpack(f'<{WORDS}Q', bytes(raw))
    if not any(words):
        return None
    if words[:3] != (MAGIC, ABI, SIZE):
        raise ValueError('unrecognized thermal report ABI')
    report = dict(zip(FIELDS, words))
    cursor = len(FIELDS)
    for key, count in ARRAYS:
        report[key] = list(words[cursor:cursor + count])
        cursor += count
    for key in SIGNED:
        if report[key] >> 63:
            report[key] -= 1 << 64
    for key in ('current_mc', 'peak_mc'):
        if report[key] == INT64_MIN:
            report[key] = None
    report['reason_name'] = REASONS.get(report['reason'], f'unknown reason {report["reason"]}')
    report['detail_name'] = DETAILS.get(report['detail'], f'unknown detail {report["detail"]}')
    return report


def slot_temperature_mc(report, index):
    """Millidegrees C of slot index from its latest status word, or None unless that word had VALID set."""
    word = report['raw'][index]
    if not (word >> 21) & 1:
        return None
    code = word & 0xfff
    return (code - 0x1000 if code & 0x800 else code) * 100


def summary(report, hz):
    if hz <= 0:
        raise ValueError('counter frequency must be positive')
    if report is None:
        return 'thermal guard disabled'
    peak = 'none' if report['peak_mc'] is None else f'{report["peak_mc"] / 1000:.1f} C'
    return (f'thermal guard: {report["reason_name"]}, {report["valid_count"]}/{report["slot_count"]} valid, '
            f'peak {peak}, ceiling {report["limit_mc"] / 1000:.1f} C, {report["polls"]} samples, '
            f'max sample {report["max_poll_ticks"] / hz * 1000:.3f} ms, '
            f'max gap {report["max_gap_ticks"] / hz * 1000:.3f} ms')


def _instant(value):
    """An aware UTC datetime from an aware datetime, an epoch number or an RFC 3339 string; None for
    anything else. A naive time is refused: its zone, and so its age, is unknown."""
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if text[-1:] in ('Z', 'z'):
            text = text[:-1] + '+00:00'
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    try:
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(datetime.timezone.utc)
    except Exception:  # an offset can push the instant out of range, and a tzinfo may raise anything
        return None


def power_attestation_ok(record, now, run_prefix):
    """True only for a fresh operator attestation of AC power, or of at least 50% battery, made for this
    run. recorded_utc must be an RFC 3339 string with an offset; now may be an aware datetime, an epoch
    number or such a string. Any malformed, stale, future-dated or mismatched input returns False; it never
    raises. The attestation replaces a power reading the monitor and host tools cannot take on the SP11
    today."""
    if not isinstance(record, dict) or not isinstance(run_prefix, str) or not run_prefix:
        return False
    if record.get('run_prefix') != run_prefix:
        return False
    on_ac = record.get('ac_connected')
    if not isinstance(on_ac, bool):
        return False
    percent = record.get('battery_percent')
    if percent is not None and (isinstance(percent, bool) or not isinstance(percent, int)
                                or not 0 <= percent <= 100):
        return False
    if not on_ac and (percent is None or percent < MIN_BATTERY_PERCENT):
        return False
    stamp = record.get('recorded_utc')
    recorded = _instant(stamp) if isinstance(stamp, str) else None
    current = _instant(now)
    if recorded is None or current is None:
        return False
    age = (current - recorded).total_seconds()
    return 0 <= age <= MAX_ATTESTATION_AGE_S
