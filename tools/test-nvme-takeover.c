/* SPDX-License-Identifier: MIT */
/* compat/nvme-takeover.c against the takeover model of tools/test-nvme-host.c (the same fake controller, now with
 * registers, an admin queue, queue creation and fault injection). Driven by tools/test-nvme-takeover.py, which also
 * runs the seeded mutants of the unit against it. Host only: nothing here touches a device.
 *
 *   clang -std=gnu11 -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror \
 *     -Wno-unused-function -DSNAP_PLATFORM_SP11=1 -DSNAP_NVME_TAKEOVER=1 -DNVMET_TEST_HOOKS=1 -Icompat \
 *     tools/test-nvme-takeover.c compat/nvme-takeover.c compat/nvme-host.c compat/board-info.c compat/ans-window.c \
 *     -o build/test-nvme-takeover/harness && build/test-nvme-takeover/harness
 *
 * Output: one line "case NAME ok" or "case NAME FAIL (n)" per case and a final "N cases, M failures". The model
 * prints "FAIL takeover model: ..." for anything the unit may not do (a write to a register it does not own, a
 * doorbell of a queue that does not exist, an opcode outside the allow-lists, a read beyond the namespace, a DMA
 * outside the extent), and its memory fails on any access outside the BAR and the extent, which includes the
 * firmware's own queues. */
#define NVMEH_TEST_MODEL_ONLY 1
#include "test-nvme-host.c"
#include "nvme-takeover.h"

#define TK_EUI 0x1122334455667788ull
#define TK_BUS 0xb40000000ull          /* the extent as a translating SMMU would show it to the controller */
#define TICKS_PER_S 1000ull
#define MANY (3 * NVMEH_IO_ENTRIES + 5)

static const char *case_name;
static int case_start, cases;
#define TCHECK(c) do { if (!(c)) { printf("FAIL case=%s %s:%d %s\n", case_name, __FILE__, __LINE__, #c); failures++; } } while (0)

static void begin(const char *name)
{
    case_name = name; case_start = failures;
}
static void end(void)
{
    cases++;
    if (failures == case_start) printf("case %s ok\n", case_name);
    else printf("case %s FAIL (%d)\n", case_name, failures - case_start);
}

static struct nvmet_config cfg(void)
{
    struct nvmet_config c = {0};
    c.bar = BAR; c.cpu_base = DMA; c.bus_base = DMA; c.mem_low = 0; c.mem_high = 0x1000;
    c.ticks_per_s = TICKS_PER_S; c.max_transfer = NVMEH_DATA_BYTES; c.eui64_pin = TK_EUI; c.flags = NVMET_C_DMA_PROVEN;
    return c;
}

/* A fresh controller in the state the firmware left it (OBSERVED twice, identical: the X2 image on 2026-11-15 and S1
 * boot 22 on 2026-11-18: CC 0x460001, CSTS 1, AQA 0x10001, its own queues in boot-services memory), memory full of poison
 * so that a page the unit forgets to clear is seen. */
static void model(void)
{
    memset(&tk, 0, sizeof tk);
    memset(regs, 0, sizeof regs);
    memset(dma, 0xa5, sizeof dma);
    memset(cache_state, 0, sizeof cache_state);
    tk.active = 1; tk.cache = 1; tk.delay = 2; tk.rdy_pending = -1;
    tk.cap = 0x303C0301FFull;
    tk.nsze = DISK_LBAS; tk.mdts = 5; tk.nn = 1; tk.sqes = 0x66; tk.cqes = 0x44; tk.nlbaf = 1; tk.flbas = 0;
    tk.eui64 = TK_EUI;
    tk.cc = 0x00460001; tk.csts = 1; tk.aqa = 0x00010001; tk.asq = 0xCF3E7000ull; tk.acq = 0xCF3E8000ull; tk.enabled = 1;
    io_sq_bus = DMA + NVMEH_IOSQ; io_cq_bus = DMA + NVMEH_IOCQ; io_entries = NVMEH_IO_ENTRIES; bus_delta = 0;
    sq_head = 0; cq_tail = 0; cq_phase = 1; doorbells = 0; clock_now = 0;
    drop = fail_status = fail_nth = wrong_cid = wrong_sqid = 0;
}
static void firmware_off(void)          /* the firmware left the controller disabled */
{
    tk.cc = 0; tk.csts = 0; tk.enabled = 0;
}

/* The data the CPU sees in the buffer, through the cache model: a read that was not invalidated shows stale lines. */
static int data_ok(uint64_t lba, uint32_t count)
{
    for (uint32_t i = 0; i < count * 128u; i++) {
        uint32_t want;
        memcpy(&want, disk + lba * 512 + 4ull * i, 4);
        if (rd(0, DMA + NVMEH_DATA + 4ull * i) != want) return 0;
    }
    return 1;
}
static int audit(void)                  /* nothing the unit may not do happened */
{
    return !tk.bad_reg && !tk.bad_doorbell && !tk.bad_dma && !tk.bad_opcode && !tk.oob_reads && !tk.bad_cache;
}
static int only_allowed_opcodes(void)
{
    for (unsigned op = 0; op < 256; op++) {
        int admin_ok = op == 0x00 || op == 0x01 || op == 0x04 || op == 0x05 || op == 0x06 || op == 0x09;
        if (tk.admin_ops[op] && !admin_ok) return 0;
        if (tk.io_ops[op] && op != 0x02) return 0;
    }
    return 1;
}

static void healthy(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    const uint64_t lbas[] = {0, 1, 17, 100, DISK_LBAS - 256, DISK_LBAS - 1};
    const uint32_t counts[] = {34, 1, 8, 16, 256, 1};
    begin("healthy");
    model();
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.stage == NVMET_ST_READY);
    TCHECK(tk.cc_on_enable == 0x00460001u && tk.aqa_on_enable == 0x001f001fu);
    TCHECK(tk.asq_on_enable == DMA + NVMEH_ASQ && tk.acq_on_enable == DMA + NVMEH_ACQ);
    TCHECK(tk.cc_writes == 3 && tk.aq_writes == 5);                 /* SHN, EN 0, EN 1; AQA, ASQ x2, ACQ x2 */
    TCHECK(tk.admin_ops[0x06] == 2 && tk.admin_ops[0x09] == 1 && tk.admin_ops[0x05] == 1 && tk.admin_ops[0x01] == 1);
    TCHECK(t.vid == 0x144d && t.nn == 1 && t.mdts == 5 && t.nsze == DISK_LBAS && t.lba_shift == 9 && t.eui64 == TK_EUI);
    TCHECK(t.oncs == 0x1f && t.vwc == 1 && t.hmpre == 0x1000 && t.hmmin == 0x800);   /* what D9 and D16 read in session 2 */
    TCHECK(t.cap == 0x303C0301FFull && t.cc_before == 0x460001u && t.asq_before == 0xCF3E7000ull && t.max_transfer == NVMEH_DATA_BYTES);
    TCHECK((t.flags & NVMET_F_DMA_LIVE) && (t.flags & NVMET_F_REGS_WRITTEN) && !(t.flags & NVMET_F_FW_QUEUES_LIVE));
    for (unsigned i = 0; i < 6; i++) TCHECK(!nvmet_read(&ops, &t, lbas[i], counts[i]) && data_ok(lbas[i], counts[i]));
    TCHECK(t.reads == 6 && t.refused == 0 && t.errors == 0);
    TCHECK(!nvmet_stop(&ops, &t) && t.stage == NVMET_ST_STOPPED && !(t.flags & NVMET_F_DMA_LIVE));
    TCHECK(tk.cc == 0 && !tk.enabled && !(tk.csts & 1) && tk.admin_ops[0x00] == 1 && tk.admin_ops[0x04] == 1);
    TCHECK(audit() && only_allowed_opcodes());
    TCHECK(nvmet_read(&ops, &t, 0, 1) == NVMET_E_DEAD);              /* a stopped controller is closed */
    end();
}

