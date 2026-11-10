/* SPDX-License-Identifier: MIT */
/* See nvme-host.h. No libc: this links into the monitor and the native test. */
#include "nvme-host.h"

#if NVMEH_DIRECT
/* The monitor: q1n1's EL2 identity map reaches the BAR and DRAM words alike. */
#define RD(o, pa)    ((void)(o), *(volatile uint32_t *)(uintptr_t)(pa))
#define WR(o, pa, v) ((void)(o), *(volatile uint32_t *)(uintptr_t)(pa) = (uint32_t)(v))
static void clean(const struct nvmeh_ops *o, uint64_t pa, uint64_t size)
{
    (void)o;
    for (uint64_t line = pa & ~63UL; line < pa + size; line += 64)
        __asm__ volatile("dc civac, %0" :: "r"(line) : "memory");
    __asm__ volatile("dsb sy" ::: "memory");
}
static uint64_t now(const struct nvmeh_ops *o)
{
    uint64_t t;
    (void)o;
    __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t) :: "memory");
    return t;
}
#define CLEAN(o, pa, size) clean(o, pa, size)
#define NOW(o) now(o)
#else
#define RD(o, pa)    ((o)->rd((o)->ctx, (pa)))
#define WR(o, pa, v) ((o)->wr((o)->ctx, (pa), (uint32_t)(v)))
#define CLEAN(o, pa, size) ((o)->clean((o)->ctx, (pa), (size)))
#define NOW(o) ((o)->now((o)->ctx))
#endif

#define PAGE 4096u
#define CSTS_RDY 1u
#define CSTS_CFS 2u

static uint64_t rd64(const struct nvmeh_ops *o, uint64_t pa)
{
    return RD(o, pa) | (uint64_t)RD(o, pa + 4) << 32;
}

int nvmeh_adopt(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
                uint64_t mem_low, uint64_t mem_high)
{
    uint64_t end = p->dma_base + NVMEH_WINDOW_BYTES;
    int e = NVMEH_E_PARAMS;
    /* What a wrong word would turn into a stray DMA or a read outside the window. */
    if ((p->flags & ~(uint64_t)NVMEH_F_ALL) || !(p->flags & NVMEH_F_ENABLE) || !p->bar || (p->bar & 0x3fff) ||
        p->stride > 4 || !p->dma_base || (p->dma_base & 0xfffff) || end < p->dma_base ||
        p->io_tail >= NVMEH_IO_ENTRIES || p->io_head >= NVMEH_IO_ENTRIES || p->io_phase > 1 ||
        !p->io_next_cid || p->io_next_cid > 0xffff || p->lba_bytes != 512 ||
        p->first_lba > p->last_lba || p->last_lba == UINT64_MAX ||
        p->max_transfer < PAGE || !p->timeout_ticks || p->reserved ||
        ((p->flags & NVMEH_F_WRITE) ?
            (p->write_first_lba < p->first_lba || p->write_last_lba > p->last_lba ||
             p->write_first_lba > p->write_last_lba) :
            (p->write_first_lba || p->write_last_lba)))
        goto out;
    e = NVMEH_E_GUEST;
    if (p->dma_base < mem_high && mem_low < end) goto out;
    e = NVMEH_E_CTRL;
    uint32_t cc = RD(o, p->bar + NVMEH_CC), csts = RD(o, p->bar + NVMEH_CSTS);
    s->csts = csts;
    if (!(cc & 1) || !(csts & CSTS_RDY) || (csts & CSTS_CFS)) goto out;
    e = NVMEH_E_QUEUES;
    if (RD(o, p->bar + NVMEH_AQA) != ((NVMEH_ADMIN_ENTRIES - 1) << 16 | (NVMEH_ADMIN_ENTRIES - 1)) ||
        rd64(o, p->bar + NVMEH_ASQ_REG) != p->dma_base + NVMEH_ASQ ||
        rd64(o, p->bar + NVMEH_ACQ_REG) != p->dma_base + NVMEH_ACQ) goto out;
    s->tail = p->io_tail; s->head = p->io_head; s->phase = p->io_phase; s->next_cid = p->io_next_cid;
    s->status |= NVMEH_S_ADOPTED;
    e = 0;
out:
    s->prepare = (uint64_t)(int64_t)e;
    return e;
}

static uint64_t doorbell(const struct nvmeh_params *p, unsigned index)
{
    return p->bar + 0x1000 + (uint64_t)index * (4u << p->stride);
}

