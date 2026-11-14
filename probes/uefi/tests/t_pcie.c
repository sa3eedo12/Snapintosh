/* SPDX-License-Identifier: MIT */
/* T06: the PCIe link and the NVMe controller state, read-only, at the root complex the firmware
 * itself used for its NVMe, and nowhere else.
 *
 * Which windows. The MCFG lists every ECAM window, and a listed window is not a window anyone has
 * seen answer. On the Surface Pro 11 the first version of this test read every window in table
 * order and the read of segment 3 never returned: the footer stayed on its address and the board
 * reset itself (OBSERVED, boot 3). The firmware's NVMe is behind segment 6, so a read of segment 3
 * was a read of a root complex the firmware never used. The test now reads a segment only when
 *   - the firmware's own PCI I/O protocol, collected before ExitBootServices (pciio.c), shows an
 *     NVMe function there, and
 *   - a second source agrees: the ACPI UID of the root bridge in that function's device path
 *     equals the segment number (the DSDT has _SEG = _UID on the unit; INFERRED from its dump),
 *     exactly one MCFG entry carries the segment, and that entry starts at bus 0.
 * With no firmware NVMe it touches nothing: there is no blind scan.
 *
 * What is read inside a window. Device 0 of bus 0 first. If it is a PCI-to-PCI bridge (a root port,
 * which is what a Designware-style controller presents) it is the only device read on bus 0,
 * because such a root bus decodes device 0 alone and other device numbers alias it; if it is not a
 * bridge, devices 1..7 of bus 0 are read as well (a generic ECAM host bridge such as the one of the
 * QEMU model). Behind each bridge only device 0 of the secondary bus is read.
 *
 * The NVMe BAR is never assumed: it is read from the endpoint's own BAR0, and the controller's
 * registers are read only if the endpoint's memory decode is on, the bridge forwards memory, the
 * BAR lies inside a window the bridge advertises, and the endpoint is the function the firmware
 * recorded (same ids, same class, same BAR0 base). Seven registers are read, all without side
 * effect, and compared with what the firmware read before ExitBootServices: CAP and VS are
 * constants and must be equal; CC, CSTS, AQA, ASQ and ACQ may differ, and what the firmware's
 * ExitBootServices handler did to them is the information. Nothing is written.
 *
 * Offsets: PCI Local Bus / PCIe base specifications as listed in Linux v6.12
 * include/uapi/linux/pci_regs.h (vendor 0x00, command 0x04, status 0x06, class 0x08, header type
 * 0x0e, BAR0 0x10, secondary bus 0x19, memory base/limit 0x20/0x22, prefetchable
 * 0x24/0x26/0x28/0x2c, capability pointer 0x34, PCIe link status at capability + 0x12) and the
 * NVMe register offsets of pciio.h.
 */
#include "suite.h"

#define CFG_SPACE_BYTES 0x1000u
#define NVME_WINDOW_BYTES 0x1000u
#define CAP_ID_PCIE 0x10u
#define MAX_ROOT_DEVICES 8u
#define MAX_LINK_ROWS 3u
/* Rows the closing part still needs (the window summary, the fault row, a "not shown" row and the
 * reason row): nothing optional starts without room for them. */
#define CLOSING_ROWS 4u
#define MAX_FUNCTION_ROWS 3u
#define MAX_SKIP_ROWS 3u
#define MAX_REFUSAL_ROWS 3u
#define REGISTER_ROWS 10u
/* What the comparison of the first target needs when the walk is done: MATCHES, CAP VS EQUAL and
 * the ten rows of the seven registers. They are the point of the test, so while a window is walked
 * the rows of the walk give way first (pcie_scan.reserve). */
#define COMPARISON_ROWS (REGISTER_ROWS + 2u)
/* The rows of the path to an endpoint still to come while the root bus is walked: the link, the
 * endpoint and its BAR. */
#define PATH_ROWS 3u

enum {
    SKIP_NONE = 0,
    SKIP_RECORD_INCOMPLETE,
    SKIP_NO_UID,
    SKIP_NOT_ROOT,
    SKIP_UID_DIFFERS,
    SKIP_MCFG_TRUNCATED,
    SKIP_NOT_IN_MCFG,
    SKIP_TWICE_IN_MCFG,
    SKIP_START_BUS,
    SKIP_COUNT
};

static const char *const skip_names[SKIP_COUNT] = {
    "", "RECORD INCOMPLETE", "UID NOT AVAILABLE", "ROOT NOT A PCI ROOT", "UID DIFFERS FROM SEGMENT",
    "MCFG HAS TOO MANY ENTRIES", "SEGMENT NOT IN MCFG", "SEGMENT TWICE IN MCFG",
    "MCFG START BUS NOT 0",
};

enum { REGS_NOT_ASKED = 0, REGS_READ, REGS_NOT_READ, REGS_FAULT };

