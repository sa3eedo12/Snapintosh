/* SPDX-License-Identifier: MIT */
#include "report.h"

#include "acpi.h"

uint64_t probe_bits(uint64_t v, unsigned hi, unsigned lo)
{
    unsigned width = hi - lo + 1;
    uint64_t mask = width >= 64 ? ~0ull : ((1ull << width) - 1);
    return (v >> lo) & mask;
}

/* ---- Register collection ---- */

void probe_regs_init(probe_regs *r)
{
    probe_memset(r, 0, sizeof *r);
    r->el = PROBE_EL_NOT_READ;
    r->stage = PROBE_STAGE_NONE;
}

static void read_reg(probe_regs *r, int id, probe_reg_reader rd, void *ctx)
{
    r->value[id] = rd(ctx, id);
    r->state[id] = PROBE_REG_READ;
}

static void skip_reg(probe_regs *r, int id, const char *why)
{
    r->state[id] = PROBE_REG_SKIPPED;
    r->skip_reason[id] = why;
}

int probe_collect_el(probe_regs *r, probe_reg_reader rd, void *ctx)
{
    read_reg(r, PSR_CURRENTEL, rd, ctx);
    r->el = probe_currentel_decode(r->value[PSR_CURRENTEL]);
    r->stage = PROBE_STAGE_EL;
    return r->el;
}

int probe_collect_stage1(probe_regs *r, probe_reg_reader rd, void *ctx)
{
    if (r->el != 2 || r->stage != PROBE_STAGE_EL)
        return -1;

    /* These two decide whether TTBR1_EL2 and ICC_SRE_EL2 exist at all. */
    read_reg(r, PSR_ID_AA64MMFR1_EL1, rd, ctx);
    read_reg(r, PSR_ID_AA64PFR0_EL1, rd, ctx);

    read_reg(r, PSR_HCR_EL2, rd, ctx);
    read_reg(r, PSR_SCTLR_EL2, rd, ctx);
    read_reg(r, PSR_TCR_EL2, rd, ctx);
    read_reg(r, PSR_TTBR0_EL2, rd, ctx);
    /* TTBR1_EL2 is defined only with FEAT_VHE (ID_AA64MMFR1_EL1.VH). */
    if (probe_bits(r->value[PSR_ID_AA64MMFR1_EL1], 11, 8) != 0)
        read_reg(r, PSR_TTBR1_EL2, rd, ctx);
    else
        skip_reg(r, PSR_TTBR1_EL2, "VH=0");
    read_reg(r, PSR_MAIR_EL2, rd, ctx);
    read_reg(r, PSR_VBAR_EL2, rd, ctx);
    read_reg(r, PSR_CPTR_EL2, rd, ctx);
    read_reg(r, PSR_MDCR_EL2, rd, ctx);
    read_reg(r, PSR_CNTHCTL_EL2, rd, ctx);
    read_reg(r, PSR_CNTVOFF_EL2, rd, ctx);
    /* ICC_SRE_EL2 exists only with the GICv3 system register interface
     * (ID_AA64PFR0_EL1.GIC); on other cores the access is undefined. */
    if (probe_bits(r->value[PSR_ID_AA64PFR0_EL1], 27, 24) != 0)
        read_reg(r, PSR_ICC_SRE_EL2, rd, ctx);
    else
        skip_reg(r, PSR_ICC_SRE_EL2, "GIC=0");
    read_reg(r, PSR_SPSR_EL2, rd, ctx);
    read_reg(r, PSR_ELR_EL2, rd, ctx);
    read_reg(r, PSR_MPIDR_EL1, rd, ctx);
    read_reg(r, PSR_CTR_EL0, rd, ctx);
    read_reg(r, PSR_CNTFRQ_EL0, rd, ctx);

    r->stage = PROBE_STAGE_1;
    return 0;
}

