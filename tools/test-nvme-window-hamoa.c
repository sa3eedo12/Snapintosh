/* SPDX-License-Identifier: MIT */
/* Native half of tools/test-nvme-window-hamoa.py: compat/nvme-host.c's SP11 takeover contract under ASan and UBSan.
 * It is a driver, not a test: it answers the lines the Python test writes on stdin, one answer line per input line, so
 * the C code and the Python host model are compared on identical inputs. It runs only through that test.
 *
 *   clang -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=undefined \
 *     -DSNAP_PLATFORM_SP11=1 -Icompat tools/test-nvme-window-hamoa.c compat/nvme-host.c compat/board-info.c \
 *     -o build/test-nvme-window-hamoa/driver
 *
 * Modes (every number is hexadecimal unless noted; a malformed line exits 2):
 *   codes     print the code table, the constants and the struct sizes;
 *   check     "T(16) R(82) P(16)" words      -> nvmeh_sp11_check
 *   admin     "phase opcode cdw10"            -> nvmeh_sp11_admin_verdict
 *   io        "opcode writable"               -> nvmeh_sp11_io_verdict
 *   board     "segment blob-hex"              -> board_info_validate, then nvmeh_sp11_target_from_board
 *   endpoint  "T(16) bar cfg(16 dwords)"      -> nvmeh_sp11_prove_endpoint against a fake config space
 *   adopt     "T(16) R(82) P(16) cfg(16) op..." -> nvmeh_sp11_adopt on a fake controller, then the ops
 *             op = "r:wlba:count", "w:wlba:count" or "f" (read, write, flush); lba and count in decimal.
 * The fake controller is the physical truth: every command it receives is checked against what must never be touched
 * (the record's protected extents and the two GPT zones) and against the params' write window. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nvme-host.h"

#define LINE_BYTES 32768
#define MAX_WORDS 256

static char line[LINE_BYTES];

static void die(const char *why) __attribute__((noreturn));
static void die(const char *why)
{
    printf("ERR %s\n", why);
    exit(2);
}

static unsigned words(char *text, uint64_t *out, unsigned max)
{
    unsigned n = 0;
    for (char *tok = strtok(text, " \t\r\n"); tok; tok = strtok(NULL, " \t\r\n")) {
        char *end;
        if (n == max) die("too many words");
        out[n++] = strtoull(tok, &end, 16);
        if (*end) die("bad hex word");
    }
    return n;
}

/* ---- the fake controller ------------------------------------------------------------------------------------- */
static uint8_t dma[NVMEH_WINDOW_BYTES];
static uint32_t regs[0x2000 / 4], cfg[16];
static uint64_t g_bar, g_dma, g_cfg;
static const struct nvmeh_sp11_record *g_rec;
static const struct nvmeh_params *g_params;
static unsigned sq_head, cq_tail, cq_phase, doorbells, violations, badreads;
static uint64_t clock_now;

static uint8_t pattern(uint64_t lba, unsigned k) { return (uint8_t)(lba * 131 + k * 7 + (lba >> 8)); }

static uint8_t *mem(uint64_t pa) { return pa >= g_dma && pa < g_dma + sizeof dma ? dma + (pa - g_dma) : 0; }
static uint64_t get64(const uint8_t *b) { uint64_t v; memcpy(&v, b, 8); return v; }

/* LBAs that no command may touch: every other partition and both GPT zones. */
static int forbidden(uint64_t first, uint64_t last)
{
    if (first < g_rec->first_usable || last > g_rec->last_usable) return 1;
    for (unsigned i = 0; i < g_rec->protected_count && i < NVMEH_SP11_MAX_PROTECTED; i++)
        if (first <= g_rec->protected_extents[i].last_lba && g_rec->protected_extents[i].first_lba <= last) return 1;
    return 0;
}

