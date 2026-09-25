"""Live dashboards: cockpit, orbit, duo and history.

The animation never invents data: light sweeps along the gauges, a scan
light passes over the core bars, a spark runs along the charts, tick
rings turn by one tick per loop, the clock colon blinks.
"""

import math

from PIL import Image, ImageDraw

from ..sensors import SensorSnapshot
from .base import Face, FaceContext, Values, fmt_int, need
from .draw import LOOP, Canvas, heat, pulse, with_alpha
from .parts import backdrop, chart_spark, date_line, hero_number, sweep, tail
from .words import words

CYAN = (120, 220, 255)
ORANGE = (255, 150, 70)
GREEN = (140, 230, 120)
BLUE = (120, 190, 255)
VIOLET = (200, 140, 255)
MUTED = (150, 160, 170)
DIM = (105, 125, 135)
LIQUID_RANGE = (25.0, 60.0)


# -- cockpit ---------------------------------------------------------------------

def cockpit_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    if not (need(snap.liquid_temp) or need(snap.cpu_temp)):
        return None
    clock, date = date_line(ctx)
    return {
        "liquid": None if snap.liquid_temp is None else round(snap.liquid_temp, 1),
        "pump": snap.pump_rpm and round(snap.pump_rpm, -1), "fan": snap.fan_rpm and round(snap.fan_rpm, -1),
        "cpu_temp": snap.cpu_temp and round(snap.cpu_temp), "gpu_temp": snap.gpu_temp and round(snap.gpu_temp),
        "ram": snap.ram_used_gb and round(snap.ram_used_gb),
        "vram": snap.gpu_mem_used_gb and round(snap.gpu_mem_used_gb, 1),
        "cores": [round(v) for v in (snap.cpu_core_loads or ())][:32],
        "cpu_load": snap.cpu_load and round(snap.cpu_load),
        "freq": snap.cpu_freq_ghz and round(snap.cpu_freq_ghz, 1),
        "charts": [tail(snap, n, 48) for n in ("cpu_load", "gpu_load", "liquid")],
        "chart_now": [snap.cpu_load and round(snap.cpu_load), snap.gpu_load and round(snap.gpu_load),
                      snap.liquid_temp and round(snap.liquid_temp)],
        "time": clock, "date": date, "lang": ctx.language,
    }


