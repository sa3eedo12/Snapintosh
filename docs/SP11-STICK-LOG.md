# Surface Pro 11 stick log: reaching the boot stick from EL2 (B3, and the first step of B2)

**Design study with host-side groundwork. The writer (S1 to S6) is UNIMPLEMENTED. Hardware status: NOT RUN for the writer;
the read-only survey S0 ran once on the unit (section 12), and its follow-up S0b, a payload, in two boots (section 13).
Two later boots (6 and 7 of 2026-11-15, section 14) ran the third X2 test image and a payload that starts and stops the
secondary cores, a three-step restart probe the same evening (section 14.1) ran a plain q1n1 page and that payload
twice more, and a two-step restart control the next night ran only a read-only payload through the same runner; none
of them read or wrote the stick's USB controller at EL2. Boot 14 (2026-11-17, section 15) ran the wrapper's GIC and stop variant and did not touch it either. Boots 15 and 16 (2026-11-17, section 16) ran the A1 No-Op DMA test, the first xHC register stores at EL2, on `usb_1_ss1`: in the filmed boot the screen went off at the end of the wait before the first RUN store, the unit stayed dark and no No-Op completed (cause UNKNOWN, no restart seen; boot 15 is from the operator's memory only). Boots 17 and 19 (2026-11-17, section 17) ran it again, on `usb_1_ss0` (boot 17) and on `usb_1_ss1` (boot 19, with the stick in the top port and no cable), and ended the same way; boot 18A, a read-only snapshot of both controllers, the clocks and the SMMU, ran to its end with no fault and no black-out (OBSERVED, one unit, one boot each; SC3 stands at 4 of 5). Boot 20 (2026-11-18, section 18) ran the SP11 monitor entry for the first time (M1 rung 1, plan `M1RUN-RUNG1`): the stock kernel executed on one vCPU for the monitor's 5 s box and printed its banner, and the monitor stopped it itself and returned with the registers and the GIC restored (OBSERVED, one unit, one boot, on video); the guest was, INFERRED, spinning in an early loop of the kernel's I/O Kit start-up that cannot end on an image with the panel withheld (one vCPU, 5 s, early boot only; the xHC and the stick's controller were not exercised).**
Nothing of the writer ran on a Surface Pro 11 beyond that test, a stand-alone payload of the q1n1 fork. When the study
was written nothing in q1n1 or slbounce had been changed; stage S0 has since been built as an opt-in, read-only survey in
the q1n1 fork (`docs/SP11-STK-SURVEY.md` there; branch `sa3eedo12-sp11-boot`, commits `8ed0c4f` and `8235433`) and has run
once on the unit: its results are OBSERVED (one unit, one boot) and are recorded in section 12. S0b is a payload for the
fork's payload runner (`docs/SP11-PAYLOAD.md` there); its results are in section 13. Stage S1 exists only as the stand-alone test of A1 (sections 16 and 17); S2 and later do not
exist. It answers plan item B3 (a persistent log sink) and the
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
3. **In S0 the stick sat behind a USB hub (OBSERVED by S0, section 12: two USB nodes in the path, a hub function on each of the
   controller's two root ports, and the stick on hub port 3 with route string 3; the operator confirmed an adapter or hub,
   Q1 in section 11).** The UEFI path has two USB nodes; in EDK2's code each USB node is
   one device on the way down, so the second node is a device behind the first, a hub (section 3.1). q1n1's xHCI driver
   cannot address a device behind a hub (G03 of the host-link study). The cheapest fix was an operator answer (Q1, section 11):
   plug the stick straight into the unit. **The operator answered on 2026-11-14 that a USB-C stick or a passive adapter is
   available, so the next boots can use a direct connection.** Hub support is stage S1.5 (about 250 to 350 lines, 2 to 4
   more boots) and is then needed only if a direct connection does not work. S0 makes the alternative, adopting the
   firmware's own slot state (S1a), unlikely: the xHC reports a host
   controller error and needs a reset, which drops every slot (INFERRED, section 12). Boots 3 to 5 of that day used the
   direct connection (OBSERVED for boot 3: one USB node in the X2 run's UEFI path, `Pci(0x80,0x0)/USB(0x1,0x0)`; boots 4 and
   5 are recorded as direct too), and in that
   topology S0b found no firmware xHCI state at EL2 and the link down (section 13), so S1a has nothing to adopt there.
4. **The stick's controller is `usb_1_ss0` at `0x0a600000` (OBSERVED by S0, section 12).** The survey prints its baseline line
   only for the host controller whose BAR 0 equals that window, and it printed. `usb_1_ss1` at `0x0a800000` had nothing
   connected and firmware never started it. **Caveat (section 17, 17.3):** that was read with the stick in the port the operator usually uses, which boots
   16 to 19 suggest is the second port, the one wired to `usb_1_ss0` (INFERRED); with the stick in the top port the stick's firmware path is `Pci(0x81,0x0)`
   and the latched port word is on `usb_1_ss1`, so the firmware appears to start whichever controller holds the stick (INFERRED), and the sentences above hold
   for the second port only. Which physical USB-C port `usb_1_ss0` is on the unit is not recorded (Q2,
   section 11; section 17 infers the second port). Before S0 this was UNKNOWN: neither the path nor any pinned fact said.
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
   SMMU (line SMMU, then the S1 No-Op). S0 has answered (a) and (b) apart from the PHY (section 12). (c) is half answered:
   both streams translate through context bank 3; S0b read that bank (section 13): it is nested with stage 1 off, so what
   it maps is still UNKNOWN.
9. **S0 ran once on the unit (OBSERVED, one unit, one boot; section 12).** The controller is powered, clocked and readable at
   EL2, an xHCI 1.10 on a DWC3 in host mode, with no fault and no hang. The xHC is halted with its host controller error flag
   (HCE) set, which this study did not anticipate. The firmware's rings and slot contexts are intact in RAM. Both USB streams
   translate through SMMU context bank 3. The conditional GO of section 4.7 is met except its SMMU condition; the read-only
   follow-up S0b has since run (item 10, section 13) and did not decide it.
10. **S0b ran in two boots and was read in one (OBSERVED, one unit, one boot; section 13).** With the stick plugged in
    directly, both USB controllers read `IDLE` at EL2: nothing connected and no DCBAAP (and, on `usb_1_ss0`, no event ring).
    The firmware's xHCI state
    that S0 found behind the hub is not there to adopt, and the link is down; the cause is UNKNOWN, because one boot per
    topology cannot separate a reset, the topology and the unit. A stick writer would have to bring the controller and the
    link up from reset (INFERRED; PHY and repeater bring-up UNIMPLEMENTED, M3 not measured). Context bank 3 is nested with
    stage 1 off, so the S0b walk is `UNSUPPORTED NESTED`: UNKNOWN, and SC2 is neither triggered nor cleared. A1 and A2 stay
    NOT granted.
11. **Boots 6 and 7, the restart probe and the restart control did not touch the stick's controller (OBSERVED, one
    unit, one boot each; section 14).** Boot 6 ran the third X2 image (T06 passed: the NVMe's PCIe root answers at EL2
    and the firmware left its controller running; the image wrote its dump through the firmware before ExitBootServices,
    as every run of it does). Boot 7 ran a payload that started the eleven secondary cores with PSCI `CPU_ON` and
    stopped them with `CPU_OFF`; the board restarted by itself afterwards (operator report; the time was not recorded
    and the cause is UNKNOWN). A restart probe the same evening ran a plain q1n1 page (alive at `UPTIME 123 S`) and that
    payload twice more: the board restarted by itself in one of those runs (operator report; no time recorded) and was
    filmed alive to `UPTIME` about 36 S in the other (UNKNOWN beyond). A restart control the next night ran only the
    read-only `psci` payload through the same runner, in two boots: both pages were photographed alive, at `UPTIME 224 S`
    and `268 S`, and no restart was reported (the state after each photograph is unknown). The panel is still the only
    log channel, so whatever it showed after the last photograph is lost. A1 and A2 stay NOT granted.
12. **Boot 14, the third GIC and stop dry run (`GICDRY-C`), ran on the unit and did not touch the stick's controller (OBSERVED,
    one unit, one boot, from one video; section 15).** The wrapper's GIC variant of work item 8 ended `GIC VERDICT=IRQ_OK` with
    a clean restore: PPI 26 reached EL2 as an IRQ (four times in window 1, once from EL1 in window 2), the quarantine of the
    other enabled interrupts held, and the monitor was not called. The panel was again the only log channel, and A1 and A2 were
    not exercised. Boots 8 to 13 are recorded elsewhere (section 15). Boots 15 and 16, the A1 test (granted for them), are in section 16: the screen went off at the end of the wait before the first RUN store and the unit stayed dark (cause UNKNOWN; A2 not exercised).
