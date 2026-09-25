"""video.gif: an evening landscape standing in for a video loop.

Clouds drift by exactly one period of their (horizontally periodic) noise
per loop, the lake ripples with travelling waves of whole wavelengths, the
sun's glow breathes; the mountain ridges stand still. Rendered in true
color at twice the resolution.
"""

import math

import numpy as np
from PIL import Image

from .common import LOOP, SIZE, blur, grid, smoothstep

COLORS = 46
SS = 2
N = SIZE * SS
HORIZON = 0.62  # of the height, from the top


def _periodic_noise(rng, height: int, width: int, k_hi: int, slope: float):
    """Noise that is periodic across the width (integer frequencies)."""
    k = np.fft.fftfreq(width, 1 / width)
    ky = np.fft.fftfreq(height, 1 / height)
    kx_grid, ky_grid = np.meshgrid(k, ky)
    kk = np.hypot(kx_grid * 2.5, ky_grid)  # clouds are wide, not tall
    amp = np.where((kk > 0) & (kk < k_hi), np.maximum(kk, 1) ** -slope, 0)
    field = np.fft.ifft2(amp * np.exp(2j * math.pi * rng.random(kk.shape))).real
    return (field - field.mean()) / field.std()


def _ridge(rng, base: float, amp: float, rough: int) -> np.ndarray:
    xs = np.linspace(0, 1, N)
    line = np.full(N, base)
    for k in range(1, rough):
        line += amp * k ** -1.4 * np.sin(2 * math.pi * k * xs + rng.uniform(0, 2 * math.pi))
    return line


def _upscale(field: np.ndarray) -> np.ndarray:
    return np.asarray(Image.fromarray(field.astype(np.float32), "F").resize(
        (N, N), Image.Resampling.BICUBIC))


def render(rng) -> list[np.ndarray]:
    x, y, r = grid(N)
    u, v = x + 0.5, y + 0.5  # 0..1 from the left and from the top
    sky_top, sky_mid, sky_low = (np.array(c) for c in ((0.06, 0.03, 0.16), (0.42, 0.13, 0.30),
                                                        (1.00, 0.52, 0.22)))
    t_sky = np.clip(v / HORIZON, 0, 1)[..., None]
    sky = np.where(t_sky < 0.6, sky_top + (sky_mid - sky_top) * (t_sky / 0.6),
                   sky_mid + (sky_low - sky_mid) * ((t_sky - 0.6) / 0.4))
    sun_x, sun_y, sun_r = 0.52, HORIZON - 0.05, 0.075
    dist = np.hypot(u - sun_x, v - sun_y)
    sun_disc = np.clip((sun_r - dist) * N * 0.5 + 0.5, 0, 1)[..., None]
    halo = np.exp(-dist / 0.10)[..., None]
    # two independent cloud fields; blending them around a circle morphs
    # the clouds continuously and returns to the start after one loop
    clouds_a = _periodic_noise(rng, N // 4, N // 4, 9, 1.6)
    clouds_b = _periodic_noise(rng, N // 4, N // 4, 9, 1.6)
    band = (np.exp(-((v - 0.28) / 0.12) ** 2) + 0.6 * np.exp(-((v - 0.46) / 0.06) ** 2))[..., None]
    lit = np.clip(1 - np.abs(v - sun_y) / 0.5, 0, 1)[..., None]
    cloud_rgb = np.array((0.30, 0.12, 0.28)) * (1 - lit) + np.array((1.0, 0.55, 0.35)) * lit
    ridges = [(_ridge(rng, HORIZON - 0.02, 0.05, 9), np.array((0.20, 0.08, 0.20))),
              (_ridge(rng, HORIZON + 0.03, 0.04, 7), np.array((0.10, 0.04, 0.12))),
              (_ridge(rng, HORIZON + 0.08, 0.035, 6), np.array((0.035, 0.015, 0.05)))]
    shore = HORIZON + 0.14
    lake = np.clip((v - shore) * N + 0.5, 0, 1)[..., None]
    depth = np.clip((v - shore) / 0.3, 0, 1)
    ripples = [(int(rng.integers(3, 9)), int(rng.integers(40, 120)), rng.uniform(0, 2 * math.pi))
               for _ in range(14)]
    columns = np.arange(N)[None, :].repeat(N, 0)
    frames = []
    for t in LOOP:
        angle = 2 * math.pi * t
        cloud = _upscale(clouds_a * math.cos(angle) + clouds_b * math.sin(angle))
        cloud = np.roll(cloud, int(round(12 * SS * math.sin(angle))), axis=1)  # a gentle sway
        cover = smoothstep(0.2, 1.4, cloud)[..., None] * band * 0.85
        glow = 0.45 * (1 + 0.08 * math.sin(angle))
        img = sky + np.array((1.0, 0.62, 0.30)) * halo * glow
        img = img * (1 - cover) + cloud_rgb * cover
        img = img * (1 - sun_disc) + np.array((1.0, 0.86, 0.60)) * sun_disc
        for line, color in ridges:
            ground = np.clip((v - line[None, :]) * N + 0.5, 0, 1)[..., None]
            img = img * (1 - ground) + color * ground
        # the lake mirrors everything above the shore, broken by ripples
        # that travel whole wavelengths per loop
        wave = sum(np.cos(2 * math.pi * (kx * u + ky * np.sqrt(depth) - t) + ph)
                   for kx, ky, ph in ripples) / len(ripples)
        mirror = np.clip(2 * shore - v + 0.02 * wave, 0, 1)
        rows = np.clip((mirror * (N - 1)).astype(int), 0, N - 1)
        reflection = img[rows, columns] * (0.55 - 0.25 * depth)[..., None]
        sparkle = np.clip(wave * 3 - 1.6, 0, 1) * np.exp(-np.abs(u - sun_x) / 0.06)
        reflection = reflection + np.array((1.0, 0.75, 0.45)) * sparkle[..., None] * 0.8
        img = img * (1 - lake) + reflection * lake
        img = np.clip(img, 0, 1) * (r < 0.5)[..., None]
        frames.append(_soften(img).reshape(SIZE, SS, SIZE, SS, 3).mean(axis=(1, 3)))
    return frames


def _soften(img: np.ndarray) -> np.ndarray:
    """A touch of softness, like a video frame rather than vector art."""
    return np.stack([blur(img[..., i], 0.8) for i in range(3)], axis=-1)
