#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Does a video stream fit a DisplayPort or eDP main link? A pure model; nothing here touches a device.

Why it exists. The SP11 120 Hz stretch goal (docs/SP11-120HZ-STUDY.md, gate X13) needs to know, before any hardware
time, whether the panel's native mode at 120 Hz can in principle travel over the link the firmware trained, and which
cases would need DSC. The answer depends on inputs nobody has read from the unit yet (lane count, link rate, bits per
component, the panel's real blanking), so every one of them is an explicit input here, and the blanking is either given
or comes from a labelled model assumption (CVT reduced blanking v1), never from "panel truth". The module assumes no
panel size: the study and the tests supply the numbers.

Model, and what it leaves out (MODEL, not measurement).
  * RGB 4:4:4 only: bits per pixel = 3 x bits per component, or the explicit DSC target when DSC is on. 4:2:2, 4:2:0
    and any DSC slice or chunk overhead are not modelled.
  * Payload capacity = lanes x line rate x 8/10 (the 8b/10b factor of RBR, HBR, HBR2 and HBR3). Transfer-unit
    packing, secondary data packets and the MSA are not charged, so a real link needs somewhat more than this model
    says: a "fits" with a margin of a few percent is not a promise. UHBR rates (128b/132b) are refused, not guessed.
  * Required rate = pixel clock x bits per pixel. The blanking intervals matter only through the pixel clock.

Evidence. Line rates are the four DPCD link-bandwidth codes (RBR 0x06, HBR 0x0a, HBR2 0x14, HBR3 0x1e) of the public
VESA DisplayPort standard as mirrored in Linux `include/drm/display/drm_dp.h:588-596`. The eDP 1.4 intermediate rates
(2.16, 2.43, 3.24, 4.32, 6.48 Gbit/s) are INFERRED from Linux i915 `intel_dp.c:547-570` and its rate-select table
read at `intel_dp.c:4745-4770` (the eDP specification itself was not read), and the SP11 panel's own table is
UNKNOWN until its DPCD is dumped. The 8/10 factor is the one Linux's msm driver applies in
`msm_dp_display_mode_valid()` (`drivers/gpu/drm/msm/dp/dp_display.c:752-795`). The CVT reduced-blanking v1 algorithm
follows the public CVT standard, with Linux `drm_cvt_mode()` and the 60 and 120 Hz reduced-blanking entries of the VESA
DMT table in `drm_edid.c` as the fact sources the tests compare against.

    python3 tools/sp11_display_mode.py link-budget --h-active W --v-active H --refresh HZ --bpc 8 --lanes 4 --rate HBR3
"""
from dataclasses import dataclass
from fractions import Fraction
import math
import re

# Per-lane line rates in kbit/s. Index of the DPCD link-bandwidth codes in the module docstring.
LINE_RATES_KBPS = {'RBR': 1_620_000, 'HBR': 2_700_000, 'HBR2': 5_400_000, 'HBR3': 8_100_000}
# eDP 1.4 rate-select values (INFERRED, see the docstring). They are only ever selected by name.
EDP_RATES_KBPS = {'EDP-2.16': 2_160_000, 'EDP-2.43': 2_430_000, 'EDP-3.24': 3_240_000, 'EDP-4.32': 4_320_000,
                  'EDP-6.48': 6_480_000}
ALL_RATES_KBPS = {**LINE_RATES_KBPS, **EDP_RATES_KBPS}
LANE_COUNTS = (1, 2, 4)
BITS_PER_COMPONENT = (6, 8, 10)
PAYLOAD_NUMERATOR, PAYLOAD_DENOMINATOR = 8, 10      # 8b/10b

# Bounds keep every product far below 2**63, so a port to C cannot overflow, and a corrupt number is refused instead
# of producing a plausible-looking verdict. 2**24 kHz is the widest pixel clock a DisplayID Type VII field can hold.
MAX_DIMENSION = 1 << 16
MAX_TOTAL = 1 << 17
MAX_PIXEL_CLOCK_KHZ = 1 << 24
MAX_REFRESH_HZ = 1000
DSC_STEP = Fraction(1, 16)                          # DSC target bits per pixel come in sixteenths
DSC_MIN_BPP = 6

# CVT 1.2 reduced blanking, version 1. A model assumption for a panel whose own blanking is unknown.
RB_MIN_VBLANK_US = 460
RB_H_BLANK = 160
RB_V_FRONT_PORCH = 3
RB_MIN_V_BACK_PORCH = 6
RB_CLOCK_STEP_KHZ = 250


class BudgetError(ValueError):
    """A refusal with a stable machine-readable code (the part before the colon)."""

    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code, self.message = code, message


def _integer(value, name, lo, hi):
    """An int, or a decimal string of one, within [lo, hi]. Booleans, floats and None are refused."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise BudgetError('budget-input', f'{name} must be an integer, not {type(value).__name__}')
    if isinstance(value, str):
        if not re.fullmatch(r'-?[0-9]{1,12}', value.strip()):
            raise BudgetError('budget-input', f'{name} is not an integer: {value!r}')
        value = int(value.strip())
    if not lo <= value <= hi:
        raise BudgetError('budget-range', f'{name} {value} is outside {lo}..{hi}')
    return value


def _rational(value, name, lo, hi):
    """An exact non-negative rational from an int, a decimal string, a float or a Fraction (never a bool)."""
    if isinstance(value, bool) or not isinstance(value, (int, str, float, Fraction)):
        raise BudgetError('budget-input', f'{name} must be a number, not {type(value).__name__}')
    try:
        if isinstance(value, float):
            if not math.isfinite(value):
                raise BudgetError('budget-input', f'{name} is not finite')
            value = repr(value)
        if isinstance(value, str) and not re.fullmatch(r'[0-9]{1,9}(\.[0-9]{1,6})?', value.strip()):
            raise BudgetError('budget-input', f'{name} is not a decimal number: {value!r}')
        number = Fraction(value.strip() if isinstance(value, str) else value)
    except (ValueError, ZeroDivisionError):
        raise BudgetError('budget-input', f'{name} is not a number: {value!r}')
    if not lo <= number <= hi:
        raise BudgetError('budget-range', f'{name} {float(number)} is outside {lo}..{hi}')
    return number


rational = _rational                        # the public name: the CLI parses its refresh tolerance with it


@dataclass(frozen=True)
class StreamTiming:
    """A video timing as the link sees it. All fields are exact integers; the refresh rate is derived."""
    hactive: int
    vactive: int
    htotal: int
    vtotal: int
    pixel_clock_khz: int
    assumption: str = 'explicit'            # 'explicit' or 'cvt-rb-v1' (a model assumption, never panel truth)

    @property
    def hblank(self):
        return self.htotal - self.hactive

    @property
    def vblank(self):
        return self.vtotal - self.vactive

    @property
    def refresh_hz(self):
        return Fraction(self.pixel_clock_khz * 1000, self.htotal * self.vtotal)


def timing_from_totals(hactive, vactive, htotal, vtotal, pixel_clock_khz, assumption='explicit'):
    """A timing from the totals and the pixel clock (what an EDID descriptor gives). Raises BudgetError."""
    hactive = _integer(hactive, 'hactive', 1, MAX_DIMENSION)
    vactive = _integer(vactive, 'vactive', 1, MAX_DIMENSION)
    htotal = _integer(htotal, 'htotal', 1, MAX_TOTAL)
    vtotal = _integer(vtotal, 'vtotal', 1, MAX_TOTAL)
    clock = _integer(pixel_clock_khz, 'pixel_clock_khz', 1, MAX_PIXEL_CLOCK_KHZ)
    if htotal < hactive:
        raise BudgetError('budget-blanking', f'a negative horizontal blank ({htotal - hactive} pixels)')
    if vtotal < vactive:
        raise BudgetError('budget-blanking', f'a negative vertical blank ({vtotal - vactive} lines)')
    return StreamTiming(hactive, vactive, htotal, vtotal, clock, assumption)


def explicit_timing(hactive, vactive, hblank, vblank, refresh_hz=None, pixel_clock_khz=None):
    """A timing from explicit blanking and exactly one of a refresh rate or a pixel clock. Raises BudgetError.

    With a refresh rate the pixel clock is rounded UP to a whole kHz, so a model never under-states the load."""
    if (refresh_hz is None) == (pixel_clock_khz is None):
        raise BudgetError('budget-input', 'give exactly one of a refresh rate and a pixel clock')
    hactive = _integer(hactive, 'hactive', 1, MAX_DIMENSION)
    vactive = _integer(vactive, 'vactive', 1, MAX_DIMENSION)
    hblank = _integer(hblank, 'hblank', -MAX_TOTAL, MAX_TOTAL)
    vblank = _integer(vblank, 'vblank', -MAX_TOTAL, MAX_TOTAL)
    if hblank < 0 or vblank < 0:
        raise BudgetError('budget-blanking', f'negative blanking ({hblank} pixels, {vblank} lines)')
    htotal, vtotal = hactive + hblank, vactive + vblank
    if pixel_clock_khz is None:
        refresh = _rational(refresh_hz, 'refresh_hz', 0, MAX_REFRESH_HZ)
        if refresh <= 0:
            raise BudgetError('budget-range', 'the refresh rate must be above zero')
        pixel_clock_khz = math.ceil(refresh * htotal * vtotal / 1000)
    return timing_from_totals(hactive, vactive, htotal, vtotal, pixel_clock_khz)


def _cvt_vsync_width(hactive, vactive):
    """The vertical sync width CVT assigns to a standard aspect ratio; anything else uses the custom value 10."""
    for h, v, width in ((4, 3, 4), (16, 9, 5), (16, 10, 6), (5, 4, 7), (15, 9, 7)):
        if hactive * v == vactive * h:
            return width
    return 10


def cvt_rb_timing(hactive, vactive, refresh_hz):
    """CVT reduced blanking v1 for a progressive mode: a MODEL ASSUMPTION, not what any panel really uses.

    Newer reduced-blanking versions use less blanking and so a lower pixel clock; choosing v1 errs on the side of
    needing more bandwidth. Raises BudgetError."""
    hactive = _integer(hactive, 'hactive', 1, MAX_DIMENSION)
    vactive = _integer(vactive, 'vactive', 1, MAX_DIMENSION)
    refresh = _rational(refresh_hz, 'refresh_hz', 0, MAX_REFRESH_HZ)
    if refresh <= 0:
        raise BudgetError('budget-range', 'the refresh rate must be above zero')
    min_vblank = Fraction(RB_MIN_VBLANK_US, 1_000_000)
    line_period = (1 / refresh - min_vblank) / vactive
    if line_period <= 0:
        raise BudgetError('budget-range', f'{float(refresh)} Hz leaves no time for the minimum vertical blank')
    vsync = _cvt_vsync_width(hactive, vactive)
    vblank = max(math.floor(min_vblank / line_period) + 1, RB_V_FRONT_PORCH + vsync + RB_MIN_V_BACK_PORCH)
    htotal, vtotal = hactive + RB_H_BLANK, vactive + vblank
    clock = RB_CLOCK_STEP_KHZ * math.floor(refresh * htotal * vtotal / 1000 / RB_CLOCK_STEP_KHZ)
    return timing_from_totals(hactive, vactive, htotal, vtotal, clock, 'cvt-rb-v1')


def line_rate_kbps(rate):
    """The per-lane line rate in kbit/s for a rate name (RBR, HBR, HBR2, HBR3, EDP-2.16 ...). Raises BudgetError."""
    if not isinstance(rate, str):
        raise BudgetError('budget-rate', f'a link rate is a name, not {type(rate).__name__}')
    name = rate.strip().upper()
    if name.startswith('UHBR'):
        raise BudgetError('budget-rate', f'{rate}: 128b/132b rates are not modelled')
    if name not in ALL_RATES_KBPS:
        raise BudgetError('budget-rate', f'unknown link rate {rate!r}; known: {", ".join(ALL_RATES_KBPS)}')
    return ALL_RATES_KBPS[name]


def _check_link(bpc, lanes):
    bpc = _integer(bpc, 'bits per component', 1, 64)
    lanes = _integer(lanes, 'lanes', 0, 64)
    if bpc not in BITS_PER_COMPONENT:
        raise BudgetError('budget-input', f'{bpc} bits per component is not one of {BITS_PER_COMPONENT}')
    if lanes not in LANE_COUNTS:
        raise BudgetError('budget-input', f'{lanes} lanes is not one of {LANE_COUNTS}')
    return bpc, lanes


def link_budget(timing, bpc, lanes, rate, dsc_target_bpp=None):
    """Whether `timing` fits `lanes` lanes at `rate`; returns a JSON-serialisable dict. Raises BudgetError.

    `dsc_target_bpp` None means DSC off (3 x bpc bits per pixel); a number means DSC on at that compressed rate, a
    multiple of 1/16 in [6, 3 x bpc). Zero lanes, an unknown rate, a non-positive clock and every out-of-range input
    are refused rather than answered."""
    if not isinstance(timing, StreamTiming):
        raise BudgetError('budget-input', 'the timing must be a StreamTiming')
    timing = timing_from_totals(timing.hactive, timing.vactive, timing.htotal, timing.vtotal, timing.pixel_clock_khz,
                                timing.assumption)
    bpc, lanes = _check_link(bpc, lanes)
    rate_kbps = line_rate_kbps(rate)
    if dsc_target_bpp is None:
        bits_per_pixel, dsc = Fraction(3 * bpc), False
    else:
        bits_per_pixel = _rational(dsc_target_bpp, 'dsc target bpp', DSC_MIN_BPP, 3 * bpc)
        if bits_per_pixel % DSC_STEP or bits_per_pixel >= 3 * bpc:
            raise BudgetError('budget-dsc', f'a DSC target of {float(bits_per_pixel)} bpp is not a multiple of 1/16 '
                                            f'below the uncompressed {3 * bpc}')
        dsc = True
    capacity = Fraction(lanes * rate_kbps * PAYLOAD_NUMERATOR, PAYLOAD_DENOMINATOR)
    required = timing.pixel_clock_khz * bits_per_pixel
    required_kbps = math.ceil(required)
    return dict(
        fits=required <= capacity,
        required_kbps=required_kbps,
        capacity_kbps=int(capacity),
        margin_kbps=int(capacity) - required_kbps,
        margin_percent=round(float((capacity - required) * 100 / capacity), 3),
        bits_per_pixel=float(bits_per_pixel),
        dsc=dsc,
        max_bits_per_pixel_that_fits=float(math.floor(capacity * 16 / timing.pixel_clock_khz) * DSC_STEP),
        timing=dict(hactive=timing.hactive, vactive=timing.vactive, htotal=timing.htotal, vtotal=timing.vtotal,
                    pixel_clock_khz=timing.pixel_clock_khz, refresh_hz=round(float(timing.refresh_hz), 4),
                    blanking=timing.assumption),
        link=dict(rate=rate.strip().upper(), line_rate_kbps=rate_kbps, lanes=lanes, bits_per_component=bpc),
        label='MODEL: not a measurement; 8b/10b only, no transfer-unit or packet overhead, RGB 4:4:4 only',
    )


def feasibility_matrix(timing, rates=None, lanes=LANE_COUNTS, bpcs=BITS_PER_COMPONENT):
    """One row per (rate, lanes, bpc) with the DSC-off verdict and the highest DSC target that would fit.

    `needs_dsc` is true when the uncompressed stream does not fit but a target of at least the DSC minimum does;
    `impossible` when even the minimum DSC target does not fit."""
    rows = []
    for rate in (rates or tuple(LINE_RATES_KBPS)):
        for n in lanes:
            for bpc in bpcs:
                result = link_budget(timing, bpc, n, rate)
                max_bpp = result['max_bits_per_pixel_that_fits']
                rows.append(dict(rate=result['link']['rate'], lanes=n, bits_per_component=bpc, fits=result['fits'],
                                 margin_percent=result['margin_percent'], required_kbps=result['required_kbps'],
                                 capacity_kbps=result['capacity_kbps'],
                                 needs_dsc=not result['fits'] and max_bpp >= DSC_MIN_BPP,
                                 impossible=max_bpp < DSC_MIN_BPP,
                                 max_bits_per_pixel_that_fits=max_bpp))
    return rows
