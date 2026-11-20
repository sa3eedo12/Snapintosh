/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_BUNDLE_H
#define SNAP_PROBES_SP11_STICK_BUNDLE_H
/* Surface Pro 11 stick bundle: the interface shared by the host bundle builder (tools/sp11_stick_bundle.py) and the on-device
 * wrapper and reporter (docs/SP11-MONITOR-PORT.md, work items 3 and 4); docs/SP11-STICK-BUNDLE.md is the note, this header the
 * layout. It has no gate: it declares a contract and no existing build includes it.
 *
 * Five files for q1n1's stick runner: SNAPMON.BIN and SNAPREP.BIN are payloads; GUEST.DAT (the chunks xnuboot.build() makes),
 * PIN.DAT (pinned facts) and REPORT.DAT (a zero file that becomes the report buffer) are data, which the runner hashes before
 * the bounce and never again at EL2. So the wrapper checks what it uses, and every check below is a pure function of bytes the
 * caller holds: no libc, no allocation, no writable globals, bounded loops, every length checked before the bytes behind it are
 * read, unsigned arithmetic that cannot wrap unnoticed. A check returns 0 or one SP11SB_E_* code; the first failing check wins,
 * in the order of sp11sb_preflight(), so a record that is wrong twice gets the same code from the host tool and the wrapper.
 *
 * Every record is little-endian, fixed width, without padding, reserved bytes zero, strings NUL padded. An unknown version,
 * flag or kind and a nonzero reserved byte are refusals, never ignored (fail closed). The hash is SHA-256, supplied by the
 * caller (q1n1's platform/uefi/sha256.c on the unit): this header carries no second implementation.
 *
 * Evidence: EXPERIMENTAL. Host-tested only (tools/test-sp11-stick-bundle.py); nothing here has run on hardware. */
#include <stddef.h>
#include <stdint.h>

#if !defined(__BYTE_ORDER__) || __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "the report head is written through a structure and the records are little-endian"
#endif

/* One version for the whole interface: three records, the error table, the check order and every constant below. Any change
 * to any of them bumps it; readers refuse every other value and nothing converts between versions. */
#define SP11SB_VERSION 1u

/* ---- What the runner promises (q1n1 platform/uefi/sp11-payload*.h; the test compares each value with the runner's headers). */
#define SP11SB_RUNNER_MAGIC UINT64_C(0x31594150314e3151)    /* "Q1N1PAY1", byte 0 first */
#define SP11SB_RUNNER_VERSION 1u
#define SP11SB_RUNNER_ARGS_BYTES 192u       /* sizeof(struct q1n1_payload_args), ABI v1 */
#define SP11SB_FILES 5u                     /* the runner's maximum; the bundle uses every slot */
#if SNAP_SP11_STICK_MONITOR
/* The root carry (docs/SP11-STICK-ROOT.md), an opt-in of the monitor entry build (SNAP_SP11_STICK_MONITOR=1) and of no other: the wrapper is inside the SNAPMON.BIN that a record pins,
 * so its limit is part of that digest, and the 256 MiB of every pair that exists stays the default. With SNAP_SP11_STICK_ROOT_CARRY=1 a file may be 2 GiB, which the runner delivers
 * only with q1n1's own opt-in Q1N1_SP11_BIGFILE (8 MiB chunk cap, 256 operations). File sizes, offsets and destinations are uint64_t everywhere in this interface, so the larger
 * limit changes no width. tools/sp11_stick_wrapper_build.py --root-carry is the one place that sets it (and refuses it without --monitor). */
#ifndef SNAP_SP11_STICK_ROOT_CARRY
#define SNAP_SP11_STICK_ROOT_CARRY 0
#endif
#if SNAP_SP11_STICK_ROOT_CARRY != 0 && SNAP_SP11_STICK_ROOT_CARRY != 1
#error "SNAP_SP11_STICK_ROOT_CARRY accepts only 0 and 1"
#endif
#if SNAP_SP11_STICK_ROOT_CARRY
#define SP11SB_FILE_MAX UINT64_C(0x80000000)    /* 2 GiB per file */
#else
#define SP11SB_FILE_MAX UINT64_C(0x10000000)    /* 256 MiB per file */
#endif
#else
#define SP11SB_FILE_MAX UINT64_C(0x10000000)    /* 256 MiB per file */
#endif
#define SP11SB_PATH_MAX 256u                /* char path[256] in the preload table */
#define SP11SB_LINE_MAX 100u
#define SP11SB_REPORTER_LINES 40u           /* the runner's budget per payload */
#define SP11SB_WRAPPER_LINES 30u            /* 6 runner lines + 2 * 3 per payload + 30 + 40 = 82, inside the unit's 89 rows */

/* ---- Memory rules (docs/SP11-STICK-BUNDLE.md section 5). */
#define SP11SB_MIB UINT64_C(0x100000)
#define SP11SB_IPA_LIMIT (UINT64_C(1) << 36)            /* stage 2 is 36-bit (probes/xnuboot/stage2.c) */
#define SP11SB_GUEST_ALIGN (32 * SP11SB_MIB)            /* xnuboot.REGION_ALIGN: XNU rounds DRAM to 32 MiB */
#define SP11SB_GUEST_MIN (192 * SP11SB_MIB)             /* SPTM data ends at 0xA700000 in xnuboot.py's layout */
#define SP11SB_RESERVE_ALIGN (16 * SP11SB_MIB)
#define SP11SB_RESERVE_MIN (48 * SP11SB_MIB)            /* xnuboot.bootstrap_reserve()'s floor */
#define SP11SB_WORK_ALIGN UINT64_C(0x10000)
#define SP11SB_WORK_BYTES (240 * SP11SB_MIB)            /* probes/monitor_work.py WORK_SIZE */
#define SP11SB_EFI_CONVENTIONAL 7u
#define SP11SB_EFI_WB 8u                                /* EFI_MEMORY_WB */
#define SP11SB_MAP_MIN_STRIDE 40u                       /* type, pad, physical, virtual, pages, attribute */
#define SP11SB_MAP_MAX 1024u                            /* descriptors read; slbounce's sweep already stops at 512 */
#define SP11SB_DECLARED_MAX 24u

/* ---- Record sizes and capacities. */
#define SP11SB_PIN_BYTES 1024u
#define SP11SB_MIDR_MAX 4u
#define SP11SB_AVOID_MAX 4u
#define SP11SB_ACPI_MAX 8u
#define SP11SB_GICR_MAX 32u
#define SP11SB_GUEST_HEAD_BYTES 128u
#define SP11SB_CHUNK_BYTES 64u
#define SP11SB_CHUNKS_MAX 1024u             /* a 64 KiB table; xnuboot.build() makes a few hundred */
#define SP11SB_DEST_ALIGN 16u               /* chunk destination and size: one ldp/stp */
#define SP11SB_DATA_ALIGN 64u               /* chunk data inside the file: one cache line */
#define SP11SB_REPORT_HEAD_PAGE 4096u
#define SP11SB_MONITOR_OUT_BYTES 0x80000u   /* run.py OUT_HEADROOM: the most the A16 layout leaves a monitor report */
#define SP11SB_REPORT_BYTES (SP11SB_REPORT_HEAD_PAGE + SP11SB_MONITOR_OUT_BYTES)
#define SP11SB_NAME_MAX 24u                 /* a refusal name: capitals, digits and _ only (the panel font) */
#define SP11SB_RET_REFUSED 0x1000u          /* wrapper return value is this plus the refusal code, 0 when it ran to the end */

#define SP11SB_PIN_MAGIC "SNAPBPIN"
#define SP11SB_GUEST_MAGIC "SNAPBGST"
#define SP11SB_REPORT_MAGIC "SNAPBRPT"

/* Manifest order = role. The runner calls payloads in manifest order and never calls data, so the wrapper runs first. */
enum sp11sb_role { SP11SB_ROLE_MONITOR = 0, SP11SB_ROLE_REPORTER = 1, SP11SB_ROLE_GUEST = 2, SP11SB_ROLE_PIN = 3,
                   SP11SB_ROLE_REPORT = 4 };

/* Chunk kinds. They name what the bytes are for accounting and for the uniqueness rules below; the wrapper copies every
 * kind the same way. PARAMS is what x3 points at and DEVICE_TREE what the guest is handed, so each exists exactly once. */
