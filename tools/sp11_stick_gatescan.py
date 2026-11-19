# SPDX-License-Identifier: MIT
"""A C source or header as it compiles where one gate macro is 0, and the provenance check built on it. The GIC pair (SNAPMON-GIC.BIN, SNAPREP-GIC.BIN) is built with
SNAP_SP11_STICK_MONITOR 0, so a file that the M1 build shares with it (probes/sp11-stick-reporter.c, the largest) may change inside `#if SNAP_SP11_STICK_MONITOR` without
changing the pair: `gate_off` drops what that gate leaves out, and `source_problems` compares the files of the pair with the commit the approved note names, after that, for C
sources and headers, and byte for byte for any other file. An edit outside the gate, a comment included, is still a change of the pair.

The scan follows `#if GATE`, `#if !GATE`, `#else` and `#endif`, with every other conditional taken both ways (as test-sp11-stick-bundle-record-gic-model.py `included` does) and
its directive lines kept as text; it does not follow anything else that mentions the gate (an `#elif` of it, an expression with it: ValueError, which fails closed), nor a
conditional left open or closed twice. Host only; nothing here touches hardware. EXPERIMENTAL."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = 'SNAP_SP11_STICK_MONITOR'


def gate_off(text, gate=GATE):
    """`text` with the blocks and branches that `gate` (0) leaves out removed, and the directive lines of the gate itself with them; ValueError for a conditional the scan does
    not follow."""
    out, stack, live = [], [], True         # stack of (live outside the conditional, is it the gate's, whether its `#if` holds where the gate is 0)
    for line in text.splitlines(keepends=True):
        s = line.strip()
        simple = re.fullmatch(r'#\s*if\s+(!?)\s*(\w+)', s)
        if re.match(r'#\s*(if|ifdef|ifndef|elif)\b', s) and re.search(rf'\b{gate}\b', s) and not (simple and simple.group(2) == gate):
            raise ValueError(f'a conditional with the gate that is not `#if GATE` or `#if !GATE`: {s!r}')
        if simple or re.match(r'#\s*(ifdef|ifndef|if)\b', s):
            known = bool(simple) and simple.group(2) == gate
            holds = simple.group(1) == '!' if known else True
            stack.append((live, known, holds))
            live = live and holds
            if not known and stack[-1][0]:
                out.append(line)
        elif re.match(r'#\s*elif\b', s):
            if not stack:
                raise ValueError('#elif without #if')
            if stack[-1][1]:
                raise ValueError('#elif of the gate')
            if stack[-1][0]:
                out.append(line)
        elif re.match(r'#\s*else\b', s):
            if not stack:
                raise ValueError('#else without #if')
            outer, known, holds = stack[-1]
            live = outer and (not holds if known else True)
            if outer and not known:
                out.append(line)
        elif re.match(r'#\s*endif\b', s):
            if not stack:
                raise ValueError('#endif without #if')
            outer, known, holds = stack.pop()
            live = outer
            if outer and not known:
                out.append(line)
        elif live:
            out.append(line)
    if stack:
        raise ValueError('a conditional left open')
    return ''.join(out)


def same_where_gate_is_off(before, now, gate=GATE):
    """Whether two texts of a C file are one where `gate` is 0; False when either has a conditional that the scan does not follow."""
    try:
        return gate_off(before, gate) == gate_off(now, gate)
    except ValueError:
        return False


def source_problems(commit, files, read=None, root=ROOT):
    """The `files` whose text now is not what it was in `commit`, where the gate is 0: [str]. `read(name)` is the text now (default: the file under `root`). A C source or
    header is compared with `same_where_gate_is_off`, any other file must be identical, and a file that was not in `commit` is a problem. LookupError when git does not
    have the commit (a shallow clone)."""
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if git('cat-file', '-e', f'{commit}^{{commit}}').returncode:
        raise LookupError(f'git does not have commit {commit[:7]}')
    read = read or (lambda name: (Path(root) / name).read_text())
    found = []
    for name in files:
        before, now = git('show', f'{commit}:{name}'), read(name)
        if before.returncode or (before.stdout != now and not (name.endswith(('.c', '.h')) and same_where_gate_is_off(before.stdout, now))):
            found.append(name)
    return found
