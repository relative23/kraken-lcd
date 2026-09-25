"""Helm, the second cockpit, built for the round display.

Two rings use the rim: one radial bar per CPU core around it, the liquid
gauge outside, both leaving the bottom open for the clock. Inside there
are only three sizes of text. Identity sits in the labels (CPU orange,
GPU green, liquid cyan), state in the numbers: temperatures and loads
are colored by heat, so a glance tells hot from cool. The chart lays CPU
and GPU load over each other. The animation is a light passing around
the core ring and along the gauge, a spark on the chart and the clock's
colon; nothing in it is invented data.
"""

from PIL import Image

from ..sensors import SensorSnapshot
from .base import Face, FaceContext, Values, fmt_int, need
from .draw import LOOP, Canvas, heat, with_alpha
from .parts import backdrop, chart_spark, date_line, hero_number, sweep, tail
from .words import words

CYAN = (120, 220, 255)
ORANGE = (255, 150, 70)
GREEN = (140, 230, 120)
MUTED = (150, 160, 170)
DIM = (105, 125, 135)
TRACK = (22, 28, 34, 255)
RING_START, RING_SPAN = 135.0, 270.0  # both rings leave the bottom open
CORE_IN, CORE_OUT = 248.0, 294.0      # the core ring, radial extent
GAUGE_R, GAUGE_W = 312.0, 8.0         # the liquid gauge outside it
LIQUID_RANGE = (25.0, 60.0)
CHART = (150.0, 400.0, 490.0, 452.0)


def temp_color(temp: float | None):
    """30 °C cool blue, 100 °C hot red; unknown stays neutral."""
    return MUTED if temp is None else heat((temp - 30) / 70)


def helm_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    if not (need(snap.liquid_temp) or need(snap.cpu_temp)):
        return None
    clock, date = date_line(ctx)
    return {
        "liquid": None if snap.liquid_temp is None else round(snap.liquid_temp, 1),
        "pump": snap.pump_rpm and round(snap.pump_rpm, -1), "fan": snap.fan_rpm and round(snap.fan_rpm, -1),
        "cpu_temp": snap.cpu_temp and round(snap.cpu_temp), "cpu_load": snap.cpu_load and round(snap.cpu_load),
        "freq": snap.cpu_freq_ghz and round(snap.cpu_freq_ghz, 1),
        "gpu_temp": snap.gpu_temp and round(snap.gpu_temp), "gpu_load": snap.gpu_load and round(snap.gpu_load),
        "gpu_w": snap.gpu_power_w and round(snap.gpu_power_w),
        "ram": snap.ram_used_gb and round(snap.ram_used_gb), "ram_total": snap.ram_total_gb and round(snap.ram_total_gb),
        "vram": snap.gpu_mem_used_gb and round(snap.gpu_mem_used_gb, 1),
        "cores": [round(v) for v in (snap.cpu_core_loads or ())][:32],
        "charts": [tail(snap, "cpu_load", 60), tail(snap, "gpu_load", 60)],
        "time": clock, "date": date, "lang": ctx.language,
    }


def _segments(count: int) -> list[tuple[float, float]]:
    step = RING_SPAN / count
    gap = min(2.5, step * 0.18)
    return [(RING_START + i * step + gap / 2, RING_START + (i + 1) * step - gap / 2)
            for i in range(count)]


def _bar_length(load: float) -> float:
    return max(4.0, (CORE_OUT - CORE_IN) * load / 100)


def _side(base: Canvas, x: float, label: str, color, temp, load, extra: str) -> None:
    base.text((x, 268), label, "mono", 15, color, tracking=3)
    base.text((x, 308), fmt_int(temp, "°"), "condensed", 60, temp_color(temp))
    base.text((x, 346), f"{fmt_int(load, ' %')} · {extra}", "condensed", 22, MUTED)