static void handoff(void)               /* the A16 serving code takes over exactly what the unit left */
{
    struct nvmet t; struct nvmet_config c = cfg();
    struct nvmeh_params p; struct nvmeh_state s;
    begin("handoff");
    model();
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    TCHECK(!nvmet_read(&ops, &t, 40, 8) && !nvmet_read(&ops, &t, 41, 8));
    TCHECK(nvmet_make_params(&t, LAST, FIRST, 100000, &p) == NVMET_E_RANGE);
    TCHECK(nvmet_make_params(&t, FIRST, DISK_LBAS, 100000, &p) == NVMET_E_RANGE);
    TCHECK(nvmet_make_params(&t, FIRST, LAST, 0, &p) == NVMET_E_RANGE && t.stage == NVMET_ST_READY);   /* a refusal closes nothing */
    TCHECK(nvmet_make_params(&t, FIRST, LAST, 100000, &p) == 0 && t.stage == NVMET_ST_HANDED_OFF);
    memset(&s, 0, sizeof s);
    tk.confine = 1; controller_last = LAST;                             /* from here the old window checks apply */
    TCHECK(nvmeh_adopt(&ops, &p, &s, 0, 0x1000) == 0);
    int ok = 1;
    for (unsigned i = 0; i < 3 * NVMEH_IO_ENTRIES; i++) {
        uint64_t w = (i * 37) % (LAST - FIRST - 8);
        ok &= nvmeh_read(&ops, &p, &s, w, 8, 0) == 0 && !memcmp(dma + NVMEH_DATA, disk + (FIRST + w) * 512, 8 * 512);
    }
    TCHECK(ok);
    TCHECK(nvmeh_write(&ops, &p, &s, 10, 8, 0) == NVMEH_E_READONLY);
    /* The serving code has moved the queue's tail, head and phase: any call of the unit would use stale ones. */
    unsigned reads = tk.reg_reads, bells = tk.db_total, ccs = tk.cc_writes;
    TCHECK(nvmet_read(&ops, &t, 0, 1) == NVMET_E_DEAD && nvmet_stop(&ops, &t) == NVMET_E_DEAD && nvmet_quiesce(&ops, &t) == NVMET_E_DEAD &&
           nvmet_enable(&ops, &t) == NVMET_E_DEAD && nvmet_identify(&ops, &t) == NVMET_E_DEAD && nvmet_ioq(&ops, &t) == NVMET_E_DEAD &&
           nvmet_make_params(&t, FIRST, LAST, 100000, &p) == NVMET_E_DEAD);
    TCHECK(tk.reg_reads == reads && tk.db_total == bells && tk.cc_writes == ccs && (t.flags & NVMET_F_DMA_LIVE));
    TCHECK(audit());
    end();
}

/* The serving code may refuse the adoption (here: a guest range over the extent). The controller is then enabled on our
 * queues, the unit is closed for reads and stop, and only nvmet_abort can still disable it: CC 0 uses no queue state. */
static void adopt_refused(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    struct nvmeh_params p; struct nvmeh_state s;
    begin("adopt-refused");
    model();
    TCHECK(!nvmet_takeover(&ops, &t, &c) && !nvmet_read(&ops, &t, 40, 8));
    TCHECK(nvmet_make_params(&t, FIRST, LAST, 100000, &p) == 0 && t.stage == NVMET_ST_HANDED_OFF);
    memset(&s, 0, sizeof s);
    unsigned ccs = tk.cc_writes;
    TCHECK(nvmeh_adopt(&ops, &p, &s, DMA, DMA + NVMEH_WINDOW_BYTES) != 0);
    TCHECK(tk.enabled && (tk.csts & 1) && (t.flags & NVMET_F_DMA_LIVE) && tk.cc_writes == ccs);   /* the refusal left the controller enabled */
    TCHECK(nvmet_stop(&ops, &t) == NVMET_E_DEAD && nvmet_read(&ops, &t, 0, 1) == NVMET_E_DEAD && tk.cc_writes == ccs);
    TCHECK(nvmet_abort(&ops, &t) == 0 && !(t.flags & (NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE)));
    TCHECK(!tk.enabled && !(tk.csts & 1) && tk.cc_writes == ccs + 1 && t.stage == NVMET_ST_FAILED);
    TCHECK(nvmet_abort(&ops, &t) == 0 && tk.cc_writes == ccs + 1);      /* done: a second abort writes nothing */
    TCHECK(audit());
    end();
}

static void initial_states(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("initial-states");
    model(); firmware_off();                                            /* disabled: no shutdown, no reset */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && tk.cc_writes == 1 && !(t.flags & NVMET_F_FW_QUEUES_LIVE));
    model(); tk.cc = 1; tk.csts = 0;                                    /* enable in progress: reset, no shutdown */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && tk.cc_writes == 2 && !(t.flags & NVMET_F_FW_QUEUES_LIVE));
    model(); tk.cc = 0; tk.csts = 1; tk.rdy_pending = 0; tk.rdy_delay = 3;   /* a reset that has not finished: RDY 0 is seen, so nothing stays attached */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && tk.cc_writes == 1 && !(t.flags & NVMET_F_FW_QUEUES_LIVE));
    /* Fatal on arrival: nothing is written, and the firmware's controller stays as found, so the flag says its queues are
     * still attached whenever CC.EN or RDY was set. */
    model(); tk.csts = 2;                                               /* CC.EN 1, CFS */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_FATAL_ENTRY && tk.cc_writes == 0 && tk.aq_writes == 0 && tk.db_total == 0);
    TCHECK(t.flags == NVMET_F_FW_QUEUES_LIVE && t.stage == NVMET_ST_FAILED);
    model(); tk.csts = 3;                                               /* CC.EN 1, RDY and CFS */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_FATAL_ENTRY && tk.cc_writes == 0 && t.flags == NVMET_F_FW_QUEUES_LIVE);
    model(); tk.cc = 0; tk.csts = 3;                                    /* CC.EN 0 but RDY and CFS */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_FATAL_ENTRY && tk.cc_writes == 0 && t.flags == NVMET_F_FW_QUEUES_LIVE);
    model(); firmware_off(); tk.csts = 2;                               /* CFS with the controller off: nothing is attached */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_FATAL_ENTRY && tk.cc_writes == 0 && t.flags == 0);
    TCHECK(audit());
    end();
}

