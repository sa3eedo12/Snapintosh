# SPDX-License-Identifier: MIT
"""The plan `a1-xhci-s1` of the xHCI S1 No-Op DMA test (docs/SP11-XHCI-S1.md), built with the public functions of tools/sp11_autorun.py: one autorun
step (`load` SLBOUNCE-DEVHE-MARK.EFI, then Q1N1-PAYLOAD.EFI --el2), one payload per boot, and the payload runner's manifest (PAYLOAD.MAN) that lists it.
XHCI1.BIN (usb_1_ss1) is the plan `a1-xhci-s1`; the optional second boot, XHCI0.BIN (usb_1_ss0), is the plan `a1-xhci-s1-ss0` with its own manifest.

This module stages nothing and writes nothing: it validates the plan against the rules of sp11_autorun (the tables, the manifest the runner would
accept, the script that the plan generates), and `tools/sp11_xhci_s1_record.py` gives the argument vector of `tools/stage-sp11-boot-media.py autorun` that
stages the tree on a scratch directory. A plan, a script or a staged tree authorises nothing: only the operator's written approval of the record does.

Evidence: EXPERIMENTAL (host only). Hardware NOT RUN.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_autorun as autorun  # noqa: E402
import sp11_autorun_fixtures as fx  # noqa: E402
import sp11_xhci_s1_record as rec  # noqa: E402

STEP = f'{rec.SLBOUNCE_NAME} {rec.RUNNER_NAME} --el2'
PLAN_NAMES = {1: rec.PLAN_NAME, 0: rec.PLAN_NAME_SS0}


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def manifest_for(controller, payload):
    """PAYLOAD.MAN for one payload, written by the independent writer of tools/sp11_autorun_fixtures.py (the layout of docs/SP11-PAYLOAD.md of the q1n1
    fork); tools/test-sp11-xhci-s1-plan.py compares it with the bytes the q1n1 manifest tool writes when a q1n1 tree is given."""
    return fx.listing({rec.CONTROLLERS[controller][0]: payload})


def build(controller, payload, manifest=None, slbounce_sha256=rec.SLBOUNCE_SHA256, runner_sha256=rec.RUNNER_SHA256):
    """The validated Plan for the payload of one controller: the step, the pinned hashes of the two EFI files, the manifest and the payload as data files.
    autorun.PlanError for anything the rules of sp11_autorun refuse."""
    name = rec.CONTROLLERS[controller][0]
    manifest = manifest if manifest is not None else manifest_for(controller, payload)
    plan = autorun.build_plan(PLAN_NAMES[controller], [STEP], {rec.SLBOUNCE_NAME: slbounce_sha256, rec.RUNNER_NAME: runner_sha256})
    files = [autorun.DataFile(autorun.MANIFEST_NAME, len(manifest), sha256_hex(manifest)), autorun.DataFile(name, len(payload), sha256_hex(payload))]
    return autorun.attach_data(plan, files, manifest)


def renderings(plan):
    """(PLAN.TXT, startup.nsh) of the plan, the bytes the staged tree would hold; autorun.PlanError if the script fails its own whitelist or the plan
    text does not read back as the plan."""
    plan_text, script = autorun.render_plan_text(plan), autorun.render_script(plan)
    problems = autorun.script_problems(script.decode('ascii'))
    if problems:
        raise autorun.PlanError('script_problems', '; '.join(problems))
    if autorun.parse_plan_text(plan_text) != plan:
        raise autorun.PlanError('plan_text_does_not_round_trip')
    return plan_text, script


def staging_files(controller, payload, runner_path='<Q1N1-PAYLOAD.EFI>', slbounce_path='<SLBOUNCE-DEVHE-MARK.EFI>', manifest_path='<PAYLOAD.MAN>',
                  payload_path=None):
    """The `plan_files` argument of sp11_xhci_s1_record.staging_command for this payload (paths are for the operator to fill in)."""
    name = rec.CONTROLLERS[controller][0]
    manifest = manifest_for(controller, payload)
    return {rec.SLBOUNCE_NAME: (slbounce_path, rec.SLBOUNCE_SHA256), rec.RUNNER_NAME: (runner_path, rec.RUNNER_SHA256),
            autorun.MANIFEST_NAME: (manifest_path, sha256_hex(manifest)), name: (payload_path or f'<{name}>', sha256_hex(payload))}
