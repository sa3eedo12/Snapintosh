/* SPDX-License-Identifier: MIT */
#ifndef SNAP_COMPAT_PLATFORM_H
#define SNAP_COMPAT_PLATFORM_H

/* Compile-time platform selection.
 *
 * SNAP_PLATFORM_A16 is the ASUS Zenbook A16 (Snapdragon X2 Elite). It is the
 * only shipped, hardware-qualified target and is implied when no platform
 * macro is defined, so every existing build preprocesses exactly as before and
 * the monitor ELF stays byte-identical.
 *
 * SNAP_PLATFORM_SP11 is the Surface Pro 11 (Snapdragon X Elite, X1E80100). It
 * is UNIMPLEMENTED and opt-in: it is enabled only by an explicit
 * -DSNAP_PLATFORM_SP11=1, and every SP11-specific path must sit under
 * `#if SNAP_PLATFORM_SP11` with the A16 behaviour left in the `#else` branch.
 * Nothing in the SP11 branch may be reachable from an A16 build.
 *
 * The two are mutually exclusive and exactly one must be selected; an
 * ambiguous or empty selection fails the build instead of silently choosing
 * a target, because a wrong choice would apply another SoC's register and
 * address assumptions to real hardware.
 *
 * This header must stay free of declarations and definitions that emit code or
 * data; the byte-identity proof (tools/monitor_baseline.py) depends on it. */

#ifndef SNAP_PLATFORM_SP11
#define SNAP_PLATFORM_SP11 0
#endif

#ifndef SNAP_PLATFORM_A16
#define SNAP_PLATFORM_A16 (!SNAP_PLATFORM_SP11)
#endif

#if SNAP_PLATFORM_A16 && SNAP_PLATFORM_SP11
#error "SNAP_PLATFORM_A16 and SNAP_PLATFORM_SP11 are mutually exclusive"
#endif

#if !SNAP_PLATFORM_A16 && !SNAP_PLATFORM_SP11
#error "no platform selected: define exactly one of SNAP_PLATFORM_A16, SNAP_PLATFORM_SP11"
#endif

#endif
