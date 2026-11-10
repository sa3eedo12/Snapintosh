# Status

What works on the ASUS Zenbook A16 (X2E-96-100) with macOS 27 build 26A428,
what is known to be wrong, and what comes next. The numbers come from the most
recent full desktop sessions, which ran about 25 to 30 minutes each.

## What works

| Area | State |
| --- | --- |
| Boot | The stock J700/T8140 kernel boots on six vCPUs from macOS installed on the internal NVMe. It reaches launchd, WindowServer, the login window and the desktop. |
| Graphics | The desktop renders on the Adreno X2-90 through AdrenoMetal, AdrenoX2 and air2ir3, with no software fallback. A pixel probe over 14 desktop scenes (36 frames) passes with 0 mismatching tiles. Core Animation effects, icons and app content render. |
| Display | AdrenoDisplay drives the internal 2880x1800 panel at 60 Hz. With `--present-beam`, presentation is locked to the panel's vblank and is tear-free. |
| GPU | Asynchronous IB execution, pipelined WindowServer submissions, GPU recovery after an overrunning IB, a 15 GiB GPU address window, and GPU DVFS. Sessions have run at a 1185 MHz ceiling without GPU errors, below 72 °C. |
| Input | The keyboard and the touchpad (precision mode, presented as an Apple multitouch device). Window drags, scrolling, trackpad gestures, Mission Control and Cmd-Tab app switching all work. |
| Storage | The emulated ANS2 controller on the real NVMe, with asynchronous command completion (`--nvme-async`). |
| Wi-Fi | Through the host-proxy harness: scan, a WPA2-PSK connection, DHCP and ping. See below for the macOS side. |
| Stability | Full desktop sessions end on a requested stop, with no panic and no WindowServer crash in the latest configuration. |

## Known issues

- **Smoothness.** Interaction is usable but not yet smooth. Recent sessions
  show a median of about 46 to 60 presents per second in busy periods, with
  1.7 to 5 % of frame intervals longer than 34 ms. The main causes:
  - *Present-gate deferrals.* With pipelining, WindowServer can swap a
    surface whose GPU work is still running. The display then waits one full
    refresh before copying it.
  - *Occasional long stalls* (100 to 350 ms) that are not GPU time. They
    coincide with heavy console output and timer lateness.
  - *Contention on the monitor's global lock* for some SPTM calls. In
    particular, page-attribute updates on buffers whose cache mode changes
    are slow; the plug-in now reuses write-back buffers to avoid most of them.
- **CPU clocks.** The CPU governor's requests above a certain performance
  level are not granted by the firmware, so the CPU clusters do not reach
  their top clocks.
- **System Settings.** The Displays pane redraws badly ("accordions") and
  glitches the screen, and icons inside some Settings panes are missing.
- **Wi-Fi in macOS.** The native QCC2072 controller loads in the guest, but
  the Wi-Fi menu cannot join a network yet.
- **Large blits.** A single 4 MiB `CP_MEMCPY` hung the GPU. The plug-in now
  splits blits into chunks of at most 1 MiB, and GPU recovery handles other
  overruns. Other hang triggers may exist.
