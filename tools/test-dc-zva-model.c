/* SPDX-License-Identifier: MIT */
/* compat/dc-zva-model.{h,c}, natively: the trap decode against every other
 * system instruction and every single-bit corruption, the block geometry, the
 * block-confined zeroing with guard bytes, the DZP truth table, the admission
 * decision, and XNU's use of DC ZVA. XNU's zeroing algorithm (osfmk/arm64/
 * bzero.s and bzero_phys_page in loose_ends.c, xnu-12377.121.6) is re-expressed
 * here from its access pattern, not copied, and run on emulated DC ZVA to prove
 * the emulation is exact for a 64-byte block and corrupts memory for any other.
 *
 *   cc -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined \
 *      -DSNAP_PLATFORM_SP11=1 -Icompat tools/test-dc-zva-model.c \
 *      compat/dc-zva-model.c -o build/test-dc-zva-model
 *
 * With --stdin it answers one command per line for the Python oracle. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dc-zva-model.h"

static unsigned long checks;
#define CHECK(x) do { checks++; if (!(x)) { \
    fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #x); exit(1); } } while (0)

static uint64_t rng_state = UINT64_C(0x2545f4914f6cdd1d);
static uint64_t rnd(void)
{
    rng_state ^= rng_state >> 12; rng_state ^= rng_state << 25; rng_state ^= rng_state >> 27;
    return rng_state * UINT64_C(0x9e3779b97f4a7c15);
}

static uint64_t esr_for(unsigned rt)
{
    return ((uint64_t)DZV_EC_SYSTEM << 26) | (UINT64_C(1) << 25) | DZV_ISS_DC_ZVA | ((uint64_t)rt << 5);
}

/* ESR for any system instruction (op0 op1 CRn CRm op2 Rt direction). */
static uint64_t esr_sys(unsigned op0, unsigned op1, unsigned crn, unsigned crm, unsigned op2, unsigned rt, unsigned dir)
{
    return ((uint64_t)DZV_EC_SYSTEM << 26) | (UINT64_C(1) << 25) | ((uint64_t)op0 << 20) | ((uint64_t)op2 << 17) |
           ((uint64_t)op1 << 14) | ((uint64_t)crn << 10) | ((uint64_t)rt << 5) | ((uint64_t)crm << 1) | dir;
}