static void cap_refusals(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("cap-refusals");
    /* A refusal writes nothing. The arrival state is read before it, and the firmware's controller stays running on its
     * queues: the flag is set when it was enabled or ready (or unreadable), clear when it was off. */
    model(); tk.gone = 1;                                               /* all ones: the state is unknown */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_GONE && tk.cc_writes == 0 && tk.aq_writes == 0 && t.flags == NVMET_F_FW_QUEUES_LIVE);
    model(); tk.cap &= ~(1ull << 37);
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CAP && tk.cc_writes == 0 && t.flags == NVMET_F_FW_QUEUES_LIVE);
    TCHECK(t.cc_before == 0x460001u && t.csts_before == 1 && t.asq_before == 0xCF3E7000ull && t.acq_before == 0xCF3E8000ull);   /* recorded before the refusal */
    model(); firmware_off(); tk.cap &= ~(1ull << 37);                   /* the same refusal with the controller off: nothing is attached */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CAP && tk.cc_writes == 0 && t.flags == 0);
    model(); tk.cap |= 1ull << 48;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CAP && tk.cc_writes == 0);
    model(); tk.cap |= 5ull << 32;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CAP && tk.cc_writes == 0);
    model(); tk.cap = (tk.cap & ~0xffffull) | 31;                       /* 32 entries: fewer than the 64 of the I/O queue */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_MQES && tk.cc_writes == 0 && tk.aq_writes == 0 && t.flags == NVMET_F_FW_QUEUES_LIVE);
    model(); firmware_off(); tk.cap = (tk.cap & ~0xffffull) | 31;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_MQES && tk.cc_writes == 0 && t.flags == 0);
    model(); tk.cap = (tk.cap & ~0xffffull) | 63;                       /* exactly 64 is enough */
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    model(); tk.cap |= 2ull << 32;                                      /* doorbell stride 16 bytes */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && !nvmet_read(&ops, &t, 3, 8) && data_ok(3, 8) && !nvmet_stop(&ops, &t));
    for (unsigned stride = 3; stride <= 4; stride++) {                  /* the largest strides: 128 and 64 bytes apart are in the contract */
        model(); tk.cap |= (uint64_t)stride << 32;
        TCHECK(!nvmet_takeover(&ops, &t, &c) && !nvmet_read(&ops, &t, 3, 8) && data_ok(3, 8) && !nvmet_stop(&ops, &t) && audit());
    }
    TCHECK(audit());
    end();
}

static void rdy_faults(void)
{
    struct nvmet t; struct nvmet_config c = cfg(); uint64_t t0;
    begin("rdy-never");
    model(); firmware_off(); tk.f_rdy_never = 1; t0 = clock_now;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_RDY && t.stage == NVMET_ST_FAILED);
    TCHECK(clock_now - t0 > 30 * TICKS_PER_S && clock_now - t0 < 40 * TICKS_PER_S);   /* CAP.TO 60 x 500 ms, then the reset */
    TCHECK(!tk.enabled && tk.cc == 0 && !(t.flags & NVMET_F_DMA_LIVE) && audit());
    end();
    begin("rdy-stuck");
    model(); tk.f_rdy_stuck = 1;                                        /* CC.EN 0 never brings RDY down */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_QUIESCE && tk.aq_writes == 0 && tk.db_total == 0);
    TCHECK(!(t.flags & NVMET_F_DMA_LIVE) && (t.flags & NVMET_F_REGS_WRITTEN) && t.stage == NVMET_ST_FAILED);
    TCHECK(t.flags & NVMET_F_FW_QUEUES_LIVE);                           /* the firmware's queues may still be the controller's */
    TCHECK(audit());
    end();
    begin("cfs-bringup");
    model(); firmware_off(); tk.f_cfs = 1;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CFS && !(t.flags & NVMET_F_DMA_LIVE) && !tk.enabled);
    model(); firmware_off(); tk.f_cfs = 1; tk.f_cfs_rdy = 1;            /* RDY and CFS together are not ready */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CFS && t.stage == NVMET_ST_FAILED && tk.db_total == 0);
    model(); firmware_off(); tk.f_cfs = 1; tk.f_cfs_rdy = 1; tk.f_rdy_stuck = 1;   /* and a reset that cannot be shown */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CFS && (t.flags & NVMET_F_DMA_LIVE));
    TCHECK(audit());
    end();
}

static void phase_and_wrap(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("wrong-phase");
    model(); tk.f_queue = 1; tk.f_kind = TKF_PHASE; tk.f_at = 2;        /* the second read completes with the other phase */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && !nvmet_read(&ops, &t, 5, 8));
    TCHECK(nvmet_read(&ops, &t, 6, 8) == NVMET_E_T_CMD && t.stage == NVMET_ST_FAILED && !(t.flags & NVMET_F_DMA_LIVE));
    TCHECK(tk.io_cq_head_db == 1);                                      /* the entry of the wrong phase was never consumed */
    model(); tk.f_queue = 0; tk.f_kind = TKF_PHASE; tk.f_at = 1;        /* an admin completion of the other phase */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_CMD && !(t.flags & NVMET_F_DMA_LIVE) && audit());
    end();
    begin("queue-wrap");
    model();
    TCHECK(!nvmet_init(&t, &c) && !nvmet_quiesce(&ops, &t) && !nvmet_enable(&ops, &t));
    int ok = 1;
    for (unsigned i = 0; i < 2 * NVMEH_ADMIN_ENTRIES + 8; i++) ok &= nvmet_identify(&ops, &t) == 0;   /* the admin ring wraps */
    TCHECK(ok && t.adm.phase == 1);                                    /* four laps of 32 entries: the phase is the first lap's */
    TCHECK(t.adm.tail == (2 * (2 * NVMEH_ADMIN_ENTRIES + 8)) % NVMEH_ADMIN_ENTRIES);
    TCHECK(!nvmet_ioq(&ops, &t));
    ok = 1;
    for (unsigned i = 0; i < MANY; i++) {
        uint64_t lba = (i * 37) % (DISK_LBAS - 16);
        ok &= nvmet_read(&ops, &t, lba, 8) == 0 && data_ok(lba, 8);
    }
    TCHECK(ok && t.reads == MANY && t.io.tail == MANY % NVMEH_IO_ENTRIES && audit());
    TCHECK(!nvmet_stop(&ops, &t));
    end();
}