/* What the walk of one window saw behind its bridge, compared with the firmware's record. */
typedef struct {
    int found;
    unsigned bus;
    uint32_t id, cls, cmd;
    uint64_t bar0;
    const tests_pciio_fn *fw; /* the firmware's NVMe function at this bus, or NULL */
    int match;                /* ids, class and BAR0 base equal the firmware's */
    int regs;                 /* REGS_* */
    uint64_t cap, asq, acq;
    uint32_t vs, cc, csts, aqa;
} ep_view;

typedef struct {
    unsigned segment;
    unsigned window;   /* index of the MCFG entry */
    int status;        /* TESTS_WIN_OK once the window is on the allow list, else why not */
    int scanned;       /* the window was actually read */
    unsigned fw_count; /* firmware NVMe functions in this segment */
    ep_view ep;
} target;

typedef struct {
    tests_ctx *c;
    tests_result *r;
    tests_allow allow;
    const tests_pciio *pre;
    unsigned window; /* index of the MCFG entry being scanned */
    unsigned segment;
    unsigned faulted, link_rows;
    unsigned refused; /* NVMe BARs the window policy turned away */
    unsigned unsafe;  /* reads not made because the photograph wait could not be made */
    unsigned reserve; /* rows kept free for the comparison while a window is walked */
    int truncated;
    /* The photograph taken, and waited for, before the first read of each kind (announce). A
     * failed one latches: nothing is read after it. */
    int photo_ecam_done, photo_bar_done, photo_failed;
    int photo_code; /* the callback's TESTS_PHOTO_* of the failed one */
    ep_view ep;
} pcie_scan;

typedef struct {
    int valid;
    uint64_t lo, hi;
} window;

static uint64_t ecam_addr(const tests_ecam *e, unsigned bus, unsigned dev, unsigned fn,
                          unsigned off)
{
    return e->base + ((uint64_t)bus << 20) + ((uint64_t)dev << 15) + ((uint64_t)fn << 12) + off;
}

/* One configuration dword of function 0 of (bus, dev). Any failure, including a read refused
 * because the address left the window, makes the result UNKNOWN. */
static int cfg_rd(pcie_scan *s, const tests_ecam *e, unsigned bus, unsigned dev, unsigned off,
                  uint32_t *v)
{
    int rc = tests_rd32(s->c->bus, &s->allow, ecam_addr(e, bus, dev, 0, off), v);

    if (rc != RD_OK)
        s->faulted++;
    return rc;
}

static int row_room(const pcie_scan *s, unsigned need)
{
    return s->r->nrows + need + s->reserve + CLOSING_ROWS <= TESTS_DETAIL_MAX;
}

static int room_or_truncate(pcie_scan *s, unsigned need)
{
    if (row_room(s, need))
        return 1;
    s->truncated = 1;
    return 0;
}

static void put_bdf(probe_sb *sb, unsigned w, unsigned bus, unsigned dev)
{
    probe_sb_putc(sb, 'W');
    probe_sb_dec(sb, w);
    probe_sb_putc(sb, ' ');
    probe_sb_hex(sb, bus, 2);
    probe_sb_putc(sb, ':');
    probe_sb_hex(sb, dev, 2);
    probe_sb_puts(sb, ".0");
}

/* Before a read that may not answer. The first read of each kind (an ECAM window, an NVMe BAR0) is
 * preceded by the photograph: the hook draws the finished page of the other tests with this
 * address as its footer and waits TESTS_PHOTOGRAPH_WAIT_SECONDS, so that a photograph taken now
 * survives whatever the read does (OBSERVED, boot 3: the board reset itself within seconds of a
 * read that never returned, and the page was nearly lost). Later reads only name their address in
 * the footer. Returns 0 when the read may be made and -1 when it may not: the wait could not be
 * made, and a read that cannot be photographed first is not made at all. */
static int announce(pcie_scan *s, int *done, const char *what, uint64_t addr)
{
    probe_sb nsb;
    char note[40];
    tests_ctx *c = s->c;
    int first = !*done;
    int rc;

    if (s->photo_failed)
        return -1;
    *done = 1;
    probe_sb_init(&nsb, note, sizeof note);
    probe_sb_puts(&nsb, what);
    probe_sb_hex(&nsb, addr, 10);
    if (first && c->photograph != NULL) {
        rc = c->photograph(c->photograph_ctx, note, TESTS_PHOTOGRAPH_WAIT_SECONDS);
        if (rc != 0) {
            s->photo_failed = 1;
            s->photo_code = rc;
            return -1;
        }
        return 0;
    }
    if (c->progress != NULL)
        c->progress(c->progress_ctx, note);
    return 0;
}

/* Why nothing was read, when the wait could not be made: the callback's code, so that a stalled
 * counter and a refused call are told apart on the page. */
static void photo_failed_row(pcie_scan *s)
{
    probe_sb *sb;

    if (room_or_truncate(s, 1) && (sb = tests_row(s->r)) != NULL) {
        probe_sb_puts(sb, "PHOTOGRAPH WAIT FAILED: CODE=");
        probe_sb_dec(sb, (uint64_t)s->photo_code);
    }
}

