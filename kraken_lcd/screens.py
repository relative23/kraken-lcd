"""Declarative screen definitions: what each carousel tile shows.

A ``Screen``'s ``build`` turns a sensor snapshot into text elements with
already-bucketed values, or ``None`` when its sensors are missing. Because
the cache key is derived from exactly these elements (plus the background
file's identity), the displayed value and the cached file can never disagree.

Per-tile appearance (label, background, color, positions) comes from
``ScreenStyle`` entries in the config; ``build_screens`` resolves them.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .config import CacheConfig, RenderConfig, ScreenStyle
from .sensors import SensorSnapshot

# bump when FONT_ROLES or the anchors change, so stale cached tiles
# rendered with an old layout are never reused
LAYOUT_VERSION = 2

# role -> (font px, shadow px) at the 640 px reference resolution
FONT_ROLES = {
    "label": (64, 3),
    "value": (130, 4),
    "dual_label": (56, 3),
    "dual_value": (96, 4),
}

# vertical anchors as fractions of the canvas; spread out enough that the
# large value text never collides with its label
_SINGLE_LABEL_Y = 0.5 - 85 / 640
_SINGLE_VALUE_Y = 0.5 + 60 / 640
_DUAL_LABEL_Y = 0.5 - 75 / 640
_DUAL_VALUE_Y = 0.5 + 40 / 640

WHITE = (255, 255, 255)


@dataclass(frozen=True)
class TextElement:
    text: str
    role: str   # key into FONT_ROLES
    cx: float   # text center, fraction of canvas width
    cy: float   # text center, fraction of canvas height
    color: tuple[int, int, int] = WHITE


@dataclass(frozen=True)
class Screen:
    name: str
    background: str  # file name inside the assets directory
    build: Callable[[SensorSnapshot, CacheConfig], tuple[TextElement, ...] | None]
    colors: int | None = None       # per-tile palette override
    max_frames: int | None = None   # per-tile frame-cap override


def effective_render_config(screen: Screen, render_cfg: RenderConfig) -> RenderConfig:
    """Global render settings with the tile's own overrides applied."""
    if screen.colors is None and screen.max_frames is None:
        return render_cfg
    return replace(render_cfg,
                   colors=screen.colors or render_cfg.colors,
                   max_frames=screen.max_frames or render_cfg.max_frames)


def _bucket(value: float, step: int) -> int:
    return int(round(value / step) * step)


def _fmt_temp(value: float, rounding: CacheConfig) -> str:
    return f"{_bucket(value, rounding.round_temp_to)}°C"


def _fmt_load(value: float, rounding: CacheConfig) -> str:
    return f"{_bucket(value, rounding.round_load_to)}%"


def _fmt_rpm(value: float, rounding: CacheConfig) -> str:
    return f"{_bucket(value, rounding.round_rpm_to)}"


# name -> (default background, default label, sensor getter, value formatter)
_SINGLE_TILES: dict[str, tuple[str, str, Callable, Callable]] = {
    "liquid": ("liquid.gif", "Liquid", lambda s: s.liquid_temp, _fmt_temp),
    "cpu": ("cpu.gif", "CPU", lambda s: s.cpu_load, _fmt_load),
    "gpu": ("gpu.gif", "GPU", lambda s: s.gpu_load, _fmt_load),
    "ram": ("cpu.gif", "RAM", lambda s: s.ram_percent, _fmt_load),
    "nvme": ("gpu.gif", "SSD", lambda s: s.nvme_temp, _fmt_temp),
    "pump": ("liquid.gif", "Pump rpm", lambda s: s.pump_rpm, _fmt_rpm),
    "fan": ("liquid.gif", "Fan rpm", lambda s: s.fan_rpm, _fmt_rpm),
}


def _make_single(name: str, style: ScreenStyle) -> Screen:
    default_bg, default_label, get_value, fmt = _SINGLE_TILES[name]
    label = style.label or default_label
    label_y = style.label_y if style.label_y is not None else _SINGLE_LABEL_Y
    value_y = style.value_y if style.value_y is not None else _SINGLE_VALUE_Y
    color = style.color

    def build(snap: SensorSnapshot, rounding: CacheConfig):
        value = get_value(snap)
        if value is None:
            return None
        return (TextElement(label, "label", 0.5, label_y, color),
                TextElement(fmt(value, rounding), "value", 0.5, value_y, color))

    return Screen(name, style.background or default_bg, build,
                  colors=style.colors, max_frames=style.max_frames)


def _make_temps(style: ScreenStyle) -> Screen:
    left = style.left_label or "CPU"
    right = style.right_label or "GPU"
    label_y = style.label_y if style.label_y is not None else _DUAL_LABEL_Y
    value_y = style.value_y if style.value_y is not None else _DUAL_VALUE_Y
    color = style.color

    def build(snap: SensorSnapshot, rounding: CacheConfig):
        if snap.cpu_temp is None and snap.gpu_temp is None:
            return None

        def fmt(temp: float | None) -> str:
            return _fmt_temp(temp, rounding) if temp is not None else "N/A"

        return (TextElement(left, "dual_label", 0.25, label_y, color),
                TextElement(fmt(snap.cpu_temp), "dual_value", 0.25, value_y, color),
                TextElement(right, "dual_label", 0.75, label_y, color),
                TextElement(fmt(snap.gpu_temp), "dual_value", 0.75, value_y, color))

    return Screen("temps", style.background or "temp.gif", build,
                  colors=style.colors, max_frames=style.max_frames)


def build_screens(styles: Mapping[str, ScreenStyle]) -> dict[str, Screen]:
    """All screens, with per-tile config overrides applied."""
    default = ScreenStyle()
    screens = {name: _make_single(name, styles.get(name, default))
               for name in _SINGLE_TILES}
    screens["temps"] = _make_temps(styles.get("temps", default))
    return screens


# default layout (no overrides); also the reference the tests run against
SCREENS: dict[str, Screen] = build_screens({})


def _file_identity(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        stat = path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return "missing"


def cache_key(screen: Screen, elements: tuple[TextElement, ...],
              render_cfg: RenderConfig, budget_bytes: int | None = None,
              background: Path | None = None) -> str:
    """Key over everything that affects the rendered pixels.

    *background* is the resolved background path; its size/mtime enter the
    key so swapping a GIF file takes effect without clearing the cache.
    """
    parts = [f"v{LAYOUT_VERSION}", screen.name, screen.background,
             _file_identity(background), str(render_cfg.size),
             str(render_cfg.max_frames), str(render_cfg.colors),
             str(render_cfg.font_scale), str(budget_bytes)]
    parts += [f"{e.text}@{e.role}@{e.cx:.4f}:{e.cy:.4f}@{e.color}" for e in elements]
    return "|".join(parts)
