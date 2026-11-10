/* SPDX-License-Identifier: MIT */
/* Shared harness for the host-native tests of the SP11 EL2 probe logic.
 *
 * A failing check is counted and printed with its source line instead of
 * aborting, so one run reports every disagreement between the code and its
 * documented behaviour. The allocation helpers hand out buffers of exactly
 * the requested size so that AddressSanitizer reports any access past them.
 *
 * Each test file implements the entry points declared at the end of this
 * header; tools/test-uefi-probe-logic.c owns the counters and main().
 */
#ifndef PROBE_TEST_HARNESS_H
#define PROBE_TEST_HARNESS_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern unsigned g_checks;
extern unsigned g_failures;

/* Starts a named group of checks; the previous group's result is printed. */
void section(const char *name);
void section_done(void);
void failed(int line, const char *text);

#define CHECK(cond)                                                                             \
    do {                                                                                        \
        g_checks++;                                                                             \
        if (!(cond))                                                                            \
            failed(__LINE__, #cond);                                                            \
    } while (0)

#define CHECK_EQ(a, b)                                                                          \
    do {                                                                                        \
        long long va_ = (long long)(a);                                                         \
        long long vb_ = (long long)(b);                                                         \
        g_checks++;                                                                             \
        if (va_ != vb_) {                                                                       \
            char msg_[160];                                                                     \
            snprintf(msg_, sizeof msg_, "%s == %s (%lld vs %lld)", #a, #b, va_, vb_);           \
            failed(__LINE__, msg_);                                                             \
        }                                                                                       \
    } while (0)

#define CHECK_EQU(a, b)                                                                         \
    do {                                                                                        \
        unsigned long long va_ = (unsigned long long)(a);                                       \
        unsigned long long vb_ = (unsigned long long)(b);                                       \
        g_checks++;                                                                             \
        if (va_ != vb_) {                                                                       \
            char msg_[160];                                                                     \
            snprintf(msg_, sizeof msg_, "%s == %s (0x%llX vs 0x%llX)", #a, #b, va_, vb_);       \
            failed(__LINE__, msg_);                                                             \
        }                                                                                       \
    } while (0)

#define CHECK_STR(a, b)                                                                         \
    do {                                                                                        \
        const char *sa_ = (a);                                                                  \
        const char *sb_ = (b);                                                                  \
        g_checks++;                                                                             \
        if (strcmp(sa_, sb_) != 0) {                                                            \
            char msg_[200];                                                                     \
            snprintf(msg_, sizeof msg_, "%s == \"%s\" (got \"%s\")", #a, sb_, sa_);             \
            failed(__LINE__, msg_);                                                             \
        }                                                                                       \
    } while (0)

/* Deterministic xorshift generator; every translation unit has its own
 * stream so that adding a test to one file never reshuffles another's. */
static inline uint64_t rnd(void)
{
    static uint64_t state = 0x243F6A8885A308D3ull;

    state ^= state >> 12;
    state ^= state << 25;
    state ^= state >> 27;
    return state * 0x2545F4914F6CDD1Dull;
}

static inline uint32_t rnd_below(uint32_t n)
{
    return (uint32_t)((rnd() >> 33) % n);
}

static inline void *xalloc(size_t n)
{
    void *p = calloc(1, n ? n : 1);

    if (p == NULL) {
        fputs("out of memory\n", stderr);
        exit(2);
    }
    return p;
}

/* A heap copy of exactly n bytes, so a parser that reads past n is caught. */
static inline uint8_t *exact_copy(const void *src, size_t n)
{
    uint8_t *p = xalloc(n);

    if (n)
        memcpy(p, src, n);
    return p;
}

static inline void st16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static inline void st32(uint8_t *p, uint32_t v)
{
    st16(p, (uint16_t)v);
    st16(p + 2, (uint16_t)(v >> 16));
}

static inline void st64(uint8_t *p, uint64_t v)
{
    st32(p, (uint32_t)v);
    st32(p + 4, (uint32_t)(v >> 32));
}

/* Writes memory descriptor i of a raw map with the given stride: all attribute
 * capability bits set, virtual start equal to physical. */
static inline void put_desc(uint8_t *map, size_t stride, size_t i, uint32_t type, uint64_t phys,
                            uint64_t pages)
{
    uint8_t *d = map + i * stride;

    memset(d, 0, stride);
    st32(d, type);
    st64(d + 8, phys);
    st64(d + 16, phys);
    st64(d + 24, pages);
    st64(d + 32, 0xF);
}

/* test_text.c: CRC, string builder, font, line format, layout, framebuffer. */
void test_crc(void);
void test_builder(void);
void test_font(void);
void test_line_format(void);
void test_layout(void);
void test_framebuffer(void);
void test_gop_policy(void);
void test_cache_walk(void);
/* Prints the font as the probe's own draw path renders it. */
int dump_font(void);
/* argv: --dump-screen WIDTH HEIGHT BODY_LINES PAGE. Draws one page of a
 * synthetic report through the probe's draw path and writes it as a binary
 * PPM to stdout; the layout it chose goes to stderr. */
int dump_screen(int argc, char **argv);

/* test_gopsel.c: the framebuffer choice, its console rule and its
 * same-framebuffer rule, over hand-built GOP lists and memory maps. */
void test_gop_select(void);

/* test_logic.c: device path, memory map, decision tables, system registers. */
void test_device_path(void);
void test_memmap(void);
void test_decisions(void);
void test_sysreg(void);

/* test_acpi.c: ACPI, SMBIOS, FADT and MADT parsing over synthetic memory. */
void test_acpi(void);
void test_smbios(void);
void test_fadt_madt(void);

/* test_report.c: register collection and the on-screen report. */
void test_collect(void);
void test_report(void);

/* test_psci.c: the PSCI experiment's entry policy (psci.c built natively). */
void test_psci_policy(void);

/* test_dump.c: the USB dump's caps, never-overwrite rule, error handling and
 * result states, against an in-memory model of the firmware's file protocol.
 * That file #includes dump.c (the functions under test are static). */
void test_dump_caps(void);
void test_dump_overwrite(void);
void test_dump_errors(void);
void test_dump_run(void);

#endif /* PROBE_TEST_HARNESS_H */