def helm_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = backdrop(ctx)
    cores = list(v["cores"]) or [v["cpu_load"] or 0]
    segments = _segments(len(cores))
    track = base.layer()
    for a0, a1 in segments:
        base.arc(CORE_OUT, a0, a1, CORE_OUT - CORE_IN, TRACK, cap=False, target=track)
    base.arc(GAUGE_R, RING_START, RING_START + RING_SPAN, GAUGE_W, TRACK, cap=False, target=track)
    base.image.alpha_composite(track)
    bars = base.layer()
    for (a0, a1), load in zip(segments, cores, strict=True):
        length = _bar_length(load)
        base.arc(CORE_IN + length, a0, a1, length, with_alpha(heat(load / 100), 255), cap=False,
                 target=bars)
    base.glow(bars, 4, 0.5)
    liquid = v["liquid"]
    lo, hi = LIQUID_RANGE
    span = 0.0 if liquid is None else RING_SPAN * min(1, max(0, (liquid - lo) / (hi - lo)))
    gauge = base.layer()
    steps = 54
    for i in range(steps):
        a0 = RING_START + RING_SPAN * i / steps
        if a0 >= RING_START + span:
            break
        base.arc(GAUGE_R, a0, min(RING_START + span, a0 + RING_SPAN / steps + 0.6), GAUGE_W,
                 heat(0.85 * i / steps), cap=False, target=gauge)
    base.glow(gauge, 5, 0.8)

    base.text((320, 118), w["liquid"], "mono", 16, (140, 205, 230), tracking=3)
    if liquid is not None:
        hero_number(base, liquid, 216, CYAN, px=112)
    freq = "–" if v["freq"] is None else f"{v['freq']:.1f} GHz"
    _side(base, 212, "CPU", ORANGE, v["cpu_temp"], v["cpu_load"], freq)
    _side(base, 428, "GPU", GREEN, v["gpu_temp"], v["gpu_load"], fmt_int(v["gpu_w"], " W"))
    ram = f"RAM {fmt_int(v['ram'])} / {fmt_int(v['ram_total'])} GB"
    vram = "" if v["vram"] is None else f"  ·  VRAM {v['vram']:.1f} GB"
    base.text((320, 380), ram + vram, "mono", 13, MUTED, tracking=1)
    draw = base.draw()
    x0, y0, x1, y1 = CHART
    draw.rounded_rectangle([base.p(x0 - 8), base.p(y0 - 8), base.p(x1 + 8), base.p(y1 + 8)],
                           radius=base.p(10), fill=(12, 16, 20), outline=(30, 38, 44), width=base.w(1))
    for series, color in zip(v["charts"], (ORANGE, GREEN), strict=True):
        base.chart(CHART, series, color, lo=0, hi=100, area=35, width=2.0)
    base.text((x0 + 4, y0 + 8), "%", "mono", 10, DIM, anchor="lm")
    base.text((x1 - 4, y0 + 8), "CPU · GPU", "mono", 10, DIM, anchor="rm")
    pump = f"{w['pump']} {fmt_int(v['pump'])}  ·  {w['fan']} {fmt_int(v['fan'])}"
    base.text((320, 476), pump, "mono", 12, DIM, tracking=1)
    base.text((320, 582), v["date"], "mono", 12, DIM, tracking=2)

    frames = []
    hours, minutes = v["time"].split(":")
    for t in LOOP:
        frame = base.copy()
        sweep(frame, GAUGE_R, RING_START, span, GAUGE_W, t)
        # a light passes around the core ring once per loop, lighting each
        # bar as it goes by
        head = RING_START + RING_SPAN * t
        pass_layer = frame.layer()
        for (a0, a1), load in zip(segments, cores, strict=True):
            center = (a0 + a1) / 2
            behind = head - center
            if behind < 0:
                behind += 360  # the light wraps through the gap
            near = max(0.0, 1 - behind / 36)
            if near <= 0:
                continue
            length = _bar_length(load)
            frame.arc(CORE_IN + length, a0, a1, length, (255, 255, 255, round(170 * near)),
                      cap=False, target=pass_layer)
        frame.glow(pass_layer, 6, 1.2)
        for k, series in enumerate(v["charts"]):
            if len(series) >= 2:
                chart_spark(frame, CHART, series, lo=0, hi=100, t=(t + k / 2) % 1)
        colon = ":" if t < 0.5 else " "
        frame.text((320, 548), f"{hours}{colon}{minutes}", "condensed", 38, (220, 228, 235))
        frames.append(frame.finish())
    return frames


FACES = (Face("helm", helm_values, helm_render),)
