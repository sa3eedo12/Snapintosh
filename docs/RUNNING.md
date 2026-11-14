# Running

This document walks through one boot of the installed macOS on the A16, driven
from the Mac with `tools/cycle.py`. Placeholders are in angle brackets.

> Read the warning in the [README](../README.md) first. A run programs the GPU,
> clocks and memory-bandwidth votes, and the guest writes the internal SSD.

## Before the first run

- q1n1 is installed on the A16 as a UEFI boot entry, and macOS 27 is installed
  on the internal NVMe. See q1n1's `docs/A16-INSTALL.md` and
  [FULL-INSTALL.md](FULL-INSTALL.md).
- The Apple inputs are extracted and the components are built
  ([BUILDING.md](BUILDING.md)).
- The A16 is connected to the Mac by USB-C, and preferably on AC power. Every
  hardware mode refuses to start below the battery threshold
  (`--battery-minimum` can raise it, never lower it).
- Find the Mac-side network interface that q1n1's USB NCM link created (for
  example with `ifconfig`). Pass it to every hardware command as
  `--device udp://<interface>`. The name changes with the USB port.
- On q1n1's gadget-NCM link (the one `a16-link-rearm.py arm` brings back after
  a reset, q1n1's `docs/A16-USB-REARM.md`), run with `Q1N1_UDP_RETRIES=2` in
  the environment. A memory write that loses a datagram is then
  resynchronised and repeated by q1n1's Link, and the control guard records
  each loss (`transport_loss`, `transport_loss_recovered` in the launch
  record's `control_guard` events) instead of freezing the session. Any other
  transport failure, and a write that still fails, freezes it as before.
- Create `tools/cycle_lib/pins.json` from `tools/cycle_lib/pins.example.json`
  and fill in the device and stage hashes ([BUILDING.md](BUILDING.md)).

## The cycle

`tools/cycle.py` runs one boot as a fixed sequence. Each mode records what it
did under `build/<run-prefix>*`, checks the records of the previous step, and
refuses when the machine is not in the state that step expects. Every mode has
an offline `--check` that validates everything without touching hardware. At
any point,

```sh
python3 tools/cycle.py next
```

prints the exact next command for the current records.

### 1. Cold boot into q1n1

Power the A16 on into q1n1, or from the Mac:

```sh
python3 ../q1n1/tools/a16ctl.py boot q1n1
python3 tools/cycle.py status
```

`status` makes one read-only proxy query: the generation, the exception level,
the image base, any SError, and the battery.

### 2. Move to an idle, chainloaded generation

A boot runs on a freshly chainloaded q1n1 generation, never on the cold-booted
one:

```sh
python3 tools/cycle.py retire --idle --prefix <label>
```

This chainloads the other stage slot and confirms that the next generation is
idle. Use the generation number it reports below.

### 3. Prepare and check

```sh
python3 tools/cycle.py installed-fresh --prepare --variant fast --prefix <run-prefix> \
    --device udp://<interface> [run flags...]
python3 tools/cycle.py installed-fresh --check --variant fast --prefix <run-prefix> \
    --device udp://<interface> [run flags...]
```

`--prepare` builds the monitor with the defines the run flags select, and
assembles the guest image, the AuxKC and the q1n1 GPU steps. Every input is
pinned by SHA-256: the AuxKC and plug-in builds the flags refer to, and the
Apple images. A missing or different input is refused. `--check` repeats all
validation offline and prints the **qualification hash**. Give the same run
flags to every mode of a run.

`--variant` picks the monitor: `fast` (lock-free fast paths) or `control`
(the reference monitor).

### 4. Boot

```sh
python3 tools/cycle.py installed-fresh --boot --variant fast --prefix <run-prefix> \
    --device udp://<interface> --generation <N> --qualification-sha256 <hash> [run flags...]
```

The boot runs these steps:

1. a read-only GPU status check;
2. q1n1's GPU power-up steps (and the DDR and LLCC votes, if requested);
3. the monitor's adoption of the GPU;
4. the start of the guest.

It then follows the guest until it stops, and captures the stopped state.
Only one hardware client may run at a time. Never interrupt a running boot,
and never start a second client against it.

While the guest runs:

```sh
# a live console (with --console); --stream only watches, --run sends one command
python3 tools/guest-console.py --device udp://<interface> \
    --launch-record build/<run-prefix>-early-stop-launch.json
# ask the guest to stop early, for example once the desktop is up
python3 tools/request-stop.py --device udp://<interface> --reason "<text>"
```

The guest also stops on its own: on a kernel panic, when the thermal guard
trips (`--thermal-guard`), at the early-stop delay after the first present
(`--early-stop-delay`), or at the watchdog.

The watchdog is 1800 s unless `--watchdog SECONDS` sets it, up to 7200. Above
1800 s the boot preflight requires the A16 on AC. For a desktop session to use,
`--console --session --watchdog 7200` drops the kext's first-present stop, so
the run lasts until the watchdog (exit 9), the thermal guard, or
`tools/request-stop.py`.

### 5. Preserve, quiesce, retire

```sh
python3 tools/cycle.py preserve --prefix <run-prefix>              # prints the preservation hash
python3 tools/cycle.py installed-fresh --quiesce --prefix <run-prefix> --device udp://<interface> [run flags...]
python3 tools/cycle.py retire --prefix <run-prefix> --preservation-sha256 <hash>
python3 tools/inspect-logs.py --prefix <run-prefix>                # decode the run's logs
```

- Preserve the evidence before anything else touches the machine.
- Quiesce the GPU gracefully after every run that used it. Never initialise
  a GPU that is still running.
- `retire` chainloads the next generation, ready for the next run.
- To start a new run from scratch, cold boot instead.

## Selecting the target

`tools/cycle.py` accepts a global `--target {a16,sp11}` **before** the subcommand
(`python3 tools/cycle.py --target sp11 status --check`). The default is `a16`, which leaves every existing command
line, output and refusal unchanged, so a script that omits `--target` always runs the A16 flow. Spell `--target` in
full: the parser does not accept abbreviations (`--tar` is an argparse error), because the single-hardware-client scan
only recognises the full spelling.

`--target sp11` is UNIMPLEMENTED and fails closed. Only `next` and the offline `--check` and `--check-inputs` modes
run, and they still read the A16 tool inputs, so a pass says nothing about the Surface Pro 11. Every other mode is
refused. It must first satisfy, in this order: a complete `sp11` section in the local `tools/cycle_lib/pins.json`
(copied from `pins.example.json`; the example itself is never used), the standing authorizations for the mode's
hardware effects, `--thermal-guard`, a current operator power attestation, the per-run authorization described below,
the thermal admission of `tools/hamoa_thermal.py`, and the single-hardware-client check. It then ends with
`no SP11 <flow> flow implemented`. DVFS, DDR, LLCC and bandwidth-probe options are refused for `sp11` in every mode.

The authorizations of [SURFACE-PRO-11.md section 8](SURFACE-PRO-11.md#8-hardware-safety-and-authorization) are
checked in two layers. The standing ones are booleans under `sp11.authorizations`; `disable_secure_boot` and
`suspend_bitlocker` are permissions that the target-I/O and code-load modes require, not instructions (way A of
[SURFACE-PRO-11.md section 8.1](SURFACE-PRO-11.md#81-secure-boot-and-bitlocker) never suspends BitLocker). The two that touch NVMe,
`enable_nvme_window_writes` and `cycle_mode_may_write_nvme`, are per run: the three modes that run a guest with writable
storage (`launch --boot`, `launch --boot-child` and `installed-fresh --boot`) need the standing booleans and also
`sp11.run_authorization`, which holds the two per-run booleans again plus `recorded_utc`, `run_prefix` and the
`source_sha256` that the refusal prints. It expires after 30 minutes like the power attestation and is bound to this run
prefix and to a digest of the tool sources plus `tools/hamoa_thermal.py`, so a standing boolean alone never enables a
write. The refusals are `no-run-authorization`, `run-authorization-invalid`, `run-authorization-expired`,
`run-authorization-prefix`, `run-authorization-source` and `missing-authorization`. `pins.example.json` carries an
all-null `sp11.run_authorization` template next to `sp11.power_attestation`. A thermal module that lacks the
`power_attestation_ok` cross-check is refused (`thermal-missing`), and so is an admission that returns anything other
than None or True.

The CP_MEMCPY bound is not a `cycle.py` option: no cycle flow chooses a copy size. A single `CP_MEMCPY` over 1 MiB is
refused in the monitor (`ADRENO_COHERENT_COPY_MAX` in `compat/adreno-coherent.h`) and in the kext and plug-in, which
enforce and test the same bound. `check_cp_memcpy` in `tools/cycle_lib/targets.py` is the predicate that any future flow
that picks a size must call first, and `tools/test-cycle-targets-hardening.py` ties its cap to that constant.

The power attestation is an operator record in `pins.json` (`sp11.power_attestation`: AC state, battery percentage,
UTC time, run prefix and the tool-source digest). It is never accepted from the command line, and any change to the
tool sources or to `tools/hamoa_thermal.py` invalidates it. It expires after 30 minutes and may not be dated in the future: `tools/hamoa_thermal.py`
cross-checks it and allows no future skew, which is stricter than the 60 s that `tools/cycle_lib/targets.py` alone
tolerates, so the stricter bound applies. It must state AC power or a battery level of at least 50%. The thermal
ceiling is a value the user chooses; there is no default, and `compat/hamoa-thermal.h` only proposes 80 C as a
placeholder. Until X2 measures the platform fingerprint, `tools/hamoa_thermal.py` refuses every target
(`thermal-refused`), which is the expected end state today. The policy lives in `tools/cycle_lib/targets.py`.

## Run flags

Every monitor, kext and q1n1 feature is opt-in, with one flag each. To list
them all, with the requirements between flags:

```sh
python3 tools/cycle.py installed-fresh --help
```

The flags fall into these groups:

| Group | Examples |
| --- | --- |
| Console and early stop | `--console`, `--single-user`, `--early-stop-delay`, `--let-panic`, `--panic-serve` |
| Input | `--input`, `--input-touchpad`, `--input-ptp`, `--input-active-us`, `--input-fnkeys` |
| Present path | `--present-dartpool`, `--present-resident`, `--present-beam`, `--present-va-reclaim`, `--present-maint`, `--present-latch`, `--present-pipeline`, `--present-early-done` |
| GPU | `--ib-async`, `--gpu-recover`, `--gpu-va-wide`, `--gpu-coherent`, `--resident-mib`, `--adreno-fill-br`, `--adreno-fence-br`, `--adreno-cs-samplers`, `--gpu-x290-survey` |
| Panel brightness | `--edp-backlight`, `--edp-backlight-mode` |
| Clocks and bandwidth | `--thermal-guard`, `--gpu-dvfs`, `--gpu-dvfs-ceiling`, `--cpu-dvfs`, `--ddr-vote`, `--llcc`, `--bw-probe` |
| Storage | `--nvme-async`, `--nvme-converted` |
| Wi-Fi | `--qcc2072-wifi`, `--qcc2072-auxkc`, `--qcc2072-auxkc-sha256` |
| Kernel view pool | `--view-reclaim` |
| Monitor performance | `--fine-locks`, `--root-cache-wide`, `--user-hw-walk`, `--fast-timer-irq`, `--fast-ipi`, `--lean-traps`, `--sptm-fine`, and others |
| Kernel patches | `--kernel-sysreg-zero`, `--kernel-user-4k`, `--kernel-refmod-skip` ([KERNEL-PATCHES.md](KERNEL-PATCHES.md)) |

The desktop configuration that currently performs best combines all of these
groups:

```
--variant fast --wfi-resume --fast-timer-irq --dart-memo --aes-bitslice --fast-ipi
--fast-el0-sysreg --service-subclass --dart-wiredcnt --panic-serve --root-miss-fast
--update-disjoint-fast --root-cache-wide --update-check-sparse --input --kernel-pperm-fast
--ib-budget-1s --unmap-mixed --prime-secondaries --txm-aspace-wide --fine-locks
--fine-switch --fair-token --user-hw-walk --fine-hw --s2-fault-retry --fast-pauth-calls
--fast-timer-irq-cpu0 --native-pacga --pauth-el0-live --class-proof-wide --el0-memo
--unnest-4k-fast --phase-fair-gate --fine-switch-hw --dart-converted --nest-fast
--detach-fast --root-index --other-profile --gate-wait --lean-traps 1 --input-touchpad
--input-ptp --present-dartpool --console --kernel-sysreg-zero --kernel-refmod-skip
--kernel-user-4k --let-panic --early-stop-delay 1500 --hide-amx --audit-sparse
--reuse-match-mapped --present-resident --el0-wfe --el0-cache-maint --user-crash-wide
--ib-async --thermal-guard --cpu-dvfs --gpu-dvfs --ddr-vote --bw-probe --spmi-scratch
--present-beam --llcc --present-va-reclaim --gpu-va-wide --sptm-fine --present-maint
--nvme-async --nvme-converted --attr-clean-defer --present-latch --adreno-fill-br
--adreno-fence-br --gpu-x290-survey --gpu-dvfs-ceiling 1185
--present-pipeline 1 --gpu-recover soft
```

The present-path flags select specific AdrenoX2 builds. Each needs the flags
before it in its chain, and `--prepare` reports any missing prerequisite.

Compared with that list, the b12v20 desktop run adds `--multipage-fast
--resident-mib 1536` and uses `--gpu-dvfs-ceiling 1700`; b12v23 also adds
`--gpu-coherent` and drops `--other-profile`. `--resident-mib` sets the residency budget of a re-pin
AdrenoX2 (boot-arg `adreno_resident_mib`). `--gpu-coherent` needs `--gpu-va-wide`
and `--root-cache-wide`: the monitor runs a bounded CPU/GPU coherency self-test at
GPU prepare (event `EV_GPU_COHERENT` in the report) and only then maps CB0's
mirror IO-coherent; the desktop-profile AdrenoX2 skips cache maintenance only
after that test passed and with the boot-arg `adreno_coherent=1` the flag adds.
The flag also runs q1n1's `a16gpu adopt --coherent`, which needs a q1n1 checkout
that has that option. `--gpu-coherent-force` is a debug build without the
self-test, for A/B rendering checks only.

`--edp-backlight` (needs `--present-latch` and its chain) lets macOS set the
panel's brightness. The monitor gets `SNAP_EDP_BACKLIGHT`: its
`ADRENO_GUEST_BACKLIGHT` call writes the panel's DPCD backlight level over the
eDP controller's AUX channel, touching only the AUX transport registers and
DPCD `0x721`-`0x723`, clamping the level to between 2% of full scale and the
panel's maximum, and stopping for the boot on any failure with the brightness
left as it was (`compat/edp-backlight.h`, event `EV_EDP_BACKLIGHT`). The guest
gets the AuxKC pinned as `INPUT_AUXKC_DESKTOP_BACKLIGHT`, whose AdrenoX2 has the
`AdrenoBacklight` service, and the boot-arg `adreno_backlight=1`. The service
lets WindowServer bind the panel's framebuffer to a brightness control;
CoreBrightness's nits then arrive in the swaps WindowServer submits (as on a
DCP Mac), and AdrenoDisplay hands each new value to the service, which logs
`SET <level> from swap` (the path and its evidence are in the kext's
`src/AdrenoBacklight.h`). The service maps nits to a level proportionally, the
top level being `adreno_backlight_max_nits` (default 500) and the darkest
request `adreno_backlight_min_nits` (default 5, raised to the 2% floor's ~10
nits); either boot-arg moves the curve without rebuilding the monitor. The
collection is built with
`KEXT_PROFILE=desktop-next I2C_PROFILE=desktop-fnkeys tools/build-auxkc-desktop.sh
NAME` for a monitor without `--gpu-coherent` (`desktop-next` is
`desktop-nocoherent` plus the service). With `--gpu-coherent` or
`--present-early-done` the run takes the collection pinned as
`INPUT_AUXKC_DESKTOP_EARLY_DONE` instead (`KEXT_PROFILE=desktop-early-done
I2C_PROFILE=desktop-fnkeys tools/build-auxkc-desktop.sh NAME`; `desktop-early-done` is
`desktop-backlight`, the coherent `desktop` plus the service, with early
completion), and is refused until that collection is pinned. `--edp-backlight-mode` lets the first change switch a panel left in
PWM-pin mode to DPCD control. `tools/survey-edp-aux.py` checks, read-only from
q1n1, that the clocks and the AUX controller the call needs are on.
`tools/test-edp-backlight.py` is the offline test.

`--present-early-done MODE` (needs `--edp-backlight` and its chain) adds the
guest boot-arg `adreno_present_early_done=MODE` for the `desktop-early-done`
AdrenoX2 (`src/AdrenoDisplayEarlyDone.h` in the kext). AdrenoDisplay then
completes a layer-0 swap right after its scanout copy instead of at the next
frame tick. WindowServer keeps at most two swaps outstanding, so completing at
the copy frees one of its entries a refresh earlier and a frame that runs past
one refresh can still make the next latch; WindowServer may then run up to one
frame further ahead. `1` completes at every copy, `2` only while WindowServer
is late (for `--present-early-hold-ms`, default 2000, after each late frame),
`0` only measures. Every mode adds one `AdrenoDisplay: perf done` line per perf
window: completions at the copy, late frames, engagements, and histograms of
WindowServer's arrivals after each tick and of each late frame's lateness. It
excludes `--present-hold on`. `tools/test-present-early-done.py` is the offline
test (with the presentation replay's early-done scenarios).

`--view-reclaim` gives the monitor `SNAP_VIEW_RECLAIM`. The kernel's per-CPU
private page-table copies (`shadow/sprr-view.h`) hold 256 tables instead of
128, and when a protecting RETYPE (read-only zone, TXM) finds them full, the
copies that no selector2 or selector4 leaf needs any more are retired: every
CPU's copies are synced back to the shared tables, published and invalidated
before their pages are reused. Without it, a desktop session stops with exit 7
(RETYPE to ROZONE refused) once the read-only zone has grown past about 120
frames (installed-fresh-desktop-20261024-next1: after 1,104 s). The report's
`VIEWRCL1` record (and `summary.json` `view_reclaim`) has the count, its
high-water mark and the reclaims. `tools/test-view-reclaim.py` is the offline
test.

`--input-fnkeys FLAGS` (needs `--input`) adds the guest boot-arg
`snap_i2c_fnkeys=FLAGS` for SnapI2CHID's keyboard Fn-key layer, which only a
SnapI2CHID built with `PROFILE=desktop-fnkeys` has (`I2C_PROFILE=desktop-fnkeys
tools/build-auxkc-desktop.sh NAME`); other builds ignore it. `0x1` turns the
keyboard's ASUS hotkey report into the usages macOS's keyboards send, `0x2` logs
what the F-row sends (rate-limited, never a key that types a character), `0x4`
(with `0x1`) swaps F1-F12 and their hotkeys in the host on Fn+Esc, and
`0x8`/`0x10` pick another brightness encoding
(`drivers/snap-i2c-hid/src/hid-keyboard.h`). The sets the kext would refuse are
refused at prepare. `tools/test-input-fnkeys.py` is the offline test.

## Installing a new Metal plug-in

The plug-in lives on the guest's system. To replace it, boot with `--console
--single-user`, then install the new bundle from the guest's own console shell
before the boot continues to multi-user:

```sh
python3 tools/deploy-guest-plugin.py --device udp://<interface> \
    --launch-record build/<run-prefix>-early-stop-launch.json --record <record.json> --continue-boot
```

The tool checks every file against its expected base before writing anything,
keeps backups, and can put them back with `--restore`.

A plug-in that changes how icons render has no visible effect until the icons
already rendered are dropped: IconServices keeps them in
`/Library/Caches/com.apple.iconservices.store` and in each user's cache folder
(`com.apple.iconservices`, `com.apple.iconservicesagent`), and the Dock keeps its
own `com.apple.dock.iconcache` there. Rename them aside from the single-user
shell, after the install and before the boot continues: run the tool without
`--continue-boot`, rename the caches with `tools/guest-console.py --run` under the
same `--owner`, then run the tool again with `--continue-boot` (it finds the files
installed and only continues the boot).

The kext side of the icons inside System Settings panes, `ADRENO_IB_CS_SAMPLERS`
in the desktop AdrenoX2 profiles, is off unless the guest boots with
`adreno_cs_samplers=1`, which `tools/cycle.py installed-fresh --adreno-cs-samplers`
adds (`tools/test-adreno-cs-samplers.py`). An AdrenoX2 built without the define
ignores the boot-arg.

## Checking icon pixels on the GPU

The encoding tests check the plug-in's packets, not what the Adreno GPU makes of
them. `icon-pixels` (SnapMetal `harness/icon-pixels*.m`, built and ad-hoc signed
by `tools/build-icon-pixels.py`) runs the CoreImage kernels of the Settings
icons in the guest on generated inputs (`synth`) and renders a few system icons
(`icons`), printing CRC-sealed text lines that `tools/compare-icon-pixels.py`
compares with this Mac's, naming what is off: unwritten or partial output,
readback or repeat differences, swapped channels, sRGB decode, a missing input
of a multi-input kernel. During a `--console` boot, once the multi-user shell
answers, as the run's only console client:

```sh
python3 tools/run-icon-pixels.py --device udp://<interface> \
    --launch-record build/<run-prefix>-early-stop-launch.json --owner <hex> \
    --tool <build-dir>/icon-pixels --output <new-dir> \
    --reference-synth tools/icon-pixels-reference-synth.txt --reference-icons <local icons reference>
```

It pushes the tool in checked base64 chunks to `/private/var/tmp/icon-pixels`,
runs it as root and, for the NSWorkspace icons, as the console user in that
user's bootstrap context, fetches the output three lines per command, collects
the plug-in's diagnostics, and compares. `--steps` picks steps; `recache` with
`--move-icon-caches TAG` renames the icon caches aside in multi-user and
restarts iconservicesd, iconservicesagent, the Dock and System Settings. The
synthetic reference is committed; the icons reference summarises Apple's icons
and stays local (`icon-pixels icons --host-caps <plug-in bundle>` on this Mac).
`tools/test-icon-pixels.py` is the offline test (fault injection and the whole
console procedure over the stand-in).

## Exit codes

A boot reports why the guest stopped:

| Code | Meaning |
| --- | --- |
| 1 | stopped for a reason the classifier does not model |
| 2 | loader failure: the image could not be built or placed |
| 3 | guest preparation failed; the kernel never ran |
| 4 | the monitor itself faulted at EL2 |
| 5 | prepared, but the guest never started |
| 6 | the kernel took an exception it did not survive |
| 7 | a monitor operation failed or reached an unsupported contract |
| 8 | the kernel panicked |
| 9 | the guest was still running when the watchdog expired |
| 10 | an SError was recorded |
| 11 | a virtual device requested a guest reset |
| 12 | stopped early: on request, at a guest milestone, on the thermal guard, or on a host abort |

A desktop session normally ends with 12. A host-side failure that leaves no
stop report is not a guest exit code. Wait out the watchdog, then recover the
real stop state with `cycle.py capture`.

On an SP11 build (UNIMPLEMENTED), exit code 3 with a prepare status from -90 to
-95 means the launch gate refused the CPU before `VNCR_EL2` or HCR_EL2.NV were
written: -90 no valid functional record is embedded; -91 the record is for another
SoC or core; -92 the ID registers do not advertise NV2; -93 the record has no
passing VNCR redirect and negative control, or its evidence label is not VERIFIED
or OBSERVED; -94 MIDR_EL1 is not a recorded Oryon or A16 core; -95 the PAuth ID
fields are not a recognised shape. The kernel did not run. Measure the core, run
`tools/nv2_record.py ingest`, and rebuild; do not edit the gate to get past it.
`tools/nv2_record.py show <measurement.log> --check` asks the same gate offline
and reports through its own exit status (see
[BUILDING.md](BUILDING.md#the-sp11-monitor-variant-experimental)). A prepare status
of -146 means the build has more guest vCPUs than one cluster holds and no fair
service lock (`-DSNAP_FAIR_TOKEN=1`); nothing was prepared. It is checked before
the NV2 gate, so a default SP11 build reports -146 and not -90. A status from -96
to -145 concerns the SSD window: -145 means the monitor does not yet receive the
SP11 target, record and per-run pins and refuses to adopt the SSD at all (the
physical NVMe state's `prepare` word holds the same number); -96 to -144 are the
refusals of `nvmeh_sp11_adopt`, named in `PREPARE_FAILURES`. On an A16 window-mode
launch, -80 to -83 are the adoption's own refusals (parameters, guest overlap,
controller state, admin queue); -80 to -82 share their numbers with the boot
relocation, so read that `prepare` word. A status from -257 to -274 is a stage-2
geometry refusal (`-(256 + reason)`, for
example -272: a stage-2 range crosses the IPA limit, so the DRAM map does not fit
the 36-bit stage 2); the names are in `PREPARE_FAILURES` in `probes/run.py`, which
also holds the table of status bands.

## Other modes

- `cycle.py speed-test` compares the `control` and `fast` monitors on the
  stock kernel with a RAM-disk BaseSystem, six CPUs and a trap census.
- `cycle.py bootstrap-smoke` is a five-second, RAM-only, single-CPU
  integration check.
- `cycle.py storage` reads persistence from, or snapshots, the SSD at an idle
  generation.

## Preparing the X0/X1 boot stick

This is the operator procedure for gates X0 and X1 of the Surface Pro 11 plan
([SURFACE-PRO-11.md](SURFACE-PRO-11.md)). Everything about the Surface Pro 11 is
UNIMPLEMENTED or EXPERIMENTAL and has not run on hardware. It is not a
`cycle.py` flow, and **no script in this repository erases or writes a disk**:
steps 3 to 6 are done by the operator at the keyboard, on a stick the operator
has identified. Disabling Secure Boot, suspending BitLocker and using
`tcblaunch.exe` need the authorizations of
[SURFACE-PRO-11.md section 8](SURFACE-PRO-11.md#8-hardware-safety-and-authorization).

**Before you start** (owner decisions of 2026-11-10, recorded as N7 in
[SURFACE-PRO-11.md](SURFACE-PRO-11.md#11-items-from-the-unit)): store the
BitLocker recovery key off the device. The full SSD backup is waived for X1 to
X3, which boot from the stick and write nothing to the internal disk, and is
required again before X6. Windows recovery media are not needed for X1 and X2;
they are needed before X3 and before X6, and they reinstall or repair Windows
rather than restore data.

**What you need.** A USB stick of 1 GB or more (the staged tree is a few MB,
and step 4 erases everything on the stick), with a USB-C plug or an adapter,
formatted FAT32 with an MBR scheme (step 4 does it). Also a Type Cover or a USB
keyboard: the stick has no `startup.nsh` unless you opt in to
[autorun](#optional-run-planned-experiments-from-the-stick-autorun) below
(`tools/stage-sp11-boot-media.py verify` fails a tree whose `startup.nsh` is not
the one an autorun plan generates), so by default you type every command at the
firmware shell yourself. That the firmware passes either keyboard to the shell
is INFERRED. The staged tree holds:

| Path on the stick | What it is |
| --- | --- |
| `EFI/BOOT/BOOTAA64.EFI` | The pinned aarch64 UEFI Shell (1.1 MB), the path the firmware boots from removable media |
| `SP11/SLBOUNCE.EFI`, `SP11/SLTEST.EFI` | slbounce and its self-test |
| `SP11/PROBE.EFI` | The EL2 probe, present only if it was supplied when staging |
| `SP11/TCBLAUNCH.EXE` and `TCBLAUNCH.EXE` at the volume root | Your own `tcblaunch.exe`, and the automatic copy that slbounce's driver needs at the root; keep both |
| `SP11/MANIFEST.SHA256`, `SP11/README.TXT` | The checksum list of every file above and the commands to type |

1. Build the inputs on a Mac host: `sh tools/build-slbounce.sh` in q1n1 (it
   builds the pinned slbounce commit with `SLBOUNCE_ALWAYS_SWITCH=1 DEBUG=1`), the
   pinned UEFI shell asset named in `tools/sp11-boot-media-pins.json`, and
   `python3 tools/build-uefi-probe.py`
   ([BUILDING.md](BUILDING.md#boot-media-for-the-surface-pro-11-x0-and-x1)).
2. Stage them into a scratch directory with `tools/stage-sp11-boot-media.py
   stage ...`. It reports `STAGED_INCOMPLETE` with `missing=["tcblaunch"]` until
   you add `--tcblaunch <your-own-copy>`; that is expected. Then run `verify --dest
   <that directory>` and require `shell_pin: match`: the shell must equal the
   pinned release, and `--no-pins` (which reports `shell_pin: unchecked`) is not
   for media that will boot the unit. Obtain
   `tcblaunch.exe` only from Microsoft-origin media you have the right to use,
   never from a third-party redistribution (a Microsoft Q&A thread reports
   Windows Defender flagging such a copy as a trojan). The five builds taken
   from the unit's own component store are Microsoft-origin media of the kind
   you may use; X1 tries them one after another.
3. Identify the stick with the read-only `diskutil list` and confirm the exact
   disk identifier by size and volume name. An identifier that is not the stick
   erases another disk: check twice, and never use the internal disk.
4. Erase the stick as FAT32 yourself (destructive; no script here does it),
   replacing `N` with the identifier you confirmed:
   `diskutil eraseDisk FAT32 SP11BOOT MBR /dev/diskN`.
5. Copy the staged tree onto the mounted volume, then verify it from the
   volume's root: `cd /Volumes/SP11BOOT && shasum -a 256 -c SP11/MANIFEST.SHA256`.
   Every line must say OK; otherwise redo the copy.
6. Eject with `diskutil eject /dev/diskN`. Prepare Secure Boot and BitLocker as
   chosen at X0 ([SURFACE-PRO-11.md section 8.1](SURFACE-PRO-11.md#81-secure-boot-and-bitlocker);
   INFERRED from public documentation, none of it tried on this unit), with the
   recovery key to hand. In the firmware setup turn Secure Boot off (neither the
   shell nor slbounce is Microsoft-signed, and the slbounce README calls turning
   it off mandatory; the menu path is read off the unit, not assumed), and boot
   from the stick.
   * Way A, the default for bring-up: leave BitLocker alone. Do not boot
     Windows while Secure Boot is off, and turn it back on in the firmware
     setup before the next Windows boot. A Windows boot made by accident in
     between only asks for the recovery key.
   * Way B, Secure Boot permanently off: from Windows, in an elevated prompt,
     run `manage-bde -protectors -disable C:`, turn Secure Boot off, boot
     Windows once so that it re-seals, then run
     `manage-bde -protectors -enable C:`. A firmware update, or a boot order
     that tries another entry first, can prompt again, and a managed-device
     policy may require Secure Boot or BitLocker.
   * Or keep the Windows SSD out of the run: the internal SSD physically
     removed (INFERRED; see
     [SURFACE-PRO-11.md section 1.5](SURFACE-PRO-11.md#15-how-the-units-disk-was-read))
     or a spare 2230 SSD. The X2 tests T06 and T07 read less without an SSD.
7. Follow `SP11\README.TXT` on the stick: run `SLTEST.EFI` first (a green line
   means the EL2 switch worked), and `SLBOUNCE.EFI` only after a confirmed-good
   self-test.

After every run, power the machine off and remove the stick to return to the
normal boot order. With way A, turn Secure Boot back on in the firmware setup
before Windows boots; with the SSD removed, do that before the SSD goes back
in.

### Optional: run planned experiments from the stick (autorun)

Off by default and opt-in per staged tree. Without it the stick runs nothing by
itself and you type every command. With it, the Shell's own `startup.nsh` runs
one pre-validated step per boot and nobody types. A step is `load` of one slbounce
build, once, then one application. Nothing can run after the application calls
ExitBootServices, so the script runs one step per boot and writes that step's
marker file (`SP11\DONE<k>.TXT`) before the step starts: a step that hangs is
never repeated.

Status. QEMU: OBSERVED with stand-in images in place of
slbounce and q1n1 (`python3 tools/test-sp11-autorun-qemu.py`, EXPERIMENTAL
evidence) under the pinned Shell binary (UEFI Interactive Shell v2.2,
edk2-stable202608) and QEMU's edk2-stable202408 firmware: the script is found in
`EFI\BOOT`, runs after the countdown with no key sent, runs one step per boot,
and ESC at the countdown skips it. The lookup order, the `homefilesystem`
variable and the ESC test agree with the `Shell.c` of the edk2-stable202608 tag
that Shell reports. **The unit: OBSERVED once (2026-11-14, one unit, the first step
of a two-step plan, an operator's photograph of the console).** Its Shell printed the
countdown, found `EFI\BOOT\startup.nsh` on the stick with no key pressed, printed the
plan banner and the two commands, waited the five seconds, and then ran
`load SP11\SLBOUNCE-DEVHE-MARK.EFI` and `SP11\TESTS-DECL.EFI` from `FS3:` (the Shell's
`homefilesystem`): the directory search, `homefilesystem`, `echo`, `stall`, the
`echo ... >a` marker write with its `if not exist` check, and the `load` and the run
all work on the unit's Shell, with nothing typed. Still untested there: that ESC reaches
the countdown (the Type Cover or a USB keyboard), a second boot of the same plan (the
markers), and `--clear` by hand. Whether the Shell file on the stick is the pinned one
depends on the tree having been staged with the default pins.

Stage it on the Mac, in the directory `stage` made, never on the mounted stick (the
tool refuses `/Volumes`). Give each file's SHA-256 from your own record of the
reviewed build, not one computed from the file about to be staged:

```sh
python3 tools/stage-sp11-boot-media.py autorun --dest <staged-directory> --name devhe-mark \
    --file SLBOUNCE-DEVHE-MARK.EFI <slbounce-build> <sha256> --file Q1N1.EFI <q1n1.efi> <sha256> \
    --step "SLBOUNCE-DEVHE-MARK.EFI Q1N1.EFI --el2"     # one --step per boot, in order
python3 tools/stage-sp11-boot-media.py verify --dest <staged-directory>   # COMPLETE, shell_pin: match
```

It copies the named files into `SP11\` (checking each hash after the copy; a name that
is already staged, such as the base tree's `SLBOUNCE.EFI` or `PROBE.EFI`, must have
the same bytes and is never replaced), writes
`EFI\BOOT\startup.nsh` and `SP11\PLAN.TXT` (the steps with their hashes), changes the
autorun paragraph of `SP11\README.TXT` and lists all of it in the manifest. It
refuses a base tree that is not `COMPLETE`, a missing or mismatching source, and
anything outside a short table: the slbounce builds `SLBOUNCE.EFI`,
`SLBOUNCE-DEVHE.EFI`, `SLBOUNCE-DEVHE-MARK.EFI` and `SLBOUNCE-DRYRUN.EFI`, and the
applications `Q1N1.EFI` (no argument, or exactly `--el2`), `Q1N1-STK.EFI` and
`Q1N1-PAYLOAD.EFI` (each exactly `--el2`, see below), `TESTS.EFI`, `TESTS-DECL.EFI` and
`PROBE.EFI` (no argument).
`--boot`, `--uart-proxy`, `--uart`, `--fault-test` and `SLBOUNCE-OLD-NOSWEEP.EFI` are
refused by name, and the script contains no command the generator does not write.
`verify` accepts the one `startup.nsh` that the plan in `PLAN.TXT` generates and still
fails any other. Copy the tree to the stick and check it from the volume's root as in
step 5.

`Q1N1-STK.EFI` is the second q1n1 image, the read-only USB survey that the q1n1 fork
builds from the same tree behind a compile-time gate (`docs/SP11-STK-SURVEY.md` in that
repository). It runs as `Q1N1-STK.EFI --el2` after the same `load`, so it gets its own
`--file` entry and a step reads `"SLBOUNCE-DEVHE-MARK.EFI Q1N1-STK.EFI --el2"`:

```sh
python3 tools/stage-sp11-boot-media.py autorun --dest <staged-directory> --name stk-survey \
    --file SLBOUNCE-DEVHE-MARK.EFI <slbounce-build> <sha256> --file Q1N1-STK.EFI <q1n1-stk.efi> <sha256> \
    --step "SLBOUNCE-DEVHE-MARK.EFI Q1N1-STK.EFI --el2"
```

It takes exactly `--el2`: the no-argument form, which is `Q1N1.EFI`'s preflight, is
refused for it, and so is every banned argument. A stand-in with that name runs under
the pinned Shell in QEMU like the others (the `survey` scenario of
`tools/test-sp11-autorun-qemu.py`, EXPERIMENTAL). A built survey image passed the
tool's image checks (arm64, EFI application) when staged and verified, but it has not
been booted: hardware NOT RUN.

`Q1N1-PAYLOAD.EFI` is the third q1n1 image, the stick payload runner (`docs/SP11-PAYLOAD.md`
in the q1n1 fork). It runs as `Q1N1-PAYLOAD.EFI --el2` after the same `load`, and at run time
it reads `\SP11\PAYLOAD.MAN` from the volume it started from, loads each file the manifest
lists, checks their sizes and SHA-256 and calls file 0 at EL2. The manifest and the files are
data that the application reads, not images the script runs, so the script never names them
and `--file` cannot stage them: `--data NAME PATH SHA256`, repeated once per file, does, and
only for a plan with a step that runs the runner. The q1n1 fork's
`python3 tools/sp11_payload_manifest.py --out-dir <directory under build/> <state.bin> [data ...]`
writes the manifest and the capitalised 8.3 copies, then:

```sh
python3 tools/stage-sp11-boot-media.py autorun --dest <staged-directory> --name payload-a \
    --file SLBOUNCE-DEVHE-MARK.EFI <slbounce-build> <sha256> --file Q1N1-PAYLOAD.EFI <q1n1-payload.efi> <sha256> \
    --data PAYLOAD.MAN <directory>/SP11/PAYLOAD.MAN <sha256> --data STATE.BIN <directory>/SP11/STATE.BIN <sha256> \
    --step "SLBOUNCE-DEVHE-MARK.EFI Q1N1-PAYLOAD.EFI --el2"
```

Checked when the tree is staged, because a boot is expensive; a plan that fails any of it is
refused before anything is written, and the tree is left as it was:

- the runner takes exactly `--el2`: no argument, every banned argument and anything else is refused;
- `--data` only with a runner step; `PAYLOAD.MAN` among the data files, and at most five others;
- each name is capitals and 8.3 (`[A-Z0-9_-]{1,8}.[A-Z0-9_-]{1,3}`) and no name of a step file, of the
  table, of the tool or of the base tree (`PLAN.TXT`, `README.TXT`, a `DONE<k>.TXT` marker, ...);
- each source is a plain file of 1 byte to 256 MiB that hashes to the SHA-256 given;
- the manifest, which the tool parses itself and does not take from the q1n1 repository: the
  `Q1N1PLD1` magic, version 1, entry size 296, 1 to 5 entries, the exact length, every path
  `\SP11\NAME` in canonical printable ASCII with an 8.3 name and never the manifest itself,
  no duplicate in any letter case, every size 1 byte to 256 MiB;
- the manifest's entries are exactly the staged data files other than itself: each names one,
  with the staged size and SHA-256, and none is left out.

`PLAN.TXT` records the data files (name, size, SHA-256) in a data section. `verify` re-hashes
them and parses the manifest again, so a changed file, or a manifest that no longer matches the
files, is a FAIL even when the stick tree's manifest was regenerated to agree. `--remove` deletes
the data files with the rest; `--clear` deletes only the markers. The tool stages what you hashed
and does not look inside the runner or the payload. The runner's name is not 8.3, so the stick
holds it as a long name entry, like `SLBOUNCE-DEVHE-MARK.EFI`; the data files are 8.3 by rule.

Status. Hardware: NOT RUN. QEMU: OBSERVED with a stand-in image named `Q1N1-PAYLOAD.EFI` and
manifest and payload files staged by this command (the `payload` and `payload-ls` scenarios of
`tools/test-sp11-autorun-qemu.py`, EXPERIMENTAL): under the pinned Shell the script loads the
slbounce stand-in and runs `SP11\Q1N1-PAYLOAD.EFI --el2` once per boot, and the Shell lists the data
files on the volume; the stand-in does not open them, so that the real runner reads them is
not shown here. A real runner image, and a manifest written by the q1n1 fork's tool, were staged and
verified on the host (`tools/test-sp11-autorun-payload-tree.py` with `Q1N1_DIR`), not booted.

A boot with an autorun stick:

1. The Shell prints "Press ESC in 5 seconds to skip startup.nsh or any other key
   to continue." (5 seconds under QEMU; the unit's start value was not recorded).
   **ESC** gives the normal `Shell>` prompt and runs nothing
   (`type SP11\PLAN.TXT` shows the plan). Any other key continues at once; with no
   key the script starts after the countdown.
2. The script selects the Shell's own volume (the `homefilesystem` variable, so the
   stick may be any `FSn:`), prints the plan, the step and the exact two commands,
   and checks that both files exist; a missing one prints `AUTORUN REFUSED: ... is
   missing, nothing was run` and writes no marker.
3. It waits 5 seconds ("Power off now to cancel"; Ctrl-C in that wait also
   cancelled the step in QEMU, unverified on the unit), writes the marker, then runs
   `load` and the application with the Shell echoing both commands.
4. If the unit hangs, hold the power button and boot the stick again: the next
   step runs. After the last step the script prints `ALL STEPS DONE, run --clear to
   repeat` and runs nothing.

Starting again or switching it off. In a staged directory, `autorun --dest <dir>
--clear` deletes the `SP11\DONE<k>.TXT` markers and `--remove` deletes
`startup.nsh`, `PLAN.TXT`, the markers and the files the plan added, and
restores `README.TXT` and the manifest, so the tree is byte for byte what it
was. A directory that holds markers (for example a copy of a used stick) fails
`verify` until they are cleared. The tool never writes the mounted stick, and
the Shell wrote the markers there, so on the stick you do it by hand (copying a
staged tree over a used stick does not delete its markers):

```sh
rm -f /Volumes/SP11BOOT/SP11/DONE*.TXT          # start the plan again at step 1
rm -f /Volumes/SP11BOOT/EFI/BOOT/startup.nsh    # autorun off: the stick is in its manual state
```

Limits. The script cannot tell that a `load` failed: `%lasterror%` stays `0x0` after a
failed `load` under this Shell (OBSERVED in QEMU), so the application still runs; the
Shell prints the error on the screen. `SLTEST.EFI` is not in the table (it needs a
path argument and the procedure above runs it without a `load`). On a stick that
cannot be written the marker cannot be written first, and the Shell stops the script at
that redirect (`Unable to redirect file.`, `Script Error Status: Invalid Parameter`)
before anything runs (OBSERVED in QEMU with a write-protected stick).

## Running the EFI probe and the X2 tests

Operator only, after step 7 above; hardware status NOT RUN. Everything these
images show on the unit is INFERRED until the first run. The order is: the
passive probe, then your choice of the X2 window policy, then the X2 image.

**1. The passive probe.** The probe image (`sp11-el2-probe.efi`, passive) runs
from the stick after slbounce has switched the machine to EL2. It dumps the
firmware tables to `\SP11DUMP\RUNnn\` on that stick, calls ExitBootServices
itself and then shows the EL2 state as photographable pages; photograph every
page, header and footer included, type the rows into a file and check them with
`python3 probes/uefi/verify-lines.py`. The operator procedure is in
[probes/uefi/README.md](../probes/uefi/README.md). Start the probe from the UEFI
Shell the stick boots, by typing `SP11\PROBE.EFI` after `load SP11\SLBOUNCE.EFI`
(a stick staged without autorun has no `startup.nsh`). The Shell replaces the
console output handle for the programs it starts, so the probe chooses its
framebuffer by a second
rule there: the one framebuffer that every usable GOP describes; two different
framebuffers are refused as `AMBIGUOUS GOP` (see "Framebuffer selection" in the
probe README). The `GOP <w>X<h> SEL=...` row in the `== PRE-EBS FACTS ==`
section of the pages says which rule fired, and `SEL=SAME` is the expected value
when started from the Shell. That the rule works is OBSERVED in a QEMU model of
the stick with the pinned Shell; what the unit's own console stack publishes is
UNKNOWN until X1. The probe installs no vector,
so it reads only ID registers that every Armv8.0 EL2 may read (a few more only
when a feature field says they exist):
`ID_AA64MMFR3_EL1`, `ID_AA64MMFR4_EL1`, `ID_AA64ISAR2_EL1` and `ID_AA64DFR1_EL1`
are not read, and its ID page says `NOT READ HERE: MMFR3 MMFR4 ISAR2 DFR1` (the
guarded X2 image reads the first three; the fourth is read nowhere). The opt-in
PSCI image needs the owner's authorization and is run only after the passive
image has been read; it issues SMC only, so a FADT that declares an HVC conduit
gives `PSCI RUN=HVC REFUSED` and no call at all.
The dump holds SMBIOS data that can carry serial numbers and UUIDs: keep it out
of version control and out of reports (`.gitignore` covers `SP11DUMP/`).

Record, besides the pages:

| Where | What to record | What it means |
| --- | --- | --- |
| The console line `GOP`, if the console is visible | `STATE=NONE WHY=<reason>`, then `EBS STATE=SKIPPED` | The probe refused the framebuffer (`NO GOP`, `BAD MODE`, `NOT IN MEMORY MAP` or `AMBIGUOUS GOP`) and ended the run without calling ExitBootServices; the dump is kept and `summary.txt` says `gop=none`. The screen part of the run is lost, so record the reason. `AMBIGUOUS GOP` means no GOP sits on the console output handle (as under the Shell) and the usable GOPs describe different framebuffers; `CANDIDATES=<n>` on the line counts the GOPs examined |
| The `GOP <w>X<h> SEL=...` row in the `== PRE-EBS FACTS ==` section of the pages, and the `GOP` console line's `SEL=` | `SEL=CONSOLE` or `SEL=SAME` | Which rule chose the framebuffer: `CONSOLE` is the GOP of the console output handle, `SAME` is the framebuffer all usable GOPs agree on (expected when started from the Shell). `summary.txt` says `gop_select=` with the same word |
| The page rows `DUMP STATE=` and `DUMP RUN=nn FILES= BYTES=` | Anything but `OK` | `OK` only if every file, then the run directory, `SP11DUMP` and the volume root, were flushed and closed; a failure shows `PARTIAL` or `ERROR`, never `OK` |
| The PSCI page, `PSCI RUN=` | `HVC REFUSED` | The FADT declares an HVC conduit and the image made no call. `psci_conduit` in `summary.txt` names the conduit in every case |
| `memmap.txt` and `summary.txt` in the dump | The whole files | Which windows the UEFI memory map lists as MMIO, the framebuffer's descriptor and the conduit; `memmap.txt` is the input of the window-policy choice below |

**2. Choose the device-window policy of the X2 image (an operator decision).**
The X2 image refuses a device window whose base is zero, misaligned or wrapping,
and any window the UEFI memory map calls RAM, firmware, ACPI or anything but
MMIO; a window the map does not mention at all is read by default. Decision: keep
that lenient default (the map of the QEMU machine lists none of its windows, so a
strict default would leave the model run unable to check the device tests). The
operator reads `memmap.txt` from the passive probe and then chooses. If the map
lists the GIC, ECAM and SMMU windows as MMIO, you may build the strict image with
`python3 tools/build-uefi-tests.py --soc x1e80100 --require-mmio-map`; if it does
not, strict makes T05 to T07 read nothing, so run the default build. Say which
one you ran: the pages show `MAP MMIO=n UNLISTED=n REFUSED=n` (default) or
`MAP STRICT MMIO=n REFUSED=n`, and the build id differs.

**3. The X2 test image.** `sp11-el2-tests.efi` is run once, with the same stick
preparation; read [SP11-X2-TESTS.md](SP11-X2-TESTS.md) first, and never run the
`model`, `model-fatal`, `model-fatal-pre` or `model-declared` builds on the unit. It writes the NV2
record next to the probe's dump; `python3 tools/nv2_record.py ingest` turns it
into the record the SP11 monitor embeds. One hardware client at a time, and AC
power. Say whether the internal SSD was installed: T06 and T07 are meant to read
the NVMe and PCIe SMMU state with one present, and the NVMe half of T06 reads
nothing without it. Photograph every page, and record these rows besides the verdicts:

| Where | What to record | What it means |
| --- | --- | --- |
| T05, T06 and T07, `MAP MMIO=n UNLISTED=n REFUSED=n` | The three numbers | How many windows were read, how many of those the map did not list, and how many were refused. A refused window is UNKNOWN (`<what> REFUSED: <reason>`), never PASS or FAIL |
| T05, T06 and T07, `FAULTS=n` | Any non-zero value | Guarded reads that faulted. An asynchronous SError would show up here; none has ever been seen |
| T02, `MECHANICS FAULT` | The whole T02 section | A write to `HCR_EL2`, `VNCR_EL2` or an EL1 register did not stick. The run is INCONCLUSIVE, never a K1 verdict |
| A framebuffer page `FATAL: CLEANUP FAILED` | The whole page (its second row names the step, its fourth says whether one more attempt restored the firmware state) | A register could not be put back. The image halted (decision: halt, not reset) with nothing written to the stick and no ExitBootServices; power cycle and discard the run |
| A framebuffer page `FATAL: <reason>` with a vector, `ESR`, `FAR` and `ELR` | The whole page | An exception that nothing expected; handle it the same way |

Fatal pages are framebuffer-only, before ExitBootServices as well as after: the
serial `SP11X2 FATAL` line exists only when no page can be drawn, so a serial log
of a run that drew its page can say nothing about the stop.
