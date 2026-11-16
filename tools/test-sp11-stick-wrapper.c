/* SPDX-License-Identifier: MIT */
/* Host harness for the SP11 stick wrapper (SNAPMON.BIN) and reporter (SNAPREP.BIN), driven by tools/test-sp11-stick-wrapper.py and
 * tools/test-sp11-stick-wrapper-refusals.py and built
 * with -fsanitize=address,undefined together with the REAL wrapper and reporter sources (probes/sp11-stick-*.c, compiled with
 * -DSP11_STICK_HOST_TEST, the entry points renamed snapmon_entry and snaprep_entry) and q1n1's sha256.c and text layer.
 *
 * What is fake is the machine and the runner; the code under test is what the unit runs. The fake machine is a set of regions of
 * physical memory (files, the memory map, the stack, the placement, the preload table), each with two images, the CPU's view and DRAM,
 * and a dirty bit per 64-byte line: a store changes the CPU view and dirties the line; DC CVAC writes a dirty line back; DC CIVAC writes
 * it back and drops it, so the CPU view of that line becomes DRAM's. A read through sp11hw_view sees the CPU view. An access outside
 * every region, a store to a region that is not writable, or a cache operation outside one is a bus fault: the scenario ends and says so.
 * Faults can be seeded: a lost clean (the line is dropped with no write-back), a bit flipped in DRAM after the write-back, a probe
 * answer, a guarded read that fails, or a patch of fake memory made when a given line is printed (a table changed between two stages).
 *
 * A scenario is text, one command per line, written by the Python test:
 *   BEGIN name | TABLE base [count] | FILE slot (path|-) base [size] | PATHHEX slot hex | SELF slot | MAP base stride path [size] |
 *   STACK base size | FB base bytes w h stride fmt | PLACEMENT base size | REG name value | ARGS magic version size | OPS puts hex load32 |
 *   LOADFAIL addr rc | AT addr len kind | FLIP addr mask | LOSE addr | TAMPER needle addr hexbytes | TICK start step | PREFILL addr size
 *   byte | DIGEST addr size | TABLEMAGIC value | RUN snapmon|snaprep | END
 * Every number is hex with 0x or decimal. Output, one record per line: RESULT, LINE, EV, REPORT, DIRTY, DIGEST, COUNTS, FAULT, END.
 * Hardware: NOT RUN; nothing here touches a device or the network. */
#define _DARWIN_C_SOURCE
#include <ctype.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef __APPLE__
#include <pthread.h>
#endif

#include "../probes/sp11-stick-hw.h"
#include "../probes/sp11-stick-bundle.h"
#include "sp11-payload.h"
#include "sha256.h"

uint64_t snapmon_entry(const struct q1n1_payload_args *a);
uint64_t snaprep_entry(const struct q1n1_payload_args *a);

#define LINE 64u
#define MAX_REGIONS 24
#define MAX_RULES 64
#define MAX_SLOTS 16
#define PAGE 4096u

struct region {
    uint64_t base, size;
    uint8_t *cpu, *dram, *dirty;
    int writable;
    char name[16];
};
enum { EV_ST, EV_CVAC, EV_CIVAC, EV_DSB, EV_ISB, EV_VIEW, EV_AT, EV_LOAD32, EV_MRS };
struct event {
    int type;
    uint64_t a, b;
    uint32_t stage, extra;
};
struct rule {
    uint64_t addr, len, aux;
    int kind;
};
struct tamper {
    char needle[48];
    uint64_t addr;
    uint8_t bytes[32];
    unsigned n;
    int done;
};

