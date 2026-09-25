"""The unit of what a screen shows: one piece of text at a position, or,
for face screens, one value (role ``data``)."""

from dataclasses import dataclass

WHITE = (255, 255, 255)


@dataclass(frozen=True)
class TextElement:
    text: str
    role: str   # key into FONT_ROLES
    cx: float   # text center, fraction of canvas width
    cy: float   # text center, fraction of canvas height
    color: tuple[int, int, int] = WHITE
