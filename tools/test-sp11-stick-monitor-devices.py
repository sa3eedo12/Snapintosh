#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of what the record of the monitor entry run says about device code, firmware calls, interrupts and thermal reads (monitor port work item 4b,
Phase D; docs/SP11-STICK-MONITOR.md section 14): D1, the families of device code in the monitor binary (found by name in its symbol table, llvm-nm), each
named in section 3b with its gates, the families the build leaves out absent from the binary, and the sources that the gates are read from; D5, the guest SMC
that nothing traps and the read-only scan of the local kernelcache that the text cites; D6, the thermal reads; D7, which interrupts the monitor acknowledges; and, since the
SP11 monitor calls nvmeh_sp11_adopt with a target and a record that a host places (storage integration), the way the monitor reaches the SSD, the stick bundle's silence about placing
those two blocks, and, since the builder refuses an ANS model in PARAMS (R3), that the call is reached only for an ANS window and that no bundle of this kind lists one (the builder's numbers
and its refusal end to end are in tools/test-sp11-stick-monitor-ans.py).
Every check has a deliberately faulty case: a record that omits a family or a gate, a binary with a function of a family that the build leaves out, an SMC in a
function that no family owns, a scan of words that hold an SMC, an ans_glue.c that adopts the SSD another way or asks for a window nowhere, an nvme-host.c that does not clear the
state word or refuse a pair that was never placed, a check_launch that looks in fewer places than the real one, and a file of the stick bundle that names what places the blocks.

    python3 tools/test-sp11-stick-monitor-devices.py