static struct {
    struct region reg[MAX_REGIONS];
    int nreg;
    uint64_t table_base, map_base, map_stride, map_size, stack_base, stack_size, place_base, place_size;
    uint64_t fb_base, fb_bytes;
    uint32_t fb_w, fb_h, fb_stride, fb_fmt;
    uint64_t file_base[MAX_SLOTS], file_size[MAX_SLOTS];
    char file_path[MAX_SLOTS][256];
    int file_present[MAX_SLOTS];
    uint32_t table_count;
    uint64_t table_magic;
    int self;
    uint64_t reg_value[8];
    uint64_t args_magic;
    uint32_t args_version, args_size;
    int ops_puts, ops_hex, ops_load32;
    struct rule load_fail[MAX_RULES], at_rule[MAX_RULES], digest[MAX_RULES];
    int nload_fail, nat, ndigest;
    int flip_set, lose_set, flip_done, lose_done;
    uint64_t flip_addr, flip_mask, lose_addr;
    struct tamper tamper[8];
    int ntamper;
    uint64_t tick, tick_step;
    struct event *ev;
    size_t nev, cap_ev;
    unsigned long n_store, n_cvac, n_civac, n_dsb, n_isb, n_at_w, n_at_r, n_load, n_ticks, n_puts, n_dropped;
    int faulted;
    char fault_text[160];
    jmp_buf jb;
    char lines[256][160];
    int line_clean[256];
    unsigned nlines;
    uint32_t loads;
    char name[64];
} W;

enum { REG_CURRENTEL, REG_MIDR, REG_MPIDR, REG_CNTFRQ, REG_HCR, REG_SCTLR, REG_TCR, REG_CTR };
static const char *const reg_names[8] = {"currentel", "midr", "mpidr", "cntfrq", "hcr", "sctlr", "tcr", "ctr"};
enum { AT_OK, AT_UNMAPPED, AT_RO, AT_DEVICE, AT_NC, AT_WT, AT_NONIDENT, AT_ABORT };
static const char *const at_names[] = {"ok", "unmapped", "ro", "device", "nc", "wt", "nonident", "abort"};

static void fail(const char *what)
{
    fprintf(stderr, "harness: %s\n", what);
    exit(2);
}

/* ---- the fake machine ---- */
static struct region *find_region(uint64_t pa, uint64_t size)
{
    int i;
    for (i = 0; i < W.nreg; i++) {
        struct region *r = &W.reg[i];
        if (size <= r->size && pa >= r->base && pa - r->base <= r->size - size) return r;
    }
    return NULL;
}

static void bus_fault(const char *what, uint64_t pa, uint64_t size)
{
    snprintf(W.fault_text, sizeof W.fault_text, "%s %llx %llx", what, (unsigned long long)pa, (unsigned long long)size);
    W.faulted = 1;
    longjmp(W.jb, 1);
}

static struct region *add_region(const char *name, uint64_t base, uint64_t size, int writable)
{
    struct region *r;
    int i;
    if (W.nreg >= MAX_REGIONS) fail("too many regions");
    size = (size + PAGE - 1) & ~(uint64_t)(PAGE - 1);
    for (i = 0; i < W.nreg; i++)
        if (base < W.reg[i].base + W.reg[i].size && W.reg[i].base < base + size) fail("regions overlap");
    r = &W.reg[W.nreg++];
    memset(r, 0, sizeof *r);
    snprintf(r->name, sizeof r->name, "%s", name);
    r->base = base;
    r->size = size;
    r->writable = writable;
    r->cpu = calloc(1, size);
    r->dram = calloc(1, size);
    r->dirty = calloc(1, size / LINE + 1);
    if (!r->cpu || !r->dram || !r->dirty) fail("out of memory for a region");
    return r;
}

static struct region *region_named(const char *name)
{
    int i;
    for (i = 0; i < W.nreg; i++)
        if (!strcmp(W.reg[i].name, name)) return &W.reg[i];
    return NULL;
}

static void clear_logs(void)
{
    free(W.ev);
    W.ev = NULL;
    W.nev = W.cap_ev = 0;
    W.n_store = W.n_cvac = W.n_civac = W.n_dsb = W.n_isb = W.n_at_w = W.n_at_r = W.n_load = W.n_ticks = W.n_puts = W.n_dropped = 0;
    W.nlines = 0;
    W.loads = 0;
    W.faulted = 0;
    W.fault_text[0] = 0;
}

static void free_world(void)
{
    int i;
    for (i = 0; i < W.nreg; i++) {
        free(W.reg[i].cpu);
        free(W.reg[i].dram);
        free(W.reg[i].dirty);
    }
    clear_logs();
    memset(&W, 0, sizeof W);
}

/* The stage the wrapper has recorded in REPORT.DAT so far: what tags a store with the point of the run it belongs to. */
static uint32_t report_stage(void)
{
    struct region *r = region_named("report");
    uint32_t stage;
    if (!r || memcmp(r->cpu, SP11SB_REPORT_MAGIC, 8)) return 0;
    memcpy(&stage, r->cpu + 0x10, 4);
    return stage;
}

