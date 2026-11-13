# Surface Pro 11 stick log: reaching the boot stick from EL2 (B3, and the first step of B2)

**Design study with host-side groundwork. UNIMPLEMENTED on hardware. Hardware status: NOT RUN.** Nothing here ran on a
Surface Pro 11. When the study was written nothing in q1n1 or slbounce had been changed; stage S0 has since been built as
an opt-in, read-only survey in the q1n1 fork (`docs/SP11-STK-SURVEY.md` there; branch `sa3eedo12-sp11-boot`, commits
`8ed0c4f` and `8235433`), EXPERIMENTAL and hardware NOT RUN. Stages S1 and later do not exist. It answers plan item B3 (a persistent log sink) and the
first deliverable of B2 (EL2 USB host bring-up) of [SURFACE-PRO-11.md](SURFACE-PRO-11.md) and
[SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md) as far as public sources and a host can: which controller the boot stick
most likely sits on, what firmware and the slbounce bounce leave behind, how a log can reach the stick from EL2 without a
file system driver, how that is made safe, and a staged plan with stop criteria. It also records the host-side pieces that
were built and tested without hardware: a Mac-side region tool, a pure C Bulk-Only Transport (BOT) and SCSI model with a
guarded log writer, and their tests (section 8).

| Label | Meaning in this study |
| --- | --- |
| VERIFIED | Read in a pinned source (section 10), in the q1n1 or slbounce tree named there, or checked by a host test that ran. It says what the source or test says, never that the unit behaves so. |
| OBSERVED | Reported from a real run: by the operator for this unit (one unit, one boot unless stated) or by a third party. Not re-measured here. |
| INFERRED | Reasoned from the above and not checked. Every statement that the unit behaves as a public source describes is INFERRED. Estimates of effort are INFERRED and carry no data. |
| UNKNOWN | Not established. A named measurement (section 3.5) or a named operator answer (section 11) settles it. |
| EXPERIMENTAL | Code exists and passes host tests only. |
| UNIMPLEMENTED | No code exists. |

## 1. Result in brief

1. **Recommendation: GO for S0 now, conditional GO for S1 to S6, with the stop criteria of section 4.7.** S0 is a
   read-only survey, B2 needs it anyway, and it decides everything after it. The writer is worth building only if, at EL2
   after the bounce, the stick's controller is powered and clocked, answers as an xHCI, and is not behind an SMMU that
   faults or redirects DMA. Otherwise the next step needs writes to clocks, PHYs or the SMMU, which nothing here authorizes.
2. **Mechanism: raw Bulk-Only writes into a pre-allocated, tagged log file; no FAT code at EL2** (section 4). One departure
   from the brief: the Mac pre-writes a tag in every sector of the region (magic, the sector's own index, a 64-bit nonce),
   not only a header, and the writer refuses to overwrite a sector that does not still hold its own tag. A wrong, stale or
   hostile map then cannot damage the stick's file system, which is the failure that matters most. The per-sector tag is also
   what makes a later q1n1 FAT walker (workflow W2) self-verifying.
3. **The stick probably sits behind a USB hub (INFERRED; the operator confirmed an adapter or hub, Q1 in section 11).** The
   UEFI path has two USB nodes; in EDK2's code each USB node is
   one device on the way down, so the second node is a device behind the first, a hub (section 3.1). q1n1's xHCI driver
   cannot address a device behind a hub (G03 of the host-link study). The cheapest fix was an operator answer (Q1, section 11):
   plug the stick straight into the unit. Whether that is possible is still open: the operator's answer was only that the
   stick goes through the adapter. Hub support is stage S1.5 (about 250 to 350 lines, 2 to 4
   more boots), or the survey shows that adopting the firmware's own slot state (S1a) avoids it.
4. **Which of the two Denali-enabled controllers the stick uses is UNKNOWN** (`usb_1_ss0` at `0x0a600000` or `usb_1_ss1` at
   `0x0a800000`). Neither the path nor any pinned fact says. The firmware's PCI I/O interface on the controller handle (if it
   exists) or the S0 port survey settles it, and swapping ports does too.
5. **One boot can answer M1, M2 and the SMMU half of M6 without a single device write** (sections 3.5 and 3.6, Appendix A),
   plus two sub-measurements this study adds: the walk of the firmware's own slot contexts (M1b) and the xHCI supported
   protocol walk (M1c). M3 stays deferred because no PHY register offset is pinned. M4, M5, M6b, M7, M8 and M10 write.
6. **Host groundwork exists and passes its host tests (EXPERIMENTAL, section 8).** A Mac-side region tool and verifier, a C
   model of the BOT wrapper, five SCSI commands and the guarded writer with a fault-injecting fake device, and one Python
   driver with seeded-bug mutants. None of it proves that a real stick behaves like the fake, that q1n1's xHCI driver can
   carry the bytes, or anything about the Surface Pro 11.
7. **Cost (INFERRED estimate, no data):** about 1,500 to 1,800 new lines in q1n1 plus about 900 lines copied from `tools/`,
   and 9 to 23 hardware boots without a hub, most likely 12 to 16 (section 6). With hub support add 250 to 350 lines and
   2 to 4 boots.
8. **The three unknowns only hardware answers:** (a) what the xHC, its GDSC, clocks and PHY look like at EL2 after the
   ExitBootServices handlers and the QHEE teardown (S0 lines GCC, GATE and OP); (b) which controller and port, and whether a
   hub is in the way (lines HUB, XECP, PORT, SLOT); (c) whether DMA from the xHC reaches q1n1's arena coherently through the
   SMMU (line SMMU, then the S1 No-Op).

## 2. The problem and the requirements

After ExitBootServices the firmware's USB stack and file system drivers are gone, and q1n1 at EL2 (non-VHE, MMU on with the
firmware's stage-1 tables, DAIF masked, framebuffer console only; OBSERVED, [SURFACE-PRO-11.md](SURFACE-PRO-11.md), Status of
B1, attempt 7) has no working USB driver on this machine. Every EL2 measurement reaches the operator as text on the panel,
which is photographed and transcribed by eye. That is slow, error-prone, and the operator is the bottleneck for dozens of
boots to come. The boot stick is plugged in the whole time and was enumerated by firmware just before ExitBootServices, so
the Mac can read results from files afterwards if EL2 code can put bytes on the stick.

Requirements, each checked in sections 4 to 6:

- **R1.** Never write a sector that is not the log's. A bad run must be harmless to the stick's file system.
- **R2.** No FAT code at EL2. The writer knows sector numbers, not files.
- **R3.** Everything polled; no interrupt is unmasked. Every wait has a deadline.
- **R4.** Fail closed with a readable reason on the panel; the boot continues without the log.
- **R5.** Opt-in. A compile-time gate (the model is behind `SNAP_PLATFORM_SP11`; a runtime writer would add its own gate and a
  boot option, UNIMPLEMENTED) and no change to the default build.
- **R6.** Nothing writes the internal NVMe, firmware, NVRAM, clocks or voltages.
- **R7.** Not throwaway: the xHCI work of stages S1 and S2 is the same work the later CDC-NCM link needs (section 7).
- **R8.** The Mac proves the map before the stick is used, and again after a run, with tools that never open a device.

## 3. (a) The controller the stick sits on after the bounce

### 3.1 What the device path says, and what it does not

The stick's UEFI device path is `Pci(0x81,0x0)/USB(0x1,0x0)/USB(0x2,0x0)` (OBSERVED, one unit, one boot; reported in the task
brief, with no transcript in the repository at the time of writing). It is UEFI device-path text, not a hardware
measurement.

- **`Pci(0x81,0x0)` is a label, not an address.** In device-path text a PCI node is `Pci(device,function)`. A PCI device
  number has 5 bits (0 to 31), and `0x81` does not fit, so the node was made up by firmware for a controller that is not on
  a PCI bus (INFERRED). It cannot identify the controller. What it does suggest is that firmware presents the controller
  through a PCI I/O style interface, because EDK2's XHCI driver binds only to PCI I/O handles (INFERRED; the driver is the one
  at `Xhci.c:1901` and `Xhci.c:2112`, and Surface firmware is EDK2-derived, INFERRED).
- **Two USB nodes mean a hub.** EDK2's USB bus driver builds each node from the device's parent port and interface number
  (`UsbEnumer.c:124`, `UsbEnumer.c:126`, `UsbEnumer.c:127`) and appends it to the device path of the parent hub interface
  (`UsbEnumer.c:134`). Ports are numbered from 0 (`UsbEnumer.c:1027`, `UsbEnumer.c:1080`). So `USB(0x1,0x0)` is a device on
  port index 1 of the root hub, interface 0, and `USB(0x2,0x0)` is a device on port index 2 of that device, interface 0. The
  first device must be a hub, and the stick is on its third port (INFERRED: it assumes Surface firmware numbers ports like
  EDK2, which it is derived from). A stick plugged straight into the controller would show one USB node.
- **Which root port that is, high speed or SuperSpeed, is UNKNOWN.** A DWC3 host usually has one USB2 and one USB3 root port;
  the supported-protocol capabilities in the xHCI extended capability list say which port number is which (line XECP,
  Appendix A). An adapter's hub on the USB2 root port would also mean the stick runs at high speed, not SuperSpeed (INFERRED).

The consequence is operator question Q1 (section 11): **is the stick plugged straight into the unit's USB-C port, or
through an adapter, dock or hub?** Many USB-C multiport adapters contain a hub. Whether the Surface Pro 11 has an internal
hub on this path is UNKNOWN. This matters because q1n1's xHCI driver puts only the root-hub port number in the slot context
and has no route string, hub class or transaction translator code (G03, section 7).

### 3.2 Which controller

The public Denali device tree enables two host controllers, one per Left-side USB-C connector (VERIFIED as what the files say,
INFERRED for the unit; [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md) section 3.1). Both nodes are in Linux
`7475ea8b58616b33c04afb976cf0fbaad9dd63fb` (section 10):

| | `usb_1_ss0` | `usb_1_ss1` |
| --- | --- | --- |
| Node, compatible | `hamoa.dtsi:5254`, `hamoa.dtsi:5255` | `hamoa.dtsi:5345` |
| Register window | `0x0a600000`, size `0xfc100` (`hamoa.dtsi:5256`) | `0x0a800000` (same layout) |
| Power domain | `gcc_usb30_prim_gdsc`, GDSCR `0x39004` (`gcc-x1e80100.c:6740`) | `gcc_usb30_sec_gdsc`, GDSCR `0xa1004` (`gcc-x1e80100.c:6752`) |
| SMMU stream (`apps_smmu`) | `0x1420` (`hamoa.dtsi:5307`) | `0x1460` (`hamoa.dtsi:5398`) |
| Cache coherent DMA | `dma-coherent` (`hamoa.dtsi:5320`) | `dma-coherent` (`hamoa.dtsi:5411`) |

**Which one the stick uses is UNKNOWN.** The path carries no controller identity (3.1). The Denali description says which
connector each controller serves, not which connector the operator used. Hamoa has three more DWC3 controllers that Denali
leaves disabled, and nothing excludes that firmware uses one of them (UNKNOWN). Three discriminators exist, cheapest first:

