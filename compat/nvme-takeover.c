/* SPDX-License-Identifier: MIT */
/* See nvme-takeover.h. No libc: nothing here may emit a call to memset or memcpy, so the one clear is a volatile loop. */
#include "nvme-takeover.h"

#if SNAP_NVME_TAKEOVER

#define RD(o, pa)          ((o)->rd((o)->ctx, (pa)))
#define WR(o, pa, v)       ((o)->wr((o)->ctx, (pa), (uint32_t)(v)))
#define CLEAN(o, pa, size) ((o)->clean((o)->ctx, (pa), (size)))
#define NOW(o)             ((o)->now((o)->ctx))

#define CSTS_RDY 1u
#define CSTS_CFS 2u
#define CSTS_SHST_MASK 0xcu
#define CSTS_SHST_DONE 0x8u
#define REG_VS 0x08u
#define EXTENT NVMEH_WINDOW_BYTES
#define TICKS_PER_S_MAX (1ull << 40)

static uint64_t rd64(const struct nvmeh_ops *o, uint64_t pa)
{
    return RD(o, pa) | (uint64_t)RD(o, pa + 4) << 32;
}
/* Low word first, as the firmware's driver and q1n1 write them. */
static void wr64(const struct nvmeh_ops *o, uint64_t pa, uint64_t v)
{
    WR(o, pa, (uint32_t)v);
    WR(o, pa + 4, (uint32_t)(v >> 32));
}
static uint64_t cpu(const struct nvmet *t, uint64_t off) { return t->cpu_base + off; }
static uint64_t bus(const struct nvmet *t, uint64_t off) { return t->bus_base + off; }
/* Queue y's tail doorbell is index 2y and its head doorbell 2y + 1 (NVMe base specification, doorbell stride). */
static uint64_t doorbell(const struct nvmet *t, unsigned index)
{
    return t->bar + 0x1000 + (uint64_t)index * (4u << t->stride);
}
static int in_extent(const struct nvmet *t, uint64_t addr, uint64_t len)
{
    return addr >= t->bus_base && len <= EXTENT && addr - t->bus_base <= EXTENT - len;
}
static void clear(void *p, uint64_t n)
{
    volatile uint8_t *b = p;
    for (uint64_t i = 0; i < n; i++) b[i] = 0;
}
static int wrong_stage(const struct nvmet *t)
{
    return t->stage == NVMET_ST_FAILED || t->stage == NVMET_ST_STOPPED || t->stage == NVMET_ST_HANDED_OFF ? NVMET_E_DEAD
                                                                                                           : NVMET_E_STATE;
}

/* A bounded wait on CSTS. All ones means no device. CFS ends only the waits that ask for it: the reset we are waiting
 * for is what clears it. The poll cap ends the wait of a counter that does not advance. */
static int wait_csts(const struct nvmeh_ops *o, struct nvmet *t, uint32_t mask, uint32_t want, uint64_t limit,
                     int timeout_error, int cfs_is_fatal)
{
    uint64_t start = NOW(o);
    for (uint32_t i = 0; i < NVMET_POLL_MAX; i++) {
        uint32_t csts = RD(o, t->bar + NVMEH_CSTS);
        t->csts = csts;
        t->polls++;
        if (csts == 0xffffffffu) return NVMET_E_GONE;
        if (cfs_is_fatal && (csts & CSTS_CFS)) return NVMET_E_CFS;
        if ((csts & mask) == want) return 0;
        if (NOW(o) - start > limit) return timeout_error;
    }
    return NVMET_E_CLOCK;
}

/* The controller may DMA until RDY reads 0 again: into the extent after our write of CC.EN 1, and into the firmware's own
 * queues while the firmware's CC.EN or RDY was still set. If a CC 0 cannot be followed by RDY 0 the flags stay, and the
 * caller must treat the extent, the firmware's queue memory and the run as lost; nvmet_abort tries again. */
