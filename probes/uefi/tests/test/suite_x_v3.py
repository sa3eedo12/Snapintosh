# SPDX-License-Identifier: MIT
"""Suites of the third version of the SP11 X2 tests image: T06 reads an ECAM window only for a root
complex in which the firmware's own PCI I/O, collected before ExitBootServices, showed an NVMe
controller (probes/uefi/tests/pciio.c, t_pcie.c, hook.c), and takes a photograph of the page, and
waits for it, before the first read of a window and again before the first read of the controller.

    XV3SourceTests   the source audit's pin on what pciio.c may do through the firmware's protocol,
                     the one call site and the one named constant of the wait, and T06's window
                     policy, each against deliberately defective sources
    XV3PciioTests    pciio.txt: the text the image writes against an independent decoder of the raw
                     dwords it was made from (probes/uefi/tests/host/pciio_text.py), and that
                     decoder against malformed files
    XV3MutantTests   seeded bugs in the collector, in T06's policy, verdict and photograph and in
                     the hook, which the native C tests must reject (and an unmodified copy, which
                     they must not, so that a mutant cannot fail for the way it was built)
    XV3QemuTests     the model image under QEMU + EDK2: T06 with EDK2's own PCI I/O and the NVMe
                     controller of the machine, pciio.txt on the stick against the page, the
                     photograph footers and their eight seconds, and a machine with no NVMe

The tests that decide what T06 may touch are the native ones (test_x_t06.c: a device model that
records every address read, and a fixture with an MCFG of eight entries shaped like the unit's);
what is here is what keeps them honest. None of this has run on the Surface Pro 11: a PASS here
says the code behaves as specified against host models and a standard firmware, nothing about the
unit, and the hardware status of the third version is NOT RUN.
"""
from __future__ import annotations

import concurrent.futures
import re
import struct

import audit
import pciio_text
import suite_x as sx
import suite_x_ebs as sxe
import suite_x_v2 as v2
import uefi_testkit as kit
from uefi_testkit import Base, ROOT, run_cmd, tail, text_of

PCIIO_C = "probes/uefi/tests/pciio.c"
PCIIO_H = "probes/uefi/tests/pciio.h"
T_PCIE = "probes/uefi/tests/t_pcie.c"
SUITE_H = "probes/uefi/tests/suite.h"
HOOK_C = "probes/uefi/tests/hook.c"
WAIT_SECONDS = 8