enum sp11sb_kind { SP11SB_KIND_KERNEL = 1, SP11SB_KIND_TABLES = 2, SP11SB_KIND_HANDOFF = 3, SP11SB_KIND_DEVICE_TREE = 4,
                   SP11SB_KIND_PARAMS = 5, SP11SB_KIND_MONITOR_IMAGE = 6, SP11SB_KIND_MONITOR_DATA = 7,
                   SP11SB_KIND_OTHER = 8, SP11SB_KIND_MAX = 8 };
#define SP11SB_CHUNK_ZERO 1u                /* no file bytes: the wrapper clears the destination. The only flag of version 1 */

/* Store classes the operator authorised for this bundle (decisions D4 and D7). The wrapper refuses a class it was built to
 * use and the bundle does not list. RAM and EL2 system registers are what every run needs; the GIC is for the GIC-timer stop. */
#define SP11SB_STORE_RAM 1u
#define SP11SB_STORE_EL2_SYSREG 2u
#define SP11SB_STORE_GIC 4u
#define SP11SB_STORE_REQUIRED (SP11SB_STORE_RAM | SP11SB_STORE_EL2_SYSREG)
#define SP11SB_STORE_ALL (SP11SB_STORE_REQUIRED | SP11SB_STORE_GIC)

/* Digest work the wrapper chooses to do beyond the table digest, which is always checked (throughput of 115 MiB is U6). */
#define SP11SB_HASH_CHUNKS 1u
#define SP11SB_HASH_FILE 2u

enum sp11sb_avoid_tag { SP11SB_AVOID_EL2_UNREACHABLE = 1, SP11SB_AVOID_DEVICE_OWNED = 2, SP11SB_AVOID_OPERATOR = 3 };

/* The wrapper's progress, written to the report head. The value names the last point reached, except COPYING and
 * MONITOR_ENTERED, which are written just before the step they name. A refusal raised while the stage is S is the check
 * that follows S. Nothing outside the report buffer is stored to before COPYING, so "refused with stage below COPYING"
 * means no RAM beyond REPORT.DAT was written. */
#define SP11SB_STAGES(X) \
    X(NONE, 0) X(ENTERED, 1) X(ARGS_OK, 2) X(FILES_OK, 3) X(PIN_OK, 4) X(OBSERVED_OK, 5) X(PLACED_OK, 6) X(GUEST_OK, 7) \
    X(COPYING, 8) X(COPIED, 9) X(PREPARED, 10) X(MONITOR_ENTERED, 11) X(MONITOR_RETURNED, 12) X(DONE, 13)
enum sp11sb_stage {
#define X(name, value) SP11SB_STAGE_##name = value,
    SP11SB_STAGES(X)
#undef X
#define X(name, value) + 1u
    SP11SB_STAGE_COUNT = 0u SP11SB_STAGES(X)
#undef X
};

/* Refusal codes: X(NAME, number, sides). The number never changes meaning inside a version and the name is what the panel
 * prints (PAYLOAD REFUSED=<NAME> is the runner's form). Sides: 1 the host builder, 2 the wrapper, 4 the reporter. Codes
 * 80 to 82 are raised by the wrapper's runtime steps only; no pure check here returns them. */
#define SP11SB_SIDE_B 1u
#define SP11SB_SIDE_W 2u
#define SP11SB_SIDE_R 4u
#define SP11SB_ERRORS(X) \
    X(ARGS_BAD, 1, 6) X(FILE_COUNT, 2, 7) X(FILE_ROLE, 3, 7) X(FILE_BASE, 4, 6) X(FILE_SIZE, 5, 7) \
    X(PIN_TRUNCATED, 10, 7) X(PIN_MAGIC, 11, 7) X(PIN_VERSION, 12, 7) X(PIN_SIZE, 13, 7) X(PIN_DIGEST, 14, 7) \
    X(PIN_FLAGS, 15, 7) X(PIN_STRING, 16, 7) X(PIN_COUNT, 17, 7) X(PIN_RESERVED, 18, 7) X(PIN_ORDER, 19, 7) \
    X(PIN_VALUE, 20, 7) X(NO_AUTHORISATION, 21, 7) X(STORE_NOT_AUTH, 22, 7) \
    X(PLACE_ALIGN, 24, 7) X(PLACE_LAYOUT, 25, 7) X(PLACE_LIMIT, 26, 7) \
    X(NOT_EL2, 30, 2) X(E2H_SET, 31, 2) X(EL2_REGIME, 32, 2) X(MIDR_MISMATCH, 33, 2) X(MPIDR_MISMATCH, 34, 2) \
    X(CNTFRQ_MISMATCH, 35, 2) X(GIC_MISMATCH, 36, 2) X(GOP_MISMATCH, 37, 2) \
    X(MAP_BAD, 40, 3) X(PLACE_NOT_CONV, 41, 3) X(PLACE_ATTR, 42, 3) X(PLACE_DECLARED, 43, 3) X(PLACE_FB, 44, 3) \
    X(PLACE_AVOID, 45, 3) \
    X(GUEST_TRUNCATED, 50, 3) X(GUEST_MAGIC, 51, 3) X(GUEST_VERSION, 52, 3) X(GUEST_FLAGS, 53, 3) X(GUEST_SIZE, 54, 3) \
    X(GUEST_RESERVED, 55, 3) X(GUEST_COUNT, 56, 3) X(GUEST_LOAD, 57, 3) X(GUEST_PIN, 58, 3) X(CHUNK_KIND, 59, 3) \
    X(CHUNK_FLAGS, 60, 3) X(CHUNK_RESERVED, 61, 3) X(CHUNK_ZERO, 62, 3) X(CHUNK_GEOMETRY, 63, 3) X(CHUNK_RANGE, 64, 3) \
    X(CHUNK_ORDER, 65, 3) X(CHUNK_OVERLAP, 66, 3) X(CHUNK_EOF, 67, 3) X(CHUNK_OFFSET, 68, 3) X(PAD_NONZERO, 69, 3) \
    X(CHUNK_DUP, 70, 3) X(CHUNK_MISSING, 71, 3) \
    X(HASH_POLICY, 72, 3) X(INDEX_DIGEST, 73, 3) X(CHUNK_DIGEST, 74, 3) X(FILE_DIGEST, 75, 3) \
    X(PLACE_UNREADABLE, 80, 2) X(COPY_VERIFY, 81, 2) X(GIC_NOT_READY, 82, 2) \
    X(REPORT_SIZE, 90, 4) X(REPORT_MAGIC, 91, 4) X(REPORT_VERSION, 92, 4) X(REPORT_RESERVED, 93, 4) X(REPORT_VALUE, 94, 4)
enum sp11sb_error {
    SP11SB_E_OK = 0,
#define X(name, code, sides) SP11SB_E_##name = code,
    SP11SB_ERRORS(X)
#undef X
#define X(name, code, sides) + 1u
    SP11SB_ERROR_ROWS = 1u SP11SB_ERRORS(X)     /* the OK row and one per code */
#undef X
};

/* ---- PIN.DAT: the facts a bundle was built for and the placement it pins, 1 KiB. Every 8-byte member is 8-aligned, so the
 * compiler inserts no padding (asserted at each group start and at the end); counts and capacities are fixed, so the size never
 * depends on content; unused slots and the tail are zero by rule; and the digest is last, so one SHA-256 over the first 992
 * bytes covers every other byte with no zeroed-field convention. */
