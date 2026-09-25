"""Loop timing, drawing helpers, palettes and the GIF encoder shared by
all scenes."""

import math
from pathlib import Path

import numpy as np
from PIL import Image

SIZE = 640          # output resolution (the renderer rescales per device)
# The renderer keeps at most max_frames frames per tile and cuts the rest,
# which would break the loop: stay at or below the smallest per-tile cap.
FRAMES = 20
FRAME_MS = 100      # 2 s loop
# palette entries per background; the renderer re-quantizes each tile to
# render.colors (as low as 40 per tile), and the text needs a few of those
LEVELS = 32

LOOP = np.arange(FRAMES) / FRAMES  # loop phase of each frame, in [0, 1)

# A ramp palette is a gradient of (position 0..1, "#rrggbb") stops and its
# number of entries; position 0 must be black, it fills everything outside
# the round display. True-color scenes find their palette in the encoder.
Stops = tuple[tuple[float, str], ...]
Palette = tuple[tuple[Stops, int], ...]


def grid(n: int = SIZE):
    """Pixel-center coordinates in display units: center 0, edge radius 0.5."""
    c = (np.arange(n) + 0.5) / n - 0.5
    x, y = np.meshgrid(c, c)  # y grows downward, like the image
    return x, y, np.hypot(x, y)


def smoothstep(edge0: float, edge1: float, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def display_mask(r):
    """Coverage of the round display, anti-aliased over one pixel."""
    return np.clip((0.5 - r) * SIZE, 0.0, 1.0)


def text_shade(r, floor: float):
    """Dim moving detail near the center, where label and value sit."""
    return floor + (1 - floor) * smoothstep(0.12, 0.40, r)


def blur(a, sigma: float):
    """Gaussian blur on a torus (FFT); fine here, the edges are black."""
    fy = np.fft.fftfreq(a.shape[0])[:, None]
    fx = np.fft.rfftfreq(a.shape[1])[None, :]
    kernel = np.exp(-2 * math.pi ** 2 * sigma ** 2 * (fx ** 2 + fy ** 2))
    return np.fft.irfft2(np.fft.rfft2(a) * kernel, s=a.shape)


def splat(px, py, n: int, weights=None, wrap: bool = True):
    """Bilinear splat of points (in pixels) onto an n x n grid: density.

    *wrap* treats the grid as a torus; otherwise points off the grid are
    dropped. *weights* scales each point (default 1).
    """
    x0, y0 = np.floor(px - 0.5), np.floor(py - 0.5)
    fx, fy = px - 0.5 - x0, py - 0.5 - y0
    x0, y0 = x0.astype(int), y0.astype(int)
    total = np.zeros(n * n)
    for dx, wx in ((0, 1 - fx), (1, fx)):
        for dy, wy in ((0, 1 - fy), (1, fy)):
            w = wx * wy if weights is None else wx * wy * weights
            xi, yi = x0 + dx, y0 + dy
            if wrap:
                cells = (yi % n) * n + xi % n
                total += np.bincount(cells.ravel(), w.ravel(), n * n)
            else:
                ok = (xi >= 0) & (xi < n) & (yi >= 0) & (yi < n)
                total += np.bincount((yi * n + xi)[ok], w[ok], n * n)
    return total.reshape(n, n)


# -- palette -------------------------------------------------------------------

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])


def to_linear(srgb):
    """sRGB in [0, 1] to linear light."""
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def linear_to_oklab(linear):
    return np.cbrt(linear @ _M1.T) @ _M2.T


def _to_oklab(rgb):
    return linear_to_oklab(to_linear(np.asarray(rgb, float) / 255))


def _from_oklab(lab):
    linear = np.clip(((lab @ np.linalg.inv(_M2).T) ** 3) @ np.linalg.inv(_M1).T, 0, 1)
    srgb = np.where(linear <= 0.0031308, 12.92 * linear,
                    1.055 * linear ** (1 / 2.4) - 0.055)
    return np.rint(srgb * 255).astype(np.uint8)