static void completion_faults(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("cid");
    model(); tk.f_queue = 1; tk.f_kind = TKF_CID; tk.f_at = 3;
    TCHECK(!nvmet_takeover(&ops, &t, &c) && !nvmet_read(&ops, &t, 1, 8) && !nvmet_read(&ops, &t, 2, 8));
    TCHECK(nvmet_read(&ops, &t, 3, 8) == NVMET_E_CID && !(t.flags & NVMET_F_DMA_LIVE));
    unsigned bells = tk.db_total;
    TCHECK(nvmet_read(&ops, &t, 4, 8) == NVMET_E_DEAD && tk.db_total == bells);   /* a desynchronised queue is never used again */
    model(); tk.f_queue = 0; tk.f_kind = TKF_CID; tk.f_at = 2;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CID && !(t.flags & NVMET_F_DMA_LIVE));
    TCHECK(audit());
    end();
    begin("duplicate");
    model(); tk.f_queue = 1; tk.f_kind = TKF_DUP; tk.f_at = 1;
    TCHECK(!nvmet_takeover(&ops, &t, &c) && nvmet_read(&ops, &t, 1, 8) == NVMET_E_DUP && !(t.flags & NVMET_F_DMA_LIVE));
    model(); tk.f_queue = 0; tk.f_kind = TKF_DUP; tk.f_at = 2;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_DUP && t.stage == NVMET_ST_FAILED);
    /* The copy that lands after the host consumed the first and rang the head doorbell: the CPU has the line cached from
     * its poll, so only an invalidation before the probe shows it. */
    model(); tk.f_queue = 0; tk.f_kind = TKF_LATE_DUP; tk.f_at = 2;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_DUP && t.stage == NVMET_ST_FAILED && !(t.flags & NVMET_F_DMA_LIVE) && !tk.enabled);
    TCHECK(tk.admin_seq == 2);                                          /* seen at its own command: a stale probe finds it one command late */
    end();
    begin("queue-id");
    model(); tk.f_queue = 1; tk.f_kind = TKF_SQID; tk.f_at = 1;
    TCHECK(!nvmet_takeover(&ops, &t, &c) && nvmet_read(&ops, &t, 1, 8) == NVMET_E_QID && !(t.flags & NVMET_F_DMA_LIVE));
    model(); tk.f_queue = 1; tk.f_kind = TKF_SQHD; tk.f_at = 1;         /* a head pointer one past the last slot is outside the queue */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && nvmet_read(&ops, &t, 1, 8) == NVMET_E_QID && !(t.flags & NVMET_F_DMA_LIVE));
    model(); tk.f_queue = 0; tk.f_kind = TKF_SQHD; tk.f_at = 3;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_QID && t.stage == NVMET_ST_FAILED && !(t.flags & NVMET_F_DMA_LIVE));
    end();
    begin("status");
    model(); tk.f_queue = 1; tk.f_kind = TKF_STATUS; tk.f_at = 2; tk.f_status = 0x281;
    TCHECK(!nvmet_takeover(&ops, &t, &c) && !nvmet_read(&ops, &t, 1, 8));
    TCHECK(nvmet_read(&ops, &t, 2, 8) == NVMET_E_STATUS && t.stage == NVMET_ST_READY && t.errors == 1 && (t.last_status >> 16) == 0x281);
    TCHECK(tk.io_cq_head_db == cq_tail);                                /* the error completion was consumed and acknowledged too */
    TCHECK(!nvmet_read(&ops, &t, 3, 8) && data_ok(3, 8) && t.reads == 2);   /* an error status leaves the queue usable */
    model(); tk.f_queue = 0; tk.f_kind = TKF_STATUS; tk.f_at = 5; tk.f_status = 0x100;   /* Create I/O SQ fails */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_STATUS && t.stage == NVMET_ST_FAILED && !(t.flags & NVMET_F_DMA_LIVE));
    TCHECK(audit());
    end();
}

static void prp_guard(void)
{
    struct nvmet t; struct nvmet_config c = cfg(); uint64_t p1 = 0, p2 = 0;
    begin("prp");
    model();
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    TCHECK(!nvmet_prp(&t, 0, 512, &p1, &p2) && p1 == DMA + NVMEH_DATA && p2 == 0);
    TCHECK(!nvmet_prp(&t, 0, 8192, &p1, &p2) && p2 == DMA + NVMEH_DATA + 4096);
    TCHECK(!nvmet_prp(&t, 0, NVMEH_DATA_BYTES, &p1, &p2) && p2 == DMA + NVMEH_PRP);
    TCHECK(!nvmet_prp(&t, 4096, 4096, &p1, &p2) && p1 == DMA + NVMEH_DATA + 4096);
    TCHECK(nvmet_prp(&t, 0, 0, &p1, &p2) == NVMET_E_PRP);
    TCHECK(nvmet_prp(&t, 0, NVMEH_DATA_BYTES + 512, &p1, &p2) == NVMET_E_PRP);          /* past the data buffer */
    TCHECK(nvmet_prp(&t, NVMEH_DATA_BYTES, 512, &p1, &p2) == NVMET_E_PRP);
    TCHECK(nvmet_prp(&t, 0x1000, NVMEH_DATA_BYTES, &p1, &p2) == NVMET_E_PRP);
    TCHECK(nvmet_prp(&t, 512, 512, &p1, &p2) == NVMET_E_PRP);                            /* not page aligned */
    TCHECK(nvmet_prp(&t, UINT64_MAX - 4095, 4096, &p1, &p2) == NVMET_E_PRP);             /* wraps */
    TCHECK(nvmet_prp(&t, 0, UINT64_MAX, &p1, &p2) == NVMET_E_PRP);
    unsigned bells = tk.db_total;
    const uint64_t outside[] = {DMA + NVMEH_WINDOW_BYTES, DMA - 4096, 0x1000, UINT64_MAX - 4095, 0xCF3E7000ull};
    for (unsigned i = 0; i < 5; i++) {
        TCHECK(nvmet_test_command(&ops, &t, 1, NVMET_IO_READ, outside[i], 0, 0) == NVMET_E_PRP);
        TCHECK(nvmet_test_command(&ops, &t, 1, NVMET_IO_READ, DMA + NVMEH_DATA, outside[i], 0) == NVMET_E_PRP);
        TCHECK(nvmet_test_command(&ops, &t, 0, NVMET_ADMIN_IDENTIFY, outside[i], 0, 1) == NVMET_E_PRP);
    }
    TCHECK(tk.db_total == bells && t.stage == NVMET_ST_READY && audit());
    end();
}

static void identify_faults(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("lba-size");
    model(); tk.flbas = 1;                                              /* the current format is 4096 bytes */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_LBA_SIZE && !(t.flags & NVMET_F_DMA_LIVE) && t.stage == NVMET_ST_FAILED);
    TCHECK(tk.io_ops[2] == 0 && audit());
    model(); tk.ms_cur = 8;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_METADATA && !(t.flags & NVMET_F_DMA_LIVE));
    end();
    begin("identify-zeros");
    model(); tk.id_zero_ctrl = 1;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_ZERO && !(t.flags & NVMET_F_DMA_LIVE) && t.stage == NVMET_ST_FAILED);
    model(); tk.id_zero_ns = 1;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_ZERO && !(t.flags & NVMET_F_DMA_LIVE));
    model(); tk.id_nodma = 2;                                           /* Identify controller completes, and its DMA never lands */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_ZERO && !(t.flags & NVMET_F_DMA_LIVE));
    model(); tk.id_nodma = 1;                                           /* the namespace's: the page must not hold the last one's */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_ZERO && !(t.flags & NVMET_F_DMA_LIVE));
    TCHECK(audit());
    end();
    begin("identify-fields");
    model(); tk.nn = 0;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_CTRL);
    model(); tk.sqes = 0x55;                                            /* the largest SQ entry is 32 bytes */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_CTRL);
    model(); tk.cqes = 0x33;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_CTRL);
    model(); tk.nsze = 0;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_NS);
    model(); tk.flbas = 5;                                              /* a format index past NLBAF */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_NS);
    model(); tk.flbas = 0x20;                                           /* bits 6:5 are reserved up to 16 formats: format 0 is current */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.lba_shift == 9);
    model(); tk.nlbaf = 17; tk.flbas = 0x21; tk.lbaf512 = 17;           /* 18 formats: bits 6:5 are the index's upper bits, 17 is 512 bytes */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.lba_shift == 9 && !nvmet_read(&ops, &t, 3, 8) && data_ok(3, 8));
    model(); tk.nlbaf = 17; tk.flbas = 0x41; tk.lbaf512 = 1;            /* index 33 is past NLBAF whatever format 1 is */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_ID_NS);
    model(); tk.mdts = 1;                                               /* 8 KiB per command */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.max_transfer == 8192 && nvmet_read(&ops, &t, 0, 17) == NVMET_E_RANGE &&
           !nvmet_read(&ops, &t, 0, 16) && data_ok(0, 16));
    model(); tk.mdts = 0;                                               /* no limit: the data buffer is the cap */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.max_transfer == NVMEH_DATA_BYTES);
    TCHECK(audit());
    end();
    begin("eui64");
    model(); c.eui64_pin = TK_EUI ^ 1;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_EUI64 && !(t.flags & NVMET_F_DMA_LIVE));
    model(); c.eui64_pin = 0;                                           /* no pin: reported, not compared */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.eui64 == TK_EUI);
    end();
}