static void test_decode(void)
{
    unsigned rt;
    for (unsigned r = 0; r < 32; r++) {
        CHECK(dzv_decode_trap(esr_for(r), &rt) == DZV_OK && rt == r);
        CHECK(dzv_decode_trap(esr_sys(1, 3, 7, 4, 1, r, 0), &rt) == DZV_OK && rt == r);
        CHECK(dzv_decode_insn(DZV_INSN_DC_ZVA | r, &rt) == DZV_OK && rt == r);
    }
    /* Every other data cache operation, with the registers that make them system instructions. */
    const struct { unsigned op1, crm, op2; } other[] = {
        { 0, 6, 1 }, { 0, 6, 2 }, { 0, 10, 2 }, { 0, 14, 2 },                 /* IVAC ISW CSW CISW */
        { 3, 10, 1 }, { 3, 11, 1 }, { 3, 14, 1 }, { 3, 12, 1 }, { 3, 13, 1 },  /* CVAC CVAU CIVAC CVAP CVADP */
        { 3, 4, 3 }, { 3, 4, 4 }, { 3, 4, 5 }, { 3, 4, 7 }, { 3, 4, 0 }, { 3, 4, 2 }, { 3, 4, 6 }, /* GVA GZVA ZGBVA GBVA ... */
        { 3, 5, 1 }, { 3, 3, 1 }, { 2, 4, 1 }, { 7, 4, 1 },
    };
    for (unsigned i = 0; i < sizeof other / sizeof other[0]; i++)
        CHECK(dzv_decode_trap(esr_sys(1, other[i].op1, 7, other[i].crm, other[i].op2, 3, 0), &rt) == DZV_E_NOT_DC_ZVA);
    /* An MSR or MRS with the same low fields. */
    CHECK(dzv_decode_trap(esr_sys(3, 3, 7, 4, 1, 3, 0), &rt) == DZV_E_NOT_DC_ZVA);   /* op0 = 3: a register */
    CHECK(dzv_decode_trap(esr_sys(1, 3, 6, 4, 1, 3, 0), &rt) == DZV_E_NOT_DC_ZVA);   /* CRn */
    /* Direction 1 is a read. */
    CHECK(dzv_decode_trap(esr_sys(1, 3, 7, 4, 1, 3, 1), &rt) == DZV_E_READ);
    /* Wrong class, a 16-bit instruction, reserved ISS bits. */
    CHECK(dzv_decode_trap((esr_for(3) & ~(UINT64_C(0x3f) << 26)) | (UINT64_C(0x20) << 26), &rt) == DZV_E_NOT_SYSTEM_TRAP);
    CHECK(dzv_decode_trap(esr_for(3) & ~(UINT64_C(1) << 25), &rt) == DZV_E_IL);
    for (unsigned b = 22; b <= 24; b++) CHECK(dzv_decode_trap(esr_for(3) | (UINT64_C(1) << b), &rt) == DZV_E_ISS_RES0);
    CHECK(dzv_decode_trap(esr_for(3) | (UINT64_C(1) << 32), &rt) == DZV_E_ISS_RES0);
    CHECK(dzv_decode_trap(esr_for(3), 0) == DZV_E_PARAMS && dzv_decode_insn(DZV_INSN_DC_ZVA, 0) == DZV_E_PARAMS);
    /* Every single-bit corruption of a valid ESR: it is accepted only when the bit is in Rt. */
    for (unsigned b = 0; b < 64; b++) {
        const unsigned in_rt = b >= 5 && b <= 9;
        const enum dzv_status s = dzv_decode_trap(esr_for(0x15) ^ (UINT64_C(1) << b), &rt);
        CHECK((s == DZV_OK) == in_rt);
    }
    for (unsigned b = 0; b < 32; b++) {
        const unsigned in_rt = b <= 4;
        CHECK((dzv_decode_insn((DZV_INSN_DC_ZVA | 0x15) ^ (UINT32_C(1) << b), &rt) == DZV_OK) == in_rt);
    }
    /* Random ESR values: accepted exactly when they match the pattern. */
    for (unsigned i = 0; i < 200000; i++) {
        uint64_t esr = rnd();
        if (i & 1) esr = (esr & ~(UINT64_C(0xfffffffff) << 20)) | (esr_for(0) & (UINT64_C(0xfffffffff) << 20));
        const int match = (esr & ~(UINT64_C(0x1f) << 5)) == esr_for(0);
        CHECK((dzv_decode_trap(esr, &rt) == DZV_OK) == match);
    }
}

static void test_geometry(void)
{
    uint32_t n;
    for (unsigned bs = 0; bs <= 15; bs++) {
        for (unsigned dzp = 0; dzp < 2; dzp++) {
            const uint64_t v = bs | ((uint64_t)dzp << 4);
            if (bs <= 9) CHECK(dzv_block_bytes(v, &n) == DZV_OK && n == 4u << bs);
            else CHECK(dzv_block_bytes(v, &n) == DZV_E_BLOCK_SIZE);
        }
    }
    CHECK(dzv_block_bytes(0x14, &n) == DZV_OK && n == 64);      /* DZP set: still a 64-byte block */
    CHECK(dzv_block_bytes(0x04, &n) == DZV_OK && n == 64);
    for (unsigned b = 5; b < 64; b++) CHECK(dzv_block_bytes(UINT64_C(4) | (UINT64_C(1) << b), &n) == DZV_E_RESERVED_BITS);
    CHECK(dzv_block_bytes(4, 0) == DZV_E_PARAMS);
}

static void test_dzp(void)
{
    /* DZP from the controls, written case by case from the DCZID_EL0 description. */
    for (unsigned m = 0; m < 32; m++) {
        const unsigned e2h = m & 1, tge = (m >> 1) & 1, tdz = (m >> 2) & 1, d1 = (m >> 3) & 1, d2 = (m >> 4) & 1;
        unsigned want;
        if (e2h && tge) want = d2 ? 0 : 1;           /* the EL2&0 regime: SCTLR_EL2.DZE, TDZ not in effect */
        else if (tdz) want = 1;                      /* trapped to EL2 */
        else want = d1 ? 0 : 1;                      /* SCTLR_EL1.DZE */
        CHECK(dzv_dzp(e2h, tge, tdz, d1, d2) == want);
    }
    /* The A16 monitor requires 4: with DZE clear a working 64-byte block reads 0x14 instead. */
    CHECK((4u | (dzv_dzp(0, 0, 0, 0, 0) << 4)) == 0x14 && (4u | (dzv_dzp(0, 0, 0, 1, 0) << 4)) == 0x04);
    CHECK((4u | (dzv_dzp(1, 1, 0, 1, 0) << 4)) == 0x14);          /* VHE host with SCTLR_EL2.DZE clear */
}

