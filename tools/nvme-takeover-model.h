/* SPDX-License-Identifier: MIT */
/* The takeover half of the fake controller of tools/test-nvme-host.c: registers that follow the NVMe rules (CC, CSTS,
 * AQA, ASQ, ACQ, CAP, doorbells), an admin queue, queue creation, Identify pages, a CPU cache over the DMA extent and
 * fault injection. Included by tools/test-nvme-host.c only with SNAP_NVME_TAKEOVER, in the middle of that file: it
 * extends that controller (whose I/O queue stays controller() there), it is not a second one, and it was split out of
 * it to keep both files under the size limits of AGENTS.md. tools/test-nvme-takeover.c drives compat/nvme-takeover.c
 * against it.
 *
 * Everything the unit may not do is counted in bad_* and fails the case. The f_* knobs inject one fault each; wait_seq
 * numbers the events the unit waits for, so a case can make the n-th one never happen. The slow_* knobs make a
 * transition take ticks (a reset, an enable, a shutdown notification) instead of a few reads, so a wait's bound is tested
 * from both sides. A CC.EN write of 0 while EN is already 0 resets nothing, as in the base specification: a controller
 * that is hung stays hung however often it is asked, so nvmet_abort's retry only buys one more wait. The completion
 * faults reach the admin queue here and the I/O queue through controller(); the late duplicate and the completion
 * latency exist on the admin queue only, which is enough because both queues run the unit's one cmd(). Host only:
 * nothing touches a device. */
#ifndef TOOLS_NVME_TAKEOVER_MODEL_H
#define TOOLS_NVME_TAKEOVER_MODEL_H

static void controller(unsigned tail);                       /* defined after this file's include point */

#define TK_MAX_WAITS 64
#define TK_FAIL(what) do { printf("FAIL takeover model: %s\n", what); failures++; } while (0)
enum { TK_WAIT_SHUTDOWN = 1, TK_WAIT_RDY0, TK_WAIT_RDY1, TK_WAIT_ADMIN, TK_WAIT_IO };
enum { TKF_NONE, TKF_PHASE, TKF_CID, TKF_DUP, TKF_STATUS, TKF_SQID, TKF_SQHD, TKF_LATE_DUP };
struct tk_fault { uint32_t cid_xor, status, sqid, sqhd; unsigned phase_flip, dup, late_dup; };
static struct tk {
    int active, confine;                      /* confine: after the hand-off the old window checks apply again */
    uint32_t cc, csts, aqa;
    uint64_t cap, asq, acq, nsze, eui64;
    unsigned enabled, delay;                  /* delay: CSTS reads before a pending transition lands */
    int rdy_pending;
    unsigned rdy_delay, shn_pending, shst_delay;
    uint64_t rdy_due, shst_due;               /* the tick a slow transition lands; 0: after `delay` CSTS reads instead */
    uint64_t slow_rdy_up, slow_rdy_down, slow_shst;   /* knobs: ticks a CC.EN 1, a reset and a shutdown notification take */
    unsigned a_head, a_cq_tail, a_phase, a_cq_head_db, cq_made, sq_made, io_cq_head_db;
    unsigned mdts, nn, sqes, cqes, nlbaf, flbas, lbaf512, ms_cur, id_zero_ctrl, id_zero_ns, id_nodma, gone, gone_on_cc, cfs_on_never;
    unsigned f_rdy_never, f_rdy_stuck, f_cfs, f_cfs_rdy, f_never_at;
    unsigned f_queue, f_at, f_kind, f_status;
    unsigned spec_fill;                       /* bit 0: the Identify page, bit 1: read data are filled into the CPU cache first */
    unsigned lat_at, late_pending;            /* the admin command whose completion is late; a duplicate waits for the head doorbell */
    uint64_t latency, lat_due, last_slba;     /* ticks a completion stays invisible; the SLBA of the last read, all 64 bits */
    uint32_t first_cc, late_cid, late_status, lat_cid, lat_status, lat_dw0, lat_sqhead;
    unsigned wait_seq, wait_kind[TK_MAX_WAITS + 1], admin_seq, io_seq;
    unsigned cc_writes, aq_writes, db_total, reg_reads, bad_reg, bad_doorbell, bad_dma, bad_opcode, bad_cache, oob_reads;
    unsigned cache;                           /* 1: the CPU's accesses to the extent go through the cache model below */
    unsigned admin_ops[256], io_ops[256];
    uint32_t cc_on_enable, aqa_on_enable;
    uint64_t asq_on_enable, acq_on_enable;
} tk;
/* The n-th waitable event: 1 when this is the one the case made never happen. */
static int tk_wait_event(unsigned kind)
{
    unsigned n = ++tk.wait_seq;
    if (n <= TK_MAX_WAITS) tk.wait_kind[n] = kind;
    if (tk.f_never_at != n) return 0;
    if (tk.cfs_on_never && (kind == TK_WAIT_ADMIN || kind == TK_WAIT_IO)) tk.csts |= 2u;   /* a controller that died with it */
    return 1;
}
/* The fault, if any, for the n-th command of a queue (0 admin, 1 I/O). */
static struct tk_fault tk_fault_for(unsigned queue, unsigned n)
{
    struct tk_fault f = {0, 0, 0, 0, 0, 0, 0};
    if (tk.f_kind == TKF_NONE || tk.f_queue != queue || tk.f_at != n) return f;
    switch (tk.f_kind) {
    case TKF_PHASE: f.phase_flip = 1; break;
    case TKF_CID: f.cid_xor = 0x40; break;
    case TKF_DUP: f.dup = 1; break;
    case TKF_STATUS: f.status = tk.f_status; break;
    case TKF_SQID: f.sqid = queue ? 2u : 1u; break;
    case TKF_SQHD: f.sqhd = queue ? io_entries : NVMEH_ADMIN_ENTRIES; break;     /* one past the last slot */
    case TKF_LATE_DUP: f.late_dup = 1; break;
    default: break;
    }
    return f;
}