static void gates(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("opcode-gate");
    model();
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    for (unsigned op = 0; op < 256; op++)
        TCHECK(nvmet_io_gate(op) == (op == 0x02 ? 0 : NVMET_E_OPCODE));
    unsigned bells = tk.db_total;
    for (unsigned op = 0; op < 256; op++) {                              /* every I/O opcode but Read: nothing is submitted */
        if (op == 0x02) continue;
        TCHECK(nvmet_test_command(&ops, &t, 1, op, DMA + NVMEH_DATA, 0, 0) == NVMET_E_OPCODE);
    }
    const unsigned dangerous[] = {0x08, 0x0c, 0x0d, 0x10, 0x11, 0x14, 0x15, 0x18, 0x19, 0x1a, 0x1c, 0x7c, 0x80, 0x81,
                                  0x82, 0x84, 0x86, 0xc0, 0xff};   /* Abort, AER, NS management and attachment, firmware, ... */
    for (unsigned i = 0; i < sizeof dangerous / sizeof dangerous[0]; i++)
        TCHECK(nvmet_test_command(&ops, &t, 0, dangerous[i], 0, 0, 1) == NVMET_E_OPCODE);
    TCHECK(nvmet_test_command(&ops, &t, 0, NVMET_ADMIN_SET_FEATURES, 0, 0, 3) == NVMET_E_OPCODE);               /* LBA Range Type */
    TCHECK(nvmet_test_command(&ops, &t, 0, NVMET_ADMIN_SET_FEATURES, 0, 0, 7u | 1u << 31) == NVMET_E_OPCODE);   /* with Save */
    TCHECK(nvmet_test_command(&ops, &t, 0, NVMET_ADMIN_IDENTIFY, DMA + NVMET_IDENT, 0, 2) == NVMET_E_OPCODE);   /* a CNS beyond 1 */
    TCHECK(tk.db_total == bells && t.stage == NVMET_ST_READY && t.refused == 256 - 1 + 19 + 3);
    TCHECK(nvmet_test_command(&ops, &t, 1, NVMET_IO_READ, DMA + NVMEH_DATA, 0, 3) == 0 && data_ok(3, 1));
    TCHECK(audit() && only_allowed_opcodes());
    end();
}

static void read_range(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("read-range");
    model();
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    unsigned bells = tk.db_total, refused = 0;
    const struct { uint64_t lba; uint32_t count; } bad[] = {
        {DISK_LBAS, 1}, {DISK_LBAS - 1, 2}, {DISK_LBAS - 255, 256}, {0, 0}, {0, 257}, {0, UINT32_MAX},
        {UINT64_MAX, 1}, {UINT64_MAX - 3, 8}, {DISK_LBAS + 5, 8}, {1ull << 40, 1},
    };
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        TCHECK(nvmet_read(&ops, &t, bad[i].lba, bad[i].count) == NVMET_E_RANGE);
        refused++;
    }
    TCHECK(tk.db_total == bells && t.refused == refused && tk.oob_reads == 0 && t.stage == NVMET_ST_READY);
    TCHECK(!nvmet_read(&ops, &t, DISK_LBAS - 1, 1) && data_ok(DISK_LBAS - 1, 1));                /* the last LBA */
    TCHECK(!nvmet_read(&ops, &t, DISK_LBAS - 256, 256) && data_ok(DISK_LBAS - 256, 256));
    TCHECK(!nvmet_read(&ops, &t, 0, 34) && data_ok(0, 34));                                       /* the primary GPT zone */
    TCHECK(audit());
    end();
}

static void timeouts(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    unsigned kinds[TK_MAX_WAITS + 1] = {0}, total;
    const int want_error[] = {0, NVMET_E_T_SHUTDOWN, NVMET_E_T_QUIESCE, NVMET_E_T_RDY, NVMET_E_T_CMD, NVMET_E_T_CMD};
    begin("timeouts");
    model();                                                            /* a clean run numbers its waits */
    int e = nvmet_takeover(&ops, &t, &c);
    for (unsigned i = 0; !e && i < 3; i++) e = nvmet_read(&ops, &t, 100 * i, 8);
    if (!e) e = nvmet_stop(&ops, &t);
    TCHECK(!e);
    total = tk.wait_seq;
    TCHECK(total == 15);                                                /* SHN, RDY0, RDY1, 5 admin, 3 reads, 2 deletes, SHN, RDY0 */
    for (unsigned k = 1; k <= total; k++) kinds[k] = tk.wait_kind[k];
    for (unsigned k = 1; k <= total; k++) {
        uint64_t t0;
        model(); tk.f_never_at = k; t0 = clock_now;
        e = nvmet_takeover(&ops, &t, &c);
        for (unsigned i = 0; !e && i < 3; i++) e = nvmet_read(&ops, &t, 100 * i, 8);
        if (!e) e = nvmet_stop(&ops, &t);
        if (e != want_error[kinds[k]]) printf("FAIL case=timeouts wait %u kind %u gave %d (%s)\n", k, kinds[k], e, nvmet_strerror(e));
        TCHECK(e == want_error[kinds[k]]);
        TCHECK(t.stage == NVMET_ST_FAILED && clock_now - t0 < 100 * TICKS_PER_S);
        /* Only the last wait leaves the controller unshown as disabled: its reset was the one that never landed. */
        if (!!(t.flags & NVMET_F_DMA_LIVE) != (k == total) || !audit())
            printf("FAIL case=timeouts wait %u kind %u: flags %#llx audit %d bad_reg %u bad_db %u bad_dma %u bad_op %u oob %u cache %u\n", k, kinds[k],
                   (unsigned long long)t.flags, audit(), tk.bad_reg, tk.bad_doorbell, tk.bad_dma, tk.bad_opcode, tk.oob_reads, tk.bad_cache);
        TCHECK(!!(t.flags & NVMET_F_DMA_LIVE) == (k == total) && audit());
        TCHECK(!!(t.flags & NVMET_F_FW_QUEUES_LIVE) == (k <= 2));       /* only a failure inside the quiesce leaves the firmware's queues unshown dead */
        if (k <= 2) TCHECK(tk.aq_writes == 0 && tk.db_total == 0);       /* nothing of ours was programmed yet */
        else TCHECK(!tk.enabled);                                        /* the controller was disabled again */
    }
    end();
}

static uint64_t now_stuck(void *ctx) { (void)ctx; return 7; }
static const struct nvmeh_ops ops_stuck = {0, rd, wr, clean, now_stuck};

static void clock_and_gone(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("clock-stuck");
    model(); tk.f_never_at = 1;                                         /* the shutdown never completes, and time stands still */
    TCHECK(nvmet_takeover(&ops_stuck, &t, &c) == NVMET_E_CLOCK);
    model();
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    tk.f_never_at = tk.wait_seq + 1;                                    /* the next read never completes, on a stopped clock */
    TCHECK(nvmet_read(&ops_stuck, &t, 1, 8) == NVMET_E_CLOCK && t.stage == NVMET_ST_FAILED);
    end();
    begin("gone");
    model();
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    tk.gone = 1; tk.f_never_at = tk.wait_seq + 1;                       /* the device disappears: nothing completes */
    TCHECK(nvmet_read(&ops, &t, 1, 8) == NVMET_E_GONE && t.stage == NVMET_ST_FAILED);
    TCHECK((t.flags & NVMET_F_DMA_LIVE));                               /* a disable that cannot be seen is reported */
    model(); tk.gone_on_cc = 1;                                         /* gone at the first register write: not a timeout */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_GONE && t.stage == NVMET_ST_FAILED && tk.aq_writes == 0);
    end();
    begin("cfs-in-command");
    model(); tk.f_never_at = 4; tk.cfs_on_never = 1;                    /* Identify never completes and the controller is fatal */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CFS && !(t.flags & NVMET_F_DMA_LIVE) && t.stage == NVMET_ST_FAILED);
    model(); tk.f_never_at = 4;                                         /* without CFS the same silence is a timeout */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_CMD);
    TCHECK(audit());
    end();
}

