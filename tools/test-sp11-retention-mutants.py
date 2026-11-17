#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mutation check for the retention probe's host side: the extract step of the autorun generator, the verdict tool, the page and
configuration library, the plan builder and the stager's one new option.

Each mutant seeds one plausible bug into a copy of a module (under build/) and runs the three host suites of the retention probe against the
copies, through SP11_RETENTION_DIR, SP11_AUTORUN_DIR and SP11_STAGE_DIR. A mutant the suites do not fail on is a hole in the suites, so this run
fails. A control run with no mutation must pass, and every replaced text must occur exactly once and leave a module that still compiles, so that
a failure means a bug was caught and not that the copy was broken. The mutants include the four the design names: a parser that ignores the CRC,
a dump step that runs after another command, a plan that starts the unattended variant without the operator's flag, and (in the q1n1 fork's own
suite) a payload that resets without the feature check. Results go to build/test-sp11-retention-mutants/mutants.tsv.

    python3 tools/test-sp11-retention-mutants.py"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import shutil
import subprocess
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
BUILD = ROOT / 'build' / 'test-sp11-retention-mutants'
SUITES = ('test-sp11-retention.py', 'test-sp11-retention-check.py', 'test-sp11-retention-autorun.py')
MODULES = ('sp11_autorun.py', 'stage-sp11-boot-media.py', 'sp11_retention.py', 'sp11_retention_check.py', 'sp11_retention_plan.py')
REAL_IMAGE_VARIABLES = ('SP11_SHELL_EFI', 'SP11_SLBOUNCE_EFI', 'SP11_Q1N1_EFI', 'SP11_Q1N1_STK_EFI', 'SP11_TESTS_EFI',
                        'Q1N1_DIR', 'SP11_Q1N1_PAYLOAD_EFI', 'SP11_PAYLOAD_STATE_BIN')
A, S, L, K, B = MODULES

