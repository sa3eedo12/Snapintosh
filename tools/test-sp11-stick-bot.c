/* SPDX-License-Identifier: MIT */
/* tools/sp11-stick-bot.{h,c} against the fake device of tools/sp11-stick-fakedev.h, natively:
 *
 *   cc -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -DSNAP_PLATFORM_SP11=1 -Itools \
 *      tools/test-sp11-stick-bot.c tools/sp11-stick-bot.c -o build/sp11-stick-log/test-sp11-stick-bot
 *
 * What it proves: the exact bytes of the command and status wrappers, the setup packets and every CDB (against vectors typed
 * from the specifications, not produced by the code under test); every fault the fake can inject, with the status the host
 * must report and whether it must demand a reset recovery; that a device that needs recovery is never sent another command;
 * that recovery needs both the reset and the halt clearing; that a range past the device capacity is never sent; and a
 * seeded random run of faults under ASan and UBSan. With --stdin it answers one command per line for the Python oracle.
 * Nothing here runs on hardware: the first real stick will teach more. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sp11-stick-fakedev.h"

static unsigned long checks;
#define CHECK(x) do { checks++; if (!(x)) { \
    fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #x); exit(1); } } while (0)

static uint64_t rng_state = UINT64_C(0x9e3779b97f4a7c15);
static uint64_t rnd(void)
{
    rng_state ^= rng_state >> 12; rng_state ^= rng_state << 25; rng_state ^= rng_state >> 27;
    return rng_state * UINT64_C(0x2545f4914f6cdd1d);
}

static void t_encoders(void)
{
    uint8_t cbw[BOT_CBW_BYTES], set[BOT_SETUP_BYTES];
    static const uint8_t inquiry[6] = {0x12, 0, 0, 0, 0x24, 0};
    static const uint8_t want_in[31] = {0x55, 0x53, 0x42, 0x43, 0x44, 0x33, 0x22, 0x11, 0x24, 0, 0, 0, 0x80, 0x00, 0x06,
                                        0x12, 0, 0, 0, 0x24, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
    static const uint8_t cdb10[10] = {0x2A, 0x08, 1, 2, 3, 4, 0, 0, 1, 0};
    static const uint8_t want_out[31] = {0x55, 0x53, 0x42, 0x43, 0xEF, 0xBE, 0xAD, 0xDE, 0x00, 0x02, 0, 0, 0x00, 0x03, 0x0A,
                                         0x2A, 0x08, 1, 2, 3, 4, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0};
    uint8_t csw[BOT_CSW_BYTES] = {0x55, 0x53, 0x42, 0x53, 0x44, 0x33, 0x22, 0x11, 0, 0, 0, 0, 0}, status = 9;
    uint32_t residue = 9;

    bot_build_cbw(cbw, 0x11223344u, 36, BOT_DIR_IN, 0, inquiry, 6);
    CHECK(memcmp(cbw, want_in, 31) == 0);
    bot_build_cbw(cbw, 0xDEADBEEFu, 512, BOT_DIR_OUT, 3, cdb10, 10);
    CHECK(memcmp(cbw, want_out, 31) == 0);
    bot_build_cbw(cbw, 1, 0, BOT_DIR_NONE, 0x1F, inquiry, 6);          /* the LUN field is 4 bits wide */
    CHECK(cbw[12] == 0 && cbw[13] == 0x0F);
    {
        static const uint8_t cdb16[16] = {0x12, 0, 0, 0, 0x24, 0};
        bot_build_cbw(cbw, 1, 0, BOT_DIR_NONE, 0, cdb16, 0x46);        /* bits 7..5 of bCBWCBLength are reserved */
        CHECK((cbw[14] & 0xE0) == 0);
    }

    CHECK(bot_check_csw(csw, 13, 0x11223344u, 36, &status, &residue) == BOT_OK && status == 0 && residue == 0);
    csw[12] = 1;
    CHECK(bot_check_csw(csw, 13, 0x11223344u, 36, &status, &residue) == BOT_OK && status == 1);
    csw[8] = 36;
    CHECK(bot_check_csw(csw, 13, 0x11223344u, 36, &status, &residue) == BOT_OK && residue == 36);
    csw[8] = 37;
    CHECK(bot_check_csw(csw, 13, 0x11223344u, 36, &status, &residue) == BOT_CSW_INVALID);   /* residue above the request */
    csw[8] = 0;
    csw[12] = 2;
    CHECK(bot_check_csw(csw, 13, 0x11223344u, 36, &status, &residue) == BOT_PHASE_ERROR);
    csw[12] = 3;
    CHECK(bot_check_csw(csw, 13, 0x11223344u, 36, &status, &residue) == BOT_CSW_INVALID);
    csw[12] = 0;
    CHECK(bot_check_csw(csw, 13, 0x11223345u, 36, &status, &residue) == BOT_CSW_INVALID);   /* wrong tag */
    CHECK(bot_check_csw(csw, 12, 0x11223344u, 36, &status, &residue) == BOT_CSW_INVALID);   /* short */
    CHECK(bot_check_csw(csw, 14, 0x11223344u, 36, &status, &residue) == BOT_CSW_INVALID);   /* long */
    csw[3] = 0x43;
    CHECK(bot_check_csw(csw, 13, 0x11223344u, 36, &status, &residue) == BOT_CSW_INVALID);   /* a CBW signature */
    CHECK(bot_check_csw(NULL, 13, 1, 0, &status, &residue) == BOT_CSW_INVALID);

    bot_setup_get_max_lun(set, 2);
    CHECK(!memcmp(set, (const uint8_t[]){0xA1, 0xFE, 0, 0, 2, 0, 1, 0}, 8));
    bot_setup_reset(set, 0x0102);
    CHECK(!memcmp(set, (const uint8_t[]){0x21, 0xFF, 0, 0, 0x02, 0x01, 0, 0}, 8));
    bot_setup_clear_halt(set, 0x81);
    CHECK(!memcmp(set, (const uint8_t[]){0x02, 0x01, 0, 0, 0x81, 0, 0, 0}, 8));
}