- **Not supported:** audio, cameras, Bluetooth, sleep and wake, external
  displays, the 120 Hz panel mode, and any other machine or macOS build.
  A Surface Pro 11 (X1E80100) port is planned in
  [SURFACE-PRO-11.md](SURFACE-PRO-11.md); it is UNIMPLEMENTED and has not
  run on hardware. Host-side groundwork exists and is EXPERIMENTAL (see
  [Surface Pro 11 (planned)](#surface-pro-11-planned) below). The A16 monitor
  is byte-identical without `-DSNAP_PLATFORM_SP11=1`.
- **Host required.** Every boot is driven from a Mac over USB-C through q1n1.
  There is no standalone boot path.

## Surface Pro 11 (planned)

The port to the Surface Pro 11 (Snapdragon X Elite X1E80100) is UNIMPLEMENTED,
and nothing in this section has run on a Surface Pro: hardware status is NOT RUN
for every row. The rows are host-side groundwork. Each is EXPERIMENTAL (tested
on the host, and the EFI images also under QEMU) and is built only with
`-DSNAP_PLATFORM_SP11=1` or as a standalone host tool. The plan, the gates and
the evidence are in [SURFACE-PRO-11.md](SURFACE-PRO-11.md), whose section 9.3
lists the tests.

| Area | State |
| --- | --- |
| Platform gate, board descriptor, board-info ABI | EXPERIMENTAL. The SP11 descriptor is a template with INFERRED values and is refused as boot-ready. The monitor has no caller for the board-info validator (UNIMPLEMENTED) |
| NV2 launch gate, Oryon PAuth ID normalization | EXPERIMENTAL. An SP11 monitor refuses every launch until a functional NV2 record from the unit exists, and none does. Two other launch refusals are by design: a vCPU count that needs the fair lock in a build without `-DSNAP_FAIR_TOKEN=1` (-146, checked first), and any SSD window until the SP11 NVMe contract has its inputs (-145) |
| vCPU placement, GIC discovery, stage-2 geometry | EXPERIMENTAL host models with INFERRED values; the monitor does not call the topology model, but it enforces the model's fair-lock rule at launch (-146) |
| EL2 entry (VHE or de-VHE), timer offsets, `DC ZVA` | EXPERIMENTAL host models and decision record ([SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md)); linked into no build |
| Thermal guard | EXPERIMENTAL read-only model; refuses every admission until X2 measures the platform |
| `tools/cycle.py --target sp11` | EXPERIMENTAL refusal layer; only the passive checks run, and no SP11 hardware flow exists (UNIMPLEMENTED). `--target` must be spelled in full, and the modes that can write NVMe need a per-run authorization on top of the standing ones |
| EFI state probe and X2 functional test image | EXPERIMENTAL, QEMU only ([SP11-X2-TESTS.md](SP11-X2-TESTS.md)); they are the instruments for gates X1 and X2. Hardened after an independent review, with host tests and QEMU only: the passive probe reads no unguarded newer ID register and the PSCI image refuses an HVC conduit; the tests image halts on a framebuffer-only fatal page (nothing written to the stick, no ExitBootServices) when a restore fails, and refuses device windows that are not plausible or that the memory map calls RAM or firmware. Hardware NOT RUN |
| Boot media | EXPERIMENTAL staging tool for the USB stick; preparing the stick is the operator's step |
| USB host link, display, input, NVMe window, GPU (a7xx) | Studies and host models ([host link](SP11-HOST-LINK-STUDY.md), [display](SP11-DISPLAY-STUDY.md), [input](SP11-INPUT-STUDY.md), [NVMe window](SP11-NVME-WINDOW.md), [GPU](SP11-GPU-A7XX-STUDY.md)), EXPERIMENTAL. No bring-up code exists: PHY and clock bring-up, the display and GPU drivers and the input driver are UNIMPLEMENTED |
| Partition planner, `sp11` profile | EXPERIMENTAL; planning only, on synthetic surveys. It never writes a disk |
| SP11 monitor hardening | EXPERIMENTAL; host tests only (`tools/test-monitor-hardening.py` and the `StatusBands` tests of `tools/test-sp11-integration.py`), hardware NOT RUN. The two launch refusals above, the static assertions at the sites that hard-code six vCPUs, and the A16 `GUEST_CPU_COUNT` as an unoverridable literal |
| SP11 host tooling hardening (13 review findings) | EXPERIMENTAL; host tests only (`tools/test-cycle-targets-hardening.py`, `test-sp11-boot-media-hardening.py`, `test-nv2-record-hardening.py`, `test-check-hamoa-acpi-hardening.py`, `test-sp11-usb-ports-hardening.py` and `test-snapmac-partition-plan-hardening.py`), hardware NOT RUN. Two items are partial by design: the 1 MiB `CP_MEMCPY` cap is enforced in the monitor and kext, not in `cycle.py`; the four-reader gate does not require a hybrid-MBR-aware reader |

## Roadmap

In rough order:

1. **Frame pacing to a steady 60 Hz.** Event-driven GPU completion instead of
   a polling timer. The display copy happens at the earliest tear-free moment
   after completion, and WindowServer's work is prioritised over other
   clients'.
2. **Settings and display modes.** Make AdrenoDisplay's mode, property and
   notification handling complete, so that the Displays pane works, and fix
   the missing icons.
3. **Monitor overhead.** Move the remaining hot SPTM endpoints off the global
   lock, and find out why the CPU performance requests are capped.
4. **Wi-Fi from the macOS menu.** Connect the native controller to the macOS
   Wi-Fi stack, so that joining a network works without the host harness.
5. **Higher clocks.** Allow GPU operating points above 1185 MHz, with the
   thermal guard on.
6. **120 Hz.** Drive the panel at its native refresh rate once 60 Hz is steady.

## History

[GUI-BOOT-ROADMAP.md](GUI-BOOT-ROADMAP.md) describes how the project got from
the first kernel instruction to the desktop.
[KERNEL-PATCHES.md](KERNEL-PATCHES.md) lists the opt-in kernel patches.
