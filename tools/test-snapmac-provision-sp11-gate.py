#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP11 offline storage provisioning, review finding R1: a plan, a pre-image or a survey read from a file is not believed.

  python3 tools/test-snapmac-provision-sp11-gate.py        (the flow itself: tools/test-snapmac-provision-sp11.py, loaded here)

The review wrote into the ESP extent of a synthetic image: the plan's new entry was the ESP (its type and LBAs, no guard, the ESP left out of
the protected entries) and the pre-image was written by hand, because `write` only compared the documents with each other. Three layers now
stand between such documents and the writer, and each is tested alone with the other two switched off: the planner's rules for the new entry
(check_plan_rules), the four-reader gate run again on the documents the pre-image binds (recheck_gate, which includes the planner's own
reproduction of the plan from the BEFORE survey) and the type of the entry in the image's own GPT (image_state). Every disk is a synthetic
image FILE under build/test-snapmac-provision-sp11; nothing opens a device. Evidence: EXPERIMENTAL (synthetic images only); hardware NOT RUN.
"""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import snapmac_image as si  # noqa: E402
import snapmac_provision as sp  # noqa: E402
import snapmac_window as sw  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flow = load('provision_flow_for_gate', HERE / 'test-snapmac-provision-sp11.py')
pt, MIB, BS, DISK, ISC_REGION, STAMP = flow.pt, flow.MIB, flow.BS, flow.DISK, flow.ISC_REGION, flow.STAMP
sha, copy_image, rewrite_gpt, readers_for, run_cli = flow.sha, flow.copy_image, flow.rewrite_gpt, flow.readers_for, flow.run_cli
PM = sp.planner()


def setUpModule():
    flow.setUpModule()


def tearDownModule():
    flow.tearDownModule()


def readers_without_eui(rows):
    """Four distinct readers, none of which carries the NVMe EUI64: the gate accepts them only with --no-eui."""
    return [('windows', pt.win_record(rows, BS, disk=DISK, eui=None)), ('mac-raw', pt.survey_of(rows, BS, eui=None, disk=DISK)),
            ('firmware', pt.norm_record(rows, 'firmware', BS, disk=DISK)), ('windows-boot', pt.win_record(rows, BS, disk=DISK, eui=None, reader='windows-boot'))]


class GateBase(flow.FlowBase):
    def setUp(self):
        super().setUp()
        self.img = self.root / 'snapintosh-test.img'
        copy_image(self.golden, self.img)
        self.before_only = self.root / 'before-only.img'
        copy_image(self.before_image, self.before_only)

    def edited_plan(self, edit=None):
        p = json.loads(json.dumps(self.w['plan']))
        if edit:
            edit(p)
        return p

    @staticmethod
    def sealed(p, isc_region=ISC_REGION):
        """A plan document consistent with the plan it holds: digest, layout and extents recomputed, as a forger who has read the code makes it."""
        n = p['new']
        return dict(format=sp.PLAN_FORMAT, plan=p, plan_sha256=sp.digest_doc(p), layout=sw.layout(n['offset'], n['bytes'], isc_region),
                    protected_extents=sp.protected_extents(p))

    def forged_esp(self):
        """The review's forgery: the new entry is the ESP (its type and LBAs, no guard), and the ESP is no longer among the protected entries."""
        def edit(p):
            esp = next(q for q in p['protected'] if q['type'] == pt.T_ESP)
            p['protected'].remove(esp)
            first, last = esp['first_lba'], esp['last_lba']
            p['new'].update(type=esp['type'], name=esp['name'], first_lba=first, last_lba=last, offset=first * BS, end=(last + 1) * BS,
                            bytes=(last + 1 - first) * BS, guard_bytes=0)
        return self.sealed(self.edited_plan(edit))

    def handmade_pre(self, w, image, readers=None):
        """A pre-image written by hand for the image as it is: the claim that the gate passed, the digests of the real documents and of the extents."""
        readers = self.readers if readers is None else readers
        with si.Image(image) as im:
            gpt = si.parse_gpt(im)
            extents = sp.extent_digests(im, w)
        n = w['plan']['new']
        entry = next(q for q in gpt['entries'] if (q['first_lba'], q['last_lba']) == (n['first_lba'], n['last_lba']))
        return dict(format=sp.PREIMAGE_FORMAT, plan_sha256=w['plan_sha256'], image=dict(bytes=DISK, block=BS),
                    new=dict(guid=entry['guid'], first_lba=n['first_lba'], last_lba=n['last_lba'], type=entry['type'], offset=n['offset'], bytes=n['bytes']),
                    gate=dict(four_reader_gate_met=True, labels=[label for label, _ in readers], agreeing=len(readers)),
                    inputs=sp._gate_inputs(self.survey_before, readers), extents=extents, extents_sha256=sp.digest_doc(extents))

    def write(self, w=None, pre=None, image=None, **over):
        image = self.img if image is None else image
        args = dict(image_path=image, w=self.w if w is None else w, pre=self.pre if pre is None else pre, container=self.container, isc=self.isc,
                    confirm=Path(image).name, stamp=STAMP, before=self.survey_before, readers=self.readers)
        args.update(over)
        return sp.write_window(**args)

    def refused_and_unchanged(self, fragment, image=None, **kw):
        image = self.img if image is None else image
        before = sha(image)
        with self.assertRaisesRegex(sp.ProvisionError, fragment):
            self.write(image=image, **kw)
        self.assertEqual(sha(image), before, 'a refused write leaves the image as it was')


