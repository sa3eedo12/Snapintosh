# SPDX-License-Identifier: MIT
"""Host tests for the Surface Pro 11 thermal guard (compat/hamoa-thermal.c, tools/hamoa_thermal.py).

A pass proves three things: the guard's policy holds against a faked register bus (the C model is built
with ASan and UBSan), the Python decoder reads the report layout the C structs define, and the admission
and power-attestation helpers refuse what they must. It proves nothing about the SP11 itself: every
register fact behind the guard is INFERRED from public Linux sources until gate X2 measures it. Nothing
here touches a device.
"""
import ast
import datetime
import hashlib
import importlib.util
import itertools
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build' / 'hamoa-thermal-guard'
SOURCES = ('compat/hamoa-thermal.c', 'compat/hamoa-thermal.h', 'tools/hamoa_thermal.py',
           'tools/test-hamoa-thermal-guard.c')
# The model includes the unit's source, so a static function such as read_word is tested directly.
BUILD = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
         '-fno-sanitize-recover=all', '-Icompat', '-Ishadow', 'tools/test-hamoa-thermal-guard.c',
         'compat/early-stop.c', 'compat/service-lock.c', '-o', 'build/hamoa-thermal-guard/guard-test']
MASK64 = (1 << 64) - 1
VALID = 1 << 21
# Control word the model presents (INFERRED from the public tsens-v2 driver): enabled in bit 0, sixteen
# sensors enabled in bits 3-18, temperature rather than raw-code output in bit 21.
HEALTHY_CONTROL = 1 | (0xffff << 3) | (1 << 21)
# Sensors the public DT thermal zones read, instance-major: 13, 11, 11 and 15 ids counted from zero.
DEFAULT_SENSORS = ([(0, n) for n in range(13)] + [(1, n) for n in range(11)] + [(2, n) for n in range(11)]
                   + [(3, n) for n in range(15)])
REASON_WORDS = {'OK': 'okay', 'HOT': 'ceiling', 'CONFIG': 'configuration', 'INVALID': 'invalid',
                'LATE': 'gap', 'READ': 'read failure', 'RANGE': 'range', 'CRITICAL': 'critical',
                'UNCHANGED': 'unchanged', 'SLOW': 'polling interval', 'CLOCK': 'frequency',
                'NO_SOURCE': 'no temperature source', 'NOT_READY': 'not ready',
                'UNCALIBRATED': 'not programmed'}
DETAIL_WORDS = {'NONE': 'none', 'NO_CONFIG': 'no configuration', 'NO_STATE': 'never initialized',
                'BAD_HEADER': 'header', 'NO_IO': 'callback', 'NO_STOP': 'early-stop', 'LIMIT': 'ceiling',
                'NO_SENSORS': 'empty sensor list', 'TOO_MANY': 'too many', 'BAD_SENSOR': 'out of range',
                'DUPLICATE': 'duplicate', 'NO_VERSION': 'not measured', 'VERSION': 'mismatch',
                'CONTROL': 'temperature mode'}


def load_module():
    spec = importlib.util.spec_from_file_location('hamoa_thermal', ROOT / 'tools' / 'hamoa_thermal.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args, timeout=120):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def code_only(path):
    text = (ROOT / path).read_text()
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'//[^\n]*', ' ', text)


