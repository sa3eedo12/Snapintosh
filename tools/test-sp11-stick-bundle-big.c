/* SPDX-License-Identifier: MIT */
/* Host harness for the 2 GiB files of the root carry (docs/SP11-STICK-ROOT.md): probes/sp11-stick-bundle.h built with SNAP_SP11_STICK_ROOT_CARRY=1 (or 0, the gate off) under
 * AddressSanitizer and UndefinedBehaviorSanitizer (and the implicit-conversion checks, which catch a size that is narrowed by assignment), driven by
 * tools/test-sp11-stick-bundle-big.py. A container of up to 2 GiB is a sparse file that this program maps read-only and hands to the checks as one pointer and one length, as the
 * wrapper has it in RAM. The checks read the head, the table and the pads through the mapping; the digest callback is given a pointer into the mapping and reads that range with
 * pread() in windows of 16 MiB instead, from the offset the pointer stands for, so that the data pages are never mapped in and the resident set stays a few MiB while the 2 GiB
 * are hashed: a wrong offset, size or narrowed length in the header shows as a wrong digest. The hash is CommonCrypto's (host only); the test compares every digest with hashlib.
 *
 *   big file <role> <base hex> <size hex>                                   sp11sb_file_check: "file <code> <NAME>"
 *   big guest <path> <base> <size> <file_size> <count> <index> <file> <pol>  sp11sb_guest_check, then sp11sb_guest_digests: "guest <code> <NAME> <bad chunk> <maxrss bytes>"
 *
 * <base> <size> <file_size> are hex, <count> and <pol> decimal, <index> and <file> 64 hex digits: the pinned fields that the guest checks read, put into a zeroed 1 KiB PIN.DAT.
 * Seeded faults, each of which the test expects to be caught: -DBIG_SEED_LEN31 passes the file length through 31 bits, -DBIG_SEED_TOUCH hashes through the mapping, which
 * makes the resident set the size of the file (so the bound of the test is a measurement and not a promise). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>

#if !defined(__APPLE__)
#error "the harness hashes with CommonCrypto: it is a macOS host test"
#endif
#include <CommonCrypto/CommonDigest.h>

#include "../probes/sp11-stick-bundle.h"

#define WINDOW (UINT64_C(16) << 20)

static const uint8_t *map_base;
static uint64_t map_len;
static int map_fd = -1;

static void sha256_windows(const void *data, uint64_t size, uint8_t digest[32])
{
    CC_SHA256_CTX ctx;
    const uint8_t *p = data;
    uint64_t done = 0;
#ifndef BIG_SEED_TOUCH
    static uint8_t window[WINDOW];
#else
    (void)map_len, (void)map_fd, (void)map_base;
#endif
    CC_SHA256_Init(&ctx);
    while (done < size) {
        uint64_t n = size - done < WINDOW ? size - done : WINDOW;
#ifndef BIG_SEED_TOUCH
        if (p + done >= map_base && p + done + n <= map_base + map_len) {
            uint64_t got = 0, at = (uint64_t)(p + done - map_base);
            while (got < n) {
                ssize_t r = pread(map_fd, window + got, (size_t)(n - got), (off_t)(at + got));
                if (r <= 0) {
                    fprintf(stderr, "pread failed\n");
                    exit(2);
                }
                got += (uint64_t)r;
            }
            CC_SHA256_Update(&ctx, window, (CC_LONG)n);
        } else
#endif
        CC_SHA256_Update(&ctx, p + done, (CC_LONG)n);
        done += n;
    }
    CC_SHA256_Final(digest, &ctx);
}

static int hex64(const char *s, uint8_t out[32])
{
    unsigned i, v;
    if (strlen(s) != 64) return 0;
    for (i = 0; i < 32; i++) {
        if (sscanf(s + 2 * i, "%2x", &v) != 1) return 0;
        out[i] = (uint8_t)v;
    }
    return 1;
}

static void put64(uint8_t *b, uint64_t v)
{
    unsigned i;
    for (i = 0; i < 8; i++) b[i] = (uint8_t)(v >> (8 * i));
}

int main(int argc, char **argv)
{
    if (argc == 5 && !strcmp(argv[1], "file")) {
        static const char *const names[] = {"SNAPMON.BIN", "SNAPREP.BIN", "GUEST.DAT", "PIN.DAT", "REPORT.DAT"};
        char path[SP11SB_PATH_MAX];
        struct sp11sb_file f;
        unsigned role = (unsigned)atoi(argv[2]);
        int r;
        if (role > 4) return 2;
        memset(path, 0, sizeof path);
        snprintf(path, sizeof path, "\\SP11\\%s", names[role]);
        f.path = path;
        f.base = strtoull(argv[3], 0, 16);
        f.size = strtoull(argv[4], 0, 16);
        r = sp11sb_file_check(&f, role);
        printf("file %d %s\n", r, r ? sp11sb_error_name((uint32_t)r) : "OK");
        return 0;
    }
    if (argc == 10 && !strcmp(argv[1], "guest")) {
        uint8_t pin[SP11SB_PIN_BYTES];
        struct stat st;
        int r;
        uint32_t bad = 0, policy = (uint32_t)atoi(argv[9]);
        uint8_t index[32], file[32];
        struct rusage ru;
        uint64_t len;
        map_fd = open(argv[2], O_RDONLY);
        if (map_fd < 0 || fstat(map_fd, &st) || !hex64(argv[7], index) || !hex64(argv[8], file)) return 2;
        len = (uint64_t)st.st_size;
        map_base = mmap(0, (size_t)len, PROT_READ, MAP_PRIVATE, map_fd, 0);
        if (map_base == MAP_FAILED) return 2;
        map_len = len;
        memset(pin, 0, sizeof pin);
        put64(pin + offsetof(struct sp11sb_pin, guest_base), strtoull(argv[3], 0, 16));
        put64(pin + offsetof(struct sp11sb_pin, guest_size), strtoull(argv[4], 0, 16));
        put64(pin + offsetof(struct sp11sb_pin, guest_file_size), strtoull(argv[5], 0, 16));
        pin[offsetof(struct sp11sb_pin, guest_chunk_count)] = (uint8_t)atoi(argv[6]);
        pin[offsetof(struct sp11sb_pin, guest_chunk_count) + 1] = (uint8_t)(atoi(argv[6]) >> 8);
        memcpy(pin + offsetof(struct sp11sb_pin, guest_index_sha256), index, 32);
        memcpy(pin + offsetof(struct sp11sb_pin, guest_file_sha256), file, 32);
#ifdef BIG_SEED_LEN31
        len &= UINT64_C(0x7FFFFFFF);
#endif
        r = sp11sb_guest_check(map_base, len, pin);
        if (!r) r = sp11sb_guest_digests(map_base, len, pin, policy, sha256_windows, &bad);
        getrusage(RUSAGE_SELF, &ru);
        printf("guest %d %s %u %llu\n", r, r ? sp11sb_error_name((uint32_t)r) : "OK", bad, (unsigned long long)ru.ru_maxrss);
        return 0;
    }
    fprintf(stderr, "usage: big file <role> <base> <size> | big guest <path> <base> <size> <file_size> <count> <index> <file> <policy>\n");
    return 2;
}
