/* SPDX-License-Identifier: MIT */
#include "regs.h"

static const struct {
    const char *name;
    uint8_t enc[5];
} sysreg_table[TR_COUNT] = {
#define X(name, op0, op1, crn, crm, op2) {#name, {op0, op1, crn, crm, op2}},
    TESTS_SYSREG_LIST(X)
#undef X
};

static const char *const wreg_names[W_COUNT] = {
    "VBAR_EL2", "HCR_EL2", "VNCR_EL2", "SCTLR_EL1/12", "VBAR_EL1/12", "SP_EL1", "DAIF",
    "ELR_EL2", "SPSR_EL2",
};

const char *tests_sysreg_name(int id)
{
    if (id < 0 || id >= TR_COUNT)
        return "?";
    return sysreg_table[id].name;
}

int tests_sysreg_encoding(int id, unsigned enc[5])
{
    if (id < 0 || id >= TR_COUNT)
        return -1;
    for (int i = 0; i < 5; i++)
        enc[i] = sysreg_table[id].enc[i];
    return 0;
}

uint32_t tests_sysreg_mrs_word(int id)
{
    unsigned e[5];

    if (tests_sysreg_encoding(id, e) != 0)
        return 0;
    /* MRS carries op0 as the single bit o0 = op0 - 2 at bit 19. */
    return 0xD5300000u | ((uint32_t)(e[0] - 2) << 19) | (e[1] << 16) | (e[2] << 12) |
           (e[3] << 8) | (e[4] << 5);
}

const char *tests_wreg_name(int id)
{
    if (id < 0 || id >= W_COUNT)
        return "?";
    return wreg_names[id];
}

int tests_wreg_allowed(int id)
{
    return id >= 0 && id < W_COUNT;
}
