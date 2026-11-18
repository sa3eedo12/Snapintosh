# SPDX-License-Identifier: MIT
"""The static text of the record of a monitor entry run (--m1-run) that is checked against binaries or against the sources, in one place of its own so
that tools/sp11_stick_bundle_record.py stays under its size limit: the guest SMC paragraph (D5), the device-code section 3b (D1), the placed entries of
the el2_sysreg class (D3) and the gic class with its facts (E1). The record module appends them to its text for the M1 kind only, and the records of the
other kinds are what they were.

 - SMC_LINES follows the shared "Not authorised" line of section 3. Its numbers are those of a read-only scan of the local J700 26A428 kernelcache
   (tools/test-sp11-stick-monitor-devices.py repeats it when the kernelcache is present and says SKIP when it is not).
 - DEVICE_LINES is section 3b. tools/test-sp11-stick-monitor-devices.py reads the symbol table of the monitor build (llvm-nm) and fails when a family
   of device code in the binary is not named here with its gates, when a family named here is not in the binary, or when a family that the build leaves
   out appears in it. A family starts at a line of three spaces, its lower-case name and an opening parenthesis.
 - PLACED_LINES are tools/sp11_monitor_census.py's `placed` output for the wrapper and the monitor of the test build, with the six spaces that the record
   module adds in front of each line taken off. The census check fails when they differ from the binaries. Regenerate them after any change of the
   monitor or of the wrapper, with `placed --binary wrapper ELF` and `placed --binary monitor ELF`, and say so in the commit. A line of prose in the
   sub-block must not start with `wrapper ` or `monitor `.
 - GIC_LINES is the gic class: what the arm half of the GIC stage (probes/sp11-stick-gic.c, built with SW_GIC_DRY_REGS=0) writes and reads, register by
   register, with its offset, when, what it is read back against and the outcome that names a failure, then how long the quarantine lasts, what the page
   shows when the arm refuses, and what the monitor does. A register is a line of two spaces, W or R and its name (entry()); the census sees system
   registers and not MMIO, so tools/test-sp11-stick-monitor-arm.py compares those lines with what the host model of the stage logs in its runs.
   GIC_FACT_LINES follows the classes: the boot 12 and 13 facts with their labels. PASS6_LINES is the sentence of pass 6, the one case in which the put-back's
   last write is not made (both group 1 gates left open); tools/test-sp11-stick-monitor-gates.py compares each claim of it with the model, in the real stage
   and in mutants of it. Any change of the arm half's registers, or of its order, conditions or outcomes, is a change of this text.

The prose is filled by wrap() below, a greedy fill on single spaces at a fixed width and not a library's, so that the bytes of the text the operator
approves (its SHA-256 is PIN.DAT's authorisation_sha256) depend on nothing but this file.

Evidence: EXPERIMENTAL. The gates are read from the sources and from the binary. The monitor ran once, in boot 20 (OBSERVED, one unit, one boot), with the PARAMS that
the builder makes, so with every device gate closed: no run has exercised a device family. The gic class and its facts carry their own labels."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_m1 as bm  # noqa: E402

WIDTH = 118
# The files of each CPU-start choice of the monitor (the builder's table), and the choices that this text can say what they are: `ack`, the acknowledging monitor, is said by rung 5's record
# (tools/sp11_stick_bundle_panel_rung5.py), the one that carries it: the builder refuses --cpu-start ack without that record's word and --let-panic (check_boot_arg, check_let_panic).
FILE_NAMES = bm.CPU_START_FILES
CPU_START_TEXTS = ('stop', 'ack')
PREPARE_AND_CLEAN_S = 0.04      # OBSERVED once, in boot 20 (the first run of the monitor entry): the call's TICKS less the time box, the prepare and the stop's clean together
# What boot 20 measured of the same step as the dry run's 2.923 s, the wrapper's checks, copy and verify of its own GUEST.DAT: the TICKS HASHED of its page is a stamp counted from the
# wrapper's entry (probes/sp11-stick-wrapper.c, c.head.r.ticks_hashed - c.ticks_enter), ticks of 19.2 MHz, and not the hash alone (that is HASHED less COPIED). Section 4 says it beside
# the dry run's figure, and what its first heartbeat and its film showed of the clock. The scaling of the dry run's figure to this GUEST.DAT (INFERRED) agrees with it: 2.923 s x 118.87 / 144.7 = 2.40 s.
BOOT20_HASHED_TICKS, BOOT20_GUEST_BYTES = 46_208_412, 118_870_336
BOOT20_HASHED_S = round(BOOT20_HASHED_TICKS / 19.2e6, 2)
BOOT20_HASHING = (f'Boot 20 measured that whole step (checks, copy and verify) for its own {BOOT20_GUEST_BYTES / 1e6:.1f} MB GUEST.DAT at {BOOT20_HASHED_S} s (TICKS HASHED='
                  f'{BOOT20_HASHED_TICKS:,} at 19.2 MHz, counted from the wrapper\'s entry; OBSERVED once), which agrees with that scaling.')
BOOT20_HASHED_ROW = (f'      {"that same step in boot 20 (TICKS HASHED)":<41}{BOOT20_HASHED_S} s   OBSERVED once ({BOOT20_HASHED_TICKS:,} ticks of 19.2 MHz',
                     f'      {"":<41}from the wrapper\'s entry)')
UPTIME_BOOT20 = '; boot 20: UPTIME 28 S at its first heartbeat, and no restart to the end of its film at 45 s; OBSERVED once'


def wrap(text, first=3, rest=3, width=WIDTH):
    """`text` filled greedily on single spaces into lines of at most `width` characters, the first indented by `first` spaces and the others by `rest`."""
    lines, line = [], ''
    for word in text.split():
        indent = first if not lines else rest
        if line and len(' ' * indent + line + ' ' + word) > width:
            lines.append(' ' * indent + line)
            line = word
        else:
            line = f'{line} {word}' if line else word
    return (*lines, ' ' * (first if not lines else rest) + line)


SMC_LINES = wrap(
    'About "a firmware write" above: an SMC that the guest executes is not trapped. HCR_EL2 starts as probes/xnuboot.S:128-142 sets it, with TSC clear, '
    'and every later write to it changes other bits only (IMO, FMO, NV, NV2, TWE, VI, VF, API, TVM and TRVM: read from the sources, INFERRED), and the '
    'monitor has no handler for the exception class of an SMC from the guest (EC 0x17). An SMC at EL1 would reach firmware without the monitor '
    '(INFERRED). The basis for not expecting one is a read-only scan of the local J700 26A428 kernelcache: its two executable segments, __TEXT_EXEC '
    '(14,774,272 words) and __TEXT_BOOT_EXEC (8,192), hold 0 SMC and 0 HVC words (read on the host (a file, not a run), one kernelcache; the same scan finds, in the two '
    'segments together, 1 SVC, 1 ERET and 148 WFE words, 146 of them in __TEXT_EXEC, so it does find instructions). Whether the guest runs SMC code from anywhere '
    'else is UNKNOWN.')

DEVICE_LINES = (
    '3b. Device code in the monitor and what closes it (D1; docs/SP11-STICK-MONITOR.md section 14)',
    *wrap('Section 3 counts system registers. The monitor binary also holds code that stores to memory-mapped devices and makes SMC calls, found by '
          'name in its symbol table (tools/test-sp11-stick-monitor-devices.py). Each family below has a gate. PRIVATE: monitor state in the work area, '
          'which stage 2 makes read-only to the guest. LIVE: a PARAMS word that the monitor reads again while the guest runs. The guest can write the '
          'RAM that holds PARAMS (INFERRED: stage 2 gives it DRAM and the reserve; whether the SPTM stops it is UNKNOWN), so a LIVE gate is a bar that a '
          'guest can lift and not a wall. With PARAMS as the builder makes it (gpu.flags, early_stop.flags and txm_native zero, and no ANS model listed) and a '
          'guest that writes none of it, no family below stores to a device or calls firmware (INFERRED from the sources; never run). A store or a call of one is in no '
          'store class above.'),
    *wrap('adreno (adreno_*, gpu_*, fwd_*, and fence_at, tlbi and gic_off of compat/adreno.c; probes/xnuboot/gpu_glue.c): stores to the GPU, its SMMU '
          'and its GMU at PAs 0x03d00000 to 0x03df0000 (compat/adreno.h), the base of the X1E80100\'s GPU too (docs/SP11-GPU-A7XX-STUDY.md: INFERRED for '
          'this machine), and to the GIC distributor at the address that PARAMS gpu.gicd holds. Those stores have a PRIVATE gate, the ADOPTED state that '
          'only gpu_prepare sets, and only when gpu.flags is not zero as the monitor prepares. Behind a LIVE gate only, gpu.flags with its enable bit: '
          'an HVC #0 from the guest makes a real SMC (adreno_smc, through gpu_smc: two TrustZone GPU calls on an allow-list, no ADOPTED test), and an '
          'INTID 0x14C interrupt is acknowledged and ended (gpu_irq: with no forwarder line, which nothing adopted means, nothing more).', rest=6),
    *wrap('early_stop (early_stop_*; compat/early-stop.c, probes/xnuboot/early_stop_thermal.c): at a timer tick before the deadline, '
          'early_stop_host_tick calls (its early_stop_host_call is inlined), at EL2, the function pointer that PARAMS early_stop.host_call holds, when '
          'early_stop.flags has HOST and host_call, session, hz and the poll period are set. LIVE gate and nothing private: a guest that writes those '
          'words makes the monitor call an address of its choosing at EL2. early_stop_hvc (LIVE: gpu.flags enable, then early_stop.flags GUEST) '
          'records a request and answers it, and stores to no device.', rest=6),
    *wrap('thermal (thermal_*, which include thermal_read, and a16_thermal_*): loads only, from the A16\'s sensor addresses (section 4). PRIVATE gate '
          'c->thermal, which the monitor\'s setup makes only if early_stop.flags has the thermal bit at that moment (probes/xnuboot/setup.c): clear, as '
          'the builder requires.', rest=6),
    *wrap('nvmeh (nvmeh_*, ans_window_*, and command of compat/nvme-host.c): doorbell stores to an NVMe controller. LIVE gate: an ANS window, which is the ANS '
          'NVMe model of PARAMS with config[3] set to the window mode, and which ans_window (probes/xnuboot/ans_glue.c) reads at prepare, at each ANS access and at '
          'the stop; ans_window_prepare returns before any of this without one. The builder refuses a PARAMS that lists any DEVICE_ANS_* model (PARAMS_ANS), so a '
          'bundle it authorises has no window (INFERRED from the sources; never run). A guest that writes PARAMS could configure one: the ADOPTED word that gates '
          'the doorbells would then be read at an address that the guest names, and nvmeh_sp11_adopt would not have run for it (INFERRED). For a window that PARAMS '
          'configure before the launch, the monitor, built for SNAP_PLATFORM_SP11, calls nvmeh_sp11_adopt and never nvmeh_adopt, with a target block and a record '
          'block that a host tool places in the window reservation after the params and the state (docs/SP11-NVME-WINDOW.md section 2.3). That call clears the state '
          'word first, so that an ADOPTED left by an earlier launch does not outlive a refusal. A pair that was never placed is all zero and is refused as UNWIRED '
          '(-145, compat/nvme-host.c): the launch is refused and the controller is left not adopted, so that a read, a write or a flush is refused before a doorbell. '
          'A pair that is not all zero is read through nvmeh_sp11_check and a load of the endpoint\'s configuration header before nvmeh_adopt re-proves the '
          'controller. No tool of the stick bundle places the two blocks (UNIMPLEMENTED). Whether the reservation\'s memory from +0x234000 to +0x237000 (the params, '
          'the state and the two blocks) is zero when the monitor starts is UNKNOWN: nothing of the stick bundle clears it, and the refusal does not depend on it. '
          'Read from those sources, which tools/test-sp11-stick-monitor-devices.py pins; never run.', rest=6),
    *wrap('secondary (secondary_*, xnuboot_secondary_stop and early_off; group G above): PRIVATE gate host_cpus, from x4, which the wrapper passes as 0 '
          '(probes/xnuboot/setup.c, cpu_boot.c); SMC for PSCI, and the GIC distributor at one fixed address (group G).', rest=6),
    *wrap('Not in this binary, and the test fails if one appears: hamoa_*, edp_*, qcc*, i2c_input*, guest_net*, dvfs*, boot_reloc*, boot_relay*, '
          'display_timing*, adreno_recover*, adreno_coherent*.', rest=6),
    *wrap('The guest-device models (sptm_*, sep_*, txm_*, device_*, aes_*, the rest of ans_*, pmgr_*, spmi_*, dart_*, and the like) work on the '
          'monitor\'s state and the guest\'s RAM by design. That none of them stores to a device is INFERRED from the sources, which were not all read '
          'for this record; the census sees system registers and not MMIO, and an exploratory scan for constant-address stores found them only in the '
          'families above.', rest=6))

# ---- The gic class (E1, E2 part 2): the arm half of the GIC stage as pass 6 left it, built for the monitor entry ----------------------------------------------
GIC_WIDTH = 111         # the lines of a class block are indented six spaces by the record module and at most 120 characters wide; the first goes after `   gic: `


def entry(kind, name, where, text):
    """One register of the gic class, `  W|R NAME WHERE text`: two spaces in front and the lines after the first indented by eight (the record module adds six
    to every line). tools/test-sp11-stick-monitor-arm.py reads these lines: a W is a register that the stage writes (and so reads first), an R one that it only reads."""
    return wrap(f'{kind} {name} {where} {text}', first=2, rest=8, width=GIC_WIDTH)


def prose(text):
    return wrap(text, first=0, rest=0, width=GIC_WIDTH)


# PASS 6: the one case in which the arm half's put-back does not make its last write, the quarantine's ISENABLER0 (probes/sp11-stick-gic.c, gic_both_gates_open). It
# is printed after the lines of that write, as a continuation of its entry, so it is wrapped for it. tools/test-sp11-stick-monitor-gates.py compares each claim of
# it with the host model, in the real stage and in mutants of it, and shows that a text without it, or with the write claimed unconditional, is refused.
PASS6_LINES = wrap(
    'The exception: that write is NOT made when the put-backs of both ICC_IGRPEN1_EL1 and GICD_CTLR failed, which leaves both group 1 gates open. The reason '
    '(INFERRED): enabling the quarantined interrupts again would let a pending one (INTID 27 was pending in boot 13, OBSERVED) be signalled with both gates '
    'open. ISENABLER0 is then only read, and named in FAILED (its bit 2, 0X4) because the bits Q cleared are still clear: they stay disabled when the run ends (SGI 0 and '
    'PPIs 23, 25, 27 and 30 on the unit\'s state), and the run ends NOT_RESTORED (48, OUTCOME=0X30), with FIRST naming the refusal when there was one. '
    'One gate stuck and the other closed is not that case: the write is made, inert behind the closed gate. A gate found open is not counted as failed: with '
    'GICD_CTLR found enabled (class G never begun) and ICC_IGRPEN1_EL1 stuck, the write is made although both gates are open. On the unit\'s state both gates '
    'are this stage\'s own (GICD_CTLR 0x10 in boots 12 and 13, ICC_IGRPEN1_EL1 0 in boot 13: OBSERVED, one unit). The rule holds after a refusal and after the '
    'call alike. EXPERIMENTAL: host model only, never run on the unit.', first=8, rest=8, width=GIC_WIDTH)


def gic_lines(pass6=PASS6_LINES, cap=None):
    """The lines of the gic class of the M1 record: the first goes after `   gic: `, the others are indented six spaces by the record module. `cap` is the wrapper's cap on the time box
    for the pair of the record (sp11_stick_bundle_m1.box_cap); None is the builder's own, BOX_MAX_S."""
    cap = bm.BOX_MAX_S if cap is None else cap
    return (
        *prose('memory-mapped GIC registers in the pinned frames (PIN.DAT gicd_base, gicr_first, gicr_stride, gicr_count) and the CPU interface, written by the '
               'arm half of the GIC stage (probes/sp11-stick-gic.c; docs/SP11-STICK-GIC.md sections 4 and 7) as pass 6 left it, before the call, and by its '
               'put-back after it. It is the dry run\'s source built here with SW_GIC_DRY_REGS=0: no read or write of CNTHP, VBAR_EL2, HCR_EL2 or an EL1 '
               'register or timer, no window, no vector table, no ERET (the wrapper\'s own reads of some of those registers around the call are in the wrapper '
               'group of section 3); the census of section 3 lists its three system register writes, ICC_PMR_EL1, '
               'ICC_BPR1_EL1 and ICC_IGRPEN1_EL1, and no other. Its stores come after the 8 s wait that its NEXT GIC STORES line announces. D below is '
               'gicd_base; RD is gicr_first + index * gicr_stride, the one frame whose GICR_TYPER affinity matches this CPU (a bounded scan: every other '
               'frame is read at +0x008 and nothing else); SGI is RD + 0x10000. The offsets are the stage\'s register map, INFERRED from the GICv3 text as '
               'remembered (the specification was not re-read). The registers are named as the host model of the stage names them, and '
               'tools/test-sp11-stick-monitor-arm.py compares this list with what the model logs in its nominal and its failing runs: a register that a '
               'run writes and no W line names, one that a run reads and no line names, and a W line that no run writes, are failures. That is a host '
               'check and shows nothing about the unit\'s GIC.'),
        *prose('Writes, in the order made. Each register (ICENABLER0 and ICPENDR0 by their readable twins, ISENABLER0 and ISPENDR0) is read before it is changed '
               'and read back after it (RWP waited where it applies); a store that does not hold is a named refusal, GIC_NOT_READY with DETAIL = the outcome, '
               'after which what was written is put back, but for the one write that the exception under ISENABLER0 withholds, and no monitor is called.'),
        *entry('W', 'GICR_ICENABLER0', 'SGI+0x180',
               'Q, the quarantine (a member of class R), first: every bit that ISENABLER0 was found to have set but bit 26, PPI 26, the stop timer\'s: SGI 0 and '
               'PPIs 23, 25, 27 and 30, so 0x4A800001 where the unit\'s ISENABLER0 read 0x4E800001 (OBSERVED, boot 12). No write, and nothing begun, when that is '
               '0. Then GICR_CTLR.RWP is polled, and ISENABLER0 must read back as exactly PPI 26\'s bit as found, else Q_NOT_HELD (22, OUTCOME=0X16). Put back '
               'here too: PPI 26\'s own bit alone, only if the stage set it.'),
        *entry('W', 'GICD_CTLR', 'D+0x000',
               'class G, only when EnableGrp1A (bit 1) reads clear: the value read with bit 1 set, and bit 0 too when DS (bit 6) is clear (the unit\'s 0x10 is written '
               '0x13, as q1n1\'s A16 recipe does; boot 13 read it back 0x12, OBSERVED). GICD_CTLR.RWP (bit 31) is polled, and ARE and EnableGrp1A must read back, '
               'else G_NOT_HELD (16). Put back: the value read, then RWP.'),
        *entry('W', 'GICR_IGROUPR0', 'SGI+0x080',
               'class R, only when DS is set and bit 26 reads clear: the value read with bit 26 set (with DS clear the group is EL3\'s and no write is made; the unit '
               'read 0, which says nothing of PPI 26\'s group: INFERRED). Read back: bit 26 set, else R_NOT_HELD (17). Put back: the value read.'),
        *entry('W', 'GICR_IPRIORITYR', 'SGI+0x41A',
               'class R: the byte of INTID 26 becomes 0x80 when it differs (it read 0xA0 on the unit, OBSERVED, boot 12). Put back: the byte read. The survey reads the '
               'bytes of INTIDs 23, 25, 27 and 30 (SGI+0x417, 0x419, 0x41B, 0x41E) and writes none.'),
        *entry('W', 'GICR_ICFGR1', 'SGI+0xC04',
               'class R: PPI 26\'s two-bit field is made level, only when it reads edge (every PPI read level on the unit, OBSERVED, boot 12). Read back: not edge, else '
               'R_NOT_HELD (17). Put back: the field read.'),
        *entry('W', 'GICR_ISENABLER0', 'SGI+0x100',
               'class R: bit 26 alone, only when it was found clear (it was found set on the unit, OBSERVED, boot 12, so the stage makes no such write on that '
               'state). After RWP, ISENABLER0 '
               'must be exactly the value read without the bits Q cleared, with bit 26 set, else R_NOT_HELD (17). Put back, and the last register write of the '
               'whole run, but for the exception that follows: the bits Q cleared (0x4A800001 on the unit\'s state), in one write with RWP waited, then ISENABLER0 is verified whole against the '
               'value read, with one retry, else NOT_RESTORED (48, OUTCOME=0X30).'),
        *pass6,
        *entry('W', 'ICC_PMR_EL1', '(MSR)',
               'class C: 0xF0. PMR read 0xF8 before and after this write on the unit (OBSERVED, boot 13): INFERRED that the write is ignored. The priority 0x80 must be '
               'below the PMR read back, else PRIO_NOT_BELOW_PMR (20). Put back: the value read, only if it differs.'),
        *entry('W', 'ICC_BPR1_EL1', '(MSR)', 'class C: 0. It read 3 on the unit (OBSERVED, boot 13). Put back: the value read, only if it differs.'),
        *entry('W', 'ICC_IGRPEN1_EL1', '(MSR)', 'class C: 1; it must read back 1, else C_NOT_HELD (18). It read 0 on the unit (OBSERVED, boot 13). Put back: the value read.'),
        *entry('W', 'GICR_ICPENDR0', 'SGI+0x280',
               'Put back only: bit 26, if INTID 26 is pending when the call ends and was not when read. The stage never writes the pending state of any other '
               'interrupt, the quarantined ones included: a source that asserts through the call is pending, and, except in the both-gates case above, enabled again, '
               'when the run ends.'),
        *prose('Reads only, before the first store unless said otherwise (the put-back reads again what it puts back):'),
        *entry('R', 'GICD_TYPER', 'D+0x004', 'ITLinesNumber (bits 4:0) bounds the survey: at most 31 registers of each kind.'),
        *entry('R', 'GICD_IIDR', 'D+0x008', 'printed on the GIC GICD line (0x401043B on the unit, OBSERVED, boot 13).'),
        *entry('R', 'GICR_TYPER', 'RD+0x008',
               '64 bits, once per frame scanned until the affinity in bits 63:32 matches MPIDR_EL1 (if none does: GICR_NOT_FOUND (8)): the one access to a frame '
               'that is not this CPU\'s.'),
        *entry('R', 'GICR_WAKER', 'RD+0x014', 'asleep (bit 1 or 2 set) refuses GICR_ASLEEP (9): waking it is not authorised.'),
        *entry('R', 'GICR_CTLR', 'RD+0x000', 'RWP (bit 3), polled after a write that needs it: at most 100000 reads and 50 ms for each poll.'),
        *entry('R', 'GICR_ISPENDR0', 'SGI+0x200',
               'INTID 26 pending refuses PPI_OWNED (10); read whole and kept in the GIC record (the unit read 0x08000000, INTID 27 pending: OBSERVED, boot 13).'),
        *entry('R', 'GICR_ISACTIVER0', 'SGI+0x300', 'INTID 26 active refuses PPI_OWNED (10); read again by the put-back and never written: an interrupt left active is NOT_RESTORED.'),
        *entry('R', 'GICD_ISENABLER[n]', 'D+0x100+4n',
               'the survey, n = 1 to ITLinesNumber (register 0 is banked in each redistributor and is not read): which SPIs are enabled. It decides nothing.'),
        *entry('R', 'GICD_ISPENDR[n]', 'D+0x200+4n', 'the survey, the same n: which SPIs are pending; the first two that are enabled and pending are kept in the GIC record.'),
        *entry('R', 'ICC_SRE_EL2', '(MRS)', 'SRE clear refuses SRE_CLEAR (11), and none of the other ICC registers is read then.'),
        *entry('R', 'ICC_CTLR_EL1', '(MRS)', 'only if SRE is set; EOImode read 1 on the unit (OBSERVED, boot 13).'),
        *entry('R', 'ICC_RPR_EL1', '(MRS)', 'once before any store and once after the put-back, as a check: never written.'),
        *entry('R', 'ICC_HPPIR1_EL1', '(MRS)',
               'after the last store, with IRQ masked (this read acknowledges nothing): 1023 (none) or 26 passes; any other INTID, the special 1020 to 1022 '
               'included, is FOREIGN_PENDING (23, OUTCOME=0X17): a PPI that did not disable, or an enabled SPI, which the quarantine of this CPU\'s redistributor '
               'does not cover. No monitor is called.'),
        *prose('How long the quarantine lasts. It is on from Q\'s write until the put-back\'s last write, which comes after the monitor has returned: the whole '
               'call, so the monitor\'s prepare, its guest and its stop are inside it, and so is what the wrapper does after the return. In the both-gates case '
               'above there is no such write, and the quarantine is still on when the run ends. What bounds it is the '
               f'monitor\'s own stop, the interrupt of its timer (CNTHP, PPI 26) at the time box on CNTPCT_EL0, at most {cap} s (the wrapper\'s PARAMS check), once the '
               'monitor has armed the timer. Before that comes the monitor\'s prepare; after it the stop\'s cache clean, the wrapper\'s scans and cleans of REPORT.DAT '
               f'and the put-back\'s polls (each at most 50 ms). The prepare and the clean took about {PREPARE_AND_CLEAN_S} s together in boot 20 (OBSERVED once), the clean '
               'is bounded in section 4 (INFERRED), and the prepare alone is UNKNOWN. '
               'That PPI 26 reaches EL2 is OBSERVED in the dry run\'s stage (boot 14, one unit, one boot: four deliveries from EL2 and one from EL1, U3 answered; '
               'docs/SP11-STICK-GIC.md section 12), and the monitor\'s own stop path ended the guest at its time box in boot 20 (OBSERVED once). Nothing in the wrapper '
               'bounds the call: a guest that the timer never '
               'interrupts, or a monitor '
               'that does not return, ends only by the operator\'s FORCE OFF (section 5c), and this stage\'s put-back does not run then (that the firmware sets the GIC up '
               'again at the restart is INFERRED: three boots, 12 to 14, found the same enabled set). On a refusal of the arm half the quarantine lasts only as long as the '
               'put-back that follows at once, except in the both-gates case, in which that put-back does not lift it.'),
        *prose('While it is on, SGI 0 and PPIs 23, 25, 27 and 30 cannot be signalled to this CPU. The guest\'s EL1 timers are among them (INTID 27 and 30 in the '
               'unit\'s GTDT, OBSERVED from its ACPI dump), and what the guest does without them inside the time box is UNKNOWN. The monitor stops its run on '
               'any interrupt that is not PPI 26 (probes/xnuboot/async.c), so with the quarantine on such a PPI cannot do that (INFERRED). An enabled SPI is '
               'not covered: one pending at the check refuses FOREIGN_PENDING and no monitor is called, and one that asserts during the call stops the run '
               'unacknowledged (below).'),
        *prose('The refusals of the arm half, Q_NOT_HELD (22, OUTCOME=0X16) and FOREIGN_PENDING (23, OUTCOME=0X17) among them, end the run before the monitor is '
               'called: the wrapper puts back what the arm wrote, but for the exception above (mon_arm_failed, probes/sp11-stick-monitor.c), and its page shows, in this order, GIC QUAR MASK= '
               'DONE= HPPIR= (these two refusals only: MASK is what was written to ICENABLER0 and DONE what ISENABLER0 read back, left out with MASK=NONE '
               'when nothing needed disabling; HPPIR is the INTID that ICC_HPPIR1_EL1 gave, - when it was not read), GIC RESTORE CLEAN= FAILED= RETRIED= '
               'BEGUN=, GIC VERDICT=<name> OUTCOME=, MON VERDICT=GIC_NOT_READY and REFUSED=GIC_NOT_READY STAGE=COPIED DETAIL=0X16 or 0X17. DETAIL is 0X30 '
               'instead when the put-back itself did not hold (NOT_RESTORED; the both-gates case above is one, and GIC RESTORE then says CLEAN=NO). There is no MON CALL line. The INTID of a FOREIGN_PENDING refusal is on that live '
               'page only: SNAPREP-M1.BIN prints GIC GICD, GICR, CPU, NEW and RESTORE from the record and has no GIC QUAR line, so a page that was not filmed '
               'does not show it (it is in the GIC record, version 3, 1456 bytes at REPORT.DAT + 0x11000, with the survey, which no M1 line prints). A '
               'put-back that fails after the call is not a refusal: GIC RESTORE CLEAN=NO.'),
        *prose('Never the ITS, another interrupt\'s priority, group or configuration, an SPI (read only), another CPU\'s redistributor (GICR_TYPER read only), '
               'SMMU, PCIe, USB or the framebuffer. The only writes to other interrupts are the enable bits of this CPU\'s own SGI 0 and PPIs, cleared by Q '
               'and set again by the put-back (not in the both-gates case above).'),
        *prose('The monitor, while the guest runs (as the record said before this arm half was described here): the census cannot see its stores to the GIC '
               'distributor: they are in the GPU code of section 3b, at the address that PARAMS gpu.gicd holds, and its one fixed distributor address is in '
               'secondary_run (group G, not reached). The monitor uses the CPU interface (group D) and, on the boot CPU, acknowledges an interrupt in two '
               'cases only: INTID 26 (the timer) when its ISTATUS is set and the run\'s deadline is ahead, and INTID 0x14C when gpu.flags has its enable bit '
               '(a LIVE gate). Any other IRQ or FIQ, and the timer at the deadline, ends the run with the interrupt not acknowledged: the monitor records it, '
               'disables its timer and returns, and a foreign interrupt stays pending (INFERRED from probes/xnuboot/async.c). Never the ITS, SMMU, PCIe, USB or '
               'the framebuffer.'))


