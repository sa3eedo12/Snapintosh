/* SPDX-License-Identifier: MIT */
/* Host harness for compat/board-info.c, driven by tools/test-board-info.py.
 *
 *   test-board-info layout     print sizeof/offsetof/enum values, one "name value" per line
 *   test-board-info strerror   print every error code with its description
 *   test-board-info batch      read records from stdin and print one verdict per record
 *
 * A batch record is: u32 payload length, u32 mode, payload bytes (little-endian host). mode 0 gives the
 * validator the payload in a heap block of exactly that size, so AddressSanitizer reports any read past the length
 * it was given; 1..7 shifts the pointer by that many bytes; BATCH_NULL_BLOB passes a null blob and BATCH_NULL_OUT
 * a null out pointer. The verdict line is "<error code> <unready mask in hex, or - when refused>". */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../compat/board-info.h"

#define BATCH_NULL_BLOB 0xffffffffu
#define BATCH_NULL_OUT 0xfffffffeu
/* A non-null stand-in for stale caller data; a validator that fails without clearing *out leaves it visible. */
#define BATCH_POISON 0x10u

#define SIZE(type) printf("sizeof.%s %zu\n", #type, sizeof(struct type))
#define OFFSET(type, field) printf("offsetof.%s.%s %zu\n", #type, #field, offsetof(struct type, field))
#define VALUE(name) printf("%s %llu\n", #name, (unsigned long long)(name))

