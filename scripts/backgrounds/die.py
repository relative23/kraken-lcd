"""ram.gif: a memory die and the traces feeding it, with data pulses.

A dark board, copper traces running from the rim to pads at the edge of
the die in the middle and bending at 45 degrees like a real layout.
Pulses travel along them, in toward the die and out from it, whole
traces per loop, each with a soft trail and a flash at the pad where it
arrives. The die's rim breathes faintly. Drawn at twice the size and
averaged down, in true colour.
"""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .common import LOOP, SIZE, grid, to_linear

COLORS = 46
DIE = 0.365          # the die's radius; the value sits inside
SS = 2
N = SIZE * SS
TRACES = 46

BOARD = (7, 11, 9)
COPPER = (138, 78, 34)
COPPER_LIT = (196, 120, 52)
CYAN = (170, 240, 255)
AMBER = (255, 190, 90)


def _unit(angle: float) -> tuple[float, float]:
    return math.cos(angle), math.sin(angle)


def _trace(rng, index: int) -> list[tuple[float, float]]:
    """Polyline in display units from the rim to a pad on the die."""
    angle = 2 * math.pi * (index + rng.uniform(-0.25, 0.25)) / TRACES
    ux, uy = _unit(angle)
    tx, ty = -uy, ux
    start = 0.53
    bend = rng.uniform(0.41, 0.47)
    jog = rng.uniform(-0.035, 0.035) * (1 if rng.random() < 0.85 else 0)
    p0 = (0.5 + start * ux, 0.5 + start * uy)
    p1 = (0.5 + bend * ux, 0.5 + bend * uy)
    p2 = (p1[0] - abs(jog) * ux + jog * tx, p1[1] - abs(jog) * uy + jog * ty)
    radius = math.hypot(p2[0] - 0.5, p2[1] - 0.5)
    p3 = (p2[0] - (radius - DIE) * (p2[0] - 0.5) / radius, p2[1] - (radius - DIE) * (p2[1] - 0.5) / radius)
    return [p0, p1, p2, p3] if jog else [p0, p3]


def _along(points, s: float) -> tuple[float, float]:
    """Point at fraction *s* of the polyline's length."""
    lengths = [math.dist(a, b) for a, b in zip(points[:-1], points[1:], strict=True)]
    target = s * sum(lengths)
    for a, b, length in zip(points[:-1], points[1:], lengths, strict=True):
        if target <= length or length == lengths[-1] and b == points[-1]:
            f = min(1.0, target / length) if length else 0.0
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        target -= length
    return points[-1]


def _px(p) -> tuple[float, float]:
    return p[0] * N, p[1] * N


def _dot(draw, p, radius: float, fill) -> None:
    x, y = _px(p)
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill)


def render(rng) -> list[np.ndarray]:
    traces = [_trace(rng, i) for i in range(TRACES)]
    inward = rng.random(TRACES) < 0.7
    laps = rng.integers(1, 3, TRACES)
    phases = rng.random(TRACES)
    doubled = rng.random(TRACES) < 0.3  # two pulses on the wire at once
    _, _, r = grid(N)
    display = (r < 0.5)[..., None]

    board = Image.new("RGB", (N, N), BOARD)
    draw = ImageDraw.Draw(board)
    for points in traces:
        draw.line([_px(p) for p in points], fill=COPPER, width=3 * SS, joint="curve")
        _dot(draw, points[0], 4.5 * SS, COPPER_LIT)
        _dot(draw, points[0], 2.2 * SS, BOARD)
        _dot(draw, points[-1], 4.0 * SS, COPPER_LIT)
    # the die: black, with a metal seal ring
    c = N / 2
    draw.ellipse([c - DIE * N, c - DIE * N, c + DIE * N, c + DIE * N], fill=(0, 0, 0),
                 outline=(150, 140, 120), width=2 * SS)
    board_lin = to_linear(np.asarray(board, float) / 255)

    frames = []
    for t in LOOP:
        layer = Image.new("RGB", (N, N), (0, 0, 0))
        ldraw = ImageDraw.Draw(layer)
        for i, points in enumerate(traces):
            path = points if inward[i] else points[::-1]
            color = CYAN if inward[i] else AMBER
            for k in range(2 if doubled[i] else 1):
                head = (phases[i] + laps[i] * t + k / 2) % 1.0
                for step in range(14):  # the trail behind the pulse
                    s = head - step * 0.018
                    if s < 0:
                        break
                    fade = (1 - step / 14) ** 2
                    _dot(ldraw, _along(path, s), (3.2 - 1.4 * step / 14) * SS,
                         tuple(round(v * fade) for v in color))
                arrive = max(0.0, (head - 0.9) / 0.1)
                if arrive > 0:  # flash at the pad
                    _dot(ldraw, path[-1], (5 + 6 * arrive) * SS, tuple(round(v * arrive) for v in color))
        glow = layer.filter(ImageFilter.GaussianBlur(4 * SS))
        wide = layer.filter(ImageFilter.GaussianBlur(14 * SS))
        pulses = (to_linear(np.asarray(layer, float) / 255) + 0.8 * to_linear(np.asarray(glow, float) / 255)
                  + 0.5 * to_linear(np.asarray(wide, float) / 255))
        breath = 0.85 + 0.15 * math.sin(2 * math.pi * t)
        seal = np.exp(-np.abs(r - DIE) / 0.004)[..., None] * to_linear(np.array([255, 200, 140]) / 255) * 0.25 * breath
        rgb = np.clip(board_lin + pulses + seal, 0, 1) * display
        rgb[r < DIE - 0.004] = 0  # the die stays black for the value
        frames.append(rgb.reshape(SIZE, SS, SIZE, SS, 3).mean(axis=(1, 3)))
    return frames