typedef struct {
    int have_link;
    unsigned gen;
    unsigned width;
    int dllla;
} link_info;

/* Finds the PCI Express capability and reads the link status. Bounded by a hop
 * count and by the 0x40..0xFC range a capability pointer may take. */
static int read_link(pcie_scan *s, const tests_ecam *e, unsigned b, unsigned dev, link_info *out)
{
    uint32_t status_cmd, ptr_dw, dw;
    unsigned ptr;

    out->have_link = 0;
    if (cfg_rd(s, e, b, dev, 0x04, &status_cmd) != RD_OK)
        return RD_FAULT;
    if (!((status_cmd >> 16) & 0x10u))
        return RD_OK; /* no capability list */
    if (cfg_rd(s, e, b, dev, 0x34, &ptr_dw) != RD_OK)
        return RD_FAULT;
    ptr = ptr_dw & 0xFCu;
    for (unsigned hop = 0; hop < 16u && ptr >= 0x40u && ptr <= 0xFCu; hop++) {
        if (cfg_rd(s, e, b, dev, ptr, &dw) != RD_OK)
            return RD_FAULT;
        if ((dw & 0xFFu) == CAP_ID_PCIE) {
            uint32_t lnk;

            if (ptr + 0x10u >= CFG_SPACE_BYTES || cfg_rd(s, e, b, dev, ptr + 0x10u, &lnk) != RD_OK)
                return RD_FAULT;
            out->have_link = 1;
            out->gen = (lnk >> 16) & 0xFu;
            out->width = (lnk >> 20) & 0x3Fu;
            out->dllla = (int)((lnk >> 29) & 1u);
            return RD_OK;
        }
        ptr = (dw >> 8) & 0xFCu;
    }
    return RD_OK;
}

static window nonpref_window(uint32_t dw)
{
    window w;

    w.lo = (uint64_t)(dw & 0xFFF0u) << 16;
    w.hi = ((uint64_t)((dw >> 16) & 0xFFF0u) << 16) | 0xFFFFFu;
    w.valid = w.hi >= w.lo;
    return w;
}

static window pref_window(uint32_t dw, uint32_t base_hi, uint32_t limit_hi)
{
    window w;

    w.lo = ((uint64_t)(dw & 0xFFF0u) << 16) | ((uint64_t)base_hi << 32);
    w.hi = (((uint64_t)((dw >> 16) & 0xFFF0u) << 16) | 0xFFFFFu) | ((uint64_t)limit_hi << 32);
    w.valid = w.hi >= w.lo;
    return w;
}

static int in_window(const window *w, uint64_t base, uint64_t len)
{
    return w->valid && base >= w->lo && base + len - 1u <= w->hi && base + len > base;
}

/* The firmware's NVMe function in this segment at (bus, device 0, function 0), or NULL. */
static const tests_pciio_fn *fw_nvme_at(const pcie_scan *s, unsigned bus)
{
    for (unsigned i = 0; i < s->pre->n && i < TESTS_PCIIO_MAX; i++) {
        const tests_pciio_fn *f = &s->pre->f[i];

        if (f->is_nvme && f->located && f->segment == s->segment && f->bus == bus &&
            f->device == 0 && f->function == 0)
            return f;
    }
    return NULL;
}

/* The seven NVMe registers behind BAR0, or none. The BAR was justified by the endpoint, by the
 * bridge's window and by the firmware's own record of it. */
static void read_nvme(pcie_scan *s, uint64_t bar)
{
    ep_view *ep = &s->ep;
    uint64_t cap, asq, acq;
    uint32_t vs, cc, csts, aqa;
    int st = tests_allow_add_device(&s->allow, s->c->memmap, s->c->require_mmio_map, bar,
                                    NVME_WINDOW_BYTES, TESTS_ALIGN_NVME, "NVME BAR0");

    if (st != TESTS_WIN_OK) {
        s->refused++;
        s->reserve = 0; /* no register rows will follow */
        ep->regs = REGS_NOT_READ;
        tests_row_refused(s->r, "NVME BAR0", -1, st);
        return;
    }
    if (announce(s, &s->photo_bar_done, "T06 NVME ", bar) != 0) {
        s->unsafe++;
        s->reserve = 0;
        ep->regs = REGS_NOT_READ;
        photo_failed_row(s);
        return;
    }
    if (tests_rd64(s->c->bus, &s->allow, bar + TESTS_NVME_CAP, &cap) == RD_OK &&
        tests_rd32(s->c->bus, &s->allow, bar + TESTS_NVME_VS, &vs) == RD_OK &&
        tests_rd32(s->c->bus, &s->allow, bar + TESTS_NVME_CC, &cc) == RD_OK &&
        tests_rd32(s->c->bus, &s->allow, bar + TESTS_NVME_CSTS, &csts) == RD_OK &&
        tests_rd32(s->c->bus, &s->allow, bar + TESTS_NVME_AQA, &aqa) == RD_OK &&
        tests_rd64(s->c->bus, &s->allow, bar + TESTS_NVME_ASQ, &asq) == RD_OK &&
        tests_rd64(s->c->bus, &s->allow, bar + TESTS_NVME_ACQ, &acq) == RD_OK) {
        ep->regs = REGS_READ;
        ep->cap = cap;
        ep->vs = vs;
        ep->cc = cc;
        ep->csts = csts;
        ep->aqa = aqa;
        ep->asq = asq;
        ep->acq = acq;
    } else {
        s->faulted++;
        s->reserve = 0;
        ep->regs = REGS_FAULT;
        if (room_or_truncate(s, 1))
            tests_row_text(s->r, "NVME REGS: READ FAULTED");
    }
}