struct sp11sb_avoid {               /* a range the placement must stay out of although the map calls it free */
    uint64_t base, size;
    uint32_t tag, reserved;
};
struct sp11sb_acpi {                /* an ACPI table the facts were read from; bound by the builder, not checkable at EL2 in ABI v1 */
    uint8_t sig[4];
    uint32_t length;
    uint8_t sha256[32];
};
struct sp11sb_pin {
    uint8_t magic[8];                   /* 0x000 SP11SB_PIN_MAGIC */
    uint32_t version, size;             /* SP11SB_VERSION, SP11SB_PIN_BYTES */
    uint32_t flags;                     /* zero in version 1 */
    uint32_t authorised_stores;         /* SP11SB_STORE_* */
    uint8_t board_id[16];               /* strings: not empty, terminated, zero after the terminator */
    uint8_t build_label[24];            /* shown on the panel */
    uint8_t descriptor_sha256[32];      /* the boot-ready board descriptor the facts come from */
    uint8_t authorisation_sha256[32];   /* the operator's authorisation record; zero is refused */
    uint64_t guest_file_size;           /* 0x080 what binds PIN.DAT to GUEST.DAT: size, chunk count and the two digests */
    uint32_t guest_chunk_count, reserved0;
    uint8_t guest_file_sha256[32];      /* whole file, equal to the runner manifest's digest */
    uint8_t guest_index_sha256[32];     /* the chunk table: a digest of digests */
    uint64_t boot_mpidr;                /* 0x0D0 facts of the unit */
    uint32_t cntfrq_hz, midr_count;
    uint32_t midr[4];                   /* strictly ascending */
    uint64_t gicd_base, gicr_first, gicr_limit, gicr_stride;    /* 0x0F0 the GIC values the monitor build takes as -D macros */
    uint64_t its_base;                  /* zero when there is no ITS */
    uint32_t gicr_count, timer_intid;   /* EL2 physical timer PPI */
    uint64_t fb_base, fb_bytes;         /* 0x120 the GOP framebuffer */
    uint32_t fb_width, fb_height, fb_stride, fb_format;     /* stride in pixels; format 0 RGBX, 1 BGRX */
    uint64_t place_base, place_size;    /* 0x140 the one placement: guest, reserve and work, contiguous */
    uint64_t guest_base, guest_size;
    uint64_t reserve_size;              /* directly above the guest */
    uint64_t work_base, work_size;      /* x1 and x2 of the monitor call */
    uint32_t avoid_count, reserved1;
    struct sp11sb_avoid avoid[4];       /* 0x180 ascending by base */
    uint32_t acpi_count, reserved2;     /* 0x1E0 */
    struct sp11sb_acpi acpi[8];         /* 0x1E8 ascending by signature */
    uint8_t reserved3[184];             /* 0x328 */
    uint8_t record_sha256[32];          /* 0x3E0 SHA-256 of bytes 0 to 0x3DF */
};
_Static_assert(sizeof(struct sp11sb_avoid) == 24 && sizeof(struct sp11sb_acpi) == 40, "array element sizes");
_Static_assert(offsetof(struct sp11sb_pin, guest_file_size) == 0x80 && offsetof(struct sp11sb_pin, boot_mpidr) == 0xD0 &&
               offsetof(struct sp11sb_pin, gicd_base) == 0xF0 && offsetof(struct sp11sb_pin, fb_base) == 0x120 &&
               offsetof(struct sp11sb_pin, place_base) == 0x140 && offsetof(struct sp11sb_pin, avoid) == 0x180 &&
               offsetof(struct sp11sb_pin, acpi) == 0x1E8 && offsetof(struct sp11sb_pin, reserved3) == 0x328,
               "PIN.DAT group starts: a padding byte before any of them would shift every later field");
_Static_assert(offsetof(struct sp11sb_pin, record_sha256) == SP11SB_PIN_BYTES - 32 && sizeof(struct sp11sb_pin) == SP11SB_PIN_BYTES,
               "the digest is the last member and covers everything before it");

/* ---- GUEST.DAT: head, chunk table, chunk data, in one canonical layout (a container has exactly one encoding): the table
 * follows the head at 128, the data follows the table, each DATA chunk starts at the next multiple of 64 after the previous
 * one, the file ends with the last chunk and pad bytes are zero. Every byte is therefore covered by a digest or required to
 * be zero. index_sha256 is the digest of the table: it commits to every chunk digest, destination and size, so it is the cheap
 * check that makes the per-chunk and whole-file digests optional work. */
struct sp11sb_guest_head {
    uint8_t magic[8];                   /* SP11SB_GUEST_MAGIC */
    uint32_t version, head_size;        /* head_size 128 */
    uint32_t flags, entry_size;         /* flags zero in version 1; entry_size 64 */
    uint32_t entry_count, reserved0;    /* 1 to SP11SB_CHUNKS_MAX */
    uint64_t table_offset, data_offset; /* 0x20 128; table_offset + entry_count * 64 */
    uint64_t file_size;                 /* exact length of the file */
    uint64_t load_base, load_size;      /* the pinned guest range: every chunk lies inside it */
    uint8_t index_sha256[32];           /* 0x48 SHA-256 of the table bytes */
    uint8_t reserved1[24];              /* 0x68 */
};
struct sp11sb_chunk {
    uint64_t dest, size;                /* physical destination and length: multiples of 16, ascending, no overlap */
    uint64_t file_offset;               /* 0x10 canonical offset of the data; 0 for a ZERO chunk */
    uint16_t kind, flags;               /* 0x18 SP11SB_KIND_*; SP11SB_CHUNK_ZERO or 0 */
    uint32_t reserved;
    uint8_t sha256[32];                 /* 0x20 SHA-256 of the data; all zero for a ZERO chunk and only for one */
};
_Static_assert(sizeof(struct sp11sb_guest_head) == SP11SB_GUEST_HEAD_BYTES && offsetof(struct sp11sb_guest_head, table_offset) == 0x20 &&
               offsetof(struct sp11sb_guest_head, index_sha256) == 0x48 && offsetof(struct sp11sb_guest_head, reserved1) == 0x68,
               "GUEST.DAT head: two cache lines, 8-byte members 8-aligned");
_Static_assert(sizeof(struct sp11sb_chunk) == SP11SB_CHUNK_BYTES && offsetof(struct sp11sb_chunk, kind) == 0x18 &&
               offsetof(struct sp11sb_chunk, sha256) == 0x20, "a chunk entry is one cache line");

/* ---- REPORT.DAT: a zero file the runner loads and the wrapper writes. Page 0 is this head (written by the wrapper, read by
 * SNAPREP); the monitor's report window (x0 of the monitor call) starts at SP11SB_REPORT_OUT and is opaque here. The file is in
 * the runner's declared memory: outside the placement, outside every stage-2 range, and reachable at EL2 as the payload stack is. */
#define SP11SB_REPORT_OUT SP11SB_REPORT_HEAD_PAGE
struct sp11sb_report {
    uint8_t magic[8];                   /* 0x000 SP11SB_REPORT_MAGIC */
    uint32_t version;                   /* 0x008 */
    uint32_t head_size;                 /* 0x00C sizeof this structure; the rest of the page is zero */
    uint32_t stage;                     /* 0x010 SP11SB_STAGE_* */
    uint32_t refusal;                   /* 0x014 SP11SB_E_*, 0 when none */
    uint64_t refusal_detail;            /* 0x018 chunk index, offending value or a runtime code's argument */
    uint32_t hash_policy;               /* 0x020 SP11SB_HASH_* actually used */
    uint32_t lines_printed;             /* 0x024 at most SP11SB_WRAPPER_LINES */
    uint64_t current_el, midr_el1, mpidr_el1, cntfrq_el0, hcr_el2, sctlr_el2, tcr_el2;      /* 0x028 raw reads, 0x060 end */
    uint32_t pin_code, guest_code;      /* 0x060 the wrapper's verdicts on PIN.DAT and GUEST.DAT */
    uint64_t guest_file_base, guest_file_size;      /* 0x068 where the runner put GUEST.DAT */
    uint64_t place_base, place_size;    /* 0x078 the placement as verified against the final map */
    uint64_t desc_base, desc_pages;     /* 0x088 the conventional descriptor that held it */
    uint32_t desc_index, chunks_total, chunks_copied, chunks_hashed;        /* 0x098 */
    uint64_t bytes_copied, bytes_hashed, counter_hz;                        /* 0x0A8 */
    uint64_t ticks_enter, ticks_placed, ticks_copied, ticks_hashed, ticks_launch, ticks_return;    /* 0x0C0 CNTPCT_EL0 */
    uint64_t monitor_out, work_base, work_size, params_pa;      /* 0x0F0 x0 to x3 of the monitor call */
    uint64_t monitor_ret;               /* 0x110 what the monitor returned in x0 */
    uint64_t monitor_status;            /* 0x118 word 0 of its report window (probes/xnuboot.S OUT_STATUS) */
    uint64_t monitor_entered;           /* 0x120 word 1 (OUT_ENTERED) */
};
#define SP11SB_REPORT_HEAD_BYTES 296u
_Static_assert(sizeof(struct sp11sb_report) == SP11SB_REPORT_HEAD_BYTES && offsetof(struct sp11sb_report, pin_code) == 0x60 &&
               offsetof(struct sp11sb_report, counter_hz) == 0xB8 && offsetof(struct sp11sb_report, monitor_out) == 0xF0 &&
               offsetof(struct sp11sb_report, monitor_entered) == 0x120,
               "report head: group starts, no padding; the monitor window begins on its own page");
