/* SPDX-License-Identifier: MIT */
/* Host harness for compat/display-pipeline-check.c, driven by tools/test-display-pipeline-check.py.
 *
 *   test-display-pipeline-check layout    print sizeof/offsetof/enum values, one "name value" per line
 *   test-display-pipeline-check names     print every verdict and reason name
 *   test-display-pipeline-check batch     read records from stdin, print one verdict line per record
 *   test-display-pipeline-check writer    read one writer request from stdin, print the snapshot the C writer builds
 *
 * A batch record is: u32 params length (must be sizeof(struct dpc_params)), u32 snapshot length, u32 mode, the params
 * bytes, the snapshot bytes (little-endian host). Both buffers are exact-size heap blocks, so AddressSanitizer
 * reports any read past the length the checker was given. Mode 0 passes the snapshot aligned; 1..7 shifts its
 * pointer by that many bytes (the wire format is read byte by byte and must not care); BATCH_NULL_PARAMS,
 * BATCH_NULL_SNAP and BATCH_NULL_OUT pass a null pointer for that argument. The verdict line is
 * "<verdict> <why> <detail> <evidence>" in decimal, or "-1" when the call itself returned -1.
 *
 * A writer request is: u32 intf_index, u32 ctl_count, u32 gap_us, u32 put count, then per put u32 kind (0 register,
 * 1 CTL), u32 index, u64 pa, u32 value, u32 status. The reply is "len <n>", one "put <i> <rc>" per put and
 * "hex <bytes>". */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../compat/display-pipeline-check.h"

#define BATCH_NULL_PARAMS 0xffffffffu
#define BATCH_NULL_SNAP 0xfffffffeu
#define BATCH_NULL_OUT 0xfffffffdu

#define SIZE(type) printf("sizeof.%s %zu\n", #type, sizeof(struct type))
#define OFFSET(type, field) printf("offsetof.%s.%s %zu\n", #type, #field, offsetof(struct type, field))
#define VALUE(name) printf("%s %llu\n", #name, (unsigned long long)(name))

