# Building

All builds run on a Mac. Nothing here touches the A16; booting is covered in
[RUNNING.md](RUNNING.md). Run every command from the repository root unless
stated otherwise.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| macOS 27 with Xcode | The macOS 27 SDK and `xcrun clang`, for the arm64e kexts and the plug-in |
| Kernel Debug Kit for 26A428 | Installed at `/Library/Developer/KDKs/KDK_27.0_26A428.kdk`. `kmutil` links AuxKCs against it, and `tools/kc-symbolicate.py` uses its symbols |
| Python 3 | For every host tool. The Surface Pro 11 board-descriptor, boot-contract and ACPI-check suites need 3.10 or newer |
| Homebrew `llvm`, `meson`, `ninja` | For the air2ir3 shader compiler and its Mesa build. The scripts expect LLVM at `/opt/homebrew/opt/llvm` |
| Optional: Homebrew `lld` (`lld-link`) and QEMU with the EDK2 AArch64 images | The Surface Pro 11 EFI probe and X2 test image build with `clang` and `lld-link` and nothing else; QEMU is needed only for the emulator half of their tests, which print SKIP without it. See [the EFI probe subsection](#efi-state-probe-for-the-surface-pro-11-x1) of section 7 |
| Submodules | Clone with `git clone --recurse-submodules`, or run `git submodule update --init` in an existing checkout. The kext, plug-in, shader compiler and two drivers live in their own repositories (see the README's Repositories section) |
| q1n1 | A checkout of [q1n1](https://github.com/Snapintosh/q1n1) next to this repository, at `../q1n1`. Several tools import its proxy client and helpers from there |

## 1. Apple inputs

The repository ships no Apple files. Fetch the macOS 27 (26A428) restore image
and extract the J700 boot set:

```sh
python3 tools/fetch-ipsw.py <ipsw-url> downloads/UniversalMac_27.0_26A428_Restore.ipsw
python3 tools/extract-firmware.py downloads/UniversalMac_27.0_26A428_Restore.ipsw j700ap
```

`fetch-ipsw.py` resumes interrupted downloads. `extract-firmware.py` reads
`BuildManifest.plist`, picks the `j700ap` identity, and unwraps its kernelcache,
device tree, TXM, iBoot and SEP images into `downloads/extracted/j700ap/`
(`--output` to change it). Every later step is pinned against these exact
images.

Install the 26A428 KDK from Apple's developer downloads.

The GPU and Wi-Fi firmware is supplied on the q1n1 side. See q1n1's
documentation and the "Bring your own Apple software" section of the
[README](../README.md).

### Generated inputs that are not in the repository

Some build inputs are derived from Apple software. They are not distributed,
so you generate them from your own copies.

**Apple class-layout headers.** The kexts subclass private Apple IOKit
classes. They need headers that reconstruct those class layouts for build
26A428:

| Header | Directory | How to produce it |
| --- | --- | --- |
| `IOMobileGraphicsFamily26A428.h` | `gpu/adreno-kext/kext/include/` | `python3 tools/gen-iomfb-header.py --kc downloads/extracted/j700ap/kernelcache.release.mac17p` (reads the KDK at its default path; `--kdk` changes it) |
| `IOGPUFamily26A428.h` | `gpu/adreno-kext/kext/include/` | reconstruct from your KDK; step-by-step recipe in [AGENTS.md](../AGENTS.md#recipe-a-header-for-a-private-apple-c-class-arm64e) |
| `IO80211Controller26A428.h`, `IO80211Interface26A428.h`, `IO80211WorkQueue26A428.h`, `IOSkywalkLifecycle26A428.h` | `drivers/snap-qcc2072/include/` | reconstruct from your KDK; step-by-step recipe in [AGENTS.md](../AGENTS.md#recipe-a-header-for-a-private-apple-c-class-arm64e), using the output of `drivers/snap-qcc2072/re/recover-controller-abi.py` |

`gpu/adreno-kext/kext/build.sh` and `drivers/snap-qcc2072/build.sh` stop with
an error that names any missing header.

**air2ir3 reference inputs.** These are needed only for the compiler's
on-device comparison tests:

- `gpu/air2ir3/tools/a16ref/probes.air` is built by
  `gpu/air2ir3/tools/a16ref/build-probes-air.sh`.
- The pinned shader sets (`tools/a16ref/render-shaders.pinned.json` and
  `tools/p7ref/p7-shaders*.json` under `gpu/air2ir3/`) are compiled from Apple's
  shaders. Generate them locally with q1n1's `tools/a16gpu-render/` and
  `gpu/air2ir3/tools/p7ref/make-p7-shaders.sh`.

**Machine pins.** `tools/cycle_lib/pins.json` holds per-machine values and is
not committed. Copy `tools/cycle_lib/pins.example.json` to
`tools/cycle_lib/pins.json`, and set `device` (`udp://enN`, the Mac's NCM
interface) and the q1n1 stage hashes for your build.

## 2. The shader compiler (air2ir3)

```sh
cd gpu/air2ir3
tools/mesa-setup.sh       # clone Mesa at the pinned revision, apply patches/, configure
tools/mesa-ninja.sh       # build only the Mesa libraries air2ir3 links
src/build.sh all          # air2ir3, libair2ir3.a and the test tools
```

`MESA_URL` can point `mesa-setup.sh` at a local mirror. Its tests and inputs
are in [gpu/air2ir3/README.md](../gpu/air2ir3/README.md).

The Surface Pro 11 chip-profile work is a patch for the air2ir3 repository,
pending that repository's commit and the `gpu/air2ir3` pointer bump; it is not in
this tree. Once applied, the compiler selects its GPU by a chip profile:
`build/air2ir3 --list-chips` lists `x2-90` (the default) and the opt-in,
EXPERIMENTAL `x1-85` (Surface Pro 11). Their checks do not need Metal:

```sh
cd gpu/air2ir3
python3 tests/test-chip-profiles.py   # needs build/air2ir3, build/api_test (src/build.sh) and llvm-as
```

## 3. The kexts

Each build script writes to `$OUT`, or to a default `out/` directory next to
it:

```sh
OUT=build/adrenox2 sh gpu/adreno-kext/kext/build.sh      # AdrenoX2.kext (with AdrenoDisplay)
OUT=build/snapi2chid sh drivers/snap-i2c-hid/build.sh    # SnapI2CHID.kext
OUT=build/qcc2072 sh drivers/snap-qcc2072/build.sh       # SnapintoshQCC2072.kext (refuses an existing OUT)
```

The kext builds use only the SDK's Kernel.framework. Undefined IOGPUFamily,
IOHIDFamily and kernel symbols are resolved when the AuxKC is linked. The
AdrenoX2 build accepts feature defines (for example `ADRENO_BEAM`,
`ADRENO_PIPELINE`, `ADRENO_PRESENT_SYNC`). The boot tool selects the matching
build for each run flag.

The deployed desktop kexts are build profiles: per-unit define sets kept in
each component's `profiles/` folder. Without `PROFILE` the builds are unchanged.

```sh
PROFILE=desktop sh gpu/adreno-kext/kext/build.sh              # AdrenoX2 with IO-coherent skipping (--gpu-coherent)
PROFILE=desktop-nocoherent sh gpu/adreno-kext/kext/build.sh   # the same without coherency
PROFILE=desktop sh drivers/snap-i2c-hid/build.sh              # SnapI2CHID with input-latency polling
```

A profile writes `DEFINES.txt` beside the kext. Compare a build with a deployed
kext using `python3 tools/macho-sections.py <deployed> <built>`: the sections
match, while the file bytes also carry object paths, object times and a UUID.

## 4. Linking an AuxKC

`kmutil` silently drops kexts that are not owned by `root:wheel`, and it still
exits 0. `build/kmutil-auxkc.sh` stages root-owned copies and then checks that
the output holds exactly the requested bundles:

```sh
sudo build/kmutil-auxkc.sh build/aux/aux.kc build/adrenox2/AdrenoX2.kext build/snapi2chid/SnapI2CHID.kext
python3 build/build-auxkc.py pin --kc build/aux/aux.kc --guest-profile <profile> \
    --bundle org.snapintosh.driver.AdrenoX2 --bundle <other-bundle-id>
```

`pin` admits the collection and writes `aux.kc.json`, which the loader
requires. `python3 build/build-auxkc.py link --out <dir> --kext <kext> ...` runs
stage, link and pin in one step. `tools/build-auxkc-user.py` links raw
collections the same way. It runs `kmutil` as you and uses ordinary `sudo`
(it prompts unless a sudo session is cached) only to stage and clean the
root-owned copies.

`tools/build-auxkc-desktop.sh NAME` builds the desktop collection from this
checkout: both kexts with their `desktop` profiles (`KEXT_PROFILE=desktop-nocoherent`
for AdrenoX2 without coherency), then `tools/build-auxkc-user.py`,
`tools/auxkc-autoload.py` and `build/build-auxkc.py pin`. It writes
`build/current-assets/NAME/`, prints the sha256 of every output and the two
`tools/cycle_lib/installed_fresh.py` lines that pin the collection, and with
`REFERENCE=<folder>` compares both kexts with a deployed collection's.

To link the Wi-Fi controller into an existing guest collection, while keeping
its graphics and input bundles:

```sh
python3 tools/build-qcc2072-native.py --output build/native-wifi \
    --baseline <base-collection> --expected-baseline-sha256 <sha256>
```

## 5. The Metal plug-in

```sh
OUT=build/plugin sh gpu/adreno-kext/plugin/build.sh
```

This builds `AdrenoMetal.bundle` (arm64e, like every IOGPU plug-in) and a small
host test harness (`regtest`). Optional environment variables:

- `ADRENO_LENIENT=1` drops a pass the encoder refuses instead of failing the
  command buffer. Use it for deployment; the tests use the strict default.
- `ADRENO_PIPELINE=1` builds for the desktop AdrenoX2's IB pipeline (IOSurface
  shadows wait for the GPU). The deployed plug-ins use it with `ADRENO_LENIENT=1`.
- `ADRENO_SETTINGS_ICONS=1` encodes the CoreImage kernels of the icons inside
  System Settings panes: sampled sRGB and grey+alpha textures, and multi-input
  kernels once AdrenoX2 publishes `AdrenoIBComputeSamplers` (its
  `ADRENO_IB_CS_SAMPLERS`, opted in with the guest boot-arg
  `adreno_cs_samplers=1`). `tools/test-adreno-settings-icons.py` and
  `tools/test-adreno-settings-ci-encoding.py` test it.

`ADRENO_PIPELINE` and `ADRENO_SETTINGS_ICONS` take 0 or 1; 0 or unset leaves the
build byte for byte as without them.
- `ADRENO_SHADER_CACHE_PACK=<file>` links a precompiled shader pack into the
  bundle.
- `ADRENO_COMPILER_RUNTIME=<dir>` bundles the air2ir3 runtime
  (`libAdrenoCompiler.dylib`).

`tools/build-adreno-arm64e-mesa.py` builds the patched Mesa compiler libraries
as arm64e, for the compiler runtime that runs inside the guest.

The harnesses in `gpu/adreno-kext/plugin/harness/` run the plug-in on the Mac
against an interposed Metal device, which is how most rendering work is tested
before it reaches the A16.

## 6. The monitor and guest image

The EL2 monitor (`compat/`, `shadow/`) and the loader (`probes/xnuboot.*`) are
built by the boot tool's prepare step. That step compiles the monitor with the
defines selected by the run flags and records the hashes of every input:

```sh
python3 tools/cycle.py installed-fresh --prepare --variant fast --prefix <run-prefix> [run flags...]
python3 tools/cycle.py installed-fresh --check   --variant fast --prefix <run-prefix> [run flags...]
```

`--check` repeats every validation without touching hardware, and prints the
qualification hash that `--boot` requires. See [RUNNING.md](RUNNING.md).

## 7. Host tests

The native suites link the real monitor sources against models. They need no
firmware and no hardware. Each C test carries its build line in its header,
for example:

```sh
mkdir -p build
cc -std=gnu11 -O1 -g -fsanitize=address,undefined -Ishadow \
   tools/test-shadow.c shadow/mmu.c shadow/shadow.c -o build/test-shadow && build/test-shadow
cc -std=gnu11 -O1 -g -fsanitize=address,undefined -Ishadow \
   tools/test-mmu.c shadow/mmu.c -o build/test-mmu && build/test-mmu

python3 tools/test-cycle.py       # the boot-cycle tool's checks and refusals
python3 tools/test-classify.py    # how a run's ending maps to an exit code
python3 tools/test-profile.py     # the boot profile and the physical layout
```

`test-shadow` walks the 16 KiB guest tables and the 4 KiB shadow at the same
address and compares the two. It does not check against hand-written
expectations.

`tools/qemu-sptm-oracle/setup.sh` builds jprx's qemu-sptm with an SPTM-call
tracer. `tools/qemu-sptm-oracle/run.sh <out> [seconds]` then boots Apple's real
SPTM/TXM with the same kernelcache and records how it behaves, as ground truth
for the monitor's implementation. The tracer patch is GPL-2.0-or-later.

There is no CI. Everything is built and run by hand.

### Surface Pro 11 host tests (experimental)

These tests are host-only and use synthetic inputs; none of them touches
hardware, and a pass says nothing about a Surface Pro. A test that lacks an input
prints SKIP with the reason, and a SKIP is not a pass.
[SURFACE-PRO-11.md section 9.3](SURFACE-PRO-11.md#93-host-tests-for-the-sp11-groundwork)
says what each one does not prove.

```sh
python3 tools/test-board-descriptor.py          # board descriptor schema, refusals, boot-readiness gate
python3 tools/test-board-info.py                # board-info blob: C validator (ASan/UBSan) against the Python packer; needs clang, plus an aarch64 target for the object check
python3 tools/test-hamoa-bootcontract.py        # Hamoa ACPI cross-check on synthetic tables
python3 tools/test-hamoa-bootcontract-bounds.py # the same, table-size and walk-bound edges
python3 tools/check-hamoa-acpi.py DUMP_DIR [--descriptor D] [--run RUNnn]   # read-only check of a dumped ACPI set
python3 tools/test-check-hamoa-acpi-hardening.py  # that checker: a table file is inspected before it is opened, and a link is refused
python3 tools/test-nv2-gate.py                  # SP11 NV2 launch gate and its record format
python3 tools/test-nv2-record-hardening.py      # nv2_record.py: show --check exit codes, how an input is read
python3 tools/test-oryon-idregs.py              # Oryon PAuth ID normalization
python3 tools/test-guest-topology.py            # vCPU placement and GIC redistributor discovery models
python3 tools/test-stage2-geometry.py           # stage-2 and IPA geometry for 16, 32 and 64 GiB DRAM
python3 tools/test-hamoa-thermal-guard.py       # Hamoa read-only thermal guard model; needs clang
python3 tools/test-cycle-targets.py             # --target policy: the SP11 refusal matrix
python3 tools/test-cycle-targets-wiring.py      # --target wiring: parser, dispatch, source digest, example pins
python3 tools/test-cycle-targets-hardening.py   # --target: thermal gate, source digest, per-run NVMe authorization, the CP_MEMCPY claim
python3 tools/test-snapmac-partition-plan-sp11.py  # the sp11 partition profile on synthetic 512e and 4Kn surveys
python3 tools/test-snapmac-partition-plan-hardening.py  # the planner: reader independence and MBR marking
python3 tools/test-gen-a7xx-cp-protect.py       # a7xx CP_PROTECT generator; the pinned-source group needs network or build/ref
python3 tools/test-sp11-boot-media.py           # boot-media staging; SP11_SHELL_EFI, SP11_SLBOUNCE_EFI and SP11_SLTEST_EFI enable the real-artifact test
python3 tools/test-sp11-boot-media-hardening.py # the stager: destination guard, pins file, validated bytes
python3 tools/test-uefi-probe.py                # EFI state probe: build, native checks, QEMU; UEFI_PROBE_SKIP_QEMU=1 skips the emulator
python3 tools/test-uefi-tests.py                # X2 test image: build, native checks, instruction audit, QEMU; UEFI_PROBE_SKIP_QEMU=1 skips the emulator
python3 tools/test-el2-entry-transition.py      # EL2 entry model, oracle, broken sequences, seeded bugs
python3 tools/test-timer-offset-model.py        # timer offset policy model
python3 tools/test-dc-zva-model.py              # DC ZVA decision and emulation model
python3 tools/test-nvme-window-hamoa.py         # NVMe window: GPT fixtures, host model, the SP11 contract in compat/nvme-host.c (ASan/UBSan; needs clang)
python3 tools/test-sp11-usb-ports.py            # USB controller table from the board descriptor: refusals, C and Python agreement, study citations
python3 tools/test-sp11-usb-ports-hardening.py  # that tool: the independent-source guard, the unmeasured core id
python3 tools/test-display-pipeline-check.py    # display pipeline decision: scenarios, properties, doc cross-check; needs clang (and an aarch64 target for the object check)
python3 tools/test-display-pipeline-check-mutants.py  # its seeded bugs
python3 tools/test-sp11-display-mode.py         # GOP/EDID mode validation and its seeded bugs
python3 tools/test-sp11-display-study.py        # the display study's citations
python3 tools/test-sam-frames.py                # Surface Aggregator frame codec: vectors, negative tests, fuzz, seeded bugs
python3 tools/test-monitor-hardening.py         # SP11 monitor: the SSD adoption refusal (-145), GUEST_CPU_COUNT rules, the vCPU launch refusal (-146); needs clang, llvm-nm and ../q1n1 for the object checks
python3 tools/test-sp11-integration.py          # the units together: status bands, the 1<<36 boundary, cites, dump names, thermal, wiring
```

Inputs that some of them read, all optional (the check that needs one is SKIP
without it):

- `build/refs/` holds the pinned public Linux files that the descriptor, NVMe
  and USB tests cite, by file name without directories. Each test's docstring
  names the files and the pinned commit; fetch them with `curl` from that
  commit. A partial set can make the descriptor test error rather than skip, so
  populate every file a test names, or none.
- `SP11_LINUX_TREE=<checkout>` adds the Linux citation checks of
  `tools/test-sp11-display-study.py`.
- `Q1N1_DIR`, `Q1N1_SP11_DIR` (a q1n1 checkout carrying the patch) and
  `build/refs/` make `tools/test-sp11-usb-ports.py` a full run.
- `SNAPMAC_PLAN_BASELINE=<pristine tools/snapmac-partition-plan.py>` adds a live
  comparison of the `sp11` and A16 profiles.
- `build/ref/` holds the four pinned Linux files of
  `tools/test-gen-a7xx-cp-protect.py`; the test fetches them itself when the
  network is available.

`tools/test-sam-frames.py` needs clang with AddressSanitizer and
UndefinedBehaviorSanitizer; it also uses `llvm-nm` and `aarch64-elf-gcc` for two
freestanding build checks and skips them, visibly, if they are absent. It takes
about 30 s, builds three copies of the codec at a time for its seeded-bug checks
(`--skip-mutants` omits them) and writes only under `build/sam-frames/`.

`tools/test-monitor-hardening.py` preprocesses and compiles small translation
units and the SP11 monitor object, so it needs clang, `llvm-nm` and the sibling
`../q1n1` checkout that the monitor build includes; the classes that need them
are SKIP without them.

The inspectors `tools/nv2_record.py`, `tools/sp11_display_mode.py` and
`tools/check-hamoa-acpi.py` look at an input file before they open it and refuse a
directory, a device, a FIFO or a symbolic link, so give them the real file.
`tools/nv2_record.py` also reads standard input (`-`) and bounds an input to
32 MiB.

### EFI state probe for the Surface Pro 11 (X1)

`probes/uefi` holds a freestanding UEFI application for the Surface Pro 11
port. It is not part of the monitor build and needs no Apple input: only
Homebrew `clang` and `lld-link`, plus QEMU with its EDK2 images for the
emulator tests. Nothing here touches hardware. See
[probes/uefi/README.md](../probes/uefi/README.md).

```sh
# Build build/uefi/sp11-el2-probe.efi and sp11-el2-probe-psci.efi.
python3 tools/build-uefi-probe.py
python3 tools/build-uefi-probe.py --variant all --verify-reproducible
# About a minute, QEMU included. Without QEMU the emulator tests print SKIP
# and count as not run.
python3 -B tools/test-uefi-probe.py
UEFI_PROBE_SKIP_QEMU=1 python3 -B tools/test-uefi-probe.py
```

The build exits with status 3 without the toolchain. The native logic tests
carry their build line in the header of `tools/test-uefi-probe-logic.c`; the
suite builds it twice, plain and under AddressSanitizer and
UndefinedBehaviorSanitizer. The shipped images are `sp11-el2-probe.efi` and the
opt-in `sp11-el2-probe-psci.efi`; the `fast*` variants under `build/uefi/test/`
shorten the timers for QEMU and are never run on hardware. The suite audits the
built images: no `msr`, `svc`, `eret` or `hvc`, and `smc` only in the PSCI image.
The passive image reads none of `ID_AA64MMFR3_EL1`, `ID_AA64MMFR4_EL1`,
`ID_AA64ISAR2_EL1` and `ID_AA64DFR1_EL1` (the X2 image reads the first three
behind an exception guard).

### X2 test image for the Surface Pro 11

The X2 test image is the probe plus nine functional tests (T01 to T09). It is
built only on request and is never part of the default probe build. The
hardware image needs the SoC named explicitly, because the NV2 record it writes
carries it and the monitor's gate refuses a record for another SoC. Its
`model`, `model-fatal` and `model-fatal-pre` variants are for QEMU only: never
run them on hardware (`model-fatal-pre` makes its unguarded bad access before
ExitBootServices, to prove that the fatal page does not depend on the console).
The operator guide is [SP11-X2-TESTS.md](SP11-X2-TESTS.md); the
developer notes are [probes/uefi/tests/README.md](../probes/uefi/tests/README.md).

```sh
python3 tools/build-uefi-tests.py --soc x1e80100      # build/uefi/sp11-el2-tests.efi (the hardware image)
python3 tools/build-uefi-tests.py --soc x1e80100 --require-mmio-map   # strict device windows, see below
python3 tools/build-uefi-tests.py --variant all --soc x1e80100 --verify-reproducible
python3 -B tools/test-uefi-tests.py                   # every host test of the image, one command
```

The default image refuses a device window (T05 to T07) that the UEFI memory map
calls RAM, firmware, ACPI or anything but MMIO, and reads a window the map does
not mention: the map of the QEMU machine lists none of its GIC, ECAM or SMMU
windows, so a strict default would leave the model run unable to check those
tests. `--require-mmio-map` makes T05 to T07 read a window only if the map lists
all of it as MMIO. Decision: keep the lenient default; read `memmap.txt` from
the passive probe first and then choose (the procedure is in
[RUNNING.md](RUNNING.md#running-the-efi-probe-and-the-x2-tests)). The option
changes the build id, the image's read-only data carries the tag
`SP11X2-WINDOWS=STRICT` (else `NO-CONFLICT`), and the pages show `MAP STRICT`.
The host tests build and audit a strict image; none runs it under QEMU, where it
would refuse every window by design.

### Boot media for the Surface Pro 11 (X0 and X1)

`tools/stage-sp11-boot-media.py` stages the USB-stick tree in a scratch
directory on the Mac; it never opens a block device and refuses a destination
that looks like a real volume: a `--dest` with a `..` component, below a mount
point, or reaching `/Volumes` or `/dev` in any letter case or through a firmlink
alias. Stage under `build/` so that nothing in the
tree is picked up by `git status`. The UEFI shell is a pinned public release
(`tools/sp11-boot-media-pins.json`), `slbounce-always.efi` and `sltest.efi` come
from `sh tools/build-slbounce.sh` in q1n1 (it builds the pinned slbounce commit
with `SLBOUNCE_ALWAYS_SWITCH=1 DEBUG=1`), and the probe is the image built above.
`tcblaunch.exe` is the operator's own copy from media they are licensed to use;
it is never committed. `stage` writes `SP11/MANIFEST.SHA256` and, because
slbounce's driver opens `tcblaunch.exe` at the volume root, a byte-identical
root copy. Each input is read once, and the bytes that were validated are the
bytes staged and hashed.

```sh
python3 tools/stage-sp11-boot-media.py stage --shell <shellaa64.efi> --slbounce <slbounce-always.efi> \
    --sltest <sltest.efi> --probe build/uefi/sp11-el2-probe.efi --tcblaunch <tcblaunch.exe> \
    --pins tools/sp11-boot-media-pins.json --dest <empty-scratch-directory>
python3 tools/stage-sp11-boot-media.py verify --dest <same-directory>
```

`stage` and `verify` read `--pins` (default `tools/sp11-boot-media-pins.json`),
which must be a readable regular file with a `shell.sha256` entry; a missing or
unusable file is refused. `--no-pins` skips the shell hash pin, and the result
then says `shell_pin: unchecked`: do not use it for media that will boot the
unit. `verify` re-checks the staged shell against the pin (`shell_pin: match`,
`mismatch` or `unchecked`) and exits 1 on a mismatch, even when the manifest is
satisfied.

Putting the staged tree on a stick is the operator's step, with the procedure in
[RUNNING.md](RUNNING.md#preparing-the-x0x1-boot-stick).

### Reproducing the A16 monitor baseline

[AGENTS.md](../AGENTS.md) asks a change that must leave the default path alone
to show it by comparing monitor ELF bytes. `tools/monitor_baseline.py` records
the identity of the monitor build before an edit to shared code and compares it
afterwards. It needs no hardware and no Apple input:

```sh
python3 tools/monitor_baseline.py capture before
# edit shared code: compat/, shadow/, probes/, config/
python3 tools/monitor_baseline.py capture after
python3 tools/monitor_baseline.py compare before after
```

`capture` builds four flag sets with `tools/monitor_build_offline.py`, each
twice into fresh directories:

| Set       | Flags                                                              |
| --------- | ------------------------------------------------------------------ |
| `bare`    | `FLAGS` in `probes/run.py`, which has no `SNAP_*` define           |
| `base`    | `config/monitor-flags.json`                                        |
| `control` | the speed-test control variant of `tools/cycle_lib/speed_test.py`  |
| `fast`    | the speed-test fast variant of the same file                       |

A set is reproducible only when its two builds agree on the binary, the linked
ELF and every object. `capture` exits 1 if a set is not, and `compare` never
reports such a set as identical. The record is written to
`build/monitor-baseline/LABEL.json`, which is git-ignored (`--results DIR`
picks another place), and an existing label is never overwritten.

`compare` exits 0 when every set is identical, 1 when a set differs and 2 when
the records cannot be compared. A difference names the objects that changed, or
says that only the link differs. Identity rests on the size, the binary and the
linked ELF.

- Homebrew `llvm` and `lld` must be at the fixed paths in
  `tools/monitor_build_offline.py`, and q1n1 must sit beside the repository at
  `../q1n1`. `--tree TREE` captures another checkout, which needs its own
  `../q1n1`.
- The monitor is built with `-flto`, so a hash depends on the exact compiler and
  linker. Records from different toolchains are reported as incomparable, not
  waived: capture both on one toolchain.
- The `-flto` sets (`base`, `control`, `fast`) compile to bitcode that embeds
  the source path. For all four sets the linked binary and ELF came out
  identical from checkouts at two different paths, but the per-object hashes of
  the LTO sets did not. The list of differing objects is therefore meaningful
  only when both captures were made from the same path.
- The tool covers the monitor only. The kext-section comparison in the same
  rule of AGENTS.md needs the KDK, the generated `*26A428.h` headers and the
  submodules. Without them it is not run, and is reported as SKIP.

`python3 tools/test-monitor-baseline.py` tests the tool itself against stand-in
builds.

### The SP11 monitor variant (experimental)

`-DSNAP_PLATFORM_SP11=1` compiles the Surface Pro 11 units into the monitor: the
board-info validator, the NV2 and PAuth launch gate, the placement model, the
stage-2 geometry check and the thermal-guard model (`OPTIONAL` in
`probes/run.py`), and the SP11 window contract inside `compat/nvme-host.c`. It
is never a default and is not one of the four sets the baseline captures; the
A16 compare must stay IDENTICAL. It is compile-checked, never run:

```sh
python3 tools/monitor_build_offline.py . build/sp11-check config/monitor-flags.json -DSNAP_PLATFORM_SP11=1
```

A unit that no call site references is dropped by the linker, so this build can
equal the `base` set in size until a call site exists. The set that includes the
placement model's requirements adds the fair-lock token and the five GIC macros
(the values are INFERRED from the public device tree until X2 measures them):

```sh
python3 tools/monitor_build_offline.py . build/sp11-full config/monitor-flags.json -DSNAP_PLATFORM_SP11=1 \
    -DSNAP_FAIR_TOKEN=1 -DSNAP_GICD_BASE=0x17000000 -DSNAP_GICR_FIRST=0x17080000 -DSNAP_GICR_LIMIT=0x17380000 \
    -DSNAP_GICR_STRIDE=0x40000 -DSNAP_TIMER_INTID=26
```

`GUEST_CPU_COUNT` defaults to 6 on SP11, the only count that passes the static
assertions in `probes/xnuboot/context.c` (the private bank offset),
`probes/xnuboot/user_views.c` (under `SNAP_USER_VIEW_BATCH`),
`compat/sptm-root.h`, `compat/panic-state.h` and `probes/secondary.h`; an SP11
build with 4 or with 12 stops at them. On an A16 build the count is the literal
six, and `-DGUEST_CPU_COUNT=N` is a macro redefinition, an error under the
monitor's `-Werror`. Six SP11 vCPUs span two clusters, which the placement model
allows only with `SNAP_FAIR_TOKEN`: a build without `-DSNAP_FAIR_TOKEN=1` still
compiles and links, but the monitor refuses to launch it with a prepare status of
-146 (`GUEST_CPU_NEEDS_FAIR_LOCK` in `compat/cpu.h`). With the token and no
embedded record every SP11 launch is refused with a prepare status of -90 by
design. Turn a measurement into the record and build with it:

```sh
python3 tools/nv2_record.py ingest <measurement.log> --header build/nv2-record.h
python3 tools/monitor_build_offline.py . build/sp11-check config/monitor-flags.json \
    -DSNAP_PLATFORM_SP11=1 "-DSNAP_NV2_RECORD_HEADER=\"$PWD/build/nv2-record.h\""
```

The record is a machine-specific measurement: keep it under `build/` and never
commit it. Do not add any SP11 flag to `config/monitor-flags.json` (it is the
A16 recipe).

`python3 tools/nv2_record.py show <measurement.log> --check` asks the same gate
whether a record would let a launch through. With all four `--live-*` values it
exits 3 unless the gate would allow the launch. Without them it still never exits
0: it exits 3 for a record the gate can never allow and 2 for any other, because
the live register values are what the gate also compares. The input must be a
regular file (`-` reads standard input), not a link, and at most 32 MiB.

The q1n1 side of the host link has a default-off SP11 build too. It lives in a
patch for the q1n1 repository (not applied to it), so it is not part of the
sibling checkout today. Once that patch is applied, the build is
`python3 tools/sp11_usb_ports.py emit-c config/boards/sp11.json <q1n1>/build/sp11-usb-table.c`
followed by `make -f platform/uefi/Makefile SP11=1 SP11_USB_TABLE_C=build/sp11-usb-table.c`
in q1n1, and its tests run in q1n1
(`Q1N1_BASE_DIR=<pristine q1n1> python3 tools/test-q1n1-sp11-usb-ports.py`). The
table is derivable only from a descriptor that lists the two USB controllers. The
shipped `config/boards/sp11.json` now lists them (INFERRED from the pinned
Denali/Hamoa device tree: `usb-1-ss0` and `usb-1-ss1`), but no core id is
measured, so `emit-c` still refuses it unless `--allow-unmeasured-id` is given;
that flag makes q1n1 accept any DWC3 core family, a weaker guard against a wrong
base, so X2 has to measure the id and pin it before the first takeover (see
[SURFACE-PRO-11.md section 11.2](SURFACE-PRO-11.md#112-decisions-for-the-project-owner)).
The descriptor is still not boot-ready: the MIDR, GIC version, timer, ECAM, DRAM
geometry and the keyboard, touchpad, touchscreen, NVMe and debug UART states are
unknown.
The generated file is build output and is never committed.
