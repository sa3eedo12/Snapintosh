/* SPDX-License-Identifier: MIT */
/* Host harness for the loader of the SP11 stick wrapper at the sizes and addresses of the root carry (docs/SP11-STICK-ROOT.md), driven by tools/test-sp11-stick-load-big.py.
 * The REAL probes/sp11-stick-load.c (or a copy of it with one seeded fault) is linked with q1n1's sha256.c; the machine under it is a model: sp11hw_view and sp11hw_store64 map a
 * physical address to host memory through a few regions, and a ZERO range of more than 4 GiB is never backed by memory at all, it is a sequence that every store must follow.
 * Built with AddressSanitizer and UndefinedBehaviorSanitizer. A bus fault of the model (an access outside every region, or a store out of sequence) ends the run with exit 3.
 *
 *   load zero                a ZERO chunk of 4 GiB + 64 KiB at an address above 4 GiB: sw_chunks_check, then sw_copy_all, which must store 0x100010000 / 8 zero words in order, clean
 *                            every 64-byte line of the range once, and count the bytes in 64 bits
 *   load sparse <path>       a sparse container of exactly 2 GiB (created here, removed at the end) whose data chunks sit at the end of the file (offset 2^31 - 64 KiB) and whose
 *                            destinations are above 4 GiB and cross a 4 GiB boundary, with a 64 KiB ZERO chunk between them: sw_chunks_check, sw_copy_all, the copy against the file, and
 *                            sw_verify_all (the runner's own q1n1_sha256); then one flipped byte in the last chunk, which must be refused and named
 *   load refuse              sw_chunks_check on single hostile entries against a 2 GiB file and a 6 GiB window: each must be refused by name, and the entry at both limits accepted
 *
 * Output: one line "RESULT <check> <ok|bad> <detail>" per check, then "MAXRSS <bytes>"; the exit code is 0 when every check is ok. Hardware: NOT RUN. */
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>

#include "../probes/sp11-stick-wrapper.h"

#define MIB (UINT64_C(1) << 20)
#define LINE_BYTES UINT64_C(64)
#define GUEST_BASE UINT64_C(0x880000000)
#define GUEST_SIZE UINT64_C(0x180000000)

struct region {
    uint64_t base, size;
    uint8_t *mem;
};
static struct region regions[8];
static unsigned nregions;
static struct {
    uint64_t base, size, next;
} zero_run;
static uint64_t cleaned_lines, cleaned_first = UINT64_MAX, cleaned_last;
static int failed;

static void fault(const char *what, uint64_t pa)
{
    printf("RESULT model_fault bad %s %#llx\n", what, (unsigned long long)pa);
    fflush(stdout);
    exit(3);
}

static struct region *find(uint64_t pa, uint64_t size)
{
    unsigned i;
    for (i = 0; i < nregions; i++)
        if (pa >= regions[i].base && pa - regions[i].base <= regions[i].size && size <= regions[i].size - (pa - regions[i].base)) return &regions[i];
    return NULL;
}

uint64_t sp11hw_read_ctr_el0(void) { return UINT64_C(4) << 16; }        /* DminLine 4: 64-byte lines */
void sp11hw_dc_cvac(uint64_t va)
{
    cleaned_lines++;
    if (va < cleaned_first) cleaned_first = va;
    if (va > cleaned_last) cleaned_last = va;
}
void sp11hw_dc_civac(uint64_t va) { (void)va; }
void sp11hw_dsb_sy(void) {}
void sp11hw_isb(void) {}
uint64_t sp11hw_at_s1e2w(uint64_t va) { return va & ~UINT64_C(0xFFF); }
uint64_t sp11hw_at_s1e2r(uint64_t va) { return va & ~UINT64_C(0xFFF); }
const void *sp11hw_view(uint64_t pa, uint64_t size)
{
    struct region *r = find(pa, size);
    if (!r) fault("view outside every region", pa);
    return r->mem + (pa - r->base);
}
void sp11hw_store64(uint64_t pa, uint64_t value)
{
    struct region *r;
    if (zero_run.size && pa >= zero_run.base && pa - zero_run.base < zero_run.size) {
        if (pa != zero_run.next || value) fault("zero store out of sequence or not zero", pa);
        zero_run.next += 8;
        return;
    }
    r = find(pa, 8);
    if (!r) fault("store outside every region", pa);
    memcpy(r->mem + (pa - r->base), &value, 8);
}
void sp11hw_store8(uint64_t pa, uint8_t value) { (void)pa, (void)value; }
void sw_head_flush(struct sw_ctx *c) { (void)c; }