static void t_cdbs(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    uint8_t buf[4096];
    fake_init(&f, 0xFFFFFFFFu, 1);                          /* a black hole: the whole 32-bit LBA space */
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    CHECK(d.last_lba == 0xFFFFFFFEu && d.block_len == 512);
    /* The commands scsi_open sent, in order, as bytes typed from SPC: TEST UNIT READY, INQUIRY, READ CAPACITY(10). */
    CHECK(f.commands == 3);
    CHECK(f.last_cdb_len == 10 && f.last_cdb[0] == 0x25 && !memcmp(f.last_cdb, (uint8_t[16]){0x25}, 16));
    CHECK(scsi_read10(&d, 0x01020304u, 3, buf) == SCSI_OK);
    CHECK(f.last_cdb_len == 10 && !memcmp(f.last_cdb, (const uint8_t[16]){0x28, 0, 1, 2, 3, 4, 0, 0, 3, 0}, 16));
    CHECK(scsi_write10(&d, 0x0A0B0C0Du, 8, buf, 1) == SCSI_OK);
    CHECK(!memcmp(f.last_cdb, (const uint8_t[16]){0x2A, 0x08, 0x0A, 0x0B, 0x0C, 0x0D, 0, 0, 8, 0}, 16));
    CHECK(scsi_write10(&d, 1, 1, buf, 0) == SCSI_OK);
    CHECK(!memcmp(f.last_cdb, (const uint8_t[16]){0x2A, 0x00, 0, 0, 0, 1, 0, 0, 1, 0}, 16));
    CHECK(scsi_sync10(&d) == SCSI_OK);
    CHECK(f.last_cdb_len == 10 && !memcmp(f.last_cdb, (const uint8_t[16]){0x35}, 16));
    CHECK(scsi_test_unit_ready(&d) == SCSI_OK);
    CHECK(f.last_cdb_len == 6 && !memcmp(f.last_cdb, (const uint8_t[16]){0}, 16));
    CHECK(scsi_inquiry(&d) == SCSI_OK);
    CHECK(f.last_cdb_len == 6 && !memcmp(f.last_cdb, (const uint8_t[16]){0x12, 0, 0, 0, 0x24, 0}, 16));
    f.sense_key = 0;
    CHECK(scsi_request_sense(&d) == SCSI_OK);
    CHECK(f.last_cdb_len == 6 && !memcmp(f.last_cdb, (const uint8_t[16]){0x03, 0, 0, 0, 0x12, 0}, 16));
    fake_free(&f);
}