/* The CPU's cache over the extent, in 64-byte lines. A CPU load or store goes through it; `clean` (DC CIVAC) writes a
 * dirty line back and drops it; the controller's DMA touches memory only. So a store that is not cleaned is invisible to
 * the controller, a completion or data that is not invalidated is invisible to the CPU, and a dirty line written back
 * late overwrites what the controller wrote. */
#define TK_LINE 64u
static uint8_t cache_copy[NVMEH_WINDOW_BYTES];
static uint8_t cache_state[NVMEH_WINDOW_BYTES / TK_LINE];       /* 0 absent, 1 clean copy, 2 dirty */
static void cache_fill(uint64_t line)
{
    if (cache_state[line]) return;
    memcpy(cache_copy + line * TK_LINE, dma + line * TK_LINE, TK_LINE);
    cache_state[line] = 1;
}
static uint32_t cache_rd(uint64_t pa)
{
    uint64_t off = pa - DMA;
    uint32_t v;
    cache_fill(off / TK_LINE);
    memcpy(&v, cache_copy + off, 4);
    return v;
}
static void cache_wr(uint64_t pa, uint32_t v)
{
    uint64_t off = pa - DMA;
    cache_fill(off / TK_LINE);
    memcpy(cache_copy + off, &v, 4);
    cache_state[off / TK_LINE] = 2;
}
static void cache_clean(uint64_t pa, uint64_t size)
{
    if (pa < DMA || !size || pa + size > DMA + NVMEH_WINDOW_BYTES) return;
    for (uint64_t line = (pa - DMA) / TK_LINE; line <= (pa + size - 1 - DMA) / TK_LINE; line++) {
        if (cache_state[line] == 2) memcpy(dma + line * TK_LINE, cache_copy + line * TK_LINE, TK_LINE);
        cache_state[line] = 0;
    }
}
/* A speculative fill, as a CPU's prefetcher does at any time: lines the controller is about to write sit in the cache
 * with their old content, so the unit must invalidate them after the DMA or read stale data. */
static void tk_spec_fill(const uint8_t *p, uint64_t len)
{
    uint64_t off = (uint64_t)(p - dma);
    for (uint64_t line = off / TK_LINE; len && line <= (off + len - 1) / TK_LINE; line++) cache_fill(line);
}
/* The controller reads or writes memory: a line the CPU still holds dirty is stale for its read and is destroyed by the
 * late write-back after its write. */
