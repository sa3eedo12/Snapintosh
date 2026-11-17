# SPDX-License-Identifier: MIT
"""The numbers of the line budget that the record (section 7) and the runbook (section 6.1) state, held to the table and the rule of the q1n1 host model that
produced them (tools/test-sp11-xhci-s1-record.py and tools/test-sp11-xhci-s1-doc.py use it). Pure text functions: nothing here runs a payload, reads a device
or writes a file. Evidence: EXPERIMENTAL (host model only); hardware NOT RUN."""
import re

# The line of tools/test-q1n1-sp11-payload-xhci-cases.c that holds the model's table: how many of the four SMMU1 reports of XHCI0.BIN are on the page after
# 0, 1, 2, 3, 4 and 8 port changes when all four arenas fail with a change of the SMMU's fault registers and the DWC3's bus-error address and valid bit; and the rule of the success that says LINES_CUT.
TABLE = re.compile(r'printed_four_arenas\[6\] = \{([^}]*)\};')
RULE = re.compile(r'b\.cut == \(!T && success > 1 && changes \+ success >= (\d+)\)')
WORDS = {1: 'one', 2: 'two', 3: 'three'}


def budget_problems(text, cases):
    """What differs between `text` (the record or the runbook; backticks and line breaks do not matter) and the host model's `cases` source: the reports of
    XHCI0.BIN that fit after 0, 1, 2 and 3 or more CHG lines, and from how many CHG lines on a success at arena 4, 3 and 2 says LINES_CUT."""
    flat = ' '.join(text.replace('`', '').split())
    problems = []
    table = TABLE.search(cases)
    printed = [int(n) for n in table.group(1).split(',')] if table else []
    if printed != [3, 2, 1, 0, 0, 0]:
        problems.append(f'the model prints {printed} of the four reports after 0, 1, 2, 3, 4 and 8 changes')
    elif not re.search(r'3, 2, 1 or 0 of the four SMMU1 reports (?:on the page )?after 0, 1, 2 or 3 or more CHG lines', flat):
        problems.append('the text does not give the reports that fit')
    rule = RULE.search(cases)
    if not rule:
        problems.append('the model has no rule for the success that says LINES_CUT')
        return problems
    limit = int(rule.group(1))
    wanted = ('after a success at arena 4 from %s CHG line on, at arena 3 from %s, at arena 2 from %s, never at arena 1' %
              tuple(WORDS.get(limit - arena, str(limit - arena)) for arena in (4, 3, 2)))
    if wanted not in flat:
        problems.append(f'the text does not say {wanted!r}')
    return problems