The fixtures of tools/test-sp11-stick-monitor-auth.py are loaded with a scratch directory of their own (build/sp11-monitor-devices-test-scratch/, deleted at
the end). The binary tests need clang, ld.lld, the llvm tools and a q1n1 checkout ($Q1N1_SP11_TREE) and SKIP without them; the kernelcache scan needs the
J700 26A428 boot set under downloads/extracted/j700ap/ (git-ignored Apple material, read and never copied) and SKIPs without it: a skip is never a pass. The
source pins read the files they cite, so that a changed source fails a test and the record is looked at again. No hardware: NOT RUN."""
import collections
import importlib
import importlib.util
import re
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_monitor_census as C  # noqa: E402
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_monitor_auth_tests', TOOLS / 'test-sp11-stick-monitor-auth.py')
AUTH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AUTH)
SCRATCH = ROOT / 'build' / 'sp11-monitor-devices-test-scratch'
AUTH.SCRATCH = SCRATCH
BT, STATE, sha, m1, sb, ch = AUTH.BT, AUTH.STATE, AUTH.sha, AUTH.m1, AUTH.sb, AUTH.ch
# The families of device code (code that stores to a device or calls firmware) that this monitor binary holds: the names of their functions as the symbol table
# gives them (an LTO clone suffix taken off) and the words of their paragraph in section 3b that say what closes them.
FAMILIES = {
    'adreno': (r'(adreno_|gpu_|fwd_).*|fence_at|tlbi|gic_off', ('adreno_*', 'gpu_*', 'fwd_*', 'gpu_smc', 'ADOPTED', 'PRIVATE', 'LIVE', 'gpu.flags', 'adreno_smc', 'gpu_irq',
                                                               'gpu.gicd')),
    'early_stop': (r'early_stop_.*', ('LIVE', 'early_stop_host_tick', 'early_stop_host_call', 'early_stop.host_call', 'EL2', 'early_stop_hvc')),
    'thermal': (r'(thermal_|a16_thermal_).*', ('thermal_*', 'thermal_read', 'PRIVATE', 'c->thermal', 'early_stop.flags')),
    'nvmeh': (r'nvmeh_.*|ans_window_.*|command(\.ok)?', ('nvmeh_*', 'ans_window_*', 'UNWIRED', 'ans_window_prepare')),
    'secondary': (r'secondary_.*|xnuboot_secondary_stop|early_off', ('secondary_*', 'PRIVATE', 'host_cpus')),
}
# What this profile leaves out, as section 3b lists it, with the pattern that finds such a function in the symbol table.
ABSENT = {'hamoa_*': r'^hamoa_', 'edp_*': r'^edp_', 'qcc*': r'^qcc', 'i2c_input*': r'^i2c_input', 'guest_net*': r'^guest_net', 'dvfs*': r'(^|_)dvfs',
          'boot_reloc*': r'^boot_reloc', 'boot_relay*': r'^boot_relay', 'display_timing*': r'^display_timing', 'adreno_recover*': r'^(adreno|gpu)_recover',
          'adreno_coherent*': r'^(adreno|gpu)_coherent'}
# The functions that hold an SMC: the families' own, and the report path, which asks PSCI about a released secondary (group G).
SMC_OTHER = {'xnuboot_report'}
SCAN = (('SMC', 0xFFE0001F, 0xD4000003), ('HVC', 0xFFE0001F, 0xD4000002), ('SVC', 0xFFE0001F, 0xD4000001), ('ERET', 0xFFFFFFFF, 0xD69F03E0), ('WFE', 0xFFFFFFFF, 0xD503205F))


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    AUTH.setUpModule()


def tearDownModule():
    AUTH.tearDownModule()
    shutil.rmtree(SCRATCH, ignore_errors=True)


def source(rel):
    """A source file with its white space folded to single spaces, so that a reflow does not fail a pin but a changed word does."""
    return ' '.join((ROOT / rel).read_text().split())


# The SP11 monitor's way to the SSD (docs/SP11-NVME-WINDOW.md section 2): the one call of ans_window_prepare, as ans_glue.c spells it once its white space is folded.
NVME_ADOPT_CALL = ('return nvmeh_sp11_adopt(&nvmeh_direct,ANS_NVMEH_TARGET(res),ANS_NVMEH_RECORD(res),ANS_NVMEH_PARAMS(res), ANS_NVMEH_STATE(res),'
                   'c->params->mem_low,c->params->mem_high);')
NVME_A16_CALL = 'return nvmeh_adopt(&nvmeh_direct,ANS_NVMEH_PARAMS(res),ANS_NVMEH_STATE(res), c->params->mem_low,c->params->mem_high);'
NVME_OLD_REFUSAL = 'ANS_NVMEH_STATE(res)->prepare=(uint64_t)(int64_t)NVMEH_SP11_STATUS_UNWIRED; return NVMEH_SP11_STATUS_UNWIRED;'
# What places the target and record blocks that the call reads (compat/ans.h, probes/nvmesp11.py). The stick profile stays refused by name only while nothing of the stick
# bundle places them: a pair that was never placed is all zero.
PLACEMENT_WORDS = ('ANS_WINDOW_NVMEH_TARGET', 'ANS_WINDOW_NVMEH_RECORD', 'nvmesp11')
STICK_CODE = ('tools/sp11_stick_*.py', 'tools/sp11_m1_*.py', 'probes/sp11-stick-*.c', 'probes/sp11-stick-*.h')


def prepare_branches(glue):
    """{'sp11': the SP11 branch, 'other': the branch of every other profile} of the `#if SNAP_PLATFORM_SP11` in ans_window_prepare, comments taken out and white space folded;
    {} when the function or the conditional is not there."""
    at = glue.find('static int64_t ans_window_prepare(')
    if at < 0:
        return {}
    depth, side, out = 0, None, {'sp11': [], 'other': []}
    for line in re.sub(r'/\*.*?\*/', '', glue[at:], flags=re.S).splitlines():
        word = line.strip()
        if word.startswith('#if'):
            depth += 1
            if depth == 1:
                side = 'sp11' if word == '#if SNAP_PLATFORM_SP11' else None
                continue
        elif word.startswith('#else') and depth == 1:
            side = 'other' if side else None
            continue
        elif word.startswith('#endif'):
            depth -= 1
            if depth == 0:
                break
        if side and word:
            out[side].append(word)
    return {name: ' '.join(words) for name, words in out.items()} if out['sp11'] else {}


def nvme_wiring_problems(glue, host):
    """What the record's nvmeh paragraph says of the SSD path, against the text of probes/xnuboot/ans_glue.c and compat/nvme-host.c: [str]. The SP11 profile adopts the SSD only
    through nvmeh_sp11_adopt, with the two blocks that the host places, and never through nvmeh_adopt; every adoption starts by clearing the state word (the reservation survives a
    warm restart, so an ADOPTED left by an earlier launch must not outlive a refusal); a pair of blocks that was never placed (all zero) is refused by name before anything is
    checked, and a refusal is stored in the state."""
    problems, folded = [], ' '.join(glue.split())
    problems += [f'ans_glue.c no longer has {needle!r}' for needle in ('if (!ans_window(c)) return 0;', '#if SNAP_PLATFORM_SP11') if needle not in folded]
    branches = prepare_branches(glue)
    sp11, other = branches.get('sp11'), branches.get('other')
    if sp11 is None:
        problems.append('ans_window_prepare has no branch for SNAP_PLATFORM_SP11')
    else:
        if NVME_ADOPT_CALL not in sp11:
            problems.append('the SP11 branch does not return nvmeh_sp11_adopt called with the target, the record, the params and the state')
        if re.search(r'\bnvmeh_adopt\s*\(', sp11):
            problems.append('the SP11 branch calls nvmeh_adopt')
        if len(re.findall(r'\breturn\b', sp11)) != 1:
            problems.append('the SP11 branch has a return besides the call of nvmeh_sp11_adopt')
    if not other or not re.search(r'\bnvmeh_adopt\s*\(', other):
        problems.append('the branch of the other profiles no longer calls nvmeh_adopt: this test does not see the code it was written for')
    adopt, unplaced = (re.search(rf'{head}\(.*?\n\}}\n', host, re.S) for head in ('int nvmeh_sp11_adopt', 'static int sp11_unplaced'))
    adopt, unplaced = (' '.join(m.group(0).split()) if m else '' for m in (adopt, unplaced))
    if 's->status = 0; int e = sp11_unplaced(t, r)' not in adopt:
        problems.append('nvmeh_sp11_adopt does not clear the state word before it checks or refuses anything')
    if 'int e = sp11_unplaced(t, r) ? NVMEH_SP11_STATUS_UNWIRED : nvmeh_sp11_check(t, r, p);' not in adopt:
        problems.append('nvmeh_sp11_adopt does not refuse a pair that was never placed by name (UNWIRED) before it checks anything')
    if 'if (e) { s->prepare = (uint64_t)(int64_t)e; return e; }' not in adopt:
        problems.append('nvmeh_sp11_adopt does not store its refusal in the state')
    for letter, block in (('t', 'target'), ('r', 'record')):
        if f'for (i = 0; i < sizeof *{letter}; i++) if ({letter}b[i]) return 0;' not in unplaced:
            problems.append(f'sp11_unplaced does not look at every byte of the {block} block')
    return problems


def stick_code():
    """{path from the tree: text} of the stick bundle's code: its builders, the M1 profile and descriptor tools that make its monitor and PARAMS, and the sources of its stage
    and reporter (STICK_CODE)."""
    return {str(path.relative_to(ROOT)): path.read_text() for pattern in STICK_CODE for path in sorted(ROOT.glob(pattern))}


def placement_problems(files):
    """The files of `files` ({path: text}) that name what places the SSD window's target and record blocks: [str]."""
    return ([] if files else ['no file of the stick bundle was found: the pin looks at nothing']) + [
        f'{path} names {word}' for path, text in sorted(files.items()) for word in PLACEMENT_WORDS if word in text]


def strip_comments(text):
    return re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)


def monitor_sources():
    """{path from the tree: text, comments taken out} of the C files the monitor is built from: its translation unit, the files that unit includes and the shared library."""
    return {str(path.relative_to(ROOT)): strip_comments(path.read_text())
            for pattern in ('probes/xnuboot.c', 'probes/xnuboot/*.c', 'compat/*.c') for path in sorted(ROOT.glob(pattern))}


def ans_reach_problems(glue, monitor):
    """Why the SP11 monitor calls nvmeh_sp11_adopt only for an ANS window, and from one place (`glue` is ans_glue.c, `monitor` is monitor_sources()): [str]. ans_window() is true
    for the DEVICE_ANS_NVME_V1 model of PARAMS whose config[3] is ANS_WINDOW_MODE, and ans_window_prepare returns before the call when it is not. The window is a LIVE read: the
    monitor asks ans_window again when it makes the ANS operations (each ANS access) and at the stop, as the record's nvmeh paragraph says."""
    folded = ' '.join(glue.split())
    problems = [f'ans_glue.c no longer has {needle!r}' for needle in (
        'if (p->models[i].kind==DEVICE_ANS_NVME_V1) return &p->models[i];', 'return m && m->config[3]==ANS_WINDOW_MODE;', 'if (!ans_window(c)) return 0;',
        'int window=ans_window(c);', 'static void ans_window_stop(struct context *c) { if (!ans_window(c)) return;') if needle not in folded]
    problems += [] if 'ans_window_stop(c);' in monitor.get('probes/xnuboot/report.c', '') else ['probes/xnuboot/report.c no longer calls ans_window_stop']
    calls = [path for path, text in sorted(monitor.items()) for _ in re.finditer(r'(?<!int )\bnvmeh_sp11_adopt\s*\(', text)]
    return problems + ([] if calls == ['probes/xnuboot/ans_glue.c'] else [f'nvmeh_sp11_adopt is called from {calls}, not once from ans_glue.c'])


