"""Configuration loading and validation."""

import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Device safety: uploads faster than this stressed the Kraken's image endpoint
# in the past (bootloader incident) — the floor cannot be lowered via config.
MIN_DISPLAY_SECONDS = 5.0

KNOWN_SCREENS = ("liquid", "cpu", "gpu", "temps", "ram", "nvme", "pump", "fan")

# duty is int 0..100; a curve is a tuple of (liquid °C, duty %) points
SpeedSetting = int | tuple[tuple[float, int], ...] | None

# None marks sections with their own nested validation
_ALLOWED_KEYS: dict[str, set[str] | None] = {
    "carousel": {"display_seconds", "brightness", "screens"},
    "render": {"size", "max_frames", "colors", "font_scale", "assets_dir"},
    "cache": {"dir", "max_megabytes", "round_temp_to", "round_load_to", "round_rpm_to"},
    "device": {"max_upload_megabytes", "upload_retries",
               "image_memory_megabytes", "driver_patch"},
    "cooling": {"pump", "fan"},
    "screens": None,
}

_SCREEN_STYLE_KEYS = {"label", "background", "color", "label_y", "value_y",
                      "left_label", "right_label", "colors", "max_frames"}


class ConfigError(Exception):
    """Invalid or unreadable configuration."""


@dataclass(frozen=True)
class CarouselConfig:
    display_seconds: float = 10.0
    brightness: int = 100
    screens: tuple[str, ...] = ("liquid", "cpu", "gpu", "temps")


@dataclass(frozen=True)
class RenderConfig:
    size: int = 0  # 0 = auto-detect from the device (640 without a device)
    max_frames: int = 30
    colors: int = 64
    font_scale: float = 1.0
    assets_dir: Path = Path("assets")


@dataclass(frozen=True)
class CacheConfig:
    dir: Path = Path("/var/cache/kraken-lcd")
    max_megabytes: float = 50.0
    # 2 °C steps halve the temps-tile combination matrix in each dimension
    # (~4x fewer renders) at a display accuracy of ±1 °C
    round_temp_to: int = 2
    round_load_to: int = 5
    round_rpm_to: int = 50


@dataclass(frozen=True)
class DeviceConfig:
    max_upload_megabytes: float = 4.0
    upload_retries: int = 2
    # clear the device image memory before the queued uploads exceed this;
    # conservative: the 2024 Elite's usable capacity varies, rejections were
    # observed anywhere between ~5 and ~10 MB of accumulated uploads
    image_memory_megabytes: float = 6.0
    # patched KrakenZ3 driver: verified bucket setup + flash-free cleanup;
    # falls back to the stock driver automatically when liquidctl changes
    driver_patch: bool = True


@dataclass(frozen=True)
class ScreenStyle:
    """Per-tile overrides; None means: use the tile's built-in default."""
    label: str | None = None
    background: str | None = None
    color: tuple[int, int, int] = (255, 255, 255)
    label_y: float | None = None
    value_y: float | None = None
    left_label: str | None = None   # dual tile (temps) only
    right_label: str | None = None  # dual tile (temps) only
    colors: int | None = None       # per-tile palette size (file-size tuning)
    max_frames: int | None = None   # per-tile frame cap (file-size tuning)


@dataclass(frozen=True)
class CoolingConfig:
    pump: SpeedSetting = None
    fan: SpeedSetting = None


@dataclass(frozen=True)
class Config:
    base_dir: Path
    carousel: CarouselConfig = CarouselConfig()
    render: RenderConfig = RenderConfig()
    cache: CacheConfig = CacheConfig()
    device: DeviceConfig = DeviceConfig()
    cooling: CoolingConfig = CoolingConfig()
    screen_styles: dict[str, ScreenStyle] = field(default_factory=dict)

    @property
    def assets_dir(self) -> Path:
        d = self.render.assets_dir
        return d if d.is_absolute() else self.base_dir / d


def _read_toml(path: Path | None, base_dir: Path) -> dict:
    if path is None:
        candidate = base_dir / "config.toml"
        if not candidate.is_file():
            log.info("no config.toml in %s, using defaults", base_dir)
            return {}
        path = candidate
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from None


