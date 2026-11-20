# SPDX-License-Identifier: MIT
"""The guide's PANEL entries of the records of the console with a word of its own that have a let_panic word: rungs 4 to 9 (docs/SP11-STICK-MONITOR.md section 20, K12 to K16, and docs/SP11-STICK-ROOT.md, K17), each the
words of the one before with the hooks that its module says again (tools/sp11_stick_bundle_panel_panic.py, ..._rung5.py, ..._rung6.py, ..._rung7.py, ..._rung8.py, ..._rung9.py). Split out of
tools/sp11_stick_bundle_panel.py, which holds the first three choices' words and calls `extend`, to keep that file under the size of a file of this tool.

The key of an entry is tools/sp11_stick_bundle_panel.py kind_key: the choice, `+word`, `+let_panic` and, for a guest with a profile of the menu, `+` its name.

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_panic as rp  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung6 as r6  # noqa: E402
import sp11_stick_bundle_panel_rung7 as r7  # noqa: E402
import sp11_stick_bundle_panel_rung8 as r8  # noqa: E402
import sp11_stick_bundle_panel_rung9 as r9  # noqa: E402

ACK = (r5, r6, r7, r8, r9)  # the modules of the acknowledging monitor's records (K13 to K17)
KEY3, KEY4, KEY5, KEY6 = 'console+cpus=1', 'console+cpus=1+let_panic', 'console+cpu_boot_timeout_secs=120+let_panic', 'console+cpu_boot_timeout_secs=90+let_panic'
KEY7 = f'console+cpu_boot_timeout_secs=120+let_panic+{ch.RUNG7_PROFILE}'
KEY8 = f'console+cpu_boot_timeout_secs=120+let_panic+{ch.RUNG8_PROFILE}'
KEY9 = f'console+cpu_boot_timeout_secs=360+let_panic+{ch.RUNG9_PROFILE}'


def rung_of(panel):
    """The module whose words a record of the console with a word of its own is written in, from the dict that read_panel returned (with the profile that the builder read back): rung 3's,
    rung 4's (K12: let_panic) or, with the acknowledging word, rung 5's (K13), rung 6's (K14: the word of 90 s), rung 7's (K15: a guest profile) or rung 8's (K16: the profile quiet-rtc)."""
    if not panel.get('let_panic'):
        return r3
    if m1.BOOT_ARG_CPU_START.get(panel.get('boot_arg')) != 'ack':
        return rp
    if panel.get('profile'):
        return {ch.RUNG8_PROFILE: r8, ch.RUNG9_PROFILE: r9}.get(panel['profile'], r7)
    return r6 if panel['boot_arg'] == r6.WORD else r5


def extend(words):
    """`words` (the table GUIDE_WORDS, with the choices up to rung 3's `console+cpus=1`) with the entries of rungs 4 to 9, in order, each from the one before; returns it."""
    words[KEY4] = rp.guide_words(words[KEY3])        # rung 4 (K12): rung 3's words, except those that tools/sp11_stick_bundle_panel_panic.py says again
    words[KEY5] = r5.guide_words(words[KEY4])        # rung 5 (K13)
    words[KEY6] = r6.guide_words(words[KEY5])        # rung 6 (K14)
    words[KEY7] = r7.guide_words(words[KEY6])        # rung 7 (K15): rung 6's words with the quiet profile
    words[KEY8] = r8.guide_words(words[KEY7])        # rung 8 (K16): rung 7's words with the clock bound
    words[KEY9] = r9.guide_words(words[KEY8])        # rung 9 (K17): rung 8's words with a RAM-disk root, the root-carry pair and a box of 300 s
    return words


def pre_call(r, default):
    """Section 5's bullets on the silences before the call: rung 9's own (the carry is minutes long, tools/sp11_stick_bundle_panel_rung9.py PRE_CALL) or `default`, the guide's."""
    panel = ((r.get('real') or {}).get('panel_facts') or {})
    return r9.pre_call() if panel.get('profile') == ch.RUNG9_PROFILE else default


def carry_rows(panel):
    """The rows that section 4's table has for the runner's read of the file before the wrapper's first line (rung 9 only, none for any other record)."""
    return r9.carry_rows() if panel.get('profile') == ch.RUNG9_PROFILE else ()
