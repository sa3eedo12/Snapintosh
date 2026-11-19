# SPDX-License-Identifier: MIT
"""Rung 5 of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K13): the record for the boot after rung 4. Boot 24 (rung 4: cpus=1 with let_panic) printed an ApplePMGR assertion
on the number of CPU complexes, which the host analysis reads as XNU's one-cluster topology under cpus=1; no guest-data change keeps a one-CPU topology past that driver. This record drops
cpus=1 (the guest has its six CPUs, as in boot 21), keeps let_panic, and changes two more things: the monitor ACKNOWLEDGES the guest's CPU-start stores (SNAPMON-M1-ACK.BIN: an EXPERIMENTAL
diagnostic, not a model of the hardware, no CPU is started: compat/cpu-start-ack.h) and the command line has cpu_boot_timeout_secs=120, so that XNU waits for the CPU that never comes for
longer than the box, which D5' makes 60 s at most.

`--cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=120 --panel console --time-box-s 1..60` is the one combination that builds it (tools/sp11_stick_bundle_m1.py); the guide's table PANEL
takes its words by the key `console+cpu_boot_timeout_secs=120+let_panic` (tools/sp11_stick_bundle_panel.py). The words shared with rung 4 are tools/sp11_stick_bundle_panel_panic.py's, by
import. Everything that depends on the box takes it from the record (`box_s`, and `wait_s`, the silence rule's wait for it). Rung 4's result is a keyed block, RUNG4 below: the operator's film as
the coordinator read it (OBSERVED, one unit, one boot) and the host analysis of its stop (read on the host, a file and not a run; INFERRED where it says so). What XNU does with the word is read
in the public source (tag xnu-12377.121.6, not this kernel's own) and is INFERRED.

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_panic as rp  # noqa: E402

WORD = 'cpu_boot_timeout_secs=120'          # the one word of this record's command line (m1.BOOT_ARGS): it goes with the acknowledging monitor
NUMBER = 5                                  # the rung's number, for the pointer to section 5d in the reading lines (tools/sp11_stick_bundle_panel.py)
LOADER_LINE = '-v debug=0x8 serial=2 cluster_power=0 wdt=-1'      # the loader's line with serial=2: rung 2's command line, to which this record adds the word
LINE = f'{LOADER_LINE} {WORD}'
assert rp.LINE == f'{LOADER_LINE} cpus=1', 'the line of rung 4 has changed under the words of rung 5'
BOX_MAX_S = m1.RUNG5_BOX_MAX_S              # D5''s 60 s
FILM_AFTER_S = 120                          # the film runs this long after the report page, and the operator waits this long before FORCE OFF: two minutes after the stop (review of the acknowledge, C3)
BOOT24_RECORD = '67d814f6...42fa'           # the approved record of rung 4: SHA-256 67d814f65331515242461fe54ac7a0292f759d82d5d72c3d21da4643f1a442fa
BOOT24_TICKS = 43_133_334                   # MON RET TICKS= of boot 24, in ticks of 19.2 MHz (OBSERVED once)
BOOT24_S = f'{BOOT24_TICKS / 19.2e6:.3f}'
BOOT24_STOP = dict(pc=0xFFFFFE0008BA7358, a=0x3082BC224, b=0x938A0005, c=0xFFFFFE612A564224)      # MON STOP S2_FAULT PC= A= B= C= of boot 24 (OBSERVED once)
BOOT24_GUEST = '5EFFB5C128395793'           # rung 4's GUEST.DAT as the runner prints its first 16 digits (PAYLOAD LOADED INDEX=2)
BOOT24_PIN = 'CE9B363C8794D8ED'             # rung 4's PIN.DAT as the runner prints its first 16 digits (PAYLOAD LOADED INDEX=3)
BOOT24_LABEL = 'M1RUN-LETPANIC-RUNG4'
FIRST_MONITOR = '1D32F5071506A00C'          # SNAPMON-M1.BIN of boots 20, 21, 23 and 24 as the runner prints it (PAYLOAD LOADED INDEX=0)
PLAIN_MONITOR = 'ad4ebaa2...'               # the monitor inside SNAPMON-M1.BIN: SHA-256 ad4ebaa27314296c82caaaf13a5f05768d966244246a33ca1f36da0f8d944c48
ACK_MONITOR = '053a239f...'                 # the monitor inside SNAPMON-M1-ACK.BIN: 608 bytes longer (SHA-256 053a239f963e99df6ba6b77a05b4e0622e49bbb5101b58405a7c12525fa818ac)
WINDOW = r3.WINDOW                          # A= in 0X300734004..0X30073400C: the three words of the CPU-start block
STAND_HEAD = '0a. Where things stand (boots 20, 21, 23 and 24, 2026-11-18; one unit, one boot each; OBSERVED unless marked INFERRED)'
HEADER = ('3c. The panel as the kernel\'s console with the CPU-start stores acknowledged and let_panic (rung 5: D9 panel handed, serial=2, cpu_boot_timeout_secs=120, let_panic set, '
          'stock kernel; docs/SP11-STICK-MONITOR.md section 20)')
FALSIFIER_ACK = ('(the acknowledge did not take: either an access that the monitor does not take by design, section 3c, or a fault of the monitor: B= on the page is the syndrome, and '
                 'the coordinator decodes it before saying which)')
PARAMS_ROW = ('1 (word 23 of struct params, offset 0xB8: probes/xnuboot/report_layout.c:23); every other PARAMS word is that of the same guest without the word: the builder compares the finished '
              'PARAMS chunk with the one it makes without it and refuses a difference elsewhere (PARAMS_LAUNCH), and holds permissive and skip_undef at 0')

# KEYED BLOCK: rung 4's result as the operator's film showed it and the coordinator read it (files/result-boot24.md of the session), and the host analysis of its stop (files/stopsite-r4.md:
# read on the host from the kernelcache file, the guest's device tree and the public XNU source, a file and not a run). `record` is rung 4's approved record, `boot` its boot as the
# operator's log names it, `verdict` one of VERDICTS as rung 4's section 5d names it, and `words` the paragraphs, each with its label. rung4_lines() holds them to the record's rules.
RUNG4 = dict(record=BOOT24_RECORD, boot='boot 24', verdict='PANIC TEXT', words=(
    'The panel showed the kernel\'s panic text (OBSERVED once; the coordinator\'s transcription of the film, the digits checked in two frames): panic(cpu 0 caller 0xfffffe00097ddef0): '
    '"ApplePMGR: virtual void ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount" @ApplePMGR.cpp:4861, then "Nested panic detected - entry count: 4 '
    'panic_caller: 0xfffffe000c245398" with ApplePMGR and AppleT8140PMGR among the kexts of its backtrace and an empty nested panic string, then "Nested panic count exceeds limit 5, machine '
    'will reset or spin" and "wdog: panic chipreset". The monitor\'s console ring carried the first lines of the same text onto the page as MON CON lines (OBSERVED once; boots 21 and 23 '
    'had none; INFERRED: the panic path writes through the serial-putc call that the monitor logs, and the console of the ordinary log is the panel).',
    f'The call returned {BOOT24_S} s in (TICKS={BOOT24_TICKS} at 19.2 MHz; boot 23: {rp.BOOT23_S} s) and the page said MON STOP S2_FAULT PC={r2.pc_text(BOOT24_STOP["pc"])} '
    f'A={r3.page_hex(BOOT24_STOP["a"])} B={r3.page_hex(BOOT24_STOP["b"])} C={r2.pc_text(BOOT24_STOP["c"])}, on a clean report (OBSERVED once). The decode of B= is INFERRED: a data abort '
    'from a lower EL, a 32-bit READ, a translation fault at level 1 at stage 2; with the last lines of the panel it reads as the kernel\'s read at that address in its panic chip-reset path, '
    'after its text (INFERRED), and the host analysis puts that address in the watchdog node (wdt) of the guest\'s device tree, as one 32-bit register (INFERRED; read on the host, a file and not '
    'a run). No SPTM call was refused after a panic entry (INFERRED: none was made, or none that this monitor refuses): '
    'the frozen monitor let the text print. M1 PASSED by section 5a; rung 4 was PANIC TEXT, the first class of its section 5d that fits.',
    'The cause, read on the host from the kernelcache file, the guest\'s device tree and the public XNU source (a file and not a run; no kernelcache bytes are quoted; INFERRED where it says '
    'so): the assertion follows XNU\'s topology and not a hardware read. A method of AppleT8140PMGR that ApplePMGR::start calls enables CPU complex 0 and then complex 1, with no branch '
    'between them, and ApplePMGR keeps its count of complexes no higher than XNU\'s cluster count (INFERRED: the word that selects that mode was not found: UNKNOWN). With cpus=1 XNU has '
    'one cluster (INFERRED: the kernelcache\'s parser has the skip and limit tests of the public source), so complex 1 is out of range and asserts; boot 21, with six CPUs and two '
    'clusters, passed this driver. The guest\'s device tree lists two complexes whatever cpus= says, so no guest-data change keeps a one-CPU topology past this driver (INFERRED): cpus=2 to 4 '
    'leave one cluster as well (a prediction of the same host replay, not run), and cpus=5 or cpumask=0x11 pass the assertion but start a CPU and meet the CPU-start write again (INFERRED).',
    'UNKNOWN: the value of the complex count at the stop (nothing read it) and whether the hardware itself, with cpus=1, asserts the same way. INFERRED, read on the host after boot 24 (a file '
    'and not a run): each nested panic repeats because the kernel\'s panic-record routine stores through a pointer that stays NULL when the guest\'s device tree has no panic-log region, so it '
    'faults the same way each time, and the monitor lets the kernel\'s own page faults through (probes/xnuboot/trap_dispatch.c:370-387): the repetition is the kernel\'s and not an artefact of '
    'the monitor.'))
VERDICTS = ('PANIC TEXT',)      # the verdicts of rung 4 that this record is for: its stop was the panic of the PMGR driver and no other result leads here


def rung4_lines():
    """The paragraph of section 0a that is rung 4's result: the keyed block. AssertionError for a verdict that this record is not for, and for a paragraph that breaks the record's rules
    (ASCII, no claim without a label, never the word that the project keeps for what an independent run showed)."""
    assert RUNG4['verdict'] in VERDICTS, 'rung 4\'s verdict is not one that rung 5 is the record for'
    for paragraph in RUNG4['words']:
        assert paragraph.isascii() and 'VERIFIED' not in paragraph, 'a paragraph of the keyed block is not ASCII or uses the word that M1 records never use'
        assert any(label in paragraph for label in ('OBSERVED', 'INFERRED', 'UNKNOWN')), 'a paragraph of the keyed block has no label'
    return (*m1t.wrap(f'Rung 4 (record {RUNG4["record"]}, {RUNG4["boot"]}; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 4 {RUNG4["verdict"]}.'),
            *(line for paragraph in RUNG4['words'] for line in m1t.wrap(paragraph)))


# ---- section 0 -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
DECIDES = ('APPROVING THIS TEXT ALSO DECIDES THREE THINGS THAT NO RUN HAS HAD TOGETHER. (1) A MONITOR THAT ACKNOWLEDGES THE GUEST\'S CPU-START STORES, WHICH IS EXPERIMENTAL AND NOT A '
           'MODEL OF THE HARDWARE (section 3c): no CPU is started, the three stores are dropped, and no store class is added (section 3). (2) A guest command line with '
           'cpu_boot_timeout_secs=120 and without cpus=1, so that the guest keeps its six CPUs. (3) The time box of D5\' and D8\' (below), for a guest that, with the acknowledge, can run on past '
           'a point that no run has passed: what it can reach there is what every run of this monitor can reach, the existing stage-2 map and the monitor\'s models and report window, with '
           'no new register, no new system-register class and no new store class (read on the host in compat/cpu-start-ack.h, a file and not a run; the census of section 3 is the plain '
           'monitor\'s but for the addresses of six data words), and this approval is for that reach for up to the box. The panel as the console (boot 21) and the monitor\'s let_panic word '
           '(boot 24) have run; this combination has not.')
RUNG = ('rungs 1 to 4, the first run with the panel withheld, the panel as the console (rung 2, boot 21), the same with cpus=1 (rung 3, boot 23) and with cpus=1 and let_panic (rung 4, '
        'boot 24), whose records have their own approvals, spent: this record is rung 5, the panel handed to the guest as the kernel\'s console (serial=2) with the CPU-start stores '
        'acknowledged, cpu_boot_timeout_secs=120 in its command line and the monitor\'s let_panic word set, and no approval covers another\'s boot')
CONSOLE = ('The guest is given the panel (section 3c) and its command line is serial=2 with cpu_boot_timeout_secs=120, its let_panic word is set and the monitor acknowledges its CPU-start '
           'stores: its console is the panel (boots 21 and 24: the kernel\'s log and panic text were on it, OBSERVED once each) and the MON CON lines are expected to carry at most the first '
           'lines of a panic text (boot 24: OBSERVED once; boots 21 and 23 had none); what the acknowledge and let_panic do to this run is INFERRED and has not been seen.')
RAN = ('has never run on this machine in its acknowledging build; the plain build ran in boots 20, 21, 23 and 24 (section 0a).')
FAULT_SEEN = (f'OBSERVED twice: boot 21 stopped that way at A={r3.page_hex(r3.BOOT21_STOP["a"])}, which section 0a INFERS to be the first CPU-start write, and boot 24 at '
              f'A={r3.page_hex(BOOT24_STOP["a"])}, after its panic text')
FAULT_NOTE = ' (not when A= lies inside the panel range or in the three CPU-start words 0X300734004..0X30073400C: section 5d)'
STOP_SEEN = {**rp.STOP_SEEN, 'S2_FAULT': 'OBSERVED twice (boots 21 and 24, one unit, one boot each).',
             'PANIC_ENTRY': 'Never seen (UNKNOWN). Boot 24\'s panic printed its text without one (INFERRED); with serial=2 the panic text is expected on the panel (boot 24: OBSERVED once).'}


def d5_prime(box_s):
    return (f'D5\' (decided by the operator on 2026-11-18, the answer "Yes, up to 60 s"): this record, the one after rung 4, may use a time box of up to {BOX_MAX_S} s, with AC power, one CPU '
            f'running (the guest\'s tree lists six, and the acknowledge starts none) and no temperature readout (D8\'); it is approved by its own SHA-256, and its box is {box_s} s')


def why_lines(real):
    """Section 0's paragraph for this record: why rung 5, the three changes, the prediction and what falsifies it. Boot 24's facts are section 0a's (stand_extra)."""
    box_s = real['panel_facts']['run']['box_s'] if real.get('panel_facts', {}).get('run') else m1.RUNG5_BOX_MAX_S
    s = r3.BOOT21_STOP
    return m1t.wrap(f'WHY RUNG 5. Rung 4 (boot 24) printed the kernel\'s panic text: an ApplePMGR assertion on the number of CPU complexes, {BOOT24_S} s into the call, and the monitor then stopped at an '
                    f'access at IPA {r3.page_hex(BOOT24_STOP["a"])}, INFERRED to be a read in the kernel\'s chip-reset path (section 0a). The host analysis of that panic (INFERRED; read from the kernelcache file and the public '
                    'XNU source, a file and not a run) says that it follows XNU\'s topology: with cpus=1 XNU has one cluster and the PMGR driver\'s start enables two complexes; no guest-data change '
                    'keeps a one-CPU topology past that driver. SO THIS RECORD DROPS cpus=1: the guest has its six CPUs again, as in boot 21, which passed that driver and stopped at the first '
                    f'CPU-start write, IPA {r3.page_hex(s["a"])} (INFERRED to be XNU starting CPU 1). THIS RECORD CHANGES THREE THINGS FROM RUNG 4, TOGETHER. (1) THE MONITOR ACKNOWLEDGES THE '
                    'CPU-START STORES: with this monitor (SNAPMON-M1-ACK.BIN) the guest\'s 32-bit stores to offsets 4, 8 and 12 of the PMGR CPU-start block (IPA 0X300734000) are taken and their '
                    'value ignored, and the guest runs on; NO CPU IS STARTED, nothing ever answers "started", and the three stores are DROPPED. That is an EXPERIMENTAL DIAGNOSTIC and NOT A MODEL '
                    'OF THE HARDWARE: on the silicon the write starts a core, and the kernel then waits for it to announce itself (INFERRED from the public XNU source, not read in this kernel). How '
                    'many stores the monitor took is UNKNOWN to the page and to the film (the plain reporter does not print it). (2) The command line gets cpu_boot_timeout_secs=120 and has no '
                    f'cpus=1, so that XNU waits for the CPU that never announces itself for 120 s, longer than the {box_s} s box, instead of panicking about 1 s after the start (INFERRED from the '
                    'public XNU source; the name is a string in the kernelcache). (3) The time box is longer: ' + d5_prime(box_s) + '. The monitor\'s let_panic word stays set, as in boot 24, so that a '
                    'panic text prints. THE PREDICTION, ITS FALSIFIERS AND WHAT THE FILM DECIDES are in section 5d, in the operator\'s words (INFERRED; section 3c has the evidence).')


