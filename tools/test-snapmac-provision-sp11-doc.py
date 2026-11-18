#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""docs/SP11-STORAGE-PROVISIONING.md against the tools and the probe it plans: what the document says must be what the code does.

  python3 tools/test-snapmac-provision-sp11-doc.py

The document is read as text. Its tables and lists are parsed and compared with the provisioning tools (subcommands, options, formats,
refusal classes and messages, the authorisation record, the window numbers, the exit status), with the planner's constants, with
Appendix D (D1 to D16 each classified), and with the probe's register order (against the q1n1 fork's own document when Q1N1_SP11_TREE
names it; otherwise that one comparison is a SKIP). Every check has seeded mistakes in the document that it must find. Nothing here
runs a tool on a disk. Evidence: EXPERIMENTAL (host only); hardware NOT RUN.
"""
import ast
import os
from pathlib import Path
import re
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import snapmac_image as si  # noqa: E402
import snapmac_provision as sp  # noqa: E402
import snapmac_window as sw  # noqa: E402

DOC = ROOT / 'docs' / 'SP11-STORAGE-PROVISIONING.md'
LABELS = ('OBSERVED', 'VERIFIED', 'INFERRED', 'UNKNOWN', 'UNIMPLEMENTED', 'EXPERIMENTAL')
# The register list and order of the probe's SMMU group (the q1n1 fork's payloads/smmuprobe.c and its document).
REGISTERS = [('IDR0', '0x00'), ('IDR1', '0x04'), ('IDR2', '0x08'), ('IDR3', '0x0c'), ('IDR4', '0x10'), ('IDR5', '0x14'), ('IIDR', '0x18'),
             ('AIDR', '0x1c'), ('CR0', '0x20'), ('CR0ACK', '0x24'), ('GBPA', '0x44'), ('GERROR', '0x60'), ('STRTAB_BASE', '0x80'),
             ('STRTAB_CFG', '0x88')]
MACHINE = re.compile('|'.join(['/Us' + 'ers/', '/ho' + 'me/']) + r'|\.loc' + r'al\b|\bsae' + r'ed\b|host' + r'name|\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b', re.I)


def section(text, title):
    """The text of the `## ` section whose heading starts with `title`, up to the next `## ` heading."""
    match = re.search(r'^## ' + re.escape(title) + r'.*?$', text, re.M)
    if not match:
        return ''
    end = re.search(r'^## ', text[match.end():], re.M)
    return text[match.start():match.end() + (end.start() if end else len(text))]


def table(text):
    """The rows of the first markdown table in `text`, each a list of stripped cells; the header and the rule are dropped."""
    rows = []
    for line in text.splitlines():
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if not all(re.fullmatch(r'-+', c) for c in cells):
                rows.append(cells)
        elif rows:
            break
    return rows[1:]


def ticks(cell):
    return re.findall(r'`([^`]+)`', cell)


def slug(heading):
    s = re.sub(r'[^a-z0-9 _-]', '', heading.lower())
    return s.replace(' ', '-')


def anchors(path):
    return {slug(m.group(2)) for m in re.finditer(r'^(#{1,6}) (.*?)\s*$', path.read_text(encoding='utf-8'), re.M)}


class Facts:
    """What the code says, read once: subcommands and their options from the argparse calls, and the constants."""

    def __init__(self):
        tree = ast.parse((HERE / 'snapmac_provision.py').read_text(encoding='utf-8'))
        main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'main')
        self.options, current = {}, None
        for node in sorted((n for n in ast.walk(main) if isinstance(n, (ast.Assign, ast.Expr))), key=lambda n: n.lineno):
            call = node.value
            if isinstance(node, ast.Assign) and isinstance(call, ast.Call) and ast.unparse(call.func) == 'sub.add_parser':
                current = call.args[0].value
                self.options[current] = set()
            elif isinstance(call, ast.Call) and ast.unparse(call.func) == 's.add_argument' and current:
                self.options[current].add(call.args[0].value)
        self.constants = dict(chunk=si.CHUNK, header_offset=sw.HEADER_OFFSET, isc_offset=sw.ISC_OFFSET, isc_region=sw.DEFAULT_ISC_REGION,
                              virtual_block=sw.VIRTUAL_BLOCK, auth_minutes=sp.AUTH_MAX_SECONDS // 60, min_readers=sp.MIN_READERS)
        pm = sp.planner()
        self.planner = dict(align=pm.ALIGN_BYTES, guard=pm.GUARD_BYTES, min_bytes=pm.SP11_MIN_BYTES, reader_format=pm.READER_FORMAT)
        self.sources = {p.name: p.read_text(encoding='utf-8') for p in (HERE / 'snapmac_image.py', HERE / 'snapmac_window.py', HERE / 'snapmac_provision.py')}
        self.classes = {n.name for t in map(ast.parse, self.sources.values()) for n in ast.walk(t) if isinstance(n, ast.ClassDef)}


def fragments_in_code(facts, fragment):
    return any(fragment in text for text in facts.sources.values())


def check(text, facts):
    """Every way `text` disagrees with the tools; [] means it agrees."""
    bad = []

    def need(condition, message):
        if not condition:
            bad.append(message)

    head = text[:1200]
    need('EXPERIMENTAL' in head and 'Hardware status: NOT RUN' in head, 'the status line does not say EXPERIMENTAL and NOT RUN')
    need(not re.search(r'[^\x00-\x7f]', text), 'the document is not ASCII')
    need(not MACHINE.search(text), 'the document names a machine, a user or an address')
    need(len(text.encode()) < 50 * 1024 and len(text.splitlines()) < 1500, 'the document is over its size limit')

    # the tools table and the commands
    tools = table(section(text, '3. The tools'))
    named = [ticks(r[0])[0] for r in tools if ticks(r[0])]
    need(sorted(named) == sorted(facts.options), f'section 3 lists {sorted(named)}, the tool has {sorted(facts.options)}')
    for line in re.findall(r'^python3 tools/snapmac_provision\.py (\S+)(.*)$', text, re.M):
        sub, rest = line
        if sub not in facts.options:
            bad.append(f'the commands show an unknown subcommand {sub}')
            continue
        for option in re.findall(r'--[a-z-]+', rest):
            need(option in facts.options[sub], f'{sub} has no option {option}')
    shown = {m.group(1) for m in re.finditer(r'^python3 tools/snapmac_provision\.py (\S+)', text, re.M)}
    need(shown == set(facts.options), f'the commands show {sorted(shown)}')

    # formats
    formats = set(re.findall(r'snapmac-[a-z-]+/\d+', text))
    mine = {sp.PLAN_FORMAT, sp.PREIMAGE_FORMAT, sp.JOURNAL_FORMAT, sp.AUTH_FORMAT, sp.WINDOW_MANIFEST_FORMAT}
    need(formats <= mine | {facts.planner['reader_format']}, f'the document names formats the tools do not have: {sorted(formats - mine)}')
    for fmt in sorted(mine):
        need(fmt in text, f'{fmt} is not in the document')

    # the exit status and the numbers
    status = re.search(r'Exit status: (.*?)\.', ast.get_docstring(ast.parse(facts.sources['snapmac_provision.py']))).group(1)
    need(f'Exit status: {status}.' in text, 'the exit status of the document is not the tool\'s')
    c = facts.constants
    need(re.findall(r'chunks of (\d+) MiB', text) == [str(c['chunk'] >> 20)], 'the chunk size is not the tool\'s')
    need(re.findall(r'oversized \(over (\d+) MiB\)', text) == [str(c['chunk'] >> 20)], 'the largest write is not the tool\'s')
    need(re.findall(r'header sits on media at \+(\d+) KiB', text) == [str(c['header_offset'] >> 10)], 'the header offset is not the tool\'s')
    need(re.findall(r'\| \+(\d+) MiB, R bytes', text) == [str(c['isc_offset'] >> 20)], 'the ISC offset is not the tool\'s')
    need(re.findall(r'(\d+) MiB by default', text) == [str(c['isc_region'] >> 20)], 'the default ISC region is not the tool\'s')
    need(re.findall(r'virtual block size \((\d+)\)', text) == [str(c['virtual_block'])], 'the virtual block size is not the tool\'s')
    need(re.findall(r'for at most (\d+) minutes', text) == [str(c['auth_minutes'])], 'the record validity is not the tool\'s')
    need(re.findall(r'needs at least (\d+) readers', text) == [str(c['min_readers'])], 'the reader count is not the gate\'s')
    need(re.findall(r'(\d+) GiB by default', text) == [str(facts.planner['min_bytes'] >> 30)], 'the planner minimum is not the planner\'s')
    need(f'multiples of {facts.planner["align"] >> 10} KiB' in text, 'the alignment is not the planner\'s')
    need('DEVICE_MODE_ENABLED = False' in text and sp.DEVICE_MODE_ENABLED is False, 'the document and the tool disagree on the device mode')

    # the refusals
    for row in table(section(text, '5. What is refused')):
        classes, quotes = [t for t in ticks(row[1]) if re.fullmatch(r'[A-Z][A-Za-z]+', t)], re.findall(r'"([^"]+)"', row[1])
        for name in classes:
            need(name in facts.classes, f'section 5 names {name}, which is not a class of the tools')
        for quote in quotes:
            need(fragments_in_code(facts, quote), f'section 5 quotes "{quote}", which no tool says')
        need(bool(classes), f'section 5 row without a class: {row[0][:40]}')

    # the authorisation record
    record = table(section(text, '6. The authorised device mode'))
    fields = {t for r in record for t in ticks(r[0])}
    need(fields == set(sp.AUTH_KEYS), f'section 6 lists the fields {sorted(fields)}')
    acks = next(([t for t in ticks(r[1]) if t != 'true'] for r in record if r[0] == '`acknowledged`'), [])
    need(acks == list(sp.ACKNOWLEDGEMENTS), 'section 6 lists the acknowledgements differently from the tool')
    need(re.findall(r'(\d+) to (\d+) characters of', text) == [('8', '64')], 'the run id length is not the tool\'s')

    # Appendix D
    rows = table(section(text, '8. Appendix D'))
    need([r[0] for r in rows] == [f'D{n}' for n in range(1, 17)], f'Appendix D lists {[r[0] for r in rows]}')
    for r in rows:
        need(len(r) == 6 and all(r), f'Appendix D row {r[0]} has an empty cell')
        need(len(r) == 6 and any(label in r[5] for label in LABELS), f'Appendix D row {r[0]} has no label in its Today cell')
        need(len(r) == 6 and bool(re.search(r'\byes\b|\bno\b|VERIFIED|host', r[2])), f'Appendix D row {r[0]} does not say whether it is offline')
    # what boot 22 (session 1, 2026-11-18) changed in the Today column: four questions read in two boots, the SMMU and the DMA reach still UNKNOWN
    today = {r[0]: r[5] for r in rows if len(r) == 6}
    for question in ('D3', 'D4', 'D8', 'D15'):
        need('OBSERVED in two boots' in today.get(question, ''), f'Appendix D row {question} does not say OBSERVED in two boots')
    need(today.get('D2', '').startswith('OBSERVED once'), 'Appendix D row D2 is not OBSERVED once (the segment comes from the MCFG: the page prints none)')
    need(today.get('D7', '').startswith('UNKNOWN (session 1 ran') and today.get('D14') == 'UNKNOWN', 'Appendix D rows D7 and D14 are not UNKNOWN after session 1')

    # the sessions
    body = section(text, '9. The first three storage hardware sessions')
    starts = [m.start() for m in re.finditer(r'^\*\*Session (\d):', body, re.M)]
    need([body[s + 10] for s in starts] == ['1', '2', '3'], 'section 9 does not have sessions 1, 2, 3 in order')
    for n, s in enumerate(starts):
        block = body[s:starts[n + 1] if n + 1 < len(starts) else len(body)]
        for label in ('- Operator:', '- Written:', '- Stop:', '- Closes:'):
            need(label in block, f'session {n + 1} has no "{label}"')
    flat = body.replace('**', '').replace('\n', ' ')
    need('nothing is written to the SSD in any of the three' in flat, 'section 9 does not say that nothing is written to the SSD')
    # what session 1 did on 2026-11-18 (boot 22): it ran, read only, and left the SMMU's mode and the DMA reach UNKNOWN
    result = re.search(r'\*\*Result of session 1 \(boot 22, 2026-11-18;.*?(?=\n\*\*Session 2:)', body, re.S)
    need(result is not None, 'section 9 has no result paragraph of session 1 before session 2')
    said = ' '.join(result.group(0).split()) if result else ''
    for phrase in ('OBSERVED, one unit, one boot, from a film that started late', 'a chain of two read-only payloads, `nvmesnap` and then `smmuprobe`', 'both ended with no fault and no refused read',
                   'the registers equal the X2 image\'s reading of 2026-11-15, so the state the firmware leaves repeats across two boots',
                   'got a translation fault at level 3: the page has no stage-1 mapping at EL2, no SMMU register was read, and the verdict is `UNKNOWN` with `SMMU_PAGE_UNMAPPED`; it is not treated as bypass',
                   '**Not settled:** the SMMU\'s mode (D7), why its page is unmapped, whether the SSD\'s DMA reaches a monitor-owned extent after a takeover (D14), anything about writes, D11, and anything about the Wi-Fi '
                   'beyond the shared SMMU.', 'The approval is spent.', 'session 2\'s prerequisite, a verdict of bypass or a proven stage 2, is not met'):
        need(phrase in said, f'the result of session 1 lost {phrase[:50]!r}')
    need('VERIFIED' not in said and not re.search(r'MODE=(?!UNKNOWN)', said), 'the result of session 1 upgrades a label or gives the SMMU a mode')
    need('session 1, a read-only survey, ran once on 2026-11-18 (section 9)' in ' '.join(head.split()), 'the status line does not say that session 1 ran once, read only')
    registers = re.search(r'<!-- storage-registers -->\n(.*?)<!-- /storage-registers -->', body, re.S)
    got = [(ticks(r[1])[0], ticks(r[2])[0]) for r in table(registers.group(1))] if registers else []
    need(got == REGISTERS, f'the register order is {got}')

    # links: every relative link resolves to a file and, if it has one, to a heading
    for target, frag in re.findall(r'\]\(([^)#]+\.md)(?:#([^)]+))?\)', text):
        path = (DOC.parent / target).resolve()
        if not path.exists():
            bad.append(f'the link {target} does not exist')
        elif frag and frag not in anchors(path):
            bad.append(f'the link {target}#{frag} has no heading')
    for command in re.findall(r'^python3 (tools/test-[a-z0-9-]+\.py)', text, re.M):
        need((ROOT / command).exists(), f'{command} does not exist')
    return bad


def q1n1_registers():
    tree = os.environ.get('Q1N1_SP11_TREE')
    if not tree or not (Path(tree) / 'docs' / 'SP11-PAYLOAD-SMMUPROBE.md').exists():
        return None
    block = re.search(r'<!-- smmuprobe-registers -->\n(.*?)<!-- /smmuprobe-registers -->', (Path(tree) / 'docs' / 'SP11-PAYLOAD-SMMUPROBE.md').read_text(), re.S)
    return [(ticks(r[1])[0], ticks(r[2])[0]) for r in table(block.group(1))]


FACTS = Facts()
TEXT = DOC.read_text(encoding='utf-8')


def seeded(old, new):
    assert TEXT.count(old) == 1, f'the needle {old!r} must occur once'
    return TEXT.replace(old, new, 1)


class StorageDoc(unittest.TestCase):
    def test_the_document_agrees_with_the_tools(self):
        self.assertEqual(check(TEXT, FACTS), [])

    def test_the_register_order_is_the_probes_own(self):
        theirs = q1n1_registers()
        if theirs is None:
            self.skipTest('Q1N1_SP11_TREE does not name a q1n1 tree with docs/SP11-PAYLOAD-SMMUPROBE.md')
        self.assertEqual(theirs, REGISTERS)

    def test_the_commands_in_the_document_parse(self):
        for sub, rest in re.findall(r'^python3 tools/snapmac_provision\.py (\S+)(.*)$', TEXT, re.M):
            self.assertIn(sub, FACTS.options)
            for option in re.findall(r'--[a-z-]+', rest):
                self.assertIn(option, FACTS.options[sub])

    def test_the_facts_come_from_the_tools(self):
        self.assertEqual(sorted(FACTS.options), ['auth-check', 'plan', 'post-check', 'pre-image', 'verify-undo', 'write'])
        self.assertIn('--confirm-image', FACTS.options['write'])
        self.assertIn('--device', FACTS.options['auth-check'])

    def test_each_seeded_mistake_is_found(self):
        mistakes = {
            'a deleted Appendix D row': seeded('| D7 | translating, bypass or abort |', '| D8 | translating, bypass or abort |'),
            'a duplicated Appendix D number': seeded('| D3 | BDF |', '| D2 | BDF |'),
            'an Appendix D row without a label': seeded('| none | OBSERVED once: 6 |', '| none | six |'),
            'a swapped register offset': seeded('| 2 | `IDR1` | `0x04` |', '| 2 | `IDR1` | `0x08` |'),
            'a swapped register order': seeded('| 11 | `GBPA` | `0x44` |\n| 12 | `GERROR` | `0x60` |', '| 11 | `GERROR` | `0x60` |\n| 12 | `GBPA` | `0x44` |'),
            'a dropped register': seeded('| 14 | `STRTAB_CFG` | `0x88` |\n', ''),
            'a subcommand dropped from section 3': seeded('| `auth-check` | a record, the plan, the pre-image |', '| `auth-test` | a record, the plan, the pre-image |'),
            'an option the tool does not have': seeded('--confirm-image sp11.img --out journal.json', '--confirm sp11.img --out journal.json'),
            'a write option the tool does not have': seeded('--pre-image preimage.json --before before.json --after r1.json', '--pre-image preimage.json --survey before.json --after r1.json'),
            'a rule message no tool says': seeded('"Windows type"', '"Windows kind"'),
            'another format': seeded('`snapmac-preimage/1`', '`snapmac-preimage/2`'),
            'another exit status': seeded('Exit status: 0 ok, 1 a check failed, 2 refused.', 'Exit status: 0 ok, 1 refused, 2 a check failed.'),
            'another chunk': seeded('chunks of 8 MiB', 'chunks of 4 MiB'),
            'another largest write': seeded('oversized (over 8 MiB)', 'oversized (over 16 MiB)'),
            'another header offset': seeded('header sits on media at +512 KiB', 'header sits on media at +256 KiB'),
            'another default ISC region': seeded('(512 MiB by default, as on the A16; a parameter)', '(256 MiB by default, as on the A16; a parameter)'),
            'another validity': seeded('valid now, for at most 30 minutes', 'valid now, for at most 60 minutes'),
            'another run id length': seeded('8 to 64 characters of', '4 to 64 characters of'),
            'another reader count': seeded('needs at least 4 readers', 'needs at least 3 readers'),
            'another planner minimum': seeded('128 GiB by default', '64 GiB by default'),
            'the device mode switched on in the text': seeded('`DEVICE_MODE_ENABLED = False`', '`DEVICE_MODE_ENABLED = True`'),
            'a renamed class': seeded('`StalePreimage`: "stale pre-image"', '`StaleImage`: "stale pre-image"'),
            'a message no tool says': seeded('"never overwritten"', '"never replaced"'),
            'a renamed acknowledgement': seeded('`backup_verified`', '`backup_checked`'),
            'a renamed record field': seeded('`tool_source_sha256`', '`tool_sha256`'),
            'a session without its stop rule': seeded('- Stop: any named refusal of the contract', '- Halt: any named refusal of the contract'),
            'the sessions out of order': seeded('**Session 2: the controller', '**Session 3: the controller'),
            'a broken link': seeded('FULL-INSTALL.md#partition-plan', 'FULL-INSTALL.md#partition-plans'),
            'a missing document': seeded('(SP11-NVME-WINDOW.md#7-appendix-d-answered-the-per-machine-questions)', '(SP11-NVME-WINDOW-OLD.md#7)'),
            'a test that does not exist': seeded('python3 tools/test-snapmac-image-sp11.py ', 'python3 tools/test-snapmac-images-sp11.py '),
            'a lost status line': seeded('**Status: EXPERIMENTAL (host only). Hardware status: NOT RUN for the tooling and for sessions 2 and 3; session 1, a read-only survey, ran once on 2026-11-18 (section 9).**',
                                         '**Status: EXPERIMENTAL (host only).**'),
            'a status line that says session 1 never ran': seeded('session 1, a read-only survey, ran once on 2026-11-18 (section 9).**', 'session 1 has never run (section 9).**'),
            'a lost result of session 1': seeded('**Result of session 1 (boot 22, 2026-11-18;', '**Note on session 1 (boot 22, 2026-11-18;'),
            'a result that gives the SMMU a mode': seeded('it is not treated as bypass.', 'it is bypass.'),
            'a result that settles the DMA reach': seeded('whether the SSD\'s DMA reaches a monitor-owned extent after a takeover (D14), anything about writes', 'that the SSD\'s DMA reaches a monitor-owned extent after a takeover (D14), anything about writes'),
            'a result that settles why the page is unmapped': seeded('**Not settled:** the SMMU\'s mode (D7), why its page is unmapped,', '**Not settled:** the SMMU\'s mode (D7), that its page is firmware-owned,'),
            'a prerequisite of session 2 that is met': seeded('a verdict of bypass or a proven stage 2, is not met.', 'a verdict of bypass or a proven stage 2, is met.'),
            'a spent approval that stands': seeded('The approval is spent.', 'The approval stands.'),
            'an upgraded D8': seeded('| none | OBSERVED in two boots: enabled, ready, two-entry firmware queues, the registers identical |', '| none | VERIFIED: enabled, ready, two-entry firmware queues, the registers identical |'),
            'a D3 observed once again': seeded('| none | OBSERVED in two boots: 01:00.0, RID `0x0100` |', '| none | OBSERVED once: 01:00.0, RID `0x0100` |'),
            'a D7 that is no longer unknown': seeded('| UNKNOWN (session 1 ran: the SMMU\'s page was unmapped at EL2, so nothing was read) |', '| bypass (session 1 ran) |'),
            'a D14 that is settled': seeded('| none to read | UNKNOWN |\n| D15 |', '| none to read | OBSERVED once |\n| D15 |'),
            'a non-ASCII character': TEXT + '\nthe dash \u2014 here\n',
            'a machine name': TEXT + '\nrun it in /Us' + 'ers/someone/work\n',
            'a claim that something is written to the SSD': seeded('**nothing is\nwritten to the SSD in any of the three.**', '**something is\nwritten to the SSD in session 2.**'),
        }
        for name, text in mistakes.items():
            with self.subTest(mistake=name):
                self.assertNotEqual(check(text, FACTS), [], name)

    def test_a_change_that_is_not_a_mistake_passes(self):
        self.assertEqual(check(TEXT.replace('A host test is not hardware evidence.', 'A host test is not hardware evidence (it never was).'), FACTS), [])
        self.assertEqual(check(TEXT + '\nA harmless extra line.\n', FACTS), [])


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
