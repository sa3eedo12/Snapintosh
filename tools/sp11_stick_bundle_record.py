# SPDX-License-Identifier: MIT
"""The authorisation record of an SP11 stick bundle (docs/SP11-STICK-BUILD.md section 6): the text the operator approves, whose SHA-256 is
PIN.DAT's authorisation_sha256, and the rules of the note that can be appended to it. It is here and not in tools/sp11_stick_bundle_build.py
so that the text the operator reads, and what every test and document says about it, has one home; the builder imports it and keeps the file
reading, the approval check and the pipeline.

The text is wrapped by hand, never by a library: its digest is the operator's approval, so it must not depend on how a library version breaks
lines, and a changed byte is another record. A record of a synthetic build must not change when a line about a real build is added: the
lines of section 1a exist only for kind `real`. In the same way the lines of section 1b, the touches of M1_TOUCHES, the paragraphs of
tools/sp11_stick_bundle_record_m1.py (the guest SMC and section 3b), the budget of section 4, the
silence rule of section 5 (M1_SILENCE_RULE, in place of SILENCE_RULE) and the two stop rules of M1_STOP_RULES exist only for a record of the monitor
entry run (--m1-run), and the records of the other kinds are what they were (tools/test-sp11-stick-bundle-record.py pins their digests).

Evidence: EXPERIMENTAL, hardware NOT RUN. Nothing here approves anything."""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_record_gic as gq  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402

ch.KNOWN_REFUSALS.add('NOTE_BAD')       # the builder lists it among its own refusals too
MIB = sb.MIB
STORES = {'ram': sb.STORE_RAM, 'el2_sysreg': sb.STORE_EL2_SYSREG, 'gic': sb.STORE_GIC}       # the classes --stores names, in the order the record lists them
DEFAULT_SEED = 'sp11-stick-bundle'      # the label `real` uses when --seed is not given: a default of the tool, not a decision of the operator
NOTE_LINES, NOTE_COLUMNS = 40, 120
PANEL_WORDS = {'withheld': 'the guest is given no framebuffer', 'handed': 'the guest is given the unit\'s framebuffer'}
HVC_WORDS = {0: 'a stock kernel', 1: 'the loader replaces the kernel\'s SPTM call words'}

# The sentences about the dry-run wrapper restate docs/SP11-STICK-WRAPPER.md section 4 and name it as their source; a build whose
# SNAPMON.BIN does more than that wrapper has to say so in a note (section 6 of the record).
TOUCHES = {
    'ram': ('RAM inside the placement only: the copy of the chunks, zero fills, the monitor work area and out buffer, and',
            'the report written into REPORT.DAT. No disk, no device, no RAM outside the placement. What the dry-run wrapper',
            'does to the machine is in docs/SP11-STICK-WRAPPER.md section 4: RAM stores only to page 0 of REPORT.DAT and,',
            'after the stage COPYING, to the chunk destinations; cache maintenance by VA; AT S1E2W and AT S1E2R (they set',
            'PAR_EL1); MRS reads; nothing else.'),
    'el2_sysreg': ('required in every PIN.DAT by the interface (STORE_REQUIRED), so always listed. The dry-run wrapper',
                   'stores no EL2 system register (docs/SP11-STICK-WRAPPER.md section 4: MRS reads, no MSR). A monitor entry',
                   'would write and restore HCR_EL2, VBAR_EL2, TPIDR_EL2, VTCR_EL2, VTTBR_EL2, CNTHCTL_EL2, MDCR_EL2 and the',
                   'OS lock (docs/SP11-MONITOR-PORT.md section 12; read from the sources, not run).'),
    'gic': ('GIC registers for the timer stop (PPI 26). Which registers is UNKNOWN until the wrapper (work item 4) and its',
            'register list (work item 6) exist: do not approve this class before they do.')}
