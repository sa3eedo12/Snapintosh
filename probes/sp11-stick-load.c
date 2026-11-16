/* SPDX-License-Identifier: MIT */
/* The loader of the SP11 stick wrapper: probes of the memory it will read and write, the copy of the guest chunks to the pinned
 * placement with cache maintenance, and the check of the copy against the chunk digests, read from DRAM
 * (docs/SP11-STICK-WRAPPER.md, section 3).
 *
 * Ownership. The placement is free memory by the final map's account (sp11sb_place_check) and nobody else's: the wrapper writes it,
 * cleans it, and leaves it as written. Nothing in it is executed in this build, so no instruction cache is touched (the IC
 * instructions are absent from the binary); a build that enters the monitor must add that before it does.
 *
 * Every store to RAM is sp11hw_store64 over a range this file has just checked, and every store is followed, per chunk, by a clean
 * of the lines it touched. The copy is checked by invalidating those lines (DC CIVAC, which writes back first, so it never loses
 * data a neighbour left dirty in a shared edge line) and hashing the destination again with the chunk's own digest. That proves
 * the bytes are in DRAM after clean and invalidate; it cannot tell a missing clean from a working one, because CIVAC cleans. A pure
 * invalidate could, and is not used: it may discard dirty bytes of an edge line the wrapper does not own. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK
#include "sp11-stick-wrapper.h"

int sw_probe_range(uint64_t base, uint64_t size, int write, struct sw_probe *p)
{
    uint64_t first = base & ~(SW_PAGE - 1), last, at;
    p->at = p->par = 0;
    p->count = 0;
    p->attr = p->sh = p->load32 = 0;
    if (!size || size > UINT64_MAX - base) {
        p->at = base;
        return SP11SB_E_PLACE_UNREADABLE;
    }
    last = (base + size - 1) & ~(SW_PAGE - 1);
#ifdef SP11_STICK_SEED_NO_AT
    return 0;
#endif
    at = first;
    for (;;) {
        struct sp11hw_walk w;
        uint64_t par = write ? sp11hw_at_s1e2w(at) : sp11hw_at_s1e2r(at);
        sp11hw_walk_decode(par, at, &w);
        if (!p->count) {
            p->attr = w.attr;
            p->sh = w.sh;
        }
        p->count++;
        if (!w.mapped || !w.ident || (write && sp11hw_attr_class(w.attr) != SP11HW_ATTR_WB)) {
            p->at = at;
            p->par = par;
            return SP11SB_E_PLACE_UNREADABLE;
        }
        if (at >= last) break;
        at = (at | (SW_PROBE_STEP - 1)) + 1;
        if (at > last) at = last;
    }
    return 0;
}

int sw_place_runtime(const struct sw_ctx *c, uint64_t place_base, uint64_t place_size, uint64_t *detail, struct sw_probe *p)
{
    uint64_t addr[2];
    uint32_t i, word = 0;
    int r;
    addr[0] = place_base;
    addr[1] = place_base + place_size - 4;
    p->at = p->par = 0;
    p->count = 0;
    p->attr = p->sh = p->load32 = 0;
    for (i = 0; i < 2; i++) {
        int32_t rc = c->a->ops.load32(addr[i], &word);
        if (rc != Q1N1_PAYLOAD_LOAD_OK) {
            p->at = *detail = addr[i];
            p->par = (uint64_t)(int64_t)rc;
            p->load32 = 1;
            return SP11SB_E_PLACE_UNREADABLE;
        }
    }
    r = sw_probe_range(place_base, place_size, 1, p);
    if (r) *detail = p->at;
    return r;
}

/* One chunk table entry, read again from the file each time: the table was checked at stage GUEST_OK, and nothing but this
 * function's own tests stands between that check and the stores. */
struct sw_chunk {
    uint64_t dest, size, file_offset;
    const uint8_t *entry;
    int zero;
};

/* The destination inside the placement and inside the pinned guest window (which PIN.DAT's geometry puts inside the placement),
 * sixteen-byte granular, and the source inside the file. CHUNK_RANGE, CHUNK_GEOMETRY or CHUNK_EOF, as the container's own check
 * names them. */