- **D1, before ExitBootServices, no MMIO (UNIMPLEMENTED):** read BAR 0 and the location from the PCI I/O protocol on the
  controller handle that the stick's device path runs through. If firmware exposes it (INFERRED from the `Pci` node), BAR 0
  is the controller's base address. Line `HC` of Appendix A.
- **D2, at EL2, read-only:** the port survey (lines PORT, SLOT): the controller whose root port shows a connection, and whose
  firmware slot contexts hold a hub (hub bit) with a device behind it (a non-zero route string) or a device with a bulk IN and
  a bulk OUT endpoint, is the one.
- **D3, operator:** move the stick to the other USB-C port and compare D1 and D2.

### 3.3 What firmware leaves behind

Every row is UNKNOWN for the unit unless a label says otherwise. "Measured by" names the S0 lines of Appendix A.

| Item | What is expected, and why | Label | Matters because | Measured by |
| --- | --- | --- | --- | --- |
| xHC run state at EL2 | Halted: EDK2's XHCI driver registers a handler on the ExitBootServices event that cancels its poll timer and clears Run/Stop (`Xhci.c:1901` comment, `Xhci.c:1926` `XhcHaltHC`, registered at `Xhci.c:2112` for `gEfiEventExitBootServicesGuid`), then clears BIOS ownership (`Xhci.c:1932`) | INFERRED (if Surface's driver is EDK2's) | The writer must start the controller itself | OP |
| PCI attributes | The same handler then restores the original PCI attributes through PCI I/O (the code after `Xhci.c:1932`). On a real PCI device that clears bus master and memory decode; on a synthetic node what the shim does is unknown, and it may gate the block | UNKNOWN | A gated block cannot be read | GCC, GATE, MAP |
| Rings and contexts in DRAM | DCBAA, command ring, event ring, the hub's and the stick's device contexts and transfer rings. The handler frees nothing (it only halts); after ExitBootServices the memory is free for the next owner. Whether the bounce, q1n1's loader or the memory map reused it is unknown | INFERRED that nothing frees it; UNKNOWN whether it is intact | Intact contexts make adopting the slot possible (S1a) and show the hub and the route | FWRINGS, SLOT |
| GDSC and clocks | Linux needs the GDSC (`hamoa.dtsi:5293`), nine clocks named at `hamoa.dtsi:5258` and following, `mock_utmi` at 19.2 MHz and `master` at 200 MHz (`hamoa.dtsi:5279`), a reset (`hamoa.dtsi:5296`) and interconnect votes (`hamoa.dtsi:5298`). What firmware leaves on is unknown | UNKNOWN | If off, no read is safe and S1 would need GCC writes: stop criterion SC1 | GCC, GATE |
| PHYs, eUSB2 repeater, retimer | The stick enumerated at P2, so the path was live then. The PHY nodes are `hamoa.dtsi:2819`, `hamoa.dtsi:2833`, `hamoa.dtsi:2892`, `hamoa.dtsi:2906`; no PHY register offset is pinned here | UNKNOWN | M3 to M5 are deferred | none in S0 |
| SMMU stream state | Streams `0x1420` and `0x1460` in `apps_smmu` (`hamoa.dtsi:8436`, `hamoa.dtsi:8437`, `hamoa.dtsi:8438`). The EL2 overlay says that under Gunyah the PCIe SMMU "is controlled by the firmware" (`x1-el2.dtso:25`) and gives the apps SMMU stream ids of two remote processors to the OS (`x1-el2.dtso:56`): at EL2 mappings that the hypervisor made become the OS's to make. What the bounce leaves is unknown | VERIFIED as what the overlay says; INFERRED consequence; UNKNOWN state | A faulting or translating stream turns every DMA into a fault or a stray write | SMMU |
| DMA cache coherence | The device tree says `dma-coherent` (`hamoa.dtsi:5320`, `hamoa.dtsi:5411`). q1n1's header assumes identity-mapped, coherent DMA (G04) | VERIFIED (what the files say); INFERRED (unit) | Wrong coherence makes stale TRBs or a garbled CBW (section 5, F6) | S1 No-Op, S4 |
| EL2 translation | MMU on with the firmware's stage-1 tables, TTBR0 only, essentially identity (OBSERVED, B1 attempt 7). Whether the device windows are mapped, and as Device memory, is unknown | OBSERVED (regime); UNKNOWN (windows) | An unmapped window faults | MAP |

### 3.4 What the bounce could disturb

- **The ExitBootServices handlers** all run before the bounce: slbounce's hook calls the real ExitBootServices first, then the
  SMC authenticate and launch calls (slbounce fork `380f25a334d4afab90a92b695cf6a8b85d106085`, `src/bounce_main.c`,
  VERIFIED by reading). So the xHC halt of 3.3 happens before everything below.
- **tcblaunch and the QHEE teardown** replace the hypervisor. A hypervisor owns the SMMU stage-2 contexts and some power
  and protection state; whether the launch changes any of it for USB is UNKNOWN (INFERRED as possible, with no evidence).
- **The cache sweep.** slbounce's blanket sweep hangs this unit's firmware, and the fork's scoped sweep got through once
  (OBSERVED, [SURFACE-PRO-11.md](SURFACE-PRO-11.md) X1). It has no USB meaning except that DRAM structures written by the
  firmware are read through caches afterwards; with coherent DMA that is fine, and S0 tests it for free by reading them.
- **Elapsed time.** After the halt nothing drives the bus. A USB 2 device suspends after a few milliseconds without frames and
  keeps its address and configuration through suspend; a controller reset or a port reset loses them (USB specification,
  INFERRED here). A SuperSpeed link leaves U0 on its own. VBUS and Type-C power belong to the ADSP
  ([SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md) section 3.5); whether they stay up is UNKNOWN. The survey sees all of it.
- **Reset is not an escape.** `ResetSystem` was OBSERVED not to work after the bounce (X1, R18 answered no), so there is no
  "reset and look again" at EL2, and every recovery is a power-cycle.

### 3.5 The ten measurements, and which are read-only at EL2

M1 to M10 are the host-link study's measurements ([SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md) section 4). The study
takes each at three points: P1 (boot services, before USB I/O), P2 (just before ExitBootServices) and P3 (EL2 after the
bounce). The stick log needs P2 and P3, and the difference is the answer to "what survives".

The "Order" column is the step number of 3.6.

| M | Measurement | Read-only at EL2? | Where it is taken | Order |
| --- | --- | --- | --- | --- |
| M2 | GCC GDSC and branch-clock status of each controller and its shared NoC clocks | **Yes**: loads from the always-on clock controller | S0 lines GCC and GATE | 3 |
| M1 | Per controller: xHCI capability, operational and port registers, DWC3 identity and role, glue registers | **Yes**, but only for a controller whose gate reads ON | S0 lines ID, OP, PORT, DWC3, GLUE | 4 and 5 |
| M1c (new) | xHCI extended capability walk: which port numbers are USB2 and USB3 | **Yes** (same condition) | line XECP | 4 |
| M1b (new) | The firmware's own DCBAA and slot contexts: route string, hub bit, root port, speed, endpoint states | **Yes**: DRAM loads, bounds-checked against the memory map | lines FWRINGS, SLOT | 6 |
| M6a | `apps_smmu` global state for streams `0x1420` and `0x1460` | **Yes**: loads from the SMMU's global register space | line SMMU | 7 |
| M3 | PHY lock and status | Read-only in principle, but no offset is pinned: UNIMPLEMENTED | none in the first boot | |
| M4 | SMB2360 repeater over SPMI | **No**: bringing up the SPMI controller writes | later, own authorization | |
| M5 | PS8830 retimer over I2C | **No**: same for I2C | later, own authorization | |
| M6b | A No-Op command through q1n1's own rings | **No**: writes xHC registers and q1n1's arena | stage S1 | |
| M7, M8 | UCSI over the ADSP channel | **No**: messages in shared memory | later | |
| M9 | On the Mac: which device appears | n/a (host side) | | |
| M10 | DRAM retention across a reset | **No**: writes canaries and needs a reset path | separate experiment, alternative (iii) | |

Before M2, two groups need no MMIO at all and are taken at P2: the **firmware protocol facts** (the stick's block geometry,
its partition, the number of USB nodes in its path, its interface class and endpoints from the USB I/O protocol, and the PCI
I/O BAR 0 of the controller handle) and the **mapping check** at EL2 (`q1n1_mmu_attr`, a read-only walk of the firmware's
stage-1 tables that already exists in q1n1).

### 3.6 The order of the survey: one boot, no device write

Each step's result is the precondition of the next, so a hang is localized and no access is made to a block that has just
been shown to be unpowered. A line `STK NEXT <block>` is printed before each block and stays on screen, so after a hang the
last NEXT line names the block that hung.

1. **P2, boot services, no MMIO.** Protocol facts: lines SELF, HUB, BLK, USBIO, HC (Appendix A). Remembered in q1n1's memory
   and printed again on the EL2 page, so one photograph holds both.
2. **EL2: MAP.** Is each window (GCC, SMMU, both controllers) mapped, and with what attribute? No device is accessed.
3. **EL2: GCC and GATE.** Loads of the GDSCR and branch-clock registers listed in Appendix A (offsets pinned in Linux, section
   10; `gcc-x1e80100.c:6740` and following). Each controller's gate is ON only if its GDSC reports power and every one of its
   nine clocks reports "running" (bit 31 of the branch-clock register clear, `clk-branch.h:63`; PWR_ON is bit 31 of the
   GDSCR, `gdsc.c:21`). The rule is conservative on purpose: a clock that is off but unneeded reads as PARTIAL and the block
   is skipped. The next boot can override it for a named block if it matters.
4. **EL2: per controller whose gate is ON,** capability registers (ID), the extended capability walk (XECP), then the
   operational registers and every port (OP, PORT).
5. **EL2: DWC3 and glue** (DWC3, GLUE): identity, role (`core.h:264`, host is 1), current mode (`core.h:295`), bus error
   address valid (`core.h:294`), and the two glue registers.
6. **EL2: the firmware's slot contexts** (FWRINGS, SLOT), only if DCBAAP lies in RAM according to the memory map, is 64-byte
   aligned, and every pointer followed does too; at most 8 slots.
7. **EL2: SMMU** (SMMU): ID registers, `sCR0`, the global fault status, and a bounded scan of the stream match registers for
   `0x1420` and `0x1460`.
8. **Verdict** per controller (Appendix A, line VERDICT).

No step stores to a device register, issues a command to the xHC, or touches an SPMI, I2C, GLINK or SMMU configuration. All
MMIO loads go through the guarded helpers q1n1 already has (`live_guarded`, the `Q1N1_GUARD_RETURN` leaf copies), so an abort
becomes a printed FAULT instead of a dead core. What the guards cannot catch is a bus hang or a protection violation that
resets the SoC; the apss watchdog the EL2 overlay enables (`x1-el2.dtso:10`) would then end the boot (UNKNOWN), and the
recovery is a power-cycle.