class HostModel(unittest.TestCase):
    """The C model, its layout and the six report snapshots it writes."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        shutil.rmtree(OUT, ignore_errors=True)
        (OUT / 'dump').mkdir(parents=True)
        # A missing clang raises FileNotFoundError here on purpose: the suite then reports SKIP, not FAIL.
        built = run(BUILD, timeout=300)
        (OUT / 'build.log').write_text(built.stdout + built.stderr)
        if built.returncode:
            raise AssertionError('thermal guard host model failed to build:\n' + built.stdout + built.stderr)
        binary = str(OUT / 'guard-test')
        cls.plain = run([binary])
        cls.layout = run([binary, '--layout'])
        cls.dump = run([binary, '--dump', str(OUT / 'dump')])
        cls.fields, cls.reasons, cls.details, cls.consts = {}, {}, {}, {}
        tables = {'field': cls.fields, 'reason': cls.reasons, 'detail': cls.details, 'const': cls.consts}
        for line in cls.layout.stdout.splitlines():
            kind, name, value = line.split()
            tables[kind][name] = int(value, 0)

    def report(self, name):
        return self.mod.decode((OUT / 'dump' / f'{name}.bin').read_bytes())

    def test_model_passes_under_sanitizers(self):
        self.assertEqual(self.plain.returncode, 0, self.plain.stdout + self.plain.stderr)
        for noise in ('runtime error', 'AddressSanitizer', 'LeakSanitizer'):
            self.assertNotIn(noise, self.plain.stderr)
        lines = self.plain.stdout.splitlines()
        self.assertTrue(lines[-1].startswith('PASS: SP11 thermal guard host model'), lines[-1])
        verdicts = [json.loads(line) for line in lines[:-1]]
        self.assertGreaterEqual(len(verdicts), 50)
        seen = set()
        for verdict in verdicts:
            self.assertIn(verdict['reason'], self.mod.REASONS)
            if 'detail' in verdict:
                self.assertIn(verdict['detail'], self.mod.DETAILS)
            seen.add(verdict['reason'])
        # Every cause the decoder can name is produced by at least one modelled scenario.
        self.assertEqual(seen, set(self.mod.REASONS))

    def test_layout_agrees_with_the_decoder(self):
        mod = self.mod
        self.assertEqual(self.layout.returncode, 0, self.layout.stderr)
        expected = {name: index for index, name in enumerate(mod.FIELDS)}
        cursor = len(mod.FIELDS)
        for name, count in mod.ARRAYS:
            expected[name] = cursor
            cursor += count
        self.assertEqual(cursor, mod.WORDS)
        self.assertEqual(self.fields, expected)
        self.assertEqual(self.consts['size'], mod.SIZE)
        self.assertEqual(self.consts['magic'], mod.MAGIC)
        self.assertEqual(self.consts['abi'], mod.ABI)
        self.assertEqual(self.consts['slots'], 64)
        self.assertLessEqual(mod.SIZE, 4096)
        self.assertEqual(sorted(self.reasons.values()), list(range(len(self.reasons))))
        self.assertEqual(set(self.reasons.values()), set(mod.REASONS))
        self.assertEqual(set(self.details.values()), set(mod.DETAILS))
        self.assertEqual(set(self.reasons), set(REASON_WORDS))
        self.assertEqual(set(self.details), set(DETAIL_WORDS))
        for name, number in self.reasons.items():
            self.assertIn(REASON_WORDS[name], mod.REASONS[number], name)
        for name, number in self.details.items():
            self.assertIn(DETAIL_WORDS[name], mod.DETAILS[number], name)

    def test_unsampled_snapshot(self):
        self.assertEqual(self.dump.returncode, 0, self.dump.stderr)
        report = self.report('unsampled')
        self.assertEqual((report['polls'], report['reads'], report['reason'], report['detail']), (0, 0, 0, 0))
        self.assertIsNone(report['current_mc'])
        self.assertIsNone(report['peak_mc'])
        self.assertEqual(report['started'], 1000)
        self.assertEqual(report['limit_mc'], self.consts['ceiling_mc'])
        self.assertEqual(report['hw_version'], self.consts['test_version'])
        self.assertEqual((report['slot_count'], report['valid_slots'], report['ready']), (50, 0, 0))
        self.assertEqual(report['raw'], [0] * 64)
        self.assertEqual(report['version'], [0] * 4)
        self.assertTrue(all(self.mod.slot_temperature_mc(report, k) is None for k in range(64)))

    def test_healthy_snapshot(self):
        mod, hz = self.mod, self.consts['hz']
        report = self.report('healthy')
        self.assertEqual((report['reason'], report['detail']), (0, 0))
        self.assertEqual((report['polls'], report['reads']), (1, 112))  # 4 x 3 controller + 50 x 2 sensor
        self.assertEqual((report['started'], report['last_poll'], report['last_change']), (1000, 1000, 1000))
        self.assertEqual(report['next_poll'], 1000 + hz // 4)
        self.assertEqual(report['location'], 0x0c2740d8)  # the last read: instance 3, sensor 14 status
        self.assertEqual((report['current_mc'], report['peak_mc']), (34400, 34400))
        self.assertEqual((report['valid_count'], report['missing_count'], report['ready']), (50, 0, 1))
        self.assertEqual((report['invalid_samples'], report['read_failures'], report['read_inflight']),
                         (0, 0, 0))
        self.assertEqual((report['valid_slots'], report['seen_slots']), ((1 << 50) - 1, (1 << 50) - 1))
        self.assertEqual(report['version'], [self.consts['test_version']] * 4)
        self.assertEqual(report['control'], [HEALTHY_CONTROL] * 4)
        self.assertEqual(report['trdy'], [1] * 4)
        self.assertEqual(report['slot'][:50], [(i << 8) | n for i, n in DEFAULT_SENSORS])
        self.assertEqual(report['slot'][50:], [0] * 14)
        for index, (instance, sensor) in enumerate(DEFAULT_SENSORS):
            self.assertEqual(mod.slot_temperature_mc(report, index), (300 + 10 * instance + sensor) * 100)
        self.assertTrue(all(value != 0 for value in report['conversion'][:50]))
        self.assertEqual(report['conversion'][50:], [0] * 14)
        self.assertEqual(report['last_valid'][:50], [1000] * 50)
        self.assertIn('50/50 valid', mod.summary(report, hz))
        self.assertIn('peak 34.4 C', mod.summary(report, hz))
        self.assertIn('ceiling 80.0 C', mod.summary(report, hz))

    def test_hot_snapshot(self):
        mod, hz = self.mod, self.consts['hz']
        report = self.report('hot')
        self.assertEqual((report['reason'], report['detail']), (1, 0))
        self.assertEqual(report['reason_name'], mod.REASONS[1])
        self.assertEqual(report['location'], 0x0c2730bc)  # instance 2, sensor 7 status
        self.assertEqual(report['slot'][31], (2 << 8) | 7)
        self.assertEqual((report['current_mc'], report['peak_mc']), (80000, 80000))
        self.assertEqual(mod.slot_temperature_mc(report, 31), 80000)
        self.assertEqual((report['polls'], report['reads']), (2, 185))  # no read after the tripping sensor
        self.assertEqual(report['trip_ticks'], hz // 4)
        self.assertEqual(report['max_gap_ticks'], hz // 4)
        self.assertEqual((report['valid_count'], report['ready']), (32, 0))
        self.assertEqual(report['valid_slots'], (1 << 32) - 1)
        self.assertEqual(report['seen_slots'], (1 << 50) - 1)
        self.assertEqual(report['last_valid'][:32], [hz // 4] * 32)
        self.assertEqual(report['last_valid'][32:50], [0] * 18)

    def test_negative_snapshot(self):
        mod = self.mod
        report = self.report('negative')
        self.assertEqual((report['current_mc'], report['peak_mc']), (-1000, -1000))
        self.assertEqual((report['slot_count'], report['valid_slots'], report['seen_slots']), (1, 1, 1))
        self.assertEqual(report['slot'][0], (2 << 8) | 5)
        self.assertEqual(report['raw'][0], VALID | 0xff6)
        self.assertEqual(mod.slot_temperature_mc(report, 0), -1000)
        self.assertEqual((report['polls'], report['reads'], report['reason'], report['ready']), (1, 5, 0, 1))
        self.assertEqual(report['version'], [0, 0, self.consts['test_version'], 0])
        self.assertEqual(report['control'], [0, 0, HEALTHY_CONTROL, 0])
        self.assertNotEqual(report['conversion'][0], 0)

    def test_no_source_snapshot(self):
        report = self.report('nosource')
        self.assertEqual((report['reason'], report['detail']), (11, 7))
        self.assertEqual(report['reason_name'], self.mod.REASONS[11])
        self.assertEqual(report['detail_name'], self.mod.DETAILS[7])
        self.assertEqual((report['polls'], report['reads'], report['slot_count']), (0, 0, 0))
        self.assertIsNone(report['current_mc'])

    def test_zero_snapshot_is_a_disabled_guard(self):
        self.assertIsNone(self.report('zero'))


class Decoder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def words(self, **fields):
        words = [0] * self.mod.WORDS
        words[:3] = [self.mod.MAGIC, self.mod.ABI, self.mod.SIZE]
        for name, value in fields.items():
            words[self.mod.FIELDS.index(name)] = value & MASK64
        return words

    @staticmethod
    def blob(words):
        return struct.pack(f'<{len(words)}Q', *words)

    def test_zero_report_means_disabled(self):
        self.assertIsNone(self.mod.decode(bytes(self.mod.SIZE)))
        self.assertIsNone(self.mod.decode(bytearray(self.mod.SIZE)))
        self.assertEqual(self.mod.summary(None, 24_000_000), 'thermal guard disabled')

    def test_wrong_length_is_refused(self):
        size = self.mod.SIZE
        for length in (0, 8, size - 8, size - 1, size + 1, size + 8, 2 * size):
            with self.subTest(length=length), self.assertRaises(ValueError):
                self.mod.decode(bytes(length))

    def test_header_is_enforced(self):
        mod = self.mod
        good = self.words()
        self.assertIsNotNone(mod.decode(self.blob(good)))
        for index, wrong in ((0, mod.MAGIC ^ 1), (0, 0), (1, mod.ABI + 1), (1, 0), (2, mod.SIZE - 8),
                             (2, mod.SIZE + 8), (2, 0)):
            words = list(good)
            words[index] = wrong
            with self.subTest(word=index, value=wrong), self.assertRaises(ValueError):
                mod.decode(self.blob(words))
        words = [0] * mod.WORDS
        words[5] = 1  # not an all-zero report, so the zero header cannot pass as a disabled guard
        with self.assertRaises(ValueError):
            mod.decode(self.blob(words))

    def test_every_field_round_trips(self):
        mod = self.mod
        values = {name: 1000 + index for index, name in enumerate(mod.FIELDS) if index >= 3}
        values.update(reason=7, detail=13, current_mc=-12345, peak_mc=1 << 63, limit_mc=-1)
        words = self.words(**values)
        cursor = len(mod.FIELDS)
        arrays = {}
        for number, (name, count) in enumerate(mod.ARRAYS, 1):
            arrays[name] = [number * 0x10000 + k for k in range(count)]
            words[cursor:cursor + count] = arrays[name]
            cursor += count
        report = mod.decode(self.blob(words))
        self.assertEqual((report['magic'], report['abi'], report['bytes']), (mod.MAGIC, mod.ABI, mod.SIZE))
        for name, value in values.items():
            if name not in mod.SIGNED:
                self.assertEqual(report[name], value, name)
        self.assertEqual(report['current_mc'], -12345)
        self.assertIsNone(report['peak_mc'])  # INT64_MIN is the "never sampled" marker
        self.assertEqual(report['limit_mc'], -1)
        for name, expected in arrays.items():
            self.assertEqual(report[name], expected, name)
        self.assertEqual(report['reason_name'], mod.REASONS[7])
        self.assertEqual(report['detail_name'], mod.DETAILS[13])

    def test_signed_fields_at_their_limits(self):
        for value in ((1 << 63) - 1, -((1 << 63) - 1), -1, 0):
            report = self.mod.decode(self.blob(self.words(current_mc=value, peak_mc=value, limit_mc=value)))
            self.assertEqual((report['current_mc'], report['peak_mc'], report['limit_mc']), (value,) * 3)

    def test_unknown_codes_are_named_not_hidden(self):
        report = self.mod.decode(self.blob(self.words(reason=99, detail=77)))
        self.assertEqual(report['reason_name'], 'unknown reason 99')
        self.assertEqual(report['detail_name'], 'unknown detail 77')

    def test_slot_temperature(self):
        mod = self.mod
        cases = [(VALID, 0), (VALID | 0x1f4, 50000), (VALID | 0x7ff, 204700), (VALID | 0x800, -204800),
                 (VALID | 0xfff, -100), (VALID | 0xffe, -200), (VALID | (1 << 19) | 0x1f4, 50000),
                 (0x1f4, None), ((1 << 20) | 0x1f4, None), ((1 << 22) | 0x1f4, None), (0, None)]
        for word, expected in cases:
            with self.subTest(word=hex(word)):
                self.assertEqual(mod.slot_temperature_mc({'raw': [word]}, 0), expected)

    def test_summary(self):
        mod = self.mod
        report = mod.decode(self.blob(self.words(limit_mc=80000, slot_count=50, valid_count=50,
                                                 current_mc=34400, peak_mc=34400, polls=3)))
        text = mod.summary(report, 24_000_000)
        for part in ('okay', '50/50 valid', 'peak 34.4 C', 'ceiling 80.0 C', '3 samples'):
            self.assertIn(part, text)
        report['peak_mc'] = None
        self.assertIn('peak none', mod.summary(report, 24_000_000))
        for hz in (0, -1):
            with self.assertRaises(ValueError):
                mod.summary(report, hz)
            with self.assertRaises(ValueError):
                mod.summary(None, hz)


class AdmitTarget(unittest.TestCase):
    TEST_FINGERPRINT = {'cntfrq': 24_000_000, 'gic_base': 0x1000}  # arbitrary test values, not SP11 facts

    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_unmeasured_platform_refuses_every_target(self):
        self.assertIsNone(self.mod.FINGERPRINT)
        matching = dict(self.TEST_FINGERPRINT, current_el=2)
        for info in (matching, {}, {'current_el': 2}, {'current_el': 1}, None, 'target', 3, [], (2,)):
            with self.subTest(info=info), self.assertRaises(ValueError):
                self.mod.admit_target(info)

    def test_measured_fingerprint_admits_only_a_match(self):
        match = dict(self.TEST_FINGERPRINT, current_el=2)
        with mock.patch.object(self.mod, 'FINGERPRINT', dict(self.TEST_FINGERPRINT)):
            self.assertIsNone(self.mod.admit_target(match))
            self.assertIsNone(self.mod.admit_target(dict(match, extra='ignored')))
            refusals = [dict(match, cntfrq=19_200_000), dict(match, gic_base=0x2000), dict(match, current_el=1),
                        dict(match, current_el=3), dict(match, current_el=None), dict(match, current_el='2')]
            for key in match:
                refusals.append({k: v for k, v in match.items() if k != key})
            refusals += [None, 'target', 3, [], (2,)]
            for info in refusals:
                with self.subTest(info=info), self.assertRaises(ValueError):
                    self.mod.admit_target(info)
        self.assertIsNone(self.mod.FINGERPRINT)

    def test_a_falsy_fingerprint_does_not_open_the_gate(self):
        for fingerprint in (None, {}, False, 0, ''):
            with mock.patch.object(self.mod, 'FINGERPRINT', fingerprint):
                with self.subTest(fingerprint=fingerprint), self.assertRaises(ValueError):
                    self.mod.admit_target({'current_el': 2})


UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
PREFIX = 'sp11-run-7'


def stamp(age_seconds, when=NOW):
    return (when - datetime.timedelta(seconds=age_seconds)).isoformat().replace('+00:00', 'Z')


def record(**changes):
    value = {'ac_connected': True, 'battery_percent': None, 'recorded_utc': stamp(0), 'run_prefix': PREFIX}
    value.update(changes)
    return value


class local_zone:
    """Pins the process time zone (POSIX TZ syntax), so a test can tell a refusal of naive times from one
    that only happens because the host's own zone differs from UTC."""

    def __init__(self, posix_name):
        self.name = posix_name

    def __enter__(self):
        self.saved = os.environ.get('TZ')
        os.environ['TZ'] = self.name
        time.tzset()

    def __exit__(self, *_):
        if self.saved is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = self.saved
        time.tzset()


