import pytest

from kraken_lcd.config import MIN_DISPLAY_SECONDS, ConfigError, load_config


def test_defaults_without_config_file(tmp_path):
    cfg = load_config(None, tmp_path)
    assert cfg.carousel.display_seconds == 10
    assert cfg.carousel.brightness == 100
    assert cfg.carousel.screens == ("liquid", "cpu", "gpu", "temps")
    assert cfg.render.size == 0  # 0 = auto-detect from the device
    assert cfg.assets_dir == tmp_path / "assets"


def test_toml_overrides(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[carousel]\ndisplay_seconds = 12\nscreens = ["cpu"]\n'
        "[render]\ncolors = 128\n")
    cfg = load_config(None, tmp_path)
    assert cfg.carousel.display_seconds == 12
    assert cfg.carousel.screens == ("cpu",)
    assert cfg.render.colors == 128
    # untouched sections keep their defaults
    assert cfg.device.upload_retries == 2


def test_display_seconds_clamped_to_safety_floor(tmp_path):
    (tmp_path / "config.toml").write_text("[carousel]\ndisplay_seconds = 1\n")
    cfg = load_config(None, tmp_path)
    assert cfg.carousel.display_seconds == MIN_DISPLAY_SECONDS


@pytest.mark.parametrize("toml_text", [
    "[carousel]\nbrightness = 150\n",
    "[carousel]\nbrightness = -1\n",
    '[carousel]\nscreens = ["bogus"]\n',
    "[carousel]\nscreens = []\n",
    "[render]\ncolors = 1\n",
    "[render]\nsize = 10\n",
    "[device]\nupload_retries = 0\n",
    "[device]\nmax_upload_megabytes = 0\n",
    '[carousel]\ndisplay_seconds = "zehn"\n',
    "[render]\nfont_scale = 0.1\n",
    "[render]\nfont_scale = 5\n",
])
def test_invalid_values_rejected(tmp_path, toml_text):
    (tmp_path / "config.toml").write_text(toml_text)
    with pytest.raises(ConfigError):
        load_config(None, tmp_path)


def test_screen_styles_parsed(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[screens.cpu]\nlabel = "Prozessor"\ncolor = "#7FDBFF"\nvalue_y = 0.7\n'
        '[screens.temps]\nleft_label = "Chip"\n')
    cfg = load_config(None, tmp_path)
    cpu = cfg.screen_styles["cpu"]
    assert cpu.label == "Prozessor"
    assert cpu.color == (0x7F, 0xDB, 0xFF)
    assert cpu.value_y == 0.7
    assert cpu.label_y is None  # untouched -> default
    assert cfg.screen_styles["temps"].left_label == "Chip"


def test_per_tile_render_overrides_parsed(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[screens.liquid]\ncolors = 40\nmax_frames = 20\n")
    style = load_config(None, tmp_path).screen_styles["liquid"]
    assert (style.colors, style.max_frames) == (40, 20)
    assert style.label is None  # untouched fields keep their defaults


@pytest.mark.parametrize("toml_text", [
    '[screens.bogus]\nlabel = "X"\n',          # unknown screen name
    '[screens.cpu]\ncolor = "blau"\n',         # invalid color format
    '[screens.cpu]\ncolor = "#12345"\n',       # too short
    "[screens.cpu]\nlabel_y = 1.5\n",          # fraction out of range
    '[screens.cpu]\nlabel = ""\n',             # empty label
    "[screens.cpu]\ncolors = 1\n",             # palette too small
    "[screens.cpu]\nmax_frames = 0\n",         # no frames
])
def test_invalid_screen_styles_rejected(tmp_path, toml_text):
    (tmp_path / "config.toml").write_text(toml_text)
    with pytest.raises(ConfigError):
        load_config(None, tmp_path)


def test_cooling_fixed_and_curve_parsed(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[cooling]\npump = 60\nfan = [[30, 30], [36.5, 50], [40, 100]]\n")
    cfg = load_config(None, tmp_path)
    assert cfg.cooling.pump == 60
    assert cfg.cooling.fan == ((30.0, 30), (36.5, 50), (40.0, 100))


def test_cooling_defaults_to_untouched(tmp_path):
    cfg = load_config(None, tmp_path)
    assert cfg.cooling.pump is None and cfg.cooling.fan is None


@pytest.mark.parametrize("toml_text", [
    "[cooling]\npump = 150\n",                     # duty out of range
    "[cooling]\nfan = [[40, 50], [30, 100]]\n",    # temps not ascending
    "[cooling]\nfan = [[30, 50]]\n",               # single point is no curve
    "[cooling]\nfan = [[30, 50, 1], [40, 100]]\n", # malformed point
    "[cooling]\nfan = [[5, 50], [40, 100]]\n",     # temp outside liquid range
    '[cooling]\npump = "schnell"\n',
    "[device]\nimage_memory_megabytes = 0\n",
])
def test_invalid_cooling_and_memory_rejected(tmp_path, toml_text):
    (tmp_path / "config.toml").write_text(toml_text)
    with pytest.raises(ConfigError):
        load_config(None, tmp_path)


def test_new_defaults(tmp_path):
    cfg = load_config(None, tmp_path)
    assert cfg.cache.round_rpm_to == 50
    assert cfg.cache.round_temp_to == 2
    assert cfg.device.image_memory_megabytes == 6.0
    assert cfg.device.driver_patch is True
    assert cfg.screen_styles == {}


def test_driver_patch_can_be_disabled(tmp_path):
    (tmp_path / "config.toml").write_text("[device]\ndriver_patch = false\n")
    assert load_config(None, tmp_path).device.driver_patch is False


def test_driver_patch_must_be_boolean(tmp_path):
    (tmp_path / "config.toml").write_text('[device]\ndriver_patch = "ja"\n')
    with pytest.raises(ConfigError):
        load_config(None, tmp_path)


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml", tmp_path)


def test_invalid_toml_is_an_error(tmp_path):
    (tmp_path / "config.toml").write_text("not [ valid toml")
    with pytest.raises(ConfigError):
        load_config(None, tmp_path)


def test_absolute_assets_dir_is_kept(tmp_path):
    (tmp_path / "config.toml").write_text('[render]\nassets_dir = "/opt/gifs"\n')
    cfg = load_config(None, tmp_path)
    assert str(cfg.assets_dir) == "/opt/gifs"