GIC_LINES = gic_lines()

GIC_FACT_LINES = (
    '   The arm half since pass 5 and what it rests on (docs/SP11-STICK-GIC.md sections 4 and 12; its labels):',
    *wrap('Changed from the stage that ran in boot 13: the survey (reads), the quarantine (one ICENABLER0 write and the one ISENABLER0 write that undoes it, '
          'which is not made in the both-gates case above), the check of ICC_HPPIR1_EL1 before the call, record version 3 and two outcomes. Passes 5 and 6, in the '
          'dry run\'s own build, RAN in boot 14 and ended GIC VERDICT=IRQ_OK (docs/SP11-STICK-GIC.md section 12, docs/SP11-MONITOR-PORT.md section 13, WI 8): the '
          'quarantine, the check and the put-back are OBSERVED there (below). This build is the same source built for the monitor entry: it ran once in boot 20 '
          '(OBSERVED, one unit, one boot: GIC NEW ISEN=0X4000000, GIC RESTORE CLEAN=YES). The both-gates case was not exercised in boot 14 or boot 20: it stays host '
          'model only.', first=6, rest=6, width=GIC_WIDTH + 6),
    *wrap('Outcomes (OUTCOME= on the page, in hex): 0X1 to 0XF a refusal before any store; 0X10 to 0X1F a store that did not hold or a foreign interrupt next, '
          'always after a store and so always followed by the put-back (0X16 Q_NOT_HELD, 0X17 FOREIGN_PENDING); 0X30 NOT_RESTORED.', first=6, rest=6, width=GIC_WIDTH + 6),
    *wrap('OBSERVED, one unit, one boot each, in the dry run\'s own build, not in this one (boot 12 from a photograph of the page, boot 13 from one video read '
          'frame by frame, boot 14 from one 60 s video with the final page read at full resolution; the transcription of boot 14 is not CRC-verified and its decodes '
          'are INFERRED):', first=6, rest=6, width=GIC_WIDTH + 6),
    '         the firmware leaves SGI 0 and PPIs 23, 25, 26, 27 and 30 enabled: ISENABLER0 0x4E800001 (boots 12 to 14);',
    '         INTID 27 was pending before any store: ISPENDR0 0x08000000 (boots 13 and 14);',
    '         the unit\'s GTDT, from its ACPI dump, gives Virtual EL1 GSIV 27 (Secure EL1 29, Non-secure EL1 30,',
    '           Non-secure EL2 26);',
    '         GICD_CTLR written 0x13 read back 0x12 (boots 13 and 14);',
    '         an enabled, pending PPI is delivered to EL2 as an IRQ once group 1 and the CPU interface are on: INTID 27,',
    '           not 26, was the first interrupt of window 1 in boot 13, and everything was put back clean;',
    '         PPI 26 itself reached EL2 four times in window 1 of boot 14 (HPPIR1 26 at the vector, latency 0 to 2',
    '           counter ticks) and once from EL1 (HCR_EL2 0x80000010): U3 answered; the monitor\'s own stop path ran later,',
    '           in boot 20 (OBSERVED once);',
    '         the quarantine held: ICENABLER0 written 0x4A800001 left ISENABLER0 0x04000000 (PPI 26 only) and',
    '           ICC_HPPIR1_EL1 read 1023, so no Q_NOT_HELD and no FOREIGN_PENDING (boot 14);',
    '         the put-back was whole, the quarantined enables last: ISENABLER0 0x4E800001, GICD_CTLR 0x10,',
    '           ICC_IGRPEN1_EL1 0 and ICC_PMR_EL1 0xF8, each as found (boot 14);',
    '         the EL1 virtual timer was asserting: CNTV_CTL_EL0 read 0x5 (ENABLE 1, IMASK 0, ISTATUS 1) with',
    '           ISPENDR0 0x08000000 (boot 14);',
    '         the survey of the SPIs counted 3 enabled and 9 pending and none both (SPI LINES=30 EN=3 PEND=9',
    '           BOTH=0): counts of INTIDs 32 to 991, not which (boot 14).',
    *wrap('INFERRED: that INTID 27 pending is that timer asserting (the GTDT\'s Virtual EL1 GSIV 27 says so; the interrupt\'s own source register is not read); that '
          'the monitor, which stops on any interrupt that is not PPI 26 (probes/xnuboot/async.c), would end a monitor entry run at its first IRQ unmask on the same '
          'interrupt; that nothing else runs on this CPU between the first store and the call (the wrapper runs after the runner has left the firmware).',
          first=6, rest=6, width=GIC_WIDTH + 6),
    *wrap('UNKNOWN: whether an extended SPI or an LPI is enabled and pending (neither is surveyed or quarantined; the one ICC_HPPIR1_EL1 read before window 1 saw '
          '1023, at one instant); which SPIs are enabled or pending (the page holds counts); the entry with PSTATE.I set (window 2 of the dry run ran with it clear, '
          'the monitor enters its guest with it set); whether the interrupt state survives a guest, and what the guest does without INTID 27 and 30 for the call.',
          first=6, rest=6, width=GIC_WIDTH + 6))