static void config_checks(void)
{
    struct nvmet t; struct nvmet_config c;
    begin("config");
    model();
    unsigned reads = tk.reg_reads;
#define REFUSED(want) do { TCHECK(nvmet_takeover(&ops, &t, &c) == (want)); \
        TCHECK(tk.cc_writes == 0 && tk.aq_writes == 0 && tk.db_total == 0 && tk.reg_reads == reads && t.stage == NVMET_ST_FAILED); } while (0)
#define BAD(field, value, want) do { c = cfg(); c.field = (value); REFUSED(want); } while (0)
/* The two addresses of the extent are checked on their own: moving one of them alone is also refused by the rule that
 * a different bus address must be declared translated, which would hide a missing check of the address itself. So the
 * other address stays valid and different, and the extent is declared translated. */
#define BAD_CPU(value, want) do { c = cfg(); c.cpu_base = (value); c.bus_base = TK_BUS; c.flags |= NVMET_C_TRANSLATED; REFUSED(want); } while (0)
#define BAD_BUS(value, want) do { c = cfg(); c.bus_base = (value); c.flags |= NVMET_C_TRANSLATED; REFUSED(want); } while (0)
    BAD(bar, 0, NVMET_E_CONFIG);
    BAD(bar, BAR + 0x1000, NVMET_E_CONFIG);                             /* not 16 KiB aligned */
    BAD(bar, 1ull << 36, NVMET_E_CONFIG);
    BAD(bar, DMA + 0x1000, NVMET_E_CONFIG);                             /* inside the extent */
    BAD_CPU(0, NVMET_E_CONFIG);
    BAD_CPU(DMA + 0x100000 / 2, NVMET_E_CONFIG);                        /* not 1 MiB aligned */
    BAD_CPU(0xfffffffffff00000ull, NVMET_E_CONFIG);                     /* wraps, and past 2^36 */
    BAD_CPU(1ull << 36, NVMET_E_CONFIG);
    BAD_BUS(0, NVMET_E_CONFIG);
    BAD_BUS(TK_BUS + 4096, NVMET_E_CONFIG);
    BAD_BUS(0xfffffffffff00000ull, NVMET_E_CONFIG);
    BAD(ticks_per_s, 0, NVMET_E_CONFIG);
    BAD(ticks_per_s, (1ull << 40) + 1, NVMET_E_CONFIG);
    BAD(max_transfer, 0, NVMET_E_CONFIG);
    BAD(max_transfer, 2048, NVMET_E_CONFIG);
    BAD(max_transfer, NVMEH_DATA_BYTES + 512, NVMET_E_CONFIG);
    BAD(max_transfer, 4096 + 100, NVMET_E_CONFIG);
    BAD(bus_base, 0x123400000ull, NVMET_E_CONFIG);                      /* a mistyped bus address is not a translating SMMU */
    BAD(flags, 3, NVMET_E_CONFIG);                                      /* translated, with the same address */
    BAD(flags, 5, NVMET_E_CONFIG);                                      /* an unknown bit */
    BAD(reserved, 1, NVMET_E_CONFIG);
    BAD(mem_high, DMA + 4096, NVMET_E_GUEST);                           /* the extent overlaps guest memory */
    c = cfg(); c.mem_low = DMA - 4096; c.mem_high = DMA + 1;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_GUEST && tk.reg_reads == reads);
    c = cfg(); c.mem_low = DMA + NVMEH_WINDOW_BYTES; c.mem_high = DMA + 2 * NVMEH_WINDOW_BYTES;   /* just above: fine */
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    model(); reads = tk.reg_reads;
    BAD(flags, 0, NVMET_E_DMA_UNPROVEN);                                /* no proof: nothing was touched, not even a read */
    c = cfg(); c.flags = 0; c.bar = 0;                                  /* a broken config is reported before the missing proof */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CONFIG);
    TCHECK(nvmet_quiesce(&ops, &t) == NVMET_E_DEAD && nvmet_enable(&ops, &t) == NVMET_E_DEAD &&
           nvmet_identify(&ops, &t) == NVMET_E_DEAD && nvmet_ioq(&ops, &t) == NVMET_E_DEAD && nvmet_stop(&ops, &t) == NVMET_E_DEAD);
    TCHECK(tk.reg_reads == reads && audit());
#undef BAD_BUS
#undef BAD_CPU
#undef REFUSED
#undef BAD
    end();
}

static void stages(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("stages");
    model();
    TCHECK(!nvmet_init(&t, &c));
    TCHECK(nvmet_enable(&ops, &t) == NVMET_E_STATE && nvmet_identify(&ops, &t) == NVMET_E_STATE &&
           nvmet_ioq(&ops, &t) == NVMET_E_STATE && nvmet_read(&ops, &t, 0, 1) == NVMET_E_STATE && nvmet_stop(&ops, &t) == NVMET_E_STATE);
    TCHECK(tk.reg_reads == 0 && tk.cc_writes == 0);
    TCHECK(!nvmet_quiesce(&ops, &t) && nvmet_quiesce(&ops, &t) == NVMET_E_STATE);
    TCHECK(!nvmet_enable(&ops, &t) && nvmet_enable(&ops, &t) == NVMET_E_STATE && nvmet_ioq(&ops, &t) == NVMET_E_STATE);
    TCHECK(!nvmet_stop(&ops, &t) && t.stage == NVMET_ST_STOPPED);                    /* a stop before the queues exist */
    TCHECK(tk.admin_ops[0x00] == 0 && !tk.enabled && audit());
    end();
}

static void translating(void)
{
    struct nvmet t; struct nvmet_config c = cfg(); struct nvmeh_params p;
    begin("bus-translate");
    model(); c.bus_base = TK_BUS; c.flags |= NVMET_C_TRANSLATED; bus_delta = (int64_t)DMA - (int64_t)TK_BUS;
    tk.eui64 = TK_EUI;
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    TCHECK(tk.asq_on_enable == TK_BUS + NVMEH_ASQ && tk.acq_on_enable == TK_BUS + NVMEH_ACQ);
    for (unsigned i = 0; i < 40; i++) TCHECK(!nvmet_read(&ops, &t, i * 11, 8 + i % 5 * 8) && data_ok(i * 11, 8 + i % 5 * 8));
    TCHECK(!nvmet_read(&ops, &t, 7, 256) && data_ok(7, 256));
    TCHECK(nvmet_make_params(&t, FIRST, LAST, 100000, &p) == NVMET_E_NOT_IDENTITY);
    TCHECK(!nvmet_stop(&ops, &t) && audit());
    end();
}

/* A loader's plain stores leave dirty lines. The unit must clean what the controller will touch before it touches it, or a
 * late write-back lands over the data of a read and the read still reports success. */