int probe_collect_stage2a(probe_regs *r, probe_reg_reader rd, void *ctx)
{
    if (r->el != 2 || r->stage != PROBE_STAGE_1)
        return -1;

    read_reg(r, PSR_MIDR_EL1, rd, ctx);
    read_reg(r, PSR_REVIDR_EL1, rd, ctx);
    read_reg(r, PSR_ID_AA64PFR1_EL1, rd, ctx);
    read_reg(r, PSR_ID_AA64MMFR0_EL1, rd, ctx);
    read_reg(r, PSR_ID_AA64MMFR2_EL1, rd, ctx);
    read_reg(r, PSR_ID_AA64ISAR0_EL1, rd, ctx);
    read_reg(r, PSR_ID_AA64ISAR1_EL1, rd, ctx);
    read_reg(r, PSR_ID_AA64DFR0_EL1, rd, ctx);
    read_reg(r, PSR_DCZID_EL0, rd, ctx);

    /* ZFR0 and SMFR0 are read only when the feature that allocates them is
     * advertised; an unallocated ID encoding can fault at EL2 on Oryon-class
     * cores (FEAT_IDST), see probes/idregs.h. */
    if (probe_bits(r->value[PSR_ID_AA64PFR0_EL1], 35, 32) != 0)
        read_reg(r, PSR_ID_AA64ZFR0_EL1, rd, ctx);
    else
        skip_reg(r, PSR_ID_AA64ZFR0_EL1, "SVE=0");
    if (probe_bits(r->value[PSR_ID_AA64PFR1_EL1], 27, 24) != 0)
        read_reg(r, PSR_ID_AA64SMFR0_EL1, rd, ctx);
    else
        skip_reg(r, PSR_ID_AA64SMFR0_EL1, "SME=0");

    r->stage = PROBE_STAGE_2A;
    return 0;
}

/* ---- Names ---- */

static const char *const dump_state_names[PROBE_DUMP_STATE_COUNT] = {
    "NOT ATTEMPTED",      "OK",           "REFUSED NO VOLUME", "REFUSED NOT USB",
    "REFUSED AMBIGUOUS",  "REFUSED MALFORMED PATH", "NO FREE RUN SLOT", "ERROR",
    "PARTIAL",            "DISABLED",
};

const char *probe_dump_state_name(int state)
{
    if (state < 0 || state >= PROBE_DUMP_STATE_COUNT)
        return "UNKNOWN";
    return dump_state_names[state];
}

static const char *const psci_run_names[PROBE_PSCI_RUN_STATE_COUNT] = {
    "NOT ATTEMPTED", "DONE", "NO CONDUIT", "NO TARGET", "NOT AT EL2", "HVC REFUSED",
};

static const char *gop_format_name(uint32_t fmt)
{
    if (fmt == PROBE_PIXEL_BGRX)
        return "BGRX";
    if (fmt == PROBE_PIXEL_RGBX)
        return "RGBX";
    return "?";
}

/* ---- Line construction ---- */

static probe_sb begin_line(probe_report *r)
{
    probe_sb sb;

    if (r->count < PROBE_REPORT_MAX_LINES) {
        probe_sb_init(&sb, r->line[r->count], sizeof r->line[0]);
    } else {
        r->overflowed = 1;
        probe_sb_init(&sb, r->scratch, sizeof r->scratch);
    }
    return sb;
}

static void end_line(probe_report *r, const probe_sb *sb)
{
    if (sb->overflow)
        r->overflowed = 1;
    if (r->count < PROBE_REPORT_MAX_LINES)
        r->count++;
}

static void text_line(probe_report *r, const char *text)
{
    probe_sb sb = begin_line(r);
    probe_sb_puts(&sb, text);
    end_line(r, &sb);
}

static void section(probe_report *r, const char *title)
{
    probe_sb sb = begin_line(r);
    probe_sb_puts(&sb, "== ");
    probe_sb_puts(&sb, title);
    probe_sb_puts(&sb, " ==");
    end_line(r, &sb);
}

static void reg_line(probe_report *r, const probe_regs *g, int id, const char *label)
{
    probe_sb sb = begin_line(r);

    probe_sb_puts(&sb, label);
    probe_sb_putc(&sb, '=');
    switch (g->state[id]) {
    case PROBE_REG_READ:
        probe_sb_hex(&sb, g->value[id], 16);
        break;
    case PROBE_REG_SKIPPED:
        probe_sb_puts(&sb, "SKIPPED ");
        probe_sb_puts(&sb, g->skip_reason[id] ? g->skip_reason[id] : "?");
        break;
    default:
        probe_sb_puts(&sb, "NOT READ");
        break;
    }
    end_line(r, &sb);
}

static int is_read(const probe_regs *g, int id)
{
    return g->state[id] == PROBE_REG_READ;
}

