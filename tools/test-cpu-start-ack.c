/* SPDX-License-Identifier: MIT */
/* Host test of compat/cpu-start-ack.h: which stage-2 faults the opt-in CPU-start acknowledge takes, and what it counts. The oracle below is written from the
 * specification in that header's comment and shares nothing with it; tools/test-cpu-start-ack.py compiles this file against the real header and against seeded
 * faults of it, and every fault must make this program fail. EXPERIMENTAL; no hardware. */
#include "cpu-start-ack.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

#define BLOCK UINT64_C(0x300734000)

static uint64_t esr_of(unsigned ec, unsigned isv, unsigned sas, unsigned srt, unsigned s1ptw, unsigned wnr, unsigned dfsc)
{
    return (uint64_t)ec << 26 | UINT64_C(1) << 25 | (uint64_t)isv << 24 | (uint64_t)sas << 22 | (uint64_t)srt << 16 | (uint64_t)s1ptw << 7 | (uint64_t)wnr << 6 | dfsc;
}

static int match(uint64_t esr, uint64_t ipa, uint64_t hpfar, unsigned *offset)
{
    struct dc_access a = dc_decode(esr);
    return cpu_start_ack_match(esr, ipa, hpfar, &a, offset);
}

/* The specification: a data abort (EC 0x24), no stage-1 walk fault, a translation fault of any level, a described 32-bit store, one of the three registers, and an
 * HPFAR_EL2 that, when it holds an address, names the same page. */
static int oracle(unsigned ec, unsigned isv, unsigned sas, unsigned s1ptw, unsigned wnr, unsigned dfsc, uint64_t ipa, uint64_t hpfar)
{
    int store32 = ec == 0x24 && isv && sas == 2 && wnr && !s1ptw && dfsc >= 4 && dfsc <= 7;
    int register_ok = ipa == BLOCK + 4 || ipa == BLOCK + 8 || ipa == BLOCK + 12;
    int hpfar_ok = !(hpfar & UINT64_C(0xfffffffffff0)) || ((hpfar >> 4) << 12) == (ipa & ~UINT64_C(0xfff));
    return store32 && register_ok && hpfar_ok;
}

static void sweep(void)
{
    static const uint64_t ipas[] = {0, 4, BLOCK - 4, BLOCK - 1, BLOCK, BLOCK + 1, BLOCK + 2, BLOCK + 3, BLOCK + 4, BLOCK + 5, BLOCK + 6, BLOCK + 7, BLOCK + 8, BLOCK + 9,
                                    BLOCK + 10, BLOCK + 11, BLOCK + 12, BLOCK + 13, BLOCK + 14, BLOCK + 15, BLOCK + 16, BLOCK + 0x100, BLOCK + 0xffc, BLOCK + 0x1004,
                                    BLOCK + 0x4004, BLOCK + 0xc000, UINT64_C(0x300730004), UINT64_C(0x300740004), UINT64_C(0x300738008), UINT64_C(0x30073400c) | UINT64_C(1) << 36,
                                    (BLOCK + 4) ^ UINT64_C(1) << 40, ~UINT64_C(0)};
    static const uint64_t hpfars[] = {0, 0x3007340, 0x3007341, 0x3007350, 0x300734f, 0xf, 0x3007340 | UINT64_C(1) << 44};
    static const unsigned srts[] = {0, 23, 30, 31};
    unsigned long accepted = 0, tried = 0;
    for (unsigned ec = 0; ec < 64; ec++)
        for (unsigned isv = 0; isv < 2; isv++)
            for (unsigned sas = 0; sas < 4; sas++)
                for (unsigned s1ptw = 0; s1ptw < 2; s1ptw++)
                    for (unsigned wnr = 0; wnr < 2; wnr++)
                        for (unsigned dfsc = 0; dfsc < 64; dfsc++)
                            for (size_t i = 0; i < sizeof ipas / sizeof *ipas; i++)
                                for (size_t h = 0; h < sizeof hpfars / sizeof *hpfars; h++) {
                                    unsigned srt = srts[(ec + sas + dfsc + i + h) % 4];
                                    uint64_t esr = esr_of(ec, isv, sas, srt, s1ptw, wnr, dfsc);
                                    unsigned offset = 0xdead;
                                    int got = match(esr, ipas[i], hpfars[h], &offset);
                                    int want = oracle(ec, isv, sas, s1ptw, wnr, dfsc, ipas[i], hpfars[h]);
                                    tried++;
                                    accepted += (unsigned long)want;
                                    assert(got == want);
                                    if (got) assert(offset == ipas[i] - BLOCK);
                                    else assert(offset == 0xdead);
                                }
    assert(tried == 64UL * 2 * 4 * 2 * 2 * 64 * (sizeof ipas / sizeof *ipas) * (sizeof hpfars / sizeof *hpfars));
    assert(accepted > 0 && accepted < tried / 1000);
}

/* A decoder that reports fields without the access being described, or an exception class that is not a data abort, must not be believed: dc_decode zeroes the
 * fields in those cases, so only a hand-made struct reaches these branches, and they are the checks that keep the match independent of the decoder. */