static int print_layout(void)
{
    SIZE(dpc_params); SIZE(dpc_block); SIZE(dpc_result);
    OFFSET(dpc_params, abi); OFFSET(dpc_params, required); OFFSET(dpc_params, intf_index);
    OFFSET(dpc_params, dpu_major); OFFSET(dpc_params, dpu_minor); OFFSET(dpc_params, gop_width);
    OFFSET(dpc_params, gop_height); OFFSET(dpc_params, ctl_count); OFFSET(dpc_params, ctl_length);
    OFFSET(dpc_params, ctl_intf_active_off); OFFSET(dpc_params, refresh_hz_min); OFFSET(dpc_params, refresh_hz_max);
    OFFSET(dpc_params, counter_gap_min_us); OFFSET(dpc_params, reserved); OFFSET(dpc_params, gcc);
    OFFSET(dpc_params, dispcc); OFFSET(dpc_params, mdss); OFFSET(dpc_params, mdp); OFFSET(dpc_params, intf);
    OFFSET(dpc_params, ctl_base); OFFSET(dpc_params, reg_off);
    OFFSET(dpc_block, base); OFFSET(dpc_block, length);
    OFFSET(dpc_result, verdict); OFFSET(dpc_result, why); OFFSET(dpc_result, detail); OFFSET(dpc_result, evidence);

    VALUE(DPC_MAX_CTL); VALUE(DPC_MAX_INTF_INDEX); VALUE(DPC_MAX_DIMENSION); VALUE(DPC_PARAMS_ABI);
    VALUE(DPC_SNAP_MAGIC); VALUE(DPC_SNAP_ABI_MAJOR); VALUE(DPC_SNAP_ABI_MINOR); VALUE(DPC_SNAP_HEADER);
    VALUE(DPC_SNAP_ENTRY); VALUE(DPC_SNAP_OFF_TOTAL); VALUE(DPC_SNAP_OFF_INTF); VALUE(DPC_SNAP_OFF_REGS);
    VALUE(DPC_SNAP_OFF_CTLS); VALUE(DPC_SNAP_OFF_GAP);
    VALUE(DPC_ST_NOT_TRIED); VALUE(DPC_ST_OK); VALUE(DPC_ST_FAULT);
    VALUE(DPC_EV_GATES); VALUE(DPC_EV_VERSION); VALUE(DPC_EV_TIMING); VALUE(DPC_EV_GEOMETRY); VALUE(DPC_EV_ROUTING);
    VALUE(DPC_EV_COUNTERS); VALUE(DPC_EV_ALL); VALUE(DPC_EV_CORE);
    VALUE(DPC_CBCR_CLK_OFF); VALUE(DPC_GDSC_PWR_ON); VALUE(DPC_INTF_EN_BIT); VALUE(DPC_INTF_STATUS_EN);
    VALUE(DPC_INTF_CFG_ACTIVE_H_EN); VALUE(DPC_INTF_CFG_ACTIVE_V_EN); VALUE(DPC_INTF_CFG2_WIDEN);
    VALUE(DPC_ACTIVE); VALUE(DPC_INACTIVE); VALUE(DPC_UNKNOWN); VALUE(DPC_INCONSISTENT);
    VALUE(DPC_BLK_GCC); VALUE(DPC_BLK_DISPCC); VALUE(DPC_BLK_MDSS); VALUE(DPC_BLK_INTF); VALUE(DPC_BLK_COUNT);
    VALUE(DPC_REG_GCC_DISP_AHB); VALUE(DPC_REG_GDSC); VALUE(DPC_REG_AHB_CBCR); VALUE(DPC_REG_MDP_CBCR);
    VALUE(DPC_REG_HW_VERSION);
    VALUE(DPC_REG_INTF_EN); VALUE(DPC_REG_INTF_STATUS); VALUE(DPC_REG_INTF_CONFIG); VALUE(DPC_REG_INTF_HSYNC_CTL);
    VALUE(DPC_REG_INTF_VSYNC_PERIOD); VALUE(DPC_REG_INTF_DISP_V_START); VALUE(DPC_REG_INTF_DISP_V_END);
    VALUE(DPC_REG_INTF_ACT_V_START); VALUE(DPC_REG_INTF_ACT_V_END); VALUE(DPC_REG_INTF_DISP_HCTL);
    VALUE(DPC_REG_INTF_ACT_HCTL); VALUE(DPC_REG_INTF_CONFIG2); VALUE(DPC_REG_INTF_FLC_EN);
    VALUE(DPC_REG_INTF_FRAME_A); VALUE(DPC_REG_INTF_FRAME_B); VALUE(DPC_REG_COUNT);
    VALUE(DPC_WHY_OK); VALUE(DPC_WHY_BAD_PARAMS); VALUE(DPC_WHY_TRUNCATED); VALUE(DPC_WHY_MALFORMED);
    VALUE(DPC_WHY_NOT_READ); VALUE(DPC_WHY_READ_FAULT); VALUE(DPC_WHY_ALL_ONES); VALUE(DPC_WHY_MISSING_EVIDENCE);
    VALUE(DPC_WHY_DISPCC_NO_ACCESS);
    VALUE(DPC_WHY_POWER_OFF); VALUE(DPC_WHY_AHB_CLOCK_OFF); VALUE(DPC_WHY_MDP_CLOCK_OFF); VALUE(DPC_WHY_TIMING_OFF);
    VALUE(DPC_WHY_ADDRESS_MISMATCH); VALUE(DPC_WHY_INTF_MISMATCH); VALUE(DPC_WHY_VERSION_MISMATCH);
    VALUE(DPC_WHY_ENABLE_STATUS); VALUE(DPC_WHY_NO_TIMING); VALUE(DPC_WHY_TIMING_INCOHERENT);
    VALUE(DPC_WHY_GEOMETRY_MISMATCH); VALUE(DPC_WHY_ROUTING_MISMATCH); VALUE(DPC_WHY_COUNTER_STALLED);
    VALUE(DPC_WHY_COUNTER_FAST); VALUE(DPC_WHY_COUNT);
    VALUE(DPC_PF_NONE); VALUE(DPC_PF_NULL); VALUE(DPC_PF_ABI); VALUE(DPC_PF_RESERVED); VALUE(DPC_PF_REQUIRED);
    VALUE(DPC_PF_INTF_INDEX); VALUE(DPC_PF_VERSION); VALUE(DPC_PF_MODE); VALUE(DPC_PF_BLOCK); VALUE(DPC_PF_OVERLAP);
    VALUE(DPC_PF_OFFSET); VALUE(DPC_PF_CTL); VALUE(DPC_PF_COUNTER);
    VALUE(DPC_TF_NONE); VALUE(DPC_TF_HTOTAL); VALUE(DPC_TF_PERIOD); VALUE(DPC_TF_VTOTAL); VALUE(DPC_TF_V_WINDOW);
    VALUE(DPC_TF_H_WINDOW); VALUE(DPC_TF_OUTSIDE_FRAME);
    return 0;
}

