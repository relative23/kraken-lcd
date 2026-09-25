"""Faces over the animated backgrounds: the fire with CPU details, the
caustics with liquid details, and a video loop with a value bar.

The text is drawn once as a layer and laid over every background frame,
so it stays perfectly still while the scene moves behind it.
"""

from PIL import Image, ImageDraw, ImageSequence

from ..sensors import SensorSnapshot
from .base import Face, FaceContext, Values, fmt_int, need
from .draw import FRAMES, Canvas
from .words import words


def _overlay(ctx: FaceContext) -> Canvas:
    canvas = Canvas(ctx.size, ctx.fonts_dir)
    canvas.image = canvas.layer()  # transparent: only the text and its shadow
    return canvas


def _compose(ctx: FaceContext, asset: str, overlay: Canvas) -> list[Image.Image]:
    # premultiplied alpha keeps the shadow edges clean when scaling down
    small = overlay.image.convert("RGBa").resize((ctx.size, ctx.size), Image.Resampling.BOX)
    small = small.convert("RGBA")
    frames = []
    with Image.open(ctx.assets_dir / asset) as src:
        for frame in ImageSequence.Iterator(src):
            rgb = frame.convert("RGB")
            if rgb.size != (ctx.size, ctx.size):
                rgb = rgb.resize((ctx.size, ctx.size), Image.Resampling.LANCZOS)
            frames.append(Image.alpha_composite(rgb.convert("RGBA"), small).convert("RGB"))
            if len(frames) >= FRAMES:
                break
    return frames


# -- fire: CPU on the hearth ------------------------------------------------------------

def fire_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    if not need(snap.cpu_load):
        return None
    return {"load": round(snap.cpu_load), "temp": snap.cpu_temp and round(snap.cpu_temp),
            "freq": snap.cpu_freq_ghz and round(snap.cpu_freq_ghz, 1), "lang": ctx.language}


def fire_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    canvas = _overlay(ctx)
    canvas.shadow_text((320, 238), "CPU", "mono", 22, (255, 214, 170))
    canvas.shadow_text((320, 312), f"{v['load']}%", "semibold", 110, (255, 255, 255))
    parts = [fmt_int(v["temp"], "°")]
    if v["freq"] is not None:
        parts.append(f"{v['freq']:.1f} GHz")
    canvas.shadow_text((320, 384), " · ".join(parts), "medium", 22, (255, 200, 150))
    return _compose(ctx, "cpu.gif", canvas)


# -- caustics: liquid under water -------------------------------------------------------

def caustics_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    if not need(snap.liquid_temp):
        return None
    return {"liquid": round(snap.liquid_temp, 1), "pump": snap.pump_rpm and round(snap.pump_rpm, -1),
            "fan": snap.fan_rpm and round(snap.fan_rpm, -1), "lang": ctx.language}


def caustics_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    w = words(ctx.language)
    canvas = _overlay(ctx)
    canvas.shadow_text((320, 222), w["liquid"], "mono", 22, (190, 235, 255))
    canvas.shadow_text((320, 300), f"{v['liquid']:.1f}°", "semibold", 118, (255, 255, 255))
    rpm = f"{w['pump'].title()} {fmt_int(v['pump'])} · {w['fan'].title()} {fmt_int(v['fan'])}"
    canvas.shadow_text((320, 380), rpm, "medium", 22, (190, 235, 255))
    return _compose(ctx, "liquid.gif", canvas)


# -- video: a loop with a value bar -------------------------------------------------------

def video_values(snap: SensorSnapshot, ctx: FaceContext) -> Values | None:
    if not (need(snap.liquid_temp) or need(snap.cpu_temp)):
        return None
    return {"liquid": snap.liquid_temp and round(snap.liquid_temp),
            "cpu": snap.cpu_temp and round(snap.cpu_temp), "lang": ctx.language}


def video_render(v: Values, ctx: FaceContext) -> list[Image.Image]:
    canvas = _overlay(ctx)
    bar = canvas.layer()
    ImageDraw.Draw(bar).rounded_rectangle(
        [canvas.p(170), canvas.p(506), canvas.p(470), canvas.p(566)], radius=canvas.p(30),
        fill=(0, 0, 0, 150), outline=(255, 255, 255, 60), width=canvas.w(1.5))
    canvas.image.alpha_composite(bar)
    canvas.text((236, 536), fmt_int(v["liquid"], "°"), "condensed", 34, (120, 220, 255))
    canvas.text((292, 536), "LIQ", "mono", 11, (200, 205, 210))
    canvas.text((366, 536), fmt_int(v["cpu"], "°"), "condensed", 34, (255, 160, 90))
    canvas.text((420, 536), "CPU", "mono", 11, (200, 205, 210))
    return _compose(ctx, "video.gif", canvas)


FACES = (
    Face("fire", fire_values, fire_render, background="cpu.gif"),
    Face("caustics", caustics_values, caustics_render, background="liquid.gif"),
    Face("video", video_values, video_render, background="video.gif"),
)