static void flag_line(probe_report *r, const char *prefix, const probe_regs *g, int id,
                      const char *const names[4], const unsigned bits[4])
{
    probe_sb sb = begin_line(r);

    probe_sb_puts(&sb, prefix);
    if (!is_read(g, id)) {
        probe_sb_puts(&sb, " NOT READ");
    } else {
        for (int i = 0; i < 4; i++) {
            probe_sb_putc(&sb, ' ');
            probe_sb_puts(&sb, names[i]);
            probe_sb_putc(&sb, '=');
            probe_sb_dec(&sb, probe_bits(g->value[id], bits[i], bits[i]));
        }
    }
    end_line(r, &sb);
}

typedef struct {
    uint8_t value;
    const char *text;
} probe_decode;

#define DECODE_COUNT(t) (sizeof(t) / sizeof((t)[0]))

static void decode_line(probe_report *r, const probe_regs *g, int id, const char *label,
                        unsigned hi, unsigned lo, const probe_decode *tab, size_t n)
{
    probe_sb sb = begin_line(r);

    probe_sb_puts(&sb, label);
    probe_sb_putc(&sb, '=');
    if (!is_read(g, id)) {
        probe_sb_puts(&sb, "NOT READ");
    } else {
        unsigned v = (unsigned)probe_bits(g->value[id], hi, lo);
        probe_sb_hex(&sb, v, 1);
        /* Values this table does not list stay raw: a guessed meaning for an
         * unfamiliar field value is worse than none. */
        for (size_t i = 0; i < n; i++) {
            if (tab[i].value == v) {
                probe_sb_putc(&sb, ' ');
                probe_sb_puts(&sb, tab[i].text);
                break;
            }
        }
    }
    end_line(r, &sb);
}

/* ---- EL2 sections ---- */

static void build_el2_state(probe_report *r, const probe_regs *g)
{
    static const char *const sctlr_names[4] = {"M", "C", "I", "WXN"};
    static const unsigned sctlr_bits[4] = {0, 2, 12, 19};
    static const char *const hcr_names[4] = {"VM", "TGE", "RW", "E2H"};
    static const unsigned hcr_bits[4] = {0, 27, 31, 34};

    section(r, "EL2 STATE");
    reg_line(r, g, PSR_HCR_EL2, "HCR_EL2");
    reg_line(r, g, PSR_SCTLR_EL2, "SCTLR_EL2");
    reg_line(r, g, PSR_TCR_EL2, "TCR_EL2");
    reg_line(r, g, PSR_TTBR0_EL2, "TTBR0_EL2");
    reg_line(r, g, PSR_TTBR1_EL2, "TTBR1_EL2");
    reg_line(r, g, PSR_MAIR_EL2, "MAIR_EL2");
    reg_line(r, g, PSR_VBAR_EL2, "VBAR_EL2");
    reg_line(r, g, PSR_CPTR_EL2, "CPTR_EL2");
    reg_line(r, g, PSR_MDCR_EL2, "MDCR_EL2");
    reg_line(r, g, PSR_CNTHCTL_EL2, "CNTHCTL_EL2");
    reg_line(r, g, PSR_CNTVOFF_EL2, "CNTVOFF_EL2");
    reg_line(r, g, PSR_ICC_SRE_EL2, "ICC_SRE_EL2");
    reg_line(r, g, PSR_SPSR_EL2, "SPSR_EL2 RAW");
    reg_line(r, g, PSR_ELR_EL2, "ELR_EL2 RAW");
    reg_line(r, g, PSR_MPIDR_EL1, "MPIDR_EL1");
    /* SPSR_EL2 and ELR_EL2 hold the state saved by the last exception taken
     * to EL2; with none taken they are whatever the firmware left. */
    text_line(r, "NOTE SPSR/ELR RAW MAY BE STALE");
    flag_line(r, "MMU", g, PSR_SCTLR_EL2, sctlr_names, sctlr_bits);
    flag_line(r, "HCR", g, PSR_HCR_EL2, hcr_names, hcr_bits);
}

static const probe_decode d_parange[] = {{0, "32 BIT"}, {1, "36 BIT"}, {2, "40 BIT"}, {3, "42 BIT"},
                                         {4, "44 BIT"}, {5, "48 BIT"}, {6, "52 BIT"}};