def stand_extra(real):
    """The paragraphs that section 0a gets after boot 20's: boot 21's and boot 23's, condensed to what this record still needs, and rung 4's keyed result."""
    s = r3.BOOT21_STOP
    return (*m1t.wrap(f'Boot 21 (rung 2, record {r3.BOOT21_RECORD}; one unit, one boot; the operator\'s film, read at full resolution) ran the monitor entry with the panel as the kernel\'s console: the '
                      f'call returned after {r3.BOOT21_S} s and the run ended as MON STOP S2_FAULT PC={r2.pc_text(s["pc"])} A={r3.page_hex(s["a"])} B={r3.page_hex(s["b"])}, INFERRED to be the '
                      'kernel\'s first CPU-start write (compat/boot-relocate.h:147-153); that PC is the one store of ApplePMGR\'s base register-write routine, on the path cpu_boot_thread, processor_boot, '
                      'cpu_start, enableCPUCore and configMiscCores (read on the host in the kernelcache, a file and not a run). The panel showed the kernel\'s own boot log from its start, with a processor '
                      'line for each of the six CPUs of the guest\'s tree and no MON CON '
                      f'line (serial=2). The log is noisy, and the noise is not the failure: {r3.NOISE} were on the film (OBSERVED once: boot 21); that they come from the monitor\'s empty models of '
                      'those devices is INFERRED. Rung 2 PASSED.'),
            *m1t.wrap(f'Boot 23 (rung 3, record {rp.BOOT23_RECORD}; one unit, one boot; the operator\'s film) added cpus=1: it was honoured (one "processor 0" line and none for the processors 1 to 5: '
                      f'OBSERVED once), the CPU-start stop was gone, and the call ended {rp.BOOT23_S} s in as MON STOP GUEST_EXC PC={r2.pc_text(rp.BOOT23_STOP["pc"])} A={r3.page_hex(rp.BOOT23_STOP["a"])}: '
                      'XNU\'s own debugger-trap word, which the kernel executes when it panics (INFERRED, read on the host); the monitor stopped there, so no text was printed. The SPMI and RTKit '
                      'lines of boot 21 were on its film as well (OBSERVED once). Boot 24 showed what that panic was (below), INFERRED to be the same one.'),
            *rung4_lines(),
            *m1t.wrap('The stick after boots 21, 23 and 24 was unchanged but for the Shell\'s marker and the Mac\'s own .fseventsd entry, and REPORT.DAT kept its all-zero staged digest: a run does '
                      'not write the report back, so the page on the panel is its only record (OBSERVED once each).'))


