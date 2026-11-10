/* SPDX-License-Identifier: MIT */
/* T06: the PCIe link and the NVMe controller state, read-only.
 *
 * Why it is built the way it is. The only address source is the MCFG: each
 * allocation gives an ECAM window, and the test reads configuration space
 * inside it. Device 0 of bus 0 is read first. If it is a PCI-to-PCI bridge (a
 * root port, which is what a Designware-style controller presents) it is the
 * only device read on bus 0, because such a root bus decodes device 0 alone and
 * other device numbers alias it; if it is not a bridge, devices 1..7 of bus 0
 * are read as well (a generic ECAM host bridge such as the one of the QEMU
 * model). Behind each bridge only device 0 of the secondary bus is read.
 *
 * The NVMe BAR is never assumed: it is read from the endpoint's own BAR0, and
 * registers behind it are touched only if the endpoint's memory decode is on,
 * the bridge forwards memory, and the BAR lies inside a window the bridge
 * advertises. Only configuration reads and four NVMe register reads (CAP, VS,
 * CC, CSTS) are made; CC.EN and CSTS are READ, never written. An ECAM window
 * whose start bus is not 0 is not probed: whether its base names bus 0 or the
 * start bus differs between consumers, and a wrong guess is a stray read.
 *
 * Offsets: PCI Local Bus / PCIe base specifications as listed in Linux v6.12
 * include/uapi/linux/pci_regs.h (vendor 0x00, command 0x04, status 0x06, class
 * 0x08, header type 0x0e, BAR0 0x10, secondary bus 0x19, memory base/limit
 * 0x20/0x22, prefetchable 0x24/0x26/0x28/0x2c, capability pointer 0x34, PCIe
 * link status at capability + 0x12) and include/linux/nvme.h lines 131-136
 * (CAP 0, VS 8, CC 0x14, CSTS 0x1c).
 */
#include "suite.h"

#define CFG_SPACE_BYTES 0x1000u
#define NVME_WINDOW_BYTES 0x1000u
#define NVME_CLASS 0x010802u
#define CAP_ID_PCIE 0x10u
#define MAX_ROOT_DEVICES 8u
#define MAX_LINK_ROWS 3u
#define MAX_NVME_FULL 2u
/* Rows an NVMe endpoint may still need (four) plus the closing rows (the window
 * summary, the fault row and a possible "more not shown" row): no device row
 * starts without this much room. */
#define ROW_RESERVE 7u

typedef struct {
    tests_ctx *c;
    tests_result *r;
    tests_allow allow;
    unsigned window; /* index of the MCFG entry being scanned */
    unsigned probed, faulted, nvme_found, nvme_read, link_rows;
    unsigned refused; /* ECAM windows and NVMe BARs the window policy turned away */
    int truncated;
} pcie_scan;

typedef struct {
    int have_link;
    unsigned gen;
    unsigned width;
    int dllla;
} link_info;

typedef struct {
    int valid;
    uint64_t lo, hi;
} window;

static uint64_t ecam_addr(const tests_ecam *e, unsigned bus, unsigned dev, unsigned fn,
                          unsigned off)
{
    return e->base + ((uint64_t)bus << 20) + ((uint64_t)dev << 15) + ((uint64_t)fn << 12) + off;
}

/* One configuration dword of function 0 of (bus, dev). Any failure, including a
 * read refused because the address left the window, makes the result UNKNOWN. */
static int cfg_rd(pcie_scan *s, const tests_ecam *e, unsigned bus, unsigned dev, unsigned off,
                  uint32_t *v)
{
    int rc = tests_rd32(s->c->bus, &s->allow, ecam_addr(e, bus, dev, 0, off), v);

    if (rc != RD_OK)
        s->faulted++;
    return rc;
}