# The census placed entries (D3): the first line is the head of the sub-block (six spaces and `placed` once the record module has indented it).
PLACED_LINES = (
    *wrap('placed by function, as tools/sp11_monitor_census.py prints them (`placed --binary wrapper|monitor ELF`) and checks them against the ELFs: '
          'every register a binary writes, every SYS operation, SMC, ERET and WFE, and every data word that decodes as a system instruction, with the '
          'functions that hold it and how many each holds (name*N; one when there is no count). A register or operation held in another function or a '
          'second time, and a new one anywhere, fails the check. The six data words are the guest instruction table of compat/native-writer.h (the '
          'J700 kernel\'s MSR to ELR_EL1 and SPSR_EL1 sites): the monitor compares guest words with them and never executes them (INFERRED).',
          first=0, rest=3, width=WIDTH - 6),
    '   wrapper AT S1E2R, AT S1E2W: sw_probe_range',
    '   wrapper DC CIVAC: sw_verify_all',
    '   wrapper DC CVAC: mon_refuse, sw_copy_all, sw_gic_arm, sw_gic_arm_reads, sw_gic_arm_stores*7,',
    '      sw_gic_begin_class, sw_gic_flush, sw_gic_put_back, sw_head_flush, sw_head_init*2,',
    '      sw_monitor_stage*8, sw_refuse*2, sw_set_stage',
    '   wrapper IC IALLUIS: sp11hw_ic_ialluis',
    '   wrapper ICC_BPR1_EL1: sp11hw_write_icc_bpr1_el1',
    '   wrapper ICC_IGRPEN1_EL1: sp11hw_write_icc_igrpen1_el1',
    '   wrapper ICC_PMR_EL1: sp11hw_write_icc_pmr_el1',
    '   monitor APDAKeyHi_EL1, APDAKeyLo_EL1, APDBKeyHi_EL1, APDBKeyLo_EL1, APIAKeyHi_EL1, APIAKeyLo_EL1,',
    '      APIBKeyHi_EL1, APIBKeyLo_EL1: pauth_native_sign_user*2, pauth_native_user*2, pauth_switch_enter,',
    '      pauth_switch_leave',
    '   monitor APGAKeyHi_EL1, APGAKeyLo_EL1: pauth_pacga*2, pauth_switch_enter, pauth_switch_leave',
    '   monitor AT S1E1R: early_stop_hvc, model_dispatch*5, vcpu_arch_prepare*2, xnuboot_trap*9',
    '   monitor CNTHCTL_EL2: vcpu_arch_prepare*2, xnuboot_report',
    '   monitor CNTHP_CTL_EL2: device_reset_stop*2, runtime_enter*2, secondary_gic_stop, timer_arm,',
    '      watchdog_off*2, xnuboot_async*2, xnuboot_pauth_failed*2, xnuboot_report*6',
    '   monitor CNTHP_CVAL_EL2: device_reset_stop, runtime_enter, timer_arm, watchdog_off, xnuboot_async,',
    '      xnuboot_pauth_failed, xnuboot_report*3',
    '   monitor CNTKCTL_EL1: clock_control, vcpu_arch_prepare, xnuboot_report',
    '   monitor CPACR_EL1, CPTR_EL2, ICC_BPR1_EL1, ICC_IGRPEN1_EL1, ICC_PMR_EL1, ICC_SRE_EL2: secondary_run',
    '   monitor DAIF: pauth_native_sign_user, pauth_native_user, xnuboot_report',
    '   monitor DAIFSet: _start, early_off, pauth_native_sign_user, pauth_native_user, secondary_entry,',
    '      secondary_gic_stop',
    '   monitor DBGBCR0_EL1, DBGBCR1_EL1, DBGBCR2_EL1, DBGBCR3_EL1, DBGBCR4_EL1, DBGBCR5_EL1, DBGBVR0_EL1,',
    '      DBGBVR1_EL1, DBGBVR2_EL1, DBGBVR3_EL1, DBGBVR4_EL1, DBGBVR5_EL1: writer_debug_write',
    '   monitor DC CIVAC: adreno_guest_cache, adreno_guest_fill32*2, adreno_guest_ib*5, adreno_mirror,',
    '      adreno_ring_kick, adreno_ring_reserve, aes_dma_publish, attr_clean_finish, command*5,',
    '      const_region_seal, fence_at*2, gpu_prepare*3, guard_write, kernel_publish_cpu, monitor_call_impl,',
    '      secondary_release, sep_pnon_query, sep_sks_publish, sep_xars_slot, sptm_attr_clean, sptm_publish,',
    '      sptm_retype_user_exec, transaction_publish*2, user_live_alloc, user_live_write, user_private_write,',
    '      user_publish_view, user_root_install, vcpu_arch_prepare, vcpu_stage2_prepare, xnuboot_report*3',
    '   monitor DC CVAC: secondary_power_off',
    '   monitor DC CVAU: monitor_call_impl*4',
    '   monitor DC IVAC: adreno_guest_cache',
    '   monitor ELR_EL1, SPSR_EL1: xnuboot_trap*4',
    '   monitor ELR_EL2, ERET: _start, resume_lower, secondary_guest_enter',
    '   monitor ESR_EL1: vcpu_arch_prepare, xnuboot_trap*3',
    '   monitor FAR_EL1: vcpu_arch_prepare, xnuboot_trap*2',
    '   monitor HCR_EL2: _start*2, guest_interrupt_sync, pauth_switch_enter, pauth_switch_leave,',
    '      secondary_entry, secondary_run*2, vcpu_arch_prepare, xnuboot_report',
    '   monitor IC IALLU, MAIR_EL2, SPSel, TTBR0_EL2: secondary_entry',
    '   monitor IC IALLUIS: sptm_retype_user_exec',
    '   monitor ICC_DIR_EL1, ICC_EOIR1_EL1: fwd_complete, secondary_gic_stop, xnuboot_async*3',
    '   monitor MAIR_EL1, TTBR1_EL1, VMPIDR_EL2, VTCR_EL2: vcpu_arch_prepare, xnuboot_report',
    '   monitor MDCR_EL2, OSLAR_EL1: vcpu_arch_prepare, xnuboot_report, xnuboot_secondary_stop',
    '   monitor MDSCR_EL1: step_arm, vcpu_arch_prepare, xnuboot_report, xnuboot_secondary_stop',
    '   monitor PAR_EL1: early_stop_hvc, model_dispatch*5, vcpu_arch_prepare, xnuboot_trap*9',
    '   monitor SCTLR_EL1: pauth_switch_enter, pauth_switch_leave, vcpu_arch_prepare*2, xnuboot_report*2',
    '   monitor SCTLR_EL2, TCR_EL2, TLBI ALLE2: pauth_native_sign_user*2, pauth_native_user*2, secondary_entry',
    '   monitor SMC: early_off, gpu_smc, secondary_power_off, secondary_release*3, xnuboot_report',
    '   monitor SPSR_EL2: _start, secondary_guest_enter, step_arm, user_sysreg_access, xnuboot_trap*5',
    '   monitor SP_EL0, SP_EL1: model_dispatch',
    '   monitor TCR_EL1: user_root_install*2, vcpu_arch_prepare, xnuboot_report',
    '   monitor TLBI ALLE1, TLBI VMALLS12E1: xnuboot_report',
    '   monitor TLBI ALLE1IS: kernel_change_pperm_timed, multipage_aperture, root_slot_release,',
    '      shadow_tlb_sync, sptm_configure_user_root, sptm_retire_shared_root, sptm_retire_user_root,',
    '      sptm_retype_rozone*2, sptm_retype_txm_default*2, sptm_retype_txm_owned*2, sptm_retype_user_exec,',
    '      sptm_share_user_root, update_disjoint_call*2, user_change_uperm, user_commit_detach,',
    '      user_commit_shared_leaf, user_commit_shared_unmap, user_commit_shared_update, user_commit_tlb_range,',
    '      user_commit_unnest, user_commit_update, user_enter_mode*2, user_private_write, user_refresh_all,',
    '      user_root_install*4, xnuboot_report',
    '   monitor TLBI VMALLE1IS, VBAR_EL1, VNCR_EL2: vcpu_arch_prepare',
    '   monitor TLBI VMALLS12E1IS: const_region_seal, guard_step, guards_close, vcpu_arch_prepare,',
    '      xnuboot_report, xnuboot_trap*5',
    '   monitor TPIDR_EL2: secondary_entry, xnuboot_report, xnuboot_setup',
    '   monitor TTBR0_EL1: user_root_install, vcpu_arch_prepare, xnuboot_report',
    '   monitor VBAR_EL2: _start, secondary_entry*2, xnuboot_report',
    '   monitor VTTBR_EL2: const_region_seal*2, vcpu_arch_prepare, xnuboot_report',
    '   monitor WFE: early_off, secondary_power_off',
    '   monitor data words: 0xd5184002*3, 0xd518402c, 0xd5184032, 0xd5184033')