static void t_normal(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    uint8_t out[4096], in[4096];
    fake_init(&f, 4096, 0);
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    CHECK(d.last_lba == 4095 && d.block_len == 512 && d.device_type == 0);
    CHECK(!strcmp(d.vendor, "SNAPTEST") && !strcmp(d.product, "FAKE STICK"));
    for (uint32_t blocks = 1; blocks <= SCSI_MAX_BLOCKS; blocks++) {
        for (uint32_t i = 0; i < blocks * 512; i++)
            out[i] = (uint8_t)(i * 7 + blocks);
        CHECK(scsi_write10(&d, 100 * blocks, blocks, out, 1) == SCSI_OK);
        memset(in, 0, sizeof(in));
        CHECK(scsi_read10(&d, 100 * blocks, blocks, in) == SCSI_OK);
        CHECK(!memcmp(in, out, blocks * 512));
        CHECK(!memcmp(f.mem + (size_t)100 * blocks * 512, out, blocks * 512));
    }
    CHECK(!d.bot.needs_reset && d.bot.stalls_cleared == 0 && d.last_bot == BOT_OK);
    CHECK(d.bot.commands == f.commands);
    fake_free(&f);
}

static void t_range_and_args(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    uint8_t buf[8192];
    fake_init(&f, 4096, 0);
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    uint32_t before = f.commands;
    CHECK(scsi_read10(&d, 4095, 1, buf) == SCSI_OK);
    CHECK(scsi_write10(&d, 4088, 8, buf, 1) == SCSI_OK);
    before = f.commands;
    CHECK(scsi_read10(&d, 4096, 1, buf) == SCSI_RANGE);
    CHECK(scsi_read10(&d, 4089, 8, buf) == SCSI_RANGE);
    CHECK(scsi_write10(&d, 4095, 2, buf, 1) == SCSI_RANGE);
    CHECK(scsi_write10(&d, 0xFFFFFFFFu, 8, buf, 1) == SCSI_RANGE);              /* lba + blocks must not wrap */
    CHECK(scsi_write10(&d, 0xFFFFFFFCu, 8, buf, 1) == SCSI_RANGE);
    CHECK(scsi_read10(&d, 0, 0, buf) == SCSI_ARG);
    CHECK(scsi_read10(&d, 0, SCSI_MAX_BLOCKS + 1, buf) == SCSI_ARG);
    CHECK(scsi_write10(&d, 0, 1, NULL, 1) == SCSI_ARG);
    CHECK(scsi_read10(NULL, 0, 1, buf) == SCSI_ARG);
    CHECK(f.commands == before);                                                  /* none of those reached the device */
    f.write_protected = 1;
    CHECK(scsi_write10(&d, 10, 1, buf, 1) == SCSI_PROTECTED && d.sense.valid && d.sense.key == SCSI_KEY_DATA_PROTECT);
    f.write_protected = 0;
    f.sync_unsupported = 1;
    CHECK(scsi_sync10(&d) == SCSI_CHECK && d.sense.key == SCSI_KEY_ILLEGAL_REQUEST && d.sense.asc == 0x20);
    CHECK(!d.bot.needs_reset);
    fake_free(&f);
}

