# SPDX-License-Identifier: MIT
"""The options of tools/sp11_stick_bundle_build.py for the kinds that build a bundle or its record template (synthetic, real; `template` leaves out the options that only a build
takes). Split out of that file unchanged, but for the help of the options that rung 7 (K15) adds or changes, to keep it under the size of a file of this tool; it re-exports build_opts.

Evidence: EXPERIMENTAL, hardware NOT RUN. Nothing here approves anything."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_bundle_record_gic as gq  # noqa: E402
import sp11_stick_bundle_profile as gpb  # noqa: E402


def build_opts(p, kind, template=False):
    for flag, kw in (('--facts', dict(required=True, help='the local facts JSON, see tools/testdata/sp11-facts.example.json')),
                     ('--dump', dict(action='append', default=[], help='a firmware dump directory (default downloads/sp11/x*-run*)')),
                     ('--base', dict(required=True, help='guest_base: a multiple of 32 MiB (D1)')), ('--guest-mib', dict(type=int, required=True)),
                     ('--reserve-mib', dict(type=int, help='default: the loader\'s bootstrap_reserve() for this guest')),
                     ('--declared', dict(action='append', default=[], help='BASE:SIZE the runner declared: kept clear')),
                     ('--monitor', dict(required=True, help='SNAPMON.BIN: payload 0, the wrapper (the dry-run one of docs/SP11-STICK-WRAPPER.md, or any file that calls no '
                                                            'monitor: WRAPPER_CALLS_MONITOR; with --m1-run, SNAPMON-M1.BIN)')),
                     ('--reporter', dict(required=True, help='SNAPREP.BIN: payload 1, the reporter')),
                     ('--stores', dict(required=True, help='ram,el2_sysreg[,gic]: the store classes authorised (D4)')),
                     ('--gic-stop', dict(action='store_true', help='the record of the GIC and stop dry run (docs/SP11-STICK-GIC.md); needs gic in --stores')),
                     (gq.OPTION, dict(action='store_true', help='with --gic-stop: the text for passes 5 and 6 of the stage, the quarantine; --monitor must be a pass 5 or 6 wrapper')),
                     ('--m1-run', dict(metavar='BUILD_REPORT', help='the record of the monitor entry run (docs/SP11-STICK-MONITOR.md section 10): the build-report.json of '
                                                                   'tools/sp11_stick_wrapper_build.py --monitor, which made the SNAPMON-M1.BIN and SNAPREP-M1.BIN given '
                                                                   'as --monitor and --reporter; needs ram, el2_sysreg and gic in --stores and a time box of 1 to 120 s')),
                     ('--time-box-s', dict(type=int, required=True, help='D5')), ('--descriptor', dict(help='a boot-ready board descriptor (real needs one)')),
                     ('--bigfile', dict(action='store_true', help='EXPERIMENTAL (docs/SP11-STICK-ROOT.md): the bundle for the wrapper built with --root-carry: files of up to 2 GiB, and q1n1\'s manifest '
                                                                  'tool called with the 8 MiB chunk cap of the runner built with Q1N1_SP11_BIGFILE; the default is the 256 MiB of the pair the records pin')),
                     ('--m1-efi', dict(action='append', default=[], metavar='NAME=SHA256', help='with --m1-run, once for SLBOUNCE-DEVHE-MARK.EFI and Q1N1-PAYLOAD.EFI: '
                                                                                              'the EFI files that run before the wrapper, named in section 1b')),
                     ('--cpu-start', dict(choices=('stop', 'ack'), default='stop', help='with --m1-run: what the monitor of the pair does with the guest\'s first CPU-start write, '
                                                                                       'which must be what the build report says: stop (the default, every record up to rung 4) or '
                                                                                       'ack (EXPERIMENTAL, the records of rungs 5 to 8 only, with --let-panic: docs/SP11-MONITOR-PROFILE.md section 10)')),
                     ('--note', dict(help='a text file (printable ASCII, at most 48 lines of 120 characters) appended to the record as its section 6'))):
        p.add_argument(flag, **kw)
    if kind == 'synthetic':
        for flag, default in (('--image-mib', 115.5), ('--tables-mib', 16.0), ('--sptm-data-mib', 5.0)):
            p.add_argument(flag, type=float, default=default)
        p.add_argument('--seed', default='synthetic')
    else:
        p.add_argument('--kernelcache', required=True)
        p.add_argument('--guest-profile', default=gpb.DEFAULT, metavar='NAME',
                       help=f'EXPERIMENTAL, the records of rungs 7 and 8 only: the guest\'s device tree (docs/SP11-M1-GUEST-PROFILES.md): one of {", ".join(gpb.NAMES)}; any other name, and the profile '
                            'in any form but those of rungs 7 (quiet) and 8 (quiet-rtc), is GUEST_PROFILE_REFUSED')
        p.add_argument('--panel', choices=('handed', 'withheld', 'console'), required=True,
                       help='D9: SNAP_NO_FB; with --m1-run also `console`: the panel handed and serial=2 in the command line (probes/run.py --console panel)')
        p.add_argument('--boot-arg', default=None, metavar='WORD',
                       help='with --m1-run --panel console: one more word in the guest\'s command line, from a short list (cpus=1 with the plain monitor, cpu_boot_timeout_secs=120 or =90 '
                            'with --cpu-start ack); any other word is BOOT_ARG_REFUSED')
        p.add_argument('--let-panic', action='store_true',
                       help='with --m1-run --panel console and --boot-arg cpus=1 --time-box-s 5 (rung 4) or --cpu-start ack with --boot-arg cpu_boot_timeout_secs=120 --time-box-s 60 (rung 5), '
                            '=90 --time-box-s 120 (rung 6) or =120 --time-box-s 120 --guest-profile j700ap-26A428-m1-quiet (rung 7) or ...-m1-quiet-rtc (rung 8), and no other combination (LET_PANIC_REFUSED): set '
                            'the monitor\'s PARAMS word let_panic (word 23, offset 0xB8), so that a guest EL1 exception that reaches one branch of the monitor runs XNU\'s own handler '
                            'instead of ending the run (docs/SP11-STICK-MONITOR.md section 20, K12 to K16)')
        p.add_argument('--sptm-hvc-calls', choices=(0, 1), type=int, required=True, help='D9: the SPTM HVC kernel patch')
        p.add_argument('--seed', default=rec.DEFAULT_SEED, help='label of the guest\'s boot random seed (SNAP_SEED): the same label, the same bytes')
    if not template:
        for flag, kw in (('--out', dict(required=True)), ('--force', dict(action='store_true')), ('--allow-under', dict(action='append', default=[])),
                         ('--authorisation', dict(help='the approved record')), ('--authorisation-sha256', dict()),
                         ('--no-manifest', dict(action='store_true', help='the bundle files only: no PAYLOAD.MAN, no stage tree')),
                         ('--base-tree', dict(help='a staged tree that stage-sp11-boot-media.py verify accepts')),
                         ('--file', dict(nargs=3, action='append', default=[], metavar=('NAME', 'PATH', 'SHA256'))), ('--pins', dict()),
                         ('--no-pins', dict(action='store_true')), ('--plan-name', dict())):
            p.add_argument(flag, **kw)
