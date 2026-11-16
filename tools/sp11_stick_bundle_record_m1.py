# SPDX-License-Identifier: MIT
"""The static text of the record of a monitor entry run (--m1-run) that is checked against binaries or against the sources, in one place of its own so
that tools/sp11_stick_bundle_record.py stays under its size limit: the guest SMC paragraph (D5), the device-code section 3b (D1) and the placed entries of
the el2_sysreg class (D3). The record module appends them to its text for the M1 kind only, and the records of the other kinds are what they were.

 - SMC_LINES follows the shared "Not authorised" line of section 3. Its numbers are those of a read-only scan of the local J700 26A428 kernelcache
   (tools/test-sp11-stick-monitor-devices.py repeats it when the kernelcache is present and says SKIP when it is not).
 - DEVICE_LINES is section 3b. tools/test-sp11-stick-monitor-devices.py reads the symbol table of the monitor build (llvm-nm) and fails when a family
   of device code in the binary is not named here with its gates, when a family named here is not in the binary, or when a family that the build leaves
   out appears in it. A family starts at a line of three spaces, its lower-case name and an opening parenthesis.
 - PLACED_LINES are tools/sp11_monitor_census.py's `placed` output for the wrapper and the monitor of the test build, with the six spaces that the record
   module adds in front of each line taken off. The census check fails when they differ from the binaries. Regenerate them after any change of the
   monitor or of the wrapper, with `placed --binary wrapper ELF` and `placed --binary monitor ELF`, and say so in the commit. A line of prose in the
   sub-block must not start with `wrapper ` or `monitor `.

The prose is filled by wrap() below, a greedy fill on single spaces at a fixed width and not a library's, so that the bytes of the text the operator
approves (its SHA-256 is PIN.DAT's authorisation_sha256) depend on nothing but this file.

Evidence: EXPERIMENTAL, hardware NOT RUN. The gates are read from the sources and from the binary; nothing here was run by the monitor on the unit."""

WIDTH = 118


def wrap(text, first=3, rest=3, width=WIDTH):
    """`text` filled greedily on single spaces into lines of at most `width` characters, the first indented by `first` spaces and the others by `rest`."""
    lines, line = [], ''
    for word in text.split():
        indent = first if not lines else rest
        if line and len(' ' * indent + line + ' ' + word) > width:
            lines.append(' ' * indent + line)
            line = word
        else:
            line = f'{line} {word}' if line else word
    return (*lines, ' ' * (first if not lines else rest) + line)


SMC_LINES = wrap(
    'About "a firmware write" above: an SMC that the guest executes is not trapped. HCR_EL2 starts as probes/xnuboot.S:128-142 sets it, with TSC clear, '
    'and every later write to it changes other bits only (IMO, FMO, NV, NV2, TWE, VI, VF, API, TVM and TRVM: read from the sources, INFERRED), and the '
    'monitor has no handler for the exception class of an SMC from the guest (EC 0x17). An SMC at EL1 would reach firmware without the monitor '
    '(INFERRED). The basis for not expecting one is a read-only scan of the local J700 26A428 kernelcache: its two executable segments, __TEXT_EXEC '
    '(14,774,272 words) and __TEXT_BOOT_EXEC (8,192), hold 0 SMC and 0 HVC words (OBSERVED on a host file, one kernelcache; the same scan finds 1 SVC, '
    '1 ERET and 146 WFE words, so it does find instructions). Whether the guest runs SMC code from anywhere else is UNKNOWN.')

