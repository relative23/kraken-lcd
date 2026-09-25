"""Everyday faces: clock, music, alarm and night.

Alarm and night show only when they apply (a temperature over the limit,
the night hours); otherwise the carousel skips them without an upload.
The music face is a preview with an example track: reading the desktop's
media player needs the user-session helper planned for 2.0.
"""

import math
import random

from PIL import Image, ImageDraw, ImageFilter

from ..sensors import SensorSnapshot
from .base import Face, FaceContext, Values, fmt_int
from .draw import LOOP, Canvas, pulse, with_alpha
from .words import words

DEFAULT_ALARM_LIMIT = 90.0
DEFAULT_NIGHT_HOURS = (22, 7)


def _date(ctx: FaceContext) -> str:
    now, w = ctx.clock(), words(ctx.language)
    return f"{w['weekdays'][now.weekday()]} {now.day}. {w['months'][now.month - 1]}"


# -- clock ---------------------------------------------------------------------------

def clock_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    now = ctx.clock()
    return {"h": now.hour, "m": now.minute, "day": now.day, "date": _date(ctx),
            "liquid": snap.liquid_temp and round(snap.liquid_temp),
            "cpu": snap.cpu_temp and round(snap.cpu_temp), "lang": ctx.language}


def _hand(canvas: Canvas, draw, angle_deg: float, length: float, width: float, color, tail=24.0):
    a = math.radians(angle_deg - 90)
    c = canvas.center
    draw.line([(c - canvas.p(tail) * math.cos(a), c - canvas.p(tail) * math.sin(a)),
               (c + canvas.p(length) * math.cos(a), c + canvas.p(length) * math.sin(a))],
              fill=color, width=canvas.w(width))


def clock_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = Canvas(ctx.size, ctx.fonts_dir, (6, 7, 8))
    draw = base.draw()
    for k in range(60):
        major = k % 5 == 0
        draw.line([base.polar(296, k * 6 - 90), base.polar(270 if major else 284, k * 6 - 90)],
                  fill=(220, 225, 230) if major else (80, 86, 92), width=base.w(4 if major else 1.5))
    for hour, label in ((0, "12"), (3, "3"), (6, "6"), (9, "9")):
        a = math.radians(hour * 30 - 90)
        base.text((320 + 238 * math.cos(a), 320 + 238 * math.sin(a)), label, "condensed", 30,
                  (200, 205, 210))
    for (cx, cy), value, label, color in (((205, 320), fmt_int(v["liquid"], "°"), w["liquid"], (110, 215, 255)),
                                          ((435, 320), fmt_int(v["cpu"], "°"), "CPU", (255, 150, 70)),
                                          ((320, 440), str(v["day"]), v["date"].split()[0], (220, 225, 230))):
        draw.ellipse([base.p(cx - 50), base.p(cy - 50), base.p(cx + 50), base.p(cy + 50)],
                     outline=(46, 52, 58), width=base.w(2))
        base.text((cx, cy - 6), value, "condensed", 34, color)
        base.text((cx, cy + 24), label, "mono", 9.5, (120, 128, 136), tracking=1)
    _hand(base, draw, (v["h"] % 12 + v["m"] / 60) * 30, 150, 11, (235, 238, 240))
    _hand(base, draw, v["m"] * 6, 232, 7, (235, 238, 240))
    c, dot = base.center, base.p(10)
    draw.ellipse([c - dot, c - dot, c + dot, c + dot], fill=(255, 120, 60))
    frames = []
    for t in LOOP:
        frame = base.copy()
        comet = frame.layer()
        cdraw = ImageDraw.Draw(comet)
        head = 360 * t - 90
        for i in range(14):  # a light runs around the minute track once per loop
            x, y = frame.polar(290, head - i * 3)
            r = frame.p(4.5 - i * 0.25)
            cdraw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 150, 90, round(230 * (1 - i / 14))))
        frame.glow(comet, 5, 1.2)
        frames.append(frame.finish())
    return frames


# -- music (preview with an example track) --------------------------------------------------

def music_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    return {"liquid": snap.liquid_temp and round(snap.liquid_temp),
            "cpu": snap.cpu_temp and round(snap.cpu_temp), "lang": ctx.language}


def _cover(size: int) -> Image.Image:
    """Abstract example artwork: soft colored light blobs."""
    rng = random.Random(11)
    art = Image.new("RGB", (size, size), (18, 16, 26))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(7):
        cx, cy, r = rng.random() * size, rng.random() * size, size * rng.uniform(0.15, 0.4)
        color = rng.choice([(255, 90, 140), (90, 120, 255), (255, 180, 80), (140, 70, 220)])
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=with_alpha(color, 150))
    blurred = layer.filter(ImageFilter.GaussianBlur(size / 14))
    art.paste(blurred, (0, 0), blurred)
    # a record's grooves, so the spin reads as a spin
    draw = ImageDraw.Draw(art)
    for k in range(6, 48, 3):
        inset = size * k / 100
        draw.ellipse([inset, inset, size - inset, size - inset], outline=(0, 0, 0), width=1)
    draw.ellipse([size * 0.44, size * 0.44, size * 0.56, size * 0.56], fill=(245, 240, 250))
    return art


