from kraken_lcd.config import (KNOWN_SCREENS, CacheConfig, RenderConfig,
                               ScreenStyle)
from kraken_lcd.screens import SCREENS, build_screens, cache_key
from kraken_lcd.sensors import SensorSnapshot

ROUNDING = CacheConfig()  # round_temp_to=1, round_load_to=5, round_rpm_to=50


def test_known_screens_matches_built_screens():
    assert set(KNOWN_SCREENS) == set(build_screens({}))


def test_cpu_load_bucketed_to_5_percent_steps():
    elements = SCREENS["cpu"].build(SensorSnapshot(cpu_load=37.4), ROUNDING)
    assert [e.text for e in elements] == ["CPU", "35%"]


def test_liquid_tile_needs_its_sensor():
    assert SCREENS["liquid"].build(SensorSnapshot(), ROUNDING) is None
    elements = SCREENS["liquid"].build(SensorSnapshot(liquid_temp=41.6), ROUNDING)
    assert elements[1].text == "42°C"


def test_gpu_tile_needs_its_sensor():
    assert SCREENS["gpu"].build(SensorSnapshot(), ROUNDING) is None
    elements = SCREENS["gpu"].build(SensorSnapshot(gpu_load=98.7), ROUNDING)
    assert elements[1].text == "100%"


def test_ram_tile():
    assert SCREENS["ram"].build(SensorSnapshot(), ROUNDING) is None
    elements = SCREENS["ram"].build(SensorSnapshot(ram_percent=26.5), ROUNDING)
    assert [e.text for e in elements] == ["RAM", "25%"]


def test_nvme_tile():
    elements = SCREENS["nvme"].build(SensorSnapshot(nvme_temp=59.9), ROUNDING)
    assert [e.text for e in elements] == ["SSD", "60°C"]


def test_pump_and_fan_tiles_bucket_rpm():
    pump = SCREENS["pump"].build(SensorSnapshot(pump_rpm=1866), ROUNDING)
    fan = SCREENS["fan"].build(SensorSnapshot(fan_rpm=914), ROUNDING)
    assert [e.text for e in pump] == ["Pump rpm", "1850"]
    assert [e.text for e in fan] == ["Fan rpm", "900"]
    assert SCREENS["pump"].build(SensorSnapshot(), ROUNDING) is None


def test_temps_tile_shows_na_for_missing_gpu():
    # round_temp_to defaults to 2 -> 61.4 °C buckets to 62 °C
    elements = SCREENS["temps"].build(SensorSnapshot(cpu_temp=61.4), ROUNDING)
    assert [e.text for e in elements] == ["CPU", "62°C", "GPU", "N/A"]


def test_temps_tile_skipped_without_any_temperature():
    assert SCREENS["temps"].build(SensorSnapshot(cpu_load=50.0), ROUNDING) is None


def test_dual_layout_positions():
    elements = SCREENS["temps"].build(
        SensorSnapshot(cpu_temp=60.0, gpu_temp=50.0), ROUNDING)
    assert [e.cx for e in elements] == [0.25, 0.25, 0.75, 0.75]


def test_style_overrides_label_color_background_and_position():
    styles = {"cpu": ScreenStyle(label="Prozessor", background="alt.gif",
                                 color=(127, 219, 255), value_y=0.7)}
    screen = build_screens(styles)["cpu"]
    assert screen.background == "alt.gif"
    label, value = screen.build(SensorSnapshot(cpu_load=50.0), ROUNDING)
    assert label.text == "Prozessor"
    assert label.color == value.color == (127, 219, 255)
    assert value.cy == 0.7


def test_per_tile_render_overrides():
    from kraken_lcd.screens import effective_render_config
    base = RenderConfig(colors=64, max_frames=30)
    plain = build_screens({})["cpu"]
    tuned = build_screens({"liquid": ScreenStyle(colors=40, max_frames=20)})["liquid"]
    assert effective_render_config(plain, base) is base  # no copy when unset
    effective = effective_render_config(tuned, base)
    assert (effective.colors, effective.max_frames) == (40, 20)
    assert effective.size == base.size  # untouched settings inherited