_Static_assert(SP11SB_REPORT_BYTES == 0x81000u && SP11SB_REPORT_HEAD_BYTES <= SP11SB_REPORT_HEAD_PAGE,
               "REPORT.DAT is the head page plus the monitor window");

/* ---- Inputs the wrapper hands to the checks. Nothing here is stored in a bundle. */
struct sp11sb_file {                /* one preload-table entry: its path (char[256]), where the runner put it, its size */
    const char *path;
    uint64_t base, size;
};
struct sp11sb_range { uint64_t base, size; };
struct sp11sb_observed {            /* registers read at EL2, the monitor build's GIC macros and the runner's framebuffer */
    uint64_t current_el, hcr_el2, sctlr_el2, tcr_el2, midr_el1, mpidr_el1, cntfrq_el0;
    uint64_t build_gicd_base, build_gicr_first, build_gicr_limit, build_gicr_stride, build_timer_intid;
    uint64_t build_stores;          /* SP11SB_STORE_* this wrapper build will use */
    uint64_t fb_base, fb_bytes;
    uint32_t fb_width, fb_height, fb_stride, fb_format;
};
typedef void (*sp11sb_sha256_fn)(const void *data, uint64_t size, uint8_t digest[32]);
struct sp11sb_env {
    uint64_t args_magic;
    uint32_t args_version, args_size;
    const struct sp11sb_file *files;
    uint32_t nfiles;
    const uint8_t *pin;
    uint64_t pin_len;
    const uint8_t *guest;
    uint64_t guest_len;
    struct sp11sb_observed obs;
    const uint8_t *map;             /* the final UEFI memory map the runner passes */
    uint64_t map_size, map_stride;
    const struct sp11sb_range *declared;    /* every range the runner declared plus the payload stack */
    uint32_t ndeclared;
    uint32_t hash_policy;
    sp11sb_sha256_fn sha256;
};

/* ---- Names. A table of characters, not of pointers, so the unit needs no relocation to print one. */
struct sp11sb_error_row { uint8_t code; uint8_t sides; char name[SP11SB_NAME_MAX + 1]; };
struct sp11sb_stage_row { uint8_t value; char name[SP11SB_NAME_MAX + 1]; };
static const struct sp11sb_error_row sp11sb_errors[SP11SB_ERROR_ROWS] __attribute__((unused)) = {
    {0, 0, "OK"},
#define X(name, code, sides) {code, sides, #name},
    SP11SB_ERRORS(X)
#undef X
};
static const struct sp11sb_stage_row sp11sb_stages[SP11SB_STAGE_COUNT] __attribute__((unused)) = {
#define X(name, value) {value, #name},
    SP11SB_STAGES(X)
#undef X
};
static inline const struct sp11sb_error_row *sp11sb_error_row_of(uint32_t code)
{
    uint32_t i;
    for (i = 0; i < SP11SB_ERROR_ROWS; i++)
        if (sp11sb_errors[i].code == code) return &sp11sb_errors[i];
    return 0;
}
static inline const char *sp11sb_error_name(uint32_t code)
{
    const struct sp11sb_error_row *row = sp11sb_error_row_of(code);
    return row ? row->name : "UNKNOWN";
}
static inline int sp11sb_error_known(uint32_t code) { return sp11sb_error_row_of(code) != 0; }
static inline const char *sp11sb_stage_name(uint32_t stage)
{
    return stage < SP11SB_STAGE_COUNT ? sp11sb_stages[stage].name : "UNKNOWN";
}

/* ---- Byte helpers. Records are read byte by byte: the buffers are file images at any alignment and may be hostile, and
 * a cast to a structure would be undefined behaviour there. */
static inline uint32_t sp11sb_rd16(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8; }
static inline uint32_t sp11sb_rd32(const uint8_t *p) { return sp11sb_rd16(p) | sp11sb_rd16(p + 2) << 16; }
static inline uint64_t sp11sb_rd64(const uint8_t *p) { return sp11sb_rd32(p) | (uint64_t)sp11sb_rd32(p + 4) << 32; }
static inline int sp11sb_zero(const uint8_t *p, uint64_t n)
{
    uint64_t i;
    for (i = 0; i < n; i++)
        if (p[i]) return 0;
    return 1;
}
static inline int sp11sb_same(const uint8_t *a, const uint8_t *b, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++)
        if (a[i] != b[i]) return 0;
    return 1;
}
static inline int sp11sb_magic(const uint8_t *p, const char *m)
{
    uint32_t i;
    for (i = 0; i < 8; i++)
        if (p[i] != (uint8_t)m[i]) return 0;
    return 1;
}
/* Half-open ranges, either of which may wrap: a wrapping range counts as overlapping everything. */
static inline int sp11sb_overlap(uint64_t a, uint64_t an, uint64_t b, uint64_t bn)
{
    if (an > UINT64_MAX - a || bn > UINT64_MAX - b) return 1;
    return an && bn && a < b + bn && b < a + an;
}
static inline int sp11sb_string_ok(const uint8_t *p, uint32_t cap)
{
    uint32_t n = 0;
    while (n < cap && p[n]) {
        uint8_t c = p[n];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-'))
            return 0;
        n++;
    }
    return n && n < cap && sp11sb_zero(p + n, cap - n);     /* not empty, terminated, zero after the terminator */
}

#define SP11SB_PIN_U32(b, f) sp11sb_rd32((b) + offsetof(struct sp11sb_pin, f))
#define SP11SB_PIN_U64(b, f) sp11sb_rd64((b) + offsetof(struct sp11sb_pin, f))
#define SP11SB_PIN_AT(b, f) ((b) + offsetof(struct sp11sb_pin, f))
#define SP11SB_MIDR(b, i) sp11sb_rd32(SP11SB_PIN_AT(b, midr) + 4 * (i))
#define SP11SB_AVOID_AT(b, i) (SP11SB_PIN_AT(b, avoid) + sizeof(struct sp11sb_avoid) * (i))
#define SP11SB_ACPI_AT(b, i) (SP11SB_PIN_AT(b, acpi) + sizeof(struct sp11sb_acpi) * (i))
static inline uint32_t sp11sb_up(uint32_t c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }

/* ---- Runner contract and the file set. */
static inline int sp11sb_args_check(uint64_t magic, uint32_t version, uint32_t size)
{
    return magic != SP11SB_RUNNER_MAGIC || version != SP11SB_RUNNER_VERSION || size < SP11SB_RUNNER_ARGS_BYTES
        ? SP11SB_E_ARGS_BAD : 0;
}
/* \SP11\ plus the role's 8.3 name, any case, and nothing after it. At most 18 bytes of the path are read. */
static inline int sp11sb_path_is(const char *path, uint32_t role)
{
    static const char dir[] = "\\SP11\\";
    static const char names[SP11SB_FILES][12] = {"SNAPMON.BIN", "SNAPREP.BIN", "GUEST.DAT", "PIN.DAT", "REPORT.DAT"};
    uint32_t i = 0, n = 0;
    if (!path || role >= SP11SB_FILES) return 0;
    for (; dir[i]; i++)
        if (sp11sb_up((uint8_t)path[i]) != (uint8_t)dir[i]) return 0;
    for (; names[role][n]; n++)
        if (sp11sb_up((uint8_t)path[i + n]) != (uint8_t)names[role][n]) return 0;
    return path[i + n] == 0;
}
/* One slot of the file set: its role by manifest position, a page-aligned base that does not wrap, and a size the role allows.
 * The wrapper checks REPORT.DAT's slot first so that a later refusal can still be recorded. */