def params_chunks(count=0, models=(), base=None):
    """The PARAMS chunk of a guest as check_launch reads it: struct params, all zero or `base`, with model_count `count` and the (slot, kind, config[3]) of `models`."""
    data = bytearray(bytes(m1.PARAMS_BYTES) if base is None else base)
    data[m1.MODEL_COUNT_AT:m1.MODEL_COUNT_AT + 8] = count.to_bytes(8, 'little')
    for slot, kind, window in models:
        at = m1.MODELS_AT + slot * m1.MODEL_BYTES
        data[at:at + 8] = kind.to_bytes(8, 'little')
        data[at + m1.ANS_WINDOW_AT:at + m1.ANS_WINDOW_AT + 8] = window.to_bytes(8, 'little')
    return [dict(kind=sb.KIND_PARAMS, dest=0x88000C000, data=bytes(data))]


ANS_CASES = {'the ASC model': (1, [(0, 49, 0)]), 'the NVMe model as a window': (1, [(0, 50, 1)]), 'the NVMe model without the window word': (1, [(0, 50, 0)]),
             'both models of the pair': (2, [(0, 49, 0), (1, 50, 1)]), 'a model in the last slot': (128, [(127, 49, 0)]), 'a model in a slot past model_count': (2, [(5, 50, 1)])}


def builder_ans_problems(check):
    """What `check` (check_launch, or a stand-in for it) says to PARAMS that list an ANS model and to PARAMS that do not: [str]."""
    def verdict(*args):
        try:
            check(params_chunks(*args))
        except m1.Refused as refused:
            return refused.name, refused.detail
        return None, ''
    problems = [f'refuses {what}' for what, args in (('PARAMS with no model', ()), ('other kinds (1, 48 and 51)', (3, [(0, 1, 0), (1, 48, 0), (2, 51, 1)])),
                                                      ('a kind whose upper half is not zero', (1, [(0, 49 | 1 << 32, 1)]))) if verdict(*args)[0]]
    problems += [f'does not refuse {what} as PARAMS_ANS (it says {verdict(*args)[0]})' for what, args in ANS_CASES.items() if verdict(*args)[0] != 'PARAMS_ANS']
    problems += [] if 'ANS window' in verdict(*ANS_CASES['the NVMe model as a window'])[1] else ['does not say which model is the window']
    return problems + ([] if verdict(129)[0] == 'BUILT_SHAPE' else ['does not refuse a model_count that struct params has no slots for as BUILT_SHAPE'])


def stand_in(slots=range(m1.MODEL_MAX), kinds=tuple(m1.ANS_KINDS), counted=False, width=8, window_only=False, shape=True, name='PARAMS_ANS'):
    """A check_launch that looks in fewer places than the real one or reads less of a word: the faulty builders of builder_ans_problems."""
    def check(chunks):
        data = chunks[0]['data']
        count = int.from_bytes(data[m1.MODEL_COUNT_AT:m1.MODEL_COUNT_AT + 8], 'little')
        if shape and count > m1.MODEL_MAX:
            raise m1.Refused('BUILT_SHAPE', 'count')
        for slot in slots:
            at = m1.MODELS_AT + slot * m1.MODEL_BYTES
            window = int.from_bytes(data[at + m1.ANS_WINDOW_AT:at + m1.ANS_WINDOW_AT + 8], 'little') == m1.ANS_WINDOW_MODE
            if int.from_bytes(data[at:at + width], 'little') in kinds and (slot < count or not counted) and (window or not window_only):
                raise m1.Refused(name, 'ANS window' if window else 'ANS model')
    return check


def edited(text, old, new):
    """`text` with `old` replaced by `new`: `old` must be there once, so that a faulty case whose source moved fails loudly and does not pass for nothing."""
    assert text.count(old) == 1, f'{old!r} is in the text {text.count(old)} times'
    return text.replace(old, new)


def edited_in(text, head, old, new):
    """`text` with `old` replaced by `new` inside the function that starts at `head` (up to the first line that is a closing brace alone)."""
    start = text.index(head)
    end = text.index('\n}\n', start) + 3
    return text[:start] + edited(text[start:end], old, new) + text[end:]


def edited_call(glue, new):
    """`glue` with the call of nvmeh_sp11_adopt replaced by `new`."""
    text, count = re.subn(r'return nvmeh_sp11_adopt\(.*?\);', lambda _: new, glue, count=1, flags=re.S)
    assert count == 1, 'the call of nvmeh_sp11_adopt is not in the text'
    return text


def function_names(elf):
    """The function symbols of `elf`, an LTO clone suffix (.NNN) taken off."""
    out = subprocess.run([C.find_tool('llvm-nm'), '--defined-only', str(elf)], capture_output=True, text=True, check=True).stdout
    return sorted({re.sub(r'\.\d+$', '', line.split()[2]) for line in out.splitlines() if len(line.split()) == 3 and line.split()[1] in ('t', 'T')})


def device_block(text):
    """Section 3b of the record."""
    start = text.index('3b. Device code')
    return text[start:text.index('\n\n', start)]


def families_of(block):
    """{name: paragraph} of the families that section 3b names: one starts at a line of three spaces, a lower-case name and an opening parenthesis, and goes
    on over the lines of six spaces."""
    found, name = {}, None
    for line in block.splitlines()[1:]:
        head = re.match(r'^ {3}([a-z][a-z_0-9]*) \(', line)
        if head:
            name = head.group(1)
            found[name] = line.strip()
        elif name and line.startswith(' ' * 6):
            found[name] += ' ' + line.strip()
        else:
            name = None
    return found