def test_style_overrides_dual_labels():
    styles = {"temps": ScreenStyle(left_label="Chip", right_label="Grafik")}
    screen = build_screens(styles)["temps"]
    texts = [e.text for e in screen.build(
        SensorSnapshot(cpu_temp=60.0, gpu_temp=50.0), ROUNDING)]
    assert texts == ["Chip", "60°C", "Grafik", "50°C"]


def test_unstyled_screens_keep_defaults():
    screens = build_screens({"cpu": ScreenStyle(label="X")})
    assert screens["gpu"].background == "gpu.gif"
    label, _ = screens["gpu"].build(SensorSnapshot(gpu_load=10.0), ROUNDING)
    assert label.text == "GPU"


def test_cache_key_stable_within_bucket_and_value_sensitive():
    render_cfg = RenderConfig()
    screen = SCREENS["cpu"]
    key_a = cache_key(screen, screen.build(SensorSnapshot(cpu_load=36.0), ROUNDING), render_cfg)
    key_b = cache_key(screen, screen.build(SensorSnapshot(cpu_load=34.0), ROUNDING), render_cfg)
    key_c = cache_key(screen, screen.build(SensorSnapshot(cpu_load=42.0), ROUNDING), render_cfg)
    assert key_a == key_b  # 36 % and 34 % both bucket to 35 %
    assert key_a != key_c  # 42 % buckets to 40 %


def test_cache_key_depends_on_render_parameters():
    screen = SCREENS["cpu"]
    elements = screen.build(SensorSnapshot(cpu_load=36.0), ROUNDING)
    assert (cache_key(screen, elements, RenderConfig(colors=64))
            != cache_key(screen, elements, RenderConfig(colors=128)))


def test_cache_key_depends_on_font_scale():
    screen = SCREENS["cpu"]
    elements = screen.build(SensorSnapshot(cpu_load=36.0), ROUNDING)
    assert (cache_key(screen, elements, RenderConfig(font_scale=1.0))
            != cache_key(screen, elements, RenderConfig(font_scale=1.2)))


def test_cache_key_depends_on_text_color():
    screen_white = build_screens({})["cpu"]
    screen_blue = build_screens({"cpu": ScreenStyle(color=(0, 0, 255))})["cpu"]
    snap = SensorSnapshot(cpu_load=36.0)
    cfg = RenderConfig()
    assert (cache_key(screen_white, screen_white.build(snap, ROUNDING), cfg)
            != cache_key(screen_blue, screen_blue.build(snap, ROUNDING), cfg))


def test_cache_key_depends_on_upload_budget():
    screen = SCREENS["cpu"]
    elements = screen.build(SensorSnapshot(cpu_load=36.0), ROUNDING)
    cfg = RenderConfig()
    assert (cache_key(screen, elements, cfg, budget_bytes=4 * 2**20)
            != cache_key(screen, elements, cfg, budget_bytes=2 * 2**20))


def test_cache_key_tracks_background_file_changes(tmp_path):
    background = tmp_path / "bg.gif"
    background.write_bytes(b"version one")
    screen = SCREENS["cpu"]
    elements = screen.build(SensorSnapshot(cpu_load=36.0), ROUNDING)
    cfg = RenderConfig()
    key_before = cache_key(screen, elements, cfg, background=background)
    background.write_bytes(b"swapped content!")  # different size and mtime
    key_after = cache_key(screen, elements, cfg, background=background)
    assert key_before != key_after


def test_cache_key_survives_missing_background(tmp_path):
    screen = SCREENS["cpu"]
    elements = screen.build(SensorSnapshot(cpu_load=36.0), ROUNDING)
    key = cache_key(screen, elements, RenderConfig(),
                    background=tmp_path / "missing.gif")
    assert "missing" in key  # no exception, render will fail loudly instead
