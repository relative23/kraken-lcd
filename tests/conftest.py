from pathlib import Path

import pytest
from PIL import Image

from kraken_lcd.config import CacheConfig, RenderConfig


@pytest.fixture
def tiny_gif(tmp_path: Path) -> Path:
    """Small 4-frame animated GIF standing in for the real backgrounds."""
    frames = [Image.new("RGB", (100, 80), color)
              for color in ((200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30))]
    path = tmp_path / "bg.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
    return path


@pytest.fixture
def render_cfg() -> RenderConfig:
    # small output keeps the tests fast
    return RenderConfig(size=128, max_frames=3, colors=32)


@pytest.fixture
def cache_cfg() -> CacheConfig:
    return CacheConfig()