static void event(int type, uint64_t a, uint64_t b, uint32_t extra)
{
    struct event *last = W.nev ? &W.ev[W.nev - 1] : NULL;
    uint32_t stage = report_stage();
    if (last && type == EV_ST && last->type == EV_ST && last->stage == stage && last->a + last->b == a) {
        last->b += b;
        return;
    }
    if (last && (type == EV_CVAC || type == EV_CIVAC) && last->type == type && (a == last->b || a == last->b + LINE)) {
        last->b = a;
        last->extra++;
        return;
    }
    if (W.nev == W.cap_ev) {
        W.cap_ev = W.cap_ev ? W.cap_ev * 2 : 4096;
        W.ev = realloc(W.ev, W.cap_ev * sizeof *W.ev);
        if (!W.ev) fail("out of memory for the event log");
    }
    W.ev[W.nev++] = (struct event){type, a, b, stage, extra};
}

#define REGISTER(fn, which) \
    uint64_t fn(void) { event(EV_MRS, which, W.reg_value[which], 0); return W.reg_value[which]; }
REGISTER(sp11hw_read_currentel, REG_CURRENTEL)
REGISTER(sp11hw_read_midr, REG_MIDR)
REGISTER(sp11hw_read_mpidr, REG_MPIDR)
REGISTER(sp11hw_read_cntfrq, REG_CNTFRQ)
REGISTER(sp11hw_read_hcr_el2, REG_HCR)
REGISTER(sp11hw_read_sctlr_el2, REG_SCTLR)
REGISTER(sp11hw_read_tcr_el2, REG_TCR)
REGISTER(sp11hw_read_ctr_el0, REG_CTR)

uint64_t sp11hw_ticks(void)
{
    uint64_t now = W.tick;
    W.tick += W.tick_step;
    W.n_ticks++;
    return now;
}

/* The machine's own line is 64 bytes whatever CTR_EL0 says; the wrapper's stride comes from CTR_EL0 and must cover every line. */
static void cache_op(uint64_t va, int invalidate)
{
    uint64_t line = va & ~(uint64_t)(LINE - 1);
    struct region *r = find_region(line, LINE);
    uint64_t off, idx;
    if (!r) bus_fault("cache op outside every region", va, 1);
    off = line - r->base;
    idx = off / LINE;
    if (invalidate) W.n_civac++;
    else W.n_cvac++;
    event(invalidate ? EV_CIVAC : EV_CVAC, line, line, 1);
    if (W.lose_set && !W.lose_done && line == (W.lose_addr & ~(uint64_t)(LINE - 1)) && r->dirty[idx]) {
        memcpy(r->cpu + off, r->dram + off, LINE);      /* the line goes away with no write-back */
        r->dirty[idx] = 0;
        W.lose_done = 1;
        return;
    }
    if (r->dirty[idx]) {
        memcpy(r->dram + off, r->cpu + off, LINE);
        r->dirty[idx] = 0;
    }
    if (invalidate) {
        if (W.flip_set && !W.flip_done && line == (W.flip_addr & ~(uint64_t)(LINE - 1))) {
            r->dram[W.flip_addr - r->base] ^= (uint8_t)W.flip_mask;
            W.flip_done = 1;
        }
        memcpy(r->cpu + off, r->dram + off, LINE);
    }
}
void sp11hw_dc_cvac(uint64_t va) { cache_op(va, 0); }
void sp11hw_dc_civac(uint64_t va) { cache_op(va, 1); }
void sp11hw_dsb_sy(void) { W.n_dsb++; event(EV_DSB, 0, 0, 0); }
void sp11hw_isb(void) { W.n_isb++; event(EV_ISB, 0, 0, 0); }

