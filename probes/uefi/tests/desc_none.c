/* SPDX-License-Identifier: MIT */
/* The descriptor for builds that do not use one (the emulator model, the host
 * tests): every value unknown, so every cross-check reports "unknown" instead of
 * comparing against an invented number. */
#include "desc.h"

const tests_desc tests_desc_data = {0};