static const probe_decode d_tgran4[] = {{0, "YES"}, {1, "LPA2"}, {0xF, "NO"}};
static const probe_decode d_tgran16[] = {{0, "NO"}, {1, "YES"}, {2, "LPA2"}};
static const probe_decode d_tgran64[] = {{0, "YES"}, {0xF, "NO"}};
static const probe_decode d_vmidbits[] = {{0, "8 BIT"}, {2, "16 BIT"}};
static const probe_decode d_vh[] = {{0, "NO"}, {1, "YES"}};
static const probe_decode d_nv[] = {{0, "NO"}, {1, "NV"}, {2, "NV2"}};
static const probe_decode d_ecv[] = {{0, "NO"}, {1, "YES"}, {2, "CNTPOFF"}};
static const probe_decode d_fgt[] = {{0, "NO"}, {1, "YES"}};
static const probe_decode d_amu[] = {{0, "NO"}, {1, "V1"}, {2, "V1P1"}};
static const probe_decode d_gic[] = {{0, "NO"}, {1, "SYSREG V3/V4"}, {3, "SYSREG V4.1"}};
static const probe_decode d_sve[] = {{0, "NO"}, {1, "YES"}};
static const probe_decode d_sme[] = {{0, "NO"}, {1, "YES"}, {2, "SME2"}};

static void pair_line(probe_report *r, const probe_regs *g, int id, const char *l1, unsigned hi1,
                      unsigned lo1, const char *l2, unsigned hi2, unsigned lo2)
{
    probe_sb sb = begin_line(r);

    probe_sb_puts(&sb, "PAUTH ");
    if (!is_read(g, id)) {
        probe_sb_puts(&sb, l1);
        probe_sb_puts(&sb, "/");
        probe_sb_puts(&sb, l2);
        probe_sb_puts(&sb, "=NOT READ");
    } else {
        probe_sb_puts(&sb, l1);
        probe_sb_putc(&sb, '=');
        probe_sb_hex(&sb, probe_bits(g->value[id], hi1, lo1), 1);
        probe_sb_putc(&sb, ' ');
        probe_sb_puts(&sb, l2);
        probe_sb_putc(&sb, '=');
        probe_sb_hex(&sb, probe_bits(g->value[id], hi2, lo2), 1);
    }
    end_line(r, &sb);
}

static void build_decoded(probe_report *r, const probe_regs *g)
{
    section(r, "DECODED (OBSERVED)");
    decode_line(r, g, PSR_ID_AA64MMFR0_EL1, "PARANGE", 3, 0, d_parange, DECODE_COUNT(d_parange));
    decode_line(r, g, PSR_ID_AA64MMFR0_EL1, "TGRAN4", 31, 28, d_tgran4, DECODE_COUNT(d_tgran4));
    decode_line(r, g, PSR_ID_AA64MMFR0_EL1, "TGRAN16", 23, 20, d_tgran16, DECODE_COUNT(d_tgran16));
    decode_line(r, g, PSR_ID_AA64MMFR0_EL1, "TGRAN64", 27, 24, d_tgran64, DECODE_COUNT(d_tgran64));
    decode_line(r, g, PSR_ID_AA64MMFR1_EL1, "VMIDBITS", 7, 4, d_vmidbits, DECODE_COUNT(d_vmidbits));
    decode_line(r, g, PSR_ID_AA64MMFR1_EL1, "VH", 11, 8, d_vh, DECODE_COUNT(d_vh));
    decode_line(r, g, PSR_ID_AA64MMFR2_EL1, "NV", 27, 24, d_nv, DECODE_COUNT(d_nv));
    decode_line(r, g, PSR_ID_AA64MMFR0_EL1, "ECV", 63, 60, d_ecv, DECODE_COUNT(d_ecv));
    decode_line(r, g, PSR_ID_AA64MMFR0_EL1, "FGT", 59, 56, d_fgt, DECODE_COUNT(d_fgt));
    decode_line(r, g, PSR_ID_AA64PFR0_EL1, "AMU", 47, 44, d_amu, DECODE_COUNT(d_amu));
    decode_line(r, g, PSR_ID_AA64PFR0_EL1, "GIC", 27, 24, d_gic, DECODE_COUNT(d_gic));
    decode_line(r, g, PSR_ID_AA64PFR0_EL1, "SVE", 35, 32, d_sve, DECODE_COUNT(d_sve));
    decode_line(r, g, PSR_ID_AA64PFR1_EL1, "SME", 27, 24, d_sme, DECODE_COUNT(d_sme));
    pair_line(r, g, PSR_ID_AA64ISAR1_EL1, "APA", 7, 4, "API", 11, 8);
    pair_line(r, g, PSR_ID_AA64ISAR1_EL1, "GPA", 27, 24, "GPI", 31, 28);
}