static void test_admit(void)
{
    struct dzv_plan pl;
    const struct { uint64_t dczid; enum dzv_zva_result f; unsigned emu; enum dzv_status want; unsigned mode, tdz; } t[] = {
        { 0x04, DZV_ZVA_WORKS, 0, DZV_OK, DZV_MODE_NATIVE, 0 },
        { 0x14, DZV_ZVA_WORKS, 0, DZV_OK, DZV_MODE_NATIVE, 0 },       /* DZP is configuration, not capability */
        { 0x14, DZV_ZVA_WORKS, 1, DZV_OK, DZV_MODE_NATIVE, 0 },
        { 0x04, DZV_ZVA_UNTESTED, 1, DZV_E_UNTESTED, 0, 0 },
        { 0x14, DZV_ZVA_UNTESTED, 1, DZV_E_UNTESTED, 0, 0 },
        { 0x04, DZV_ZVA_BROKEN, 0, DZV_E_BROKEN_NO_EMULATION, 0, 0 },
        { 0x14, DZV_ZVA_BROKEN, 0, DZV_E_BROKEN_NO_EMULATION, 0, 0 },
        { 0x04, DZV_ZVA_BROKEN, 1, DZV_OK, DZV_MODE_EMULATE, 1 },
        { 0x14, DZV_ZVA_BROKEN, 1, DZV_OK, DZV_MODE_EMULATE, 1 },
        { 0x05, DZV_ZVA_WORKS, 1, DZV_E_BLOCK_SIZE, 0, 0 },           /* 128 bytes */
        { 0x03, DZV_ZVA_WORKS, 1, DZV_E_BLOCK_SIZE, 0, 0 },           /* 32 bytes */
        { 0x00, DZV_ZVA_WORKS, 1, DZV_E_BLOCK_SIZE, 0, 0 },
        { 0x0a, DZV_ZVA_WORKS, 1, DZV_E_BLOCK_SIZE, 0, 0 },
        { 0x24, DZV_ZVA_WORKS, 1, DZV_E_RESERVED_BITS, 0, 0 },
        { 0x104, DZV_ZVA_WORKS, 1, DZV_E_RESERVED_BITS, 0, 0 },
        { UINT64_C(0x14) | (UINT64_C(1) << 63), DZV_ZVA_WORKS, 1, DZV_E_RESERVED_BITS, 0, 0 },
        { 0x04, (enum dzv_zva_result)7, 1, DZV_E_PARAMS, 0, 0 },
    };
    for (unsigned i = 0; i < sizeof t / sizeof t[0]; i++) {
        memset(&pl, 0xee, sizeof pl);
        enum dzv_status s = dzv_admit(t[i].dczid, t[i].f, t[i].emu, &pl);
        CHECK(s == t[i].want);
        if (s == DZV_OK) CHECK(pl.mode == t[i].mode && pl.sctlr_el1_dze == 1 && pl.hcr_tdz == t[i].tdz && pl.block_bytes == 64);
    }
    CHECK(dzv_admit(4, DZV_ZVA_WORKS, 0, 0) == DZV_E_PARAMS);
}

/* --- block-confined zeroing -------------------------------------------------- */
#define ARENA 8192
static uint8_t arena[ARENA];