static int command(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
                    unsigned op, uint64_t wlba, uint32_t count, uint32_t at)
{
    if (!(s->status & NVMEH_S_ADOPTED) || (s->status & NVMEH_S_FAILED)) return NVMEH_E_DEAD;
    if (op == 1 && !(p->flags & NVMEH_F_WRITE)) { s->refused++; return NVMEH_E_READONLY; }
    uint64_t bytes = (uint64_t)count * p->lba_bytes, span = p->last_lba - p->first_lba + 1;
    if (op && (!count || wlba >= span || count > span - wlba || (at & (PAGE - 1)) ||
        bytes > p->max_transfer || at > NVMEH_DATA_BYTES || bytes > NVMEH_DATA_BYTES - at)) {
        s->refused++;
        return NVMEH_E_RANGE;
    }
    uint64_t lba = op ? p->first_lba + wlba : 0;
    if (op == 1 && (lba < p->write_first_lba || lba > p->write_last_lba ||
                    count - 1 > p->write_last_lba - lba)) {
        s->refused++;
        return NVMEH_E_RANGE;
    }
    s->csts = RD(o, p->bar + NVMEH_CSTS);
    if (!(s->csts & CSTS_RDY) || (s->csts & CSTS_CFS)) {
        s->errors++; s->status |= NVMEH_S_FAILED;
        return NVMEH_E_CTRL;
    }
    uint64_t data = op ? p->dma_base + NVMEH_DATA + at : 0;
    uint64_t pages = (bytes + PAGE - 1) / PAGE, prp2 = 0;
    if (pages == 2) prp2 = data + PAGE;
    else if (pages > 2) {
        uint64_t list = p->dma_base + NVMEH_PRP;
        for (uint64_t i = 1; i < pages; i++) {
            WR(o, list + 8 * (i - 1), (uint32_t)(data + PAGE * i));
            WR(o, list + 8 * (i - 1) + 4, (uint32_t)((data + PAGE * i) >> 32));
        }
        CLEAN(o, list, 8 * (pages - 1));
        prp2 = list;
    }
    /* Publish writes; leave no dirty line over a pending SSD read DMA. */
    if (op) CLEAN(o, data, bytes);
    uint32_t cid = (uint32_t)s->next_cid;
    s->next_cid = s->next_cid % 0xffff + 1;
    uint64_t sqe = p->dma_base + NVMEH_IOSQ + s->tail * 64;
    const uint32_t w[16] = {
        op | cid << 16, 1, 0, 0, 0, 0, (uint32_t)data, (uint32_t)(data >> 32),
        (uint32_t)prp2, (uint32_t)(prp2 >> 32), (uint32_t)lba, (uint32_t)(lba >> 32),
        op ? (count - 1) | (op == 1 ? 1u << 30 : 0) : 0, 0, 0, 0,
    };
    for (unsigned i = 0; i < 16; i++) WR(o, sqe + 4 * i, w[i]);
    CLEAN(o, sqe, 64);
    s->tail = (s->tail + 1) % NVMEH_IO_ENTRIES;
    s->last_opcode = op;
    WR(o, doorbell(p, 2), (uint32_t)s->tail);                 /* SQ 1 tail */
    uint64_t cqe = p->dma_base + NVMEH_IOCQ + s->head * 16, start = NOW(o), ticks;
    uint32_t dw3;
    for (;;) {
        CLEAN(o, cqe, 16);
        dw3 = RD(o, cqe + 12);
        ticks = NOW(o) - start;
        if (((dw3 >> 16) & 1) == s->phase) break;
        if (ticks > p->timeout_ticks) {
            s->timeouts++;
            s->csts = RD(o, p->bar + NVMEH_CSTS);
            s->status |= NVMEH_S_FAILED;
            return NVMEH_E_TIMEOUT;
        }
    }
    s->head = (s->head + 1) % NVMEH_IO_ENTRIES;
    if (!s->head) s->phase ^= 1;
    WR(o, doorbell(p, 3), (uint32_t)s->head);                 /* CQ 1 head */
    s->total_ticks += ticks;
    if (ticks > s->max_ticks) s->max_ticks = ticks;
    uint32_t status = (dw3 >> 17) & 0x7fff, dw2 = RD(o, cqe + 8);
    int identity_bad = (dw3 & 0xffff) != cid || (dw2 >> 16) != 1 ||
                       (dw2 & 0xffff) >= NVMEH_IO_ENTRIES;
    if (identity_bad || status) {
        s->errors++;
        s->last_status = (uint64_t)status << 16 | (dw3 & 0xffff);
        s->last_lba = lba;
        if (identity_bad) s->status |= NVMEH_S_FAILED;   /* out of step: stop using the queue */
        return NVMEH_E_STATUS;
    }
    if (op == 2) {
        CLEAN(o, data, bytes);                /* drop lines fetched before the DMA landed */
        s->reads++; s->read_bytes += bytes;
    } else if (op == 1) { s->writes++; s->write_bytes += bytes; }
    else s->flushes++;
    s->last_lba = lba;
    return 0;
}

int nvmeh_read(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
               uint64_t wlba, uint32_t count, uint32_t at)
{
    return command(o, p, s, 2, wlba, count, at);
}

