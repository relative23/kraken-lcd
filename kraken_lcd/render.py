"""GIF rendering: scale the background, burn in text, quantize, save atomically."""

import logging
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from .config import RenderConfig
from .screens import FONT_ROLES, TextElement

log = logging.getLogger(__name__)

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEFAULT_FRAME_MS = 80
REFERENCE_SIZE = 640  # FONT_ROLES and shadow sizes are defined for this


@lru_cache(maxsize=16)
def _font(px: int):
    try:
        return ImageFont.truetype(FONT_PATH, px)
    except OSError:
        log.warning("font %s not available, falling back to PIL default", FONT_PATH)
        return ImageFont.load_default()


def _scale_fill_crop(frame: Image.Image, size: int) -> Image.Image:
    """Scale so the frame covers size x size completely, then center-crop."""
    scale = max(size / frame.width, size / frame.height)
    new_w, new_h = round(frame.width * scale), round(frame.height * scale)
    frame = frame.resize((new_w, new_h), Image.Resampling.LANCZOS)
    if (new_w, new_h) != (size, size):
        left, top = (new_w - size) // 2, (new_h - size) // 2
        frame = frame.crop((left, top, left + size, top + size))
    return frame


def _draw_element(draw: ImageDraw.ImageDraw, element: TextElement,
                  cfg: RenderConfig) -> None:
    font_px, shadow_px = FONT_ROLES[element.role]
    size = cfg.size
    scale = (size / REFERENCE_SIZE) * cfg.font_scale
    font = _font(max(8, round(font_px * scale)))
    shadow = max(1, round(shadow_px * scale))
    bbox = draw.textbbox((0, 0), element.text, font=font)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = round(element.cx * size) - width // 2 - bbox[0]
    y = round(element.cy * size) - height // 2 - bbox[1]
    for ox, oy in ((shadow, shadow), (-shadow, -shadow), (shadow, -shadow), (-shadow, shadow)):
        draw.text((x + ox, y + oy), element.text, font=font, fill=(0, 0, 0))
    draw.text((x, y), element.text, font=font, fill=element.color)


def _build_palette(frames: list[Image.Image], colors: int) -> Image.Image:
    """Quantize a strip of sample frames so the shared palette covers colors
    from the whole animation, not just the first frame."""
    step = max(1, len(frames) // 4)
    sample = frames[::step][:4]
    strip = Image.new("RGB", (sum(f.width for f in sample), sample[0].height))
    x = 0
    for frame in sample:
        strip.paste(frame, (x, 0))
        x += frame.width
    return strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)


def _save_quantized(frames: list[Image.Image], durations: list[int],
                    colors: int, path: Path) -> None:
    # One shared small palette keeps the file size down; small files upload
    # fast and are gentle on the Kraken's flaky image endpoint.
    palette = _build_palette(frames, colors)
    quantized = []
    for frame in frames:
        q = frame.quantize(colors=colors, palette=palette, dither=Image.Dither.NONE)
        q.info.pop("transparency", None)  # inherited transparency breaks GIF saving
        quantized.append(q)
    quantized[0].save(path, format="GIF", save_all=True,
                      append_images=quantized[1:], duration=durations,
                      loop=0, optimize=True)


def render_gif(background: Path, elements: tuple[TextElement, ...],
               out_path: Path, cfg: RenderConfig,
               budget_bytes: int | None = None) -> None:
    """Render *background* with burned-in *elements* to *out_path*.

    When the result exceeds *budget_bytes*, frames are progressively thinned
    (durations stretched so the animation keeps its timing) and the palette
    shrunk until it fits. The output is written atomically (temp file +
    rename) so a reader can never observe a half-written GIF.
    """
    frames: list[Image.Image] = []
    durations: list[int] = []
    with Image.open(background) as src:
        for frame in ImageSequence.Iterator(src):
            duration = int(frame.info.get("duration", DEFAULT_FRAME_MS))
            rgb = _scale_fill_crop(frame.convert("RGB"), cfg.size)
            draw = ImageDraw.Draw(rgb)
            for element in elements:
                _draw_element(draw, element, cfg)
            frames.append(rgb)
            durations.append(max(20, duration))
            if len(frames) >= cfg.max_frames:
                break
    if not frames:
        raise ValueError(f"{background} contains no frames")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    attempts = ((1, cfg.colors), (2, cfg.colors),
                (2, max(16, cfg.colors // 2)), (4, max(16, cfg.colors // 2)))
    try:
        size = 0
        for thin, colors in attempts:
            thinned_frames = frames[::thin]
            thinned_durations = [min(1000, d * thin) for d in durations[::thin]]
            _save_quantized(thinned_frames, thinned_durations, colors, tmp_path)
            size = tmp_path.stat().st_size
            if budget_bytes is None or size <= budget_bytes:
                break
            log.info("%s: %.2f MB is over the %.2f MB budget, thinning "
                     "(every %d. frame, %d colors was not enough)", out_path.name,
                     size / 2**20, budget_bytes / 2**20, thin, colors)
        else:
            log.warning("%s is still %.2f MB after maximum thinning; the "
                        "upload size guard may reject it", out_path.name, size / 2**20)
        os.replace(tmp_path, out_path)
    finally:
        tmp_path.unlink(missing_ok=True)