class PowerAttestation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def ok(self, rec, now=NOW, prefix=PREFIX):
        result = self.mod.power_attestation_ok(rec, now, prefix)
        self.assertIsInstance(result, bool)
        return result

    def test_good_records(self):
        self.assertTrue(self.ok(record()))  # the base every refusal below departs from in one respect
        cases = [record(battery_percent=0), record(battery_percent=100),
                 record(ac_connected=False, battery_percent=50), record(ac_connected=False, battery_percent=100),
                 record(recorded_utc=stamp(1800)), record(recorded_utc=stamp(1)),
                 record(recorded_utc='2026-10-20T11:59:59.5Z'), record(recorded_utc='2026-10-20T12:00:00z'),
                 record(recorded_utc='2026-10-20T14:00:00+02:00'), record(recorded_utc='2026-10-20T13:30:00+02:00'),
                 record(recorded_utc='2026-10-20T06:00:00-06:00')]
        for rec in cases:
            with self.subTest(rec=rec):
                self.assertTrue(self.ok(rec))

    def test_the_clock_argument_may_be_a_datetime_an_epoch_number_or_a_string(self):
        rec = record()
        for now in (NOW, NOW.timestamp(), int(NOW.timestamp()), '2026-10-20T12:00:00Z',
                    NOW.astimezone(datetime.timezone(datetime.timedelta(hours=9)))):
            with self.subTest(now=now):
                self.assertTrue(self.ok(rec, now))

    def test_staleness(self):
        for age in (1801, 1800.001, 3600, 86400, 10 ** 9):
            with self.subTest(age=age):
                self.assertFalse(self.ok(record(recorded_utc=stamp(age))))
        for rec in (record(recorded_utc=stamp(-1)), record(recorded_utc=stamp(-0.001)),
                    record(recorded_utc=stamp(-3600)), record(recorded_utc='9999-12-31T23:59:59Z')):
            with self.subTest(rec=rec):
                self.assertFalse(self.ok(rec))

    def test_battery_rules(self):
        for percent in (49, 0, 1, None, -1, 101, 1000, True, False, 50.0, 49.5, '50', [50]):
            with self.subTest(on_battery=percent):
                self.assertFalse(self.ok(record(ac_connected=False, battery_percent=percent)))
        for percent in (-1, 101, True, 50.0, '50', [50]):
            with self.subTest(on_ac=percent):
                self.assertFalse(self.ok(record(ac_connected=True, battery_percent=percent)))

    def test_run_prefix(self):
        rec = record()
        for prefix in ('sp11-run-70', 'sp11-run-', 'SP11-RUN-7', ' sp11-run-7', 'other', '', None, 7, b'sp11-run-7'):
            with self.subTest(prefix=prefix):
                self.assertFalse(self.ok(rec, prefix=prefix))
        for wrong in ('sp11-run-70', 'sp11-run-', '', None, 7):
            with self.subTest(record_prefix=wrong):
                self.assertFalse(self.ok(record(run_prefix=wrong)))
        self.assertFalse(self.ok({k: v for k, v in rec.items() if k != 'run_prefix'}, prefix=''))

    def test_every_field_is_required(self):
        for key in record():
            with self.subTest(missing=key):
                self.assertFalse(self.ok({k: v for k, v in record(ac_connected=False, battery_percent=60).items()
                                          if k != key}))
        for key in ('ac_connected', 'recorded_utc', 'run_prefix'):
            with self.subTest(missing_on_ac=key):
                self.assertFalse(self.ok({k: v for k, v in record().items() if k != key}))

    def test_ac_flag_must_be_a_real_boolean(self):
        for flag in (1, 0, 'true', 'True', 'yes', None, [], {}, 1.0):
            with self.subTest(flag=flag):
                self.assertFalse(self.ok(record(ac_connected=flag, battery_percent=90)))

    def test_malformed_timestamps(self):
        for text in ('2026-10-20T12:00:00', '2026-10-20', 'yesterday', '', ' ', 'Z', 'NaN', '2026-13-45T25:61:61Z',
                     '2026-10-20T12:00:00+25:00', '\x00', 'x' * 10000, 1_790_000_000, 1.79e9, None, True,
                     NOW, ['2026-10-20T12:00:00Z']):
            with self.subTest(text=text):
                self.assertFalse(self.ok(record(recorded_utc=text)))

    @unittest.skipUnless(hasattr(time, 'tzset'), 'needs time.tzset to pin the local zone')
    def test_a_naive_time_is_refused_in_every_local_zone(self):
        # A naive wall-clock time equal to "now" in the process's own zone would pass every freshness check
        # if that zone were assumed, so only an outright refusal keeps these False in all three zones.
        for zone, hours in (('UTC0', 0), ('JST-9', 9), ('PST8', -8)):
            local_now = (NOW + datetime.timedelta(hours=hours)).replace(tzinfo=None)
            for text in (local_now.isoformat(), local_now.isoformat(sep=' ')):
                with local_zone(zone), self.subTest(zone=zone, text=text):
                    self.assertFalse(self.ok(record(recorded_utc=text)))
                    self.assertFalse(self.ok(record(), local_now))
                    self.assertFalse(self.ok(record(), text))

    def test_extreme_timestamps_do_not_raise(self):
        for text in ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00', '0001-01-01T00:00:00Z',
                     '9999-12-31T23:59:59Z'):
            with self.subTest(text=text):
                self.assertFalse(self.ok(record(recorded_utc=text)))
        low = datetime.datetime.min.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=1)))
        high = datetime.datetime.max.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=-1)))
        for now in (low, high, 1e300, -1e300, float('inf'), float('nan')):
            with self.subTest(now=now):
                self.assertFalse(self.ok(record(), now))

    def test_malformed_clock_and_record(self):
        for now in (None, 'garbage', '', datetime.datetime(2026, 10, 20, 12, 0, 0), True, False, [], {}):
            with self.subTest(now=now):
                self.assertFalse(self.ok(record(), now))
        for rec in (None, [], (), 'record', 7, set(), b'', {}, object()):
            with self.subTest(rec=rec):
                self.assertFalse(self.ok(rec))

    def test_a_misbehaving_zone_is_a_refusal(self):
        class Broken(datetime.tzinfo):
            def utcoffset(self, when):
                raise RuntimeError('broken zone')

            def dst(self, when):
                return None

        self.assertFalse(self.ok(record(), datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=Broken())))

    def test_record_is_not_modified(self):
        rec = record(ac_connected=False, battery_percent=60)
        before = dict(rec)
        self.assertTrue(self.ok(rec))
        self.assertEqual(rec, before)

    def test_nothing_makes_it_raise(self):
        weird = [None, True, 0, -1, 1.5, float('nan'), '', 'x', b'x', [], {}, object(), NOW, 10 ** 30]
        for rec_values in itertools.product(weird, repeat=2):
            rec = {'ac_connected': rec_values[0], 'battery_percent': rec_values[1],
                   'recorded_utc': stamp(0), 'run_prefix': PREFIX}
            for now, prefix in ((NOW, PREFIX), (rec_values[0], PREFIX), (NOW, rec_values[1])):
                self.assertIn(self.mod.power_attestation_ok(rec, now, prefix), (True, False))
        for rec_stamp, now in itertools.product(weird, repeat=2):
            self.assertIn(self.mod.power_attestation_ok(record(recorded_utc=rec_stamp), now, PREFIX),
                          (True, False))