def shifted(p, delta):
    n = p['new']
    n['offset'] += delta
    n['end'] += delta
    n['first_lba'] += delta // BS
    n['last_lba'] += delta // BS


def edit_new(**fields):
    return lambda p: p['new'].update(fields)


def as_float(key):
    return lambda p: p['new'].__setitem__(key, float(p['new'][key]))


def protected_with_new_type(p):
    p['protected'].append(dict(p['protected'][0], index=9, type=PM.TYPE))


def without_type(kind):
    return lambda p: p.__setitem__('protected', [q for q in p['protected'] if q['type'] != kind])


class PlanRules(GateBase):
    """The planner's rules for the one new entry, on a plan that came from a file."""

    def test_the_plan_of_the_planner_passes_every_rule(self):
        sp.check_plan_rules(self.edited_plan())
        sp.check_plan_document(self.w)

    def test_each_rule_refuses_a_plan_that_breaks_only_that_rule(self):
        n = self.w['plan']['new']
        cases = [
            ('a block size of 1024', 'block size is not 512 or 4096', lambda p: p['disk'].__setitem__('block', 1024)),
            ('a block size that is a float', 'block size is not 512 or 4096', lambda p: p['disk'].__setitem__('block', 512.0)),
            ('a block size that is a bool', 'block size is not 512 or 4096', lambda p: p['disk'].__setitem__('block', True)),
            ('a negative guard', 'whole number for guard_bytes', edit_new(guard_bytes=-1)),
            ('an EFI system type', 'Windows type', edit_new(type=PM.T_ESP)),
            ('a reserved type', 'Windows type', edit_new(type=PM.T_MSR)),
            ('a basic data type', 'Windows type', edit_new(type=PM.T_BASIC)),
            ('a recovery type', 'Windows type', edit_new(type=PM.T_RECOVERY)),
            ('an EFI system type in capitals', 'Windows type', edit_new(type=PM.T_ESP.upper())),
            ('a Linux data type', 'not of the private Snapintosh type', edit_new(type=pt.T_OTHER)),
            ('no type', 'not of the private Snapintosh type', edit_new(type=None)),
            ('a type that is a number', 'not of the private Snapintosh type', edit_new(type=7)),
            ('no guard', 'the guard is 0 bytes', edit_new(guard_bytes=0)),
            ('a guard one unit short', 'the guard is', edit_new(guard_bytes=PM.GUARD_BYTES - PM.ALIGN_BYTES)),
            ('an empty window', 'empty or not whole', edit_new(bytes=0)),
            ('an offset 4 KiB off the alignment', 'empty or not whole', lambda p: shifted(p, 4096)),
            ('a size 4 KiB off the alignment', 'empty or not whole', lambda p: p['new'].update(end=n['end'] - 4096, last_lba=n['last_lba'] - 8, bytes=n['bytes'] - 4096)),
            ('a last LBA that is not the end', "bytes and LBAs disagree", edit_new(last_lba=n['last_lba'] + 32)),
            ('a first LBA that is not the offset', "bytes and LBAs disagree", edit_new(first_lba=n['first_lba'] - 32)),
            ('a size that is not end minus offset', "bytes and LBAs disagree", edit_new(bytes=n['bytes'] - PM.ALIGN_BYTES)),
            ('a first usable LBA past the window', 'usable range', lambda p: p['disk'].__setitem__('first_usable', n['first_lba'] + 1)),
            ('a last usable LBA inside the window', 'usable range', lambda p: p['disk'].__setitem__('last_usable', n['last_lba'] - 1)),
            ('a protected entry of the new type', 'already has the new type', protected_with_new_type),
            ('no ESP among the protected entries', 'does not protect an EFI system partition', without_type(PM.T_ESP)),
            ('no Windows data partition among them', 'does not protect a Windows data partition', without_type(PM.T_BASIC)),
        ]
        cases += [(f'{key} that is a float', f'whole number for {key}', as_float(key)) for key in ('first_lba', 'last_lba', 'offset', 'bytes', 'end', 'guard_bytes')]
        for name, fragment, edit in cases:
            with self.subTest(rule=name):
                with self.assertRaisesRegex(sp.ProvisionError, fragment):
                    sp.check_plan_rules(self.edited_plan(edit))

    def test_the_document_check_runs_the_rules_on_a_plan_that_was_sealed_again(self):
        def by_hand(p):
            return dict(self.w, plan=p, plan_sha256=sp.digest_doc(p))            # the layout and extents are stale: the rule must refuse first
        cases = {'a Windows type': ('Windows type', edit_new(type=PM.T_BASIC), self.sealed), 'a foreign type': ('not of the private', edit_new(type=pt.T_OTHER), self.sealed),
                 'no guard': ('the guard is 0', edit_new(guard_bytes=0), self.sealed), 'a window off the alignment': ('empty or not whole', lambda p: shifted(p, 4096), by_hand),
                 'a protected entry of the new type': ('already has the new type', lambda p: p['protected'][-1].__setitem__('type', PM.TYPE), self.sealed)}
        for name, (fragment, edit, seal) in cases.items():
            with self.subTest(rule=name):
                w = seal(self.edited_plan(edit))
                with self.assertRaisesRegex(sp.ProvisionError, fragment):
                    sp.check_plan_document(w)

    def test_a_plan_that_is_malformed_is_a_refusal_and_not_a_crash(self):
        def sealed_by_hand(edit):
            p = self.edited_plan(edit)
            return dict(self.w, plan=p, plan_sha256=sp.digest_doc(p))
        cases = {'no new entry': lambda p: p.pop('new'), 'a new entry that is a list': lambda p: p.__setitem__('new', []),
                 'no disk': lambda p: p.pop('disk'), 'no usable range': lambda p: p['disk'].pop('first_usable'),
                 'protected entries that are null': lambda p: p.__setitem__('protected', None), 'a protected entry that is a string': lambda p: p['protected'].append('x'),
                 'no end': lambda p: p['new'].pop('end'), 'a usable range of strings': lambda p: p['disk'].update(first_usable='a', last_usable='b')}
        for name, edit in cases.items():
            with self.subTest(case=name):
                with self.assertRaisesRegex(sp.ProvisionError, 'malformed'):
                    sp.check_plan_document(sealed_by_hand(edit))


