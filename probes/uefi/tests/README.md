<!-- SPDX-License-Identifier: MIT -->
# SP11 X2 EL2 tests (the functional instrument for kill criterion K1)

The probe's image plus nine tests, built only on request. Operator-facing
description: [docs/SP11-X2-TESTS.md](../../../docs/SP11-X2-TESTS.md). Plan context:
[docs/SURFACE-PRO-11.md](../../../docs/SURFACE-PRO-11.md) sections 6.2, 6.3 and 9.

**Hardware status: NOT RUN.** QEMU with EDK2 is a model; a PASS there says the
logic, the vector and ERET mechanics, the record and the screen work against a
standard virtual machine, not that the Surface Pro 11 behaves the same way.

```sh
python3 tools/build-uefi-tests.py --soc x1e80100        # build/uefi/sp11-el2-tests.efi (hardware)
python3 tools/build-uefi-tests.py --soc x1e80100 --variant declared   # build/uefi/sp11-el2-tests-declared.efi
python3 tools/build-uefi-tests.py --variant all --soc x1e80100 --verify-reproducible
python3 -B tools/test-uefi-tests.py                     # every host test, one command
python3 -B tools/test-uefi-tests.py XNativeTests        # one class (or Class.test_name)
```

The default probe build is untouched: `probe_main.c` has additive hooks under
`#if PROBE_TESTS_IMAGE` and preprocesses to the same text without the flag (its build ids change
only because they hash the source). The tests image is produced only by
`tools/build-uefi-tests.py`. The `declared` variant adds one more block, under
`PROBE_TESTS_IMAGE && TESTS_DECLARE_EBS_RANGES`; with that gate off, which is every other
variant, `probe_main.c` and `hook.c` preprocess to exactly the text they had before the gate
existed (see "The `declared` variant" below).

## Layout

| Part | Files |
| --- | --- |
| Vocabulary: labels, verdicts, result rows | `tests_types.[ch]` |
| ID decode, NV classification, HCR value, syndrome helpers | `decode.[ch]`, `regs.[ch]` |
| NV2 decision, run and record | `nv2.[ch]`, `nv2rec.[ch]` |
| Register save/restore, per-test scope | `sysstate.[ch]` |
| Guarded-access policy | `guard.[ch]` |
| ACPI parsers (MADT, MCFG, IORT) and the read windows | `acpix.[ch]`, `env.[ch]` |
| The tests | `t_misc.c` (T01 to T04, T08, T09), `t_gic.c`, `t_pcie.c`, `t_smmu.c` |
| Report, layout, countdown | `report_x.[ch]` |
| USB result writer | `stick.[ch]` |
| Privileged instructions (the only place) | `sys_aarch64.c`, `vectors_aarch64.c`, `sys.h` |
| Entry hooks | `hook.c`, `tests_hook.h` |
| Board descriptor cross-check | `desc.h`, `desc_none.c` (generated `desc_data.c` for hardware) |
| Declared-ranges table and its check (`declared` variant only) | `ebs_ranges.[ch]` |
| Host tests | `test/test_x_*.c`, `test/suite_x.py`, `test/suite_x_ebs.py`, `host/audit.py`, `host/nv2screen.py`, `../../../tools/test-uefi-tests-logic.c` |

## Safety design

