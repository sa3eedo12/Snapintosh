<!-- SPDX-License-Identifier: MIT -->
# SP11 stick bundle builder (monitor port work item 3)

Status: EXPERIMENTAL host-side tooling; the tool never touches a stick or a disk (the operator copies, section 8). A bundle of its default size ran once on the unit in the dry run and passed (OBSERVED, 2026-11-16, `docs/SP11-STICK-WRAPPER.md` section 12); the monitor entry, the GIC, the timers and the guest are hardware NOT RUN. The rest rests on host tests with synthetic inputs and read-only comparisons with local firmware dumps. The kernelcache path (`real`) ran on the host against the J700 kernelcache, never on the unit (`docs/SP11-STICK-REAL.md`).

Labels are those of [SP11-STICK-BUNDLE.md](SP11-STICK-BUNDLE.md): VERIFIED, OBSERVED, INFERRED, EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. The file formats, the refusal codes and the placement rule are that document's; the plan is `docs/SP11-MONITOR-PORT.md` section 13, work item 3 (WI 3). This note changes neither.

## 1. What it does

`tools/sp11_stick_bundle_build.py` is a command line and a library. From a local facts file, the firmware dumps of the unit and a guest image it builds, below `build/`:

- the five files of the interface, in the manifest order `SNAPMON.BIN` (payload 0, the one the runner calls), `SNAPREP.BIN`, `GUEST.DAT`, `PIN.DAT`, `REPORT.DAT` (all zero, the interface's size);
- `PAYLOAD.MAN`, written by q1n1's own manifest tool (found through `Q1N1_SP11_TREE`, never copied);
- with `--base-tree`, a stage tree: the stager's base tree plus one autorun step `SLBOUNCE-DEVHE-MARK.EFI Q1N1-PAYLOAD.EFI --el2` and the six data files, made by `tools/stage-sp11-boot-media.py autorun` and verified by its `verify` before and after;
- `STICK-FILES.TXT`, the `sha256  path` list of the stage tree, for copying to the stick by explicit names (section 8), and `build-report.json`, which says what was checked and what was not run.

Every file passes the interface's own checks (`tools/sp11_stick_bundle.py`, imported and not copied) before anything is written; the tests run the C twins of those checks too. A refusal prints `REFUSED=<NAME>` as the last line of standard output, the detail on standard error, and exits 1. A build exits 0 and ends with `HARDWARE=NOT RUN`.

It decides nothing. D1 (placement), D4 (store classes), D5 (time box) and D9 (panel, SPTM call patch) are required arguments with no default; D7 is the operator's authorisation, of which the tool only prints the text (section 6). D1 to D10 are untouched.

Properties that hold for every subcommand:

- Same inputs, same bytes: the tests build twice and compare the SHA-256 of every file. The bundle files get file time 0 and mode 0644, and `build-report.json` has no path, host name or time.
- Output only below `build/` (or a directory named by `--allow-under`), never through `..` or a symbolic link, never under `/Volumes` or `/dev`. An existing non-empty `--out` is refused unless `--force` is given and it holds a `build-report.json` from an earlier build. A failed build removes what it wrote; a refused `--force` build leaves the earlier build alone.
- `synthetic` and `real` share one pipeline. They differ only in where `built`, the dictionary that `probes/xnuboot.py build()` returns, comes from: `synthetic` makes one of the same shape from a SHA-256 counter stream (no Apple material, the loader's own layout, the image slot 115.5 MiB, the tables 16 MiB and the SPTM data 5 MiB by default); `real` calls `build()` on a kernelcache. Both then go through the same classification, so the synthetic path exercises the code that `real` depends on.
- The chunk plumbing (classify, flatten, pad, split, pack, the synthetic filler, the loader call of `real`) is in `tools/sp11_stick_bundle_chunks.py` and the record text in `tools/sp11_stick_bundle_record.py`: the builder alone would pass the 50 KiB limit of AGENTS.md.

## 2. Evidence

| Statement | Label | Evidence |
| --- | --- | --- |
| The platform constants of the example (GICD, redistributor range and stride, ITS, timer INTID, boot MPIDR) agree with every local firmware dump of the unit | OBSERVED | six local dumps (three X1 runs, three X2 runs); `RealDumps` in the test file, which SKIPs without `downloads/sp11/` |
| The bank 0x880000000 to 0x1000000000 is one conventional descriptor in every local map; the descriptor count differs between boots | OBSERVED | the same dump maps (227 and 231 descriptors among them); `place-check` prints the result per map |
| The unit's MADT writes its GICD structure 25 bytes long, states no redistributor range, and gives each CPU interface its own redistributor base | OBSERVED | every local dump; `tools/check-hamoa-acpi.py` refused such a table (`MADT GICD entry has length 25, expected 24`) until `probes/bootcontract.py` was changed to accept that exact GICD form (25 bytes, zero final byte) and to take the redistributors from the per-CPU GICC bases (section 10) |
| A synthetic bundle passes the interface's checks in Python and in C (AddressSanitizer) for a placement in the bank and for one in a low extent | EXPERIMENTAL | `Build` tests, host only |
| The stage tree of a synthetic bundle is COMPLETE under `stage-sp11-boot-media.py verify` | EXPERIMENTAL | host tests with stub EFI files and `--no-pins`: the shell pin is not checked |
| `build()`'s output is classified into the interface's chunk kinds, flattened as the loader's upload flattens it | VERIFIED | `Chunks` tests on a synthetic `built`; the J700 kernelcache's real `built` fell into the same kinds (host, `docs/SP11-STICK-REAL.md`) |
| `real` produces a bundle from the J700 kernelcache | VERIFIED | host run (`docs/SP11-STICK-REAL.md`); on the unit: NOT RUN |
| `SNAPMON.BIN` and `SNAPREP.BIN` | EXPERIMENTAL (a dry-run wrapper and reporter, which ran once on the unit); UNIMPLEMENTED (a wrapper with a monitor inside) | `docs/SP11-STICK-WRAPPER.md`: the dry-run wrapper never enters a monitor and stores no EL2 system register; the tests of this builder use placeholder files that say so |
| Where the runner puts the big file | OBSERVED | memhash plan, boots 8 and 9: a 150,994,944-byte data file at 0xC3E65000 in both boots; the dry run (plan DRYRUN-A step 1): `GUEST.DAT` (144,665,408 bytes) at 0xC446E000; each at the top of the 590 MiB low extent (INFERRED for the dry run) |
| Whether the map the wrapper sees equals a recorded one | UNKNOWN | the descriptor count differs between boots; the dry run's map was not recorded, only the bank's descriptor number (215); until a boot reports the runner's ranges `--declared` is the only guard |
| The guest's console command line | UNKNOWN | the default of `probes/xnuboot.py` (`serial=3`) is kept; whether the first monitor run needs another is not decided here |

## 3. Subcommands

| Subcommand | What it does | Writes |
| --- | --- | --- |
| `facts-check` | Reads the facts file strictly and checks every fact a dump can check against every dump. Prints `DUMP <name> OK` per dump, then what no dump can check. | nothing |
| `place-check` | `--base A --guest-mib N [--reserve-mib N] [--facts F] [--dump DIR ...] [--declared BASE:SIZE ...]`. Applies the interface's placement rule (alignment, layout, 2^36, one conventional descriptor, off the framebuffer, off the avoid and declared ranges) to every map. Prints `PLACE`, a `MAP` line per dump, `PROPOSAL low` and `PROPOSAL bank` (listed, never chosen: D1 is the operator's) and `RESULT maps=a/b`. | nothing |
| `synthetic` | The whole bundle from deterministic filler. | `--out` |
| `authorisation-template synthetic` or `real` | Runs the pipeline up to PIN.DAT and prints the authorisation record (section 6). | nothing |
| `real` | As `synthetic`, with `--kernelcache`, `--panel handed`, `withheld` or (with `--m1-run` only) `console`, `--sptm-hvc-calls 0` or `1`, and a boot-ready `--descriptor`. Host only. | `--out` |

Required for `synthetic`, `real` and the template: `--facts`, `--base` (D1), `--guest-mib` (D1), `--monitor`, `--reporter`, `--stores` (D4, a list from `ram`, `el2_sysreg`, `gic`), `--time-box-s` (D5); for a build also `--out`. For `real` also `--kernelcache`, `--panel` and `--sptm-hvc-calls` (D9) and `--descriptor`. Optional: `--dump` (repeatable; default `downloads/sp11/x*-run*`), `--reserve-mib`, `--declared`, `--note FILE` (section 6), `--seed LABEL`, `--gic-stop` (`docs/SP11-STICK-GIC.md`; `gic` must be in `--stores`), `--gic-quarantine` (with `--gic-stop`: the record text for passes 5 and 6 of that stage; `--monitor` must be a pass 5 or 6 wrapper), for `synthetic` the sizes `--image-mib`, `--tables-mib` and `--sptm-data-mib`, and for a build `--force`, `--allow-under`, `--no-manifest`, `--base-tree` with exactly two `--file NAME PATH SHA256` (`SLBOUNCE-DEVHE-MARK.EFI` and `Q1N1-PAYLOAD.EFI`), `--pins` or `--no-pins`, and `--plan-name`.

`--reserve-mib` defaults to the loader's own `bootstrap_reserve()` for the guest size. A different value makes PARAMS (made by the loader's rule) disagree with PIN.DAT, so it is refused (`PARAMS_MISMATCH`).