# The same three classes for the GIC and stop dry run (--gic-stop; docs/SP11-STICK-GIC.md sections 4 and 5), where payload 0 is the wrapper
# built with SNAP_SP11_STICK_GIC=1. A register named here is one the binary's census lists (tools/test-sp11-stick-gic-build.py checks it).
GIC_STOP_TOUCHES = {
    'ram': ('RAM inside the placement only, as the dry-run wrapper: the copy of the chunks, zero fills and the report in',
            'REPORT.DAT. One more RAM store of the GIC and stop stage: its record, 1376 bytes at REPORT.DAT offset 0x11000',
            '(after the monitor\'s 64 KiB window), which is inside the report. No other RAM.'),
    'el2_sysreg': ('required in every PIN.DAT by the interface (STORE_REQUIRED). The GIC and stop wrapper writes these',
                   'registers of the boot CPU, each read first, put back and verified by read-back: class C ICC_PMR_EL1,',
                   'ICC_BPR1_EL1, ICC_IGRPEN1_EL1; class T CNTHP_CVAL_EL2 and CNTHP_CTL_EL2 (the EL2 physical timer, armed',
                   'for a time box of 2 s in the default build, in each of two windows, then disabled); class V VBAR_EL2 (the',
                   'wrapper\'s own vector table) and DAIF.I (cleared only inside a window of at most twice the time box); in',
                   'the handlers ICC_EOIR1_EL1 (ICC_DIR_EL1 when EOImode is set), SPSR_EL2 and ELR_EL2. Class E, only after a',
                   'first window passed and a second announcement and 8 s: HCR_EL2 (RW and IMO, TGE clear), VBAR_EL1 and',
                   'SCTLR_EL1 (MMU and caches off) for one ERET to a stub at EL1 that spins on CNTPCT_EL0 for at most twice',
                   'the time box and ends by HVC; its lines are made visible with DC CIVAC and IC IVAU. It writes no',
                   'CNTHCTL_EL2, TPIDR_EL2 or other register, executes no SMC, SVC or TLBI, and calls no monitor. Total',
                   'time of the two windows and the two 8 s waits: about 20 s.'),
    'gic': ('memory-mapped GIC registers in the pinned frames only (PIN.DAT gicd_base, gicr_first, gicr_stride,',
            'gicr_count). Class G: GICD_CTLR, only when the value read shows group 1 disabled: EnableGrp1A is set, and',
            'EnableGrp1 too unless DS is set (a read of 0x10 becomes 0x13); the old value is saved and put back. Class R: in',
            'this CPU\'s own redistributor (found by matching GICR_TYPER to MPIDR; the other',
            'frames are read for GICR_TYPER only) the SGI frame of INTID 26: IPRIORITYR byte, ISENABLER0 bit, ICFGR1',
            'field if it reads edge, IGROUPR0 bit if the distributor shows one security state, and the put-backs',
            'ICENABLER0 and ICPENDR0. Never the ITS, another interrupt, SMMU, PCIe, USB or the framebuffer.')}