static inline int sp11sb_file_check(const struct sp11sb_file *f, uint32_t role)
{
    if (!f || !sp11sb_path_is(f->path, role)) return SP11SB_E_FILE_ROLE;
    if (!f->base || (f->base & 0xFFF) || f->size > UINT64_MAX - f->base) return SP11SB_E_FILE_BASE;
    if (!f->size || f->size > SP11SB_FILE_MAX || (role == SP11SB_ROLE_REPORT && f->size != SP11SB_REPORT_BYTES)) return SP11SB_E_FILE_SIZE;
    return 0;
}
static inline int sp11sb_files_check(const struct sp11sb_file *files, uint32_t count)
{
    uint32_t i;
    if (count != SP11SB_FILES || !files) return SP11SB_E_FILE_COUNT;
    for (i = 0; i < SP11SB_FILES; i++) {
        int r = sp11sb_file_check(&files[i], i);
        if (r) return r;
    }
    return 0;
}

/* ---- PIN.DAT. */
static inline int sp11sb_pin_head_check(const uint8_t *b, uint64_t len)
{
    if (!b || len < SP11SB_PIN_BYTES) return SP11SB_E_PIN_TRUNCATED;
    if (!sp11sb_magic(b, SP11SB_PIN_MAGIC)) return SP11SB_E_PIN_MAGIC;
    if (SP11SB_PIN_U32(b, version) != SP11SB_VERSION) return SP11SB_E_PIN_VERSION;
    if (len != SP11SB_PIN_BYTES || SP11SB_PIN_U32(b, size) != SP11SB_PIN_BYTES) return SP11SB_E_PIN_SIZE;
    return 0;
}
static inline int sp11sb_pin_counts_ok(const uint8_t *b)
{
    uint32_t m = SP11SB_PIN_U32(b, midr_count), a = SP11SB_PIN_U32(b, avoid_count), t = SP11SB_PIN_U32(b, acpi_count);
    uint32_t r = SP11SB_PIN_U32(b, gicr_count), c = SP11SB_PIN_U32(b, guest_chunk_count);
    return m >= 1 && m <= SP11SB_MIDR_MAX && a <= SP11SB_AVOID_MAX && t <= SP11SB_ACPI_MAX && r >= 1 && r <= SP11SB_GICR_MAX &&
           c >= 1 && c <= SP11SB_CHUNKS_MAX;
}
static inline int sp11sb_pin_reserved_ok(const uint8_t *b)
{
    uint32_t i, m = SP11SB_PIN_U32(b, midr_count), a = SP11SB_PIN_U32(b, avoid_count), t = SP11SB_PIN_U32(b, acpi_count);
    if (SP11SB_PIN_U32(b, reserved0) || SP11SB_PIN_U32(b, reserved1) || SP11SB_PIN_U32(b, reserved2)) return 0;
    if (!sp11sb_zero(SP11SB_PIN_AT(b, reserved3), sizeof(((struct sp11sb_pin *)0)->reserved3))) return 0;
    for (i = m; i < SP11SB_MIDR_MAX; i++)
        if (SP11SB_MIDR(b, i)) return 0;
    for (i = 0; i < SP11SB_AVOID_MAX; i++)
        if (i >= a ? !sp11sb_zero(SP11SB_AVOID_AT(b, i), sizeof(struct sp11sb_avoid)) :
                     sp11sb_rd32(SP11SB_AVOID_AT(b, i) + offsetof(struct sp11sb_avoid, reserved))) return 0;
    for (i = t; i < SP11SB_ACPI_MAX; i++)
        if (!sp11sb_zero(SP11SB_ACPI_AT(b, i), sizeof(struct sp11sb_acpi))) return 0;
    return 1;
}
static inline int sp11sb_pin_order_ok(const uint8_t *b)
{
    uint32_t i, j, m = SP11SB_PIN_U32(b, midr_count), a = SP11SB_PIN_U32(b, avoid_count), t = SP11SB_PIN_U32(b, acpi_count);
    for (i = 1; i < m && i < SP11SB_MIDR_MAX; i++)
        if (SP11SB_MIDR(b, i - 1) >= SP11SB_MIDR(b, i)) return 0;
    for (i = 1; i < t && i < SP11SB_ACPI_MAX; i++) {
        const uint8_t *p = SP11SB_ACPI_AT(b, i - 1), *q = SP11SB_ACPI_AT(b, i);
        for (j = 0; j < 4 && p[j] == q[j]; j++) {}
        if (j == 4 || p[j] > q[j]) return 0;
    }
    for (i = 1; i < a && i < SP11SB_AVOID_MAX; i++)
        if (sp11sb_rd64(SP11SB_AVOID_AT(b, i - 1)) >= sp11sb_rd64(SP11SB_AVOID_AT(b, i))) return 0;
    return 1;
}
/* Facts that must be internally consistent. The GIC rules are the monitor's own (probes/secondary.c static asserts): a
 * redistributor stride of at least two 64 KiB frames, in whole frames, and the timer a PPI. */