static void synthetic(void)
{
    unsigned offset = 0;
    uint64_t good = 0x93970045;
    struct dc_access ok = {.valid = 1, .write = 1, .size = 4, .reg = 23};
    assert(cpu_start_ack_match(good, BLOCK + 4, 0, &ok, &offset) && offset == 4);
    struct dc_access bad;
    bad = ok; bad.valid = 0; assert(!cpu_start_ack_match(good, BLOCK + 4, 0, &bad, &offset));
    bad = ok; bad.write = 0; assert(!cpu_start_ack_match(good, BLOCK + 4, 0, &bad, &offset));
    for (unsigned size = 0; size < 17; size++) {
        bad = ok; bad.size = (uint8_t)size;
        assert(cpu_start_ack_match(good, BLOCK + 4, 0, &bad, &offset) == (size == 4));
    }
    for (unsigned ec = 0; ec < 64; ec++)
        assert(cpu_start_ack_match(esr_of(ec, 1, 2, 23, 0, 1, 5), BLOCK + 4, 0, &ok, &offset) == (ec == 0x24));
    assert(!cpu_start_ack_match(good | 1u << 7, BLOCK + 4, 0, &ok, &offset));            /* S1PTW */
    assert(!cpu_start_ack_match((good & ~UINT64_C(0x3f)) | 0x0d, BLOCK + 4, 0, &ok, &offset)); /* a permission fault */
    assert(!cpu_start_ack_match((good & ~UINT64_C(0x3f)) | 0x21, BLOCK + 4, 0, &ok, &offset)); /* an alignment fault */
}

/* The three syndromes the unit's kernel would use for the three registers, with the registers that the stock helper may pick, and the unit's own (boot 21). */
static void the_units_syndrome(void)
{
    unsigned offset = 0;
    assert(match(0x93970045, UINT64_C(0x300734004), 0x3007340, &offset) && offset == 4);
    assert(match(0x93970045, UINT64_C(0x300734004), 0, &offset) && offset == 4);
    assert(esr_of(0x24, 1, 2, 23, 0, 1, 5) == 0x93970045);                         /* the fields of the unit's syndrome, put back together */
    for (unsigned srt = 0; srt < 32; srt++) {
        assert(match(esr_of(0x24, 1, 2, srt, 0, 1, 5), BLOCK + 8, 0, &offset) && offset == 8);
        assert(match(esr_of(0x24, 1, 2, srt, 0, 1, 4), BLOCK + 12, 0, &offset) && offset == 12);
        assert(match(esr_of(0x24, 1, 2, srt, 0, 1, 7), BLOCK + 4, 0, &offset) && offset == 4);
    }
    assert(!match(esr_of(0x24, 1, 2, 23, 0, 0, 5), BLOCK + 4, 0, &offset));          /* the same access as a read */
    assert(!match(esr_of(0x24, 1, 3, 23, 0, 1, 5), BLOCK + 4, 0, &offset));          /* a 64-bit store */
    assert(!match(esr_of(0x24, 1, 1, 23, 0, 1, 5), BLOCK + 4, 0, &offset));          /* a 16-bit store */
    assert(!match(esr_of(0x24, 1, 0, 23, 0, 1, 5), BLOCK + 4, 0, &offset));          /* a byte store */
    assert(!match(esr_of(0x24, 0, 2, 23, 0, 1, 5), BLOCK + 4, 0, &offset));          /* a syndrome that does not describe the access */
    assert(!match(0x93970045, BLOCK, 0, &offset) && !match(0x93970045, BLOCK + 16, 0, &offset));
    assert(!match(0x93970045, UINT64_C(0x300734004), 0x3007350, &offset));          /* HPFAR names the next page */
}

static void accounting(void)
{
    struct cpu_start_ack s;
    memset(&s, 0, sizeof s);
    for (unsigned n = 0; n < 20; n++) {
        unsigned offset = 4 + 4 * (n % 3);
        uint64_t value = UINT64_C(0xfeed000000000000) | n;
        cpu_start_ack_note(&s, 0xfffffe00097a352cull + 4 * n, offset, value);
        assert(s.total == n + 1);
        assert(s.last_pc == 0xfffffe00097a352cull + 4 * n && s.last_what == ((uint64_t)offset << 32 | n));
        if (n < CPU_START_ACK_FIRST) assert(s.first[n].pc == s.last_pc && s.first[n].what == s.last_what);
    }
    assert(s.total == 20 && s.at[0] == 7 && s.at[1] == 7 && s.at[2] == 6);
    for (unsigned n = 0; n < CPU_START_ACK_FIRST; n++)
        assert(s.first[n].pc == 0xfffffe00097a352cull + 4 * n && (s.first[n].what >> 32) == 4 + 4 * (n % 3) && (uint32_t)s.first[n].what == n);
    struct cpu_start_ack before = s;
    for (unsigned offset = 0; offset < 40; offset++) {
        if (offset == 4 || offset == 8 || offset == 12) continue;
        cpu_start_ack_note(&s, 1, offset, 1);
        assert(memcmp(&s, &before, sizeof s) == 0);
    }
    assert(sizeof s == 22 * 8 && CPU_START_ACK_REPORT_WORDS == 24 && CPU_START_ACK_FIRST == 8);
    assert(CPU_START_ACK_MAGIC == ((uint64_t)'C' << 0 | (uint64_t)'P' << 8 | (uint64_t)'U' << 16 | (uint64_t)'A' << 24 | (uint64_t)'C' << 32 | (uint64_t)'K' << 40 |
                                  (uint64_t)'0' << 48 | (uint64_t)'1' << 56));
}

int main(void)
{
    the_units_syndrome();
    synthetic();
    accounting();
    sweep();
    puts("PASS: the CPU-start acknowledge takes exactly a described 32-bit translation-fault store to +4, +8 or +12 of the block, and counts it");
    return 0;
}
