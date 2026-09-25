"""cpu.gif: a ring of fire, the value on the dark hearth in the middle.

Flames the way a fire shader builds them, made to loop: two periodic
noise fields blended around a circle morph continuously and return to
the start after one loop; scrolled outward by whole periods they rise;
a second noise bends the sampling so tongues fork and curl; a threshold
that grows with height tapers them into tips. Sparks rise with the
flames and wink out before they wrap. Colour follows a black-body ramp,
a bloom of the brightest parts lights the smoke above, and embers glow
on the rim of the hearth. Rendered in true colour at twice the size.
"""

import math

import numpy as np

from .common import LOOP, SIZE, blur, grid, ramp, smoothstep, to_linear

COLORS = 46
# The renderer draws "100%" out to 0.347 of the width (shadow included);
# the hearth covers that with a margin.
HEARTH = 0.36
SS = 2
N = SIZE * SS
TEX = 512  # noise texture, a torus of (height, angle)
# angle runs 2.7 display units around the ring, height 0.14 out to the edge;
# tongues are taller than wide, so the noise is stretched along the height
ASPECT = (2 * math.pi * (HEARTH + 0.5) / 2) / (0.5 - HEARTH) * 0.45

BLACK_BODY = ((0.00, "#000000"), (0.12, "#1a0400"), (0.28, "#5a0d02"), (0.45, "#b3300a"),
              (0.62, "#f07414"), (0.78, "#ffb531"), (0.90, "#ffe89a"), (1.00, "#fffbe8"))


def _noise(rng, k_lo: float, k_hi: float, slope: float) -> np.ndarray:
    """Periodic noise on the texture torus, isotropic in display units."""
    k = np.fft.fftfreq(TEX, 1 / TEX)
    kx, ky = np.meshgrid(k, k)  # kx along the angle, ky along the height
    kk = np.hypot(kx, ky * ASPECT)
    amp = np.where((kk >= k_lo) & (kk <= k_hi), np.maximum(kk, 1) ** -slope, 0)
    field = np.fft.ifft2(amp * np.exp(2j * math.pi * rng.random(kk.shape))).real
    return (field - field.mean()) / field.std()