static int disable(const struct nvmeh_ops *o, struct nvmet *t)
{
    int e;
    WR(o, t->bar + NVMEH_CC, 0);
    t->flags |= NVMET_F_REGS_WRITTEN;
    e = wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0);
    if (!e) t->flags &= ~(uint64_t)(NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE);
    return e;
}
static int fatal(const struct nvmeh_ops *o, struct nvmet *t, int e)
{
    if (t->stage != NVMET_ST_FAILED) {
        if (t->flags & NVMET_F_DMA_LIVE) disable(o, t);
        t->stage = NVMET_ST_FAILED;
        t->last_error = (uint64_t)(int64_t)e;
    }
    return e;
}

int nvmet_init(struct nvmet *t, const struct nvmet_config *c)
{
    uint64_t cpu_end = c->cpu_base + EXTENT, bus_end = c->bus_base + EXTENT;
    int e = NVMET_E_CONFIG;
    clear(t, sizeof *t);
    t->stage = NVMET_ST_FAILED;
    if ((c->flags & ~(uint64_t)NVMET_C_ALL) || c->reserved || !c->bar || (c->bar & 0x3fff) ||
        c->bar >= NVMEH_SP11_IPA_LIMIT ||
        !c->cpu_base || (c->cpu_base & (EXTENT - 1)) || cpu_end < c->cpu_base || cpu_end > NVMEH_SP11_IPA_LIMIT ||
        !c->bus_base || (c->bus_base & (EXTENT - 1)) || bus_end < c->bus_base ||
        (!!(c->flags & NVMET_C_TRANSLATED) != (c->bus_base != c->cpu_base)) ||
        (c->bar < cpu_end && c->cpu_base < c->bar + 0x4000) ||
        !c->ticks_per_s || c->ticks_per_s > TICKS_PER_S_MAX ||
        c->max_transfer < NVMET_PAGE || c->max_transfer > NVMEH_DATA_BYTES || (c->max_transfer % NVMET_LBA_BYTES))
        goto out;
    e = NVMET_E_GUEST;
    if (c->cpu_base < c->mem_high && c->mem_low < cpu_end) goto out;
    e = NVMET_E_DMA_UNPROVEN;
    if (!(c->flags & NVMET_C_DMA_PROVEN)) goto out;
    t->bar = c->bar; t->cpu_base = c->cpu_base; t->bus_base = c->bus_base; t->ticks_per_s = c->ticks_per_s;
    t->max_transfer = c->max_transfer; t->eui64_pin = c->eui64_pin;
    t->cmd_ticks = NVMET_CMD_SECONDS * c->ticks_per_s;
    t->shutdown_ticks = NVMET_SHUTDOWN_SECONDS * c->ticks_per_s;
    t->stage = NVMET_ST_NEW;
    return 0;
out:
    t->last_error = (uint64_t)(int64_t)e;
    return e;
}