static void tk_dev_access(const uint8_t *p, uint64_t len, int write)
{
    uint64_t off = (uint64_t)(p - dma);
    for (uint64_t line = off / TK_LINE; len && line <= (off + len - 1) / TK_LINE; line++)
        if (cache_state[line] == 2) {
            tk.bad_cache++;
            TK_FAIL(write ? "the controller wrote a line the CPU holds dirty" : "the controller read a line the CPU had not cleaned");
            return;
        }
}
#define DEV_READ(p, n) do { if (tk.active && tk.cache && (p)) tk_dev_access((p), (n), 0); } while (0)
#define DEV_WRITE(p, n) do { if (tk.active && tk.cache && (p)) tk_dev_access((p), (n), 1); } while (0)

static void put16(uint8_t *b, uint16_t v) { memcpy(b, &v, 2); }
static void put32(uint8_t *b, uint32_t v) { memcpy(b, &v, 4); }
static void put64(uint8_t *b, uint64_t v) { memcpy(b, &v, 8); }

/* CSTS as the controller shows it: a pending RDY or SHST transition lands after `delay` reads, or, with a slow knob set,
 * once the counter has passed its due tick: that is what the waits' bounds are tested against. */
static uint32_t tk_csts_read(void)
{
    if (tk.rdy_pending >= 0 && (tk.rdy_due ? clock_now >= tk.rdy_due : tk.rdy_delay && !--tk.rdy_delay)) {
        tk.csts = (tk.csts & ~1u) | (uint32_t)tk.rdy_pending;
        if (!tk.rdy_pending) tk.csts &= ~(2u | 0xcu);            /* a reset clears CFS and SHST */
        tk.rdy_pending = -1; tk.rdy_due = 0;
    }
    if (tk.shn_pending && (tk.shst_due ? clock_now >= tk.shst_due : tk.shst_delay && !--tk.shst_delay)) {
        tk.csts = (tk.csts & ~0xcu) | 0x8u;
        tk.shn_pending = 0; tk.shst_due = 0;
    }
    return tk.csts;
}

/* Identify data: obviously fake identifiers, the knobs' geometry. The namespace has one 512-byte format, lbaf512, and
 * every other format 4096 bytes; flbas is the raw byte the unit decodes. */
static void tk_identify_page(uint8_t *b, unsigned cns)
{
    memset(b, 0, 4096);
    if (cns == 1) {
        if (tk.id_zero_ctrl) return;
        put16(b, 0x144d);
        memcpy(b + 4, "FAKE-SERIAL-0000001 ", 20);
        memcpy(b + 24, "SNAPTEST FAKE NVME CONTROLLER           ", 40);
        memcpy(b + 64, "FK000001", 8);
        b[77] = (uint8_t)tk.mdts;
        put32(b + 80, 0x00010400);
        b[512] = (uint8_t)tk.sqes; b[513] = (uint8_t)tk.cqes;
        put32(b + 516, tk.nn);
        put16(b + 520, 0x001f); b[525] = 1;
        put32(b + 272, 0x1000); put32(b + 276, 0x800);           /* HMPRE, HMMIN: a controller that wants 16 MiB, at least 8 */
        return;
    }
    if (tk.id_zero_ns) return;
    put64(b, tk.nsze); put64(b + 8, tk.nsze); put64(b + 16, tk.nsze);
    b[25] = (uint8_t)tk.nlbaf; b[26] = (uint8_t)tk.flbas;
    for (unsigned i = 0; i <= tk.nlbaf && i < 64; i++) b[130 + 4 * i] = i == tk.lbaf512 ? 9 : 12;
    put16(b + 128 + 4 * tk.lbaf512, (uint16_t)tk.ms_cur);
    for (unsigned i = 0; i < 8; i++) b[120 + i] = (uint8_t)(tk.eui64 >> (56 - 8 * i));   /* the page order of the survey's hex string */
    memcpy(b + 104, "FAKE-NGUID-00001", 16);
}

