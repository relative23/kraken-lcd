#!/usr/bin/env python3
"""Generate the default background GIFs (procedural, license-free).

Each background is a seamlessly looping "plasma" animation: layered sine
fields evaluated at low resolution and upscaled, colored by a three-stop
gradient. Regenerate with:

    python3 scripts/generate_backgrounds.py [output_dir]

The shipped assets/ were produced by exactly this script, so users can
tweak SCHEMES below and rebuild their own set.
"""

import math
import sys
from pathlib import Path

from PIL import Image

SIZE = 640          # output resolution (the renderer rescales per device)
GRID = 160          # plasma is computed here, then upscaled (it is smooth)
FRAMES = 20
FRAME_MS = 90
COLORS = 64         # shared palette keeps the files small (~1 MB)

# name -> (three gradient stops dark->mid->bright, sine multipliers a,b,c,d)
SCHEMES = {
    "liquid": (((8, 16, 48), (24, 96, 192), (168, 232, 255)), (2, 3, 1, 2)),
    "cpu": (((48, 12, 8), (200, 88, 16), (255, 224, 128)), (3, 2, 2, 1)),
    "gpu": (((6, 32, 20), (16, 144, 96), (176, 255, 208)), (2, 2, 3, 2)),
    "temp": (((32, 8, 48), (144, 32, 160), (255, 176, 224)), (1, 3, 2, 3)),
}


def _gradient(stops, value: float) -> tuple[int, int, int]:
    """Map value in [0, 1] onto a three-stop gradient."""
    lo, mid, hi = stops
    if value < 0.5:
        a, b, t = lo, mid, value * 2
    else:
        a, b, t = mid, hi, (value - 0.5) * 2
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _frame(stops, mult, phase: float) -> Image.Image:
    a, b, c, d = mult
    img = Image.new("RGB", (GRID, GRID))
    pixels = img.load()
    for y in range(GRID):
        ny = y / GRID * 2 * math.pi
        for x in range(GRID):
            nx = x / GRID * 2 * math.pi
            radius = math.hypot(x - GRID / 2, y - GRID / 2) / GRID * 2 * math.pi
            value = (math.sin(nx * a + phase)
                     + math.sin(ny * b - phase)
                     + math.sin((nx + ny) * c / 2 + 2 * phase)
                     + math.sin(radius * d - 2 * phase))
            pixels[x, y] = _gradient(stops, (value + 4) / 8)
    return img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def generate(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (stops, mult) in SCHEMES.items():
        # integer multiples of 2*pi/FRAMES -> the animation loops seamlessly
        frames = [_frame(stops, mult, 2 * math.pi * i / FRAMES)
                  for i in range(FRAMES)]
        palette = frames[0].quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT)
        frames = [f.quantize(colors=COLORS, palette=palette, dither=Image.Dither.NONE)
                  for f in frames]
        path = out_dir / f"{name}.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=FRAME_MS, loop=0, optimize=True)
        print(f"{path}  {path.stat().st_size / 2**20:.2f} MB, {FRAMES} frames")


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else
             Path(__file__).resolve().parent.parent / "assets")