def family_problems(text, names, named=None):
    """The problems between section 3b of `text` and the function names of a monitor binary: [str]. `named` replaces the families that section 3b names
    ({name: paragraph}), for a record whose paragraphs are cut."""
    problems, block = [], device_block(text)
    named = families_of(block) if named is None else named
    for family, (pattern, words) in FAMILIES.items():
        members = [n for n in names if re.fullmatch(pattern, n)]
        if not members:
            problems.append(f'no function of the {family} family is in the binary: the table of this test is stale')
        elif family not in named:
            problems.append(f'the binary holds {family} code ({members[0]}) and section 3b does not name the family')
        else:
            problems += [f'section 3b names the {family} family without {word}' for word in words if word not in named[family]]
    problems += [f'section 3b names the family {name} that this test does not know' for name in sorted(set(named) - set(FAMILIES))]
    for shown, pattern in ABSENT.items():
        if found := [n for n in names if re.search(pattern, n)]:
            problems.append(f'{found[0]} ({shown}) is in the binary, and section 3b says that this build leaves it out')
    listed = re.search(r'Not in this binary, and the test fails if one appears: (.*?)\. The guest-device', ' '.join(block.split()))
    if not listed or listed.group(1).split(', ') != list(ABSENT):
        problems.append('the list of what the build leaves out in section 3b is not the list that this test looks for')
    return problems


def smc_problems(census):
    """The functions that hold an SMC and that no family of section 3b owns: [str]."""
    owners = set(census['plain'].get('SMC', ()))
    owned = {n for n in owners if any(re.fullmatch(pattern, n) for pattern, _ in FAMILIES.values())} | (owners & SMC_OTHER)
    return [f'{name} holds an SMC and belongs to no family of section 3b' for name in sorted(owners - owned)]