static void controller(unsigned tail)
{
    while (sq_head != tail) {
        uint8_t *e = mem(g_dma + NVMEH_IOSQ + sq_head * 64);
        uint32_t cdw0, nlb;
        sq_head = (sq_head + 1) % NVMEH_IO_ENTRIES;
        memcpy(&cdw0, e, 4);
        uint64_t prp1 = get64(e + 24), prp2 = get64(e + 32), lba = get64(e + 40);
        memcpy(&nlb, e + 48, 4);
        unsigned op = cdw0 & 0xff;
        uint64_t n = (nlb & 0xffff) + 1;
        if (op > 2) violations++;
        if (op) {
            if (forbidden(lba, lba + n - 1)) violations++;
            if (op == 1 && (!(g_params->flags & NVMEH_F_WRITE) || lba < g_params->write_first_lba ||
                            lba + n - 1 > g_params->write_last_lba || !(nlb & (1u << 30)))) violations++;
            uint64_t bytes = n * 512, pages = (bytes + 4095) / 4096;
            for (uint64_t pg = 0; pg < pages; pg++) {
                uint64_t pa = pg == 0 ? prp1 : pages == 2 ? prp2 : get64(mem(prp2) + 8 * (pg - 1));
                uint64_t len = bytes - pg * 4096 < 4096 ? bytes - pg * 4096 : 4096;
                uint8_t *m = mem(pa);
                if (!m || !mem(pa + len - 1)) { violations++; continue; }
                if (op == 2)
                    for (uint64_t k = 0; k < len; k++) m[k] = pattern(lba + (pg * 4096 + k) / 512, (unsigned)k % 512);
            }
        }
        uint8_t *c = mem(g_dma + NVMEH_IOCQ + cq_tail * 16);
        uint32_t dw2 = sq_head | 1u << 16, dw3 = (cdw0 >> 16) | cq_phase << 16;
        memset(c, 0, 16);
        memcpy(c + 8, &dw2, 4);
        memcpy(c + 12, &dw3, 4);
        cq_tail = (cq_tail + 1) % NVMEH_IO_ENTRIES;
        if (!cq_tail) cq_phase ^= 1;
    }
}

static uint32_t rd(void *ctx, uint64_t pa)
{
    (void)ctx;
    uint32_t v = 0;
    if (pa >= g_bar && pa + 4 <= g_bar + sizeof regs) return regs[(pa - g_bar) / 4];
    if (pa >= g_cfg && pa + 4 <= g_cfg + sizeof cfg) return cfg[(pa - g_cfg) / 4];
    uint8_t *m = mem(pa);
    if (!m || !mem(pa + 3)) { badreads++; return 0; }
    memcpy(&v, m, 4);
    return v;
}
static void wr(void *ctx, uint64_t pa, uint32_t v)
{
    (void)ctx;
    if (pa >= g_bar && pa + 4 <= g_bar + sizeof regs) {
        regs[(pa - g_bar) / 4] = v;
        if (pa == g_bar + 0x1008) { doorbells++; controller(v); }               /* SQ 1 tail, DSTRD 0 */
        return;
    }
    uint8_t *m = mem(pa);
    if (!m || !mem(pa + 3)) { violations++; return; }
    memcpy(m, &v, 4);
}
static void clean(void *ctx, uint64_t pa, uint64_t size) { (void)ctx; (void)pa; (void)size; }
static uint64_t now(void *ctx) { (void)ctx; return clock_now += 10; }
static const struct nvmeh_ops ops = {0, rd, wr, clean, now};

static void fake_reset(const struct nvmeh_params *p, const struct nvmeh_sp11_record *r)
{
    g_bar = p->bar; g_dma = p->dma_base; g_rec = r; g_params = p;
    memset(regs, 0, sizeof regs);
    memset(dma, 0, sizeof dma);
    regs[0x14 / 4] = 0x460001; regs[0x1c / 4] = 1;
    regs[0x24 / 4] = (NVMEH_ADMIN_ENTRIES - 1) << 16 | (NVMEH_ADMIN_ENTRIES - 1);
    regs[0x28 / 4] = (uint32_t)(g_dma + NVMEH_ASQ); regs[0x2c / 4] = (uint32_t)((g_dma + NVMEH_ASQ) >> 32);
    regs[0x30 / 4] = (uint32_t)(g_dma + NVMEH_ACQ); regs[0x34 / 4] = (uint32_t)((g_dma + NVMEH_ACQ) >> 32);
    sq_head = (unsigned)p->io_tail; cq_tail = (unsigned)p->io_head; cq_phase = (unsigned)p->io_phase;
    /* Slots before the head hold this lap's completions, as a real controller left them; a zeroed slot would read as
     * the next lap's entry once the head wraps. */
    for (unsigned i = 0; i < (unsigned)p->io_head && i < NVMEH_IO_ENTRIES; i++)
        dma[NVMEH_IOCQ + 16 * i + 14] |= (uint8_t)p->io_phase;
    doorbells = violations = badreads = 0;
    clock_now = 0;
}