/* Reads the endpoint behind a bridge and, if it is the NVMe function the firmware recorded at that
 * place and the bridge justifies its BAR, the controller's registers. Only one endpoint of the
 * window is compared with the firmware's record: the first one at a bus the firmware knows an NVMe
 * function at, else the first one found. */
static void follow_bridge(pcie_scan *s, const tests_ecam *e, unsigned dev, uint32_t root_cmd)
{
    uint32_t buses, memw, prefw, pref_hi, lim_hi, ep_id, ep_cls, ep_cmd, bar0, bar1;
    unsigned sec;
    window nw, pw;
    uint64_t bar;
    int in_win, take;
    const tests_pciio_fn *fw;
    link_info lk;
    probe_sb *sb;

    if (read_link(s, e, 0, dev, &lk) == RD_OK && lk.have_link && s->link_rows < MAX_LINK_ROWS &&
        row_room(s, 1) && (sb = tests_row(s->r)) != NULL) {
        s->link_rows++;
        probe_sb_putc(sb, 'W');
        probe_sb_dec(sb, s->window);
        probe_sb_puts(sb, " LINK GEN");
        probe_sb_dec(sb, lk.gen);
        probe_sb_puts(sb, " X");
        probe_sb_dec(sb, lk.width);
        probe_sb_puts(sb, " DLLLA=");
        probe_sb_dec(sb, (uint64_t)lk.dllla);
    }
    if (cfg_rd(s, e, 0, dev, 0x18, &buses) != RD_OK || cfg_rd(s, e, 0, dev, 0x20, &memw) != RD_OK ||
        cfg_rd(s, e, 0, dev, 0x24, &prefw) != RD_OK ||
        cfg_rd(s, e, 0, dev, 0x28, &pref_hi) != RD_OK ||
        cfg_rd(s, e, 0, dev, 0x2C, &lim_hi) != RD_OK)
        return;
    sec = (buses >> 8) & 0xFFu;
    if (sec == 0 || sec > e->end_bus)
        return;
    if (cfg_rd(s, e, sec, 0, 0x00, &ep_id) != RD_OK)
        return;
    if (ep_id == 0xFFFFFFFFu || ep_id == 0u)
        return;
    if (cfg_rd(s, e, sec, 0, 0x08, &ep_cls) != RD_OK ||
        cfg_rd(s, e, sec, 0, 0x04, &ep_cmd) != RD_OK ||
        cfg_rd(s, e, sec, 0, 0x10, &bar0) != RD_OK || cfg_rd(s, e, sec, 0, 0x14, &bar1) != RD_OK)
        return;
    bar = tests_pciio_bar_base(bar0, bar1);
    fw = fw_nvme_at(s, sec);
    take = !s->ep.found || (fw != NULL && s->ep.fw == NULL);
    if (take) {
        probe_memset(&s->ep, 0, sizeof s->ep);
        s->ep.found = 1;
        s->ep.bus = sec;
        s->ep.id = ep_id;
        s->ep.cls = ep_cls;
        s->ep.cmd = ep_cmd;
        s->ep.bar0 = bar;
        s->ep.fw = fw;
        s->ep.match = fw != NULL && fw->cfg_ok && fw->id == ep_id &&
                      (fw->class_rev >> 8) == (ep_cls >> 8) && fw->bar0_base == bar;
    }
    if (room_or_truncate(s, 1) && (sb = tests_row(s->r)) != NULL) {
        put_bdf(sb, s->window, sec, 0);
        probe_sb_puts(sb, " V=");
        probe_sb_hex(sb, ep_id & 0xFFFFu, 4);
        probe_sb_puts(sb, " C=");
        probe_sb_hex(sb, ep_cls >> 8, 6);
    }
    if (!take || (ep_cls >> 8) != TESTS_PCIIO_CLASS_NVME)
        return;

    nw = nonpref_window(memw);
    pw = pref_window(prefw, pref_hi, lim_hi);
    in_win = in_window(&nw, bar, NVME_WINDOW_BYTES) || in_window(&pw, bar, NVME_WINDOW_BYTES);
    if (room_or_truncate(s, 1) && (sb = tests_row(s->r)) != NULL) {
        probe_sb_puts(sb, "EP CMD=");
        probe_sb_hex(sb, ep_cmd & 0xFFFFu, 4);
        probe_sb_puts(sb, " BAR0=");
        probe_sb_hex(sb, bar, 10);
        probe_sb_puts(sb, in_win ? " IN WIN" : " NOT IN WIN");
    }
    /* The registers are read only for the function the firmware recorded: with ids, class or BAR0
     * that differ from its record, the BAR is an address nobody else has vouched for. */
    if (!s->ep.match) {
        s->ep.regs = REGS_NOT_READ;
        s->reserve = 0;
        if (room_or_truncate(s, 1))
            tests_row_text(s->r, "NVME REGS NOT READ: VIEW DIFFERS");
        return;
    }
    /* Decode must be on at both ends: a disabled endpoint or bridge does not
     * answer memory reads, and a read would then be a stray access. */
    if (!(ep_cmd & 2u) || !(root_cmd & 2u) || bar == 0 || (bar & 0xFFFu) != 0 || !in_win) {
        s->ep.regs = REGS_NOT_READ;
        s->reserve = 0;
        if (room_or_truncate(s, 1))
            tests_row_text(s->r, "NVME REGS NOT READ: DECODE OFF");
        return;
    }
    read_nvme(s, bar);
}