def hazard(real, box_s, wait_s):
    """The panel's bullet of 0b: the console on the panel is boots 21's and 24's; the path of the guest is one that no run has taken, and the panic path is boot 24's."""
    return ('The guest can write the unit\'s framebuffer, which the display scans out, and the kernel draws its console there (OBSERVED once: boots 21 and 24): from the MON CALL line the panel is '
            'cleared and fills with the kernel\'s log (the kernel\'s console clears the whole panel when it takes it: public source, not this kernel\'s own), and its text can paint over the '
            'page, so that the report can be hard to read. The film is the only record of that text and of the lines printed before the guest ran (section 5d). The monitor acknowledges the '
            f'guest\'s CPU-start stores (section 3c) and no CPU starts: what a stock kernel does when its CPU never announces itself, for up to {box_s} s, is UNKNOWN (it may wait, panic or spin), '
            'and with let_panic set a panic runs the kernel\'s own panic path, which boot 24 showed printing its text (OBSERVED once) before the monitor stopped it at a device that has no model. '
            f'The monitor ends the run at the box or at its own stop either way (section 5d). {r2.force_off(wait_s)}.')


def film(real, box_s, wait_s):
    """Step 2 of section 5c, after rung 2's sentences: the film is long for a long box, and runs two minutes after the stop."""
    return (f'{r2.FILM_STEP} The film runs about {film_total_s(box_s)} s for this box (the runner\'s loading, the call of {box_s} s, the stop and {FILM_AFTER_S} s after the report '
            'page: an estimate, not a measurement): have the camera\'s storage and battery ready for it.')


