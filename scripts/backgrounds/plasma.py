"""temp.gif: a plasma globe, its core between the two values."""

import math

import numpy as np
from PIL import Image, ImageDraw

from .common import LEVELS, LOOP, SIZE, blur, display_mask, grid, smoothstep

PALETTE = ((((0.00, "#000000"), (0.10, "#07021a"), (0.26, "#1e0b4a"),
             (0.45, "#4c1b90"), (0.62, "#8f31c6"), (0.78, "#d551cf"),
             (0.90, "#ff96da"), (1.00, "#fff1fb")), LEVELS),)


def render(rng) -> list[np.ndarray]:
    """Each filament sways about its angle, ripples crawl outward along it
    by whole wavelengths per loop, and it flares where it touches the glass.
    """
    count = 8
    ss = 2
    big = SIZE * ss
    core = 0.045
    radii = np.linspace(core * 0.6, 0.492, 90)
    along = (radii - radii[0]) / (radii[-1] - radii[0])  # 0 at the core, 1 at the glass
    angles = rng.uniform(0, 2 * math.pi) + 2 * math.pi * (
        np.arange(count) + rng.uniform(-0.25, 0.25, count)) / count
    sway = rng.uniform(0.10, 0.22, count)
    sway_phase = rng.random(count)
    curl = rng.uniform(-0.8, 0.8, count)
    flicker_m = rng.integers(1, 3, count)
    flicker_phase = rng.random(count)
    ripple_k = np.arange(1, 5)
    ripple_amp = rng.uniform(0.5, 1.0, (count, len(ripple_k))) * ripple_k ** -1.2
    ripple_phase = rng.uniform(0, 2 * math.pi, (count, len(ripple_k)))
    forks = rng.integers(1, 3, count)
    fork_from = int(len(radii) * 0.72)

    def polyline(angle, radius):
        return list(zip(big / 2 + np.cos(angle) * radius * big,
                        big / 2 + np.sin(angle) * radius * big, strict=True))

    x, y, r = grid()
    fields = []
    for t in LOOP:
        image = Image.new("L", (big, big))
        draw = ImageDraw.Draw(image)
        for i in range(count):
            ripple = sum(a * np.sin(2 * math.pi * (k * along - t) + ph)
                         for a, k, ph in zip(ripple_amp[i], ripple_k, ripple_phase[i], strict=True))
            angle = (angles[i] + sway[i] * math.sin(2 * math.pi * (t + sway_phase[i]))
                     + 0.35 * curl[i] * along ** 2 + 0.10 * along * ripple)
            level = 0.75 + 0.25 * math.cos(2 * math.pi * (flicker_m[i] * t + flicker_phase[i]))
            draw.line(polyline(angle, radii), fill=round(255 * level), width=6, joint="curve")
            # the filament splits shortly before the glass
            tail = (along[fork_from:] - along[fork_from]) / (1 - along[fork_from])
            for f in range(forks[i]):
                spread = (f + 1) * 0.07 * (1 if (i + f) % 2 else -1)
                draw.line(polyline(angle[fork_from:] + spread * tail ** 1.3, radii[fork_from:]),
                          fill=round(180 * level), width=4, joint="curve")
            ex, ey = polyline(angle[-1:], radii[-1:])[0]
            flare = 5 * ss
            draw.ellipse([ex - flare, ey - flare, ex + flare, ey + flare], fill=round(255 * level))
        lines = np.asarray(image.resize((SIZE, SIZE), Image.Resampling.BOX), float) / 255
        fields.append(0.9 * lines + 0.9 * blur(lines, 3.0) + 0.45 * blur(lines, 9.0))

    glow = 1.1 * np.exp(-(r / core) ** 2) + 0.25 * np.exp(-r / 0.08)
    glass = 0.05 + 0.10 * np.exp(-(0.5 - r) / 0.02)
    # filaments pass behind the two values a little dimmer
    shade = 0.6 + 0.4 * smoothstep(0.10, 0.30, np.hypot(np.abs(x) - 0.25, y + 0.03))
    return [(f * shade + glow + glass) * display_mask(r) for f in fields]