static void scan_device(pcie_scan *s, const tests_ecam *e, unsigned dev, int *is_bridge)
{
    uint32_t id, cls, hdr, cmd;
    probe_sb *sb;
    int bridge;

    *is_bridge = 0;
    if (cfg_rd(s, e, 0, dev, 0x00, &id) != RD_OK)
        return;
    if (id == 0xFFFFFFFFu || id == 0u) {
        if (dev == 0 && room_or_truncate(s, 1) && (sb = tests_row(s->r)) != NULL) {
            put_bdf(sb, s->window, 0, 0);
            probe_sb_puts(sb, " NO DEVICE");
        }
        return;
    }
    if (cfg_rd(s, e, 0, dev, 0x08, &cls) != RD_OK || cfg_rd(s, e, 0, dev, 0x0C, &hdr) != RD_OK ||
        cfg_rd(s, e, 0, dev, 0x04, &cmd) != RD_OK)
        return;
    bridge = ((hdr >> 16) & 0x7Fu) == 1u;
    /* A device that is no bridge is not on the way to an endpoint, so its row is the first to go
     * when the result is full: it is shown only with the rows of the path still to come in hand,
     * and a device behind device 0 with one more to spare. */
    if (room_or_truncate(s, 1u + (bridge ? 0u : PATH_ROWS + (dev == 0 ? 0u : 1u))) &&
        (sb = tests_row(s->r)) != NULL) {
        put_bdf(sb, s->window, 0, dev);
        probe_sb_puts(sb, " V=");
        probe_sb_hex(sb, id & 0xFFFFu, 4);
        probe_sb_puts(sb, " D=");
        probe_sb_hex(sb, id >> 16, 4);
        probe_sb_puts(sb, " C=");
        probe_sb_hex(sb, cls >> 8, 6);
    }
    if (!bridge)
        return; /* not a bridge: no endpoint behind it to follow */
    *is_bridge = 1;
    follow_bridge(s, e, dev, cmd);
}

/* ---- The verdict ---- */

int tests_pcie_decide(const tests_pcie_tally *t, const char **why)
{
    *why = NULL;
    if (t->scanned == 0) {
        if (t->unsafe != 0) {
            *why = "PHOTOGRAPH WAIT FAILED";
            return TV_UNKNOWN;
        }
        if (t->refused != 0) {
            *why = "A WINDOW WAS REFUSED";
            return TV_UNKNOWN;
        }
        *why = "NO SEGMENT WAS JUSTIFIED";
        return TV_NOT_RUN;
    }
    if (t->faulted != 0) {
        *why = "A READ FAULTED";
        return TV_UNKNOWN;
    }
    if (t->no_endpoint != 0) {
        *why = "NO ENDPOINT AT EL2";
        return TV_FAIL;
    }
    if (t->view_mismatch != 0) {
        *why = "IDS OR BAR0 DIFFER FROM FW";
        return TV_FAIL;
    }
    if (t->const_mismatch != 0) {
        *why = "CAP OR VS DIFFER FROM FW";
        return TV_FAIL;
    }
    if (t->regs_incomplete != 0) {
        *why = "NVME REGISTERS NOT READ";
        return TV_UNKNOWN;
    }
    if (t->unsafe != 0) {
        *why = "PHOTOGRAPH WAIT FAILED";
        return TV_UNKNOWN;
    }
    if (t->refused != 0 || t->unverified != 0) {
        *why = "NOT EVERY FW NVME CHECKED";
        return TV_UNKNOWN;
    }
    return TV_PASS;
}

/* ---- Rows ---- */