static void tk_post(uint64_t cq, unsigned *tail, unsigned *phase, unsigned entries, unsigned sqid, unsigned sqhead,
                    uint32_t cid, uint32_t status, uint32_t dw0, const struct tk_fault *f)
{
    for (unsigned copy = 0; copy < 1 + f->dup; copy++) {
        uint8_t *c = dmem(cq + *tail * 16ull);
        uint32_t dw2 = (f->sqhd ? f->sqhd : sqhead) | (f->sqid ? f->sqid : sqid) << 16;
        uint32_t dw3 = (cid ^ f->cid_xor) | (*phase ^ f->phase_flip) << 16 | (f->status ? f->status : status) << 17;
        if (!c) { tk.bad_dma++; TK_FAIL("a completion outside the extent"); return; }
        DEV_WRITE(c, 16);
        memset(c, 0, 16);
        memcpy(c, &dw0, 4); memcpy(c + 8, &dw2, 4); memcpy(c + 12, &dw3, 4);
        if (f->phase_flip) return;                               /* an entry of the wrong phase is not posted: the slot stays */
        *tail = (*tail + 1) % entries;
        if (!*tail) *phase ^= 1;
    }
    if (f->late_dup) { tk.late_pending = 1; tk.late_cid = cid; tk.late_status = f->status ? f->status : status; }
}
/* A completion held back by tk.latency appears once the counter has passed its due time. */
static void tk_pending_flush(void)
{
    struct tk_fault none = {0, 0, 0, 0, 0, 0, 0};
    if (!tk.lat_due || clock_now < tk.lat_due) return;
    tk.lat_due = 0;
    tk_post(tk.acq, &tk.a_cq_tail, &tk.a_phase, NVMEH_ADMIN_ENTRIES, 0, tk.lat_sqhead, tk.lat_cid, tk.lat_status, tk.lat_dw0, &none);
}

static void tk_admin(unsigned tail)
{
    while (tk.a_head != tail) {
        uint8_t *e = dmem(tk.asq + tk.a_head * 64ull);
        uint32_t cdw0, cdw10, cdw11, status = 0, dw0 = 0;
        uint64_t prp1;
        unsigned op, mqes = (unsigned)(tk.cap & 0xffff) + 1;
        if (!e) { tk.bad_dma++; TK_FAIL("an admin SQE outside the extent"); return; }
        DEV_READ(e, 64);
        tk.a_head = (tk.a_head + 1) % NVMEH_ADMIN_ENTRIES;
        memcpy(&cdw0, e, 4); prp1 = get64(e + 24); memcpy(&cdw10, e + 40, 4); memcpy(&cdw11, e + 44, 4);
        op = cdw0 & 0xff;
        tk.admin_ops[op]++;
        if (tk_wait_event(TK_WAIT_ADMIN)) continue;              /* this command never completes */
        switch (op) {
        case 0x06: {                                             /* Identify */
            uint8_t *page = dmem(prp1);
            if (!page) { tk.bad_dma++; TK_FAIL("an Identify PRP outside the extent"); status = 0x04; }
            else if ((cdw10 & 0xff) > 1) status = 0x02;
            else if (!(tk.id_nodma & 1u << (cdw10 & 0xff))) {          /* id_nodma bit 0: no DMA for CNS 0, bit 1: for CNS 1 */
                if (tk.spec_fill & 1) tk_spec_fill(page, 4096);
                DEV_WRITE(page, 4096);
                tk_identify_page(page, cdw10 & 0xff);
            }
            break;
        }
        case 0x09:                                               /* Set Features: Number of Queues only */
            status = (cdw10 & 0xff) == 7 ? 0 : 0x02;
            break;
        case 0x05: {                                             /* Create I/O CQ */
            unsigned qid = cdw10 & 0xffff, size = (cdw10 >> 16) + 1;
            if (qid != 1 || tk.cq_made) status = 0x101;
            else if (size > mqes) status = 0x102;
            else if (!(cdw11 & 1) || !prp1 || (prp1 & 0xfff)) status = 0x02;
            else {
                io_cq_bus = prp1; io_entries = size; tk.cq_made = 1; cq_tail = 0; cq_phase = 1; tk.io_cq_head_db = 0;
            }
            break;
        }
        case 0x01: {                                             /* Create I/O SQ */
            unsigned qid = cdw10 & 0xffff, size = (cdw10 >> 16) + 1;
            if (qid != 1 || tk.sq_made) status = 0x101;
            else if (!tk.cq_made || (cdw11 >> 16) != 1) status = 0x100;
            else if (size > mqes || size != io_entries) status = 0x102;
            else if (!(cdw11 & 1) || !prp1 || (prp1 & 0xfff)) status = 0x02;
            else { io_sq_bus = prp1; tk.sq_made = 1; sq_head = 0; }
            break;
        }
        case 0x00:                                               /* Delete I/O SQ */
            if (!tk.sq_made || (cdw10 & 0xffff) != 1) status = 0x101; else tk.sq_made = 0;
            break;
        case 0x04:                                               /* Delete I/O CQ: its SQ goes first */
            if (!tk.cq_made || (cdw10 & 0xffff) != 1) status = 0x101;
            else if (tk.sq_made) status = 0x10c;
            else tk.cq_made = 0;
            break;
        default:
            tk.bad_opcode++; TK_FAIL("an admin opcode outside the takeover allow-list reached the controller");
            status = 0x01;
        }
        struct tk_fault f = tk_fault_for(0, ++tk.admin_seq);
        if (tk.latency && tk.admin_seq == tk.lat_at) {           /* the completion lands later, after tk.latency ticks */
            tk.lat_due = clock_now + tk.latency; tk.lat_cid = cdw0 >> 16; tk.lat_status = status; tk.lat_dw0 = dw0;
            tk.lat_sqhead = tk.a_head;
        } else tk_post(tk.acq, &tk.a_cq_tail, &tk.a_phase, NVMEH_ADMIN_ENTRIES, 0, tk.a_head, cdw0 >> 16, status, dw0, &f);
    }
}