TIME_FACTS = dict(wrapper_s=2.923, arm_wait_s=8, verify_s=0.96, verify_mib=141.4, stop_s=0.6)       # the dry run (docs/SP11-STICK-WRAPPER.md section 12), SW_GIC_WAIT_S, and the plan's estimate of the stop
# The M1 run (--m1-run; docs/SP11-STICK-MONITOR.md section 10): payload 0 is SNAPMON-M1.BIN, the wrapper with the monitor appended. What the monitor
# can write is what the disassembly of its binary holds, not what a reader of the sources lists: tools/sp11_monitor_census.py reads the names of
# the el2_sysreg class below out of the rendered record and compares them with both binaries in both directions (tools/test-sp11-stick-monitor-auth.py
# runs it against binaries built with a local test record). The groups `wrapper` and `monitor A` .. `monitor G` are that tool's grammar: a group
# starts at a line of six spaces and its label, and goes on over the lines indented further. Do not wrap a TLBI, DC, IC or AT operation across lines.
M1_TOUCHES = {
    'ram': ('RAM inside the placement and the tail of REPORT.DAT, as the GIC and stop dry run, and what the monitor and its',
            'guest add. The wrapper: the copy of the chunks and the zero fills; REPORT.DAT page 0 (the head), its GIC record at',
            '0x11000 and its monitor record at 0x12000; a 256 KiB stack for the call at REPORT.DAT 0x41000 to 0x81000, filled',
            'with a pattern, scanned after the call and zeroed. A stack that outgrows it writes below it, into REPORT.DAT',
            '0x13000 to 0x41000: that range must be zero before the call (else WINDOW_DIRTY, a refusal) and is scanned',
            'after it; a word in it is STACK FULL on the page, and the wrapper then zeroes it and cleans it (a store to',
            'REPORT.DAT, made only then). That the stack fits its area is INFERRED; one that went below the filler would',
            'reach the two record slots, which the wrapper compares afterwards (WINDOW OVERRUN).',
            'The monitor: its 64 KiB window (the out buffer of the call,',
            'REPORT.DAT 0x1000), its work area (work_size bytes at work_base: tables, logs, a console buffer), the page tables',
            'and frame tables it builds over the guest, and the cache maintenance that publishes them. The guest: its own RAM',
            'and the reserve above it, guest_base to work_base; its other accesses are meant to fault at stage 2 and stop the',
            'run (docs/SP11-MONITOR-PORT.md section 12; INFERRED from the sources, never run). No disk and no RAM',
            'outside the placement and REPORT.DAT. The monitor binary also holds code that stores to devices and calls',
            'firmware, behind gates that a guest can lift: section 3b says which, and that with the PARAMS this builder',
            'makes none of it runs (INFERRED).'),
    'el2_sysreg': ('required in every PIN.DAT by the interface (STORE_REQUIRED). The writers, in the order they act:',
                   'The registers are named as llvm-objdump names them. tools/sp11_monitor_census.py compares every name below with',
                   'the disassembly of both binaries, in both directions: an instruction that a binary has and no group names, and a',
                   'name that the binary lacks, are both failures; so is a register, an operation or a data word that is not in the',
                   'placed entries at the end of this class with the functions that hold it and how many times, or is held elsewhere;',
                   'and the ELFs it reads must be the wrapper and the monitor that section 1b names by digest. The builder does',
                   'not run it (it needs those ELFs). Which group is reached when is INFERRED from the sources (the monitor has',
                   'indirect branches that no static pass follows); no write below was run by the monitor on the unit (boot 11\'s',
                   'payload wrote CNTHCTL_EL2 and HCR_EL2: OBSERVED). The monitor reads CNTVOFF_EL2 and never writes it.',
                   'wrapper: ICC_PMR_EL1, ICC_BPR1_EL1 and ICC_IGRPEN1_EL1, each read first, set, read back and put back after the',
                   '   call (class C, with the gic class); IC IALLUIS once after the copy; DC CVAC and DC CIVAC by VA; AT S1E2R and',
                   '   AT S1E2W (they set PAR_EL1). It writes no other register. Before and after the call it reads DAIF, SPSel,',
                   '   VBAR_EL2, HCR_EL2, TPIDR_EL2, SCTLR_EL2, TCR_EL2, CNTHCTL_EL2, CNTHP_CTL_EL2, MDCR_EL2, VTTBR_EL2, VTCR_EL2 and',
                   '   VMPIDR_EL2 and compares them: a difference is reported, never refused.',
                   'monitor A, entry and stage-2 setup, before the NV2 gate, whether or not the gate allows: DAIFSet, VBAR_EL2,',
                   '   HCR_EL2, TPIDR_EL2, VTCR_EL2, VTTBR_EL2 with TLBI VMALLS12E1IS, VMPIDR_EL2, CNTHCTL_EL2, CNTKCTL_EL1; DC CIVAC',
                   '   publishes its tables and work area.',
                   'monitor B, only if the NV2 gate allows (D6; else the monitor stops before this with status -90 to -95), before the',
                   '   guest runs: VNCR_EL2, HCR_EL2 (NV and NV2), MDCR_EL2, MAIR_EL1, TCR_EL1, TTBR1_EL1, TTBR0_EL1, VBAR_EL1,',
                   '   ESR_EL1, FAR_EL1, SCTLR_EL1 (the guest MMU on, over the shadow tables), MDSCR_EL1, TLBI VMALLE1IS, AT S1E1R',
                   '   (sets PAR_EL1), DBGBVR0_EL1 DBGBVR1_EL1 DBGBVR2_EL1 DBGBVR3_EL1 DBGBVR4_EL1 DBGBVR5_EL1 (breakpoint values) and',
                   '   DBGBCR0_EL1 DBGBCR1_EL1 DBGBCR2_EL1 DBGBCR3_EL1 DBGBCR4_EL1 DBGBCR5_EL1 (their controls); OSLAR_EL1 only if',
                   '   OSLSR_EL1 reads the OS lock set (not taken on this unit: OSLSR 0x8, OBSERVED).',
                   'monitor C, the guest starts: HCR_EL2 (IMO and FMO), SPSR_EL2, ELR_EL2, ERET; CNTHP_CVAL_EL2 and CNTHP_CTL_EL2 arm',
                   '   the stop timer (the monitor arms it itself; the wrapper does not).',
                   'monitor D, while the guest runs, each only when the guest causes it. Interrupts: ICC_IAR1_EL1 (a read that',
                   '   acknowledges), ICC_EOIR1_EL1, ICC_DIR_EL1, CNTHP_CVAL_EL2, CNTHP_CTL_EL2. Exceptions given to the guest, and',
                   '   traps: ELR_EL1, SPSR_EL1, ESR_EL1, FAR_EL1, SPSR_EL2, ELR_EL2, HCR_EL2 (read-modify-write), MDSCR_EL1 (single',
                   '   step), AT S1E1R with PAR_EL1, SP_EL0 and SP_EL1 (an emulated paired access to a modelled device), TTBR0_EL1 and',
                   '   TCR_EL1 (a user translation root), CNTKCTL_EL1. Stage 2 and the caches: VTTBR_EL2 with TLBI VMALLS12E1IS,',
                   '   TLBI ALLE1IS, DC CIVAC, DC CVAU, IC IALLUIS.',
                   'monitor E, the stop and the report, on every run that reached the monitor, refusals included. Puts back,',
                   '   from the values saved at entry: VBAR_EL2, HCR_EL2, TPIDR_EL2 (last), SCTLR_EL1 (zero first, translation',
                   '   off), TCR_EL1, DAIF; from the values saved in prepare: VTCR_EL2, VTTBR_EL2, VMPIDR_EL2, MDCR_EL2, MDSCR_EL1,',
                   '   MAIR_EL1, TTBR0_EL1, TTBR1_EL1, CNTHCTL_EL2 and CNTKCTL_EL1 (the last two only if prepare saved the clock),',
                   '   the six breakpoint pairs of group B and OSLAR_EL1 (only if the lock was set at entry); stops the timer with',
                   '   CNTHP_CTL_EL2; TLBI ALLE1, TLBI ALLE1IS, TLBI VMALLS12E1 and TLBI VMALLS12E1IS; DC CIVAC over the guest',
                   '   range and the reserve.',
                   '   IF PREPARE REFUSED BEFORE IT SAVED THEM (for example MON STATUS -24, -15, -14, -22 or -17; a status such as',
                   '   -22 can come from either side, so the rows tell), the saved values are zero and the monitor writes ZEROS,',
                   '   not what the registers held, into MDCR_EL2, VTCR_EL2, VTTBR_EL2, VMPIDR_EL2, MDSCR_EL1, MAIR_EL1, TTBR0_EL1',
                   '   and TTBR1_EL1: expect CHANGED rows for the first four on such a status (the other four have no row). That',
                   '   this is harmless to the runner, which is at EL2 with E2H = 0 and has no use for them, is INFERRED.',
                   '   NOT put back: VNCR_EL2, VBAR_EL1, ESR_EL1, FAR_EL1, SPSR_EL1, ELR_EL1, SPSR_EL2, ELR_EL2, SP_EL0, SP_EL1 and',
                   '   PAR_EL1. The PAC keys are not the report\'s to put back: group F does, and no row shows them. The report also',
                   '   asks PSCI (SMC, AFFINITY_INFO) about a secondary CPU that was released: none is (group G), so the query is not',
                   '   made (INFERRED).',
                   'monitor F, CONDITIONAL, pointer authentication. Each write below is made only if the guest reaches the code',
                   '   that makes it: that reach is INFERRED from the callers, and whether the guest reaches any of it inside the',
                   '   time box is UNKNOWN (untried on Oryon). Three parts:',
                   '   - The native helper that signs or authenticates a user pointer, called with the MMU on from the SPTM sign',
                   '     and authenticate endpoints, single and batch. It first checks EL2, E2H = 0, SCTLR_EL2.M = 1, a 4 KiB',
                   '     TCR_EL2 and the PAuth ID fields, and writes nothing if one fails. Then it writes DAIFSet, TCR_EL2 (T0SZ',
                   '     and the TBI bits), TLBI ALLE2, SCTLR_EL2 (the pointer-authentication enables) and the key registers',
                   '     APIAKeyLo_EL1 APIAKeyHi_EL1 APIBKeyLo_EL1 APIBKeyHi_EL1 APDAKeyLo_EL1 APDAKeyHi_EL1 APDBKeyLo_EL1',
                   '     APDBKeyHi_EL1 (the callers pass the IA and DA keys; the code names all four pairs), and puts back DAIF,',
                   '     TCR_EL2, SCTLR_EL2 and the keys. The rows compare TCR_EL2, SCTLR_EL2 and DAIF; the keys have no row.',
                   '   - A guest PACGA that traps: APGAKeyLo_EL1 APGAKeyHi_EL1.',
                   '   - The key banks: on a return to EL0 the guest\'s keys are installed, and when the guest leaves, the host\'s are',
                   '     put back before any other monitor code runs: the ten key registers (the eight above and APGAKeyLo_EL1',
                   '     APGAKeyHi_EL1) with SCTLR_EL1 and HCR_EL2. A bank switch that fails stops the run through the timer',
                   '     registers of group C.',
                   'monitor G, in the binary and not reached by this run (INFERRED from the callers and the gates; no static pass',
                   '   follows the indirect branches). A secondary CPU: its entry, release, stop and PSCI queries, which need the',
                   '   host_cpus argument that the wrapper passes as 0 (the guest\'s CPU-release write is taken only with it, and a',
                   '   launch refuses without it): CPACR_EL1, CPTR_EL2, ICC_SRE_EL2, MAIR_EL2, TTBR0_EL2, SPSel, IC IALLU, DC CVAC,',
                   '   WFE, and in the monitor ICC_PMR_EL1, ICC_BPR1_EL1 and ICC_IGRPEN1_EL1 (the wrapper writes those three before',
                   '   the call); SMC for PSCI CPU_ON, CPU_OFF and AFFINITY_INFO (the last also in the report path, inlined, asked',
                   '   only while a released secondary is unaccounted for). The secondary entry also writes SCTLR_EL2, TCR_EL2 and',
                   '   TLBI ALLE2, which group F reaches too: they are not claimed unreached. The GPU calls of the guest, behind the',
                   '   LIVE gate gpu.flags (the builder requires zero; a guest that writes PARAMS can lift it: section 3b): SMC,',
                   '   the two TrustZone calls, and DC IVAC. The A16 thermal sensors are memory-mapped and are read only through',
                   '   c->thermal (section 3b); they write no system register. SMC is a call to firmware.',
                   *m1t.PLACED_LINES),
    'gic': ('memory-mapped GIC registers in the pinned frames only (PIN.DAT gicd_base, gicr_first, gicr_stride, gicr_count),',
            'written by the wrapper before the call and put back after it, as in the GIC and stop dry run',
            '(docs/SP11-STICK-GIC.md). Class G: GICD_CTLR, only when group 1 reads disabled (a read of 0x10 becomes 0x13).',
            'Class R, in this CPU\'s own redistributor (found by matching GICR_TYPER to MPIDR; the other frames are read for',
            'GICR_TYPER only), the SGI frame of INTID 26: IPRIORITYR byte (0x80 if it differs), ISENABLER0 bit 26 only when it',
            'reads clear (the other PPIs that firmware enabled stay as found: one boot read ISENABLER0 0x4E800001, OBSERVED),',
            'ICFGR1 field if it reads edge, IGROUPR0 bit if the distributor shows one security state, and the put-backs',
            'ICENABLER0 and ICPENDR0. The census cannot see the monitor\'s stores to the GIC distributor: they are in the GPU',
            'code of section 3b, at the address that PARAMS gpu.gicd holds, and its one fixed distributor address is in',
            'secondary_run (group G, not reached). The monitor uses the CPU interface (group D) and, on the boot CPU,',
            'acknowledges an interrupt in two cases only: INTID 26 (the timer) when its ISTATUS is set and the run\'s deadline',
            'is ahead, and INTID 0x14C when gpu.flags has its enable bit (a LIVE gate). Any other IRQ or FIQ, and the timer at',
            'the deadline, ends the run with the interrupt not acknowledged: the monitor records it, disables its timer and',
            'returns, and a foreign interrupt stays pending (INFERRED from probes/xnuboot/async.c). Never the ITS, SMMU, PCIe,',
            'USB or the framebuffer.')}
