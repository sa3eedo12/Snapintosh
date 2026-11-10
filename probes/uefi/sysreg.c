/* SPDX-License-Identifier: MIT */
#include "sysreg.h"

static const struct {
    const char *name;
    uint8_t enc[PROBE_SYSREG_ENC_FIELDS];
} sysreg_table[PSR_COUNT] = {
#define X(name, op0, op1, crn, crm, op2) {#name, {op0, op1, crn, crm, op2}},
    PROBE_SYSREG_LIST(X)
#undef X
};

const char *probe_sysreg_name(int id)
{
    if (id < 0 || id >= PSR_COUNT)
        return "?";
    return sysreg_table[id].name;
}

int probe_sysreg_encoding(int id, unsigned enc[PROBE_SYSREG_ENC_FIELDS])
{
    if (id < 0 || id >= PSR_COUNT)
        return -1;
    for (int i = 0; i < PROBE_SYSREG_ENC_FIELDS; i++)
        enc[i] = sysreg_table[id].enc[i];
    return 0;
}

uint32_t probe_sysreg_mrs_word(int id)
{
    unsigned e[PROBE_SYSREG_ENC_FIELDS];
    if (probe_sysreg_encoding(id, e) != 0)
        return 0;
    /* MRS places op0 as the single bit o0 = op0 - 2 at bit 19. */
    return 0xD5300000u | ((uint32_t)(e[0] - 2) << 19) | (e[1] << 16) | (e[2] << 12) |
           (e[3] << 8) | (e[4] << 5);
}
