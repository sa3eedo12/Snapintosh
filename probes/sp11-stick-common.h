/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_COMMON_H
#define SNAP_PROBES_SP11_STICK_COMMON_H
/* What the wrapper (SNAPMON.BIN) and the reporter (SNAPREP.BIN) both need from the stick runner: the five files found through the
 * preload table, and a text line built in the stack. Header only and static, like q1n1's payloads/payload-util.h, which it builds on
 * (a payload is a flat binary with no runtime to link; the build passes -I for that directory and for platform/uefi).
 *
 * Every function here stores only into the structures the caller passes, which are in the caller's stack: the reporter's binary is
 * checked to hold no other store (tools/test-sp11-stick-wrapper-build.py), which needs them inlined, hence always_inline.
 *
 * Evidence: EXPERIMENTAL; hardware NOT RUN. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK
#include "sp11-stick-bundle.h"
#include "payload-util.h"
#include "preload.h"

#define SC_INLINE static inline __attribute__((always_inline))
#define SC_ADDRESS_LIMIT (UINT64_C(1) << 48)

/* The five files, by manifest position, from the preload table the runner built (docs/SP11-PAYLOAD.md: "the entry whose base is
 * payload_base is this payload"). Paths point into the table, which is why sp11sb_file wants a pointer and not a copy.
 *
 * ARGS_BAD for a table that cannot be read: a null or misaligned pointer, a size that cannot hold the header or the entries it
 * claims, or a header that is not the runner's (magic, version 1, entry size). The interface has no code of its own for that and
 * the runner builds the table itself, so it is a violation of the arguments. A count other than 5 is left to FILE_COUNT.
 * *self is the position of the entry that is this very payload (base and size equal the arguments'), or ~0u. */
SC_INLINE int sc_files(const struct q1n1_payload_args *a, struct sp11sb_file files[SP11SB_FILES], uint32_t *count, uint32_t *self)
{
    const struct q1n1_preload_cache *t;
    uint64_t i;
    *count = 0;
    *self = ~0u;
    if (!a->preload_table || (a->preload_table & 7) || a->preload_table >= SC_ADDRESS_LIMIT ||
        a->preload_size < offsetof(struct q1n1_preload_cache, files) || a->preload_size > SC_ADDRESS_LIMIT - a->preload_table)
        return SP11SB_E_ARGS_BAD;
    t = (const struct q1n1_preload_cache *)sp11hw_view(a->preload_table, a->preload_size);
    if (t->magic != Q1N1_CACHE_MAGIC || t->version != 1 || t->entry_size != sizeof(struct q1n1_cached_file) ||
        t->count > Q1N1_PRELOAD_MAX ||
        t->count > (a->preload_size - offsetof(struct q1n1_preload_cache, files)) / sizeof(struct q1n1_cached_file))
        return SP11SB_E_ARGS_BAD;
    for (i = 0; i < t->count; i++) {
        if (*self == ~0u && t->files[i].base == a->payload_base && t->files[i].file.size == a->payload_size) *self = (uint32_t)i;
        if (i < SP11SB_FILES) {
            files[i].path = t->files[i].file.path;
            files[i].base = t->files[i].base;
            files[i].size = t->files[i].file.size;
        }
    }
    *count = (uint32_t)t->count;
    return 0;
}

/* What a FILE_* refusal is about, for the report's detail word and the panel: the first slot that fails its own check, or the table's
 * count when every slot passes and only the count is wrong (FILE_COUNT). The wrapper records it and the reporter prints the same. */
SC_INLINE uint64_t sc_bad_slot(const struct sp11sb_file *files, uint32_t count)
{
    uint32_t i;
    for (i = 0; i < count && i < SP11SB_FILES; i++)
        if (sp11sb_file_check(&files[i], i)) return i;
    return count;
}

/* Decimal, which the runner's ops do not have (its hex op prints 0X and the fewest digits). */
SC_INLINE void sc_dec(struct pl_line *l, uint64_t value)
{
    char digits[20];
    uint32_t n = 0;
    do {
        digits[n++] = (char)('0' + value % 10);
        value /= 10;
    } while (value);
    while (n && l->len < Q1N1_PAYLOAD_LINE_MAX) l->buf[l->len++] = digits[--n];
    l->buf[l->len] = 0;
}
/* " KEY=0XVALUE" and " KEY=DECIMAL": the key carries its own leading space and equals sign. */
SC_INLINE void sc_key_hex(const struct q1n1_payload_args *a, struct pl_line *l, const char *key, uint64_t value)
{
    pl_text(l, key);
    pl_hex(a, l, value);
}
SC_INLINE void sc_key_dec(struct pl_line *l, const char *key, uint64_t value)
{
    pl_text(l, key);
    sc_dec(l, value);
}
#endif /* SNAP_SP11_STICK */
#endif /* SNAP_PROBES_SP11_STICK_COMMON_H */
