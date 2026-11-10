/* SPDX-License-Identifier: MIT */
/* The compiler may lower struct copies, zero initialisation and string
 * literals to calls to these five functions even with -ffreestanding, and the
 * image links no C library. Each one forwards to the probe's own byte-wise
 * implementation in logic.c, which is built so that its loops are never
 * recognised as a memcpy/memset idiom (that would make it call itself). */
#include <stddef.h>

#include "logic.h"

void *memcpy(void *dst, const void *src, size_t n)
{
    return probe_memcpy(dst, src, n);
}

void *memmove(void *dst, const void *src, size_t n)
{
    return probe_memmove(dst, src, n);
}

void *memset(void *dst, int c, size_t n)
{
    return probe_memset(dst, c, n);
}

int memcmp(const void *a, const void *b, size_t n)
{
    return probe_memcmp(a, b, n);
}

int bcmp(const void *a, const void *b, size_t n)
{
    return probe_memcmp(a, b, n);
}
