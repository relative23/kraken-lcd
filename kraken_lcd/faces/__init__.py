"""Face screens: full-screen layouts with many values and their own
animation, next to the classic label-and-value tiles.

A face's values travel as ``TextElement``s with the role ``data`` (one
``key=json`` each). That way the render cache keys faces exactly like
tiles: a face is re-rendered only when something it shows has changed.
"""

import json
import logging
from pathlib import Path

from ..config import RenderConfig
from ..elements import TextElement
from .base import Face, FaceContext, Values
from .dashboards import FACES as _DASHBOARDS
from .draw import FRAME_MS
from .everyday import FACES as _EVERYDAY
from .helm import FACES as _HELM
from .scenes import FACES as _SCENES

log = logging.getLogger(__name__)

FACES: dict[str, Face] = {face.name: face for face in (*_DASHBOARDS, *_HELM, *_SCENES, *_EVERYDAY)}
DATA_ROLE = "data"


def encode(values: Values) -> tuple[TextElement, ...]:
    return tuple(TextElement(f"{key}={json.dumps(value, separators=(',', ':'))}", DATA_ROLE, 0.0, 0.0)
                 for key, value in values.items())


def decode(elements: tuple[TextElement, ...]) -> Values:
    values: Values = {}
    for element in elements:
        key, _, raw = element.text.partition("=")
        values[key] = json.loads(raw)
    return values


def render_face(face: Face, elements: tuple[TextElement, ...], out_path: Path,
                cfg: RenderConfig, context: FaceContext,
                budget_bytes: int | None = None) -> None:
    """Render *face* with the values carried by *elements* to *out_path*."""
    from ..render import save_frames  # render imports screens, which builds faces

    frames = face.render(decode(elements), context)
    save_frames(frames, [FRAME_MS] * len(frames), out_path, cfg, budget_bytes)


__all__ = ["FACES", "Face", "FaceContext", "decode", "encode", "render_face"]