int nvmeh_write(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
                uint64_t wlba, uint32_t count, uint32_t at)
{
    return command(o, p, s, 1, wlba, count, at);
}

int nvmeh_flush(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s)
{
    return command(o, p, s, 0, 0, 0, 0);
}

void nvmeh_stop(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s)
{
    if (s->status & NVMEH_S_ADOPTED) s->csts = RD(o, p->bar + NVMEH_CSTS);
}

#if SNAP_ANS_ASYNC
/* command() above, split at the doorbell (nvme-host.h). Statement for statement the same up to the SQ tail
 * doorbell; the flight keeps what the completion half needs. */
int nvmeh_submit(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
                 struct nvmeh_flight *f, unsigned op, uint64_t wlba, uint32_t count, uint32_t at)
{
    if (f->active) return NVMEH_E_BUSY;
    if (!(s->status & NVMEH_S_ADOPTED) || (s->status & NVMEH_S_FAILED)) return NVMEH_E_DEAD;
    if (op == 1 && !(p->flags & NVMEH_F_WRITE)) { s->refused++; return NVMEH_E_READONLY; }
    uint64_t bytes = (uint64_t)count * p->lba_bytes, span = p->last_lba - p->first_lba + 1;
    if (op && (!count || wlba >= span || count > span - wlba || (at & (PAGE - 1)) ||
        bytes > p->max_transfer || at > NVMEH_DATA_BYTES || bytes > NVMEH_DATA_BYTES - at)) {
        s->refused++;
        return NVMEH_E_RANGE;
    }
    uint64_t lba = op ? p->first_lba + wlba : 0;
    if (op == 1 && (lba < p->write_first_lba || lba > p->write_last_lba ||
                    count - 1 > p->write_last_lba - lba)) {
        s->refused++;
        return NVMEH_E_RANGE;
    }
    s->csts = RD(o, p->bar + NVMEH_CSTS);
    if (!(s->csts & CSTS_RDY) || (s->csts & CSTS_CFS)) {
        s->errors++; s->status |= NVMEH_S_FAILED;
        return NVMEH_E_CTRL;
    }
    uint64_t data = op ? p->dma_base + NVMEH_DATA + at : 0;
    uint64_t pages = (bytes + PAGE - 1) / PAGE, prp2 = 0;
    if (pages == 2) prp2 = data + PAGE;
    else if (pages > 2) {
        uint64_t list = p->dma_base + NVMEH_PRP;
        for (uint64_t i = 1; i < pages; i++) {
            WR(o, list + 8 * (i - 1), (uint32_t)(data + PAGE * i));
            WR(o, list + 8 * (i - 1) + 4, (uint32_t)((data + PAGE * i) >> 32));
        }
        CLEAN(o, list, 8 * (pages - 1));
        prp2 = list;
    }
    /* Publish writes; leave no dirty line over a pending SSD read DMA. */
    if (op) CLEAN(o, data, bytes);
    uint32_t cid = (uint32_t)s->next_cid;
    s->next_cid = s->next_cid % 0xffff + 1;
    uint64_t sqe = p->dma_base + NVMEH_IOSQ + s->tail * 64;
    const uint32_t w[16] = {
        op | cid << 16, 1, 0, 0, 0, 0, (uint32_t)data, (uint32_t)(data >> 32),
        (uint32_t)prp2, (uint32_t)(prp2 >> 32), (uint32_t)lba, (uint32_t)(lba >> 32),
        op ? (count - 1) | (op == 1 ? 1u << 30 : 0) : 0, 0, 0, 0,
    };
    for (unsigned i = 0; i < 16; i++) WR(o, sqe + 4 * i, w[i]);
    CLEAN(o, sqe, 64);
    s->tail = (s->tail + 1) % NVMEH_IO_ENTRIES;
    s->last_opcode = op;
    WR(o, doorbell(p, 2), (uint32_t)s->tail);                 /* SQ 1 tail */
    f->op = op; f->lba = lba; f->bytes = bytes; f->data = data; f->cid = cid;
    f->start = NOW(o);
    f->active = 1;
    return 0;
}