## 4. Inputs

### 4.1 The facts file

A local JSON file, never committed: copy `tools/testdata/sp11-facts.example.json` (SYNTHETIC: the framebuffer base and the table digests are invented; the platform constants repeat the OBSERVED values of `docs/SP11-STICK-BUNDLE.md`) to a file under `build/` and replace what your own dumps and boots say. It is read strictly: no unknown or duplicate key, every number checked (`FACTS_BAD`).

| Field | Meaning | Checked against |
| --- | --- | --- |
| `board_id`, `build_label` | PIN.DAT strings (at most 15 and 23 characters); the label also names the autorun plan unless `--plan-name` is given | nothing |
| `midr` | up to four MIDR values the pin accepts | nothing: a dump does not hold it |
| `boot_mpidr` | the boot CPU's affinity | an enabled CPU interface of every MADT |
| `cntfrq_hz` | the counter frequency | nothing: a dump does not hold it |
| `gicd_base`, `gicr_first`, `gicr_limit`, `gicr_stride`, `gicr_count`, `its_base` | the GIC pins | every MADT: the GICD and ITS structures; the redistributor ranges, or, in the unit's form, one base per CPU interface, contiguous at `gicr_stride` |
| `timer_intid` | the EL2 timer interrupt | the GTDT, non-secure EL2 timer GSIV |
| `gop` (`base`, `bytes`, `width`, `height`, `stride` in pixels, `format`) | the framebuffer the placement must stay off | the `gop_*` lines of each dump's `summary.txt`, where it has them; the range must not lie in conventional memory |
| `avoid` | up to four ranges the placement must stay off, tagged `EL2_UNREACHABLE`, `DEVICE_OWNED`, `OPERATOR` or a number | nothing; `place-check` keeps off them (`PLACE_AVOID`) |
| `acpi` | up to eight tables: `sig`, `length`, `sha256` | the same table of every dump |
| `ecam` | optional; segment, base and buses | the MCFG |