M1_STOP_RULES = ('   - MON STATUS -90 to -95 is the monitor\'s NV2 and PAuth gate refusing: a result, and the expected first one while D6',
                 '     is open; the same bundle is not retried.',
                 '   - MON QUARANTINE on the page (RET=0X51554E01 or 0X51554E02): the monitor\'s window is partial. Power cycle; never',
                 '     continue on that RAM.')
# The silence rule of every kind, and the rule of this one in its place: the monitor prints nothing while it runs and its prepare is UNKNOWN, so twice the
# time box after ENTER (10 s at most) could end a legitimate run. The 60 s is the operator's safe wait, not a measurement (docs/SP11-STICK-MONITOR.md section 10).
SILENCE_RULE = '   - No page change for twice the time box after ENTER: note the last line and power cycle.'
M1_SILENCE_RULE = ('   - No page change for 60 seconds after the MON CALL line: note the last line and power cycle. The 60 s is an operator-safe wait and not a',
                   '     measurement: the monitor prints nothing while it runs and its prepare is UNKNOWN (never run on this unit), so the rule of the other',
                   '     kinds, twice the time box after ENTER, could end a legitimate run.')


def m1_lines(m1):
    """Section 1b of the record of an M1 run: SNAPMON-M1.BIN taken apart (wrapper, padding, monitor), the profile and the NV2 record the monitor was
    built with, as tools/sp11_stick_bundle_m1.py read them from the build report and checked them against the bytes of the files."""
    w, m, n, image, reporter, efi = m1['wrapper'], m1['monitor'], m1['nv2'], m1['image'], m1['reporter'], m1['efi']
    lines = ['1b. The monitor entry run (docs/SP11-STICK-MONITOR.md): payload 0 is SNAPMON-M1.BIN, the wrapper plus the monitor',
             '   EFI files    the two that run at EL2 before the wrapper, named by SHA-256 as the builder was given them:',
             *(f'                {name:<23} sha256 {digest}' for name, digest in efi.items()),
             '                No check at boot compares them with this record: the wrapper sees PIN.DAT and the data, and the',
             '                runner checks the payloads (INFERRED from the review of the boot path). The operator\'s own check of',
             '                the stick is the control: the stager\'s verify and every staged file against STICK-FILES.TXT, which',
             '                these digests are compared with',
             '   label        PIN.DAT\'s build_label starts with M1RUN-, which only this kind\'s builder writes and the wrapper',
             '                requires: a PIN.DAT of another kind, whose record says that no monitor is called, is refused before',
             '                the call (docs/SP11-STICK-MONITOR.md section 14). The reverse, this PIN.DAT with another wrapper, is',
             '                not refused: those wrappers do not know the marker',
             f'   file         SNAPMON-M1.BIN  {image["bytes"]} bytes  sha256 {image["sha256"]}',
             '                (payload 0 above: the stick names it SNAPMON.BIN)',
             f'   wrapper      {w["bytes"]} bytes  sha256 {w["sha256"]}',
             f'                then {w["padding"]} zero bytes up to the monitor',
             f'   monitor      xnuboot.bin at offset {m["offset"]:#x}, {m["bytes"]} bytes, profile {m1["profile"]}',
             f'                sha256 {m["sha256"]}',
             f'   elf sha256   {m["elf_sha256"]}',
             '                (the monitor\'s ELF: tools/sp11_monitor_census.py checks section 3 against it)',
             f'   build record {m["build_record_sha256"]}',
             '                (its profile-build.json holds the monitor\'s flags; SNAP_FAIR_TOKEN=1 among them is D10, still open)',
             '   position     the build tool relinked the monitor\'s objects with --emit-relocs; q1n1\'s ELF check found no absolute',
             '                relocation (' + ', '.join(f'{count} {name[10:]}' for name, count in sorted(m['relocations'].items())) + '); the wrapper calls it',
             '                with one bl and hashes it in place against the digest above before the call. That shows',
             '                no absolute relocation and that these objects make these bytes. It cannot show an address',
             '                dependence that is no relocation, an address built from integers, .init_array or TLS; and the',
             '                build record above was written by the build tool itself: its digests tie the files together,',
             '                and nothing authenticates the record',
             f'   NV2 record   sha256 {n["sha256"]}',
             f'                soc {n["soc"]}  tool {n["tool"]}  build_id {n["build_id"]}  evidence {n["evidence"]}',
             f'                vncr_redirect {n["vncr_redirect"]}  negative_control_trapped {n["negative_control_trapped"]}',
             '                the monitor\'s gate refuses (status -91) on a core that reads other values than these:',
             f'                MIDR_EL1 {n["midr_el1"]}  REVIDR_EL1 {n["revidr_el1"]}',
             f'                ID_AA64MMFR2_EL1 {n["id_aa64mmfr2_el1"]}  ID_AA64MMFR4_EL1 {n["id_aa64mmfr4_el1"]}']
    if m1['test_record']:
        lines += ['                LOCAL TEST RECORD: made by the host tests (tool wi4b-test-only), synthetic, never measured on a unit. A',
                  '                monitor built with it is a test artefact and this record must not be approved. D6 (which NV2 record) is',
                  '                the operator\'s and is open.']
    else:
        lines += ['                D6 (which NV2 record, and whether its regime matters) is the operator\'s decision and is open: approving',
                  '                this record approves this NV2 record and no other.']
    lines += [f'   reporter     SNAPREP-M1.BIN  {reporter["bytes"]} bytes  sha256 {reporter["sha256"]}',
              '                (payload 1: reads REPORT.DAT, prints, stores to its stack only)']
    return lines