def cockpit_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = backdrop(ctx)
    base.arc(300, 150, 390, 9, (36, 46, 56))
    liquid = v["liquid"]
    lo, hi = LIQUID_RANGE
    span = 0.0 if liquid is None else 240 * min(1, max(0, (liquid - lo) / (hi - lo)))
    fill = base.layer()
    for i in range(48):
        a0 = 150 + 240 * i / 48
        if a0 >= 150 + span:
            break
        base.arc(300, a0, min(150 + span, a0 + 240 / 48 + 0.6), 9, heat(0.85 * i / 48),
                 cap=False, target=fill)
    base.glow(fill, 6, 0.9)
    base.text((320, 104), w["liquid"], "mono", 17, (140, 205, 230), tracking=3)
    if liquid is not None:
        hero_number(base, liquid, 204, CYAN)
    rpm = f"{w['pump']} {fmt_int(v['pump'])} · {w['fan']} {fmt_int(v['fan'])} {w['rpm']}"
    base.text((320, 224), rpm, "mono", 12, MUTED, tracking=1)
    kpis = ((fmt_int(v["cpu_temp"], "°"), "CPU", ORANGE), (fmt_int(v["gpu_temp"], "°"), "GPU", GREEN),
            (fmt_int(v["ram"]), w["ram_gb"], BLUE),
            ("–" if v["vram"] is None else f"{v['vram']:.1f}", w["vram_gb"], VIOLET))
    for x, (value, label, color) in zip((170, 270, 370, 470), kpis, strict=True):
        base.text((x, 272), value, "condensed", 52, color)
        base.text((x, 308), label, "mono", 12, MUTED, tracking=1)
    cores = list(v["cores"]) or [v["cpu_load"] or 0]
    x0, x1, y0, y1 = 128.0, 512.0, 332.0, 372.0
    step = (x1 - x0) / len(cores)
    draw = base.draw()
    for i, load in enumerate(cores):
        x = x0 + i * step
        draw.rounded_rectangle([base.p(x + 1.5), base.p(y0), base.p(x + step - 1.5), base.p(y1)],
                               radius=base.p(3), fill=(24, 30, 36))
        top = y1 - (y1 - y0) * max(0.04, load / 100)
        draw.rounded_rectangle([base.p(x + 1.5), base.p(top), base.p(x + step - 1.5), base.p(y1)],
                               radius=base.p(3), fill=heat(load / 100))
    freq = "" if v["freq"] is None else f" · {v['freq']:.1f} GHZ"
    base.text((320, 386), f"{len(cores)} {w['cores']} · {fmt_int(v['cpu_load'], ' %')}{freq}",
              "mono", 11, DIM, tracking=1)
    boxes = ((150, 408, 250, 458), (270, 408, 370, 458), (390, 408, 490, 458))
    labels = ("CPU %", "GPU %", "LIQ °C")
    for box, series, color, label, now in zip(boxes, v["charts"], (ORANGE, GREEN, CYAN), labels,
                                              v["chart_now"], strict=True):
        draw.rounded_rectangle([base.p(box[0] - 6), base.p(box[1] - 6), base.p(box[2] + 6),
                                base.p(box[3] + 22)], radius=base.p(8), fill=(14, 18, 22),
                               outline=(34, 42, 48), width=base.w(1))
        base.chart(box, series, color, lo=0 if "%" in label else None)
        base.text(((box[0] + box[2]) / 2, box[3] + 10), f"{label} {fmt_int(now)}", "mono", 10.5, MUTED)
    base.text((320, 540), v["date"], "mono", 12, DIM, tracking=2)

    frames = []
    hours, minutes = v["time"].split(":")
    for t in LOOP:
        frame = base.copy()
        sweep(frame, 300, 150, span, 9, t)
        scan = frame.layer()
        pos = x0 + (x1 - x0) * t
        for i, load in enumerate(cores):
            x = x0 + i * step
            near = max(0.0, 1 - abs(x + step / 2 - pos) / (step * 2.5))
            if near > 0:
                top = y1 - (y1 - y0) * max(0.04, load / 100)
                ImageDraw.Draw(scan).rounded_rectangle(
                    [frame.p(x + 1.5), frame.p(top), frame.p(x + step - 1.5), frame.p(y1)],
                    radius=frame.p(3), fill=(255, 255, 255, round(110 * near)))
        frame.image.alpha_composite(scan)
        for k, (box, series) in enumerate(zip(boxes, v["charts"], strict=True)):
            if len(series) >= 2:
                chart_spark(frame, box, series, lo=0 if k < 2 else None, hi=None, t=(t + k / 3) % 1)
        colon = ":" if t < 0.5 else " "
        frame.text((320, 510), f"{hours}{colon}{minutes}", "condensed", 34, (220, 228, 235))
        frames.append(frame.finish())
    return frames


# -- orbit -----------------------------------------------------------------------

def orbit_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    if not need(snap.liquid_temp) and not need(snap.cpu_load):
        return None
    return {"liquid": snap.liquid_temp and round(snap.liquid_temp), "cpu": snap.cpu_load and round(snap.cpu_load),
            "gpu": snap.gpu_load and round(snap.gpu_load), "cpu_temp": snap.cpu_temp and round(snap.cpu_temp),
            "gpu_temp": snap.gpu_temp and round(snap.gpu_temp),
            "pump": snap.pump_rpm and round(snap.pump_rpm, -1), "fan": snap.fan_rpm and round(snap.fan_rpm, -1),
            "lang": ctx.language}