def kernelcache_counts(path):
    """({segment: (words, {class: count})}, the names of the executable segments): every 4-byte word of the two segments, data words included."""
    kc = importlib.import_module('kc-load')
    image = kc.Image(Path(path).read_bytes())
    counts = {}
    for name, _, _, offset, size in image.segments:
        if name in ('__TEXT_EXEC', '__TEXT_BOOT_EXEC'):
            counts[name] = (size // 4, word_counts(struct.unpack_from(f'<{size // 4}I', image.data, offset)))
    return counts, [segment[0] for segment in image.executable_segments]


def word_counts(words):
    return {label: sum(1 for w in words if w & mask == value) for label, mask, value in SCAN}


class Need(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not AUTH.tools_ready() or 'm1' not in STATE:
            raise unittest.SkipTest('no clang, ld.lld, llvm tools or q1n1 checkout (Q1N1_SP11_TREE): the test monitor is not built')
        cls.elf, cls.names, cls.text = STATE['profile'] / 'xnuboot.elf', function_names(STATE['profile'] / 'xnuboot.elf'), STATE['real_text']
        cls.census = C.census(cls.elf)


class Families(Need):
    """D1: every family of device code in the binary is named with its gates; none that the build leaves out is in it."""

    def test_the_record_names_every_family_the_binary_holds_with_its_gates_and_none_it_leaves_out(self):
        self.assertEqual(family_problems(self.text, self.names), [])
        for family, (pattern, _) in FAMILIES.items():
            self.assertGreater(len([n for n in self.names if re.fullmatch(pattern, n)]), 0, family)

    def test_the_names_the_review_lists_are_in_the_record_and_in_the_binary(self):
        block = ' '.join(device_block(self.text).split())
        for name in ('adreno_*', 'gpu_smc', 'fwd_*', 'early_stop_host_call', 'thermal_read', 'nvmeh_*', 'secondary_*'):
            self.assertIn(name, block, name)
        for name in ('gpu_smc', 'nvmeh_direct', 'early_stop_host_tick', 'early_stop_hvc', 'thermal_read'):
            self.assertTrue(name in self.names, f'{name} is a function of the binary')
        self.assertFalse('early_stop_host_call' in self.names, 'early_stop_host_call is inlined into early_stop_host_tick: the record says so')
        listing = subprocess.run([C.find_tool('llvm-objdump'), '-d', '--no-show-raw-insn', '--disassemble-symbols=early_stop_host_tick', str(self.elf)],
                                 capture_output=True, text=True, check=True).stdout
        self.assertRegex(listing, r'\n\s+[0-9a-f]+:\s+blr\s', 'the call through the PARAMS pointer is inside early_stop_host_tick')

    def test_a_record_that_omits_a_family_or_a_gate_is_refused_naming_it(self):
        for family in FAMILIES:
            with self.subTest(family):
                block = device_block(self.text)
                lines, keep = [], True
                for line in block.splitlines():
                    if re.match(rf'^ {{3}}{family} \(', line):
                        keep = False
                    elif re.match(r'^ {3}\S', line):
                        keep = True
                    if keep:
                        lines.append(line)
                problems = family_problems(self.text.replace(block, '\n'.join(lines)), self.names)
                self.assertTrue([p for p in problems if family in p and 'does not name the family' in p], problems)
        for family, (_, words) in FAMILIES.items():
            paragraph = families_of(device_block(self.text))[family]
            for word in words:
                with self.subTest(family=family, gate=word):
                    self.assertIn(word, paragraph)
                    cut = {**families_of(device_block(self.text)), family: paragraph.replace(word, '')}
                    problems = family_problems(self.text, self.names, cut)
                    self.assertTrue([p for p in problems if f'the {family} family without {word}' in p], (word, problems[:3]))

    def test_a_function_of_a_family_the_build_leaves_out_is_refused_in_the_names_and_in_a_binary(self):
        for extra in ('hamoa_poll', 'edp_backlight_set', 'qcc_open', 'i2c_input_scan', 'guest_net_poll', 'cpu_dvfs_tick', 'boot_relocate_wait', 'boot_relay_call',
                      'display_timing_apply', 'adreno_recover_final', 'gpu_recover_hvc', 'adreno_coherent_enable'):
            with self.subTest(extra):
                self.assertTrue([p for p in family_problems(self.text, [*self.names, extra]) if 'leaves it out' in p], extra)
        elf = SCRATCH / 'extra.elf'
        subprocess.run([C.find_tool('llvm-objcopy'), '--add-symbol', 'hamoa_poll=.text:0x10,global,function', str(self.elf), str(elf)], check=True, capture_output=True)
        self.assertTrue([p for p in family_problems(self.text, function_names(elf)) if 'hamoa_poll' in p])

    def test_a_record_whose_list_of_what_is_left_out_differs_is_refused(self):
        edited = self.text.replace('hamoa_*, edp_*, ', 'edp_*, ')
        self.assertNotEqual(edited, self.text)
        self.assertTrue([p for p in family_problems(edited, self.names) if 'list of what the build leaves out' in p])

    def test_every_smc_is_in_a_family_of_section_3b_or_in_the_report_path_and_a_new_one_is_refused(self):
        self.assertEqual(sorted(self.census['plain']['SMC']), ['early_off', 'gpu_smc', 'secondary_power_off', 'secondary_release', 'xnuboot_report'])
        self.assertEqual(smc_problems(self.census), [])
        grown = {'plain': {'SMC': collections.Counter({**self.census['plain']['SMC'], 'vcpu_arch_prepare': 1})}}
        self.assertEqual(smc_problems(grown), ['vcpu_arch_prepare holds an SMC and belongs to no family of section 3b'])

    def test_the_blocks_this_phase_adds_to_the_record_fit_the_columns_of_the_rest(self):
        for what, lines in (('the SMC paragraph', m1t.SMC_LINES), ('section 3b', m1t.DEVICE_LINES), ('the placed entries', [f'      {l}' for l in m1t.PLACED_LINES])):
            self.assertLessEqual(max(map(len, lines)), 120, what)
            self.assertTrue(all(line.isascii() for line in lines), what)
        for line in m1t.SMC_LINES + m1t.DEVICE_LINES:
            self.assertIn(line, self.text.splitlines())


class Sources(unittest.TestCase):
    """D1, D5, D6, D7: the lines of the sources that the text relies on. A changed line fails here, and the record is read again before it is approved."""

    def pins(self, rel, *needles):
        text = source(rel)
        for needle in needles:
            with self.subTest(rel=rel, needle=needle[:60]):
                self.assertIn(needle, text)

    def test_the_live_gates_are_reads_of_params_while_the_guest_runs(self):
        self.pins('probes/xnuboot/trap_dispatch.c', 'if ((iss & 0xffff) == ADRENO_GUEST_HVC && (p->gpu.flags & ADRENO_F_ENABLE)) {',
                  'if (!(iss & 0xffff) && (p->gpu.flags & ADRENO_F_ENABLE)) {', 'int refused = adreno_smc(&o, p->gpu.flags, &c->gpu, r);')
        self.pins('probes/xnuboot/async.c', 'if (which == ASYNC_IRQ && (c->params->gpu.flags & ADRENO_F_ENABLE)) {', 'if (pending == ADRENO_INTID) { gpu_irq(c);',
                  'if (!thermal_stop && !vcpu_of(c)->slot && c->params->gpu.flags) gpu_tick(c, elr);', 'int host_stop=thermal_stop ? 0 : early_stop_host_tick(c,physical);')
        self.pins('compat/early-stop.c', '!(p->flags & EARLY_STOP_F_HOST) || !p->host_call || !p->session || !hz ||', 'if (!p || !(p->flags & EARLY_STOP_F_GUEST)) return EARLY_STOP_E_DISABLED;')
        self.pins('probes/xnuboot/early_stop_thermal.c', '(uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t))(uintptr_t)c->params->early_stop.host_call;',
                  'if (vcpu_of(c)->slot || !early_stop_host_due(s, p, physical, c->params->clock_host_hz))')

    def test_the_private_gates_are_state_in_the_work_area_that_only_the_monitor_sets(self):
        self.pins('probes/xnuboot/gpu_glue.c', 'if (!p->gpu.flags) return 0;', 'int e=adreno_adopt(&o,&g,&c->gpu,p->mem_low,p->mem_high);',
                  'if (!(s->status&ADRENO_S_ADOPTED) || (s->status&ADRENO_S_QUIESCED) || now<s->run_at) return;')
        adreno = source('compat/adreno.c')
        self.assertEqual(adreno.count('s->status |= ADRENO_S_ADOPTED |'), 1, 'one place sets ADOPTED: adreno_adopt')
        body = adreno[adreno.index('int adreno_smc(const struct adreno_ops *o'):adreno.index('static void rwp(')]
        self.assertNotIn('ADOPTED', body, 'adreno_smc has no ADOPTED test: its gate is the caller\'s, the PARAMS flag')
        self.assertIn('!adreno_smc_allowed(fid, r[1], r[2], r[3])', body)
        self.assertIn('o->smc(o->ctx, r)', body)
        self.pins('probes/xnuboot/gpu_glue.c', '__asm__ volatile("smc #0"')
        self.pins('probes/xnuboot/early_stop_thermal.c', 'if(vcpu_of(c)->slot || !c->thermal)return 0;', 'if(vcpu_of(c)->slot || !c->thermal)return -1;')
        self.pins('probes/xnuboot/setup.c', 'if(c->params->early_stop.flags & EARLY_STOP_F_THERMAL) {', 'c->thermal=(struct a16_thermal_state *)c->next_table;',
                  'c->host_cpus=host_cpus;')
        self.pins('probes/xnuboot/cpu_boot.c', 'if (!c->host_cpus || !c->secondary_base || !slot || slot>=6')

    def test_the_gpu_windows_of_stage_2_are_made_once_at_prepare_from_the_flag(self):
        stage2 = source('probes/xnuboot/stage2.c')
        self.assertIn('if (p->gpu.flags & ADRENO_F_ENABLE) { ranges[count++] = (struct stage2_range){ADRENO_PA, ADRENO_PASS0_END,', stage2)
        self.assertEqual(stage2.count('p->gpu.flags & ADRENO_F_ENABLE'), 1, 'one read, in the function that builds the ranges: not a gate that the guest can lift')

    def test_the_gpu_and_sensor_addresses_are_the_ones_the_record_gives(self):
        self.pins('compat/adreno.h', '#define ADRENO_PA 0x03d00000UL', '#define ADRENO_END 0x03df0000UL')
        self.pins('docs/SP11-GPU-A7XX-STUDY.md', 'Same GPU base `0x03d00000`')
        self.pins('compat/a16-thermal.c', 'static uint64_t tm(unsigned b) {return UINT64_C(0x0c22c000)+b*0x1000;}',
                  'static uint64_t srot(unsigned b) {return UINT64_C(0x0c222000)+b*0x1000;}')
        self.pins('compat/hamoa-thermal.c', '#define SROT_BASE UINT64_C(0x0c222000)', '#define TM_BASE UINT64_C(0x0c271000)')

    def test_the_sp11_profile_adopts_the_ssd_only_through_nvmeh_sp11_adopt_never_through_nvmeh_adopt_and_blocks_never_placed_are_refused(self):
        self.pins('probes/xnuboot/ans_glue.c', 'if (!ans_window(c)) return 0;', '#if SNAP_PLATFORM_SP11', NVME_ADOPT_CALL)
        glue, host = (ROOT / 'probes/xnuboot/ans_glue.c').read_text(), (ROOT / 'compat/nvme-host.c').read_text()
        self.assertEqual(nvme_wiring_problems(glue, host), [])
        branches = prepare_branches(glue)
        self.assertIn(NVME_A16_CALL, branches['other'], 'the A16 branch still adopts with nvmeh_adopt: it is the code the A16 monitor runs')
        self.assertNotIn('nvmeh_adopt', branches['sp11'].replace('nvmeh_sp11_adopt', ''), 'the SP11 branch does not name nvmeh_adopt at all')
        head, tail = glue.split('static int64_t ans_window_prepare(', 1)
        faulty = {
            'the old refusal in place of the call': (edited_call(glue, NVME_OLD_REFUSAL), host, 'does not return nvmeh_sp11_adopt'),
            'nvmeh_adopt in place of the call': (edited_call(glue, NVME_A16_CALL), host, 'the SP11 branch calls nvmeh_adopt'),
            'the target and the record swapped': (edited(glue, 'ANS_NVMEH_TARGET(res),ANS_NVMEH_RECORD(res)', 'ANS_NVMEH_RECORD(res),ANS_NVMEH_TARGET(res)'), host,
                                                  'does not return nvmeh_sp11_adopt'),
            'the call and then a second route': (edited_call(glue, NVME_ADOPT_CALL + ' ' + NVME_A16_CALL), host, 'a return besides the call'),
            'no branch for the SP11 profile': (head + 'static int64_t ans_window_prepare(' + edited(tail, '#if SNAP_PLATFORM_SP11', '#if 0'), host, 'has no branch for SNAP_PLATFORM_SP11'),
            'the A16 branch no longer adopts': (edited(glue, 'return nvmeh_adopt(&nvmeh_direct,', 'return nvmeh_other(&nvmeh_direct,'), host,
                                                'the branch of the other profiles no longer calls nvmeh_adopt'),
            'an unplaced pair is not refused by name': (glue, edited(host, 'sp11_unplaced(t, r) ? NVMEH_SP11_STATUS_UNWIRED : nvmeh_sp11_check(t, r, p)', 'nvmeh_sp11_check(t, r, p)'),
                                                        'does not refuse a pair that was never placed by name'),
            'only the target block is looked at': (glue, edited(host, 'for (i = 0; i < sizeof *r; i++) if (rb[i]) return 0;', ''), 'does not look at every byte of the record block'),
            'only the record block is looked at': (glue, edited(host, 'for (i = 0; i < sizeof *t; i++) if (tb[i]) return 0;', ''), 'does not look at every byte of the target block'),
            'a refusal is not stored': (glue, edited_in(host, 'int nvmeh_sp11_adopt', 's->prepare = (uint64_t)(int64_t)e;', ''), 'does not store its refusal in the state'),
            'a stale state word is not cleared': (glue, edited_in(host, 'int nvmeh_sp11_adopt', 's->status = 0;', ''), 'does not clear the state word'),
            'the state word is set, not cleared': (glue, edited_in(host, 'int nvmeh_sp11_adopt', 's->status = 0;', 's->status |= 1;'), 'does not clear the state word'),
        }
        for what, (g, h, expected) in faulty.items():
            with self.subTest(what):
                problems = nvme_wiring_problems(g, h)
                self.assertTrue([p for p in problems if expected in p], problems)
        quoted = edited(glue, 'return nvmeh_sp11_adopt(', '/* never nvmeh_adopt(res), as the A16 branch does */ return nvmeh_sp11_adopt(')
        self.assertEqual(nvme_wiring_problems(quoted, host), [], 'a comment that names nvmeh_adopt( is no call')

    def test_nothing_of_the_stick_bundle_places_the_two_blocks_so_this_profile_stays_refused_by_name(self):
        files = stick_code()
        self.assertEqual(placement_problems(files), [])
        for known in ('tools/sp11_stick_bundle_build.py', 'tools/sp11_stick_bundle_record_m1.py', 'tools/sp11_stick_wrapper_build.py', 'tools/sp11_m1_descriptor.py',
                      'probes/sp11-stick-monitor.c', 'probes/sp11-stick-wrapper.c', 'probes/sp11-stick-gic.h'):
            self.assertIn(known, files, 'the pin looks at the files it is meant to look at')
        for word in PLACEMENT_WORDS:
            for path in ('tools/sp11_stick_bundle_build.py', 'tools/sp11_stick_bundle_record_m1.py', 'probes/sp11-stick-monitor.c'):
                with self.subTest(word=word, path=path):
                    self.assertEqual(placement_problems({**files, path: files[path] + f'\n{word}\n'}), [f'{path} names {word}'])
        self.assertTrue(placement_problems({}), 'a pin that finds no file is refused')
        for word, rel in (('ANS_WINDOW_NVMEH_TARGET', 'compat/ans.h'), ('ANS_WINDOW_NVMEH_RECORD', 'compat/ans.h'), ('nvmesp11', 'docs/SP11-NVME-WINDOW.md')):
            self.assertIn(word, (ROOT / rel).read_text(), f'the pin names what {rel} names')
        self.assertTrue((ROOT / 'probes/nvmesp11.py').is_file(), 'the host library that places the blocks')

    def test_the_call_of_nvmeh_sp11_adopt_is_reached_only_for_an_ans_window_and_the_builder_refuses_every_ans_model_so_no_bundle_reaches_it(self):
        glue_path, trap_path, report_path = 'probes/xnuboot/ans_glue.c', 'probes/xnuboot/trap_dispatch.c', 'probes/xnuboot/report.c'
        glue, monitor = (ROOT / glue_path).read_text(), monitor_sources()
        self.assertEqual(ans_reach_problems(glue, monitor), [])
        self.assertEqual(builder_ans_problems(m1.check_launch), [])
        faulty_reach = {
            'a window that needs no window word': (edited(glue, 'return m && m->config[3]==ANS_WINDOW_MODE;', 'return m != 0;'), monitor, 'config[3]==ANS_WINDOW_MODE'),
            'the ASC model is taken for the window model': (edited(glue, 'if (p->models[i].kind==DEVICE_ANS_NVME_V1) return &p->models[i];',
                                                                   'if (p->models[i].kind==DEVICE_ANS_ASC_V1) return &p->models[i];'), monitor, 'DEVICE_ANS_NVME_V1'),
            'a launch that does not return when there is no window': (edited(glue, 'if (!ans_window(c)) return 0;', ''), monitor, 'if (!ans_window(c)) return 0;'),
            'ANS operations made without asking for a window': (edited(glue, 'int window=ans_window(c);', 'int window=0;'), monitor, 'int window=ans_window(c);'),
            'a stop that does not ask for a window': (edited(glue, 'static void ans_window_stop(struct context *c)\n{\n    if (!ans_window(c)) return;', 'static void ans_window_stop(struct context *c)\n{\n'),
                                                      monitor, 'ans_window_stop(struct context *c)'),
            'a report that does not stop the window': (glue, {**monitor, report_path: monitor[report_path].replace('ans_window_stop(c);', '')}, 'no longer calls ans_window_stop'),
            'a second call, from the trap handler': (glue, {**monitor, trap_path: monitor[trap_path] + '\nint again(void) { return nvmeh_sp11_adopt(0, 0, 0, 0, 0, 0, 0); }'}, 'is called from'),
            'no call at all': (glue, {**monitor, glue_path: monitor[glue_path].replace('nvmeh_sp11_adopt', 'nvmeh_adopt')}, 'is called from []'),
        }
        for what, (g, mon, expected) in faulty_reach.items():
            with self.subTest(what):
                problems = ans_reach_problems(g, mon)
                self.assertTrue([p for p in problems if expected in p], problems)
        faulty_builder = {
            'a check that does not look at the models (the one before this change)': (stand_in(slots=()), 'does not refuse the ASC model'),
            'a check for the NVMe model only': (stand_in(kinds=(50,)), 'does not refuse the ASC model'),
            'a check that looks at the first slot only': (stand_in(slots=[0]), 'does not refuse a model in the last slot'),
            'a check that looks at the counted slots only': (stand_in(counted=True), 'does not refuse a model in a slot past model_count'),
            'a check that refuses the window word and nothing else': (stand_in(window_only=True), 'does not refuse the NVMe model without the window word'),
            'a check that reads half of the kind': (stand_in(width=4), 'refuses a kind whose upper half is not zero'),
            'a check that has no shape refusal': (stand_in(shape=False), 'does not refuse a model_count that struct params has no slots for'),
            'a check that is another refusal': (stand_in(name='PARAMS_LAUNCH'), 'does not refuse the ASC model as PARAMS_ANS'),
        }
        for what, (check, expected) in faulty_builder.items():
            with self.subTest(what):
                problems = builder_ans_problems(check)
                self.assertTrue([p for p in problems if expected in p], problems)
        words = bytearray(params_chunks(1, [(0, 50, 1)])[0]['data'])
        words[0x2340] = 1                                  # gpu.flags: the refusal that was there first keeps its place
        with self.assertRaises(ch.Refused) as caught:
            m1.check_launch(params_chunks(1, [(0, 50, 1)], bytes(words)))
        self.assertEqual(caught.exception.name, 'PARAMS_LAUNCH')

    def test_hcr_el2_starts_without_tsc_and_no_write_sets_it_and_no_handler_takes_an_smc_from_the_guest(self):
        entry = (ROOT / 'probes/xnuboot.S').read_text()
        start = entry.index('mov x8, #(1 << 31)')
        first = entry[start:entry.index('msr hcr_el2, x8', start)]
        bits = [int(b) for b in re.findall(r'#\(1 << (\d+)\)', first)] + [0]
        self.assertEqual(sorted(bits), [0, 14, 18, 20, 26, 30, 31], 'RW, TRVM, TVM, TIDCP, TID3, TWE and VM, as the record says')
        self.assertEqual(sum(1 << b for b in bits), 0xC4144001)
        self.assertNotIn(19, bits, 'TSC is bit 19')
        for rel in ('probes/xnuboot.S', *sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'probes/xnuboot').glob('*.c')), 'probes/secondary.S', 'probes/shadowmmu.S',
                    'probes/tid3.S', 'probes/sptmentry.S', 'probes/nativecalls.S'):
            self.assertNotRegex((ROOT / rel).read_text(), r'\bTSC\b', rel)
        trap = source('probes/xnuboot/trap_dispatch.c')
        self.assertIn('if (ec == 0x16) {', trap, 'the HVC handler is where the check looks')
        self.assertNotRegex(trap, r'ec\s*==\s*0x17|case 0x17', 'no handler for an SMC from the guest')


