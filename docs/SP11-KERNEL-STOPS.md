<!-- SPDX-License-Identifier: MIT -->
# SP11 kernel stops: what the stock kernel was doing when the monitor ended each run (host analyses of the stops of boots 20, 21, 23, 24, 25 and 26)

**Status: EXPERIMENTAL. This note records host analyses of six hardware stops, the reading of one wait (boot 27) and the reading of one userspace failure (boot 28); it is not a hardware result of its own. Hardware: the monitor entry ran eight times on
the unit, in boots 20, 21, 23, 24, 25, 26, 27 and 28 (M1 rungs 1 to 7 and 9; rung 8 was skipped and never run; 2026-11-18 to 20; OBSERVED, one unit, one boot each; [SP11-STICK-LOG.md](SP11-STICK-LOG.md) sections 18, 19, 21,
22, 23, 24, 25 and 26). Everything else here is a reading of a file on the host or of a film. Nothing in this note authorises a run.**

Each run of the monitor entry ended in a stop that the page names (`MON STOP`), and each time a read-only analysis of the guest kernel followed, except after boot 27, whose run ended at the monitor's own watchdog at the box's end with the kernel waiting for a root device: section 7B records that first wait from the film and the earlier analyses, and no host analysis of it has been done. Boot 28 is the first run whose guest ran user-mode code: the guest's own `launchd` failed its first boot task and the guest panicked in userspace, and the monitor's own end is the chip-reset stop of boot 24 (section 7C; no host analysis of it has been done either). This note is the short
reference for those analyses, one section per stop, with what was read, what is inferred, which predictions were tested by the next boot and what is still unknown. It is
the place where later stops are added (section 11). The runs themselves, their pages, films and verdicts are in [SP11-STICK-LOG.md](SP11-STICK-LOG.md); the plan, the
decisions and the expected failures are in [SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md) (section 14); the records and the builder are in [SP11-STICK-MONITOR.md](SP11-STICK-MONITOR.md)
(section 20) and [SP11-STICK-REAL.md](SP11-STICK-REAL.md).

## 1. Reading this note

- **Labels.** VERIFIED here means read on the host, in a file: the local J700 26A428 kernelcache, the staged `GUEST.DAT` and its device tree, this repository or the public XNU
  source at tag `xnu-12377.121.6`; it is never "ran on the unit". OBSERVED is a page or a film of one boot of one unit. INFERRED is a reading. UNKNOWN is what no source shows.
- **Provenance.** The findings are those of the analyses written after each boot (the stop-site notes of 2026-11-18 and 19, the timer design note and the CPU-start design note,
  kept outside this repository). This note records them with their labels and does not re-derive them: its author did not re-read the kernelcache, so each VERIFIED is the
  analysis's own.
- **Names.** The kernel is stripped and the KDK is absent, so `tools/kc-symbolicate.py` was NOT RUN. Function and variable names below are INFERRED from literals, structure and
  the public source; 26A428 is xnu-13432, whose source is not public, so every match with the public tag is structural. Where a public name is used (`cpu_idle`,
  `processor_boot`, `SavePanicInfo` and so on) it names a role, not a symbol that was read.