static void on_alarm(int s)
{
    (void)s;
    printf("RESULT time_limit bad the scenario did not finish in time\n");
    fflush(stdout);
    _exit(4);
}

static void check(const char *name, int ok, const char *detail)
{
    printf("RESULT %s %s %s\n", name, ok ? "ok" : "bad", detail);
    failed |= !ok;
}

static void put64(uint8_t *b, uint64_t v)
{
    unsigned i;
    for (i = 0; i < 8; i++) b[i] = (uint8_t)(v >> (8 * i));
}

static void entry(uint8_t *table, unsigned i, uint64_t dest, uint64_t size, uint64_t file_offset, int zero, const uint8_t digest[32])
{
    uint8_t *e = table + SP11SB_CHUNK_BYTES * i;
    memset(e, 0, SP11SB_CHUNK_BYTES);
    put64(e + offsetof(struct sp11sb_chunk, dest), dest);
    put64(e + offsetof(struct sp11sb_chunk, size), size);
    put64(e + offsetof(struct sp11sb_chunk, file_offset), zero ? 0 : file_offset);
    e[offsetof(struct sp11sb_chunk, kind)] = SP11SB_KIND_OTHER;
    e[offsetof(struct sp11sb_chunk, flags)] = zero ? SP11SB_CHUNK_ZERO : 0;
    if (!zero) memcpy(e + offsetof(struct sp11sb_chunk, sha256), digest, 32);
}

static uint8_t pin_bytes[SP11SB_PIN_BYTES];
static void init_ctx(struct sw_ctx *c, const uint8_t *file, uint64_t len, uint32_t count)
{
    memset(c, 0, sizeof *c);
    put64(pin_bytes + offsetof(struct sp11sb_pin, place_base), GUEST_BASE);
    put64(pin_bytes + offsetof(struct sp11sb_pin, place_size), GUEST_SIZE + UINT64_C(0x28000000));
    put64(pin_bytes + offsetof(struct sp11sb_pin, guest_base), GUEST_BASE);
    put64(pin_bytes + offsetof(struct sp11sb_pin, guest_size), GUEST_SIZE);
    c->pin = pin_bytes;
    c->guest = file;
    c->guest_len = len;
    c->head.r.chunks_total = count;
}

static void scenario_zero(void)
{
    static uint8_t file[4096];
    struct sw_ctx c;
    char detail[200];
    uint32_t bad = 0;
    const uint64_t dest = GUEST_BASE + UINT64_C(0x100000), size = UINT64_C(0x100010000);
    uint64_t first = dest & ~(LINE_BYTES - 1), last = (dest + size - 1) & ~(LINE_BYTES - 1);
    entry(file + SP11SB_GUEST_HEAD_BYTES, 0, dest, size, 0, 1, NULL);
    init_ctx(&c, file, sizeof file, 1);
    zero_run.base = dest;
    zero_run.size = size;
    zero_run.next = dest;
    alarm(60);
    check("zero.chunks_check", sw_chunks_check(&c, &bad) == 0, "");
    check("zero.copy_returns_0", sw_copy_all(&c, &bad) == 0, "");
    snprintf(detail, sizeof detail, "next=%#llx want %#llx", (unsigned long long)zero_run.next, (unsigned long long)(dest + size));
    check("zero.every_word_stored_once_in_order", zero_run.next == dest + size, detail);
    snprintf(detail, sizeof detail, "lines=%llu want %llu first=%#llx last=%#llx", (unsigned long long)cleaned_lines, (unsigned long long)((last - first) / LINE_BYTES + 1),
             (unsigned long long)cleaned_first, (unsigned long long)cleaned_last);
    check("zero.every_line_cleaned_once", cleaned_lines == (last - first) / LINE_BYTES + 1 && cleaned_first == first && cleaned_last == last, detail);
    snprintf(detail, sizeof detail, "bytes_copied=%#llx chunks_copied=%u", (unsigned long long)c.head.r.bytes_copied, c.head.r.chunks_copied);
    check("zero.counters_are_64_bit", c.head.r.bytes_copied == size && c.head.r.chunks_copied == 1, detail);
}