DEVICE_LINES = (
    '3b. Device code in the monitor and what closes it (D1; docs/SP11-STICK-MONITOR.md section 14)',
    *wrap('Section 3 counts system registers. The monitor binary also holds code that stores to memory-mapped devices and makes SMC calls, found by '
          'name in its symbol table (tools/test-sp11-stick-monitor-devices.py). Each family below has a gate. PRIVATE: monitor state in the work area, '
          'which stage 2 makes read-only to the guest. LIVE: a PARAMS word that the monitor reads again while the guest runs. The guest can write the '
          'RAM that holds PARAMS (INFERRED: stage 2 gives it DRAM and the reserve; whether the SPTM stops it is UNKNOWN), so a LIVE gate is a bar that a '
          'guest can lift and not a wall. With PARAMS as the builder makes it (gpu.flags, early_stop.flags and txm_native zero) and a guest that writes '
          'none of it, no family below stores to a device or calls firmware (INFERRED from the sources; never run). A store or a call of one is in no '
          'store class above.'),
    *wrap('adreno (adreno_*, gpu_*, fwd_*, and fence_at, tlbi and gic_off of compat/adreno.c; probes/xnuboot/gpu_glue.c): stores to the GPU, its SMMU '
          'and its GMU at PAs 0x03d00000 to 0x03df0000 (compat/adreno.h), the base of the X1E80100\'s GPU too (docs/SP11-GPU-A7XX-STUDY.md: INFERRED for '
          'this machine), and to the GIC distributor at the address that PARAMS gpu.gicd holds. Those stores have a PRIVATE gate, the ADOPTED state that '
          'only gpu_prepare sets, and only when gpu.flags is not zero as the monitor prepares. Behind a LIVE gate only, gpu.flags with its enable bit: '
          'an HVC #0 from the guest makes a real SMC (adreno_smc, through gpu_smc: two TrustZone GPU calls on an allow-list, no ADOPTED test), and an '
          'INTID 0x14C interrupt is acknowledged and ended (gpu_irq: with no forwarder line, which nothing adopted means, nothing more).', rest=6),
    *wrap('early_stop (early_stop_*; compat/early-stop.c, probes/xnuboot/early_stop_thermal.c): at a timer tick before the deadline, '
          'early_stop_host_tick calls (its early_stop_host_call is inlined), at EL2, the function pointer that PARAMS early_stop.host_call holds, when '
          'early_stop.flags has HOST and host_call, session, hz and the poll period are set. LIVE gate and nothing private: a guest that writes those '
          'words makes the monitor call an address of its choosing at EL2. early_stop_hvc (LIVE: gpu.flags enable, then early_stop.flags GUEST) '
          'records a request and answers it, and stores to no device.', rest=6),
    *wrap('thermal (thermal_*, which include thermal_read, and a16_thermal_*): loads only, from the A16\'s sensor addresses (section 4). PRIVATE gate '
          'c->thermal, which the monitor\'s setup makes only if early_stop.flags has the thermal bit at that moment (probes/xnuboot/setup.c): clear, as '
          'the builder requires.', rest=6),
    *wrap('nvmeh (nvmeh_*, ans_window_*, and command of compat/nvme-host.c): doorbell stores to an NVMe controller. This profile\'s ans_window_prepare '
          'answers UNWIRED when PARAMS configures an ANS window, which refuses the launch and leaves the controller not adopted, so that a read, a write '
          'or a flush is refused before a doorbell (probes/xnuboot/ans_glue.c: a build-time gate; whether this run\'s PARAMS configure one is UNKNOWN).',
          rest=6),
    *wrap('secondary (secondary_*, xnuboot_secondary_stop and early_off; group G above): PRIVATE gate host_cpus, from x4, which the wrapper passes as 0 '
          '(probes/xnuboot/setup.c, cpu_boot.c); SMC for PSCI, and the GIC distributor at one fixed address (group G).', rest=6),
    *wrap('Not in this binary, and the test fails if one appears: hamoa_*, edp_*, qcc*, i2c_input*, guest_net*, dvfs*, boot_reloc*, boot_relay*, '
          'display_timing*, adreno_recover*, adreno_coherent*.', rest=6),
    *wrap('The guest-device models (sptm_*, sep_*, txm_*, device_*, aes_*, the rest of ans_*, pmgr_*, spmi_*, dart_*, and the like) work on the '
          'monitor\'s state and the guest\'s RAM by design. That none of them stores to a device is INFERRED from the sources, which were not all read '
          'for this record; the census sees system registers and not MMIO, and an exploratory scan for constant-address stores found them only in the '
          'families above.', rest=6))

