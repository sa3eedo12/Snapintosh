#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mutation check for the SP11 autorun generator and stager.

Each mutant seeds one plausible bug into a copy of tools/sp11_autorun.py or tools/stage-sp11-boot-media.py (under
build/) and runs the two host suites against that copy, through SP11_AUTORUN_DIR and SP11_STAGE_DIR. A mutant the
suites do not fail on is a hole in the suites, so this run fails. The first mutants put a banned argument or the
scratch build into the tables or the template, for q1n1 and for the survey image Q1N1-STK.EFI, which is the check that
the tests notice a banned argument seeded into the generator. A control run with no mutation must pass, and every
replaced text must occur exactly once and leave a module that still compiles, so that a failure means a bug was caught
and not that the copy was broken.
Results go to build/test-sp11-autorun-mutants/mutants.tsv.

    python3 tools/test-sp11-autorun-mutants.py
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import shutil
import subprocess
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
BUILD = ROOT / 'build' / 'test-sp11-autorun-mutants'
SUITES = ('test-sp11-autorun.py', 'test-sp11-autorun-tree.py')
REAL_IMAGE_VARIABLES = ('SP11_SHELL_EFI', 'SP11_SLBOUNCE_EFI', 'SP11_Q1N1_EFI', 'SP11_Q1N1_STK_EFI', 'SP11_TESTS_EFI')
GENERATOR, STAGER = 'sp11_autorun.py', 'stage-sp11-boot-media.py'
SURVEY = "'Q1N1-STK.EFI': (('--el2',),),"   # the table entry of the survey image, which takes exactly --el2