class Interrupts(Need):
    """D7: the monitor acknowledges INTID 26 and, behind a LIVE gate, INTID 0x14C; anything else ends the run unacknowledged."""

    def test_the_boot_cpu_reads_the_acknowledge_register_in_two_places_and_a_secondary_in_one(self):
        self.assertEqual(dict(self.census['mrs']['ICC_IAR1_EL1']), {'xnuboot_async': 2, 'secondary_gic_stop': 1})
        self.assertNotIn('ICC_IAR0_EL1', self.census['mrs'])

    def test_the_two_acknowledges_are_the_two_cases_the_record_names_and_everything_else_stops_unacknowledged(self):
        async_c = source('probes/xnuboot/async.c')
        for needle in ('if (which == ASYNC_IRQ && (ctl & CNTHP_ISTATUS) && (int64_t)(run_deadline(c) - physical) > 0) {', 'if (pending == 26) {',
                       'mrs %0, icc_iar1_el1; dsb sy', 'if (intid == 26) {', 'if (intid < 1020) timer_eoi(intid);', 'record(c, EV_WATCHDOG, elr, esr, far, el1);',
                       'watchdog_off(c); return 1; }'):
            self.assertIn(needle, async_c, needle)
        self.assertEqual(async_c.count('icc_iar1_el1'), 1, 'one acknowledge in the locked handler, besides the GPU interrupt\'s in gpu_glue.c')
        self.assertEqual(source('probes/xnuboot/gpu_glue.c').count('icc_iar1_el1'), 1)
        flat = ' '.join(self.text.split())
        gic_class = flat[flat.index('gic: memory-mapped GIC registers'):flat.index('The arm half since pass 5')]
        for needle in ('and, on the boot CPU, acknowledges an interrupt in two cases only: INTID 26 (the timer) when its ISTATUS is set and the run\'s deadline is ahead, and INTID 0x14C when gpu.flags '
                       'has its enable bit (a LIVE gate)', 'Any other IRQ or FIQ, and the timer at the deadline, ends the run with the interrupt not acknowledged',
                       'a foreign interrupt stays pending (INFERRED from probes/xnuboot/async.c)'):
            self.assertIn(needle, gic_class, 'the monitor\'s part of the gic class, kept when Phase E described the arm half')
        for gone in ('acknowledges and ends whatever interrupt reaches EL2', 'the firmware-enabled PPIs among them (INFERRED); any interrupt but PPI 26 stops the run',
                     'The monitor writes no memory-mapped GIC register'):
            self.assertNotIn(gone, flat)