static void put_pre_el2_32(tests_result *r, const char *name, int have_pre, uint32_t pre,
                           uint32_t el2)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, name);
    probe_sb_puts(sb, " PRE=");
    if (have_pre)
        probe_sb_hex(sb, pre, 8);
    else
        probe_sb_puts(sb, "N/A");
    probe_sb_puts(sb, " EL2=");
    probe_sb_hex(sb, el2, 8);
}

/* A 64-bit register does not fit one row next to its other value: two rows, PRE then EL2. */
static void put_pre_el2_64(tests_result *r, const char *name, int have_pre, uint64_t pre,
                           uint64_t el2)
{
    probe_sb *sb = tests_row(r);

    if (sb != NULL) {
        probe_sb_puts(sb, name);
        probe_sb_puts(sb, " PRE=");
        if (have_pre)
            probe_sb_hex(sb, pre, 16);
        else
            probe_sb_puts(sb, "N/A");
    }
    sb = tests_row(r);
    if (sb != NULL) {
        probe_sb_puts(sb, name);
        probe_sb_puts(sb, " EL2=");
        probe_sb_hex(sb, el2, 16);
    }
}

/* Whether CAP and VS were compared and equal: 1 equal, 0 different, -1 not comparable. */
static int const_registers_equal(const ep_view *ep)
{
    if (ep->regs != REGS_READ || ep->fw == NULL || ep->fw->regs_state != TESTS_PCIIO_REGS_OK)
        return -1;
    return ep->cap == ep->fw->cap && ep->vs == ep->fw->vs;
}

/* The comparison rows of the first target in full; later targets get one row each. */
static void emit_target_rows(pcie_scan *s, const target *t, int full)
{
    const ep_view *ep = &t->ep;
    int same = const_registers_equal(ep);
    probe_sb *sb;

    if (!full) {
        if (room_or_truncate(s, 1) && (sb = tests_row(s->r)) != NULL) {
            probe_sb_puts(sb, "SEG");
            probe_sb_dec(sb, t->segment);
            probe_sb_puts(sb, " VIEW=");
            probe_sb_puts(sb, ep->match ? "YES" : "NO");
            probe_sb_puts(sb, " CAPVS=");
            probe_sb_puts(sb, same < 0 ? "N/A" : same ? "OK" : "DIFF");
        }
        return;
    }
    if (room_or_truncate(s, 1) && (sb = tests_row(s->r)) != NULL) {
        probe_sb_puts(sb, "MATCHES FIRMWARE VIEW=");
        probe_sb_puts(sb, ep->match ? "YES" : "NO");
    }
    if (room_or_truncate(s, 1) && (sb = tests_row(s->r)) != NULL) {
        probe_sb_puts(sb, "CAP VS EQUAL=");
        probe_sb_puts(sb, same < 0 ? "N/A" : same ? "YES" : "NO");
    }
    if (ep->regs != REGS_READ || ep->fw == NULL || !room_or_truncate(s, REGISTER_ROWS))
        return;
    {
        const tests_pciio_fn *f = ep->fw;
        int pre = f->regs_state == TESTS_PCIIO_REGS_OK;

        put_pre_el2_64(s->r, "CAP", pre, f->cap, ep->cap);
        put_pre_el2_32(s->r, "VS", pre, f->vs, ep->vs);
        put_pre_el2_32(s->r, "CC", pre, f->cc, ep->cc);
        put_pre_el2_32(s->r, "CSTS", pre, f->csts, ep->csts);
        put_pre_el2_32(s->r, "AQA", pre, f->aqa, ep->aqa);
        put_pre_el2_64(s->r, "ASQ", pre, f->asq, ep->asq);
        put_pre_el2_64(s->r, "ACQ", pre, f->acq, ep->acq);
    }
}

static void row_function(tests_result *r, unsigned k, const tests_pciio_fn *f)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, "NVME");
    probe_sb_dec(sb, k);
    probe_sb_puts(sb, " SEG=");
    probe_sb_dec(sb, f->segment);
    probe_sb_puts(sb, " UID=");
    if (f->have_root)
        probe_sb_dec(sb, f->root_uid);
    else
        probe_sb_puts(sb, "NONE");
    probe_sb_putc(sb, ' ');
    probe_sb_hex(sb, f->bus, 2);
    probe_sb_putc(sb, ':');
    probe_sb_hex(sb, f->device, 2);
    probe_sb_putc(sb, '.');
    probe_sb_dec(sb, f->function);
}

/* Why the firmware's function may not justify a read of its segment, or SKIP_NONE. The checks are
 * in the order of how little they trust the record: the record itself, then the device path, then
 * the MCFG. *window receives the MCFG entry when there is no reason. */