def m1_time_lines(box_s, place):
    """Section 4 of the record of an M1 run: the box, what it does not bound, the time budget with its numbers, and D8. The sum is of the figures as printed."""
    t = TIME_FACTS
    clean_mib = (place['guest_size'] + place['reserve_size']) // MIB
    clean_s = round(t['verify_s'] * clean_mib / t['verify_mib'], 1)
    wrapper_s = round(t['wrapper_s'], 1)
    low = round(wrapper_s + t['arm_wait_s'] + box_s + t['stop_s'], 1)
    high = round(wrapper_s + t['arm_wait_s'] + box_s + clean_s, 1)
    return [
        f'4. Time box: {box_s} s of wall time on CNTPCT_EL0, carried in the PARAMS chunk (watchdog_seconds, word 24).',
        '   The builder refuses any other PARAMS watchdog (TIME_BOX_MISMATCH). The stop is the monitor\'s own timer',
        '   interrupt (CNTHP, PPI 26), not a wait.',
        '   What the box bounds: the guest, from its first instruction. It does not bound the monitor\'s prepare, which runs',
        '   before the timer is armed (duration UNKNOWN: never run on this unit), nor the stop\'s cache clean, nor a guest',
        '   that the timer never interrupts (U3: whether PPI 26 reaches EL2 at all is UNKNOWN until the GIC dry run',
        '   answers): then only the operator\'s power cycle ends the run.',
        '   Time, from the wrapper\'s first line to the end of the report:',
        f'      wrapper checks, copy and verify          {wrapper_s} s    OBSERVED once (the dry run: {t["wrapper_s"]} s)',
        f'      GIC arm: the announcement and the wait   {t["arm_wait_s"]} s      SW_GIC_WAIT_S in probes/sp11-stick-gic.h',
        '      monitor prepare                          UNKNOWN  never run on this unit',
        f'      guest                                    {box_s} s      the time box',
        f'      stop: clean of {clean_mib} MiB                  about {t["stop_s"]} s (the plan\'s estimate, INFERRED, not measured),',
        f'                                                        at most {clean_s} s (INFERRED bound: the dry run verified',
        f'                                                        {t["verify_mib"]} MiB in {t["verify_s"]} s, re-hash included)',
        f'      the known parts                          at least {low} s ({wrapper_s} + {t["arm_wait_s"]} + {box_s} + {t["stop_s"]}), at most {high} s, plus the prepare',
        '   The plan\'s budget is a run well under 20 s of UPTIME, a precaution of low weight (docs/SP11-MONITOR-PORT.md',
        f'   section 10, D5); the known parts alone are at least {low} s, so this run does not meet it, and keeping or relaxing',
        '   it, with the 8 s wait, is the operator\'s. The earliest restart datum is about UPTIME 17 s (INFERRED: one boot,',
        '   in a run that started secondary CPUs; the plan, section 10): this run is as long as that by construction. On',
        '   restarts: a PSCI-only control was alive at UPTIME 224 s and 268 s (OBSERVED, two boots); the restarts seen went',
        '   with CPU_ON and CPU_OFF (INFERRED) and this run starts no secondary (x4 = 0).',
        '   Thermal (D8): no thermal guard exists on this path. The monitor\'s own (compat/a16-thermal.c) only loads, from the',
        '   A16\'s sensor addresses: SROT at 0x0c222000 + n * 0x1000, which compat/hamoa-thermal.c gives as this machine\'s TSENS',
        '   SROT (INFERRED there), and TM at 0x0c22c000 + n * 0x1000, where this machine\'s is UNKNOWN. It is closed by',
        '   c->thermal, which the monitor\'s setup (probes/xnuboot/setup.c) makes only if PARAMS early_stop.flags has the thermal',
        '   bit at that moment: the builder required it zero (PARAMS_LAUNCH), the wrapper checks it again before the call, and',
        '   nothing makes c->thermal later (a PRIVATE gate, section 3b). compat/hamoa-thermal.c is not in the monitor: its',
        '   binary has no hamoa_ symbol (tools/test-sp11-stick-monitor-devices.py). Nothing reads a temperature during the',
        '   run. The exposure is the CPU',
        f'   time of one CPU, {low} to {high} s plus the prepare in all, of which the guest runs at most {box_s} s. No clock, voltage or',
        '   DVFS write is compiled in or reachable by the sources read (INFERRED from the profile\'s gates; the census sees',
        '   system registers only, not MMIO). Approving this record accepts the exception D8 describes',
        '   (docs/SP11-MONITOR-PORT.md section 15).',
        '   PARAMS words: gpu.flags, early_stop.flags and txm_native are zero in the chunk (this builder checked all three);',
        '   the wrapper reads the first two again before the call, not txm_native. That is a snapshot: the monitor reads',
        '   gpu.flags and early_stop.flags again while the guest runs, and the guest can write the RAM that holds them',
        '   (stage 2 gives it DRAM and the reserve: INFERRED), so a guest that sets one opens that path.']