class Thermal(Need):
    """D6: the monitor's thermal code only loads, from the A16's sensor addresses, behind a private gate."""

    def test_the_section_4_text_names_the_gate_and_what_is_known_and_unknown_of_the_addresses(self):
        flat = ' '.join(self.text.split())
        for needle in ('only loads, from the A16\'s sensor addresses: SROT at 0x0c222000 + n * 0x1000, which compat/hamoa-thermal.c gives as this machine\'s TSENS SROT (INFERRED there)',
                       'and TM at 0x0c22c000 + n * 0x1000, where this machine\'s is UNKNOWN', 'It is closed by c->thermal, which the monitor\'s setup (probes/xnuboot/setup.c) makes only if '
                       'PARAMS early_stop.flags has the thermal bit at that moment', 'nothing makes c->thermal later (a PRIVATE gate, section 3b)',
                       'compat/hamoa-thermal.c is not in the monitor: its binary has no hamoa_ symbol'):
            self.assertIn(needle, flat)
        for gone in ('at addresses that are not this machine\'s', 'is linked and no caller was found', 'reachable only through PARAMS early_stop.flags'):
            self.assertNotIn(gone, flat)

    def test_the_binary_has_no_hamoa_code_and_only_the_a16_reads(self):
        self.assertEqual([n for n in self.names if n.startswith('hamoa_')], [])
        self.assertTrue([n for n in self.names if n.startswith('a16_thermal_')])
        for name in ('thermal_tick', 'thermal_admit'):
            self.assertTrue(name in self.names, name)