static void build_raw_ids(probe_report *r, const probe_regs *g)
{
    static const int ids[] = {
        PSR_MIDR_EL1,        PSR_REVIDR_EL1,      PSR_ID_AA64PFR0_EL1,  PSR_ID_AA64PFR1_EL1,
        PSR_ID_AA64ZFR0_EL1, PSR_ID_AA64SMFR0_EL1, PSR_ID_AA64DFR0_EL1, PSR_ID_AA64ISAR0_EL1,
        PSR_ID_AA64ISAR1_EL1, PSR_ID_AA64MMFR0_EL1, PSR_ID_AA64MMFR1_EL1, PSR_ID_AA64MMFR2_EL1,
        PSR_DCZID_EL0,       PSR_CTR_EL0,         PSR_CNTFRQ_EL0,
    };

    section(r, "RAW ID REGISTERS");
    for (size_t i = 0; i < sizeof ids / sizeof ids[0]; i++)
        reg_line(r, g, ids[i], probe_sysreg_name(ids[i]));
    /* NV_frac, E2H0 and the QARMA3 field live in the registers named here; they
     * are read only by the guarded tests image. */
    text_line(r, "NOT READ HERE: MMFR3 MMFR4 ISAR2 DFR1");
}

static void build_not_el2(probe_report *r, const probe_regs *g)
{
    probe_sb sb;

    text_line(r, "NOT AT EL2");
    sb = begin_line(r);
    probe_sb_puts(&sb, "CURRENTEL RAW=");
    probe_sb_hex(&sb, g->value[PSR_CURRENTEL], 16);
    probe_sb_puts(&sb, " EL=");
    if (g->el >= 0)
        probe_sb_dec(&sb, (uint64_t)g->el);
    else
        probe_sb_putc(&sb, '?');
    end_line(r, &sb);
    text_line(r, "NO OTHER REGISTERS READ");
}

/* ---- Pre-EBS facts ---- */

static void yn(probe_sb *sb, const char *name, int v)
{
    probe_sb_puts(sb, name);
    probe_sb_putc(sb, '=');
    probe_sb_putc(sb, v ? 'Y' : 'N');
}