struct fault_case {
    const char *name;
    int op;                         /* 0 TEST UNIT READY, 1 READ(10) of one block, 2 WRITE(10) of one block */
    enum fake_fault_kind kind;
    uint32_t arg;
    int recovery_ops;               /* 1: the transport has clear_halt and reset; 0: it has neither */
    enum bot_status want;
    int want_reset, want_cleared;
    uint32_t want_moved;
};

static const struct fault_case cases[] = {
    {"read ok", 1, FK_NONE, 0, 1, BOT_OK, 0, 0, 512},
    {"read stall in data stage", 1, FK_STALL_DATA, 0, 1, BOT_CMD_FAILED, 0, 1, 0},
    {"read stall, no clear_halt op", 1, FK_STALL_DATA, 0, 0, BOT_DATA_FAILED, 1, 0, 0},
    {"read short data", 1, FK_SHORT_DATA, 100, 1, BOT_SHORT, 0, 0, 100},
    {"read short data, status claims no residue", 1, FK_SHORT_DATA_QUIET, 100, 1, BOT_SHORT, 0, 0, 100},
    {"read bad tag", 1, FK_BAD_TAG, 0, 1, BOT_CSW_INVALID, 1, 0, 512},
    {"read bad signature", 1, FK_BAD_SIGNATURE, 0, 1, BOT_CSW_INVALID, 1, 0, 512},
    {"read short csw", 1, FK_SHORT_CSW, 0, 1, BOT_CSW_INVALID, 1, 0, 512},
    {"read phase error", 1, FK_PHASE_ERROR, 0, 1, BOT_PHASE_ERROR, 1, 0, 512},
    {"read reserved status", 1, FK_RESERVED_STATUS, 0, 1, BOT_CSW_INVALID, 1, 0, 512},
    {"read residue", 1, FK_RESIDUE, 7, 1, BOT_SHORT, 0, 0, 512},
    {"read residue too big", 1, FK_RESIDUE_TOO_BIG, 0, 1, BOT_CSW_INVALID, 1, 0, 512},
    {"read command failed", 1, FK_CMD_FAILED, SCSI_KEY_MEDIUM_ERROR, 1, BOT_CMD_FAILED, 0, 1, 0},
    {"read cbw timeout", 1, FK_TIMEOUT_CBW, 0, 1, BOT_CBW_FAILED, 1, 0, 0},
    {"read data timeout", 1, FK_TIMEOUT_DATA, 0, 1, BOT_DATA_FAILED, 1, 0, 0},
    {"read csw timeout", 1, FK_TIMEOUT_CSW, 0, 1, BOT_CSW_FAILED, 1, 0, 512},
    {"read csw stall once", 1, FK_STALL_CSW_ONCE, 0, 1, BOT_OK, 0, 1, 512},
    {"read csw stall once, no clear_halt op", 1, FK_STALL_CSW_ONCE, 0, 0, BOT_CSW_FAILED, 1, 0, 512},
    {"read csw stall always", 1, FK_STALL_CSW_ALWAYS, 0, 1, BOT_CSW_FAILED, 1, 1, 512},
    {"read wedge", 1, FK_WEDGE, 0, 1, BOT_CSW_FAILED, 1, 2, 0},
    {"write ok", 2, FK_NONE, 0, 1, BOT_OK, 0, 0, 512},
    {"write stall in data stage", 2, FK_STALL_DATA, 0, 1, BOT_CMD_FAILED, 0, 1, 0},
    {"write data timeout", 2, FK_TIMEOUT_DATA, 0, 1, BOT_DATA_FAILED, 1, 0, 0},
    {"write phase error", 2, FK_PHASE_ERROR, 0, 1, BOT_PHASE_ERROR, 1, 0, 512},
    {"write bad tag", 2, FK_BAD_TAG, 0, 1, BOT_CSW_INVALID, 1, 0, 512},
    {"write command failed", 2, FK_CMD_FAILED, SCSI_KEY_DATA_PROTECT, 1, BOT_CMD_FAILED, 0, 1, 0},
    {"tur ok", 0, FK_NONE, 0, 1, BOT_OK, 0, 0, 0},
    {"tur command failed", 0, FK_CMD_FAILED, SCSI_KEY_NOT_READY, 1, BOT_CMD_FAILED, 0, 0, 0},
    {"tur phase error", 0, FK_PHASE_ERROR, 0, 1, BOT_PHASE_ERROR, 1, 0, 0},
    {"tur csw timeout", 0, FK_TIMEOUT_CSW, 0, 1, BOT_CSW_FAILED, 1, 0, 0},
    {"tur short csw", 0, FK_SHORT_CSW, 0, 1, BOT_CSW_INVALID, 1, 0, 0},
};