A fact that disagrees with any one dump is `FACTS_DISAGREE`, and the detail names the fact and the dump. A dump that is missing a table, or whose table has a wrong length or checksum, is `DUMP_INCOMPLETE`.

### 4.2 The dumps

A dump is a directory such as `downloads/sp11/x2-run10` (git-ignored local data: it is never committed, and the tests read it only in tests that SKIP without it): `memmap.raw` (the final EFI memory map; descriptors of `memmap_descriptor_size` bytes from `summary.txt`, default 48), `summary.txt`, `APIC.bin`, `GTDT.bin` and every other table the facts' `acpi` list names (`MCFG.bin` for `ecam`). Every dump found is used, because the unit's map differs from boot to boot: a placement must sit inside ONE conventional descriptor in all of them.

### 4.3 The placement

`--base`, `--guest-mib` and the reserve give the interface's placement: the guest, the reserve directly above it, then the 240 MiB work area, one contiguous range below 2^36. `--declared BASE:SIZE` names ranges the runner has declared in use (kept clear: `PLACE_DECLARED`). `place-check` lists the largest 32 MiB-aligned low candidate and the bank candidate with the largest guest that leaves room, verified against every map; it never picks one.

**The runner's big file, observed, and `--declared`.** On the unit the runner put a 144 MiB data file at 0xC3E65000 in both boots of the memhash plan (OBSERVED, boots 8 and 9): the top of the 590 MiB low extent, `[0xC3E65000, 0xCCE65000)`, which leaves `[0xA8000000, 0xC3E65000)`, 446 MiB, below it. A low placement at 0xa8000000 with a 288 MiB guest needs 576 MiB (288 + 48 + 240) and so meets the file: `place-check --base 0xa8000000 --guest-mib 288 --declared 0xC3E65000:0x9000000` ends `REFUSED=PLACE_DECLARED` with 0 of 6 local maps passing, while the bank at 0x880000000 with 384 MiB passes 6 of 6 (a host result of running the tool on the six local dumps, which are git-ignored data; they predate the memhash boots and do not show the file). `--declared BASE:SIZE` is repeatable and is taken by `place-check`, `synthetic`, `real` and `authorisation-template`: give it the file's base and its size in bytes (`0x9000000` is 144 MiB) and every other range the runner allocated, as the runner's page lists them or as `hashbig` prints them (`FILE ... BASE= BYTES=`). The 138 MiB `GUEST.DAT` sat at 0xC446E000 in the dry run (OBSERVED once; INFERRED: the top of the same extent), so declare what a boot reports and check again.