class NoWritePath(unittest.TestCase):
    """Static scans. They catch an accidental write path, not a determined one: the proof is the shape of
    the I/O struct plus the host model's bus oracle, which refuses every read outside the allow-list."""

    @classmethod
    def setUpClass(cls):
        cls.unit = code_only('compat/hamoa-thermal.c')
        cls.header = code_only('compat/hamoa-thermal.h')

    def test_io_struct_has_no_write_member(self):
        body = re.search(r'struct\s+hamoa_thermal_io\s*\{(.*?)\}\s*;', self.header, re.S).group(1)
        names = []
        for member in (part.strip() for part in body.split(';')):
            if member:
                names.append((re.search(r'\(\s*\*\s*(\w+)\s*\)', member) or re.search(r'(\w+)\s*$', member)).group(1))
        self.assertEqual(names, ['ctx', 'read32', 'counter'])

    def test_unit_touches_hardware_only_through_one_read_site(self):
        used = set(re.findall(r'\bio\s*->\s*(\w+)', self.unit))
        self.assertLessEqual(used, {'ctx', 'read32', 'counter'})
        self.assertEqual(len(re.findall(r'\bio\s*->\s*read32\s*\(', self.unit)), 1)  # the NULL checks are not calls
        self.assertIsNone(re.search(r'\bio\s*->\s*\w+\s*(?:<<|>>|[-+*/|&^%])?=(?!=)', self.unit))

    def test_no_store_vocabulary(self):
        words = re.compile(r'\b\w*(?:write|poke|wr32|iowrite|store|put32|out32|set32)\w*\b', re.I)
        self.assertEqual(words.findall(self.unit), [])
        self.assertEqual(words.findall(self.header), [])

    def test_volatile_and_asm_are_only_the_empty_compiler_barrier(self):
        barrier = r'__asm__\s+volatile\s*\(\s*""\s*:::\s*"memory"\s*\)'
        for name, text in (('unit', self.unit), ('header', self.header)):
            self.assertEqual(len(re.findall(r'\bvolatile\b', text)), len(re.findall(barrier, text)), name)
            self.assertEqual(len(re.findall(r'\b(?:__asm__|__asm|asm)\b', text)),
                             len(re.findall(barrier, text)), name)

    def test_only_the_state_is_reinterpreted_as_words(self):
        casts = re.findall(r'\(\s*(?:const\s+)?u?int\d*_t\s*\*\s*\)\s*(\w+)', self.unit)
        self.assertEqual(casts, ['s'])

    def test_read_word_checks_the_allow_list_before_reading(self):
        body = re.search(r'\bstatic\s+int\s+read_word\s*\([^)]*\)\s*\{(.*?)\n\}', self.unit, re.S).group(1)
        allowed = re.search(r'hamoa_thermal_address_allowed\s*\(', body)
        read = re.search(r'io\s*->\s*read32\s*\(', body)
        self.assertTrue(allowed and read)
        self.assertLess(allowed.start(), read.start())

    def test_python_helper_does_no_io(self):
        tree = ast.parse((ROOT / 'tools' / 'hamoa_thermal.py').read_text())
        imports, calls = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or '').split('.')[0])
            elif isinstance(node, ast.Call):
                func = node.func
                calls.add(func.id if isinstance(func, ast.Name) else getattr(func, 'attr', ''))
        self.assertLessEqual(imports, {'datetime', 'struct'})
        self.assertFalse(calls & {'open', 'eval', 'exec', 'compile', '__import__', 'system', 'popen', 'write',
                                  'read_text', 'read_bytes', 'write_text', 'write_bytes'})


def write_receipt():
    receipt = {
        'result': 'PASS',
        'scope': 'host model only; no hardware; register facts INFERRED from public Linux sources until X2',
        'command': ' '.join(BUILD),
        'sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
    }
    (OUT / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    program = unittest.main(exit=False, argv=[sys.argv[0]])
    passed = program.result.wasSuccessful()
    if passed:
        write_receipt()
        print('PASS: SP11 thermal guard (host model under ASan/UBSan, layout cross-check, decoder, admit_target, '
              'power attestation, static no-write scan; no hardware)')
    sys.exit(0 if passed else 1)