static int chunk_fetch(const struct sw_ctx *c, uint32_t i, struct sw_chunk *k)
{
    uint64_t pb = SP11SB_PIN_U64(c->pin, place_base), ps = SP11SB_PIN_U64(c->pin, place_size);
    uint64_t gb = SP11SB_PIN_U64(c->pin, guest_base), gs = SP11SB_PIN_U64(c->pin, guest_size);
    k->entry = SP11SB_CHUNK_AT(c->guest, i);
    k->dest = SP11SB_CHUNK_U64(k->entry, dest);
    k->size = SP11SB_CHUNK_U64(k->entry, size);
    k->file_offset = SP11SB_CHUNK_U64(k->entry, file_offset);
    k->zero = (SP11SB_CHUNK_U16(k->entry, flags) & SP11SB_CHUNK_ZERO) != 0;
    if ((k->dest | k->size) & (SP11SB_DEST_ALIGN - 1) || !k->size) return SP11SB_E_CHUNK_GEOMETRY;
#ifndef SP11_STICK_SEED_NO_CHUNK_CHECK
    if (k->dest < pb || k->dest - pb > ps || k->size > ps - (k->dest - pb)) return SP11SB_E_CHUNK_RANGE;
    if (k->dest < gb || k->dest - gb > gs || k->size > gs - (k->dest - gb)) return SP11SB_E_CHUNK_RANGE;
#else
    (void)pb, (void)ps, (void)gb, (void)gs;
#endif
    if (!k->zero && (k->file_offset > c->guest_len || k->size > c->guest_len - k->file_offset)) return SP11SB_E_CHUNK_EOF;
    return 0;
}

int sw_chunks_check(const struct sw_ctx *c, uint32_t *bad)
{
    struct sw_chunk k;
    uint32_t i;
    for (i = 0; i < c->head.r.chunks_total; i++) {
        int r;
#ifdef SP11_STICK_SEED_NO_PREPASS
        break;
#endif
        r = chunk_fetch(c, i, &k);
        if (r) {
            *bad = i;
            return r;
        }
    }
    return 0;
}

static void copy_chunk(const struct sw_ctx *c, const struct sw_chunk *k)
{
    const uint8_t *src = c->guest + k->file_offset;
    uint64_t o, word;
    if (k->zero) {
        for (o = 0; o < k->size; o += 8) sp11hw_store64(k->dest + o, 0);
        return;
    }
    for (o = 0; o < k->size; o += 8) {
        __builtin_memcpy(&word, src + o, 8);
        sp11hw_store64(k->dest + o, word);
    }
}

int sw_copy_all(struct sw_ctx *c, uint32_t *bad)
{
    struct sw_chunk k;
    uint32_t i;
    for (i = 0; i < c->head.r.chunks_total; i++) {
        int r = chunk_fetch(c, i, &k);
        if (r) {
            *bad = i;
            return r;
        }
        copy_chunk(c, &k);
#ifndef SP11_STICK_SEED_NO_CLEAN
        sp11hw_clean_range(k.dest, k.size);
#endif
        c->head.r.chunks_copied = i + 1;
        c->head.r.bytes_copied += k.size;
        sw_head_flush(c);
    }
    sp11hw_dsb_sy();
    sp11hw_isb();
    return 0;
}

int sw_verify_all(struct sw_ctx *c, uint32_t *bad)
{
    struct sw_chunk k;
    uint8_t digest[32];
    uint32_t i;
    for (i = 0; i < c->head.r.chunks_total; i++) {
        int r = chunk_fetch(c, i, &k);
        if (r) {
            *bad = i;
            return r;
        }
        sp11hw_flush_range(k.dest, k.size);
    }
    sp11hw_dsb_sy();
    sp11hw_isb();
    for (i = 0; i < c->head.r.chunks_total; i++) {
        const uint8_t *memory;
        int r = chunk_fetch(c, i, &k);
        if (r) {
            *bad = i;
            return r;
        }
        memory = (const uint8_t *)sp11hw_view(k.dest, k.size);
#ifdef SP11_STICK_SEED_VERIFY_SOURCE
        if (!k.zero) memory = c->guest + k.file_offset;
#endif
        if (k.zero) {
            if (!sp11sb_zero(memory, k.size)) {
                *bad = i;
                return SP11SB_E_COPY_VERIFY;
            }
            continue;
        }
        sw_sha256(memory, k.size, digest);
        if (!sp11sb_same(digest, k.entry + offsetof(struct sp11sb_chunk, sha256), 32)) {
            *bad = i;
            return SP11SB_E_COPY_VERIFY;
        }
        c->head.r.chunks_hashed++;
        c->head.r.bytes_hashed += k.size;
        sw_head_flush(c);
    }
    return 0;
}
#endif /* SNAP_SP11_STICK */