static uint64_t par_for(uint64_t va, int write)
{
    uint64_t page = va & ~(uint64_t)(PAGE - 1);
    int i, kind = find_region(page, PAGE) ? AT_OK : AT_UNMAPPED;
    uint64_t attr = 0xFF, out = page;
    for (i = 0; i < W.nat; i++)
        if (va >= W.at_rule[i].addr && va - W.at_rule[i].addr < W.at_rule[i].len) {
            kind = W.at_rule[i].kind;
            break;
        }
    switch (kind) {
    case AT_UNMAPPED: return 1 | 5 << 1;                    /* translation fault, level 1 */
    case AT_RO: if (write) return 1 | 0x0F << 1; break;     /* permission fault, level 3 */
    case AT_ABORT: return 1 | 0x10 << 1;                    /* synchronous external abort */
    case AT_DEVICE: attr = 0x00; break;
    case AT_NC: attr = 0x44; break;
    case AT_WT: attr = 0xBB; break;
    case AT_NONIDENT: out = page + PAGE; break;
    default: break;
    }
    return attr << 56 | out | UINT64_C(3) << 7 | 1 << 11;
}
uint64_t sp11hw_at_s1e2w(uint64_t va)
{
    uint64_t par = par_for(va, 1);
    W.n_at_w++;
    event(EV_AT, va, par, 1);
    return par;
}
uint64_t sp11hw_at_s1e2r(uint64_t va)
{
    uint64_t par = par_for(va, 0);
    W.n_at_r++;
    event(EV_AT, va, par, 0);
    return par;
}

const void *sp11hw_view(uint64_t pa, uint64_t size)
{
    struct region *r = find_region(pa, size ? size : 1);
    if (!r) bus_fault("read outside every region", pa, size);
    event(EV_VIEW, pa, size, 0);
    return r->cpu + (pa - r->base);
}

static void store_bytes(uint64_t pa, const void *src, unsigned n)
{
    struct region *r = find_region(pa, n);
    uint64_t off, i;
    if (!r) bus_fault("store outside every region", pa, n);
    if (!r->writable) bus_fault("store to a region that is not writable", pa, n);
    off = pa - r->base;
    memcpy(r->cpu + off, src, n);
    for (i = off / LINE; i <= (off + n - 1) / LINE; i++) r->dirty[i] = 1;
    W.n_store++;
    event(EV_ST, pa, n, 0);
}
void sp11hw_store64(uint64_t pa, uint64_t value)
{
    if (pa & 7) bus_fault("unaligned 64-bit store", pa, 8);
    store_bytes(pa, &value, 8);
}
void sp11hw_store8(uint64_t pa, uint8_t value) { store_bytes(pa, &value, 1); }

/* ---- the fake runner ---- */
static uint32_t fake_hex(char *dst, uint32_t cap, uint64_t value) { return (uint32_t)q1n1_payload_fmt_hex(dst, cap, value); }

static void patch(const char *line)
{
    int i;
    for (i = 0; i < W.ntamper; i++) {
        struct tamper *t = &W.tamper[i];
        struct region *r;
        if (t->done || !strstr(line, t->needle)) continue;
        r = find_region(t->addr, t->n);
        if (!r) fail("TAMPER outside every region");
        memcpy(r->cpu + (t->addr - r->base), t->bytes, t->n);
        memcpy(r->dram + (t->addr - r->base), t->bytes, t->n);
        t->done = 1;
    }
}

static void fake_puts(const char *line)
{
    char text[Q1N1_PAYLOAD_LINE_MAX + 1];
    unsigned n = 0;
    while (line[n] && n < Q1N1_PAYLOAD_LINE_MAX) {
        text[n] = line[n];
        n++;
    }
    text[n] = 0;
    W.n_puts++;
    if (W.nlines >= Q1N1_PAYLOAD_LINES_MAX) {
        W.n_dropped++;
        return;
    }
    /* What the panel would show is the runner's own folding of the line; a line is clean when only its case is folded (the pinned
     * build label may be lower case) and no character is replaced. */
    {
        char shown[Q1N1_PAYLOAD_LINE_MAX + 1], folded[Q1N1_PAYLOAD_LINE_MAX + 1];
        unsigned k;
        memcpy(shown, text, sizeof shown);
        q1n1_payload_sanitize(shown);
        for (k = 0; k <= n; k++) folded[k] = (char)toupper((unsigned char)text[k]);
        W.line_clean[W.nlines] = !strcmp(shown, folded) && strlen(line) <= Q1N1_PAYLOAD_LINE_MAX;
    }
    snprintf(W.lines[W.nlines], sizeof W.lines[0], "%s", line);
    W.nlines++;
    patch(line);
}