def orbit_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = Canvas(ctx.size, ctx.fonts_dir, (4, 5, 6))
    cpu, gpu = (v["cpu"] or 0) / 100, (v["gpu"] or 0) / 100
    arcs = base.layer()
    base.arc(296, 120, 240, 16, (38, 30, 26), target=arcs)
    base.arc(296, 300, 420, 16, (22, 36, 28), target=arcs)
    if cpu > 0:
        base.arc(296, 240 - 120 * cpu, 240, 16, (255, 138, 60), target=arcs)
    if gpu > 0:
        base.arc(296, 300, 300 + max(3, 120 * gpu), 16, (130, 235, 120), target=arcs)
    base.glow(arcs, 10, 0.8)
    base.text((138, 320), "CPU", "mono", 15, (255, 170, 110), tracking=2)
    base.text((138, 348), fmt_int(v["cpu"], "%"), "condensed", 30, (255, 200, 160))
    base.text((502, 320), "GPU", "mono", 15, (150, 240, 140), tracking=2)
    base.text((502, 348), fmt_int(v["gpu"], "%"), "condensed", 30, (190, 250, 180))
    base.text((320, 212), w["liquid"], "mono", 17, (150, 200, 220), tracking=4)
    base.text((320, 300), fmt_int(v["liquid"], "°"), "semibold", 150, (235, 245, 250))
    base.text((320, 392), f"{fmt_int(v['cpu_temp'], '°')} CPU · {fmt_int(v['gpu_temp'], '°')} GPU",
              "medium", 22, (170, 185, 195))
    base.text((320, 470), f"{w['pump']} {fmt_int(v['pump'])} · {w['fan']} {fmt_int(v['fan'])}",
              "mono", 13, (120, 180, 210), tracking=1.5)
    frames = []
    for t in LOOP:
        frame = base.copy()
        ticks = frame.layer()
        draw = ImageDraw.Draw(ticks)
        turn = 20 * t  # one major tick spacing per loop: the dial turns seamlessly
        for k in range(-10, 61):
            for start, color in ((120, (110, 85, 70, 255)), (300, (70, 110, 85, 255))):
                a = start + 2 * k + turn
                if not start <= a <= start + 120:
                    continue
                inner = 254 if k % 10 == 0 else 262
                draw.line([frame.polar(270, a), frame.polar(inner, a)], fill=color, width=frame.w(1.5))
        frame.image.alpha_composite(ticks)
        if cpu > 0:
            sweep(frame, 296, 240 - 120 * cpu, 120 * cpu, 16, 1 - t)
        if gpu > 0:
            sweep(frame, 296, 300, max(3, 120 * gpu), 16, t)
        frames.append(frame.finish())
    return frames


# -- duo ---------------------------------------------------------------------------

def duo_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    if not (need(snap.cpu_temp) or need(snap.gpu_temp)):
        return None
    return {"cpu_temp": snap.cpu_temp and round(snap.cpu_temp), "cpu": snap.cpu_load and round(snap.cpu_load),
            "freq": snap.cpu_freq_ghz and round(snap.cpu_freq_ghz, 1),
            "gpu_temp": snap.gpu_temp and round(snap.gpu_temp), "gpu": snap.gpu_load and round(snap.gpu_load),
            "gpu_w": snap.gpu_power_w and round(snap.gpu_power_w),
            "liquid": snap.liquid_temp and round(snap.liquid_temp), "lang": ctx.language}


def duo_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = Canvas(ctx.size, ctx.fonts_dir, (5, 5, 6))
    draw = base.draw()
    draw.line([(base.center, base.p(150)), (base.center, base.p(500))], fill=(40, 44, 50), width=base.w(1.5))
    sides = (
        (185, "CPU", v["cpu_temp"], v["cpu"], "–" if v["freq"] is None else f"{v['freq']:.1f} GHz",
         (255, 140, 60), (50, 34, 26)),
        (455, "GPU", v["gpu_temp"], v["gpu"], fmt_int(v["gpu_w"], " W"), (130, 235, 120), (26, 44, 30)),
    )
    hot = max(sides, key=lambda s: s[2] or 0)[0]
    for cx, name, temp, load, extra, color, dim in sides:
        box = [base.p(cx - 105), base.p(145), base.p(cx + 105), base.p(355)]
        layer = base.layer()
        ImageDraw.Draw(layer).arc(box, 150, 390, fill=dim, width=base.w(14))
        if temp is not None:
            fill = 240 * min(1, max(0, (temp - 30) / 70))
            ImageDraw.Draw(layer).arc(box, 150, 150 + fill, fill=color, width=base.w(14))
        base.glow(layer, 8, 0.8)
        base.text((cx, 196), name, "mono", 17, color, tracking=3)
        base.text((cx, 256), fmt_int(temp, "°"), "semibold", 84, (240, 244, 248))
        base.text((cx, 322), fmt_int(load, " %"), "condensed", 26, (200, 205, 210))
        base.text((cx, 392), extra, "condensed", 34, color)
        base.text((cx, 420), w["power"] if name == "GPU" else w["clock"], "mono", 11, (120, 128, 136),
                  tracking=2)
    draw = base.draw()
    draw.rounded_rectangle([base.p(250), base.p(470), base.p(390), base.p(510)], radius=base.p(20),
                           fill=(14, 26, 34), outline=(40, 70, 90), width=base.w(1.5))
    base.text((320, 490), f"{w['liquid']} {fmt_int(v['liquid'], '°')}", "mono", 15, (130, 210, 240), tracking=1)
    frames = []
    for t in LOOP:
        frame = base.copy()
        for cx, _name, temp, _load, _extra, color, _dim in sides:
            if temp is None:
                continue
            fill = 240 * min(1, max(0, (temp - 30) / 70))
            layer = frame.layer()
            box = [frame.p(cx - 105), frame.p(145), frame.p(cx + 105), frame.p(355)]
            head = 150 + fill * t
            for i in range(14):
                a = head - i * 2
                if a < 150:
                    break
                ImageDraw.Draw(layer).arc(box, a - 2.2, a, fill=(255, 255, 255, round(230 * (1 - i / 14))),
                                          width=frame.w(14))
            if cx == hot:  # the hotter side breathes
                glow = pulse(t)
                ImageDraw.Draw(layer).arc(box, 150, 150 + fill, fill=with_alpha(color, round(170 * glow)),
                                          width=frame.w(24))
            frame.glow(layer, 9, 1.4)
        frames.append(frame.finish())
    return frames