# The census placed entries (D3): the first line is the head of the sub-block (six spaces and `placed` once the record module has indented it).
PLACED_LINES = (
    *wrap('placed by function, as tools/sp11_monitor_census.py prints them (`placed --binary wrapper|monitor ELF`) and checks them against the ELFs: '
          'every register a binary writes, every SYS operation, SMC, ERET and WFE, and every data word that decodes as a system instruction, with the '
          'functions that hold it and how many each holds (name*N; one when there is no count). A register or operation held in another function or a '
          'second time, and a new one anywhere, fails the check. The six data words are the guest instruction table of compat/native-writer.h (the '
          'J700 kernel\'s MSR to ELR_EL1 and SPSR_EL1 sites): the monitor compares guest words with them and never executes them (INFERRED).',
          first=0, rest=3, width=WIDTH - 6),
    '   wrapper AT S1E2R, AT S1E2W: sw_probe_range',
    '   wrapper DC CIVAC: sw_verify_all',
    '   wrapper DC CVAC: mon_refuse, sw_copy_all, sw_gic_arm, sw_gic_arm_reads, sw_gic_arm_stores*7,',
    '      sw_gic_begin_class, sw_gic_flush, sw_gic_put_back, sw_head_flush, sw_head_init*2,',
    '      sw_monitor_stage*8, sw_refuse*2, sw_set_stage',
    '   wrapper IC IALLUIS: sp11hw_ic_ialluis',
    '   wrapper ICC_BPR1_EL1: sp11hw_write_icc_bpr1_el1',
    '   wrapper ICC_IGRPEN1_EL1: sp11hw_write_icc_igrpen1_el1',
    '   wrapper ICC_PMR_EL1: sp11hw_write_icc_pmr_el1',
    '   monitor APDAKeyHi_EL1, APDAKeyLo_EL1, APDBKeyHi_EL1, APDBKeyLo_EL1, APIAKeyHi_EL1, APIAKeyLo_EL1,',
    '      APIBKeyHi_EL1, APIBKeyLo_EL1: pauth_native_sign_user*2, pauth_native_user*2, pauth_switch_enter,',
    '      pauth_switch_leave',
    '   monitor APGAKeyHi_EL1, APGAKeyLo_EL1: pauth_pacga*2, pauth_switch_enter, pauth_switch_leave',
    '   monitor AT S1E1R: early_stop_hvc, model_dispatch*5, vcpu_arch_prepare*2, xnuboot_trap*9',
    '   monitor CNTHCTL_EL2: vcpu_arch_prepare*2, xnuboot_report',
    '   monitor CNTHP_CTL_EL2: device_reset_stop*2, runtime_enter*2, secondary_gic_stop, timer_arm,',
    '      watchdog_off*2, xnuboot_async*2, xnuboot_pauth_failed*2, xnuboot_report*6',
    '   monitor CNTHP_CVAL_EL2: device_reset_stop, runtime_enter, timer_arm, watchdog_off, xnuboot_async,',
    '      xnuboot_pauth_failed, xnuboot_report*3',
    '   monitor CNTKCTL_EL1: clock_control, vcpu_arch_prepare, xnuboot_report',
    '   monitor CPACR_EL1, CPTR_EL2, ICC_BPR1_EL1, ICC_IGRPEN1_EL1, ICC_PMR_EL1, ICC_SRE_EL2: secondary_run',
    '   monitor DAIF: pauth_native_sign_user, pauth_native_user, xnuboot_report',
    '   monitor DAIFSet: _start, early_off, pauth_native_sign_user, pauth_native_user, secondary_entry,',
    '      secondary_gic_stop',
    '   monitor DBGBCR0_EL1, DBGBCR1_EL1, DBGBCR2_EL1, DBGBCR3_EL1, DBGBCR4_EL1, DBGBCR5_EL1, DBGBVR0_EL1,',
    '      DBGBVR1_EL1, DBGBVR2_EL1, DBGBVR3_EL1, DBGBVR4_EL1, DBGBVR5_EL1: writer_debug_write',
    '   monitor DC CIVAC: adreno_guest_cache, adreno_guest_fill32*2, adreno_guest_ib*5, adreno_mirror,',
    '      adreno_ring_kick, adreno_ring_reserve, aes_dma_publish, attr_clean_finish, command*5,',
    '      const_region_seal, fence_at*2, gpu_prepare*3, guard_write, kernel_publish_cpu, monitor_call_impl,',
    '      secondary_release, sep_pnon_query, sep_sks_publish, sep_xars_slot, sptm_attr_clean, sptm_publish,',
    '      sptm_retype_user_exec, transaction_publish*2, user_live_alloc, user_live_write, user_private_write,',
    '      user_publish_view, user_root_install, vcpu_arch_prepare, vcpu_stage2_prepare, xnuboot_report*3',
    '   monitor DC CVAC: secondary_power_off',
    '   monitor DC CVAU: monitor_call_impl*4',
    '   monitor DC IVAC: adreno_guest_cache',
    '   monitor ELR_EL1, SPSR_EL1: xnuboot_trap*4',
    '   monitor ELR_EL2, ERET: _start, resume_lower, secondary_guest_enter',
    '   monitor ESR_EL1: vcpu_arch_prepare, xnuboot_trap*3',
    '   monitor FAR_EL1: vcpu_arch_prepare, xnuboot_trap*2',
    '   monitor HCR_EL2: _start*2, guest_interrupt_sync, pauth_switch_enter, pauth_switch_leave,',
    '      secondary_entry, secondary_run*2, vcpu_arch_prepare, xnuboot_report',
    '   monitor IC IALLU, MAIR_EL2, SPSel, TTBR0_EL2: secondary_entry',
    '   monitor IC IALLUIS: sptm_retype_user_exec',
    '   monitor ICC_DIR_EL1, ICC_EOIR1_EL1: fwd_complete, secondary_gic_stop, xnuboot_async*3',
    '   monitor MAIR_EL1, TTBR1_EL1, VMPIDR_EL2, VTCR_EL2: vcpu_arch_prepare, xnuboot_report',
    '   monitor MDCR_EL2, OSLAR_EL1: vcpu_arch_prepare, xnuboot_report, xnuboot_secondary_stop',
    '   monitor MDSCR_EL1: step_arm, vcpu_arch_prepare, xnuboot_report, xnuboot_secondary_stop',
    '   monitor PAR_EL1: early_stop_hvc, model_dispatch*5, vcpu_arch_prepare, xnuboot_trap*9',
    '   monitor SCTLR_EL1: pauth_switch_enter, pauth_switch_leave, vcpu_arch_prepare*2, xnuboot_report*2',
    '   monitor SCTLR_EL2, TCR_EL2, TLBI ALLE2: pauth_native_sign_user*2, pauth_native_user*2, secondary_entry',
    '   monitor SMC: early_off, gpu_smc, secondary_power_off, secondary_release*3, xnuboot_report',
    '   monitor SPSR_EL2: _start, secondary_guest_enter, step_arm, user_sysreg_access, xnuboot_trap*5',
    '   monitor SP_EL0, SP_EL1: model_dispatch',
    '   monitor TCR_EL1: user_root_install*2, vcpu_arch_prepare, xnuboot_report',
    '   monitor TLBI ALLE1, TLBI VMALLS12E1: xnuboot_report',
    '   monitor TLBI ALLE1IS: kernel_change_pperm_timed, multipage_aperture, root_slot_release,',
    '      shadow_tlb_sync, sptm_configure_user_root, sptm_retire_shared_root, sptm_retire_user_root,',
    '      sptm_retype_rozone*2, sptm_retype_txm_default*2, sptm_retype_txm_owned*2, sptm_retype_user_exec,',
    '      sptm_share_user_root, update_disjoint_call*2, user_change_uperm, user_commit_detach,',
    '      user_commit_shared_leaf, user_commit_shared_unmap, user_commit_shared_update, user_commit_tlb_range,',
    '      user_commit_unnest, user_commit_update, user_enter_mode*2, user_private_write, user_refresh_all,',
    '      user_root_install*4, xnuboot_report',
    '   monitor TLBI VMALLE1IS, VBAR_EL1, VNCR_EL2: vcpu_arch_prepare',
    '   monitor TLBI VMALLS12E1IS: const_region_seal, guard_step, guards_close, vcpu_arch_prepare,',
    '      xnuboot_report, xnuboot_trap*5',
    '   monitor TPIDR_EL2: secondary_entry, xnuboot_report, xnuboot_setup',
    '   monitor TTBR0_EL1: user_root_install, vcpu_arch_prepare, xnuboot_report',
    '   monitor VBAR_EL2: _start, secondary_entry*2, xnuboot_report',
    '   monitor VTTBR_EL2: const_region_seal*2, vcpu_arch_prepare, xnuboot_report',
    '   monitor WFE: early_off, secondary_power_off',
    '   monitor data words: 0xd5184002*3, 0xd518402c, 0xd5184032, 0xd5184033')