static void scenario_sparse(const char *path)
{
    const uint64_t file_len = UINT64_C(1) << 31;
    const struct {
        uint64_t dest, size, file_offset;
        int zero;
    } chunks[] = {
        {UINT64_C(0x88ED00000), MIB, 0x1000, 0},
        {UINT64_C(0x8A0000000), 0x10000, 0, 1},
        {UINT64_C(0x8F0000000), MIB, UINT64_C(1) << 30, 0},
        {UINT64_C(0x8FFFF8000), 0x10000, (UINT64_C(1) << 31) - 0x10000, 0},      /* crosses 0x900000000; the last bytes of the file */
    };
    const unsigned n = sizeof chunks / sizeof chunks[0];
    uint8_t table[SP11SB_CHUNK_BYTES * 4];
    struct sw_ctx c;
    char detail[200];
    const uint8_t *file;
    uint32_t bad = 0;
    unsigned i;
    int same = 1, rc, fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    uint64_t copied = 0;
    if (fd < 0 || ftruncate(fd, (off_t)file_len)) exit(2);
    memset(table, 0, sizeof table);
    for (i = 0; i < n; i++) {
        uint8_t digest[32] = {0};
        if (!chunks[i].zero) {
            uint8_t *data = malloc((size_t)chunks[i].size);
            uint64_t k;
            if (!data) exit(2);
            for (k = 0; k < chunks[i].size; k++) data[k] = (uint8_t)(k * 131 + i * 17 + (k >> 8));
            if (pwrite(fd, data, (size_t)chunks[i].size, (off_t)chunks[i].file_offset) != (ssize_t)chunks[i].size) exit(2);
            q1n1_sha256(data, (size_t)chunks[i].size, digest);
            free(data);
            copied += chunks[i].size;
        }
        entry(table, i, chunks[i].dest, chunks[i].size, chunks[i].file_offset, chunks[i].zero, digest);
        regions[nregions].base = chunks[i].dest;
        regions[nregions].size = chunks[i].size;
        regions[nregions].mem = malloc((size_t)chunks[i].size);
        if (!regions[nregions].mem) exit(2);
        memset(regions[nregions].mem, 0xEE, (size_t)chunks[i].size);       /* a zero chunk is checked to have been cleared, so it starts dirty */
        nregions++;
    }
    if (pwrite(fd, table, sizeof table, SP11SB_GUEST_HEAD_BYTES) != (ssize_t)sizeof table) exit(2);
    file = mmap(NULL, (size_t)file_len, PROT_READ, MAP_PRIVATE, fd, 0);
    if (file == MAP_FAILED) exit(2);
    init_ctx(&c, file, file_len, n);
    alarm(60);
    check("sparse.chunks_check", sw_chunks_check(&c, &bad) == 0, "");
    check("sparse.copy_returns_0", sw_copy_all(&c, &bad) == 0, "");
    for (i = 0; i < n; i++) {
        if (chunks[i].zero) {
            uint64_t k;
            for (k = 0; k < chunks[i].size; k++) same &= regions[i].mem[k] == 0;
        } else {
            same &= memcmp(regions[i].mem, file + chunks[i].file_offset, (size_t)chunks[i].size) == 0;
        }
    }
    check("sparse.destination_equals_source_incl_offset_2G_less_64K", same, "");
    snprintf(detail, sizeof detail, "bytes_copied=%llu want %llu", (unsigned long long)c.head.r.bytes_copied, (unsigned long long)(copied + chunks[1].size));
    check("sparse.bytes_copied", c.head.r.bytes_copied == copied + chunks[1].size, detail);
    check("sparse.verify_returns_0", sw_verify_all(&c, &bad) == 0, "");
    snprintf(detail, sizeof detail, "hashed=%u bytes_hashed=%llu", c.head.r.chunks_hashed, (unsigned long long)c.head.r.bytes_hashed);
    check("sparse.hashed_the_data_chunks", c.head.r.chunks_hashed == 3 && c.head.r.bytes_hashed == copied, detail);
    regions[3].mem[0x1234] ^= 1;
    rc = sw_verify_all(&c, &bad);
    snprintf(detail, sizeof detail, "rc=%d chunk=%u", rc, bad);
    check("sparse.a_flipped_byte_in_the_last_chunk_is_refused", rc == SP11SB_E_COPY_VERIFY && bad == 3, detail);
    regions[3].mem[0x1234] ^= 1;
    regions[1].mem[0x20] = 1;
    rc = sw_verify_all(&c, &bad);
    snprintf(detail, sizeof detail, "rc=%d chunk=%u", rc, bad);
    check("sparse.a_nonzero_byte_in_the_zero_chunk_is_refused", rc == SP11SB_E_COPY_VERIFY && bad == 1, detail);
    unlink(path);
}