static void test_emulate(void)
{
    for (unsigned bs = 2; bs <= 9; bs++) {
        const uint32_t n = 4u << bs;
        for (unsigned k = 0; k < 4000; k++) {
            memset(arena, 0xa5, sizeof arena);
            const uint64_t base = (rnd() >> 20) & ~UINT64_C(0xf);
            const uint64_t len = ARENA - (rnd() % 64);
            const struct dzv_window w = { arena, base, len, 0 };
            const uint64_t va = base + rnd() % len;
            uint64_t blk;
            const uint64_t aligned = va & ~(uint64_t)(n - 1);
            const int inside = aligned >= base && aligned + n <= base + len;
            enum dzv_status s = dzv_emulate(&w, va, n, &blk);
            if (!inside) {
                CHECK(s == DZV_E_RANGE);
                for (size_t i = 0; i < ARENA; i++) CHECK(arena[i] == 0xa5);
                continue;
            }
            CHECK(s == DZV_OK && blk == aligned);
            for (size_t i = 0; i < ARENA; i++) {
                const int zero = base + i >= aligned && base + i < aligned + n;
                CHECK(arena[i] == (zero ? 0 : 0xa5));
            }
        }
    }
    /* Out of the window: before, after, straddling, wrapping around 2^64; nothing is written. */
    memset(arena, 0xa5, sizeof arena);
    const struct dzv_window w = { arena, 0x1000, 256, 0 };
    uint64_t blk;
    CHECK(dzv_emulate(&w, 0xfff, 64, &blk) == DZV_E_RANGE && dzv_emulate(&w, 0x1100, 64, &blk) == DZV_E_RANGE);
    const struct dzv_window short_window = { arena, 0x1000, 200, 0 };       /* ends inside the block at 0x10c0 */
    CHECK(dzv_emulate(&short_window, 0x10c8, 64, &blk) == DZV_E_RANGE && dzv_emulate(&short_window, 0x1080, 64, &blk) == DZV_OK);
    CHECK(dzv_emulate(&w, 0x1080, 128, &blk) == DZV_OK && blk == 0x1080);
    CHECK(dzv_emulate(&w, 0x10ff, 64, &blk) == DZV_OK && blk == 0x10c0);
    CHECK(dzv_emulate(&w, UINT64_MAX, 64, &blk) == DZV_E_RANGE && dzv_emulate(&w, 0, 64, &blk) == DZV_E_RANGE);
    const struct dzv_window top = { arena, UINT64_MAX - 127, 128, 0 };
    CHECK(dzv_emulate(&top, UINT64_MAX, 64, &blk) == DZV_OK && blk == UINT64_MAX - 63);
    CHECK(dzv_emulate(&top, UINT64_MAX - 127, 128, &blk) == DZV_OK);
    CHECK(dzv_emulate(&top, UINT64_MAX - 127, 256, &blk) == DZV_E_RANGE);
    /* Device memory is a fault, never a zeroing. */
    memset(arena, 0xa5, sizeof arena);
    const struct dzv_window dev = { arena, 0x1000, 256, 1 };
    CHECK(dzv_emulate(&dev, 0x1000, 64, &blk) == DZV_E_DEVICE && arena[0] == 0xa5);
    /* Parameters. */
    const uint32_t bad[] = { 0, 8, 24, 48, 4096, 8192 };
    for (unsigned i = 0; i < 6; i++) CHECK(dzv_emulate(&w, 0x1000, bad[i], &blk) == DZV_E_PARAMS);
    CHECK(dzv_emulate(0, 0x1000, 64, &blk) == DZV_E_PARAMS && dzv_emulate(&w, 0x1000, 64, 0) == DZV_E_PARAMS);
    const struct dzv_window nobytes = { 0, 0x1000, 256, 0 };
    CHECK(dzv_emulate(&nobytes, 0x1000, 64, &blk) == DZV_E_PARAMS);
}

/* --- XNU's use of DC ZVA --------------------------------------------------------- */
static unsigned zva_calls;
static uint32_t zva_block;
static struct dzv_window zva_window;

/* One DC ZVA at guest address va, emulated with the block size under test. */
static void dc_zva(uint64_t va)
{
    uint64_t blk;
    zva_calls++;
    CHECK(dzv_emulate(&zva_window, va, zva_block, &blk) == DZV_OK);
}

static void zero(uint64_t va, uint64_t n)
{
    for (uint64_t i = 0; i < n; i++) zva_window.bytes[va - zva_window.base_va + i] = 0;
}

/* xnu osfmk/arm64/bzero.s, large path (length >= 128): the first 64 bytes by stores, the first 64-aligned address
 * at or above buf + 64 as the first DC ZVA, one DC ZVA per 64 bytes while more than 64 bytes remain after it, then
 * the last 64 bytes by stores. Shorter buffers use plain stores. */