def note_lines(data):
    """The lines of a --note file's bytes. They are appended to the record the operator approves, so they must read on a terminal exactly as
    they are stored: printable ASCII and line feeds only (a carriage return, a backspace or an escape sequence could hide or overwrite a
    word), at most NOTE_LINES lines of NOTE_COLUMNS characters. A refusal quotes no byte of the file."""
    if (bad := next((i for i, byte in enumerate(data) if byte != 10 and not 32 <= byte <= 126), None)) is not None:
        raise ch.Refused('NOTE_BAD', f'--note byte {bad} is not printable ASCII or a line feed')
    lines = data.decode('ascii').split('\n')
    if lines[-1] == '':
        lines.pop()         # the terminator of the last line, not a line of its own
    if not any(line.strip() for line in lines):
        raise ch.Refused('NOTE_BAD', '--note holds no text')
    if len(lines) > NOTE_LINES:
        raise ch.Refused('NOTE_BAD', f'--note has {len(lines)} lines; at most {NOTE_LINES}')
    if long := next((i for i, line in enumerate(lines, 1) if len(line) > NOTE_COLUMNS), None):
        raise ch.Refused('NOTE_BAD', f'--note line {long} has {len(lines[long - 1])} characters; at most {NOTE_COLUMNS}')
    return lines