## 5. What a build writes

```
<out>/build-report.json            what was checked, what was NOT RUN, the descriptor and (`real`) its inputs; no paths or times
<out>/STICK-FILES.TXT              sha256  path of every file of the stage tree, sorted
<out>/bundle/SP11/                 SNAPMON.BIN SNAPREP.BIN GUEST.DAT PIN.DAT REPORT.DAT PAYLOAD.MAN
<out>/stage/                       with --base-tree: the tree for the stick
    EFI/BOOT/BOOTAA64.EFI, EFI/BOOT/startup.nsh        the shell and the autorun script
    SP11/SLBOUNCE-DEVHE-MARK.EFI, SP11/Q1N1-PAYLOAD.EFI, SP11/PAYLOAD.MAN
    SP11/SNAPMON.BIN SNAPREP.BIN GUEST.DAT PIN.DAT REPORT.DAT
    SP11/PLAN.TXT, README.TXT, MANIFEST.SHA256, SLBOUNCE.EFI, SLTEST.EFI, TCBLAUNCH.EXE
    TCBLAUNCH.EXE                                      the root copy slbounce's driver needs
```

`GUEST.DAT` is built from the chunks of `built`. Each chunk of `built['chunks']` and each guest table page of `built['tables']` is classified by the reservation of `place()` it lies in (handoff, image, monitors, frame table, frame map, page tables, SPTM data) into the interface's kinds: KERNEL, TABLES, HANDOFF, DEVICE_TREE, PARAMS, MONITOR_IMAGE, MONITOR_DATA. Overlapping chunks are flattened as the A16 loader's upload flattens them (ascending address, the later write wins), runs are padded to 16 bytes, and a run that is all zero, or a zero stretch of at least 64 KiB inside a run, becomes a zero chunk with no bytes in the file (PARAMS and DEVICE_TREE stay whole). A chunk below the region, in the bootstrap reserve, across two reservations, unaligned or empty, or a table page that is also a chunk, is refused (`BUILT_SHAPE`): the builder does not guess.

The PARAMS chunk is checked against the placement: `mem_low` is the guest base, `mem_high` the guest base plus guest plus reserve (the work base), and `watchdog_seconds` the time box (`PARAMS_MISMATCH`, `TIME_BOX_MISMATCH`). The monitor call's third argument, the PARAMS destination, must equal PARAMS_PA (`BUILT_SHAPE`). The default synthetic build writes a `GUEST.DAT` of 144,665,408 bytes (the cap is 256 MiB) and takes a few seconds. `build-report.json` names the descriptor that PIN.DAT pins (`descriptor`, its state; `descriptor_sha256`; `board_id` and `board_status`, null for a synthetic build without one) and, for `real`, a `real` section (`kernelcache_sha256`, `guest_profile`, `panel`, `sptm_hvc_calls`, `seed`).

## 6. The authorisation record

`PIN.DAT` carries `authorisation_sha256`, the SHA-256 of a text the operator approves (D7). The builder does not approve anything.

1. `authorisation-template synthetic ...` (or `real`; section 1a of its record: `docs/SP11-STICK-REAL.md` section 7) with the arguments of the build prints the record: the digests and sizes of the five files, the placement, the store classes with what each may touch, the time box, and the refusal and stop rules of `docs/SP11-MONITOR-PORT.md` section 14. It states "operator decision NOT RECORDED" and "Hardware: NOT RUN", names no person, and stays local: never commit it.
2. The operator reads it and decides. The approval is the digest of exactly that text; a changed byte is another record.
3. The build takes `--authorisation FILE` (the digest is computed from the file) or `--authorisation-sha256 HEX`, regenerates the record from its own arguments, and refuses a digest that is not that record's (`AUTH_RECORD_MISMATCH`). A different monitor, guest, placement, store class, time box or note is a different record, so an approval cannot be carried over to another build. No digest is `NO_AUTHORISATION`, raised before any filler is generated; an all-zero digest is refused the same way before any file is written.