def ramp(stops: Stops, levels: int) -> np.ndarray:
    """*levels* colors along the gradient, mixed in OKLab so hue transitions
    stay clean instead of passing through gray."""
    positions = [p for p, _ in stops]
    lab = np.array([_to_oklab([int(c[i:i + 2], 16) for i in (1, 3, 5)])
                    for _, c in stops])
    t = np.linspace(0, 1, levels)
    mixed = np.stack([np.interp(t, positions, lab[:, i]) for i in range(3)], axis=1)
    return _from_oklab(mixed)


# -- encoder -------------------------------------------------------------------

def _bayer(n: int) -> np.ndarray:
    """n x n ordered-dither thresholds in [-0.5, 0.5)."""
    m = np.zeros((1, 1))
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m + 0.5) / m.size - 0.5


# With only a few dozen steps, smooth dark gradients show contour rings. An
# ordered dither hides them at the LCD's pixel density. It is applied only
# where a pixel sits at its resting value (its minimum over the loop): the
# still backdrop gets smooth, while moving detail, where dither would only
# cost upload size, stays undithered.
DITHER = np.tile(_bayer(4), (SIZE // 4, SIZE // 4))


def save_gif(path: Path, frames: list, palette: Palette) -> Path:
    """Map brightness fields onto a ramp palette and write a looping GIF."""
    (stops, levels), = palette
    colors = ramp(stops, levels).tobytes()
    resting = np.min(frames, axis=0)
    images = []
    for value in frames:
        level = np.clip(value, 0, 1) * (levels - 1)
        level = np.where(value <= resting, level + DITHER, level)
        index = np.clip(np.rint(level), 0, levels - 1)
        image = Image.frombytes("P", (SIZE, SIZE), index.astype(np.uint8).tobytes())
        image.putpalette(colors)
        images.append(image)
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=FRAME_MS, loop=0, optimize=True)
    return path


def _kmeans(samples, count: int, rounds: int = 24):
    """Deterministic k-means in OKLab, seeded along lightness quantiles
    (the colors of a scene mostly lie on ramps, so this starts close)."""
    order = np.argsort(samples[:, 0], kind="stable")
    centers = samples[order[np.linspace(0, len(order) - 1, count).astype(int)]].copy()
    for _ in range(rounds):
        nearest = ((samples[:, None, :] - centers[None]) ** 2).sum(axis=2).argmin(axis=1)
        for i in range(count):
            members = samples[nearest == i]
            if len(members):
                centers[i] = members.mean(axis=0)
    return centers


def save_gif_true_color(path: Path, frames: list, colors: int) -> Path:
    """Write true-color frames (linear RGB in [0, 1]) as a looping GIF.

    One palette of *colors* entries serves the whole loop: black plus
    k-means centers over pixels from every frame. Pixels map to it with an
    ordered dither along lightness, which hides banding and, being fixed
    in place, does not crawl from frame to frame. Exact black stays black,
    so the moon and the corners never pick up dither noise.
    """
    labs = [linear_to_oklab(f.reshape(-1, 3)) for f in frames]
    lit = np.concatenate([lab[lab[:, 0] > 0.02][::37] for lab in labs])
    # colorful pixels are rare but carry the accent: give them more weight
    chroma = np.hypot(lit[:, 1], lit[:, 2])
    samples = np.concatenate([lit, np.repeat(lit[chroma > 0.12], 3, axis=0)])
    centers = np.vstack([[0.0, 0.0, 0.0], _kmeans(samples, colors - 1)])
    lightness = np.sort(centers[:, 0])
    step = float(np.median(np.diff(lightness)))
    offset = DITHER.reshape(-1) * step
    palette = _from_oklab(centers).tobytes()
    images = []
    for lab in labs:
        shifted = lab.copy()
        shifted[:, 0] += offset
        index = np.empty(len(lab), dtype=np.uint8)
        for start in range(0, len(lab), 65536):  # bounded memory
            part = shifted[start:start + 65536]
            index[start:start + 65536] = (
                (part[:, None, :] - centers[None]) ** 2).sum(axis=2).argmin(axis=1)
        index[lab[:, 0] <= 1e-6] = 0
        image = Image.frombytes("P", (SIZE, SIZE), index.tobytes())
        image.putpalette(palette)
        images.append(image)
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=FRAME_MS, loop=0, optimize=True)
    return path