static int host_stack_holds(const void *p)
{
#ifdef __APPLE__
    pthread_t self = pthread_self();
    char *top = pthread_get_stackaddr_np(self), *bottom = top - pthread_get_stacksize_np(self);
    return (const char *)p >= bottom && (const char *)p < top;
#else
    (void)p;
    return 1;
#endif
}

static int32_t fake_load32(uint64_t addr, uint32_t *out)
{
    struct region *r;
    int i;
    if (!out) return Q1N1_PAYLOAD_LOAD_E_PARAM;
    if ((addr & 3) || ((uintptr_t)out & 3)) return Q1N1_PAYLOAD_LOAD_E_ALIGN;
    if (addr > (UINT64_C(1) << 48) - 4) return Q1N1_PAYLOAD_LOAD_E_RANGE;
    if (!host_stack_holds(out)) return Q1N1_PAYLOAD_LOAD_E_OUT;     /* the runner takes a destination inside the payload's stack only */
    if (W.loads >= Q1N1_PAYLOAD_LOADS_MAX) return Q1N1_PAYLOAD_LOAD_E_BUDGET;
    W.loads++;
    W.n_load++;
    for (i = 0; i < W.nload_fail; i++)
        if (W.load_fail[i].addr == addr) {
            event(EV_LOAD32, addr, W.load_fail[i].aux, 0);
            return (int32_t)W.load_fail[i].aux;
        }
    r = find_region(addr, 4);
    if (!r) {
        event(EV_LOAD32, addr, Q1N1_PAYLOAD_LOAD_E_FAULT, 0);
        return Q1N1_PAYLOAD_LOAD_E_FAULT;
    }
    memcpy(out, r->cpu + (addr - r->base), 4);
    event(EV_LOAD32, addr, 0, 0);
    return Q1N1_PAYLOAD_LOAD_OK;
}

static uint64_t fake_counter(void) { return sp11hw_ticks(); }

/* ---- scenarios ---- */
static uint64_t num(const char *token)
{
    char *end;
    unsigned long long v;
    if (!token) fail("missing number");
    v = strtoull(token, &end, 0);
    if (*end) fail("bad number");
    return v;
}

static unsigned hexbytes(const char *text, uint8_t *out, unsigned cap)
{
    unsigned n = 0;
    while (text[0] && text[1] && n < cap) {
        char pair[3] = {text[0], text[1], 0};
        out[n++] = (uint8_t)strtoul(pair, NULL, 16);
        text += 2;
    }
    return n;
}

static uint8_t *read_file(const char *path, uint64_t *size)
{
    FILE *f = fopen(path, "rb");
    uint8_t *data;
    long n;
    if (!f) fail("cannot open a scenario file");
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    data = malloc(n ? (size_t)n : 1);
    if (!data || fread(data, 1, (size_t)n, f) != (size_t)n) fail("cannot read a scenario file");
    fclose(f);
    *size = (uint64_t)n;
    return data;
}

static const char *const role_names[SP11SB_FILES] = {"SNAPMON.BIN", "SNAPREP.BIN", "GUEST.DAT", "PIN.DAT", "REPORT.DAT"};

static void defaults(void)
{
    int i;
    W.table_count = 5;
    W.table_magic = Q1N1_CACHE_MAGIC;
    W.self = 0;
    W.args_magic = Q1N1_PAYLOAD_ABI_MAGIC;
    W.args_version = Q1N1_PAYLOAD_ABI_VERSION;
    W.args_size = sizeof(struct q1n1_payload_args);
    W.ops_puts = W.ops_hex = W.ops_load32 = 1;
    W.tick_step = 1000;
    W.tick = 1000;
    for (i = 0; i < MAX_SLOTS; i++) snprintf(W.file_path[i], sizeof W.file_path[0], "\\SP11\\%s", i < 5 ? role_names[i] : "EXTRA.DAT");
}

/* Builds the regions a scenario describes: the preload table the runner would have made, the files that have an image, the map, the
 * stack and the placement. A file with no image gets no region: the code under test must not read it. */