static int row_room(pcie_scan *s)
{
    if (s->r->nrows + ROW_RESERVE <= TESTS_DETAIL_MAX)
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

static void read_nvme(pcie_scan *s, uint64_t bar)
{
    probe_sb nsb;
    char note[40];
    uint64_t cap;
    uint32_t vs, cc, csts;
    probe_sb *sb;
    int st = tests_allow_add_device(&s->allow, s->c->memmap, s->c->require_mmio_map, bar,
                                    NVME_WINDOW_BYTES, TESTS_ALIGN_NVME, "NVME BAR0");

    if (st != TESTS_WIN_OK) {
        s->refused++;
        tests_row_refused(s->r, "NVME BAR0", -1, st);
        return;
    }
    probe_sb_init(&nsb, note, sizeof note);
    probe_sb_puts(&nsb, "T06 NVME ");
    probe_sb_hex(&nsb, bar, 10);
    if (s->c->progress != NULL)
        s->c->progress(s->c->progress_ctx, note);
    if (tests_rd64(s->c->bus, &s->allow, bar + 0x00u, &cap) == RD_OK &&
        tests_rd32(s->c->bus, &s->allow, bar + 0x08u, &vs) == RD_OK &&
        tests_rd32(s->c->bus, &s->allow, bar + 0x14u, &cc) == RD_OK &&
        tests_rd32(s->c->bus, &s->allow, bar + 0x1Cu, &csts) == RD_OK) {
        s->nvme_read++;
        tests_row_hex(s->r, "NVME CAP", cap, 16);
        if ((sb = tests_row(s->r)) != NULL) {
            probe_sb_puts(sb, "NVME VS=");
            probe_sb_hex(sb, vs, 8);
            probe_sb_puts(sb, " CC=");
            probe_sb_hex(sb, cc, 8);
            probe_sb_puts(sb, " EN=");
            probe_sb_dec(sb, cc & 1u);
        }
        if ((sb = tests_row(s->r)) != NULL) {
            probe_sb_puts(sb, "NVME CSTS=");
            probe_sb_hex(sb, csts, 8);
            probe_sb_puts(sb, " RDY=");
            probe_sb_dec(sb, csts & 1u);
            probe_sb_puts(sb, " CFS=");
            probe_sb_dec(sb, (csts >> 1) & 1u);
        }
    } else {
        s->faulted++;
        tests_row_text(s->r, "NVME REGS: READ FAULTED");
    }
}

/* Reads the endpoint behind a bridge and, if it is an NVMe controller whose
 * address the bridge justifies, its four registers. */
static void follow_bridge(pcie_scan *s, const tests_ecam *e, unsigned dev, uint32_t root_cmd)
{
    uint32_t buses, memw, prefw, pref_hi, lim_hi, ep_id, ep_cls, ep_cmd, bar0, bar1;
    unsigned sec;
    window nw, pw;
    uint64_t bar;
    int in_win;
    link_info lk;
    probe_sb *sb;

    if (read_link(s, e, 0, dev, &lk) == RD_OK && lk.have_link && s->link_rows < MAX_LINK_ROWS &&
        row_room(s) && (sb = tests_row(s->r)) != NULL) {
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
    if (!row_room(s))
        return;
    if ((sb = tests_row(s->r)) != NULL) {
        put_bdf(sb, s->window, sec, 0);
        probe_sb_puts(sb, " V=");
        probe_sb_hex(sb, ep_id & 0xFFFFu, 4);
        probe_sb_puts(sb, " C=");
        probe_sb_hex(sb, ep_cls >> 8, 6);
    }
    if ((ep_cls >> 8) != NVME_CLASS)
        return;
    s->nvme_found++;
    if (s->nvme_found > MAX_NVME_FULL) {
        s->truncated = 1;
        return;
    }

    bar = (bar0 & 1u) ? 0 : (uint64_t)(bar0 & ~0xFu);
    if (((bar0 >> 1) & 3u) == 2u)
        bar |= (uint64_t)bar1 << 32;
    nw = nonpref_window(memw);
    pw = pref_window(prefw, pref_hi, lim_hi);
    in_win = in_window(&nw, bar, NVME_WINDOW_BYTES) || in_window(&pw, bar, NVME_WINDOW_BYTES);
    if ((sb = tests_row(s->r)) != NULL) {
        probe_sb_puts(sb, "EP CMD=");
        probe_sb_hex(sb, ep_cmd & 0xFFFFu, 4);
        probe_sb_puts(sb, " BAR0=");
        probe_sb_hex(sb, bar, 10);
        probe_sb_puts(sb, in_win ? " IN WIN" : " NOT IN WIN");
    }
    /* Decode must be on at both ends: a disabled endpoint or bridge does not
     * answer memory reads, and a read would then be a stray access. */
    if (!(ep_cmd & 2u) || !(root_cmd & 2u) || bar == 0 || (bar & 0xFFFu) != 0 || !in_win) {
        tests_row_text(s->r, "NVME REGS NOT READ: DECODE OFF");
        return;
    }
    read_nvme(s, bar);
}

static void scan_device(pcie_scan *s, const tests_ecam *e, unsigned dev, int *is_bridge)
{
    uint32_t id, cls, hdr, cmd;
    probe_sb *sb;

    *is_bridge = 0;
    if (cfg_rd(s, e, 0, dev, 0x00, &id) != RD_OK)
        return;
    s->probed++;
    if (id == 0xFFFFFFFFu || id == 0u) {
        if (dev == 0 && row_room(s) && (sb = tests_row(s->r)) != NULL) {
            put_bdf(sb, s->window, 0, 0);
            probe_sb_puts(sb, " NO DEVICE");
        }
        return;
    }
    if (cfg_rd(s, e, 0, dev, 0x08, &cls) != RD_OK || cfg_rd(s, e, 0, dev, 0x0C, &hdr) != RD_OK ||
        cfg_rd(s, e, 0, dev, 0x04, &cmd) != RD_OK)
        return;
    if (row_room(s) && (sb = tests_row(s->r)) != NULL) {
        put_bdf(sb, s->window, 0, dev);
        probe_sb_puts(sb, " V=");
        probe_sb_hex(sb, id & 0xFFFFu, 4);
        probe_sb_puts(sb, " D=");
        probe_sb_hex(sb, id >> 16, 4);
        probe_sb_puts(sb, " C=");
        probe_sb_hex(sb, cls >> 8, 6);
    }
    if (((hdr >> 16) & 0x7Fu) != 1u)
        return; /* not a bridge: no endpoint behind it to follow */
    *is_bridge = 1;
    follow_bridge(s, e, dev, cmd);
}

void tests_run_pcie(tests_ctx *c, tests_result *r)
{
    tests_mcfg mcfg;
    pcie_scan s;
    size_t len;
    unsigned scanned = 0, parse_refused = 0;
    int win_st[TESTS_MAX_ECAM] = {0};
    unsigned faults_before = c->bus->faults(c->bus->ctx);

    tests_init_result(r, T_PCIE);
    r->label = TL_OBSERVED;
    len = tests_acpi_load(c->scan, c->phys, c->phys_ctx, "MCFG", c->acpi_buf, TESTS_ACPI_BUF_BYTES);
    if (len == 0 || tests_mcfg_parse(c->acpi_buf, len, &mcfg) != 0 || mcfg.n == 0) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, "NO USABLE MCFG: NOTHING TOUCHED");
        tests_result_seal(r);
        return;
    }
    probe_memset(&s, 0, sizeof s);
    s.c = c;
    s.r = r;
    tests_allow_init(&s.allow);
    for (unsigned i = 0; i < mcfg.n; i++) {
        const tests_ecam *e = &mcfg.e[i];

        if (e->start_bus != 0)
            continue;
        win_st[i] = e->status;
        if (win_st[i] != TESTS_WIN_OK)
            parse_refused++;
        else
            win_st[i] = tests_allow_add_device(&s.allow, c->memmap, c->require_mmio_map, e->base,
                                               ((uint64_t)e->end_bus + 1u) << 20, TESTS_ALIGN_ECAM,
                                               "MCFG ECAM");
    }
    tests_row_dec(r, "MCFG WINDOWS", mcfg.n);

    for (unsigned i = 0; i < mcfg.n; i++) {
        const tests_ecam *e = &mcfg.e[i];
        probe_sb nsb;
        char note[40];
        int is_bridge;

        s.window = i;
        if (e->start_bus != 0) {
            probe_sb *sb;

            if (row_room(&s) && (sb = tests_row(r)) != NULL) {
                probe_sb_putc(sb, 'W');
                probe_sb_dec(sb, i);
                probe_sb_puts(sb, " START BUS NOT 0: SKIPPED");
            }
            continue;
        }
        if (win_st[i] != TESTS_WIN_OK) {
            s.refused++;
            if (row_room(&s))
                tests_row_refused(r, "W", (int)i, win_st[i]);
            continue;
        }
        scanned++;
        probe_sb_init(&nsb, note, sizeof note);
        probe_sb_puts(&nsb, "T06 ECAM ");
        probe_sb_hex(&nsb, e->base, 10);
        if (c->progress != NULL)
            c->progress(c->progress_ctx, note);

        scan_device(&s, e, 0, &is_bridge);
        if (!is_bridge)
            for (unsigned dev = 1; dev < MAX_ROOT_DEVICES; dev++) {
                int bridge;

                scan_device(&s, e, dev, &bridge);
            }
    }
    if (s.truncated)
        tests_row_text(r, "MORE DEVICES NOT SHOWN");
    if (s.nvme_found == 0 && s.faulted == 0 && s.refused == 0)
        tests_row_text(r, "NO NVME ENDPOINT FOUND");
    tests_row_windows(r, &s.allow, parse_refused, c->require_mmio_map);
    tests_row_dec(r, "FAULTS", c->bus->faults(c->bus->ctx) - faults_before);
    tests_result_seal(r);

    if (scanned == 0 || s.probed == 0 || s.faulted != 0 || s.refused != 0 ||
        (s.nvme_found != 0 && s.nvme_read == 0))
        r->verdict = TV_UNKNOWN;
    else
        r->verdict = TV_INFO;
}