# (module, what the bug is, [(old text, new text), ...]). Each old text occurs exactly once in its module.
MUTANTS = (
    (GENERATOR, 'q1n1 may take --boot', [("'Q1N1.EFI': ((), ('--el2',)),", "'Q1N1.EFI': ((), ('--el2',), ('--boot',)),")]),
    (GENERATOR, 'q1n1 may take --uart-proxy', [("'Q1N1.EFI': ((), ('--el2',)),", "'Q1N1.EFI': ((), ('--el2',), ('--uart-proxy',)),")]),
    (GENERATOR, 'q1n1 may take --el2 --uart', [("'Q1N1.EFI': ((), ('--el2',)),", "'Q1N1.EFI': ((), ('--el2',), ('--el2', '--uart')),")]),
    (GENERATOR, 'the survey image may take --boot', [(SURVEY, "'Q1N1-STK.EFI': (('--el2',), ('--boot',)),")]),
    (GENERATOR, 'the survey image may take --uart', [(SURVEY, "'Q1N1-STK.EFI': (('--el2',), ('--uart',)),")]),
    (GENERATOR, 'the survey image may take --uart-proxy', [(SURVEY, "'Q1N1-STK.EFI': (('--el2',), ('--uart-proxy',)),")]),
    (GENERATOR, 'the survey image may take --fault-test', [(SURVEY, "'Q1N1-STK.EFI': (('--el2',), ('--fault-test',)),")]),
    (GENERATOR, 'the survey image may run with no argument', [(SURVEY, "'Q1N1-STK.EFI': ((), ('--el2',)),")]),
    (GENERATOR, 'the survey image may take --boot and the ban check is gone',
     [(SURVEY, "'Q1N1-STK.EFI': (('--el2',), ('--boot',)),"), ("if arg.lower().split('=')[0] in BANNED_ARGUMENTS:", 'if False:')]),
    (GENERATOR, 'the tests image may take --fault-test', [("'TESTS.EFI': ((),),", "'TESTS.EFI': ((), ('--fault-test',)),")]),
    (GENERATOR, 'the scratch build is an allowed slbounce build and no longer banned',
     [("'SLBOUNCE-DRYRUN.EFI')\n# Applications", "'SLBOUNCE-DRYRUN.EFI', 'SLBOUNCE-OLD-NOSWEEP.EFI')\n# Applications"),
      ("BANNED_NAMES = ('SLBOUNCE-OLD-NOSWEEP.EFI',)", 'BANNED_NAMES = ()')]),
    (GENERATOR, 'the ban list is empty', [("BANNED_NAMES = ('SLBOUNCE-OLD-NOSWEEP.EFI',)", 'BANNED_NAMES = ()')]),
    (GENERATOR, 'a banned argument is no longer recognised', [("if arg.lower().split('=')[0] in BANNED_ARGUMENTS:", 'if False:')]),
    (GENERATOR, 'the template appends --boot to the run line',
     [("f'SP11\\\\{step.run_line}'\n", "f'SP11\\\\{step.run_line} --boot'\n")]),
    (GENERATOR, 'the template ends with a del line', [("return lines + [':done', _say(", "return lines + [':done', 'del SP11\\\\DONE1.TXT', _say(")]),
    (GENERATOR, 'the marker is never written', [("""              f'{_say(f"AUTORUN {plan.name} STEP {k} STARTED")} >a {marker}',\n""", '')]),
    (GENERATOR, 'no pause before the marker', [("              f'stall {PAUSE_MICROSECONDS}',\n", '')]),
    (GENERATOR, 'a step does not leave the script after the run', [("run, 'goto end']", 'run]')]),
    (GENERATOR, 'a step runs when its marker does not exist yet', [("f':step{k}', f'if exist {marker} then'", "f':step{k}', f'if not exist {marker} then'")]),
    (GENERATOR, 'the files are not checked before the step', [("for needed in (f'SP11\\\\{step.slbounce}', f'SP11\\\\{step.app}'):", 'for needed in ():')]),
    (GENERATOR, 'the marker write is not checked', [("              f'if not exist {marker} then', *_refuse(f'cannot write {marker}'),\n", '')]),
    (GENERATOR, 'many more steps are allowed', [('MAX_STEPS = 8', 'MAX_STEPS = 80')]),
    (GENERATOR, 'echo text is not quoted', [("""return f'{indent}echo "{text}"'""", "return f'{indent}echo {text}'")]),
    (GENERATOR, 'PLAN.TXT need not be canonical', [('if render_plan_text(plan) != data:', 'if False:')]),
    (GENERATOR, 'any printable plan name is accepted', [("NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,23}')", "NAME_RE = re.compile(r'[ -~]{1,24}')")]),
    (GENERATOR, 'upper-case hashes are accepted', [("SHA256_RE = re.compile(r'[0-9a-f]{64}')", "SHA256_RE = re.compile(r'[0-9a-fA-F]{64}')")]),
    (GENERATOR, 'a file without a hash is tolerated', [('if digest is None:', 'if False:')]),
    (GENERATOR, 'a file that no step uses is tolerated', [('if given not in used:', 'if False:')]),
    (GENERATOR, 'the banner names another build than the one loaded', [("_say(f'CMD1: load {load}')", "_say(f'CMD1: load SP11\\\\SLBOUNCE.EFI')")]),
    (GENERATOR, 'the line checker does not look at the order of a step', [('if positions != sorted(positions):', 'if False:')]),
    (STAGER, 'verify accepts any script in an autorun tree', [('if script_bytes != expected_script:', 'if False:')]),
    (STAGER, 'verify no longer reports another startup.nsh', [('startup_hits = [hit for hit in startup_hits if hit != autorun.SCRIPT_REL]', 'startup_hits = []')]),
    (STAGER, 'verify does not compare the staged files with the plan', [("if found['sha256'] != digest:", 'if False:')]),
    (STAGER, 'verify does not look at the README', [('if read_staged_bytes(dest / README_REL, AUTORUN_TEXT_MAX) != expected_readme:', 'if False:')]),
    (STAGER, 'a default tree gets an autorun key', [('if autorun_report is not None:\n        result', 'if True:\n        result')]),
    (STAGER, 'apply does not compare the source hash', [("if facts['sha256'] != hashes[name]:", 'if False:')]),
    (STAGER, 'apply accepts a source named like the scratch build', [('if path.name.upper() in autorun.BANNED_NAMES:', 'if False:')]),
    (STAGER, 'apply accepts an incomplete base tree', [("if base['status'] != 'COMPLETE':", 'if False:')]),
    (STAGER, 'apply replaces a staged file with other bytes', [("if sha256_file(dest / rel) != facts['sha256']:", 'if False:')]),
    (STAGER, 'apply can be applied twice', [('if autorun_findings(dest) is not None:', 'if False:')]),
    (STAGER, 'apply does not list the new files in the manifest', [('entries.update({rel: sha256_bytes(data) for rel, data in writes.items()})', 'pass')]),
    (STAGER, 'a failed apply is not undone', [('for path, previous in reversed(self.undo):', 'for path, previous in []:')]),
    (STAGER, 'remove leaves the markers', [('doomed += marker_paths(dest)', 'doomed += []')]),
    (STAGER, 'clear removes nothing', [('for path in marker_paths(dest):\n        path.unlink()\n', 'for path in marker_paths(dest):\n')]),
)


