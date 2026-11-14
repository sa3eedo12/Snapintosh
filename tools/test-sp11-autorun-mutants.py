#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mutation check for the SP11 autorun generator and stager.

Each mutant seeds one plausible bug into a copy of tools/sp11_autorun.py or tools/stage-sp11-boot-media.py (under
build/) and runs the host suites against that copy, through SP11_AUTORUN_DIR and SP11_STAGE_DIR. A mutant the
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
SUITES = ('test-sp11-autorun.py', 'test-sp11-autorun-tree.py', 'test-sp11-autorun-payload.py',
          'test-sp11-autorun-payload-tree.py')
REAL_IMAGE_VARIABLES = ('SP11_SHELL_EFI', 'SP11_SLBOUNCE_EFI', 'SP11_Q1N1_EFI', 'SP11_Q1N1_STK_EFI', 'SP11_TESTS_EFI',
                        'Q1N1_DIR', 'SP11_Q1N1_PAYLOAD_EFI', 'SP11_PAYLOAD_STATE_BIN')
GENERATOR, STAGER = 'sp11_autorun.py', 'stage-sp11-boot-media.py'
SURVEY = "'Q1N1-STK.EFI': (('--el2',),),"   # the table entry of the survey image, which takes exactly --el2
RUNNER = "'Q1N1-PAYLOAD.EFI': (('--el2',),),"   # the same for the payload runner

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
    (GENERATOR, 'the runner may take --boot', [(RUNNER, "'Q1N1-PAYLOAD.EFI': (('--el2',), ('--boot',)),")]),
    (GENERATOR, 'the runner may take --uart', [(RUNNER, "'Q1N1-PAYLOAD.EFI': (('--el2',), ('--uart',)),")]),
    (GENERATOR, 'the runner may take --uart-proxy', [(RUNNER, "'Q1N1-PAYLOAD.EFI': (('--el2',), ('--uart-proxy',)),")]),
    (GENERATOR, 'the runner may take --fault-test', [(RUNNER, "'Q1N1-PAYLOAD.EFI': (('--el2',), ('--fault-test',)),")]),
    (GENERATOR, 'the runner may run with no argument', [(RUNNER, "'Q1N1-PAYLOAD.EFI': ((), ('--el2',)),")]),
    (GENERATOR, 'the runner may take --boot and the ban check is gone',
     [(RUNNER, "'Q1N1-PAYLOAD.EFI': (('--el2',), ('--boot',)),"), ("if arg.lower().split('=')[0] in BANNED_ARGUMENTS:", 'if False:')]),
    # The data files of the payload runner, and its manifest.
    (GENERATOR, 'the manifest is not checked when the plan is staged', [('if manifest is not None:', 'if False:')]),
    (GENERATOR, 'the manifest sizes are not compared with the staged files', [('if entry.size != item.size:', 'if False:')]),
    (GENERATOR, 'the manifest hashes are not compared with the staged files', [('if entry.sha256 != item.sha256:', 'if False:')]),
    (GENERATOR, 'neither the sizes nor the hashes are compared',
     [('if entry.size != item.size:', 'if False:'), ('if entry.sha256 != item.sha256:', 'if False:')]),
    (GENERATOR, 'a staged data file the manifest does not list is tolerated', [('if unlisted:', 'if False:')]),
    (GENERATOR, 'the data section is missing from PLAN.TXT', [('if plan.data:', 'if False:')]),
    (GENERATOR, 'data files are accepted without a runner step', [('if hashes and not runner:', 'if False:')]),
    (GENERATOR, 'a runner step does not need its manifest', [('if runner and MANIFEST_NAME not in hashes:', 'if False:')]),
    (GENERATOR, 'a data name may be any name', [('if not DATA_NAME_RE.fullmatch(name):', 'if False:')]),
    (GENERATOR, 'a data name may collide with a plan file', [('if name in taken or MARKER_RE.fullmatch(name):', 'if MARKER_RE.fullmatch(name):')]),
    (GENERATOR, 'a data name may be a marker name', [('if name in taken or MARKER_RE.fullmatch(name):', 'if name in taken:')]),
    (GENERATOR, 'any number of data files is allowed', [('if len(hashes) - (MANIFEST_NAME in hashes) > MAX_PAYLOAD_FILES:', 'if False:')]),
    (GENERATOR, 'a manifest may hold more than five entries', [('MAX_PAYLOAD_FILES = 5 ', 'MAX_PAYLOAD_FILES = 50 ')]),
    (GENERATOR, 'a manifest of any magic is accepted', [('if magic != MANIFEST_MAGIC:', 'if False:')]),
    (GENERATOR, 'a manifest of any version is accepted', [('if version != 1:', 'if False:')]),
    (GENERATOR, 'a manifest of any entry size is accepted', [('if entry_size != _ENTRY.size:', 'if False:')]),
    (GENERATOR, 'bytes after the entries are tolerated', [('if len(blob) != _HEADER.size + count * _ENTRY.size:', 'if False:')]),
    (GENERATOR, 'a duplicate manifest entry is tolerated', [('if path.lower() in seen:', 'if False:')]),
    (GENERATOR, 'a manifest path outside SP11 is tolerated', [("if len(parts) != 2 or parts[0].upper() != 'SP11':", 'if False:')]),
    (GENERATOR, 'a manifest path that is not 8.3 is tolerated', [('if not _NAME83_RE.fullmatch(parts[1]):', 'if False:')]),
    (GENERATOR, 'a manifest path that is not canonical is tolerated',
     [("if not path.startswith('\\\\') or any(char in path for char in '/:*?\"<>|') or any(part in ('', '.', '..') for part in parts):", 'if False:')]),
    (GENERATOR, 'a manifest may list itself', [('if parts[1].upper() == MANIFEST_NAME:', 'if False:')]),
    (GENERATOR, 'a manifest size is not range checked', [('if not 1 <= size <= DATA_MAX_BYTES:', 'if False:')]),
    (STAGER, 'verify does not parse the manifest of a payload plan', [('if manifest is not None:', 'if False:')]),
    (STAGER, 'verify ignores a tampered data file', [("if (found['size'], found['sha256']) != (item.size, item.sha256):", 'if False:')]),
    (STAGER, 'remove leaves the data files', [("| {f'SP11/{item.name}' for item in plan.data}) - BASE_RELS", ') - BASE_RELS')]),
    (STAGER, 'a data source is not hash checked', [("if info['sha256'] != data_hashes[name]:", 'if False:')]),
    (STAGER, 'the scratch build is accepted as a data source', [('for name, path in {**sources, **data_sources}.items():', 'for name, path in sources.items():')]),
    (STAGER, 'a data file may replace a staged file', [("if (dest / 'SP11' / name).exists():", 'if False:')]),
    (STAGER, 'a data name given twice is tolerated', [('if name in data_sources:', 'if False:')]),
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
    """0 when every host suite passes against the modules in `directory`; the first failing return code otherwise.

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