static void xnu_bzero(uint64_t buf, uint64_t len)
{
    if (len < 128) { zero(buf, len); return; }
    zero(buf, 64);
    uint64_t x3 = (buf + 64) & ~UINT64_C(63);
    const uint64_t end = buf + len, x4 = x3 + 64;
    if (end > x4) {
        uint64_t remaining = end - x4;
        for (;;) {
            dc_zva(x3);
            x3 += 64;
            const uint64_t before = remaining;
            remaining -= 64;
            if (!(before > 64)) break;
        }
        x3 += remaining;                      /* back up to (end of buffer) - 64 (remaining may have wrapped) */
    } else {
        x3 += end - x4;
    }
    zero(x3, 64);
}

/* xnu bzero_phys_page: one unrolled DC ZVA per 1 << MMU_CLINE = 64 bytes over a whole page. */
static void xnu_bzero_page(uint64_t page, uint64_t page_size)
{
    for (uint64_t off = 0; off < page_size; off += 64) dc_zva(page + off);
}

#define XNU_ARENA 4096
static uint8_t xnu_mem[XNU_ARENA];
static const uint64_t XNU_BASE = UINT64_C(0xfffffe0010000000);

/* Returns 0 when [buf, buf + len) is zero and everything else is intact, 1 when a byte outside was changed, 2 when a
 * byte inside was left. */
static int run_bzero(uint32_t block, uint64_t buf, uint64_t len)
{
    memset(xnu_mem, 0xa5, sizeof xnu_mem);
    zva_window = (struct dzv_window){ xnu_mem, XNU_BASE, XNU_ARENA, 0 };
    zva_block = block;
    xnu_bzero(buf, len);
    int outside = 0, inside = 0;
    for (uint64_t i = 0; i < XNU_ARENA; i++) {
        const uint64_t va = XNU_BASE + i;
        const int should_be_zero = va >= buf && va < buf + len;
        if (should_be_zero && xnu_mem[i] != 0) inside = 1;
        if (!should_be_zero && xnu_mem[i] != 0xa5) outside = 1;
    }
    return outside ? 1 : (inside ? 2 : 0);
}

static void test_xnu_use(void)
{
    /* With a 64-byte block the emulation is exact for every length and alignment XNU can pass. */
    for (uint64_t off = 0; off < 130; off++) {
        for (uint64_t len = 0; len < 700; len++) {
            if (off + len + 192 > XNU_ARENA) continue;
            CHECK(run_bzero(64, XNU_BASE + 192 + off, len) == 0);
        }
    }
    for (unsigned k = 0; k < 3000; k++) {
        const uint64_t off = rnd() % 200, len = rnd() % 3400;
        if (192 + off + len + 192 > XNU_ARENA) continue;
        CHECK(run_bzero(64, XNU_BASE + 192 + off, len) == 0);
    }
    /* The number of DC ZVAs is ceil((end - (first aligned address + 64)) / 64), the loop above. */
    run_bzero(64, XNU_BASE + 256, 4096 - 512);
    CHECK(zva_calls > 0);
    zva_calls = 0;
    run_bzero(64, XNU_BASE + 256, 127);
    CHECK(zva_calls == 0);                       /* under 128 bytes: stores only */
    /* The page clear is one DC ZVA per 64 bytes, for 4 KiB and 16 KiB pages. */
    static uint8_t page[16384 + 128];
    for (unsigned page_size = 4096; page_size <= 16384; page_size *= 4) {
        memset(page, 0xa5, sizeof page);
        zva_window = (struct dzv_window){ page, XNU_BASE, sizeof page, 0 };
        zva_block = 64;
        zva_calls = 0;
        xnu_bzero_page(XNU_BASE + 64, page_size);
        CHECK(zva_calls == page_size / 64);
        for (size_t i = 0; i < sizeof page; i++) CHECK(page[i] == ((i >= 64 && i < 64 + page_size) ? 0 : 0xa5));
    }
    /* A block size other than 64 breaks XNU: 128 bytes overwrites a neighbour, 32 leaves gaps. */
    unsigned over = 0, gap = 0;
    for (uint64_t off = 0; off < 130; off++)
        for (uint64_t len = 128; len < 700; len += 7) {
            if (off + len + 192 > XNU_ARENA) continue;
            over += run_bzero(128, XNU_BASE + 192 + off, len) == 1;
            gap += run_bzero(32, XNU_BASE + 192 + off, len) == 2;
        }
    CHECK(over > 100 && gap > 100);
    /* The admission refuses exactly those block sizes. */
    struct dzv_plan pl;
    CHECK(dzv_admit(5, DZV_ZVA_WORKS, 1, &pl) == DZV_E_BLOCK_SIZE && dzv_admit(3, DZV_ZVA_WORKS, 1, &pl) == DZV_E_BLOCK_SIZE);
}