static void build_world(const char *const slot_image[MAX_SLOTS], const char *map_image)
{
    static const char *const region_names[SP11SB_FILES] = {"snapmon", "snaprep", "guest", "pin", "report"};
    struct q1n1_preload_cache *t;
    struct region *r;
    int i;
    uint64_t size;
    uint8_t *data;
    r = add_region("table", W.table_base, sizeof(struct q1n1_preload_cache), 0);
    t = (struct q1n1_preload_cache *)r->cpu;
    t->magic = W.table_magic;
    t->version = 1;
    t->count = W.table_count;
    t->entry_size = sizeof(struct q1n1_cached_file);
    for (i = 0; i < MAX_SLOTS && (uint32_t)i < W.table_count && i < Q1N1_PRELOAD_MAX; i++) {
        t->files[i].base = W.file_base[i];
        t->files[i].allocation = (W.file_size[i] + PAGE - 1) & ~(uint64_t)(PAGE - 1);
        t->files[i].file.size = W.file_size[i];
        memcpy(t->files[i].file.path, W.file_path[i], sizeof t->files[i].file.path);
    }
    memcpy(r->dram, r->cpu, r->size);
    for (i = 0; i < MAX_SLOTS; i++) {
        if (!slot_image[i]) continue;
        data = read_file(slot_image[i], &size);
        r = add_region(i < (int)SP11SB_FILES ? region_names[i] : "extra", W.file_base[i], size > W.file_size[i] ? size : W.file_size[i],
                       i == SP11SB_ROLE_REPORT);
        memcpy(r->cpu, data, size);
        memcpy(r->dram, data, size);
        free(data);
    }
    if (map_image) {
        data = read_file(map_image, &size);
        r = add_region("map", W.map_base, size, 0);
        memcpy(r->cpu, data, size);
        memcpy(r->dram, data, size);
        free(data);
        if (!W.map_size) W.map_size = size;
    }
    if (W.stack_size) add_region("stack", W.stack_base, W.stack_size, 0);
    if (W.place_size) add_region("placement", W.place_base, W.place_size, 1);
}

static void run_entry(const char *which)
{
    struct q1n1_payload_args a;
    uint64_t ret = 0;
    int self = !strcmp(which, "snaprep") ? 1 : W.self;
    memset(&a, 0, sizeof a);
    a.magic = W.args_magic;
    a.version = W.args_version;
    a.size = W.args_size;
    a.payload_base = W.file_base[self];
    a.payload_size = W.file_size[self];
    a.fb_base = W.fb_base;
    a.fb_bytes = W.fb_bytes;
    a.fb_width = W.fb_w;
    a.fb_height = W.fb_h;
    a.fb_stride = W.fb_stride;
    a.fb_format = W.fb_fmt;
    a.map_base = W.map_base;
    a.map_size = W.map_size;
    a.map_stride = W.map_stride;
    a.preload_table = W.table_base;
    a.preload_size = sizeof(struct q1n1_preload_cache);
    a.stack_base = W.stack_base;
    a.stack_size = W.stack_size;
    a.ops.puts = W.ops_puts ? fake_puts : NULL;
    a.ops.hex = W.ops_hex ? fake_hex : NULL;
    a.ops.load32 = W.ops_load32 ? fake_load32 : NULL;
    a.ops.counter = fake_counter;
    clear_logs();
    W.flip_done = W.lose_done = 0;
    if (!setjmp(W.jb)) ret = !strcmp(which, "snaprep") ? snaprep_entry(&a) : snapmon_entry(&a);
    printf("RESULT %s %s ret=%llx faulted=%d\n", W.name, which, (unsigned long long)ret, W.faulted);
    if (W.faulted) printf("FAULT %s\n", W.fault_text);
}

static void print_digest(const struct rule *d)
{
    struct region *r = find_region(d->addr, d->len);
    uint8_t a[32], b[32];
    int i;
    if (!r) {
        printf("DIGEST %llx %llx -\n", (unsigned long long)d->addr, (unsigned long long)d->len);
        return;
    }
    q1n1_sha256(r->cpu + (d->addr - r->base), (size_t)d->len, a);
    q1n1_sha256(r->dram + (d->addr - r->base), (size_t)d->len, b);
    printf("DIGEST %llx %llx ", (unsigned long long)d->addr, (unsigned long long)d->len);
    for (i = 0; i < 32; i++) printf("%02x", a[i]);
    printf(" ");
    for (i = 0; i < 32; i++) printf("%02x", b[i]);
    printf("\n");
}