static inline int sp11sb_pin_values_ok(const uint8_t *b)
{
    uint32_t i, m = SP11SB_PIN_U32(b, midr_count), a = SP11SB_PIN_U32(b, avoid_count), t = SP11SB_PIN_U32(b, acpi_count);
    uint64_t gd = SP11SB_PIN_U64(b, gicd_base), gf = SP11SB_PIN_U64(b, gicr_first), gl = SP11SB_PIN_U64(b, gicr_limit);
    uint64_t gs = SP11SB_PIN_U64(b, gicr_stride), n = SP11SB_PIN_U32(b, gicr_count), prev_end = 0;
    uint64_t fbb = SP11SB_PIN_U64(b, fb_bytes), fbs = SP11SB_PIN_U64(b, fb_base), gsz = SP11SB_PIN_U64(b, guest_file_size);
    uint32_t w = SP11SB_PIN_U32(b, fb_width), h = SP11SB_PIN_U32(b, fb_height), st = SP11SB_PIN_U32(b, fb_stride);
    uint32_t intid = SP11SB_PIN_U32(b, timer_intid);
    for (i = 0; i < m && i < SP11SB_MIDR_MAX; i++)
        if (!SP11SB_MIDR(b, i)) return 0;
    if (!SP11SB_PIN_U32(b, cntfrq_hz)) return 0;
    if (!gd || (gd & 0xFFFF) || !gf || (gf & 0xFFFF) || gs < 0x20000 || (gs & 0xFFFF) || (SP11SB_PIN_U64(b, its_base) & 0xFFFF)) return 0;
    if (!n || gs > (UINT64_MAX - gf) / n || gl != gf + n * gs) return 0;       /* the window is exactly gicr_count frames */
    if (gd > UINT64_MAX - 0x10000 || (gd < gl && gf < gd + 0x10000)) return 0;  /* the distributor is not inside the window */
    if (intid < 16 || intid > 31) return 0;
    if (!fbs || (fbs & 0xFFF) || !w || !h || st < w || SP11SB_PIN_U32(b, fb_format) > 1) return 0;
    if ((uint64_t)st * h > (UINT64_MAX >> 2) || fbb < (uint64_t)st * h * 4 || fbb > UINT64_MAX - fbs) return 0;
    if (sp11sb_zero(SP11SB_PIN_AT(b, descriptor_sha256), 32) || sp11sb_zero(SP11SB_PIN_AT(b, guest_file_sha256), 32) ||
        sp11sb_zero(SP11SB_PIN_AT(b, guest_index_sha256), 32)) return 0;
    if (gsz < SP11SB_GUEST_HEAD_BYTES + SP11SB_CHUNK_BYTES || gsz > SP11SB_FILE_MAX) return 0;
    for (i = 0; i < a && i < SP11SB_AVOID_MAX; i++) {
        uint64_t base = sp11sb_rd64(SP11SB_AVOID_AT(b, i)), size = sp11sb_rd64(SP11SB_AVOID_AT(b, i) + 8);
        uint32_t tag = sp11sb_rd32(SP11SB_AVOID_AT(b, i) + 16);
        if ((base | size) & 0xFFF || !size || size > UINT64_MAX - base || (i && base < prev_end)) return 0;
        if (tag < SP11SB_AVOID_EL2_UNREACHABLE || tag > SP11SB_AVOID_OPERATOR) return 0;
        prev_end = base + size;
    }
    for (i = 0; i < t && i < SP11SB_ACPI_MAX; i++) {
        const uint8_t *e = SP11SB_ACPI_AT(b, i);
        uint32_t k;
        for (k = 0; k < 4; k++)
            if (!((e[k] >= 'A' && e[k] <= 'Z') || (e[k] >= '0' && e[k] <= '9'))) return 0;
        if (sp11sb_rd32(e + 4) < 36 || sp11sb_zero(e + 8, 32)) return 0;       /* an ACPI header is 36 bytes */
    }
    return 1;
}
/* The pinned placement as arithmetic: alignments, the layout xnuboot.place() and run.py produce, and the stage-2 limit. */
static inline int sp11sb_pin_geometry_check(const uint8_t *b)
{
    uint64_t pb = SP11SB_PIN_U64(b, place_base), ps = SP11SB_PIN_U64(b, place_size), gb = SP11SB_PIN_U64(b, guest_base);
    uint64_t gs = SP11SB_PIN_U64(b, guest_size), rs = SP11SB_PIN_U64(b, reserve_size), wb = SP11SB_PIN_U64(b, work_base);
    uint64_t ws = SP11SB_PIN_U64(b, work_size), e1, e2, e3;
    if ((gb | gs) & (SP11SB_GUEST_ALIGN - 1) || rs & (SP11SB_RESERVE_ALIGN - 1) || wb & (SP11SB_WORK_ALIGN - 1) ||
        (pb | ps) & (SP11SB_RESERVE_ALIGN - 1)) return SP11SB_E_PLACE_ALIGN;
    if (gs > UINT64_MAX - gb) return SP11SB_E_PLACE_LIMIT;
    e1 = gb + gs;
    if (rs > UINT64_MAX - e1) return SP11SB_E_PLACE_LIMIT;
    e2 = e1 + rs;
    if (ws > UINT64_MAX - e2) return SP11SB_E_PLACE_LIMIT;
    e3 = e2 + ws;
    if (!gb || gs < SP11SB_GUEST_MIN || rs < SP11SB_RESERVE_MIN || ws != SP11SB_WORK_BYTES || pb != gb || wb != e2 || ps != gs + rs + ws)
        return SP11SB_E_PLACE_LAYOUT;
    return e3 > SP11SB_IPA_LIMIT ? SP11SB_E_PLACE_LIMIT : 0;    /* ending exactly at the limit is allowed, as in shadow/stage2.c */
}
static inline int sp11sb_pin_body_check(const uint8_t *b)
{
    int r;
    if (SP11SB_PIN_U32(b, flags) || (SP11SB_PIN_U32(b, authorised_stores) & ~SP11SB_STORE_ALL)) return SP11SB_E_PIN_FLAGS;
    if (!sp11sb_string_ok(SP11SB_PIN_AT(b, board_id), 16) || !sp11sb_string_ok(SP11SB_PIN_AT(b, build_label), 24)) return SP11SB_E_PIN_STRING;
    if (!sp11sb_pin_counts_ok(b)) return SP11SB_E_PIN_COUNT;
    if (!sp11sb_pin_reserved_ok(b)) return SP11SB_E_PIN_RESERVED;
    if (!sp11sb_pin_order_ok(b)) return SP11SB_E_PIN_ORDER;
    if (!sp11sb_pin_values_ok(b)) return SP11SB_E_PIN_VALUE;
    if ((r = sp11sb_pin_geometry_check(b)) != 0) return r;
    if ((SP11SB_PIN_U32(b, authorised_stores) & SP11SB_STORE_REQUIRED) != SP11SB_STORE_REQUIRED) return SP11SB_E_STORE_NOT_AUTH;
    return sp11sb_zero(SP11SB_PIN_AT(b, authorisation_sha256), 32) ? SP11SB_E_NO_AUTHORISATION : 0;
}
/* Everything but the digest: the order the builder and the tests use to reach the semantic checks of a record whose digest
 * is not the point of the case. */
static inline int sp11sb_pin_check_nodigest(const uint8_t *b, uint64_t len)
{
    int r = sp11sb_pin_head_check(b, len);
    return r ? r : sp11sb_pin_body_check(b);
}
static inline int sp11sb_pin_check(const uint8_t *b, uint64_t len, sp11sb_sha256_fn sha256)
{
    uint8_t d[32];
    int r = sp11sb_pin_head_check(b, len);
    if (r) return r;
    sha256(b, SP11SB_PIN_BYTES - 32, d);
    if (!sp11sb_same(d, SP11SB_PIN_AT(b, record_sha256), 32)) return SP11SB_E_PIN_DIGEST;
    return sp11sb_pin_body_check(b);
}

/* ---- What the wrapper reads at EL2 against what the bundle pinned. The EL2 regime rule is the one compat/pauth-native.S
 * applies before it edits TCR_EL2 and SCTLR_EL2: non-VHE, MMU on, 4 KiB granule, T0SZ 17 to 24. */
static inline int sp11sb_observed_check(const uint8_t *pin, const struct sp11sb_observed *o)
{
    uint32_t i, m = SP11SB_PIN_U32(pin, midr_count);
    int found = 0;
    if (o->current_el != 8) return SP11SB_E_NOT_EL2;
    if (o->hcr_el2 & (UINT64_C(1) << 34)) return SP11SB_E_E2H_SET;
    if (!(o->sctlr_el2 & 1) || ((o->tcr_el2 >> 14) & 3) || (o->tcr_el2 & 63) < 17 || (o->tcr_el2 & 63) > 24) return SP11SB_E_EL2_REGIME;
    for (i = 0; i < m && i < SP11SB_MIDR_MAX; i++)
        found |= SP11SB_MIDR(pin, i) == o->midr_el1;
    if (!found) return SP11SB_E_MIDR_MISMATCH;
    if (o->mpidr_el1 != SP11SB_PIN_U64(pin, boot_mpidr)) return SP11SB_E_MPIDR_MISMATCH;
    if (o->cntfrq_el0 != SP11SB_PIN_U32(pin, cntfrq_hz)) return SP11SB_E_CNTFRQ_MISMATCH;
    if (o->build_gicd_base != SP11SB_PIN_U64(pin, gicd_base) || o->build_gicr_first != SP11SB_PIN_U64(pin, gicr_first) ||
        o->build_gicr_limit != SP11SB_PIN_U64(pin, gicr_limit) || o->build_gicr_stride != SP11SB_PIN_U64(pin, gicr_stride) ||
        o->build_timer_intid != SP11SB_PIN_U32(pin, timer_intid)) return SP11SB_E_GIC_MISMATCH;
    if (o->fb_base != SP11SB_PIN_U64(pin, fb_base) || o->fb_bytes != SP11SB_PIN_U64(pin, fb_bytes) ||
        o->fb_width != SP11SB_PIN_U32(pin, fb_width) || o->fb_height != SP11SB_PIN_U32(pin, fb_height) ||
        o->fb_stride != SP11SB_PIN_U32(pin, fb_stride) || o->fb_format != SP11SB_PIN_U32(pin, fb_format)) return SP11SB_E_GOP_MISMATCH;
    return (o->build_stores & ~(uint64_t)SP11SB_PIN_U32(pin, authorised_stores)) ? SP11SB_E_STORE_NOT_AUTH : 0;
}

/* ---- The placement against the final memory map. The placement must lie inside ONE conventional write-back descriptor
 * and no other descriptor may touch it; it must not overlap a declared range, the framebuffer or a pinned avoid range.
 * A descriptor is read as the EFI layout: u32 type at 0, u64 physical start at 8, u64 pages at 24, u64 attribute at 32. */