## 4. (b) The writer's mechanism

### 4.1 In one paragraph

The Mac-side tool pre-allocates a contiguous 1 MiB file `SP11/SNAPLOG.BIN` on the stick's FAT volume and a 128-byte map file
`SP11/SNAPLOG.MAP` that names the file's absolute LBAs, the stick's capacity and the volume geometry. q1n1 reads the map
before ExitBootServices with ordinary file reads and proves it against the stick (4.4). After the bounce an EL2 driver writes
log sectors straight into those LBAs over USB Bulk-Only Transport with TEST UNIT READY, INQUIRY, READ CAPACITY(10), READ(10),
WRITE(10) and SYNCHRONIZE CACHE(10). No FAT code runs at EL2, and the file system's metadata is never written: the file keeps
its size, its clusters and its directory entry for ever.

### 4.2 The region and the map (EXPERIMENTAL: host-tested formats; the byte tables are in Appendix C)

- **Region:** 2048 sectors of 512 bytes. Sector 0 is a header (64 bytes used: magic `SNAPLOG1`, version, nonce, region
  geometry, the start LBA, the partition LBA, the capacity, a CRC32). Sectors 1 to 2047 are log sectors: a 32-byte tag
  (magic `SLG1`, the sector's own index, the nonce, a boot id, a sequence number, a used count, flags, a CRC32 over the tag and
  the used bytes) and 480 payload bytes.
- **Map:** 128 bytes (magic `SNAPLMAP`, nonce, sector size, capacity, partition LBA, cluster size, data-area LBA, region size,
  one run of (start LBA, sectors), a CRC32). Version 1 accepts exactly one run: a fragmented file is refused, never written
  around.
- **The refinement of the brief: a tag in every sector.** The brief proposed a zero-filled file with a signature header. The
  Mac tool instead writes the header and an empty but fully tagged sector for every index (`build_region`). The writer may
  overwrite a sector only after reading it and finding its own tag (magic, own index, nonce), so every write authorizes
  itself. A zero-filled file would leave 2,047 sectors with nothing to prove and a header that protects only one.
- **Order, not position, is the history.** Sectors are ordered by (boot id, sequence number), the area is a ring over indexes 1
  to 2047, and the header is never rewritten. Before ExitBootServices q1n1 reads the whole file through the file system and
  picks the next index and boot id (`slog_scan`: it refuses a region holding a sector that is not ours).
- **Capacity.** One run of 2048 sectors holds 982,560 bytes of text per lap (2,047 log sectors of 480 bytes); the budget is
  4,096 sector writes per boot, about two laps.

### 4.3 Getting the region onto the stick, and reading it back

The tools open image files only and refuse any path at or below `/Volumes` or `/dev` (spelling and identity), a `..`
component, a symbolic-link component, a mount point or an existing output: the same rules as
`tools/stage-sp11-boot-media.py`, compared against it in the test. The operator writes images to a stick with the operating
system's own tools, outside this repository. Commands (all EXPERIMENTAL, host tests only):

```text
python3 tools/sp11_stick_logmap.py build    --tree DIR --out IMG [--size-mib N] [--device-sectors N] [--no-mbr]
python3 tools/sp11_stick_logmap.py prepare  --image BASE --out IMG [--device-sectors N] [--log-mib N] [--mkdir]
python3 tools/sp11_stick_logmap.py verify   --image IMG [--device-sectors N] [--allow-written]
python3 tools/sp11_stick_logmap.py read-log --image IMG
python3 tools/sp11_stick_logmap.py decode   --region SNAPLOG.BIN
```

`--device-sectors` is the stick's capacity in 512-byte sectors as the operating system reports it; it ends up in the map and
q1n1 compares it with two other sources (4.4). `prepare` copies the base image to a new file, allocates the contiguous log
file and map in `SP11/`, writes the pristine region, verifies the result and deletes the output if it does not verify.

| W | What happens | Who proves the map | Status |
| --- | --- | --- | --- |
| W1a | `build` makes a fresh FAT32 image (MBR or bare volume) from a directory tree of 8.3 names plus the region | the Mac verifier, reading the image back | EXPERIMENTAL. Long names are refused (`tree_name_not_83`): the staged tree's `MANIFEST.SHA256` has a 6-letter extension, so W1a does not take the real staged tree |
| W1b | The operator makes a FAT32 base image containing the tree with OS tools (UNTESTED here) and `prepare` adds the region; the operator writes the result to the stick once | the Mac verifier; long names and existing entries survive byte for byte (tested on a FAT16 fixture with long-name entries, and checked by macOS `fsck_msdos -n`) | EXPERIMENTAL. GPT disks are refused (`gpt_unsupported`): use an MBR disk or a bare volume |
| W2 | The Mac supplies only a pristine `SNAPLOG.BIN` in the tree; q1n1 walks the FAT read-only before ExitBootServices (about 250 lines) and builds the map itself | q1n1, by reading each mapped sector raw and checking its tag: a wrong walker is caught because the tags will not be there | UNIMPLEMENTED |
| W2' | q1n1 scans the first N MiB of the raw disk for the header carrying the nonce, no FAT code | q1n1, by the tags; two candidates (a deleted copy) are refused | UNIMPLEMENTED |
| W3 | A tool reads and patches the stick's device | n/a | REJECTED: no tool here opens a device |
| W4 | Live inspection of the mounted stick through the OS | n/a | UNIMPLEMENTED, and the tools refuse `/Volumes` today, so it needs a policy decision |

**Recommendation: W1b for the first sessions.** It is what the tests prove, it is done once per stick, and afterwards the
operator copies new `q1n1.efi` and other files onto the stick as usual. A FAT driver never moves an existing file, so the log
file keeps its clusters and the map stays valid (INFERRED for macOS's driver). If the operator replaces `SNAPLOG.BIN` by a
copy, the map goes stale and q1n1's proof refuses to write (4.4), which is the intended failure. W2 is the follow-up if
imaging is a burden; the tags make its FAT walker low-risk because a wrong answer cannot pass the proof.

**Reading the log.** The operator copies `SNAPLOG.BIN` off the mounted stick as an ordinary file into an ordinary directory
and runs `decode`; `decode` reads the nonce from the header sector, orders the sectors by (boot id, sequence number), and
counts torn sectors and sectors that are not ours. `read-log` does the same on an image. The two paths agree (tested).

### 4.4 What q1n1 proves before ExitBootServices (UNIMPLEMENTED in q1n1; the logic is `slog_map_parse`, `slog_header_check`, `slog_scan`)

All of it uses boot services and writes nothing. Any failure prints a reason and leaves the writer disarmed; the boot goes on.

1. Read `SNAPLOG.MAP` through the file system. Parse it: magic, version, CRC, nonce not zero, exactly one run, sector size 512,
   region 2 to 65,536 sectors, run size equal to region size, end of run inside the capacity (64-bit arithmetic), cluster
   size a power of two up to 128, run start aligned to the data area.
2. **Three capacities must agree:** the map, the Block I/O media of the raw disk (`LastBlock + 1`, `BlockSize` 512), and, at EL2,
   READ CAPACITY(10) (S3). The partition start in the map must equal the `HD()` node's start in the device path q1n1 was
   loaded from (0 for a bare volume, which has no `HD()` node).
3. Read the header sector through the raw disk's Block I/O at the mapped LBA and check it against the map (nonce, start,
   partition, capacity, CRC).
4. Optionally (recommended for the first sessions) read the whole file through the file API and the same 2048 sectors through
   Block I/O at the mapped LBAs, and require them to be equal. With every sector tagged by index and nonce, equality proves
   that the map points at this file, sector by sector, and that the file is contiguous.
5. `slog_scan` over the file's bytes: pick the next index and boot id; refuse a region with a sector that is not ours.

### 4.5 The write path at EL2 (EXPERIMENTAL: the C model; the xHCI side is UNIMPLEMENTED)

- **The API takes an index, not an LBA.** The only write path is `slog_put`: index 1 to 2047; the header sector is never
  written; the target LBA is `start + index` in 64-bit arithmetic inside the map's region.
- **Pre-read, then write.** The first time a session touches a sector it reads it and requires its own tag (magic, index,
  nonce). Any other content aborts the session, and nothing is written. Then WRITE(10) with the force-unit-access bit, then
  READ(10) of the same sector and a byte compare, then SYNCHRONIZE CACHE(10) at flush time (tolerated if the device answers
  ILLEGAL REQUEST). The counters `syncs` and `sync_refused` say which durability claim held; the panel marker LOG FLUSHED is
  printed only after write, read-back and sync.
- **Sticky disarm.** The first failure of any kind (device, tag, read-back, budget, transport needing reset) disarms the writer
  for the rest of the boot and records a reason. Nothing is retried blindly.