static void build_facts(probe_report *r, const probe_facts *f)
{
    probe_sb sb;

    section(r, "PRE-EBS FACTS");

    sb = begin_line(r);
    probe_sb_puts(&sb, "PREEBS EL=");
    if (f->have_preebs_el) {
        int el = probe_currentel_decode(f->preebs_el_raw);
        if (el >= 0)
            probe_sb_dec(&sb, (uint64_t)el);
        else
            probe_sb_putc(&sb, '?');
        probe_sb_puts(&sb, " RAW=");
        probe_sb_hex(&sb, f->preebs_el_raw, 16);
    } else {
        probe_sb_puts(&sb, "? RAW=NONE");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "FW REV=");
    probe_sb_hex(&sb, f->fw_revision, 8);
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "FW VENDOR=");
    probe_sb_puts(&sb, f->fw_vendor);
    end_line(r, &sb);

    sb = begin_line(r);
    if (f->have_gop) {
        probe_sb_puts(&sb, "GOP ");
        probe_sb_dec(&sb, f->gop_width);
        probe_sb_putc(&sb, 'X');
        probe_sb_dec(&sb, f->gop_height);
        probe_sb_puts(&sb, " SEL=");
        probe_sb_puts(&sb, probe_gop_rule_name(f->gop_rule));
    } else {
        probe_sb_puts(&sb, "GOP NONE");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "GOP STRIDE=");
    if (f->have_gop) {
        probe_sb_dec(&sb, f->gop_stride_pixels);
        probe_sb_puts(&sb, " FMT=");
        probe_sb_puts(&sb, gop_format_name(f->gop_format));
    } else {
        probe_sb_puts(&sb, "NONE");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "GOP BASE=");
    if (f->have_gop)
        probe_sb_hex(&sb, f->gop_base, 16);
    else
        probe_sb_puts(&sb, "NONE");
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "GOP SIZE=");
    if (f->have_gop)
        probe_sb_hex(&sb, f->gop_size, 16);
    else
        probe_sb_puts(&sb, "NONE");
    end_line(r, &sb);

    sb = begin_line(r);
    if (f->have_layout) {
        probe_sb_puts(&sb, "LAYOUT S=");
        probe_sb_dec(&sb, f->layout_scale);
        probe_sb_puts(&sb, " C=");
        probe_sb_dec(&sb, f->layout_cols);
        probe_sb_puts(&sb, " R=");
        probe_sb_dec(&sb, f->layout_rows);
        probe_sb_puts(&sb, " P=");
        probe_sb_dec(&sb, f->layout_pages);
    } else {
        probe_sb_puts(&sb, "LAYOUT NONE");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "CFG N=");
    probe_sb_dec(&sb, f->cfg_count);
    probe_sb_putc(&sb, ' ');
    yn(&sb, "ACPI20", f->cfg_acpi20);
    probe_sb_putc(&sb, ' ');
    yn(&sb, "ACPI10", f->cfg_acpi10);
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "CFG ");
    yn(&sb, "SMBIOS", f->cfg_smbios);
    probe_sb_putc(&sb, ' ');
    yn(&sb, "SMBIOS3", f->cfg_smbios3);
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "ACPI RSDP ");
    if (f->rsdp_status != PROBE_ACPI_OK) {
        probe_sb_puts(&sb, probe_acpi_status_name(f->rsdp_status));
    } else {
        probe_sb_puts(&sb, "REV=");
        probe_sb_dec(&sb, f->rsdp_revision);
        if (f->root_status == PROBE_ACPI_OK) {
            probe_sb_puts(&sb, f->root_is_xsdt ? " ROOT=XSDT" : " ROOT=RSDT");
        } else {
            probe_sb_puts(&sb, " ROOT ");
            probe_sb_puts(&sb, probe_acpi_status_name(f->root_status));
        }
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "ACPI OK=");
    probe_sb_dec(&sb, f->acpi_ok);
    probe_sb_puts(&sb, " REJ=");
    probe_sb_dec(&sb, f->acpi_rejected);
    probe_sb_puts(&sb, " DUP=");
    probe_sb_dec(&sb, f->acpi_dup);
    end_line(r, &sb);

    sb = begin_line(r);
    if (f->fadt_valid) {
        probe_sb_puts(&sb, "FADT REV=");
        probe_sb_dec(&sb, f->fadt_revision);
        probe_sb_puts(&sb, " MINOR=");
        probe_sb_dec(&sb, f->fadt_minor);
        probe_sb_puts(&sb, " ARMBOOT=");
        if (f->has_arm_boot_arch)
            probe_sb_hex(&sb, f->arm_boot_arch, 4);
        else
            probe_sb_puts(&sb, "NONE");
    } else {
        probe_sb_puts(&sb, "FADT NOT PARSED");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "PSCI CONDUIT=");
    probe_sb_puts(&sb, probe_psci_conduit_name((enum probe_psci_conduit)f->psci_conduit));
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "SMBIOS ");
    if (f->smbios_status == PROBE_SMBIOS_OK) {
        probe_sb_putc(&sb, 'V');
        probe_sb_dec(&sb, f->smbios_major);
        probe_sb_putc(&sb, '.');
        probe_sb_dec(&sb, f->smbios_minor);
        probe_sb_puts(&sb, " LEN=");
        probe_sb_dec(&sb, f->smbios_table_len);
    } else {
        probe_sb_puts(&sb, probe_smbios_status_name(f->smbios_status));
    }
    end_line(r, &sb);

    sb = begin_line(r);
    if (f->memmap_ok) {
        probe_sb_puts(&sb, "MEMMAP N=");
        probe_sb_dec(&sb, f->memmap_count);
        probe_sb_puts(&sb, " DESCSZ=");
        probe_sb_dec(&sb, f->memmap_desc_size);
    } else {
        probe_sb_puts(&sb, "MEMMAP INVALID");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "RAM ");
    if (f->have_ram) {
        probe_sb_hex(&sb, f->ram_lo, 16);
        probe_sb_putc(&sb, '-');
        probe_sb_hex(&sb, f->ram_hi, 16);
    } else {
        probe_sb_puts(&sb, "NONE");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "DUMP STATE=");
    probe_sb_puts(&sb, probe_dump_state_name(f->dump_state));
    end_line(r, &sb);

    sb = begin_line(r);
    if (f->dump_run != 0) {
        probe_sb_puts(&sb, "DUMP RUN=");
        if (f->dump_run < 10)
            probe_sb_putc(&sb, '0');
        probe_sb_dec(&sb, f->dump_run);
        probe_sb_puts(&sb, " FILES=");
        probe_sb_dec(&sb, f->dump_files);
        probe_sb_puts(&sb, " BYTES=");
        probe_sb_dec(&sb, f->dump_bytes);
    } else {
        probe_sb_puts(&sb, "DUMP RUN=NONE");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, f->variant_psci ? "VARIANT=PSCI" : "VARIANT=DEFAULT");
    probe_sb_puts(&sb, f->reset_enabled ? " RESET=ON" : " RESET=OFF");
    probe_sb_puts(&sb, f->psci.enabled ? " PSCI=ON" : " PSCI=OFF");
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "EBS ATTEMPTS=");
    probe_sb_dec(&sb, f->ebs_attempts);
    end_line(r, &sb);
}

