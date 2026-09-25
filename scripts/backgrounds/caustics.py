"""liquid.gif: pool-floor caustics from a looping wave spectrum."""

import math

import numpy as np

from .common import LEVELS, LOOP, SIZE, blur, display_mask, grid, smoothstep, splat, text_shade

PALETTE = ((((0.00, "#000000"), (0.10, "#010816"), (0.28, "#04264d"),
             (0.50, "#08609a"), (0.70, "#1aa9d6"), (0.86, "#7fe6fa"),
             (1.00, "#f4feff")), LEVELS),)


def render(rng) -> list[np.ndarray]:
    """The height field is a sum of plane waves on a torus. Shorter waves
    travel one or two wavelengths per loop; the long swell only sways back
    and forth, since a whole wavelength in two seconds would race across
    the display. Either way the surface repeats exactly. Two light rays per
    pixel and axis are refracted by the surface slope and splatted onto the
    floor; their density is the caustic brightness.
    """
    n = 2 * SIZE
    k = np.fft.fftfreq(n, 1 / n)  # integer cycles per display width
    kx, ky = np.meshgrid(k, k)
    kk = np.hypot(kx, ky)
    # waves of 2-12 cycles per width; the steep falloff keeps the caustic
    # cells large (~120 px): fewer, brighter lines
    band = np.maximum(kk, 1)
    amp = np.where((kk >= 2) & (kk <= 12), band ** -3.0, 0)
    spectrum = amp * np.exp(2j * math.pi * rng.random(kk.shape))
    swell = kk < 6
    speed = np.where(kk > 9, 2, 1)  # shorter waves travel a bit further
    sway = 2 * math.pi * rng.random(kk.shape)
    to_px = 2j * math.pi / SIZE
    lap = np.fft.ifft2(spectrum * (to_px ** 2) * (kx ** 2 + ky ** 2)).real
    focus = 0.95 / lap.std()  # strong enough to form sharp focal lines

    pos = (np.arange(n) + 0.5) / 2  # ray origins in display pixels
    px0, py0 = np.meshgrid(pos, pos)
    x, y, r = grid()
    fields = []
    for t in LOOP:
        phase = np.where(swell, 0.9 * np.sin(2 * math.pi * t + sway),
                         -2 * math.pi * speed * t)
        h = spectrum * np.exp(1j * phase)
        gx = np.fft.ifft2(h * to_px * kx).real
        gy = np.fft.ifft2(h * to_px * ky).real
        density = blur(splat(px0 + focus * gx, py0 + focus * gy, SIZE) / 4, 0.9)
        # soft highlight roll-off: only the focal points reach white
        fields.append(1 - np.exp(-np.maximum(density - 0.75, 0) / 1.8))

    # light from above: brighter water and stronger caustics toward the top
    depth = 0.5 - y  # 1 at the top edge, 0 at the bottom edge
    water = 0.12 + 0.12 * depth + 0.10 * np.exp(-((x - 0.08) ** 2 + (y + 0.42) ** 2) / 0.10)
    strength = (0.55 + 0.45 * depth) * text_shade(r, 0.5)
    vignette = 1 - 0.45 * smoothstep(0.30, 0.50, r)
    return [(water + 0.85 * c * strength) * vignette * display_mask(r) for c in fields]