* **Order.** The tests that touch no device (T01, T03, T04, T02, in that
  least-intrusive-first order) run before ExitBootServices (phase A) on a
  platform already at EL2 there, and their results reach the stick. A platform
  whose only path to EL2 is during/after this firmware's own ExitBootServices
  call (OBSERVED, twice, on a Surface Pro 11 X1E80100: `tests_hook_pre_ebs`'s own
  CurrentEL check there decoded to EL1 both times, because the Secure-Launch SMC
  sequence runs inside slbounce's ExitBootServices hook) instead runs the same
  four, in the same order, at the start of phase B (`tests_hook_post_ebs`),
  screen only: `g_res[id].phase` (tracked per test) says which ones phase A
  already ran, so phase B runs exactly the rest and never repeats a test phase A
  did. The read-only device tests (T09, T05, T07, T06, T08) always run in phase
  B, after any such fallback, and are always screen only: nothing may reach the
  stick once ExitBootServices has returned.
* **No write path to a device.** `tests_bus` (`env.h`) has a guarded 32-bit read
  and a guarded system-register read, nothing else. A read is made only inside a
  window the test justified from ACPI (MADT, MCFG, IORT) or from a register it
  has just read; the board descriptor is compared, never used as an address.
  `host/audit.py` checks this from the source and from the built image.
* **A device window is refused unless it is plausible.** A base from a table (or
  from a BAR) that is zero, not aligned to the frame the table names (64 KiB for
  the GICv3 distributor, the ITS and an SMMUv3, a page for a redistributor range
  and an NVMe BAR, 1 MiB for an ECAM window), empty, wrapping or beyond the 52-bit
  physical address space is dropped by the parser (or refused by the test) and
  never allow-listed. A window that the UEFI memory map says is something other
  than MMIO (RAM, firmware, ACPI, reserved: any overlapping descriptor of another
  type, even beside one that says MMIO) is refused too, because a read there
  answers with whatever is stored and would pass for a register. With no valid
  memory map no window is read. A window the map does not mention at all is read
  by default, because the map of the QEMU machine lists none of the GIC, ECAM, SMMU
  or NVMe windows (OBSERVED; whether the Surface Pro 11's does is for the first run
  to show). `--require-mmio-map` makes every window need full MMIO coverage in the
  map. Each of T05 to T07 prints `MAP MMIO=n UNLISTED=n REFUSED=n` (and
  `REFUSED: <reason>` rows), and a refusal is never a PASS or a FAIL: it is UNKNOWN.
* **Every register write is saved first and restored on every path.**
  `sysstate.c` refuses a write whose old value was not saved and verifies each
  restore by reading back. The saved state covers `ELR_EL2` and `SPSR_EL2` as
  well (every guarded exception and every ERET into the EL1 stub overwrites
  them), taken before the first exception can reach this image's vectors. The
  restore order is HCR_EL2 and VNCR_EL2, the EL1 registers, `ELR_EL2`, `SPSR_EL2`,
  `VBAR_EL2`, then DAIF last: an interrupt that became pending during a test is
  taken only when the firmware's vectors are back. `host/audit.py` checks that
  the register enum, the code that reads and writes the registers, the restore
  order and its own write list name the same registers.
* **A restore that fails ends the run.** A failed restore, or a failed roll-back
  of a set-up, sets a sticky flag and makes `tests_scope_enter`, `nv2_prepare` and
  `tests_run_nv2` return a distinct "cleanup failed" status. From then on the
  image runs no further test, writes nothing to the stick, calls no Boot Services
  function and never reaches ExitBootServices: it makes one more attempt to put
  everything back, masks interrupts, draws a framebuffer page and halts (it does
  not reset: a reset would run the firmware's reset path under the state that
  could not be restored).
* **The temporary HCR_EL2 keeps the firmware's bits.** Only VM, FMO, IMO, AMO,
  VF, VI, VSE, TGE, HCD, NV, NV1, AT and NV2 are cleared and RW is set, then NV
  and NV2 as the step asks; `HCR_EL2`, `VNCR_EL2`, `SCTLR_EL1`, `VBAR_EL1` and
  `SP_EL1` are read back before the ERET to EL1, and a mismatch rolls back and is
  reported as a mechanics fault, never as a K1 verdict. The audit requires that
  every register write of `sys_aarch64.c` is read back.
* **A fault is a result.** Guarded reads run under this image's EL2 vector table
  (installed and removed per test). A fault on a load, an `mrs` or the one
  `dc zva` is skipped, the destination becomes all ones and the fault is counted.
  An SError raised by a guarded read resumes at an explicit failure continuation
  with SError masked, never at the instruction that raised it. Anything else, or
  any exception while no guard is armed, is fatal: a page says so and the image
  halts.
* **No firmware call inside the scope.** The firmware console unmasks interrupts
  on its way out, and a timer interrupt would reach this image's vector table,
  which cannot service it. Console text produced while the scope is installed is
  held back and printed after the scope is left.
* **The framebuffer is the probe's.** `hook.c` adds no GOP logic: the image takes
  the framebuffer `probe_gop_select` chose (`fbtext.c`). Started from the UEFI Shell,
  which replaces the console output handle, that is the framebuffer every usable
  GOP agrees on (`SEL=SAME`); two different usable framebuffers refuse as
  `AMBIGUOUS GOP`. With no framebuffer `tests_hook_pre_ebs` refuses before any test
  runs (`SP11X2 REFUSE REASON=NO LAYOUT FOR THE REPORT`, then `EBS STATE=SKIPPED
  REASON=TESTS REFUSED`): no ExitBootServices, no phase A or phase B, no report
  pages and no `x2-*.txt` file; only the probe's own dump is on the stick (OBSERVED
  in QEMU with no display device). `suite_x` boots the model image through the
  pinned Shell when `SP11_UEFI_SHELL` names it (SKIP otherwise); that is OBSERVED
  in QEMU, and the unit's console stack is UNKNOWN until X1.