/* ---- modes --------------------------------------------------------------------------------------------------- */
#define CODES(X) X(ABI) X(RESERVED) X(UNKNOWN_ECAM) X(UNKNOWN_BDF) X(UNKNOWN_SMMU) X(UNKNOWN_STREAM) \
    X(UNKNOWN_EUI64) X(UNKNOWN_GENERATION) X(UNKNOWN_DEVICE) X(TARGET_INVALID) X(RECORD_UNCLEAN) X(STALE) \
    X(SEGMENT) X(ECAM) X(BUS_RANGE) X(BDF) X(CFG_ADDRESS) X(CLASS) X(EUI64) X(SMMU_BASE) X(SMMU_MODE) \
    X(DMA_UNPROVEN) X(STREAM) X(LBA_FORMAT) X(METADATA) X(PARAMS) X(GEOMETRY) X(PROTECTED) X(WINDOW_ORDER) \
    X(WINDOW_SIZE) X(WINDOW_RANGE) X(WINDOW_GPT) X(WINDOW_ALIGN) X(WINDOW_GUARD) X(OVERLAP) X(WRITE_WINDOW) \
    X(WRITE_UNAUTHORISED) X(NO_DEVICE) X(DEVICE_DISABLED) X(HEADER_TYPE) X(BAR) X(ADMIN_PHASE) X(ADMIN_OPCODE) \
    X(ADMIN_CNS) X(ADMIN_LOG) X(ADMIN_FEATURE) X(ADMIN_SAVE) X(IO_OPCODE) X(IO_READONLY)

static void mode_codes(void)
{
#define PRINT_CODE(name) printf("code %s %d %s\n", #name, NVMEH_E_SP11_##name, nvmeh_sp11_strerror(NVMEH_E_SP11_##name));
    CODES(PRINT_CODE)
#define PRINT_CONST(name) printf("const %s %llu\n", #name, (unsigned long long)(name));
    PRINT_CONST(NVMEH_SP11_ABI) PRINT_CONST(NVMEH_SP11_LBA_BYTES) PRINT_CONST(NVMEH_SP11_ALIGN_LBAS)
    PRINT_CONST(NVMEH_SP11_GUARD_LBAS) PRINT_CONST(NVMEH_SP11_EDGE_LBAS) PRINT_CONST(NVMEH_SP11_MIN_WINDOW_LBAS)
    PRINT_CONST(NVMEH_SP11_GPT_LBAS) PRINT_CONST(NVMEH_SP11_MAX_PROTECTED) PRINT_CONST(NVMEH_SP11_CLASS_NVME)
    PRINT_CONST(NVMEH_SP11_IPA_LIMIT) PRINT_CONST(NVMEH_SP11_K_ALL) PRINT_CONST(NVMEH_SP11_R_ALL)
    PRINT_CONST(NVMEH_SP11_SMMU_BYPASS) PRINT_CONST(NVMEH_SP11_SMMU_S2_IDENTITY) PRINT_CONST(NVMEH_F_ENABLE)
    PRINT_CONST(NVMEH_F_WRITE) PRINT_CONST(NVMEH_SP11_PHASE_TAKEOVER) PRINT_CONST(NVMEH_SP11_PHASE_ADOPTED)
    PRINT_CONST(NVMEH_SP11_K_ECAM) PRINT_CONST(NVMEH_SP11_K_BDF) PRINT_CONST(NVMEH_SP11_K_SMMU)
    PRINT_CONST(NVMEH_SP11_K_STREAM) PRINT_CONST(NVMEH_SP11_K_EUI64) PRINT_CONST(NVMEH_SP11_K_GENERATION)
    PRINT_CONST(NVMEH_SP11_R_CLEAN) PRINT_CONST(NVMEH_SP11_R_DMA_PROVEN) PRINT_CONST(NVMEH_SP11_R_WRITE_AUTH)
    printf("size target %zu\nsize record %zu\nsize params %zu\nsize state %zu\n", sizeof(struct nvmeh_sp11_target),
           sizeof(struct nvmeh_sp11_record), sizeof(struct nvmeh_params), sizeof(struct nvmeh_state));
    printf("size params_words %d\nsize state_words %d\n", NVMEH_PARAMS_WORDS, NVMEH_STATE_WORDS);
}

