"""What a face is: a function from sensors to values, and one from values
to animation frames. The values double as the render cache key, so a face
is re-rendered exactly when something it shows has changed."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from ..sensors import SensorSnapshot

Values = dict[str, object]


@dataclass(frozen=True)
class FaceContext:
    size: int = 640
    assets_dir: Path = Path("assets")
    language: str = "en"
    now: datetime | None = None
    threshold: float | None = None       # alarm: degrees C
    hours: tuple[int, int] | None = None  # night: start hour, end hour

    @property
    def fonts_dir(self) -> Path:
        return self.assets_dir / "fonts"

    def clock(self) -> datetime:
        return self.now or datetime.now()


@dataclass(frozen=True)
class Face:
    name: str
    values: Callable[[SensorSnapshot, FaceContext], Values | None]
    render: Callable[[Values, FaceContext], list[Image.Image]]
    background: str | None = None  # asset GIF the face animates over, if any


def fmt_int(value: float | None, suffix: str = "") -> str:
    return "–" if value is None else f"{round(value)}{suffix}"


def fmt_one(value: float | None, suffix: str = "") -> str:
    return "–" if value is None else f"{value:.1f}{suffix}"


def need(*values: object) -> bool:
    """True when every value a face cannot do without is present."""
    return all(v is not None for v in values)