int nvmet_quiesce(const struct nvmeh_ops *o, struct nvmet *t)
{
    uint64_t cap, to;
    uint32_t cc, csts;
    int e;
    if (t->stage != NVMET_ST_NEW) return wrong_stage(t);
    /* The arrival state is read, and the flag set, before any refusal. A refusal writes nothing, so the firmware's
     * controller is left exactly as found and still runs on its own queues: "both flags clear" must not be read as
     * "nothing is running". An all-ones read (no device, or a link that is down) has CC.EN and RDY set too, so it is
     * flagged as well: the state is unknown and the firmware's queue memory stays in use as far as we can show. */
    cap = rd64(o, t->bar + NVMEH_CAP);
    t->vs = RD(o, t->bar + REG_VS);
    cc = RD(o, t->bar + NVMEH_CC);
    csts = RD(o, t->bar + NVMEH_CSTS);
    t->cc_before = cc; t->csts_before = csts;
    t->aqa_before = RD(o, t->bar + NVMEH_AQA);
    t->asq_before = rd64(o, t->bar + NVMEH_ASQ_REG);
    t->acq_before = rd64(o, t->bar + NVMEH_ACQ_REG);
    if ((cc & 1) || (csts & CSTS_RDY)) t->flags |= NVMET_F_FW_QUEUES_LIVE;
    if ((uint32_t)cap == 0xffffffffu || (uint32_t)(cap >> 32) == 0xffffffffu) return fatal(o, t, NVMET_E_GONE);
    t->cap = cap;
    t->stride = (cap >> 32) & 0xf;
    to = (cap >> 24) & 0xff;
    t->rdy_ticks = (to ? to : 1) * t->ticks_per_s / 2;       /* CAP.TO counts 500 ms units; 0 is read as one */
    if (!((cap >> 37) & 1) || ((cap >> 48) & 0xf) || t->stride > 4) return fatal(o, t, NVMET_E_CAP);
    if ((cap & 0xffff) + 1 < NVMEH_IO_ENTRIES) return fatal(o, t, NVMET_E_MQES);
    if (csts == 0xffffffffu) return fatal(o, t, NVMET_E_GONE);
    if (csts & CSTS_CFS) return fatal(o, t, NVMET_E_FATAL_ENTRY);
    if (cc & 1) {
        if (csts & CSTS_RDY) {
            /* The firmware's driver may have written; a normal shutdown lets the controller flush before it resets. The
             * SHN field is replaced, not or-ed: 10b (abrupt) from the firmware would turn into the reserved 11b. */
            WR(o, t->bar + NVMEH_CC, (cc & ~NVMET_CC_SHN_MASK) | NVMET_CC_SHN_NORMAL);
            t->flags |= NVMET_F_REGS_WRITTEN;
            e = wait_csts(o, t, CSTS_SHST_MASK, CSTS_SHST_DONE, t->shutdown_ticks, NVMET_E_T_SHUTDOWN, 0);
            if (e) return fatal(o, t, e);
        }
        WR(o, t->bar + NVMEH_CC, 0);
        t->flags |= NVMET_F_REGS_WRITTEN;
        e = wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0);
        if (e) return fatal(o, t, e);
    } else if (csts & CSTS_RDY) {                              /* a reset that has not finished */
        e = wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0);
        if (e) return fatal(o, t, e);
    }
    t->flags &= ~(uint64_t)NVMET_F_FW_QUEUES_LIVE;             /* RDY 0 was seen: the firmware's queues are nobody's now */
    t->stage = NVMET_ST_QUIESCED;
    return 0;
}

static void zero_page(const struct nvmeh_ops *o, const struct nvmet *t, uint64_t off)
{
    for (uint64_t i = 0; i < NVMET_PAGE; i += 4) WR(o, cpu(t, off + i), 0);
    CLEAN(o, cpu(t, off), NVMET_PAGE);
}

/* One command on queue q and its one completion. Pre-submission refusals (opcode, PRP) send nothing and leave the
 * controller alone; every failure after the doorbell disables the controller and closes it, except an error status,
 * which is the controller's answer and leaves the queue usable (the caller decides). */