- **Bounded.** 4,096 sector writes per boot; every USB wait has a deadline (the xHCI layer's); a command that needs Bulk-Only
  reset recovery is never followed by another command until the reset and both halt clears succeeded.
- **Blocking, by design, and not for hot paths.** `slog_append` writes whenever a sector fills; it is the drain side. q1n1's
  main thread may call it. A monitor trap handler must not: the later log API (S6) puts a non-blocking RAM ring in front
  (single producer, bounded, no allocation) and drains it from a safe context (UNIMPLEMENTED; AGENTS.md sections 2 and 3).
- **Rate (INFERRED, not measured).** A sector costs three Bulk-Only commands (pre-read once, write, read-back), each a few USB
  frames; on the order of milliseconds per sector, so a full lap takes seconds to tens of seconds. The panel stays the
  channel for anything the log cannot carry (a hang before USB works).

### 4.6 Alternatives, judged honestly

| | Option | For | Against | Verdict |
| --- | --- | --- | --- | --- |
| (i) | **Bulk-only (BOT) versus UAS** | BOT is three transfers per command on one pair of bulk endpoints, needs no streams, and a stick that firmware read has a BOT interface: EDK2's mass-storage driver speaks only CBI and BOT (`UsbMassImpl.c:16`, `UsbMassImpl.c:17`, `UsbMassImpl.c:18`; INFERRED that Surface's does too). UAS needs xHCI streams, four pipes, information units and tags; no code exists here or in q1n1, and its speed gain is irrelevant to a log. The protocol numbers are BOT `0x50` and UAS `0x62` (`storage.h:32`, `storage.h:33`) | A UAS-only stick has no BOT interface (UNKNOWN for the unit; S2 reads the descriptors) | **BOT.** A stick with no BOT interface is not usable; use another stick |
| (ii) | **FAT-aware versus raw LBA writes** | Raw writes touch no metadata: the file never changes size, clusters or entry, and a bug can only damage log sectors, which the tags guard. FAT-aware writes need cluster allocation, both FAT copies, FSInfo, directory entries and long names: about 600 to 1,000 risky lines (INFERRED estimate), and a torn write corrupts the FAT so the stick no longer boots | Raw needs the Mac-side pre-allocation and a stable map (4.3); the file's modification time does not reflect writes (harmless) | **Raw** |
| (iii) | **DRAM retention across a warm reset, then a dump on the second boot** | No EL2 USB at all: firmware's own USB and file system drivers write the dump in boot services. About 100 to 150 lines for the canary test, which is M10 of the host-link study | Three unknowns in series: a reset that works at EL2 (`ResetSystem` was OBSERVED not to work after the bounce; PSCI SYSTEM_RESET and a watchdog reset are untried, UNKNOWN), retention of DRAM across it (UNKNOWN, A1), and a boot that returns to the stick. The last needs either BootNext, which is a non-volatile variable write that needs the operator's explicit authorization, or the operator holding the boot keys on every boot | **Keep as a cheap parallel experiment (M10), not the primary route.** If it works it could also recover something after a forced reset following a hang before USB works, which the stick writer cannot |
| (iv) | **UEFI runtime SetVariable after ExitBootServices** | Needs no USB driver | An NVRAM write is a persistent firmware modification and needs explicit authorization (AGENTS.md section 5). Runtime services at EL2 here are unverified (`ResetSystem` was OBSERVED not to work after the bounce). The store is small, wears, and a damaged store can disturb the boot configuration. A volatile variable (no NON_VOLATILE attribute, as q1n1's A16 prep step stores its snapshot) is not an NVRAM write but does not survive a reset and cannot be written after ExitBootServices | **Rejected** unless explicitly authorized, and then only for a few bytes of stage marker as a last resort |
| (v) | **A camera pointed at the panel** | No device risk and no new q1n1 code. A fixed camera takes a photograph at each page; a Mac script decodes the text. The glyph set is known and small (`screen_char` in q1n1's `main.c`), so template matching should decode it almost exactly; QR frames would make it exact (an encoder in q1n1 is a few hundred lines, UNIMPLEMENTED) | Shows only what is on screen when photographed; after a hang nothing later appears; someone still arms the camera. Not built here: it needs real photographs to validate | **Do it in parallel.** It removes the tiring part, transcription, at no device risk, but it is not a persistent log |

### 4.7 The go/no-go recommendation and the stop criteria

**The go/no-go.**

- **GO for S0 now.** It is read-only, B2 needs it regardless of B3, and its verdict decides S1 to S6.
- **Conditional GO for S1 to S6**, only if S0 shows at EL2 for the stick's controller: gate ON (GDSC and all required clocks
  running), a block that answers as an xHCI with a sane identity, and an SMMU stream that is bypass or that maps q1n1's DMA
  arena (or no SMMU in the way), with no fault.
- **NO-GO** for (iv). (iii) only as the M10 experiment. (v) yes, in parallel.

**Stop criteria.** Each is a point at which work on the stick writer stops and the plan is reviewed, not retried.

- **SC1. Stop criterion:** S0 shows the stick's controller GATED-OFF or PARTIAL (after one boot that overrides the
  conservative rule), FAULT, or NOT-XHCI at EL2. The next step would write GCC, GDSC or PHY registers, which nothing here
  authorizes.
- **SC2. Stop criterion:** the stick's SMMU stream is FAULT, or translates without a context that maps q1n1's arena (S0); or
  S1 or S4 show data that cannot be explained except by DMA redirection. Fixing it needs SMMU writes, and DMA through a wrong
  mapping can write outside the arena.
- **SC3. Stop criterion:** five boots at S1 without a No-Op command completing, or two boots with an unexplained Host System
  Error.
- **SC4. Stop criterion:** INQUIRY or READ CAPACITY failing in three consecutive boots after a No-Op success; or the stick
  exposes only a UAS interface (S2).
- **SC5. Stop criterion:** any verified write outside the log region, or any damage to the stick's file system found by the
  Mac verifier or `fsck_msdos -n` on an unmounted copy. All stick writes stop until the cause is found.
- **SC6. Stop criterion:** more than 25 hardware boots spent on B3 without reaching S5. The operator decides whether the
  remaining benefit (dozens of boots without transcription) still pays for it.

Fallbacks while any of these holds: option (v), the M10 experiment, and the panel as today.

## 5. (c) Safety analysis

The aim is that a bad run is harmless. Nothing in this plan writes the internal NVMe, firmware, NVRAM, clocks or voltages. The
only new write target is a log region on a removable boot stick, behind guards that fail closed. Hardware work needs one
hardware client at a time and a documented stop path (AGENTS.md section 5).

### 5.1 What can go wrong, and the guard

| # | Failure | Guard | Proven by |
| --- | --- | --- | --- |
| F1 | A write lands outside the region (wrong map, arithmetic error, a stale map after the file was replaced) | The API takes an index; the LBA is `start + index` in 64-bit inside the map's region; the target sector must still hold its own tag (magic, index, nonce) before its first write; the header sector is never written; the map is parsed with policy before anything | EXPERIMENTAL: C driver (hostile maps, off-by-one in both directions, a map into the file system, forged header over foreign data, a foreign sector in the middle), a forbidden-range counter that must stay 0, an end-to-end stale-map test, seeded bugs |
| F2 | The stick's FAT or boot sector is damaged so it no longer boots | The writer can only touch sectors that held our tag, and file system sectors never do; no FAT code exists at EL2. Residual: a defect in the guard itself, or a wrong LBA in a garbled CBW (F6) | As F1, and the Mac verifier plus `fsck_msdos -n` after the first writes (SC5) |
| F3 | A write reaches a device that is not the stick (internal NVMe, other media) | The driver knows only the stick's own endpoints; q1n1 has no NVMe code on this path; the stick's identity is capacity plus nonce | By construction; the capacity and nonce checks are EXPERIMENTAL |
| F4 | The bus or controller hangs | Every wait has a deadline; a command that needs reset recovery is never followed by another until recovery completes; 4,096 writes per boot; the survey prints NEXT before each block. A hang still ends the boot: power-cycle | The fault matrix of the C driver (stalls, timeouts, wrong tag, short and invalid status, wedge); real xHCI hangs are NOT RUN |
| F5 | DMA through an unconfigured SMMU faults, or worse, is redirected into another page | S0 reads the stream state before anything is written; the S1 No-Op needs no data; SC2 stops on FAULT or on a translation that does not cover the arena | UNIMPLEMENTED. A DMA-bounds assertion in q1n1's xHCI simulator (every DMA address must fall inside the arena) is recommended before S1 |
| F6 | Cache incoherence: the xHC reads stale TRBs or a stale CBW, or q1n1 reads stale data. The worst case is a garbled WRITE(10) CDB with a wrong LBA, which no tag can see because the target is not ours | Coherence is proven before any write: S1 No-Op, then S4's verified READs in three consecutive boots, whose CBW LBA must be right for the header to come back. If it is not, add cache maintenance by virtual address around every DMA buffer (about 30 lines) and repeat S4. The CBW is built in a local buffer and copied once | UNIMPLEMENTED (the model builds CBWs correctly; whether the xHC sees them is NOT RUN) |
| F7 | Interrupts stay masked | Everything is polled with deadlines; no handler exists | By construction |
| F8 | A torn write (hang or power loss mid-sector) | Each sector carries a CRC32 over its tag and used bytes; the Mac reports torn sectors; the next boot's scan treats an owned but invalid sector as the end | EXPERIMENTAL: codec, scan and decode tests |
| F9 | A stale, foreign or swapped stick | The nonce in the map and header, the capacity (three sources), the partition start | EXPERIMENTAL |
| F10 | Write protection, medium error, unit not ready | SCSI sense is decoded; DATA PROTECT disarms with its own reason; UNIT ATTENTION and NOT READY are retried at most 4 times | EXPERIMENTAL: fake device |
| F11 | Wear and runaway loops | 4,096 sector writes per boot | EXPERIMENTAL |
| F12 | A durability claim that is false | FUA on every write, read-back compare, SYNCHRONIZE CACHE at flush; the marker LOG FLUSHED only after all three; the counters say whether the sync was refused | EXPERIMENTAL: fake device with dropped and corrupted writes |
| F13 | The firmware file system's cache holds stale data for the region | q1n1 only reads the log file before ExitBootServices and never writes it through the file system; after ExitBootServices the firmware drivers are gone | By construction |
| F14 | Two hardware clients at once | One hardware client at a time (AGENTS.md section 5); after ExitBootServices the firmware's USB driver is gone, and S1 halts or resets the xHC before using it | Process |
| F15 | A destructive-capable step is taken without authorization | Authorization points A1 (the first write to an xHC register at EL2, S1) and A2 (the first sector write to the stick, S5) need the operator's explicit sign-off; nothing in this study grants them | Process |
| F16 | Identifiers leak into logs or files | The survey and the log print geometry, classes and states, never serial numbers, vendor or product strings; no file here carries a machine or device identifier | Test: `test_no_machine_identifying_strings` |
| F17 | A Mac tool opens the wrong thing | Images only; `/Volumes`, `/dev`, `..`, symlinks, mount points and existing outputs refused; an unverified output is deleted | EXPERIMENTAL: refusal tests, and parity with the staging tool's refusals |
| F18 | An old log is mistaken for a new one | Boot ids and sequence numbers; the scan picks the next boot id | EXPERIMENTAL: two-boot and three-boot tests |

### 5.2 Recovery and quiescing

- **Stop path.** The writer disarms itself on the first failure and the boot continues; the panel says why. To stop everything,
  power-cycle the unit.
- **Recovery.** Power-cycle, then rebuild the stick from the Mac with `prepare` (W1b) and write it with the operating
  system's tools. No recovery step involves the internal disk. A stick whose log region was damaged is rebuilt the same way.
- **Before the first write (S5),** copy the stick's image head (the first few hundred MiB) with OS tools, and keep it, so
  that SC5 can be judged by comparison.

## 6. (d) The staged bring-up plan

Every stage is observed as panel text until the writer works (S5), and every stage has an acceptance criterion and a stop
criterion. Code estimates are lines of C in q1n1 including comments, new code only (INFERRED, no data); the BOT and SCSI
model (about 460 lines) and the guarded writer (about 430 lines) are copied from `tools/` and already host-tested. Boot
counts are ranges, with the assumptions below the table.

| Stage | Scope | New q1n1 code | Hardware boots | Authorization |
| --- | --- | --- | --- | --- |
| S0 | Read-only survey: protocol facts at P2, MMIO and DRAM loads at EL2 | 500 to 650 | 2 to 4 | none (read-only) |
| S1 | xHC halt or reset and re-init with q1n1's own rings, port detection, No-Op | 250 to 330 | 2 to 5 | A1 |
| S1.5 | Hub support (only if needed) | 250 to 350 | 2 to 4 more | A1 |
| S2 | Enumerate and configure the stick | 150 to 200 | 1 to 3 | A1 |
| S3 | INQUIRY and READ CAPACITY | about 200 | 1 to 3 | A1 |
| S4 | Verified READ of the log header and tags | about 120 | 1 to 2 | A1 |
| S5 | First WRITE with read-back | about 60 | 1 to 3 | A1 and A2 |
| S6 | The log API | about 200 | 1 to 3 | A1 and A2 |
| Total | without S1.5 | 1,480 to 1,760 | 9 to 23 (most likely 12 to 16) | |

