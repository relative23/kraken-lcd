"""Pieces the dashboard faces share: the backdrop, a light sweeping along
a gauge, the hero number with its small decimal, sparks on charts, recent
history and the date line."""


from PIL import ImageDraw

from ..sensors import SensorSnapshot
from .base import FaceContext
from .draw import Canvas, with_alpha
from .words import words


def date_line(ctx: FaceContext) -> tuple[str, str]:
    now, w = ctx.clock(), words(ctx.language)
    return now.strftime("%H:%M"), f"{w['weekdays'][now.weekday()]} {now.day}. {w['months'][now.month - 1]}"


def tail(snap: SensorSnapshot, name: str, count: int) -> list[float]:
    series = (snap.history or {}).get(name, ())
    return [round(v, 1) for v in series[-count:]]


def backdrop(ctx: FaceContext, base=(5, 7, 10)) -> Canvas:
    canvas = Canvas(ctx.size, ctx.fonts_dir, base)
    layer = canvas.layer()
    draw = ImageDraw.Draw(layer)
    for i in range(24, 0, -1):
        r = canvas.center * i / 24
        v = round(14 * (1 - i / 24))
        draw.ellipse([canvas.center - r, canvas.center - r, canvas.center + r, canvas.center + r],
                     fill=(v // 2, v, v + 4, 255))
    canvas.image.alpha_composite(layer)
    return canvas


def sweep(canvas: Canvas, radius: float, start: float, span: float, width: float,
           t: float, color=(255, 255, 255)) -> None:
    """A short bright highlight running along an arc once per loop."""
    if span <= 2:
        return
    layer = canvas.layer()
    head = start + span * t
    for i in range(10):
        a = head - i * 1.4
        if a < start:
            break
        canvas.arc(radius, a - 1.6, a, width, with_alpha(color, round(210 * (1 - i / 10))),
                   cap=False, target=layer)
    canvas.glow(layer, 5, 1.3)


def hero_number(canvas: Canvas, value: float, y: float, color, px: float = 104) -> None:
    """47.6° with the decimal set smaller, centered as a whole."""
    whole, frac = f"{value:.1f}".split(".")
    draw = canvas.draw()
    big, small = canvas.font("semibold", px), canvas.font("semibold", px * 0.54)
    whole_w = draw.textlength(whole, font=big)
    frac_w = draw.textlength("." + frac, font=small)
    x0 = canvas.center - (whole_w + frac_w) / 2 - canvas.p(10)
    base = canvas.p(y)
    draw.text((x0, base), whole, font=big, fill=color, anchor="ls")
    draw.text((x0 + whole_w, base), "." + frac, font=small, fill=color, anchor="ls")
    top = draw.textbbox((x0, base), whole, font=big, anchor="ls")[1]
    draw.text((x0 + whole_w + frac_w + canvas.p(2), top - canvas.p(2)), "°", font=small,
              fill=color, anchor="lt")


def chart_spark(canvas: Canvas, box, series, lo, hi, t: float) -> None:
    """A glowing spark on the chart line, at fraction *t* of its length
    (same scale as ``Canvas.chart``)."""
    x0, y0, x1, y1 = box
    low = min(series) if lo is None else lo
    high = max(series) if hi is None else hi
    span = (high - low) or 1.0
    i = min(len(series) - 1, int(t * (len(series) - 1)))
    x = x0 + (x1 - x0) * i / (len(series) - 1)
    y = y1 - (y1 - y0) * (series[i] - low) / span
    layer = canvas.layer()
    r = canvas.p(5)
    ImageDraw.Draw(layer).ellipse([canvas.p(x) - r, canvas.p(y) - r, canvas.p(x) + r, canvas.p(y) + r],
                                  fill=(255, 255, 255, 230))
    canvas.glow(layer, 4, 1.2)