static void t_fault_matrix(void)
{
    uint8_t buf[512], tur[6] = {0}, rd[10] = {0x28, 0, 0, 0, 0, 0, 0, 0, 1, 0}, wr[10] = {0x2A, 0, 0, 0, 0, 5, 0, 0, 1, 0};
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        const struct fault_case *c = &cases[i];
        struct fake_stick f;
        struct bot b;
        uint32_t moved = 99;
        enum bot_status st;
        fake_init(&f, 64, 0);
        for (int k = 0; k < 512; k++)
            buf[k] = (uint8_t)(k + 1);
        if (c->kind != FK_NONE)
            fake_fault_add(&f, 1, c->kind, c->arg);
        bot_init(&b, c->recovery_ops ? &fake_ops : &fake_ops_no_recovery, &f, 0);
        if (c->op == 0)
            st = bot_exec_none(&b, tur, 6);
        else if (c->op == 1)
            st = bot_exec_in(&b, rd, 10, buf, 512, &moved);
        else
            st = bot_exec_out(&b, wr, 10, buf, 512, &moved);
        if (st != c->want || b.needs_reset != c->want_reset || (int)b.stalls_cleared != c->want_cleared ||
            (c->op && (st == BOT_OK || st == BOT_SHORT || st == BOT_CMD_FAILED) && moved != c->want_moved)) {
            fprintf(stderr, "fault case '%s': status %s (want %s), needs_reset %d (want %d), cleared %u (want %d), "
                    "moved %u (want %u)\n", c->name, bot_status_name(st), bot_status_name(c->want), b.needs_reset,
                    c->want_reset, b.stalls_cleared, c->want_cleared, moved, c->want_moved);
            exit(1);
        }
        checks++;
        if (c->op == 2 && c->kind == FK_STALL_DATA)
            CHECK(f.n_writes == 0);                              /* a stalled write must not reach the store */
        if (c->want_reset) {
            /* A device that needs recovery is never sent another command. */
            uint32_t before = f.commands, in_calls = f.in_calls, out_calls = f.out_calls;
            CHECK(bot_exec_none(&b, tur, 6) == BOT_NEEDS_RESET);
            CHECK(f.commands == before && f.in_calls == in_calls && f.out_calls == out_calls);
            if (c->recovery_ops) {
                CHECK(bot_reset_recovery(&b) == BOT_OK && !b.needs_reset && b.resets == 1);
                CHECK(f.resets == 1 && !f.halt_in && !f.halt_out);
                CHECK(bot_exec_none(&b, tur, 6) == BOT_OK);        /* usable again */
            } else {
                CHECK(bot_reset_recovery(&b) == BOT_RESET_FAILED && b.needs_reset);   /* fail closed: unusable */
                CHECK(bot_exec_none(&b, tur, 6) == BOT_NEEDS_RESET);
            }
        }
        fake_free(&f);
    }
}