The `gic` store class is listed in the record with what it touches marked UNKNOWN: which registers, until WI 4 and WI 6 (its register list) exist. Do not approve it before then. With `--gic-stop` the record is that of the GIC and stop dry run (`docs/SP11-STICK-GIC.md` sections 4 and 5): payload 0 is `SNAPMON-GIC.BIN`, no monitor is called, and each class names what that wrapper writes (the registers and frames, the record in `REPORT.DAT`), so its `gic` class is not marked UNKNOWN. With `--gic-stop --gic-quarantine` the text is that of passes 5 and 6 of the stage (`tools/sp11_stick_bundle_record_gic.py`): the classes name the registers one by one, section 4 says that the wrapper times itself and uses no PARAMS watchdog, and section 5 has the silence rule of that stage (15 s at any point, a NEXT line or not) and stops the series on NOT_RESTORED; the texts of the other kinds are byte for byte as they were. A wrapper that carries the outcome names `Q_NOT_HELD` and `FOREIGN_PENDING` under a `gic` record without the option is refused (`GIC_QUARANTINE_REQUIRED`), and the option with another wrapper (`GIC_QUARANTINE_ABSENT`). With `--m1-run BUILD_REPORT` the record is that of the monitor entry run (`docs/SP11-STICK-MONITOR.md` section 10).

**What the store-class lines say.** One record format serves `synthetic` and `real`, a dry run and a run with a monitor, so each line states the scope of its class and names its source for the dry-run wrapper (`docs/SP11-STICK-WRAPPER.md` section 4): RAM stores only to page 0 of `REPORT.DAT` and, after the stage COPYING, to the chunk destinations; cache maintenance by VA; AT S1E2W and AT S1E2R (they set PAR_EL1); MRS reads; nothing else. `el2_sysreg` is listed in every record because the interface requires it in every PIN.DAT (`STORE_REQUIRED`: `--stores ram` is refused, with or without a note), and its line says that the dry-run wrapper stores no EL2 system register. A build whose `SNAPMON.BIN` is not that wrapper has to say what it does in a note.

**The note.** `--note FILE`, on `authorisation-template` and on every build, appends the text of the file verbatim as a final section `6. Notes of this build (part of the digest)`, after a blank line; a record without a note has no section 6. The note is part of the text the operator approves, so a build needs the same `--note` as the template, and a changed note is another record (`AUTH_RECORD_MISMATCH`, as for any other change). Nothing else in the record changes when a note is given. The file is read as plain text: a regular file (no symbolic link) that holds some text, printable ASCII (0x20 to 0x7E) and line feeds only, at most 48 lines of at most 120 characters. Anything else is `NOTE_BAD`, and its detail quotes no byte of the file, so a note cannot write to the operator's terminal: a carriage return, a tab or an escape sequence is refused for that reason. The last line feed ends the last line and is not text. Like the rest of the record, a note names no person and stays local.

**What a real record adds.** For `real` the record has a section `1a. What this build is` after section 1, so that the approved digest also covers what the files are made from: the kernelcache (guest profile, SHA-256), the D9 choices as used, the boot seed label (a default, not an operator decision, when it is `sp11-stick-bundle`), and the descriptor (SHA-256, board id, status), with `guest view: pcie.ecam empty; D11 proper open` when it is EXPERIMENTAL and lists no config window (`docs/SP11-STICK-REAL.md` section 3). Another kernelcache, choice, seed or descriptor is another record; a synthetic record has no section 1a. As printed (digests shown as `<sha256>`):

```
1a. What this build is (the host-side real path of docs/SP11-STICK-REAL.md: GUEST.DAT is made from a kernelcache)
   kernelcache  guest profile j700ap-26A428  sha256 <sha256>
   D9 as built  panel withheld: the guest is given no framebuffer
                SPTM HVC call patch 0: a stock kernel
   boot seed    label sp11-stick-bundle (a default, not an operator decision; boot entropy UNIMPLEMENTED)
   descriptor   sha256 <sha256> (PIN.DAT descriptor_sha256)
                board sp11  status EXPERIMENTAL
                guest view: pcie.ecam empty; D11 proper open
```

## 7. The commands on the Mac

EXPERIMENTAL; the dry run (`docs/SP11-STICK-WRAPPER.md` section 12) booted a default-size bundle once; `real` has run on the host only. Values in angle brackets are the operator's or the coordinator's; the tool supplies none. `<BUILD ARGS>` stands for `--facts build/sp11-facts.json --base <D1> --guest-mib <D1> --monitor <SNAPMON.BIN> --reporter <SNAPREP.BIN> --stores <D4> --time-box-s <D5>`, optionally `--note <note file>` (the same on the template and on the build), plus `--dump DIR` for each dump if they are not under `downloads/sp11/`.