#define TW (sizeof(struct nvmeh_sp11_target) / 8)
#define RW (sizeof(struct nvmeh_sp11_record) / 8)
#define PW (sizeof(struct nvmeh_params) / 8)

static void mode_check(void)
{
    uint64_t w[MAX_WORDS];
    while (fgets(line, sizeof line, stdin)) {
        struct nvmeh_sp11_target t; struct nvmeh_sp11_record r; struct nvmeh_params p;
        if (words(line, w, MAX_WORDS) != TW + RW + PW) die("check needs target, record and params words");
        memcpy(&t, w, sizeof t); memcpy(&r, w + TW, sizeof r); memcpy(&p, w + TW + RW, sizeof p);
        printf("%d\n", nvmeh_sp11_check(&t, &r, &p));
    }
}

static void mode_admin(void)
{
    uint64_t w[8];
    while (fgets(line, sizeof line, stdin)) {
        if (words(line, w, 8) != 3) die("admin needs phase opcode cdw10");
        printf("%d\n", nvmeh_sp11_admin_verdict((unsigned)w[0], (unsigned)w[1], (uint32_t)w[2]));
    }
}

static void mode_io(void)
{
    uint64_t w[8];
    while (fgets(line, sizeof line, stdin)) {
        if (words(line, w, 8) != 2) die("io needs opcode writable");
        printf("%d\n", nvmeh_sp11_io_verdict((unsigned)w[0], (unsigned)w[1]));
    }
}

static int hexval(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; }

static void mode_board(void)
{
    while (fgets(line, sizeof line, stdin)) {
        char *end, *blob_hex;
        uint64_t segment = strtoull(line, &end, 16);
        while (*end == ' ') end++;
        blob_hex = end;
        size_t digits = strcspn(blob_hex, "\r\n");
        if (digits % 2) die("odd blob hex");
        size_t len = digits / 2;
        void *blob = malloc(len ? len : 1);                                     /* exact size: ASan sees overreads */
        uint8_t *bytes = blob;
        for (size_t i = 0; i < len; i++) {
            int hi = hexval(blob_hex[2 * i]), lo = hexval(blob_hex[2 * i + 1]);
            if (hi < 0 || lo < 0) die("bad blob hex");
            bytes[i] = (uint8_t)(hi << 4 | lo);
        }
        const struct board_info *info = NULL;
        int rc = board_info_validate(blob, len, &info);
        if (rc) printf("INVALID %d\n", rc);
        else {
            struct nvmeh_sp11_target t;
            rc = nvmeh_sp11_target_from_board(info, segment, &t);
            printf("%d %llx %llx %llx %llx %llx %llx\n", rc, (unsigned long long)t.known, (unsigned long long)t.segment,
                   (unsigned long long)t.ecam_base, (unsigned long long)t.start_bus, (unsigned long long)t.end_bus,
                   (unsigned long long)t.smmu_base);
        }
        free(blob);
    }
}

static void load_cfg(const uint64_t *w)
{
    for (unsigned i = 0; i < 16; i++) cfg[i] = (uint32_t)w[i];
}

static uint64_t ecam_address(const struct nvmeh_sp11_target *t)
{
    return t->ecam_base + ((t->bdf >> 8) << 20 | ((t->bdf >> 3) & 0x1f) << 15 | (t->bdf & 7) << 12);
}