class XV3SourceTests(Base):
    def test_the_pciio_audit_passes_on_the_real_tree(self):
        self.check(audit.audit_pciio_access(ROOT) + audit.audit_sources(ROOT))

    def test_the_pciio_audit_rejects_defective_sources(self):
        cfg = "static int cfg_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)\n{\n    uint32_t dw = 0;\n"
        mem = "static int nvme_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)\n{\n    uint32_t dw = 0;\n"
        free = "(void)bs->free_pool(handles);"
        cases = (
            ("a Pci.Write", PCIIO_C, cfg, cfg + "    (void)io->pci.write;\n", ("pci.write",)),
            ("a Mem.Write", PCIIO_C, mem, mem + "    (void)io->mem.write;\n", ("mem.write",)),
            ("an Io.Read", PCIIO_C, cfg, cfg + "    (void)io->io.read;\n", ("io.read",)),
            ("an Io.Write", PCIIO_C, cfg, cfg + "    (void)io->io.write;\n", ("io.write",)),
            ("SetBarAttributes", PCIIO_C, cfg, cfg + "    (void)io->set_bar_attributes;\n",
             ("other than GetLocation",)),
            ("Attributes", PCIIO_C, cfg, cfg + "    (void)io->attributes;\n", ("other than GetLocation",)),
            ("Flush", PCIIO_C, cfg, cfg + "    (void)io->flush;\n", ("other than GetLocation",)),
            ("Map", PCIIO_C, cfg, cfg + "    (void)io->map;\n", ("other than GetLocation",)),
            ("AllocateBuffer", PCIIO_C, cfg, cfg + "    (void)io->allocate_buffer;\n",
             ("other than GetLocation",)),
            ("CopyMem", PCIIO_C, cfg, cfg + "    (void)io->copy_mem;\n", ("other than GetLocation",)),
            ("another boot service", PCIIO_C, free, free + "\n    (void)bs->allocate_pool;",
             ("boot service allocate_pool",)),
            ("a member offset no longer pinned", PCIIO_H,
             '_Static_assert(offsetof(efi_pci_io, flush) == 104, "Flush slot");\n', "",
             ("19 member offsets",)),
            ("the size no longer pinned", PCIIO_H,
             '_Static_assert(sizeof(efi_pci_io) == 160, "EFI_PCI_IO_PROTOCOL size");\n', "",
             ("size of efi_pci_io",)),
            ("Pci.Write made callable", PCIIO_H,
             "        efi_pci_io_cfg_read_fn read;\n        void *write;\n    } pci;",
             "        efi_pci_io_cfg_read_fn read;\n        efi_pci_io_cfg_read_fn write;\n    } pci;",
             ("typed members",)),
        )
        problems = []
        for name, relative, old, new, wanted in cases:
            tree = sxe.copy_tree("pciio-" + re.sub(r"\W+", "-", name), [(relative, old, new)])
            found = audit.audit_pciio_access(tree)
            if not found or any(not any(w in p for p in found) for w in wanted):
                problems.append(f"{name}: the audit said {found or 'nothing'}, expected {wanted}")
        # A mention in a comment or a string is not a call.
        quiet = sxe.copy_tree("pciio-quiet", [(PCIIO_C, cfg, cfg + "    /* io->pci.write and bs->allocate_pool */\n")])
        if audit.audit_pciio_access(quiet):
            problems.append(f"a comment that names a write was reported: {audit.audit_pciio_access(quiet)}")
        self.check(problems)

    def test_the_wait_is_one_named_constant_with_one_call_site(self):
        suite = (ROOT / SUITE_H).read_text(encoding="utf-8")
        problems = []
        defs = re.findall(r"#define\s+TESTS_PHOTOGRAPH_WAIT_SECONDS\s+(\d+)u?\b", suite)
        if defs != [str(WAIT_SECONDS)]:
            problems.append(f"TESTS_PHOTOGRAPH_WAIT_SECONDS is defined as {defs}, expected one definition: "
                            f"{WAIT_SECONDS}")
        calls = []
        for path in sorted((ROOT / "probes" / "uefi").rglob("*.[ch]")):
            if path.parent.name == "test":  # the host tests call it through their own models
                continue
            for n, line in enumerate(audit._code_only(path.read_text(encoding="utf-8")).splitlines(), 1):
                if re.search(r"->\s*photograph\s*\(", line):
                    calls.append((path.name, n, line.strip()))
        if len(calls) != 1 or calls[0][0] != "t_pcie.c" or "TESTS_PHOTOGRAPH_WAIT_SECONDS" not in calls[0][2]:
            problems.append(f"the photograph is called from {calls}, expected one call in t_pcie.c that passes "
                            f"TESTS_PHOTOGRAPH_WAIT_SECONDS")
        hook = (ROOT / HOOK_C).read_text(encoding="utf-8")
        if len(re.findall(r"g_tc\.photograph\s*=\s*photograph_wait\s*;", hook)) != 1:
            problems.append("hook.c does not install photograph_wait exactly once")
        for stray in re.findall(r"\b(?:WAIT|PHOTO)\w*_(?:SECONDS|S)\b", hook):
            if stray not in ("PHOTO_MAX_SECONDS", "PHOTO_BACKWARD_LIMIT_SECONDS"):  # limits, not lengths
                problems.append(f"hook.c defines or uses another wait length: {stray}")
        self.check(problems)

    def test_t06_keeps_the_window_policy_and_has_no_2_36_rule(self):
        text = audit._code_only((ROOT / T_PCIE).read_text(encoding="utf-8"))
        problems = []
        if "tests_allow_add_device(" not in text or '"MCFG ECAM"' not in (ROOT / T_PCIE).read_text(encoding="utf-8"):
            problems.append("T06 does not put its ECAM windows through tests_allow_add_device")
        if "TESTS_ALIGN_ECAM" not in text or "TESTS_ALIGN_NVME" not in text:
            problems.append("T06 does not apply the alignment of an ECAM window and of an NVMe BAR")
        if re.search(r"<<\s*36\b|0x1000000000\b|ECAM_LIMIT|ABOVE_LIMIT", text):
            problems.append("T06 has a 2^36 rule: the window policy is alignment, the memory map and the "
                            "allow list, and TESTS_PA_LIMIT")
        env = (ROOT / "probes/uefi/tests/env.h").read_text(encoding="utf-8")
        if "#define TESTS_PA_LIMIT (1ull << 52)" not in env:
            problems.append("TESTS_PA_LIMIT is not 1 << 52")
        self.check(problems)


# ---- pciio.txt against an independent decoder ----

def emitted_fixtures():
    rc, out, err = run_cmd([sx.native_exe("plain"), "--emit-pciio"])
    if rc != 0:
        raise AssertionError(f"--emit-pciio failed: {tail(text_of(err))}")
    fixtures, current = {}, None
    for line in text_of(out).splitlines():
        if line.startswith("FIXTURE "):
            current = fixtures.setdefault(line.split()[1], {"raw": [], "text": None})
        elif current is not None and line.startswith("RAW "):
            fields = dict(re.findall(r"(\w+)=(\S+)", line))
            current["raw"].append(dict(
                seg=int(fields["seg"]), bus=int(fields["bus"]), dev=int(fields["dev"]), func=int(fields["func"]),
                hid=int(fields["hid"], 16), uid=int(fields["uid"]),
                cfg=[int(x, 16) for x in fields["cfg"].split(",")],
                reg=[int(x, 16) for x in fields["reg"].split(",")]))
        elif current is not None and line.startswith("TEXT "):
            current["text"] = bytes.fromhex(line[5:]).decode("ascii")
    return fixtures