static void t_recovery_needs_both_steps(void)
{
    struct fake_stick f;
    struct bot b;
    uint8_t tur[6] = {0};
    fake_init(&f, 8, 0);
    bot_init(&b, &fake_ops, &f, 0);
    fake_fault_add(&f, 1, FK_PHASE_ERROR, 0);
    CHECK(bot_exec_none(&b, tur, 6) == BOT_PHASE_ERROR && b.needs_reset);
    f.allow_reset = 0;                                       /* the reset request fails */
    CHECK(bot_reset_recovery(&b) == BOT_RESET_FAILED && b.needs_reset);
    f.allow_reset = 1;
    f.allow_clear_halt = 0;                                  /* the reset works, the halts cannot be cleared */
    CHECK(bot_reset_recovery(&b) == BOT_RESET_FAILED && b.needs_reset);
    CHECK(bot_exec_none(&b, tur, 6) == BOT_NEEDS_RESET);
    f.allow_clear_halt = 1;
    CHECK(bot_reset_recovery(&b) == BOT_OK && !b.needs_reset);
    CHECK(f.clear_in >= 1 && f.clear_out >= 1);
    fake_free(&f);
}

static void t_tags(void)
{
    struct fake_stick f;
    struct bot b;
    uint8_t tur[6] = {0};
    fake_init(&f, 8, 0);
    bot_init(&b, &fake_ops, &f, 0);
    CHECK(bot_exec_none(&b, tur, 6) == BOT_OK && f.cur_tag == 1);
    CHECK(bot_exec_none(&b, tur, 6) == BOT_OK && f.cur_tag == 2);
    b.tag = 0xFFFFFFFFu;
    CHECK(bot_exec_none(&b, tur, 6) == BOT_OK && f.cur_tag == 1);   /* 0 is skipped */
    fake_free(&f);
}

static void t_args(void)
{
    struct fake_stick f;
    struct bot b;
    uint8_t tur[6] = {0}, buf[16], cdb17[17] = {0};
    fake_init(&f, 8, 0);
    bot_init(&b, &fake_ops, &f, 0);
    CHECK(bot_exec_none(&b, NULL, 6) == BOT_ARG);
    CHECK(bot_exec_none(&b, tur, 0) == BOT_ARG);
    CHECK(bot_exec_none(&b, cdb17, 17) == BOT_ARG);
    CHECK(bot_exec_in(&b, tur, 6, buf, 0, NULL) == BOT_ARG);              /* a data stage needs a length */
    CHECK(bot_exec_in(&b, tur, 6, NULL, 8, NULL) == BOT_ARG);
    CHECK(bot_exec_out(&b, tur, 6, NULL, 8, NULL) == BOT_ARG);
    b.lun = 16;
    CHECK(bot_exec_none(&b, tur, 6) == BOT_ARG);
    CHECK(f.commands == 0);
    CHECK(bot_exec_none(NULL, tur, 6) == BOT_ARG);
    CHECK(bot_reset_recovery(NULL) == BOT_ARG);
    fake_free(&f);
}

static void t_open_paths(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    fake_init(&f, 256, 0);
    f.ua_left = 2;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK && f.ua_left == 0);        /* UNIT ATTENTION is retried */
    f.ua_left = SCSI_TUR_TRIES + 3;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) != SCSI_OK);                          /* but not forever */
    f.ua_left = 0;
    f.not_ready_left = 100;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_NOT_READY);
    f.not_ready_left = 0;
    f.inquiry_type = 5;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_NOT_DISK && d.device_type == 5);
    f.inquiry_type = 0x20;                                                      /* qualifier: not connected */
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_NOT_DISK);
    f.inquiry_type = 0;
    f.block_len = 4096;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_BLOCK_SIZE);
    f.block_len = 512;
    f.last_lba_override = 0xFFFFFFFFu;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_CAPACITY);
    f.last_lba_override = 0;
    f.inquiry_bytes = 5;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK && d.vendor[0] == 0);       /* a short INQUIRY is enough */
    f.inquiry_bytes = 4;
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_SHORT);
    f.inquiry_bytes = 36;
    f.commands = 0;
    fake_fault_add(&f, 1, FK_TIMEOUT_CSW, 0);
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_TRANSPORT && f.commands == 1);  /* transport trouble is not retried */
    fake_free(&f);
    fake_init(&f, 256, 0);
    fake_fault_add(&f, 2, FK_CMD_FAILED, SCSI_KEY_ILLEGAL_REQUEST);                /* INQUIRY fails */
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_CHECK && d.sense.key == SCSI_KEY_ILLEGAL_REQUEST);
    CHECK(scsi_open(NULL, &fake_ops, &f, 0) == SCSI_ARG && scsi_open(&d, NULL, &f, 0) == SCSI_ARG);
    fake_free(&f);
}

