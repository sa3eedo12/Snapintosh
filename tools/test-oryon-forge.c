/* SPDX-License-Identifier: MIT */
/* Host harness for the id_forge() that probes/idregs.h really contains.
 *
 * tools/test-oryon-idregs.py writes idregs-under-test.h next to the build: the
 * header's own text, with only id_register() replaced by the stub below (the
 * real one is a table of MRS instructions that cannot run on a host) and the
 * include of the normalizer pointed at compat/. What this program calls is
 * therefore the monitor's own id_forge(), with the SNAP_PLATFORM_SP11 hook when
 * it is built with -DSNAP_PLATFORM_SP11=1, answered from a made-up register
 * file.
 *
 * stdin:  "<crm> <op2> <value> <midr> <isar1> <isar2>" per line, in hex
 * stdout: "<id_forge result> <registers read>" per line, in hex. The second
 *         number has bit (crm * 8 + op2) set for every ID register the stub was
 *         asked for, which pins what the hook is allowed to read.
 *
 * No value here is a measurement of any machine. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint64_t stub_register[8][8];
static uint64_t stub_reads;

#include "idregs-under-test.h"

int main(void)
{
    char line[512];

    /* Unused when the SP11 hook is compiled out (or a weakened copy stops calling it), and
     * -Werror knows it. The comparison with the model is what has to notice, not the compiler. */
    (void)id_register;
#if SNAP_PLATFORM_SP11
    (void)id_forge_sp11;
#endif
    while (fgets(line, sizeof line, stdin)) {
        unsigned crm, op2;
        unsigned long long value, midr, isar1, isar2;

        if (sscanf(line, "%x %x %llx %llx %llx %llx", &crm, &op2, &value, &midr, &isar1,
                   &isar2) != 6) {
            fprintf(stderr, "bad line: %s", line);
            return 2;
        }
        memset(stub_register, 0, sizeof stub_register);
        stub_register[0][0] = midr;
        stub_register[6][1] = isar1;
        stub_register[6][2] = isar2;
        stub_reads = 0;
        printf("%llx %llx\n", (unsigned long long)id_forge(crm, op2, value),
               (unsigned long long)stub_reads);
    }
    return 0;
}