# (module, what the bug is, [(old text, new text), ...]). Each old text occurs exactly once in its module.
MUTANTS = (
    # -- the page rule: WHOLE, PARTIAL, NONE
    (L, 'the verdict tool ignores the CRC', [('crc_ok = page_crc(page) == words[CRC_WORD]', 'crc_ok = True')]),
    (L, 'a page is WHOLE without its CRC', [("'WHOLE' if crc_ok and not any(wrong) else 'PARTIAL'", "'WHOLE' if not any(wrong) else 'PARTIAL'")]),
    (L, 'a page is WHOLE without its body', [("'WHOLE' if crc_ok and not any(wrong) else 'PARTIAL'", "'WHOLE' if crc_ok else 'PARTIAL'")]),
    (L, 'the header need not name this index', [('== (_MAGIC_LO, _MAGIC_HI, LAYOUT_VERSION, index) and', '== (_MAGIC_LO, _MAGIC_HI, LAYOUT_VERSION, words[3]) and')]),
    (L, 'the header need not name this address', [('words[4] == address & 0xFFFFFFFF \\\n            and words[5] == address >> 32 and', 'True \\\n            and')]),
    (L, 'a counter of zero names a page', [('and 1 <= counter <= COUNTER_MAX:\n        expected', 'and 0 <= counter <= COUNTER_MAX:\n        expected')]),
    (L, 'a counter above the limit names a page', [('and 1 <= counter <= COUNTER_MAX:\n        expected', 'and 1 <= counter <= COUNTER_MAX + 1:\n        expected')]),
    (L, 'a lost header needs 17 words of an epoch', [('MIN_BODY_MATCHES = 16 ', 'MIN_BODY_MATCHES = 17 ')]),
    (L, 'a lost header needs 8 words of an epoch', [('MIN_BODY_MATCHES = 16 ', 'MIN_BODY_MATCHES = 8 ')]),
    (L, 'a tie between epochs goes to the larger', [('epoch = min((key for key', 'epoch = max((key for key')]),
    (L, 'the vote counts words whose low eleven bits equal the index', [('if w & 0x3FF == i)', 'if w & 0x7FF == i)')]),
    (L, 'the body pattern has another shift', [('return (counter << 12 | index << 10 | i) & 0xFFFFFFFF', 'return (counter << 11 | index << 10 | i) & 0xFFFFFFFF')]),
    (L, 'the boot counter does not advance', [("if v.state == 'WHOLE'], default=0) + 1", "if v.state == 'WHOLE'], default=0)")]),
    (L, 'a PARTIAL page gives the boot counter', [("if v.state == 'WHOLE'], default=0) + 1", "if v.counter], default=0) + 1")]),
    (L, 'the page address is not stored', [('words[2:6] = [LAYOUT_VERSION, index, address & 0xFFFFFFFF, address >> 32]', 'words[2:6] = [LAYOUT_VERSION, index, 0, 0]')]),
    (L, 'the flush method byte is zero', [('words[14] = previous_states[2] | FLUSH_DC_CVAC << 8', 'words[14] = previous_states[2]')]),
    (L, 'the CRC word is not zeroed for the CRC', [('return zlib.crc32(page[:CRC_WORD * 4] + bytes(4) + page[CRC_WORD * 4 + 4:])', 'return zlib.crc32(page)')]),
    # -- the configuration file
    (L, 'a configuration with a wrong CRC is accepted', [('if zlib.crc32(blob[:-4]) != w[15]:', 'if False:')]),
    (L, 'a configuration of another version is accepted', [('if w[2] != LAYOUT_VERSION:', 'if False:')]),
    (L, 'a fourth reset path is accepted', [('if w[3] >= len(PATHS):', 'if w[3] > len(PATHS):')]),
    (L, 'a countdown of zero is accepted', [('if not COUNTDOWN_MIN <= w[4] <= COUNTDOWN_MAX:', 'if not 0 <= w[4] <= COUNTDOWN_MAX:')]),
    (L, 'a countdown above sixty is accepted', [('if not COUNTDOWN_MIN <= w[4] <= COUNTDOWN_MAX:', 'if not COUNTDOWN_MIN <= w[4] <= COUNTDOWN_MAX + 1:')]),
    (L, 'a configuration of four pages is accepted', [('if w[5] != PAGE_COUNT:', 'if False:')]),
    (L, 'reserved words need not be zero', [('if w[12] | w[13] | w[14]:', 'if False:')]),
    (L, 'a page address need not be aligned', [('if page % PAGE_BYTES:', 'if False:')]),
    (L, 'page zero is accepted', [('if not page or page > ADDRESS_LIMIT - PAGE_BYTES:', 'if page > ADDRESS_LIMIT - PAGE_BYTES:')]),
    (L, 'a page above the address limit is accepted', [('if not page or page > ADDRESS_LIMIT - PAGE_BYTES:', 'if not page:')]),
    (L, 'the same page may be named twice', [('if len(set(pages)) != PAGE_COUNT:', 'if False:')]),
    (L, 'a configuration of the wrong size is accepted', [('if len(blob) != CONFIG_BYTES:', 'if False:')]),
    # -- pages against a map
    (L, 'a page may be in firmware memory', [('elif any(not e.conventional for e in hits):', 'elif False:')]),
    (L, 'a page may straddle two extents', [('elif len(hits) != 1 or not (hits[0].start <= page and page + PAGE_BYTES <= hits[0].end):', 'elif False:')]),
    (L, 'a page in no extent is accepted', [('if not hits:\n            problems.append', 'if False:\n            problems.append')]),
    (L, 'every type is conventional', [('int(fields[1], 16) == CONVENTIONAL_TYPE', 'True')]),
    (L, 'the unit pages include the reserved panel region', [('UNIT_PAGES = (0x9C000000, 0xF8400000, 0xFFFF00000)', 'UNIT_PAGES = (0x9C000000, 0xE4800000, 0xFFFF00000)')]),
    # -- the verdict tool: files
    (K, 'a file with no end line is judged', [('    if end is None:\n        if lines and', '    if False:\n        if lines and')]),
    (K, 'a UCS-2 file gets another name', [("if blob[:2] in (b'\\xff\\xfe', b'\\xfe\\xff'):", 'if False:')]),
    (K, 'a block of another size is accepted', [('if size != retention.PAGE_BYTES:', 'if False:')]),
    (K, 'a duplicate page is accepted', [("if address in [a for a, _ in pages]:", 'if False:')]),
    (K, 'a missing line is not noticed', [("if i >= len(body) or _BLOCK_HEAD.fullmatch(body[i]) or _MAP_HEAD.fullmatch(body[i]):", 'if i >= len(body):')]),
    (K, 'an extra line is not noticed', [('if i < len(body) and _DATA_LINE.fullmatch(body[i]):', 'if False:')]),
    (K, 'a line out of order is not noticed', [('if shown not in (want, want & 0xFFFFFFFF):', 'if False:')]),
    (K, 'the 32-bit line addresses of the Shell are refused', [('if shown not in (want, want & 0xFFFFFFFF):', 'if shown != want:')]),
    (K, 'full line addresses are refused', [('if shown not in (want, want & 0xFFFFFFFF):', 'if shown != want & 0xFFFFFFFF:')]),
    (K, 'the text column is not checked', [("if found.group(3) != ''.join(", 'if False and found.group(3) != \'\'.join(')]),
    (K, 'two pages are enough', [('if len(pages) != retention.PAGE_COUNT:', 'if len(pages) < 1:')]),
    (K, 'a stray line is skipped', [("raise DumpError('UNEXPECTED_LINE', f'line {i + 1}: {body[i][:60]!r}')", 'i += 1')]),
    (K, 'an end line of another step is accepted', [('if step is not None and n != step:', 'if False:')]),
    (K, 'other pages than the configuration are accepted', [('if expected_pages is not None and [a for a, _ in pages] != list(expected_pages):', 'if False:')]),
    (K, 'a memory map whose page counts disagree is accepted', [("if end <= start or int(row.group(4), 16) * retention.PAGE_BYTES != end - start:", 'if end <= start:')]),
    (K, 'the configuration is not compared with the pages', [("if list(parsed['pages']) != [a for a, _ in dump.pages]:", 'if False:')]),
    # -- the verdict tool: judgement
    (K, 'a page owned by firmware counts as free', [('        elif hit[0].conventional:\n', '        elif True:\n')]),
    (K, 'the series needs nine trials', [('SERIES_PASS_TRIALS = 10 ', 'SERIES_PASS_TRIALS = 9 ')]),
    (K, 'a broken run is not reset', [("trial['streak'] = trial['streak'] + 1 if kept else 0", "trial['streak'] = trial['streak'] + 1")]),
    (K, 'firmware-owned pages count in the series', [("kept = report.verdict == 'WHOLE' and not report.counter_conflict and all(r.map_free is True for r in report.pages if r.verdict.state == 'WHOLE')",
                                                      "kept = report.verdict == 'WHOLE' and not report.counter_conflict")]),
    (K, 'the best state is the worst', [("best = 'WHOLE' if whole else 'PARTIAL' if any(r.verdict.state == 'PARTIAL' for r in reports) else 'NONE'",
                                         "best = 'NONE' if not whole else 'WHOLE'")]),
    (K, 'the path of the pages is not read', [('path_used = retention.PATH_LABELS[next(iter(used))] if len(used) == 1 else None', 'path_used = None')]),
    (K, 'the stick command ignores the configuration', [("args.config, args.files, args.pages, args.json = (config if config.is_file() else None), files, None, False",
                                                         "args.config, args.files, args.pages, args.json = None, files, None, False")]),
    (K, 'the PSCI answers are not printed', [("    named = [r.verdict for r in report.pages if r.verdict.named]\n    if named:\n        header", "    named = [r.verdict for r in report.pages if r.verdict.named]\n    if False:\n        header")]),
    (K, 'a negative PSCI answer is called implemented', [("    if value < 0x80000000:\n        return 'implemented'", "    if True:\n        return 'implemented'")]),
    (K, 'a page whose CRC fails is believed for the PSCI answers', [("header = next((v for v in named if v.crc_ok), named[0]).header", "header = named[0].header")]),
    # -- the extract step in the generator
    (A, 'the extract block gets a stall before the dump', [("             'if exist ' + result + ' then', *_refuse(f'{result} exists and is never overwritten')]",
                                                          "             'if exist ' + result + ' then', *_refuse(f'{result} exists and is never overwritten'), f'stall {PAUSE_MICROSECONDS}']")]),
    (A, 'the extract block writes its marker before the dump', [("             'if exist ' + result + ' then', *_refuse(f'{result} exists and is never overwritten')]",
                                                               "             'if exist ' + result + ' then', *_refuse(f'{result} exists and is never overwritten'),\n"
                                                               "             f'{_say(f\"AUTORUN {plan.name} STEP {k} EXTRACTED\")} >a {marker}']")]),
    (A, 'a result may be overwritten', [("             'if exist ' + result + ' then', *_refuse(f'{result} exists and is never overwritten')]", '             ]')]),
    (A, 'the first dump does not start the file', [('{">a" if index == 0 else ">>a"}', '{">>a"}')]),
    (A, 'every dump overwrites the file', [('{">a" if index == 0 else ">>a"}', '{">a"}')]),
    (A, 'the first dump is a UCS-2 redirect', [('{">a" if index == 0 else ">>a"}', '{">" if index == 0 else ">>a"}')]),
    (A, 'the memory map is not appended', [("    return lines + [f'memmap >>a {result}',", "    return lines + [f'memmap >a {result}',")]),
    (A, 'the end line is not written', [("""                    f'{_say(f"{EXTRACT_END} plan={plan.name} step={k} pages={EXTRACT_PAGES}")} >>a {result}',\n""", '')]),
    (A, 'the marker is not checked', [("                    'if not exist ' + marker + ' then', *_refuse('cannot write ' + marker),\n", '')]),
    (A, 'the extract block runs on into the next step', [("                    'goto end']\n\n\ndef script_lines", "                    ]\n\n\ndef script_lines")]),
    (A, 'a dump is of a page the plan did not name', [('lines.append(f\'dmem 0x{page:X} 0x{EXTRACT_PAGE_BYTES:X}', 'lines.append(f\'dmem 0x{page + 0x1000:X} 0x{EXTRACT_PAGE_BYTES:X}')]),
    (A, 'the extract block is not told apart from an ordinary step', [("        check = _extract_problems if any(kind == 'dmem' for kind, _ in segments[label]) else _step_problems",
                                                                      "        check = _step_problems")]),
    (A, 'the line checker lets any kind of line into an extract block', [('    if kinds != _EXTRACT_KINDS:\n', '    if False:\n')]),
    (A, 'the line checker does not look at the redirects', [("    if [found.group(2) for found in dumps] != ['>a'] + ['>>a'] * (EXTRACT_PAGES - 1):", '    if False:')]),
    (A, 'the line checker accepts repeated or unaligned pages', [("    if len({found.group(1) for found in dumps}) != EXTRACT_PAGES or any(int(found.group(1), 16) % EXTRACT_PAGE_BYTES for found in dumps):", '    if False:')]),
    (A, 'the line checker accepts another step number', [("    if ends != {str(k)} or segment[15][1].group(1) != str(k):", '    if False:')]),
    (A, 'the line checker does not look at the marker check', [("    if not segment[16][1].group(1) or segment[16][1].group(2) != MARKER_FORMAT.format(k) or segment[18][1].group(1) != 'end' or segment[-1][1].group(1) != 'end':", '    if False:')]),
    (A, 'the line checker does not look at the guard', [("    if segment[4][1].group(1) or segment[4][1].group(2) != RESULT_FORMAT.format(k) or segment[6][1].group(1) != 'end':", '    if False:')]),
    (A, 'a dump line is tolerated outside the steps', [("for kind in ('load', 'run', 'marker', 'stall', 'extract_end', 'dmem', 'memmap', 'clock'):", "for kind in ('load', 'run', 'marker', 'stall', 'extract_end'):")]),
    # -- the plan rules in the model
    (A, 'the unattended plan is built without the flag', [("    if name == RETENTION_UNATTENDED_PLAN and not stick_first_authorised:", '    if False:')]),
    (A, 'the flag is accepted on the baseline plan', [("    if name == RETENTION_PLAN and stick_first_authorised:", '    if False:')]),
    (A, 'the flag is accepted on any other plan', [("        if stick_first_authorised:\n            raise PlanError('stick_first_flag_not_for_this_plan', f'only", "        if False:\n            raise PlanError('stick_first_flag_not_for_this_plan', f'only")]),
    (A, 'an extract step may be in any plan', [("        if extracts:\n            raise PlanError('extract_outside_retention_plan'", "        if False:\n            raise PlanError('extract_outside_retention_plan'")]),
    (A, 'a retention plan may have any shape', [("    if len(parsed) != 2 or isinstance(parsed[0], ExtractStep) or parsed[0][1] != RUNNER or not isinstance(parsed[1], ExtractStep):", '    if False:')]),
    (A, 'a page need not be aligned', [("    if any(page % EXTRACT_PAGE_BYTES for page in pages):", '    if False:')]),
    (A, 'the same page may be named twice in the extract step', [("    if len(set(pages)) != len(pages):", '    if False:')]),
    (A, 'page addresses may be spelled any way', [("EXTRACT_ADDRESS_RE = re.compile(r'0x[1-9A-F][0-9A-F]{0,11}')", "EXTRACT_ADDRESS_RE = re.compile(r'0x[0-9A-Fa-f]{1,16}')")]),
    (A, 'the extract step counts as a file of the plan', [("            if isinstance(step, ExtractStep):\n                continue\n", '')]),
    (A, 'a result name may be a data name', [("        if RESULT_RE.fullmatch(name):\n", '        if False:\n')]),
    (A, 'PLAN.TXT does not record the unattended line', [("*([UNATTENDED_LINE] if plan.stick_first_authorised else [])", '*[]')]),
    (A, 'PLAN.TXT always claims the authorisation', [("unattended = re.search(rf'^{re.escape(UNATTENDED_LINE)}\\r\\n', text, re.MULTILINE) is not None", 'unattended = True')]),
    (A, 'PLAN.TXT never reads the authorisation', [("unattended = re.search(rf'^{re.escape(UNATTENDED_LINE)}\\r\\n', text, re.MULTILINE) is not None", 'unattended = False')]),
    # -- the stager and the plan builder
    (S, 'the stager ignores the flag', [("hashes, getattr(args, 'stick_first_authorised', False))", 'hashes, False)')]),
    (S, 'the stager always passes the flag', [("hashes, getattr(args, 'stick_first_authorised', False))", 'hashes, True)')]),
    (B, 'the builder builds the unattended plan without the flag', [("    if unattended and not stick_first_authorised:\n        raise PlanRefusal('unattended_not_authorised'", "    if False:\n        raise PlanRefusal('unattended_not_authorised'")]),
    (B, 'the builder builds SYSTEM_RESET2 for the unit', [("    if path == 'psci-reset2' and not expect_refusal:", '    if False:')]),
    (B, 'the builder builds an unattended plan with no reset', [("    if unattended and path == 'none':", '    if False:')]),
    (B, 'the builder does not validate the configuration', [("    try:\n        retention.config_bytes(retention.PATHS[path], countdown, pages)\n    except retention.ConfigError as error:\n        raise PlanRefusal('config_refused', str(error)) from error",
                                                           "    pass")]),
    (B, 'the record leaves out the stop on a returned reset', [("    out += ['   - A line RESET RETURNED: the call came back. Stop: no second call and no other path in this run.',\n", "    out += [\n")]),
    (B, 'the operator is not told to hold the key', [("f'  5. From the moment the page shows NEXT RESET AFTER {countdown} S, HOLD Volume-Down and keep holding it.", "f'  5. From the moment the page shows NEXT RESET AFTER {countdown} S, press nothing and wait.")]),
    (B, 'the operator may power-cycle after a lost restart', [("'  power-cycle to try again in this run: a power cycle loses what the probe wrote.", "'  power-cycle to try again: it is fine.")]),
    # -- the memory map: every row is read (the review's R1)
    (L, 'rows whose names have spaces are skipped', [("        fields = line.split(None, 6)\n", "        fields = line.split()\n        if len(fields) != 7:\n            continue\n")]),
    (L, 'a header count that the rows contradict is accepted', [("    if declared is not None and declared != len(extents):\n", '    if False:\n')]),
    (L, 'a line that is not a row is skipped', [("            raise MapError(f'line {number} is not a descriptor row: {line[:60]!r}')\n", '            continue\n')]),
    (L, 'a row with no pages is accepted', [(' or not int(fields[4], 16):', ':')]),
    (B, 'the builder does not read the memory map it is given', [("            except retention.MapError as error:\n                raise PlanRefusal('memory_map_unreadable'", "            except KeyError as error:\n                raise PlanRefusal('memory_map_unreadable'")]),
    # -- a series counts trials, and only trials it can judge (R2, R3, R4)
    (K, 'one file counts as many trials as it is given', [('        if report.digest in files:\n', '        if False:\n')]),
    (K, 'the same write dumped twice is a new trial', [('            if stamp in writes:\n', '            if False:\n')]),
    (K, 'a file with no memory map is counted in a series', [("        if not report.has_map:\n            raise DumpError('NO_MEMORY_MAP'", "        if False:\n            raise DumpError('NO_MEMORY_MAP'")]),
    (K, 'a page in no extent of the map counts as free', [("all(r.map_free is True for r in report.pages if r.verdict.state == 'WHOLE')", "all(r.map_free is not False for r in report.pages if r.verdict.state == 'WHOLE')")]),
    (K, 'a file with whole pages of different boots is kept', [('and not report.counter_conflict and all(', 'and all(')]),
    (K, 'every file is said to have a memory map', [('bool(dump.memmap), tuple(sorted(', 'True, tuple(sorted(')]),
    (K, 'the pages name no write', [("tuple(sorted({(v.counter, v.header['timestamp']) for v in whole}))", '()')]),
    (K, 'the boots of the whole pages are never compared', [('len({v.counter for v in whole}) > 1,', 'False,')]),
    (K, 'the file has no digest', [("clock[1] if clock else '', hashlib.sha256(blob).hexdigest())", "clock[1] if clock else '', '')")]),
    # -- the clock the extract step writes, and the times the stick command compares (R7)
    (K, 'a stick result with no clock is compared with the staging anyway', [("        if report.clock is None:\n            raise DumpError('NO_CLOCK'", "        if False:\n            raise DumpError('NO_CLOCK'")]),
    (K, 'a result left by an earlier run is judged', [('        if report.clock <= staged:\n', '        if False:\n')]),
    (K, 'a result written at the staging time is judged', [('        if report.clock <= staged:\n', '        if report.clock < staged:\n')]),
    (K, 'a step 1 marker left by an earlier run is accepted', [('        if started <= staged:\n', '        if False:\n')]),
    (K, 'a step 1 marker written at the staging time is accepted', [('        if started <= staged:\n', '        if started < staged:\n')]),
    (K, 'a result with no step 1 marker is judged', [('        if report.step < 2 or not marker.is_file():\n', '        if report.step < 2:\n')]),
    (K, 'a tree with no staging time is judged', [("    if found is None:\n        raise DumpError('NO_STAGING_TIME'", "    if False:\n        raise DumpError('NO_STAGING_TIME'")]),
    (K, 'a file copied or edited after the unit wrote it is not noticed', [('        if abs((written - report.clock).total_seconds()) > FILE_TIME_SLACK_SECONDS:\n', '        if False:\n')]),
    (A, 'the extract block writes no date', [("                    f'date >>a {result}',\n", '')]),
    (A, 'the extract block writes the time before the date', [("                    f'date >>a {result}',\n                    f'time >>a {result}',\n", "                    f'time >>a {result}',\n                    f'date >>a {result}',\n")]),
    (A, 'the line checker does not look at the order of the date and the time', [("    if [segment[12][1].group(1), segment[13][1].group(1)] != ['date', 'time']:\n", '    if False:\n')]),
    (A, 'PLAN.TXT does not record the staging time', [("             *([f'staged: {plan.staged}'] if plan.staged else []), '']", "             '']")]),
    (A, 'PLAN.TXT never reads the staging time', [('            plan = stamp_staging(plan, datetime.strptime(staged.group(1), STAGED_FORMAT))\n', '            pass\n')]),
    (A, 'a plan that is not a retention plan is stamped', [('    if plan.name not in RETENTION_PLANS:\n        return plan\n    return replace(plan, staged=', '    return replace(plan, staged=')]),
    (S, 'the stager does not record when it staged', [('    plan = autorun.stamp_staging(plan)\n', '')]),
    # -- a retention plan stages its three data files and no others (R8)
    (A, 'a retention plan may stage any data file', [('    if plan.name in RETENTION_PLANS and set(hashes) != set(RETENTION_DATA_NAMES):\n', '    if False:\n')]),
    (A, 'a retention plan may stage fewer data files', [('    if plan.name in RETENTION_PLANS and set(hashes) != set(RETENTION_DATA_NAMES):\n', '    if plan.name in RETENTION_PLANS and not set(hashes) <= set(RETENTION_DATA_NAMES):\n')]),
    (A, 'the manifest lists the configuration before the payload', [('    files = tuple(files)\n    blob = _HEADER.pack(', '    files = tuple(reversed(files))\n    blob = _HEADER.pack(')]),
    (B, 'the record does not pin the manifest', [("           f'   Manifest:       PAYLOAD.MAN, {manifest_size} bytes, SHA-256 {manifest_sha256}',\n", "           '   Manifest:       PAYLOAD.MAN',\n")]),
    (B, 'the builder does not write the manifest', [('    (out_dir / autorun.MANIFEST_NAME).write_bytes(manifest)\n', '')]),
    # -- what the record and the actions say (R6, R10, R11)
    (B, 'the record says the payload writes no system register', [('they write PAR_EL1, the one system register the', 'they write nothing, the one system register the')]),
    (B, 'the record forgets the loads nothing guards', [('Plain loads that nothing guards:', 'Loads:')]),
    (B, 'the record forgets what a reset that quiesces nothing does', [('acts as a power cut would (INFERRED)', 'is harmless')]),
    (B, 'the record makes the power button the answer to a dark screen', [('so the power button is not the answer to a dark', 'so the power button is the answer to a dark')]),
    (B, 'the record calls SYSTEM_RESET the warm reset', [('SYSTEM_RESET is the architectural cold-reset call;', 'SYSTEM_RESET is the architectural warm-reset call;')]),
    (B, 'the record says the clean reaches DRAM', [('DC CIVAC reach the point of', 'DC CIVAC reach DRAM, the point of')]),
    (B, 'the record says the MMU check shows the page is free', [('They show that EL2 maps the page, not that TrustZone', 'They show that EL2 maps the page and that TrustZone')]),
    (B, 'the record calls NONE a failure', [('nothing is wrong with the probe or the unit.', 'the probe is wrong.')]),
    (B, 'the actions do not say what a marker left by an earlier run does', [("A marker left by an earlier run does harm: a DONE1.TXT", "A marker left by an earlier run is harmless: a DONE1.TXT")]),
    (B, 'the actions make the power button the answer to a lost restart', [('The power button is for the end of the run (step 7) and for danger (heat, a fan at full speed, a smell): then hold it and say so.', 'Hold the power button.')]),
)