def film_until(real, box_s, wait_s):
    """Step 2 of section 5c: the film runs until 120 s after the report page, as the coordinator reads the panel from the film alone."""
    return (f'until {FILM_AFTER_S} seconds after the SNAPREP report page has appeared, or, if it never appears, until {FILM_AFTER_S} s after the last page change')


STEP6 = (f' For this record wait until {FILM_AFTER_S} seconds after the SNAPREP page appeared, by the film, before that FORCE OFF (a margin, not a measurement). Before that FORCE OFF, and '
         f'after those {FILM_AFTER_S} seconds, take three sharp still photographs of the part of the panel left of the report page (about its left 55 percent), close and without glare, one of its top '
         'band, one of its middle and one of its bottom, so that the text on it can be read (asked, not required; section 5d).')
# The wait after the STAGE=DONE line in the silence bullet of section 5 (tools/sp11_stick_bundle_record.py m1_silence_rule): the film of this record runs FILM_AFTER_S past the report page
# (step 6 above), so a page that has stopped is not FORCE OFF'd before; every other kind says the 60 s of SILENCE_AFTER_PAGE_S of the guide, which this key does not touch.
SILENCE_TAIL = (f'The wait is {FILM_AFTER_S} s after the STAGE=DONE line for this record (the film of the kernel\'s text: step 6 of section 5c): a lit page that has stopped there, with or '
                'without the SNAPREP page, is FORCE OFF then (section 5c)')