13. **Boots 17, 18A and 19 (2026-11-17; section 17; each on video; OBSERVED, one unit, one boot each): two more A1 runs ended as boot 16 did, and a
    read-only snapshot ran to its end.** Boot 17 (`XHCI0.BIN` on `usb_1_ss0`) and boot 19 (`XHCI1.BIN` on `usb_1_ss1`, the stick in the top port, no cable)
    both ended with the page at `NEXT RUN AFTER 8 S` and the screen off about 8 s later, to the end of the video; no No-Op completed and the cause is
    UNKNOWN. SC3 stands at 4 of 5 (boots 15, 16, 17 and 19; 3 if boot 15 does not count). Boot 18A, a read-only snapshot payload of both controllers, the
    clocks and the SMMU (a q1n1 worktree, not committed), made 334 loads with no fault and no black-out; the NoC clocks, the PHY clock and domain words and
    the power domains of both controllers read on, and the controllers read alike except for `USBSTS` and port 2. The Shell's map line (`Pci(0x80,0x0)` in boot 16,
    `Pci(0x81,0x0)` in boots 17, 18A and 19) and the latched port word follow the port that holds the stick: INFERRED, the top port is `usb_1_ss1` and the
    second port `usb_1_ss0`, and the firmware starts the controller whose port holds the stick. Then the tested controller was without the stick in boots
    15 to 17 and with it in boot 19, and the screen went off in all of them: INFERRED, not an unstarted controller, not `usb_1_ss1` alone, not the Mac's
    cable. The hypotheses (the SMMU's context bank 3, the port state machines, an event of the embedded controller or the PMIC, the display subsystem) are
    untested and nothing further is authorised. The sentences of this log that say `usb_1_ss1` was never started by firmware were read with the stick in the
    second port and hold for that wiring only (item 4, section 17.3).
14. **Boot 20 (2026-11-18; section 18; on video; OBSERVED, one unit, one boot): the first run of the SP11 monitor entry (M1 rung 1, plan `M1RUN-RUNG1`) returned, and the
    stock kernel had executed on one vCPU for the monitor's 5 s box.** The monitor ended the run itself (`MON STOP WATCHDOG`, 96,720,173 ticks for the whole call, 5.0375 s), returned `0` with the
    guest entered, and the wrapper restored the registers and the GIC clean (`MON REGS RESTORED=YES`, `GIC RESTORE CLEAN=YES`). The report page printed the kernel's own banner and four early
    lines of its VM bootstrap from the guest's console buffer, with the stop PC `0XFFFFFE000C1EEF00` inside the kernelcache's `__TEXT_EXEC` range; 3,127 traps, no stage-2 abort. By the
    record's rows a to g: a, c, d, e and f held and g held as a host build comparison (VERIFIED, host); b was not exercised. The unit stayed up to `UPTIME 28 S`, with no restart and no
    fault page. **The guest was stuck, not slow (INFERRED, high; a read-only analysis after the boot, 18.5):** the stop PC is the head of a five-instruction register-only loop in the kernel's
    I/O Kit start-up (`PE_init_iokit`, identified by structure against the public source), which cannot end when the display size is 0, as it is on this image (panel withheld, D9, and no
    `-noprogress`); a longer box on the same image only repeats it, and the next run hands the panel (rung 2) or adds `-noprogress`. The run shows nothing about SMP, userspace, a root device,
    a panel console, stability or performance, nor that the kernel boots or that macOS boots; the regime of the NV2 record the gate passed with is still not recorded (UNKNOWN). The stick was
    only the boot medium: it held every staged file byte for byte, and A1 and A2 were not exercised. No next step is authorised.

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

**Before S0, which one the stick uses was UNKNOWN; S0 answered it on the unit: `usb_1_ss0` (section 12).** The path carries
no controller identity (3.1). Section 17 adds that this answer was for the port the stick was in then: the controller follows the port (INFERRED, 17.3). The Denali description says which
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
| (iii) | **DRAM retention across a warm reset, then a dump on the second boot** | No EL2 USB at all: firmware's own USB and file system drivers write the dump in boot services. About 100 to 150 lines for the canary test, which is M10 of the host-link study | Three unknowns in series: a reset that works at EL2 (`ResetSystem` was OBSERVED not to work after the bounce; PSCI SYSTEM_RESET and a watchdog reset are untried, UNKNOWN), retention of DRAM across it (UNKNOWN, A1), and a boot that returns to the stick. The last needs either BootNext, which is a non-volatile variable write that needs the operator's explicit authorization, or the operator holding the boot keys on every boot. Update, 2026-11-14: after the automatic reboot that followed a stall at EL2 (boot 4) the Surface booted something other than the stick (operator report), so the operator at the keys is a fact and not a worst case; whether DRAM survives such a reset is still UNKNOWN (M10 NOT RUN; section 13) | **Keep as a cheap parallel experiment (M10), not the primary route.** If it works it could also recover something after a forced reset following a hang before USB works, which the stick writer cannot |
| (iv) | **UEFI runtime SetVariable after ExitBootServices** | Needs no USB driver | An NVRAM write is a persistent firmware modification and needs explicit authorization (AGENTS.md section 5). Runtime services at EL2 here are unverified (`ResetSystem` was OBSERVED not to work after the bounce). The store is small, wears, and a damaged store can disturb the boot configuration. A volatile variable (no NON_VOLATILE attribute, as q1n1's A16 prep step stores its snapshot) is not an NVRAM write but does not survive a reset and cannot be written after ExitBootServices | **Rejected** unless explicitly authorized, and then only for a few bytes of stage marker as a last resort |
| (v) | **A camera pointed at the panel** | No device risk and no new q1n1 code. A fixed camera takes a photograph at each page; a Mac script decodes the text. The glyph set is known and small (`screen_char` in q1n1's `main.c`), so template matching should decode it almost exactly; QR frames would make it exact (an encoder in q1n1 is a few hundred lines, UNIMPLEMENTED) | Shows only what is on screen when photographed; after a hang nothing later appears; someone still arms the camera. Not built here: it needs real photographs to validate | **Do it in parallel.** It removes the tiring part, transcription, at no device risk, but it is not a persistent log |

### 4.7 The go/no-go recommendation and the stop criteria

**The go/no-go.**

- **GO for S0 now.** It is read-only, B2 needs it regardless of B3, and its verdict decides S1 to S6.
- **Conditional GO for S1 to S6**, only if S0 shows at EL2 for the stick's controller: gate ON (GDSC and all required clocks
  running), a block that answers as an xHCI with a sane identity, and an SMMU stream that is bypass or that maps q1n1's DMA
  arena (or no SMMU in the way), with no fault.
- **S0 outcome (OBSERVED once, section 12).** The gate is ON and the identity is sane, so the first two conditions are met.
  The SMMU condition is not decided: both streams translate through context bank 3 and S0 did not read it, so neither GO nor
  SC2 applied after S0. S0 also found what this study had not anticipated: the xHC is halted with HCE set. The follow-up was
  the read-only S0b of section 12, not S1.
- **S0b outcome (OBSERVED once, section 13).** S0b read context bank 3: it is nested (stage 1 followed by stage 2) with stage 1
  off and no table base, so its walk is `UNSUPPORTED NESTED`, which is UNKNOWN. The SMMU condition is still not decided and
  SC2 is neither triggered nor cleared. What would decide it: a read-only stage-2 walker payload (it must first find the
  stage-2 context, UNKNOWN how, with NUMS2CB 0), or the S1 No-Op, which needs A1 (NOT granted then; granted for boots 15, 16, 17 and 19, sections 16 and 17). S1 had not started then.
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
  needs the firmware's contexts intact (S0 line FWRINGS with INRAM=1) and the link alive; its feasibility is UNKNOWN behind a hub, and
  with a direct connection it has nothing to adopt (S0b, OBSERVED once, section 13: DCBAAP 0, no event ring, link down at EL2). It is the riskier variant (writes into state the controller owns). The study recommends S1b with a direct connection first, and S1a only if the stick must stay behind a hub.
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
any claim in section 3 about the unit is true beyond what section 12 records. Hardware: NOT RUN for the writer.

## 9. Evidence summary

| Claim | Label | Where |
| --- | --- | --- |
| The stick's path has two USB nodes and a hub is in front of it (slots 1 and 2 are hub-shaped, the stick has route string 3) | OBSERVED (S0; that slots 1 and 2 are hub functions is INFERRED from the endpoint shape) | 3.1, 12 |
| With the stick plugged in directly, the UEFI path has one USB node | OBSERVED (X2 run 6, one boot) | 1, 13 |
| Which of the two Denali controllers the stick uses | OBSERVED (S0): `usb_1_ss0`, for the port the stick was in then; the controller follows the port (INFERRED, section 17) | 3.2, 12, 17 |
| The xHC is halted at EL2 | OBSERVED (S0); that firmware's ExitBootServices handler halted it is INFERRED | 3.3, 12 |
| The xHC reports HCE (USBSTS bit 12) at EL2, which it did not before ExitBootServices | OBSERVED (S0); cause UNKNOWN | 12 |
| GDSC and clocks of both controllers at EL2 | OBSERVED (S0): on | 3.3, 12 |
| SMMU streams `0x1420` and `0x1460` at EL2 | OBSERVED (S0): translate through context bank 3 | 3.3, 12 |
| Both USB controllers at EL2 with the stick plugged in directly: `IDLE`, nothing connected, no DCBAAP (on `usb_1_ss0` also no event ring), link down | OBSERVED (S0b, one boot); the cause of the difference from S0 is UNKNOWN | 13 |
| A stick writer has to bring the controller and the link up from reset | INFERRED; PHY and repeater bring-up UNIMPLEMENTED, M3 not measured | 13 |
| Context bank 3 is nested with stage 1 off; the S0b walk is `UNSUPPORTED NESTED` | OBSERVED (the registers); what the bank maps is UNKNOWN; SC2 neither triggered nor cleared | 13 |
| PHY, repeater, retimer, what context bank 3 maps, DMA coherence at EL2 | UNKNOWN (the bank's registers were read by S0b: 13) | 3.3, 12, 13 |
| DRAM retention across a reset | UNKNOWN (M10 NOT RUN); after an automatic reboot the Surface booted something other than the stick (operator report) | 4.6, 13 |
| M2, M1, M1b, M1c and M6a are read-only at EL2 | VERIFIED as a classification; run once (S0) without a fault | 3.5, 12 |
| `ResetSystem` does not work after the bounce | OBSERVED (X1) | 3.4, 4.6 |
| Raw writes into a tagged, pre-allocated region are guarded against wrong maps and device faults | EXPERIMENTAL (host tests) | 4.5, 5 |
| A real stick has a BOT interface and answers like the fake | UNKNOWN / not shown | 4.6, 8 |
| Effort and boot counts | INFERRED (estimates, no data) | 6 |
| q1n1 can address a device behind a hub | VERIFIED false (G03) | 7 |
| The first xHC register stores at EL2 (the A1 No-Op test, boots 15, 16, 17 and 19, on both controllers, with the stick and the Mac's cable in several places): the page ended at `NEXT RUN` and the screen went off about 8 s later, and no No-Op completed | OBSERVED (boots 16, 17 and 19 on video; boot 15 from memory); that the RS store is the cause is INFERRED; the cause is UNKNOWN | 16, 17 |
| A read-only snapshot of both controllers, the NoC and PHY clocks and the SMMU (boot 18A): 334 loads, no fault, no black-out; the clocks and power domains read on | OBSERVED (one unit, one boot) | 17 |
| The SP11 monitor entry, run once (boot 20, M1 rung 1, plan `M1RUN-RUNG1`): the stock J700 26A428 kernel executed at EL1 on one vCPU until the monitor's own 5 s stop (`MON STOP WATCHDOG`), printed its banner and four early console lines, and the call returned with the registers and the GIC restored; no stage-2 abort | OBSERVED (one unit, one boot, on video); the guest was, INFERRED (high), stuck in an early loop of the kernel's I/O Kit start-up (18.5); one vCPU, 5 s, early boot only: nothing about SMP, userspace, a root device, a panel console, stability or performance; the NV2 record's regime UNKNOWN | 18 |
| The stick's port decides which controller the firmware starts: the top port is `usb_1_ss1` (`Pci(0x81)`), the second port `usb_1_ss0` (`Pci(0x80)`) | INFERRED (the Shell's map line and the latched port word of four boots; the wiring was not traced) | 17 |
| Any behaviour of the writer on the unit | not established | NOT RUN |

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
   plug it straight in? (If not: S1a or S1.5.) **Answered 2026-11-14 (operator): through an adapter or hub.** S0 confirmed an
   active hub (section 12). **Asked the same day whether the stick can go in without a hub: yes, the operator has a USB-C
   stick or a plain passive adapter.** Boots 3 to 5 used a direct connection (section 13); S1.5 stays on the table only as the
   fallback, and S1a has nothing to adopt in the direct topology (section 13).
2. **Q2. Which port.** Which of the unit's USB-C ports is the stick in, so that D3 of 3.2 has a starting point? S0 says
   `usb_1_ss0` carries it now (section 12). The path node was `Pci(0x81,0x0)` in the earlier report of 3.1, which suggests
   (INFERRED) that the stick was then in the other port. The operator can say which physical port is which. **Answered in part by section 17 (INFERRED):** the
   operator put the stick in the top port in boots 17, 18A and 19 (his statements; the Shell's map line read `Pci(0x81,0x0)` each time), and in boot 16, where
   the map line read `Pci(0x80,0x0)`, it was by his habit in the port he usually uses, the second (not recorded for that boot); the top port is then
   `usb_1_ss1` and the second port `usb_1_ss0` (D3 of 3.2, moving the stick to the other port and comparing, was in effect done with the map line and the
   port words). The wiring itself was not traced.
3. **Q3. Imaging.** Can the operator write a disk image to the stick (W1b), or must the log region be created by copying files
   (W2, which needs a q1n1 FAT walker)? Will the operator keep the stick on an MBR disk (GPT is refused)?
4. **Q4. Authorization.** May A1 (the first write to an xHC register at EL2, S1) and A2 (the first sector write to the stick,
   S5) be granted, and when? Nothing in this study grants them, and both stay NOT granted after S0b.
5. **Q5. Gate name.** The model is gated by `SNAP_PLATFORM_SP11`; a runtime writer needs its own gate and a boot option. Name
   them before S0 code is written.
6. **Q6. Camera.** Is a fixed camera, with a Mac-side decoder, worth setting up in parallel (alternative (v))? Still open:
   boot 4's photographs did not frame the right-hand column of the page and its payload text was lost (section 13);
   boot 7's last photograph was taken at an angle and read with some blur, and the board restarted right after it. A
   video does help: step 3 of the restart probe was filmed, its panel was crisp, and a video that runs through a
   restart would time it (section 14.1).
7. **Q7. Retention.** Is the M10 experiment to run now, since it is cheap and independent? Still open, with one new fact: after
   the automatic reboot of boot 4 the Surface booted something other than the stick (operator report; section 13), so a dump
   on the next stick boot would need the operator to select the stick again. DRAM retention across such a reset is UNKNOWN
   (M10 NOT RUN).

## 12. Result of the S0 survey on the unit (OBSERVED, one unit, one boot)

**What ran.** Autorun step 2 of the plan `nv2-then-survey` (nothing typed): `SLBOUNCE-DEVHE-MARK.EFI`, then
`Q1N1-STK.EFI --el2`, q1n1 build `8235433AAD59`. The scoped sweep accepted the two ranges the build declared (its image and
its memory-map copy, each inside one descriptor), the bounce happened and q1n1 reached EL2 (`HCR_EL2` `0x88000010`: E2H 0,
TGE 1). The page stayed up for at least 20 s (uptime 4, 12 and 20 s in three photographs). The survey ran to its end
(`STK AT DONE` and a verdict line) with no fault, no hang and no write: it only loads. Seven photographs; every STK value was
re-read against enlarged crops. This is one boot of one unit, not a distribution.

| # | Question | Survey lines | What the unit showed | Label | Consequence |
| --- | --- | --- | --- | --- | --- |
| 1 | The stick's controller | P2 `U0` | The baseline line, which is printed only for the host controller whose BAR 0 equals the `usb_1_ss0` window `0x0a600000`, appeared. Firmware lists four xHCI handles; the survey shows three (BAR 0 `0x0a000000`, `0x0a400000`, `0x0a200000`) | OBSERVED | S1 targets `usb_1_ss0`. `usb_1_ss1` (`0x0a800000`) was never started by firmware (caveat, section 17: read with the stick in the second port, INFERRED; with the stick in the top port the firmware appears to start `usb_1_ss1` instead) |
| 2 | The path | `SELF`, `HUB` | `Pci(0x80,0x0)/USB(0x1,0x0)/USB(0x2,0x0)`: two USB nodes (root port index 1, hub port index 2). The report in 3.1 had `Pci(0x81,0x0)` | OBSERVED | The shape is as 3.1 says. INFERRED: the PCI-style node number follows the controller, so the stick was then in the other port (Q2) |
| 3 | The hub | `SLOT` | Firmware slot 1: high speed, root port 1, interrupt IN with a 1-byte packet. Slot 2: SuperSpeed, root port 2, interrupt IN with a 2-byte packet. Slot 3: SuperSpeed, route string 3, bulk OUT and bulk IN with 1024-byte packets, the stick. The Hub flag reads 0 in all three slot contexts | OBSERVED; that slots 1 and 2 are the two halves of one hub is INFERRED from the endpoint shape | An active hub (a USB 2 and a USB 3 hub function, one per root port) is in front of the stick: the adapter is not passive. q1n1 has no route string or hub code (G03), so S1.5 is needed unless the stick can go in directly (Q1) |
| 4 | The stick | `BLK`, `USBIO` | 512-byte blocks, removable, not read-only, partition at sector `0x800`; `bcdUSB` `0x310`; interface class 8, subclass 6, protocol `0x50` (mass storage, SCSI, Bulk-Only); bulk IN `0x82`, bulk OUT `0x01`, 1024-byte packets | OBSERVED | Firmware drives it as BOT, not UAS: the UAS clause of SC4 is not triggered, and BOT at SuperSpeed is what S2 to S5 must carry |
| 5 | Power and clocks | `GCC`, `GATE` | Both controllers: GDSC power bit set and software collapse clear; the five controller clocks read `0x1`; the shared clocks read NoC `0x1`, system `0x1`, north `0x0`, south `0x0` (raw values, bit 31 clear in all of them, which is what the gate tests). Gate ON for both | OBSERVED | SC1 is not triggered: nothing needs a GCC write to be read |
| 6 | Identity | `ID`, `XECP`, `DWC3` | xHCI 1.10, 64 slots, 2 ports (port 1 USB 2, port 2 USB 3), 64-byte contexts; DWC3 `GSNPSID` `0x33313130`, host mode (`PRTCAPDIR` 1, `CURMOD` 1), no latched bus error; identical on both controllers | OBSERVED | The second condition of the conditional GO is met |
| 7 | xHC run state | `OP`, P2 `U0` | At EL2: `USBCMD` 0, `USBSTS` `0x1019` = HCH, EINT, PCD and **HCE (bit 12)**; HSE 0, CNR 0; `DCBAAP` (`0xCF412000`) and `CONFIG` (`0x40`) still firmware's. At P2, before ExitBootServices, the same controller ran (`USBCMD` 1, `USBSTS` `0x18`, command ring running) and HCE was clear | OBSERVED | Halted, as 3.3 expected (INFERRED: by firmware's handler). HCE appeared between P2 and P3; its cause is UNKNOWN (firmware's ExitBootServices handler, the hypervisor swap, or a DMA failure; the DWC3 latched no bus error, which weakly argues against the last). The survey decodes HSE but not HCE, so HCE is visible only in the raw value. A controller with HCE set needs a host controller reset, which clears all slots and the root hub: S1a is unlikely to work (INFERRED) and S1 starts with a reset and a full enumeration |
| 8 | Ports | `PORT` | Port 1 `PORTSC` `0xE03` (connected, enabled, powered, high speed, link state U0). Port 2 `0x81203` (the same at SuperSpeed, warm-reset-change flag set). Both equal the values at P2 | OBSERVED | The links stayed in U0 through the halt and the bounce, so the PHYs and the hub kept their state (INFERRED). No PHY register was read (M3 stays UNIMPLEMENTED) |
| 9 | Firmware's rings | `FWRINGS`, `SLOT` | `DCBAAP` `0xCF412000`, event ring segment table `0xCF415240`, event dequeue pointer `0xCF414CB8`, all RAM; three slot contexts decode sanely (configured, addresses 1 to 3). `CRCR` reads 0 on a halted controller | OBSERVED | Nothing reused or freed the firmware's xHCI memory by P3, and CPU reads of it after the sweep decode (INFERRED: coherent enough) |
| 10 | The other controller | `OP`, `PORT` | `USBSTS` `0x1` (halted, no error), `DCBAAP` 0, `CONFIG` 0, both ports in RxDetect, nothing connected | OBSERVED | Untouched by firmware (caveat, section 17: with the stick in the second port, INFERRED) |
| 11 | SMMU | `SMMU` | `ID0` `0x7C017E9A` (154 stream match registers); `sCR0` `0x230406` (unmatched streams fault, client port enabled); no global fault. Stream `0x1420` matches register 3 and stream `0x1460` register 4; both translate into context bank 3 | OBSERVED | Neither bypass nor fault. What context bank 3 maps was not read: this is the SC2 question and it is open |

The verdict line was `VERDICT U0=STICK-PRESENT-HALTED U1=IDLE SMMU=TRANS`.

**Against section 4.7.** The conditional GO needs, for the stick's controller, a gate that is ON (met), a block that answers
as an xHCI with a sane identity (met) and an SMMU stream that is bypass or maps q1n1's arena without a fault (not decided).
SC1 is not triggered. SC2 is neither triggered nor cleared. SC3 to SC6 are not reached: S1 has not started. Three risks are
new: HCE (row 7); the hub (row 3), which is now a fact and no longer a possibility; and a translating context bank whose
tables may live in memory that the hypervisor teardown or q1n1 reuses (UNKNOWN).

**What S0 does not show.** Any DMA by the xHC; the cause of HCE; PHY, repeater and retimer state; which physical port is
`usb_1_ss0`; the fourth firmware xHCI handle; whether a reset of this xHC works at EL2; the state of either controller with a
device attached to `usb_1_ss1`. S0b (section 13) added the direct-topology state of both controllers and the registers of
context bank 3, and showed none of the rest.

**S0b: the read-only follow-up, before any write (it has run: section 13).** It needs no authorization because it only loads,
and it rode in boots that were happening anyway, as a payload for the payload runner (`docs/SP11-PAYLOAD.md` in the q1n1
fork). It was specified to print, in order and bounded:

1. the whole of `USBSTS` and the last few event TRBs before the firmware's event dequeue pointer, to look for the cause of HCE;
2. SMMU context bank 3: its base register, control and translation-control registers, the translation table base, the memory
   attributes, the fault status, address and syndrome; the fault status of every context bank (non-zero only); the global
   fault address and syndrome registers;
3. a read-only walk of context bank 3's tables (RAM loads only, bounded), printed as coalesced IOVA ranges that say whether
   each maps to itself. This decides where an EL2 xHCI driver may put its rings: anywhere in DRAM, or only inside the
   firmware's own xHCI memory;
4. the firmware's slot contexts and the port status registers of both controllers again, taken with the stick plugged in
   directly (no hub): the topology the writer should target, and whether HCE appears without a hub in the path.

The S0 build does not decode HCE, lists three of four xHCI handles and cannot read a context bank; S0b was to cover these without a
new survey image, and section 13 says what it did. S1 follows only if S0b does not trigger SC2 (it did not trigger it, and did not
clear it), with A1 (section 11, Q4) granted and a direct connection shown to work (otherwise S1.5 is planned). Neither condition
holds: A1 is NOT granted, and in the direct topology the link was down at EL2 (section 13). A1 and A2 stay NOT granted.

## 13. Result of S0b on the unit (OBSERVED, one unit, one boot)

**What ran.** On 2026-11-14 two boots ran the payload runner (`Q1N1-PAYLOAD.EFI`; q1n1 build `A9F00F9CCBB5`, commit `a9f00f9`
of the fork; `docs/SP11-PAYLOAD.md` there) with the stick plugged in directly, no hub. S0b is its payload `usbsmmu`: a file on
the stick that the runner loads before ExitBootServices, hashes again at EL2 and calls. The runner replaces a new survey image
per measurement by a file; it does not replace the photographs. The lines still reach the operator only as text on the panel,
and the persistent log (B3) is still UNIMPLEMENTED. The payload reads devices only through the runner's guarded 32-bit load and
has no store (213 loads, 0 faults, 0 refused in boot 5): no write to an xHC register, to the SMMU or to the stick. File hashes
(SHA-256 prefixes): `q1n1-payload.efi` `c20579093ec7`, `PSCI.BIN` `d391a9c8`, `USBSMMU.BIN` `9f0260dc`, `TIMER.BIN`
`68cace99`.

- **Boot 4** (chain `psci`, `usbsmmu`, `timer`; manifest `3f99a4d0`, 920 bytes). Before ExitBootServices the console showed all
  three payloads loaded and hashed, six declared ranges accepted (every one `contained=yes`) and
  `PAYLOAD READY FILES=3 RANGES=6 CODE=3`; the bounce was taken and EL2 was reached. The right-hand panel held 58 lines, counted
  from the geometry of a photograph that cut the column off, so no payload text was captured and boot 4 gave no `usbsmmu`
  values. `UPTIME` stayed 0 S in both photographs (the heartbeat loop starts only after the chain returns) and the board rebooted
  by itself before a third could be taken (operator report); afterwards it booted something other than the stick (operator
  report). With boot 5's line counts (`psci` 15, `usbsmmu` 17), 58 lines are the runner's four notes, a `VERIFY`, a `CALL` and
  (except for the last payload) a `DONE` for each of the three payloads, and 14 lines of the `timer` payload, the 14th being
  `PAYLOAD TIMER NEXT CNTPOFF_EL2`: the `timer` payload stopped at its read of `CNTPOFF_EL2` and the board was reset. That is
  INFERRED from a count, assuming that `psci` and `usbsmmu` printed as many lines as in boot 5; the cause (`SCR_EL3.ECVEn` clear)
  stays INFERRED too.
- **Boot 5** (chain `psci`, `usbsmmu` only; manifest `9d6f731b`, 624 bytes; the `timer` payload is not run again).
  `PAYLOAD READY FILES=2 RANGES=5 CODE=2`; both payloads returned (`RETURN=0X0 STACK=OK`), the page stayed up (`UPTIME 3 S` in
  one photograph, `UPTIME 10 S` in another) and the panel was photographed in full. Its 41 lines are in Appendix D, verbatim.

**The `usbsmmu` lines, decoded (boot 5; every value was re-read against enlarged crops).**

| # | Question | Panel line | What the unit showed | Label | Consequence |
| --- | --- | --- | --- | --- | --- |
| 1 | `usb_1_ss0` (`U0`, window `0x0a600000`) at EL2, stick plugged in directly | `U0` | `IDLE`: `CAPL` `0x20`, `USBCMD` 0, `USBSTS` `0x11` (HCH and PCD; HCE clear), `DCBAAP` 0, port 1 `PORTSC` `0x20002A0` (RxDetect, port power, WCE). The rule for `IDLE` is `DCBAAP` 0 and CCS clear on both ports, so port 2 has no connection; its raw value is not on the page (the payload drops a field that does not fit the 100-character line) | OBSERVED | In S0, behind a hub, this controller still held the firmware's rings: `DCBAAP` `0xCF412000`, three slots, `USBSTS` `0x1019` with HCE, port 1 `0xE03` in U0. Here nothing is started and nothing is connected |
| 2 | `usb_1_ss1` (`U1`, `0x0a800000`) | `U1` | `IDLE`: `USBSTS` `0x1` (HCH), `DCBAAP` 0, port 1 `0x20002A0` | OBSERVED | As in S0: untouched by firmware (caveat, section 17: with the stick in the second port, INFERRED) |
| 3 | The DWC3 of `U0` | `DWC3` | `GSNPSID` `0x33313130` (as in S0), `GCTL` `0x101005`, `GSTS` `0x7E800001`, no bus error latched, `GDBGLTSSM` `0x9CC20026` (raw, not decoded here) | OBSERVED | The same core, readable, no fault |
| 4 | The event ring and the slots | `RINGS`, `TRB`, `U0 SLOTS` | `ERSTSZ`, `ERSTBA` and `ERDP` all 0 (`SEG0=NO_ERST`), so the TRB and slot groups were skipped (`NO_SEG0`, `NO_DCBAAP`) | OBSERVED | There are no firmware rings to read in this topology: S0b item 1 (the last event TRBs, to look for the cause of HCE) could not be done. HCE is clear here, so its cause in S0 stays UNKNOWN |
| 5 | The apps SMMU (`0x15000000`), global registers | `SMMU` | `ID0` `0x7C017E9A` (154 stream match registers), `ID1` `0x6000005F` (95 context banks, `NUMS2CB` 0, 4 KiB pages, `NUMPAGENDXB` 6), `ID2` `0x5222` (IAS, OAS and UBS 40 bits; 4 KiB and 64 KiB granules), `sCR0` `0x230406`, `sGFSR` 0, context bank base `0x15080000`; streams `0x1420` and `0x1460` match SMR3 and SMR4, S2CR `TRANS` into context bank 3 | OBSERVED | As S0 row 11, plus the identification registers. `NUMS2CB` is 0 |
| 6 | Context bank 3 | `CB3` | `CBAR` `0x35E03`: TYPE 3 (stage 1 followed by stage 2, `NESTED`), VMID/CBNDX field 3, MemAttr 5, BPSHCFG 2; `CBA2R` `0x1` (VA64); `SCTLR` `0xE0` (M 0: stage 1 off; only fault-report bits set); `TCR`, `TTBR0`, `TTBR1` and `MAIR0` all 0, `TCR2` `0x60`; `FSR` `0x400`, `FAR` `0x953FDF7CF7F7`, `FSYNR0` `0x46005D` | OBSERVED (the values); VERIFIED (as what the pinned `arm-smmu.h` says: `FSR` `0x400` is bit 10, inside the FORMAT field, bits 10 to 9, `arm-smmu.h:203`, and the fault mask at its lines 218 to 224 does not include that field); INFERRED (for the unit: no fault bit is set, so the bank records no fault, and the FORMAT value `0b10` fits the AArch64 table format that `CBA2R` VA64 selects; the Arm SMMU architecture text was not re-read); UNKNOWN (what `FAR` and `FSYNR0` mean: with no fault bit set they are not fault records, INFERRED) | Stage 1 is off and no table base is programmed, so there is no stage-1 table to walk. Whether stage 2 maps an EL2 xHCI driver's DMA is UNKNOWN |
| 7 | The walk | `WALK` | `UNSUPPORTED NESTED`, echoing `CBAR`, `CBA2R`, `SCTLR`, `TCR` and `TTBR0` | UNKNOWN | The payload's rule: a bank it does not model (stage 2, nested, all zero) is UNKNOWN, not a stop. Neither a GO nor SC2 follows (below) |

**What S0b answered of its four items (section 12).** (1) `USBSTS` was read and HCE is clear; the event TRBs could not be read,
because no firmware ring exists in this topology. (2) Context bank 3's registers were read (row 6). No line shows another bank's
fault status or the global fault address and syndrome; whether none was non-zero or the payload does not print them is not
established by the page. (3) The walk was impossible (row 7). (4) The ports and slots were read in the direct topology (rows 1, 2
and 4): nothing connected, no slots.

**Against section 4.7.** The conditional GO needs an SMMU stream that is bypass or maps q1n1's arena without a fault. S0b did not
decide it: stage 1 of the stream's context bank is off and its tables are zero, and the bank is nested, so the translation that
would apply is a stage 2 that the payload does not model. SC1 is not triggered (both controllers answer as xHCI). SC2 is neither
triggered nor cleared. What would decide it: a read-only stage-2 walker payload (it must first find the stage-2 context: UNKNOWN
how, with `NUMS2CB` 0), or the S1 No-Op, which needs A1 (the first xHC register write at EL2; NOT granted). SC3 to SC6 are not
reached: S1 has not started.

**What this changes.**

- **The firmware's xHCI state cannot be adopted in the direct topology (on `U0` or `U1`).** In the hub run (S0) `usb_1_ss0` still
  held the firmware's rings and slots. In the direct run both controllers read as unstarted or reset at EL2, with the stick
  attached and the firmware having just booted from it: S1a has nothing to adopt there (which controller the stick was on is not
  printed; see the last paragraph). The cause of the difference is UNKNOWN: a reset between the firmware's last use and EL2, the
  different topology, or the unit. One boot per topology cannot separate them.
- **The link is down at EL2.** No port of either controller shows a connection. A stick writer would have to bring the controller
  and the link up from reset (INFERRED); the PHY and repeater state is UNKNOWN (M3 not measured) and their bring-up is
  UNIMPLEMENTED. Every such step is a write and needs A1 (NOT granted), and A2 stays NOT granted: the writer (S1 to S6) is still
  UNIMPLEMENTED.
- **The DRAM-retention alternative ((iii), M10) has an operator fact.** After the automatic reboot of boot 4 the Surface booted
  something other than the stick (operator report, once). A dump on the next stick boot therefore needs the operator to select the
  stick again, and whether DRAM survives such a reset is UNKNOWN (M10 NOT RUN). The mechanism of the reboot was not observed (a
  watchdog is INFERRED).
- **The camera alternative ((v)) stays open.** Boot 4's payload text was lost because its photographs missed the right-hand column
  of the page. Nothing was built or tried for (v).

**What S0b does not show.** Why `U0` reads differently from S0 (UNKNOWN, above); which controller the stick was on in these boots
(the payload prints neither the stick's device path nor a baseline from before ExitBootServices; the path of boot 3 carried the
same PCI-style node as S0's, `Pci(0x80,0x0)`, which makes `usb_1_ss0` likely, INFERRED); stage-2 translation for streams `0x1420`
and `0x1460`; any DMA by the xHC; PHY, repeater and retimer state; whether a controller or port reset brings a link up at EL2; DRAM
retention. Boot 4 gave no `usbsmmu` values at all.

## 14. Result of boots 6 and 7, the restart probe and the restart control on the unit (OBSERVED, one unit, one boot each)

**What ran.** On 2026-11-15 the autorun plan `morning-nvme-smp` ran two steps, one per boot, with nothing typed, both
under the de-VHE slbounce build `SLBOUNCE-DEVHE-MARK.EFI` (SHA-256 prefix `4107c28c`). Ten photographs were taken: four
of boot 6's page; for boot 7 two of the console before the bounce, one of q1n1's EL2 page and three of the panel. The
stick was read again afterwards: `DONE1.TXT` and `DONE2.TXT` say `AUTORUN morning-nvme-smp STEP 1 STARTED` and `STEP 2
STARTED`, which show that each step started and nothing more. Boot 7's console shows `Pci(0x80,0x0)/USB(0x1,0x0)`: one
USB node, the direct connection of boots 3 to 5. A third plan, `restart-probe`, ran the same evening and a fourth,
`restart-control`, the next night (section 14.1). A1
and A2 were not exercised by any of these steps. None read or wrote an xHC register or a stick sector at EL2, and
`cpuon` issued PSCI `CPU_ON`, `CPU_OFF` and `AFFINITY_INFO` only (boot 6's image wrote its dump and `pciio.txt` to the
stick through the firmware, before ExitBootServices, as every run of it does).

- **Boot 6, step 1:** the third X2 test image, `declared` variant (`TESTS-DECL.EFI`, build id `DDE4BC575316`, SHA-256
  prefix `b592f741`). Its page and the comparison with the prediction are in [SP11-X2-TESTS.md](SP11-X2-TESTS.md)
  (Run 7) and [SP11-X2-V3.md](SP11-X2-V3.md) (section 13). In short (OBSERVED, read-only): T06 passed. The NVMe is
  behind PCIe segment 6 (ECAM `0x70000000`) at 01:00.0, the link is Gen4 x4 and up at EL2, and the firmware left the
  controller enabled and ready with its own two-entry admin queues in boot-services memory. T07 (the PCIe SMMU)
  faulted again: UNKNOWN. T01 to T05 and T09 read as in the earlier run, and K1 stays GO. The two 8 s photograph pauses
  worked, no hang and no reset happened inside T06, and the finished page counted down to the image's own designed
  reset (`RESET IN 54 S`).
- **Boot 7, step 2:** `Q1N1-PAYLOAD.EFI --el2` (q1n1 build `A9F00F9CCBB5`, runner SHA-256 prefix `c2057909`, the runner
  of boots 4 and 5) with one payload, `cpuon` (`CPUON.BIN`, 18,056 bytes, SHA-256 prefix `3b4bfda7`; the panel shows
  `SHA=3B4BFDA7D59F923C`; source and document `docs/SP11-PAYLOAD-CPUON.md` of the q1n1 fork, commit `d48b0af` of
  branch `sa3eedo12-sp11-boot`). `cpuon` asks `AFFINITY_INFO` about the eleven secondary cores, starts one of them with
  `CPU_ON` after an 8 s pause on the counter (so that the page can be photographed first), then the others after
  another 8 s; each secondary reports the state it was entered with and powers itself off with `CPU_OFF`. The boot CPU
  is never started and writes no system register; a secondary's stub writes its own `VBAR_EL2` and the DAIF masks and
  nothing else, and nothing touches `CNTPOFF_EL2`. The runner declared four ranges (its image, its map copy, the
  preload and the payload), each inside one descriptor (types 1 and 2, attribute `0x100F`), and printed `PAYLOAD READY
  FILES=1 RANGES=4 CODE=1`; the bounce was taken and q1n1's EL2 page showed the de-VHE state seen before (`HCR_EL2`
  `0x88000010`, E2H 0, TGE 1; `MPIDR_EL1` `0x80000000`; `CNTFRQ_EL0` `0x124F800`). The whole panel is in Appendix E.
  The same file and runner ran twice more in the restart probe (14.1), whose step 3 gave a second, crisp panel.

**The `cpuon` lines, decoded (boot 7 and step 3 of the restart probe, so OBSERVED twice on one unit: boot 7's lines after
the first pause were read from a photograph taken at an angle, with some blur; step 3's whole 45-line panel was read from
a video with every line legible, and it confirms or refines every value that boot 7 gave; step 2's mid-chain
photograph, crisp, shows phase 1 a third time; Appendix E).**

| # | Question | Panel line | What the unit showed | Label |
| --- | --- | --- | --- | --- |
| 1 | Are the secondaries OFF, as the `psci` payload found? | `AFFINITY` | All eleven targets answered OFF before any start, as in boot 5 | OBSERVED |
| 2 | Does the firmware's PSCI start a core at an address the payload chose, called from EL2? | `P1 CPU_ON`, `P2 CPU_ON` | Yes. `CPU_ON` with an entry address inside the payload image (EfiLoaderCode, image base plus `0x1000`) returned `0x0:SUCCESS` for core `0x100` alone, then `ISSUED=11 SUCCESS=11` for all eleven back to back (core `0x100` a second time) | OBSERVED (both boots) |
| 3 | What state does a secondary start in? | `EL=`, `SCTLR_EL2=`, `MPIDR=`, `FRQ=`, `CNTVOFF_EL2=` | The ten printed reports of each boot (the eleventh, `0x20300`, was dropped by the line budget; `REPORTED=11` counts it) read `EL=2`, `MPIDR=OK` (the MPIDR the MADT lists), `FRQ=SAME` (the boot CPU's `CNTFRQ_EL0`) and `CNTVOFF_EL2` 0. Every printed register line reads `SCTLR_EL2` `0x30000000`: M, C and I clear (MMU and caches off), only bits 29 and 28 set (arithmetic on the printed value) | OBSERVED (both boots, ten of eleven cores printed) |
| 4 | Do the ID registers match the boot CPU's? | `IDS=` | `IDS=SAME` for the three cores of cluster 0 (`0x100`, `0x200`, `0x300`); `IDS=MIDR` for the cores of clusters 1 and 2, whose `MIDR_EL1` differs and whose other compared registers (`ID_AA64MMFR0_EL1`, `ID_AA64ISAR0_EL1`, `ID_AA64PFR0_EL1`) equal the boot CPU's | OBSERVED (both boots) |
| 5 | What do `MIDR_EL1` and the entry state of the EL2 control registers look like per core? | `MIDR=`, `HCR_EL2=`, `VBAR_EL2=` | The next table and the bullets after it | OBSERVED (both boots; crisp in step 3) |
| 6 | Does `CPU_OFF` work, and can a core be started again? | `P1 POWER_OFF`, `CORES` | Phase 1: `OFF=1 NOT_OFF=0`. At the end `STARTED=11 REPORTED=11 OFF=11`, the payload's own return was `RETURN=0X0` and its last summary line says `LEFT_RUNNING=0`: no core left running (`AFFINITY_INFO` said OFF for all eleven within the payload's wait). Core `0x100` was started twice | OBSERVED (both boots) |
| 7 | Did anything fault or get refused? | `DONE` | `STACK=OK LOADS=0 FAULTS=0 REFUSED=0 LINES=40 DROPPED=0`. The 40-line budget suppressed six lines in both boots (`SUPPRESSED=6`: the last two lines of core `0x20200`, all three of `0x20300` and the phase 2 `POWER_OFF` line); the summary counts are not affected. The line that lists what was not done reads `NOT_DONE NO_SYSTEM_OFF_RESET_SUSPEND NO_CPU_SUSPEND BOOT_CPU_NO_MSR LEFT_RUNNING=0` (cut by the edge of boot 7's photograph, whole in step 3's video) | OBSERVED (both boots) |
| 8 | Do the payload's cache rules work? | `NO_REPORT` | None of the eleven reports read `NO_REPORT` in either boot: cleaning the image and the slots to the point of coherency before `CPU_ON` and invalidating a slot before reading it were enough for these runs (QEMU, which is coherent, cannot show it) | OBSERVED (both boots) |

**Per core (OBSERVED).** `HCR_EL2` and `VBAR_EL2` are what the stub found at entry (`SCTLR_EL2` was `0x30000000` on every
printed core). The `MIDR_EL1` and entry-state columns are step 3's crisp values, and boot 7's agree with them where they
were legible. `DT_US` is the secondary's first counter read minus the primary's read right after its `CPU_ON` returned,
in microseconds, and differs between the boots; `~` marks a boot 7 value read from the blurred photograph.

| Core (affinity) | Cluster | `MIDR_EL1` | `HCR_EL2` | `VBAR_EL2` | `DT_US`, boot 7 | `DT_US`, step 3 |
| --- | --- | --- | --- | --- | --- | --- |
| `0x100`, first start (phase 1) | 0 | `0x512F0011`, the boot CPU's (`IDS=SAME`) | `0x1488000038` | `0xFFFFFF9CEA002800` | 45 | 45 |
| `0x100`, second start (phase 2) | 0 | same | `0x1488000038` | `0xCE60A800` | ~330 | 294 |
| `0x200` | 0 | same | `0x80000000` | `0x0` | 263 | 288 |
| `0x300` | 0 | same | `0x80000000` | `0x0` | ~2006 | 2001 |
| `0x10000` | 1 | `0x511F0011` | `0x80000000` | `0x0` | 369 | 476 |
| `0x10100` | 1 | `0x511F0011` | `0x80000000` | `0x0` | 425 | 444 |
| `0x10200` | 1 | `0x511F0011` | `0x1488000038` | `0xFFFFFF9CEA002800` | 452 | 458 |
| `0x10300` | 1 | `0x511F0011` | `0x80000000` | `0x0` | ~1999 | 1994 |
| `0x20000` | 2 | `0x511F0011` | `0x80000000` | `0x0` | 416 | 369 |
| `0x20100` | 2 | `0x511F0011` | `0x80000000` | `0x0` | 442 | 428 |
| `0x20200` | 2 | `IDS=MIDR`: differs from the boot CPU's (its `MIDR=` line was dropped) | not printed | not printed | 456 | 440 |
| `0x20300` | 2 | not printed | not printed | not printed | not printed | not printed |

The `VBAR_EL2` values are step 3's. In boot 7 the first start's and `0x10200`'s were an upper-half address ending in
`2800` with some digits not legible, and the second start's an address inside that boot's payload image (`0xCEA8B...`).
Step 2's mid-chain photograph, taken at the second pause and crisp, shows phase 1 only: core `0x100`'s first start with
`DT_US` 47, `SCTLR_EL2` `0x30000000`, `HCR_EL2` `0x1488000038` and `VBAR_EL2` `0xFFFFFFB460602800`. The vector table that
the stub installs sits at the payload image base plus `0x800`: base `0xCE5E5000` in step 2, `0xCE60A000` in step 3 and
`0xCEA8B000` in boot 7 (allocations of one boot each, not constants).

- **`MIDR_EL1` differs by cluster (OBSERVED twice).** The boot CPU and cluster 0 read `0x512F0011`; every line that shows
  a value for clusters 1 and 2 reads `0x511F0011` (six of those eight cores print it, the seventh flags `IDS=MIDR` and
  the eighth was not printed, in both boots). The two values differ in the variant field only (variant 2 against variant
  1; implementer `0x51`, architecture `0xF`, part `0x001` and revision 1 in both: arithmetic on the printed values). Why
  is not established: it is what the cores report. The third-party profile of another X1E80100 machine lists the same
  two values ([SURFACE-PRO-11.md](SURFACE-PRO-11.md), E26).
- **The entry state of the EL2 control registers is not the same on every core (OBSERVED twice, crisp in step 3).**
  Seven cores (`0x200`, `0x300`, `0x10000`, `0x10100`, `0x10300`, `0x20000`, `0x20100`) came up with `HCR_EL2`
  `0x80000000` (RW only, E2H clear) and `VBAR_EL2` 0. Core `0x100` at its first start and core `0x10200` came up with
  `HCR_EL2` `0x1488000038` (E2H, TGE, RW, TERR, AMO, IMO and FMO set: arithmetic on the printed value) and with the same
  16-digit high-half `VBAR_EL2` (2 KiB aligned) within a boot (step 3: both cores). That value differs from boot to
  boot (OBSERVED, two crisp values: step 2's mid-chain photograph read `0xFFFFFFB460602800` for core `0x100`'s first
  start, step 3's `0xFFFFFF9CEA002800` there and on `0x10200`; boot 7's digits were only partly legible,
  `0xFFFFFF...2800`, the same shape). INFERRED, untested, and only the reason: an EL2 image at a high virtual address,
  in the VHE regime (E2H and TGE set), ran on those two cores earlier, at an address that differs per boot (randomised,
  INFERRED); which software is not established. The entry state of `0x20200` and `0x20300` is UNKNOWN: their register
  lines were dropped by the line budget in both boots.
- **`HCR_EL2` and `VBAR_EL2` of core `0x100` survived its own `CPU_OFF` and `CPU_ON` (OBSERVED, step 3; boot 7's blurred
  photograph agrees).** On its second start the core read `HCR_EL2` `0x1488000038` and `VBAR_EL2` `0xCE60A800`, which is
  that boot's payload base plus `0x800`: the vector table that the stub itself installed on its first start, where the
  first start had read the high-half address. So this unit keeps or restores at least those two EL2 registers of a core
  across `CPU_OFF` and `CPU_ON`; whether it does for the others, or for another core, was not read.
- **Start latency.** 45 us for the single phase 1 start in boot 7 and step 3, 47 us in step 2. Of the ten printed
  phase 2 values, eight lie between 263 and 456 us in boot 7 and between 288 and 476 us in step 3; `0x300` and `0x10300`
  took about 2 ms in both (2001 and 1994 us in step 3).

**What this changes (the observations are in the tables above; the consequences below are INFERRED from them, and the
plan is [SURFACE-PRO-11.md](SURFACE-PRO-11.md)).**

1. **SMP through PSCI from EL2 works on this unit (OBSERVED twice: boot 7 and step 3).** `CPU_ON` is accepted for all
   eleven secondaries,
   they start at EL2 with the MMU off (in the ten printed reports), and `CPU_OFF` takes them down again. `CPU_ON` implemented was INFERRED from
   `PSCI_FEATURES` in boot 5; it is now an observation. Measurement M4 of [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) is
   partly answered: `SCTLR_EL2`, `HCR_EL2`, `VBAR_EL2`, `CNTVOFF_EL2`, `CNTFRQ_EL0` and three ID registers were read on
   arrival, but not `TCR_EL2`, `TTBR0_EL2`, `MAIR_EL2` or `DCZID_EL0`, and the arrival of cores `0x20200` and `0x20300`
   is not fully printed. The monitor's own start path, SMP with the MMU on, interrupts and the guest are untouched.
2. **A secondary entry stub must establish all EL2 state itself.** Two cores arrived with `HCR_EL2.E2H` set and seven
   with it clear (the other two are unknown), and `VBAR_EL2` was a high-half address on two of them and 0 on the seven,
   so the model's "E2H UNKNOWN" arrival of a PSCI secondary (SP11-EL2-ENTRY.md, section 2.4) describes this unit. A
   core that is started again can come back with the EL2 state it left (core `0x100`: `HCR_EL2` and `VBAR_EL2`), so a
   stub must not assume that a restarted core is clean. The MMU is off on arrival, as the PSCI specification says.
3. **The cores are not identical.** `MIDR_EL1` differs between clusters, so the monitor's ID-register normalisation
   (`compat/oryon-idregs.c` records both values) and anything keyed on the MIDR are per-core questions (the NV2 record,
   whose MIDR the gate compares exactly with the core it runs on, and the K1 result itself, which was measured on the
   boot CPU, in cluster 0).
4. **A1 and A2 are not affected.** Nothing here wrote an xHC register or a stick sector.

**The board restarted by itself after boot 7 (OBSERVED, operator report; the cause is UNKNOWN, and so is the time).**
After the photographs the operator reported that the second boot restarted right after the last photograph. The time
was not recorded, and the still photographs carry no capture times (their EXIF data is stripped). What the page's
`UPTIME` says has to be read correctly. In q1n1's heartbeat code (`platform/uefi/main.c` of the fork at `d48b0af`, lines
1626 to 1655) `start` is read before the payload runner is called and the heartbeat's `last` begins equal to it, so the
first heartbeat after the chain returns draws `(counter - start) / frequency`, which already includes the whole chain;
the page shows `UPTIME 0 S` while the chain runs (boot 4's photographs, and step 2's, 14.1). The `cpuon` chain holds two
8 s waits, so its first heartbeat reads about 16 to 20 S (in step 3 the chain returned at about `UPTIME 19 to 20 S`).
The last photograph of boot 7 shows `UPTIME 16 S`: it is that first heartbeat, so it was taken within about a second
of the chain's end (INFERRED from the one-second heartbeat). It is not the time of the restart and does not say how
long the page stayed up after the chain. No number was recorded for the restart (UNKNOWN); the operator's "right after
the last photograph" makes it INFERRED to have come seconds after that heartbeat, at about `UPTIME 17 to 25 S`. What
the page showed after that photograph is lost.

- q1n1 does not reset the board by itself in this flow: its idle loop is a `for (;;)` with a heartbeat; it calls
  `SetWatchdogTimer(0)` before ExitBootServices (two sites in `platform/uefi/main.c`); the one `ResetSystem` call is in
  a path that runs after ExitBootServices only to return to the firmware, and not in the payload flow (VERIFIED by
  reading the fork's source; it says nothing about what the firmware does).
- Earlier, a plain q1n1 EL2 page stayed up for about 40 minutes with no restart (operator report, B1 attempt 4, the VHE
  state, an older build). In the de-VHE state a plain page was alive at `UPTIME 123 S` (step 1 of the restart probe,
  14.1). Boot 5's page, after a chain of read-only payloads, was photographed at `UPTIME 3 S` and `UPTIME 10 S`, and the
  restart control's pages, with only the read-only `psci` payload, at `UPTIME 224 S` and `268 S` in two boots (14.1);
  no restart was reported for any of them. Boot 6 also ended in a restart, which is the X2 image's designed reset
  (INFERRED from the page's `RESET IN` countdown).
- So the restart is new, and it coincides with the `cpuon` payload, the first that issued `CPU_ON` and `CPU_OFF`: it
  was reported after two of its three boots (boot 7 and step 2; the time of neither was recorded) and not observed in
  the third (step 3, filmed alive through `UPTIME 36 S`; its state beyond that is UNKNOWN: 14.1). The read-only control
  makes it INFERRED, strongly (evidence, not proof), that the runner flow alone is not what restarts the board, and the
  restart is associated with the `cpuon` payload, that is with its `CPU_ON` and `CPU_OFF` of the secondary cores
  (INFERRED: a correlation over five boots, not a cause). A delayed reaction of a watchdog, or of another firmware
  agent, to the core power cycles is a hypothesis (INFERRED, untested). The cause is UNKNOWN.
- What would separate them: the exact uptime at the restart (film a `cpuon` page until it restarts: the last `UPTIME`
  in the video, with its `creationdate`, gives the time; 14.1); a `cpuon` variant that starts and stops only one core;
  and a variant that leaves the cores running instead of powering them off. The read-only control has run (14.1).

### 14.1 The restart probe (three steps, 2026-11-15) and its control (two steps, 2026-11-16); OBSERVED, one boot per step

**What ran.** The plan `restart-probe` ran three steps, one per boot, each preceded by the de-VHE slbounce build
`SLBOUNCE-DEVHE-MARK.EFI`: step 1, `Q1N1.EFI --el2`, plain q1n1 (SHA-256 prefix `82b63938`), as the control for the page
itself; steps 2 and 3, `Q1N1-PAYLOAD.EFI --el2` with a manifest of the same `cpuon.bin` as boot 7 (SHA-256 prefix
`3b4bfda7`) and the same runner. Nothing was typed in a boot, but the operator started every boot of the stick (power
off, then Volume-Down with Power): after a restart by itself the Surface does not come back to the stick but follows
its normal boot order (operator report after boot 4, section 13), so a plan cannot run unattended and a restart ends
its step. None of the three read or wrote an xHC register or a stick sector at EL2 (A1 and A2 stay NOT granted).

**How the runs were timed (a method for later runs).** The Shell script writes a marker file before each step, here
`DONE1.TXT` to `DONE3.TXT` (`AUTORUN restart-probe STEP n STARTED`). Its FAT time is the firmware's real-time clock,
which reads the same local time (UTC+4) as the operator's phone: 21:38:28, 21:41:20 and 21:42:50 (FAT resolution 2 s).
The operator's videos keep the recording start in the QuickTime `creationdate` metadata, in local time, while the still
photographs have their EXIF data stripped. The two clocks agree within the 2 s resolution: step 1's video started in
the same 2 s slot as its marker, and step 3's started at 21:42:47, 3 s before its marker, as it should if filming began
first. With them a page's `UPTIME 0 S` can be placed on the wall clock (step 3's was at about 21:42:53.5, 3.5 s after
the marker write), and from there every later `UPTIME`. A marker gives only a weak upper bound on an event that the
page cannot show, such as a restart, because the gap to the next marker also holds the operator's power cycle (below);
a video that runs through the restart gives its time directly, from the last `UPTIME` it shows.

| Step | What ran | What was seen | Label |
| --- | --- | --- | --- |
| 1 | plain `Q1N1.EFI --el2` | The page was alive at `UPTIME 123 S` (photographed; the operator then power-cycled). A 30.5 s video, started at 21:38:28, covers its first seconds | OBSERVED |
| 2 | `cpuon` | The operator reported that the board restarted by itself. All five photographs show `UPTIME 0 S` (the heartbeat does not update during the chain; the last was taken mid-chain, at `NEXT CPU_ON ALL AFTER 8 S`, and is crisp: base `0XCE5E5000`, phase 1 `DT_US=47`, `VBAR_EL2=0XFFFFFFB460602800`; Appendix E) and none shows the finished panel, so the report is of the screen going away, with no `UPTIME` | OBSERVED (operator report); the time UNKNOWN (INFERRED, weak: `UPTIME` about 41 S or earlier, below) |
| 3 | `cpuon` | The operator reported that it stayed up, without a duration. A 42.5 s video, started at 21:42:47, shows the page from `UPTIME 0 S` through the chain (it returned at about `UPTIME 19 to 20 S`) and the finished panel at `UPTIME 32 S`, to the end of the video at about `UPTIME 36 S` (21:43:29.5), the finished page still on screen | OBSERVED alive through `UPTIME 36 S`; UNKNOWN beyond |

**When did step 2 restart? (UNKNOWN; INFERRED, weak: `UPTIME` about 41 S or earlier).** The operator reported that it
did, and no time was recorded. No photograph shows the page after the chain: the last is mid-chain. `DONE3` minus
`DONE2` is 90 s, plus or minus 2 s, and that gap holds three things: the time from step 2's marker to its restart; the
operator noticing it and holding Power; and the operator's Volume-Down with Power, the firmware's boot, the Shell's
countdown and the marker write of step 3. The last two took 45 s once, between step 1 and step 2 (from the photograph
at `UPTIME 123 S`, taken at about 21:40:35, to `DONE2` at 21:41:20, the same actions; OBSERVED once), and 52 s between
the two steps of the restart control (below). If the operator needed at least as long this time, step 2's restart came
no later than about 45 s after `DONE2`, that is at about `UPTIME 41 S` or earlier (its page started about 3.5 s after
the marker). It may have come much earlier, even before the chain ended: the page's `UPTIME` stays 0 S during the
chain, which lasts about 20 s. Both the assumed pace and the one-sided bound make this weak. For boot 7 see section 14
(INFERRED about `UPTIME 17 to 25 S`; no number recorded).

**The restart control (2026-11-16; plan `restart-control`, two steps; OBSERVED, one boot per step).** It used the same
runner (`Q1N1-PAYLOAD.EFI --el2`) and the same slbounce build (`SLBOUNCE-DEVHE-MARK.EFI`) as boot 7 and steps 2 and 3,
but its manifest listed only `PSCI.BIN`, the read-only `psci` payload of boot 5 (4,285 bytes, SHA-256 prefix `d391a9c8`;
the panel shows `SHA=D391A9C8CBEECD00`): it calls `PSCI_VERSION`, `PSCI_FEATURES`, `AFFINITY_INFO` and `SMCCC_VERSION`,
and no `CPU_ON` and no `CPU_OFF`. The markers `DONE1.TXT` and `DONE2.TXT` (`AUTORUN restart-control STEP n STARTED`) read
00:16:02 and 00:20:42 (local time, FAT resolution 2 s). The operator photographed each page once and powered off; two
short videos (22.6 s from 00:16:00 and 12.6 s from 00:20:43, `creationdate` metadata) cover only the first seconds of
each page. No restart was reported. A1 and A2 stay NOT granted; no device register or stick sector was written.

| Control step | What ran | What was seen | Label |
| --- | --- | --- | --- |
| 1 | the runner, `psci` only | A crisp photograph shows the page at `UPTIME 224 S` (about 00:19:50 on the wall clock: the page started about 4 s after its marker), with the payload's 15 lines and `DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=0 FAULTS=0 REFUSED=0 LINES=15 DROPPED=0`; the answers are boot 5's (`PSCI_VERSION` `0x10001`, the eleven secondaries OFF) | OBSERVED once; alive at 224 S, the state afterwards unknown |
| 2 | the same | A crisp photograph at `UPTIME 268 S` (about 00:25:14), with the same 15 lines | OBSERVED once; alive at 268 S, the state afterwards unknown |

So the runner flow with a read-only payload kept the page up to `UPTIME 224 S` and `268 S` in two boots, far past the
17 to 25 S and 41 S or earlier at which the `cpuon` restarts are estimated (INFERRED, weak), while `cpuon` was followed
by a restart in two of three boots. INFERRED, strongly (evidence, not proof): the runner flow alone is not what restarts
the board. If it did so in two boots of three, the chance that both read-only boots survived is about 11 %, that is
(1/3) x (1/3), a rough figure that treats the boots as independent and takes the two in three from only three `cpuon`
boots. INFERRED: the restart is associated with the `cpuon` payload, that is with its `CPU_ON` and `CPU_OFF` of the
secondary cores, a correlation over five boots and not a cause; the `cpuon` payload also differs from `psci` in its
two 8 s waits and in the code the secondaries run, so the association is with its whole sequence until a variant takes
it apart. The cause is UNKNOWN and so are the restart times.

**What is solid, and what is not.**

- A plain q1n1 page was alive at `UPTIME 123 S` (step 1 of the restart probe, the de-VHE state, one boot), past every
  restart estimate above: in that boot the page itself did not restart.
- The runner with only the read-only `psci` payload was photographed alive at `UPTIME 224 S` and `268 S` in two boots of
  the restart control (OBSERVED once each; the state after each photograph is unknown, and the operator reported no
  restart). So the read-only runner flow is no longer unobserved beyond `UPTIME 10 S` (boot 5).
- A restart was reported after two of the three `cpuon` boots (boot 7 and step 2); the time of neither was recorded
  (UNKNOWN). Step 3 was OBSERVED alive through `UPTIME 36 S` (video; the operator's "stayed up" has no duration), and
  its state beyond that is UNKNOWN.
- INFERRED, and weak: the restart is not at one fixed `UPTIME`. Step 3 was alive through 36 S, step 2's restart is
  estimated at 41 S or earlier and boot 7's at 17 to 25 S; one common time would have to fall between 36 and 41 S,
  which boot 7's estimate does not allow. Both estimates rest on the operator's wording and pace.
- INFERRED, strongly (evidence, not proof): the runner flow alone is not what restarts the board, because both
  read-only boots were alive at 224 S and 268 S (the 11 % chance above).
- INFERRED: the restart is associated with the `cpuon` payload, that is with its `CPU_ON` and `CPU_OFF` of the
  secondary cores (a correlation over five boots: two restarts in three `cpuon` boots, none reported in two read-only
  boots). The cause is UNKNOWN, and so are the restart times.

**What would settle it next (NOT RUN).** The read-only control is done (above). Next: film a `cpuon` page from its start
until it restarts, or for minutes if it does not, keeping the video's `creationdate`: the last `UPTIME` the video shows
is the time of the restart. Run a `cpuon` variant that starts and stops only one core, and a variant that leaves the
cores running instead of powering them off. Each is a boot of its own, started by the operator and needing the
operator's decision like every boot; none writes to a device, and A1 and A2 stay NOT granted.

### 14.2 Logging, the stick, and what is not shown

**What this changes for logging.** The panel stays the only log channel. No stick write at EL2 exists (S1 to S6 are
UNIMPLEMENTED; A1 and A2 stay NOT granted), so what the panel showed after the last photograph is lost, and the one fact
that matters most about the restarts, when the board restarted, is exactly what a page cannot show. The restart probe
found a way to put a page's `UPTIME` on the wall clock (a video's `creationdate` with the stick's marker times, 14.1),
but as run it did not time a restart: the next marker only bounds one from above, and weakly, because the operator's
power cycle sits in the gap, and step 3's video, the only one of a `cpuon` page, ended at `UPTIME` about 36 S with the
page still up. A video that runs through a restart would give its time directly. Filming the page (alternative (v), Q6)
is therefore the cheap answer for timing; a persistent log (B3) would be the lasting one and is not advanced by these
boots. The runner's own line budget cost six lines in each `cpuon` boot (counts intact). After a restart by itself the
board does not come back to the stick (14.1; section 13), so each boot of the stick needs the operator.

**One fact for the stick question (OBSERVED, `pciio.txt` of boot 6, the firmware's PCI I/O view).** It lists four USB
controller instances, with BAR0 `0x0A000000`, `0x0A400000`, `0x0A200000` and `0x0A600000`, and no PCI location.
Section 12 recorded that firmware lists four xHCI handles and that the survey showed three of them (BAR0 `0x0a000000`,
`0x0a400000`, `0x0a200000`): those three are in this list, so the fourth is the one at `0x0A600000`, the `usb_1_ss0`
window (INFERRED by elimination). `0x0A800000`, the window of `usb_1_ss1`, is not among them. (Caveat, section 17: the list was taken with the stick in the
port the operator usually uses, INFERRED to be the second port; with the stick in the top port the firmware appears to start `usb_1_ss1`, and what its
listing shows then was not read, UNKNOWN.) Nothing was read from any
of them at EL2 in these boots.

**What these runs do not show.** The cause of the restarts and their times (UNKNOWN for boot 7 and step 2; the
estimates of 14.1 and section 14 are INFERRED and weak); whether step 3 restarted after `UPTIME 36 S`; what the
restart control's pages did after their photographs at `UPTIME 224 S` and `268 S` (the operator powered them off); which
part of the `cpuon` sequence goes with the restarts (`CPU_ON`, the code the secondaries run, `CPU_OFF` or the 8 s
waits); what the panel showed after the last photograph of boot 7; repeatability beyond three `cpuon` boots and two
control boots on one unit; the arrival state of
cores `0x20200` and `0x20300` (their lines were dropped from both panels); `TCR_EL2`, `TTBR0_EL2`, `MAIR_EL2` and
`DCZID_EL0` on a secondary; anything about a secondary with the MMU on, interrupts or the guest; why the PCIe SMMU
registers fault at EL2; why `MIDR_EL1` and the entry state differ between cores, or why the high-half `VBAR_EL2` differs
between boots (no cause is claimed); any controller reset or DMA on the NVMe.

## 15. Result of boot 14, the third GIC and stop dry run (`GICDRY-C`), on the unit (OBSERVED, one unit, one boot)

Boots 8 to 13 (2026-11-16: the memhash, dry-run, timers and first two GIC boots) are recorded in
[SP11-STICK-WRAPPER.md](SP11-STICK-WRAPPER.md), [SP11-STICK-GIC.md](SP11-STICK-GIC.md) (section 12) and the evidence table of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) (E65 to E69), not in this log. This section adds the next boot. It touches this study's
question (a log from EL2 to the stick) in two ways only: the panel was again the only record, and it was read from a video. Everything
below is OBSERVED, from one video (60 s, 4K, one take; frames at 1 fps and the final page at full resolution; the camera moved and the
page was stable), one unit, one boot; the decodes (arithmetic on printed values, and what a value means) are INFERRED where said.

**What ran.** On 2026-11-17 the autorun plan `GICDRY-C` ran one step in one boot: the Shell's startup script (its countdown is on the
first frame; the stick at `Pci(0x80,0x0)/USB(0x1,0x0)`: one USB node, the direct connection of boots 3 to 7), q1n1's EL2 page, then
the payload runner with five files and the wrapper's GIC and stop variant of work item 8 ([SP11-STICK-GIC.md](SP11-STICK-GIC.md):
passes 5 and 6, the quarantine of the foreign enabled interrupts and its put-back rule). The bundle was the authorised one: the
operator named its record by its SHA-256 for this boot (this log does not repeat the digest) and the committed tree is 6ef8dbd. The wrapper
never calls the monitor. A1 and A2 were not exercised: the wrapper touches no xHC register and no stick sector, the step's marker
`SP11\DONE1.TXT` was written by the Shell before the step (the bundle is single-shot), and nothing else was written by the run (the
results note).

| Index | File | Bytes | SHA-256 prefix on the page |
| --- | --- | --- | --- |
| 0 | the wrapper, `SNAPMON-GIC.BIN` | 98,236 | `EBAC8675A246DFDB` |
| 1 | the reporter, `SNAPREP-GIC.BIN` | 62,000 | `89B8FAB1F6DADD72` |
| 2 | `GUEST.DAT` | 144,665,408 | `0E32E28F3ABCBD97` |
| 3 | `PIN.DAT` | 1,024 | `AA1397BB81C60EAC` |
| 4 | `REPORT.DAT` | 528,384 | `2AF25E8AC647A885` |

The runner printed `PAYLOAD VERIFY=OK` and the wrapper `PIN_OK LABEL=GICDRY-C CHUNKS=16`.

**The video, in time (OBSERVED).**

| Video time | What is on the panel |
| --- | --- |
| 1 s | the UEFI Shell: "Press ESC in 2 seconds to skip startup.nsh" |
| 17 s | q1n1's EL2 page right after ExitBootServices, `UPTIME 0 S` |
| 22 to 24 s | the runner's page: the `PAYLOAD` lines, the `SNAPMON` stages and `NEXT GIC STORES AFTER 8 S` (the first NEXT line, at about 24 s) |
| 34 s | `NEXT GIC WINDOW 2 S`, the IRQ line and `NEXT GIC EL1 EXCURSION AFTER 8 S` |
| 46 s | the VERDICT and the reporter's block, about 21 s after the first NEXT line (the record said about 20 s and at most about 24 s) |
| 53 to 57 s | the page alternates with q1n1's page, whose `UPTIME` reads 37 S and 38 S (17 s + 37 s = 54 s): no restart |

**The page lines that matter** (capital letters only, as the page folds case; the addresses of the stub and its table are one boot's
allocation, not constants). The wrapper:

```text
SNAPMON PLACE BASE=0X880000000 SIZE=0X2A000000 DESC=217 DBASE=0X880000000 DPAGES=0X780000
SNAPMON STAGE=COPIED COPIED=16 BYTES=148284944 HASHED=13 HASHED_BYTES=144664080
SNAPMON GIC GICD CTLR=0X10 ARE=1 DS=0 G1A=0 NEED_G=1 TYPER=0XF87F051E IIDR=0X401043B
SNAPMON GIC GICR IDX=0 TYPER=0XD0000E7 WAKER=0X0 ISEN=0X4E800001 PEND=0 ACT=0
SNAPMON GIC CPU SRE=0XF EOIMODE=1 PMR=0XF8 BPR1=0X3 IGRPEN1=0 PRIO=0XA0 CFG=0X0 GRP=0 CTL=0X2
SNAPMON NEXT GIC STORES AFTER 8 S G=1 R=1 C=1 T=1 V=1 E=1
SNAPMON NEXT GIC WINDOW 2 S GICD=0X12 ISEN=0X4000000 PRIO=0X80 PMR=0XF8 IGRPEN1=1 HPPIR=1023
SNAPMON GIC IRQ INTID=26 LAT_US=0/0/0/0 OK=1111 FINAL=CLEAR
SNAPMON NEXT GIC EL1 EXCURSION AFTER 8 S HCR=0X80000010 SCTLR1=0X30500800 VBAR1=0XCEA8F000 PCT=1
SNAPMON GIC EL1 INTID=26 LAT_US=0 ON_TIME ELR=0XCEA8F798 SPSR=0X345 OK=1
SNAPMON GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRCTVE VBAR=OK EL1=OK
SNAPMON GIC VERDICT=IRQ_OK OUTCOME=0X20 SUB=DONE
SNAPMON TICKS HZ=19200000 PLACED=47666 COPIED=37684016 HASHED=56123297
SNAPMON STAGE=DONE RETURN=0
SNAPMON MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD
PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=2 FAULTS=0 REFUSED=0 LINES=29 DROPPED=0
```

The reporter, which decodes the record the wrapper left in RAM:

```text
SNAPREP DRY RUN COMPLETE STAGE=DONE: THE MONITOR WAS NOT CALLED
SNAPREP GIC VERDICT=IRQ_OK OUTCOME=0X20 SUB=DONE
SNAPREP GIC OLD PRIO=0XA0 ICFGR1=0X0 IGROUPR0=0X0 ISPEND=0X8000000 ISACT=0X0
SNAPREP GIC SPI LINES=30 EN=3 PEND=9 BOTH=0 FIRST=0 SECOND=0
SNAPREP GIC PPIP P23=0XA0 P25=0XA0 P27=0XA0 P30=0XA0
SNAPREP GIC TIMERS VCTL=0X5 VCVAL=0X2843B1D7 PCTL=0X0 PCVAL=0X0
SNAPREP GIC QUAR MASK=0X4A800001 DONE=0X4000000 HPPIR=1023
SNAPREP GIC NEW GICD=0X13 RB=0X12 ISEN=0X4000000 PRIO=0X80 PMR=0XF8 IGRPEN1=1
SNAPREP GIC IRQ INTID=26 LAT_US=0/0/0/0 OK=1111 FINAL=CLEAR
SNAPREP GIC CYC0 IAR=26 HPPIR=26 POST=1023 F=100 TICKS=1 US=0
SNAPREP GIC CYC1 IAR=26 HPPIR=26 POST=1023 F=100 TICKS=0 US=0
SNAPREP GIC CYC2 IAR=26 HPPIR=26 POST=1023 F=100 TICKS=0 US=0
SNAPREP GIC CYC3 IAR=0 HPPIR=26 POST=1023 F=100 TICKS=0 US=0
SNAPREP GIC EL1 INTID=26 LAT_US=0 ON_TIME ELR=0XCEA8F798 SPSR=0X345 OK=1
SNAPREP GIC CYC4 IAR=26 HPPIR=26 POST=1023 F=100 TICKS=2 US=0
SNAPREP GIC HCR OLD=0X88000010 RB=0X80000010 FIN=0X88000010
SNAPREP GIC VBAR1 OLD=0XA7004000 RB=0XCEA8F000 FIN=0XA7004000
SNAPREP GIC SCTLR1 OLD=0X100030D0198D RB=0X30500980 FIN=0X100030D0198D
SNAPREP GIC FINAL GICD=0X10 ISEN=0X4E800001 PRIO=0XA0 PMR=0XF8 IGRPEN1=0 RPR=0XFF DAIF=0X3C0
SNAPREP GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRCTVE VBAR=OK EL1=OK
PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=1 FAULTS=0 REFUSED=0 LINES=32 DROPPED=0
```

`CYC3` is the final deadline taken without an acknowledge, by design (`IAR=0`: none read), as the monitor's stop path takes it.
The reporter's block is the record's lines in the order of [SP11-STICK-GIC.md](SP11-STICK-GIC.md) section 6.

**What it answers, against the record's UNKNOWN list.**

| # | Question | Page lines | What the unit showed | Label |
| --- | --- | --- | --- | --- |
| 1 | Is PPI 26, the EL2 physical timer's interrupt, delivered to EL2 as an IRQ (U3)? | `GIC IRQ`, `CYC0` to `CYC3` | Yes, once group 1 and the CPU interface are on (`GICD_CTLR` written `0x13`, read back `0x12`; `IGRPEN1` 1; `PMR` `0xF8`; priority `0x80`): four deliveries in window 1, each with `HPPIR=26` at the vector and `POST=1023` afterwards, `IAR=26` in the first three, `F=100` (ISTATUS 1 and the other two flags 0, in the format of [SP11-STICK-GIC.md](SP11-STICK-GIC.md) section 6) | OBSERVED |
| 2 | How late after the programmed deadline? | `LAT_US`, `TICKS` | `LAT_US=0/0/0/0`; 1, 0, 0 and 0 counter ticks in window 1 and 2 in window 2 (19.2 MHz): at most about 0.1 microsecond, by arithmetic on the printed values | OBSERVED (the figures); INFERRED (the conversion) |
| 3 | Did the quarantine of the other enabled interrupts hold? | `QUAR`, `NEXT GIC WINDOW 2 S` | The non-secure write of `ICENABLER0` with `0x4A800001` left `ISENABLER0` `0x04000000` (PPI 26 only) and `ICC_HPPIR1_EL1` read 1023; no `Q_NOT_HELD`, no `FOREIGN_PENDING` | OBSERVED |
| 4 | Is an interrupt taken while the CPU runs at EL1 routed to EL2? | `GIC EL1`, `CYC4` | Yes: INTID 26, `ON_TIME`, `ELR` inside the stub, `SPSR` `0x345` (EL1h with I clear) under `HCR_EL2` `0x80000010` (IMO 1, TGE clear), `IAR=26`, `HPPIR=26`, `POST=1023` | OBSERVED; INFERRED (the decode of `SPSR`) |
| 5 | Was everything put back, the quarantined enables last? | `HCR`, `VBAR1`, `SCTLR1`, `FINAL`, `RESTORE` | Yes: `ISENABLER0` `0x4E800001`, `GICD_CTLR` `0x10`, `IGRPEN1` 0, `PMR` `0xF8`, `HCR_EL2` `0x88000010`, `VBAR_EL1` `0xA7004000`, `SCTLR_EL1` `0x100030D0198D`, `DAIF` `0x3C0` and `RPR` `0xFF`, each as found; `CLEAN=YES FAILED=0X0 RETRIED=0X0` | OBSERVED |
| 6 | What was the foreign interrupt of boot 13? | `TIMERS`, `OLD` | `CNTV_CTL_EL0` `0x5` (ENABLE 1, IMASK 0, ISTATUS 1), `CNTV_CVAL_EL0` `0x2843B1D7`, `CNTP_CTL_EL0` 0, and `ISPENDR0` `0x08000000` again before any store: the EL1 virtual timer was asserting. That it is the source of INTID 27 follows from the GTDT's Virtual EL1 GSIV 27 | OBSERVED (the timer asserting); INFERRED (that INTID 27 is its) |
| 7 | Is any SPI both enabled and pending? | `GIC SPI` | `LINES=30` (INTIDs 32 to 991): 3 SPIs enabled, 9 pending, none both (`BOTH=0`); the counts are of INTIDs, as the stage's survey makes them | OBSERVED |
| 8 | The priorities of the other enabled PPIs | `GIC PPIP` | `0xA0` for each of PPIs 23, 25, 27 and 30 | OBSERVED |
| 9 | How long did the stage take? | the video | About 21 s from the first NEXT line to the VERDICT; the tick marks of the copy and the hash, 37,684,016 and 56,123,297 (1.96 s and 2.92 s at 19.2 MHz), as in the earlier boots | OBSERVED |
| 10 | Did the page stay up? | q1n1's `UPTIME` | 37 S and 38 S at 53 to 57 s of the video; no restart in the video, and nothing is known beyond its end | OBSERVED (alive to `UPTIME` 38 S); UNKNOWN (after) |

**What it does not show.**

- Extended SPIs and LPIs (`GICD_TYPER` ESPI 1, `GICR_TYPER` PLPIS 1; the decode of the two registers is INFERRED) were neither
  surveyed nor quarantined; the one `ICC_HPPIR1_EL1` read before window 1 saw 1023, at one instant. UNKNOWN whether any is enabled and pending.
- Which SPIs are enabled or pending: the page holds counts, not the registers (UNKNOWN).
- The I-set entry: window 2 ran with PSTATE.I clear, while the monitor enters its guest with it set (UNKNOWN).
- Anything about a guest: nothing of it ran and the monitor was not called (`UNIMPLEMENTED IN THIS BUILD`). Whether the interrupt state
  survives a guest is UNKNOWN.
- The exception of pass 6, the withheld last write behind two open group 1 gates, was not exercised: both gates went back.
- PPI 26's group, beyond what `IAR=26` implies (INFERRED, the specification was not re-read), and the `PEND` field of the `GICR` line,
  which this transcription does not decode (`ISPEND` `0x8000000` is the whole `ISPENDR0`).
- The restarts of 14.1: this boot ran no `CPU_ON`. It adds one page seen alive (to `UPTIME` 38 S, then the video ends), not a restart time.

**What follows.** By the record, after any window verdict the series stops: the next work is the M1 stop design (D2), not a retry
([SP11-STICK-GIC.md](SP11-STICK-GIC.md) section 11). For this log nothing changes: the panel is still the only channel, no stick
write at EL2 exists (S1 to S6 are UNIMPLEMENTED), and A1 and A2 were not exercised by this boot.

## 16. Result of boots 15 and 16, the A1 xHCI S1 No-Op DMA test (`XHCI1.BIN`, `usb_1_ss1`), on the unit: the screen went off at the end of the wait before the first RUN store and the unit stayed dark (OBSERVED, one unit; boot 16 on video, boot 15 from memory)

Boots 15 and 16 (2026-11-17) ran the plan `a1-xhci-s1`: authorisation point A1 (sections 5 and 11), the first xHC register stores at EL2,
on the controller `usb_1_ss1` (the stage-S1 No-Op DMA test of [SP11-XHCI-S1.md](SP11-XHCI-S1.md); that runbook is pinned by the digest of
the approved record and is not edited, so where it says that the unit's `DBOFF`, `RTSOFF` or scratchpad count are not recorded, that the
meaning of `FSR` `0x400` is UNKNOWN (it is the FORMAT field and records no fault: row 3 below), or reads a page that stops after a `NEXT`
line as a hang or a reset, this section is the later word). The operator approved the record of A1 (by its
SHA-256, which this log does not repeat); A2 was neither granted nor exercised. The statements of sections 4.7, 12 and 13 that A1
is NOT granted or that S1 has not started describe the time of those sections. Boot 16 was filmed in two videos (29 s and 42 s, 4 to 5 s
apart); the page is cumulative, every line was read on several frames and the digits agree. Boot 15 was not filmed. Everything about boot
16 below is OBSERVED (one unit, one boot) unless a label says otherwise; the decodes (what a value means) are INFERRED.

**What ran.** The Shell's script ran one step (`AUTORUN a1-xhci-s1 STEP 1 OF 1`), then the bounce, q1n1's EL2 page and the payload runner
with one payload, `XHCI1.BIN` (19,798 bytes; the page prints `SHA=989324B52E66C212`, the first 16 digits of the authorised file's SHA-256;
loaded at `0xCEAB1000` in boot 16). The payload reads the controller and the SMMU, watches the PORTSC of both controllers for 30 s while the
operator plugs the Mac's cable at an on-screen cue, then runs five groups of stores, each announced by a `NEXT ... AFTER 8 S` line and
started after that wait: ARENA (RAM only), RESET, PROGRAM, RUN, NOOP. The stick was plugged straight into the unit; which port held it is not
in the readings, so whether `usb_1_ss1` was the stick's controller is UNKNOWN (`usb_1_ss0` is the likely one, INFERRED, section 13). Section 17, written later,
reads boot 16's Shell map line, `Pci(0x80,0x0)`, as the `usb_1_ss0` side: INFERRED, `usb_1_ss1` did not hold the stick in boot 16.

| Boot | Evidence | What is known |
| --- | --- | --- |
| 15 | none: not filmed; the operator's memory | The screen went off too (his first answer was that it ran to END; he has since said that both boots ended with the screen off, and that he is unsure of the details). Its page, VERDICT, arena and the moment the screen went off are UNKNOWN |
| 16 | two videos, every line read on several frames | The page below; the screen went off at the end of the wait after `NEXT RUN AFTER 8 S`; no VERDICT, END or fault page |

**The videos, in time (OBSERVED unless marked).** The payload's own clock starts the watch at 0 and puts the `NEXT` lines 8 s apart (the
runbook's table, checked against the video).

| Video, time | Payload, s | What is on the panel |
| --- | --- | --- |
| 1, 0 to about 12 s | before 0 | the Shell's countdown, the autorun lines, the bounce, q1n1's EL2 page (`EL2 CONFIRMED AFTER EXITBOOTSERVICES`) |
| 1, about 25 s | watch T=14.7 s | the Mac's cable is plugged (INFERRED from the first `CHG` line; the camera leaves the panel at 26 s and the video ends at 29 s) |
| 2, 0 to 7 s | about 23 to 30 (INFERRED) | the end of the watch: `WATCH 10 S LEFT`, `WATCH DONE CHANGED=U1P2 N=2` |
| 2, about 7 s and about 15 s | 30 and 38 (INFERRED) | `NEXT ARENA`, `NEXT RESET` |
| 2, 22.5 to 23.5 s | 46 | `NEXT PROGRAM` (read at 1 frame per second) |
| 2, 30.9 to 31.2 s | 54 | `NEXT RUN AFTER 8 S` first on the page (4 frames per second) |
| 2, 39.1 to 39.3 s | about 62 | the screen goes dark (10 frames per second) |
| 2, to 41.8 s | | still dark: no further line |

The screen went off 8.15 s (7.95 to 8.35 s) after `NEXT RUN AFTER 8 S` was drawn: the end of the 8 s wait that the payload makes before
the first store of the RUN group, a single store, `USBCMD` with RS.

**The page of boot 16** as read at 38.5 s of the second video (capital letters only, as the page folds case; the addresses are one boot's
allocations, not constants):

```text
PAYLOAD LOADED INDEX=0 BASE=0XCEAB1000 BYTES=19798 SHA=989324B52E66C212 TYPE=1
PAYLOAD READY FILES=1 RANGES=4 CODE=1
PAYLOAD VERIFY=OK
PAYLOAD CALL 0 989324B52E66C212
PAYLOAD XHCI U1 ID=0X33313130 CAPL=0X20 VER=0X110 HCS1=0X2000340 HCS2=0X140000F1 HCC1=0X110FFC5
PAYLOAD XHCI U1 SCRATCH=2 PAGESZ=0X1 AC64=1 CSZ=64 DBOFF=0X2000 RTSOFF=0X1000
PAYLOAD XHCI U1 CMD=0X0 STS=0X1:HCH CRCR=0X0 DCBAAP=0X0 CFG=0X0
PAYLOAD XHCI SMMU STREAM=0X1460 SMR=4 TRANS CB=3 NCB=0X5F
PAYLOAD XHCI SMMU0 GFSR=0X0 FSR=0X400 FAR=0X153F9FF8F7F3 FSYNR0=0X660D5D
PAYLOAD XHCI ARENAS 2=OK 3=OK 4=OK
PAYLOAD XHCI WATCH 30 S: CONNECT THE MAC USB-C CABLE TO THE FREE PORT NOW
PAYLOAD XHCI BASE U0P1=0X20002A0 U0P2=0X24002C0 U1P1=0X20002A0 U1P2=0X20002A0
PAYLOAD XHCI WATCH 20 S LEFT
PAYLOAD XHCI CHG T=14.7 U1P2 FROM 0X20002A0 TO 0X20002E0:POLL
PAYLOAD XHCI CHG T=15.7 U1P2 FROM 0X20002E0 TO 0X24002C0:INACT
PAYLOAD XHCI WATCH 10 S LEFT
PAYLOAD XHCI WATCH DONE CHANGED=U1P2 N=2
PAYLOAD XHCI NEXT ARENA 2 STACK 0XCEA7B000 PAGES 3 AFTER 8 S
PAYLOAD XHCI NEXT RESET AFTER 8 S
PAYLOAD XHCI NEXT PROGRAM AFTER 8 S
PAYLOAD XHCI NEXT RUN AFTER 8 S
```

Nothing follows: no `R1` line, no VERDICT, no END, no fault page; q1n1's left column is unchanged and the heartbeat was never reached
(`UPTIME` read 0 S on its page).

**What it answers.**

| # | Question | Page lines | What the unit showed | Label |
| --- | --- | --- | --- | --- |
| 1 | Did the authorised file run? | `LOADED`, `VERIFY`, `CALL` | Yes: the 16 digits are the first of the file's SHA-256, `VERIFY=OK`, and q1n1's page confirmed EL2 | OBSERVED |
| 2 | What does the xHC of `usb_1_ss1` report? | the three `U1` lines | `DBOFF` `0x2000`, `RTSOFF` `0x1000` and 2 scratchpad buffers (`HCS2` `0x140000F1`: count 2, restore bit set); 64 slots, 3 interrupters, 2 ports (`HCS1` `0x2000340`); 64-bit addressing, 64-byte contexts, 4 KiB pages supported (`PAGESZ` `0x1`), `CAPLENGTH` `0x20`, version `0x110`, `HCC1` `0x110FFC5`; halted (`STS` `0x1`, `HCH`) with `CMD`, `CRCR`, `DCBAAP` and `CONFIG` all 0 and no `HCE` or `HSE` | OBSERVED (`DBOFF`, `RTSOFF` and the scratchpad count are new: the simulator's `0x2000` and `0x1000` were INFERRED); INFERRED (the field decodes; bit 3 of `HCC1`, PPC, reads clear, so the record's hazard (f), `HCRST` removing port power, is not expected here) |
| 3 | Is the SMMU state the one S0b read? | `SMMU`, `SMMU0` | Stream `0x1460` is `TRANS` through context bank 3 (`SMR=4`, `NCB=0x5F`); `GFSR` 0; `FSR` `0x400` reads as in S0b and is the FORMAT field (bits 10 to 9, `0b10`), not a fault bit, so no fault is recorded in the bank (section 13, row 6: VERIFIED as what the pinned `arm-smmu.h` says, INFERRED for the unit); `FAR` and `FSYNR0` differ from S0b's and, with no fault recorded, are not fault records (INFERRED); their meaning stays UNKNOWN | OBSERVED (the values); INFERRED (what they mean) |
| 4 | Which controller serves the free connector, and does a link form? | `BASE`, `CHG`, `WATCH DONE` | After the cable was plugged `U1P2`, port 2 of the payload's own controller, went from `0x20002A0` to `0x20002E0` at T=14.7 s and to `0x24002C0` at T=15.7 s; `U1P1`, `U0P1` and `U0P2` did not change. Decode: port power and wake-on-connect set throughout; link state 5 (RxDetect), then 7 (Polling), then 6 (Inactive) with the link-state-change bit. So the cable's port is wired to port 2 of `usb_1_ss1`, a SuperSpeed port, and its link went Inactive about a second after the first change: no link formed with the Mac (the page shape of O12, `CHANGED=U1P2`, but no connection, CCS, was ever set). `BASE` already showed `U0P2` at `0x24002C0` before the cable: why is UNKNOWN | OBSERVED (the values); INFERRED (the decode: the PORTSC layout is from memory) |
| 5 | How far did the sequence get? | `NEXT ARENA` to `NEXT RUN` | The page advanced through `NEXT ARENA`, `NEXT RESET` and `NEXT PROGRAM` to `NEXT RUN`, 8 s apart, with no `R1`, VERDICT or END. By the runbook's page structure (a failure in those groups prints `R1`, a VERDICT and END), the ARENA group, the RESET group (HCRST, then HCRST and CNR clear within their bounds) and the PROGRAM group (the five read-backs held) completed: 11 register stores | OBSERVED (the page); INFERRED (the stores) |
| 6 | What happened at `NEXT RUN`? | the screen | The screen went off 8.15 s (7.95 to 8.35 s) after `NEXT RUN AFTER 8 S` was drawn, the end of the wait before the first RUN-group store, and stayed off to the end of the video (2.6 s). That the store is what the screen followed is INFERRED from the payload's nominal wait; the cause is UNKNOWN | OBSERVED (the screen); INFERRED (the link to the store) |
| 7 | What did the unit do afterwards? | not on video | The operator reports that it stayed dark and that he never turned it off; nothing came back, and no restart was seen. It was still a little warm about 30 minutes after the test (his impression, not a measurement). When and how it was powered off afterwards are not recorded | OBSERVED (operator report, from memory) |

**The Mac side (OBSERVED).** The logger ran from before the boot until some minutes after the screen went off: 989 ticks, no USB device or
network interface added or removed, 0 errors. A1 presents no device, so a quiet log was expected during the run; after the screen went off
it also shows that the unit did not enumerate as a USB device on the plugged cable (INFERRED: that cable was in the port wired to
`usb_1_ss1`; a mode that uses another port is not excluded).

**Boot 15 (not filmed; the operator's memory).** The operator first answered that it ran to END; he has since said that both boots ended
with the screen off, and that he is unsure of the details. Its VERDICT, its arena, whether it reached `NEXT RUN` and its `WRITES` count are
UNKNOWN. In boot 15 the cable was plugged when the page said `WATCH 20 S LEFT` (about 10 to 11 s into the watch; boot 16: 14.7 s), so the
cable's timing is not a known difference between the boots. The runner's payload base moved between the boots of
this log (from `0xCE5E5000` to `0xCEC90000` in section 14 and Appendices D and E, and `0xCEAB1000` in boot 16), so arena 2, the runner's
stack (`0xCEA7B000` in boot 16), probably moved too: boot 15's address and the port of its stick are UNKNOWN. That the ending repeated
rests on the operator's memory for boot 15 and on one video for boot 16; whether it depends on the arena or on anything else is not tested.

**The stop rules applied.**

- O10 of the runbook (no VERDICT: the page stops, or it hangs): the page ended at `NEXT RUN AFTER 8 S` and the screen went off at the end
  of the wait, the shape that O10 reads as the first store of that group having hung the bus or reset the SoC. No restart was seen, so of
  those two readings the hang fits (INFERRED); a dead display with a live SoC is a third, which the runbook does not list. The runbook
  marks O10 an SC1 candidate; nothing is authorised by that, and the clock, GDSC and PHY writes it would lead to stay UNIMPLEMENTED.
- SC3 (five boots at S1 without a No-Op completing, or two boots with an unexplained Host System Error): boot 16 is one boot at S1 without
  a No-Op completing; boot 15 is a second if it ended the same way, which is UNKNOWN (its page was not read): the count is 1 or 2 of 5 (4 of 5, or 3, after
  boots 17 and 19: section 17).
  No HSE was read in either boot, so the second limb is not counted.
- The runbook's own rules (4.3, 4.4): no `XHCI0.BIN` boot, no retry in the same boot, the unit is power-cycled before any further boot,
  and no further xHC write until the screen-off is understood (the operator waived the stop for boots 17 and 19, one boot each: section 17).
- The restarts of 14.1 (after two of three `cpuon` boots) are not this event: the firmware's normal boot followed them, and here the screen
  never came back. Their question, what restarts the board after `cpuon`, is untouched.

**What the run does not show.**

- The cause. The hypotheses that nothing here separates, none tested (UNKNOWN): a bus or interconnect hang from the first xHC access after
  RS (the CPU stuck on the store, or the whole SoC stalled); the display subsystem stopping (a scanout underrun, or a clock or power
  domain) with the SoC alive; a live SoC and a dead display for another reason; a SoC reset that then failed to boot, stuck early with no
  display; an action of the embedded controller or the PMIC.
- Whether the SoC was alive after the screen went off: no probe of it exists (the panel was the only channel; no timer, counter or q1n1
  heartbeat was reported after the last line). Whether a hardware watchdog was armed is UNKNOWN as well: nothing restarted the unit in the
  30 minutes or more during which the operator found it dark.
- Whether the RS store completed, whether the xHC started (`HCH` after the store was never read) and whether it fetched `DCBAA[0]` or the
  scratchpad array: no DMA was observed. The question of A1 (does the xHC's DMA reach the runner's RAM: a TRB read and a completion
  written back) stays UNKNOWN: no doorbell was rung, and SC2 is neither triggered nor cleared.
- The `HSE` and `HCE` bits, the SMMU fault registers and the DWC3's bus-error address after the RS store: never read.
- Boot 15's page, and whether the ending repeats on the same arena: UNKNOWN. The stick was not read back for this section: nothing is
  claimed about its contents.

**The hazards, as they fell (the record's section 8).** Written before the screen went off, as far as the page shows (INFERRED from its
structure; the page has no END line, so the payload's own `WRITES` count was never printed): the words of one RAM arena (arena 2, the
runner's own stack pages, the only arena tried); `HCRST`; the ten PROGRAM stores (`CONFIG`, `DCBAAP`, `CRCR`, `ERSTSZ`, `ERDP`, `ERSTBA`,
the 64-bit ones as two stores each); and, at the end of the last wait, the RS store of `USBCMD`, the twelfth register store, after which the
screen went off. Nothing else was written: no port, DWC3, glue, SMMU, clock, GDSC, PHY, retimer or stick register, no doorbell, `IMAN` or
`ERDP`-with-EHB store (those belong to the NOOP group), and no stick sector (A2). Hazards that may have fallen, which nothing here separates: (a) an
unclocked block hanging the bus (the clocks and GDSC read ON in S0), (b) an XPU or SMMU violation, (g) a store that faults, an exception at
EL2 and a hang rather than a refusal (no fault page reached the screen). Not observed: (c) DMA into a page that is not the arena (no DMA
was observed at all), (d) the effect of `HCRST` on a stick behind this controller (the stick's controller is not recorded), (f) the
effect of `HCRST` on port power (the page read PORTSC only before it).

**What follows.** By the runbook's rules (4.3, 4.4, O10) the sequence stops. No next step is decided or requested here, and any further xHC
write needs the screen-off understood first and its own named authorisation. For this log the panel is still the only channel and could not
show what followed the RUN store; the writer stays UNIMPLEMENTED, no completed No-Op has been seen (boot 16 ended at the wait before the first RUN store; boot 15's
page is UNKNOWN), no USB DMA or live link has been demonstrated (cause UNKNOWN), and A2 was not exercised. (Boots 17 and 19 later ran, each by the operator's
waiver of that stop for one boot, and ended the same way: section 17.)

## 17. Result of boots 17, 18A and 19, two more runs of the A1 xHCI S1 No-Op DMA test (`XHCI0.BIN` on `usb_1_ss0`, then `XHCI1.BIN` on `usb_1_ss1` again) and a read-only snapshot, on the unit: the screen went off at the end of the wait before the first RUN store both times, and the snapshot ran to its end (OBSERVED, one unit, one boot each; every boot on video)

Boots 17, 18A and 19 (2026-11-17) followed boots 15 and 16 (section 16). Boot 17 ran the other A1 payload, `XHCI0.BIN`, on `usb_1_ss0` (the plan
`a1-xhci-s1-ss0`); boot 18A ran a read-only snapshot payload of both controllers, the clocks and the SMMU (`usbsnap`, version 1; its source and its
document are in a q1n1 worktree and are not committed, so no q1n1 commit is cited here); boot 19 ran `XHCI1.BIN` again (the plan `a1-xhci-s1`), with
the stick in the top port and no Mac cable. Boots 17 and 19 are A1 boots like boots 15 and 16 (section 16: the first xHC register stores at EL2, the
stage-S1 No-Op DMA test of [SP11-XHCI-S1.md](SP11-XHCI-S1.md)). The page of boot 16 had ended at a `NEXT` line, which by rule 9 of the A1 record stops
the sequence; the operator waived that stop for one boot at a time, for boot 17 by approving a supplement record and for boot 19 by approving a short
authorisation note (each by its SHA-256, which this log does not repeat), and neither approved anything else. A2 was neither granted nor exercised, and
boot 18A only loads: it is not an A1 boot. The runbook is still not edited (the A1 record pins it by digest), so the remark of section 16 holds here too:
where the runbook reads a page that stops after a `NEXT` line as a hang or a reset, or says that the unit's `DBOFF`, `RTSOFF` or scratchpad count are not
recorded, this log is the later word. Everything below is OBSERVED (one unit, one boot each, each from one video) unless a label says otherwise; the
decodes (what a value means) are INFERRED.

| Boot | What ran | Evidence | What is known |
| --- | --- | --- | --- |
| 17 | `XHCI0.BIN` (19,870 bytes; the page prints `SHA=63B11F901ACBB430`, the first 16 digits of its SHA-256; loaded at `0xCE5E8000`) on `usb_1_ss0`, the Mac's cable plugged at the cue | one video (78 s) | The page below; the screen went off about 8 s after `NEXT RUN AFTER 8 S`; no `R1` line, VERDICT, END or fault page; the unit stayed dark, and the steps after the black-out found nothing (17.1) |
| 18A | `usbsnap` version 1 (9,973 bytes; `SHA=6C783E7B6DBAF990`; loaded at `0xCE200000`), read-only, the stick in the top port, no cable | one video (33 s) | The page of 28 lines below; `END LOADS=334 FAULTS=0`; no black-out; the screen stayed lit for 2 minutes and more after the video |
| 19 | `XHCI1.BIN` (19,798 bytes; `SHA=989324B52E66C212`; loaded at `0xCE5EA000`) on `usb_1_ss1`, the stick in the top port, no cable at the cue | one video (73 s) | The page below; the screen went off about 8 s after `NEXT RUN AFTER 8 S`; no `R1` line, VERDICT, END or fault page; the video ends dark |

### 17.1 Boot 17: `XHCI0.BIN` on `usb_1_ss0`, the first arena the firmware's own

The record of boot 17 changed two things from boot 16: the controller, and the first arena (arena 1, the firmware's own xHCI memory at `0xCF412000`, the
DCBAAP address that S0 read in the hub run (section 12), instead of the runner's stack). It chose `XHCI0.BIN` because the firmware had initialised
`usb_1_ss0` and used it for the stick in S0 (INFERRED, section 12), so that the controller under test would be the stick's. That premise did not hold
(17.3): the operator said afterwards that the stick was in the top port and the Mac's cable, at the cue, in the second port (OBSERVED: his later statement; the
map line of 17.3 agrees with it).

**The page of boot 17** as read at 62 to 67.5 s of the video, its last lit frames (capital letters only; the addresses are one boot's allocations, not
constants):

```text
PAYLOAD LOADED INDEX=0 BASE=0XCE5E8000 BYTES=19870 SHA=63B11F901ACBB430 TYPE=1
PAYLOAD READY FILES=1 RANGES=4 CODE=1
PAYLOAD VERIFY=OK
PAYLOAD CALL 0 63B11F901ACBB430
PAYLOAD XHCI U0 ID=0X33313130 CAPL=0X20 VER=0X110 HCS1=0X2000340 HCS2=0X140000F1 HCC1=0X110FFC5
PAYLOAD XHCI U0 SCRATCH=2 PAGESZ=0X1 AC64=1 CSZ=64 DBOFF=0X2000 RTSOFF=0X1000
PAYLOAD XHCI U0 CMD=0X0 STS=0X1:HCH CRCR=0X0 DCBAAP=0X0 CFG=0X0
PAYLOAD XHCI SMMU STREAM=0X1420 SMR=3 TRANS CB=3 NCB=0X5F
PAYLOAD XHCI SMMU0 GFSR=0X0 FSR=0X400 FAR=0X15379FF8FFF7 FSYNR0=0X460D5D
PAYLOAD XHCI ARENAS 1=OK 2=OK 3=OK 4=OK
PAYLOAD XHCI WATCH 30 S: CONNECT THE MAC USB-C CABLE TO THE FREE PORT NOW
PAYLOAD XHCI BASE U0P1=0X20002A0 U0P2=0X20002A0 U1P1=0X20002A0 U1P2=0X24002C0
PAYLOAD XHCI CHG T=5.7 U0P2 FROM 0X20002A0 TO 0X20002E0:POLL
PAYLOAD XHCI CHG T=6.0 U0P2 FROM 0X20002E0 TO 0X2C002C0:INACT
PAYLOAD XHCI WATCH 20 S LEFT
PAYLOAD XHCI WATCH 10 S LEFT
PAYLOAD XHCI WATCH DONE CHANGED=U0P2 N=2
PAYLOAD XHCI NEXT ARENA 1 FW 0XCF412000 PAGES 3 AFTER 8 S
PAYLOAD XHCI NEXT RESET AFTER 8 S
PAYLOAD XHCI NEXT PROGRAM AFTER 8 S
PAYLOAD XHCI NEXT RUN AFTER 8 S
```

Nothing follows: no `R1` line, no VERDICT, no END, no fault page.

**The video, in time (OBSERVED).**

| Video, time | What is on the panel |
| --- | --- |
| 60 s | the page ends at `NEXT PROGRAM AFTER 8 S` (a full frame) |
| by 62 s | `NEXT RUN AFTER 8 S` is on the page (read at 2 frames per second from 62 s) |
| 67.5 to 68.0 s | the screen goes dark |
| to 78.05 s | still dark: no further line |

The screen went dark about 8 s after `NEXT RUN AFTER 8 S` was drawn (the frames bound the interval between 5.5 s and 8 s, because the line was drawn between
60 s and 62 s; boot 16, read at 10 frames per second, gave 8.15 s): the end of the 8 s wait before the first store of the RUN group, a single store, `USBCMD`
with RS. That the screen followed that store is INFERRED from the payload's nominal wait.

**What it answers.**

| # | Question | Page lines | What the unit showed | Label |
| --- | --- | --- | --- | --- |
| 1 | What does the xHC of `usb_1_ss0` report? | the three `U0` lines | The same words as `usb_1_ss1` in boot 16: `DBOFF` `0x2000`, `RTSOFF` `0x1000`, 2 scratchpad buffers, `HCS1` `0x2000340`, `HCS2` `0x140000F1`, `HCC1` `0x110FFC5`, 64-byte contexts; halted (`STS` `0x1`, `HCH`) with `CMD`, `CRCR`, `DCBAAP` and `CONFIG` all 0 | OBSERVED |
| 2 | Is the SMMU state the one S0b and boot 16 read? | `SMMU`, `SMMU0` | Stream `0x1420` is `TRANS` through context bank 3 (`SMR=3`, `NCB=0x5F`); `GFSR` 0; `FSR` `0x400` is the FORMAT field and records no fault (section 16, row 3). `FAR` `0x15379FF8FFF7` and `FSYNR0` `0x460D5D` differ from S0b's and boot 16's (`FAR` `0x953FDF7CF7F7` and `0x153F9FF8F7F3`, `FSYNR0` `0x46005D` and `0x660D5D`); with no fault recorded they are not fault records (INFERRED), and their meaning stays UNKNOWN | OBSERVED (the values); INFERRED (what they mean) |
| 3 | Which controller serves the free connector? | `BASE`, `CHG`, `WATCH DONE` | After the cable was plugged `U0P2`, port 2 of the payload's own controller, went from `0x20002A0` to `0x20002E0` at T=5.7 s (POLL) and to `0x2C002C0` at T=6.0 s (INACT: link state 6 with the link-state-change flag and, beyond boot 16's word, the config-error-change flag); no other port changed. In boot 16 the cable changed `U1P2`. `BASE` showed `U1P2` at `0x24002C0` before the cable, the latched word that `U0P2` held in boot 16 | OBSERVED (the values); INFERRED (the decode: the PORTSC layout is from memory) |
| 4 | How far did the sequence get? | `ARENAS`, `NEXT ARENA` to `NEXT RUN` | As in boot 16, but with arena 1: `ARENAS 1=OK 2=OK 3=OK 4=OK` (the payload's own arena check printed OK for each of the four), then `NEXT ARENA 1 FW`, `NEXT RESET`, `NEXT PROGRAM` and `NEXT RUN`, 8 s apart, with no `R1`, VERDICT or END: by the runbook's page structure the ARENA, RESET and PROGRAM groups completed, 11 register stores | OBSERVED (the page); INFERRED (the stores) |
| 5 | What happened at `NEXT RUN`? | the screen | It went dark about 8 s after `NEXT RUN AFTER 8 S` was drawn and stayed dark to the end of the video (about 10 s); no `R1` line, VERDICT, END or fault page followed | OBSERVED (the screen); INFERRED (the link to the store) |

**The steps after the black-out (E0; OBSERVED: the operator's report and the Mac-side logger).** The record of boot 17 added steps for a black panel; the
one that matters here moves the Mac's cable into the stick's port and waits 60 s with the Mac-side logger running, to see any new USB device (a Qualcomm
download-mode device, vendor `05C6`, was the case looked for), and then the unit is forced off. Result: no new device appeared on the Mac, no pop-up appeared, and the operator found the unit "normal warm" (his words; an impression, not a measurement). A null result: it says that the unit did not
show up as a USB device on the stick's port in that minute, and nothing about whether the SoC was alive (INFERRED: a host controller that is not running
presents nothing to a Mac either way). The Mac logger, which ran from before the boot, saw no USB device added or removed; its one network-interface change
was a tunnel interface of the Mac itself, not a device of the unit (INFERRED). When and how the unit was powered off afterwards are not recorded here.

### 17.2 Boot 18A: a read-only snapshot of both controllers, the clocks and the SMMU

Boot 18A ran `usbsnap` (version 1: a read-only snapshot payload whose source and document are in a q1n1 worktree and are not committed) through the payload
runner, with the stick in the top port and no cable. The operator had been asked for the second port and said afterwards that he put the stick in the top
port; the Shell's map line on the video agrees (17.3). The payload only loads. It reads the clock and power words of both controllers, the PHY clocks and
domain, the NoC clocks, both controllers' xHC, DWC3 core and glue registers and the SMMU, waits 8 s, reads the same blocks again (the `NEXT S2` lines) and
compares the two passes (`DRIFT`). A line with one value means that both controllers read the same, a line with `A/B` that they differ (`A` is
`usb_1_ss0`, `B` is `usb_1_ss1`: the page's `KEY` line).

**The page of boot 18A** as read at 22 s of the video (the payload's 28 lines, between the runner's four opening lines and its `DONE` line; capital letters
only; the address is one boot's allocation):

```text
PAYLOAD LOADED INDEX=0 BASE=0XCE200000 BYTES=9973 SHA=6C783E7B6DBAF990 TYPE=1
PAYLOAD READY FILES=1 RANGES=4 CODE=1
PAYLOAD VERIFY=OK
PAYLOAD CALL 0 6C783E7B6DBAF990
PAYLOAD USBSNAP READ ONLY EL=2 HZ=0X124F800 GAP=8 S
PAYLOAD USBSNAP KEY=A/B A=USB_1_SS0 B=USB_1_SS1 ONE VALUE=SAME DIFF=THE LINE HAS A/B
PAYLOAD USBSNAP GCC GDSC=0XF822F800.ON MST=1.ON SLP=1.ON MOCK=1.ON CFGN=1.ON AGGR=1.ON
PAYLOAD USBSNAP DWC ID=0X33313130 STS=0X7E800001 CM=1 BV=0 BERR=0
PAYLOAD USBSNAP XHC CAP=0X1100020 CMD=0 USTS=1/0X11 P1=0X20002A0 P2=0X20002A0/0X24002C0 DIFF
PAYLOAD USBSNAP SMMU SCR0=0X230406 ID0=0X7C017E9A ID1=0X6000005F GFSR=0
PAYLOAD USBSNAP SMMU SMRN=3/4 SMR=0X80001420/0X80001460 S2CR=3 CB=3 DIFF
PAYLOAD USBSNAP SMMU CBAR=0X35E03 CBA2=1 SCTL=0XE0 TCR=0 MAIR=0
PAYLOAD USBSNAP SMMU TTBR=0 FSR=0X400 FMT=2 FLT=0 FAR=0X951F9F78F7F3 FSY0=0X460D5D
PAYLOAD USBSNAP NEXT GCCPHY
PAYLOAD USBSNAP GCCPHY AUX=1.ON CAUX=1.ON PIPE_NW=1 PGDS=0XF8222800.ON
PAYLOAD USBSNAP NEXT NOC
PAYLOAD USBSNAP NOC ANOC=1.ON SYS=1.ON NTH=0.ON STH=0.ON
PAYLOAD USBSNAP NOC AHB=0.ON NAXI=1.ON SAXI=1.ON
PAYLOAD USBSNAP NEXT DWC3
PAYLOAD USBSNAP DWC3 GCTL=0X101005 PC=1 U2PC=0X102400 SU2=0
PAYLOAD USBSNAP DWC3 U3PI=0XB081402 SU3=0 LTSM=0X9CC20026
PAYLOAD USBSNAP NEXT QSCR
PAYLOAD USBSNAP QSCR GENC=4 HSPC=0X10000000 SSPC=0
PAYLOAD USBSNAP NEXT SACR
PAYLOAD USBSNAP SACR SACR=0X41C
PAYLOAD USBSNAP NEXT WAIT 8 S
PAYLOAD USBSNAP NEXT S2 GCC
PAYLOAD USBSNAP NEXT S2 U0
PAYLOAD USBSNAP NEXT S2 U1
PAYLOAD USBSNAP NEXT S2 SMMU
PAYLOAD USBSNAP DRIFT NONE CMP=84
PAYLOAD USBSNAP END LOADS=334 FAULTS=0 SKIPS=0 BLK=0 CUTS=0 GAP_MS=8004 LINES=28
PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=334 FAULTS=0 REFUSED=0 LINES=28 DROPPED=0
```

The last frame of the video (33 s) is q1n1's foothold page at `UPTIME 23 S`; the page above stood unchanged from about 16 s to 31 s.

**What it answers.**

| # | Question | Page lines | What the unit showed | Label |
| --- | --- | --- | --- | --- |
| 1 | Did a read-only snapshot of both controllers run to its end, and did the screen stay lit? | `END`, `DONE` | Yes: `END LOADS=334 FAULTS=0 SKIPS=0 BLK=0 CUTS=0 GAP_MS=8004 LINES=28` and the runner's `DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=334 FAULTS=0 REFUSED=0 LINES=28 DROPPED=0`; no black-out. The operator reports that the screen stayed lit for 2 minutes and more after the video ended | OBSERVED (the page, one boot); OBSERVED (the operator's report) |
| 2 | Are the NoC clocks on the controllers' path running? | `NOC` | `ANOC=1.ON SYS=1.ON NTH=0.ON STH=0.ON` and `AHB=0.ON NAXI=1.ON SAXI=1.ON`: no word reads off (the page's `.ON` is bit 31, CLK_OFF, clear: `clk-branch.h:63`), and `NAXI` and `SAXI` read `1` (bit 0 set). The earlier lead, that a NoC clock on the way to `usb_1_ss1` is off at EL2 (design notes that are not committed), is REFUTED for this wiring, the stick in the top port. Boot 16's wiring, the stick in the second port, was not read: `NAXI` and `SAXI` might differ there (UNKNOWN) | OBSERVED (the words); INFERRED (that these are the clocks of the controllers' path) |
| 3 | Are the controllers' power domain and clocks, and the PHY clock and domain words, on? | `GCC`, `GCCPHY` | `GDSC=0XF822F800.ON` and the five clocks `MST`, `SLP`, `MOCK`, `CFGN` and `AGGR` (each `1.ON`) read the same on both controllers (one value, no `/`), and so do the PHY words `AUX=1.ON CAUX=1.ON PIPE_NW=1` and `PGDS=0XF8222800.ON`: nothing reads off | OBSERVED |
| 4 | Do the DWC3 cores and the glue differ? | `DWC`, `DWC3`, `QSCR` | No: `ID=0X33313130 STS=0X7E800001 CM=1 BV=0 BERR=0` (host mode, no bus error latched: the decode of section 12, row 6), `GCTL=0X101005 PC=1 U2PC=0X102400 SU2=0`, `U3PI=0XB081402 SU3=0 LTSM=0X9CC20026` and `GENC=4 HSPC=0X10000000 SSPC=0` are one value each; so is the last block, `SACR=0X41C` (not decoded here) | OBSERVED (the values) |
| 5 | What differs between the two controllers? | `XHC`, `SMMU` | Only `USTS` (`A` `0x1`, HCH; `B` `0x11`, HCH and PCD) and port 2 (`A` `0x20002A0`; `B` `0x24002C0`), and the two streams' own match registers (`SMRN` 3 and 4, `SMR` `0x80001420` and `0x80001460`, as in section 12, row 11). `CAP` `0x1100020`, `CMD` 0 and port 1 (`0x20002A0`) are one value. Decode (INFERRED; the PORTSC layout is from memory): `0x20002A0` is port power, wake-on-connect and link state 5 (RxDetect), nothing connected; `0x24002C0` is link state 6 (Inactive) with the link-state-change flag, and the `PCD` of `USTS` follows from it | OBSERVED (the values); INFERRED (the decode) |
| 6 | Is the SMMU as S0b read it? | `SMMU` | As S0b read it for every value both print, except `FAR` and `FSYNR0`: `SCR0=0X230406 ID0=0X7C017E9A ID1=0X6000005F GFSR=0`; streams `0x1420` and `0x1460` into context bank 3 (`CB=3`); `CBAR=0X35E03` (nested), `CBA2=1`, `SCTL=0XE0`, `TCR=0`, `MAIR=0`, `TTBR=0`; `FSR=0X400 FMT=2 FLT=0` (the FORMAT field is 2, no fault bit is set); `FAR=0X951F9F78F7F3` and `FSY0=0X460D5D`: `FAR` differs from every earlier boot's, and `FSY0` is boot 17's value (S0b read `0x46005D`, boot 16 `0x660D5D`). `SCTL` `0xE0` is bits 5, 6 and 7 with `M`, bit 0, clear: `CFRE`, `CFIE` and `CFCFG`, as the pinned `arm-smmu.h` names them (`arm-smmu.h:150`, `arm-smmu.h:149`, `arm-smmu.h:147`; `M` at `arm-smmu.h:154`). What `CFCFG` does, to stall a transaction that faults instead of terminating it, is the architecture's meaning of the bit: the pinned file does not say it and the Arm text was not re-read here | OBSERVED (the values); VERIFIED (the bit names, as what the pinned file says); INFERRED (what `CFCFG` does); UNKNOWN (what stage 2 maps, `NUMS2CB` 0; what `FAR` and `FSY0` mean) |
| 7 | Did anything move during the 8 s? | `NEXT S2`, `DRIFT` | `DRIFT NONE CMP=84`: of the 84 words compared, none differed between the first pass and the second, 8 s later (`GAP_MS=8004`) | OBSERVED |

**What the snapshot shows.** Reads of both controllers' xHC, DWC3 and glue registers, the clock and power words and the SMMU, 334 loads, did not fault,
did not black out the screen and did not change over 8 s, and the screen stayed lit for minutes: so the dark screens of the A1 boots did not come from
those reads, from the payload runner or from the passage of time (INFERRED); they followed the xHC stores in each of those boots (INFERRED: the RUN store). Of
everything the page prints, only the port word and `PCD` separate `usb_1_ss1`, the controller by the map line of 17.3 that held the stick, from `usb_1_ss0`
(besides the two streams' own match registers).

**What it does not show (UNKNOWN).** `DCBAAP`, `CRCR`, the event ring and `IMAN` of either controller (`usbsnap` prints `CAP`, `CMD`, `USTS` and the two
`PORTSC` words only; boot 19's own pre-flight read of `usb_1_ss1` is in 17.4); `NAXI` and `SAXI`, and every other word of this page, in boot 16's wiring; the
PHY, repeater and retimer state beyond the clock and domain words; whether the xHC's DMA reaches DRAM.

### 17.3 Which port is which, and which controller the firmware started (INFERRED)

The Shell prints the stick's firmware path in its map (the `BLK0` line, on the panel during the countdowns before the script writes its marker). It was read
on the video of boot 16 and of each boot of this section (OBSERVED, one video each). The port word latched at the payload's first reading (`BASE`, or `P2`
on the page of boot 18A) and the port that the Mac's cable changed are the payloads' own readings (OBSERVED):

| Boot | Shell map line, `BLK0: Alias(s): ...` | The stick's port (who said) | Port word `0x24002C0` (Inactive, link-state change) latched at the first reading | Port changed by the Mac's cable at the cue |
| --- | --- | --- | --- | --- |
| 16 | `Pci(0x80,0x0)/USB(0x1,0x0)` | the second port, the one the operator usually uses (not recorded for that boot) | `U0P2` (`U0P1`, `U1P1` and `U1P2` read `0x20002A0`) | `U1P2` |
| 17 | `Pci(0x81,0x0)/USB(0x1,0x0)` | the top port (the operator, afterwards) | `U1P2` (the other three read `0x20002A0`) | `U0P2` |
| 18A | `Pci(0x81,0x0)/USB(0x1,0x0)` | the top port (the operator; the second had been asked for) | `P2` of `B`, `usb_1_ss1` (`A` reads `0x20002A0`) | no cable |
| 19 | `Pci(0x81,0x0)/USB(0x1,0x0)` | the top port (the operator; the stick is in the frame of the video) | `U1P2` (the other three read `0x20002A0`) | none: the cue was ignored |

**What it says (INFERRED).** S0 named `Pci(0x80)` as the controller at `0x0a600000`, `usb_1_ss0` (section 12, rows 1 and 2; the stick was, INFERRED, in the
port the operator usually uses), so `Pci(0x81)` is the other controller, `usb_1_ss1`. In every row the latched port word is on the controller that the path names: it is the
stick's link, a SuperSpeed port 2 left Inactive with a link-state change. S0b fits it: its `U0` read `USBSTS` `0x11` (HCH and PCD) and its `U1` `0x1`
(section 13, rows 1 and 2), the same pattern, on the controller that held the stick if the stick was in the usual port. Then the top port is wired to
`usb_1_ss1` and the second port, the one the operator usually uses, to `usb_1_ss0`, and the firmware starts the controller whose port holds the stick (its
USB stack read the stick through that controller before ExitBootServices). The Mac's cable fits it: in boot 16, with the stick in the second port, the
cable in the free top port changed `U1P2`; in boot 17, with the stick in the top port, the cable in the free second port changed `U0P2`. Each leg is one
boot of one unit, the map line is UEFI device-path text, and nothing traced the wiring.

**The consequence (INFERRED).** In boots 15, 16 and 17 the controller under test was the one without the stick: `XHCI1.BIN` on `usb_1_ss1` with the stick
on the `usb_1_ss0` side (boots 15 and 16; boot 15's port is UNKNOWN, the usual port is the likely one), and `XHCI0.BIN` on `usb_1_ss0` with the stick in the
top port, on the `usb_1_ss1` side (boot 17). The premise of the boot 17 record, to test the controller that the firmware used for the stick, did not hold,
because the stick's port in boot 16 had not been recorded: a wiring miss, not a payload fault. Boot 19 is the first test with the stick on the tested
controller (17.4). At EL2 that controller reads as reset (OBSERVED, boot 19): there is nothing of the firmware's state to adopt on it (S1a, section 1,
item 3), only the port word and `PCD` tell it from the other.

**The earlier sentences, and the caveat they need.** Several sentences of this log say that `usb_1_ss1` was never started by firmware, was untouched by it or
is not among its USB controllers: section 1, item 4 ("firmware never started it"), section 12, rows 1 and 10, section 13, row 2, and the paragraph "One fact
for the stick question" of 14.2 ("is not among them"). Each was read with the stick in the port the operator usually uses, which this section reads as the
second port, the one wired to `usb_1_ss0` (INFERRED), so each holds for that wiring only. With the stick in the top port the path names `Pci(0x81,0x0)` and
the latched port word is on `usb_1_ss1`, which fits a controller that the firmware did start and use (INFERRED). Each of those sentences now carries a pointer
to this section where it stands; its words are unchanged. Section 3.2, the evidence table of section 9 and Q2 of section 11 say which controller the stick
uses or which port is which, and say now that it follows the port. What the firmware's PCI I/O listing of 14.2 shows with the stick in the top port was
not read (UNKNOWN).

### 17.4 Boot 19: `XHCI1.BIN` with the stick on its controller and no cable

Boot 19 ran `XHCI1.BIN` again, the file of boots 15 and 16 (the plan `a1-xhci-s1`), and changed only the attachments of boot 16: the stick in the top port,
on the controller under test (INFERRED, 17.3), and no Mac cable at the cue. The checks that its approval asked for passed (OBSERVED): the Shell's map line
read `Pci(0x81,0x0)/USB(0x1,0x0)` (the stick is in the frame of the video, in the top port), the script printed `AUTORUN a1-xhci-s1 STEP 1 OF 1`, the
runner's digits were `989324B52E66C212` with `PAYLOAD VERIFY=OK`, and `BASE` showed `U1P2` at `0x24002C0` and `U0P2` at `0x20002A0`, the pattern of boots 17
and 18A. The cue was ignored on purpose: `WATCH DONE CHANGED=NONE N=0` (O14 of the runbook: the run goes on).

**The page of boot 19** as read at 66 s of the video, its last lit frame, on enlarged crops (capital letters only; the addresses are one boot's allocations,
not constants):

```text
PAYLOAD LOADED INDEX=0 BASE=0XCE5EA000 BYTES=19798 SHA=989324B52E66C212 TYPE=1
PAYLOAD READY FILES=1 RANGES=4 CODE=1
PAYLOAD VERIFY=OK
PAYLOAD CALL 0 989324B52E66C212
PAYLOAD XHCI U1 ID=0X33313130 CAPL=0X20 VER=0X110 HCS1=0X2000340 HCS2=0X140000F1 HCC1=0X110FFC5
PAYLOAD XHCI U1 SCRATCH=2 PAGESZ=0X1 AC64=1 CSZ=64 DBOFF=0X2000 RTSOFF=0X1000
PAYLOAD XHCI U1 CMD=0X0 STS=0X11:HCH,PCD CRCR=0X0 DCBAAP=0X0 CFG=0X0
PAYLOAD XHCI SMMU STREAM=0X1460 SMR=4 TRANS CB=3 NCB=0X5F
PAYLOAD XHCI SMMU0 GFSR=0X0 FSR=0X400 FAR=0X951F9F8F773 FSYNR0=0X460D5D
PAYLOAD XHCI ARENAS 2=OK 3=OK 4=OK
PAYLOAD XHCI WATCH 30 S: CONNECT THE MAC USB-C CABLE TO THE FREE PORT NOW
PAYLOAD XHCI BASE U0P1=0X20002A0 U0P2=0X20002A0 U1P1=0X20002A0 U1P2=0X24002C0
PAYLOAD XHCI WATCH 20 S LEFT
PAYLOAD XHCI WATCH 10 S LEFT
PAYLOAD XHCI WATCH DONE CHANGED=NONE N=0
PAYLOAD XHCI NEXT ARENA 2 STACK 0XCE5D9000 PAGES 3 AFTER 8 S
PAYLOAD XHCI NEXT RESET AFTER 8 S
PAYLOAD XHCI NEXT PROGRAM AFTER 8 S
PAYLOAD XHCI NEXT RUN AFTER 8 S
```

The `FAR` digits are as transcribed from a blurry crop (the page of boot 18A reads `0X951F9F78F7F3`): they are uncertain, and the meaning of `FAR` stays UNKNOWN,
as it differs in every boot; `FSYNR0` `0x460D5D` is the value of boots 17 and 18A. Nothing follows: no `R1` line, no VERDICT, no END, no fault page.

**The video, in time (OBSERVED; the 1 frame per second crops give +-1 s).**

| Video, time | What is on the panel |
| --- | --- |
| about 3 s | the Shell's map line, `AUTORUN a1-xhci-s1 STEP 1 OF 1` and the five-second countdown |
| about 38 s | `NEXT ARENA 2 STACK 0XCE5D9000 PAGES 3 AFTER 8 S` |
| about 46 s | `NEXT RESET AFTER 8 S` |
| about 54 s | `NEXT PROGRAM AFTER 8 S` |
| about 62 s | `NEXT RUN AFTER 8 S` |
| 69.5 to 70.0 s | the screen is lit at 69.5 s and dark at 70.0 s |
| to 72.8 s | still dark: no further line |

The screen went dark about 8 s after `NEXT RUN AFTER 8 S` was drawn, the signature of boot 16 (8.15 s) and boot 17 (about 8 s): the end of the 8 s wait before
the first RUN-group store, `USBCMD` with RS. That the screen followed that store is INFERRED from the payload's nominal wait.

**What it answers.**

| # | Question | What the unit showed | Label |
| --- | --- | --- | --- |
| 1 | What does the controller under test report? | `usb_1_ss1`, the one the stick sat on (17.3), read halted with `CMD` 0, `STS` `0x11` (`HCH` and `PCD`), `CRCR` 0, `DCBAAP` 0 and `CONFIG` 0. That is indistinguishable from an unstarted controller: boot 16's `usb_1_ss1` read `STS` `0x1` with the same zeros, and boot 17's `usb_1_ss0` too; only `PCD`, with the latched port word, differs. So the firmware's state of the stick's controller is not adoptable at EL2: it reads as reset (INFERRED) | OBSERVED (the values); INFERRED (the reading) |
| 2 | Does the screen go off with the stick on the tested controller and no cable anywhere? | Yes: the page ended at `NEXT RUN AFTER 8 S`, the screen went dark about 8 s later and the video ends dark (72.8 s); no `R1` line, VERDICT, END or fault page. The operator's report: "screen went black" | OBSERVED (the screen, the report); INFERRED (the link to the store) |
| 3 | What did the Mac see afterwards? | After the black-out the operator plugged the Mac's cable into the free port, the second one: an optional step outside the approved boot, on the Mac's side only, with the logger started first. Nothing appeared on the Mac: the logger saw no USB or network event. With boot 17's null result, the Mac has seen no USB device of any kind in either port after a black-out (boot 17: the top port; boot 19: the second) | OBSERVED (the logger, the operator's report) |

Not reported for this boot: the unit's heat, when and how it was powered off, and whether it came back (UNKNOWN). The runbook's success page (`VERDICT NOOP_OK`)
was not reached.

### 17.5 What the four boots that wrote xHC registers show together (INFERRED)

Boots 15, 16, 17 and 19 are the four A1 boots. What differed between them, as far as the readings go, and how each ended:

| Boot | Payload, controller | The stick | The Mac's cable at the cue | The ending |
| --- | --- | --- | --- | --- |
| 15 | `XHCI1.BIN`, `usb_1_ss1` | port UNKNOWN (the usual port, the second, is the likely one: not on the tested controller) | plugged at `WATCH 20 S LEFT` (memory) | the screen went off (the operator's memory; its page is UNKNOWN) |
| 16 | `XHCI1.BIN`, `usb_1_ss1` | the second port: not on the tested controller | the free port; `U1P2` changed | off 8.15 s after `NEXT RUN`, and stayed off |
| 17 | `XHCI0.BIN`, `usb_1_ss0`, first arena the firmware's own | the top port: not on the tested controller | the free port; `U0P2` changed | off about 8 s after `NEXT RUN`, and stayed off |
| 19 | `XHCI1.BIN`, `usb_1_ss1` | the top port: ON the tested controller | none: `CHANGED=NONE N=0` | off about 8 s after `NEXT RUN`; the video ends dark |

In every row for which a page was read, the page ended at `NEXT RUN AFTER 8 S`, and in every row the screen went off at about the end of the wait before the
first store of the RUN group, `USBCMD` with RS (the link to the store is INFERRED from the timing; boot 15 rests on the operator's memory). What differed
between the rows did not change the ending: the controller, the stick on or off the tested controller, the Mac's cable present or absent, the first arena
(the runner's stack or the firmware's memory) and the payload's base address (it moves between boots). So the black-out is not specific to `usb_1_ss1`, not
an unstarted controller, not the absence of the stick from the tested controller and not the Mac's cable (INFERRED: one unit, one boot per setup, boot 15
from memory); each of these was a candidate when boot 16 ended. What the four share is the sequence of stores up to and including the first store of the RUN
group (INFERRED for boot 15).

**SC3 (section 4.7: five boots at S1 without a No-Op completing, or two boots with an unexplained Host System Error).** Boots 15, 16, 17 and 19 are four
boots at S1 without a No-Op completing: the count is 4 of 5 (3 if boot 15 does not count, because its page and its ending are the operator's memory). One more
such boot would be the fifth (the fourth without boot 15). Boot 18A is not a boot at S1: it stored nothing. No HSE was read in any of these boots, so the
second limb is not counted. The stop criteria are points at which work on the stick writer stops and the plan is reviewed, not retried.

**The stop rules applied.** O10 of the runbook reads each of these pages as section 16 says: no VERDICT, the page stops, the screen goes off. No restart was
seen in boots 16 and 17 and none was reported in boot 19, so of O10's two readings, hang or reset, the hang fits (INFERRED); a dead display with a live SoC is
a third, which the runbook does not list. The runbook marks O10 an SC1 candidate; nothing is authorised by that, and the clock, GDSC and PHY writes it would
lead to stay UNIMPLEMENTED. The runbook's rules (4.3, 4.4) stand, except where the operator's waiver named one boot.

### 17.6 What these boots do not show, the open hypotheses, and what follows

**What these boots do not show (UNKNOWN).**

- Whether the SoC was alive after the black-out: no probe of it exists. The Mac saw no USB device in either port, which excludes only a state that a Mac
  can see (INFERRED), and the lit screen of boot 18A says nothing about it.
- Whether the RS store completed, whether the xHC started (`HCH` after the store was never read) and whether it fetched `DCBAA[0]` or the scratchpad array: no
  DMA was observed in any boot. The question of A1 (does the xHC's DMA reach the runner's RAM) stays UNKNOWN, and SC2 is neither triggered nor cleared.
- The `HSE` and `HCE` bits, the SMMU fault registers and the DWC3's bus-error address after the RS store: never read.
- `DCBAAP`, `CRCR`, the event ring and `IMAN` of the controller that holds the stick, as the firmware left them (`usbsnap` does not print them; boot 19's own
  pre-flight read of `usb_1_ss1` gave `DCBAAP`, `CRCR` and `CONFIG` 0, before any store), and the words of boot 18A in boot 16's wiring, `NAXI` and `SAXI`
  among them.
- Boot 15's page. The stick was not read back for this section: nothing is claimed about its contents.

**Hypotheses that nothing here separates (UNKNOWN unless a label says otherwise; none tested).** Each fits all four boots.

- **(a) The xHC's first DMA after RS meets the SMMU's context bank 3.** Both streams translate through that bank (`SMR` 3 and 4, `S2CR` `TRANS`, `CB=3`:
  S0, S0b and the pages of this section). Its registers read `CBAR` `0x35E03` (nested), `CBA2R` 1, `SCTLR` `0xE0` (stage 1 off, `M` clear; `CFRE`, `CFIE`
  and `CFCFG` set), `TCR` and `TTBR0` 0, no fault bit in `FSR`, and `NUMS2CB` 0 (OBSERVED: S0b and boot 18A; the stream and `FSR` also in boots 16, 17 and
  19). If `CFCFG` set means that a faulting transaction is stalled and not terminated (INFERRED, 17.2 row 6), a DMA of the xHC that faulted in the bank
  would stall, and a stalled transaction could block interconnect paths that the display also uses (INFERRED mechanism; nothing here checks it beyond the
  register values). The hypervisor that, INFERRED, configured the bank was replaced at the bounce (section 3.4), and what its stage 2 maps is UNKNOWN (section 13).
- **(b) The start of the port state machines.** RS starts the xHC, which then runs its ports and PHYs with a PHY or Type-C path that is not as the
  controller expects (PHY, repeater and retimer state UNKNOWN, M3 not measured; the stick's port word was latched Inactive at the first reading in boots 16 to
  19).
- **(c) An event of the embedded controller or the PMIC** that the start of a port provokes (UNKNOWN: nothing here reads either).
- **(d) The display subsystem stopping while the SoC is alive**, for a reason that the xHC's start provokes or for another (a scanout underrun, a clock or a
  power domain; UNKNOWN).

The lead of earlier design notes (not committed), that a NoC clock on the way to `usb_1_ss1` is stopped at EL2, is REFUTED for the top-port wiring: `NAXI` and
`SAXI` read on (OBSERVED, boot 18A, 17.2 row 2). Boot 16's wiring was not read.

**What would separate them (NOT RUN; nothing is authorised, nothing is written here).** A payload that clears `CFCFG` in the bank's `SCTLR` before the RS
store is the obvious next probe for (a): with the bit clear a faulting transaction would be terminated, not stalled (INFERRED), so if (a) holds the page should
go on, perhaps with a fault bit in `FSR` or a VERDICT, and if the screen still goes off, (a) in this form is not the cause. It writes an SMMU register, which
the records of A1 ban, so it needs its own named authorisation. For (b), (c) and (d) no probe is in hand: each needs a reading after the store, and the panel
is the only channel. Read-only boots would add to the picture without a store: a snapshot like boot 18A with the stick in the second port (`NAXI` and `SAXI`
in boot 16's wiring), and one that also prints `DCBAAP`, `CRCR` and the event ring of the controller that holds the stick.

**The hazards, as they fell (the record's section 8).** As in section 16, INFERRED from the page structure (no boot printed an END line, so the payload's own
`WRITES` count never appeared): in boots 17 and 19 the words of one RAM arena (boot 17: arena 1, the firmware's own xHCI memory; boot 19: arena 2, the runner's
stack pages), `HCRST`, the ten PROGRAM stores and, at the end of the last wait, the RS store of `USBCMD`, the twelfth register store, after which the screen
went off. Nothing else was written: no port, DWC3, glue, SMMU, clock, GDSC, PHY, retimer or stick register, no doorbell, `IMAN` or `ERDP`-with-EHB store, and
no stick sector (A2). Boot 18A stored nothing by its design (`READ ONLY` on its page; its source is not committed here).

**What follows.** No next step of the xHC work is requested here. On 2026-11-17, after boot 19, the operator paused the xHC and stick-writer work and turned
to the monitor entry (M1 of [SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md)), which needs neither USB nor a log channel. By the runbook's rules (4.3, 4.4, O10) the A1 sequence stops again, and SC3 stands at 4 of 5 (3 if
boot 15 does not count): the plan is reviewed, not retried. Any further xHC write needs a new record that the operator approves by name, and a write to the
SMMU needs its own named authorisation; nothing is authorised, and this section writes nothing. For this log the panel is still the only channel and could not
show what followed the RUN store in any boot; the writer stays UNIMPLEMENTED, no completed No-Op has been seen in any boot (boot 15's page is UNKNOWN), no USB
DMA or live link has been demonstrated, and A2 was not exercised. Boot 20 (section 18) was the first run of that monitor entry.

## 18. Result of boot 20, the first run of the SP11 monitor entry (M1 rung 1, plan `M1RUN-RUNG1`), on the unit: the stock kernel executed on one vCPU for the monitor's 5 s box and printed its banner, the monitor stopped it itself and returned, and the guest was, INFERRED, stuck in an early loop of the kernel's I/O Kit start-up (OBSERVED, one unit, one boot; on video)

Boot 20 (2026-11-18, 01:23) followed boot 19 (section 17). After boot 19 the operator paused the xHC and stick-writer work and turned to the monitor entry (work item 11, M1 of
[SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md)), which needs neither USB nor a log channel; boot 20 is its first run: rung 1, one boot of the plan `M1RUN-RUNG1`. The operator approved
the record of the run by its SHA-256 at 01:18 (this log does not repeat the digest). By the record that approval covers one boot, and a second rung, another file, kernel, patch
value, time box or store class needs a record of its own. The record's store classes are RAM, EL2 system registers and the GIC, and the monitor's guest has no USB controller and no
disk: the stick was only the boot medium, and A1 and A2 were neither run nor exercised. The stick was in the port the operator usually uses (the Shell's map line, read on the film at
0.5 s, is `BLK0: Pci(0x80,0x0)/USB(0x1,0x0)`; by 17.3 that is the port wired to `usb_1_ss0`, INFERRED). The film is one video of 45 s, not CRC-verified, from the Shell's map table to
q1n1's own page; the page was read on frames at 36 to 43 s and, for its later part, on full-resolution crops. Everything below is OBSERVED (one unit, one boot, from one video) unless
a label says otherwise; the decodes (what a value means) are INFERRED, and what was read in a source on the host is VERIFIED and says nothing about hardware.

### 18.1 What ran, and the film in time

| Step | What ran | Evidence | What is known |
| --- | --- | --- | --- |
| Plan | `M1RUN-RUNG1`, one step: `load SP11\SLBOUNCE-DEVHE-MARK.EFI`, then `SP11\Q1N1-PAYLOAD.EFI --el2`, after the Shell's 5 s countdown | the film, 12 s | the Shell printed `AUTORUN M1RUN-RUNG1 STEP 1 OF 1` and the two commands, and wrote its marker `SP11\DONE1.TXT` (the stick, 18.3) |
| Runner | q1n1's payload runner with five files: payload 0 `SNAPMON.BIN` (447,855 bytes; `SHA=1D32F5071506A00C`), payload 1 `SNAPREP.BIN` (58,977 bytes; `SHA=EE86676001D89CF6`), `GUEST.DAT` (118,870,336 bytes; `SHA=9E69E7B5612802F0`), `PIN.DAT` (1,024 bytes) and `REPORT.DAT` (528,384 bytes; `SHA=2AF25E8AC647A805`) | the page | `PAYLOAD READY FILES=5 RANGES=8 CODE=2`, then `PAYLOAD VERIFY=OK`; both payload prefixes equal the record's (read against the record) |
| Wrapper | payload 0, the stick wrapper with the monitor behind it (`SNAPMON START VERSION=1 EL=0X8 POLICY=0X3`); `SNAPMON.BIN` holds the monitor image at offset `0X20000` (`MON IMAGE OFF=0X20000 BYTES=316783`: 131,072 + 316,783 = 447,855, the file's size, arithmetic) | the page | every stage to `NEXT GIC STORES AFTER 8 S`, then the call into the monitor |
| Monitor | the monitor entry: `GUEST.DAT`, built from the J700 26A428 kernelcache (`kernelcache.release.mac17p`; [SP11-STICK-MONITOR.md](SP11-STICK-MONITOR.md) section 14), launched at EL1 on one vCPU (the plan's setting; the page prints no vCPU count) with a 5 s time box (`BOX_S=5`) | the page | `MON RET=0X0 STATUS=0X0 ENTERED=1`; the stop kind `WATCHDOG` (18.4) |
| Reporter | payload 1, `SNAPREP.BIN`, which prints what the monitor left in its window in RAM | the page | `MONITOR RETURNED STAGE=DONE` and the report lines of 18.2 |

**The film, in time (OBSERVED; one video, 45 s, from the Shell's map table).**

| Video, time | What is on the panel |
| --- | --- |
| 0.5 s | the Shell's map table with its countdown to skip `startup.nsh`; the last map line is `BLK0: Pci(0x80,0x0)/USB(0x1,0x0)` |
| 12 s | `AUTORUN M1RUN-RUNG1: 1 step, one per boot`, `STEP 1 OF 1`, `CMD1: load SP11\SLBOUNCE-DEVHE-MARK.EFI`, `CMD2: SP11\Q1N1-PAYLOAD.EFI --el2` and the 5 s countdown before the marker is written |
| 12 to 18 s | SL-Bounce (`Running in EL=1`, its image loaded), q1n1's UEFI entry, the GOP framebuffer captured (`0xE4800000`, 2880 x 1920, stride 2880, format 1) and the ACPI SPCR UART found |
| 18.5 s | q1n1's EL2 page: build `A9F00F9CCBB5`, `UPTIME 0 S`, `EL2 CONFIRMED AFTER EXITBOOTSERVICES`, `ENTRY EL 1`, `CURRENT EL 2`, `MIDR 0x512F0011`, `HCR_EL2 0x88000010 E2H=0 TGE=1` |
| 21 s | the runner's page with the q1n1 logo, from the first `PAYLOAD LOADED` line to `NEXT GIC STORES AFTER 8 S` |
| 36 to 43 s | the page complete and unchanged, ending at `PAYLOAD DONE INDEX=1` |
| 44.8 s | q1n1's own page redrawn: `UPTIME 28 S` (the first heartbeat after the chain) and its footer `BOOT FOOTHOLD ONLY - NO GUEST OR SMP YET`, q1n1's fixed text for its own stage: it says nothing about the monitor's guest |
| to 45 s | no black-out, no restart, no fault page |

### 18.2 The page of boot 20

**The page** as read at 36 to 43 s of the film (capital letters only; the addresses are this boot's allocations, not constants). Two things are left out: the `SHA=` field of the `PIN.DAT`
line, whose 16 digits are not legible at the frame's resolution (the file's content is the record's pin), and the digits of the sixth `MON CON` line's last value after `0XFFFFFE`. The colons
are given as `:` (INFERRED: the reporter passes a colon through, and the panel draws it as a mark that reads like `!` on the frames). The lines to `NEXT GIC STORES AFTER 8 S` were read on
frames about 1,300 pixels wide (at 21 s and at 38 s, which agree) and the later lines on full-resolution crops, so a digit of the first part is read less surely than one of the second.

```text
PAYLOAD LOADED INDEX=0 BASE=0XCEA22000 BYTES=447855 SHA=1D32F5071506A00C TYPE=1
PAYLOAD LOADED INDEX=1 BASE=0XCEA12000 BYTES=58977 SHA=EE86676001D89CF6 TYPE=1
PAYLOAD LOADED INDEX=2 BASE=0XC5D07000 BYTES=118870336 SHA=9E69E7B5612802F0 TYPE=2
PAYLOAD LOADED INDEX=3 BASE=0XCEA11000 BYTES=1024 SHA=... TYPE=2
PAYLOAD LOADED INDEX=4 BASE=0XCE7ED000 BYTES=528384 SHA=2AF25E8AC647A805 TYPE=2
PAYLOAD READY FILES=5 RANGES=8 CODE=2
PAYLOAD VERIFY=OK
PAYLOAD CALL 0 1D32F5071506A00C
SNAPMON START VERSION=1 EL=0X8 POLICY=0X3
SNAPMON STAGE=ENTERED
SNAPMON STAGE=ARGS_OK
SNAPMON STAGE=FILES_OK GUEST=0XC5D07000 BYTES=118870336
SNAPMON STAGE=PIN_OK LABEL=M1RUN-RUNG1 CHUNKS=44
SNAPMON REGS MIDR=0X512F0011 MPIDR=0X80000000 CNTFRQ=19200000
SNAPMON REGS HCR=0X88000010 SCTLR=0X100030C5183D TCR=0X80843514 CTR=0XB444C004 DLINE=64
SNAPMON GOP BASE=0XE4800000 BYTES=0X1518000 W=2880 H=1920 STRIDE=2880 FMT=1
SNAPMON STAGE=OBSERVED_OK
SNAPMON PLACE BASE=0X880000000 SIZE=0X2A000000 DESC=220 DBASE=0X880000000 DPAGES=0X780000
SNAPMON AT PLACEMENT PROBES=337 ATTR=WB SH=OS
SNAPMON STAGE=PLACED_OK
SNAPMON AT GUEST.DAT PROBES=59 ATTR=WB SH=OS
SNAPMON STAGE=GUEST_OK CHUNKS=44 POLICY=0X3 TICKS=30014805
SNAPMON STAGE=COPYING
SNAPMON STAGE=COPIED COPIED=44 BYTES=129117632 HASHED=26 HASHED_BYTES=118866864
SNAPMON MON IDS MMFR2=0X1201011112101011 MMFR4=0X0 REVIDR=0X0
SNAPMON MON IDS ISAR1=0X110111101211052 ISAR2=0X10000012
SNAPMON MON IMAGE OFF=0X20000 BYTES=316783 SHA=0XAD4EBAA27314296C BOX_S=5 ESF=0X0 GPUF=0X0
SNAPMON GIC GICD CTLR=0X10 ARE=1 DS=0 G1A=0 NEED_G=1 TYPER=0XF87F051E IIDR=0X401043B
SNAPMON GIC GICR IDX=0 TYPER=0XD0000E7 WAKER=0X0 ISEN=0X4E800001 PEND=0 ACT=0
SNAPMON GIC CPU SRE=0XF EOIMODE=1 PMR=0XF8 BPR1=0X3 IGRPEN1=0 PRIO=0XA0 CFG=0X0 GRP=0 CTL=0X0
SNAPMON NEXT GIC STORES AFTER 8 S G=1 R=1 C=1 T=0 V=0 E=0
SNAPMON MON CALL X0=0XCE7EE000 X1=0X89B000000 X2=0XF000000 X3=0X88000C000 X4=0X0 SP=0XCE86E000
SNAPMON MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=96720173 STACK=8736/262144
SNAPMON MON REGS RESTORED=YES
SNAPMON GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRC VBAR=NONE EL1=NONE
SNAPMON MON VERDICT=RETURNED FLAGS=0XF SUB=7
SNAPMON STAGE=DONE RETURN=0
PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=2 FAULTS=0 REFUSED=0 LINES=29 DROPPED=0
PAYLOAD VERIFY=OK
PAYLOAD CALL 1 EE86676001D89CF6
SNAPREP MONITOR RETURNED STAGE=DONE
SNAPREP VERDICTS PIN=OK GUEST=OK POLICY=0X3 LINES=29
SNAPREP REGS EL=0X8 MIDR=0X512F0011 MPIDR=0X80000000 CNTFRQ=19200000
SNAPREP REGS HCR=0X88000010 SCTLR=0X100030C5183D TCR=0X80843514
SNAPREP CHUNKS TOTAL=44 COPIED=44 HASHED=26 BYTES_COPIED=129117632 BYTES_HASHED=118866864
SNAPREP TICKS HZ=19200000 PLACED=37914 COPIED=30997290 HASHED=46208412
SNAPREP PLACE BASE=0X880000000 SIZE=0X2A000000 DESC=220 DBASE=0X880000000 DPAGES=0X780000
SNAPREP GUEST.DAT BASE=0XC5D07000 BYTES=118870336
SNAPREP MON VERDICT=RETURNED FLAGS=0XF SUB=7
SNAPREP MON IMAGE OFF=0X20000 BYTES=316783 SHA=0XAD4EBAA27314296C BOX_S=5 ESF=0X0 GPUF=0X0
SNAPREP MON IDS MMFR2=0X1201011112101011 MMFR4=0X0 REVIDR=0X0
SNAPREP MON IDS ISAR1=0X110111101211052 ISAR2=0X10000012
SNAPREP MON CALL X0=0XCE7EE000 X1=0X89B000000 X2=0XF000000 X3=0X88000C000 X4=0X0 SP=0XCE86E000
SNAPREP MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=96720173 STACK=8736/262144
SNAPREP MON REGS RESTORED=YES
SNAPREP MON STATUS=OK ENTERED=0X1
SNAPREP MON COUNTS EVENTS=512 TRAPS=3127 ABORTS=0 STEPS=0 CALLS=1443 DROPPED=834
SNAPREP MON STOP WATCHDOG PC=0XFFFFFE000C1EEF00 A=0X0 B=0X0 C=0XD3AA0A80
SNAPREP MON EXIT PC=0XFFFFFE000C1EEF00 SPSR=0X804003C4 ESR=0X0 FAR=0X0
SNAPREP MON EV 0 OTHER PC=0X0 A=0X1201201023111111 B=0X21 C=0X512F0011
SNAPREP MON EV 1 OTHER PC=0X1 A=0X1201011112101011 B=0X0 C=0X2100122100001024
SNAPREP MON EV 2 MONITOR PC=0XFFFFFE000C2640AC A=0XF B=0X0 C=0X1
SNAPREP MON EV 3 MONITOR PC=0XFFFFFE000C22B9E0 A=0X12 B=0XFFFFFE000B86FBB0 C=0XFFFFFE0006F84000
SNAPREP MON EV 4 SYSREG PC=0XFFFFFE000C2448CC A=0X3A7FE2 B=0X0 C=0X0
SNAPREP MON CON DARWIN KERNEL VERSION 27.0.0: TUE AUG 11 21:05:57 PDT 2026? ROOT
SNAPREP MON CON :XNU-13432.1.9?1/RELEASE_ARM64_T8140?
SNAPREP MON CON PMAP_STARTUP() INIT/RELEASE TIME: 628 MICROSEC?
SNAPREP MON CON VM_PAGE_BOOTSTRAP: 9344 FREE PAGES? 15200 WIRED PAGES?
SNAPREP MON CON VM BOOTSTRAP: 73 MAPS? 256 ENTRIES AND 64 NODES AVAILABLE?
SNAPREP MON CON PTR-PACKING MAX: COMPRESSOR:0X?PTR? PAGE:0X?PTR? VMN:0XFFFFFE...
SNAPREP GIC GICD CTLR=0X10 ARE=1 DS=0 G1A=0 NEED_G=1 TYPER=0XF87F051E IIDR=0X401043B
SNAPREP GIC GICR IDX=0 TYPER=0XD0000E7 WAKER=0X0 ISEN=0X4E800001 PEND=0 ACT=0
SNAPREP GIC CPU SRE=0XF EOIMODE=1 PMR=0XF8 BPR1=0X3 IGRPEN1=0 PRIO=0XA0 CFG=0X0 GRP=0 CTL=0X0
SNAPREP GIC NEW GICD=0X13 RB=0X12 ISEN=0X4000000 PRIO=0X80 PMR=0XF8 IGRPEN1=1
SNAPREP GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRC VBAR=NONE EL1=NONE
PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=1 FAULTS=0 REFUSED=0 LINES=35 DROPPED=0
```

The page has 76 lines: the 29 `SNAPMON` lines that `PAYLOAD DONE INDEX=0` counts (`LINES=29`) and the 35 `SNAPREP` lines that `PAYLOAD DONE INDEX=1` counts (`LINES=35`). Nothing follows: no
black-out, no fault page. **The font and the console lines.** The reporter prints the guest's console through a font list (`rp_font` in `probes/sp11-stick-reporter.c`): lower case becomes
capitals, and every character other than capitals, digits, space and `: - . / = ( ) _` becomes `?` (VERIFIED, read in the source). So a `?` on the `MON CON` lines is a comma, a semicolon, a
carriage return before the line break (INFERRED: the kernel's console writes `\r\n`), `~` or another character the font lacks. The reporter prints at most five events and six console
lines of 64 columns, from the monitor's first 1,024 console bytes (VERIFIED, read in the sources: `RP_EVENTS`, `RP_CONSOLE_LINES`, `RP_CONSOLE_WIDTH`, `CONSOLE_MAX`), and the monitor keeps
its first 512 events and counts the later ones as dropped (VERIFIED, read in `probes/xnuboot/telemetry.c`). The page therefore cannot show more than six console lines, and what the
console held after them is UNKNOWN: the sixth line is the cap, not the guest's last line (18.5).

**The console lines against the kernelcache (VERIFIED, host: read in the local copy of the J700 26A428 kernelcache, which is not committed).** The first two `MON CON` lines are that file's
own `VERSION` string, `Darwin Kernel Version 27.0.0: Tue Aug 11 21:05:57 PDT 2026; root:xnu-13432.1.9~1/RELEASE_ARM64_T8140`, as the font draws it: capitals, and `?` for the `;` and the `~`
(the `?` in `1.9?1` is that `~`). The next three are instances of format strings that the same file holds (`pmap_startup() init/release time: %lld microsec`, `vm_page_bootstrap: %d free
pages, %d wired pages%c`, `VM bootstrap: %d maps, %d entries and %d nodes available`). The sixth has a format there too (`ptr-packing max: compressor:0x%lx page:0x%lx vmn:0x%lx vme:0x%lx`);
the `?PTR?` after the first two `0X` is INFERRED to be the placeholder `<ptr>` that a kernel's printf puts in place of a pointer-like value (`<` and `>` print as `?`), which is not checked
against any source, and the line is cut at the page's cap. That these lines come from this kernelcache's own code follows from the match (INFERRED; the match is of strings, and says
nothing about how far the run went).

### 18.3 The verdict against the rows a to g of the record

The record defines M1 by seven rows (the record's section 5a, built by `tools/sp11_stick_bundle_record_m1_guide.py`; [SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md) section 2 has the same rows).

| Row | The rule | Result | Label |
| --- | --- | --- | --- |
| a | the runner loads the files and both hashes agree: `PAYLOAD READY`, then `PAYLOAD VERIFY=OK` for both payloads | held: `PAYLOAD READY FILES=5 RANGES=8 CODE=2`, `PAYLOAD VERIFY=OK` before each call, both 16-digit prefixes equal the record's | OBSERVED |
| b | the wrapper refuses on any precondition mismatch: one `REFUSED` line and no guest entry | not exercised: nothing mismatched, so the page has no `REFUSED` line; by the record a `REFUSED` line is no pass | OBSERVED (the absence) |
| c | the guest ran: `MON STATUS=OK ENTERED=0X1`, and the stop PC is inside the kernel image | held: `MON STATUS=OK ENTERED=0X1`; the PC `0XFFFFFE000C1EEF00` lies between the start and the end of the kernelcache's `__TEXT_EXEC` segment as that file's segment table gives it (`0xFFFFFE0008A08000` to `0xFFFFFE000C264000`; VERIFIED, host arithmetic on the local copy; the loader maps the kernel at its link addresses, 18.5) and the kernel printed its own banner; the PC was not symbolicated, because the tool needs the KDK (NOT RUN), and the function it lies in was identified by structure only (INFERRED, 18.5) | OBSERVED (the page); VERIFIED (the range, host) |
| d | the run ended by the monitor's own stop, a `MON STOP` line | held: `MON STOP WATCHDOG`; no hang and no reset | OBSERVED |
| e | the stop came inside the time box (5 s, 96,000,000 ticks of 19.2 MHz); `TICKS` above it neither proves nor fails the row, and the stop kind `WATCHDOG`, the monitor's own deadline, decides | held by that rule: `TICKS=96720173` is 720,173 ticks (0.0375 s) above the box and the stop is `WATCHDOG` | OBSERVED (the page); INFERRED (what the 0.0375 s holds, 18.4) |
| f | no disk was written: the stick is byte-identical after the run but for the Shell's marker | held: every file of the staged list passes `shasum -a 256 -c` from the stick's root, `REPORT.DAT` is still the all-zero staged file, and the only changes since staging are `SP11\DONE1.TXT` (36 bytes, the Shell's marker) and macOS's own `.fseventsd` entry, made when the Mac mounted the stick; the unit's own storage was not read back (NOT RUN) | OBSERVED (host check of the stick, read only, one boot) |
| g | the A16 monitor is byte-identical with the new gate off (`tools/monitor_baseline.py` IDENTICAL), a host check on no page | held: a host build comparison on 2026-11-18 showed the default A16 monitor build (four flag sets) byte-identical between revisions 87da59673c9a and ea593683db78 (VERIFIED, host; says nothing about hardware); the monitor sources probes/ compat/ shadow/ config/ gpu/ drivers/ are also unchanged by git diff between 1e938a7 and ea59368 | VERIFIED (host) |

The commands of row g: `python3 tools/monitor_baseline.py capture NAME` at ea593683db78 (10 s; all four flag sets `reproducible`: each built twice with identical results), then
`python3 tools/monitor_baseline.py compare EARLIER LATER` against the capture of 87da59673c9a (2026-11-17, before any SP11 monitor source): exit 0, IDENTICAL for `bare` (368,439 bytes,
ELF digest prefix `419fbd516138b03a`), `base` (313,391 bytes, `f8f72f4bd7d85d5b`), `control` (324,311 bytes, `0debb3249ec6df99`) and `fast` (333,279 bytes, `4d1fbac34769b5c2`), with the same
toolchain on both sides (Homebrew clang 23.1.5 and LLD 23.1.5). The page is of the kind that the record calls M1 PASSED (rung 1): rows a, c, d and e hold by the page, f by the check of the
stick and g by the host comparison, and b was not exercised. It is a pass of that record's rows and of nothing else; gate X4's "kernel executes" stands at PARTIAL (one vCPU, 5 s, early boot only; the guest ran 5 s, then spun in an early console loop, INFERRED, 18.5; [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.4).

### 18.4 What the page shows, reading by reading

| # | Question | Page lines | What the unit showed | Label |
| --- | --- | --- | --- | --- |
| 1 | Did the call return, and with what? | `MON CALL`, `MON RET`, `MON STATUS` | The wrapper called the monitor (`X0` to `X4` and `SP` are this boot's addresses) and it returned `0` with `STATUS=0X0 ENTERED=1`: the guest was entered and no prepare check refused (there is no `MON PREPARE FAILED` line). The monitor's stack used 8,736 of 262,144 bytes. The runner's own count for each payload is clean: return 0, `STACK=OK`, no fault, no refusal, no dropped line | OBSERVED |
| 2 | How long did the call take? | `MON RET ... TICKS=`, `TICKS HZ=` | 96,720,173 ticks of the 19.2 MHz counter, 5.0375 s (arithmetic), for the whole call, against a box of 96,000,000 ticks: 720,173 ticks, 0.0375 s, more. By the record `TICKS` counts the monitor's prepare and the stop's clean as well, so if the box starts at the guest's first instruction the prepare and the clean took at most that long together, far below the record's estimate of about 0.6 s for the clean alone (INFERRED then, never measured). The wrapper's own phases, from the same page: placement 37,914 ticks (0.002 s), copy 30,997,290 (1.61 s), hashing 46,208,412 (2.41 s) | OBSERVED (the figures); INFERRED (what the excess holds) |
| 3 | How did the run end? | `MON STOP`, `MON EXIT` | `MON STOP WATCHDOG PC=0XFFFFFE000C1EEF00 A=0X0 B=0X0 C=0XD3AA0A80`: the monitor records `WATCHDOG` when an interrupt reaches EL2 with the EL2 timer's `ISTATUS` set after the deadline (VERIFIED, read in `probes/xnuboot/async.c`), so the monitor's own timer ended the run. For that kind `PC` is the guest's `ELR_EL2`, `A` its `ESR_EL1`, `B` its `FAR_EL1` and `C` its `ELR_EL1` (VERIFIED, read in the same file). `A` and `B` read 0 and `C` is not a kernel address: INFERRED that the guest took no EL1 exception that wrote them since it was entered. `MON EXIT PC=0XFFFFFE000C1EEF00 SPSR=0X804003C4 ESR=0X0 FAR=0X0`: the `ESR` and `FAR` of that line are zero by construction at an interrupt stop (VERIFIED, read in the same file) and carry no information; `SPSR` `0x804003C4` has D, A, I and F all set (INFERRED: the layout is from memory), so the kernel ran with every interrupt masked | OBSERVED (the values); VERIFIED (what the monitor puts in them); INFERRED (the decode) |
| 4 | What did the monitor count? | `MON COUNTS` | `EVENTS=512 TRAPS=3127 ABORTS=0 STEPS=0 CALLS=1443 DROPPED=834`: 3,127 traps to the monitor, no stage-2 abort, no single step, the monitor's own count of 1,443 calls (what they were is not on the page), 512 events kept and 834 dropped for lack of room. The traps and calls are cumulative counts over the call, not a rate (about 620 traps a second averages a mostly silent 5 s, 18.5) | OBSERVED |
| 5 | What are the five events? | `MON EV 0` to `MON EV 4` | The first five of the 512 kept: two `OTHER` events at `PC=0X0` and `PC=0X1` whose words equal the `MIDR` (`C=0X512F0011`) and the word that the `MON IDS` line calls `MMFR2` (`A=0X1201011112101011`) on the `REGS` and `MON IDS` lines (INFERRED: the same words), then two `MONITOR` events and one `SYSREG` event at kernel addresses. They are the first milliseconds of the boot, before the VM bootstrap lines, and not the stop: `EV 2` (`A=0XF`) is the SPTM fixups-complete call, `EV 3` (`A=0X12`) the register-exception-return call and `EV 4` an implementation-defined system-register write trapped by TIDCP in an early function (roles INFERRED, 18.5) | OBSERVED (the values); INFERRED (the match, the roles) |
| 6 | What did the guest print? | six `MON CON` lines | The kernel's banner and four early lines, as 18.2 reads them: `PMAP_STARTUP() INIT/RELEASE TIME: 628 MICROSEC`, `VM_PAGE_BOOTSTRAP: 9344 FREE PAGES, 15200 WIRED PAGES` (the comma as `?`), `VM BOOTSTRAP: 73 MAPS, 256 ENTRIES AND 64 NODES AVAILABLE` and a `PTR-PACKING MAX` line. The sixth line is the reporter's cap of six lines, not the guest's last line (18.5) | OBSERVED (the text); VERIFIED (the match with the kernelcache's strings, host; the cap, read in the reporter's source) |
| 7 | Were the registers and the GIC put back? | `MON REGS RESTORED`, `GIC` lines | `MON REGS RESTORED=YES` and `GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRC VBAR=NONE EL1=NONE`: the put-back of the GIC state was whole, nothing failed and nothing was retried. The `GIC NEW` line shows `ISEN=0X4000000` for the window, the one enabled word that the quarantine left in boot 14 (`0x04000000`, PPI 26 only; section 15), so INFERRED only PPI 26 was enabled while the guest ran and the guest's own timer interrupts were off | OBSERVED (the values); INFERRED (the decode) |
| 8 | Did the unit survive? | the film | q1n1's own page was redrawn at 44.8 s with `UPTIME 28 S`; no black-out, no restart and no fault page to the end of the film. What happened after the film is not known | OBSERVED |

### 18.5 Where the guest was, and why the page ends there (a read-only analysis after the boot; INFERRED, high confidence, one boot)

The analysis was made on the host on 2026-11-18, after the boot: no tree was edited, no hardware was used, and nothing of the kernelcache is copied here beyond virtual addresses and the roles of functions. Its tools were
`tools/kc-disas.py` and `tools/kc-xref.py` on the local J700 26A428 kernelcache (their output is not committed) and the public XNU source at tag `xnu-12377.121.6`. The source of the 26A428 kernel (xnu-13432, macOS 27) is not
public, so every match with the public source is structural and INFERRED for this kernel. `tools/kc-symbolicate.py` needs the KDK, which is absent on the machine used: NOT RUN. **Result (INFERRED, high): the guest was not slow and
not waiting, it was stuck.** At the stop it was spinning in a loop of the kernel's I/O Kit start-up that cannot end on this image, and a longer box on the same image would only repeat it. The cause chain, step by step:

- **Where (VERIFIED, host: the structure; INFERRED: the identity).** The loader maps the kernelcache at its link addresses, so the stop PC `0XFFFFFE000C1EEF00` is a link-time address in `__TEXT_EXEC` ([SP11-STICK-REAL.md](SP11-STICK-REAL.md)
  section 5: the loader's rebase only rewrites chained pointers). It lies in the function that spans `0xFFFFFE000C1EECA4` to `0xFFFFFE000C1EF188`, whose only caller is `kernel_bootstrap_thread` (`0xFFFFFE000B940CD4`). That function
  prints two iBoot-version lines, reads the boot-logo entries of the device tree's `/chosen/memory-map` and parses `-restore`, `-noprogress` and `-noprogressonce`: it is `PE_init_iokit`, the kernel's I/O Kit start-up, called from the kernel's
  second thread after the banner and the VM bootstrap printfs.
- **What the loop is (VERIFIED, host: the arithmetic; INFERRED: that it is the source's loop).** The PC is the head of a five-instruction loop that touches only registers (arithmetic, one unsigned compare and one branch back; no load, store,
  call, system register access or SPTM stub), so it is not a copy, zero or wait-on-memory loop. It matches structurally the progress-bar loop of the public `pexpert/arm/pe_init.c` (line 351 at that tag),
  `while (((unsigned)(*delta + image_size)) >= (display_size / 2))`, which with `display_size` 0 compares an unsigned value with 0 and never ends.
- **Why the display size is 0 (VERIFIED, host: the staged file and the builder; INFERRED: the register).** In the public source `show_progress` is true on macOS unless `-restore`, `-noprogress` or `-noprogressonce` is given, and the
  kernel fills its video state only from the boot arguments. The staged `GUEST.DAT` (the run's, `SHA=9E69E7B5612802F0`) carries the command line `-v debug=0x8 serial=3 cluster_power=0 wdt=-1` and an all-zero `Boot_Video` block, because
  the panel is withheld (D9; `tools/sp11_stick_bundle_chunks.py`, `probes/xnuboot.py`). So the display size is 0 at the loop and the register that holds it (`x10`) is 0 at the stop; no register was printed, so that is INFERRED. It is not
  a device and not a timer: the D9 choice, a withheld panel, meets a stock kernel that draws a progress bar.
- **What the page agrees with (OBSERVED: the values; INFERRED: the decode).** `SPSR` `0x804003C4` is EL1t with D, A, I and F masked, and its N, Z and C flags are those that a final compare leaves when this loop continues. XNU enables
  interrupts only after `PE_init_iokit` returns (public source), so nothing before that point needs a timer interrupt, and the quarantined guest timers (INTID 27 and 30) are not the cause. The `MON EXIT` `ESR` and `FAR` are zero by
  construction, and the stop line's `A=`, `B=` and `C=` are `ESR_EL1`, `FAR_EL1` and `ELR_EL1` (VERIFIED, read in the monitor's source): `C=0XD3AA0A80` is a stale value, not a guest PC.
- **The phase (VERIFIED, host: the order of the calls in the public source and in the kernelcache's startup table; INFERRED: that it is 26A428's order).** The banner is the first printf of `kernel_bootstrap`; `pmap_startup`,
  `vm_page_bootstrap` and `vm_map_init` print inside `vm_mem_bootstrap`; `ptr-packing max` comes from the zone allocator's initialisation, a startup entry at the end of that phase. The stop is past all of `kernel_bootstrap`, in the second
  thread, after the scheduler start, `thread_call`, the clock service, kdp and hypervisor initialisation and the sysctl startups: early, but beyond every line on the page.
- **The last line is the reporter's cap, not the guest's end (VERIFIED: arithmetic and the reporter's source; INFERRED: that more text existed).** The reporter prints six console lines (`RP_CONSOLE_LINES`); the banner is 100 characters and wraps
  over two 64-column lines, so the six lines are exactly the cap and hold about 330 of the 1,024 bytes the window keeps. `PE_init_iokit` prints its iBoot-version line as its first statement, so more text was probably in the window; the
  monitor's `console_dropped` counter would say whether the guest passed 1,024 bytes, and the page does not print it.
- **The events and the counts (OBSERVED: the values; INFERRED: the roles).** `EV 2` to `EV 4` are the first milliseconds of the boot (an SPTM fixups-complete call, a register-exception-return call whose `C=` is the loader's hand-off page and
  not a kernel base, and an implementation-defined system-register write trapped by TIDCP in an early function), not the stop. `TRAPS=3127` and `CALLS=1443` are cumulative, not a rate: about 620 traps a second averages a mostly silent
  5 s, and the loop makes no trap. `CALLS` includes one `SERIAL_PUTC` per console byte (counted, not logged), so at most about 1,115 were page-table or other calls, and the 1,346 events (512 kept, 834 dropped) exclude them. The guest's own
  `pmap_startup` line (628 microseconds) says that its early boot is fast. More time would not show more text or traps, because the loop ends only if the video block changes.

### 18.6 What this settles, what it does not, the falsifiers, the consequence for the plan, the next stops, and what follows

**What it settles (OBSERVED, one unit, one boot; the analysis of 18.5 is INFERRED).**

- The monitor entry works on Oryon v1, once: the wrapper placed and copied the guest, armed the GIC quarantine and called the monitor; the monitor launched the stock J700 26A428 kernel at EL1,
  ended the run itself at its 5 s box (`MON STOP WATCHDOG`) and returned `0`; the wrapper restored the registers and the GIC (`MON REGS RESTORED=YES`, `GIC RESTORE CLEAN=YES`) and the reporter printed the
  page. U3 of the plan, that the monitor's own stop path works, is answered once: it did.
- The monitor's launch gates did not refuse (`STATUS=0X0`, no `MON PREPARE FAILED` line), among them the NV2 and PAuth gate with the first-image NV2 record that the monitor embeds (decision
  D6): the refusal `-91` and its neighbours were not seen. The regime that record describes is still not recorded (INFERRED E2H = 1 for the image it came from; the monitor runs with E2H = 0),
  so what a passing gate says about the regime is UNKNOWN.
- The kernel executed for the whole box under the monitor: its banner and four early lines are on the guest's console, and its PC at the stop is inside the kernelcache's `__TEXT_EXEC` range. One vCPU, as planned. The monitor's
  early SPTM, TXM and GIC paths ran with `ABORTS=0` (OBSERVED: the counts; INFERRED: which paths the 1,443 calls and 3,127 traps used), and the kernel got through its VM bootstrap and the scheduler start to the I/O Kit start-up (INFERRED, 18.5).
- U10 was not seen: no stage-2 abort was counted (`ABORTS=0`), so the execute-never fault on the monitor's first native call did not occur in this run.
- The unit stayed up to `UPTIME 28 S`, with no restart and no fault page.

**What it does not settle (UNKNOWN unless a label says otherwise).**

- That the kernel boots. It spun in an early loop (INFERRED, high), at the first point where the withheld panel matters, so nothing past `PE_init_iokit` has run, and a longer box on the withheld image only repeats the loop (INFERRED). This
  boot does not show that macOS boots: SMP, userspace, a root device, a console on the panel and a desktop were not reached.
- Anything the page does not hold: it prints five of 512 events (834 more were dropped) and six console lines of 64 columns, the sixth being the cap. Whether the guest printed past the sixth line is UNKNOWN, and so is how long it took to reach
  the loop. The stop PC was not symbolicated (NOT RUN), and `C=` of the stop line is the guest's `ELR_EL1` by the source, not an instruction. Whether the 26A428 loop equals the public source's is not established (the match is structural).
- What the kernel does after the loop, and where it stops next (below); whether other code assumes a framebuffer once the loop is gone. Whether RAM survives a restart for a later read (M10 NOT RUN): boot 20's report window is likely unrecoverable.
- Stability and performance: one boot of 5 s, and no speed was measured (the 5.0375 s is the time box, not a figure of performance).
- The NV2 regime (above). PAuth under the monitor beyond this run: no PAuth refusal and no guest exception were seen in 5 s of kernel code, so signed returns are INFERRED to have worked, and the user-pointer sign and auth calls of EL0 were not reached.
- That the guest is confined beyond this run: `ABORTS=0` means that no access was stopped, not that a stray access would have been (INFERRED; none was counted). The unit's own storage was not read
  back (NOT RUN): that nothing wrote to it rests on the record's store classes and the run's code (INFERRED).
- What the guest does without its own timer interrupts, which the quarantine switched off for the window (row 7; INFERRED).
- A1 and A2: the monitor's guest has no USB controller and no disk, and neither was exercised.

**The falsifiers of the cause chain (18.5).** The same stop PC again once the panel is handed, or once `-noprogress` is added; and, if the guest's registers are ever printed (the monitor already captures them at an interrupt stop, and the
reporter prints four of the 37 words it holds), `x10` not 0 or `x21` not 1 at the stop (INFERRED roles: the display size that the loop tests and its scale).

**The consequence for the plan (INFERRED).** While the loop stays no time box helps, and the 5 s box stays. The next run hands the panel to the guest (rung 2: a non-zero display size) or adds `-noprogress` to the guest's command line (the
hand-off bytes only: a new `GUEST.DAT` digest and no code). Either changes the guest's input and not the monitor, and each is a decision of the operator and a record of its own. Printing the registers that the monitor already holds
at the stop would test the chain; it is a reporter-only change (a new `SNAPREP.BIN` digest to approve) that writes nothing to the unit.

**The likely next stops once the loop is gone (INFERRED order; timing UNKNOWN).** The first stop is most likely `MON STOP S2_FAULT` with an IPA, at the first access to a device that the monitor does not model: most likely XNU's first
CPU-start write to the power-management (PMGR) block, which the plan counts as success for M1 ([SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md) sections 7 and 14). If a CPU-start write were accepted and no CPU started, the kernel would panic
with a `cpu N failed to boot` message (it says `waited 1 seconds`) about a second later (INFERRED from the public source; UNKNOWN on this guest), the first real test of a guest deadline on the stop timer's own line (PPI 26). Any other device-tree node that
I/O Kit probes could stop the run first (UNKNOWN which).

**Hypothesis that nothing here separates (UNKNOWN unless a label says otherwise; not tested).** INFERRED: the 720,173 ticks above the box are the prepare, the stop and the clean; if the box starts at the guest's first instruction, they took
0.0375 s together at most. Nothing here measures the prepare alone.

**What would separate it, and test the chain (NOT RUN; nothing is authorised, nothing is written here).** A run with the panel handed or `-noprogress` added; a reporter that prints the guest's registers and the console lines past the
sixth; a longer time box only once the loop is gone, which needs the wrapper's 5 s cap raised, a new digest and the operator's decisions D5 and D8 again. None is requested here.

**What follows.** The approval covered one boot and is spent. By the record, any further run (a second rung, another file, kernel, patch value, time box or store class) needs a record of its own that the
operator approves by its SHA-256, and nothing is authorised by this section. The xHC and stick-writer work stays paused (section 17.6): the writer is UNIMPLEMENTED, no completed No-Op has been seen in any
boot, and A1 and A2 were not exercised in this one either.

## Appendix A. The page lines of the survey boot (S0; EXPERIMENTAL in the q1n1 fork; ran once on the unit, section 12)

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

## Appendix D. The panel of boot 5 (payload runner; the `usbsmmu` lines are S0b, section 13)

The whole panel of boot 5, verbatim as photographed and re-read against enlarged crops: 41 lines, capital letters only (the page
font folds lower case to upper case), at most 100 characters each. Lines 1 to 3 are the runner's notes. Lines 4 to 21 are the
`psci` payload with its framing (a `VERIFY`, a `CALL`, its own 15 lines and a `DONE`); its results are in
[SURFACE-PRO-11.md](SURFACE-PRO-11.md), the stick payload runner paragraph. Lines 22 to 41 are the `usbsmmu` payload with its
framing (lines 24 to 40 are its own 17 lines), which section 13 decodes. EXPERIMENTAL in the q1n1 fork; OBSERVED once on the unit.

```text
PAYLOAD LOADED INDEX=0 BASE=0XCEC90000 BYTES=4285 SHA=D391A9C8CBEECD00 TYPE=1
PAYLOAD LOADED INDEX=1 BASE=0XCEA89000 BYTES=17936 SHA=9F0260DC5B2F910F TYPE=1
PAYLOAD READY FILES=2 RANGES=5 CODE=2
PAYLOAD VERIFY=OK
PAYLOAD CALL 0 D391A9C8CBEECD00
PAYLOAD PSCI MPIDR_EL1=0X80000000
PAYLOAD PSCI NEXT VERSION
PAYLOAD PSCI VERSION PSCI_VERSION=0X10001:V1.1
PAYLOAD PSCI VERSION MIGRATE_INFO_TYPE=0XFFFFFFFFFFFFFFFF:NOT_SUPPORTED(-1)
PAYLOAD PSCI NEXT FEATURES
PAYLOAD PSCI FEATURES CPU_SUSPEND=0X3 CPU_OFF=0X0 CPU_ON=0X0 AFFINITY_INFO=0X0
PAYLOAD PSCI FEATURES MIGRATE_INFO_TYPE=0XFFFFFFFFFFFFFFFF:NOT_SUPPORTED(-1) SYSTEM_OFF=0X0
PAYLOAD PSCI FEATURES SYSTEM_RESET=0X0 PSCI_FEATURES=0X0 SYSTEM_SUSPEND=0X0
PAYLOAD PSCI FEATURES SYSTEM_RESET2=0XFFFFFFFFFFFFFFFF:NOT_SUPPORTED(-1)
PAYLOAD PSCI NEXT AFFINITY
PAYLOAD PSCI AFFINITY 0X0=0X0:ON 0X100=0X1:OFF 0X200=0X1:OFF 0X300=0X1:OFF 0X10000=0X1:OFF
PAYLOAD PSCI AFFINITY 0X10100=0X1:OFF 0X10200=0X1:OFF 0X10300=0X1:OFF 0X20000=0X1:OFF
PAYLOAD PSCI AFFINITY 0X20100=0X1:OFF 0X20200=0X1:OFF 0X20300=0X1:OFF
PAYLOAD PSCI NEXT SMCCC
PAYLOAD PSCI SMCCC SMCCC_VERSION=0X10001:V1.1
PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=0 FAULTS=0 REFUSED=0 LINES=15 DROPPED=0
PAYLOAD VERIFY=OK
PAYLOAD CALL 1 9F0260DC5B2F910F
PAYLOAD USBSMMU NEXT U0
PAYLOAD USBSMMU U0 IDLE CAPL=0X20 CMD=0X0 STS=0X11:HCH.PCD DCBAAP=0X0 P1=0X20002A0:RXDET
PAYLOAD USBSMMU U1 IDLE CAPL=0X20 CMD=0X0 STS=0X1:HCH DCBAAP=0X0 P1=0X20002A0:RXDET
PAYLOAD USBSMMU DWC3 ID=0X33313130 GCTL=0X101005 GSTS=0X7E800001 BERR=0X0 LTSSM=0X9CC20026
PAYLOAD USBSMMU NEXT SMMU
PAYLOAD USBSMMU SMMU ID0=0X7C017E9A ID1=0X6000005F ID2=0X5222 SCR0=0X230406 GFSR=0X0
PAYLOAD USBSMMU SMMU SMRG=0X9A NCB=0X5F CBBASE=0X15080000 S1420=SMR3.TRANS.CB3 S1460=SMR4.TRANS.CB3
PAYLOAD USBSMMU NEXT RINGS
PAYLOAD USBSMMU RINGS CSZ=1 ERSTSZ=0X0 ERSTBA=0X0 ERDP=0X0 SEG0=NO_ERST
PAYLOAD USBSMMU TRB SKIPPED=NO_SEG0
PAYLOAD USBSMMU U0 SLOTS=NO_DCBAAP
PAYLOAD USBSMMU NEXT CB3
PAYLOAD USBSMMU CB3 CBAR=0X35E03 TYPE=NESTED CBA2R=0X1 VA64=1
PAYLOAD USBSMMU CB3 SCTLR=0XE0 M=0 TCR=0X0 T0SZ=0 TG0=4K EPD0=0 TCR2=0X60
PAYLOAD USBSMMU CB3 TTBR0=0X0 ASID=0X0 MAIR0=0X0 FSR=0X400 FAR=0X953FDF7CF7F7 FSYNR0=0X46005D
PAYLOAD USBSMMU CB3 TTBR1=0X0
PAYLOAD USBSMMU WALK UNSUPPORTED NESTED CBAR=0X35E03 CBA2R=0X1 SCTLR=0XE0 TCR=0X0 TTBR0=0X0
PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=213 FAULTS=0 REFUSED=0 LINES=17 DROPPED=0
```

## Appendix E. The panel of the `cpuon` payload (boot 7 and restart-probe step 3; section 14)

The panel of the `cpuon` payload, capital letters only (the page font folds lower case to upper case), at most 100
characters per line: the runner's own notes, the payload's 40 lines (`LINES=40`) and its `DONE` line, 45 lines in all.
Boot 7's first block was read from two crisp photographs, taken at the two 8 s pauses (the last line of the block is
the second pause); its later lines were read from a photograph taken at an angle, with some blur. Step 3 of the restart
probe showed the whole panel again and it was read from a video with every line legible, so the values of the later
lines below are crisp. Step 2's mid-chain photograph, taken at the second pause, is crisp too and shows the panel up to
the last line of the first block. The first block is boot 7's as transcribed; step 3's panel has the same lines, with the
two differences given after it. EXPERIMENTAL in the q1n1 fork; OBSERVED twice on the unit.

```text
PAYLOAD LOADED INDEX=0 BASE=0XCEA8B000 BYTES=18056 SHA=3B4BFDA7D59F923C TYPE=1
PAYLOAD READY FILES=1 RANGES=4 CODE=1
PAYLOAD VERIFY=OK
PAYLOAD CALL 0 3B4BFDA7D59F923C
PAYLOAD CPUON BOOT_CPU MPIDR_EL1=0X80000000 CNTFRQ_EL0=0X124F800
PAYLOAD CPUON NEXT AFFINITY
PAYLOAD CPUON AFFINITY 0X100=OFF 0X200=OFF 0X300=OFF 0X10000=OFF 0X10100=OFF 0X10200=OFF 0X10300=OFF
PAYLOAD CPUON AFFINITY 0X20000=OFF 0X20100=OFF 0X20200=OFF 0X20300=OFF
PAYLOAD CPUON NEXT PREPARE
PAYLOAD CPUON NEXT CPU_ON 0X100 AFTER 8 S
PAYLOAD CPUON P1 CPU_ON 0X100 RET=0X0:SUCCESS
PAYLOAD CPUON P1 0X100 EL=2 MPIDR=OK IDS=SAME FRQ=SAME CNTVOFF_EL2=0X0 DT_US=45
PAYLOAD CPUON P1 0X100 SCTLR_EL2=0X30000000 HCR_EL2=0X1488000038 VBAR_EL2=0XFFFFFF...2800
PAYLOAD CPUON P1 POWER_OFF OFF=1 NOT_OFF=0
PAYLOAD CPUON P1 NORMAL
PAYLOAD CPUON NEXT CPU_ON ALL AFTER 8 S
```

The `...` in boot 7's `VBAR_EL2` line stands for six digits that were not legible (the value has 16 digits). In step 2's
mid-chain photograph (crisp) the `LOADED` line read `BASE=0XCE5E5000`, the phase 1 report `DT_US=47`, the register line
`SCTLR_EL2=0X30000000 HCR_EL2=0X1488000038 VBAR_EL2=0XFFFFFFB460602800` and the last line `NEXT CPU_ON ALL AFTER 8 S`. In
step 3 the `LOADED` line read `BASE=0XCE60A000` and the same register line read `VBAR_EL2=0XFFFFFF9CEA002800`. The three
bases (boot 7 `0XCEA8B000`, step 2 `0XCE5E5000`, step 3 `0XCE60A000`) are allocations of one boot each. The two crisp
`VBAR_EL2` values differ (OBSERVED), and boot 7's partly legible one has the same shape; why they differ is not
established (section 14).

After that line phase 2 follows (the `P2` lines), in this form:

```text
PAYLOAD CPUON P2 CPU_ON ISSUED=11 SUCCESS=11
PAYLOAD CPUON P2 <affinity> EL=<n> MPIDR=<OK> IDS=<SAME|MIDR> FRQ=<SAME> CNTVOFF_EL2=<value> DT_US=<n>
PAYLOAD CPUON P2 <affinity> SCTLR_EL2=<value> HCR_EL2=<value> VBAR_EL2=<value>
PAYLOAD CPUON P2 <affinity> MIDR=<value>/BOOT=<value>          (only where IDS=MIDR)
```

one report line and one register line per core, plus the `MIDR` line for the cores whose `MIDR_EL1` differs from the
boot CPU's (for example `MIDR=0X511F0011/BOOT=0X512F0011` for core `0X10000`). Cores of clusters 1 and 2 therefore need
three lines and cluster 0 cores two, which is why the 40-line budget ran out in both boots: the last two lines of core
`0X20200`, all three of `0X20300` and the phase 2 `POWER_OFF` line are not on the panel (`SUPPRESSED=6`). The values of
the printed `P2` lines are in the per-core table of section 14 (crisp from step 3's video). The summary lines at the end
of the payload and the runner's `DONE` line, as step 3 shows them whole:

```text
PAYLOAD CPUON CORES STARTED=11 REPORTED=11 OFF=11 SUPPRESSED=6
PAYLOAD CPUON NOT_DONE NO_SYSTEM_OFF_RESET_SUSPEND NO_CPU_SUSPEND BOOT_CPU_NO_MSR LEFT_RUNNING=0
PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=0 FAULTS=0 REFUSED=0 LINES=40 DROPPED=0
```

In boot 7 the middle line was cut by the edge of the photograph after `LEFT_RUNNING`, and the left column of that page
read `UPTIME 16 S`: the first heartbeat after the chain returned, not a measure of how long the page then stayed up
(section 14). In step 3 the page started at about 21:42:53.5, the chain returned at about `UPTIME 19 to 20 S`, and the
video ended at about `UPTIME 36 S` with this panel on screen (section 14.1).