def _warn_unknown(data: dict) -> None:
    for section, content in data.items():
        if section not in _ALLOWED_KEYS:
            log.warning("config: unknown section [%s] ignored", section)
            continue
        allowed = _ALLOWED_KEYS[section]
        if allowed is None or not isinstance(content, dict):
            continue  # nested sections validate themselves
        for key in content:
            if key not in allowed:
                log.warning("config: unknown key %s.%s ignored", section, key)


def _num(section: dict, key: str, default: float, where: str) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}.{key} must be a number")
    return float(value)


def _int(section: dict, key: str, default: int, where: str) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}.{key} must be an integer")
    return value


def _bool(section: dict, key: str, default: bool, where: str) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}.{key} must be true or false")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _parse_color(value, where: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ConfigError(f'{where}.color must look like "#RRGGBB"')
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def _parse_fraction(section: dict, key: str, where: str) -> float | None:
    if key not in section:
        return None
    value = section[key]
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0.05 <= value <= 0.95):
        raise ConfigError(f"{where}.{key} must be a number between 0.05 and 0.95")
    return float(value)


def _parse_screen_styles(data: dict) -> dict[str, ScreenStyle]:
    section = data.get("screens", {})
    if not isinstance(section, dict):
        raise ConfigError("[screens] must contain one table per screen, "
                          "e.g. [screens.cpu]")
    styles: dict[str, ScreenStyle] = {}
    for name, entries in section.items():
        where = f"screens.{name}"
        _require(name in KNOWN_SCREENS,
                 f"[{where}]: unknown screen, known: {list(KNOWN_SCREENS)}")
        if not isinstance(entries, dict):
            raise ConfigError(f"[{where}] must be a table")
        for key in entries:
            if key not in _SCREEN_STYLE_KEYS:
                log.warning("config: unknown key %s.%s ignored", where, key)

        def text(key: str) -> str | None:
            value = entries.get(key)
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{where}.{key} must be a non-empty string")
            return value

        color = (_parse_color(entries["color"], where)
                 if "color" in entries else (255, 255, 255))
        tile_colors = None
        if "colors" in entries:
            tile_colors = _int(entries, "colors", 0, where)
            _require(2 <= tile_colors <= 256,
                     f"{where}.colors must be between 2 and 256")
        tile_max_frames = None
        if "max_frames" in entries:
            tile_max_frames = _int(entries, "max_frames", 0, where)
            _require(tile_max_frames >= 1,
                     f"{where}.max_frames must be at least 1")
        styles[name] = ScreenStyle(
            label=text("label"),
            background=text("background"),
            color=color,
            label_y=_parse_fraction(entries, "label_y", where),
            value_y=_parse_fraction(entries, "value_y", where),
            left_label=text("left_label"),
            right_label=text("right_label"),
            colors=tile_colors,
            max_frames=tile_max_frames,
        )
    return styles


def _parse_speed(section: dict, key: str) -> SpeedSetting:
    if key not in section:
        return None
    value = section[key]
    where = f"cooling.{key}"
    if isinstance(value, bool):
        raise ConfigError(f"{where} must be a percentage or a curve")
    if isinstance(value, int):
        _require(0 <= value <= 100, f"{where}: fixed duty must be 0–100 %")
        return value
    if isinstance(value, list):
        _require(len(value) >= 2, f"{where}: a curve needs at least 2 points")
        points: list[tuple[float, int]] = []
        for point in value:
            valid = (isinstance(point, list) and len(point) == 2
                     and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                             for x in point))
            _require(valid, f"{where}: curve points must be [liquid °C, duty %] pairs")
            temp, duty = float(point[0]), int(point[1])
            _require(15 <= temp <= 60, f"{where}: curve temperatures must be "
                                       f"15–60 °C (liquid temperature)")
            _require(0 <= duty <= 100, f"{where}: duty must be 0–100 %")
            points.append((temp, duty))
        temps = [t for t, _ in points]
        _require(temps == sorted(temps) and len(set(temps)) == len(temps),
                 f"{where}: curve temperatures must be strictly increasing")
        return tuple(points)
    raise ConfigError(f"{where} must be a fixed percentage (e.g. 60) or a "
                      f"curve (e.g. [[30, 50], [40, 100]])")