- **What is not here.** No kernelcache bytes, listings or instruction words beyond the public constants, and no machine identifiers. Addresses appear only where an observed page
  value or panel text needs them. The runtime address of the kernel equals its file address (arithmetic VERIFIED; that no slide exists in the tools' sense is INFERRED).
- **The page fields of a stop** (VERIFIED, read in `probes/xnuboot/`): on `S2_FAULT`, `PC=` is the guest's PC, `A=` the intermediate physical address, `B=` `ESR_EL2` and `C=` the
  guest's `FAR`; on `WATCHDOG`, `PC=` is the guest's PC at the monitor's interrupt, `A=` `ESR_EL1`, `B=` `FAR_EL1` and `C=` `ELR_EL1`, the guest's last EL1 exception, which is not
  necessarily its current state; on `GUEST_EXC`, `PC=` is the guest's `x30`, `A=` `ESR_EL1`, and `C=` `ELR_EL1`. The page prints 100 columns, so the last digits of a long `C=` are cut.

## 2. The six stops at a glance

Boot 27, the second to last row, is not a seventh stop at a device: the kernel went through `bsd_init` to a wait for a root device that its log names, and the monitor's own watchdog ended the run at the box's end (section 7B). Boot 28, the last row, is no stop at a device either: the guest panicked in userspace after `launchd`'s first boot task failed, and the monitor's own end is the chip-reset stop of boot 24 (section 7C).

| Boot | Rung and guest input | What the page ended with (OBSERVED) | What the kernel was doing (the reading) | Label of the reading |
| --- | --- | --- | --- | --- |
| 20 | rung 1: panel withheld, `serial=3` | `MON STOP WATCHDOG` at the 5 s box, `PC=0XFFFFFE000C1EEF00` | spinning in the I/O Kit start-up's progress-bar loop, whose bound is the display size (0) | loop structure VERIFIED; identity INFERRED, high |
| 21 | rung 2: panel as the console, `serial=2` | `MON STOP S2_FAULT` at 2.044 s, `A=0X300734004`, a 32-bit write | writing the first CPU-start word of the platform power manager's register block | code path VERIFIED; that it is that write INFERRED, by address |
| 23 | rung 3: `cpus=1` | `MON STOP GUEST_EXC` at 2.166 s, `A=0X2000000` | at the kernel's own debugger trap, so in its panic funnel | INFERRED, high; the cause UNKNOWN |
| 24 | rung 4: `cpus=1` and `let_panic` | `MON STOP S2_FAULT` at 2.2465 s, `A=0X3082BC224`, a 32-bit read, after a panic text | in the chip-reset request of the panic path, after an ApplePMGR assertion and five nested panics | text OBSERVED; chain VERIFIED; reading INFERRED |
| 25 | rung 5: six CPUs, CPU-start stores acknowledged, `let_panic`, 60 s box | `MON STOP WATCHDOG` at 60.18 s, `PC=0XFFFFFE000B8716A8` | idle, in the wait-for-interrupt of the retention idle path, interrupts masked | the WFI VERIFIED; the idle thread INFERRED; what it waits for UNKNOWN (read in section 7A: a wait of `bsd_init`, INFERRED) |
| 26 | rung 6: as rung 5 with `cpu_boot_timeout_secs=90`, 120 s box, timer words printed | `MON STOP S2_FAULT` at 61.60 s, `PC=0XFFFFFE00095FF9AC`, `A=0X291000004`, a 32-bit write of 1 | in the scaler driver's hardware-abstraction initialisation, one pulse of one register, released with the other `IOSurfaceRoot` waiters when `bsd_init` published the BSD resource | code VERIFIED; names and the release INFERRED; what the register does UNKNOWN |
| 27 | rung 7: as rung 6 with the `quiet` device tree (43 nodes withdrawn by data) and `cpu_boot_timeout_secs=120`, 120 s box | `MON STOP WATCHDOG` at 120.185 s, `PC=0XFFFFFE000B8716A8`, `A=0X96000047`; last line of the log `Waiting on <dict ...>` for an `IOMedia` of content `Apple_HFS` at t0 + 60 s, then silence | waiting for a root device in the wait that follows `bsd_init`, the kernel's first wait named by its log; the monitor ended the run at the box's end, at no device | the last line and the page OBSERVED; the wait INFERRED; the root device UNKNOWN |
| 28 | rung 9: the root-carry pair, a 6,144 MiB guest with the 1.86 GB RAM-disk root, the `quiet-rtc-acm` tree (clock bound, `acm_fastsim=1`), `cpu_boot_timeout_secs=360`, 300 s box | `MON STOP S2_FAULT` at 40.08 s, `PC=0XFFFFFE0008BA7358` (boot 24's), `A=0X3082BC...`, a 32-bit read, after `launchd`'s `mount-phase-1` failed and the guest panicked in userspace | in the chip-reset request of the panic path, as boot 24; the panic itself is `launchd`'s first boot task failing for the absent Preboot and Update volumes | the text and the page OBSERVED; the chain to the chip-reset read as boot 24 INFERRED; the ISC reading INFERRED; what the next boot tasks need UNKNOWN |

## 3. Boot 20: the early loop in the I/O Kit start-up

- **Page (OBSERVED).** `MON STOP WATCHDOG` with `PC=0XFFFFFE000C1EEF00` and `SPSR` `0x804003C4` (EL1t, D, A, I and F masked), 5.04 s into the call, `TRAPS=3127`, `ABORTS=0`.
- **VERIFIED (host).** The PC is the head of a five-instruction loop that touches only registers (arithmetic, one unsigned compare, one branch back: no load, store, call or system
  register access), in the function that the kernel's second thread calls after the banner and the VM bootstrap lines and that parses `-restore`, `-noprogress` and `-noprogressonce`.
  The staged `GUEST.DAT` of that run carried an all-zero `Boot_Video` block (the panel was withheld) and no `-noprogress`.
- **INFERRED, high.** The function is the kernel's I/O Kit start-up (`PE_init_iokit`) and the loop is the public progress-bar loop of `pexpert/arm/pe_init.c`, which never ends when
  the display size is 0; the register that holds the size was never printed. Interrupts are masked until that function returns, so the quarantined guest timers are not the cause.
- **Predictions made then.** With the panel handed to the guest (or `-noprogress`) the loop is gone, and the next stop is the first CPU-start write or, if a write were accepted and
  nothing started, a `cpu N failed to boot` panic about a second later (section 8, rows 1 and 2).
- **UNKNOWN.** Whether `-noprogress` alone would have been enough (never run); the value of the register at the stop.

## 4. Boot 21: the first CPU-start write

- **Page (OBSERVED).** `MON STOP S2_FAULT PC=0XFFFFFE00097A352C A=0X300734004 B=0X93970045 C=0XFFFFFE60A0178004` at 2.044 s. The decode of `B=` (INFERRED, from the architecture's
  layout): data abort from a lower exception level, a 32-bit write, register 23, a translation fault at level 1.
- **VERIFIED (host).** The PC is the one store of the platform power manager driver's base register-write helper, so every register write of that driver has the same valid 32-bit
  syndrome; the staged device tree's power-manager window 24 starts at `0x300730000`, so the page's address is that window plus `0x4004`, the offset the A16 sources document for XNU's first
  CPU-start write; the monitor's own CPU-start window is `0x300734004` to `0x30073400C` (`probes/xnuboot/trap_dispatch.c`, `compat/cpu-release.c`).
- **The CPU-start path (VERIFIED code path; role names INFERRED from the public source).** The kernel's CPU-boot thread registers every CPU, calls `processor_boot` for each one without
  waiting ("all CPUs can start in parallel"), and then waits for each CPU in turn in `sched_cpu_init_completed` for `cpu_boot_timeout_secs` seconds (default 1), and panics
  `cpu N failed to boot for the first time, waited N seconds` if one does not check in. `cpu_start` for a secondary tail-calls the power manager's `enableCPUCore`, which takes the
  complex locks and writes the register block: one global write at +0x4004 and one or two cluster words at +0x4008 and +0x400C, so five start sequences give at least 10 and at most 15
  writes (INFERRED from the loop structure). After the write the guest does no read, poll, retry or wait in the driver or in `cpu_start` (VERIFIED: both read in full).
- **INFERRED.** That the stop is the first CPU-start write (an address match, and the code path above). The value written (`x23`) was not printed.
- **UNKNOWN.** The value; why 925 single-steps were counted.

## 5. Boot 23: the debugger trap

- **Page (OBSERVED).** `MON STOP GUEST_EXC PC=0XFFFFFE000B8C2288 A=0X2000000 B=0XFFFFFEA937FDC000 C=0XFFFFFE000B8C22` (two digits cut), 2.166 s; `MON EXIT` `ESR=0X8200000F`, the monitor's
  own guard fault on the exception-vector page (INFERRED).
- **Decode (INFERRED).** `A=` is `ESR_EL1` 0x02000000: exception class 0 (an unallocated instruction), length 32 bits, ISS 0, taken at EL1; `FAR` is not meaningful; the exception PC lies in
  `0xFFFFFE000B8C2200` to `0xFFFFFE000B8C22FF`.
- **VERIFIED (host).** The only unallocated word in the 256 bytes around the last call's return address is XNU's debugger-trap word, a public constant (`TRAP_DEBUGGER` in
  `osfmk/kern/debug.c`, `GDB_TRAP_INSTR2` in `osfmk/arm/trap_internal.h`); the host decode rejects only that word.
- **INFERRED, high.** The stop is the guest kernel's own debugger trap, so the kernel had entered its panic or debugger funnel (a panic is far likelier than a stackshot or a profiling
  path); its text is printed only after the trap, which the monitor stopped on, so the panel showed none. The exact `ELR` is INFERRED to be `0x...22A0`; boot 24's panic text lists that
  address in every backtrace (OBSERVED), the same site (INFERRED). Hiding a CPU feature in the ID registers does not apply, and emulation in the monitor is the wrong tool (INFERRED).
- **UNKNOWN.** The cause of the panic in boot 23 itself (its text never printed); boot 24 shows an ApplePMGR assertion with the same guest and `let_panic` set, so the same panic is the
  likeliest reading (INFERRED, not shown).

## 6. Boot 24: the ApplePMGR assertion, the nested panics and the chip-reset read

- **Page and panel (OBSERVED).** The panel printed `panic(cpu 0 caller 0xfffffe00097ddef0)` with `ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex <
  _cpuComplexCount`, nested panics up to `Nested panic count exceeds limit 5, machine will reset or spin`, and `wdog: panic chipreset`. The page ended in `MON STOP S2_FAULT
  PC=0XFFFFFE0008BA7358 A=0X3082BC224 B=0X938A0005` at 2.2465 s (a 32-bit read, translation fault at level 1: INFERRED decode).
- **Build identity (VERIFIED, host).** The Mach-O build identities of the kernel and of the kexts in the panic's backtrace equal those of the local kernelcache (one digit of the kernel's
  identity was misread on the film). The panic caller is the return address of the last call in the outlined failure code of `enableCPUComplex`, and the literal of line 4861 is in the
  kernelcache.