static void mode_endpoint(void)
{
    uint64_t w[MAX_WORDS];
    while (fgets(line, sizeof line, stdin)) {
        struct nvmeh_sp11_target t; struct nvmeh_params p;
        if (words(line, w, MAX_WORDS) != TW + 1 + 16) die("endpoint needs target, bar and 16 config dwords");
        memcpy(&t, w, sizeof t);
        memset(&p, 0, sizeof p);
        p.bar = w[TW];
        load_cfg(w + TW + 1);
        g_cfg = ecam_address(&t); g_bar = 0; g_dma = 0; badreads = 0;
        printf("%d %u\n", nvmeh_sp11_prove_endpoint(&ops, &t, &p), badreads);
    }
}

static void mode_adopt(void)
{
    uint64_t w[MAX_WORDS];
    char text[LINE_BYTES];
    while (fgets(line, sizeof line, stdin)) {
        struct nvmeh_sp11_target t; struct nvmeh_sp11_record r; struct nvmeh_params p; struct nvmeh_state s;
        strcpy(text, line);
        /* The first TW + RW + PW + 16 tokens are numbers; the rest are ops. */
        char *rest = text;
        unsigned n = 0;
        for (; n < TW + RW + PW + 16; n++) {
            char *end;
            while (*rest == ' ') rest++;
            if (!*rest || *rest == '\n') die("adopt line too short");
            w[n] = strtoull(rest, &end, 16);
            if (*end != ' ' && *end != '\n' && *end) die("bad hex word");
            rest = end;
        }
        memcpy(&t, w, sizeof t); memcpy(&r, w + TW, sizeof r); memcpy(&p, w + TW + RW, sizeof p);
        load_cfg(w + TW + RW + PW);
        memset(&s, 0, sizeof s);
        fake_reset(&p, &r);
        g_cfg = ecam_address(&t);
        int rc = nvmeh_sp11_adopt(&ops, &t, &r, &p, &s, 0x1000, 0x100000);
        printf("%d %lld %u %u %u", rc, (long long)(int64_t)s.prepare, doorbells, violations, badreads);
        for (char *tok = strtok(rest, " \r\n"); tok; tok = strtok(NULL, " \r\n")) {
            unsigned before = doorbells;
            unsigned long long lba = 0, count = 0, at = 0;
            int r2;
            if (tok[0] == 'f') r2 = nvmeh_flush(&ops, &p, &s);
            else if ((tok[0] != 'r' && tok[0] != 'w') || sscanf(tok + 1, ":%llu:%llu:%llu", &lba, &count, &at) < 2) die("bad op");
            else if (tok[0] == 'r') r2 = nvmeh_read(&ops, &p, &s, lba, (uint32_t)count, (uint32_t)at);
            else r2 = nvmeh_write(&ops, &p, &s, lba, (uint32_t)count, (uint32_t)at);
            /* A read that succeeded must hold the pattern of the LBAs it asked for, where it asked for them. */
            if (tok[0] == 'r' && !r2)
                for (unsigned long long k = 0; k < count * 512; k++)
                    if (dma[NVMEH_DATA + at + k] != pattern(p.first_lba + lba + k / 512, (unsigned)(k % 512))) {
                        violations++;
                        break;
                    }
            printf(" %d:%u", r2, doorbells - before);
        }
        printf(" v%u\n", violations);
    }
}

int main(int argc, char **argv)
{
    if (argc != 2) die("usage: driver codes|check|admin|io|board|endpoint|adopt");
    _Static_assert(sizeof(struct nvmeh_sp11_target) == 128 && sizeof(struct nvmeh_params) == 128, "word layouts");
    if (!strcmp(argv[1], "codes")) mode_codes();
    else if (!strcmp(argv[1], "check")) mode_check();
    else if (!strcmp(argv[1], "admin")) mode_admin();
    else if (!strcmp(argv[1], "io")) mode_io();
    else if (!strcmp(argv[1], "board")) mode_board();
    else if (!strcmp(argv[1], "endpoint")) mode_endpoint();
    else if (!strcmp(argv[1], "adopt")) mode_adopt();
    else die("unknown mode");
    return 0;
}