static inline int sp11sb_place_check(const uint8_t *pin, const uint8_t *map, uint64_t map_size, uint64_t stride,
                                     uint64_t fb_base, uint64_t fb_bytes, const struct sp11sb_range *declared, uint32_t ndeclared)
{
    uint64_t pb = SP11SB_PIN_U64(pin, place_base), ps = SP11SB_PIN_U64(pin, place_size), pe, i, n;
    uint64_t hit_attr = 0;
    uint32_t k, a = SP11SB_PIN_U32(pin, avoid_count), hit = 0, other = 0;
    if (!ps || ps > UINT64_MAX - pb) return SP11SB_E_PLACE_LAYOUT;
    pe = pb + ps;
    if (!map || stride < SP11SB_MAP_MIN_STRIDE || !map_size || map_size % stride || map_size / stride > SP11SB_MAP_MAX) return SP11SB_E_MAP_BAD;
    n = map_size / stride;
    for (i = 0; i < n; i++) {
        const uint8_t *d = map + i * stride;
        uint64_t start = sp11sb_rd64(d + 8), pages = sp11sb_rd64(d + 24), end;
        if ((start & 0xFFF) || !pages || pages > (UINT64_MAX >> 12) || (pages << 12) > UINT64_MAX - start) return SP11SB_E_MAP_BAD;
        end = start + (pages << 12);
        if (start < pe && pb < end) {
            if (sp11sb_rd32(d) == SP11SB_EFI_CONVENTIONAL && start <= pb && pe <= end) { hit++; hit_attr = sp11sb_rd64(d + 32); }
            else other = 1;
        }
    }
    if (hit != 1 || other) return SP11SB_E_PLACE_NOT_CONV;
    if (!(hit_attr & SP11SB_EFI_WB)) return SP11SB_E_PLACE_ATTR;
    if (ndeclared > SP11SB_DECLARED_MAX || (ndeclared && !declared)) return SP11SB_E_PLACE_DECLARED;
    for (k = 0; k < ndeclared; k++)
        if (sp11sb_overlap(pb, ps, declared[k].base, declared[k].size)) return SP11SB_E_PLACE_DECLARED;
    if (sp11sb_overlap(pb, ps, fb_base, fb_bytes)) return SP11SB_E_PLACE_FB;
    if (a > SP11SB_AVOID_MAX) return SP11SB_E_PLACE_AVOID;
    for (k = 0; k < a; k++)
        if (sp11sb_overlap(pb, ps, sp11sb_rd64(SP11SB_AVOID_AT(pin, k)), sp11sb_rd64(SP11SB_AVOID_AT(pin, k) + 8))) return SP11SB_E_PLACE_AVOID;
    return 0;
}

/* ---- GUEST.DAT. Structure first (no hashing), then digests. */
#define SP11SB_GUEST_U32(b, f) sp11sb_rd32((b) + offsetof(struct sp11sb_guest_head, f))
#define SP11SB_GUEST_U64(b, f) sp11sb_rd64((b) + offsetof(struct sp11sb_guest_head, f))
#define SP11SB_CHUNK_AT(f, i) ((f) + SP11SB_GUEST_HEAD_BYTES + (uint64_t)(i) * SP11SB_CHUNK_BYTES)
#define SP11SB_CHUNK_U64(e, f) sp11sb_rd64((e) + offsetof(struct sp11sb_chunk, f))
#define SP11SB_CHUNK_U16(e, f) sp11sb_rd16((e) + offsetof(struct sp11sb_chunk, f))

static inline int sp11sb_guest_head_check(const uint8_t *f, uint64_t len, const uint8_t *pin)
{
    uint64_t file_size, table_end;
    uint32_t count;
    if (!f || len < SP11SB_GUEST_HEAD_BYTES) return SP11SB_E_GUEST_TRUNCATED;
    if (!sp11sb_magic(f, SP11SB_GUEST_MAGIC)) return SP11SB_E_GUEST_MAGIC;
    if (SP11SB_GUEST_U32(f, version) != SP11SB_VERSION) return SP11SB_E_GUEST_VERSION;
    if (SP11SB_GUEST_U32(f, flags)) return SP11SB_E_GUEST_FLAGS;
    if (SP11SB_GUEST_U32(f, head_size) != SP11SB_GUEST_HEAD_BYTES || SP11SB_GUEST_U32(f, entry_size) != SP11SB_CHUNK_BYTES ||
        SP11SB_GUEST_U64(f, table_offset) != SP11SB_GUEST_HEAD_BYTES) return SP11SB_E_GUEST_SIZE;
    if (SP11SB_GUEST_U32(f, reserved0) || !sp11sb_zero(f + offsetof(struct sp11sb_guest_head, reserved1), sizeof(((struct sp11sb_guest_head *)0)->reserved1)))
        return SP11SB_E_GUEST_RESERVED;
    file_size = SP11SB_GUEST_U64(f, file_size);
    if (len < file_size) return SP11SB_E_GUEST_TRUNCATED;
    if (len > file_size || file_size > SP11SB_FILE_MAX) return SP11SB_E_GUEST_SIZE;
    count = SP11SB_GUEST_U32(f, entry_count);
    if (count < 1 || count > SP11SB_CHUNKS_MAX) return SP11SB_E_GUEST_COUNT;
    table_end = SP11SB_GUEST_HEAD_BYTES + (uint64_t)count * SP11SB_CHUNK_BYTES;
    if (table_end > file_size) return SP11SB_E_GUEST_COUNT;                     /* the table does not fit in the file */
    if (SP11SB_GUEST_U64(f, data_offset) != table_end) return SP11SB_E_GUEST_SIZE;
    if (SP11SB_GUEST_U64(f, load_base) != SP11SB_PIN_U64(pin, guest_base) || SP11SB_GUEST_U64(f, load_size) != SP11SB_PIN_U64(pin, guest_size))
        return SP11SB_E_GUEST_LOAD;
    if (file_size != SP11SB_PIN_U64(pin, guest_file_size) || count != SP11SB_PIN_U32(pin, guest_chunk_count) ||
        !sp11sb_same(f + offsetof(struct sp11sb_guest_head, index_sha256), SP11SB_PIN_AT(pin, guest_index_sha256), 32))
        return SP11SB_E_GUEST_PIN;
    return 0;
}
static inline int sp11sb_guest_check(const uint8_t *f, uint64_t len, const uint8_t *pin)
{
    uint64_t file_size, load_base, load_size, expect, prev_dest = 0, prev_end = 0;
    uint32_t count, i, seen[SP11SB_KIND_MAX + 1];
    int r = sp11sb_guest_head_check(f, len, pin);
    if (r) return r;
    file_size = SP11SB_GUEST_U64(f, file_size);
    load_base = SP11SB_GUEST_U64(f, load_base);
    load_size = SP11SB_GUEST_U64(f, load_size);
    count = SP11SB_GUEST_U32(f, entry_count);
    expect = SP11SB_GUEST_U64(f, data_offset);          /* where the next DATA chunk's bytes would start, before alignment */
    for (i = 0; i <= SP11SB_KIND_MAX; i++) seen[i] = 0;
    for (i = 0; i < count; i++) {
        const uint8_t *e = SP11SB_CHUNK_AT(f, i);
        uint64_t dest = SP11SB_CHUNK_U64(e, dest), size = SP11SB_CHUNK_U64(e, size);
        uint64_t foff = SP11SB_CHUNK_U64(e, file_offset), aligned;
        uint32_t kind = SP11SB_CHUNK_U16(e, kind), flags = SP11SB_CHUNK_U16(e, flags);
        const uint8_t *digest = e + offsetof(struct sp11sb_chunk, sha256);
        int zero = (flags & SP11SB_CHUNK_ZERO) != 0;
        if (kind < 1 || kind > SP11SB_KIND_MAX) return SP11SB_E_CHUNK_KIND;
        if (flags & ~SP11SB_CHUNK_ZERO) return SP11SB_E_CHUNK_FLAGS;
        if (sp11sb_rd32(e + offsetof(struct sp11sb_chunk, reserved))) return SP11SB_E_CHUNK_RESERVED;
        if (zero ? (foff || !sp11sb_zero(digest, 32)) : sp11sb_zero(digest, 32)) return SP11SB_E_CHUNK_ZERO;
        if ((dest | size) & (SP11SB_DEST_ALIGN - 1) || !size) return SP11SB_E_CHUNK_GEOMETRY;
        if (dest < load_base || dest - load_base > load_size || size > load_size - (dest - load_base)) return SP11SB_E_CHUNK_RANGE;
        if (i && dest < prev_dest) return SP11SB_E_CHUNK_ORDER;
        if (i && dest < prev_end) return SP11SB_E_CHUNK_OVERLAP;
        if (!zero) {
            if (foff > file_size || size > file_size - foff) return SP11SB_E_CHUNK_EOF;
            aligned = (expect + SP11SB_DATA_ALIGN - 1) & ~(uint64_t)(SP11SB_DATA_ALIGN - 1);
            if (foff != aligned) return SP11SB_E_CHUNK_OFFSET;
            if (!sp11sb_zero(f + expect, aligned - expect)) return SP11SB_E_PAD_NONZERO;
            expect = foff + size;
        }
        seen[kind]++;
        prev_dest = dest;
        prev_end = dest + size;
    }
    if (expect != file_size) return SP11SB_E_GUEST_SIZE;                        /* bytes after the last chunk */
    if (seen[SP11SB_KIND_PARAMS] > 1 || seen[SP11SB_KIND_DEVICE_TREE] > 1) return SP11SB_E_CHUNK_DUP;
    if (!seen[SP11SB_KIND_KERNEL] || !seen[SP11SB_KIND_TABLES] || !seen[SP11SB_KIND_HANDOFF] || !seen[SP11SB_KIND_DEVICE_TREE] ||
        !seen[SP11SB_KIND_PARAMS]) return SP11SB_E_CHUNK_MISSING;
    return 0;
}
/* x3 of the monitor call: the destination of the one PARAMS chunk, or 0 when there is none (a file that passed
 * sp11sb_guest_check() has exactly one). */
