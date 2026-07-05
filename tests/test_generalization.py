"""Tests for the multi-device / multi-vendor generalizations:
resolution auto-detect, AMD GPU fallback, system config search order."""

import psutil
import pytest
from PIL import Image

from kraken_lcd import cli
from kraken_lcd import sensors as sensors_mod
from kraken_lcd.config import ConfigError, DeviceConfig, load_config
from kraken_lcd.device import KrakenDevice
from kraken_lcd.sensors import SensorReader
from test_carousel import FakeDevice, _carousel
from test_device import FakeDriver, _device


@pytest.fixture
def cfg(tmp_path, tiny_gif):
    """Small self-contained carousel config (mirrors test_carousel.cfg)."""
    import shutil
    from pathlib import Path

    from kraken_lcd.config import (CacheConfig, CarouselConfig, Config,
                                   RenderConfig)
    for name in ("liquid.gif", "cpu.gif", "temp.gif"):
        shutil.copy(tiny_gif, tmp_path / name)
    return Config(
        base_dir=tmp_path,
        carousel=CarouselConfig(display_seconds=0.01,
                                screens=("liquid", "cpu", "gpu", "temps")),
        render=RenderConfig(size=96, max_frames=2, colors=16,
                            assets_dir=Path(".")),
        cache=CacheConfig(dir=tmp_path / "cache"),
        device=DeviceConfig(),
    )


# ---------------------------------------------------------- resolution auto

def test_size_zero_means_auto_and_is_valid(tmp_path):
    (tmp_path / "config.toml").write_text("[render]\nsize = 0\n")
    assert load_config(None, tmp_path).render.size == 0


def test_tiny_sizes_are_still_rejected(tmp_path):
    (tmp_path / "config.toml").write_text("[render]\nsize = 32\n")
    with pytest.raises(ConfigError):
        load_config(None, tmp_path)


def test_device_reports_lcd_resolution(monkeypatch):
    driver = FakeDriver()
    driver.lcd_resolution = (320, 320)
    dev, _ = _device(monkeypatch, driver=driver)
    assert dev.lcd_resolution == (320, 320)


def test_device_without_resolution_attribute(monkeypatch):
    dev, _ = _device(monkeypatch)  # plain FakeDriver has no lcd_resolution
    assert dev.lcd_resolution is None


def test_device_rejects_garbage_resolution(monkeypatch):
    driver = FakeDriver()
    driver.lcd_resolution = "640x640"
    dev, _ = _device(monkeypatch, driver=driver)
    assert dev.lcd_resolution is None


def test_carousel_resolves_auto_size_from_device(cfg):
    from dataclasses import replace
    auto_cfg = replace(cfg, render=replace(cfg.render, size=0))
    device = FakeDevice()
    device.lcd_resolution = (240, 240)
    carousel = _carousel(auto_cfg, device)
    carousel._resolve_render_size()
    assert carousel._render_cfg.size == 240
    carousel._one_cycle()
    with Image.open(device.uploads[0]) as img:
        assert img.size == (240, 240)


def test_carousel_auto_size_falls_back_to_default(cfg):
    from dataclasses import replace
    auto_cfg = replace(cfg, render=replace(cfg.render, size=0))
    device = FakeDevice()
    device.lcd_resolution = None
    carousel = _carousel(auto_cfg, device)
    carousel._resolve_render_size()
    assert carousel._render_cfg.size == 640


def test_explicit_size_is_never_overridden(cfg):
    device = FakeDevice()
    device.lcd_resolution = (240, 240)
    carousel = _carousel(cfg, device)  # cfg fixture uses size=96
    carousel._resolve_render_size()
    assert carousel._render_cfg.size == 96


# ------------------------------------------------------------- AMD fallback

class _Temp:
    def __init__(self, current, label=""):
        self.current = current
        self.label = label


def test_amd_gpu_fallback(monkeypatch, tmp_path):
    def no_nvidia(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(sensors_mod.subprocess, "run", no_nvidia)
    busy = tmp_path / "gpu_busy_percent"
    busy.write_text("37\n")
    monkeypatch.setattr(sensors_mod.glob, "glob", lambda pattern: [str(busy)])
    monkeypatch.setattr(psutil, "sensors_temperatures", lambda: {
        "amdgpu": [_Temp(58.0, "edge"), _Temp(75.0, "junction")],
    })
    reader = SensorReader()
    snap = reader.snapshot()
    assert snap.gpu_temp == 58.0  # edge, not the junction hotspot
    assert snap.gpu_load == 37.0
    assert reader._no_nvidia is True  # binary missing: no more subprocess calls


def test_nvidia_wins_over_amd(monkeypatch):
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        return sp.CompletedProcess(cmd, 0, stdout="55, 23\n", stderr="")

    monkeypatch.setattr(sensors_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(psutil, "sensors_temperatures",
                        lambda: {"amdgpu": [_Temp(58.0, "edge")]})
    snap = SensorReader().snapshot()
    assert (snap.gpu_temp, snap.gpu_load) == (55.0, 23.0)


def test_no_gpu_at_all_degrades_quietly(monkeypatch):
    def no_nvidia(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(sensors_mod.subprocess, "run", no_nvidia)
    monkeypatch.setattr(sensors_mod.glob, "glob", lambda pattern: [])
    monkeypatch.setattr(psutil, "sensors_temperatures", lambda: {})
    snap = SensorReader().snapshot()
    assert snap.gpu_temp is None and snap.gpu_load is None


# ------------------------------------------------------ config search order

def test_system_config_is_used_when_present(monkeypatch, tmp_path):
    system = tmp_path / "system.toml"
    system.write_text("[carousel]\nbrightness = 55\n")
    monkeypatch.setattr(cli, "SYSTEM_CONFIG", system)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path / "empty")

    class Args:
        config = None

    assert cli._load(Args()).carousel.brightness == 55


def test_explicit_config_beats_system_config(monkeypatch, tmp_path):
    system = tmp_path / "system.toml"
    system.write_text("[carousel]\nbrightness = 55\n")
    explicit = tmp_path / "mine.toml"
    explicit.write_text("[carousel]\nbrightness = 77\n")
    monkeypatch.setattr(cli, "SYSTEM_CONFIG", system)

    class Args:
        config = str(explicit)

    assert cli._load(Args()).carousel.brightness == 77


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "1.0.0" in capsys.readouterr().out