class Other(unittest.TestCase):
    """The texts of the other kinds keep what they said, and D5's paragraph is this kind's."""

    def test_the_dry_run_and_gic_texts_are_untouched_by_this_phase(self):
        self.assertIn('No disk, no device, no RAM outside the placement', ' '.join(rec.TOUCHES['ram']))
        self.assertIn('calls no monitor', ' '.join(rec.GIC_STOP_TOUCHES['el2_sysreg']))
        for kind, text in (('dry run', BT.template(stores='ram,el2_sysreg')[1]), ('gic and stop', BT.cli(['authorisation-template', 'synthetic',
                                                                                                         *BT.opts(stores='ram,el2_sysreg,gic'), '--gic-stop'])[1])):
            for line in (*m1t.SMC_LINES, *m1t.DEVICE_LINES, m1t.GIC_FACT_LINES[0], *(f'      {l}' for l in m1t.GIC_LINES[1:4])):
                self.assertNotIn(line, text, kind)
            self.assertNotIn('3b. Device code', text)

    def test_the_smc_paragraph_carries_the_scan_and_labels_what_it_is(self):
        flat = ' '.join(' '.join(m1t.SMC_LINES).split())
        for needle in ('an SMC that the guest executes is not trapped', 'TSC clear', 'EC 0x17', '(INFERRED)', '(read on the host (a file, not a run), one kernelcache;', 'hold 0 SMC and 0 HVC words',
                       'Whether the guest runs SMC code from anywhere else is UNKNOWN', '__TEXT_EXEC (14,774,272 words) and __TEXT_BOOT_EXEC (8,192)',
                       'in the two segments together, 1 SVC, 1 ERET and 148 WFE words, 146 of them in __TEXT_EXEC'):
            self.assertIn(needle, flat)
        self.assertNotIn('OBSERVED on a host file', flat, 'a file read on the host is not OBSERVED (a run on the unit is)')
        self.assertNotIn('and 146 WFE words, so it does', flat, 'the 146 are the count of one segment')
        self.assertNotIn('VERIFIED', flat)


class KernelcacheScan(unittest.TestCase):
    """D5: the scan that the paragraph cites, run again on the local kernelcache, and on words that hold an SMC to show that it finds one."""

    def test_the_scanner_counts_what_it_is_given(self):
        words = [0xD4000003, 0xD4000003 | (7 << 5), 0xD4000002, 0xD4000001, 0xD69F03E0, 0xD503205F, 0xD503201F, 0x12345678, 0]
        self.assertEqual(word_counts(words), {'SMC': 2, 'HVC': 1, 'SVC': 1, 'ERET': 1, 'WFE': 1})
        self.assertEqual(word_counts([0xD503201F] * 100), {'SMC': 0, 'HVC': 0, 'SVC': 0, 'ERET': 0, 'WFE': 0})

    def test_the_local_kernelcache_has_no_smc_and_no_hvc_word_in_its_executable_segments(self):
        try:
            path = BT.bb.xnuboot().DEFAULT_KERNELCACHE
            present = path.is_file()
        except Exception:       # noqa: BLE001  a broken checkout skips the test
            present = False
        if not present:
            self.skipTest('no J700 26A428 kernelcache under downloads/extracted/j700ap (git-ignored Apple material): the scan NOT RUN')
        counts, executable = kernelcache_counts(path)
        self.assertEqual(executable, ['__TEXT_EXEC', '__TEXT_BOOT_EXEC'], 'the only two segments with execute permission')
        self.assertEqual({k: (v[0], v[1]['SMC'], v[1]['HVC']) for k, v in counts.items()}, {'__TEXT_EXEC': (14774272, 0, 0), '__TEXT_BOOT_EXEC': (8192, 0, 0)})
        self.assertEqual({k: v for k, v in counts['__TEXT_EXEC'][1].items() if k not in ('SMC', 'HVC')}, {'SVC': 1, 'ERET': 1, 'WFE': 146}, 'the control: it finds instructions')
        self.assertEqual({k: sum(v[1][k] for v in counts.values()) for k in ('SVC', 'ERET', 'WFE')}, {'SVC': 1, 'ERET': 1, 'WFE': 148}, 'the two segments together: the text says 148')


class Hygiene(unittest.TestCase):
    def test_the_files_of_this_phase_are_ascii_with_an_spdx_line_within_the_size_limits_and_hold_no_machine_data(self):
        for rel in ('tools/sp11_stick_bundle_record_m1.py', 'tools/test-sp11-stick-monitor-census.py', 'tools/test-sp11-stick-monitor-builder.py',
                    'tools/test-sp11-stick-monitor-devices.py'):
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                self.assertLess(len(data), 51200)
                self.assertLess(data.count(b'\n'), 1500)
                for needle in BT.MACHINE_PATHS:
                    self.assertNotIn(needle.encode(), data)


if __name__ == '__main__':
    unittest.main()