/* One pass of command()'s wait loop; past it, command()'s tail unchanged. */
int nvmeh_reap(const struct nvmeh_ops *o, const struct nvmeh_params *p, struct nvmeh_state *s,
               struct nvmeh_flight *f)
{
    if (!f->active) return NVMEH_E_BUSY;
    uint64_t cqe = p->dma_base + NVMEH_IOCQ + s->head * 16, ticks;
    CLEAN(o, cqe, 16);
    uint32_t dw3 = RD(o, cqe + 12);
    ticks = NOW(o) - f->start;
    if (((dw3 >> 16) & 1) != s->phase) {
        if (ticks <= p->timeout_ticks) return NVMEH_PENDING;
        f->active = 0;
        s->timeouts++;
        s->csts = RD(o, p->bar + NVMEH_CSTS);
        s->status |= NVMEH_S_FAILED;
        return NVMEH_E_TIMEOUT;
    }
    f->active = 0;
    s->head = (s->head + 1) % NVMEH_IO_ENTRIES;
    if (!s->head) s->phase ^= 1;
    WR(o, doorbell(p, 3), (uint32_t)s->head);                 /* CQ 1 head */
    s->total_ticks += ticks;
    if (ticks > s->max_ticks) s->max_ticks = ticks;
    uint32_t status = (dw3 >> 17) & 0x7fff, dw2 = RD(o, cqe + 8);
    int identity_bad = (dw3 & 0xffff) != f->cid || (dw2 >> 16) != 1 ||
                       (dw2 & 0xffff) >= NVMEH_IO_ENTRIES;
    if (identity_bad || status) {
        s->errors++;
        s->last_status = (uint64_t)status << 16 | (dw3 & 0xffff);
        s->last_lba = f->lba;
        if (identity_bad) s->status |= NVMEH_S_FAILED;   /* out of step: stop using the queue */
        return NVMEH_E_STATUS;
    }
    if (f->op == 2) {
        CLEAN(o, f->data, f->bytes);          /* drop lines fetched before the DMA landed */
        s->reads++; s->read_bytes += f->bytes;
    } else if (f->op == 1) { s->writes++; s->write_bytes += f->bytes; }
    else s->flushes++;
    s->last_lba = f->lba;
    return 0;
}

int nvmeh_ready(const struct nvmeh_ops *o, const struct nvmeh_params *p, const struct nvmeh_state *s)
{
    uint64_t cqe = p->dma_base + NVMEH_IOCQ + (s->head % NVMEH_IO_ENTRIES) * 16;
    CLEAN(o, cqe, 16);
    return (int)((RD(o, cqe + 12) >> 16) & 1) == (int)(s->phase & 1);
}
#endif

#if SNAP_PLATFORM_SP11
/* ---- SP11 takeover contract (nvme-host.h). Stateless, no libc, no writes. ---- */

/* ECAM puts the bus in bits 27:20, the device in 19:15 and the function in 14:12 of the offset from an allocation's
 * base (PCI Express Base Specification, Enhanced Configuration Access Mechanism). VERIFIED in this tree for device 0
 * function 0: q1n1 tools/nvme_m3.py locate() adds `bus << 20`. */
static uint64_t sp11_cfg_offset(uint64_t rid)
{
    return (rid >> 8) << 20 | ((rid >> 3) & 0x1f) << 15 | (rid & 7) << 12;
}

