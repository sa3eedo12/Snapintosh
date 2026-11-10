/* SPDX-License-Identifier: MIT */
/* Fixtures shared by the device-test files (test_x_dev.c and test_x_windows.c):
 * ACPI table builders, a model of the guarded bus that records every read,
 * firmware memory holding the tables, and a "world" that ties them to a test
 * context together with a UEFI memory map. The functions are defined in
 * test_x_dev.c. */
#ifndef TESTS_XDEV_H
#define TESTS_XDEV_H

#include "xtests.h"

#include "acpix.h"
#include "report_x.h"
#include "suite.h"

/* Device addresses of the fixtures, and RAM that no device window overlaps. */
#define GICD 0x10000000ull
#define GICR 0x11000000ull
#define ITS 0x10080000ull
#define ECAM 0x60000000ull
#define NVME_BAR 0x70000000ull
#define SMMU0 0x15000000ull
#define SMMU1 0x15100000ull
#define W_RAM_BASE 0x200000000ull
#define W_RAM_PAGES 0x100000ull

/* ---- Table builders ---- */

typedef struct {
    uint8_t b[4096];
    size_t len;
} tbl;

void t_start(tbl *t, const char *sig, size_t extra);
/* Length and checksum, as a firmware table carries them. */
void t_seal(tbl *t);
void madt_gicc(tbl *t, uint32_t flags, uint64_t gicr, uint64_t mpidr);
void madt_gicd(tbl *t, uint64_t base, unsigned version);
void madt_gicr(tbl *t, uint64_t base, uint32_t len);
void madt_its(tbl *t, uint64_t base);
void mcfg_entry(tbl *t, uint64_t base, unsigned seg, unsigned start, unsigned end);
void iort_node(tbl *t, unsigned type, size_t len, uint64_t base);
tbl good_madt(void);

/* ---- The device model ---- */

#define MAX_REGS 512u
#define MAX_LOG 4096u

typedef struct {
    struct {
        uint64_t addr;
        uint32_t val;
    } reg[MAX_REGS];
    unsigned nreg;
    uint64_t log[MAX_LOG];
    unsigned nlog;
    uint64_t fault_at[8];
    unsigned nfault_at;
    int fault_all;
    unsigned faults;
    unsigned unlisted; /* reads of an address the model was not told about */
    /* system registers */
    uint64_t sys[TR_COUNT];
    uint8_t sys_fault[TR_COUNT];
    uint64_t pct;
    int counter_stuck;
    uint64_t vct_offset;
} devmodel;

void dm_set(devmodel *d, uint64_t addr, uint32_t val);

/* Reads that fall outside every [base, base+len) window given. */
typedef struct {
    uint64_t base, len;
} win;

unsigned stray_reads(const devmodel *d, const win *w, unsigned n);
int read_at(const devmodel *d, uint64_t addr);

/* Firmware memory holding the ACPI tables. */
typedef struct {
    struct {
        uint64_t addr;
        const uint8_t *data;
        size_t len;
    } t[8];
    unsigned n;
} fwmem;

typedef struct {
    devmodel dm;
    fwmem fw;
    probe_acpi_scan scan;
    probe_phys_ops phys;
    tests_bus bus;
    tests_desc desc;
    tests_ctx ctx;
    uint8_t *acpi_buf;
    tbl tables[3];
    uint8_t mmbuf[48 * 16];
    unsigned nmm;
    probe_memmap mm;
} world;

/* One EFI memory descriptor with a 48-byte stride, as firmware lays them out. */
unsigned md_add(uint8_t *map, unsigned n, uint32_t type, uint64_t phys, uint64_t pages);
/* The default world's memory map describes RAM only: device registers are not
 * listed, as on the QEMU machine, so a window is "unlisted" unless a test adds a
 * descriptor with w_map (which re-validates the map). */
void w_init(world *w);
void w_free(world *w);
void w_map(world *w, uint32_t type, uint64_t phys, uint64_t pages);
/* Publishes table k of the world under its signature at a fake address. */
void w_table(world *w, unsigned k, const char *sig, const tbl *t);

/* ---- Per-test fixtures ---- */

void gic_good_regs(devmodel *d, unsigned arch, int last_on_second);
void gic_setup(world *w, tbl *madt);

typedef struct {
    int bridge_type;     /* header type of 00:00.0 */
    uint32_t bridge_cmd; /* command register of the bridge */
    uint32_t ep_cmd;
    uint32_t memw;       /* bridge memory base/limit dword */
    uint32_t bar0;
    uint32_t cc, csts;
} pcie_cfg;

uint64_t ecam_at(unsigned bus, unsigned dev, unsigned off);
pcie_cfg good_pcie(void);
void pcie_world(world *w, const pcie_cfg *p, unsigned start_bus);
void smmu_regs(devmodel *d, uint64_t base, uint32_t cr0, uint32_t ack, uint32_t gbpa);
int has_row(const tests_result *r, const char *needle);
void iort_world(world *w, unsigned n);

#endif /* TESTS_XDEV_H */