class XV3PciioTests(Base):
    def test_the_text_agrees_with_an_independent_decoder_of_the_raw_dwords(self):
        fixtures = emitted_fixtures()
        problems = []
        if sorted(fixtures) != ["basic", "no-nvme"]:
            self.fail(f"the fixtures are {sorted(fixtures)}")
        for name, fx in fixtures.items():
            want = pciio_text.expected_text(fx["raw"], "TESTBUILD001", "HARDWARE")
            if fx["text"] != want:
                got, exp = fx["text"].splitlines(), want.splitlines()
                diff = [f"{g!r} != {e!r}" for g, e in zip(got, exp) if g != e][:2]
                problems.append(f"{name}: the image's text and the decoder's differ "
                                f"({len(got)} lines against {len(exp)}): {diff}")
                continue
            parsed = pciio_text.parse(fx["text"])
            nvme = [r for r in fx["raw"] if pciio_text.is_nvme(r)]
            if len(parsed["controllers"]) != len(nvme) or parsed["nvme"] != len(nvme):
                problems.append(f"{name}: the reader counts {len(parsed['controllers'])} controllers, "
                                f"the fixture holds {len(nvme)}")
            if not parsed["complete"]:
                problems.append(f"{name}: the file does not end with its closing line")
        basic = pciio_text.parse(fixtures["basic"]["text"])
        ctl = basic["controllers"][0] if basic["controllers"] else {}
        if (ctl.get("bar0"), ctl.get("regs"), ctl.get("cap")) != (0x7A000000, "OK", 0x004008200107FFFF):
            problems.append(f"the controller line decodes to {ctl}")
        endpoint = [f for f in basic["functions"] if f.get("cls") == pciio_text.CLASS_NVME]
        if len(endpoint) != 1 or endpoint[0]["root"] != "PcieRoot(0x6)" or endpoint[0]["seg"] != 6:
            problems.append(f"the NVMe function decodes to {endpoint}")
        self.check(problems)

    def test_the_reader_refuses_malformed_files(self):
        good = emitted_fixtures()["basic"]["text"]
        lines = good.splitlines(keepends=True)
        cases = {
            "no title": "".join(lines[1:]),
            "a state that does not exist": good.replace("state=OK", "state=MAYBE"),
            "a pci line cut short": good.replace(" cmd=0006 sts=0010 bar=00000000,00000000 buses=00:01:01", ""),
            "a lower case id": good.replace("id=1B36:0010", "id=1b36:0010"),
            "counts that do not match": good.replace("kept=3", "kept=4"),
            "a controller that is not there": good.replace("nvme=1 ", "nvme=2 "),
            "a nvme line for a missing function": good.replace("nvme idx=1", "nvme idx=7"),
            "a line of no kind": good.replace("\nend\n", "\nsomething else\nend\n"),
        }
        problems = []
        for name, text in cases.items():
            try:
                pciio_text.parse(text)
            except pciio_text.PciioError:
                continue
            problems.append(f"{name}: the reader accepted it")
        # A file the buffer cut short is still read, and says it is not complete.
        cut = good[: good.rindex("end\n")]
        try:
            if pciio_text.parse(cut)["complete"]:
                problems.append("a file without its closing line was called complete")
        except pciio_text.PciioError as exc:
            problems.append(f"a file cut at a line boundary was refused: {exc}")
        self.check(problems)


# ---- Seeded bugs ----