static int cmd(const struct nvmeh_ops *o, struct nvmet *t, struct nvmet_queue *q, unsigned opcode, uint32_t nsid,
               uint64_t prp1, uint64_t prp2, const uint32_t cdw[6], uint32_t *result)
{
    uint64_t sqe = cpu(t, q->sq_off + (uint64_t)q->tail * 64), cqe = cpu(t, q->cq_off + (uint64_t)q->head * 16), start;
    uint32_t cid = q->next_cid, dw0, dw2, dw3, status;
    int dup;
    if (q->qid ? nvmet_io_gate(opcode) : nvmeh_sp11_admin_verdict(NVMEH_SP11_PHASE_TAKEOVER, opcode, cdw[0])) {
        t->refused++;
        t->last_opcode = opcode;
        return NVMET_E_OPCODE;
    }
    if ((prp1 && !in_extent(t, prp1, 1)) || (prp2 && !in_extent(t, prp2, 1))) {
        t->refused++;
        return NVMET_E_PRP;
    }
    q->next_cid = q->next_cid % 0xffff + 1;
    const uint32_t w[16] = {
        opcode | cid << 16, nsid, 0, 0, 0, 0, (uint32_t)prp1, (uint32_t)(prp1 >> 32),
        (uint32_t)prp2, (uint32_t)(prp2 >> 32), cdw[0], cdw[1], cdw[2], cdw[3], cdw[4], cdw[5],
    };
    for (unsigned i = 0; i < 16; i++) WR(o, sqe + 4 * i, w[i]);
    CLEAN(o, sqe, 64);                                         /* the SQE reaches the point of coherency before the bell */
    q->tail = (q->tail + 1) % q->entries;
    t->last_opcode = opcode;
    t->commands++;
    WR(o, doorbell(t, 2 * q->qid), q->tail);
    start = NOW(o);
    for (uint32_t n = 0;; n++) {
        CLEAN(o, cqe, 16);
        dw3 = RD(o, cqe + 12);
        t->polls++;
        if (((dw3 >> 16) & 1) == q->phase) break;              /* an entry of the other phase is not an entry */
        if (NOW(o) - start > t->cmd_ticks) {
            t->csts = RD(o, t->bar + NVMEH_CSTS);
            return fatal(o, t, t->csts == 0xffffffffu ? NVMET_E_GONE : (t->csts & CSTS_CFS) ? NVMET_E_CFS : NVMET_E_T_CMD);
        }
        if (n >= NVMET_POLL_MAX) return fatal(o, t, NVMET_E_CLOCK);
    }
    dw2 = RD(o, cqe + 8);
    dw0 = RD(o, cqe);
    q->head = (q->head + 1) % q->entries;
    if (!q->head) q->phase ^= 1;
    WR(o, doorbell(t, 2 * q->qid + 1), q->head);
    /* One command is in flight, so a second valid entry behind the consumed one can only be a duplicate. */
    CLEAN(o, cpu(t, q->cq_off + (uint64_t)q->head * 16), 16);
    dup = ((RD(o, cpu(t, q->cq_off + (uint64_t)q->head * 16) + 12) >> 16) & 1) == q->phase;
    status = (dw3 >> 17) & 0x7fff;
    if (dup) return fatal(o, t, NVMET_E_DUP);
    if ((dw3 & 0xffff) != cid) return fatal(o, t, NVMET_E_CID);
    if ((dw2 >> 16) != q->qid || (dw2 & 0xffff) >= q->entries) return fatal(o, t, NVMET_E_QID);
    if (status) {
        t->errors++;
        t->last_status = (uint64_t)status << 16 | cid;
        return NVMET_E_STATUS;
    }
    if (result) *result = dw0;
    return 0;
}

/* A failure of a bring-up or stop step: the controller is ours, so it is disabled whatever the cause. */
static int step(const struct nvmeh_ops *o, struct nvmet *t, int e)
{
    return e ? fatal(o, t, e) : 0;
}

int nvmet_enable(const struct nvmeh_ops *o, struct nvmet *t)
{
    int e;
    if (t->stage != NVMET_ST_QUIESCED) return wrong_stage(t);
    for (uint64_t off = NVMEH_ASQ; off <= NVMET_IDENT; off += NVMET_PAGE) zero_page(o, t, off);
    /* A loader's stores into the PRP page and the data buffer may still be dirty in the CPU's cache; a write-back that
     * comes after the controller's DMA would replace what it wrote. Nothing in these pages is ours yet. */
    CLEAN(o, cpu(t, NVMEH_PRP), NVMEH_DATA + NVMEH_DATA_BYTES - NVMEH_PRP);
    t->adm = (struct nvmet_queue){NVMEH_ASQ, NVMEH_ACQ, 0, NVMEH_ADMIN_ENTRIES, 0, 0, 1, 1};
    t->flags |= NVMET_F_REGS_WRITTEN;
    WR(o, t->bar + NVMEH_AQA, (NVMEH_ADMIN_ENTRIES - 1) << 16 | (NVMEH_ADMIN_ENTRIES - 1));
    wr64(o, t->bar + NVMEH_ASQ_REG, bus(t, NVMEH_ASQ));
    wr64(o, t->bar + NVMEH_ACQ_REG, bus(t, NVMEH_ACQ));
    t->flags |= NVMET_F_DMA_LIVE;                              /* live from the write: its effect is not yet known */
    WR(o, t->bar + NVMEH_CC, NVMET_CC_ENABLE);
    e = step(o, t, wait_csts(o, t, CSTS_RDY, CSTS_RDY, t->rdy_ticks, NVMET_E_T_RDY, 1));
    if (e) return e;
    t->stage = NVMET_ST_ENABLED;
    return 0;
}