static inline uint64_t sp11sb_params_dest(const uint8_t *f, uint64_t len)
{
    uint32_t i, count;
    if (!f || len < SP11SB_GUEST_HEAD_BYTES) return 0;
    count = SP11SB_GUEST_U32(f, entry_count);
    if (count > SP11SB_CHUNKS_MAX || SP11SB_GUEST_HEAD_BYTES + (uint64_t)count * SP11SB_CHUNK_BYTES > len) return 0;
    for (i = 0; i < count; i++)
        if (SP11SB_CHUNK_U16(SP11SB_CHUNK_AT(f, i), kind) == SP11SB_KIND_PARAMS) return SP11SB_CHUNK_U64(SP11SB_CHUNK_AT(f, i), dest);
    return 0;
}
/* The table digest is always checked; policy adds the per-chunk and whole-file digests. Safe on a file that failed the
 * structure check: every range is checked again before it is hashed. *bad_chunk (optional) names the failing entry. */
static inline int sp11sb_guest_digests(const uint8_t *f, uint64_t len, const uint8_t *pin, uint32_t policy, sp11sb_sha256_fn sha256,
                                       uint32_t *bad_chunk)
{
    uint8_t d[32];
    uint32_t count, i;
    if (policy & ~(SP11SB_HASH_CHUNKS | SP11SB_HASH_FILE)) return SP11SB_E_HASH_POLICY;
    if (!f || len < SP11SB_GUEST_HEAD_BYTES) return SP11SB_E_GUEST_TRUNCATED;
    count = SP11SB_GUEST_U32(f, entry_count);
    if (count < 1 || count > SP11SB_CHUNKS_MAX || SP11SB_GUEST_HEAD_BYTES + (uint64_t)count * SP11SB_CHUNK_BYTES > len) return SP11SB_E_GUEST_COUNT;
    sha256(f + SP11SB_GUEST_HEAD_BYTES, (uint64_t)count * SP11SB_CHUNK_BYTES, d);
    if (!sp11sb_same(d, f + offsetof(struct sp11sb_guest_head, index_sha256), 32)) return SP11SB_E_INDEX_DIGEST;
    for (i = 0; (policy & SP11SB_HASH_CHUNKS) && i < count; i++) {
        const uint8_t *e = SP11SB_CHUNK_AT(f, i);
        uint64_t size = SP11SB_CHUNK_U64(e, size), foff = SP11SB_CHUNK_U64(e, file_offset);
        if (SP11SB_CHUNK_U16(e, flags) & SP11SB_CHUNK_ZERO) continue;
        if (foff > len || size > len - foff) return SP11SB_E_CHUNK_EOF;
        sha256(f + foff, size, d);
        if (!sp11sb_same(d, e + offsetof(struct sp11sb_chunk, sha256), 32)) {
            if (bad_chunk) *bad_chunk = i;
            return SP11SB_E_CHUNK_DIGEST;
        }
    }
    if (policy & SP11SB_HASH_FILE) {
        sha256(f, len, d);
        if (!sp11sb_same(d, SP11SB_PIN_AT(pin, guest_file_sha256), 32)) return SP11SB_E_FILE_DIGEST;
    }
    return 0;
}

/* ---- REPORT.DAT as SNAPREP reads it. An all-zero first word is "never written" and the reporter says so; every other
 * failure is damage and names the check. The monitor's window is not looked at. */
#define SP11SB_REPORT_U32(b, f) sp11sb_rd32((b) + offsetof(struct sp11sb_report, f))
static inline int sp11sb_report_check(const uint8_t *b, uint64_t len)
{
    uint32_t stage, refusal, copied, total, hashed;
    if (!b || len != SP11SB_REPORT_BYTES) return SP11SB_E_REPORT_SIZE;
    if (!sp11sb_magic(b, SP11SB_REPORT_MAGIC)) return SP11SB_E_REPORT_MAGIC;
    if (SP11SB_REPORT_U32(b, version) != SP11SB_VERSION) return SP11SB_E_REPORT_VERSION;
    if (SP11SB_REPORT_U32(b, head_size) != SP11SB_REPORT_HEAD_BYTES) return SP11SB_E_REPORT_SIZE;
    if (!sp11sb_zero(b + SP11SB_REPORT_HEAD_BYTES, SP11SB_REPORT_HEAD_PAGE - SP11SB_REPORT_HEAD_BYTES)) return SP11SB_E_REPORT_RESERVED;
    stage = SP11SB_REPORT_U32(b, stage);
    refusal = SP11SB_REPORT_U32(b, refusal);
    total = SP11SB_REPORT_U32(b, chunks_total);
    copied = SP11SB_REPORT_U32(b, chunks_copied);
    hashed = SP11SB_REPORT_U32(b, chunks_hashed);
    if (stage > SP11SB_STAGE_DONE || (refusal && !sp11sb_error_known(refusal)) ||
        (SP11SB_REPORT_U32(b, hash_policy) & ~(SP11SB_HASH_CHUNKS | SP11SB_HASH_FILE)) ||
        SP11SB_REPORT_U32(b, lines_printed) > SP11SB_WRAPPER_LINES || total > SP11SB_CHUNKS_MAX || copied > total || hashed > total ||
        (SP11SB_REPORT_U32(b, pin_code) && !sp11sb_error_known(SP11SB_REPORT_U32(b, pin_code))) ||
        (SP11SB_REPORT_U32(b, guest_code) && !sp11sb_error_known(SP11SB_REPORT_U32(b, guest_code))) ||
        (refusal && stage > SP11SB_STAGE_MONITOR_ENTERED)) return SP11SB_E_REPORT_VALUE;   /* once the monitor ran, nothing is refused */
    return 0;
}

/* The wrapper's order. The wrapper calls these steps one at a time so it can write the stage between them; this composition
 * is what the host tool and the tests use, and it defines "first failing check". Every step reads and hashes only; nothing is
 * stored to the placement before all of them have passed. */
static inline int sp11sb_preflight(const struct sp11sb_env *e, uint32_t *bad_chunk)
{
    int r;
    if ((r = sp11sb_args_check(e->args_magic, e->args_version, e->args_size)) != 0) return r;
    if ((r = sp11sb_files_check(e->files, e->nfiles)) != 0) return r;
    if ((r = sp11sb_pin_check(e->pin, e->pin_len, e->sha256)) != 0) return r;
    if ((r = sp11sb_observed_check(e->pin, &e->obs)) != 0) return r;
    if ((r = sp11sb_place_check(e->pin, e->map, e->map_size, e->map_stride, e->obs.fb_base, e->obs.fb_bytes, e->declared,
                                e->ndeclared)) != 0) return r;
    if ((r = sp11sb_guest_check(e->guest, e->guest_len, e->pin)) != 0) return r;
    return sp11sb_guest_digests(e->guest, e->guest_len, e->pin, e->hash_policy, e->sha256, bad_chunk);
}

#endif /* SNAP_PROBES_SP11_STICK_BUNDLE_H */