/* One entry against a 2 GiB file and the 6 GiB window: what sw_copy_all would read again just before the stores. */
static int refused(uint64_t dest, uint64_t size, uint64_t file_offset, int zero)
{
    static uint8_t file[4096];
    struct sw_ctx c;
    uint32_t bad = 0;
    uint8_t digest[32];
    memset(digest, 1, sizeof digest);
    entry(file + SP11SB_GUEST_HEAD_BYTES, 0, dest, size, file_offset, zero, digest);
    init_ctx(&c, file, UINT64_C(1) << 31, 1);
    return sw_chunks_check(&c, &bad);
}

static void scenario_refuse(void)
{
    const uint64_t end = GUEST_BASE + GUEST_SIZE;
    check("refuse.file_offset_at_2_pow_32", refused(GUEST_BASE, 0x1000, (UINT64_C(1) << 32) + 0x1000, 0) == SP11SB_E_CHUNK_EOF, "");
    check("refuse.size_whose_end_wraps_32_bits", refused(GUEST_BASE, UINT64_C(0x100001000), 0x1000, 0) == SP11SB_E_CHUNK_EOF, "");
    check("refuse.one_line_past_the_end_of_the_file", refused(GUEST_BASE, 0x2000, (UINT64_C(1) << 31) - 0x1000, 0) == SP11SB_E_CHUNK_EOF, "");
    check("refuse.one_line_past_the_end_of_the_window", refused(end - 0x1000, 0x2000, 0x1000, 0) == SP11SB_E_CHUNK_RANGE, "");
    check("refuse.a_zero_chunk_past_the_end_of_the_window", refused(end - 0x1000, 0x2000, 0, 1) == SP11SB_E_CHUNK_RANGE, "");
    check("refuse.a_destination_that_wraps_64_bits", refused(UINT64_C(0xFFFFFFFFFFFFF000), 0x2000, 0x1000, 0) == SP11SB_E_CHUNK_RANGE, "");
    check("refuse.the_last_bytes_of_file_and_window_are_accepted", refused(end - 0x1000, 0x1000, (UINT64_C(1) << 31) - 0x1000, 0) == 0, "");
}

int main(int argc, char **argv)
{
    struct rusage ru;
    signal(SIGALRM, on_alarm);
    if (argc == 2 && !strcmp(argv[1], "zero")) scenario_zero();
    else if (argc == 3 && !strcmp(argv[1], "sparse")) scenario_sparse(argv[2]);
    else if (argc == 2 && !strcmp(argv[1], "refuse")) scenario_refuse();
    else return 2;
    getrusage(RUSAGE_SELF, &ru);
    printf("MAXRSS %llu\n", (unsigned long long)ru.ru_maxrss);
    return failed;
}
