/* SPDX-License-Identifier: MIT */
/* The fixture of the T06 host tests, shared by test_x_t06.c and the hook test that runs T06 inside
 * the real driver (test_x_hook.c): an MCFG shaped like the unit's, what one segment of it holds, and
 * the firmware's pre-ExitBootServices record of its NVMe function.
 *
 * The MCFG has eight entries, segments 0 to 7, three windows of 256 buses and five of two. The
 * segment numbers and bases are plain numbers; no table bytes of any machine are here. The register
 * images are synthetic fixtures, none a measurement. The functions are static inline because both
 * users build them into their own translation unit.
 */
#ifndef TESTS_XT06_H
#define TESTS_XT06_H

#include "xdev.h"

#include "pciio.h"

typedef struct {
    uint64_t base;
    unsigned seg, start, end;
} mcfg_row;

#define UNIT_ENTRIES 8u
static const mcfg_row UNIT_MCFG[UNIT_ENTRIES] = {
    {0x400000000ull, 0, 0, 255}, {0x500000000ull, 1, 0, 255}, {0x6000000000ull, 2, 0, 255},
    {0x740000000ull, 3, 0, 1},   {0x7C000000ull, 4, 0, 1},    {0x7E000000ull, 5, 0, 1},
    {0x70000000ull, 6, 0, 1},    {0x74000000ull, 7, 0, 1},
};

/* Where the fixture's NVMe functions live. The BARs lie in no MCFG window of the table. */
#define ECAM_SEG6 0x70000000ull
#define ECAM_SEG5 0x7E000000ull
#define ECAM_SEG2 0x6000000000ull
#define BAR_SEG6 0x78000000ull
#define BAR_SEG5 0x79000000ull
#define BAR_SEG2 0x7A000000ull
#define WAIT_S TESTS_PHOTOGRAPH_WAIT_SECONDS
#define NVME_WINDOW 0x1000ull

/* Dword index of each of the ten reads an NVMe controller gets (CAP twice, VS, CC, CSTS, AQA, ASQ
 * twice, ACQ twice), as a bit set over offset / 4. */
#define NVME_READ_SET                                                                              \
    ((1u << 0) | (1u << 1) | (1u << 2) | (1u << 5) | (1u << 7) | (1u << 9) | (1u << 10) |         \
     (1u << 11) | (1u << 12) | (1u << 13))

/* ---- What a segment holds ---- */

typedef struct {
    uint32_t bridge_cmd, ep_cmd, memw;
    uint32_t ep_id, ep_class, bar0_lo;
    uint32_t cap_lo, cap_hi, vs, cc, csts, aqa, asq_lo, asq_hi, acq_lo, acq_hi;
} segcfg;

/* A root port at 00:00.0 whose secondary bus 1 holds an NVMe endpoint with a 64-bit BAR0 inside
 * the port's memory window. */
static inline segcfg good_seg(uint64_t bar)
{
    segcfg c;
    uint32_t window = (uint32_t)(bar >> 16) & 0xFFF0u;

    memset(&c, 0, sizeof c);
    c.bridge_cmd = 0x0006;
    c.ep_cmd = 0x0006;
    c.memw = window | (window << 16);
    c.ep_id = 0x00101B36u;
    c.ep_class = 0x01080201u;
    c.bar0_lo = (uint32_t)bar | 0x4u;
    c.cap_lo = 0x0107FFFFu;
    c.cap_hi = 0x00400820u;
    c.vs = 0x00020000u;
    c.cc = 0x00460001u;
    c.csts = 0x00000001u;
    c.aqa = 0x001F001Fu;
    c.asq_lo = 0xF6A00000u;
    c.asq_hi = 0x00000001u;
    c.acq_lo = 0xF6A01000u;
    c.acq_hi = 0x00000001u;
    return c;
}

static inline void put_cfg(devmodel *d, uint64_t ecam, unsigned bus, unsigned dev, unsigned off,
                           uint32_t v)
{
    dm_set(d, ecam + ((uint64_t)bus << 20) + ((uint64_t)dev << 15) + off, v);
}

/* The configuration space and the controller registers the device model answers with, as the
 * unit's firmware would show them at EL2. */