def real_lines(real):
    """Section 1a of the record of a `real` build: what GUEST.DAT and PIN.DAT were made from, as used and not as decided. `real` holds
    kernelcache_sha256, guest_profile, panel, sptm_hvc_calls, seed, descriptor_sha256, board_id, board_status and ecam_windows (the number of
    config windows the descriptor lists). A descriptor that is EXPERIMENTAL and lists none is the guest view of docs/SP11-STICK-REAL.md
    section 3: it does not describe the Surface, and the board adapter's equality rule on the MCFG (D11 proper) is not satisfied by it."""
    seed = real['seed']
    lines = ['1a. What this build is (the host-side real path of docs/SP11-STICK-REAL.md: GUEST.DAT is made from a kernelcache)',
             f'   kernelcache  guest profile {real["guest_profile"]}  sha256 {real["kernelcache_sha256"]}',
             f'   D9 as built  panel {real["panel"]}: {PANEL_WORDS[real["panel"]]}',
             f'                SPTM HVC call patch {real["sptm_hvc_calls"]}: {HVC_WORDS[real["sptm_hvc_calls"]]}',
             f'   boot seed    label {seed} ' + ('(a default, not an operator decision; boot entropy UNIMPLEMENTED)' if seed == DEFAULT_SEED else '(given with --seed)'),
             f'   descriptor   sha256 {real["descriptor_sha256"]} (PIN.DAT descriptor_sha256)',
             f'                board {real["board_id"]}  status {real["board_status"]}']
    if real['board_status'] == 'EXPERIMENTAL' and real['ecam_windows'] == 0:
        lines.append('                guest view: pcie.ecam empty; D11 proper open')
    return lines