def load_config(path: Path | None, base_dir: Path) -> Config:
    """Load configuration.

    *path* None means: use ``base_dir/config.toml`` when present, otherwise
    pure defaults. An explicitly given but missing path is an error.
    """
    data = _read_toml(path, base_dir)
    _warn_unknown(data)
    car = data.get("carousel", {})
    ren = data.get("render", {})
    cac = data.get("cache", {})
    dev = data.get("device", {})
    cool = data.get("cooling", {})
    if not isinstance(cool, dict):
        raise ConfigError("[cooling] must be a table")

    brightness = _int(car, "brightness", 100, "carousel")
    _require(0 <= brightness <= 100, "carousel.brightness must be between 0 and 100")

    display_seconds = _num(car, "display_seconds", 10.0, "carousel")
    if display_seconds < MIN_DISPLAY_SECONDS:
        log.warning("carousel.display_seconds=%.1f is below the device safety "
                    "floor, using %.0f s", display_seconds, MIN_DISPLAY_SECONDS)
        display_seconds = MIN_DISPLAY_SECONDS

    screens = car.get("screens", ["liquid", "cpu", "gpu", "temps"])
    _require(isinstance(screens, list) and len(screens) > 0
             and all(s in KNOWN_SCREENS for s in screens),
             f"carousel.screens must be a non-empty list out of {list(KNOWN_SCREENS)}")

    size = _int(ren, "size", 0, "render")
    _require(size == 0 or size >= 64,
             "render.size must be 0 (= auto-detect) or at least 64")
    max_frames = _int(ren, "max_frames", 30, "render")
    _require(max_frames >= 1, "render.max_frames must be at least 1")
    colors = _int(ren, "colors", 64, "render")
    _require(2 <= colors <= 256, "render.colors must be between 2 and 256")
    font_scale = _num(ren, "font_scale", 1.0, "render")
    _require(0.5 <= font_scale <= 3.0, "render.font_scale must be between 0.5 and 3.0")

    max_megabytes = _num(cac, "max_megabytes", 50.0, "cache")
    _require(max_megabytes > 0, "cache.max_megabytes must be positive")
    round_temp_to = _int(cac, "round_temp_to", 2, "cache")
    round_load_to = _int(cac, "round_load_to", 5, "cache")
    round_rpm_to = _int(cac, "round_rpm_to", 50, "cache")
    _require(round_temp_to >= 1 and round_load_to >= 1 and round_rpm_to >= 1,
             "cache round_* values must be at least 1")

    max_upload = _num(dev, "max_upload_megabytes", 4.0, "device")
    _require(max_upload > 0, "device.max_upload_megabytes must be positive")
    retries = _int(dev, "upload_retries", 2, "device")
    _require(retries >= 1, "device.upload_retries must be at least 1")
    image_memory = _num(dev, "image_memory_megabytes", 6.0, "device")
    _require(image_memory > 0, "device.image_memory_megabytes must be positive")

    return Config(
        base_dir=base_dir,
        carousel=CarouselConfig(
            display_seconds=display_seconds,
            brightness=brightness,
            screens=tuple(screens),
        ),
        render=RenderConfig(
            size=size,
            max_frames=max_frames,
            colors=colors,
            font_scale=font_scale,
            assets_dir=Path(str(ren.get("assets_dir", "assets"))),
        ),
        cache=CacheConfig(
            dir=Path(str(cac.get("dir", "/var/cache/kraken-lcd"))),
            max_megabytes=max_megabytes,
            round_temp_to=round_temp_to,
            round_load_to=round_load_to,
            round_rpm_to=round_rpm_to,
        ),
        device=DeviceConfig(
            max_upload_megabytes=max_upload,
            upload_retries=retries,
            image_memory_megabytes=image_memory,
            driver_patch=_bool(dev, "driver_patch", True, "device"),
        ),
        cooling=CoolingConfig(
            pump=_parse_speed(cool, "pump"),
            fan=_parse_speed(cool, "fan"),
        ),
        screen_styles=_parse_screen_styles(data),
    )