static void report_results(void)
{
    static const char *const types[] = {"ST", "CVAC", "CIVAC", "DSB", "ISB", "VIEW", "AT", "LOAD32", "MRS"};
    struct region *rep = region_named("report");
    size_t i;
    int k;
    unsigned long long nonzero = 0, j;
    for (i = 0; i < W.nlines; i++) printf("LINE %u %zu %d %s\n", (unsigned)i, strlen(W.lines[i]), W.line_clean[i], W.lines[i]);
    for (i = 0; i < W.nev; i++)
        printf("EV %zu %s %llx %llx %x %u\n", i, types[W.ev[i].type], (unsigned long long)W.ev[i].a, (unsigned long long)W.ev[i].b,
               W.ev[i].stage, W.ev[i].extra);
    if (rep) {
        printf("REPORT ");
        for (j = 0; j < SP11SB_REPORT_HEAD_BYTES; j++) printf("%02x", rep->cpu[j]);
        for (j = SP11SB_REPORT_HEAD_BYTES; j < SP11SB_REPORT_BYTES && j < rep->size; j++) nonzero += rep->cpu[j] != 0;
        printf("\nREPORTTAIL %llu\n", nonzero);
    }
    for (k = 0; k < W.nreg; k++) {
        unsigned long long dirty = 0, idx;
        for (idx = 0; idx < W.reg[k].size / LINE; idx++) dirty += W.reg[k].dirty[idx] != 0;
        printf("DIRTY %s %llu\n", W.reg[k].name, dirty);
    }
    for (k = 0; k < W.ndigest; k++) print_digest(&W.digest[k]);
    printf("COUNTS store=%lu cvac=%lu civac=%lu dsb=%lu isb=%lu at_w=%lu at_r=%lu load32=%lu ticks=%lu puts=%lu dropped=%lu\n", W.n_store,
           W.n_cvac, W.n_civac, W.n_dsb, W.n_isb, W.n_at_w, W.n_at_r, W.n_load, W.n_ticks, W.n_puts, W.n_dropped);
}