static uint32_t id32(const struct nvmeh_ops *o, const struct nvmet *t, unsigned off)
{
    return RD(o, cpu(t, NVMET_IDENT + off));
}
static uint32_t id8(const struct nvmeh_ops *o, const struct nvmet *t, unsigned off)
{
    return (id32(o, t, off & ~3u) >> (8 * (off & 3u))) & 0xff;
}
static uint64_t id64(const struct nvmeh_ops *o, const struct nvmet *t, unsigned off)
{
    return id32(o, t, off) | (uint64_t)id32(o, t, off + 4) << 32;
}
/* Eight bytes in page order as one big-endian integer: the order of nvme_m3.py's EXPECT_EUI64 string and of the SP11
 * record fixtures (tools/fixtures/sp11/nvmewindow.py), so a pin taken from the survey compares as it stands. */
static uint64_t id64_be(const struct nvmeh_ops *o, const struct nvmet *t, unsigned off)
{
    uint64_t v = 0;
    for (unsigned i = 0; i < 8; i++) v = v << 8 | id8(o, t, off + i);
    return v;
}

/* One Identify into the zeroed page. A page that is still all zero afterwards means the controller's DMA never landed
 * (an SMMU that did not map the page, or nothing behind the command) or that nothing is there: both are refusals. */
static int identify_page(const struct nvmeh_ops *o, struct nvmet *t, uint32_t cns, uint32_t nsid)
{
    uint32_t cdw[6] = {cns, 0, 0, 0, 0, 0}, i = 0;
    int e;
    zero_page(o, t, NVMET_IDENT);
    e = cmd(o, t, &t->adm, NVMET_ADMIN_IDENTIFY, nsid, bus(t, NVMET_IDENT), 0, cdw, 0);
    if (e) return e;
    CLEAN(o, cpu(t, NVMET_IDENT), NVMET_PAGE);
    while (i < NVMET_PAGE / 4 && !id32(o, t, 4 * i)) i++;
    return i == NVMET_PAGE / 4 ? NVMET_E_ID_ZERO : 0;
}

static int parse_controller(const struct nvmeh_ops *o, struct nvmet *t)
{
    uint64_t limit;
    t->vid = id32(o, t, 0) & 0xffff;
    t->mdts = id8(o, t, 77);
    t->nn = id32(o, t, 516);
    t->sqes = id8(o, t, 512);
    t->cqes = id8(o, t, 513);
    t->oncs = id32(o, t, 520) & 0xffff;
    t->vwc = id8(o, t, 525);
    /* Reported only, in 4 KiB units: the takeover never sets a host memory buffer, so a controller that wants one shows
     * here first (docs/SP11-STORAGE-PROVISIONING.md, D16). */
    t->hmpre = id32(o, t, 272);
    t->hmmin = id32(o, t, 276);
    if (!t->nn) return NVMET_E_ID_CTRL;
    /* SQES/CQES: bits 3:0 the required and 7:4 the maximum entry size, as log2; CC asks for 64 and 16 bytes. */
    if ((t->sqes & 0xf) > 6 || (t->sqes >> 4) < 6 || (t->cqes & 0xf) > 4 || (t->cqes >> 4) < 4) return NVMET_E_ID_CTRL;
    /* MDTS counts 4 KiB pages as a power of two (CAP.MPSMIN is 4 KiB here); 0 is no limit. */
    limit = t->mdts && t->mdts < 20 ? (uint64_t)NVMET_PAGE << t->mdts : NVMEH_DATA_BYTES;
    t->ctrl_max_transfer = limit;
    if (limit < t->max_transfer) t->max_transfer = limit;
    return 0;
}