class ReviewForgery(GateBase):
    """The forgery of the review, end to end, and each layer that stops it on its own."""

    def setUp(self):
        super().setUp()
        self.forged = self.forged_esp()
        self.esp = next(e for e in self.w['protected_extents'] if e['name'] == 'partition-0')
        self.forged_pre = self.handmade_pre(self.forged, self.before_only)

    def test_the_forged_documents_agree_with_each_other_and_with_the_image(self):
        with mock.patch.object(sp, 'check_plan_rules'):
            sp.check_plan_document(self.forged)
        sp.check_preimage_document(self.forged_pre, self.forged)
        n = self.forged['plan']['new']
        self.assertEqual((n['type'], n['offset'], n['bytes'], n['guard_bytes']), (PM.T_ESP, self.esp['offset'], self.esp['bytes'], 0))
        self.assertNotIn(self.esp['offset'], [e['offset'] for e in self.forged['protected_extents'] if e['bytes']])

    def test_the_rules_refuse_the_forged_plan_before_anything_is_written(self):
        with si.Image(self.before_only) as image:
            esp_before = image.digest(self.esp['offset'], self.esp['bytes'])
        self.refused_and_unchanged('Windows type', image=self.before_only, w=self.forged, pre=self.forged_pre)
        with si.Image(self.before_only) as image:
            self.assertEqual(image.digest(self.esp['offset'], self.esp['bytes']), esp_before)
        with self.assertRaisesRegex(sp.ProvisionError, 'Windows type'):
            sp.pre_image(self.before_only, self.forged, self.survey_before, self.readers)

    def test_the_gate_at_write_time_alone_refuses_the_forged_plan(self):
        with mock.patch.object(sp, 'check_plan_rules'):
            self.refused_and_unchanged('gate is not met at write time.*cannot be reproduced from BEFORE', image=self.before_only, w=self.forged, pre=self.forged_pre)

    def test_the_image_check_alone_refuses_a_window_in_an_entry_that_is_not_the_private_type(self):
        with mock.patch.object(sp, 'check_plan_rules'), mock.patch.object(sp, 'recheck_gate'):
            self.refused_and_unchanged('not of the private Snapintosh type', image=self.before_only, w=self.forged, pre=self.forged_pre)

    def test_the_command_line_refuses_the_forged_plan_and_writes_nothing(self):
        plan, pre = self.jsonfile('forged-plan.json', self.forged), self.jsonfile('forged-pre.json', self.forged_pre)
        survey, journal = self.jsonfile('before.json', self.survey_before), self.root / 'journal.json'
        afters = []
        for i, (label, doc) in enumerate(self.readers):
            afters += ['--after', self.jsonfile(f'r{i}-{label}.json', doc)]
        before = sha(self.before_only)
        rc, out, err = run_cli(['write', '--image', self.before_only, '--plan', plan, '--pre-image', pre, '--before', survey, '--container', self.container,
                                '--isc', self.isc, '--confirm-image', self.before_only.name, '--stamp', STAMP, '--out', journal] + afters)
        self.assertEqual(rc, 2, (out, err))
        self.assertIn('Windows type', err)
        self.assertEqual(sha(self.before_only), before)
        self.assertFalse(journal.exists())

    def test_a_window_the_planner_would_not_have_made_is_refused_when_the_gate_runs_again(self):
        """Every rule holds and the image's GPT has the entry where this plan says: only the planner's reproduction of the plan from BEFORE stops it."""
        moved = self.sealed(self.edited_plan(lambda p: p['new'].update(offset=p['new']['offset'] + MIB, first_lba=p['new']['first_lba'] + MIB // BS,
                                                                       bytes=p['new']['bytes'] - MIB)))
        sp.check_plan_document(moved)
        rewrite_gpt(self.img, pt.applied(self.before_rows, moved['plan']))
        with si.Image(self.img) as image:
            sp.image_state(image, moved)
        pre = self.handmade_pre(moved, self.img)
        self.refused_and_unchanged('gate is not met at write time.*differs from what BEFORE yields', w=moved, pre=pre)
        with self.assertRaisesRegex(sp.ProvisionError, 'gate'):
            sp.pre_image(self.img, moved, self.survey_before, self.readers)