int nvmeh_sp11_check(const struct nvmeh_sp11_target *t, const struct nvmeh_sp11_record *r, const struct nvmeh_params *p)
{
    static const struct { uint64_t bit; int error; } unknown[] = {
        {NVMEH_SP11_K_ECAM, NVMEH_E_SP11_UNKNOWN_ECAM}, {NVMEH_SP11_K_BDF, NVMEH_E_SP11_UNKNOWN_BDF},
        {NVMEH_SP11_K_SMMU, NVMEH_E_SP11_UNKNOWN_SMMU}, {NVMEH_SP11_K_STREAM, NVMEH_E_SP11_UNKNOWN_STREAM},
        {NVMEH_SP11_K_EUI64, NVMEH_E_SP11_UNKNOWN_EUI64},
        {NVMEH_SP11_K_GENERATION, NVMEH_E_SP11_UNKNOWN_GENERATION},
    };
    unsigned i;
    uint64_t first, last, size;
    if (t->abi != NVMEH_SP11_ABI || r->abi != NVMEH_SP11_ABI) return NVMEH_E_SP11_ABI;
    if ((t->known & ~(uint64_t)NVMEH_SP11_K_ALL) || (r->flags & ~(uint64_t)NVMEH_SP11_R_ALL) ||
        (t->reserved[0] | t->reserved[1] | t->reserved[2])) return NVMEH_E_SP11_RESERVED;
    for (i = 0; i < sizeof unknown / sizeof unknown[0]; i++)
        if (!(t->known & unknown[i].bit)) return unknown[i].error;
    /* A pin that is marked known but cannot be real, or that contradicts another pin, is a descriptor fault. The
     * bounds are the 36-bit physical map, the 1 MiB ECAM bus size, the 64 KiB SMMUv3 register page and the 16-bit RID
     * and 32-bit StreamID widths. Short-circuit order matters: later terms rely on earlier ones for their bounds. */
    if (t->segment > 0xffff || t->start_bus > t->end_bus || t->end_bus > 255 || !t->ecam_base ||
        (t->ecam_base & 0xfffff) || t->ecam_base >= NVMEH_SP11_IPA_LIMIT ||
        ((t->end_bus + 1) << 20) > NVMEH_SP11_IPA_LIMIT - t->ecam_base ||
        t->bdf > 0xffff || (t->bdf >> 8) < t->start_bus || (t->bdf >> 8) > t->end_bus ||
        !t->smmu_base || (t->smmu_base & 0xffff) || t->smmu_base >= NVMEH_SP11_IPA_LIMIT - 0x20000 ||
        t->rid_base > 0xffff || !t->rid_count || t->rid_count > 0x10000 - t->rid_base ||
        t->stream_base > 0xffffffffull - (t->rid_count - 1) || !t->eui64 || !t->generation)
        return NVMEH_E_SP11_TARGET_INVALID;

    if (!(r->flags & NVMEH_SP11_R_CLEAN)) return NVMEH_E_SP11_RECORD_UNCLEAN;
    if (r->generation != t->generation) return NVMEH_E_SP11_STALE;
    if (r->segment != t->segment) return NVMEH_E_SP11_SEGMENT;
    if (r->ecam_base != t->ecam_base) return NVMEH_E_SP11_ECAM;
    if (r->bdf > 0xffff) return NVMEH_E_SP11_BDF;
    if ((r->bdf >> 8) < t->start_bus || (r->bdf >> 8) > t->end_bus) return NVMEH_E_SP11_BUS_RANGE;
    if (r->bdf != t->bdf) return NVMEH_E_SP11_BDF;
    if (r->cfg_pa != t->ecam_base + sp11_cfg_offset(t->bdf)) return NVMEH_E_SP11_CFG_ADDRESS;
    if (r->class_code != NVMEH_SP11_CLASS_NVME) return NVMEH_E_SP11_CLASS;
    if (r->eui64 != t->eui64) return NVMEH_E_SP11_EUI64;
    if (r->smmu_base != t->smmu_base) return NVMEH_E_SP11_SMMU_BASE;
    if (r->smmu_mode != NVMEH_SP11_SMMU_BYPASS && r->smmu_mode != NVMEH_SP11_SMMU_S2_IDENTITY)
        return NVMEH_E_SP11_SMMU_MODE;
    if (!(r->flags & NVMEH_SP11_R_DMA_PROVEN)) return NVMEH_E_SP11_DMA_UNPROVEN;
    if (r->bdf < t->rid_base || r->bdf - t->rid_base >= t->rid_count ||
        r->stream_id != t->stream_base + (r->bdf - t->rid_base)) return NVMEH_E_SP11_STREAM;

    if (r->lba_bytes != NVMEH_SP11_LBA_BYTES) return NVMEH_E_SP11_LBA_FORMAT;
    if (r->metadata_bytes) return NVMEH_E_SP11_METADATA;
    if (p->lba_bytes != r->lba_bytes) return NVMEH_E_SP11_PARAMS;
    if (r->nsze < 2 * NVMEH_SP11_GPT_LBAS + 1 || r->nsze > UINT64_MAX / NVMEH_SP11_LBA_BYTES ||
        r->first_usable < NVMEH_SP11_GPT_LBAS || r->first_usable > r->last_usable ||
        r->last_usable > r->nsze - NVMEH_SP11_GPT_LBAS) return NVMEH_E_SP11_GEOMETRY;
    /* A record with no protected extent is a survey that never ran: every disk this contract serves carries at least
     * the EFI system partition and the Windows volume, so an empty list would let any aligned window through. */
    if (!r->protected_count || r->protected_count > NVMEH_SP11_MAX_PROTECTED) return NVMEH_E_SP11_PROTECTED;
    for (i = 0; i < r->protected_count; i++) {
        const struct nvmeh_sp11_extent *e = &r->protected_extents[i];
        if (e->first_lba > e->last_lba || e->first_lba < r->first_usable || e->last_lba > r->last_usable)
            return NVMEH_E_SP11_PROTECTED;
    }

    first = p->first_lba; last = p->last_lba;
    if (first > last || last == UINT64_MAX) return NVMEH_E_SP11_WINDOW_ORDER;
    size = last - first + 1;
    if (size < NVMEH_SP11_MIN_WINDOW_LBAS) return NVMEH_E_SP11_WINDOW_SIZE;
    if (last >= r->nsze) return NVMEH_E_SP11_WINDOW_RANGE;
    if (first < r->first_usable || last > r->last_usable) return NVMEH_E_SP11_WINDOW_GPT;
    /* Overlap is reported before alignment: a window that straddles a neighbour by one block is first of all an
     * overlap, whatever its alignment. */
    for (i = 0; i < r->protected_count; i++)
        if (first <= r->protected_extents[i].last_lba && r->protected_extents[i].first_lba <= last)
            return NVMEH_E_SP11_OVERLAP;
    if ((first | size) & (NVMEH_SP11_ALIGN_LBAS - 1)) return NVMEH_E_SP11_WINDOW_ALIGN;
    if (first - r->first_usable < NVMEH_SP11_GUARD_LBAS || r->last_usable - last < NVMEH_SP11_GUARD_LBAS)
        return NVMEH_E_SP11_WINDOW_GUARD;
    for (i = 0; i < r->protected_count; i++) {
        const struct nvmeh_sp11_extent *e = &r->protected_extents[i];
        /* No overlap above, so each extent lies wholly before or wholly after the window. */
        uint64_t gap = e->last_lba < first ? first - e->last_lba - 1 : e->first_lba - last - 1;
        if (gap < NVMEH_SP11_GUARD_LBAS) return NVMEH_E_SP11_WINDOW_GUARD;
    }

    if (!(p->flags & NVMEH_F_WRITE))
        return (p->write_first_lba | p->write_last_lba) ? NVMEH_E_SP11_WRITE_WINDOW : 0;
    if (!(r->flags & NVMEH_SP11_R_WRITE_AUTH)) return NVMEH_E_SP11_WRITE_UNAUTHORISED;
    if (p->write_first_lba > p->write_last_lba || p->write_first_lba < first + NVMEH_SP11_EDGE_LBAS ||
        p->write_last_lba > last - NVMEH_SP11_EDGE_LBAS) return NVMEH_E_SP11_WRITE_WINDOW;
    return 0;
}