def step4(guest, label):
    """5c, step 4: the programs' digits differ from every earlier run's in this record (SNAPMON-M1-ACK.BIN), so the page's INDEX=0 line, the GUEST.DAT line, the label and the chunk count are compared,
    and rung 4's bundle, which has the same GUEST.DAT but for two pages and another SNAPMON, is the wrong one. PIN.DAT carries this record's SHA-256, so its own SHA is not in this text (section 1)."""
    return (f' Then compare, on the PAYLOAD LOADED line, INDEX=0 ... SHA=the first sixteen digits of SNAPMON-M1-ACK.BIN in section 1 (boots 20, 21, 23 and 24 had SHA={FIRST_MONITOR}: '
            f'a page that still says that has the old monitor), INDEX=2 ... BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, '
            f'LABEL={label} CHUNKS={guest["chunks"]} (boot 24\'s page had LABEL={BOOT24_LABEL} CHUNKS=46, INDEX=2 ... SHA={BOOT24_GUEST} and INDEX=3 ... SHA={BOOT24_PIN}, the SHA of its PIN.DAT: '
            'rung 4\'s bundle has the same size and the same chunk count and only those digits, the SNAPMON digits and the label tell it from this one; this record cannot name its own PIN.DAT SHA, '
            'which depends on this record\'s SHA-256).')


def guide_words(base):
    """The guide's PANEL entry of this record: rung 4's (`base`), with the words that this module says again."""
    return {**base, 'decides': DECIDES, 'hazard': hazard, 'rung': RUNG, 'why': why_lines, 'stand': stand_extra, 'stand_head': STAND_HEAD, 'console': CONSOLE, 'ran': RAN,
            'stop_seen': STOP_SEEN, 'fault_seen': FAULT_SEEN, 'fault_note': FAULT_NOTE, 'film': film, 'film_until': film_until, 'step4': step4, 'step6': STEP6, 'silence_tail': SILENCE_TAIL}


# ---- section 3 and 3c ------------------------------------------------------------------------------------------------------------------------------------------------------------------
def console_text(panel):
    """The Console paragraph of section 3c for this record: boots 21 and 24 showed the console on the panel, so the long evidence of rung 2's paragraph (the loader's comments, the three
    console modes) is kept to what this record rests on; rung 2's and rung 3's records have the long one."""
    return ('Console. What the operator saw in boots 21 and 24 (OBSERVED once each) and expects to see again (INFERRED): XNU\'s own boot text, drawn by the kernel itself into the panel while the '
            'guest runs. How: the command line has serial=2 where the loader\'s has serial=3, the replacement that probes/run.py:1591-1594 makes for --console panel; the builder makes it on '
            'the loader\'s line before the guest is built (tools/sp11_stick_bundle_chunks.py, real_built) and reads the command line back from boot_args (PANEL_MISMATCH if it is anything else, serial=3 '
            'included), because with bit 0 of serial set and a working UART the kernel\'s printf goes to the serial line and the panel is never its console (probes/xnuboot.py:167-187 and '
            'probes/xnuboot.py:1961-1969, the loader\'s own comments for this kernelcache). The MON CON lines are not expected to carry the ordinary log (boots 21 and 23 had none, OBSERVED once '
            'each): the first 1,024 bytes of console that SNAPREP-M1-ACK.BIN prints as at most 6 MON CON lines of 64 characters (probes/sp11-stick-reporter.c:244-245) held, in boot 24, the first lines '
            'of the panic text (OBSERVED once); whether kprintf text reaches them is UNKNOWN. A kernel that never takes its console leaves the panel as it was.')


def screen_text(height, width):
    """The paragraph 'The screen' of section 3c for this record: what the runner's page does to the same pixels and what that leaves of the kernel's text, kept to what the film's rules rest on."""
    return ('The screen. What the runner\'s page does to the same pixels, read in q1n1\'s runner (platform/uefi/main.c: screen_char, payload_emit, sp11_draw_uptime and the clear at the runner\'s start), '
            'INFERRED for this run because nothing of it was run and this record does not check that source: the runner paints the whole panel dark once, when it starts, and never clears the panel '
            'again; a character is drawn as foreground pixels only (no background, nothing erased), so the report that SNAPREP-M1-ACK.BIN draws after the stop only adds glyph pixels over whatever the '
            'kernel left in its region and clears nothing; after the report the runner\'s heartbeat redraws, once a second, a blinking block at the bottom left and its UPTIME line at (20, 74), clearing '
            'that line\'s 576 x 27 pixels first. The kernel\'s console is expected to CLEAR the whole panel when it takes it (read in the public source, below; INFERRED for this kernel), which wipes '
            'the runner\'s page and the wrapper\'s lines drawn before the call: only the film holds them, and what the kernel leaves stays in the framebuffer until something paints over it (INFERRED). '
            'Its cell is 8 x 16 pixels, read on the host (a file, not a run) in the kernelcache (initialize_screen, at 0xfffffe000ba55740), so '
            f'{height // 16} rows of {width // 8} columns on this panel; the console scrolls when its text passes the last row, and what scrolled off is on the film only (INFERRED).')


def kind_lines(panel):
    """The lines that section 3 adds after the kind's own line."""
    return m1t.wrap('This record is of the choice that hands the guest the panel as the kernel\'s console (serial=2) with cpu_boot_timeout_secs=120 in its command line, with the acknowledging '
                    'monitor and the monitor\'s let_panic word set (section 3c): the ram class names its range and no class is added. The monitor takes the guest\'s 32-bit stores to offsets 4, 8 and 12 '
                    'of the PMGR CPU-start block (IPA 0X300734000) and ignores their value; it writes only its own RAM and its report window, so the store classes are the same three (D1b of '
                    'docs/SP11-STICK-MONITOR.md), and no CPU is started: "a secondary CPU" below stays not authorised. EXPERIMENTAL, not a model of the hardware.')