static void t_sense_decode(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    fake_init(&f, 64, 0);
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    fake_set_sense(&f, 3, 0x11, 0x07);
    CHECK(scsi_request_sense(&d) == SCSI_OK && d.sense.valid && d.sense.key == 3 && d.sense.asc == 0x11 && d.sense.ascq == 7);
    CHECK(scsi_request_sense(&d) == SCSI_OK && d.sense.valid && d.sense.key == 0);   /* cleared by the first read */
    fake_free(&f);
}

/* A seeded random run: faults on random commands, recovery after each, the same invariants after every step. */
static void t_fuzz(void)
{
    struct fake_stick f;
    struct scsi_dev d;
    uint8_t buf[4096], ref[4096];
    uint64_t sectors = 512;
    fake_init(&f, sectors, 0);
    CHECK(scsi_open(&d, &fake_ops, &f, 0) == SCSI_OK);
    for (int i = 0; i < 4000; i++) {
        uint32_t next = f.commands + 1;
        enum fake_fault_kind k = FK_NONE;
        memset(f.fault, 0, sizeof(f.fault));
        if (rnd() % 4 == 0)
            k = (enum fake_fault_kind)(1 + rnd() % FK_WEDGE);
        if (k != FK_NONE)
            fake_fault_add(&f, next, k, (uint32_t)(rnd() % 64));
        uint32_t lba = (uint32_t)(rnd() % (sectors + 8)), blocks = 1 + (uint32_t)(rnd() % SCSI_MAX_BLOCKS);
        enum scsi_status st;
        int op = (int)(rnd() % 5);
        uint32_t cmds_before = f.commands, bot_before = d.bot.commands;
        for (uint32_t j = 0; j < sizeof(buf); j++)
            buf[j] = (uint8_t)rnd();
        if (op == 0) {
            st = scsi_read10(&d, lba, blocks, buf);
            if (st == SCSI_OK) {
                CHECK(lba + blocks <= sectors);
                memcpy(ref, f.mem + (size_t)lba * 512, blocks * 512u);
                CHECK(!memcmp(ref, buf, blocks * 512u));
            }
        } else if (op == 1) {
            uint32_t writes = f.n_writes;
            st = scsi_write10(&d, lba, blocks, buf, rnd() & 1);
            if (st == SCSI_RANGE)
                CHECK(f.n_writes == writes);
            else if (f.n_writes != writes)
                CHECK(f.write_lba[writes] == lba && f.write_blocks[writes] == blocks);
        } else if (op == 2) {
            st = scsi_test_unit_ready(&d);
        } else if (op == 3) {
            st = scsi_inquiry(&d);
        } else {
            st = scsi_sync10(&d);
        }
        (void)st;
        /* The driver counts a command if and only if the device saw its CBW attempt. */
        CHECK(f.commands == d.bot.commands);
        CHECK(f.commands - cmds_before == d.bot.commands - bot_before);
        if (d.bot.needs_reset) {
            uint32_t before = f.commands;
            CHECK(scsi_test_unit_ready(&d) == SCSI_TRANSPORT && d.last_bot == BOT_NEEDS_RESET && f.commands == before);
            CHECK(bot_reset_recovery(&d.bot) == BOT_OK);
        }
        f.halt_in = f.halt_out = 0;
        f.state = FS_IDLE;
        f.ua_left = f.not_ready_left = 0;
    }
    CHECK(f.violation == 0);
    fake_free(&f);
}