def record_text(r):
    """The text the operator approves (its SHA-256 is PIN.DAT's authorisation_sha256): deterministic, ASCII, no personal name. `r` holds place
    (the interface's placement), stores (the mask), time_box_s, label, monitor and reporter ((size, sha256)), guest (size, sha256, chunks,
    index), note (lines or None), gic_stop (the record of the GIC and stop dry run), m1 (None, else the facts of the monitor entry run that
    tools/sp11_stick_bundle_m1.py read_report returns: section 1b, the touches of M1_TOUCHES, section 4 with the time budget and two stop rules)
    and real (None for a synthetic build, else real_lines' argument). gic_quarantine, with gic_stop, chooses the pass 5 text of
    tools/sp11_stick_bundle_record_gic.py in place of GIC_STOP_TOUCHES."""
    p, g, m1 = r['place'], r['guest'], r.get('m1')
    assert not (m1 and r.get('gic_stop')), 'a record is of one kind'
    assert not r.get('gic_quarantine') or r.get('gic_stop'), 'the pass 5 text is a variant of the GIC and stop record'
    lines = [
        f'SNAPINTOSH SP11 STICK BUNDLE - AUTHORISATION RECORD (format 1, label {r["label"]})', '',
        'Status: operator decision NOT RECORDED. Hardware: NOT RUN. This text is a template until the operator approves it.',
        'Approval is the operator\'s alone: the SHA-256 of exactly this text, recorded as authorisation_sha256 of PIN.DAT; a changed byte is another',
        'record. It names no person and stays local: it is never committed or published.', '',
        '1. What may run (named by SHA-256; the runner hashes the payloads before each call)',
        f'   payload 0  SNAPMON.BIN  {r["monitor"][0]} bytes  sha256 {r["monitor"][1]}',
        f'   payload 1  SNAPREP.BIN  {r["reporter"][0]} bytes  sha256 {r["reporter"][1]}',
        f'   data       GUEST.DAT    {g["size"]} bytes  sha256 {g["sha256"]}  chunks {g["chunks"]}  table sha256 {g["index"]}',
        f'   data       REPORT.DAT   {sb.REPORT_BYTES} bytes, all zero, sha256 {hashlib.sha256(bytes(sb.REPORT_BYTES)).hexdigest()}',
        '   data       PIN.DAT      1024 bytes; it carries this record\'s digest, so its own digest cannot be named here', '']
    if r.get('real'):
        lines += [*real_lines(r['real']), '']
    if m1:
        lines += [*m1_lines(m1), '']
    lines += [
        '2. Placement (the one range the run may store to)',
        f'   guest_base {p["guest_base"]:#x}  guest_size {p["guest_size"] // MIB} MiB  reserve {p["reserve_size"] // MIB} MiB  '
        f'work_base {p["work_base"]:#x}  work_size {p["work_size"] // MIB} MiB',
        f'   place_base {p["place_base"]:#x}  place_end {p["place_base"] + p["place_size"]:#x}  (limit 2^36)', '',
        f'3. Store classes authorised (PIN.DAT authorised_stores = {r["stores"]})']
    touches = M1_TOUCHES if m1 else (gq.TOUCHES if r.get('gic_quarantine') else GIC_STOP_TOUCHES) if r.get('gic_stop') else TOUCHES
    if r.get('gic_stop'):
        lines.append('   Record of the GIC and stop dry run (docs/SP11-STICK-GIC.md): payload 0 is SNAPMON-GIC.BIN; no monitor is called.')
        lines += gq.HEAD_LINES if r.get('gic_quarantine') else ()
    if m1:
        lines.append('   Record of the monitor entry run (docs/SP11-STICK-MONITOR.md section 10): payload 0 is SNAPMON-M1.BIN.')
    for name in STORES:
        if r['stores'] & STORES[name]:
            first, *rest = touches[name]
            lines += [f'   {name}: {first}', *(f'      {line}' for line in rest)]
    lines += gq.FACT_LINES if r.get('gic_quarantine') else ()
    lines += ['   Not authorised: any other store class, a disk or NVMe write, a firmware write, a clock, voltage or DVFS change, a secondary CPU.',
              *(m1t.SMC_LINES if m1 else ()), *(('', *m1t.DEVICE_LINES) if m1 else ()), '',
              *(m1_time_lines(r['time_box_s'], p) if m1 else
                [f'4. Time box: {r["time_box_s"]} s of wall time on CNTPCT_EL0, carried in the PARAMS chunk (watchdog_seconds); the stop is the monitor\'s own.']), '',
              '5. Refusal and stop rules (docs/SP11-MONITOR-PORT.md section 14)',
              '   - A wrapper REFUSED line ends the boot: fix the cause, never retry the same bundle.',
              *(M1_SILENCE_RULE if m1 else (SILENCE_RULE,)),
              '   - Two resets at the same last line stop the series; a touch outside RAM is a finding, not a retry.',
              '   - One hardware client; AC power or at least 50% battery; the operator present and filming. A spinning guest is ended by the',
              '     operator\'s power cycle, which writes nothing.',
              *(M1_STOP_RULES if m1 else ())]
    if r['note']:
        lines += ['', '6. Notes of this build (part of the digest)', *r['note']]
    text = '\n'.join(lines) + '\n'
    text.encode('ascii')
    return text