def in_short(panel):
    """The summary of section 3c."""
    box_s = panel['run']['box_s']
    return ('In short. This record is rung 5, after boot 24 (rung 4), whose panic in the PMGR driver the host analysis reads as XNU\'s one-cluster topology under cpus=1 (section 0a): the guest has its '
            'six CPUs again, the monitor ACKNOWLEDGES the guest\'s CPU-start stores (SNAPMON-M1-ACK.BIN: EXPERIMENTAL, not a model of the hardware, no CPU is started, the three stores are dropped), '
            f'the command line has cpu_boot_timeout_secs=120, let_panic stays set and the box is {box_s} s (D5\'). What it expects, what falsifies that and what counts as a result are in section 5d, '
            'in the operator\'s words; this section is the evidence for them.')


def programs(panel):
    """The sentence of the introduction on the programs: what is not rung 4's and boots 20 to 24's."""
    run = panel['run']
    return (f'The programs: SNAPMON-M1-ACK.BIN has the SHA-256 {run["monitor"][:16]}...: the wrapper with the acknowledging monitor ({ACK_MONITOR}, 608 bytes longer than the plain one, '
            f'{PLAIN_MONITOR}), built with the box cap at {m1.BOX_MAX_S} s in place of 5 s (SP11_STICK_MONITOR_BOX_MAX_S, section 4). SNAPREP-M1-ACK.BIN is the bytes of SNAPREP-M1.BIN, the reporter of the '
            'earlier runs' + (f' ({r2.RUNG1_REPORTER[:8]}...)' if run['same_reporter'] else f' (NOT here: {run["reporter"][:16]}...)')
            + ': nothing in the reporter reads the acknowledge, so its page says nothing of it (read on the host, a file, not a run). A record of the plain pair names other digests, and the '
              'builder refuses a report of the other choice (M1_REPORT_MISMATCH).')


def intro(panel):
    """The introduction of section 3c: rung 4 is the run before; this record changes the guest's input and the monitor."""
    box_s = panel['run']['box_s']
    return (f'Rung 4 is the run before this one: the panel as the console with cpus=1 and let_panic, record {BOOT24_RECORD}, boot 24 (section 0a); its approval is spent. This record is rung 5: its '
            f'command line has cpu_boot_timeout_secs=120 where rung 4\'s has cpus=1, its monitor acknowledges the CPU-start stores, and its box is {box_s} s (D5\'). It is not decided: approving the '
            f'SHA-256 of this record is that decision, and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); rung 4\'s approval covers none of it. {programs(panel)} '
            'SNAPMON.BIN (the acknowledging monitor), GUEST.DAT (the command line in boot_args and the time box in PARAMS), PIN.DAT (the label and the digests) and PAYLOAD.MAN, which lists them, '
            'differ from rung 4\'s; SNAPREP.BIN does not. Section 1b has the programs\' digests and the NV2 record, which is the first run\'s. Section 5 has the rule that rung 2 had.')


def label(panel, label_text):
    """The label paragraph of section 3c."""
    mark = m1.BOOT_ARGS[panel['boot_arg']]
    return (f'Label: PIN.DAT\'s build_label is {label_text}. The builder puts {mark} after M1RUN- for this record, in place of CONSOLE-, and refuses a facts label that starts with it, with '
            'CONSOLE-, CPUS1-, LETPANIC- or PANEL-, so that the label of an earlier record cannot read as this one (PIN.DAT\'s label holds 23 characters); the page prints it on its SNAPMON '
            'STAGE=PIN_OK line (LABEL=; probes/sp11-stick-wrapper.c:199-202). The wrapper checks M1RUN- and no more (docs/SP11-STICK-MONITOR.md section 14, D2): the rest of the label is the '
            'builder\'s rule and the operator\'s check, and an edited PIN.DAT carries any label.')


def other_differences(panel):
    """The sentence of section 3c on what else differs: the guest's pages."""
    return ('Other differences from the withheld guest follow from the panel\'s address, which lies below the bank: the guest\'s frame map and tables are wider (docs/SP11-STICK-REAL.md '
            'section 2: 46 chunks against 44 in the host run that table records). From rung 4\'s guest this one differs in the command line in boot_args (cpu_boot_timeout_secs=120 where '
            'rung 4\'s has cpus=1) and the time box in PARAMS, and in nothing else: the same size and the same chunks, and its own SHA-256 (docs/SP11-STICK-REAL.md section 2).')