class WriteTimeGate(GateBase):
    """The pre-image binds the survey and the readers; `write` runs the gate again on them instead of believing the pre-image's own claim."""

    def test_the_pre_image_binds_the_survey_and_every_reader_by_digest(self):
        bound = self.pre['inputs']
        self.assertEqual(bound['before_sha256'], sp.digest_doc(self.survey_before))
        self.assertEqual(bound['readers'], [dict(label=label, sha256=sp.digest_doc(doc)) for label, doc in self.readers])
        self.assertEqual(len({r['sha256'] for r in bound['readers']}), len(self.readers))

    def test_the_documents_the_pre_image_was_made_from_pass_in_any_order(self):
        self.write(readers=list(reversed(self.readers)))

    def test_a_survey_or_a_reader_that_is_not_the_one_the_pre_image_was_made_from_is_refused(self):
        survey = json.loads(json.dumps(self.survey_before))
        survey['disks'][0]['block_size'] = 4096
        other = ('mac-raw', pt.survey_of(self.after_rows, BS, eui=pt.EUI, disk=DISK, guid=pt.G(2)))
        cases = [('another survey', 'BEFORE survey is not the one', dict(before=survey)),
                 ('a reader that differs', 'readers are not the ones', dict(readers=self.readers[:1] + [other] + self.readers[2:])),
                 ('a reader missing', 'readers are not the ones', dict(readers=self.readers[:3])),
                 ('a reader too many', 'readers are not the ones', dict(readers=self.readers + [('extra', pt.win_record(self.after_rows, BS, disk=DISK, reader='extra'))])),
                 ('no readers', 'readers are not the ones', dict(readers=[])),
                 ('a reader that could not be read', 'cannot be digested', dict(readers=self.readers[:3] + [('windows-boot', PM.PlanError('no such file'))]))]
        for name, fragment, over in cases:
            with self.subTest(case=name):
                self.refused_and_unchanged(fragment, **over)

    def test_a_pre_image_that_claims_the_gate_is_met_is_refused_when_the_readers_disagree(self):
        """The forger binds the digests of the readers he supplies, and keeps the claim: the gate must run again to see the disagreement."""
        stale = ('mac-raw', pt.survey_of(self.before_rows, BS, eui=None, disk=DISK))
        readers = self.readers[:1] + [stale] + self.readers[2:]
        pre = dict(self.pre, inputs=sp._gate_inputs(self.survey_before, readers))
        sp.check_preimage_document(pre, self.w)
        self.refused_and_unchanged('gate is not met at write time.*mac-raw', pre=pre, readers=readers)
        with self.assertRaisesRegex(sp.ProvisionError, 'gate'):
            sp.pre_image(self.img, self.w, self.survey_before, readers)

    def test_a_survey_the_planner_cannot_use_is_refused_at_write_time_by_the_gate(self):
        """The pre-image binds the digests of an unusable survey, as a forger would: only running the gate again shows that it is unusable."""
        empty = {'disks': []}
        pre = dict(self.pre, inputs=sp._gate_inputs(empty, self.readers))
        sp.check_preimage_document(pre, self.w)
        self.refused_and_unchanged('gate refuses at write time', pre=pre, before=empty)

    def test_a_pre_image_whose_inputs_are_missing_or_malformed_is_refused(self):
        def edited(edit):
            pre = json.loads(json.dumps(self.pre))
            edit(pre)
            return pre
        digest = '0' * 64
        cases = {'no inputs': lambda p: p.pop('inputs'), 'inputs that are null': lambda p: p.__setitem__('inputs', None),
                 'no survey digest': lambda p: p['inputs'].pop('before_sha256'), 'an extra key': lambda p: p['inputs'].__setitem__('extra', 1),
                 'a digest that is not hex': lambda p: p['inputs'].__setitem__('before_sha256', 'z' * 64),
                 'a digest that is short': lambda p: p['inputs'].__setitem__('before_sha256', digest[:63]),
                 'a digest in capitals': lambda p: p['inputs'].__setitem__('before_sha256', 'A' * 64),
                 'a digest with a newline': lambda p: p['inputs'].__setitem__('before_sha256', digest[:63] + '\n'),
                 'readers that are not a list': lambda p: p['inputs'].__setitem__('readers', 'x'),
                 'three readers': lambda p: p['inputs']['readers'].pop(),
                 'a reader without a digest': lambda p: p['inputs']['readers'][0].pop('sha256'),
                 'a reader with another key': lambda p: p['inputs']['readers'][0].__setitem__('extra', 1),
                 'a reader that is no object': lambda p: p['inputs']['readers'].__setitem__(0, 'x'),
                 'a reader digest that is not hex': lambda p: p['inputs']['readers'][1].__setitem__('sha256', 'g' * 64)}
        for name, edit in cases.items():
            pre = edited(edit)
            with self.subTest(case=name):
                with self.assertRaisesRegex(sp.ProvisionError, 'does not bind'):
                    sp.check_preimage_document(pre, self.w)
                with self.assertRaisesRegex(sp.ProvisionError, 'does not bind'):
                    sp.post_check(self.img, self.w, pre, {})
        sp.check_preimage_document(self.pre, self.w)

    def test_a_pre_image_with_a_gate_flag_that_is_not_true_or_extents_that_are_malformed_is_refused(self):
        for name, edit in (('a gate that is a string', lambda p: p.__setitem__('gate', 'met')), ('a gate that is not met', lambda p: p['gate'].__setitem__('four_reader_gate_met', False)),
                           ('an extent without a name', lambda p: p['extents'][0].pop('name')), ('extents that are not a list', lambda p: p.__setitem__('extents', 5))):
            pre = json.loads(json.dumps(self.pre))
            edit(pre)
            if name.startswith('an extent') or name.startswith('extents'):
                pre['extents_sha256'] = sp.digest_doc(pre['extents'])
            with self.subTest(case=name):
                with self.assertRaises(sp.ProvisionError):
                    sp.check_preimage_document(pre, self.w)

    def test_the_eui_requirement_reaches_the_gate_at_write_time(self):
        readers = readers_without_eui(self.after_rows)
        with self.assertRaisesRegex(sp.ProvisionError, 'NVMe EUI64'):
            sp.pre_image(self.img, self.w, self.survey_before, readers)
        pre = sp.pre_image(self.img, self.w, self.survey_before, readers, require_eui=False)
        self.refused_and_unchanged('NVMe EUI64', pre=pre, readers=readers)
        self.write(pre=pre, readers=readers, require_eui=False)