def _sample(tex: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Bilinear lookup on the torus at fractional texture coordinates."""
    r0, c0 = np.floor(rows), np.floor(cols)
    fr, fc = rows - r0, cols - c0
    r0, c0 = r0.astype(int) % TEX, c0.astype(int) % TEX
    r1, c1 = (r0 + 1) % TEX, (c0 + 1) % TEX
    top = tex[r0, c0] * (1 - fc) + tex[r0, c1] * fc
    bottom = tex[r1, c0] * (1 - fc) + tex[r1, c1] * fc
    return top * (1 - fr) + bottom * fr


def _morph(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """A noise that changes continuously and is itself after one loop."""
    return a * math.cos(2 * math.pi * t) + b * math.sin(2 * math.pi * t)


def _sparks(rng, count: int = 70):
    return [(rng.random(), rng.random(), int(rng.integers(1, 3)), rng.uniform(1.2, 2.4),
             rng.uniform(0.5, 1.0), int(rng.integers(2, 6)), rng.random())
            for _ in range(count)]


def _splat_sparks(sparks, t: float, x, y) -> np.ndarray:
    """Bright dots rising with the flames, fading before they wrap."""
    field = np.zeros((N, N))
    px, py = (x + 0.5) * N, (y + 0.5) * N
    for angle0, h0, laps, size, bright, twinkle, phase in sparks:
        h = (h0 + laps * t) % 1.0
        alpha = smoothstep(0.0, 0.12, h) * (1 - smoothstep(0.55, 0.95, h))
        alpha *= 0.55 + 0.45 * math.cos(2 * math.pi * (twinkle * t + phase))
        if alpha <= 0.02:
            continue
        angle = 2 * math.pi * (angle0 + 0.012 * math.sin(2 * math.pi * (2 * t + phase)))
        radius = HEARTH + h * (0.5 - HEARTH)
        cx, cy = (0.5 + radius * math.cos(angle)) * N, (0.5 + radius * math.sin(angle)) * N
        x0, y0 = int(cx) - 6, int(cy) - 6
        if not (0 <= x0 < N - 12 and 0 <= y0 < N - 12):
            continue
        window = (slice(y0, y0 + 12), slice(x0, x0 + 12))
        d2 = (px[window] - cx) ** 2 + (py[window] - cy) ** 2
        field[window] += 1.6 * bright * alpha * np.exp(-d2 / (2 * (size * SS / 2) ** 2))
    return field


def render(rng) -> list[np.ndarray]:
    x, y, r = grid(N)
    angle = (np.arctan2(y, x) / (2 * math.pi)) % 1.0
    height = np.clip((r - HEARTH) / (0.5 - HEARTH), 0, 1)
    band = (r >= HEARTH - 0.01) & (r < 0.5)
    ang, hgt = angle[band], height[band]

    coarse = (_noise(rng, 3, 18, 1.7), _noise(rng, 3, 18, 1.7))
    fine = (_noise(rng, 14, 80, 1.5), _noise(rng, 14, 80, 1.5))
    warp_a = (_noise(rng, 1, 8, 1.8), _noise(rng, 1, 8, 1.8))
    warp_h = (_noise(rng, 1, 8, 1.8), _noise(rng, 1, 8, 1.8))
    crust = _noise(rng, 6, 80, 1.2)[0]  # one row: embers along the hearth rim
    sparks = _sparks(rng)
    lut = to_linear(ramp(BLACK_BODY, 1024) / 255)
    ember_rgb = to_linear(np.array([255, 96, 20]) / 255)
    glow_rgb = to_linear(np.array([255, 150, 60]) / 255)
    inside = np.clip((HEARTH - r) * N + 0.5, 0, 1)  # anti-aliased hearth edge
    rim = np.exp(-np.maximum(HEARTH - r, 0) / 0.006) * inside
    display = (r < 0.5)[..., None]

    frames = []
    for t in LOOP:
        # the base scrolls outward one period per loop, the fine detail two
        rows_base = (hgt - t) * TEX
        rows_fine = (2 * hgt - 2 * t) * TEX
        cols = ang * TEX
        bend_a = _sample(_morph(*warp_a, t), rows_base * 0.5, cols)
        bend_h = _sample(_morph(*warp_h, t), rows_base * 0.5, cols + TEX / 3)
        cols_w = cols + 0.022 * TEX * bend_a * (0.2 + hgt)  # tongues lean as they rise
        rows_w = rows_base + 0.05 * TEX * bend_h
        n = (0.7 * _sample(_morph(*coarse, t), rows_w, cols_w)
             + 0.3 * _sample(_morph(*fine, t), rows_fine + 0.06 * TEX * bend_h, cols_w))
        n01 = 0.5 + 0.5 * np.clip(n / 2.0, -1, 1)
        # solid and white-hot at the hearth, only the strongest tongues
        # reach the edge; a steep ramp keeps the tongue edges crisp
        flame = np.clip((n01 * 2.3 + 0.25 - 2.1 * hgt) * 1.3, 0, 1.6) + 0.5 * (1 - hgt) ** 4
        intensity = np.zeros((N, N))
        intensity[band] = flame
        intensity *= 1 - inside  # nothing burns on the hearth itself
        tone = 1 - np.exp(-1.6 * intensity)
        rgb = lut[np.clip((tone * 1023).astype(int), 0, 1023)]
        # bloom: the hottest parts light the air around them
        hot = np.clip(intensity - 0.7, 0, None)
        rgb = rgb + (0.35 * blur(hot, 6.0) + 0.12 * blur(hot, 22.0))[..., None] * glow_rgb
        rgb = rgb + _splat_sparks(sparks, t, x, y)[..., None] * np.array([1.0, 0.85, 0.55])
        # embers: the hearth's rim glows, brighter where the crust breaks
        ember = rim * (0.35 + 0.65 * (0.5 + 0.5 * np.interp(
            angle, np.linspace(0, 1, TEX, endpoint=False), crust, period=1.0))) \
            * (0.8 + 0.2 * math.sin(2 * math.pi * t))
        rgb = rgb + ember[..., None] * ember_rgb * 0.9
        rgb = np.clip(rgb, 0, 1) * display
        frames.append(rgb.reshape(SIZE, SS, SIZE, SS, 3).mean(axis=(1, 3)))
    return frames