MUTANTS = (
    # T06's policy: which window may be read
    ("T06 scans every window again", T_PCIE,
     "    /* Each target window is validated as every device window is (alignment, the memory-map",
     "    for (unsigned k = 0; k < mcfg.n && ntg < TESTS_MAX_ECAM; k++) {\n"
     "        unsigned at;\n\n"
     "        for (at = 0; at < ntg && tg[at].window != k; at++)\n            ;\n"
     "        if (at == ntg) {\n            probe_memset(&tg[ntg], 0, sizeof tg[ntg]);\n"
     "            tg[ntg].segment = mcfg.e[k].segment;\n            tg[ntg].window = k;\n"
     "            tg[ntg].fw_count = 1;\n            ntg++;\n        }\n    }\n"
     "    /* Each target window is validated as every device window is (alignment, the memory-map"),
    ("T06 ignores the UID check", T_PCIE, "    if (f->root_uid != f->segment)\n        return SKIP_UID_DIFFERS;\n", ""),
    ("T06 ignores that the root is not a PCI root", T_PCIE,
     "    if (!tests_pciio_hid_is_pci_root(f->root_hid))\n        return SKIP_NOT_ROOT;\n", ""),
    ("T06 reads a segment that is in the MCFG twice", T_PCIE,
     "    if (in_segment > 1)\n        return SKIP_TWICE_IN_MCFG;\n", ""),
    ("T06 reads a window that starts above bus 0", T_PCIE,
     "    if (mcfg->e[at].start_bus != 0)\n        return SKIP_START_BUS;\n", ""),
    ("T06 trusts an MCFG that lost entries", T_PCIE,
     "    if (mcfg->truncated != 0)\n        return SKIP_MCFG_TRUNCATED;\n", ""),
    ("T06 takes the segment for the index into the MCFG", T_PCIE, "    *window = at;", "    *window = f->segment;"),
    ("T06 reads without a firmware NVMe", T_PCIE,
     "if (pre == NULL || pre->state != TESTS_PCIIO_OK || pre->n_nvme == 0) {", "if (pre == NULL) {"),
    ("a refused window is read anyway", T_PCIE,
     "        if (tg[t].status != TESTS_WIN_OK)\n            continue;\n        s.window = tg[t].window;",
     "        s.window = tg[t].window;"),
    ("a function the firmware could not locate justifies a read", T_PCIE,
     "    if (!f->located || !f->cfg_ok)\n        return SKIP_RECORD_INCOMPLETE;\n", ""),
    # T06's comparison and verdict
    ("CAP/VS mismatch still PASS", T_PCIE, "    return ep->cap == ep->fw->cap && ep->vs == ep->fw->vs;", "    return 1;"),
    ("VS is not compared", T_PCIE, "    return ep->cap == ep->fw->cap && ep->vs == ep->fw->vs;",
     "    return ep->cap == ep->fw->cap;"),
    ("CAP is not compared", T_PCIE, "    return ep->cap == ep->fw->cap && ep->vs == ep->fw->vs;",
     "    return ep->vs == ep->fw->vs;"),
    ("the vendor and device ids are not compared", T_PCIE, "fw->cfg_ok && fw->id == ep_id &&", "fw->cfg_ok &&"),
    ("the class is not compared", T_PCIE, "(fw->class_rev >> 8) == (ep_cls >> 8) && fw->bar0_base == bar;",
     "fw->bar0_base == bar;"),
    ("BAR0 is not compared", T_PCIE, "(fw->class_rev >> 8) == (ep_cls >> 8) && fw->bar0_base == bar;",
     "(fw->class_rev >> 8) == (ep_cls >> 8);"),
    ("the registers are read although the view differs", T_PCIE, "    if (!s->ep.match) {\n        s->ep.regs = REGS_NOT_READ;",
     "    if (0) {\n        s->ep.regs = REGS_NOT_READ;"),
    ("the registers are read with memory decode off", T_PCIE,
     "    if (!(ep_cmd & 2u) || !(root_cmd & 2u) || bar == 0 || (bar & 0xFFFu) != 0 || !in_win) {",
     "    if (0 && (root_cmd & 2u)) {"),
    ("a mismatch of ids is not a FAIL", T_PCIE, "    if (t->view_mismatch != 0) {", "    if (0) {"),
    ("a fault does not make the verdict UNKNOWN", T_PCIE,
     "    if (t->faulted != 0) {\n        *why = \"A READ FAULTED\";", "    if (0) {\n        *why = \"A READ FAULTED\";"),
    ("a missing endpoint is not a FAIL", T_PCIE, "    if (t->no_endpoint != 0) {", "    if (0) {"),
    ("a controller the firmware read badly still passes", T_PCIE, "    if (t->regs_incomplete != 0) {", "    if (0) {"),
    # The photograph before the read
    ("no photograph before the first window", T_PCIE,
     'if (announce(&s, &s.photo_ecam_done, "T06 ECAM ", e->base) != 0) {', "if (0) {"),
    ("no photograph before the controller", T_PCIE,
     'if (announce(s, &s->photo_bar_done, "T06 NVME ", bar) != 0) {', "if (0) {"),
    ("a photograph before every window", T_PCIE, "    int first = !*done;", "    int first = 1;"),
    ("a failed photograph does not stop the read", T_PCIE,
     "            s->photo_failed = 1;\n            s->photo_code = rc;\n            return -1;",
     "            s->photo_failed = 1;\n            s->photo_code = rc;\n            return 0;"),
    ("the wait is five seconds", T_PCIE,
     "c->photograph(c->photograph_ctx, note, TESTS_PHOTOGRAPH_WAIT_SECONDS)", "c->photograph(c->photograph_ctx, note, 5u)"),
    ("the wait is a tenth of what is asked", HOOK_C,
     "    total = (uint64_t)seconds * freq;", "    total = (uint64_t)seconds * freq / 10u;"),
    ("an implausible frequency is used as it is", HOOK_C,
     "    return (f >= PHOTO_FREQ_MIN && f <= PHOTO_FREQ_MAX) ? f : PHOTO_FREQ_FALLBACK;", "    return f;"),
    ("the page is not drawn before the wait", HOOK_C, "    draw_page(0, 1, ft);\n    shown = seconds;",
     "    shown = seconds;"),
    ("a counter that runs backwards is not refused", HOOK_C,
     "            if (high - now > (uint64_t)PHOTO_BACKWARD_LIMIT_SECONDS * freq)\n"
     "                return TESTS_PHOTO_BACKWARDS;\n", ""),
    ("a read below the highest ends the wait", HOOK_C,
     "            if (high - now > (uint64_t)PHOTO_BACKWARD_LIMIT_SECONDS * freq)", "            if (now < high)"),
    # The collector (pciio.c)
    ("pre-EBS code calls Pci.Write", PCIIO_C,
     "static int cfg_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)\n{\n    uint32_t dw = 0;\n",
     "static int cfg_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)\n{\n    uint32_t dw = 0;\n\n"
     "    ((efi_status (*)(void))io->pci.write)();\n"),
    ("pre-EBS code calls Mem.Write", PCIIO_C,
     "static int nvme_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)\n{\n    uint32_t dw = 0;\n",
     "static int nvme_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)\n{\n    uint32_t dw = 0;\n\n"
     "    ((efi_status (*)(void))io->mem.write)();\n"),
    ("pre-EBS code calls Flush", PCIIO_C,
     "static int nvme_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)\n{\n    uint32_t dw = 0;\n",
     "static int nvme_dword(efi_pci_io *io, uint32_t offset, uint32_t *v)\n{\n    uint32_t dw = 0;\n\n"
     "    ((efi_status (*)(void))io->flush)();\n"),
    ("pre-EBS code reads a register that is not on the list", PCIIO_C, "!nvme_dword(io, TESTS_NVME_AQA, &aqa)",
     "!nvme_dword(io, 0x20u, &aqa)"),
    ("pre-EBS code reads the BARs a bridge does not have", PCIIO_C, "        want = TESTS_PCIIO_BARS_BRIDGE;",
     "        want = TESTS_PCIIO_BARS;"),
    ("pre-EBS code reads the controller with memory decode off", PCIIO_C,
     "    if ((f->cmd_status & TESTS_PCIIO_CMD_MEMORY) == 0) {", "    if (0) {"),
    ("the handle list is not given back", PCIIO_C, "    (void)bs->free_pool(handles);\n", ""),
    ("more handles than the table holds overrun it", PCIIO_C,
     "        if (out->n >= TESTS_PCIIO_MAX)\n            break;\n", ""),
    ("a device path node is read past the length it was given", PCIIO_C,
     "    if (node_len != DP_ACPI_NODE_BYTES || node_len > max_len)", "    if (node_len != DP_ACPI_NODE_BYTES)"),
    ("a class code of another programming interface is NVMe", PCIIO_H,
     "    return (class_rev >> 8) == TESTS_PCIIO_CLASS_NVME;", "    return (class_rev >> 16) == (TESTS_PCIIO_CLASS_NVME >> 8);"),
    ("registers read before a failure are kept", PCIIO_C,
     "        f->regs_state = TESTS_PCIIO_REGS_READ_FAILED;\n        return;\n",
     "        f->regs_state = TESTS_PCIIO_REGS_READ_FAILED;\n        f->cc = 0xC0FFEEu;\n        return;\n"),
    # The hook: what phase A leaves on the stick
    ("pciio.txt is written before the K1 record", HOOK_C,
     "    /* Text that did not fit is still valid text up to where it stops; it is written as it is. */",
     "    nfiles = 0;\n    /* Text that did not fit is still valid text up to where it stops; it is written as it is. */"),
    ("the PCI I/O view is not collected where the platform is not at EL2 yet", HOOK_C,
     "    collect_pciio(c);\n    if (!c->facts->have_preebs_el || probe_currentel_decode(c->facts->preebs_el_raw) != 2) {\n"
     "        marker(\"SKIP\", \"REASON\", \"NOT_EL2\", NULL, NULL, NULL, NULL);\n        persist_pre_ebs(0);\n"
     "        return 0;\n    }\n",
     "    if (!c->facts->have_preebs_el || probe_currentel_decode(c->facts->preebs_el_raw) != 2) {\n"
     "        marker(\"SKIP\", \"REASON\", \"NOT_EL2\", NULL, NULL, NULL, NULL);\n        persist_pre_ebs(0);\n"
     "        return 0;\n    }\n    collect_pciio(c);\n"),
    ("a platform not at EL2 writes no pciio.txt", HOOK_C, "        persist_pre_ebs(0);\n", ""),
)