static int parse_namespace(const struct nvmeh_ops *o, struct nvmet *t)
{
    uint64_t idx;
    uint32_t lbaf;
    t->nsze = id64(o, t, 0);
    t->ncap = id64(o, t, 8);
    t->nuse = id64(o, t, 16);
    t->nlbaf = id8(o, t, 25);
    t->flbas = id8(o, t, 26);
    idx = t->flbas & 0xf;
    if (t->nlbaf > 15) idx |= (uint64_t)((t->flbas >> 5) & 3) << 4;   /* the upper index bits exist past 16 formats */
    if (!t->nsze || t->nsze > 1ull << 55 || idx > t->nlbaf) return NVMET_E_ID_NS;
    lbaf = id32(o, t, 128 + 4 * (unsigned)idx);
    t->metadata = lbaf & 0xffff;
    t->lba_shift = (lbaf >> 16) & 0xff;
    if (t->lba_shift != 9) return NVMET_E_LBA_SIZE;                   /* a 4096-byte format is refused, never selected */
    if (t->metadata) return NVMET_E_METADATA;
    t->eui64 = id64_be(o, t, 120);
    t->nguid_lo = id64(o, t, 104);
    t->nguid_hi = id64(o, t, 112);
    if (t->eui64_pin && t->eui64 != t->eui64_pin) return NVMET_E_EUI64;
    return 0;
}

int nvmet_identify(const struct nvmeh_ops *o, struct nvmet *t)
{
    int e;
    if (t->stage != NVMET_ST_ENABLED && t->stage != NVMET_ST_IDENTIFIED) return wrong_stage(t);
    e = identify_page(o, t, 1, 0);
    if (!e) e = parse_controller(o, t);
    if (!e) e = identify_page(o, t, 0, NVMET_NSID);
    if (!e) e = parse_namespace(o, t);
    e = step(o, t, e);
    if (e) return e;
    t->stage = NVMET_ST_IDENTIFIED;
    return 0;
}

int nvmet_ioq(const struct nvmeh_ops *o, struct nvmet *t)
{
    uint32_t qid_size = (NVMEH_IO_ENTRIES - 1) << 16 | 1, result = 0;
    uint32_t features[6] = {NVMET_FEAT_NUMBER_OF_QUEUES, 0, 0, 0, 0, 0};          /* one SQ and one CQ, 0-based */
    uint32_t cq[6] = {qid_size, 1, 0, 0, 0, 0};                                    /* PC, no interrupt, vector 0 */
    uint32_t sq[6] = {qid_size, 1u << 16 | 1, 0, 0, 0, 0};                         /* PC, priority 0, completion queue 1 */
    int e;
    if (t->stage != NVMET_ST_IDENTIFIED) return wrong_stage(t);
    e = cmd(o, t, &t->adm, NVMET_ADMIN_SET_FEATURES, 0, 0, 0, features, &result);
    t->qalloc = result;
    if (!e) e = cmd(o, t, &t->adm, NVMET_ADMIN_CREATE_CQ, 0, bus(t, NVMEH_IOCQ), 0, cq, 0);
    if (!e) e = cmd(o, t, &t->adm, NVMET_ADMIN_CREATE_SQ, 0, bus(t, NVMEH_IOSQ), 0, sq, 0);
    e = step(o, t, e);
    if (e) return e;
    t->io = (struct nvmet_queue){NVMEH_IOSQ, NVMEH_IOCQ, 1, NVMEH_IO_ENTRIES, 0, 0, 1, 1};
    t->stage = NVMET_ST_READY;
    return 0;
}

int nvmet_takeover(const struct nvmeh_ops *o, struct nvmet *t, const struct nvmet_config *c)
{
    int e = nvmet_init(t, c);
    if (!e) e = nvmet_quiesce(o, t);
    if (!e) e = nvmet_enable(o, t);
    if (!e) e = nvmet_identify(o, t);
    if (!e) e = nvmet_ioq(o, t);
    return e;
}

int nvmet_io_gate(unsigned opcode)
{
    return opcode == NVMET_IO_READ ? 0 : NVMET_E_OPCODE;
}

int nvmet_prp(const struct nvmet *t, uint64_t buf_off, uint64_t bytes, uint64_t *prp1, uint64_t *prp2)
{
    uint64_t pages;
    if (!bytes || (buf_off & (NVMET_PAGE - 1)) || buf_off > NVMEH_DATA_BYTES || bytes > NVMEH_DATA_BYTES - buf_off)
        return NVMET_E_PRP;
    pages = (bytes + NVMET_PAGE - 1) / NVMET_PAGE;
    *prp1 = bus(t, NVMEH_DATA + buf_off);
    *prp2 = pages == 2 ? *prp1 + NVMET_PAGE : pages > 2 ? bus(t, NVMEH_PRP) : 0;
    return 0;
}