def music_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = Canvas(ctx.size, ctx.fonts_dir, (7, 6, 10))
    cover = _cover(round(base.p(300)))
    mask = Image.new("L", cover.size, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, cover.size[0] - 1, cover.size[1] - 1], fill=255)
    ambient = cover.resize((base.px, base.px)).filter(ImageFilter.GaussianBlur(base.p(60)))
    base.image = Image.blend(Image.new("RGB", ambient.size, (0, 0, 0)), ambient, 0.35).convert("RGBA")
    box = [base.center - base.p(166), base.p(64), base.center + base.p(166), base.p(396)]
    draw = base.draw()
    draw.arc(box, 0, 360, fill=(60, 56, 70), width=base.w(5))
    draw.arc(box, -90, -90 + 360 * 0.62, fill=(255, 255, 255), width=base.w(5))
    base.text((320, 440), w["example_title"], "semibold", 34, (245, 245, 250))
    base.text((320, 476), f"{w['example_artist']} · 2:31 / 4:05", "regular", 20, (180, 175, 195))
    base.text((320, 530), f"{w['liquid']} {fmt_int(v['liquid'], '°')} · CPU {fmt_int(v['cpu'], '°')}",
              "mono", 13, (150, 145, 165), tracking=1)
    frames = []
    left = round(base.center - base.p(150))
    for t in LOOP:
        frame = base.copy()
        spun = cover.rotate(-360 * t, resample=Image.Resampling.BICUBIC)
        frame.image.paste(spun, (left, round(base.p(80))), mask)
        frames.append(frame.finish())
    return frames


# -- alarm -------------------------------------------------------------------------------

def alarm_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    limit = ctx.threshold or DEFAULT_ALARM_LIMIT
    hot = [(name, temp) for name, temp in (("CPU", snap.cpu_temp), ("GPU", snap.gpu_temp))
           if temp is not None and temp >= limit]
    if not hot:
        return None
    name, temp = max(hot, key=lambda item: item[1])
    return {"name": name, "temp": round(temp), "limit": round(limit), "lang": ctx.language}


def alarm_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = Canvas(ctx.size, ctx.fonts_dir, (18, 4, 5))
    layer = base.layer()
    draw = ImageDraw.Draw(layer)
    for i in range(24, 0, -1):
        r = base.center * i / 24
        red = round(20 + 50 * (i / 24) ** 3)
        draw.ellipse([base.center - r, base.center - r, base.center + r, base.center + r],
                     fill=(red, 4, 6, 255))
    base.image.alpha_composite(layer)
    base.text((320, 234), f"{v['name']} {w['hot']}", "mono", 20, (255, 180, 170), tracking=4)
    base.text((320, 318), f"{v['temp']}°", "semibold", 140, (255, 255, 255))
    base.text((320, 404), f"{w['limit']} {v['limit']} °C", "medium", 24, (255, 190, 180))
    frames = []
    for t in LOOP:
        frame = base.copy()
        beat = pulse(t)
        ring = frame.layer()
        frame.arc(300, 0, 360, 10 + 8 * beat, (255, 50, 50, round(160 + 95 * beat)), cap=False, target=ring)
        frame.glow(ring, 10 + 8 * beat, 1.0)
        sign = frame.layer()
        tri = [(frame.center, frame.p(118)), (frame.center - frame.p(38), frame.p(184)),
               (frame.center + frame.p(38), frame.p(184))]
        ImageDraw.Draw(sign).polygon(tri, fill=(255, 210, 60, round(120 + 135 * beat)))
        frame.image.alpha_composite(sign)
        frame.text((320, 164), "!", "bold", 40, (40, 10, 10))
        frames.append(frame.finish())
    return frames


# -- night -------------------------------------------------------------------------------

def night_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    start, end = ctx.hours or DEFAULT_NIGHT_HOURS
    hour = ctx.clock().hour
    inside = start <= hour or hour < end if start > end else start <= hour < end
    if not inside:
        return None
    now = ctx.clock()
    return {"time": now.strftime("%H:%M"), "liquid": snap.liquid_temp and round(snap.liquid_temp),
            "lang": ctx.language}


def night_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    base = Canvas(ctx.size, ctx.fonts_dir, (0, 0, 0))
    base.text((320, 368), f"{w['liquid']} {fmt_int(v['liquid'], '°')}", "mono", 16, (90, 45, 18), tracking=3)
    hours, minutes = v["time"].split(":")
    frames = []
    for t in LOOP:
        frame = base.copy()
        frame.text((320, 292), f"{hours}{':' if t < 0.5 else ' '}{minutes}", "condensed", 92, (120, 60, 20))
        frames.append(frame.finish())
    return frames


FACES = (
    Face("clock", clock_values, clock_render),
    Face("music", music_values, music_render),
    Face("alarm", alarm_values, alarm_render),
    Face("night", night_values, night_render),
)