# -- history -------------------------------------------------------------------------

def history_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    series = list((snap.history or {}).get("liquid", ()))
    if len(series) < 10:
        return None  # not enough history yet
    step = max(1, len(series) // 120)
    points = [round(sum(series[i:i + step]) / len(series[i:i + step]), 1)
              for i in range(0, len(series), step)]
    now = snap.liquid_temp if snap.liquid_temp is not None else series[-1]
    return {"points": points, "now": round(now), "min": round(min(series)),
            "mean": round(sum(series) / len(series)), "max": round(max(series)), "lang": ctx.language}


def history_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = Canvas(ctx.size, ctx.fonts_dir, (4, 7, 10))
    points = list(v["points"])
    lo, hi = math.floor(min(points) - 2), math.ceil(max(points) + 2)
    box = (80, 250, 560, 430)
    draw = base.draw()
    for g in range(lo + (5 - lo % 5) % 5, hi + 1, 5):
        y = box[3] - (box[3] - box[1]) * (g - lo) / (hi - lo)
        draw.line([(base.p(box[0]), base.p(y)), (base.p(box[2]), base.p(y))], fill=(30, 40, 48),
                  width=base.w(1))
        base.text((box[0] + 16, y - 10), f"{g}°", "mono", 11, (90, 110, 120))
    base.chart(box, points, (110, 215, 255), lo=lo, hi=hi, area=55, width=2.6)
    base.text((320, 118), f"{w['liquid']} · {w['last_24h']}", "mono", 16, (140, 200, 225), tracking=3)
    base.text((320, 185), f"{v['now']}°", "semibold", 96, (235, 245, 250))
    for x, (key, label) in zip((220, 320, 420), (("min", w["min"]), ("mean", w["mean"]), ("max", w["max"])),
                               strict=True):
        base.text((x, 470), f"{v[key]}°", "condensed", 34, (220, 228, 235))
        base.text((x, 500), label, "mono", 12, (110, 130, 140), tracking=2)
    base.text((box[2] - 20, 446), w["now"], "mono", 11, (90, 110, 120))
    frames = []
    for t in LOOP:
        frame = base.copy()
        # a cursor runs along the day and reads the value where it stands
        i = min(len(points) - 1, int(t * (len(points) - 1)))
        x = box[0] + (box[2] - box[0]) * i / (len(points) - 1)
        y = box[3] - (box[3] - box[1]) * (points[i] - lo) / (hi - lo)
        cursor = frame.layer()
        ImageDraw.Draw(cursor).line([(frame.p(x), frame.p(box[1])), (frame.p(x), frame.p(box[3]))],
                                    fill=(110, 215, 255, 90), width=frame.w(1.5))
        frame.image.alpha_composite(cursor)
        chart_spark(frame, box, points, lo=lo, hi=hi, t=t)
        label_x = min(box[2] - 30, max(box[0] + 30, x))
        frame.text((label_x, y - 22), f"{points[i]:.1f}°", "mono", 13, (220, 240, 250))
        frames.append(frame.finish())
    return frames


FACES = (
    Face("cockpit", cockpit_values, cockpit_render),
    Face("orbit", orbit_values, orbit_render),
    Face("duo", duo_values, duo_render),
    Face("history", history_values, history_render),
)