int main(void)
{
    char buf[1024];
    const char *slot_image[MAX_SLOTS] = {0}, *map_image = NULL;
    char *held[MAX_SLOTS + 1] = {0};
    int in_scenario = 0, nheld = 0, i;
    setvbuf(stdout, NULL, _IOLBF, 0);
    while (fgets(buf, sizeof buf, stdin)) {
        char *tok[16], *p = buf, *save;
        int n = 0;
        while (n < 16 && (tok[n] = strtok_r(p, " \t\r\n", &save))) {
            n++;
            p = NULL;
        }
        if (!n || tok[0][0] == '#') continue;
        if (!strcmp(tok[0], "BEGIN")) {
            free_world();
            defaults();
            memset(slot_image, 0, sizeof slot_image);
            for (i = 0; i < nheld; i++) free(held[i]);
            nheld = 0;
            map_image = NULL;
            snprintf(W.name, sizeof W.name, "%s", tok[1]);
            in_scenario = 1;
            continue;
        }
        if (!in_scenario) fail("command outside a scenario");
        if (!strcmp(tok[0], "TABLE")) {
            W.table_base = num(tok[1]);
            if (n > 2) W.table_count = (uint32_t)num(tok[2]);
        } else if (!strcmp(tok[0], "TABLEMAGIC")) {
            W.table_magic = num(tok[1]);
        } else if (!strcmp(tok[0], "FILE")) {
            int slot = (int)num(tok[1]);
            if (slot < 0 || slot >= MAX_SLOTS) fail("slot");
            if (strcmp(tok[2], "-")) {
                held[nheld] = strdup(tok[2]);
                slot_image[slot] = held[nheld++];
            }
            W.file_base[slot] = num(tok[3]);
            if (n > 4) W.file_size[slot] = num(tok[4]);
            else {
                uint64_t size = 0;
                free(read_file(tok[2], &size));
                W.file_size[slot] = size;
            }
        } else if (!strcmp(tok[0], "PATHHEX")) {
            int slot = (int)num(tok[1]);
            memset(W.file_path[slot], 0, sizeof W.file_path[0]);
            hexbytes(tok[2], (uint8_t *)W.file_path[slot], sizeof W.file_path[0]);
        } else if (!strcmp(tok[0], "SELF")) {
            W.self = (int)num(tok[1]);
        } else if (!strcmp(tok[0], "MAP")) {
            W.map_base = num(tok[1]);
            W.map_stride = num(tok[2]);
            held[nheld] = strdup(tok[3]);
            map_image = held[nheld++];
            if (n > 4) W.map_size = num(tok[4]);
        } else if (!strcmp(tok[0], "STACK")) {
            W.stack_base = num(tok[1]);
            W.stack_size = num(tok[2]);
        } else if (!strcmp(tok[0], "FB")) {
            W.fb_base = num(tok[1]);
            W.fb_bytes = num(tok[2]);
            W.fb_w = (uint32_t)num(tok[3]);
            W.fb_h = (uint32_t)num(tok[4]);
            W.fb_stride = (uint32_t)num(tok[5]);
            W.fb_fmt = (uint32_t)num(tok[6]);
        } else if (!strcmp(tok[0], "PLACEMENT")) {
            W.place_base = num(tok[1]);
            W.place_size = num(tok[2]);
        } else if (!strcmp(tok[0], "REG")) {
            for (i = 0; i < 8 && strcmp(tok[1], reg_names[i]); i++) {}
            if (i == 8) fail("REG name");
            W.reg_value[i] = num(tok[2]);
        } else if (!strcmp(tok[0], "ARGS")) {
            W.args_magic = num(tok[1]);
            W.args_version = (uint32_t)num(tok[2]);
            W.args_size = (uint32_t)num(tok[3]);
        } else if (!strcmp(tok[0], "OPS")) {
            W.ops_puts = (int)num(tok[1]);
            W.ops_hex = (int)num(tok[2]);
            W.ops_load32 = (int)num(tok[3]);
        } else if (!strcmp(tok[0], "LOADFAIL")) {
            W.load_fail[W.nload_fail].addr = num(tok[1]);
            W.load_fail[W.nload_fail++].aux = n > 2 ? num(tok[2]) : Q1N1_PAYLOAD_LOAD_E_FAULT;
        } else if (!strcmp(tok[0], "AT")) {
            for (i = 0; i < 8 && strcmp(tok[3], at_names[i]); i++) {}
            if (i == 8) fail("AT kind");
            W.at_rule[W.nat].addr = num(tok[1]);
            W.at_rule[W.nat].len = num(tok[2]);
            W.at_rule[W.nat++].kind = i;
        } else if (!strcmp(tok[0], "FLIP")) {
            W.flip_set = 1;
            W.flip_addr = num(tok[1]);
            W.flip_mask = num(tok[2]);
        } else if (!strcmp(tok[0], "LOSE")) {
            W.lose_set = 1;
            W.lose_addr = num(tok[1]);
        } else if (!strcmp(tok[0], "TAMPER")) {
            struct tamper *t = &W.tamper[W.ntamper++];
            snprintf(t->needle, sizeof t->needle, "%s", tok[1]);
            t->addr = num(tok[2]);
            t->n = hexbytes(tok[3], t->bytes, sizeof t->bytes);
        } else if (!strcmp(tok[0], "TICK")) {
            W.tick = num(tok[1]);
            W.tick_step = num(tok[2]);
        } else if (!strcmp(tok[0], "PREFILL")) {
            struct region *r;
            if (!W.nreg) build_world(slot_image, map_image);
            r = find_region(num(tok[1]), num(tok[2]));
            if (!r) fail("PREFILL outside every region");
            memset(r->cpu + (num(tok[1]) - r->base), (int)num(tok[3]), (size_t)num(tok[2]));
            memset(r->dram + (num(tok[1]) - r->base), (int)num(tok[3]), (size_t)num(tok[2]));
        } else if (!strcmp(tok[0], "DIGEST")) {
            W.digest[W.ndigest].addr = num(tok[1]);
            W.digest[W.ndigest++].len = num(tok[2]);
        } else if (!strcmp(tok[0], "RUN")) {
            if (!W.nreg) build_world(slot_image, map_image);
            run_entry(tok[1]);
            report_results();
        } else if (!strcmp(tok[0], "END")) {
            printf("END %s\n", W.name);
            free_world();
            in_scenario = 0;
        } else {
            fail("unknown command");
        }
    }
    free_world();
    return 0;
}