static void test_rules(void)
{
    CHECK(dzv_rule_citation(DZV_OK) == 0);
    for (unsigned s = 1; s < DZV_STATUS_COUNT; s++) {
        const char *name = dzv_status_name((enum dzv_status)s), *cite = dzv_rule_citation((enum dzv_status)s);
        CHECK(name && strcmp(name, "UNKNOWN_STATUS") && cite && strlen(cite) > 20);
        CHECK(strstr(cite, "VERIFIED") || strstr(cite, "INFERRED") || strstr(cite, "model:"));
        for (unsigned t = 1; t < s; t++) CHECK(strcmp(name, dzv_status_name((enum dzv_status)t)));
    }
    CHECK(!strcmp(dzv_status_name((enum dzv_status)99), "UNKNOWN_STATUS") && !dzv_rule_citation((enum dzv_status)99));
}

/* --- differential mode ---------------------------------------------------------------
 *   E <esr hex>              decode a trap        -> NAME [rt]
 *   I <insn hex>             decode an instruction
 *   B <dczid hex>            block size           -> NAME [bytes]
 *   P e2h tge tdz d1 d2      DZP
 *   A <dczid hex> f emu      admit                -> NAME mode dze tdz bytes
 *   Z block base len va dev  emulate on a fresh window of 0xa5 bytes -> NAME [block_va zeroed_bytes] */
static int differential(void)
{
    char line[512];
    while (fgets(line, sizeof line, stdin)) {
        char cmd = line[0];
        unsigned long long a, b, c, d, e;
        unsigned rt;
        uint32_t n;
        struct dzv_plan pl;
        enum dzv_status s;
        if (cmd == 'E' && sscanf(line + 1, "%llx", &a) == 1) {
            s = dzv_decode_trap(a, &rt);
            if (s) printf("%s\n", dzv_status_name(s)); else printf("OK %u\n", rt);
        } else if (cmd == 'I' && sscanf(line + 1, "%llx", &a) == 1) {
            s = dzv_decode_insn((uint32_t)a, &rt);
            if (s) printf("%s\n", dzv_status_name(s)); else printf("OK %u\n", rt);
        } else if (cmd == 'B' && sscanf(line + 1, "%llx", &a) == 1) {
            s = dzv_block_bytes(a, &n);
            if (s) printf("%s\n", dzv_status_name(s)); else printf("OK %u\n", n);
        } else if (cmd == 'P' && sscanf(line + 1, "%llu %llu %llu %llu %llu", &a, &b, &c, &d, &e) == 5) {
            printf("%u\n", dzv_dzp((unsigned)a, (unsigned)b, (unsigned)c, (unsigned)d, (unsigned)e));
        } else if (cmd == 'A' && sscanf(line + 1, "%llx %llu %llu", &a, &b, &c) == 3) {
            s = dzv_admit(a, (enum dzv_zva_result)b, (unsigned)c, &pl);
            if (s) printf("%s\n", dzv_status_name(s)); else printf("OK %u %u %u %u\n", pl.mode, pl.sctlr_el1_dze, pl.hcr_tdz, pl.block_bytes);
        } else if (cmd == 'Z' && sscanf(line + 1, "%llu %llu %llu %llu %llu", &a, &b, &c, &d, &e) == 5) {
            static uint8_t win[4096];
            memset(win, 0xa5, sizeof win);
            struct dzv_window w = { win, b, c > sizeof win ? sizeof win : c, (uint8_t)e };
            uint64_t blk;
            s = dzv_emulate(&w, d, (uint32_t)a, &blk);
            unsigned zeroed = 0;
            for (size_t i = 0; i < sizeof win; i++) zeroed += win[i] == 0;
            if (s) printf("%s %u\n", dzv_status_name(s), zeroed); else printf("OK %" PRIu64 " %u\n", blk, zeroed);
        } else {
            puts("PARSE");
        }
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--stdin")) return differential();
    test_decode();
    test_geometry();
    test_dzp();
    test_admit();
    test_emulate();
    test_xnu_use();
    test_rules();
    printf("%lu checks\nPASS\n", checks);
    return 0;
}