/* A head doorbell must consume at least one and at most the posted entries. */
static void tk_head_db(unsigned *prev, unsigned v, unsigned posted, unsigned entries, const char *what)
{
    unsigned adv = (v + entries - *prev) % entries, avail = (posted + entries - *prev) % entries;
    if (v >= entries || !adv || adv > avail) { tk.bad_doorbell++; TK_FAIL(what); }
    *prev = v % entries;
}

static void tk_doorbell(uint64_t off, uint32_t v)
{
    unsigned stride = 4u << ((tk.cap >> 32) & 0xf), idx = (unsigned)((off - 0x1000) / stride);
    tk.db_total++;
    if ((off - 0x1000) % stride || !tk.enabled || !(tk.csts & 1)) {
        tk.bad_doorbell++; TK_FAIL("a doorbell off the stride or while the controller is not ready"); return;
    }
    switch (idx) {
    case 0:
        if (v >= NVMEH_ADMIN_ENTRIES || v != (tk.a_head + 1) % NVMEH_ADMIN_ENTRIES) {
            tk.bad_doorbell++; TK_FAIL("the admin SQ tail doorbell is not the next entry");
        } else tk_admin(v);
        break;
    case 1:
        tk_head_db(&tk.a_cq_head_db, v, tk.a_cq_tail, NVMEH_ADMIN_ENTRIES, "the admin CQ head doorbell");
        if (tk.late_pending) {                                   /* a duplicate that arrives after the host consumed the first */
            struct tk_fault again = {0, 0, 0, 0, 0, 0, 0};
            tk.late_pending = 0;
            tk_post(tk.acq, &tk.a_cq_tail, &tk.a_phase, NVMEH_ADMIN_ENTRIES, 0, tk.a_head, tk.late_cid, tk.late_status, 0, &again);
        }
        break;
    case 2:
        if (!tk.sq_made || v >= io_entries || v != (sq_head + 1) % io_entries) {
            tk.bad_doorbell++; TK_FAIL("the I/O SQ tail doorbell of a queue that does not exist or not the next entry");
        } else { doorbells++; controller(v); }
        break;
    case 3:
        if (!tk.cq_made) { tk.bad_doorbell++; TK_FAIL("the I/O CQ head doorbell of a queue that does not exist"); }
        else tk_head_db(&tk.io_cq_head_db, v, cq_tail, io_entries, "the I/O CQ head doorbell");
        break;
    default: tk.bad_doorbell++; TK_FAIL("a doorbell of a queue that was never created");
    }
}