def mutate(text, replacements):
    for old, new in replacements:
        if text.count(old) != 1:
            raise AssertionError(f'{text.count(old)} occurrences of {old!r}')
        text = text.replace(old, new)
    return text


def run_suites(directory):
    """0 when every suite passes against the modules in `directory`; the first failing return code otherwise."""
    inherited = {k: v for k, v in os.environ.items() if k not in REAL_IMAGE_VARIABLES}
    for suite in SUITES:
        done = subprocess.run([sys.executable, '-B', str(TOOLS / suite)], capture_output=True, text=True, timeout=900,
                              env={**inherited, 'SP11_RETENTION_DIR': str(directory), 'SP11_AUTORUN_DIR': str(directory), 'SP11_STAGE_DIR': str(directory),
                                   'PYTHONDONTWRITEBYTECODE': '1'})
        if done.returncode:
            return done.returncode, f'{suite}: {done.stderr.strip().splitlines()[-1] if done.stderr.strip() else ""}'
    return 0, ''


def prepare(index, module, replacements):
    directory = BUILD / f'{index:02d}'
    directory.mkdir(parents=True)
    for name in MODULES:
        shutil.copy(TOOLS / name, directory / name)
    if replacements:
        text = mutate((directory / module).read_text(), replacements)
        compile(text, str(directory / module), 'exec')
        (directory / module).write_text(text)
    return directory