class XV3MutantTests(Base):
    def test_an_unmodified_copy_passes(self):
        """The way the mutants are built does not itself break the tests."""
        exe, why = v2.mutated_exe("v3-control", None, "", "")
        self.assertIsNotNone(exe, why)
        self.check(v2.native_problems(exe))

    def test_every_mutant_changes_the_text_it_names_exactly_once(self):
        problems = []
        for name, relative, old, new in MUTANTS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            if text.count(old) != 1:
                problems.append(f"{name}: the text to change occurs {text.count(old)} times in {relative}")
            if old == new:
                problems.append(f"{name}: the replacement is the original")
        self.check(problems)

    def test_seeded_bugs_are_rejected_by_the_native_tests(self):
        def run(indexed):
            index, (name, relative, old, new) = indexed
            exe, why = v2.mutated_exe(f"v3-m{index:02d}", relative, old, new)
            if exe is None:
                return name, why
            return name, None if v2.native_problems(exe) else "the C tests still pass"

        problems = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for name, problem in pool.map(run, enumerate(MUTANTS)):
                if problem:
                    problems.append(f"{name}: {problem}")
        self.check(problems)


# ---- The images ----

class XV3BuildTests(Base):
    def test_the_image_names_and_the_defines_of_the_machine_images_are_unchanged(self):
        bt = sx.build_tool()
        problems = []
        for name, image in (("hardware", "sp11-el2-tests.efi"), ("declared", "sp11-el2-tests-declared.efi")):
            variant = bt.VARIANTS[name]
            if variant.image != image:
                problems.append(f"{name} is built to {variant.image}, the stager takes {image}")
            if not variant.shipped:
                problems.append(f"{name} is not a shipped variant")
        hardware = bt.VARIANTS["hardware"]
        declared = bt.VARIANTS["declared"]
        if "TESTS_DECLARE_EBS_RANGES=1" in hardware.defines or "TESTS_DECLARE_EBS_RANGES=1" not in declared.defines:
            problems.append("the declaration is not exactly what separates the two images")
        for variant in (hardware, declared):
            if any(d.startswith(("TESTS_READ_CNTPOFF", "TESTS_MODEL_BUILD=1")) for d in variant.defines):
                problems.append(f"{variant.name} carries a define it must not: {variant.defines}")
        self.check(problems)

    def test_the_pciio_unit_is_part_of_every_image(self):
        bt = sx.build_tool()
        self.assertIn("pciio.c", bt.TESTS_SOURCES)
        self.assertIn("t_pcie.c", bt.TESTS_SOURCES)
        for name, built in sx.x_images().items():
            manifest = built.manifest.read_text(encoding="utf-8")
            if "pciio.c" not in manifest:
                self.fail(f"the manifest of {name} does not list pciio.c")

    def test_the_images_carry_the_photograph_footer_text(self):
        """The footer the photograph draws is in the bytes of every image, so `strings` shows that
        the wait is there."""
        problems = []
        for name, built in sx.x_images().items():
            if b"PHOTO NOW: " not in built.image.read_bytes():
                problems.append(f"{name} has no photograph footer text")
        self.check(problems)