int nvmet_read(const struct nvmeh_ops *o, struct nvmet *t, uint64_t lba, uint32_t count)
{
    uint64_t bytes = (uint64_t)count * NVMET_LBA_BYTES, prp1, prp2, pages;
    uint32_t cdw[6] = {(uint32_t)lba, (uint32_t)(lba >> 32), count - 1, 0, 0, 0};
    int e;
    if (t->stage != NVMET_ST_READY) return wrong_stage(t);
    /* Reads are bounded by the namespace and the transfer cap, not by a window: the GPT must be readable. */
    if (!count || bytes > t->max_transfer || lba >= t->nsze || count > t->nsze - lba) {
        t->refused++;
        return NVMET_E_RANGE;
    }
    e = nvmet_prp(t, 0, bytes, &prp1, &prp2);
    if (e) {
        t->refused++;
        return e;
    }
    pages = (bytes + NVMET_PAGE - 1) / NVMET_PAGE;
    if (pages > 2) {
        for (uint64_t i = 1; i < pages; i++) wr64(o, cpu(t, NVMEH_PRP + 8 * (i - 1)), bus(t, NVMEH_DATA + NVMET_PAGE * i));
        CLEAN(o, cpu(t, NVMEH_PRP), 8 * (pages - 1));
    }
    /* Clean before the command, not only after: a CPU store since the last read (or a loader's) leaves a dirty line that
     * the hardware may write back over the data the controller is about to land. */
    CLEAN(o, cpu(t, NVMEH_DATA), bytes);
    e = cmd(o, t, &t->io, NVMET_IO_READ, NVMET_NSID, prp1, prp2, cdw, 0);
    if (e) return e;
    CLEAN(o, cpu(t, NVMEH_DATA), bytes);                       /* drop lines fetched before the DMA landed */
    t->reads++;
    t->read_bytes += bytes;
    return 0;
}

int nvmet_stop(const struct nvmeh_ops *o, struct nvmet *t)
{
    uint32_t qid[6] = {1, 0, 0, 0, 0, 0};
    int e = 0;
    if (t->stage != NVMET_ST_ENABLED && t->stage != NVMET_ST_IDENTIFIED && t->stage != NVMET_ST_READY)
        return wrong_stage(t);
    if (t->stage == NVMET_ST_READY) {                          /* the SQ goes before the CQ it reports to */
        e = cmd(o, t, &t->adm, NVMET_ADMIN_DELETE_SQ, 0, 0, 0, qid, 0);
        if (!e) e = cmd(o, t, &t->adm, NVMET_ADMIN_DELETE_CQ, 0, 0, 0, qid, 0);
        e = step(o, t, e);
        if (e) return e;
    }
    WR(o, t->bar + NVMEH_CC, NVMET_CC_ENABLE | NVMET_CC_SHN_NORMAL);
    e = step(o, t, wait_csts(o, t, CSTS_SHST_MASK, CSTS_SHST_DONE, t->shutdown_ticks, NVMET_E_T_SHUTDOWN, 0));
    if (e) return e;
    WR(o, t->bar + NVMEH_CC, 0);
    e = step(o, t, wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0));
    if (e) return e;
    t->flags &= ~(uint64_t)NVMET_F_DMA_LIVE;
    t->stage = NVMET_ST_STOPPED;
    return 0;
}

int nvmet_make_params(struct nvmet *t, uint64_t first_lba, uint64_t last_lba, uint64_t timeout_ticks, struct nvmeh_params *p)
{
    if (t->stage != NVMET_ST_READY) return wrong_stage(t);
    if (t->bus_base != t->cpu_base) return NVMET_E_NOT_IDENTITY;
    if (first_lba > last_lba || last_lba >= t->nsze || !timeout_ticks) return NVMET_E_RANGE;
    clear(p, sizeof *p);
    p->flags = NVMEH_F_ENABLE; p->bar = t->bar; p->stride = t->stride; p->dma_base = t->cpu_base;
    p->io_tail = t->io.tail; p->io_head = t->io.head; p->io_phase = t->io.phase; p->io_next_cid = t->io.next_cid;
    p->lba_bytes = NVMET_LBA_BYTES; p->first_lba = first_lba; p->last_lba = last_lba;
    p->max_transfer = t->max_transfer; p->timeout_ticks = timeout_ticks;
    /* The serving code moves the queue's tail, head and phase from here: a read or stop of ours would use stale ones. */
    t->stage = NVMET_ST_HANDED_OFF;
    return 0;
}