def word_text():
    """The paragraph of section 3c on the command line: the one word, what XNU does with it (INFERRED) and why cpus=1 is gone."""
    return (f'The extra word. The command line is the loader\'s with serial=2 and, after it, cpu_boot_timeout_secs=120 ({LINE}): one word, from the builder\'s list (--boot-arg: cpus=1 with --cpu-start '
            'stop and cpu_boot_timeout_secs=120 with --cpu-start ack, BOOT_ARG_REFUSED for any other word or pair), read back from boot_args by the builder (PANEL_MISMATCH for any other line). The '
            'name is a string in the kernelcache and its variable is a 32-bit tunable with the default 1 and no clamp (read on the host, a file, not a run); that the boot-args set it as the public source '
            'says is INFERRED. What it does is read in the kernelcache and in the public source (tag xnu-12377.121.6, not this kernel\'s own), and is INFERRED where it rests on the public source: the '
            'kernel\'s CPU-boot thread calls processor_boot for all five secondary CPUs first, and processor_boot does not wait; only then, in that dedicated thread, sched_cpu_init_completed waits '
            'for each CPU in turn, CPU 1 first (processor_wait_for_start: a wait of cpu_boot_timeout_secs times debug_cpu_performance_degradation_factor seconds, a 32-bit product, the factor being 1 '
            'here because the staged device tree has no target-is-fpga: read on the host) and panics "cpu 1 failed to boot for the first time, waited N seconds" when it times out: about 1 s after '
            'the last start by default, and not inside the box with 120. kernel_bootstrap_thread does not wait for the CPUs, so IOKit matching, bsd_init and the root mount are not blocked by that wait '
            'in the source; what waits is the resource IOAllCPUInitialized, published after sched_cpu_init_completed returns (the public consumer is the exclaves start; kext consumers are not '
            'enumerated: UNKNOWN), so the kernel is expected to go on on the boot CPU (INFERRED). cpus=1 is not in this line: with it '
            'boot 24 asserted in the PMGR driver (section 0a), and no guest-data change keeps a one-CPU topology past that driver (INFERRED). This record carries no cpus= and no cpumask= (XNU takes '
            'the two together as an incorrect configuration, and --boot-arg passes one word). This record is EXPERIMENTAL, and a new operator decision.')


def ack_paragraphs(panel):
    """The paragraphs of section 3c on the acknowledge: what it takes, what it does not, what it lets the guest reach."""
    return (
        'The acknowledge. Boot 21\'s stop was a 32-bit store to IPA 0X300734004 at PC 0XFFFFFE00097A352C (section 0a), the one store of ApplePMGR\'s base register-write routine (read on the host in the '
        'kernelcache, a file and not a run), which AppleT8140PMGR\'s override reaches by default (the defaults of its optional hooks are UNKNOWN): the writes at +8 and +0XC are the same instruction and '
        'decode exactly like it (INFERRED). The kernel\'s start path is cpu_boot_thread, '
        'processor_boot, cpu_start, enableCPUCore and configMiscCores, and after the write there is no read, poll, retry or wait in it (read on the host), so the acknowledge is enough. Each of '
        'the five secondary CPUs is one global word (+4) and one cluster word (+8 for CPUs 1 to 3, the E cluster, +0XC for CPUs 4 and 5, the P cluster), INFERRED from the loop structure of that '
        'path and from q1n1\'s start code: N=10 AT4=5 AT8=3 AT12=2 in the monitor\'s window (INFERRED, a prediction that nothing in this run can confirm or refute: the page does not print the '
        'count; 10 to 15 stores in all, the order per CPU not established). The monitor takes such a store only when it is a valid-syndrome 32-bit store at exactly one of the three addresses, as a stage-2 translation fault, '
        'with no secondary CPU configured; any other access keeps the stop (compat/cpu-start-ack.h:56-75 and probes/xnuboot/trap_dispatch.c:451-460; INFERRED from those sources, a file and '
        'not a run): a read, another width or another offset, a fault of another kind (not a fault of the stage-1 walk), a syndrome that does not describe the access, and an HPFAR that names '
        'another page. That only a single-register load or store gives a valid syndrome (a pair, a writeback form, an atomic, a SIMD store or DC ZVA does not) is INFERRED from the Arm '
        'architecture text as remembered, not read here. The monitor changes nothing of the guest but its resume address (ELR, 4 bytes on) and writes nothing outside its own RAM and report '
        'window: no register of the guest, no stage-2 entry, no device, no system register. It counts what it took in that window; this record\'s SNAPREP does not print the block, so how many '
        'stores it took is UNKNOWN to the page and the film, and no result of this record rests on it.',
        'What the acknowledge lets the guest reach. A guest that is not stopped at the CPU-start write runs on past a point that no run has passed, for up to the box. What it can then touch is '
        'what every run of this monitor can: the existing stage-2 map (its RAM and reserve, the panel range, the monitor\'s work area read-only) and the monitor\'s models and report window; the '
        'acknowledging build adds no register, no system-register class and no store class (read on the host: its census is the plain monitor\'s but for the addresses of six data words), and a '
        'guest that stores forever to the block costs one trap each, ended by the box. The approval of this record covers that reach for up to the box (section 0). Which device the kernel touches '
        'first after the acknowledged stores is UNKNOWN (the host analysis expects the video decoder\'s window at IPA 0X380001000 first, INFERRED): the six CoreSight lock models of the staged PARAMS cover the key write that cpu_start makes for CPUs 1 to 5, '
        'and the CPU nodes\' acc-impl-reg and cpu-uttdbg-reg windows have no model, but the start path does not touch them (read on the host). A device that has no model is a stage-2 fault and ends '
        'the run, as in every run.')