/* ---- PSCI section (opt-in variant only) ---- */

static const char *psci_ret_name(uint32_t ret)
{
    /* Return codes of the Arm PSCI specification (DEN0022). */
    switch ((int32_t)ret) {
    case 0:
        return "SUCCESS";
    case -1:
        return "NOT SUPPORTED";
    case -2:
        return "INVALID PARAMS";
    case -3:
        return "DENIED";
    case -4:
        return "ALREADY ON";
    case -5:
        return "ON PENDING";
    case -6:
        return "INTERNAL FAILURE";
    case -7:
        return "NOT PRESENT";
    case -8:
        return "DISABLED";
    case -9:
        return "INVALID ADDRESS";
    default:
        return "UNKNOWN";
    }
}

static void build_psci(probe_report *r, const probe_psci_facts *p)
{
    probe_sb sb;

    section(r, "PSCI (OPT-IN VARIANT)");

    sb = begin_line(r);
    probe_sb_puts(&sb, "PSCI RUN=");
    if (p->state >= 0 && p->state < PROBE_PSCI_RUN_STATE_COUNT)
        probe_sb_puts(&sb, psci_run_names[p->state]);
    else
        probe_sb_puts(&sb, "UNKNOWN");
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "PSCI_VERSION=");
    if (p->version_called)
        probe_sb_hex(&sb, p->version, 8);
    else
        probe_sb_puts(&sb, "NOT CALLED");
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "CPU_ON TARGET=");
    if (p->have_target)
        probe_sb_hex(&sb, p->target_mpidr, 16);
    else
        probe_sb_puts(&sb, "NONE");
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "CPU_ON RET=");
    if (p->cpu_on_called) {
        probe_sb_hex(&sb, p->cpu_on_ret, 8);
        probe_sb_putc(&sb, ' ');
        probe_sb_puts(&sb, psci_ret_name(p->cpu_on_ret));
    } else {
        probe_sb_puts(&sb, "NOT CALLED");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "SECONDARY ARRIVED=");
    if (p->cpu_on_called)
        probe_sb_puts(&sb, p->arrived ? "YES" : "NO");
    else
        probe_sb_puts(&sb, "NOT CALLED");
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "SECONDARY EL=");
    if (p->arrived) {
        int el = probe_currentel_decode(p->arrived_el_raw);
        if (el >= 0)
            probe_sb_dec(&sb, (uint64_t)el);
        else
            probe_sb_putc(&sb, '?');
        probe_sb_puts(&sb, " RAW=");
        probe_sb_hex(&sb, p->arrived_el_raw, 16);
    } else {
        probe_sb_puts(&sb, "NONE");
    }
    end_line(r, &sb);

    sb = begin_line(r);
    probe_sb_puts(&sb, "SECONDARY MPIDR=");
    if (p->arrived)
        probe_sb_hex(&sb, p->arrived_mpidr, 16);
    else
        probe_sb_puts(&sb, "NONE");
    end_line(r, &sb);
}

/* ---- Public builders ---- */