- **Why the assertion failed (VERIFIED chain; the count INFERRED).** The assertion is driven by XNU's topology, not by a hardware read. The driver keeps `_cpuComplexCount` at most XNU's
  cluster count, and the T8140 power manager's start-time method calls `enableCPUComplex` with the constants 0 and then 1. With `cpus=1` XNU has one cluster, so the call with 1 asserts. The boot
  CPU's own complex (0) passes: the failing call is the constant 1, which corrects the first hypothesis of the boot 24 reading. That the count is 1 at the stop is INFERRED (nothing read it),
  and so is the mode of the driver's initialisation (the word that selects it has no initialiser store in either kext: UNKNOWN). A host replay of the kernel's `cpus=` and `cpumask=`
  parser on the staged device tree gives: the default 6 CPUs in 2 clusters (the driver passes, as in boot 21); `cpus=1` 1 CPU in 1 cluster (asserts: boots 23 and 24); `cpus=2`, 3 and 4 one cluster
  (predicted to assert too, not run); `cpus=5` two clusters; `cpumask=0x11` two CPUs in two clusters. The word `cpus` does not occur in the two power-manager kexts (VERIFIED) and a one-CPU
  topology has one cluster whatever the device tree lists, so no guest-data change keeps a one-CPU topology past this driver (INFERRED from those two reads).