static void dirty_cache(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    unsigned dirty = 0;
    begin("dirty-cache");
    model();                                                            /* the whole extent was zero-filled through the cache and left dirty */
    memset(cache_copy, 0, sizeof cache_copy); memset(cache_state, 2, sizeof cache_state);
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    for (uint64_t line = NVMEH_PRP / TK_LINE; line < (NVMEH_DATA + NVMEH_DATA_BYTES) / TK_LINE; line++) dirty += cache_state[line] == 2;
    TCHECK(dirty == 0);                                                 /* nothing the controller will write is dirty in the CPU any more */
    for (unsigned i = 0; i < 4; i++) TCHECK(!nvmet_read(&ops, &t, 100 + 37 * i, 8 + 8 * i) && data_ok(100 + 37 * i, 8 + 8 * i));
    TCHECK(tk.bad_cache == 0 && audit());
    for (unsigned i = 0; i < 4096; i += 4) wr(0, DMA + NVMEH_DATA + i, 0xdeadbeefu);   /* a CPU store between two reads */
    TCHECK(!nvmet_read(&ops, &t, 7, 8) && data_ok(7, 8) && tk.bad_cache == 0 && audit());
    /* The pre-read clean spans exactly the bytes the read lands on: the whole buffer for the largest read, and the first
     * block alone, below one page, for a GPT header. */
    for (uint64_t i = 0; i < NVMEH_DATA_BYTES; i += 4) wr(0, DMA + NVMEH_DATA + i, 0xfeedf00du);
    TCHECK(!nvmet_read(&ops, &t, 1000, NVMEH_DATA_BYTES / 512) && data_ok(1000, NVMEH_DATA_BYTES / 512) && tk.bad_cache == 0 && audit());
    for (unsigned i = 0; i < 512; i += 4) wr(0, DMA + NVMEH_DATA + i, 0xfeedf00du);
    TCHECK(!nvmet_read(&ops, &t, 0, 1) && data_ok(0, 1) && tk.bad_cache == 0 && audit());
    TCHECK(!nvmet_stop(&ops, &t));
    end();
}

/* Every wait is bounded from both sides: a transition just inside its bound is seen, one just outside it is refused with
 * the wait's own code. CAP.TO is 60 x 500 ms = 30 s here, the shutdown wait is 10 s, and a slow knob of the model makes the
 * controller take that long. A bound that is too short, too long or the wrong constant fails one of the two sides. */
static void timing(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    const uint64_t shst_in = 9 * TICKS_PER_S, shst_out = 11 * TICKS_PER_S, rdy_in = 29 * TICKS_PER_S, rdy_out = 31 * TICKS_PER_S;
    begin("timing");
    model(); tk.slow_shst = shst_in;                                    /* the quiesce's shutdown wait */
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    model(); tk.slow_shst = shst_out;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_SHUTDOWN && t.stage == NVMET_ST_FAILED && (t.flags & NVMET_F_FW_QUEUES_LIVE));
    model(); tk.slow_rdy_down = rdy_in;                                 /* its reset wait */
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    model(); tk.slow_rdy_down = rdy_out;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_QUIESCE && (t.flags & NVMET_F_FW_QUEUES_LIVE) && !(t.flags & NVMET_F_DMA_LIVE));
    model(); firmware_off(); tk.slow_rdy_up = rdy_in;                   /* the enable's ready wait */
    TCHECK(!nvmet_takeover(&ops, &t, &c));
    model(); firmware_off(); tk.slow_rdy_up = rdy_out;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_RDY && t.stage == NVMET_ST_FAILED && !(t.flags & NVMET_F_DMA_LIVE) && !tk.enabled);
    model(); TCHECK(!nvmet_takeover(&ops, &t, &c)); tk.slow_shst = shst_in;     /* the stop's shutdown wait */
    TCHECK(!nvmet_stop(&ops, &t) && t.stage == NVMET_ST_STOPPED);
    model(); TCHECK(!nvmet_takeover(&ops, &t, &c)); tk.slow_shst = shst_out;
    TCHECK(nvmet_stop(&ops, &t) == NVMET_E_T_SHUTDOWN && t.stage == NVMET_ST_FAILED && !tk.enabled);
    model(); TCHECK(!nvmet_takeover(&ops, &t, &c)); tk.slow_rdy_down = rdy_in;  /* the stop's reset wait */
    TCHECK(!nvmet_stop(&ops, &t) && t.stage == NVMET_ST_STOPPED && !(t.flags & NVMET_F_DMA_LIVE));
    model(); TCHECK(!nvmet_takeover(&ops, &t, &c)); tk.slow_rdy_down = rdy_out;
    TCHECK(nvmet_stop(&ops, &t) == NVMET_E_T_QUIESCE && t.stage == NVMET_ST_FAILED);
    /* The disable after a failure waits the same window: a fatal controller that resets within it is shown disabled, one
     * that needs longer keeps DMA_LIVE until an abort sees it. */
    model(); firmware_off(); tk.f_cfs = 1; tk.f_cfs_rdy = 1; tk.slow_rdy_down = rdy_in;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CFS && !(t.flags & NVMET_F_DMA_LIVE) && !tk.enabled && !(tk.csts & 1));
    model(); firmware_off(); tk.f_cfs = 1; tk.f_cfs_rdy = 1; tk.slow_rdy_down = rdy_out + 15 * TICKS_PER_S;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CFS && (t.flags & NVMET_F_DMA_LIVE));
    TCHECK(audit());
    end();
}

/* Whatever the firmware left in CC.SHN, the unit asks for a normal shutdown and for nothing else. */
static void shn_field(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("shn-field");
    for (unsigned shn = 0; shn < 4; shn++) {
        model(); tk.cc = 0x00460001u | shn << 14;
        TCHECK(!nvmet_takeover(&ops, &t, &c));
        TCHECK(tk.first_cc == 0x00464001u && tk.bad_reg == 0 && audit());
        TCHECK(!nvmet_stop(&ops, &t));
    }
    model(); tk.cc = 0x00464001u; tk.csts = 1 | 8;                      /* a shutdown the firmware started and finished */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && tk.first_cc == 0x00464001u && audit());
    end();
}

/* The flags say which memory the controller may still use. nvmet_abort writes CC 0 and waits one more CAP.TO window: the
 * case shows that it clears both flags exactly when RDY 0 is seen in that window (a slow reset: yes; a hung controller:
 * no, however often it is called), not that it can bring a controller down: a repeated CC 0 resets nothing. */