int nvmet_abort(const struct nvmeh_ops *o, struct nvmet *t)
{
    int e = 0;
    if (t->flags & (NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE)) e = disable(o, t);
    if (t->stage != NVMET_ST_STOPPED) t->stage = NVMET_ST_FAILED;
    return e;
}

const char *nvmet_strerror(int code)
{
    switch (code) {
    case 0: return "ok";
    case NVMET_E_CONFIG: return "a config word is out of contract";
    case NVMET_E_GUEST: return "the DMA extent overlaps guest memory";
    case NVMET_E_DMA_UNPROVEN: return "the DMA extent was not proven reachable for the controller; nothing was touched";
    case NVMET_E_GONE: return "all ones from the controller: no device behind the BAR";
    case NVMET_E_CAP: return "CAP: no NVM command set, a page size above 4 KiB or a doorbell stride above 4";
    case NVMET_E_MQES: return "CAP.MQES allows fewer entries than the queues";
    case NVMET_E_FATAL_ENTRY: return "CSTS.CFS was already set on arrival; nothing was written";
    case NVMET_E_T_SHUTDOWN: return "CSTS.SHST never reached shutdown complete";
    case NVMET_E_T_QUIESCE: return "CSTS.RDY never cleared after CC.EN 0";
    case NVMET_E_T_RDY: return "CSTS.RDY never set after CC.EN 1";
    case NVMET_E_CFS: return "CSTS.CFS during bring-up or a command";
    case NVMET_E_T_CMD: return "no valid completion within the command timeout";
    case NVMET_E_CID: return "a completion for another command identifier";
    case NVMET_E_DUP: return "a second valid completion where one command was in flight";
    case NVMET_E_QID: return "a completion for another queue or a head pointer outside the queue";
    case NVMET_E_STATUS: return "the completion carried an error status";
    case NVMET_E_ID_ZERO: return "Identify returned an all-zero page";
    case NVMET_E_ID_CTRL: return "Identify controller: no namespace, or entry sizes that exclude 64 and 16 bytes";
    case NVMET_E_ID_NS: return "Identify namespace: zero or impossible size, or a format index past NLBAF";
    case NVMET_E_LBA_SIZE: return "the current LBA format is not 512 bytes";
    case NVMET_E_METADATA: return "the current LBA format carries metadata";
    case NVMET_E_EUI64: return "the namespace is not the pinned one";
    case NVMET_E_OPCODE: return "the opcode is not on an allow-list; nothing was submitted";
    case NVMET_E_RANGE: return "a read outside the namespace, empty or over the maximum transfer";
    case NVMET_E_PRP: return "a PRP outside the data buffer or the PRP page";
    case NVMET_E_STATE: return "a call in the wrong stage";
    case NVMET_E_DEAD: return "the unit is closed: an earlier failure, a stop, an abort or the hand-off";
    case NVMET_E_CLOCK: return "the counter did not advance within the poll cap";
    case NVMET_E_NOT_IDENTITY: return "the controller's address of the extent is not the CPU's";
    }
    return "unknown takeover code";
}

#ifdef NVMET_TEST_HOOKS
int nvmet_test_command(const struct nvmeh_ops *o, struct nvmet *t, unsigned qid, unsigned opcode, uint64_t prp1,
                       uint64_t prp2, uint32_t cdw10)
{
    uint32_t cdw[6] = {cdw10, 0, 0, 0, 0, 0};
    if (t->stage != NVMET_ST_READY) return wrong_stage(t);
    return cmd(o, t, qid ? &t->io : &t->adm, opcode, qid ? NVMET_NSID : 0, prp1, prp2, cdw, 0);
}
#endif
#endif