* **A fatal page needs only the framebuffer.** The layout validated before
  ExitBootServices is kept, so a failure in phase A puts a page on the screen
  instead of a console line that may be invisible on the machine. Nothing is
  written to the console after that page: a console line scrolled the whole page
  up a row under QEMU, and the call can take an interrupt that these vectors answer
  by parking. The console is only the fallback when there is no page to draw.
* **The NV2 decision uses behaviour, not ID bits.** `tests_nv2_decide` returns GO
  only for NV == 2, NV_frac == 0, plausible ID registers, a restored and
  fault-free run, a landing at VNCR offset `0x090` and a trapped control. All
  other inputs are NO-GO or INCONCLUSIVE; the host tests sweep the whole input
  space (22,400 combinations) for exactly one GO. HCR_EL2.NV is never written on
  a core whose ID registers do not advertise FEAT_NV.
* **A model result can never be mistaken for a measurement.** The label shows as
  `OBS-MODEL`, the record's evidence is EXPERIMENTAL and its tool is
  `SP11-X2-MODEL`; the monitor's gate refuses it on its evidence (tested).

## The `declared` variant (opt-in)

`-DTESTS_DECLARE_EBS_RANGES=1`, built only by `--variant declared` (`model-declared` is its
emulator twin): the hardware image plus a declaration of the memory the image dirtied, for the
reviewed slbounce fork built with `SLBOUNCE_SCOPED_SWEEP=1`. That build flushes only the ranges an
application declares in a volatile UEFI variable and returns `EFI_ABORTED` from its
ExitBootServices hook, printing nothing, when the variable is absent or names a range it cannot
justify; q1n1 declares the same way. The default image declares nothing, so that build would
refuse it. The hardware runs so far used a test-only slbounce with its sweep compiled out, which is
not in the reviewed set. The default `sp11-el2-tests.efi` is unchanged: the gate is off in it.

* **One range:** this image's whole PE image, `ImageBase` and `ImageSize` of its
  `EFI_LOADED_IMAGE_PROTOCOL`, chunk cap 1 MiB. Every buffer the image reads after the bounce is
  static storage inside it (`g_memmap_buf`, `g_ebs_map_buf`, `g_bounce`, `g_text`, the tests'
  state, the EL1 stub, the VNCR page), so one range covers them. The stack and the framebuffer are
  not declared (the framebuffer is a RESERVED descriptor on the unit, which the fork refuses by
  design).
* **The variable:** `Q1N1Sp11EbsRanges`, GUID `1a3c9e02-7b6f-4d81-9c2e-535031314552`, attributes
  `EFI_VARIABLE_BOOTSERVICE_ACCESS` only, a 160-byte version-1 table. `ebs_ranges.h` is an
  independently written copy of the layout q1n1 and the fork share; tests keep them in agreement.
  It is written in `probe_main.c` after `tests_hook_pre_ebs` and the existing refusals and before
  the ExitBootServices loop, which fetches a fresh memory map on every attempt, so nothing is
  allocated between the final `GetMemoryMap` and ExitBootServices.
