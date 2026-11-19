# SP11 stick reporter: the window lines of SNAPREP-M1.BIN's page

Work item 4b, the reporter's part (docs/SP11-STICK-MONITOR.md section 4 has the page as a whole). EXPERIMENTAL; hardware NOT RUN: no value below has ever been printed by a unit.
Labels: VERIFIED is a host fact (a source read, a compile, a test on a modelled window); OBSERVED is read on the unit, and nothing here is yet; INFERRED and UNKNOWN as in the
other notes. The new lines print RAW words of the monitor's window. A value is what the monitor stored, never a finding: what a reader makes of it is INFERRED at best.

## 1. Why these lines

Boot 20 (M1 rung 1, one unit, one boot) stopped under the monitor with `MON STOP WATCHDOG`. The page gave the stop's PC, three counts, five events and six console lines, and a
reading of the kernelcache was needed to say what the guest was doing. The monitor's window held more: the guest's registers at the stop, thirteen counters, the calls by
SPTM endpoint, the clock's samples and the guest timer's words. SNAPREP-M1.BIN prints them now, and for a stop at a stage-2 fault the decoded syndrome and the value its store
wrote (boot 21, as the coordinator read it: the kernel stops at stage-2 faults whose registers are wanted, and whether a guest deadline ever fired was UNKNOWN because no timer
counter was printed; not re-read here). Boot 25 (the acknowledging monitor, a 60 s time box) stopped at the watchdog in an idle WFI and the page had no timer word either, and its `MON STOP` line was cut at 100 columns, losing the last two digits of `C=` (as the coordinator read it; not re-read here). It is still a reader of REPORT.DAT and a printer: its only store is into its own stack, it reads no system
register and runs no cache, translation or barrier instruction (tools/test-sp11-stick-wrapper-build.py and tools/test-sp11-stick-monitor-build.py audit the binary). The
monitor and SNAPMON-M1.BIN are not changed, and the reporter without the monitor gate (the dry run's, the GIC pair's) is byte for byte what it was.

## 2. The page of a guest that ran

After `MON STATUS=OK`, `MON COUNTS`, `MON STOP` and `MON EXIT` (unchanged), and only when the monitor's status is zero, in this order:

| Lines | Prints | Where in the window |
| --- | --- | --- |
| `MON ESR EC= ISV= SIZE= SRT= WNR= S1PTW= DFSC=` (1, only after `MON STOP S2_FAULT`) | the fields of the stop's syndrome (the `B=` of `MON STOP`): EC and DFSC in hex, the others in decimal (SIZE is the access size in bytes: 1, 2, 4 or 8), `NA` where the bits mean something else (section 4) | the terminal word at RP_W_TERMINAL 0x6840, word 3 |
| `MON WVAL X<n>= DATA=` (1, only for a stage-2 fault that is a store the instruction made, with the capture word 3) | the guest register that SRT names (`XZR` for 31, which is zero) whole, and `DATA=` its low SIZE bytes | the capture's x0 to x30, below |
| `MON GX0-3` to `MON GX28-SP` (8, only with a capture: the capture word 1, 2 or 3) | x0 to x30 and the selected SP, four to a line in `0X` hex; the last line is x28, x29, x30, SP | RP_W_ASYNC 0x7AC8: a capture word, then 36 words, x0 to x30, SP, SPSR, PC, ESR, FAR (`fill_regs`, probes/xnuboot/mmu_access.c); `MON EXIT` prints the last four |
| `MON CLK READS= AFTER= FIRST= LAST=` (1) | the trapped counter reads; `AFTER`: the monitor's last sample `LAST` less `ticks_call` of the wrapper's record, in counter ticks, or `NA`; the first and last raw samples of the clock backend (`LAST` is the report writer's own sample: section 4) | RP_W_CLOCK_READS 0x7C40 (`clock.reads`); RP_W_CLOCK_RAW 0x8520, two words (`clock_raw_first`, `clock_raw_last`) |
| `MON TIM` (1 or 2, only with a capture) | the guest timer: `IRQS ASSERTS CLEARS VECTORS`, `CTL` and `CVAL` in hex, then `WRITES`, `IRQPC` in hex and `READS`, each `NAME=value` | RP_W_TIMER 0x7C80, nine words (OUT_TIMER_FLOW: reads, writes, irqs, asserts, clears, vectors, ctl, cval, irq_pc) |
| `MON CNT` (2 or 3) | `MAPS MAPFAIL DISP PANIC UNDEF MAPOUT LOGDROP`, then `CONDROP RNGDROP MAPDEV MMIORD MMIOWR UARTTX`, each `NAME=decimal` | RP_W_COUNTERS 0x67C0, 13 words in that order |
| `MON ACK COUNT= <offset>=<value> ...` (0 or 1, only for an acknowledging monitor) | the guest's 32-bit stores to PMGR's CPU-start block that the monitor took and ignored: the count, then the first eight as the register's offset (4, 8 or 12) and the 32 bits written in hex, and `MORE=n` for the stores not listed; `MON ACK LAYOUT=0X.. NOT READ` for a block whose layout word is not the one this reads | RP_W_ACK 0xFA10, 24 words (OUT_CPU_START_ACK of probes/xnuboot/report_layout.c; compat/cpu-start-ack.h): `CPUACK01`, struct size << 32 \| version, the total, the counts at +4, +8, +12, the last store, the first eight stores as PC and what |
| `MON EP` (0 to 3) | `TOTAL=` and `OTHER=`, then `ENDPOINT:CALLS` for every bucket that is not zero, ascending, decimal | RP_W_ENDPOINTS 0x6870: 64 buckets, then the bucket of the other calls |

Two lines of the head of the window change when they would be cut or would say too little. `MON STOP`: when `C=` would pass the runner's 100 columns (boot 25: a kernel address of 16 digits after a long head lost its last two digits), `C=` goes on a line of its own, `MON STOP C=0X...`.
`MON CAPTURE WORD=0X... IS NO CLASS` (status zero only) replaces `MON EXIT` and the registers when the capture word is not 0 and not 1 to 3: a window of another build or a damaged one, which the page says and does not read as a capture; the word 0 is a window that took none and prints nothing.

All offsets are bytes from the window's start (REPORT.DAT + 0x1000) and are compiled against the monitor's own translation unit (VERIFIED: the drift test of tools/test-sp11-stick-monitor-record.py);
which counter is at which index, which word of the guest timer is which, the classes of the capture word, and that bucket n counts endpoint n, are read from the monitor's sources by
tools/test-sp11-stick-monitor-page-sources.py.

## 3. The budget, and the width of a line

The runner keeps 40 lines for a payload (the page has 82 of the unit's 89 rows with the wrapper's 30). Five lines are kept for the GIC lines, which follow the window's. After `MON EXIT`
each group above is printed, in the order of the table, only if after it one line is left for the first event and one for the first console line (when there are any). The events and
the console then share what is left: the first event, then up to six console lines, then the other events (five in all). `MON TIM`, like `MON GX`, is printed only with a capture, so that the
page of a window without one keeps the lines it had (tools/test-sp11-stick-monitor-page.py holds `panic-entry-nocap` to them); `MON ESR`, which is the stop's own, is the one new line that prints
with or without a capture, and `MON ACK` is the block's, with or without one. Two groups have a variable number of lines: `MON TIM` has a second
line only when the counters (two lines) and the acknowledge line (when the monitor has one) still have theirs after it, and `MON CNT` a third only when a line is left for the histogram after it. The histogram is
the lowest of the groups and keeps no line for itself. `MON STOP C=` and `MON CAPTURE` come before the groups and are not budgeted. The page of the shape of boot 25 (an acknowledging monitor, a watchdog stop with a capture of class 1 and an empty console) is exactly the 40:
15 head and monitor lines, `MON STATUS`, `MON COUNTS`, `MON STOP` (two lines), `MON EXIT`, 8 `MON GX`, `MON CLK`, two `MON TIM`, two `MON CNT`, `MON ACK`, one `MON EV` and 5 GIC lines. Worst case for a stage-2 store: 15 head and monitor lines, 4 window lines, `MON ESR`, `MON WVAL`, 8 `MON GX`, `MON CLK`, one `MON TIM`, two `MON CNT`, 5 GIC lines, one
`MON EV` and one `MON CON` make the 40 and leave no line for `MON EP` or `MON ACK`; with an empty console `MON ACK` has its line, and for a load, or a stop that is not a store, the histogram has its line too. No line named by the record of the run is left
out of that page, none is reworded and no GIC line gives way (tools/test-sp11-stick-monitor-page.py, `CITED`); the lines that give way are instances of the two repeated lines, `MON EV 1` to `4`
and `MON CON` after the first, and the histogram, which the record does not name.

The runner cuts a line at 100 characters, and a value that is cut is a wrong value. A line built from values is never wider than 95: `MON GX`, `MON ESR` and `MON WVAL` cannot pass it (their
fields have fixed widths); in `MON CLK`, `MON TIM`, `MON CNT` and `MON EP` a value is added only if the line stays within 95 characters, with 9 to spare on the last line the group may use unless
the value is the last there is, which is the room ` MORE=n` needs. What does not fit goes on the next line of its group while there is one, and otherwise the line ends with `MORE=n`, the number of
values left out, in the order of the table: never a cut value. The older lines (`MON COUNTS`, `MON STOP`, `MON EXIT`, `MON EV`) are unchanged and can still be cut at 100 by values wider than any real one.

## 4. What the values would mean

**Registers (`MON GX`).** VERIFIED by reading: x0 to x30 are the trap frame's, the SP is SP_EL1 when SPSR_EL2 bit 0 is set and SP_EL0 otherwise, the PC is ELR_EL2. INFERRED: they are the
guest's at the instruction `MON EXIT PC=` names, for an interrupt, a stage-2 fault or a device-window stop. The capture word is 1 (an interrupt or a stop with no syndrome: ESR and FAR zero),
2 (a trap or monitor-call stop, ESR and FAR as that site fills them) or 3 (a stage-2 fault or a device-window failure; ESR is ESR_EL2 and the FAR word is the FAR or the IPA of that site): INFERRED from the
call sites, not printed. Those are the only values the monitor stores (VERIFIED: every `async_captured =` of probes/xnuboot, tools/test-sp11-stick-monitor-page-sources.py); any other word, a
second bit set included, is no capture: no `MON EXIT`, no `MON GX`, no `MON WVAL`, no `MON TIM`, and the window has no integrity word to say more. A stop that no site captured gets a capture of class 1 at the exit of the trap path (probes/xnuboot/trap_entry.c) or of the asynchronous one (async.c, the watchdog's):
INFERRED from reading those sources, so a GUEST_EXC or PANIC_ENTRY stop is expected to print its registers too; a page of such a stop without `MON GX` lines says only that the capture word was not 1, 2 or 3 (UNKNOWN why).
The stop-site analysis of boot 20 predicts x10 = 0 and x21 = 1 (INFERRED, falsifiable by this page); a value says nothing else by itself.

**Syndrome and value written (`MON ESR`, `MON WVAL`).** Only a `MON STOP S2_FAULT` has them: EV_S2_FAULT is `a` = the IPA (0 when neither the stage-1 walk nor HPFAR could name it), `b` =
ESR_EL2, `c` = the guest VA, pc = the guest's PC (probes/xnuboot/report_layout.c, trap_dispatch.c: VERIFIED by reading). The fields are where probes/run.py `s2_fault_words` and the monitor's own
`dc_decode` (shadow/dockchannel.c) take them, and tools/test-sp11-stick-monitor-page.py holds the line to that host decoder on sixteen syndromes: EC is ESR[31:26], 0X24 a data abort and 0X20 an
instruction abort from the guest's EL1 (any other EC is no guest stage-2 fault: every field but EC is `NA`); ISV is ESR[24], SAS ESR[23:22] (the access is 1, 2, 4 or 8 bytes for 0 to 3, printed as `SIZE=` in bytes, so that a store of a W register reads `SIZE=4` and not the code 2), SRT
ESR[20:16] (the guest register, 31 being the zero register), WNR ESR[6] (1 a store) and S1PTW ESR[7] (the fault is on the stage-1 table walk, whose access is the hardware's and not the instruction's):
those of a data abort, and SIZE and SRT only with ISV set (ISV clear, a pair or a writeback form, leaves the instruction at the PC as the only word on what it did: they print `NA`); DFSC is
ESR[5:0] in hex, the fault status of a data or instruction abort (0X4 to 0X7 a translation fault at level 0 to 3, 0X8 to 0XB an access flag fault, 0XC to 0XF a permission fault, by `FSC_KINDS` of run.py).
`MON WVAL` is printed only for a store the instruction made (EC 0X24, ISV and WNR set, S1PTW clear) and only with the capture word 3, the class of a stage-2 fault, whose registers are those of the faulting
instruction (VERIFIED by reading trap_dispatch.c): `X<n>` is that register whole and `DATA` its low SIZE bytes, which is what the monitor's `dc_narrow` makes of it for its own handlers; INFERRED:
that is the data the access carried. Not decoded (UNIMPLEMENTED): the other stops that carry an ESR, `EV_MMIO_STOP` (its `b`) among them.

**Clock (`MON CLK`).** READS counts the reads `guest_clock_access` answered (CNTVCT, CNTVCTSS and the two Apple encodings; the locked and the fast path call it: VERIFIED by reading compat/clock.c,
compat/fast-trap.h). FIRST and LAST are the host-rate CNTVCT_EL0 at the first and at the last sample the monitor's clock backend took (`clock_counter`, probes/xnuboot/sysreg_clock.c, which stores every
sample as `clock_raw_last`), for any caller: the prepare, WFE waits, timer programming, device models and a guest read. LAST is therefore the monitor's OWN sample at the report: the report writer reads the
counter itself while it writes the report (probes/xnuboot/report.c, for the guest timer's control word), so LAST is NOT the guest's last use of the clock and a small AFTER does not say that the backend was idle (VERIFIED by reading).
CNTVCT_EL0 is the physical count only while CNTVOFF_EL2 is zero; the SP11 guard refuses a launch otherwise (compat/sp11-timer-guard.h: VERIFIED by reading, checked at launch only). AFTER is LAST minus the wrapper's
counter read before the call, in ticks of CNTFRQ (`MON RET TICKS=` is the whole call in the same unit): the time from the call's start to the report, normally a little below TICKS because the report is written just before the call
returns. It is `NA` when there was no sample or when LAST is outside the call, below its start or above its end (`ticks_return`): a sample is taken inside the call, so one outside it means that the units disagree
(CNTVOFF_EL2, a frequency adapter) and AFTER can never be above TICKS. AFTER and LAST say how far the report was from the call's start and nothing about the guest: no line measures when the guest last read the clock, or that it stopped.

**Guest timer (`MON TIM`).** The nine words of OUT_TIMER_FLOW (probes/xnuboot/report.c, which probes/run.py prints as the "deadline timer"; VERIFIED by reading, with the update sites): IRQS is
`timer_irqs`, the monitor's own timer interrupts taken (INTID 26, probes/xnuboot/async.c, compat/fast-timer-irq.h): they serve every deadline the monitor keeps (its stop, its devices' ticks, the guest's),
so IRQS alone says nothing about a guest deadline; ASSERTS and CLEARS are `timer_asserts` and `timer_clears`, HCR_EL2.VF set and cleared for the guest by the monitor's interrupt sync (stage2.c,
fast-timer-irq.h), the line being the guest timer's pending state or a posted IPI (`guest_ipi_fiq`); VECTORS is `timer_vectors`, entries into the guest's FIQ vectors (0X100, 0X300, 0X500) with VF
set (trap_dispatch.c, compat/fast-vector.h), which also record `EV_TIMER_FIQ`; CTL is `guest_timer_control` at the report (ENABLE 1, IMASK 2, ISTATUS 4: 5 is a deadline that has passed and is
not masked, which is what `guest_timer_pending` tests), CVAL the guest's compare value, WRITES and READS the guest's writes and reads of the timer's registers and IRQPC ELR_EL2 at the last of the monitor's timer
interrupts. The order on the page puts the chain that delivers a guest deadline and the guest timer's state first, so that a page with one line for them (a stage-2 store) keeps the most that
way: `IRQS ASSERTS CLEARS VECTORS CTL CVAL`, then `WRITES IRQPC READS`. INFERRED: ASSERTS and VECTORS both zero means that no guest timer line was ever raised and taken (VERIFIED that those are the only
places that count them); both non-zero means that a guest deadline fired and was taken, unless an IPI was posted (UNKNOWN whether any is in M1). WRITES zero means that the guest never programmed its timer. Read together (INFERRED): IRQS and VECTORS above zero, or CTL 0X1 with CVAL far ahead, a timer that works; CTL 0X0 or 0X2 with WRITES above zero, a timer armed and left disabled
or masked; CTL 0X5 with IRQS zero, a deadline that has passed and no tick of the monitor's; CTL 0X5 with ASSERTS above CLEARS and VECTORS below ASSERTS, a virtual FIQ raised and never taken (tools/test-sp11-stick-monitor-page-ack.py reads these four from the page text alone).
The line comes with a capture (the capture word 1, 2 or 3): the monitor leaves one at every stop that ends a run (INFERRED, section 4), and a window without one keeps the page it had.

**CPU-start acknowledge (`MON ACK`).** The block of a monitor built with the acknowledge (compat/cpu-start-ack.h, docs/SP11-MONITOR-PROFILE.md section 10; `xnuboot_report` writes it at the normal return, with zero stores too; a monitor without it leaves
zeros): COUNT is every 32-bit store the guest made to PMGR's CPU-start block at offset 4, 8 or 12 (the global, E-cluster and P-cluster masks) that the monitor took and ignored, no CPU started; then the first eight as `offset=value`, the 32 bits written, and `MORE=n`
for the stores not listed (those past the eight the block keeps and those that do not fit the line, counted exactly, so that the room for it is too). Every acknowledged store is a 32-bit one (`cpu_start_ack_match`), so the width is not printed. The block is
there when its first word is `CPUACK01`; one whose layout word (struct size << 32 | version) is not 176 << 32 | 1 is named, `MON ACK LAYOUT=0X.. NOT READ`, and not read. COUNT=0 is a block with no store, no line a monitor without the block (or a window that is not status zero).
VERIFIED by reading: the words and their order (tools/test-sp11-stick-monitor-page-sources.py); OBSERVED once (boot 25): that the run passed the CPU-start stores and did not stop at the three registers, not how many were made nor their values: the counts and the values in the fixtures are MADE UP.

**Counters (`MON CNT`).** The fields of `struct context` (probes/xnuboot/context.c), VERIFIED by reading: MAPS `map_pages` (SPTM MAP_PAGE calls completed), MAPFAIL `map_failures` (of those, returned an
error), DISP `dispatch_cmds` (calls refused for not being endpoint calls), PANIC `panic_calls` (panic-entry calls served), UNDEF `undef_skipped` (instructions stepped over for a CPU that lacks them),
MAPOUT `map_outside` (mappings outside the declared memory), LOGDROP `log_dropped` (events the log had no room for: the `DROPPED=` of `MON COUNTS`), CONDROP `console_dropped` (console bytes past 1,024, counted not
kept), RNGDROP `ranges_dropped` (mapping spans that did not fit the table), MAPDEV `map_device` (mappings of a device the guest's tree names), MMIORD and MMIOWR `mmio_reads`, `mmio_writes` (accesses answered by the
UART window model, which records no event), UARTTX `uart_tx` (bytes the guest wrote to its UART's transmit register: one console byte each).

**Calls by endpoint (`MON EP`).** Every call that reached the dispatcher is counted before it is answered (probes/xnuboot/sptm_dispatch.c): bucket n for an endpoint call with endpoint n below 64, OTHER for anything else
(another domain or table, a dispatch command that is not a call, an endpoint of 64 or more). TOTAL is the 64 buckets and OTHER added; `MON COUNTS CALLS=` is a count the monitor keeps on its own, INFERRED equal when every
path counts, and a difference is a finding about the counters and not about the guest. SERIAL_PUTC (45) is counted and not logged. The names (decimal and hex number, from SPTM's own dispatch table as probes/run.py holds
it; tools/test-sp11-stick-monitor-page.py ties this table to it):

| N | Hex | Name | Read as (INFERRED) |
| --- | --- | --- | --- |
| 0 | 0x00 | LOCKDOWN | called once the kernel locks down, late in its start: a count above zero says the kernel got that far |
| 1 | 0x01 | RETYPE | page-frame typing, with the page-table calls below the bulk of early boot |
| 2 | 0x02 | MAP_PAGE | one call per page mapped, devices included |
| 3 | 0x03 | MAP_TABLE | |
| 4 | 0x04 | UNMAP_TABLE | |
| 5 | 0x05 | UPDATE_REGION | |
| 6 | 0x06 | UPDATE_DISJOINT | |
| 7 | 0x07 | UNMAP_REGION | |
| 8 | 0x08 | UNMAP_DISJOINT | |
| 9 | 0x09 | CONFIGURE_SHAREDREGION | |
| 10 | 0x0A | NEST_REGION | |
| 11 | 0x0B | UNNEST_REGION | |
| 12 | 0x0C | CONFIGURE_ROOT | an address space is made: user processes |
| 13 | 0x0D | SWITCH_ROOT | an address space is switched |
| 14 | 0x0E | REGISTER_CPU | CPU bring-up |
| 15 | 0x0F | FIXUPS_COMPLETE | the first calls of the kernel |
| 16 | 0x10 | SIGN_USER_POINTER | |
| 17 | 0x11 | AUTH_USER_POINTER | |
| 18 | 0x12 | REGISTER_EXC_RETURN | the first calls of the kernel |
| 19 | 0x13 | CPU_ID | CPU bring-up |
| 20 | 0x14 | SLIDE_REGION | |
| 21 | 0x15 | UPDATE_DISJOINT_MULTIPAGE | |
| 24 | 0x18 | GUEST_VA_TO_IPA | |
| 25 | 0x19 | GUEST_STAGE1_TLBOP | |
| 26 | 0x1A | GUEST_STAGE2_TLBOP | |
| 27 | 0x1B | GUEST_DISPATCH | |
| 28 | 0x1C | GUEST_EXIT | |
| 30 | 0x1E | HIB_BEGIN | |
| 31 | 0x1F | HIB_VERIFY_HASH_NON_WIRED | |
| 32 | 0x20 | HIB_FINALIZE_NON_WIRED | |
| 33 | 0x21 | IOFILTER_PROTECTED_WRITE | |
| 39 | 0x27 | SET_SHARED_REGION | |
| 40 | 0x28 | BATCH_SIGN_USER_POINTER | |
| 41 | 0x29 | SURT_ALLOC | |
| 42 | 0x2A | SURT_FREE | |
| 43 | 0x2B | CONDEMN_LEAF_TABLE | |
| 44 | 0x2C | UNCONDEMN_LEAF_TABLE | |
| 45 | 0x2D | SPTM_SERIAL_PUTC | one call per console byte the kernel printed through the monitor |
| 46 | 0x2E | SPTM_SERIAL_DISABLE | the kernel takes its serial line back |
| 50 | 0x32 | OUTPUT_AREA | CPU bring-up |

## 5. Not printed

UNIMPLEMENTED, for want of lines: the tail of the console (the window holds the first 1,024 bytes only; the 64 KiB tail ring is in the monitor's work arena and is never copied to the window), the
frames of the first and last guest exception (`first_exc`, `last_exc` at OUT_GUEST_REGS) and of the panic entry (`panic_exc` at OUT_PANIC_REGS), which are in the window whether or not the capture of a
GUEST_EXC or PANIC_ENTRY stop also holds them (UNKNOWN for this build: it depends on the stop's path), the capture word itself (its class says what ESR and FAR mean in `MON EXIT`), the last two words of
OUT_TIMER_FLOW (the watchdog's deadline and the repaired step exceptions), the acknowledge block's counts at +4, +8 and +12, its last store and the guest PC of each store, and the syndrome of every stop but a stage-2 fault's. The reporter also prints nothing of a window whose record is damaged or whose monitor did not return zero, and a REPORT.DAT of another size, magic or version is
refused by the checks it has always had (`REFUSED=FILE_SIZE`, `REPORT_MAGIC`, `REPORT_VERSION`, `REPORT_RESERVED`, `REPORT_VALUE`; `MON RECORD DAMAGED`, `MON NO RECORD`): no register, counter or histogram line comes from it.

## 6. Evidence

PASS on the host, never on a unit: tools/test-sp11-stick-monitor-record.py (the layout compiled against the monitor's translation unit, the acknowledge block's too; the page of a guest that ran against the independent renderer of
tools/sp11_stick_monitor_pagelib.py), tools/test-sp11-stick-monitor-page.py (the boot-20 situation, stage-2 faults with an IPA and sixteen syndromes held to the host's decoder, a panic entry with and without a capture, a guest exception,
a full window and the worst stage-2 page, windows as wide as the lines can be made, a capture word the monitor never stores, a clock outside the call; malformed REPORT.DAT files), tools/test-sp11-stick-monitor-page-ack.py (the four
outcomes of the guest timer read from the page text alone, the acknowledge line, the capture marker, the stop's `C=` at the edge of the runner's width, and the words of the documents about AFTER and LAST), on the windows of
tools/sp11_stick_monitor_pagefix.py, tools/test-sp11-stick-monitor-page-mutants.py (eighty-nine mutants of the reporter, among them the registers in another order, a width guard removed from the clock, the counters, the timer and the acknowledge, a capture word of any value,
the guest timer without a capture, a wrong word of the timer or the acknowledge, a sample above the call's end, a wrong bit of the syndrome, a load shown as a store, the stop's `C=` one column off, and twenty budget mutants built with
the room that exposes each), tools/test-sp11-stick-monitor-page-sources.py (the ties to the monitor's sources) and tools/test-sp11-stick-gatescan.py (the provenance pin of the GIC pair, which the reporter's gated lines must not trip). The reporter is
58,634 bytes of the 65,536-byte cap (58,977 bytes before this work). A page of a real run is the only evidence that the values are what the monitor held.