void probe_report_build(probe_report *r, const probe_facts *f, const probe_regs *regs)
{
    probe_memset(r, 0, sizeof *r);

    /* The EL2 layout is used unless CurrentEL was actually read and was not
     * EL2, so that a preview built before any register is read has the same
     * line count as the final EL2 report. */
    if (regs->state[PSR_CURRENTEL] == PROBE_REG_READ && regs->el != 2) {
        build_not_el2(r, regs);
    } else {
        build_el2_state(r, regs);
        build_decoded(r, regs);
        build_raw_ids(r, regs);
    }
    build_facts(r, f);
    if (f->psci.enabled)
        build_psci(r, &f->psci);
}

void probe_report_header0(probe_payload out, const probe_facts *f)
{
    probe_sb sb;

    probe_sb_init(&sb, out, sizeof(probe_payload));
    probe_sb_puts(&sb, f->variant_psci ? "SP11 EL2 PROBE+PSCI B=" : "SP11 EL2 PROBE B=");
    probe_sb_puts(&sb, f->build_id);
}

void probe_report_header1(probe_payload out, const probe_regs *regs, unsigned page,
                          unsigned pages)
{
    probe_sb sb;

    probe_sb_init(&sb, out, sizeof(probe_payload));
    probe_sb_puts(&sb, "CURRENTEL=");
    if (regs->el >= 0)
        probe_sb_dec(&sb, (uint64_t)regs->el);
    else
        probe_sb_putc(&sb, '?');
    probe_sb_puts(&sb, " PAGE ");
    probe_sb_dec(&sb, page);
    probe_sb_putc(&sb, '/');
    probe_sb_dec(&sb, pages);
}

void probe_report_footer(probe_payload out, int kind, unsigned seconds)
{
    probe_sb sb;

    probe_sb_init(&sb, out, sizeof(probe_payload));
    switch (kind) {
    case PROBE_FOOTER_STAGE0:
        probe_sb_puts(&sb, "STAGE 0 DONE - READING 1 NOW");
        break;
    case PROBE_FOOTER_STAGE1:
        probe_sb_puts(&sb, "STAGE 1 DONE - READING 2A NOW");
        break;
    case PROBE_FOOTER_STAGE2A:
        probe_sb_puts(&sb, "ALL STAGES DONE");
        break;
    case PROBE_FOOTER_PSCI:
        probe_sb_puts(&sb, "PSCI CALL IN PROGRESS");
        break;
    case PROBE_FOOTER_COUNTDOWN:
        probe_sb_puts(&sb, "RESET IN ");
        probe_sb_dec(&sb, seconds);
        probe_sb_puts(&sb, " S");
        break;
    case PROBE_FOOTER_HOLD:
        probe_sb_puts(&sb, "RESET DISABLED - HOLDING");
        break;
    case PROBE_FOOTER_RESETTING:
        probe_sb_puts(&sb, "RESETTING NOW");
        break;
    case PROBE_FOOTER_RESET_FAILED:
        probe_sb_puts(&sb, "RESETSYSTEM DID NOT RESET");
        break;
    case PROBE_FOOTER_HALTED:
        probe_sb_puts(&sb, "HALTED - NO RESET");
        break;
    default:
        probe_sb_puts(&sb, "FOOTER ?");
        break;
    }
}

void probe_sanitize_utf16(char *out, size_t cap, const uint16_t *s, size_t max_units)
{
    size_t n = 0;
    size_t limit = cap ? cap - 1 : 0;
    int pending_space = 0;

    if (limit > PROBE_VENDOR_MAX)
        limit = PROBE_VENDOR_MAX;
    if (!cap)
        return;
    for (size_t i = 0; s && i < max_units && s[i] != 0 && n < limit; i++) {
        unsigned c = s[i];

        if (c >= 'a' && c <= 'z')
            c -= 'a' - 'A';
        /* The on-screen font has glyphs only for 0x20..0x5F. Everything else,
         * including '|' which separates the CRC, is shown as '?'. */
        if (c < 0x20 || c > 0x5F)
            c = '?';
        if (c == ' ') {
            pending_space = n != 0;
            continue;
        }
        if (pending_space) {
            if (n + 2 > limit)
                break;
            out[n++] = ' ';
        }
        pending_space = 0;
        out[n++] = (char)c;
    }
    out[n] = 0;
}