static void tk_cc_write(uint32_t v)
{
    unsigned en_old = tk.cc & 1, en_new = v & 1, shn = (v >> 14) & 3;
    tk.cc_writes++;
    if (tk.cc_writes == 1) tk.first_cc = v;
    if (shn > 1) { tk.bad_reg++; TK_FAIL("CC.SHN written as abrupt or reserved: the unit asks for a normal shutdown only"); }
    if (tk.gone_on_cc) tk.gone = 1;                              /* the device vanishes at the first CC write */
    if (!en_old && en_new) {                                     /* enable */
        int bad_cc = ((v >> 16) & 0xf) != 6 || ((v >> 20) & 0xf) != 4 || ((v >> 7) & 0xf) || ((v >> 4) & 7) ||
                     ((v >> 11) & 7) || shn;
        int bad_aq = (tk.aqa & 0xfff) < 1 || ((tk.aqa >> 16) & 0xfff) < 1 || (tk.aqa & 0xfff) > (tk.cap & 0xffff) ||
                     ((tk.aqa >> 16) & 0xfff) > (tk.cap & 0xffff) || !tk.asq || !tk.acq || (tk.asq & 0xfff) || (tk.acq & 0xfff);
        int never = tk_wait_event(TK_WAIT_RDY1) || tk.f_rdy_never;
        tk.cc_on_enable = v; tk.aqa_on_enable = tk.aqa; tk.asq_on_enable = tk.asq; tk.acq_on_enable = tk.acq;
        tk.a_head = 0; tk.a_cq_tail = 0; tk.a_phase = 1; tk.a_cq_head_db = 0; tk.cq_made = tk.sq_made = 0;
        tk.enabled = 1;
        if (bad_cc || bad_aq || tk.f_cfs) tk.csts |= 2u | (tk.f_cfs_rdy ? 1u : 0u);   /* a setup it cannot run is fatal */
        else if (!never) { tk.rdy_pending = 1; tk.rdy_delay = tk.delay; tk.rdy_due = tk.slow_rdy_up ? clock_now + tk.slow_rdy_up : 0; }
    } else if (en_old && !en_new) {                              /* disable: a controller reset, on the 1 to 0 transition only */
        int never = tk_wait_event(TK_WAIT_RDY0) || tk.f_rdy_stuck;
        tk.enabled = 0; tk.cq_made = tk.sq_made = 0; tk.shn_pending = 0; tk.shst_due = 0;
        if (!never) { tk.rdy_pending = 0; tk.rdy_delay = tk.delay; tk.rdy_due = tk.slow_rdy_down ? clock_now + tk.slow_rdy_down : 0; }
    } else if (en_old && en_new && shn == 1) {                   /* normal shutdown notification */
        if (!tk_wait_event(TK_WAIT_SHUTDOWN)) {
            tk.shn_pending = 1; tk.shst_delay = tk.delay; tk.csts = (tk.csts & ~0xcu) | 0x4u;
            tk.shst_due = tk.slow_shst ? clock_now + tk.slow_shst : 0;
        }
    } else if (!en_old && !en_new && v) { tk.bad_reg++; TK_FAIL("CC written non-zero while disabled"); }
    tk.cc = v;
}

static int tk_rd(uint64_t off, uint32_t *v)
{
    tk.reg_reads++;
    if (tk.gone) { *v = 0xffffffffu; return 1; }
    switch (off) {
    case 0x00: *v = (uint32_t)tk.cap; return 1;
    case 0x04: *v = (uint32_t)(tk.cap >> 32); return 1;
    case 0x08: *v = 0x00010400; return 1;
    case 0x14: *v = tk.cc; return 1;
    case 0x1c: *v = tk_csts_read(); return 1;
    case 0x24: *v = tk.aqa; return 1;
    case 0x28: *v = (uint32_t)tk.asq; return 1;
    case 0x2c: *v = (uint32_t)(tk.asq >> 32); return 1;
    case 0x30: *v = (uint32_t)tk.acq; return 1;
    case 0x34: *v = (uint32_t)(tk.acq >> 32); return 1;
    default: return 0;
    }
}

/* Every write to the BAR: only CC, AQA, ASQ, ACQ and the doorbells are the takeover's to make. */
static void tk_wr(uint64_t off, uint32_t v)
{
    if (off == 0x14) { tk_cc_write(v); return; }
    if (off == 0x24 || off == 0x28 || off == 0x2c || off == 0x30 || off == 0x34) {
        tk.aq_writes++;
        if (tk.cc & 1) { tk.bad_reg++; TK_FAIL("AQA, ASQ or ACQ written while CC.EN is 1"); }
        if (off == 0x24) tk.aqa = v;
        else if (off == 0x28) tk.asq = (tk.asq & ~0xffffffffull) | v;
        else if (off == 0x2c) tk.asq = (tk.asq & 0xffffffffull) | (uint64_t)v << 32;
        else if (off == 0x30) tk.acq = (tk.acq & ~0xffffffffull) | v;
        else tk.acq = (tk.acq & 0xffffffffull) | (uint64_t)v << 32;
        return;
    }
    if (off >= 0x1000) { tk_doorbell(off, v); return; }
    tk.bad_reg++; TK_FAIL("a write to a register the takeover does not own");
}

#endif