static int hex_nibble(int c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

static int hex_parse(const char *s, uint8_t *out, size_t max)
{
    size_t n = 0;
    if (s[0] == '-' && !s[1])
        return 0;                       /* "-" stands for no bytes, so that an empty field survives sscanf */
    while (s[0] && s[1] && n < max) {
        int a = hex_nibble(s[0]), b = hex_nibble(s[1]);
        if (a < 0 || b < 0)
            return -1;
        out[n++] = (uint8_t)(a * 16 + b);
        s += 2;
    }
    return (int)n;
}

static void hex_print(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++)
        printf("%02x", p[i]);
    printf("\n");
}

/* One command per line: B tag len dir lun cdbhex | C cswhex tag requested | Q R|W lba blocks fua | U kind arg */
static int stdin_mode(void)
{
    char line[1024];
    while (fgets(line, sizeof(line), stdin)) {
        char op = line[0], word[600];
        unsigned long a, b, c, d;
        uint8_t raw[BOT_CBW_BYTES + 2], cdb[16];
        if (op == 'B' && sscanf(line + 1, "%lx %lu %lu %lu %599s", &a, &b, &c, &d, word) == 5) {
            int n = hex_parse(word, cdb, sizeof(cdb));
            bot_build_cbw(raw, (uint32_t)a, (uint32_t)b, (enum bot_dir)c, (uint8_t)d, cdb, (uint32_t)n);
            hex_print(raw, BOT_CBW_BYTES);
        } else if (op == 'C' && sscanf(line + 1, "%599s %lx %lu", word, &a, &b) == 3) {
            uint8_t csw[32], status = 0;
            uint32_t residue = 0;
            int n = hex_parse(word, csw, sizeof(csw));
            enum bot_status st = bot_check_csw(csw, (uint32_t)n, (uint32_t)a, (uint32_t)b, &status, &residue);
            if (st == BOT_OK)
                printf("OK %u %u\n", status, residue);
            else
                printf("%s\n", st == BOT_PHASE_ERROR ? "PHASE" : "INVALID");
        } else if (op == 'Q' && sscanf(line + 1, " %c %lu %lu %lu", (char *)&word[0], &a, &b, &c) == 4) {
            struct fake_stick f;
            struct scsi_dev dev;
            uint8_t buf[4096] = {0};
            fake_init(&f, 0xFFFFFFFFu, 1);
            if (scsi_open(&dev, &fake_ops, &f, 0) != SCSI_OK)
                return 2;
            f.last_cdb_len = 0;
            enum scsi_status st = word[0] == 'R' ? scsi_read10(&dev, (uint32_t)a, (uint32_t)b, buf)
                                                  : scsi_write10(&dev, (uint32_t)a, (uint32_t)b, buf, (int)c);
            if (st != SCSI_OK)
                printf("%s\n", scsi_status_name(st));
            else
                hex_print(f.last_cdb, f.last_cdb_len);
        } else if (op == 'U' && sscanf(line + 1, "%lu %lx", &a, &b) == 2) {
            uint8_t set[BOT_SETUP_BYTES];
            if (a == 0)
                bot_setup_get_max_lun(set, (uint16_t)b);
            else if (a == 1)
                bot_setup_reset(set, (uint16_t)b);
            else
                bot_setup_clear_halt(set, (uint8_t)b);
            hex_print(set, BOT_SETUP_BYTES);
        } else {
            printf("?\n");
        }
        fflush(stdout);
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--stdin"))
        return stdin_mode();
    t_encoders();
    t_cdbs();
    t_normal();
    t_range_and_args();
    t_fault_matrix();
    t_recovery_needs_both_steps();
    t_tags();
    t_args();
    t_open_paths();
    t_sense_decode();
    t_fuzz();
    printf("%lu checks\nPASS\n", checks);
    return 0;
}