```sh
export Q1N1_SP11_TREE=<q1n1 checkout with tools/sp11_payload_manifest.py>   # absent or wrong: Q1N1_TREE_ABSENT
python3 tools/sp11_stick_bundle_build.py facts-check --facts build/sp11-facts.json
python3 tools/sp11_stick_bundle_build.py place-check --base <D1> --guest-mib <D1> --facts build/sp11-facts.json
python3 tools/sp11_stick_bundle_build.py authorisation-template synthetic <BUILD ARGS> > build/sp11-record.txt
# the operator reads build/sp11-record.txt and decides; then the same arguments again:
python3 tools/sp11_stick_bundle_build.py synthetic <BUILD ARGS> --out build/sp11-bundle \
    --authorisation build/sp11-record.txt --base-tree <staged base tree> \
    --file SLBOUNCE-DEVHE-MARK.EFI <slbounce build> <sha256> --file Q1N1-PAYLOAD.EFI <runner image> <sha256>
python3 tools/stage-sp11-boot-media.py verify --dest build/sp11-bundle/stage      # COMPLETE; shell_pin: match
```

The base tree is the one `docs/RUNNING.md` makes with `stage-sp11-boot-media.py stage` (shell, slbounce, sltest and tcblaunch present, no autorun plan yet). For a stick that will boot the unit leave `--no-pins` out, so that the verify reports `shell_pin: match`; the tests use `--no-pins` because their base tree holds stubs. The `real` form is the same with `real` in place of `synthetic` (and `authorisation-template real`), plus `--kernelcache <local path> --descriptor <boot-ready board descriptor> --panel <handed|withheld> --sptm-hvc-calls <0|1>`, and no `SNAP_*` variable in the environment (`ENV_SNAP_SET`: the build reads them, so an inherited one would change the guest).

## 8. The morning after (the operator's workflow)

EXPERIMENTAL proposal; no step has been run. The operator's rule stands: `diskutil mount` is not used again for this stick. Use the volume macOS mounted when the stick was plugged in; `diskutil list` (read-only) names the disk. If the stick is not mounted, stop and ask; do not mount it by hand. Because a second mount is not allowed, the stick is verified before it is unmounted.

1. On the host, check that `build/sp11-bundle/build-report.json` says `"hardware": "NOT RUN"`, `"status": "COMPLETE"` and `"shell_pin": "match"`, and that `shasum -a 256 build/sp11-record.txt` is the `authorisation_sha256` the operator approved.
2. Copy exactly the files of the list, one by one. No `cp -R`, no `rsync`, no drag and drop of the directory: they would add files nobody listed.
   ```sh
   OUT=build/sp11-bundle; STICK=<the volume macOS mounted>
   while read -r digest rel; do
       mkdir -p "$STICK/$(dirname "$rel")" && cp -X "$OUT/stage/$rel" "$STICK/$rel" || { echo "copy failed: $rel"; break; }
   done < "$OUT/STICK-FILES.TXT"
   ```
3. A used stick keeps the markers earlier plans wrote. This plan has one step, so its marker is `SP11/DONE1.TXT`: delete it by that name if `ls "$STICK/SP11"` shows it (INFERRED from the autorun section of `docs/RUNNING.md`: the script runs the first step whose marker is missing). Copying a stage tree over a used stick does not delete markers.
4. Verify from the stick's root; every line must say `OK`, otherwise redo the copy: `LIST="$(pwd)/$OUT/STICK-FILES.TXT"; (cd "$STICK" && shasum -a 256 -c "$LIST")`.
5. Unmount as `docs/RUNNING.md` step 6 says (`diskutil eject /dev/diskN`, the identifier from `diskutil list`).
6. On the host run the stager's `verify` on `$OUT/stage` again. It shows that the source tree did not change between the build and the copy; it says nothing about the stick.
7. Boot as the autorun section of `docs/RUNNING.md` describes, with the stop rules of `docs/SP11-MONITOR-PORT.md` section 14. One hardware client only; AC power or at least 50% battery.

## 9. Refusals

The interface's names are used wherever it has one (`PLACE_*`, `PIN_*`, `FILE_*`, `CHUNK_*`, `GUEST_COUNT`, `NO_AUTHORISATION`); the builder adds these.