- **The nested panics are XNU's own behaviour for this device tree, not a monitor artefact (VERIFIED chain; the message INFERRED).** The function that saves the panic information has no NULL
  check on its global pointer; that pointer is stored only after four device-tree lookups succeed, among them a `pram` node with a non-zero `reg` and `/chosen/embedded-panic-log-size`. The staged
  device tree has `pram` `reg` of (0, 0) and no `embedded-panic-log-size`, so the pointer stays NULL and the first store faults; the fault enters the panic path again (`panic_with_thread_kernel_state`),
  which traps to the debugger and runs the same function, until `nested_panic_max` (default 5, VERIFIED as the variable's value) ends it with `Nested panic count exceeds limit 5` and a chip reset.
  The 12-frame cycle of the backtrace decodes as trap, fault, panic and trap again. The `Nested panic string:` looks empty because that string is printed to the UART path, whose bytes the monitor's console
  ring keeps (the first 1 KiB; the page shows six lines of 64 columns), while the panel gets only the video console's text (INFERRED). The monitor lets kernel page faults through unconditionally and
  `let_panic` was needed only for the first trap (VERIFIED, `probes/xnuboot/trap_dispatch.c`).
- **The stop (VERIFIED code; the register's name INFERRED).** `A=0X3082BC224` is the one 32-bit register that is the second window of the staged device tree's `wdt` node; the PC is in the watchdog
  timer driver, in the function that prints `wdog: panic chipreset`, and the stop is the read of a read-modify-write of the watchdog's reset register (the register number in `B=` matches). The
  monitor models no device there, which is why the run ended; the note's judgement (INFERRED) is that no model is needed, because the stage-2 fault is the earliest possible end after the panic text.
- **Tested later.** The topology reading was tested by boot 25, which dropped `cpus=1` (section 8, row 6).
- **UNKNOWN.** The value of the count at the stop; the initialiser of the mode word; whether real hardware with `cpus=1` asserts the same way; what the nested string says.

## 7. Boot 25: the idle thread in a WFI

- **Page (OBSERVED).** `MON STOP WATCHDOG PC=0XFFFFFE000B8716A8 A=0X9600004F B=0XFFFFFECFFBFDC000 C=0XFFFFFE000BA87E` (two digits of `C=` cut) at 60.18 s (`TICKS=1155520917`), `MON EXIT` `SPSR=0X604003C4`
  (EL1t, D, A, I and F masked). No panic text; the log was silent for the last 25 s or so ([SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 23).
- **VERIFIED (host).** The PC is the instruction after a `wfi`, in a four-word routine that is identical in structure to the public `arm64_retention_wfi` of `osfmk/arm64/machine_routines_asm.s`.
  Its only caller is the kernel's `cpu_idle`, entered by a tail branch from `Idle_context` (which saves the callee-saved registers and switches to the CPU's interrupt stack), whose only caller is
  `machine_idle`, called only by the scheduler's idle loop (`processor_idle`). `machine_idle` is entered with the interrupts masked and unmasks them only after `Idle_context` returns, which matches
  the `SPSR`. Any pending interrupt, masked or not, ends a WFI, and the monitor's stop timer is one such event: `MON EXIT PC` is the WFI plus 4 (INFERRED that this is what the stop caught).
- **INFERRED, high.** CPU 0 was in its idle thread, with every other thread blocked, with interrupts masked, when the box ended. `kernel_bootstrap_thread` is blocked, not "in a different wait".
- **The last EL1 exception (INFERRED, medium).** `A=` is `ESR_EL1` 0x9600004F: a data abort at the same level, a write, a permission fault at level 3, at the page-aligned `FAR` on the page; the `ELR_EL1` is
  most probably the first frame push of `machine_idle`. Kernel permission faults go first to the pmap's fast-fault path and only then to `vm_fault`; the idle path runs masked and no panic followed, so
  the fault was most probably recovered. The region of that address is UNKNOWN.
- **The USB lines are synchronous failures (INFERRED).** The three lines at the end of the log (the XHCI driver found no Type-C PHY provider and no PHY service, the hub IC device no host port) are the end of a
  start that returned failure; the XHCI kext has no call to the timed-wait API, so no waiter is left by them. They are not a stop for a kernel-only run.
- **The timer, as the monitor gives it (VERIFIED, code).** XNU on T8140 reads the count through an Apple alias register and keeps its deadline in an Apple alias pair (a control and a timer-value register), and
  never touches the architectural virtual or physical timers or counters in the kernel's text (a byte scan of their encodings found none; the kexts were not scanned). The monitor traps those aliases (`HCR_EL2.TIDCP`), keeps
  the deadline in software, arms its own EL2 timer (`CNTHP`, PPI 26: the interrupt that also ends the run) to the earliest of the guest's deadline, a device deadline and the run budget, and delivers the expiry as a virtual
  FIQ (`HCR_EL2.VF`). The GIC quarantine covers only the physical sources other than PPI 26 (SGI 0 and PPIs 23, 25, 27 and 30), which this path never uses. In the M1 regime (`x4` = 0) there is no 1 ms physical
  poll, so an idle CPU 0 wakes only at its guest deadline or at the box end; the A16's six-vCPU runs have that poll, so their timer evidence does not cover this regime.
- **The first reading of the idle did not survive (INFERRED).** The reading in the first note of the boot, that the guest's timers are quarantined so that no timer interrupt can ever arrive, is not supported by the
  code above. The silence of 25 s fits a legitimate kernel wait (every timed wait expires after the box, then a root-device wait with no timeout and no text) as well as a dead timer. Whether any guest FIQ was delivered
  in boot 25 is UNKNOWN: the monitor's report window holds the guest-timer words (reads, writes, interrupts, asserts, clears, vector entries, control, compare value, interrupt PC), and the stick reporter prints none of them.
  In-boot evidence leans, weakly, to working timers (the PMU's 1 ms retry sleeps block on a timer unless the CPU's idle latency is above 1 ms; INFERRED, medium), and the same timer code ran XNU to userspace on the A16 (that
  chain's record; not re-checked here). Boot 26 answered both questions: its timer counters show guest timer interrupts delivered (the counters are OBSERVED, the delivery INFERRED from them), and the idle was a wait of `bsd_init` (INFERRED; section 7A).
- **UNKNOWN.** Why the kernel is idle; which wait `bsd_init` was in at 59 s of kernel time; what ended the pause of about 15 s before the USB lines; whether the CPU-boot thread sits in its wait for CPU 1 (INFERRED
  once the starts are acknowledged) and which drivers wait on it; whether the virtual FIQ is delivered and a pending masked one completes a WFI on Oryon v1 under NV2; how many of the CPU-start stores the acknowledge
  took (the report window holds the count, and no reporter prints it).

## 7A. Boot 26: the scaler's register pulse, and the real-time clock waits behind the silence

- **Page (OBSERVED).** `MON STOP S2_FAULT PC=0XFFFFFE00095FF9AC A=0X291000004 B=0X93880045 C=0XFFFFFE63AA8A0004` at 61.60 s (`TICKS=1182753011`, a box of 120 s), `MON EXIT` `SPSR=0X80400204`; `MON ESR` gives class `0x24`, a valid
  syndrome, size 4, `SRT` 8, `WnR` 1 and `DFSC` `0x5` (a translation fault at level 1) and `MON WVAL` `X8=0X1 DATA=0X1`: a 32-bit write of 1. `MON TIM IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=969 CTL=0X1` and
  `MON ACK COUNT=15` are on the same page. The film shows a silence of about 28.5 s, then about 40 new kernel lines at once at t0 + 60.4 s, a partial last line, and the page at t0 + 61.6 s
  ([SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 24). No panic text.
- **Provenance.** The labels are those of the stop-site analysis written after the boot (kept outside this repository); this note's author did not re-read the kernelcache. Names are INFERRED, as in section 1.
- **Owner (VERIFIED).** The fileset entry whose own segment covers the PC is the scaler's driver (`AppleM2ScalerCSCDriver`); the device tree's `scaler0` node (`compatible` `scaler,t8101 scaler,s5l8960x`) is its
  personality, and the address `0x291000004` is in that node's first window. `scaler0` has four windows, `0x291000000` (size `0x38000`), `0x291210000` (`0x4000`), `0x291204000` (`0x4000`) and `0x290000000` (`0x8000`);
  its DART is a separate node, `dart-scaler`, with one window, `0x291260000` (`0x20000`), and is not part of `scaler0` (it stays in the quiet profile's tree). Read on the host in the local DeviceTree.j700ap through the
  `ranges` of `arm-io`: a file, not a run.
- **The function (code VERIFIED; the name INFERRED).** It is a step of the driver's hardware-abstraction layer, run once from the layer's initialisation that ends in `Scaler HAL[%u] Initialized.` (25 layer classes
  carry a copy). It maps the first register window (`Couldn't map register memory!` otherwise), reads the device tree's `hardware-version` of the node (22), and when that is below `0x1a` writes 1 and then 0 to
  word 1 of the window, then reads word 0: **one pulse, not a loop** (the function has no branch back; the first reading of the stop as a loop is withdrawn) and not a reset or a clock gate. For version 22 the value read
  back is discarded. What the register is, is UNKNOWN.
- **What the survey of the registers gives (VERIFIED code; INFERRED outcome).** The next reads are the chip version, taken from a register of the power manager's window (what the monitor's power-manager model returns
  there: UNKNOWN), and a match of (version, hardware version) against a 47-row table that has both possible rows for 22. A model that reads zeros and ignores writes would pass the identify step. The steps after it, by
  literals (order INFERRED), are the power and clock enables, the driver's DART, a firmware load through the scaler's own instruction memory and a wait for the firmware's acknowledge (`Firmware initialization failed to
  ack` on zeros: a failed start, no panic literal seen, the wait's length UNKNOWN). A model that lets the driver succeed must emulate the scaler's firmware CPU, which is not cheap; the data-only answer is to withdraw `scaler0`
  from the guest's device tree (the quiet profile of the host notes does, with 42 other nodes).
- **Why it runs now (VERIFIED mechanism; the order UNKNOWN).** Eleven kexts carry the literal `IOSurfaceRoot`; nine wait for the service with no timeout (the video decoder, the video encoder, the JPEG and ProRes engines,
  the scaler, the display controller, the display coprocessor's driver, the GPU family and the camera; the GPU, ISP and neural-engine nodes are deferred in the base profile). `IOSurfaceRoot` matches on the
  `IOBSD` resource, which `bsd_init` publishes after the burst of lines, so every waiter is released at the same moment; the scaler first is OBSERVED, the order of the others UNKNOWN. The other 51 nodes with absent or partial
  models survived 61.6 s with their drivers eligible (OBSERVED): only a driver released by `IOBSD` or `IOSurfaceRoot`, or one with a timer longer than 61 s, can still end a run. The video decoder, ranked first by the host list, was
  not touched before the stop (a touch of its window would have stopped the run: INFERRED from the fail-closed rule).
- **The silence (VERIFIED statically; the timing INFERRED).** `bsd_init` starts about 0.4 s into the call and its first call, the time initialisation, waits 30 s for the `IORTC` resource and then goes on to the
  calendar's first read of the platform clock, which waits 30 s for the same resource again: 0.4 + 30 + 30 = 60.4 s, the time at which the burst came. The kernel's idle thread is what boot 25 caught inside that second
  wait, its box of 60 s having ended there (INFERRED). No third clock wait follows: the next call only reads the calendar and warns for a negative time.
- **`IORTC` is never published (VERIFIED code; the cause UNKNOWN).** It is published by the real-time clock driver's start after its `registering service` line, or by the platform expert's hook for a device-tree node
  that matches the key `rtc`; the local device tree has no such node. The film shows the driver's first line (`started`) and the PMU clock child's lines and never the `registering service` line (OBSERVED in the three frames viewed; a film is not proof of absence; boot 27's film has three start lines and no such line on any frame of its machine OCR, section 7B), so the start
  returned false or blocked before it (UNKNOWN which, and why); both waits expire by timeout.
- **A correction.** The two messages of those waits (`... RTC did not show up` and `... "getGMTTimeOfDay" call failed with`) are `kprintf` lines. `kprintf` goes to the serial output when the serial console is
  initialised (VERIFIED, the public `pe_kprintf` source), not to the video console, so the film cannot show them. The analysis after boot 25, the first version of
  section 9 below and a later design note expected them on the film; that was wrong and is fixed here. The correction is about those two messages only: the clock driver's own start lines are
  on the film (OBSERVED in boots 26 and 27), so a statement that "the RTC messages cannot be on the film" is not true of them (section 7B).
- **The partial line (strings and plist VERIFIED; the match INFERRED, high).** `Load request for %s failed: %d` is printed by the BSD kext starter when it cannot load a kext; its VPN personality (`IOResourceMatch`
  `IOBSD`) loads the PPP, L2TP and PPTP network extensions in that order, the kernelcache has the first two and no PPTP, and `L2TP domain init complete` came just before the partial line, so the line is
  `Load request for com.apple.nke.pptp failed: <err>`; the console stopped in mid-line when the monitor stopped the run. Harmless.
- **Memory (INFERRED from VERIFIED code).** `memorystatus: System is unhealthy` with 8838 free pages of 16 KiB (about 138 MiB of the 384 MiB guest) and `wired_mem_exhaustion: 1` ends in `no victim found (action: 10)` and no panic
  path was read: a warning now, repeated with a back-off, and a blocker for launchd and daemons later. The A16 chain's installed-fresh guest is 8192 MiB (VERIFIED, repo); 384 MiB is the M1 builder's size, chosen to fit
  the runner's free low extent (590 MiB, OBSERVED). The smallest guest that suffices for launchd is UNKNOWN; 2 GiB in a bank placement is a guess (INFERRED), 8 GiB is the evidence-backed size.
- **UNKNOWN.** Which of the two waits the film's quiet stretch was (the second is INFERRED by arithmetic only); why the clock driver's start stops; what causes the USB lines at t0 + 31 s; the order of the `IOSurfaceRoot`
  waiters; what the scaler's register does and how long its firmware wait is; what the monitor's power-manager model returns for the chip-version read; what `AppleT8140CLPC` does with the CPU-initialised resource; which drivers
  beyond the nine touch hardware after `IOBSD`; the exact threshold of the wired-memory warning; the gates on launchd's path.

## 7B. Boot 27: the kernel's first wait, for a root device

- **Page and film (OBSERVED).** `MON STOP WATCHDOG PC=0XFFFFFE000B8716A8 A=0X96000047 B=0XFFFFFE110E00C040`, `C=0XFFFFFE000BA87EA0`, at 120.185 s (`TICKS=2307553890`, a box of 120 s), `MON EXIT` `SPSR=0X604003C4`; `PANIC=0`, `UNDEF=0`, `ABORTS=0`;
  `MON TIM IRQS=1560 ASSERTS=3134 CLEARS=3134 VECTORS=1519 CTL=0X1`. The film: the burst of `bsd_init` lines at t0 + 60 s (boot 26's block, finished) ends in `Waiting on <dict ...>` for an `IOMedia` whose `Content` is `Apple_HFS`, then 59 s of
  silence and no panic text ([SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 25). The guest is boot 26's with the `quiet` device tree (43 nodes withdrawn by data) and `cpu_boot_timeout_secs=120`.
- **Provenance.** The labels are those of the reading of the film and the stick and of sections 7 to 9; this note's author did not re-read the kernelcache, and no host analysis of boot 27 was done.
- **Not a stop at a device (OBSERVED).** No `S2_FAULT` of any address came in 120.185 s: the scaler's stop did not return, and the JPEG, encoder and display drivers did not stop the run (whether they started is UNKNOWN).
  The monitor ended the run because its box ended, as in boot 25.
- **The wait (INFERRED, from the VERIFIED code of section 9, item 2).** The line is `IOFindBSDRoot`'s text, printed before its wait for a root device (a matching `IOMedia`); the guest has no storage, so the wait has no end, in silent 60 s loops whose first timeout
  falls at about t0 + 120 to 122 s, outside the box. The PC is boot 25's, the instruction after the `wfi` of the retention-idle routine, with every interrupt masked (the address is OBSERVED; the idle thread INFERRED, section 7); timers
  are delivered (`VECTORS` above 0 is the normal page of a waiting kernel), and the armed deadline lies 176 ms past the last clock read (arithmetic on the page).
- **The last EL1 exception (INFERRED).** `A=` is `ESR_EL1` `0x96000047`: a data abort at the same level, a write, a translation fault at level 3; boot 25's was a permission fault at level 3 with a `C=` in the same region. The kernel handled it (no panic text, `PANIC=0`);
  which routine is at `C=` and why a kernel write faults there is UNKNOWN (`tools/kc-disas.py` on the local kernelcache, not done).
- **The clock (OBSERVED lines; the cause UNKNOWN).** At t0 + 7 s the film has `AppleARMRTC started!#####`, `AppleDialogSPMIPMURTC started!******` and `Read RTC offset from leg_scrpad`, then the PMU's SPMI reads (address f704, count 4; 8800, count 5; 56 bytes) fail the parity check
  (`parity_exp` is one bit per byte, all ones for zero data: the monitor's SPMI model's parity bits are the suspect, INFERRED). `registering service!` is on no frame (the OCR cannot read every frame). INFERRED that the driver's start does not reach its publish of `IORTC`, so the
  two 30 s waits of section 7A still end by timeout, at t0 + 60 s as in boot 26; the two timeout messages are `kprintf` lines and are on no frame. The earlier sentence that the RTC messages cannot be on the film holds for those two messages only.
- **Memory (OBSERVED).** The warning of boot 26 (section 7A) is on boot 27's film too: `memorystatus: System is unhealthy` with 9,094 free pages of 16 KiB (about 142 MiB), `wired_mem_exhaustion: 1` and `no victim found (action: 10)`; no panic.
- **UNKNOWN.** A root device; the CPU-boot panic (about 122 s with the word 120, outside the box); what the 60 s loops print; the routine at `C=`; why the PMU reads fail the parity check; whether withdrawing the scaler alone sufficed.

## 7C. Boot 28: the first userspace boot, a failed boot task of `launchd`

- **Page and film (OBSERVED).** `MON STOP S2_FAULT PC=0XFFFFFE0008BA7358 A=0X3082BC...` (the digits after `BC` are cut off by the frame), `MON ESR EC=0X24 ISV=1 SIZE=4 SRT=10 WNR=0`, `SPSR=0X604003C5`, at 40.082 s (`TICKS=769573015`, a box of 300 s); `PANIC=13`, `ABORTS=0`, `MON TIM IRQS=6525 ASSERTS=12376 CLEARS=12375`. The films ([SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 26): `launchd` ran from the RAM-disk root, its first boot task `mount-phase-1` failed, and the guest printed `userspace panic: boot task failure: mount-phase-1 - exited due to SIGABRT`, nested panics and `wdog: panic chipreset`. The guest is boot 27's with the `quiet-rtc-acm` tree (the clock bound, `acm_fastsim=1`), a 1.86 GB RAM-disk root, 6,144 MiB of RAM, `cpu_boot_timeout_secs=360` and a box of 300 s; rung 8, the clock alone, was skipped and never run.
- **Provenance.** The labels are those of the reading of the films and the stick and of sections 7 to 9; this note's author did not re-read the kernelcache, and no host analysis of boot 28 was done.
- **A userspace panic, then boot 24's stop (OBSERVED text; the chain INFERRED).** The first panic is not a kernel assertion: `launchd`'s first boot task, `mount-phase-1`, failed (the text: `exited due to SIGABRT`), `launchd` printed `Boot task failed: mount-phase-1` and `Panicking in 3 seconds.`, and the panic text calls itself a userspace panic. It is the first time that the stock kernel ran EL0 code under the monitor. The monitor's own end is boot 24's stop (section 5): the same PC, the same syndrome (a 32-bit read into `w10`), the same `SPSR`, and `A=` equal to boot 24's in the digits that are not cut off, after `Nested panic count exceeds limit 5, machine will reset or spin` and `wdog: panic chipreset`; the read is INFERRED to be the kernel's reset request, as then.
- **The failure (OBSERVED text, verbatim in section 26.3 of the log).** `mount-phase-1` looked for volumes of role 0x10 and 0xC0 in the APFS container of the root device `/dev/disk1s1`, found none (`volcnt = 0`), ignored the failure of the Update volume, waited ten times (about 30 s) for an APFS IOKit node that never came, and failed the boot task. The roles are Preboot and Update (INFERRED from the numbers). The ISC container (the roles xART, Preboot and Hardware) is not in the RAM disk: that `launchd` wants the ISC's Preboot volume is INFERRED, strongly. What `volcnt = 0` counts, whether an ISC with no content satisfies the task and what the following boot tasks need are UNKNOWN; the BaseSystem image's boot-task list is on the host and can be read there (not done).
- **The root mount (INFERRED).** The failure text names the root device and the ignition library's volume group is the profile's, so the RAM disk of `/chosen` took and the kernel mounted the root; the `BSD root` line is on no film, and the wait of section 7B did not end the run (the run reached `launchd`). Nothing of the kernel's log from the call to `launchd` is on a film (UNKNOWN).
- **The clock (OBSERVED date; the rest INFERRED).** `launchd`'s calendar date is the clock model's seed, 2026-11-20T00:00:00Z, plus 4.3 s (arithmetic), so the kernel read the PMU clock that the model serves and `IORTC` was published (INFERRED from the date; the driver's lines are on no film), and the two 30 s waits of section 7A are not in this run (INFERRED: the whole run to the panic took 40.08 s). That the clock binding is what removed them is INFERRED, not separated: rung 8, which would have tested the binding alone, was skipped, and boot 28 changed the binding, the RAM disk, `acm_fastsim=1` and the size of the guest together. How `launchd`'s 4.3 s fits boot 27's clock driver start at t0 + 7 s is UNKNOWN.
- **EL0 (OBSERVED running; the reading INFERRED).** `launchd`, `dyld` with its shared cache and the ignition library ran for about 36 s under the monitor's user-address-space path (arithmetic on `launchd`'s calendar second and `TICKS`), and no user-mode fault stopped the run. Beyond that run nothing is known about user-mode stability: the ASID and TLBI scope under many processes, pointer authentication in userspace (`launchd` is arm64e) and `SIGILL` from `WFE` are UNKNOWN.
- **The nested panics (OBSERVED text; the reading INFERRED).** The backtrace lists `com.apple.driver.RTBuddy`, `AppleA7IOP`, `AppleARMPlatform`, `IOSlaveProcessor`, `CoreAnalyticsFamily`, `IOReportFamily` and `corecrypto`: the panic path went through the coprocessor-firmware services, which are absent (INFERRED, noise); the exact path is UNKNOWN.
- **UNKNOWN.** The kernel's log from the call to `launchd`; what the next boot tasks of `launchd` need; whether an ISC with no content satisfies `mount-phase-1`; the CPU-boot panic (about 362 s with the word 360; the run ended at 40.08 s); the guest's WD1 (its counter wraps at 178.96 s, after the end); the digits cut off the page.

## 8. Predictions that were tested (boot 21 to boot 26)

Rows 18 to 23 were tested by boot 27 and rows 24 to 30 by boot 28 (the heading's range is kept so that its anchor holds).

| # | Prediction (where it was made; its label then) | Tested by | Outcome | Label |
| --- | --- | --- | --- | --- |
| 1 | After boot 20: with the panel handed, the stop PC is no longer the loop (the falsifier is the same PC) | boot 21 | held | OBSERVED (another PC, phases after the I/O Kit start-up on the panel); the cause chain stays INFERRED, because `serial=2` changed with it |
| 2 | After boot 20: the next stop is the first CPU-start write, a stage-2 fault with an IPA | boot 21 | held, by address | OBSERVED (`A=0X300734004`); that it is that write INFERRED |
| 3 | After boot 21: with `cpus=1` no CPU-start write and no `failed to boot` panic | boot 23 | held in part: the word was honoured and the CPU-start stop is gone; the run then ended at the debugger trap | OBSERVED (the stop); INFERRED (the word honoured, the trap) |
| 4 | After boot 21, from the A16's record: a reduced CPU set trips a PMGR assertion, and the panel text would name it | boots 23 and 24 | held in shape: an ApplePMGR CPU-complex assertion with `cpus=1` | OBSERVED (the text); that it is the A16's assertion UNKNOWN |
| 5 | After boot 23: letting the guest's own panic handler run (`let_panic`) prints the panic text | boot 24 | held | OBSERVED |
| 6 | After boot 24: the assertion follows XNU's topology, so dropping `cpus=1` (six CPUs, two clusters) passes the driver | boot 25 | held: no ApplePMGR assertion, the run went far beyond | OBSERVED; the chain VERIFIED; `cpus=2` to `4` asserting is untested |
| 7 | The rung 5 record: the acknowledge is enough, no read or poll follows the CPU-start writes | boot 25 | held in what a page can show: no stop in the CPU-start block, no read there; how many stores were taken is not on the page | OBSERVED (the absence); the count UNKNOWN |
| 8 | The rung 5 record: no `cpu 1 failed to boot` panic inside the box (120 s timeout, 60 s box) | boot 25 | held, and not a test of the wait: the box ended first | OBSERVED (no panic text) |
| 9 | The rung 5 record, from the host analysis: the run may end within seconds of the acknowledged stores at the video decoder's first unmodelled window (IPA `0X380001000`) | boot 25 | did NOT occur: the run lasted the whole box with no stage-2 fault | OBSERVED; whether the decoder driver started at all is UNKNOWN (no line of it was read) |
| 10 | The analysis after boot 24: the clean end, once the unmodelled devices are passed, is `WATCHDOG` at the box end with a quiet kernel (a silent root-device wait) | boot 25 | held in shape (`WATCHDOG`, about 25 s of silence); the stop PC is the idle path, so which wait it is stays UNKNOWN | OBSERVED (shape); INFERRED (idle) |
| 11 | The first reading of boot 25: the idle waits for a timer interrupt that the GIC quarantine never delivers | later host reading | not supported by the code (section 7); not refuted by a run either | VERIFIED (the code reading); the delivery UNKNOWN then, the counters OBSERVED and the delivery INFERRED from them in boot 26 (row 14) |
| 12 | After boot 25 (the rung 6 record): the CPU-start acknowledge is enough to go on, as boot 25 showed in 60 s | boot 26 | held for 61.6 s: the guest made 15 CPU-start stores that the monitor took (`MON ACK COUNT=15`) with nothing started, and went on to `bsd_init`; the kernel's own wait for the CPUs was not tested (row 16) | OBSERVED |
| 13 | After boot 25 (the host analysis): the 25 s of silence was a wait that ends, so the kernel goes on after it | boot 26 | held: the silence of about 28.5 s ended at t0 + 60.4 s with the burst of `bsd_init` lines | OBSERVED (the end of the silence); INFERRED (that it was the second real-time clock wait, section 7A) |
| 14 | After boot 25 (the timer design note): the guest's timers work, because the monitor delivers its own EL2 timer as a virtual FIQ (row 11) | boot 26 | held: `VECTORS=969`, `IRQS=1010`, `ASSERTS=CLEARS=2036` and `CTL=0X1` with a deadline about 9.6 ms ahead at the stop; boot 25's silent wait-for-interrupt was a kernel wait and not a dead timer; in hindsight its page would have been the record's class NOT DUE (a deadline armed and not served) | OBSERVED (the counters); INFERRED (that guest timer interrupts reached the kernel, from the vector entries, and the hindsight reading) |
| 15 | After boot 25: the next stop is an unmodelled device, a stage-2 fault with an IPA (the video decoder ranked first, the scaler second) | boot 26 | held in kind: `MON STOP S2_FAULT` with `A=0X291000004` in the scaler's window; the decoder, ranked first, was not touched before it | OBSERVED (the stop); INFERRED (the decoder not touched, by the fail-closed rule) |
| 16 | After boot 25 (the rung 6 record): with `cpu_boot_timeout_secs=90` the kernel's panic `cpu 1 failed to boot for the first time, waited 90 seconds` comes at about 92 s, inside the box of 120 s, and proves the timers | boot 26 | NOT TESTED: the run ended at 61.60 s on the scaler stop, 30 s before that time; no panic text of any kind came | OBSERVED (the time of the stop and the absence of text); the prediction itself UNKNOWN |
| 17 | After boot 25 (the first note of section 9): the RTC lines `... RTC did not show up` and `... "getGMTTimeOfDay" call failed with` would be on the film at about 60 to 65 s | boot 26 | did NOT occur, and could not: they are `kprintf` lines that go to the serial output and not to the video console, so no film can show them (section 7A) | VERIFIED (the code path); OBSERVED (the absence on the film) |
| 18 | The rung 7 record: the quiet profile removes the scaler's stop, so `A=0X291000004` does not come back | boot 27 | held: no `S2_FAULT` of any address in 120.185 s | OBSERVED |
| 19 | The rung 7 record: `bsd_init` runs at about t0 + 60 s as in boot 26 and `Waiting on <dict>` is the last text, about 62 s in | boot 27 | held, 2 s early: the burst ends in `Waiting on <dict ...>` for an `IOMedia` of content `Apple_HFS` at t0 + 60 s | OBSERVED (text and time); INFERRED (that it is the root-device wait) |
| 20 | The rung 7 record and section 9, item 4: with the scaler and the decoder withdrawn the clean end is the box's end in a silent wait, `VECTORS` above 0 | boot 27 | held: `MON STOP WATCHDOG` at 120.185 s, 59 s of silence, no panic text, `VECTORS=1519` | OBSERVED |
| 21 | The rung 7 record: the CPU-boot panic (word 120) falls about 122 s in, outside the box | boot 27 | NOT TESTED: the box ended first, as designed; no panic text inside it | OBSERVED (the absence); the prediction UNKNOWN |
| 22 | Section 9, item 4 and the rung 7 record: the next stop after the scaler is a JPEG, encoder or display window (low to medium) | boot 27 | did NOT occur in 120.185 s: the run ended in a wait; whether those drivers started is UNKNOWN | OBSERVED (the absence) |
| 23 | The rung 7 record and row 17: `... RTC did not show up`, `getGMTTimeOfDay` and `!BSD` are not on the film | boot 27 | held: none is on the film (the clock driver's start lines are: section 7B) | OBSERVED (the absences) |
| 24 | The rung 8 record (never run) and the rung 9 record, case A: with the PMU clock bound the kernel publishes its clock, the two 30 s waits are gone and the `bsd_init` block comes within about 10 s of the call | boot 28 (rung 8 skipped) | held in part: `launchd`'s calendar date is the seed plus 4.3 s, so the clock took and the run reached `launchd` within 40.08 s; the time of the `bsd_init` block is on no film | OBSERVED (the date); INFERRED (the publish, the waits gone); the block's time UNKNOWN |
| 25 | Section 7B and section 9, item 2: with no storage the root wait has no end; a carried RAM disk replaces it by the mount | boot 28 | held in shape: the kernel went on to `launchd`, and the failure text names `/dev/disk1s1` as the root device; the `BSD root` line is on no film | OBSERVED (`launchd`'s lines); INFERRED (the root mounted from the RAM disk) |
| 26 | The rung 9 record: with the RAM disk and `acm_fastsim=1` the kernel passes the import and the services to the mount and the first user process, which the monitor's user path had never run | boot 28 | held: `launchd` ran at EL0 and the monitor's user path served `launchd`, `dyld` and the ignition library; the stages before it are on no film | OBSERVED (`launchd`'s lines); INFERRED (the stages before it) |
| 27 | The rung 9 record, stage 6: userspace waits for the volumes of the ISC, which the image does not carry | boot 28 | held as text: `mount-phase-1` asked for roles 0x10 and 0xC0, found none and waited ten times for an APFS node; that these are the ISC's volumes is INFERRED | OBSERVED (the text); INFERRED (the ISC) |
| 28 | The rung 9 record: the clean end is `MON STOP WATCHDOG` at the box's end (300 s) with `launchd`'s lines | boot 28 | did NOT hold: the guest panicked in userspace and the run ended at 40.08 s with the monitor's chip-reset stop (`S2_FAULT`, boot 24's PC), a PANIC TEXT end that the record also names | OBSERVED |
| 29 | The rung 9 record and row 21: the CPU-boot panic (word 360) falls about 362 s in, outside the box of 300 s | boot 28 | NOT TESTED: the run ended at 40.08 s; no panic text of that kind | OBSERVED (the time of the end and the text); the prediction UNKNOWN |
| 30 | The rung 9 record: the guest's WD1 counter stays unarmed with `wdt=-1`, and a box of 300 s is the first to span its wrap at 178.96 s | boot 28 | NOT TESTED: the run ended at 40.08 s, before the wrap; no `MON STOP DEVICE_RESET` | OBSERVED (the end); the prediction UNKNOWN |

## 9. The next timed waits, and what would separate the cases (INFERRED; nothing built, nothing approved)

**What boot 26 observed after the last line of boot 25, and what comes next** (the timer path is INFERRED to work, from boot 26's counters, which are OBSERVED; the rest INFERRED from VERIFIED code, with the unknowns of section 10):

1. Two real-time clock waits, 0.4 + 30 + 30 = 60.4 s, ended by timeout (the silence of boot 26 ended at t0 + 60.4 s): `IOKitInitializeTime`, a 30 s wait for the `IORTC` resource, then the calendar's first read of the
   platform clock (`getUTCTimeOfDay`), a second 30 s wait for the same resource; the resource is never published (section 7A). Their messages (`...RTC did not show up` and `... "getGMTTimeOfDay" call failed with %x`)
   are `kprintf` lines that go to the serial output and not to the video console, so the film cannot show them: the first version of this item expected them on the film, which was wrong (VERIFIED, public source;
   corrected; the clock driver's own start lines are on the film, section 7B). No third clock wait follows.
2. Next, in `IOFindBSDRoot`, a stall of up to 30 s on the IOBSD resource (short in practice), `!BSD` only if an IOBSD-matched start hangs, then `Waiting on <matching dictionary>`, the one visible line. The root-device wait
   itself then prints nothing, in silent 60 s loops without end: `Still waiting for
   root device` is not in this kernelcache (VERIFIED absence; compiled out for macOS arm64 in the public `IOKitBSDInit.cpp`), and with `wdt=-1` no recovery boot is requested, and no panic. A run that no unmodelled device
   stops first ends in that loop, with `Waiting on <matching dictionary>` as its last text, at the box's end. Boot 27 ended there (section 7B).
3. The CPU wait. The CPU-boot thread's wait starts after the last acknowledged store, at about 2.3 s, and ends in the panic `cpu 1 failed to boot for the first time, waited N seconds` at about 2.3 s plus
   `cpu_boot_timeout_secs`, then nested panics and the chip-reset read of boot 24 (`MON STOP S2_FAULT` `A=0X3082BC224`). With 120 it fires at about 122 s, which is outside any box the wrapper takes (its cap is 120 s); boot 27's box ended at 120.185 s, so it was not reached;
   with 90, rung 6's word, at about 92 s, inside the box of 120 s, which boot 26 did not reach (its stop was at 61.60 s). The wait blocks only that thread (no lock is held) and the starts of three kexts that
   import the CPU-initialised resource, the watchdog timer's, the processor trace's and the CLPC's (what the last one does with it was not read: UNKNOWN); nothing on the root or launchd path was found (launchd's exec path
   was not examined).
4. The other `IOSurfaceRoot` waiters, released with the scaler (section 7A). The host list ranks them by their chance to end the run (INFERRED): the video decoder (a stage-2 fault at its window, `0x380001000`, high), the
   `apr` node (low to medium) and the JPEG engines (four nodes, low to medium) can stop it; the video encoders, the display controller and the display coprocessor's driver wait (for a power state and a firmware
   that never wakes) and are not expected to. With the scaler and the decoder withdrawn by data, the expected clean end is the CPU panic or the box's end, last text `Waiting on <matching dictionary>`; a stage-2 fault with an
   IPA in the JPEG, encoder or display windows would name the row that stopped it. Boot 27 withdrew the scaler and the decoder: the end was the box's end (rows 18 to 22).

The kernel prints nothing when I/O Kit quiesces (the `Waiting for matching to complete` literal exists but needs a debug flag).

**What would have separated "the timer works and the kernel waits for something else" from "no timer fires"** (the timer design note; INFERRED; settled by boot 26: the first case holds by the counters, INFERRED; the reporter printed the
words). The monitor's report window already exports the guest-timer words, and a
reporter that prints them is the missing piece: no monitor change, no new store class, a new record. Read against the clock adapter's words, the page then says one of four things: the timer works (interrupts and
vector entries counted, or a far deadline armed), nothing is armed (the kernel waits for another interrupt, which is not a timer matter), a deadline was armed and no tick came (the monitor's wake of an idle CPU is
the suspect), or the virtual FIQ was asserted and never taken (the architecture says a pending masked interrupt completes a WFI; Oryon v1 is UNKNOWN). A gated trace and a gated wake cap are options only after a
page shows a failure. A shorter `cpu_boot_timeout_secs` is guest data only and, if a deadline is delivered, ends the run in the kernel's own timed panic inside the box (`let_panic` is set), which would prove the
timer; silence to the box end would count against the timer reading. These are readings of a design note, not decisions.

**Real secondary CPUs** (the CPU-start design note; INFERRED, UNIMPLEMENTED in the monitor entry). The start machinery of the A16 is compiled into the M1 monitor but unreachable: the wrapper passes `x4` = 0, so the
CPU-start block is not routed to it. Its blockers on the SP11 are the wrapper (it cannot build the secondary configuration), the secondary's GIC start (the wrapper's arm makes `GICD_CTLR` 0x12 inside the call,
so the group-1 enable that the start needs is there, INFERRED for a secondary because none ran; the per-core enable register of the eleven other redistributor frames was never read: UNKNOWN), the cluster
difference (`MIDR_EL1` differs on clusters 1 and 2, and the NV2, timer and PPI measurements are cluster 0 only), the page (it prints no secondary status) and the board restarts that followed two of three
`cpuon` boots (cause UNKNOWN; [SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md) section 10 and [SURFACE-PRO-11.md](SURFACE-PRO-11.md) E63). It is the honest fix for six CPUs and the longest; nothing is chosen.

## 10. Open UNKNOWNs

- What the kernel waits for in each of its timed waits is now read (section 7A); what stays open is why the real-time clock driver's start stops before it publishes `IORTC`, which of the two clock waits the film's
  quiet stretch was (the second is INFERRED by arithmetic), and what causes the USB lines at t0 + 31 s. That guest timer interrupts are delivered is INFERRED from boot 26's counters (`VECTORS=969`, OBSERVED once), and no longer open as a question.
- What `_cpuComplexCount` was at boot 24's stop, the initialiser of the driver's mode word, and whether real hardware with `cpus=1` asserts the same way.
- Which kexts wait for `IOAllCPUInitialized` (published only after `sched_cpu_init_completed` returns): four kexts import its name, the platform driver that publishes it and the watchdog timer, processor trace and
  CLPC kexts (VERIFIED by a scan of the kexts' imports); what the CLPC kext does with it, and whether the CPU wait would panic at about 92 s with a word of 90, are UNKNOWN (boot 26 ended at 61.60 s).
- The run-time value of the CoreSight guard byte that `cpu_start` tests, and the defaults of the power-manager hook flags; whether other CPU-event listeners do MMIO when a secondary becomes active (the
  seven callbacks found are inert for the events a CPU start broadcasts: VERIFIED for those found; the search is INFERRED to be complete).
- Which of the driver families with unmodelled device windows would stop a longer run: boot 26 reached the first (the scaler, ranked second by the host list; the decoder ranked first was not touched before it). The order in which
  the other `IOSurfaceRoot` waiters run after `IOBSD`, which of them touches an unmodelled window next, and which drivers beyond the nine touch hardware after `IOBSD` are UNKNOWN.
- What the scaler's register does, how long its firmware-acknowledge wait is, and what the monitor's power-manager model returns for the chip-version read the driver makes.
- The smallest guest that is enough for launchd (384 MiB draws the kernel's memory warning; the A16 chain's guest is 8192 MiB; boot 28's guest of 6,144 MiB ran `launchd` for about 36 s, so the smallest is still UNKNOWN), the exact threshold of the wired-memory warning, and the gates on launchd's path (`launchd` ran in boot 28; its path past `mount-phase-1` is UNKNOWN).
- The unreadable parts of the runs: the value of the stores, the last digits of `C=` on a long line, the `REPORT.DAT` ring beyond the first 1 KiB (a run does not write it back). The count the acknowledge took is
  OBSERVED in boot 26 (`MON ACK COUNT=15`).
- After boot 27 (section 7B): a root device and what follows the first 60 s loop of the root wait; the CPU-boot panic at about 122 s; the routine at boot 27's `C=` and why its write faults; why the PMU's SPMI reads fail the
  parity check and whether that is why `IORTC` is not published; whether the JPEG, encoder and display drivers started; whether withdrawing the scaler alone would have sufficed.
- After boot 28 (section 7C): what `launchd`'s boot tasks after `mount-phase-1` need and whether an ISC with no content satisfies it (the BaseSystem image's boot-task list can be read on the host); what `volcnt = 0` counts; the kernel's log from the call to `launchd`;
  how `launchd`'s calendar second fits the clock driver's start of boot 27; whether the clock binding alone removed the two 30 s waits (rung 8 was skipped); user-mode stability beyond one run of about 36 s; the CPU-boot panic at about 362 s with the word 360; the guest's WD1 beyond 178.96 s.

## 11. Adding a stop to this note

A new section for a new boot keeps this shape: the page values as read (OBSERVED, one boot), what was read on the host (VERIFIED, with the sources named and no kernelcache bytes or listings), the
causes (INFERRED, with their strength), the predictions that the boot tested (a row in section 8, with the label of the outcome and what stays open), the UNKNOWNs (section 10), and the next waits or options
(INFERRED, none a decision). The page and the film belong in [SP11-STICK-LOG.md](SP11-STICK-LOG.md) and the plan and decisions in [SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md); this note holds only the
kernel's side of the stop. A label is never raised: a host read is VERIFIED only as "read on the host", never as "ran on the unit". Section numbers that other documents cite stay fixed, so a later stop takes the
next letter after section 7 (7A for boot 26, 7B for boot 27, 7C for the next) and its row goes to section 8, rather than renumbering; a stop that the host analysis corrects states the correction in the section it corrects and in section 8.