* **Refusal before writing:** the range is checked against the image's own parsed map by the
  fork's rule (inside exactly one descriptor, of type 1 to 6 or 9, with `EFI_MEMORY_WB`), plus a
  page-aligned base, no wrap, and at most 512 descriptors (the fork's limit). A failure, a failed
  `SetVariable` or a missing loaded-image protocol prints
  `SP11PROBE EBS STATE=SKIPPED REASON=<why> BASE=... SIZE=...` (and `STATUS=...` for
  `SetVariable`), waits 10 s and returns `EFI_ABORTED` without calling ExitBootServices. Success
  prints `SP11PROBE EBSRANGES STATE=DECLARED RANGES=1 BASE=... SIZE=... STATUS=...`.
* **Limits.** INFERRED: nothing the image reads after the bounce depends on lines that are not
  declared (the firmware's stack, with the hook's context on it, among them); the earlier runs had
  no sweep at all and worked, so declaring the image is at least as protective, which is inference
  and not a test. The variable is volatile and is not deleted if ExitBootServices then fails; a
  reset clears it. UNKNOWN until a run: that this firmware's map holds the image in one
  descriptor that passes the rule (the X1 probe dumps, OBSERVED, have 226 to 227 descriptors and
  `EFI_MEMORY_WB` set on every descriptor of the swept types, but do not say which holds this image).
* **Tests:** `XEbsRangesTests`, `XDeclaredBuildTests`, `XDeclaredAuditTests` and
  `XDeclaredQemuTests` in `test/suite_x_ebs.py` (the last also runs three test-only builds that
  each make one step of the declaration fail, and checks the refusal line and that
  ExitBootServices is never reached), and the native sections `ebs *` of
  `test/test_x_ebs.c`: exact bytes, a differential run against `tools/sp11_ebs_sweep_model.py`,
  seeded builder bugs, the gate-off text against the baseline revision (`HEAD`, or
  `UEFI_TESTS_BASELINE_REV`; a baseline that already has the gate is a SKIP, never a pass), and
  the audit's pin on the one `set_variable` (`host/audit.py`). `SLBOUNCE_SP11_DIR` additionally
  compiles the fork's own validator and range rule against the builder (SKIP when unset).

**Hardware status of this variant: NOT RUN.**

## Public facts used

Linux v6.12 (tag commit `adc218676eef25575469234709c2d87185ca223a`, GPL-2.0-only,
read for facts only; no code or text is copied): `arch/arm64/include/asm/vncr_mapping.h`
(TPIDR_EL2 at 0x090), `arch/arm64/kvm/hyp/vhe/switch.c` (NV, NV2 and the VNCR write),
`arch/arm64/include/asm/esr.h` (EC values and the system-register ISS layout),
`arch/arm64/tools/sysreg` (ID register fields), `include/linux/irqchip/arm-gic-v3.h`,
`include/linux/nvme.h`, `include/uapi/linux/pci_regs.h`,
`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.h`, and `include/acpi/actbl2.h` (MADT,
MCFG, IORT layouts). Each source file names the lines it took a fact from.

## What is EXPERIMENTAL

* The EL1 stub, the vector tables and the ERET mechanics are exercised under
  QEMU 11.1.3 (which implements FEAT_NV2) and by the audit of the image, never on
  an SP11.
* The SError continuation (`tests_bus_ldr32`) is covered by the host policy tests
  and the audit only: QEMU answers an unmapped read with a synchronous external
  abort, so no run has taken an asynchronous SError. Whether one can re-fire after
  the continuation masks SError is for the first run to observe.
* The HCR_EL2 / VNCR_EL2 read-back compares the whole register. Its logic is
  host-tested; on QEMU the writes always stick, so no run has seen a mismatch.
* The device-window policy (see Safety design) is host-tested with zero,
  misaligned, wrapping and RAM bases; QEMU exercises only the default policy. Which
  windows the Surface Pro 11's memory map lists as MMIO is unknown until the
  probe's `memmap.txt` is read.
* CNTVOFF across cores is not implemented (it needs a PSCI-started second CPU).
* T08 (ADSP, PMIC-GLINK) has no address source and is UNIMPLEMENTED.
* No display test: no study of the panel registers existed when this was written.