static inline void seg_devices(devmodel *d, uint64_t ecam, uint64_t bar, const segcfg *c)
{
    put_cfg(d, ecam, 0, 0, 0x00, 0x01201234u);
    put_cfg(d, ecam, 0, 0, 0x04, (0x0010u << 16) | c->bridge_cmd);
    put_cfg(d, ecam, 0, 0, 0x08, 0x06040001u);
    put_cfg(d, ecam, 0, 0, 0x0C, 1u << 16);
    put_cfg(d, ecam, 0, 0, 0x18, 0x00010100u);
    put_cfg(d, ecam, 0, 0, 0x20, c->memw);
    put_cfg(d, ecam, 0, 0, 0x24, 0x0000FFF0u);
    put_cfg(d, ecam, 0, 0, 0x28, 0);
    put_cfg(d, ecam, 0, 0, 0x2C, 0);
    put_cfg(d, ecam, 0, 0, 0x34, 0x40);
    put_cfg(d, ecam, 0, 0, 0x40, 0x00005010u);
    put_cfg(d, ecam, 0, 0, 0x50, (0x2043u << 16) | 0x0040u); /* link status: gen3 x4, DLLLA */
    put_cfg(d, ecam, 1, 0, 0x00, c->ep_id);
    put_cfg(d, ecam, 1, 0, 0x04, c->ep_cmd);
    put_cfg(d, ecam, 1, 0, 0x08, c->ep_class);
    put_cfg(d, ecam, 1, 0, 0x10, c->bar0_lo);
    put_cfg(d, ecam, 1, 0, 0x14, 0);
    dm_set(d, bar + 0x00, c->cap_lo);
    dm_set(d, bar + 0x04, c->cap_hi);
    dm_set(d, bar + 0x08, c->vs);
    dm_set(d, bar + 0x14, c->cc);
    dm_set(d, bar + 0x1C, c->csts);
    dm_set(d, bar + 0x24, c->aqa);
    dm_set(d, bar + 0x28, c->asq_lo);
    dm_set(d, bar + 0x2C, c->asq_hi);
    dm_set(d, bar + 0x30, c->acq_lo);
    dm_set(d, bar + 0x34, c->acq_hi);
}

/* The firmware of test_x_pciio.c as boot services, for the hook tests: a root port and an NVMe
 * endpoint (segment 6, bus 1, BAR0 0x7A000000, the registers of good_seg) and an unrelated function
 * at segment 0, or without the NVMe. */
efi_boot_services *x_pciio_fake_bs(int with_nvme);

/* ---- The firmware's record (pciio.h) ---- */

static inline tests_pciio_fn *pre_add(tests_pciio *p)
{
    tests_pciio_fn *f = &p->f[p->n++];

    p->state = TESTS_PCIIO_OK;
    p->located = p->n;
    return f;
}

/* The root port as the firmware saw it: not an NVMe function, so it justifies nothing. */
static inline void pre_root_port(tests_pciio *p, unsigned seg, unsigned uid)
{
    tests_pciio_fn *f = pre_add(p);

    f->located = 1;
    f->segment = (uint16_t)seg;
    f->base_ok = 1;
    f->cfg_ok = 1;
    f->header_type = 1;
    f->bars_read = 2;
    f->id = 0x01201234u;
    f->class_rev = 0x06040001u;
    f->hdr = 1u << 16;
    f->buses = 0x00010100u;
    f->have_root = 1;
    f->root_hid = TESTS_PCIIO_HID_PCIE_ROOT;
    f->root_uid = uid;
}

/* The NVMe endpoint at bus 1, device 0, function 0 of a segment, with the registers the firmware
 * read before ExitBootServices. */
static inline tests_pciio_fn *pre_nvme(tests_pciio *p, unsigned seg, unsigned uid,
                                       const segcfg *c)
{
    tests_pciio_fn *f = pre_add(p);

    f->located = 1;
    f->segment = (uint16_t)seg;
    f->bus = 1;
    f->base_ok = 1;
    f->cfg_ok = 1;
    f->bars_read = 6;
    f->id = c->ep_id;
    f->cmd_status = (0x0010u << 16) | c->ep_cmd;
    f->class_rev = c->ep_class;
    f->bar[0] = c->bar0_lo;
    f->have_root = 1;
    f->root_hid = TESTS_PCIIO_HID_PCIE_ROOT;
    f->root_uid = uid;
    f->is_nvme = 1;
    f->regs_state = TESTS_PCIIO_REGS_OK;
    f->bar0_base = tests_pciio_bar_base(c->bar0_lo, 0);
    f->cap = ((uint64_t)c->cap_hi << 32) | c->cap_lo;
    f->vs = c->vs;
    f->cc = c->cc;
    f->csts = c->csts;
    f->aqa = c->aqa;
    f->asq = ((uint64_t)c->asq_hi << 32) | c->asq_lo;
    f->acq = ((uint64_t)c->acq_hi << 32) | c->acq_lo;
    p->n_nvme++;
    return f;
}

#endif /* TESTS_XT06_H */