**Assumptions behind the boot ranges (INFERRED).** A boot is one hardware session with the stick staged beforehand. The low end
assumes the stage works the first time. The high end assumes one defect per stage that the host cannot see (a timing, a stick
quirk, a platform surprise), and for S0 a block that faults or hangs once. Fixes are developed and host-tested between boots,
where possible in q1n1's existing native xHCI simulator (section 7). Two stages may share a boot when the second stage's
lines are printed only if the first succeeded (S2 with S3). S4 and S5 may not share a boot: the coherence gate of F6 and the
authorization A2 sit between them. The ranges exclude building and staging time, the M10 experiment, the camera track, and
S1.5.

**S0. Read-only survey (the first deliverable of B2).**

- *Scope:* the lines of Appendix A, at P2 (boot services) and at EL2. Gated by `SNAP_PLATFORM_SP11` plus a runtime option
  (UNIMPLEMENTED).
- *Acceptance:* every line through VERDICT is printed for both controllers without a hang or an unguarded fault, and the
  stick's controller has a verdict. A verdict of STICK-PRESENT-HALTED, STICK-PRESENT-RUNNING or IDLE with gate ON and an SMMU
  stream that is not FAULT passes to S1. GATED-OFF, FAULT or NOT-XHCI is SC1.
- *Observed by:* one or two photographs. This is the last time registers are transcribed by eye for this question.
- *Stop criterion:* SC1. If the same block hangs in two boots, skip it and record it UNKNOWN; if that block is the stick's own
  controller, SC1 applies.
- *Code:* 500 to 650 lines: protocol reads at P2 about 150 (device path walk, Block I/O, USB I/O, PCI I/O), EL2 reads, the slot
  walk, the SMMU scan and the formatting about 350 to 500. It reuses `live_guarded`, `q1n1_mmu_attr`, `out`, `hex` and `field`.
  A native test with a simulated register bank, in the style of q1n1's `tools/test-q1n1-xhci.c`, should precede hardware.
- *Boots:* 2 to 4 (a surprise in a block costs one boot; an XPU violation that resets the SoC costs one and a power-cycle).

**S1. xHCI halt or reset and re-init at EL2 with our own rings, and detection of the stick's port.**

- *Scope, two variants chosen by what S0 shows.* **S1b, reset and re-enumerate:** q1n1's `xhci_init` as it exists (stop, host
  controller reset, own DCBAA, command ring and event ring), a No-Op command, and root port detection. The reset erases the
  firmware's slot state, so a stick behind a hub is gone until a hub is enumerated (S1.5). **S1a, adopt the firmware's slot:**
  no reset; keep the firmware's DCBAA and slot contexts, give the controller q1n1's own command and event rings, set Run, resume
  the ports, then stop the stick's endpoints and set their dequeue pointers to q1n1's rings. It avoids hub code entirely, but
  needs the firmware's contexts intact (S0 line FWRINGS with INRAM=1) and the link alive; its feasibility is UNKNOWN and it
  is the riskier variant (writes into state the controller owns). The study recommends S1b with a direct connection first, and
  S1a only if the stick must stay behind a hub.