# ---- QEMU --------------------------------------------------------------------------------------

def footers(out):
    """(time, payload) of the footer of every decoded frame the run recorded, in order."""
    return [(smp.t, smp.screen.footer.check.payload) for smp in out.samples
            if smp.screen.ok and smp.screen.footer is not None]


def photograph_window(seq, kind):
    """What the footers show of one photograph: (when "PHOTO NOW: T06 <kind> ..." was first seen,
    when it was last seen, when the first other footer followed, the countdown values seen, the
    address). The plain "T06 <kind> ..." footer that follows in the image lasts only as long as the
    reads do, which on a model is a fraction of a second and may fall between two screenshots, so the
    end is whatever footer comes after the last countdown value."""
    photo = re.compile(rf"^PHOTO NOW: T06 {kind} ([0-9A-F]{{10}}) IN (\d) S$")
    seen = [(t, m) for t, p in seq if (m := photo.match(p))]
    if not seen:
        return None
    last = seen[-1][0]
    end = next((t for t, p in seq if t > last and not photo.match(p)), None)
    return seen[0][0], last, end, [int(m.group(2)) for _, m in seen], seen[0][1].group(1)


def report_complete(out, allow_not_run=()):
    """Every page of the final report was seen and the summary gives every test a verdict (the
    tests in allow_not_run may be NOT-RUN: T06 on a machine with no NVMe is)."""
    total, pages = sx.report_pages(out)
    if total < 2 or len(pages) != total:
        return False
    rows = [p for _, body in sx.page_rows(out) for p in body]
    for i in range(1, 10):
        verdicts = "INFO|PASS|FAIL|UNKNOWN|GO|NO-GO|INCONCL" + ("|NOT-RUN" if i in allow_not_run else "")
        if not any(re.match(rf"^T0{i} .+ ({verdicts}) ", r) for r in rows):
            return False
    return True