int nvmeh_sp11_prove_endpoint(const struct nvmeh_ops *o, const struct nvmeh_sp11_target *t, const struct nvmeh_params *p)
{
    uint64_t cfg = t->ecam_base + sp11_cfg_offset(t->bdf), bar;
    uint32_t id = RD(o, cfg), lo;
    /* A function that is not there completes as all ones where the platform does not raise an abort instead; q1n1
     * runs its reads under the SError strict guard for the same reason (tools/nvme_m3.py main()). */
    if (id == 0xffffffffu || (id & 0xffff) == 0xffff) return NVMEH_E_SP11_NO_DEVICE;
    if ((RD(o, cfg + 8) >> 8) != NVMEH_SP11_CLASS_NVME) return NVMEH_E_SP11_CLASS;
    if ((RD(o, cfg + 4) & 6) != 6) return NVMEH_E_SP11_DEVICE_DISABLED;       /* Command: Memory Space, Bus Master */
    if (((RD(o, cfg + 0xc) >> 16) & 0x7f) != 0) return NVMEH_E_SP11_HEADER_TYPE;
    lo = RD(o, cfg + 0x10);
    if (lo & 1) return NVMEH_E_SP11_BAR;                                       /* an I/O-space BAR */
    switch ((lo >> 1) & 3) {                                                   /* memory BAR type: 32 or 64 bit */
    case 0: bar = lo & ~0xfu; break;
    case 2: bar = (lo & ~0xfu) | (uint64_t)RD(o, cfg + 0x14) << 32; break;
    default: return NVMEH_E_SP11_BAR;
    }
    return !bar || bar != p->bar ? NVMEH_E_SP11_BAR : 0;
}

int nvmeh_sp11_adopt(const struct nvmeh_ops *o, const struct nvmeh_sp11_target *t, const struct nvmeh_sp11_record *r,
                     const struct nvmeh_params *p, struct nvmeh_state *s, uint64_t mem_low, uint64_t mem_high)
{
    int e = nvmeh_sp11_check(t, r, p);
    if (!e) e = nvmeh_sp11_prove_endpoint(o, t, p);
    if (e) {
        s->prepare = (uint64_t)(int64_t)e;
        return e;
    }
    return nvmeh_adopt(o, p, s, mem_low, mem_high);
}