def extra_paragraphs(panel):
    """The paragraphs of section 3c that only this record has, after the command line's: the PARAMS word, the acknowledge, what still stops the run, the panic path and what the page can show."""
    if not panel.get('let_panic'):
        return ()
    params_word, still_stops = rp.extra_paragraphs(panel)[:2]
    params_word = params_word.replace('and compares the finished PARAMS chunk with the one it makes for rung 3: they differ in this word and in no other (PARAMS_LAUNCH)',
                                      'and compares the finished PARAMS chunk with the one it makes for the same guest without the word: they differ in this word and in no other (PARAMS_LAUNCH)')
    assert 'the same guest without the word' in params_word and 'LET_PANIC_REFUSED for any other combination' in params_word, 'the PARAMS paragraph of rung 4 has changed under rung 5'
    params_word = params_word.replace('The builder sets the word for this record only (--let-panic: LET_PANIC_REFUSED for any other combination)',
                                      'The builder sets the word for rung 4\'s and this record only (--let-panic: LET_PANIC_REFUSED for any other combination)')
    assert still_stops.count('probes/xnuboot/trap_dispatch.c:472') == 1, 'the paragraph of rung 4 on what still stops the run has changed under rung 5'
    still_stops = still_stops.replace('probes/xnuboot/trap_dispatch.c:472', 'probes/xnuboot/trap_dispatch.c:491')      # the acknowledge's 19 lines moved that call (CITE_DRIFT)
    return (
        params_word,
        *ack_paragraphs(panel),
        still_stops + ' In boot 24 the text printed with no such stop before it (INFERRED: no call was refused after the panic entry).',
        'What the panic path may reach (boot 24 showed it once, OBSERVED; the rest is INFERRED): XNU draws its panic text with the video console into the panel range after its panic-entry call, '
        'and boot 24 printed it, then nested panics with an empty string, "wdog: panic chipreset", and a 32-bit read at IPA 0X3082BC224 that has no model: MON STOP S2_FAULT. Any panic of this '
        'run can end the same way, at a read in the kernel\'s chip-reset path, which is then a later stop and not a result of its own (section 5d). The A16\'s desktop configuration sets let_panic '
        'with --panic-serve, which serves calls after the panic entry (docs/RUNNING.md, section "Run flags"); this monitor does not serve after the entry (probes/xnuboot/sptm_dispatch.c:35-37) '
        'and nothing here prints the panic record, so the film is the only full reading.',
        'What the page can and cannot show. It prints the first five events of the monitor\'s log, the counts and the stop line, which the runner clips at 100 columns (C= may lose its last '
        'digits), and the first lines of the console ring as MON CON lines (boot 24: the first lines of the panic text, OBSERVED once). It does not print the rest of the panic text, the number of '
        'exceptions that the monitor let through or the number of acknowledged stores (they are in the RAM window and in no line of this reporter: UNKNOWN to the page and the film).')


def expect_text(run):
    """The paragraph of section 3c that stands where rung 3's and 4's what-the-run-may-end-as is: the prediction and its falsifiers are section 5d's, in the operator's words, and only there."""
    return ('What the run may end as. The prediction (INFERRED from the host analysis of boots 21 and 24: none of it has been seen with the acknowledge), its falsifiers and the other ends are in '
            'section 5d, in the operator\'s words (WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT), and what the film decides is there as well; which end comes first, and when, is UNKNOWN. '
            'A restart of the Surface is NOT A RESULT for M1 (section 5a) and NOT PASSED for rung 5 (section 5d); whether a restart comes inside a box of this length is UNKNOWN (section 4). In '
            'every case the operator does nothing but film (section 5d).')


# ---- section 5d ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def film_total_s(box_s):
    """About how long the film runs for a box of `box_s` s: the runner's loading (about 60 s), the call, the stop and FILM_AFTER_S after the report page (an estimate, not a measurement)."""
    return box_s + 60 + FILM_AFTER_S


def section_5d(panel, wait_s):
    """Section 5d of the record, the operator's words: tools/sp11_stick_bundle_panel_rung5d.py, which takes its constants from this module (so it is imported here, when asked)."""
    import sp11_stick_bundle_panel_rung5d as d
    return d.section_5d(panel, wait_s)


# What section 3c and the sections after it cite, by path:line (held to the sources, in both directions, by tools/test-sp11-stick-monitor-rung5.py): the acknowledge's match and its call site.
CITES = (
    ('compat/cpu-start-ack.h', 56, 75, ('cpu_start_ack_match', '(esr >> 26) != CPU_START_ACK_EC_DABT', '!access->valid || !access->write || access->size != 4', 'at != CPU_START_ACK_GLOBAL')),
    ('probes/xnuboot/trap_dispatch.c', 451, 460, ('if (!c->host_cpus) {', 'cpu_start_ack_match(esr,ipa,hpfar,&a,&register_at)', '*elr+=4;')),
    ('probes/xnuboot/trap_dispatch.c', 491, 491, ('record(c, EV_S2_FAULT, *elr, ipa, esr, far);',)),
    ('probes/xnuboot/sptm_dispatch.c', 35, 37, ('#ifndef SNAP_PANIC_SERVE', '#define SNAP_PANIC_SERVE 0')),
)
# The acknowledge's 19 lines at probes/xnuboot/trap_dispatch.c:446-464 moved the line that rung 4's approved text cites as :472 (MON STOP S2_FAULT's record call) to :491, which the text of rung 5
# cites (above). The text of rung 4's record is approved as it is (its SHA-256 is part of the proof that this change leaves it alone), so its suite holds its cite to the moved line:
# (path, the cited first line) -> lines of drift.
CITE_DRIFT = {('probes/xnuboot/trap_dispatch.c', 472): 19}
# The records of rungs 2 to 5 cite probes/sp11-stick-reporter.c at the lines that the reporter of their pair had (ac9f634). The reporter of rung 6 prints more, so the same code lies elsewhere in
# the tree; those texts are approved as they are (their SHA-256 is part of the proof that this change leaves them alone) and rung 6's own text cites the new lines
# (tools/sp11_stick_bundle_panel_rung6.py). The suites that hold a cite to its source read it through this table: the cite as written (path, first, last) -> (first, last, fragments) in the tree.
REPORTER = 'probes/sp11-stick-reporter.c'
REPORTER_DRIFT = {
    (REPORTER, 244, 245): (256, 257, ('RP_CONSOLE_LINES 6u', 'RP_CONSOLE_WIDTH 64u')),
    (REPORTER, 338, 360): (390, 433, ('n < RP_CONSOLE_LINES', 'pl_text(&l, "MON CON ")', 'if (!width) break;')),
    (REPORTER, 247, 271): (281, 305, ('case 6: return "WATCHDOG";', 'case 9: return "PANIC_ENTRY";', 'default: return "UNNAMED";')),
}