static int justify(const tests_pciio_fn *f, const tests_mcfg *mcfg, unsigned *window)
{
    unsigned in_segment = 0, at = 0;

    if (!f->located || !f->cfg_ok)
        return SKIP_RECORD_INCOMPLETE;
    if (!f->have_root)
        return SKIP_NO_UID;
    if (!tests_pciio_hid_is_pci_root(f->root_hid))
        return SKIP_NOT_ROOT;
    if (f->root_uid != f->segment)
        return SKIP_UID_DIFFERS;
    if (mcfg->truncated != 0)
        return SKIP_MCFG_TRUNCATED;
    for (unsigned i = 0; i < mcfg->n; i++) {
        if (mcfg->e[i].segment != f->segment)
            continue;
        in_segment++;
        at = i;
    }
    if (in_segment == 0)
        return SKIP_NOT_IN_MCFG;
    if (in_segment > 1)
        return SKIP_TWICE_IN_MCFG;
    if (mcfg->e[at].start_bus != 0)
        return SKIP_START_BUS;
    *window = at;
    return SKIP_NONE;
}

static void not_run(tests_result *r, const char *first, const char *second)
{
    r->verdict = TV_NOT_RUN;
    tests_row_text(r, first);
    if (second != NULL)
        tests_row_text(r, second);
    tests_result_seal(r);
}

static void put_scanned(probe_sb *sb, unsigned segments, unsigned entries)
{
    probe_sb_puts(sb, "SCANNED SEGMENTS=");
    probe_sb_dec(sb, segments);
    probe_sb_puts(sb, " MCFG=");
    probe_sb_dec(sb, entries);
}

