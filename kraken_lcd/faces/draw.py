"""Drawing toolkit for the face screens.

Faces are laid out in 640 px reference units, drawn at twice the output
size and averaged down, so arcs, text and thin lines stay smooth at 640,
320 and 240 px alike. Every animation runs over ``LOOP`` (20 frames of
100 ms) and is periodic in it, so the GIF loops without a jump.
"""

import logging
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger(__name__)

FRAMES = 20
FRAME_MS = 100
LOOP = tuple(i / FRAMES for i in range(FRAMES))
SS = 2
REF = 640

FONT_FILES = {
    "regular": "Barlow-Regular.ttf",
    "medium": "Barlow-Medium.ttf",
    "semibold": "Barlow-SemiBold.ttf",
    "bold": "Barlow-Bold.ttf",
    "condensed": "BarlowCondensed-SemiBold.ttf",
    "mono": "IBMPlexMono-Medium.ttf",
}
FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


@lru_cache(maxsize=96)
def _font(fonts_dir: str, role: str, px: int):
    for path in (Path(fonts_dir) / FONT_FILES[role], Path(FALLBACK_FONT)):
        try:
            return ImageFont.truetype(str(path), px)
        except OSError:
            continue
    log.warning("no usable font in %s, using PIL's default", fonts_dir)
    return ImageFont.load_default()