- *Acceptance:* a No-Op Command Completion event with a success code (proves DMA both ways, the event ring and coherence of TRBs;
  the host-link study's G12 checkpoint, M6b), and for S1b a connection on the right root port after the reset. Lines
  `STK S1 NOOP` and `STK S1 PORT`.
- *Stop criterion:* SC3 and SC2.
- *Code:* 250 to 330 lines: the No-Op, Stop and Reset Endpoint and Set TR Dequeue Pointer (needed from S3 for stall recovery),
  an exact-length bulk OUT flag (a Bulk-Only data stage ends by the length the CBW announced; the extra zero-length packet that
  `xhci_send` appends when the length is a multiple of 512 can be read by a device as a malformed CBW, INFERRED), a
  cache-maintenance fallback (about 30), the flow and its lines.
- *Boots:* 2 to 5. *Authorization:* A1, the first xHC register write at EL2.

**S1.5. Hub support (conditional).** Needed only if the stick is behind a hub, S1a is not feasible, and the operator cannot plug
it in directly. Slot context route string and hub fields, the hub class requests (descriptor, port power, port reset, port
status), port-change polling, Address Device through the hub. A high-speed stick behind a high-speed hub needs no
transaction translator; a SuperSpeed hub needs its depth set. About 250 to 350 lines and 2 to 4 more boots. The later CDC-NCM
link behind a dock needs the same code (G03).

**S2. Enumeration and configuration of the stick.**

- *Scope:* Address Device, device and configuration descriptors, find the interface with class `08`, subclass `06`, protocol
  `50` and its two bulk endpoints, Set Configuration, Configure Endpoint, GET_MAX_LUN (a stall is read as LUN 0).
- *Acceptance:* the panel line `STK S2 BOT` (interface, endpoint addresses, packet size, speed, LUN count) equals line USBIO of
  P2 (class, subclass, protocol, endpoint addresses, packet size): a cheap integrity cross-check against what firmware saw.
- *Stop criterion:* SC4 (only a UAS interface). *Code:* 150 to 200 lines (a descriptor parser; `xhci_address_device`,
  `xhci_control` and `xhci_configure_endpoints` exist). *Boots:* 1 to 3.

**S3. SCSI INQUIRY and READ CAPACITY.**

- *Scope:* copy `tools/sp11-stick-bot.[ch]` into q1n1 and write its `bot_ops` glue over the xHCI transfer functions (bulk OUT
  of exactly the length, bulk IN, endpoint halt clear, Bulk-Only reset). TEST UNIT READY, INQUIRY (direct-access devices
  only), READ CAPACITY(10) (refuses a capacity that needs READ CAPACITY(16); requires 512-byte blocks).
- *Acceptance:* `STK S3 SCSI` shows type 00, the last LBA and the block size, and `CAP=MATCH`: READ CAPACITY(10) last LBA + 1
  equals the Block I/O capacity of P2 and the map's capacity.
- *Stop criterion:* SC4. *Code:* about 200 lines of glue. *Boots:* 1 to 3 (real sticks differ from the fake in ways nobody has
  seen: a unit attention after reset, a stalled GET_MAX_LUN, a lying residue).

**S4. A verified READ of the log region header.**

- *Scope:* parse the map, READ(10) one sector at the start LBA and compare it with the copy read through the file system at
  P2 byte for byte; read a few tagged sectors.
- *Acceptance:* `STK S4 HEADER OK` and `STK S4 TAGS OK` in three consecutive boots. The header can only come back if the CBW's
  LBA field reached the device intact, so this is the coherence gate of F6.
- *Stop criterion:* a mismatch with correct LBAs is SC2. *Code:* about 120 lines. *Boots:* 1 to 2.

**S5. A WRITE of a log record with read-back.**

- *Scope:* `slog_open`, append a short text, flush: pre-read the tag, WRITE(10) with FUA, READ(10) back and compare,
  SYNCHRONIZE CACHE(10).
- *Acceptance, on the panel:* `STK S5 WRITE OK` with the write and sync counters. *On the Mac, the real acceptance:* `decode`
  on a copy of `SNAPLOG.BIN` shows the text; `verify --allow-written` accepts an image head; `fsck_msdos -n` on an unmounted
  copy is clean; and a comparison with the copy taken before the run shows no changed sector outside the region.
- *Stop criterion:* SC5. *Code:* about 60 lines. *Boots:* 1 to 3. *Authorization:* A2.

**S6. The log API that q1n1 and later tests call.**

- *Scope:* `stick_log_put()` (a non-blocking RAM ring, single producer, bounded, no allocation), `stick_log_drain()` and
  `stick_log_flush()` at safe points (append record, flush), the LOG FLUSHED marker, boot-id continuity from the P2 scan, a
  runtime option, and a mirror of the panel text so that the page stays the primary channel.
- *Acceptance:* three consecutive boots whose logs decode in order on the Mac with boot ids 1, 2 and 3 and no torn sector, and
  one later test that uses the API instead of the panel.
- *Stop criterion:* SC6. *Code:* about 200 lines. *Boots:* 1 to 3.

**Risk to record for S1b.** q1n1's own comment in `xhci_release` says that on the A16 a device left configured when the
controller is reset under it can refuse to enumerate again until it is replugged (an A16 observation with another peripheral,
not verified here; INFERRED to apply). The firmware left the stick configured, so S1b may need the operator to replug the
stick after the first reset; the survey's PORT lines show whether it enumerates.

## 7. (e) What is generic and reusable, and what is A16-specific

The inventory is [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md) section 2 (ids I01 to I24, N01, N02, G01 to G14, checked
against q1n1 `7872398694ca8322e9cdebcc4ca2380491295f24`). This section re-reads the files at q1n1
`ce219120573b23c8308d60df321800fecf455140` (the SP11 boot branch) for the stick, so that stage S1 serves the later CDC-NCM link.

| Piece in q1n1 `platform/uefi` | Class | For the stick (S1 to S5) | For the later CDC-NCM link | Gap or work |
| --- | --- | --- | --- | --- |
| `xhci_init`, `ring_init`, `command`, `xhci_poll`, `xhci_stop`, `xhci_running` | GENERIC (G01, G02) | S1b as it is: stop, host controller reset, own DCBAA, command and event rings, scratchpads, Run | the same | No No-Op checkpoint (G12). DMA assumed identity-mapped and coherent (G04). A "start without reset" entry for S1a is new |
| `xhci_wait_port`, port-status handling | GENERIC | S1b | the same | Root-port devices only |
| `xhci_address_device` | GENERIC with a limit (G03) | S2 | the same | The slot context carries only the root-hub port number: no route string, hub bit or transaction translator (S1.5) |
| `xhci_control`, `xhci_configure_endpoints` | GENERIC | S2 | the same | None known |
| `xhci_send`, `xhci_send_start`, `xhci_post`, `xhci_reap` | GENERIC | S3 to S5 | the same | `xhci_send` appends a zero-length packet when the length is a multiple of 512 and `xhci_send_start` refuses such a length: Bulk-Only needs exact lengths. No stall recovery: no Stop Endpoint, Reset Endpoint or Set TR Dequeue Pointer |
| Cache maintenance | absent | a fallback if coherence fails (F6) | the same | about 30 lines |
| `ncm.c`, the CDC-NCM host | GENERIC (G05) | not used; `get_descriptor` and `find_function` are the pattern for S2's descriptor parser | the link itself | None for the stick |
| `usb-ports.c` port manager and the `sp11-usb-ports.h` table | A16 values, GENERIC logic, with the gated SP11 path (I01 to I06, G09 to G11) | The controller base and glue offset come from the generated table once the DWC3 id is measured (X2), not from constants; the halt-first rule (G11) stays | the same | `emit-c` still needs the measured id |
| `usb-ebs-prep.c`, `usb-ebs.h` | A16-SPECIFIC and FIRMWARE-COUPLED (I07 to I09, I14 to I16): it drives the ASUS firmware's USB Function driver | Not reusable. Its pattern, a snapshot at P2 compared with EL2, is what S0 does | n/a | None |
| `qcom-dwc3.c`, `usb1-device.c` (device-mode gadget) | A16-SPECIFIC | Not used: the Denali controllers are host only | n/a | |
| `usb-probe.c`, the passive protocol inventory | GENERIC (G13) | A zero-risk first look at which USB protocols firmware publishes | | None |
| `tools/test-q1n1-xhci.c` in q1n1 (872 lines), a native xHCI simulator | GENERIC | The bench for S1 to S3: extend it with a BOT device (this repository's fake in `tools/sp11-stick-fakedev.h` is the model) and a DMA-bounds assertion (F5) | the same | **No new TRB-ring model is needed, and none is built here** |

**What S1 and S2 give the CDC-NCM link:** reset and own rings, the No-Op checkpoint, stall recovery, an exact-length OUT
flag, the cache-maintenance fallback, polled event handling with deadlines, and (S1.5) hub and route-string support, which an
NCM adapter behind a dock needs too (G03). **What is stick-only:** the BOT and SCSI layer and the log writer.

**What would make a host model of the TRB rings worth building:** nothing yet. The existing simulator covers the ring
arithmetic; what it lacks is a mass-storage device behind it, which is test code on top of it, not a second implementation.
If S1 shows event-ring or cycle-bit defects that the simulator did not predict, extend the simulator, not this repository.

## 8. Host groundwork delivered

All of it is host-only and EXPERIMENTAL. No file here is linked into any build, and with `SNAP_PLATFORM_SP11` unset the C
files preprocess to nothing (tested).

| Path | What it is |
| --- | --- |
| `tools/sp11_fatvol.py` | FAT16 and FAT32 access on image files by `pread` and `pwrite` (never a device or a link): MBR or bare volume, chain walking with loop and bounds checks, allocation of contiguous runs, mirrored FAT copies, FSInfo, directory entries, sparse FAT32 formatting. Refusals have stable names (`fat12_unsupported`, `gpt_unsupported`, `unsupported_sector_size`, `chain_loops`, ...) |
| `tools/sp11_stick_logmap.py` | `build`, `prepare`, `verify`, `read-log`, `decode`; the format codecs; the path refusals of the staging tool; deletion of an unverified output |
| `tools/sp11-stick-bot.h`, `.c` | Pure C, freestanding style: BOT CBW and CSW, the three setup packets, TEST UNIT READY, INQUIRY, REQUEST SENSE, READ CAPACITY(10), READ(10), WRITE(10), SYNCHRONIZE CACHE(10), stall handling, Reset Recovery |
| `tools/sp11-stick-log.h`, `.c` | CRC32, map, header and sector codecs, `slog_scan`, and the guarded writer |
| `tools/sp11-stick-fakedev.h` | A fault-injecting fake BOT device (test support): stalls, short data, a lying residue, wrong tag or signature, short or reserved status, phase error, timeouts, a wedge, capacity lies, write protection, dropped and corrupted writes, and a counter of writes into forbidden ranges |
| `tools/test-sp11-stick-bot.c`, `tools/test-sp11-stick-log.c` | Native drivers (AddressSanitizer and UndefinedBehaviorSanitizer): vectors, a fault matrix of 31 cases, recovery, hostile maps, device failures, budget, wrap, two boots, seeded fuzz; plus oracle modes the Python driver talks to |
| `tools/test-sp11-stick-log.py` | The one command (below). It is larger than the other tests in `tools/`; AGENTS.md limits C and C++ implementation files, not tests, but it should be split if it grows |

**Command:** `python3 -B tools/test-sp11-stick-log.py`. It needs a C compiler (`cc`); the LLVM tools for the freestanding
AArch64 check; and, optionally, `Q1N1_DIR` (a q1n1 checkout, default `../q1n1`) for the q1n1 facts and
`build/sp11-stick-log/refs/` for the pinned-source citations. Absent optional inputs are reported as SKIP, never as a pass.
Scratch files stay under `build/sp11-stick-log/`.

What passing shows (EXPERIMENTAL):

- The Mac tool and verifier against an independent reader written from the BPB layout, against `probes/uefi/host/fatimg.py`
  for FAT16, and against macOS `fsck_msdos -n` on a bare FAT32 volume and a FAT16 volume (macOS only); faulty images that must
  be rejected: a fragmented file, a wrong cluster size, a map off by one sector either way, wrong signatures (map, header, tag,
  nonce, CRC), a capacity that does not match, a wrong file size, hostile FAT chains, torn and unwritten sectors, GPT, wrong
  sector sizes, protected destinations.
- The C model under sanitizers with the fake device; C and Python encoders and parsers agree on thousands of random and
  mutated inputs; the C writer writes into an image the Python tool made, the Python tool reads it back, and nothing outside
  the region changes; a stale map cannot touch the file system.
- Seeded bugs, one at a time: 28 in the BOT and SCSI layer, 26 in the writer and codecs, 22 in the Python tools. Each must
  be caught, and an unmutated copy run through the same harness must pass.
- Hygiene: licence line, ASCII, size limits for the C files, no libc calls in the model, a freestanding AArch64 object that
  links without libc, no machine strings, the constants block of Appendix C against the code, and the citations of this
  document against the pinned sources.

What passing does not show: that a real stick answers as the fake does; that q1n1's xHCI driver can carry these bytes; that
any claim in section 3 about the unit is true. Hardware: NOT RUN.

## 9. Evidence summary

| Claim | Label | Where |
| --- | --- | --- |
| The stick's path has two USB nodes, so a device (a hub) is in front of it | INFERRED (path OBSERVED; EDK2 numbering VERIFIED by reading) | 3.1 |
| Which of the two Denali controllers the stick uses | UNKNOWN | 3.2 |
| The xHC is halted at EL2 by firmware's ExitBootServices handler | INFERRED | 3.3 |
| GDSC, clocks, PHY, repeater, SMMU and coherence state at EL2 | UNKNOWN | 3.3 |
| M2, M1, M1b, M1c and M6a are read-only at EL2 | VERIFIED as a classification; not run | 3.5 |
| `ResetSystem` does not work after the bounce | OBSERVED (X1) | 3.4, 4.6 |
| Raw writes into a tagged, pre-allocated region are guarded against wrong maps and device faults | EXPERIMENTAL (host tests) | 4.5, 5 |
| A real stick has a BOT interface and answers like the fake | UNKNOWN / not shown | 4.6, 8 |
| Effort and boot counts | INFERRED (estimates, no data) | 6 |
| q1n1 can address a device behind a hub | VERIFIED false (G03) | 7 |
| Any behaviour of the unit with this design | not established | NOT RUN |

## 10. Pinned sources

| Source | Revision | Licence | Used for |
| --- | --- | --- | --- |
| Linux `arch/arm64/boot/dts/qcom/{hamoa.dtsi,x1-el2.dtso}`, `drivers/clk/qcom/{gcc-x1e80100.c,clk-branch.h,gdsc.c}`, `drivers/iommu/arm/arm-smmu/arm-smmu.h`, `drivers/usb/dwc3/{core.h,dwc3-qcom.c}`, `include/linux/usb/storage.h`, `include/scsi/scsi_proto.h` | `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` | BSD-3-Clause for the device tree files; GPL-2.0 family for the rest (read for facts: offsets, names, constants; no text or code copied) | Sections 3 to 5, Appendices |
| EDK2 `MdeModulePkg/Bus/Pci/XhciDxe/Xhci.c`, `MdeModulePkg/Bus/Usb/UsbBusDxe/UsbEnumer.c`, `MdeModulePkg/Bus/Usb/UsbMassStorageDxe/{UsbMassBot.h,UsbMassImpl.c}` | tag `edk2-stable202402` | BSD-2-Clause-Patent (read for facts, none copied) | Sections 3.1, 3.3, 4.6 |
| q1n1 (`platform/uefi`, `tools/test-q1n1-xhci.c`) | `ce219120573b23c8308d60df321800fecf455140`; the host-link inventory uses `7872398694ca8322e9cdebcc4ca2380491295f24` | MIT | Section 7. Function names, not line numbers, are cited |
| slbounce fork, `src/bounce_main.c` | `380f25a334d4afab90a92b695cf6a8b85d106085` | as the repository | Section 3.4 |

The cited files are fetched into `build/sp11-stick-log/refs/` (git-ignored) and the test reads every cited line back. SHA-256 of
the fetched bytes (the `hamoa.dtsi` digest is the one recorded in [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md)
section 10):

| File | SHA-256 |
| --- | --- |
| `hamoa.dtsi` | `d5bb1f7ce37c721d75fb5b4cffc8f58bed4d3ef9786a231a0deca05e917529e0` |
| `x1-el2.dtso` | `d6a9666bd89e8787223ea2d14b8cb798669647da6bbf0f87b835719d409e2df1` |
| `gcc-x1e80100.c` | `f1c3779c7bc273dfe170ea2ff4d40f975083ee149e3b02a4f42d5939596eb775` |
| `clk-branch.h` | `bac7dcf51a0da5b27eaa0d4dc276f540fec80013bd4862cfbbc115a50161ef72` |
| `gdsc.c` | `a5a73fcc890f658b48a1e2dc015f3d47316d77b8c7853bbf999828f25a8c86b1` |
| `arm-smmu.h` | `b7221ee9b767594ba79cfc139fca838236f81ba949fb5e0be81e31ef42b1911c` |
| `core.h` | `525c1d5d8d0e501f4a92ddd6232961542b5c04521f29a50b8acf9ee86c41373c` |
| `dwc3-qcom.c` | `1550c5cf584c05b8074c85e31b4dbb96c67c81361ac036fd924d9ba7ec10672e` |
| `storage.h` | `b0a64313842229a8a7ba67e3fa7d4e3fb560fea1dc402f6b5c85af8a87de3b22` |
| `scsi_proto.h` | `ba7204243fbc50b11095c90d2a7fc2ed8b46d5efc6c4ae05b7fbd0890d480199` |
| `Xhci.c` | `565f42f17017abbb296ba2646344903a28738e8ce079fbdfd570c45512cec166` |
| `UsbEnumer.c` | `fac83fe8039a38b16de7386b280506da06d425af63e96f7a4f598ca34113de83` |
| `UsbMassBot.h` | `ced438d4872f00617665487f21ebdef5d645fe03b9c9f74006ef28cc31c1beb4` |
| `UsbMassImpl.c` | `f1cd283646c8a9449545c1c632c426dde06ccbf1791b437166ccf243c7c5c088` |

## 11. Open questions

The first two need the operator.

1. **Q1. Hub.** Is the stick plugged straight into the unit's USB-C port, or through an adapter, dock or hub? If it is
   direct, the two-node path of 3.1 is a surprise worth a photograph of the setup. If it is behind a hub, may the next sessions
   plug it straight in? (If not: S1a or S1.5.) **Answered 2026-11-14 (operator): through an adapter or hub.** Whether the
   stick may be plugged in directly is not yet asked; S1a or S1.5 stay on the table.
2. **Q2. Which port.** Which of the unit's USB-C ports is the stick in, so that D3 of 3.2 has a starting point?
3. **Q3. Imaging.** Can the operator write a disk image to the stick (W1b), or must the log region be created by copying files
   (W2, which needs a q1n1 FAT walker)? Will the operator keep the stick on an MBR disk (GPT is refused)?
4. **Q4. Authorization.** May A1 (the first write to an xHC register at EL2, S1) and A2 (the first sector write to the stick,
   S5) be granted, and when? Nothing in this study grants them.
5. **Q5. Gate name.** The model is gated by `SNAP_PLATFORM_SP11`; a runtime writer needs its own gate and a boot option. Name
   them before S0 code is written.
6. **Q6. Camera.** Is a fixed camera, with a Mac-side decoder, worth setting up in parallel (alternative (v))?
7. **Q7. Retention.** Is the M10 experiment to run now, since it is cheap and independent?

## Appendix A. The page lines of the first survey boot (S0; EXPERIMENTAL in the q1n1 fork, hardware NOT RUN)

The survey as built differs from this table in a few named places (extra verdict names, extra fields and lines, the baseline
read through the firmware's PCI I/O instead of raw MMIO); the fork's `docs/SP11-STK-SURVEY.md` lists them.

q1n1's page font has capital letters, digits and `: - . / = ? ( ) _` only (lower case is folded to upper case), and its
`hex()` prints `0X` and 16 digits. The survey lines are `key=value` text, numbers as `0X` and the fewest digits (decimal where
a `<n>` is shown), flags as 0 or 1, `?` for a value that could not be read, `NONE` for absent. Each line is at most 100
characters. A line that cannot be produced prints its topic with `SKIPPED=<reason>`. The page already has about 14 lines of EL2
state; the survey adds at most 60, with at most 4 ports and 3 slots per controller, so one or two photographs hold it. Every
line is also mirrored to the UART when one exists (`out()` does). None prints a serial number, vendor string or product
string.

**P2, before ExitBootServices (firmware protocols; no MMIO except the optional baseline). Remembered and printed again at EL2.**

| Line | Fields | Meaning |
| --- | --- | --- |
| `STK P2 BUILD` | `GATE=1 REV=<8 hex>` | The build identity: the gate and the q1n1 revision |
| `STK P2 SELF` | `PATH=PCI(81.0)/USB(1.0)/USB(2.0)/HD(1.MBR)` | The device path q1n1 was loaded from, as `PCI(device.function)`, `USB(port.interface)`, `HD(partition.type)`; any other node as `T<type>.<subtype>` in hex |
| `STK P2 HUB` | `USBNODES=<n> ROOTIDX=<n> HUBPORTIDX=<n>` | The number of USB nodes in that path and their zero-based port indexes. More than one node means a hub in front of the stick (3.1, INFERRED rule) |
| `STK P2 BLK` | `LASTLBA=<hex> BLKSZ=<hex> REMOVABLE=<0/1> READONLY=<0/1> PARTSTART=<hex> PARTSIZE=<hex>` | Block I/O media of the raw disk (capacity source 1 of 3) and the partition from the `HD` node |
| `STK P2 USBIO` | `BCDUSB=<hex> CLASS=<hex> SUB=<hex> PROTO=<hex> EPIN=<hex> EPOUT=<hex> MPS=<hex>` | The stick's mass-storage interface from the USB I/O protocol: bcdUSB, class, subclass, protocol, bulk endpoint addresses, packet size. Protocol 50 is BOT |
| `STK P2 HC` | `N=<n>`, then per handle `HC<i> BAR0=<hex> CLASS=<hex> LOC=<seg>.<bus>.<dev>.<fn>` or `HC<i> PCIIO=NONE` | The USB host controller handles and, where firmware exposes it, their PCI I/O view: BAR 0 is the controller's base (discriminator D1 of 3.2) |
| `STK P2 U<i>` | `CAPLEN=<hex> USBCMD=<hex> USBSTS=<hex> CRCR=<hex> DCBAAP=<hex> PORTSC1=<hex> PORTSC2=<hex>` | Optional baseline: the operational registers of the controller D1 identified, read by MMIO while firmware still runs it. EL2 lines are compared with it |

**EL2, after the bounce (read-only; each block is preceded by `STK NEXT <BLOCK>`).**

| Line | Fields | Meaning |
| --- | --- | --- |
| `STK NEXT` | `<BLOCK>` | The block about to be read: MAP, GCC, U0, U1, FWRINGS0, FWRINGS1, SMMU. After a hang the last NEXT names the culprit |
| `STK P3 MAP` | `GCC=<a> SMMU=<a> U0=<a> U1=<a> RAM=<a>` | The stage-1 attribute class of each window from `q1n1_mmu_attr`: DEVICE, NORMAL, NONE (unmapped) or `?`. No device is accessed |
| `STK P3 GCC` | `U0 GDSCR=<hex> PWR=<0/1> SWC=<0/1> MASTER=<hex> UTMI=<hex> SLEEP=<hex> CFGNOC=<hex> AGGRE=<hex>`; the same for `U1`; `SHARED NOC=<hex> SYS=<hex> NORTH=<hex> SOUTH=<hex>` | GCC loads, offsets below. PWR is GDSCR bit 31 (power on, `gdsc.c:21`); SWC is bit 0 (software collapse, `gdsc.c:27`). A branch-clock value with bit 31 set means the clock is OFF (`clk-branch.h:63`) |
| `STK P3 GATE` | `U0=<ON/PARTIAL/OFF> U1=<ON/PARTIAL/OFF>` | ON: PWR=1 and all nine clocks (five of the controller, four shared) running. OFF: PWR=0. PARTIAL: PWR=1 and a clock off. Only ON controllers are read |
| `STK P3 U<i> ID` | `CAPLEN=<hex> HCIVER=<hex> SLOTS=<n> PORTS=<n> HCS2=<hex> HCC1=<hex> DBOFF=<hex> RTSOFF=<hex>` | xHCI capability registers (M1) |
| `STK P3 U<i> XECP` | `PROTO<k>=USB<2/3> REV=<hex> FIRST=<n> COUNT=<n>` | The supported-protocol capabilities: which port numbers are USB2 and which USB3 (M1c; a bounded walk) |
| `STK P3 U<i> OP` | `USBCMD=<hex> USBSTS=<hex> RS=<0/1> HCH=<0/1> HSE=<0/1> CNR=<0/1> CRCR=<hex> DCBAAP=<hex> CONFIG=<hex>` | Operational registers. HCH=1 means halted, as 3.3 expects |
| `STK P3 U<i> PORT<n>` | `SC=<hex> CCS=<0/1> PED=<0/1> PP=<0/1> PLS=<n> SPEED=<n>` | Each root port: connected, enabled, powered, link state, speed |
| `STK P3 U<i> DWC3` | `GSNPSID=<hex> GCTL=<hex> PRTCAPDIR=<n> GSTS=<hex> CURMOD=<n> GBUSERR=<hex> USB2CFG=<hex> USB3PIPE=<hex>` | DWC3 identity and role; host is 1 (`core.h:265`, `core.h:297`) |
| `STK P3 U<i> GLUE` | `HSPHY=<hex> SSPHY=<hex>` | The glue registers at `+0x10` and `+0x30` of the glue block (`dwc3-qcom.c:26`, `dwc3-qcom.c:30`) |
| `STK P3 U<i> FWRINGS` | `DCBAAP=<hex> CRCR=<hex> ERSTBA=<hex> ERDP=<hex> INRAM=<0/1>` | The firmware's ring pointers and whether each lies in RAM per the memory map and is aligned (M1b) |
| `STK P3 U<i> SLOT<n>` | `CTX=<hex> ROUTE=<hex> HUB=<0/1> ROOTPORT=<n> SPEED=<n> STATE=<n> ADDR=<n> EP<d>=<state>.<type>.<mps>` | Each non-empty firmware slot: route string, hub flag, root port, speed, slot state, address; each endpoint context's state, type and packet size. The field positions follow the xHCI specification (not fetched here) and must be checked against the context macros in q1n1's `xhci.c`, where the root port sits at bits 23:16 of the second slot dword |
| `STK P3 SMMU` | `ID0=<hex> NUMSMRG=<n> SCR0=<hex> USFCFG=<0/1> CLIENTPD=<0/1> EXID=<0/1> GFSR=<hex>` | The SMMU global registers (M6a) |
| `STK P3 SMMU S1420`, `S1460` | `SMR=<n or NONE> TYPE=<TRANS/BYPASS/FAULT/NONE> CBNDX=<n>` | The stream's matching entry from a bounded scan and its S2CR type (0 translate, 1 bypass, 2 fault) |
| `STK P3 VERDICT` | `U0=<v> U1=<v> SMMU=<w>` | Per controller `v`: GATED-OFF, FAULT (a guarded abort), NOT-XHCI, STICK-PRESENT-RUNNING, STICK-PRESENT-HALTED, IDLE (answers, nothing connected), INCONSISTENT (readings that the xHCI rules forbid, such as CCS=1 with PP=0). STICK-PRESENT means a root port with CCS=1 and a firmware slot on it; RUNNING or HALTED follows HCH. `w`: BYPASS, TRANS, FAULT or UNREAD |

Registers read (all loads; absolute address = base + offset):

| Block | Base | Registers (offset from the base) | Source |
| --- | --- | --- | --- |
| GCC | `0x00100000`, size `0x200000` (`hamoa.dtsi:758`, `hamoa.dtsi:760`) | GDSCR primary `0x39004` (`gcc-x1e80100.c:6740`); GDSCR secondary `0xa1004` (`gcc-x1e80100.c:6752`) | VERIFIED as what the file says |
| GCC, primary controller clocks | as above | master `0x39018` (`gcc-x1e80100.c:5369`); mock_utmi `0x39028` (`gcc-x1e80100.c:5387`); sleep `0x39024` (`gcc-x1e80100.c:5405`); cfg_noc `0x3908c` (`gcc-x1e80100.c:2801`); aggre `0x39090` (`gcc-x1e80100.c:2493`) | as above |
| GCC, secondary controller clocks | as above | master `0xa1018` (`gcc-x1e80100.c:5418`); mock_utmi `0xa1028` (`gcc-x1e80100.c:5436`); sleep `0xa1024` (`gcc-x1e80100.c:5454`); cfg_noc `0xa108c` (`gcc-x1e80100.c:2821`); aggre `0xa1090` (`gcc-x1e80100.c:2513`) | as above |
| GCC, shared NoC clocks | as above | aggre_usb_noc `0x2d034` (`gcc-x1e80100.c:2613`); sys_noc_usb `0x2d014` (`gcc-x1e80100.c:5107`); anoc north `0x2d028` (`gcc-x1e80100.c:2876`); anoc south `0x2d02c` (`gcc-x1e80100.c:2891`) | as above |
| xHCI, DWC3 | `0x0a600000` and `0x0a800000` (`hamoa.dtsi:5256`, and `usb_1_ss1` at `hamoa.dtsi:5345`) | capability at `+0`, operational at `+CAPLENGTH`; GCTL `0xc110` (`core.h:102`), GSTS `0xc118` (`core.h:104`), GSNPSID `0xc120` (`core.h:106`), GBUSERRADDR0 `0xc130` (`core.h:110`), GUSB2PHYCFG `0xc200` (`core.h:138`), GUSB3PIPECTL `0xc2c0` (`core.h:143`) | VERIFIED as what the files say; the 4 KiB rounding of the window is the host-link study's decision |
| Glue | base + `0xf8800` (`dwc3-qcom.c:41`) | HS_PHY_CTRL `+0x10`, SS_PHY_CTRL `+0x30` | as above |
| SMMU `apps_smmu` | `0x15000000`, size `0x100000` (`hamoa.dtsi:8436`, `hamoa.dtsi:8438`; compatible `hamoa.dtsi:8437`) | sCR0 `+0x0` (`arm-smmu.h:27`); USFCFG (`arm-smmu.h:33`), CLIENTPD (`arm-smmu.h:39`), EXIDENABLE (`arm-smmu.h:36`) | VERIFIED as what the file says |
| SMMU identification | as above | ID0 `+0x20` (`arm-smmu.h:45`): NUMSIDB (`arm-smmu.h:55`), EXIDS (`arm-smmu.h:56`), NUMSMRG (`arm-smmu.h:57`); ID1 `+0x24` (`arm-smmu.h:59`); sGFSR `+0x48` (`arm-smmu.h:83`) | as above |
| SMMU stream tables | as above | SMR(n) `+0x800 + 4n` (`arm-smmu.h:100`): VALID bit 31 (`arm-smmu.h:101`), mask (`arm-smmu.h:102`), id (`arm-smmu.h:103`). S2CR(n) `+0xc00 + 4n` (`arm-smmu.h:105`): TYPE bits 17:16 (`arm-smmu.h:113`), translate (`arm-smmu.h:115`), bypass (`arm-smmu.h:116`), fault (`arm-smmu.h:117`), EXIDVALID (`arm-smmu.h:119`), CBNDX bits 7:0 (`arm-smmu.h:120`) | as above |

The SMR scan matches a stream when the entry is valid (SMR bit 31, or S2CR.EXIDVALID when `EXIDENABLE` is set) and
`((stream ^ id) & ~mask)` is zero over the implemented stream-id bits (ID0.NUMSIDB), and prints the lowest matching index.

## Appendix B. Bulk-Only Transport and SCSI byte layouts (model: `tools/sp11-stick-bot.[ch]`)

Command Block Wrapper, 31 bytes, little endian, sent on the bulk OUT endpoint (`storage.h:62`, `storage.h:63`;
`UsbMassBot.h:21`):

| Offset | Size | Field | Value |
| --- | --- | --- | --- |
| 0 | 4 | dCBWSignature | `0x43425355` ("USBC") |
| 4 | 4 | dCBWTag | chosen by the host and echoed in the CSW; the model increments it and skips 0 |
| 8 | 4 | dCBWDataTransferLength | bytes of the data stage |
| 12 | 1 | bmCBWFlags | bit 7 set: device to host (IN); the other bits zero |
| 13 | 1 | bCBWLUN | bits 3:0 the LUN; bits 7:4 zero |
| 14 | 1 | bCBWCBLength | bits 4:0 the command block length, 1 to 16; bits 7:5 zero |
| 15 | 16 | CBWCB | the command block, zero padded |

Command Status Wrapper, 13 bytes, read from the bulk IN endpoint (`storage.h:75`, `storage.h:76`; `UsbMassBot.h:22`):
`dCSWSignature` `0x53425355` ("USBS") at 0, `dCSWTag` at 4 (must equal the CBW's), `dCSWDataResidue` at 8 (requested minus
processed; above the request is invalid), `bCSWStatus` at 12: 0 passed, 1 failed, 2 phase error (`storage.h:77`,
`storage.h:78`, `storage.h:79`); 3 and above are reserved and refused.

Setup packets (8 bytes: type, request, value, index, length): Get Max LUN `A1 FE 0000 <interface> 0001` (`storage.h:83`;
`UsbMassBot.h:20`; a stall is read as LUN 0); Bulk-Only Mass Storage Reset `21 FF 0000 <interface> 0000` (`storage.h:82`;
`UsbMassBot.h:19`); CLEAR_FEATURE(ENDPOINT_HALT) `02 01 0000 <endpoint address> 0000`.

SCSI command blocks (`scsi_proto.h:21` and following); multi-byte fields are big endian:

| Command | Bytes | Data |
| --- | --- | --- |
| TEST UNIT READY (6) (`scsi_proto.h:21`) | `00 00 00 00 00 00` | none; UNIT ATTENTION and NOT READY are retried up to 4 times |
| REQUEST SENSE (6) (`scsi_proto.h:23`) | `03 00 00 00 12 00` | 18 bytes; fixed format (`70`, `71`) and descriptor format (`72`, `73`) decoded for key, ASC, ASCQ |
| INQUIRY (6) (`scsi_proto.h:34`) | `12 00 00 00 24 00` | 36 bytes; byte 0 bits 4:0 (device type) and bits 7:5 (qualifier) must both be 0 |
| READ CAPACITY(10) (`scsi_proto.h:49`) | `25 00 00 00 00 00 00 00 00 00` | 8 bytes: the last LBA, then the block length. A last LBA of `0xFFFFFFFF` needs READ CAPACITY(16) and is refused; the capacity is last LBA + 1; the block length must be 512 |
| READ(10) (`scsi_proto.h:50`) | `28 FL LBA(4) 00 LEN(2) 00` | `LEN` blocks, 1 to 8 per command; the range is checked in 64 bits against the capacity |
| WRITE(10) (`scsi_proto.h:51`) | `2A FL LBA(4) 00 LEN(2) 00` | as READ(10); `FL` bit 3 is force unit access (`0x08`) |
| SYNCHRONIZE CACHE(10) (`scsi_proto.h:62`) | `35 00 00 00 00 00 00 00 00 00` | none; LBA 0 and 0 blocks mean the whole medium; ILLEGAL REQUEST is tolerated |

Rules the model applies: a stall in the data stage is cleared on that endpoint and the status is read; a stalled status
read is cleared and read once more; an invalid status (wrong length, signature or tag, a reserved status value, a residue above
the request) or a phase error marks the device as needing Reset Recovery (Bulk-Only reset, then clear halt on bulk IN, then on
bulk OUT) and no command is sent until all three succeed; status 1 triggers REQUEST SENSE; a residue other than zero, or fewer
bytes than requested, is a failure.

## Appendix C. Region and map formats (model: `tools/sp11-stick-log.[ch]`, `tools/sp11_stick_logmap.py`)

All fields are little endian. The block below is read by `tools/test-sp11-stick-log.py` and compared with the code.

```text format-constants
MAP_BYTES = 128
HEADER_BYTES = 64
SECTOR_BYTES = 512
PAYLOAD_BYTES = 480
TAG_BYTES = 32
VERSION = 1
MAX_RUNS = 4
WRITE_BUDGET = 4096
MAP_MAGIC = SNAPLMAP
HEADER_MAGIC = SNAPLOG1
SECTOR_MAGIC = SLG1
LOG_FILE = SP11/SNAPLOG.BIN
MAP_FILE = SP11/SNAPLOG.MAP
CBW_BYTES = 31
CSW_BYTES = 13
CBW_SIGNATURE = 0x43425355
CSW_SIGNATURE = 0x53425355
OP_TEST_UNIT_READY = 0x00
OP_REQUEST_SENSE = 0x03
OP_INQUIRY = 0x12
OP_READ_CAPACITY_10 = 0x25
OP_READ_10 = 0x28
OP_WRITE_10 = 0x2A
OP_SYNCHRONIZE_CACHE_10 = 0x35
```

**Region header, sector 0 (64 bytes used, the rest zero):**

| Offset | Size | Field |
| --- | --- | --- |
| 0x00 | 8 | magic `SNAPLOG1` |
| 0x08 | 4 | version (1) |
| 0x0C | 4 | header bytes (64) |
| 0x10 | 8 | nonce (random, never 0) |
| 0x18 | 4 | sector size (512) |
| 0x1C | 4 | region sectors |
| 0x20 | 4 | start LBA, absolute on the stick's block device |
| 0x24 | 4 | partition LBA |
| 0x28 | 4 | capacity in sectors |
| 0x2C | 4 | flags (0) |
| 0x30 | 12 | reserved (0) |
| 0x3C | 4 | CRC32 of bytes 0x00 to 0x3B |

**Log sector, indexes 1 and up (a 32-byte tag, then 480 payload bytes, zero padded):**

| Offset | Size | Field |
| --- | --- | --- |
| 0x00 | 4 | magic `SLG1` |
| 0x04 | 4 | the sector's own index |
| 0x08 | 8 | nonce |
| 0x10 | 4 | boot id (0 in a pristine sector) |
| 0x14 | 4 | sequence number within the boot |
| 0x18 | 2 | used payload bytes (0 to 480) |
| 0x1A | 2 | flags (0) |
| 0x1C | 4 | CRC32 over tag bytes 0x00 to 0x1B followed by the used payload bytes (zlib CRC32) |
| 0x20 | 480 | payload |

A sector is **owned** when magic, index and nonce are right (the writer's rule) and **valid** when, in addition, the used
count is at most 480 and the CRC matches (the reader's rule). The Mac writes every sector of the region pristine: owned and
valid with boot id 0.

**Map file `SP11/SNAPLOG.MAP` (128 bytes):**

| Offset | Size | Field |
| --- | --- | --- |
| 0x00 | 8 | magic `SNAPLMAP` |
| 0x08 | 4 | version (1) |
| 0x0C | 4 | map bytes (128) |
| 0x10 | 8 | nonce |
| 0x18 | 4 | sector size (512) |
| 0x1C | 4 | capacity in sectors |
| 0x20 | 4 | partition LBA |
| 0x24 | 4 | cluster size in sectors (a power of two, 1 to 128) |
| 0x28 | 4 | data area LBA (cluster 2) |
| 0x2C | 4 | region sectors |
| 0x30 | 4 | run count (version 1: exactly 1) |
| 0x34 | 4 | flags (0) |
| 0x38 | 32 | four runs of (start LBA, sectors); unused runs are zero |
| 0x58 | 36 | reserved (0) |
| 0x7C | 4 | CRC32 of bytes 0x00 to 0x7B |

Policy (checked by the C parser, the Python decoder and the verifier): the nonce is not zero; the run's sectors equal the
region's; the run ends inside the capacity (64-bit sum); the capacity is not `0xFFFFFFFF` (a device that needs READ CAPACITY(16));
the run starts on a cluster boundary of the data area and the data area does not precede the partition; and the verifier adds
that the run is exactly the file's clusters, that the file is one contiguous run whose size is the region's, and that every
sector of a pristine region holds its own tag.