void tests_run_pcie(tests_ctx *c, tests_result *r)
{
    const tests_pciio *pre = c->pciio;
    tests_mcfg mcfg;
    pcie_scan s;
    target tg[TESTS_MAX_ECAM];
    tests_pcie_tally tally;
    const char *why;
    probe_sb *sb;
    unsigned ntg = 0, n_admitted = 0, unjustified = 0, fn_rows = 0, skip_rows = 0, ref_rows = 0;
    unsigned parse_refused = 0, nth = 0, scanned_row = ~0u;
    size_t len;
    unsigned faults_before = c->bus->faults(c->bus->ctx);

    tests_init_result(r, T_PCIE);
    r->label = TL_OBSERVED;
    probe_memset(&tally, 0, sizeof tally);

    /* No firmware NVMe, no read: not even the MCFG is looked at. */
    if (pre == NULL || pre->state != TESTS_PCIIO_OK || pre->n_nvme == 0) {
        probe_sb nb;
        char text[40];

        probe_sb_init(&nb, text, sizeof text);
        probe_sb_puts(&nb, "PCI I/O: ");
        if (pre != NULL && pre->state == TESTS_PCIIO_OK) {
            probe_sb_dec(&nb, pre->n);
            probe_sb_puts(&nb, " FUNCTIONS, NONE NVME");
        } else {
            probe_sb_puts(&nb, tests_pciio_state_name(pre != NULL ? pre->state
                                                                  : TESTS_PCIIO_NOT_COLLECTED));
        }
        not_run(r, "NO FIRMWARE NVME: NOTHING TOUCHED", text);
        return;
    }
    len = tests_acpi_load(c->scan, c->phys, c->phys_ctx, "MCFG", c->acpi_buf, TESTS_ACPI_BUF_BYTES);
    if (len == 0 || tests_mcfg_parse(c->acpi_buf, len, &mcfg) != 0 || mcfg.n == 0) {
        not_run(r, "NO USABLE MCFG: NOTHING TOUCHED", NULL);
        return;
    }
    probe_memset(&s, 0, sizeof s);
    s.c = c;
    s.r = r;
    s.pre = pre;
    tests_allow_init(&s.allow);

    /* Which segments the firmware's NVMe functions justify. */
    tests_row_dec(r, "FW NVME FUNCTIONS", pre->n_nvme);
    for (unsigned i = 0; i < pre->n && i < TESTS_PCIIO_MAX; i++) {
        const tests_pciio_fn *f = &pre->f[i];
        unsigned window = 0, k, reason, t;

        if (!f->is_nvme)
            continue;
        k = nth++;
        if (fn_rows < MAX_FUNCTION_ROWS) {
            fn_rows++;
            row_function(r, k, f);
        }
        reason = (unsigned)justify(f, &mcfg, &window);
        if (reason != SKIP_NONE) {
            unjustified++;
            if (skip_rows < MAX_SKIP_ROWS && (sb = tests_row(r)) != NULL) {
                skip_rows++;
                probe_sb_puts(sb, "NVME");
                probe_sb_dec(sb, k);
                probe_sb_puts(sb, " SKIP: ");
                probe_sb_puts(sb, skip_names[reason]);
            }
            continue;
        }
        for (t = 0; t < ntg; t++)
            if (tg[t].segment == f->segment)
                break;
        if (t < ntg) {
            tg[t].fw_count++;
            continue;
        }
        if (ntg >= TESTS_MAX_ECAM) {
            unjustified++;
            continue;
        }
        probe_memset(&tg[ntg], 0, sizeof tg[ntg]);
        tg[ntg].segment = f->segment;
        tg[ntg].window = window;
        tg[ntg].fw_count = 1;
        ntg++;
    }
    if (nth > fn_rows && (sb = tests_row(r)) != NULL) {
        probe_sb_dec(sb, nth - fn_rows);
        probe_sb_puts(sb, " MORE NVME FUNCTIONS");
    }

    /* Each target window is validated as every device window is (alignment, the memory-map
     * policy, the allow list) and only then put on the allow list: a read outside the list is
     * refused without being touched, so no other MCFG window can be read whatever the code below
     * does. */
    for (unsigned t = 0; t < ntg; t++) {
        const tests_ecam *e = &mcfg.e[tg[t].window];

        if (e->status != TESTS_WIN_OK) {
            tg[t].status = e->status;
            parse_refused++;
        } else {
            tg[t].status = tests_allow_add_device(&s.allow, c->memmap, c->require_mmio_map, e->base,
                                                  ((uint64_t)e->end_bus + 1u) << 20,
                                                  TESTS_ALIGN_ECAM, "MCFG ECAM");
        }
        if (tg[t].status == TESTS_WIN_OK) {
            n_admitted++;
            continue;
        }
        tally.refused++;
        if (ref_rows < MAX_REFUSAL_ROWS) {
            ref_rows++;
            tests_row_refused(r, "W", (int)tg[t].window, tg[t].status);
        }
    }
    /* The count is of the windows about to be read, so that a photograph taken before the first read
     * shows it; it is rewritten after the walk if a window was not read after all (the photograph
     * could not be taken). */
    if ((sb = tests_row(r)) != NULL) {
        scanned_row = r->nrows - 1u;
        put_scanned(sb, n_admitted, mcfg.n);
    }

    s.reserve = COMPARISON_ROWS + (n_admitted > 1u ? n_admitted - 1u : 0u); /* one row per later segment */
    for (unsigned t = 0; t < ntg; t++) {
        const tests_ecam *e = &mcfg.e[tg[t].window];
        int is_bridge;

        if (tg[t].status != TESTS_WIN_OK)
            continue;
        s.window = tg[t].window;
        s.segment = tg[t].segment;
        probe_memset(&s.ep, 0, sizeof s.ep);
        if (announce(&s, &s.photo_ecam_done, "T06 ECAM ", e->base) != 0) {
            tally.unsafe++;
            s.reserve = 0;
            photo_failed_row(&s);
            break;
        }
        tg[t].scanned = 1;

        scan_device(&s, e, 0, &is_bridge);
        if (!is_bridge)
            for (unsigned dev = 1; dev < MAX_ROOT_DEVICES; dev++) {
                int bridge;

                scan_device(&s, e, dev, &bridge);
            }
        tg[t].ep = s.ep;
    }
    s.reserve = 0;
    {
        unsigned n_scanned = 0;
        probe_sb fix;

        for (unsigned t = 0; t < ntg; t++)
            n_scanned += tg[t].scanned != 0;
        if (scanned_row != ~0u && n_scanned != n_admitted) {
            probe_sb_init(&fix, r->rows[scanned_row], sizeof r->rows[scanned_row]);
            put_scanned(&fix, n_scanned, mcfg.n);
        }
    }

    /* The comparison with the firmware's record, window by window. */
    {
        int first = 1;

        for (unsigned t = 0; t < ntg; t++) {
            const ep_view *ep = &tg[t].ep;

            if (tg[t].status != TESTS_WIN_OK || !tg[t].scanned)
                continue;
            tally.scanned++;
            if (!ep->found) {
                tally.no_endpoint++;
            } else if (!ep->match) {
                tally.view_mismatch++;
            } else if (ep->regs != REGS_FAULT) { /* a fault is counted with the others below */
                if (ep->regs != REGS_READ || ep->fw->regs_state != TESTS_PCIIO_REGS_OK)
                    tally.regs_incomplete++;
                else if (const_registers_equal(ep) == 0)
                    tally.const_mismatch++;
            }
            if (tg[t].fw_count > 1)
                tally.unverified += tg[t].fw_count - 1u;
            emit_target_rows(&s, &tg[t], first);
            first = 0;
        }
    }
    tally.unverified += unjustified;
    tally.faulted = s.faulted;
    tally.refused += s.refused;
    tally.unsafe += s.unsafe;
    if (s.truncated && (sb = tests_row(r)) != NULL)
        probe_sb_puts(sb, "MORE ROWS NOT SHOWN");
    tests_row_windows(r, &s.allow, parse_refused, c->require_mmio_map);
    tests_row_dec(r, "FAULTS", c->bus->faults(c->bus->ctx) - faults_before);
    r->verdict = (uint8_t)tests_pcie_decide(&tally, &why);
    if (why != NULL && r->verdict != TV_PASS && (sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "WHY: ");
        probe_sb_puts(sb, why);
    }
    tests_result_seal(r);
}