static int print_names(void)
{
    uint32_t i;

    for (i = 0; i <= DPC_INCONSISTENT + 1; i++)
        printf("verdict %u %s\n", i, dpc_verdict_name(i));
    for (i = 0; i <= DPC_WHY_COUNT + 1; i++)
        printf("why %u %s\n", i, dpc_why_name(i));
    return 0;
}

static int read_exact(void *dst, size_t n) { return n == 0 || fread(dst, 1, n, stdin) == n; }

static int run_batch(void)
{
    uint32_t header[3];

    while (fread(header, sizeof header[0], 3, stdin) == 3) {
        uint32_t plen = header[0], slen = header[1], mode = header[2];
        size_t shift = mode >= 1 && mode <= 7 ? mode : 0;
        struct dpc_params *params = malloc(plen ? plen : 1);
        uint8_t *snap = malloc(slen + shift ? slen + shift : 1);
        struct dpc_result result = {0xdead, 0xdead, 0xdead, 0xdead};
        struct dpc_result *out = mode == BATCH_NULL_OUT ? NULL : &result;
        int rc;

        if (plen != sizeof(struct dpc_params) || !params || !snap || !read_exact(params, plen) ||
            !read_exact(snap + shift, slen)) {
            fprintf(stderr, "bad batch record\n");
            free(params);
            free(snap);
            return 2;
        }
        rc = display_pipeline_check(mode == BATCH_NULL_PARAMS ? NULL : params,
                                    mode == BATCH_NULL_SNAP ? NULL : snap + shift, slen, out);
        if (rc == -1 && !out)
            printf("-1\n");
        else if (rc != 0 || !out)
            printf("bad-return %d\n", rc);
        else
            printf("%u %u %u %u\n", result.verdict, result.why, result.detail, result.evidence);
        free(params);
        free(snap);
    }
    return 0;
}

static int run_writer(void)
{
    uint32_t head[4], i;
    uint8_t buf[DPC_SNAP_HEADER + DPC_SNAP_ENTRY * (DPC_REG_COUNT + DPC_MAX_CTL)];
    size_t len;

    if (fread(head, sizeof head[0], 4, stdin) != 4)
        return 2;
    memset(buf, 0xa5, sizeof buf);
    len = dpc_snapshot_init(buf, sizeof buf, head[0], head[1]);
    printf("len %zu\n", len);
    if (!len)
        return 0;
    if (dpc_snapshot_set_gap(buf, len, head[2]))
        printf("gap failed\n");
    for (i = 0; i < head[3]; i++) {
        uint32_t kind, index, value, status;
        uint64_t pa;
        int rc;

        if (!read_exact(&kind, 4) || !read_exact(&index, 4) || !read_exact(&pa, 8) || !read_exact(&value, 4) ||
            !read_exact(&status, 4))
            return 2;
        rc = kind ? dpc_snapshot_put_ctl(buf, len, index, pa, value, status)
                  : dpc_snapshot_put(buf, len, index, pa, value, status);
        printf("put %u %d\n", i, rc);
    }
    printf("hex ");
    for (i = 0; i < len; i++)
        printf("%02x", buf[i]);
    printf("\n");
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "layout"))
        return print_layout();
    if (argc == 2 && !strcmp(argv[1], "names"))
        return print_names();
    if (argc == 2 && !strcmp(argv[1], "batch"))
        return run_batch();
    if (argc == 2 && !strcmp(argv[1], "writer"))
        return run_writer();
    fprintf(stderr, "usage: %s layout|names|batch|writer\n", argv[0]);
    return 2;
}