int nvmeh_sp11_target_from_board(const struct board_info *b, uint64_t segment, struct nvmeh_sp11_target *t)
{
    unsigned i, v3 = 0, at = 0;
    int e = 0;
    t->abi = NVMEH_SP11_ABI; t->known = 0; t->segment = 0; t->ecam_base = 0; t->start_bus = 0; t->end_bus = 0;
    t->bdf = 0; t->smmu_base = 0; t->stream_base = 0; t->rid_base = 0; t->rid_count = 0; t->eui64 = 0;
    t->generation = 0; t->reserved[0] = t->reserved[1] = t->reserved[2] = 0;
    if (board_info_device_state(b, BOARD_INFO_DEV_NVME) != BOARD_INFO_DEVICE_PRESENT) e = NVMEH_E_SP11_UNKNOWN_DEVICE;
    else if (!(b->known & BOARD_INFO_KNOWN_ECAM)) e = NVMEH_E_SP11_UNKNOWN_ECAM;
    else {
        for (i = 0; i < b->ecam_count && i < BOARD_INFO_MAX_ECAM; i++)
            if (b->ecam[i].segment == segment) {
                t->segment = segment; t->ecam_base = b->ecam[i].base;
                t->start_bus = b->ecam[i].start_bus; t->end_bus = b->ecam[i].end_bus;
                t->known |= NVMEH_SP11_K_ECAM;
            }
        if (!(t->known & NVMEH_SP11_K_ECAM)) e = NVMEH_E_SP11_UNKNOWN_ECAM;
    }
    /* Which SMMU serves the SSD is not a descriptor field: a board with one SMMUv3 answers it, anything else is
     * ambiguous and stays unknown rather than guessed. */
    if (!e && !(b->known & BOARD_INFO_KNOWN_SMMU)) e = NVMEH_E_SP11_UNKNOWN_SMMU;
    if (!e) {
        for (i = 0; i < b->smmu_count && i < BOARD_INFO_MAX_SMMU; i++)
            if (b->smmu[i].kind == BOARD_INFO_SMMU_V3) { v3++; at = i; }
        if (v3 != 1) e = NVMEH_E_SP11_UNKNOWN_SMMU;
        else { t->smmu_base = b->smmu[at].base; t->known |= NVMEH_SP11_K_SMMU; }
    }
    if (e) {
        t->known = 0; t->segment = 0; t->ecam_base = 0; t->start_bus = 0; t->end_bus = 0; t->smmu_base = 0;
    }
    return e;
}

int nvmeh_sp11_admin_verdict(unsigned phase, unsigned opcode, uint32_t cdw10)
{
    unsigned id = cdw10 & 0xff;
    if (phase != NVMEH_SP11_PHASE_TAKEOVER && phase != NVMEH_SP11_PHASE_ADOPTED) return NVMEH_E_SP11_ADMIN_PHASE;
    if (opcode > 0xff) return NVMEH_E_SP11_ADMIN_OPCODE;
    /* Read-only commands first: the same in both phases. Opcodes and identifiers are the NVMe base specification's,
     * VERIFIED as read in Linux include/linux/nvme.h at 7475ea8b58616b33c04afb976cf0fbaad9dd63fb (enum
     * nvme_admin_opcode 1306-1335, NVME_ID_CNS_* 561-562, NVME_FEAT_* 1375-1396, NVME_LOG_* 1406-1408). */
    switch (opcode) {
    case 0x06: return id <= 1 ? 0 : NVMEH_E_SP11_ADMIN_CNS;                       /* Identify: namespace, controller */
    case 0x02: return id >= 1 && id <= 3 ? 0 : NVMEH_E_SP11_ADMIN_LOG;            /* Get Log Page */
    case 0x0a: return (id >= 1 && id <= 0x14) || id == 0x16 || id == 0x17 ? 0 : NVMEH_E_SP11_ADMIN_FEATURE;
    }
    if (phase == NVMEH_SP11_PHASE_ADOPTED) return NVMEH_E_SP11_ADMIN_OPCODE;
    switch (opcode) {
    case 0x00: case 0x01: case 0x04: case 0x05: return 0;           /* Delete/Create I/O SQ, Delete/Create I/O CQ */
    case 0x09:                                                      /* Set Features: Number of Queues, no Save bit */
        if (id != 0x07) return NVMEH_E_SP11_ADMIN_FEATURE;
        return cdw10 >> 31 ? NVMEH_E_SP11_ADMIN_SAVE : 0;
    }
    return NVMEH_E_SP11_ADMIN_OPCODE;
}

int nvmeh_sp11_io_verdict(unsigned opcode, unsigned writable)
{
    switch (opcode) {
    case 0x00: case 0x02: return 0;                                 /* Flush, Read */
    case 0x01: return writable ? 0 : NVMEH_E_SP11_IO_READONLY;      /* Write */
    }
    return NVMEH_E_SP11_IO_OPCODE;
}