static void fw_queues(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    unsigned writes;
    begin("fw-queues");
    model(); firmware_off();                                            /* disabled on arrival: the firmware's queues were never in use */
    TCHECK(!nvmet_init(&t, &c) && !nvmet_quiesce(&ops, &t) && !(t.flags & NVMET_F_FW_QUEUES_LIVE) && tk.cc_writes == 0);
    writes = tk.cc_writes;
    TCHECK(nvmet_abort(&ops, &t) == 0 && tk.cc_writes == writes && t.stage == NVMET_ST_FAILED);   /* nothing to show: nothing written */
    model();                                                            /* enabled and ready on arrival */
    TCHECK(!nvmet_init(&t, &c) && !nvmet_quiesce(&ops, &t) && !(t.flags & NVMET_F_FW_QUEUES_LIVE));   /* RDY 0 was seen */
    /* A reset that is only slow: the quiesce gives up after one CAP.TO window, the abort waits one more and sees RDY 0. */
    model(); tk.slow_rdy_down = 45 * TICKS_PER_S;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_QUIESCE && (t.flags & NVMET_F_FW_QUEUES_LIVE) && !(t.flags & NVMET_F_DMA_LIVE));
    TCHECK(nvmet_stop(&ops, &t) == NVMET_E_DEAD);                       /* a failed unit cannot retry through the stop */
    writes = tk.cc_writes;
    TCHECK(nvmet_abort(&ops, &t) == 0 && tk.cc_writes == writes + 1 && !(t.flags & (NVMET_F_FW_QUEUES_LIVE | NVMET_F_DMA_LIVE)) &&
           !(tk.csts & 1) && audit());
    /* A hung controller: the CC 0 of an abort resets nothing (CC.EN is already 0), so each abort is one more wait, writes
     * nothing else, and the flags stay. A controller that stays up needs a reset this unit does not have. */
    model(); tk.f_rdy_stuck = 1;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_QUIESCE && (t.flags & NVMET_F_FW_QUEUES_LIVE) && !(t.flags & NVMET_F_DMA_LIVE));
    for (unsigned i = 0; i < 2; i++) {
        writes = tk.cc_writes;
        TCHECK(nvmet_abort(&ops, &t) == NVMET_E_T_QUIESCE && tk.cc_writes == writes + 1 && (t.flags & NVMET_F_FW_QUEUES_LIVE) &&
               t.stage == NVMET_ST_FAILED);
    }
    TCHECK((tk.csts & 1) && tk.aq_writes == 0 && tk.db_total == 0 && audit());
    model(); tk.f_never_at = 1;                                         /* the shutdown notification never completes */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_SHUTDOWN && (t.flags & NVMET_F_FW_QUEUES_LIVE) && tk.enabled);
    TCHECK(nvmet_abort(&ops, &t) == 0 && !(t.flags & NVMET_F_FW_QUEUES_LIVE) && !tk.enabled && !(tk.csts & 1));
    model(); firmware_off(); tk.f_cfs = 1; tk.f_cfs_rdy = 1; tk.slow_rdy_down = 45 * TICKS_PER_S;   /* after our CC.EN 1: the extent, not the firmware's */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CFS && (t.flags & NVMET_F_DMA_LIVE) && !(t.flags & NVMET_F_FW_QUEUES_LIVE));
    TCHECK(nvmet_abort(&ops, &t) == 0 && !(t.flags & NVMET_F_DMA_LIVE) && !(tk.csts & 1));
    model(); tk.cc = 1; tk.csts = 0; tk.gone_on_cc = 1;                 /* enabled but not ready: it may still come up on the firmware's queues */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_GONE && (t.flags & NVMET_F_FW_QUEUES_LIVE) && !(t.flags & NVMET_F_DMA_LIVE));
    model(); tk.cc = 0; tk.csts = 1;                                    /* ready with CC.EN already 0: a reset that never finishes */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_QUIESCE && (t.flags & NVMET_F_FW_QUEUES_LIVE) && tk.cc_writes == 0);
    model(); tk.cap &= ~(1ull << 37);                                   /* a refusal writes nothing and leaves the firmware's controller running */
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_CAP && tk.cc_writes == 0 && t.flags == NVMET_F_FW_QUEUES_LIVE && tk.enabled);
    TCHECK(nvmet_abort(&ops, &t) == 0 && !(t.flags & (NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE)) && !tk.enabled && !(tk.csts & 1));   /* the caller's way to quiet it */
    model();                                                            /* a healthy unit asked to abort is disabled and closed */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && nvmet_abort(&ops, &t) == 0 && t.stage == NVMET_ST_FAILED && !tk.enabled && !(t.flags & NVMET_F_DMA_LIVE));
    TCHECK(nvmet_read(&ops, &t, 0, 1) == NVMET_E_DEAD);
    model();                                                            /* a stopped unit has nothing to abort */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && !nvmet_stop(&ops, &t));
    writes = tk.cc_writes;
    TCHECK(nvmet_abort(&ops, &t) == 0 && tk.cc_writes == writes && t.stage == NVMET_ST_STOPPED);
    end();
}

/* A namespace above 2^32 blocks: the SLBA needs its high dword (the model's disk is smaller and aliases). */
static void high_lba(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    const uint64_t lbas[] = {1ull << 32, (1ull << 32) + 5, 0x123456789ull, (1ull << 33) - 256, (1ull << 33) - 1};
    const uint32_t counts[] = {8, 8, 8, 8, 1};
    begin("high-lba");
    model(); tk.nsze = 1ull << 33;
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.nsze == 1ull << 33);
    for (unsigned i = 0; i < 5; i++)
        TCHECK(!nvmet_read(&ops, &t, lbas[i], counts[i]) && tk.last_slba == lbas[i] && data_ok(lbas[i] % DISK_LBAS, counts[i]));
    unsigned bells = tk.db_total;
    TCHECK(nvmet_read(&ops, &t, 1ull << 33, 1) == NVMET_E_RANGE && nvmet_read(&ops, &t, (1ull << 33) - 1, 2) == NVMET_E_RANGE &&
           tk.db_total == bells && audit());
    end();
}

/* Command identifiers run 1 to 0xffff and then wrap to 1, on both queues. */
static void cid_wrap(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("cid-wrap");
    model();
    TCHECK(!nvmet_init(&t, &c) && !nvmet_quiesce(&ops, &t) && !nvmet_enable(&ops, &t));
    t.adm.next_cid = 0xfffe;
    TCHECK(!nvmet_identify(&ops, &t) && !nvmet_identify(&ops, &t) && t.adm.next_cid == 3);          /* 0xfffe, 0xffff, 1, 2 */
    TCHECK(!nvmet_ioq(&ops, &t));
    t.io.next_cid = 0xfffe;
    for (unsigned i = 0; i < 4; i++) TCHECK(!nvmet_read(&ops, &t, 10 * i, 8) && data_ok(10 * i, 8));
    TCHECK(t.io.next_cid == 3 && audit());
    end();
}

/* A completion that takes 4.9 s is inside the 5 s bound and one that takes 5.1 s is not. */
static void latency(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("latency");
    model(); tk.latency = 4900; tk.lat_at = 2;                          /* the second admin command */
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.stage == NVMET_ST_READY && audit());
    model(); tk.latency = 5100; tk.lat_at = 2;
    TCHECK(nvmet_takeover(&ops, &t, &c) == NVMET_E_T_CMD && t.stage == NVMET_ST_FAILED && !(t.flags & NVMET_F_DMA_LIVE) && !tk.enabled);
    end();
}

/* The CPU may fill the lines the controller is about to write: what lands must still be what is read. */
static void spec_fill(void)
{
    struct nvmet t; struct nvmet_config c = cfg();
    begin("spec-fill");
    model(); tk.spec_fill = 3;
    TCHECK(!nvmet_takeover(&ops, &t, &c) && t.vid == 0x144d && t.nsze == DISK_LBAS);
    for (unsigned i = 0; i < 4; i++) TCHECK(!nvmet_read(&ops, &t, 50 + 37 * i, 8 + 8 * i) && data_ok(50 + 37 * i, 8 + 8 * i));
    TCHECK(tk.bad_cache == 0 && audit());
    end();
}

int main(void)
{
    for (size_t i = 0; i < sizeof disk; i++) disk[i] = (uint8_t)(i * 2654435761u >> 13);
    healthy(); handoff(); adopt_refused(); initial_states(); cap_refusals(); rdy_faults(); phase_and_wrap(); completion_faults();
    prp_guard(); identify_faults(); gates(); read_range(); timeouts(); clock_and_gone(); config_checks(); stages();
    translating(); dirty_cache(); shn_field(); fw_queues(); high_lba(); cid_wrap(); latency(); spec_fill(); timing();
    printf("%d cases, %d failures\n", cases, failures);
    return failures != 0;
}