def mutate(text, replacements):
    for old, new in replacements:
        if text.count(old) != 1:
            raise AssertionError(f'{text.count(old)} occurrences of {old!r}')
        text = text.replace(old, new)
    return text


def run_suites(directory, tag):
    """0 when both host suites pass against the modules in `directory`; the first failing return code otherwise.

    The real-image tests of the tree suite look for the pins file beside the stager, which a mutant's two-module copy does
    not have, so the child runs get no real image and those tests skip; the mutants are checked on the synthetic fixtures."""
    inherited = {k: v for k, v in os.environ.items() if k not in REAL_IMAGE_VARIABLES}
    for suite in SUITES:
        done = subprocess.run([sys.executable, '-B', str(TOOLS / suite)], capture_output=True, text=True, timeout=900,
                              env={**inherited, 'SP11_AUTORUN_DIR': str(directory), 'SP11_STAGE_DIR': str(directory),
                                   'PYTHONDONTWRITEBYTECODE': '1'})
        if done.returncode:
            return done.returncode, f'{suite}: {done.stderr.strip().splitlines()[-1] if done.stderr.strip() else ""}'
    return 0, ''


def prepare(index, module, replacements):
    directory = BUILD / f'{index:02d}'
    directory.mkdir(parents=True)
    for name in (GENERATOR, STAGER):
        shutil.copy(TOOLS / name, directory / name)
    if replacements:
        text = mutate((directory / module).read_text(), replacements)
        compile(text, str(directory / module), 'exec')
        (directory / module).write_text(text)
    return directory


class Mutants(unittest.TestCase):
    def test_the_control_run_passes_and_every_mutant_is_caught(self):
        shutil.rmtree(BUILD, ignore_errors=True)
        BUILD.mkdir(parents=True)
        control = prepare(0, GENERATOR, [])
        code, detail = run_suites(control, 'control')
        self.assertEqual(code, 0, f'the suites fail without any mutation: {detail}')
        directories = [prepare(i, module, replacements) for i, (module, _, replacements) in enumerate(MUTANTS, 1)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda d: run_suites(d, d.name), directories))
        rows = [f'{i:02d}\t{"caught" if code else "SURVIVED"}\t{module}\t{what}\t{detail}'
                for i, ((module, what, _), (code, detail)) in enumerate(zip(MUTANTS, results), 1)]
        (BUILD / 'mutants.tsv').write_text('\n'.join(rows) + '\n')
        survivors = [row for row in rows if '\tSURVIVED\t' in row]
        self.assertEqual(survivors, [], 'mutants the suites did not fail on:\n' + '\n'.join(survivors))
        print(f'\n{len(MUTANTS)} mutants, all caught; control run passed; see {(BUILD / "mutants.tsv").relative_to(ROOT)}')


if __name__ == '__main__':
    unittest.main()
