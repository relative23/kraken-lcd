"""gpu.gif: a wireframe geodesic sphere spinning about a five-fold axis."""

import math

import numpy as np
from PIL import Image, ImageDraw

from .common import LEVELS, LOOP, SIZE, display_mask, grid, smoothstep

PALETTE = ((((0.00, "#000000"), (0.12, "#01150d"), (0.30, "#043a23"),
             (0.52, "#0b7646"), (0.70, "#17b567"), (0.86, "#5cf0a0"),
             (1.00, "#effff6")), LEVELS),)


def _icosphere(freq: int):
    """Geodesic sphere: unit vertices, edges, and one five-fold axis."""
    phi = (1 + 5 ** 0.5) / 2
    corners = np.array([(-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
                        (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
                        (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1)], float)
    faces = ((0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
             (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
             (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
             (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1))
    index: dict[tuple, int] = {}
    verts: list[np.ndarray] = []
    edges: set[tuple[int, int]] = set()

    def vertex(p) -> int:
        p = p / np.linalg.norm(p)
        key = tuple(np.round(p, 6))  # shared face edges yield the same point
        if key not in index:
            index[key] = len(verts)
            verts.append(p)
        return index[key]

    for a, b, c in faces:
        # barycentric grid over the face, pushed out onto the sphere
        ids = {(i, j): vertex((corners[a] * i + corners[b] * j
                               + corners[c] * (freq - i - j)) / freq)
               for i in range(freq + 1) for j in range(freq + 1 - i)}
        for (i, j), here in ids.items():
            for di, dj in ((1, 0), (0, 1), (-1, 1)):
                there = ids.get((i + di, j + dj))
                if there is not None:
                    edges.add((min(here, there), max(here, there)))
    return np.array(verts), sorted(edges), corners[5] / np.linalg.norm(corners[5])


def _rotation(axis, angle: float) -> np.ndarray:
    """Rotation matrix about *axis* (Rodrigues)."""
    x, y, z = axis / np.linalg.norm(axis)
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * k @ k


def render(rng) -> list[np.ndarray]:
    """A fifth of a turn per loop brings the sphere back onto itself, so the
    spin never visibly restarts. Edges facing the viewer are brighter. The
    silhouette of a spinning sphere never changes, so the rim glow costs
    nothing after the first frame.
    """
    verts, edges, axis = _icosphere(3)
    # tilt the spin axis toward the viewer so the rotation reads as 3D
    # (screen y points down, z toward the viewer)
    tilt = 0.42
    up = np.array([0.0, -math.cos(tilt), math.sin(tilt)])
    align = _rotation(np.cross(axis, up), math.acos(np.clip(axis @ up, -1, 1)))
    ss = 4  # lines are drawn at 4x and averaged down: smooth, thin edges
    big = SIZE * ss
    sphere = 0.41  # sphere radius in display units

    _, _, r = grid()
    backdrop = (0.07 * (1 - smoothstep(0.0, 0.5, r))
                + 0.05 * (np.minimum(r, sphere) / sphere) ** 3  # fresnel-like fill
                + 0.16 * np.exp(-np.abs(r - sphere) / 0.012))   # atmosphere at the rim
    fields = []
    for t in LOOP:
        p = verts @ _rotation(axis, 2 * math.pi / 5 * t).T @ align.T
        sx = big / 2 + p[:, 0] * sphere * big
        sy = big / 2 + p[:, 1] * sphere * big
        front = (p[:, 2] + 1) / 2  # 0 = far side, 1 = facing the viewer
        image = Image.new("L", (big, big))
        draw = ImageDraw.Draw(image)
        # back to front, so near edges are drawn over far ones
        for a, b in sorted(edges, key=lambda e: front[e[0]] + front[e[1]]):
            level = 0.18 + 0.82 * ((front[a] + front[b]) / 2) ** 1.6
            draw.line([(sx[a], sy[a]), (sx[b], sy[b])], fill=round(255 * level), width=5)
        for i in np.argsort(front):
            dot = ss * (1.2 + 2.2 * front[i])
            level = min(1.0, 0.25 + 0.95 * front[i] ** 1.6)
            draw.ellipse([sx[i] - dot, sy[i] - dot, sx[i] + dot, sy[i] + dot],
                         fill=round(255 * level))
        lines = np.asarray(image.resize((SIZE, SIZE), Image.Resampling.BOX), float) / 255
        fields.append((backdrop + 1.1 * lines) * display_mask(r))
    return fields