class CommandLine(GateBase):
    def files(self, readers=None):
        survey, plan = self.jsonfile('before.json', self.survey_before), self.jsonfile('plan.json', self.w)
        afters = []
        for i, (label, doc) in enumerate(self.readers if readers is None else readers):
            afters += ['--after', self.jsonfile(f'r{i}-{label}.json', doc)]
        return survey, plan, afters

    def argv(self, plan, pre, survey, afters, *extra):
        before = [] if survey is None else ['--before', survey]
        return (['write', '--image', self.img, '--plan', plan, '--pre-image', pre, '--container', self.container, '--isc', self.isc,
                 '--confirm-image', self.img.name, '--stamp', STAMP, '--out', self.root / 'journal.json'] + before + list(extra) + afters)

    def test_write_needs_the_survey_and_the_readers_and_refuses_others(self):
        survey, plan, afters = self.files()
        pre = self.jsonfile('pre.json', self.pre)
        before = sha(self.img)
        self.assertEqual(run_cli(self.argv(plan, pre, survey, []))[0], 2, 'no --after')
        self.assertEqual(run_cli(self.argv(plan, pre, None, afters))[0], 2, 'no --before')
        other = self.jsonfile('other.json', pt.survey_of(self.after_rows, BS, eui=pt.EUI, disk=DISK, guid=pt.G(2)))
        rc, out, err = run_cli(self.argv(plan, pre, survey, afters[:-1] + [other]))
        self.assertEqual(rc, 2, (out, err))
        self.assertIn('readers are not the ones', err)
        self.assertEqual(sha(self.img), before)
        self.assertFalse((self.root / 'journal.json').exists())

    def test_the_no_eui_option_of_write_reaches_the_gate(self):
        readers = readers_without_eui(self.after_rows)
        survey, plan, afters = self.files(readers)
        pre_file = self.root / 'pre.json'
        rc, out, err = run_cli(['pre-image', '--image', self.img, '--plan', plan, '--before', survey, '--out', pre_file, '--no-eui'] + afters)
        self.assertEqual((rc, err), (0, ''), out)
        before = sha(self.img)
        rc, _, err = run_cli(self.argv(plan, pre_file, survey, afters))
        self.assertEqual(rc, 2)
        self.assertIn('NVMe EUI64', err)
        self.assertEqual(sha(self.img), before)
        rc, out, err = run_cli(self.argv(plan, pre_file, survey, afters, '--no-eui'))
        self.assertEqual((rc, err), (0, ''), out)
        self.assertNotEqual(sha(self.img), before)


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