static int print_layout(void)
{
    SIZE(board_info_header); SIZE(board_info_ecam); SIZE(board_info_smmu); SIZE(board_info_range); SIZE(board_info);
    OFFSET(board_info_header, abi_major); OFFSET(board_info_header, abi_minor); OFFSET(board_info_header, total_length);
    OFFSET(board_info_header, crc32); OFFSET(board_info_header, flags_required); OFFSET(board_info_header, flags_optional);
    OFFSET(board_info, status); OFFSET(board_info, soc_id); OFFSET(board_info, board_id); OFFSET(board_info, known);
    OFFSET(board_info, reserved0); OFFSET(board_info, midr_count); OFFSET(board_info, reserved1);
    OFFSET(board_info, midr_value); OFFSET(board_info, midr_mask); OFFSET(board_info, cluster_count);
    OFFSET(board_info, cores_per_cluster); OFFSET(board_info, affinity_mask); OFFSET(board_info, cluster_mask);
    OFFSET(board_info, mpidr_count); OFFSET(board_info, reserved2); OFFSET(board_info, mpidr);
    OFFSET(board_info, gic_version); OFFSET(board_info, its_present); OFFSET(board_info, gicd_base);
    OFFSET(board_info, gicd_size); OFFSET(board_info, gicr_base); OFFSET(board_info, gicr_stride);
    OFFSET(board_info, gicr_size); OFFSET(board_info, its_base); OFFSET(board_info, its_size);
    OFFSET(board_info, timer_hz); OFFSET(board_info, timer_ppi); OFFSET(board_info, ecam_count);
    OFFSET(board_info, reserved3); OFFSET(board_info, ecam); OFFSET(board_info, smmu_count);
    OFFSET(board_info, reserved4); OFFSET(board_info, smmu); OFFSET(board_info, range_count);
    OFFSET(board_info, reserved5); OFFSET(board_info, range); OFFSET(board_info, device_present);
    OFFSET(board_info, device_absent); OFFSET(board_info, device_mmio); OFFSET(board_info, ipa_bits);
    OFFSET(board_info_ecam, base); OFFSET(board_info_ecam, segment); OFFSET(board_info_ecam, start_bus);
    OFFSET(board_info_ecam, end_bus);
    OFFSET(board_info_ecam, reserved); OFFSET(board_info_smmu, base); OFFSET(board_info_smmu, size);
    OFFSET(board_info_smmu, kind); OFFSET(board_info_smmu, reserved); OFFSET(board_info_range, base);
    OFFSET(board_info_range, size); OFFSET(board_info_range, kind); OFFSET(board_info_range, geometry_known);
    OFFSET(board_info_range, device); OFFSET(board_info_range, reserved);

    VALUE(BOARD_INFO_ABI_MAJOR); VALUE(BOARD_INFO_ABI_MINOR); VALUE(BOARD_INFO_V1_SIZE); VALUE(BOARD_INFO_MAX_LENGTH);
    VALUE(BOARD_INFO_NAME_LEN); VALUE(BOARD_INFO_MAX_MIDR); VALUE(BOARD_INFO_MAX_CPUS); VALUE(BOARD_INFO_MAX_ECAM);
    VALUE(BOARD_INFO_MAX_SMMU); VALUE(BOARD_INFO_MAX_RANGES); VALUE(BOARD_INFO_IPA_BITS); VALUE(BOARD_INFO_PAGE);
    VALUE(BOARD_INFO_DEVICE_NONE); VALUE(BOARD_INFO_FLAGS_REQUIRED_KNOWN); VALUE(BOARD_INFO_FLAGS_OPTIONAL_KNOWN);
    VALUE(BOARD_INFO_STATUS_VERIFIED); VALUE(BOARD_INFO_STATUS_OBSERVED); VALUE(BOARD_INFO_STATUS_INFERRED);
    VALUE(BOARD_INFO_STATUS_EXPERIMENTAL); VALUE(BOARD_INFO_STATUS_UNIMPLEMENTED); VALUE(BOARD_INFO_STATUS_COUNT);
    VALUE(BOARD_INFO_SOC_NONE); VALUE(BOARD_INFO_SOC_X1E80100); VALUE(BOARD_INFO_SOC_X2E_96_100);
    VALUE(BOARD_INFO_SOC_COUNT);
    VALUE(BOARD_INFO_DEV_GPU); VALUE(BOARD_INFO_DEV_GPU_GMU); VALUE(BOARD_INFO_DEV_DISPLAY);
    VALUE(BOARD_INFO_DEV_UART_DEBUG); VALUE(BOARD_INFO_DEV_USB_HOST); VALUE(BOARD_INFO_DEV_KEYBOARD);
    VALUE(BOARD_INFO_DEV_TOUCHPAD); VALUE(BOARD_INFO_DEV_TOUCHSCREEN); VALUE(BOARD_INFO_DEV_WIFI);
    VALUE(BOARD_INFO_DEV_NVME); VALUE(BOARD_INFO_DEV_COUNT);
    VALUE(BOARD_INFO_RANGE_DRAM); VALUE(BOARD_INFO_RANGE_MMIO); VALUE(BOARD_INFO_SMMU_V3); VALUE(BOARD_INFO_SMMU_MMU500);
    VALUE(BOARD_INFO_KNOWN_SOC); VALUE(BOARD_INFO_KNOWN_MIDR); VALUE(BOARD_INFO_KNOWN_COUNTS);
    VALUE(BOARD_INFO_KNOWN_AFFINITY); VALUE(BOARD_INFO_KNOWN_MPIDRS); VALUE(BOARD_INFO_KNOWN_GIC_VERSION);
    VALUE(BOARD_INFO_KNOWN_GICD); VALUE(BOARD_INFO_KNOWN_GICR); VALUE(BOARD_INFO_KNOWN_ITS);
    VALUE(BOARD_INFO_KNOWN_SMMU); VALUE(BOARD_INFO_KNOWN_TIMER_HZ); VALUE(BOARD_INFO_KNOWN_TIMER_PPI);
    VALUE(BOARD_INFO_KNOWN_ECAM); VALUE(BOARD_INFO_KNOWN_RANGES); VALUE(BOARD_INFO_KNOWN_IPA);
    VALUE(BOARD_INFO_KNOWN_ALL);
    VALUE(BOARD_INFO_OK); VALUE(BOARD_INFO_ERR_NULL); VALUE(BOARD_INFO_ERR_ALIGN); VALUE(BOARD_INFO_ERR_TRUNCATED);
    VALUE(BOARD_INFO_ERR_MAGIC); VALUE(BOARD_INFO_ERR_MAJOR); VALUE(BOARD_INFO_ERR_LENGTH); VALUE(BOARD_INFO_ERR_CRC);
    VALUE(BOARD_INFO_ERR_REQUIRED_FLAGS); VALUE(BOARD_INFO_ERR_OPTIONAL_FLAGS); VALUE(BOARD_INFO_ERR_COUNT);
    VALUE(BOARD_INFO_ERR_RESERVED); VALUE(BOARD_INFO_ERR_VALUE); VALUE(BOARD_INFO_ERR_CONSISTENCY);
    VALUE(BOARD_INFO_ERR_WINDOW); VALUE(BOARD_INFO_ERR_OVERLAP); VALUE(BOARD_INFO_ERR_COUNT_ALL);
    VALUE(BOARD_INFO_UNREADY_STATUS); VALUE(BOARD_INFO_UNREADY_SOC); VALUE(BOARD_INFO_UNREADY_MIDR);
    VALUE(BOARD_INFO_UNREADY_COUNTS); VALUE(BOARD_INFO_UNREADY_AFFINITY); VALUE(BOARD_INFO_UNREADY_MPIDRS);
    VALUE(BOARD_INFO_UNREADY_GIC_VERSION); VALUE(BOARD_INFO_UNREADY_GICD); VALUE(BOARD_INFO_UNREADY_GICR);
    VALUE(BOARD_INFO_UNREADY_ITS); VALUE(BOARD_INFO_UNREADY_SMMU); VALUE(BOARD_INFO_UNREADY_TIMER_HZ);
    VALUE(BOARD_INFO_UNREADY_TIMER_PPI); VALUE(BOARD_INFO_UNREADY_ECAM); VALUE(BOARD_INFO_UNREADY_RANGES);
    VALUE(BOARD_INFO_UNREADY_DEVICES); VALUE(BOARD_INFO_UNREADY_IPA);
    return 0;
}