const char *nvmeh_sp11_strerror(int code)
{
    switch (code) {
    case 0: return "ok";
    case NVMEH_E_SP11_ABI: return "target or record ABI revision is not 1";
    case NVMEH_E_SP11_RESERVED: return "a reserved word, unknown known-bit or unknown flag is set";
    case NVMEH_E_SP11_UNKNOWN_ECAM: return "the descriptor has no measured ECAM window for the SSD's segment";
    case NVMEH_E_SP11_UNKNOWN_BDF: return "the endpoint's bus/device/function is not pinned";
    case NVMEH_E_SP11_UNKNOWN_SMMU: return "the PCIe SMMU is unknown or ambiguous";
    case NVMEH_E_SP11_UNKNOWN_STREAM: return "the RID to StreamID mapping is not pinned";
    case NVMEH_E_SP11_UNKNOWN_EUI64: return "the namespace identity is not pinned";
    case NVMEH_E_SP11_UNKNOWN_GENERATION: return "the run identity is not pinned";
    case NVMEH_E_SP11_UNKNOWN_DEVICE: return "the descriptor does not say the NVMe device is present";
    case NVMEH_E_SP11_TARGET_INVALID: return "a pinned value is zero, unaligned, out of range or self-contradicting";
    case NVMEH_E_SP11_RECORD_UNCLEAN: return "the adoption record is not a clean hand-off";
    case NVMEH_E_SP11_STALE: return "the adoption record belongs to another run";
    case NVMEH_E_SP11_SEGMENT: return "the record's PCI segment is not the descriptor's";
    case NVMEH_E_SP11_ECAM: return "the record's ECAM base is not the descriptor's";
    case NVMEH_E_SP11_BUS_RANGE: return "the record's bus is outside the descriptor's MCFG range";
    case NVMEH_E_SP11_BDF: return "the record's bus/device/function is not the one the descriptor pins";
    case NVMEH_E_SP11_CFG_ADDRESS: return "the record's config address is not the ECAM address of the pinned BDF";
    case NVMEH_E_SP11_CLASS: return "the endpoint is not an NVM Express controller";
    case NVMEH_E_SP11_EUI64: return "the record's namespace is not the pinned one";
    case NVMEH_E_SP11_SMMU_BASE: return "the record's SMMU is not the descriptor's";
    case NVMEH_E_SP11_SMMU_MODE: return "the SSD's stream is neither bypass nor an identity stage 2";
    case NVMEH_E_SP11_DMA_UNPROVEN: return "the DMA window was not proven reachable for the SSD";
    case NVMEH_E_SP11_STREAM: return "the StreamID is not the descriptor's mapping of the endpoint's RID";
    case NVMEH_E_SP11_LBA_FORMAT: return "the current LBA format is not 512 bytes";
    case NVMEH_E_SP11_METADATA: return "the current LBA format carries per-block metadata";
    case NVMEH_E_SP11_PARAMS: return "the params disagree with the record about the LBA size";
    case NVMEH_E_SP11_GEOMETRY: return "the namespace size or GPT usable range is impossible";
    case NVMEH_E_SP11_PROTECTED: return "the protected-extent list is empty, malformed or too long";
    case NVMEH_E_SP11_WINDOW_ORDER: return "the window has zero size, wrapped size or first after last";
    case NVMEH_E_SP11_WINDOW_SIZE: return "the window is smaller than the two synthesized edges";
    case NVMEH_E_SP11_WINDOW_RANGE: return "the window reaches past the end of the namespace";
    case NVMEH_E_SP11_WINDOW_GPT: return "the window reaches into the primary or backup GPT zone";
    case NVMEH_E_SP11_WINDOW_ALIGN: return "the window start or size is not a whole number of MiB";
    case NVMEH_E_SP11_WINDOW_GUARD: return "the window is closer than the guard to another extent or a GPT zone";
    case NVMEH_E_SP11_OVERLAP: return "the window shares an LBA with another partition";
    case NVMEH_E_SP11_WRITE_WINDOW: return "the write window is wrong for the write flag or reaches an edge MiB";
    case NVMEH_E_SP11_WRITE_UNAUTHORISED: return "writes were requested without the per-run authorisation";
    case NVMEH_E_SP11_NO_DEVICE: return "config space reads as no device";
    case NVMEH_E_SP11_DEVICE_DISABLED: return "Memory Space or Bus Master is off";
    case NVMEH_E_SP11_HEADER_TYPE: return "the endpoint's header type is not 0";
    case NVMEH_E_SP11_BAR: return "BAR0 is not the memory BAR the params name";
    case NVMEH_E_SP11_ADMIN_PHASE: return "the admin phase is neither takeover nor adopted";
    case NVMEH_E_SP11_ADMIN_OPCODE: return "the admin opcode is not on the allow-list";
    case NVMEH_E_SP11_ADMIN_CNS: return "Identify is allowed for namespace and controller only";
    case NVMEH_E_SP11_ADMIN_LOG: return "Get Log Page is allowed for error, SMART and firmware slot only";
    case NVMEH_E_SP11_ADMIN_FEATURE: return "the feature is not on the allow-list for this command";
    case NVMEH_E_SP11_ADMIN_SAVE: return "Set Features with the Save bit is refused";
    case NVMEH_E_SP11_IO_OPCODE: return "the I/O opcode is not Read, Write or Flush";
    case NVMEH_E_SP11_IO_READONLY: return "Write while the window is read-only";
    case NVMEH_SP11_STATUS_UNWIRED:
        return "the monitor does not yet receive the SP11 target, record and per-run pins, so it refuses to adopt the SSD";
    }
    return "unknown SP11 refusal code";
}
#endif