class Mutants(unittest.TestCase):
    def test_every_replaced_text_occurs_once_and_the_copy_compiles(self):
        shutil.rmtree(BUILD, ignore_errors=True)
        BUILD.mkdir(parents=True)
        for i, (module, what, replacements) in enumerate(MUTANTS, 1):
            with self.subTest(mutant=what):
                prepare(i, module, replacements)
        self.assertEqual(len({what for _, what, _ in MUTANTS}), len(MUTANTS), 'a description is used twice')

    def test_the_control_run_passes_and_every_mutant_is_caught(self):
        shutil.rmtree(BUILD, ignore_errors=True)
        BUILD.mkdir(parents=True)
        code, detail = run_suites(prepare(0, A, []))
        self.assertEqual(code, 0, f'the suites fail without any mutation: {detail}')
        directories = [prepare(i, module, replacements) for i, (module, _, replacements) in enumerate(MUTANTS, 1)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run_suites, directories))
        rows = [f'{i:02d}\t{"caught" if code else "SURVIVED"}\t{module}\t{what}\t{detail}'
                for i, ((module, what, _), (code, detail)) in enumerate(zip(MUTANTS, results), 1)]
        (BUILD / 'mutants.tsv').write_text('\n'.join(rows) + '\n')
        survivors = [row for row in rows if '\tSURVIVED\t' in row]
        self.assertEqual(survivors, [], 'mutants the suites did not fail on:\n' + '\n'.join(survivors))
        print(f'\n{len(MUTANTS)} mutants, all caught; control run passed; see {(BUILD / "mutants.tsv").relative_to(ROOT)}')


if __name__ == '__main__':
    unittest.main()
