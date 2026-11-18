#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests of compat/nvme-takeover.{c,h}, the unit-side NVMe takeover (stage 1, read only).

    python3 tools/test-nvme-takeover.py            # needs clang; without it every native class reports SKIP, never PASS
    NVMET_ONLY=dirty python3 tools/test-nvme-takeover.py Mutants    # only the mutants whose name contains the text

What it runs. (1) The harness tools/test-nvme-takeover.c, built under ASan and UBSan, against the takeover model of
tools/nvme-takeover-model.h (included by tools/test-nvme-host.c: registers that follow the NVMe rules, an admin queue,
queue creation, a CPU cache over the DMA extent, fault injection); every case must report ok. (2) The old tests of
tools/test-nvme-host.c, built with the model extension off, with SNAP_ANS_ASYNC and with the model on, still pass. (3) The
gate: with SNAP_NVME_TAKEOVER off (the default) the unit compiles to an object with no symbol, and the gate refuses a build
without SNAP_PLATFORM_SP11. (4) The unit is freestanding: an aarch64 object has no undefined symbol but the shared admin
allow-list. (5) A source tripwire, not a proof: the only I/O opcode the unit names is Read and no Write, Flush, Dataset
Management, Format, Sanitize or Firmware word is in its code. It can be walked past (an opcode built arithmetically, a
function that stores an SQE by itself); what keeps the unit read only is cmd(), the one command builder, running both gates
before it stores anything, and the model's counters in (1), which fail on any opcode outside the lists. (6) Seeded
mutants: each lets one of the faults of the harness through, and the harness must fail, in the case that owns the fault (a
mutant that makes the harness hang is killed by the timeout). Mutants that an independent review of the unit wrote are
folded in; their names say what they change. Nothing here touches a device. Hardware: NOT RUN."""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'test-nvme-takeover'
CLANG = shutil.which('clang') or '/opt/homebrew/opt/llvm/bin/clang'
NM = shutil.which('llvm-nm') or '/opt/homebrew/opt/llvm/bin/llvm-nm'
SAN = ['-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-Wall', '-Wextra', '-Werror',
       '-Wno-unused-function']
DEFS = ['-DSNAP_PLATFORM_SP11=1', '-DSNAP_NVME_TAKEOVER=1', '-DNVMET_TEST_HOOKS=1']
HOST_SOURCES = ['compat/nvme-host.c', 'compat/board-info.c', 'compat/ans-window.c']
C, H = 'compat/nvme-takeover.c', 'compat/nvme-takeover.h'
MODEL = 'tools/nvme-takeover-model.h'
HANG_SECONDS = 60
CASES = ('healthy', 'handoff', 'initial-states', 'cap-refusals', 'rdy-never', 'rdy-stuck', 'cfs-bringup', 'wrong-phase', 'queue-wrap',
         'cid', 'duplicate', 'queue-id', 'status', 'prp', 'lba-size', 'identify-zeros', 'identify-fields', 'eui64', 'opcode-gate',
         'read-range', 'timeouts', 'clock-stuck', 'gone', 'cfs-in-command', 'config', 'stages', 'bus-translate', 'dirty-cache',
         'shn-field', 'fw-queues', 'high-lba', 'cid-wrap', 'latency', 'spec-fill', 'adopt-refused', 'timing')

# (name, [(file, old, new)], the cases that must fail, or 'hang'). Every `old` must occur exactly once.
M = []


def mutant(name, edits, cases):
    M.append((name, edits, cases))


def c(old, new, cases, name):
    mutant(name, [(C, old, new)], cases)


# RDY never set, RDY stuck, CFS during bring-up
c('e = step(o, t, wait_csts(o, t, CSTS_RDY, CSTS_RDY, t->rdy_ticks, NVMET_E_T_RDY, 1));', 'e = step(o, t, 0);',
  {'rdy-never', 'timeouts', 'cfs-bringup'}, 'the-enable-does-not-wait-for-ready')
c('        if (NOW(o) - start > limit) return timeout_error;\n', '', {'rdy-never', 'timeouts'}, 'a-register-wait-has-no-timeout')
c('        t->flags |= NVMET_F_REGS_WRITTEN;\n        e = wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0);\n'
  '        if (e) return fatal(o, t, e);\n    } else if', '        t->flags |= NVMET_F_REGS_WRITTEN;\n    } else if', {'rdy-stuck'},
  'the-quiesce-does-not-wait-for-ready-to-clear')
c('    if (!e) t->flags &= ~(uint64_t)(NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE);\n',
  '    t->flags &= ~(uint64_t)(NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE);\n', {'cfs-bringup', 'timeouts', 'fw-queues'},
  'a-disable-that-is-not-seen-clears-the-live-flags')
c('        if (t->flags & NVMET_F_DMA_LIVE) disable(o, t);', '        if (0) disable(o, t);', {'rdy-never', 'cfs-bringup', 'wrong-phase', 'timeouts'},
  'a-failure-leaves-the-controller-enabled')
c('    t->flags |= NVMET_F_DMA_LIVE;                              /* live from the write: its effect is not yet known */\n', '',
  {'healthy', 'timeouts'}, 'the-controller-is-never-marked-live')
c('        if (cfs_is_fatal && (csts & CSTS_CFS)) return NVMET_E_CFS;\n', '', {'cfs-bringup'}, 'cfs-is-not-fatal-while-enabling')
c('    if (csts & CSTS_CFS) return fatal(o, t, NVMET_E_FATAL_ENTRY);\n', '', {'initial-states'}, 'a-fatal-controller-on-arrival-is-reset')
c('        if (csts == 0xffffffffu) return NVMET_E_GONE;\n        if (cfs_is_fatal', '        if (cfs_is_fatal', {'gone'},
  'all-ones-is-not-checked-in-a-wait')
# the controller's capabilities
c('    if ((cap & 0xffff) + 1 < NVMEH_IO_ENTRIES) return fatal(o, t, NVMET_E_MQES);\n', '', {'cap-refusals'}, 'mqes-is-not-checked')
c('if ((cap & 0xffff) + 1 < NVMEH_IO_ENTRIES)', 'if ((cap & 0xffff) < NVMEH_IO_ENTRIES)', {'cap-refusals'}, 'mqes-is-read-as-a-count')
c('if (!((cap >> 37) & 1) || ((cap >> 48) & 0xf) || t->stride > 4)', 'if (((cap >> 48) & 0xf) || t->stride > 4)', {'cap-refusals'},
  'the-nvm-command-set-is-not-required')
c('if (!((cap >> 37) & 1) || ((cap >> 48) & 0xf) || t->stride > 4)', 'if (!((cap >> 37) & 1) || t->stride > 4)', {'cap-refusals'},
  'a-page-size-above-4k-is-accepted')
c('if (!((cap >> 37) & 1) || ((cap >> 48) & 0xf) || t->stride > 4)', 'if (!((cap >> 37) & 1) || ((cap >> 48) & 0xf))', {'cap-refusals'},
  'a-doorbell-stride-above-4-is-accepted')
c('    if ((uint32_t)cap == 0xffffffffu || (uint32_t)(cap >> 32) == 0xffffffffu) return fatal(o, t, NVMET_E_GONE);\n', '', {'cap-refusals'},
  'an-all-ones-cap-is-a-controller')
c('        if (csts & CSTS_RDY) {\n            /* The firmware', '        if (0) {\n            /* The firmware', {'healthy', 'initial-states'},
  'no-normal-shutdown-before-the-reset')
# phase, wrap, completion identity
c('        if (((dw3 >> 16) & 1) == q->phase) break;              /* an entry of the other phase is not an entry */', '        break;',
  {'healthy', 'wrong-phase'}, 'the-phase-bit-is-ignored')
c('    if (!q->head) q->phase ^= 1;\n', '', {'queue-wrap'}, 'the-phase-does-not-toggle-on-wrap')
c('    q->tail = (q->tail + 1) % q->entries;\n    t->last_opcode = opcode;', '    q->tail = q->tail + 1;\n    t->last_opcode = opcode;', {'queue-wrap'},
  'the-submission-tail-does-not-wrap')
c('    q->head = (q->head + 1) % q->entries;\n    if (!q->head)', '    q->head = q->head + 1;\n    if (!q->head)', {'queue-wrap'},
  'the-completion-head-does-not-wrap')
c('    if ((dw3 & 0xffff) != cid) return fatal(o, t, NVMET_E_CID);\n', '', {'cid'}, 'a-completion-for-another-command-is-accepted')
c('    if (dup) return fatal(o, t, NVMET_E_DUP);\n', '', {'duplicate'}, 'a-duplicate-completion-is-accepted')
c('    if ((dw2 >> 16) != q->qid || (dw2 & 0xffff) >= q->entries) return fatal(o, t, NVMET_E_QID);\n', '', {'queue-id'},
  'a-completion-of-another-queue-is-accepted')
c('    if (status) {\n        t->errors++;', '    if (0) {\n        t->errors++;', {'status'}, 'an-error-status-is-success')
# PRPs
c('    if ((prp1 && !in_extent(t, prp1, 1)) || (prp2 && !in_extent(t, prp2, 1))) {', '    if (0) {', {'prp'}, 'a-prp-outside-the-extent-is-submitted')
c('|| buf_off > NVMEH_DATA_BYTES || bytes > NVMEH_DATA_BYTES - buf_off)', ')', {'prp'}, 'a-transfer-may-leave-the-data-buffer')
c('(buf_off & (NVMET_PAGE - 1)) || ', '', {'prp'}, 'an-unaligned-prp-is-built')
c('if (!bytes || (buf_off', 'if ((buf_off', {'prp'}, 'an-empty-transfer-is-built')
c('    *prp1 = bus(t, NVMEH_DATA + buf_off);', '    *prp1 = cpu(t, NVMEH_DATA + buf_off);', {'bus-translate'}, 'the-prp-is-a-cpu-address')
c('    wr64(o, t->bar + NVMEH_ASQ_REG, bus(t, NVMEH_ASQ));', '    wr64(o, t->bar + NVMEH_ASQ_REG, cpu(t, NVMEH_ASQ));', {'bus-translate'},
  'the-admin-queue-is-programmed-with-a-cpu-address')
# the namespace
c('if (t->lba_shift != 9) return NVMET_E_LBA_SIZE;', '', {'lba-size'}, 'a-4096-byte-namespace-is-accepted')
c('if (t->lba_shift != 9) return NVMET_E_LBA_SIZE;', 'if (t->lba_shift < 9) return NVMET_E_LBA_SIZE;', {'lba-size'},
  'only-smaller-lba-sizes-are-refused')
c('if (t->metadata) return NVMET_E_METADATA;', '', {'lba-size'}, 'a-format-with-metadata-is-accepted')
c('    if (t->eui64_pin && t->eui64 != t->eui64_pin) return NVMET_E_EUI64;\n', '', {'eui64'}, 'another-namespace-is-accepted')
c('    t->eui64 = id64_be(o, t, 120);\n', '    t->eui64 = id64(o, t, 120);\n', {'healthy', 'eui64'}, 'the-eui64-is-read-in-the-wrong-byte-order')
c('    return i == NVMET_PAGE / 4 ? NVMET_E_ID_ZERO : 0;', '    return 0;', {'identify-zeros'}, 'an-all-zero-identify-is-not-refused-as-such')
mutant('an-all-zero-identify-is-accepted', [
    (C, '    return i == NVMET_PAGE / 4 ? NVMET_E_ID_ZERO : 0;', '    return 0;'),
    (C, '    if (!e) e = parse_controller(o, t);', '    if (!e) parse_controller(o, t);'),
    (C, '    if (!e) e = parse_namespace(o, t);', '    if (!e) parse_namespace(o, t);')], {'identify-zeros', 'identify-fields', 'lba-size'})
c('    zero_page(o, t, NVMET_IDENT);\n    e = cmd(', '    e = cmd(', {'identify-zeros'}, 'the-identify-page-is-not-cleared-first')
c('    if (!t->nn) return NVMET_E_ID_CTRL;\n', '', {'identify-fields'}, 'a-controller-without-namespaces-is-accepted')
c('(t->sqes & 0xf) > 6 || (t->sqes >> 4) < 6 || ', '', {'identify-fields'}, 'entry-sizes-are-not-checked')
c('if (!t->nsze || t->nsze > 1ull << 55 || idx > t->nlbaf) return NVMET_E_ID_NS;', 'if (idx > t->nlbaf) return NVMET_E_ID_NS;',
  {'identify-fields'}, 'an-empty-namespace-is-accepted')
c('if (!t->nsze || t->nsze > 1ull << 55 || idx > t->nlbaf) return NVMET_E_ID_NS;', 'if (!t->nsze || t->nsze > 1ull << 55) return NVMET_E_ID_NS;',
  {'identify-fields'}, 'a-format-index-past-nlbaf-is-used')
c('    if (limit < t->max_transfer) t->max_transfer = limit;\n', '', {'identify-fields'}, 'mdts-does-not-cap-the-transfer')
c('t->hmpre = id32(o, t, 272);', 't->hmpre = id32(o, t, 276);', {'healthy'}, 'the-host-memory-sizes-are-read-from-the-wrong-offset')
# opcodes
c('return opcode == NVMET_IO_READ ? 0 : NVMET_E_OPCODE;', 'return opcode <= NVMET_IO_READ ? 0 : NVMET_E_OPCODE;', {'opcode-gate'},
  'the-io-gate-accepts-flush-and-write')
c('if (q->qid ? nvmet_io_gate(opcode) : nvmeh_sp11_admin_verdict(', 'if (q->qid ? 0 : nvmeh_sp11_admin_verdict(', {'opcode-gate'},
  'the-io-gate-is-not-consulted')
c(': nvmeh_sp11_admin_verdict(NVMEH_SP11_PHASE_TAKEOVER, opcode, cdw[0])) {', ': 0) {', {'opcode-gate'}, 'the-admin-gate-is-not-consulted')
# reads
c('if (!count || bytes > t->max_transfer || lba >= t->nsze || count > t->nsze - lba) {', 'if (!count || bytes > t->max_transfer) {', {'read-range'},
  'a-read-beyond-the-namespace-is-sent')
c('if (!count || bytes > t->max_transfer || lba >= t->nsze || count > t->nsze - lba) {',
  'if (!count || bytes > t->max_transfer || lba + count > t->nsze) {', {'read-range'}, 'the-end-of-a-read-wraps')
c('if (!count || bytes > t->max_transfer || lba >= t->nsze', 'if (bytes > t->max_transfer || lba >= t->nsze', {'read-range'}, 'an-empty-read-is-sent')
c('if (!count || bytes > t->max_transfer || lba >= t->nsze', 'if (!count || lba >= t->nsze', {'read-range', 'identify-fields'},
  'the-transfer-cap-is-not-applied')
c('    if (t->stage != NVMET_ST_READY) return wrong_stage(t);\n    /* Reads are bounded', '    /* Reads are bounded', {'healthy', 'stages'},
  'a-read-needs-no-stage')
# timeouts, bounded waits
c('t->shutdown_ticks, NVMET_E_T_SHUTDOWN, 0);\n            if (e) return fatal(o, t, e);', '~0ull, NVMET_E_T_SHUTDOWN, 0);\n            if (e) return fatal(o, t, e);',
  {'timeouts'}, 'the-first-shutdown-wait-is-unbounded')
c('        if (NOW(o) - start > t->cmd_ticks) {', '        if (0) {', {'timeouts', 'wrong-phase'}, 'a-command-wait-has-no-timeout')
mutant('the-wait-has-no-poll-cap', [(C, '    for (uint32_t i = 0; i < NVMET_POLL_MAX; i++) {', '    for (uint32_t i = 0;; i++) {')], 'hang')
mutant('the-command-has-no-poll-cap', [(C, '        if (n >= NVMET_POLL_MAX) return fatal(o, t, NVMET_E_CLOCK);\n', '')], 'hang')
c('t->csts == 0xffffffffu ? NVMET_E_GONE : (t->csts & CSTS_CFS) ? NVMET_E_CFS : NVMET_E_T_CMD',
  '(t->csts & CSTS_CFS) ? NVMET_E_CFS : NVMET_E_T_CMD', {'gone'}, 'a-vanished-device-is-a-timeout')
c('t->csts == 0xffffffffu ? NVMET_E_GONE : (t->csts & CSTS_CFS) ? NVMET_E_CFS : NVMET_E_T_CMD',
  't->csts == 0xffffffffu ? NVMET_E_GONE : NVMET_E_T_CMD', {'cfs-in-command'}, 'a-fatal-controller-is-a-timeout')
# doorbells
c('    WR(o, doorbell(t, 2 * q->qid), q->tail);', '    WR(o, doorbell(t, 2 * q->qid + 1), q->tail);', {'healthy'}, 'the-sq-tail-rings-the-cq-head-doorbell')
c('    WR(o, doorbell(t, 2 * q->qid + 1), q->head);', '    WR(o, doorbell(t, 2 * q->qid), q->head);', {'healthy'}, 'the-cq-head-rings-the-sq-tail-doorbell')
c('    WR(o, doorbell(t, 2 * q->qid), q->tail);', '    WR(o, doorbell(t, 0), q->tail);', {'healthy'}, 'every-queue-rings-the-admin-doorbell')
c('(uint64_t)index * (4u << t->stride)', '(uint64_t)index * 4u', {'cap-refusals'}, 'the-doorbell-stride-is-ignored')
# register values and order
mutant('the-wrong-cc-value', [(H, '#define NVMET_CC_ENABLE 0x00460001u', '#define NVMET_CC_ENABLE 0x00450001u')], {'healthy'})
c('    for (uint64_t off = NVMEH_ASQ; off <= NVMET_IDENT; off += NVMET_PAGE) zero_page(o, t, off);\n', '', {'healthy'}, 'the-queues-are-not-cleared')
c('        e = cmd(o, t, &t->adm, NVMET_ADMIN_DELETE_SQ, 0, 0, 0, qid, 0);\n        if (!e) e = cmd(o, t, &t->adm, NVMET_ADMIN_DELETE_CQ, 0, 0, 0, qid, 0);',
  '        e = cmd(o, t, &t->adm, NVMET_ADMIN_DELETE_CQ, 0, 0, 0, qid, 0);\n        if (!e) e = cmd(o, t, &t->adm, NVMET_ADMIN_DELETE_SQ, 0, 0, 0, qid, 0);',
  {'healthy'}, 'the-cq-is-deleted-before-its-sq')
# the CPU's cache
c('    CLEAN(o, sqe, 64);', '', {'healthy'}, 'the-sqe-is-not-cleaned-before-the-bell')
c('        CLEAN(o, cqe, 16);\n        dw3 = RD(o, cqe + 12);', '        dw3 = RD(o, cqe + 12);', {'healthy', 'timeouts'}, 'the-completion-is-not-invalidated')
c('    for (uint64_t i = 0; i < NVMET_PAGE; i += 4) WR(o, cpu(t, off + i), 0);\n    CLEAN(o, cpu(t, off), NVMET_PAGE);',
  '    for (uint64_t i = 0; i < NVMET_PAGE; i += 4) WR(o, cpu(t, off + i), 0);', {'healthy'}, 'a-cleared-page-is-not-written-back')
c('    CLEAN(o, cpu(t, NVMEH_DATA), bytes);                       /* drop lines fetched before the DMA landed */', '', {'spec-fill'},
  'read-data-is-not-invalidated')
c('        CLEAN(o, cpu(t, NVMEH_PRP), 8 * (pages - 1));\n', '', {'healthy'}, 'the-prp-list-is-not-cleaned')
# the DMA extent holds a loader's dirty lines, and a write-back after the controller's DMA replaces its data
c('    CLEAN(o, cpu(t, NVMEH_DATA), bytes);\n    e = cmd(o, t, &t->io,', '    e = cmd(o, t, &t->io,', {'dirty-cache'}, 'a-read-does-not-clean-its-buffer-first')
c('    CLEAN(o, cpu(t, NVMEH_PRP), NVMEH_DATA + NVMEH_DATA_BYTES - NVMEH_PRP);\n', '', {'dirty-cache'},
  'the-enable-does-not-clean-the-prp-page-and-the-buffer')
# the firmware may have left SHN 10b, and or-ing 01b into it writes the reserved 11b
c('(cc & ~NVMET_CC_SHN_MASK) | NVMET_CC_SHN_NORMAL', 'cc | NVMET_CC_SHN_NORMAL', {'shn-field'}, 'the-shutdown-field-is-or-ed-not-replaced')
# the flags say which memory the controller may still use; nvmet_abort retries the reset
c('    if ((cc & 1) || (csts & CSTS_RDY)) t->flags |= NVMET_F_FW_QUEUES_LIVE;\n',
  '', {'rdy-stuck', 'fw-queues', 'timeouts'}, 'the-firmwares-queues-are-never-marked-live')
c('    t->flags &= ~(uint64_t)NVMET_F_FW_QUEUES_LIVE;             /* RDY 0 was seen: the firmware\'s queues are nobody\'s now */\n', '',
  {'healthy'}, 'the-firmwares-queues-stay-marked-live-after-a-good-quiesce')
c('    if ((cc & 1) || (csts & CSTS_RDY)) t->flags |= NVMET_F_FW_QUEUES_LIVE;', '    if (csts & CSTS_RDY) t->flags |= NVMET_F_FW_QUEUES_LIVE;',
  {'fw-queues'}, 'an-enabled-controller-that-is-not-ready-is-not-marked-live')
c('    if ((cc & 1) || (csts & CSTS_RDY)) t->flags |= NVMET_F_FW_QUEUES_LIVE;', '    if (cc & 1) t->flags |= NVMET_F_FW_QUEUES_LIVE;',
  {'fw-queues'}, 'a-reset-that-has-not-finished-is-not-marked-live')
c('    if (t->flags & (NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE)) e = disable(o, t);', '    if (0) e = disable(o, t);', {'fw-queues'},
  'an-abort-does-not-retry-the-reset')
c('    if (t->flags & (NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE)) e = disable(o, t);', '    if (t->flags & NVMET_F_DMA_LIVE) e = disable(o, t);',
  {'fw-queues'}, 'an-abort-ignores-the-firmwares-queues')
c('    if (t->stage != NVMET_ST_STOPPED) t->stage = NVMET_ST_FAILED;\n    return e;', '    t->stage = NVMET_ST_FAILED;\n    return e;', {'fw-queues'},
  'an-abort-reopens-a-stopped-unit-as-failed')
# a translating SMMU must be declared, and the hand-off closes the unit
c('        (!!(c->flags & NVMET_C_TRANSLATED) != (c->bus_base != c->cpu_base)) ||\n', '', {'config'}, 'a-mistyped-bus-address-is-a-translating-smmu')
c('(!!(c->flags & NVMET_C_TRANSLATED) != (c->bus_base != c->cpu_base))', '(!!(c->flags & NVMET_C_TRANSLATED) && c->bus_base == c->cpu_base)',
  {'config'}, 'a-different-bus-address-needs-no-translated-flag')
c('(!!(c->flags & NVMET_C_TRANSLATED) != (c->bus_base != c->cpu_base))', '(!(c->flags & NVMET_C_TRANSLATED) && c->bus_base != c->cpu_base)',
  {'config'}, 'the-translated-flag-is-accepted-with-equal-addresses')
c('    t->stage = NVMET_ST_HANDED_OFF;\n    return 0;', '    return 0;', {'handoff'}, 'the-hand-off-leaves-the-unit-ready')
c(' || t->stage == NVMET_ST_HANDED_OFF ? NVMET_E_DEAD', ' ? NVMET_E_DEAD', {'handoff'}, 'a-handed-off-unit-answers-with-a-wrong-stage-not-dead')
# after a refused adoption only nvmet_abort can still disable the controller, so it must stay open
c('int nvmet_abort(const struct nvmeh_ops *o, struct nvmet *t)\n{\n    int e = 0;\n',
  'int nvmet_abort(const struct nvmeh_ops *o, struct nvmet *t)\n{\n    int e = 0;\n    if (t->stage == NVMET_ST_HANDED_OFF) return NVMET_E_DEAD;\n',
  {'adopt-refused'}, 'an-abort-after-the-hand-off-is-refused')
# every refusal of the quiesce leaves the firmware's controller running, and the flag must say so
FLAG = '    if ((cc & 1) || (csts & CSTS_RDY)) t->flags |= NVMET_F_FW_QUEUES_LIVE;\n'
mutant('the-flag-is-set-only-after-the-cap-refusals', [
    (C, FLAG, ''), (C, '    if (csts == 0xffffffffu) return fatal(o, t, NVMET_E_GONE);\n', FLAG + '    if (csts == 0xffffffffu) return fatal(o, t, NVMET_E_GONE);\n')],
    {'cap-refusals'})
mutant('the-flag-is-set-only-after-the-fatal-entry-refusal', [
    (C, FLAG, ''), (C, '    if (csts & CSTS_CFS) return fatal(o, t, NVMET_E_FATAL_ENTRY);\n', '    if (csts & CSTS_CFS) return fatal(o, t, NVMET_E_FATAL_ENTRY);\n' + FLAG)],
    {'initial-states'})
mutant('an-unreadable-controller-is-not-marked-live', [
    (C, FLAG, ''), (C, '(uint32_t)(cap >> 32) == 0xffffffffu) return fatal(o, t, NVMET_E_GONE);\n', '(uint32_t)(cap >> 32) == 0xffffffffu) return fatal(o, t, NVMET_E_GONE);\n' + FLAG)],
    {'cap-refusals'})
c(FLAG, '    t->flags |= NVMET_F_FW_QUEUES_LIVE;\n', {'initial-states', 'cap-refusals'}, 'the-flag-is-set-for-a-controller-that-is-off')
# the pre-read clean spans exactly the bytes a read lands on, and every wait is bounded from both sides
PRE = '    CLEAN(o, cpu(t, NVMEH_DATA), bytes);\n    e = cmd(o, t, &t->io,'
c(PRE, '    CLEAN(o, cpu(t, NVMEH_DATA), NVMET_PAGE);\n    e = cmd(o, t, &t->io,', {'dirty-cache'}, 'the-pre-read-clean-covers-only-the-first-page')
c(PRE, '    CLEAN(o, cpu(t, NVMEH_DATA), bytes & ~(uint64_t)(NVMET_PAGE - 1));\n    e = cmd(o, t, &t->io,', {'dirty-cache'},
  'the-pre-read-clean-rounds-down-to-whole-pages')
c(PRE, '    CLEAN(o, cpu(t, NVMEH_DATA), bytes - 64);\n    e = cmd(o, t, &t->io,', {'dirty-cache'}, 'the-pre-read-clean-stops-one-line-short')
c('CLEAN(o, cpu(t, NVMEH_PRP), NVMEH_DATA + NVMEH_DATA_BYTES - NVMEH_PRP);', 'CLEAN(o, cpu(t, NVMEH_PRP), NVMET_PAGE);', {'dirty-cache'},
  'the-enable-cleans-only-the-prp-page')
mutant('the-shutdown-timeout-is-1-s', [(H, 'NVMET_SHUTDOWN_SECONDS 10u', 'NVMET_SHUTDOWN_SECONDS 1u')], {'timing'})
c('    e = wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0);\n    if (!e) t->flags',
  '    e = wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks / 1000, NVMET_E_T_QUIESCE, 0);\n    if (!e) t->flags', {'timing', 'fw-queues'},
  'the-disable-waits-a-thousandth-of-cap-to')
c('    e = step(o, t, wait_csts(o, t, CSTS_SHST_MASK, CSTS_SHST_DONE, t->shutdown_ticks, NVMET_E_T_SHUTDOWN, 0));',
  '    e = step(o, t, wait_csts(o, t, CSTS_SHST_MASK, CSTS_SHST_DONE, t->rdy_ticks, NVMET_E_T_SHUTDOWN, 0));', {'timing'},
  'the-stop-waits-for-the-shutdown-as-long-as-for-a-reset')
c('wait_csts(o, t, CSTS_SHST_MASK, CSTS_SHST_DONE, t->shutdown_ticks, NVMET_E_T_SHUTDOWN, 0);\n            if (e) return fatal(o, t, e);',
  'wait_csts(o, t, CSTS_SHST_MASK, CSTS_SHST_DONE, t->rdy_ticks, NVMET_E_T_SHUTDOWN, 0);\n            if (e) return fatal(o, t, e);', {'timing'},
  'the-quiesce-waits-for-the-shutdown-as-long-as-for-a-reset')
c('e = step(o, t, wait_csts(o, t, CSTS_RDY, CSTS_RDY, t->rdy_ticks, NVMET_E_T_RDY, 1));',
  'e = step(o, t, wait_csts(o, t, CSTS_RDY, CSTS_RDY, t->rdy_ticks / 1000, NVMET_E_T_RDY, 1));', {'timing'}, 'the-enable-waits-a-thousandth-of-cap-to')
c('        e = wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0);\n        if (e) return fatal(o, t, e);\n    } else if',
  '        e = wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks / 1000, NVMET_E_T_QUIESCE, 0);\n        if (e) return fatal(o, t, e);\n    } else if', {'timing'},
  'the-quiesce-waits-a-thousandth-of-cap-to-for-the-reset')
c('    e = step(o, t, wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0));',
  '    e = step(o, t, wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks / 1000, NVMET_E_T_QUIESCE, 0));', {'timing'},
  'the-stop-waits-a-thousandth-of-cap-to-for-the-reset')
mutant('the-command-timeout-is-4-s', [(H, 'NVMET_CMD_SECONDS 5u', 'NVMET_CMD_SECONDS 4u')], {'latency'})
mutant('the-command-timeout-is-6-s', [(H, 'NVMET_CMD_SECONDS 5u', 'NVMET_CMD_SECONDS 6u')], {'latency'})
c('    q->next_cid = q->next_cid % 0xffff + 1;', '    q->next_cid = q->next_cid % 0x10000 + 1;', {'cid-wrap'}, 'the-command-identifier-wraps-at-0x10000')
c('    if ((dw3 & 0xffff) != cid) return fatal(o, t, NVMET_E_CID);', '    if ((dw3 & 0x3f) != (cid & 0x3f)) return fatal(o, t, NVMET_E_CID);', {'cid'},
  'only-the-low-6-bits-of-the-completion-cid-are-compared')
c('(cc & ~NVMET_CC_SHN_MASK) | NVMET_CC_SHN_NORMAL', '(cc & ~(1u << 14)) | NVMET_CC_SHN_NORMAL', {'shn-field'}, 'the-shutdown-field-clears-bit-14-only')
c('    if (t->stage != NVMET_ST_STOPPED) t->stage = NVMET_ST_FAILED;\n    return e;', '    if (e && t->stage != NVMET_ST_STOPPED) t->stage = NVMET_ST_FAILED;\n    return e;',
  {'fw-queues'}, 'an-abort-leaves-a-healthy-unit-open')
c('    if (!e) t->flags &= ~(uint64_t)(NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE);\n', '    if (!e) t->flags &= ~(uint64_t)NVMET_F_DMA_LIVE;\n', {'fw-queues'},
  'a-seen-reset-clears-only-the-extent-flag')
mutant('the-flag-stays-after-a-reset-in-progress-is-seen-clear', [
    (C, '    t->flags &= ~(uint64_t)NVMET_F_FW_QUEUES_LIVE;             /* RDY 0 was seen: the firmware\'s queues are nobody\'s now */\n', ''),
    (C, '        if (e) return fatal(o, t, e);\n    } else if (csts & CSTS_RDY) {', '        if (e) return fatal(o, t, e);\n        t->flags &= ~(uint64_t)NVMET_F_FW_QUEUES_LIVE;\n    } else if (csts & CSTS_RDY) {')],
    {'initial-states'})
c('    } else if (csts & CSTS_RDY) {                              /* a reset that has not finished */\n',
  '    } else if (csts & CSTS_RDY) {                              /* a reset that has not finished */\n        t->flags &= ~(uint64_t)NVMET_F_FW_QUEUES_LIVE;\n', {'fw-queues'},
  'the-flag-is-cleared-before-a-reset-in-progress-is-waited-for')
mutant('an-unknown-config-flag-bit-is-accepted', [(H, '#define NVMET_C_ALL 3u', '#define NVMET_C_ALL 7u')], {'config'})
c('    if (t->stage != NVMET_ST_READY) return wrong_stage(t);\n    if (t->bus_base != t->cpu_base) return NVMET_E_NOT_IDENTITY;',
  '    if (t->stage != NVMET_ST_READY) return wrong_stage(t);\n    t->stage = NVMET_ST_HANDED_OFF;\n    if (t->bus_base != t->cpu_base) return NVMET_E_NOT_IDENTITY;',
  {'bus-translate', 'handoff'}, 'the-hand-off-closes-the-unit-before-the-identity-check')
c('    t->stage = NVMET_ST_HANDED_OFF;\n    return 0;', '    t->stage = NVMET_ST_HANDED_OFF;\n    t->flags &= ~(uint64_t)NVMET_F_DMA_LIVE;\n    return 0;', {'handoff', 'adopt-refused'},
  'the-hand-off-clears-the-live-flag')
c('    if (t->flags & (NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE)) e = disable(o, t);', '    e = disable(o, t);', {'fw-queues'}, 'an-abort-writes-cc-even-when-nothing-is-live')
c('    if (t->flags & (NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE)) e = disable(o, t);',
  '    if (t->flags & (NVMET_F_DMA_LIVE | NVMET_F_FW_QUEUES_LIVE)) { (void)disable(o, t); e = 0; }', {'fw-queues'}, 'an-abort-ignores-a-failed-wait')
# faults an independent review of the unit found or seeded; the ones that survived an earlier harness come with the case
# that now kills them
c('    return opcode == NVMET_IO_READ ? 0 : NVMET_E_OPCODE;', '    return opcode == NVMET_IO_READ || opcode == 0x01u ? 0 : NVMET_E_OPCODE;', {'opcode-gate'},
  'the-io-gate-also-accepts-opcode-1')
c('    WR(o, t->bar + NVMEH_CC, NVMET_CC_ENABLE);\n    e = step(', '    WR(o, t->bar + 0x0c, 0xffffffffu);\n    WR(o, t->bar + NVMEH_CC, NVMET_CC_ENABLE);\n    e = step(',
  {'healthy'}, 'intms-is-written-at-enable')
c('    e = step(o, t, e);\n    if (e) return e;\n    t->io = (struct nvmet_queue)',
  '    { const uint32_t lp[6] = {0x007f0002u, 0, 0, 0, 0, 0};\n      if (!e) e = cmd(o, t, &t->adm, 0x02u, 0xffffffffu, bus(t, NVMET_IDENT), 0, lp, 0); }\n'
  '    e = step(o, t, e);\n    if (e) return e;\n    t->io = (struct nvmet_queue)', {'healthy'}, 'an-extra-get-log-page-is-sent')
c('nvmeh_sp11_admin_verdict(NVMEH_SP11_PHASE_TAKEOVER, opcode, cdw[0])', 'nvmeh_sp11_admin_verdict(NVMEH_SP11_PHASE_TAKEOVER, opcode, cdw[1])',
  {'healthy', 'opcode-gate'}, 'the-admin-gate-sees-cdw11-not-cdw10')
c('bytes > t->max_transfer || lba >= t->nsze', 'bytes >= t->max_transfer || lba >= t->nsze', {'read-range'}, 'the-transfer-cap-is-off-by-one')
c('lba >= t->nsze || count > t->nsze - lba) {', 'lba >= t->nsze || count > t->nsze - lba + 1) {', {'read-range'},
  'a-read-may-end-one-block-past-the-namespace')
c('{(uint32_t)lba, (uint32_t)(lba >> 32), count - 1, 0, 0, 0}', '{(uint32_t)lba, 0, count - 1, 0, 0, 0}', {'high-lba'}, 'the-slba-high-dword-is-dropped')
c('bus(t, NVMEH_DATA + NVMET_PAGE * i));', 'bus(t, NVMEH_DATA + NVMET_PAGE * i) + 4);', {'healthy', 'read-range'}, 'a-prp-list-entry-is-misaligned-by-4')
c('*prp2 = pages == 2 ? *prp1 + NVMET_PAGE : pages > 2 ? bus(t, NVMEH_PRP) : 0;', '*prp2 = pages >= 2 ? bus(t, NVMEH_PRP) : 0;', {'healthy', 'prp'},
  'a-two-page-read-uses-the-prp-list')
c('    if (t->nlbaf > 15) idx |= (uint64_t)((t->flbas >> 5) & 3) << 4;   /* the upper index bits exist past 16 formats */',
  '    idx |= (uint64_t)((t->flbas >> 5) & 3) << 4;', {'identify-fields'}, 'the-reserved-flbas-bits-always-extend-the-format-index')
c('(dw2 & 0xffff) >= q->entries', '(dw2 & 0xffff) > q->entries', {'queue-id'}, 'a-head-pointer-equal-to-the-queue-size-is-accepted')
c('    status = (dw3 >> 17) & 0x7fff;', '    status = (dw3 >> 17) & 0xff;', {'status'}, 'the-status-keeps-only-the-code-byte')
c('if (!((cap >> 37) & 1) || ((cap >> 48) & 0xf) || t->stride > 4)', 'if (!((cap >> 37) & 1) || ((cap >> 48) & 0xf) || t->stride > 3)',
  {'cap-refusals'}, 'a-doorbell-stride-of-4-is-refused')
c('for (uint64_t off = NVMEH_ASQ; off <= NVMET_IDENT; off += NVMET_PAGE) zero_page(o, t, off);',
  'for (uint64_t off = NVMEH_ASQ; off <= NVMEH_IOSQ; off += NVMET_PAGE) zero_page(o, t, off);', {'healthy'}, 'the-queue-clearing-stops-before-the-io-cq')
c('    if (!q->head) q->phase ^= 1;\n    WR(o, doorbell(t, 2 * q->qid + 1), q->head);',
  '    if (!q->head) { static unsigned wraps; if (q->qid != 1 || ++wraps != 2) q->phase ^= 1; }\n    WR(o, doorbell(t, 2 * q->qid + 1), q->head);',
  {'queue-wrap'}, 'the-phase-is-wrong-on-the-second-io-wrap')
c('    WR(o, doorbell(t, 2 * q->qid), q->tail);', '    WR(o, doorbell(t, 2 * q->qid), (q->tail + 1) % q->entries);', {'healthy'},
  'the-sq-doorbell-is-one-entry-ahead')
c('    q->next_cid = q->next_cid % 0xffff + 1;', '    q->next_cid = q->next_cid + 1;', {'cid-wrap'}, 'the-command-identifier-does-not-wrap')
c('    WR(o, doorbell(t, 2 * q->qid + 1), q->head);\n    /* One command',
  '    if (!((dw3 >> 17) & 0x7fff)) WR(o, doorbell(t, 2 * q->qid + 1), q->head);\n    /* One command', {'status'},
  'an-error-completion-is-not-acknowledged')
c('    CLEAN(o, cpu(t, q->cq_off + (uint64_t)q->head * 16), 16);\n    dup =', '    dup =', {'duplicate'}, 'the-duplicate-probe-is-not-invalidated')
c('* t->ticks_per_s / 2;', '* t->ticks_per_s;', {'rdy-never', 'timeouts'}, 'cap-to-is-counted-in-seconds')
mutant('the-command-timeout-is-zero', [(H, 'NVMET_CMD_SECONDS 5u', 'NVMET_CMD_SECONDS 0u')], {'latency'})
mutant('the-command-timeout-is-5000-s', [(H, 'NVMET_CMD_SECONDS 5u', 'NVMET_CMD_SECONDS 5000u')], {'timeouts', 'latency'})
mutant('the-shutdown-timeout-is-zero', [(H, 'NVMET_SHUTDOWN_SECONDS 10u', 'NVMET_SHUTDOWN_SECONDS 0u')], {'healthy'})
mutant('the-controller-is-live-only-once-ready',
       [(C, '    t->flags |= NVMET_F_DMA_LIVE;                              /* live from the write: its effect is not yet known */\n', ''),
        (C, '    t->stage = NVMET_ST_ENABLED;', '    t->flags |= NVMET_F_DMA_LIVE;\n    t->stage = NVMET_ST_ENABLED;')], {'cfs-bringup', 'rdy-never', 'timeouts'})
c('    WR(o, t->bar + NVMEH_CC, 0);\n    e = step(o, t, wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0));',
  '    WR(o, t->bar + NVMEH_CC, 0);\n    t->flags &= ~(uint64_t)NVMET_F_DMA_LIVE;\n    e = step(o, t, wait_csts(o, t, CSTS_RDY, 0, t->rdy_ticks, NVMET_E_T_QUIESCE, 0));',
  {'timeouts'}, 'the-stop-clears-the-live-flag-before-its-last-wait')
c('    WR(o, t->bar + NVMEH_CC, NVMET_CC_ENABLE | NVMET_CC_SHN_NORMAL);\n    e = step(o, t, wait_csts(o, t, CSTS_SHST_MASK, CSTS_SHST_DONE, t->shutdown_ticks, NVMET_E_T_SHUTDOWN, 0));\n    if (e) return e;\n',
  '', {'timeouts'}, 'the-stop-sends-no-shutdown-notification')
c('    CLEAN(o, cpu(t, NVMET_IDENT), NVMET_PAGE);\n    while (i < NVMET_PAGE / 4', '    while (i < NVMET_PAGE / 4', {'spec-fill'},
  'the-identify-page-is-not-invalidated-after-the-dma')
c('CLEAN(o, sqe, 64);', 'if (q->qid == 0) CLEAN(o, sqe, 64);', {'healthy'}, 'an-sqe-is-cleaned-for-the-admin-queue-only')
c('    CLEAN(o, cpu(t, NVMEH_DATA), bytes);                       /* drop lines fetched before the DMA landed */',
  '    CLEAN(o, cpu(t, NVMEH_DATA), NVMET_PAGE);                  /* drop lines fetched before the DMA landed */', {'spec-fill'},
  'only-the-first-data-page-is-invalidated')
c('if (c->cpu_base < c->mem_high && c->mem_low < cpu_end) goto out;', 'if (c->cpu_base <= c->mem_high && c->mem_low <= cpu_end) goto out;', {'config'},
  'memory-next-to-the-extent-is-an-overlap')
c('    wr64(o, t->bar + NVMEH_ACQ_REG, bus(t, NVMEH_ACQ));', '    WR(o, t->bar + NVMEH_ACQ_REG, (uint32_t)bus(t, NVMEH_ACQ));', {'healthy'},
  'the-acq-high-word-is-not-written')
# configuration and hand-off
c('    if (!(c->flags & NVMET_C_DMA_PROVEN)) goto out;\n', '', {'config'}, 'the-dma-proof-is-optional')
c('    if (c->cpu_base < c->mem_high && c->mem_low < cpu_end) goto out;\n', '', {'config'}, 'the-extent-may-overlap-the-guest')
c('(c->bar & 0x3fff) ||', '', {'config'}, 'an-unaligned-bar-is-accepted')
c('(c->cpu_base & (EXTENT - 1)) ||', '', {'config'}, 'an-unaligned-extent-is-accepted')
c('cpu_end < c->cpu_base || ', '', {'config'}, 'a-wrapping-extent-is-accepted')
c('!c->cpu_base || ', '', {'config'}, 'a-zero-extent-is-accepted')
c('(c->bus_base & (EXTENT - 1)) ||', '', {'config'}, 'an-unaligned-bus-address-is-accepted')
c('bus_end < c->bus_base ||', '', {'config'}, 'a-wrapping-bus-address-is-accepted')
c('!c->bus_base || ', '', {'config'}, 'a-zero-bus-address-is-accepted')
c('!c->ticks_per_s || c->ticks_per_s > TICKS_PER_S_MAX ||', '', {'config'}, 'the-counter-frequency-is-not-checked')
c('c->max_transfer < NVMET_PAGE || c->max_transfer > NVMEH_DATA_BYTES || (c->max_transfer % NVMET_LBA_BYTES))', '0)', {'config'},
  'the-transfer-cap-is-not-checked')
c('    if (t->bus_base != t->cpu_base) return NVMET_E_NOT_IDENTITY;\n', '', {'bus-translate'}, 'the-adoption-params-accept-a-translated-extent')
c('if (first_lba > last_lba || last_lba >= t->nsze || !timeout_ticks) return NVMET_E_RANGE;', 'if (0) return NVMET_E_RANGE;', {'handoff'},
  'the-adoption-window-is-not-checked')


def sh(argv, **kw):
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, **kw)


def have_clang():
    return Path(CLANG).exists() or shutil.which(CLANG) is not None


def compile_object(out, source, extra=(), includes=()):
    out.parent.mkdir(parents=True, exist_ok=True)
    r = sh([CLANG, *SAN, *DEFS, *[f'-I{i}' for i in includes], f'-I{ROOT / "compat"}', *extra, '-c', str(source), '-o', str(out)])
    return r


def link(exe, objects):
    return sh([CLANG, *SAN[:3], '-fsanitize=address,undefined', *map(str, objects), '-o', str(exe)])


def case_lines(text):
    return {m.group(1): m.group(2) for m in re.finditer(r'^case (\S+) (ok|FAIL)', text, re.M)}


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_clang():
            raise unittest.SkipTest('clang not found')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.objects = {}
        for name, source, extra in (('host', 'compat/nvme-host.c', ()), ('board', 'compat/board-info.c', ()), ('window', 'compat/ans-window.c', ()),
                                    ('harness', 'tools/test-nvme-takeover.c', ()), ('unit', C, ())):
            obj = BUILD / f'{name}.o'
            r = compile_object(obj, source, extra)
            if r.returncode:
                raise AssertionError(f'{source} does not build:\n{r.stderr[-3000:]}')
            cls.objects[name] = obj
        exe = BUILD / 'harness'
        r = link(exe, cls.objects.values())
        if r.returncode:
            raise AssertionError(f'link failed:\n{r.stderr[-2000:]}')
        cls.exe = exe

    def test_every_case_of_the_harness_passes(self):
        r = sh([str(self.exe)], timeout=HANG_SECONDS)
        cases = case_lines(r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        self.assertEqual(set(cases), set(CASES), 'the cases the test names and the cases the harness runs differ')
        self.assertTrue(all(v == 'ok' for v in cases.values()), r.stdout)
        self.assertRegex(r.stdout, rf'\b{len(CASES)} cases, 0 failures')

    def test_the_old_tests_of_nvme_host_still_pass(self):
        """The model extension changes nothing the old tests see: sync, async, and with the takeover model compiled in."""
        for name, defines in (('sync', []), ('async', ['-DSNAP_ANS_ASYNC=1']), ('model', ['-DSNAP_PLATFORM_SP11=1', '-DSNAP_NVME_TAKEOVER=1'])):
            exe = BUILD / f'old-{name}'
            sources = ['tools/test-nvme-host.c', 'compat/nvme-host.c', 'compat/ans-window.c'] + (['compat/board-info.c'] if name == 'model' else [])
            r = sh([CLANG, *SAN, *defines, f'-I{ROOT / "compat"}', *sources, '-o', str(exe)])
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = sh([str(exe)], timeout=HANG_SECONDS)
            self.assertEqual(r.returncode, 0, f'{name}: {r.stdout[-2000:]}')
            self.assertRegex(r.stdout, r'ok: \d+ reads, 0 failures', name)

    def test_a_second_optimisation_level_agrees(self):
        exe = BUILD / 'harness-o2'
        r = sh([CLANG, '-std=gnu11', '-O2', '-g', '-fsanitize=undefined', '-fno-sanitize-recover=undefined', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-function', *DEFS, f'-I{ROOT / "compat"}', 'tools/test-nvme-takeover.c', C, *HOST_SOURCES, '-o', str(exe)])
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = sh([str(exe)], timeout=HANG_SECONDS)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])


class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_clang():
            raise unittest.SkipTest('clang not found')
        BUILD.mkdir(parents=True, exist_ok=True)

    def test_off_by_default_the_unit_has_no_symbol(self):
        """The gate is off unless -DSNAP_NVME_TAKEOVER=1: the object of an ordinary build, on either platform, is empty."""
        for name, platform in (('a16', []), ('sp11', ['-DSNAP_PLATFORM_SP11=1'])):
            obj = BUILD / f'off-{name}.o'
            r = sh([CLANG, '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', *platform, f'-I{ROOT / "compat"}', '-c', C, '-o', str(obj)])
            self.assertEqual(r.returncode, 0, r.stderr)
            nm = sh([NM, '--defined-only', '--extern-only', str(obj)])
            self.assertEqual(nm.stdout.strip(), '', f'{name}: the gate is off and the object still has symbols:\n{nm.stdout}')

    def test_the_gate_needs_the_sp11_platform(self):
        r = sh([CLANG, '-std=gnu11', '-fsyntax-only', '-DSNAP_NVME_TAKEOVER=1', f'-I{ROOT / "compat"}', C])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SNAP_PLATFORM_SP11', r.stderr)

    def test_the_test_hook_is_not_in_a_normal_build(self):
        obj = BUILD / 'on-nohook.o'
        r = sh([CLANG, '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-DSNAP_PLATFORM_SP11=1', '-DSNAP_NVME_TAKEOVER=1', f'-I{ROOT / "compat"}',
                '-c', C, '-o', str(obj)])
        self.assertEqual(r.returncode, 0, r.stderr)
        symbols = sh([NM, '--defined-only', str(obj)]).stdout
        self.assertIn('nvmet_takeover', symbols)
        self.assertNotIn('nvmet_test_command', symbols)

    def test_the_unit_is_freestanding(self):
        """An aarch64 object: no libc call (a struct clear or copy would pull in memset or memcpy); the one undefined symbol is the
        shared admin allow-list of nvme-host.c."""
        obj = BUILD / 'aarch64.o'
        r = sh([CLANG, '--target=aarch64-unknown-none-elf', '-ffreestanding', '-O2', '-Wall', '-Wextra', '-Werror', '-DSNAP_PLATFORM_SP11=1',
                '-DSNAP_NVME_TAKEOVER=1', f'-I{ROOT / "compat"}', '-c', C, '-o', str(obj)])
        if r.returncode and 'target' in r.stderr:
            self.skipTest('clang cannot target aarch64-unknown-none-elf')
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        undefined = sorted(sh([NM, '--undefined-only', str(obj)]).stdout.split())
        undefined = [u.lstrip('_') for u in undefined if u not in ('U',)]
        self.assertEqual(undefined, ['nvmeh_sp11_admin_verdict'], undefined)


def code_of(path):
    text = (ROOT / path).read_text()
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    text = re.sub(r'//[^\n]*', ' ', text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)                    # the diagnostic texts may name what is refused


class Audit(unittest.TestCase):
    """A tripwire, not a proof. These tests read the source; a refactor can walk past them (an opcode built arithmetically, a
    macro with another name, a new function that stores an SQE itself: a reviewer showed each). What keeps the
    unit read only is cmd(), the one function that builds a command and runs both gates before it stores anything, together
    with the model's counters in the harness (opcode-gate, and every case's audit()). Every later diff of the unit needs a
    re-review whatever these report."""

    def test_read_is_the_only_io_opcode_the_unit_names(self):
        header = code_of(H)
        self.assertEqual(dict(re.findall(r'#define (NVMET_IO_\w+) (0x[0-9a-f]+)u', header)), {'NVMET_IO_READ': '0x02'})
        self.assertEqual(dict(re.findall(r'#define (NVMET_ADMIN_\w+) (0x[0-9a-f]+)u', header)),
                         {'NVMET_ADMIN_DELETE_SQ': '0x00', 'NVMET_ADMIN_CREATE_SQ': '0x01', 'NVMET_ADMIN_DELETE_CQ': '0x04',
                          'NVMET_ADMIN_CREATE_CQ': '0x05', 'NVMET_ADMIN_IDENTIFY': '0x06', 'NVMET_ADMIN_SET_FEATURES': '0x09'})
        body = code_of(C)
        sent = set(re.findall(r'cmd\(o, t, &t->(adm|io), (\w+),', body))
        admin = {('adm', n) for n in ('NVMET_ADMIN_DELETE_SQ', 'NVMET_ADMIN_CREATE_SQ', 'NVMET_ADMIN_DELETE_CQ', 'NVMET_ADMIN_CREATE_CQ',
                                      'NVMET_ADMIN_IDENTIFY', 'NVMET_ADMIN_SET_FEATURES')}
        self.assertTrue(sent)
        self.assertEqual(sent - admin - {('io', 'NVMET_IO_READ')}, set(), 'a command is built with an opcode that is not on the lists')
        self.assertIn(('io', 'NVMET_IO_READ'), sent)

    def test_no_write_flush_trim_format_sanitize_or_firmware_word_is_in_the_code(self):
        for path in (C, H):
            words = re.findall(r'(?i)\w*(?:write|flush|dataset|dsm|format|sanitize|firmware|\bfua\b|trim|deallocate|erase)\w*', code_of(path))
            self.assertEqual(words, [], f'{path} names a command that this stage must not have: {words}')

    def test_the_two_risks_stay_in_the_header(self):
        text = (ROOT / H).read_text()
        for need in ('D11', 'BitLocker', 'READ ONLY', 'NOT RUN', 'NVMET_F_DMA_LIVE', 'NVMET_F_FW_QUEUES_LIVE', 'UNKNOWN', 'SNAP_NVME_TAKEOVER',
                     'not a proof', 'UNWIRED'):
            self.assertIn(need, text, f'the header no longer says {need}')
        self.assertIn('INTERNAL', text)

    def test_the_size_limits_of_agents_md(self):
        for path in (C, H, MODEL, 'tools/test-nvme-takeover.c', 'tools/test-nvme-takeover.py', 'tools/test-nvme-host.c'):
            data = (ROOT / path).read_bytes()
            self.assertLess(data.count(b'\n'), 1500, path)
            self.assertLess(len(data), 50 * 1024, path)


class Mutants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_clang():
            raise unittest.SkipTest('clang not found')
        cls.objects = {}
        BUILD.mkdir(parents=True, exist_ok=True)
        for name, source in (('host', 'compat/nvme-host.c'), ('board', 'compat/board-info.c'), ('window', 'compat/ans-window.c'),
                             ('harness', 'tools/test-nvme-takeover.c')):
            cls.objects[name] = BUILD / f'{name}.o'
            r = compile_object(cls.objects[name], source)
            if r.returncode:
                raise AssertionError(r.stderr[-2000:])

    def build_mutant(self, name, edits):
        directory = BUILD / 'mutants' / name
        directory.mkdir(parents=True, exist_ok=True)
        texts = {C: (ROOT / C).read_text(), H: (ROOT / H).read_text()}
        for path, old, new in edits:
            self.assertEqual(texts[path].count(old), 1, f'{name}: the anchor occurs {texts[path].count(old)} times: {old[:70]!r}')
            texts[path] = texts[path].replace(old, new)
        for path, text in texts.items():                       # build products: the mutated copies of the unit
            (directory / Path(path).name).write_text(text)
        objects = [directory / 'unit.o']
        relaxed = ('-Wno-error=unused-but-set-variable', '-Wno-error=unused-variable', '-Wno-error=unused-parameter')   # a seeded fault may leave one unused
        r = compile_object(objects[0], directory / 'nvme-takeover.c', extra=relaxed, includes=(directory,))
        self.assertEqual(r.returncode, 0, f'{name} does not build:\n{r.stderr[-2500:]}')
        if any(path == H for path, _, _ in edits):            # the harness includes the header: rebuild it with the mutated one
            objects.append(directory / 'harness.o')
            r = compile_object(objects[1], ROOT / 'tools/test-nvme-takeover.c', extra=relaxed, includes=(directory,))
            self.assertEqual(r.returncode, 0, r.stderr[-2500:])
        else:
            objects.append(self.objects['harness'])
        objects += [self.objects['host'], self.objects['board'], self.objects['window']]
        exe = directory / 'harness'
        r = link(exe, objects)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return exe

    def test_every_seeded_mutant_is_killed_by_its_own_case(self):
        names = [m[0] for m in M]
        self.assertEqual(len(names), len(set(names)), 'a mutant name is used twice')
        self.assertGreaterEqual(len(M), 60)
        only = [s for s in os.environ.get('NVMET_ONLY', '').split(',') if s]            # a development filter: a subset of the names
        ran = 0
        for name, edits, cases in M:
            if only and not any(s in name for s in only):
                continue
            ran += 1
            with self.subTest(mutant=name):
                exe = self.build_mutant(name, edits)
                try:
                    r = sh([str(exe)], timeout=HANG_SECONDS)
                except subprocess.TimeoutExpired:
                    self.assertEqual(cases, 'hang', f'{name} hangs the harness but is meant to fail a case')
                    print(f'mutant {name}: killed by a hang (the {HANG_SECONDS} s limit)')
                    continue
                self.assertNotEqual(cases, 'hang', f'{name} should hang the harness and did not (rc {r.returncode})')
                failing = {k for k, v in case_lines(r.stdout).items() if v == 'FAIL'}
                print(f'mutant {name}: killed by {sorted(failing) or "a crash"}')
                self.assertNotEqual(r.returncode, 0, f'{name} was not caught: the harness passed')
                if r.returncode == 1:                                           # a failure of a case, not a crash
                    self.assertTrue(failing & cases, f'{name} fails {sorted(failing)}, not one of {sorted(cases)}')
        if only:                                                                # a subset is not a pass of the whole set
            self.skipTest(f'NVMET_ONLY ran {ran} of {len(M)} mutants')


if __name__ == '__main__':
    unittest.main(verbosity=2)