def pulse(t: float, phase: float = 0.0) -> float:
    """0..1..0 once per loop."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * (t + phase))


def with_alpha(color: RGB, alpha: int) -> RGBA:
    return (*color, alpha)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    t = min(1.0, max(0.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


HEAT = ((0.0, (70, 200, 255)), (0.5, (120, 230, 170)), (0.75, (255, 196, 80)),
        (1.0, (255, 90, 60)))


def heat(value: float) -> RGB:
    """Cool blue through green and amber to red, for 0..1."""
    value = min(1.0, max(0.0, value))
    for (p0, c0), (p1, c1) in zip(HEAT, HEAT[1:], strict=False):
        if value <= p1:
            return mix(c0, c1, (value - p0) / (p1 - p0))
    return HEAT[-1][1]


class Canvas:
    """One frame, drawn at SS times the output size in reference units."""

    def __init__(self, size: int, fonts_dir: Path,
                 background: Image.Image | RGB = (0, 0, 0)) -> None:
        self.size = size
        self.fonts_dir = str(fonts_dir)
        self.px = size * SS
        self.scale = self.px / REF
        if isinstance(background, Image.Image):
            self.image = background.convert("RGBA").resize((self.px, self.px),
                                                           Image.Resampling.LANCZOS)
        else:
            self.image = Image.new("RGBA", (self.px, self.px), with_alpha(background, 255))

    def copy(self) -> "Canvas":
        twin = Canvas.__new__(Canvas)
        twin.__dict__.update(self.__dict__)
        twin.image = self.image.copy()
        return twin

    def p(self, value: float) -> float:
        return value * self.scale

    def w(self, value: float) -> int:
        return max(1, round(value * self.scale))

    @property
    def center(self) -> float:
        return self.px / 2

    def font(self, role: str, px: float):
        return _font(self.fonts_dir, role, max(6, round(px * self.scale)))

    def draw(self) -> ImageDraw.ImageDraw:
        return ImageDraw.Draw(self.image)

    def layer(self) -> Image.Image:
        return Image.new("RGBA", self.image.size, (0, 0, 0, 0))

    def glow(self, layer: Image.Image, radius: float, strength: float = 1.0) -> None:
        """Composite *layer* with a soft halo around it."""
        halo = layer.filter(ImageFilter.GaussianBlur(self.p(radius)))
        if strength != 1.0:
            alpha = halo.getchannel("A").point(lambda a: min(255, round(a * strength)))
            halo.putalpha(alpha)
        self.image.alpha_composite(halo)
        self.image.alpha_composite(layer)

    # -- primitives (reference units) ------------------------------------------

    def text(self, xy: tuple[float, float], text: str, role: str, px: float,
             fill: RGB | RGBA, anchor: str = "mm", tracking: float = 0.0,
             target: Image.Image | None = None) -> None:
        draw = ImageDraw.Draw(target or self.image)
        font = self.font(role, px)
        x, y = self.p(xy[0]), self.p(xy[1])
        if not tracking:
            draw.text((x, y), text, font=font, fill=fill, anchor=anchor)
            return
        widths = [draw.textlength(ch, font=font) for ch in text]
        total = sum(widths) + self.p(tracking) * (len(text) - 1)
        x -= {"l": 0, "m": total / 2, "r": total}[anchor[0]]
        for ch, width in zip(text, widths, strict=True):
            draw.text((x, y), ch, font=font, fill=fill, anchor="l" + anchor[1])
            x += width + self.p(tracking)

    def shadow_text(self, xy: tuple[float, float], text: str, role: str, px: float,
                    fill: RGB, anchor: str = "mm") -> None:
        """Text with a soft dark halo, readable on moving backgrounds."""
        shade = self.layer()
        self.text(xy, text, role, px, (0, 0, 0, 235), anchor, target=shade)
        self.image.alpha_composite(shade.filter(ImageFilter.GaussianBlur(self.p(3))))
        self.image.alpha_composite(shade.filter(ImageFilter.GaussianBlur(self.p(1))))
        self.text(xy, text, role, px, fill, anchor)

    def arc(self, radius: float, start: float, end: float, width: float,
            fill: RGB | RGBA, cap: bool = True, target: Image.Image | None = None) -> None:
        """Arc on a circle around the center; 0 degrees = 3 o'clock, clockwise."""
        draw = ImageDraw.Draw(target or self.image)
        c, r = self.center, self.p(radius)
        draw.arc([c - r, c - r, c + r, c + r], start, end, fill=fill, width=self.w(width))
        if cap:
            half = self.p(width) / 2
            for angle in (start, end):
                a = math.radians(angle)
                x, y = c + (r - half) * math.cos(a), c + (r - half) * math.sin(a)
                draw.ellipse([x - half, y - half, x + half, y + half], fill=fill)

    def polar(self, radius: float, angle_deg: float) -> tuple[float, float]:
        a = math.radians(angle_deg)
        return (self.center + self.p(radius) * math.cos(a),
                self.center + self.p(radius) * math.sin(a))

    def chart(self, box: tuple[float, float, float, float], values, color: RGB,
              lo: float | None = None, hi: float | None = None, area: int = 60,
              width: float = 2.2, highlight: float | None = None) -> None:
        """Area chart in *box*; *highlight* (0..1) puts a bright pulse on the
        line at that fraction of its length."""
        values = list(values)
        if len(values) < 2:
            return
        x0, y0, x1, y1 = (self.p(v) for v in box)
        lo = min(values) if lo is None else lo
        hi = max(values) if hi is None else hi
        span = (hi - lo) or 1.0
        points = [(x0 + (x1 - x0) * i / (len(values) - 1),
                   y1 - (y1 - y0) * (min(max(v, lo), hi) - lo) / span)
                  for i, v in enumerate(values)]
        layer = self.layer()
        draw = ImageDraw.Draw(layer)
        draw.polygon([*points, (x1, y1), (x0, y1)], fill=with_alpha(color, area))
        draw.line(points, fill=with_alpha(color, 255), width=self.w(width), joint="curve")
        end = points[-1]
        dot = self.p(4)
        draw.ellipse([end[0] - dot, end[1] - dot, end[0] + dot, end[1] + dot], fill=color)
        self.image.alpha_composite(layer)
        if highlight is not None:
            spark = self.layer()
            i = min(len(points) - 1, int(highlight * (len(points) - 1)))
            px, py = points[i]
            glow = self.p(6)
            ImageDraw.Draw(spark).ellipse([px - glow, py - glow, px + glow, py + glow],
                                          fill=(255, 255, 255, 220))
            self.glow(spark, 6, 1.4)

    def finish(self) -> Image.Image:
        """Average down to the output size and black out the corners."""
        out = self.image.convert("RGB").resize((self.size, self.size), Image.Resampling.BOX)
        mask = Image.new("L", (self.px, self.px), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, self.px - 1, self.px - 1], fill=255)
        mask = mask.resize((self.size, self.size), Image.Resampling.BOX)
        black = Image.new("RGB", out.size, (0, 0, 0))
        return Image.composite(out, black, mask)
