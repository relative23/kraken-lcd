import pytest
from PIL import Image

from kraken_lcd.render import render_gif
from kraken_lcd.screens import TextElement

ELEMENTS = (
    TextElement("CPU", "label", 0.5, 0.41),
    TextElement("35%", "value", 0.5, 0.54),
)


def test_render_produces_square_animated_gif(tiny_gif, tmp_path, render_cfg):
    out = tmp_path / "out.gif"
    render_gif(tiny_gif, ELEMENTS, out, render_cfg)
    assert out.stat().st_size > 0
    assert not (tmp_path / "out.gif.tmp").exists()  # atomic write, no leftovers
    with Image.open(out) as img:
        assert img.size == (128, 128)
        assert img.is_animated
        assert img.n_frames == 3  # capped at render_cfg.max_frames


def test_render_creates_missing_output_directory(tiny_gif, tmp_path, render_cfg):
    out = tmp_path / "sub" / "dir" / "out.gif"
    render_gif(tiny_gif, ELEMENTS, out, render_cfg)
    assert out.is_file()


def test_render_missing_background_raises(tmp_path, render_cfg):
    with pytest.raises(FileNotFoundError):
        render_gif(tmp_path / "missing.gif", ELEMENTS, tmp_path / "out.gif", render_cfg)


def test_budget_forces_frame_thinning(tiny_gif, tmp_path, render_cfg):
    generous = tmp_path / "generous.gif"
    tight = tmp_path / "tight.gif"
    render_gif(tiny_gif, ELEMENTS, generous, render_cfg, budget_bytes=None)
    render_gif(tiny_gif, ELEMENTS, tight, render_cfg, budget_bytes=1)
    with Image.open(generous) as a, Image.open(tight) as b:
        assert b.n_frames < a.n_frames  # thinning kicked in
    assert tight.stat().st_size > 0  # best effort is still written


def test_large_budget_changes_nothing(tiny_gif, tmp_path, render_cfg):
    unlimited = tmp_path / "unlimited.gif"
    budgeted = tmp_path / "budgeted.gif"
    render_gif(tiny_gif, ELEMENTS, unlimited, render_cfg, budget_bytes=None)
    render_gif(tiny_gif, ELEMENTS, budgeted, render_cfg, budget_bytes=10**9)
    assert unlimited.read_bytes() == budgeted.read_bytes()


def test_overlay_actually_changes_pixels(tiny_gif, tmp_path, render_cfg):
    with_text = tmp_path / "with.gif"
    without_text = tmp_path / "without.gif"
    render_gif(tiny_gif, ELEMENTS, with_text, render_cfg)
    render_gif(tiny_gif, (), without_text, render_cfg)
    a = Image.open(with_text).convert("RGB")
    b = Image.open(without_text).convert("RGB")
    assert a.tobytes() != b.tobytes()