def t06_section(rows):
    start = next((i for i, r in enumerate(rows) if r.startswith("== T06")), None)
    if start is None:
        return []
    end = next((i for i, r in enumerate(rows) if i > start and r.startswith("== T07")), len(rows))
    return rows[start + 1:end]


class XV3QemuTests(Base):
    def setUp(self):
        if kit.SKIP_QEMU:
            self.skipTest("UEFI_PROBE_SKIP_QEMU=1")

    def test_t06_reads_the_nvme_the_firmware_showed_and_compares_the_two_views(self):
        out = sx.default_run()
        section = t06_section(v2.report_rows(out))
        problems = []
        if not section:
            self.fail("no T06 section on the pages")
        for exact in ("FW NVME FUNCTIONS=1", "SCANNED SEGMENTS=1 MCFG=1", "MATCHES FIRMWARE VIEW=YES",
                      "CAP VS EQUAL=YES", "FAULTS=0"):
            if exact not in section:
                problems.append(f"the row {exact!r} is not in T06's section: {section}")
        if not any(re.match(r"^NVME0 SEG=0 UID=0 [0-9A-F]{2}:00\.0$", r) for r in section):
            problems.append("no row names the firmware's NVMe function with its segment and UID")
        regs = {}
        for r in section:
            m = re.match(r"^(CAP|ASQ|ACQ) (PRE|EL2)=([0-9A-F]{16})$", r) or \
                re.match(r"^(VS|CC|CSTS|AQA) PRE=([0-9A-F]{8}) EL2=([0-9A-F]{8})$", r)
            if m and m.group(1) in ("CAP", "ASQ", "ACQ"):
                regs[(m.group(1), m.group(2))] = int(m.group(3), 16)
            elif m:
                regs[(m.group(1), "PRE")], regs[(m.group(1), "EL2")] = int(m.group(2), 16), int(m.group(3), 16)
        for name in ("CAP", "VS", "CC", "CSTS", "AQA", "ASQ", "ACQ"):
            if (name, "PRE") not in regs or (name, "EL2") not in regs:
                problems.append(f"no PRE and EL2 values of {name} on the page")
        for constant in ("CAP", "VS"):
            if regs.get((constant, "PRE")) != regs.get((constant, "EL2")):
                problems.append(f"{constant} differs between the firmware's reading and EL2's")
        if any(r.startswith("WHY:") for r in section):
            problems.append(f"T06 gives a reason it did not pass: {[r for r in section if r.startswith('WHY:')]}")
        marker = [m for m in sx.x_markers(out) if m[0] == "PCIIO"]
        if len(marker) != 1 or marker[0][1].get("STATE") != "OK" or marker[0][1].get("NVME") != "1":
            problems.append(f"the PCIIO marker is {[m[2] for m in marker]}")
        self.check(problems, "\n".join(section))

    def test_pciio_txt_on_the_stick_agrees_with_the_page_and_the_dumped_mcfg(self):
        out = sx.default_run()
        if out.stick is None:
            self.fail(f"no stick was recovered: {out.stick_error}")
        files = out.stick.files
        problems = []
        if "SP11DUMP/RUN01/pciio.txt" not in files:
            self.fail("SP11DUMP/RUN01/pciio.txt was not written")
        try:
            parsed = pciio_text.parse(text_of(files["SP11DUMP/RUN01/pciio.txt"]))
        except pciio_text.PciioError as exc:
            self.fail(f"pciio.txt is not a file the reader accepts: {exc}")
        if not parsed["complete"] or parsed["state"] != "OK" or parsed["kind"] != "MODEL":
            problems.append(f"pciio.txt: complete={parsed['complete']} state={parsed['state']} kind={parsed['kind']}")
        if len(parsed["controllers"]) != 1:
            self.fail(f"pciio.txt holds {len(parsed['controllers'])} NVMe controllers, QEMU has one")
        ctl = parsed["controllers"][0]
        fn = parsed["functions"][ctl["idx"]]
        if fn.get("cls") != pciio_text.CLASS_NVME or fn.get("seg") != 0 or ctl.get("regs") != "OK":
            problems.append(f"the NVMe function and its registers decode to {fn} {ctl}")
        if not fn["root"].endswith("Root(0x0)"):
            problems.append(f"the root bridge of the NVMe function is {fn['root']!r}")
        # The page's PRE values are the ones in the file.
        section = t06_section(v2.report_rows(sx.default_run()))
        want = {"CAP PRE": f"{ctl['cap']:016X}", "ASQ PRE": f"{ctl['asq']:016X}", "ACQ PRE": f"{ctl['acq']:016X}"}
        for key, value in want.items():
            if f"{key}={value}" not in section:
                problems.append(f"the page has no row {key}={value}, which pciio.txt says the firmware read")
        for key, field_name in (("VS", "vs"), ("CC", "cc"), ("CSTS", "csts"), ("AQA", "aqa")):
            if not any(r.startswith(f"{key} PRE={ctl[field_name]:08X} ") for r in section):
                problems.append(f"the page does not show {key} PRE={ctl[field_name]:08X}")
        # The dump's own MCFG names one window; T06 scanned exactly that one.
        mcfg = files.get("SP11DUMP/RUN01/MCFG.bin")
        if mcfg is None:
            problems.append("the dump has no MCFG.bin to compare the window with")
        else:
            entries = (len(mcfg) - 44) // 16
            if entries != 1 or "SCANNED SEGMENTS=1 MCFG=1" not in section:
                problems.append(f"the MCFG has {entries} entries and T06 says "
                                f"{[r for r in section if r.startswith('SCANNED')]}")
        self.check(problems)

    def test_the_photograph_footers_hold_for_eight_seconds_before_each_kind_of_read(self):
        out = sx.default_run()
        seq = footers(out)
        problems = []
        stick = {} if out.stick is None else out.stick.files
        mcfg = stick.get("SP11DUMP/RUN01/MCFG.bin")
        base = struct.unpack_from("<Q", mcfg, 44)[0] if mcfg and len(mcfg) >= 52 else None
        try:
            bar0 = pciio_text.parse(text_of(stick["SP11DUMP/RUN01/pciio.txt"]))["controllers"][0]["bar0"]
        except (KeyError, IndexError, pciio_text.PciioError):
            bar0 = None
        for kind, address in (("ECAM", base), ("NVME", bar0)):
            window = photograph_window(seq, kind)
            if window is None:
                problems.append(f"no footer reads 'PHOTO NOW: T06 {kind} <address> IN <n> S' in "
                                f"{len(seq)} decoded frames")
                continue
            start, last, end, counts, shown = window
            # The first countdown value the screenshots caught, n, was on the screen n seconds before
            # the wait ended; a screenshot may be up to half a second late at either end.
            if end is None:
                problems.append(f"{kind}: no footer followed the photograph's")
            elif not counts[0] - 1.5 <= end - start <= counts[0] + 20.0:
                problems.append(f"{kind}: the footer showed IN {counts[0]} S for {end - start:.1f} s")
            if counts[0] < WAIT_SECONDS - 1 or counts != sorted(counts, reverse=True) or len(set(counts)) < 4:
                problems.append(f"{kind}: the countdown seen is {counts}")
            if address is not None and shown != f"{address:010X}":
                problems.append(f"{kind}: the footer names {shown}, the machine's window is {address:010X}")
        self.check(problems, f"{len(seq)} frames, last footers {[p for _, p in seq[-3:]]}")

    def test_a_machine_without_an_nvme_gives_not_run_and_never_shows_a_photograph(self):
        out = sx.scenario("v3-nonvme", "model", sx.FULL, 150, nvme=False,
                          until=lambda o: report_complete(o, allow_not_run=(6,)))
        section = t06_section(v2.report_rows(out))
        problems = [f"harness: {out.failure}"] if out.failure else []
        if not section:
            self.fail("no T06 section on the pages")
        if "NO FIRMWARE NVME: NOTHING TOUCHED" not in section:
            problems.append(f"T06 does not say it touched nothing: {section}")
        if not any(r.startswith("PCI I/O: ") for r in section):
            problems.append("T06 does not say what the firmware's PCI I/O showed")
        if any(r.startswith(("W0 ", "SCANNED", "CAP ")) for r in section):
            problems.append(f"T06 shows rows of a window that was read: {section}")
        summary = [r for r in v2.report_rows(out) if r.startswith("T06 ")]
        if not summary or " NOT-RUN " not in summary[0]:
            problems.append(f"T06's summary row is {summary}, expected NOT-RUN")
        if any(p.startswith(("PHOTO NOW", "T06 ECAM", "T06 NVME")) for _, p in footers(out)):
            problems.append("a footer names a window or a photograph although T06 had nothing to read")
        marker = [m for m in sx.x_markers(out) if m[0] == "PCIIO"]
        if len(marker) != 1 or marker[0][1].get("NVME") != "0":
            problems.append(f"the PCIIO marker is {[m[2] for m in marker]}")
        files = {} if out.stick is None else out.stick.files
        if "SP11DUMP/RUN01/pciio.txt" in files:
            parsed = pciio_text.parse(text_of(files["SP11DUMP/RUN01/pciio.txt"]))
            if parsed["controllers"] or parsed["nvme"] != 0:
                problems.append("pciio.txt shows an NVMe controller on a machine that has none")
        else:
            problems.append("pciio.txt was not written on the machine without an NVMe")
        self.check(problems, "\n".join(section))