static int print_strerror(void)
{
    int code;

    for (code = -1; code <= BOARD_INFO_ERR_COUNT_ALL + 1; code++)
        printf("%d\t%s\n", code, board_info_strerror(code));
    return 0;
}

static int read_exact(void *dst, size_t n)
{
    return n == 0 || fread(dst, 1, n, stdin) == n;
}

static int run_batch(void)
{
    uint32_t header[2];

    while (fread(header, sizeof header[0], 2, stdin) == 2) {
        uint32_t length = header[0], mode = header[1];
        size_t shift = mode >= 1 && mode <= 7 ? mode : 0;
        uint8_t *block = malloc(length + shift ? length + shift : 1);
        const struct board_info *info = (const struct board_info *)(uintptr_t)BATCH_POISON;
        const struct board_info **out = &info;
        const void *blob;
        int code;

        if (!block || !read_exact(block + shift, length)) {
            fprintf(stderr, "short batch record\n");
            free(block);
            return 2;
        }
        blob = mode == BATCH_NULL_BLOB ? NULL : block + shift;
        if (mode == BATCH_NULL_OUT)
            out = NULL;
        code = board_info_validate(blob, length, out);
        if (code == BOARD_INFO_OK && info != blob)
            code = -1000;               /* success must hand back the caller's own memory */
        if (code != BOARD_INFO_OK && out && info)
            code = -1001;               /* failure must clear a stale out pointer, never leave it for the caller */
        if (code == BOARD_INFO_OK)
            printf("0 %x\n", (unsigned)board_info_unready(info));
        else
            printf("%d -\n", code);
        free(block);
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "layout"))
        return print_layout();
    if (argc == 2 && !strcmp(argv[1], "strerror"))
        return print_strerror();
    if (argc == 2 && !strcmp(argv[1], "batch"))
        return run_batch();
    fprintf(stderr, "usage: %s layout|strerror|batch\n", argv[0]);
    return 2;
}
