/* SPDX-License-Identifier: MIT */
/* Writes the X2 result files into the run directory the probe's dump created,
 * on the USB stick the image was loaded from, before ExitBootServices.
 *
 * The same rules as probes/uefi/dump.h, restated because this is a second
 * writer: only the volume the image itself came from is opened, only if its
 * device path has a USB node and no other storage transport; the run directory
 * must already exist (this code never creates a directory); a file that
 * already exists is never opened for writing; every file is flushed and closed,
 * then the directories and the volume root are flushed and closed. Any UEFI
 * error ends the writing. Nothing else on the machine is touched.
 */
#ifndef TESTS_STICK_H
#define TESTS_STICK_H

#include <stddef.h>
#include <stdint.h>

#include "efi_types.h"

typedef struct {
    const char *name; /* at most TESTS_STICK_NAME_MAX characters, FAT-safe */
    const void *data;
    size_t len;
} tests_file;

#define TESTS_STICK_NAME_MAX 24u
#define TESTS_STICK_MAX_FILES 16u
#define TESTS_STICK_MAX_FILE_BYTES (256u * 1024u)
#define TESTS_STICK_MAX_TOTAL_BYTES (1024u * 1024u)
#define TESTS_STICK_DP_MAX_LEN 2048u

enum tests_stick_state {
    STICK_OK = 0,
    STICK_NOT_ATTEMPTED,
    STICK_REFUSED_NO_VOLUME,
    STICK_REFUSED_NOT_USB,
    STICK_REFUSED_AMBIGUOUS,
    STICK_REFUSED_MALFORMED,
    STICK_NO_RUN_DIR, /* SP11DUMP or RUNnn is not there: nothing is created */
    STICK_REFUSED_ARGS,
    STICK_EXISTS, /* a file of that name is already on the stick: not touched */
    STICK_ERROR,
    STICK_PARTIAL,
    STICK_STATE_COUNT
};

const char *tests_stick_state_name(int state);

/* Writes files[0..n) into \SP11DUMP\RUN<run>\. *written receives the number of
 * files completed. Returns an enum tests_stick_state. run is 1..99. */
int tests_stick_write(efi_system_table *st, efi_handle image, unsigned run,
                      const tests_file *files, unsigned n, unsigned *written);

#endif /* TESTS_STICK_H */