| Name | Raised when |
| --- | --- |
| `FACTS_BAD` | the facts file is unreadable, or not the strict JSON of the example (unknown or duplicate key, wrong `format`, a value that is not a number) |
| `FACTS_DISAGREE` | a fact differs from a dump (the detail names both), or from the boot-ready descriptor |
| `NO_DUMPS` | no `--dump` and nothing under `downloads/sp11/x*-run*` |
| `DUMP_INCOMPLETE` | a dump has no `memmap.raw` or no needed table, or a table is malformed |
| `FB_UNKNOWN` | `place-check` without `--facts` on a dump that states no framebuffer: the placement cannot be kept off it |
| `DESCRIPTOR_NOT_READY` | `--descriptor` is not boot-ready (the committed `config/boards/sp11.json` is a template and is refused), or `real` was given none |
| `AUTH_RECORD_MISMATCH` | the digest is not that of the record this build regenerates |
| `Q1N1_TREE_ABSENT` | the manifest is wanted and `Q1N1_SP11_TREE` (or the default search beside the checkout) finds no q1n1 tree with `tools/sp11_payload_manifest.py` |
| `STAGE_REFUSED` | the stager or q1n1's manifest tool refused the plan, or the manifest lists other files than were written |
| `STAGE_VERIFY_FAILED` | the base tree, or the finished stage tree, is not COMPLETE under the stager's `verify` |
| `OUT_DIR_REFUSED` | `--out` is outside `build/` and every `--allow-under`, has `..` or a symbolic link, or is a protected location |
| `OUT_NOT_EMPTY` | `--out` exists and is not empty, and `--force` does not apply |
| `PAYLOAD_BAD` | a payload, the facts file or another input is missing, is not a regular file, or is too short |
| `REPORT_NOT_ZERO` | `REPORT.DAT` is not all zero |
| `ENV_SNAP_SET` | `real` with a `SNAP_*` variable in the environment |
| `KERNELCACHE_MISSING` | `real` with no kernelcache at the path |
| `OPTIONS_BAD` | an option is out of range or inconsistent (a time box of 0, an unknown store class, `--gic-stop` without `gic`, `--gic-quarantine` without `--gic-stop`, `--base-tree` without exactly the two `--file`, `--no-manifest` with either) |
| `GIC_QUARANTINE_REQUIRED` | `--monitor` carries the outcome names of pass 5 of the GIC stage (`Q_NOT_HELD` and `FOREIGN_PENDING`) and the record authorises the `gic` class without `--gic-quarantine` |
| `GIC_QUARANTINE_ABSENT` | `--gic-quarantine` is given and `--monitor` does not carry those outcome names |
| `NOTE_BAD` | the `--note` file is missing, a symbolic link, a directory or without any text, has a byte that is not printable ASCII or a line feed, more than 48 lines, a line of more than 120 characters, or more than 5,808 bytes |
| `PARAMS_MISMATCH` | the PARAMS chunk's `mem_low` or `mem_high` disagrees with the placement |
| `TIME_BOX_MISMATCH` | the PARAMS `watchdog_seconds` is not the time box |
| `BUILT_SHAPE` | `built` is not shaped like `build()`'s return value (section 5), the PARAMS chunk is missing or short, `build()` itself refused, or an APF device slice of the guest's tree overlaps owned memory (the loader's check) |

## 10. What is not done

- **The kernelcache path.** `real` follows the sequence of `probes/run.py` (`configure_guest`, `configure_txm_arena`, `configure_adreno_va_wide`, the timebase, `place`, the framebuffer rule for `--panel`, `SNAP_NATIVE_CALLS`, `SNAP_SPTM_HVC_CALLS` (from `--sptm-hvc-calls`) and `SNAP_SEED` (from `--seed`), `build()`, the APF check, PARAMS serialised again after the time box) and ran on the host against the J700 26A428 kernelcache: EXPERIMENTAL, the unit NOT RUN (`docs/SP11-STICK-REAL.md`). It calls `build()` with its defaults; the options `probes/run.py` passes (RAM disk, root hash, ISC image, AuxKC, trust additions) and `build()`'s other switches have no argument here. Whether the first monitor run needs any is UNKNOWN. A `built` with a chunk the classification does not know is refused with `BUILT_SHAPE`, the intended failure.
- **The wrapper and the reporter.** A dry-run `SNAPMON.BIN` and `SNAPREP.BIN` exist (`docs/SP11-STICK-WRAPPER.md`: no monitor inside; its dry run passed once on the unit, section 12); a wrapper with a monitor is UNIMPLEMENTED. `--monitor` and `--reporter` take any file and the builder cannot tell which it was given: the record names their digests, and the operator compares them with the wrapper build's `build-report.json`. The tests of this builder use placeholders that say so.
- **D1 to D10**: untouched. The tests build a bank placement and a low placement (D1) to show both work, and recommend neither.
- **Where the runner puts the 138 MiB GUEST.DAT** (OBSERVED once, 0xC446E000 in the dry run, section 2; a 144 MiB data file went to 0xC3E65000 in both boots of the memhash plan, section 4.3). The placement check cannot see the runner's own allocations; `--declared` takes them once `hashbig` or a boot reports them.
- **The console.** `probes/xnuboot.py`'s default command line (`serial=3`) is kept, except for `--panel console` (with `--m1-run` only: any other kind is `OPTIONS_BAD`), which makes the replacement that `probes/run.py` makes for `--console panel` (`serial=2`) on `xnuboot.BOOT_CMDLINE` before the guest is built and puts the loader's line back after it. `probes/run.py` also drops `-v` for a graphics boot; that is not an argument here.
- **Finding, resolved for the structure walk.** The unit's MADT writes its GICD structure 25 bytes long (ACPI gives 24) and has no redistributor-range structure (each GICC carries its own redistributor base). `tools/check-hamoa-acpi.py` and `probes/bootcontract.py` refused that dump, so any host flow through `from_hamoa` would have refused this unit's real tables. They now accept exactly the observed GICD form (25 bytes, final byte zero) and take the redistributor range from the per-CPU GICC bases when no GICR structure exists, and still refuse every other length, a nonzero pad, a missing, duplicated, overlapping, gapped or shifted base (`tools/test-hamoa-bootcontract.py`, `tools/test-hamoa-bootcontract-gicc.py`). What remains open (a policy decision, not needed for the bundle): the MCFG lists eight allocations while a descriptor holds at most four and the adapter requires equality, so a boot-ready descriptor cannot yet match the real MCFG; on the unit's dump `tools/check-hamoa-acpi.py` now reads all four tables and stops at the descriptor, which is a template (`descriptor-unimplemented`, by design). With the guest-view descriptor of `docs/SP11-STICK-REAL.md` section 3 (`pcie.ecam` empty) it passes the descriptor's gate and stops at `ecam-mismatch`, as expected: D11 proper, the adapter's equality rule, is left for later. The builder reads such a table as Linux does (extra bytes of a structure are ignored; the redistributors come from the per-CPU bases) and checks it against the facts, which is more lenient than the checkers on purpose.

## 11. Tests

```sh
python3 tools/test-sp11-stick-bundle-build.py                                   # all of it
SP11_BUILD_SKIP_SLOW=1 python3 tools/test-sp11-stick-bundle-build.py            # without the default-size build (about 144 MB under build/, deleted after)
Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-bundle-build.py       # the tests that need q1n1's manifest tool SKIP
python3 tools/test-sp11-stick-bundle-record.py                                  # the record, its note and its store-class lines (needs no q1n1 tree)
python3 tools/test-sp11-stick-bundle-real.py                                    # the real path (`Kernelcache` needs the boot set)
python3 tools/test-sp11-stick-bundle-record-gic.py                              # the record text of passes 5 and 6 and its coupling to the wrapper (the binary tests need clang and a q1n1 tree)
python3 tools/test-sp11-stick-bundle-record-gic-model.py                        # the same text against the host model of the stage, register by register (the same needs)
```

Every file the tests write is below `build/sp11-bundle-build-scratch/` (the record tests: `build/sp11-bundle-record-scratch/`), which they delete. The fixtures are synthetic (dumps, tables, a stage base tree of stub PE files, placeholder payloads); the record tests reuse those of the first file, which is at its size limit. `RealDumps` reads `downloads/sp11/` and SKIPs when it is absent; a skip is not a pass. The C validators run under the harness of `tools/test-sp11-stick-bundle.py`. The classes: `Facts`, `RealDumps`, `Place`, `Authorisation`, `Build` (determinism, the output rules, the stager's verify, the C and Python checks, the default sizes), `Chunks` (the shape of `build()`'s output, flattening, padding, PARAMS), `LibraryRefusals`, `Hygiene` and `Document`; and `Record` in the second file (the note, its digest and its refusals, the text of the store-class lines). The seeded refusals of this note's section 9 each have a case that makes the mistake and expects the name. Hardware: NOT RUN.

`tools/test-sp11-stick-bundle-record.py` also pins four synthetic record texts by digest, so that no move of the text or lines of a real build changes one; `tools/test-sp11-stick-bundle-record-gic.py` pins the digest of the text of `--gic-quarantine` and, from local files that are not committed, of the template that the facts and the run note of the third attempt of the GIC stage give (a LOCAL TEST record, not the authorised one), and holds its section 5 to the 15 s of `docs/SP11-STICK-GIC.md` section 11; `tools/test-sp11-stick-bundle-record-gic-model.py` holds what that text says is written and read to the host model of the stage (the tests of the stage itself are listed in `docs/SP11-STICK-GIC.md` section 9: `tools/test-sp11-stick-gic.py`, `-gic-quarantine.py`, `-gic-putback.py`, `-gic-hppir.py`, `-gic-doc.py`, `-gic-record.py`, `-gic-build.py` and `-gic-qemu.py`); `tools/test-sp11-stick-bundle-real.py` tests section 1a and the report's descriptor fields around a stand-in for the loader, and the real build with the J700 boot set (`docs/SP11-STICK-REAL.md` section 9).
